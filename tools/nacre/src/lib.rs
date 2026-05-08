#![feature(rustc_private)]

extern crate rustc_abi;
extern crate rustc_driver;
extern crate rustc_hir;
extern crate rustc_interface;
extern crate rustc_middle;
extern crate rustc_span;
extern crate rustc_target;

use rustc_abi::{BackendRepr, FieldsShape, Primitive};
use rustc_driver::Compilation;
use rustc_hir::def::{DefKind, Res};
use rustc_hir::def_id::DefId;
use rustc_hir::ItemKind;
use rustc_middle::ty::{self, GenericArg, GenericParamDefKind, Instance, Ty, TyCtxt, TypingEnv};
use rustc_target::callconv::PassMode;

use pyo3::prelude::*;

use std::sync::atomic::{AtomicU64, Ordering};

pub mod emit_c;

/// concurrent python worker hell, avoid temp-file collisions
static TMP_CNT: AtomicU64 = AtomicU64::new(0);

fn nacre_tmp_path(stem: &str) -> std::path::PathBuf {
    let n = TMP_CNT.fetch_add(1, Ordering::Relaxed);
    std::env::temp_dir().join(format!("{stem}_{}_{n}.rs", std::process::id()))
}

/// Rustc release string nacre was built against.
pub const RUSTC_VERSION: &str = env!("NACRE_RUSTC_VERSION");

#[derive(IntoPyObject)]
pub struct FlatField {
    pub path: String,
    pub offset: u64,
    pub size: u64,
    #[pyo3(item("type"))]
    pub ty_desc: String,
    #[pyo3(item("is_ptr"))]
    pub is_ptr: bool,
    #[pyo3(item("is_scalar"))]
    pub is_scalar: bool,
}

#[derive(IntoPyObject)]
pub struct StructLayout {
    pub name: String,
    pub size: u64,
    pub align: u64,
    #[pyo3(item("fields"))]
    pub flat: Vec<FlatField>,
}

pub fn query_layout<'tcx>(
    tcx: TyCtxt<'tcx>,
    ty: Ty<'tcx>,
) -> Result<ty::layout::TyAndLayout<'tcx>, &'tcx ty::layout::LayoutError<'tcx>> {
    tcx.layout_of(TypingEnv::fully_monomorphized().as_query_input(ty))
}

/// Classify a leaf type as (is_ptr, is_scalar) using rustc's `BackendRepr`
/// see : https://doc.rust-lang.org/beta/nightly-rustc/rustc_abi/enum.BackendRepr.html
fn ty_flavor<'tcx>(tcx: TyCtxt<'tcx>, ty: Ty<'tcx>) -> (bool, bool) {
    let Ok(layout) = query_layout(tcx, ty) else {
        return (false, false);
    };
    match layout.backend_repr {
        BackendRepr::Scalar(s) => match s.primitive() {
            Primitive::Pointer(_) => (true, false),
            Primitive::Int(..) | Primitive::Float(..) => (false, true),
        },
        _ => (false, false),
    }
}

fn flatten_into<'tcx>(
    tcx: TyCtxt<'tcx>,
    ty: Ty<'tcx>,
    base: u64,
    prefix: &str,
    out: &mut Vec<FlatField>,
) {
    let Ok(layout) = query_layout(tcx, ty) else {
        return;
    };

    match ty.kind() {
        ty::Adt(adt, args) if adt.is_struct() => {
            let variant = adt.non_enum_variant();
            if let FieldsShape::Arbitrary { ref offsets, .. } = layout.fields {
                for (i, field_def) in variant.fields.iter_enumerated() {
                    let field_ty = field_def.ty(tcx, args);
                    let offset = offsets[i].bytes();
                    let abs = base + offset;
                    let name = field_def.name.as_str();
                    let path = if prefix.is_empty() {
                        name.to_string()
                    } else {
                        format!("{prefix}.{name}")
                    };

                    if let ty::Adt(inner, _) = field_ty.kind() {
                        if inner.is_struct() {
                            flatten_into(tcx, field_ty, abs, &path, out);
                            continue;
                        }
                    }

                    let fl = query_layout(tcx, field_ty).unwrap();
                    let (is_ptr, is_scalar) = ty_flavor(tcx, field_ty);
                    out.push(FlatField {
                        path,
                        offset: abs,
                        size: fl.size.bytes(),
                        ty_desc: clean_ty_desc(tcx, field_ty),
                        is_ptr,
                        is_scalar,
                    });
                }
            }
        }
        _ => {
            let (is_ptr, is_scalar) = ty_flavor(tcx, ty);
            out.push(FlatField {
                path: prefix.to_string(),
                offset: base,
                size: layout.size.bytes(),
                ty_desc: clean_ty_desc(tcx, ty),
                is_ptr,
                is_scalar,
            });
        }
    }
}

pub fn flatten_struct<'tcx>(tcx: TyCtxt<'tcx>, ty: Ty<'tcx>, name: &str) -> StructLayout {
    let layout = query_layout(tcx, ty).unwrap();
    let mut flat = Vec::new();
    flatten_into(tcx, ty, 0, "", &mut flat);
    flat.sort_by_key(|f| f.offset);

    StructLayout {
        name: name.to_string(),
        size: layout.size.bytes(),
        align: layout.align.abi.bytes(),
        flat,
    }
}

/// make struct shallow, do not recurse sub adts
pub fn shallow_struct<'tcx>(tcx: TyCtxt<'tcx>, ty: Ty<'tcx>, name: &str) -> StructLayout {
    let layout = match query_layout(tcx, ty) {
        Ok(l) => l,
        Err(_) => {
            return StructLayout { name: name.to_string(), size: 0, align: 1, flat: vec![] }
        }
    };
    let mut flat = Vec::new();
    if let ty::Adt(adt, args) = ty.kind() {
        if adt.is_struct() {
            if let FieldsShape::Arbitrary { ref offsets, .. } = layout.fields {
                let variant = adt.non_enum_variant();
                for (i, fd) in variant.fields.iter_enumerated() {
                    let fty = fd.ty(tcx, args);
                    let off = offsets[i].bytes();
                    let fl = match query_layout(tcx, fty) {
                        Ok(l) => l,
                        Err(_) => continue,
                    };
                    let (is_ptr, is_scalar) = ty_flavor(tcx, fty);
                    flat.push(FlatField {
                        path: fd.name.as_str().to_string(),
                        offset: off,
                        size: fl.size.bytes(),
                        ty_desc: clean_ty_desc(tcx, fty),
                        is_ptr,
                        is_scalar,
                    });
                }
            }
        }
    }
    flat.sort_by_key(|f| f.offset);
    StructLayout {
        name: name.to_string(),
        size: layout.size.bytes(),
        align: layout.align.abi.bytes(),
        flat,
    }
}

/// Nested access tree
#[derive(IntoPyObject, Clone)]
pub struct AccessNode {
    pub offset: i64,
    pub size: u32,
    pub is_ptr: bool,
    pub is_scalar: bool,
    pub children: Vec<AccessNode>,
}

/// Canonical-path name for use in `ty_desc`
pub fn clean_ty_desc<'tcx>(tcx: TyCtxt<'tcx>, ty: Ty<'tcx>) -> String {
    if let ty::Adt(adt, args) = ty.kind() {
        return tcx.def_path_str_with_args(adt.did(), args);
    }
    format!("{ty}")
}

/// Pointee of a single-indirection pointer type
pub fn pointee_of<'tcx>(tcx: TyCtxt<'tcx>, ty: Ty<'tcx>) -> Option<Ty<'tcx>> {
    match ty.kind() {
        ty::Ref(_, pointee, _) | ty::RawPtr(pointee, _) => Some(*pointee),
        ty::Adt(adt, args) => {
            let path = tcx.def_path_str(adt.did());
            let direct = matches!(
                path.as_str(),
                "alloc::boxed::Box" | "std::boxed::Box"
                    | "core::ptr::NonNull" | "std::ptr::NonNull"
                    | "core::ptr::Unique" | "std::ptr::Unique"
                    | "alloc::rc::Rc" | "std::rc::Rc"
                    | "alloc::sync::Arc" | "std::sync::Arc"
            );
            if direct { args.get(0).and_then(|a| a.as_type()) } else { None }
        }
        _ => None,
    }
}

fn sort_tree(nodes: &mut Vec<AccessNode>) {
    nodes.sort_by_key(|n| n.offset);
    for n in nodes {
        sort_tree(&mut n.children);
    }
}

/// turns type into tree, pointers are edges, primitives are leaves
fn tree_into<'tcx>(tcx: TyCtxt<'tcx>, ty: Ty<'tcx>, base: i64, depth: u32, out: &mut Vec<AccessNode>) {
    let Ok(layout) = query_layout(tcx, ty) else { return };

    // Pointer-shaped: emit one ptr node, recurse into pointee as children.
    if depth > 0 {
        if let Some(pointee) = pointee_of(tcx, ty) {
            let mut children = Vec::new();
            tree_into(tcx, pointee, 0, depth - 1, &mut children);
            out.push(AccessNode {
                offset: base,
                size: layout.size.bytes() as u32,
                is_ptr: true,
                is_scalar: false,
                children,
            });
            return;
        }
    }

    // Composite struct: descend into fields at their offsets.
    if let ty::Adt(adt, args) = ty.kind() {
        if adt.is_struct() {
            if let FieldsShape::Arbitrary { ref offsets, .. } = layout.fields {
                let variant = adt.non_enum_variant();
                for (i, fd) in variant.fields.iter_enumerated() {
                    let fty = fd.ty(tcx, args);
                    let off = base + offsets[i].bytes() as i64;
                    tree_into(tcx, fty, off, depth, out);
                }
                return;
            }
        }
    }

    // Leaf.
    let (is_ptr, is_scalar) = ty_flavor(tcx, ty);
    out.push(AccessNode {
        offset: base,
        size: layout.size.bytes() as u32,
        is_ptr,
        is_scalar,
        children: Vec::new(),
    });
}

pub struct LayoutCallbacks {
    pub filter: Option<String>,
    pub results: Vec<StructLayout>,
}

/// Second callback variant: collects a nested `AccessNode` tree for one
/// struct rather than a flat field list.
pub struct TreeCallbacks {
    pub filter: String,
    pub max_depth: u32,
    pub result: Option<Vec<AccessNode>>,
}

impl rustc_driver::Callbacks for TreeCallbacks {
    fn after_analysis<'tcx>(
        &mut self,
        _compiler: &rustc_interface::interface::Compiler,
        tcx: TyCtxt<'tcx>,
    ) -> Compilation {
        for item_id in tcx.hir_crate_items(()).free_items() {
            let item = tcx.hir().item(item_id);
            if matches!(item.kind, ItemKind::Struct(..)) { let ident = item.ident;
                if ident.name.as_str() != self.filter.as_str() { continue; }
                let def_id = item.owner_id.def_id.to_def_id();
                let ty = tcx.type_of(def_id).instantiate_identity();
                let mut nodes = Vec::new();
                tree_into(tcx, ty, 0, self.max_depth, &mut nodes);
                sort_tree(&mut nodes);
                self.result = Some(nodes);
                break;
            }
        }
        Compilation::Stop
    }
}

impl rustc_driver::Callbacks for LayoutCallbacks {
    fn after_analysis<'tcx>(
        &mut self,
        _compiler: &rustc_interface::interface::Compiler,
        tcx: TyCtxt<'tcx>,
    ) -> Compilation {
        for item_id in tcx.hir_crate_items(()).free_items() {
            let item = tcx.hir().item(item_id);
            if matches!(item.kind, ItemKind::Struct(..)) { let ident = item.ident;
                let name = ident.name.as_str();
                if let Some(ref f) = self.filter {
                    if name != f.as_str() {
                        continue;
                    }
                }
                let def_id = item.owner_id.def_id.to_def_id();
                let ty = tcx.type_of(def_id).instantiate_identity();
                if query_layout(tcx, ty).is_ok() {
                    self.results.push(flatten_struct(tcx, ty, name));
                }
            }
        }
        Compilation::Stop
    }
}

pub fn compute_layouts(
    source: &str,
    target: Option<&str>,
    filter: Option<&str>,
) -> Result<Vec<StructLayout>, String> {
    compute_layouts_with_externs(source, target, filter, &[])
}

// Generic-param substitutions for monomorphized probes. TODO make generic
const SUBS: [(&str, fn(TyCtxt<'_>) -> Ty<'_>); 6] = [
    ("U64", |tcx| tcx.types.u64),
    ("U32", |tcx| tcx.types.u32),
    ("U16", |tcx| tcx.types.u16),
    ("U8", |tcx| tcx.types.u8),
    ("Bool", |tcx| tcx.types.bool),
    ("PtrU8", |tcx| Ty::new_ptr(tcx, tcx.types.u8, ty::Mutability::Not)),
];

struct CatalogCallbacks {
    results: Vec<StructLayout>,
}

impl rustc_driver::Callbacks for CatalogCallbacks {
    fn after_analysis<'tcx>(
        &mut self,
        _c: &rustc_interface::interface::Compiler,
        tcx: TyCtxt<'tcx>,
    ) -> Compilation {
        let mut queue: Vec<DefId> = Vec::new();
        let mut seen: std::collections::HashSet<DefId> = Default::default();
        for &cnum in tcx.crates(()).iter() {
            if tcx.extern_crate(cnum).is_none_or(|e| !e.is_direct()) {
                continue;
            }
            queue.push(cnum.as_def_id());
        }
        while let Some(m) = queue.pop() {
            for child in tcx.module_children(m).iter() {
                if !child.vis.is_public() {
                    continue;
                }
                let Res::Def(kind, did) = child.res else { continue };
                // Skip doc(hidden) reexports.
                if tcx.is_doc_hidden(did) {
                    continue;
                }
                if !seen.insert(did) {
                    continue;
                }
                match kind {
                    DefKind::Mod => queue.push(did),
                    DefKind::Struct | DefKind::Enum | DefKind::Union => {
                        emit_for_def(tcx, did, &mut self.results);
                    }
                    _ => {}
                }
            }
        }
        Compilation::Stop
    }
}

fn emit_for_def<'tcx>(tcx: TyCtxt<'tcx>, did: DefId, out: &mut Vec<StructLayout>) {
    let gens = tcx.generics_of(did);
    if gens
        .own_params
        .iter()
        .any(|p| matches!(p.kind, GenericParamDefKind::Const { .. }))
    {
        return;
    }
    let has_ty = gens
        .own_params
        .iter()
        .any(|p| matches!(p.kind, GenericParamDefKind::Type { .. }));
    let path = tcx.def_path_str(did);

    let params_behind_ptr_only = has_ty && params_all_behind_ptr(tcx, did);
    let subs: &[(&str, fn(TyCtxt<'_>) -> Ty<'_>)] = if has_ty && !params_behind_ptr_only {
        &SUBS
    } else {
        &SUBS[..1]
    };

    // Monomorphize, then dedup by byte-identical flat structure.
    let mut candidates: Vec<(String, StructLayout)> = Vec::new();
    for (suffix, mk_sub) in subs {
        let sub = mk_sub(tcx);
        let args = ty::GenericArgs::for_item(tcx, did, |p, _| -> GenericArg<'tcx> {
            match p.kind {
                GenericParamDefKind::Lifetime => tcx.lifetimes.re_static.into(),
                GenericParamDefKind::Type { .. } => sub.into(),
                GenericParamDefKind::Const { .. } => unreachable!(),
            }
        });
        let ty = tcx.type_of(did).instantiate(tcx, args);
        if query_layout(tcx, ty).is_err() {
            continue;
        }
        candidates.push((suffix.to_string(), flatten_struct(tcx, ty, &path)));
        if !has_ty || params_behind_ptr_only {
            break;
        }
    }

    if candidates.is_empty() {
        return;
    }
    if !has_ty || params_behind_ptr_only || candidates.len() == 1 {
        let (_, mut layout) = candidates.remove(0);
        layout.name = path;
        out.push(layout);
        return;
    }
    let mut by_fp: std::collections::BTreeMap<String, (String, StructLayout)> =
        Default::default();
    for (suffix, layout) in candidates {
        let fp = layout_fingerprint(&layout);
        by_fp.entry(fp).or_insert_with(|| (suffix, layout));
    }
    if by_fp.len() == 1 {
        let (_, (_, mut layout)) = by_fp.into_iter().next().unwrap();
        layout.name = path;
        out.push(layout);
    } else {
        for (_, (suffix, mut layout)) in by_fp {
            layout.name = format!("{path}_G{suffix}");
            out.push(layout);
        }
    }
}

fn layout_fingerprint(sl: &StructLayout) -> String {
    let mut s = format!("{}|{}|", sl.size, sl.align);
    for f in &sl.flat {
        s.push_str(&format!("{}:{}:{}|", f.offset, f.size, f.ty_desc));
    }
    s
}

/// True when no type param of `did` is carried by value (layout is T-invariant).
fn params_all_behind_ptr<'tcx>(tcx: TyCtxt<'tcx>, did: DefId) -> bool {
    let ty = tcx.type_of(did).instantiate_identity();
    let ty::Adt(adt, args) = ty.kind() else {
        return false;
    };
    for variant in adt.variants() {
        for field in &variant.fields {
            let fty = field.ty(tcx, args);
            if ty_contains_param_by_value(tcx, fty) {
                return false;
            }
        }
    }
    true
}

fn ty_contains_param_by_value<'tcx>(tcx: TyCtxt<'tcx>, ty: Ty<'tcx>) -> bool {
    match ty.kind() {
        ty::Param(_) => true,
        ty::Ref(..) | ty::RawPtr(..) => false,
        ty::Adt(adt, args) => {
            if tcx.is_lang_item(adt.did(), rustc_hir::LangItem::PhantomData) {
                return false;
            }
            for v in adt.variants() {
                for f in &v.fields {
                    let fty = f.ty(tcx, args);
                    if ty_contains_param_by_value(tcx, fty) {
                        return true;
                    }
                }
            }
            false
        }
        ty::Array(inner, _) | ty::Slice(inner) => ty_contains_param_by_value(tcx, *inner),
        ty::Tuple(elems) => elems.iter().any(|e| ty_contains_param_by_value(tcx, e)),
        _ => false,
    }
}

/// Enumerate + layout every pub ADT reachable via `--extern`ed crates.
pub fn dep_catalog(
    target: Option<&str>,
    externs: &[(String, String)],
    extra_args: &[String],
) -> Result<Vec<StructLayout>, String> {
    let mut src = String::from("#![allow(warnings)]\n");
    let mut seen: std::collections::BTreeSet<&str> = Default::default();
    for (name, _) in externs {
        if seen.insert(name.as_str()) {
            src.push_str(&format!("extern crate {name};\n"));
        }
    }
    let tmp = nacre_tmp_path("nacre_catalog");
    std::fs::write(&tmp, src).map_err(|e| e.to_string())?;

    let mut args = vec![
        "nacre".to_string(),
        tmp.to_str().unwrap().to_string(),
        "--edition".to_string(),
        "2021".to_string(),
        "--crate-type".to_string(),
        "lib".to_string(),
        "-Awarnings".to_string(),
    ];
    if let Some(t) = target {
        args.push("--target".to_string());
        args.push(t.to_string());
    }
    for (name, path) in externs {
        args.push("--extern".to_string());
        args.push(format!("{name}={path}"));
    }
    args.extend(extra_args.iter().cloned());

    let mut cb = CatalogCallbacks { results: Vec::new() };
    let run = std::panic::AssertUnwindSafe(|| {
        let _ = rustc_driver::RunCompiler::new(&args, &mut cb).run();
    });
    let res = std::panic::catch_unwind(run);
    let _ = std::fs::remove_file(&tmp);
    if res.is_err() {
        return Err("rustc driver aborted during catalog enumeration".to_string());
    }
    Ok(cb.results)
}

/// Compile `source` with `--extern` entries so it can `extern crate ...;`.
pub fn compute_layouts_with_externs(
    source: &str,
    target: Option<&str>,
    filter: Option<&str>,
    externs: &[(String, String)],
) -> Result<Vec<StructLayout>, String> {
    compute_layouts_full(source, target, filter, externs, &[])
}

pub fn compute_layouts_full(
    source: &str,
    target: Option<&str>,
    filter: Option<&str>,
    externs: &[(String, String)],
    extra_args: &[String],
) -> Result<Vec<StructLayout>, String> {
    let tmp = nacre_tmp_path("nacre");
    std::fs::write(&tmp, source).map_err(|e| e.to_string())?;

    let mut args = vec![
        "nacre".to_string(),
        tmp.to_str().unwrap().to_string(),
        "--edition".to_string(),
        "2021".to_string(),
        "--crate-type".to_string(),
        "lib".to_string(),
        "-Awarnings".to_string(),
    ];
    if let Some(t) = target {
        args.push("--target".to_string());
        args.push(t.to_string());
    }
    for (name, path) in externs {
        args.push("--extern".to_string());
        args.push(format!("{name}={path}"));
    }
    args.extend(extra_args.iter().cloned());

    let mut cb = LayoutCallbacks {
        filter: filter.map(|s| s.to_string()),
        results: Vec::new(),
    };
    // catch_unwind so rustc fatals don't take down the parent process.
    let run = std::panic::AssertUnwindSafe(|| {
        let _ = rustc_driver::RunCompiler::new(&args, &mut cb).run();
    });
    let res = std::panic::catch_unwind(run);
    let _ = std::fs::remove_file(&tmp);
    if res.is_err() {
        return Err("rustc driver aborted (fatal compile error)".to_string());
    }

    Ok(cb.results)
}

/// Walks the ADT graph rooted at `root_struct`, returning one shallow
/// `StructLayout` per reachable ADT. Sub-struct fields keep their full
/// type path in `ty_desc`; consumers (e.g. the `--c` CLI mode) use that
/// to reconstruct nested type definitions instead of the inlined-leaves
/// view from `compute_layouts`. BFS, dedup by type-display, hard-capped
/// to avoid blowing up on stdlib types with deep generic chains.
pub struct NestedCallbacks {
    pub filter: String,
    pub max_types: usize,
    pub results: Vec<StructLayout>,
}

impl rustc_driver::Callbacks for NestedCallbacks {
    fn after_analysis<'tcx>(
        &mut self,
        _c: &rustc_interface::interface::Compiler,
        tcx: TyCtxt<'tcx>,
    ) -> Compilation {
        let mut root_ty: Option<Ty<'tcx>> = None;
        for item_id in tcx.hir_crate_items(()).free_items() {
            let item = tcx.hir().item(item_id);
            if matches!(item.kind, ItemKind::Struct(..)) { let ident = item.ident;
                if ident.name.as_str() == self.filter.as_str() {
                    let did = item.owner_id.def_id.to_def_id();
                    root_ty = Some(tcx.type_of(did).instantiate_identity());
                    break;
                }
            }
        }
        let Some(root) = root_ty else { return Compilation::Stop };

        let mut queue: Vec<(Ty<'tcx>, String)> = vec![(root, self.filter.clone())];
        let mut seen: std::collections::HashSet<String> = Default::default();
        while let Some((ty, name)) = queue.pop() {
            if self.results.len() >= self.max_types {
                break;
            }
            if !seen.insert(name.clone()) {
                continue;
            }
            self.results.push(shallow_struct(tcx, ty, &name));
            // queue every struct-typed field, including ABI-pointer
            // wrappers (NonNull/Unique/Box/Rc/Arc). rustc's DWARF keeps
            // these as DW_TAG_structure_type with a single pointer member,
            // so raw-pointer leaves match binja's view of the binary.
            if let ty::Adt(adt, args) = ty.kind() {
                if adt.is_struct() {
                    let variant = adt.non_enum_variant();
                    for fd in variant.fields.iter() {
                        let fty = fd.ty(tcx, args);
                        if let ty::Adt(inner, _) = fty.kind() {
                            if inner.is_struct() {
                                queue.push((fty, format!("{fty}")));
                            }
                        }
                    }
                }
            }
        }
        Compilation::Stop
    }
}

pub fn compute_layouts_nested(
    source: &str,
    target: Option<&str>,
    root_struct: &str,
) -> Result<Vec<StructLayout>, String> {
    let tmp = nacre_tmp_path("nacre");
    std::fs::write(&tmp, source).map_err(|e| e.to_string())?;

    let mut args = vec![
        "nacre".to_string(),
        tmp.to_str().unwrap().to_string(),
        "--edition".to_string(),
        "2021".to_string(),
        "--crate-type".to_string(),
        "lib".to_string(),
        "-Awarnings".to_string(),
    ];
    if let Some(t) = target {
        args.push("--target".to_string());
        args.push(t.to_string());
    }

    let mut cb = NestedCallbacks {
        filter: root_struct.to_string(),
        max_types: 256,
        results: Vec::new(),
    };
    let run = std::panic::AssertUnwindSafe(|| {
        let _ = rustc_driver::RunCompiler::new(&args, &mut cb).run();
    });
    let res = std::panic::catch_unwind(run);
    let _ = std::fs::remove_file(&tmp);
    if res.is_err() {
        return Err("rustc driver aborted (fatal compile error)".to_string());
    }
    if cb.results.is_empty() {
        return Err(format!("struct `{root_struct}` not found"));
    }
    Ok(cb.results)
}

/// Same pipeline as `compute_layouts` but emits a nested `AccessNode` tree
/// for one struct, matching the shape produced by exoskeleton from binaries.
pub fn compute_access_tree(
    source: &str,
    struct_name: &str,
    target: Option<&str>,
    max_depth: u32,
) -> Result<Vec<AccessNode>, String> {
    let tmp = nacre_tmp_path("nacre");
    std::fs::write(&tmp, source).map_err(|e| e.to_string())?;

    let mut args = vec![
        "nacre".to_string(),
        tmp.to_str().unwrap().to_string(),
        "--edition".to_string(), "2021".to_string(),
        "--crate-type".to_string(), "lib".to_string(),
        "-Awarnings".to_string(),
    ];
    if let Some(t) = target {
        args.push("--target".to_string());
        args.push(t.to_string());
    }

    let mut cb = TreeCallbacks {
        filter: struct_name.to_string(),
        max_depth,
        result: None,
    };
    let run = std::panic::AssertUnwindSafe(|| {
        let _ = rustc_driver::RunCompiler::new(&args, &mut cb).run();
    });
    let res = std::panic::catch_unwind(run);
    let _ = std::fs::remove_file(&tmp);
    if res.is_err() {
        return Err("rustc driver aborted (fatal compile error)".to_string());
    }
    cb.result.ok_or_else(|| format!("struct `{struct_name}` not found"))
}

pub fn to_json(layouts: &[StructLayout]) -> String {
    let mut s = String::from("[");
    for (i, sl) in layouts.iter().enumerate() {
        if i > 0 {
            s.push(',');
        }
        s.push_str(&format!(
            "\n  {{\"name\":{:?},\"size\":{},\"align\":{},\"fields\":[",
            sl.name, sl.size, sl.align
        ));
        for (j, f) in sl.flat.iter().enumerate() {
            if j > 0 {
                s.push(',');
            }
            s.push_str(&format!(
                "\n    {{\"path\":{:?},\"offset\":{},\"size\":{},\"type\":{:?}}}",
                f.path, f.offset, f.size, f.ty_desc
            ));
        }
        s.push_str("\n  ]}");
    }
    s.push_str("\n]\n");
    s
}

pub fn display_layout(sl: &StructLayout) -> String {
    let mut out = format!("{} (size: {}, align: {})\n", sl.name, sl.size, sl.align);
    let mut pos = 0u64;
    for f in &sl.flat {
        if f.offset > pos {
            out += &format!("  +{:<4} [{} bytes padding]\n", pos, f.offset - pos);
        }
        out += &format!(
            "  +{:<4} {}: {} ({} {})\n",
            f.offset,
            f.path,
            f.ty_desc,
            f.size,
            if f.size == 1 { "byte" } else { "bytes" },
        );
        pos = f.offset + f.size;
    }
    if pos < sl.size {
        out += &format!("  +{:<4} [{} bytes padding]\n", pos, sl.size - pos);
    }
    out
}

/// A single store the probe emitted, annotated with the canonical flavor
/// from the source struct. Dict shape mirrors roe::AccessNode so that
/// `roe.compatible(binary_obs, nacre_ground_truth)` just works.
#[derive(IntoPyObject)]
pub struct Store {
    pub offset: u64,
    pub size: u64,
    pub is_ptr: bool,
    pub is_scalar: bool,
    /// Always 1 (one store per probe write); mirrors AccessNode.writes.
    pub writes: u32,
}

// Break a (offset, size) span into natively-aligned power-of-two chunks
// so we always emit a single store per chunk (`u8`/`u16`/`u32`/`u64`).
fn chunk_writes(offset: u64, size: u64) -> Vec<(u64, u64)> {
    let mut out = Vec::new();
    let mut cur = offset;
    let end = offset.saturating_add(size);
    while cur < end {
        let remaining = end - cur;
        let align_cap = 1u64 << cur.trailing_zeros().min(3);
        let w = remaining
            .next_power_of_two()
            .min(8)
            .min(align_cap);
        let w = if remaining < w { 1 << (63 - remaining.leading_zeros()) } else { w };
        out.push((cur, w));
        cur += w;
    }
    out
}

fn generate_probe(source: &str, struct_name: &str, fields: &[FlatField]) -> String {
    let mut out = format!("#![allow(warnings)]\n{source}\n");
    out.push_str(&format!("#[inline(never)]\npub fn _rl_probe_(p: &mut {struct_name}) {{\n"));
    out.push_str("    let base = p as *mut _ as *mut u8;\n");
    out.push_str("    unsafe {\n");
    let mut val: u64 = 1;
    for f in fields {
        for (off, w) in chunk_writes(f.offset, f.size) {
            let ty = match w { 1 => "u8", 2 => "u16", 4 => "u32", 8 => "u64", _ => continue };
            out.push_str(&format!(
                "        core::ptr::write(base.add({off}) as *mut {ty}, {val} as {ty});\n"
            ));
            val = val.wrapping_add(1);
        }
    }
    out.push_str("    }\n}\n");
    out
}

// rustc_codegen_llvm is uber unstable, atleast we get a consistent api like this
pub fn probe_stores(
    source: &str,
    struct_name: &str,
    target: Option<&str>,
    opt_level: u8,
) -> Result<Vec<Store>, String> {
    let layouts = compute_layouts(source, target, Some(struct_name))?;
    let layout = layouts.into_iter().next().ok_or("struct not found")?;
    let probe_src = generate_probe(source, struct_name, &layout.flat);

    let tmp_rs = nacre_tmp_path("rl_probe");
    let tmp_asm = tmp_rs.with_extension("s");
    std::fs::write(&tmp_rs, &probe_src).map_err(|e| e.to_string())?;

    let mut args = vec![
        "nacre".to_string(),
        tmp_rs.to_str().unwrap().to_string(),
        "--edition".to_string(), "2021".to_string(),
        "--crate-type".to_string(), "lib".to_string(),
        "-Awarnings".to_string(),
        "-C".to_string(), format!("opt-level={opt_level}"),
        "--emit".to_string(), format!("asm={}", tmp_asm.to_str().unwrap()),
    ];
    if let Some(t) = target {
        args.push("--target".to_string());
        args.push(t.to_string());
    }

    struct Noop;
    impl rustc_driver::Callbacks for Noop {}
    let run = std::panic::AssertUnwindSafe(|| {
        let _ = rustc_driver::RunCompiler::new(&args, &mut Noop).run();
    });
    let res = std::panic::catch_unwind(run);
    let _ = std::fs::remove_file(&tmp_rs);
    if res.is_err() {
        let _ = std::fs::remove_file(&tmp_asm);
        return Err("rustc driver aborted (fatal compile error)".to_string());
    }
    let asm = std::fs::read_to_string(&tmp_asm).map_err(|e| e.to_string())?;
    let _ = std::fs::remove_file(&tmp_asm);

    let mut stores = parse_x86_stores(&asm, "_rl_probe_");
    annotate_stores(&mut stores, &layout.flat);
    Ok(stores)
}

fn parse_x86_stores(asm: &str, fn_name: &str) -> Vec<Store> {
    let mut stores = Vec::new();
    let mut inside = false;

    for line in asm.lines() {
        let t = line.trim();
        if t.contains(fn_name) && t.ends_with(':') && !t.starts_with('.') {
            inside = true;
            continue;
        }
        if !inside || t.is_empty() || t.starts_with('.') || t.starts_with('#') {
            continue;
        }
        if t == "retq" || t == "ret" {
            break;
        }
        if let Some(s) = parse_one_store(t) {
            stores.push(s);
        }
    }
    stores.sort_by_key(|s| s.offset);
    stores
}

fn parse_one_store(instr: &str) -> Option<Store> {
    let (mnemonic, rest) = instr.split_once(|c: char| c == ' ' || c == '\t')?;
    let size: u64 = match mnemonic {
        "movb" => 1, "movw" => 2, "movl" => 4,
        "movq" | "movabsq" => 8,
        "movups" | "movaps" => 16,
        _ => return None,
    };
    let dest = rest.rsplit(',').next()?.trim();
    if !dest.contains("(%rdi)") || dest.starts_with('%') {
        return None;
    }
    let offset = if dest == "(%rdi)" {
        0
    } else {
        dest.strip_suffix("(%rdi)")?.parse::<i64>().ok()? as u64
    };
    Some(Store { offset, size, is_ptr: false, is_scalar: false, writes: 1 })
}

fn annotate_stores(stores: &mut [Store], flat: &[FlatField]) {
    for st in stores {
        let end = st.offset + st.size;
        if let Some(f) = flat.iter().find(|f| f.offset <= st.offset && st.offset + st.size <= f.offset + f.size) {
            st.is_ptr = f.is_ptr;
            st.is_scalar = f.is_scalar && !f.is_ptr;
        }
        let _ = end;
    }
}

#[pyfunction]
#[pyo3(signature = (source, target=None, struct_name=None))]
fn compute(source: &str, target: Option<&str>, struct_name: Option<&str>) -> PyResult<Vec<StructLayout>> {
    compute_layouts(source, target, struct_name)
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e))
}

/// Compile a probe function for any Rust type and return the surviving
/// stores from x86-64 assembly. Same `ty`/`prelude` surface as `layout()`.
///
/// Examples:
///   `probe("Vec<u8>")`
///   `probe("State", prelude="struct State { jt: u64, tc: u8 }")`
///   `probe("HashMap<String, u64>", prelude="use std::collections::HashMap;")`
#[pyfunction]
#[pyo3(signature = (ty, prelude=None, target=None, opt_level=3))]
fn probe(
    ty: &str,
    prelude: Option<&str>,
    target: Option<&str>,
    opt_level: u8,
) -> PyResult<Vec<Store>> {
    let prelude = prelude.unwrap_or("");
    let ty_with_lt = add_static_lifetimes(ty);
    let source = format!("#![allow(warnings)]\n{prelude}\npub struct __NacreWrap(pub {ty_with_lt});\n");
    probe_stores(&source, "__NacreWrap", target, opt_level)
        .map_err(pyo3::exceptions::PyRuntimeError::new_err)
}

/// Rustc release string this nacre build was linked against.
/// Layouts are only authoritative for binaries compiled by this exact version.
#[pyfunction]
fn rustc_version() -> &'static str {
    RUSTC_VERSION
}

/// Add `'static` to any bare `&` reference (i.e. not already followed by a
/// lifetime). Lets ad-hoc type strings like `&Vec<u8>` typecheck inside the
/// synthetic wrapper struct.
fn add_static_lifetimes(ty: &str) -> String {
    let mut out = String::with_capacity(ty.len() + 8);
    let bytes = ty.as_bytes();
    let mut i = 0;
    while i < bytes.len() {
        let c = bytes[i];
        out.push(c as char);
        i += 1;
        if c == b'&' {
            // Skip whitespace.
            let mut j = i;
            while j < bytes.len() && bytes[j].is_ascii_whitespace() { j += 1; }
            // Optional `mut `.
            if bytes[j..].starts_with(b"mut ") {
                j += 4;
                while j < bytes.len() && bytes[j].is_ascii_whitespace() { j += 1; }
            }
            // Already a lifetime? Skip.
            if !bytes[j..].starts_with(b"'") {
                out.push_str("'static ");
            }
        }
    }
    out
}

/// Layout tree for any Rust type expression. Works for primitives, generics,
/// references, and user-defined structs (pass the definition in `prelude`).
///
/// Examples:
///   `layout("Vec<u8>")`
///   `layout("&Vec<char>")`
///   `layout("State", prelude="struct State { jt: u64, tc: u8 }")`
///   `layout("HashMap<String, u64>", prelude="use std::collections::HashMap;")`
#[pyfunction]
#[pyo3(signature = (ty, prelude=None, target=None, max_depth=2))]
fn layout(
    ty: &str,
    prelude: Option<&str>,
    target: Option<&str>,
    max_depth: u32,
) -> PyResult<Vec<AccessNode>> {
    let prelude = prelude.unwrap_or("");
    let ty_with_lt = add_static_lifetimes(ty);
    let source = format!("#![allow(warnings)]\n{prelude}\npub struct __NacreWrap(pub {ty_with_lt});\n");
    compute_access_tree(&source, "__NacreWrap", target, max_depth)
        .map_err(pyo3::exceptions::PyRuntimeError::new_err)
}

// Given a `fn(...) -> ...` declaration, ask rustc for its FnAbi and emit
// per-arg + return-slot info: the PassMode rustc chose, the SysV-x64
// registers it lands in, and the recursive layout of each slot. PassMode
// is authoritative (handles niches, tuple returns, sret); reg mapping
// follows SysV-x64: int/ptr in rdi/rsi/rdx/rcx/r8/r9, floats in xmm0..7.

#[derive(IntoPyObject, Clone)]
pub struct SignatureSlot {
    pub ty: String,
    pub size: u64,
    pub align: u64,
    /// "ignore" | "direct" | "pair" | "cast" | "indirect"
    pub pass_mode: String,
    /// SysV-x64 registers carrying this slot, in order. Empty if on-stack.
    pub regs: Vec<String>,
    pub on_stack: bool,
    pub indirect: bool,
    pub access_tree: Vec<AccessNode>,
}

#[derive(IntoPyObject, Clone)]
pub struct SignatureLayout {
    pub args: Vec<SignatureSlot>,
    pub ret: SignatureSlot,
    /// True when the return is so big rustc passes a hidden out-pointer
    /// in rdi (the standard sret pattern). Subsequent args shift by one.
    pub sret: bool,
}

/// Per-platform consumption state used while walking FnAbi.args in order.
struct RegPool {
    int_idx: usize,
    xmm_idx: usize,
}

impl RegPool {
    fn new() -> Self { Self { int_idx: 0, xmm_idx: 0 } }
    fn take_int(&mut self) -> Option<&'static str> {
        const INT: [&str; 6] = ["rdi", "rsi", "rdx", "rcx", "r8", "r9"];
        let r = INT.get(self.int_idx).copied();
        self.int_idx += 1;
        r
    }
    fn take_xmm(&mut self) -> Option<String> {
        if self.xmm_idx >= 8 { return None; }
        let r = format!("xmm{}", self.xmm_idx);
        self.xmm_idx += 1;
        Some(r)
    }
}

/// Pick the SysV-x64 register class for a single primitive scalar.
fn reg_kind_for_primitive(p: Primitive) -> &'static str {
    match p {
        Primitive::Int(..) | Primitive::Pointer(..) => "int",
        Primitive::Float(..) => "xmm",
    }
}

/// Assign registers for one arg (or return) given its layout + PassMode.
fn assign_regs<'tcx>(
    pool: &mut RegPool,
    layout: &ty::layout::TyAndLayout<'tcx>,
    mode: &PassMode,
) -> (Vec<String>, bool) {
    let mut regs = Vec::new();
    let mut on_stack = false;
    match mode {
        PassMode::Ignore => {}
        PassMode::Direct(_) => {
            let kind = match layout.backend_repr {
                BackendRepr::Scalar(s) => reg_kind_for_primitive(s.primitive()),
                _ => "int",
            };
            let r = if kind == "xmm" {
                pool.take_xmm()
            } else {
                pool.take_int().map(String::from)
            };
            match r {
                Some(r) => regs.push(r),
                None => on_stack = true,
            }
        }
        PassMode::Pair(_, _) => {
            // ScalarPair: both halves classified independently.
            if let BackendRepr::ScalarPair(a, b) = layout.backend_repr {
                for s in [a, b] {
                    let kind = reg_kind_for_primitive(s.primitive());
                    let r = if kind == "xmm" {
                        pool.take_xmm()
                    } else {
                        pool.take_int().map(String::from)
                    };
                    match r {
                        Some(r) => regs.push(r),
                        None => { on_stack = true; break; }
                    }
                }
            } else {
                // Defensive: shouldn't happen for PassMode::Pair, but degrade
                // gracefully to "two int regs".
                for _ in 0..2 {
                    match pool.take_int() {
                        Some(r) => regs.push(r.into()),
                        None => { on_stack = true; break; }
                    }
                }
            }
        }
        PassMode::Cast { .. } => {
            // Cast ABIs (split into a sequence of Reg units). Approximate as
            // "fill int regs until the layout's size is covered". This handles
            // 8-32 byte cast targets correctly on SysV-x64.
            let mut bytes = layout.size.bytes();
            while bytes > 0 {
                match pool.take_int() {
                    Some(r) => regs.push(r.into()),
                    None => { on_stack = true; break; }
                }
                bytes = bytes.saturating_sub(8);
            }
        }
        PassMode::Indirect { on_stack: stk, .. } => {
            if *stk {
                on_stack = true;
            } else {
                // By-pointer: one int reg holds the pointer.
                match pool.take_int() {
                    Some(r) => regs.push(r.into()),
                    None => on_stack = true,
                }
            }
        }
    }
    (regs, on_stack)
}

fn pass_mode_str(mode: &PassMode) -> &'static str {
    match mode {
        PassMode::Ignore => "ignore",
        PassMode::Direct(_) => "direct",
        PassMode::Pair(_, _) => "pair",
        PassMode::Cast { .. } => "cast",
        PassMode::Indirect { .. } => "indirect",
    }
}

fn build_slot<'tcx>(
    tcx: TyCtxt<'tcx>,
    layout: &ty::layout::TyAndLayout<'tcx>,
    mode: &PassMode,
    pool: &mut RegPool,
    max_depth: u32,
) -> SignatureSlot {
    let (regs, on_stack) = assign_regs(pool, layout, mode);
    let indirect = matches!(mode, PassMode::Indirect { .. });
    let mut access = Vec::new();
    // For an Indirect arg the value lives behind a pointer; the access tree
    // we want is the pointee's layout. `tree_into` already handles pointer
    // dereferencing when given depth > 0, so feed it the layout type.
    tree_into(tcx, layout.ty, 0, max_depth, &mut access);
    sort_tree(&mut access);
    SignatureSlot {
        ty: format!("{}", layout.ty),
        size: layout.size.bytes(),
        align: layout.align.abi.bytes(),
        pass_mode: pass_mode_str(mode).to_string(),
        regs,
        on_stack,
        indirect,
        access_tree: access,
    }
}

fn classify_signature<'tcx>(
    tcx: TyCtxt<'tcx>,
    fn_did: DefId,
    max_depth: u32,
) -> Result<SignatureLayout, String> {
    let instance = Instance::mono(tcx, fn_did);
    let fn_abi = tcx
        .fn_abi_of_instance(TypingEnv::fully_monomorphized()
            .as_query_input((instance, ty::List::empty())))
        .map_err(|e| format!("fn_abi_of_instance failed: {e:?}"))?;

    let mut pool = RegPool::new();
    // Sret consumes rdi before any user arg lands on a register.
    let sret = matches!(fn_abi.ret.mode, PassMode::Indirect { .. });
    if sret {
        let _ = pool.take_int();
    }

    let mut args = Vec::with_capacity(fn_abi.args.len());
    for arg in fn_abi.args.iter() {
        args.push(build_slot(tcx, &arg.layout, &arg.mode, &mut pool, max_depth));
    }

    // For sret returns the "regs" slot is rdi (the out-pointer) and the
    // access tree is the *pointee* layout - i.e. the structure rustc
    // expects the callee to write through that pointer.
    let mut ret_pool = RegPool::new();
    let ret = if sret {
        let mut s = build_slot(tcx, &fn_abi.ret.layout, &fn_abi.ret.mode, &mut ret_pool, max_depth);
        s.regs = vec!["rdi".to_string()];
        s
    } else {
        // Non-sret returns land in rax (and rdx for ScalarPair, xmm0 for
        // float). Reset pool, then assign.
        let mut s = build_slot(tcx, &fn_abi.ret.layout, &fn_abi.ret.mode, &mut ret_pool, max_depth);
        // Override caller-side regs to return-side names. SysV: rax/rdx
        // for ints, xmm0/xmm1 for floats.
        s.regs = ret_regs_for(&fn_abi.ret.layout, &fn_abi.ret.mode);
        s
    };

    Ok(SignatureLayout { args, ret, sret })
}

fn ret_regs_for<'tcx>(
    layout: &ty::layout::TyAndLayout<'tcx>,
    mode: &PassMode,
) -> Vec<String> {
    match mode {
        PassMode::Ignore => Vec::new(),
        PassMode::Direct(_) => match layout.backend_repr {
            BackendRepr::Scalar(s) => match s.primitive() {
                Primitive::Float(..) => vec!["xmm0".to_string()],
                _ => vec!["rax".to_string()],
            },
            _ => vec!["rax".to_string()],
        },
        PassMode::Pair(_, _) => {
            if let BackendRepr::ScalarPair(a, b) = layout.backend_repr {
                let pick = |s: rustc_abi::Scalar, slot: usize| -> String {
                    match s.primitive() {
                        Primitive::Float(..) => format!("xmm{slot}"),
                        _ => if slot == 0 { "rax".into() } else { "rdx".into() },
                    }
                };
                vec![pick(a, 0), pick(b, 1)]
            } else {
                vec!["rax".into(), "rdx".into()]
            }
        }
        PassMode::Cast { .. } => vec!["rax".into(), "rdx".into()],
        PassMode::Indirect { .. } => Vec::new(), // handled by caller as sret
    }
}

/// rustc_driver Callbacks that classifies the function named `__nacre_sig_probe`.
struct SignatureCallbacks {
    max_depth: u32,
    result: Option<SignatureLayout>,
    error: Option<String>,
}

impl rustc_driver::Callbacks for SignatureCallbacks {
    fn after_analysis<'tcx>(
        &mut self,
        _compiler: &rustc_interface::interface::Compiler,
        tcx: TyCtxt<'tcx>,
    ) -> Compilation {
        for item_id in tcx.hir_crate_items(()).free_items() {
            let item = tcx.hir().item(item_id);
            if matches!(item.kind, ItemKind::Fn(..)) { let ident = item.ident;
                if ident.name.as_str() != "__nacre_sig_probe" { continue; }
                let did = item.owner_id.def_id.to_def_id();
                match classify_signature(tcx, did, self.max_depth) {
                    Ok(s) => self.result = Some(s),
                    Err(e) => self.error = Some(e),
                }
                break;
            }
        }
        Compilation::Stop
    }
}

pub fn compute_signature(
    source: &str,
    target: Option<&str>,
    max_depth: u32,
) -> Result<SignatureLayout, String> {
    let tmp = nacre_tmp_path("nacre_sig");
    std::fs::write(&tmp, source).map_err(|e| e.to_string())?;
    let mut args = vec![
        "nacre".to_string(),
        tmp.to_str().unwrap().to_string(),
        "--edition".to_string(), "2021".to_string(),
        "--crate-type".to_string(), "lib".to_string(),
        "-Awarnings".to_string(),
    ];
    if let Some(t) = target {
        args.push("--target".into());
        args.push(t.into());
    }
    let mut cb = SignatureCallbacks { max_depth, result: None, error: None };
    let run = std::panic::AssertUnwindSafe(|| {
        let _ = rustc_driver::RunCompiler::new(&args, &mut cb).run();
    });
    let res = std::panic::catch_unwind(run);
    let _ = std::fs::remove_file(&tmp);
    if res.is_err() {
        return Err("rustc driver aborted (fatal compile error)".into());
    }
    if let Some(e) = cb.error { return Err(e); }
    cb.result.ok_or_else(|| "no `__nacre_sig_probe` fn found".to_string())
}

/// Full SysV-x64 calling-convention layout for a Rust `fn` declaration:
/// per-arg PassMode + register assignment + recursive layout, plus the
/// return slot (with sret detection). The PassMode comes from rustc's
/// own `fn_abi_of_instance` query, so Rust-ABI subtleties (niches,
/// tuple returns, indirect aggregates) are handled authoritatively.
///
/// `decl` is the parenthesized parameter list and optional return type,
/// e.g. `(a: u64, b: &str) -> i32`. Either a leading `fn` keyword or a
/// fn name is fine; both are stripped before synthesizing the probe.
///
/// Examples:
///   `signature("(a: u64, b: u32) -> i64")`
///   `signature("fn(state: &mut State, req: Req) -> Result<(), E>", prelude="...")`
#[pyfunction]
#[pyo3(signature = (decl, prelude=None, target=None, max_depth=2))]
fn signature(
    decl: &str,
    prelude: Option<&str>,
    target: Option<&str>,
    max_depth: u32,
) -> PyResult<SignatureLayout> {
    let prelude = prelude.unwrap_or("");
    let body = normalize_fn_decl(decl);
    let source = format!(
        "#![allow(warnings)]\n{prelude}\npub fn __nacre_sig_probe{body} {{ unimplemented!() }}\n"
    );
    compute_signature(&source, target, max_depth)
        .map_err(pyo3::exceptions::PyRuntimeError::new_err)
}

/// Strip a leading `fn` keyword and any function name so the caller can
/// pass `(a: T) -> R`, `fn(a: T) -> R`, or `fn name(a: T) -> R`.
fn normalize_fn_decl(decl: &str) -> String {
    let s = decl.trim();
    let s = s.strip_prefix("fn").unwrap_or(s).trim_start();
    // Skip an identifier between `fn` and the `(`.
    let cut = s.find('(').unwrap_or(0);
    s[cut..].to_string()
}

/// Build the catalog of every reachable struct used in `decl`'s
/// signature (args + ret + their transitive struct fields), shallow-
/// per-ADT so `emit_c_nested` has every typedef it needs. Walks
/// through pointer types (refs, raw ptrs, Box/NonNull/Unique/Rc/Arc
/// wrappers) so that, e.g., `&Vec<u8>` still yields the Vec catalog.
fn collect_signature_catalog<'tcx>(
    tcx: TyCtxt<'tcx>,
    abi: &rustc_target::callconv::FnAbi<'tcx, Ty<'tcx>>,
) -> Vec<StructLayout> {
    use std::collections::BTreeSet;
    let mut queue: Vec<Ty<'tcx>> = Vec::new();
    queue.push(abi.ret.layout.ty);
    for a in abi.args.iter() {
        queue.push(a.layout.ty);
    }
    let mut catalog: Vec<StructLayout> = Vec::new();
    let mut emitted: BTreeSet<String> = BTreeSet::new();
    while let Some(ty) = queue.pop() {
        match ty.kind() {
            // Refs / raw ptrs aren't ADTs, so there's no struct to emit;
            // just follow the pointee. Pointer-shaped ADTs (Box, NonNull,
            // Unique, Rc, Arc) are intentionally NOT special-cased here:
            // they're real structs in DWARF and we keep them as such, in
            // sync with compute_layouts_nested + emit_c_nested.
            ty::Ref(_, pointee, _) | ty::RawPtr(pointee, _) => {
                queue.push(*pointee);
                continue;
            }
            ty::Adt(adt, args) if adt.is_struct() => {
                let path = tcx.def_path_str_with_args(adt.did(), args);
                if !emitted.insert(path.clone()) {
                    continue;
                }
                catalog.push(shallow_struct(tcx, ty, &path));
                let variant = adt.non_enum_variant();
                for fd in variant.fields.iter() {
                    queue.push(fd.ty(tcx, args));
                }
            }
            _ => continue,
        }
    }
    catalog
}

/// Probe-driven C struct catalog for any Rust type expression. Same
/// `ty`/`prelude` surface as `layout()`/`probe()`. Output is the C
/// source emitted by the `--c` CLI mode - every reachable ADT is
/// rendered as a `struct <path>` with byte-accurate offsets sourced
/// from rustc's `Layout` query.
///
/// Examples:
///   `c_layout("Vec<u8>")`
///   `c_layout("State", prelude="struct State { a: u64, b: Vec<u8> }")`
#[pyfunction]
#[pyo3(signature = (ty, prelude=None, target=None))]
fn c_layout(
    ty: &str,
    prelude: Option<&str>,
    target: Option<&str>,
) -> PyResult<String> {
    let prelude = prelude.unwrap_or("");
    let ty_with_lt = add_static_lifetimes(ty);
    let source = format!(
        "#![allow(warnings)]\n{prelude}\npub struct __NacreWrap(pub {ty_with_lt});\n"
    );
    let layouts = compute_layouts_nested(&source, target, "__NacreWrap")
        .map_err(pyo3::exceptions::PyRuntimeError::new_err)?;
    // Hide the synthetic wrapper struct from the C output - it exists
    // only so rustc has a top-level item to resolve `ty` against (e.g.
    // bare `Vec<u8>` or `&str`); the user wants to see the real types.
    let trimmed: Vec<StructLayout> = layouts
        .into_iter()
        .filter(|sl| sl.name != "__NacreWrap")
        .collect();
    Ok(emit_c::emit_c_nested(&trimmed))
}

#[derive(IntoPyObject)]
struct CSignature {
    /// C function declaration string, e.g.
    /// `int64_t f(struct std_vec_Vec_u8_* a0, uint32_t a1)`.
    #[pyo3(item("decl"))]
    decl: String,
    /// All struct typedefs referenced by `decl`, deepest-first.
    #[pyo3(item("structs"))]
    structs: String,
}

/// Rust fn declaration -> C declaration + reachable-struct catalog.
/// Drives off the same `fn_abi_of_instance` query as `signature()`:
/// `Indirect` args become `T*`, sret returns get a hidden `T* _ret`
/// first param + `void` return, and pointer-shaped types
/// (`&T`/`*const T`/`Box<T>`/`Rc<T>`/`Unique<T>`/...) collapse to typed
/// `T*` whenever the pointee is recoverable.
///
/// Examples:
///   `c_signature("(a: u64, b: u32) -> i64")`
///   `c_signature("(s: &Vec<u8>) -> Option<usize>")`
#[pyfunction]
#[pyo3(signature = (decl, prelude=None, target=None))]
fn c_signature(
    decl: &str,
    prelude: Option<&str>,
    target: Option<&str>,
) -> PyResult<CSignature> {
    let prelude = prelude.unwrap_or("");
    let body = normalize_fn_decl(decl);
    let source = format!(
        "#![allow(warnings)]\n{prelude}\npub fn __nacre_sig_probe{body} {{ unimplemented!() }}\n"
    );
    compute_c_signature(&source, target)
        .map_err(pyo3::exceptions::PyRuntimeError::new_err)
}

struct CSigCallbacks {
    result: Option<CSignature>,
    error: Option<String>,
}

impl rustc_driver::Callbacks for CSigCallbacks {
    fn after_analysis<'tcx>(
        &mut self,
        _compiler: &rustc_interface::interface::Compiler,
        tcx: TyCtxt<'tcx>,
    ) -> Compilation {
        for item_id in tcx.hir_crate_items(()).free_items() {
            let item = tcx.hir().item(item_id);
            if matches!(item.kind, ItemKind::Fn(..)) {
                let ident = item.ident;
                if ident.name.as_str() != "__nacre_sig_probe" {
                    continue;
                }
                let did = item.owner_id.def_id.to_def_id();
                let instance = Instance::mono(tcx, did);
                let fn_abi = match tcx.fn_abi_of_instance(
                    TypingEnv::fully_monomorphized()
                        .as_query_input((instance, ty::List::empty())),
                ) {
                    Ok(a) => a,
                    Err(e) => {
                        self.error = Some(format!("fn_abi_of_instance failed: {e:?}"));
                        return Compilation::Stop;
                    }
                };
                // Param names live in HIR's body, not in FnAbi (codegen
                // doesn't need them). Pull them out so the C decl reads
                // `(struct LookupMap* this, ...)` instead of `(... a0, ...)`.
                let arg_names: Vec<String> = collect_param_names(tcx, did);
                // emit_c_fn_decl needs a sink for ADT paths it touches,
                // even though collect_signature_catalog independently
                // walks the FnAbi for the catalog (refs/raw ptrs need
                // pointee following that the decl renderer doesn't do).
                let mut sink: Vec<String> = Vec::new();
                let decl_str = emit_c::emit_c_fn_decl(tcx, fn_abi, "f", &arg_names, &mut sink);
                let catalog = collect_signature_catalog(tcx, fn_abi);
                let structs = emit_c::emit_c_nested(&catalog);
                self.result = Some(CSignature { decl: decl_str, structs });
                break;
            }
        }
        Compilation::Stop
    }
}

/// Extract source-level parameter names for `fn_did` from HIR. Returns
/// the empty string for any param whose pattern isn't a simple binding
/// (e.g. tuple or struct destructuring) - emit_c_fn_decl falls back to
/// `a{i}` in that case.
fn collect_param_names<'tcx>(tcx: TyCtxt<'tcx>, fn_did: DefId) -> Vec<String> {
    let Some(local) = fn_did.as_local() else { return Vec::new() };
    let body = tcx.hir().body_owned_by(local);
    body.params
        .iter()
        .map(|p| match p.pat.kind {
            rustc_hir::PatKind::Binding(_, _, ident, _) => ident.name.as_str().to_string(),
            _ => String::new(),
        })
        .collect()
}

fn compute_c_signature(source: &str, target: Option<&str>) -> Result<CSignature, String> {
    let tmp = nacre_tmp_path("nacre_csig");
    std::fs::write(&tmp, source).map_err(|e| e.to_string())?;
    let mut args = vec![
        "nacre".to_string(),
        tmp.to_str().unwrap().to_string(),
        "--edition".to_string(),
        "2021".to_string(),
        "--crate-type".to_string(),
        "lib".to_string(),
        "-Awarnings".to_string(),
    ];
    if let Some(t) = target {
        args.push("--target".into());
        args.push(t.into());
    }
    let mut cb = CSigCallbacks { result: None, error: None };
    let run = std::panic::AssertUnwindSafe(|| {
        let _ = rustc_driver::RunCompiler::new(&args, &mut cb).run();
    });
    let res = std::panic::catch_unwind(run);
    let _ = std::fs::remove_file(&tmp);
    if res.is_err() {
        return Err("rustc driver aborted (fatal compile error)".into());
    }
    if let Some(e) = cb.error {
        return Err(e);
    }
    cb.result.ok_or_else(|| "no `__nacre_sig_probe` fn found".to_string())
}

#[pymodule]
fn nacre(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(compute, m)?)?;
    m.add_function(wrap_pyfunction!(probe, m)?)?;
    m.add_function(wrap_pyfunction!(layout, m)?)?;
    m.add_function(wrap_pyfunction!(signature, m)?)?;
    m.add_function(wrap_pyfunction!(c_layout, m)?)?;
    m.add_function(wrap_pyfunction!(c_signature, m)?)?;
    m.add_function(wrap_pyfunction!(rustc_version, m)?)?;
    m.add("RUSTC_VERSION", RUSTC_VERSION)?;
    Ok(())
}
