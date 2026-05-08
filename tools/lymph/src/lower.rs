//! Lower one MIR body into a [`FlowGraph`].

use std::collections::HashMap;

use rustc_hir::def_id::{DefId, LocalDefId};
use rustc_middle::mir::{
    Local, Operand, Place, PlaceElem, ProjectionElem, Rvalue, StatementKind,
    TerminatorKind, VarDebugInfoContents,
};
use rustc_middle::ty::{AdtDef, GenericArgsRef, Ty, TyCtxt};

use crate::flow::{CallSite, EdgeKind, FlowGraph, Slot, SlotId};

pub fn lower_body<'tcx>(tcx: TyCtxt<'tcx>, def_id: LocalDefId) -> FlowGraph {
    // still using `-Zmir-opt-level=0` (set in driver.rs)
    let body = tcx.optimized_mir(def_id);
    let fn_name = tcx.def_path_str(def_id.to_def_id());
    let mut g = FlowGraph::new(fn_name);

    // Parameter names win over later `let` bindings that share a local.
    let mut local_name: HashMap<Local, String> = HashMap::new();
    for info in &body.var_debug_info {
        if let VarDebugInfoContents::Place(p) = info.value {
            if p.projection.is_empty() {
                local_name
                    .entry(p.local)
                    .or_insert_with(|| info.name.to_string());
            }
        }
    }

    for local in body.args_iter() {
        let decl = &body.local_decls[local];
        let ty = type_string(tcx, decl.ty);
        let name = local_name
            .get(&local)
            .cloned()
            .unwrap_or_else(|| format!("_{}", local.as_usize()));
        let id = g.intern(Slot::root_only(name, ty));
        g.params.push(id);
    }
    let ret_ty = type_string(tcx, body.return_ty());
    let ret_id = g.intern(Slot::root_only("<return>", ret_ty));
    g.return_slot = Some(ret_id);

    // Pre-pass: every `_x = &<place>` / `_x = &raw [const|mut] <place>` /
    // every `_y = move _x` (re-binding of one ref local into another) gives
    // place_to_slot enough info to alias `(*_x).f` back to `<place>.f` —
    // see `LowerCtx::resolve_ref_origin`. Without this pass the slot for
    // `(*_x).f` lives independently of `<place>.f`, so writes through `_x`
    // never connect to the original.
    let mut ref_origins: HashMap<Local, Place<'tcx>> = HashMap::new();
    for bb in body.basic_blocks.iter() {
        for stmt in &bb.statements {
            let StatementKind::Assign(box (dst, rvalue)) = &stmt.kind else { continue };
            // Direct binding: only no-projection LHS — `_x.f = &p` gives no
            // useful aliasing rule because subsequent `_x` reads pick up the
            // whole struct, not the field.
            if !dst.projection.is_empty() {
                continue;
            }
            match rvalue {
                Rvalue::Ref(_, _, p) | Rvalue::RawPtr(_, p) => {
                    // Reborrow case `_lhs = &mut (*_x)`: if the borrowed
                    // place starts with a Deref of a known ref local,
                    // splice through to the canonical origin so chained
                    // refs collapse to the original named place.
                    let canonical = canonicalize_origin(tcx, *p, &ref_origins);
                    ref_origins.insert(dst.local, canonical);
                }
                Rvalue::Use(Operand::Copy(p) | Operand::Move(p)) => {
                    // Re-binding `_y = _x` where `_x` was itself a ref —
                    // propagate the alias so `(*_y).f` resolves the same
                    // way as `(*_x).f`.
                    if p.projection.is_empty() {
                        if let Some(origin) = ref_origins.get(&p.local).copied() {
                            ref_origins.insert(dst.local, origin);
                        }
                    }
                }
                _ => {}
            }
        }
    }

    let ctx = LowerCtx { tcx, body, local_name: &local_name, ref_origins: &ref_origins };
    for bb in body.basic_blocks.iter() {
        for stmt in &bb.statements {
            if let StatementKind::Assign(box (dst, rvalue)) = &stmt.kind {
                lower_assign(&ctx, &mut g, dst, rvalue);
            }
        }
        if let Some(term) = &bb.terminator {
            lower_terminator(&ctx, &mut g, &term.kind);
        }
    }
    g
}

struct LowerCtx<'a, 'tcx> {
    tcx: TyCtxt<'tcx>,
    body: &'a rustc_middle::mir::Body<'tcx>,
    local_name: &'a HashMap<Local, String>,
    /// `&place` / `&mut place` / `&raw [const|mut] place` introductions,
    /// transitively chased through ref-to-ref re-bindings. Used by
    /// `place_to_slot` to alias `(*_x).f` ↔ `<place>.f`.
    ref_origins: &'a HashMap<Local, Place<'tcx>>,
}

impl<'a, 'tcx> LowerCtx<'a, 'tcx> {
    /// If `local` was introduced by `_x = &origin` (or any chain of
    /// `_y = move _x` re-bindings on top), return `origin`. None when
    /// no source-level ref edge connects this local to a place.
    fn resolve_ref_origin(&self, local: Local) -> Option<Place<'tcx>> {
        self.ref_origins.get(&local).copied()
    }
}

/// Splice through `_x = &(*_y).f` reborrows so the recorded origin
/// always points at a "real" named place rather than another deref of
/// a ref local. Walks at most as long as the leading projection
/// element is `Deref` of a ref local with a known origin — bounded by
/// the chain length, so no fixed-point reasoning needed.
fn canonicalize_origin<'tcx>(
    tcx: TyCtxt<'tcx>,
    place: Place<'tcx>,
    ref_origins: &HashMap<Local, Place<'tcx>>,
) -> Place<'tcx> {
    let mut current = place;
    loop {
        let Some(ProjectionElem::Deref) = current.projection.first().copied() else {
            return current;
        };
        let Some(origin) = ref_origins.get(&current.local).copied() else {
            return current;
        };
        let mut elems: Vec<PlaceElem<'tcx>> = origin.projection.iter().collect();
        elems.extend(current.projection.iter().skip(1));
        current = Place {
            local: origin.local,
            projection: tcx.mk_place_elems(&elems),
        };
    }
}

fn lower_assign<'tcx>(
    ctx: &LowerCtx<'_, 'tcx>,
    g: &mut FlowGraph,
    dst: &Place<'tcx>,
    rvalue: &Rvalue<'tcx>,
) {
    let dst_slot = place_to_slot(ctx, dst, g);
    match rvalue {
        Rvalue::Use(op) => {
            if let Some(src) = operand_slot(ctx, op, g) {
                g.push_edge(src, dst_slot, EdgeKind::Assign);
            }
        }
        Rvalue::Ref(_, _, place) => {
            let src = place_to_slot(ctx, place, g);
            g.push_edge(src, dst_slot, EdgeKind::Ref);
        }
        Rvalue::RawPtr(_, place) => {
            let src = place_to_slot(ctx, place, g);
            g.push_edge(src, dst_slot, EdgeKind::Ref);
        }
        Rvalue::Aggregate(_kind, operands) => {
            let dst_ty = place_type(ctx, dst);
            let field_names = aggregate_field_names(ctx.tcx, dst_ty, operands.len());
            for (i, op) in operands.iter().enumerate() {
                let Some(src) = operand_slot(ctx, op, g) else { continue };
                let dst_slot_ref = g.slot(dst_slot).clone();
                let field_ty = aggregate_field_ty(ctx.tcx, dst_ty, i);
                let mut path = dst_slot_ref.path.clone();
                path.push(field_names[i].clone());
                let field_slot = g.intern(Slot::new(
                    dst_slot_ref.root.clone(),
                    path,
                    field_ty
                        .map(|t| type_string(ctx.tcx, t))
                        .unwrap_or_else(|| "_".to_string()),
                ));
                g.push_edge(src, field_slot, EdgeKind::FieldInit);
            }
        }
        Rvalue::Cast(_kind, op, _ty) => {
            if let Some(src) = operand_slot(ctx, op, g) {
                g.push_edge(src, dst_slot, EdgeKind::Assign);
            }
        }
        Rvalue::BinaryOp(_op, box (lhs, rhs)) => {
            if let Some(src) = operand_slot(ctx, lhs, g) {
                g.push_edge(src, dst_slot, EdgeKind::Assign);
            }
            if let Some(src) = operand_slot(ctx, rhs, g) {
                g.push_edge(src, dst_slot, EdgeKind::Assign);
            }
        }
        Rvalue::UnaryOp(_op, op) => {
            if let Some(src) = operand_slot(ctx, op, g) {
                g.push_edge(src, dst_slot, EdgeKind::Assign);
            }
        }
        Rvalue::Discriminant(place) | Rvalue::CopyForDeref(place) => {
            let src = place_to_slot(ctx, place, g);
            g.push_edge(src, dst_slot, EdgeKind::Assign);
        }
        _ => {}
    }
}

fn lower_terminator<'tcx>(
    ctx: &LowerCtx<'_, 'tcx>,
    g: &mut FlowGraph,
    kind: &TerminatorKind<'tcx>,
) {
    match kind {
        TerminatorKind::Call { func, args, destination, .. } => {
            let callee = callee_label(ctx, func);
            // Pre-collect each arg's source slot + place, per arg edges
            let mut arg_srcs: Vec<Option<SlotId>> = Vec::with_capacity(args.len());
            let mut arg_places: Vec<Option<Place<'tcx>>> = Vec::with_capacity(args.len());
            // Phase-2.5 cross-fn ref propagation: per-arg, if the
            // operand is a ref local (`Move(_x)` where `_x = &place`
            // upstream), record the canonical caller-side place so the
            // merge step can rewrite callee accesses through that
            // param onto the same slot tree the caller already has.
            let mut arg_origins: Vec<Option<Slot>> = Vec::with_capacity(args.len());
            for arg in args.iter() {
                match &arg.node {
                    Operand::Copy(p) | Operand::Move(p) => {
                        arg_srcs.push(Some(place_to_slot(ctx, p, g)));
                        arg_places.push(Some(*p));
                        arg_origins.push(arg_origin_slot(ctx, p, g));
                    }
                    _ => {
                        arg_srcs.push(None);
                        arg_places.push(None);
                        arg_origins.push(None);
                    }
                }
            }
            g.call_sites.push(CallSite {
                fn_name: callee.clone(),
                arg_origins,
            });
            // Synthetic <ret:callee> 
            let ret_root = format!("<ret:{}>", callee);
            let ret_src = g.intern(Slot::root_only(ret_root, "_"));
            for (i, src) in arg_srcs.iter().enumerate() {
                let Some(src) = *src else { continue };
                let dst_root = format!("{}#{}", callee, i);
                let dst = g.intern(Slot::root_only(dst_root, "_"));
                g.push_edge(src, dst, EdgeKind::CallArg);
                g.push_edge(src, ret_src, EdgeKind::CallArg);
            }
            let dst_slot = place_to_slot(ctx, destination, g);
            g.push_edge(ret_src, dst_slot, EdgeKind::CallReturn);

            // side effects
            for i in 0..args.len() {
                let Some(place) = arg_places[i] else { continue };
                let outer_ty = place.ty(&ctx.body.local_decls, ctx.tcx).ty;
                if !is_mut_ref(outer_ty) {
                    continue;
                }
                let mut elems: Vec<PlaceElem<'tcx>> = place.projection.iter().collect();
                elems.push(ProjectionElem::Deref);
                let deref_place = Place {
                    local: place.local,
                    projection: ctx.tcx.mk_place_elems(&elems),
                };
                let post = place_to_slot(ctx, &deref_place, g);
                for (j, src) in arg_srcs.iter().enumerate() {
                    if j == i { continue }
                    let Some(src) = *src else { continue };
                    g.push_edge(src, post, EdgeKind::CallArg);
                }
            }
        }
        _ => {}
    }
}

fn is_mut_ref<'tcx>(ty: Ty<'tcx>) -> bool {
    use rustc_middle::ty::TyKind;
    match ty.kind() {
        TyKind::Ref(_, _, m) | TyKind::RawPtr(_, m) => m.is_mut(),
        _ => false,
    }
}


fn operand_slot<'tcx>(
    ctx: &LowerCtx<'_, 'tcx>,
    op: &Operand<'tcx>,
    g: &mut FlowGraph,
) -> Option<SlotId> {
    match op {
        Operand::Copy(p) | Operand::Move(p) => Some(place_to_slot(ctx, p, g)),
        _ => None,
    }
}

fn place_to_slot<'tcx>(
    ctx: &LowerCtx<'_, 'tcx>,
    place: &Place<'tcx>,
    g: &mut FlowGraph,
) -> SlotId {
    // Phase-1 ref aliasing: if the place starts with `Deref` of a local
    // we know was introduced by `_x = &origin`, splice `origin`'s
    // local + projection in front and continue with the trailing
    // projections. Chained refs (`_y = _x` after `_x = &origin`) are
    // already collapsed in the ref_origins pre-pass, so one rewrite is
    // enough — no fixed-point loop needed.
    let (root_local, projection): (Local, Vec<PlaceElem<'tcx>>) =
        if let Some(ProjectionElem::Deref) = place.projection.first().copied() {
            if let Some(origin) = ctx.resolve_ref_origin(place.local) {
                let mut combined: Vec<PlaceElem<'tcx>> =
                    origin.projection.iter().collect();
                combined.extend(place.projection.iter().skip(1));
                (origin.local, combined)
            } else {
                (place.local, place.projection.iter().collect())
            }
        } else {
            (place.local, place.projection.iter().collect())
        };
    let (root_name, root_ty) = root_of(ctx, root_local);
    let mut path: Vec<String> = Vec::new();
    let mut current_ty: Ty<'tcx> = ctx.body.local_decls[root_local].ty;
    for elem in projection.iter().copied() {
        match elem {
            ProjectionElem::Deref => {
                path.push("(*)".to_string());
                current_ty = match current_ty.kind() {
                    rustc_middle::ty::TyKind::Ref(_, inner, _) => *inner,
                    rustc_middle::ty::TyKind::RawPtr(inner, _) => *inner,
                    _ => current_ty,
                };
            }
            ProjectionElem::Field(idx, ty) => {
                let name = field_name_at(ctx.tcx, current_ty, idx.as_usize());
                path.push(name);
                current_ty = ty;
            }
            ProjectionElem::Index(local) => {
                let label = ctx.local_name.get(&local).cloned().unwrap_or_else(|| {
                    format!("_{}", local.as_usize())
                });
                path.push(format!("[{}]", label));
                current_ty = current_ty
                    .builtin_index()
                    .unwrap_or(current_ty);
            }
            ProjectionElem::ConstantIndex { offset, .. } => {
                path.push(format!("[{}]", offset));
                current_ty = current_ty
                    .builtin_index()
                    .unwrap_or(current_ty);
            }
            ProjectionElem::Subslice { from, to, .. } => {
                path.push(format!("[{}..{}]", from, to));
            }
            ProjectionElem::Downcast(_variant, _idx) => {}
            ProjectionElem::OpaqueCast(_) => {}
            ProjectionElem::Subtype(_) => {}
        }
    }
    let leaf_ty = if path.is_empty() {
        root_ty.clone()
    } else {
        type_string(ctx.tcx, current_ty)
    };
    let leaf = g.intern(Slot::new(root_name.clone(), path.clone(), leaf_ty));
    // Projections (`_5.0`, `s.inner`, `arr[i]`) are sub-locations of their
    // root. Anything flowing into the root flows into every projection too.
    // Wire root -> leaf with an Assign edge so reachability queries connect.
    if !path.is_empty() {
        let root_id = g.intern(Slot::root_only(root_name, root_ty));
        if root_id != leaf {
            g.push_edge(root_id, leaf, EdgeKind::Assign);
        }
    }
    leaf
}

fn root_of<'tcx>(ctx: &LowerCtx<'_, 'tcx>, local: Local) -> (String, String) {
    let name = if local.as_usize() == 0 {
        "<return>".to_string()
    } else if let Some(n) = ctx.local_name.get(&local) {
        n.clone()
    } else {
        format!("_{}", local.as_usize())
    };
    let ty = type_string(ctx.tcx, ctx.body.local_decls[local].ty);
    (name, ty)
}

fn place_type<'tcx>(ctx: &LowerCtx<'_, 'tcx>, place: &Place<'tcx>) -> Ty<'tcx> {
    place.ty(&ctx.body.local_decls, ctx.tcx).ty
}

fn field_name_at<'tcx>(_tcx: TyCtxt<'tcx>, parent_ty: Ty<'tcx>, idx: usize) -> String {
    match parent_ty.kind() {
        rustc_middle::ty::TyKind::Adt(adt, _) => adt_field_name(*adt, idx),
        rustc_middle::ty::TyKind::Tuple(_) => idx.to_string(),
        _ => idx.to_string(),
    }
}

fn adt_field_name<'tcx>(adt: AdtDef<'tcx>, idx: usize) -> String {
    if adt.is_struct() {
        let variant = adt.non_enum_variant();
        if let Some(f) = variant.fields.iter().nth(idx) {
            return f.name.as_str().to_string();
        }
    }
    idx.to_string()
}

fn aggregate_field_names<'tcx>(tcx: TyCtxt<'tcx>, ty: Ty<'tcx>, arity: usize) -> Vec<String> {
    let _ = tcx;
    match ty.kind() {
        rustc_middle::ty::TyKind::Adt(adt, _) if adt.is_struct() => {
            let variant = adt.non_enum_variant();
            variant.fields.iter().map(|f| f.name.as_str().to_string()).collect()
        }
        rustc_middle::ty::TyKind::Tuple(_) => (0..arity).map(|i| i.to_string()).collect(),
        _ => (0..arity).map(|i| i.to_string()).collect(),
    }
}

fn aggregate_field_ty<'tcx>(
    tcx: TyCtxt<'tcx>,
    ty: Ty<'tcx>,
    idx: usize,
) -> Option<Ty<'tcx>> {
    match ty.kind() {
        rustc_middle::ty::TyKind::Adt(adt, args) if adt.is_struct() => {
            let variant = adt.non_enum_variant();
            let f = variant.fields.iter().nth(idx)?;
            Some(f.ty(tcx, args))
        }
        rustc_middle::ty::TyKind::Tuple(elems) => elems.iter().nth(idx),
        _ => None,
    }
}

/// If `arg_place` is a bare local introduced by a known ref (`_x = &place`,
/// possibly chained), return the *canonical* caller-side place as a
/// `Slot` (root + path) — the same slot identity `place_to_slot` would
/// produce for that place. Returns None when the operand isn't a ref
/// local, when the local has no recorded origin, or when the origin
/// resolves to a non-named root (e.g. another temporary).
fn arg_origin_slot<'tcx>(
    ctx: &LowerCtx<'_, 'tcx>,
    arg_place: &Place<'tcx>,
    g: &mut FlowGraph,
) -> Option<Slot> {
    if !arg_place.projection.is_empty() {
        return None;
    }
    let origin = ctx.resolve_ref_origin(arg_place.local)?;
    // Reuse place_to_slot to produce the canonical slot — same intern
    // path the caller's other accesses go through, so display names
    // line up perfectly.
    let id = place_to_slot(ctx, &origin, g);
    Some(g.slot(id).clone())
}

fn callee_label<'tcx>(ctx: &LowerCtx<'_, 'tcx>, func: &Operand<'tcx>) -> String {
    if let Operand::Constant(c) = func {
        if let rustc_middle::ty::TyKind::FnDef(def_id, _) = c.const_.ty().kind() {
            return ctx.tcx.def_path_str(*def_id);
        }
    }
    "<indirect>".to_string()
}

/// Statically resolvable `(DefId, GenericArgs)` for every TerminatorKind::Call
/// in `def_id`'s MIR. Indirect / dyn / fn-ptr calls collapse to none — that
/// matches `callee_label`'s `<indirect>` rendering, the BFS just doesn't
/// follow them. Same lookup `callee_label` already does, factored out so
/// the depth-bounded driver can reuse it without re-walking the body.
pub fn static_callees<'tcx>(
    tcx: TyCtxt<'tcx>,
    def_id: LocalDefId,
) -> Vec<(DefId, GenericArgsRef<'tcx>)> {
    let body = tcx.optimized_mir(def_id);
    let mut out = Vec::new();
    for bb in body.basic_blocks.iter() {
        let Some(term) = &bb.terminator else { continue };
        let TerminatorKind::Call { func, .. } = &term.kind else { continue };
        let Operand::Constant(c) = func else { continue };
        if let rustc_middle::ty::TyKind::FnDef(callee_def, args) = c.const_.ty().kind() {
            out.push((*callee_def, *args));
        }
    }
    out
}

fn type_string<'tcx>(_tcx: TyCtxt<'tcx>, ty: Ty<'tcx>) -> String {
    ty.to_string()
}
