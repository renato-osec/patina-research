//! Run rustc_driver and collect one [`FlowGraph`] per function body.

use std::collections::HashSet;

use rustc_driver::Compilation;
use rustc_hir::def::DefKind;
use rustc_hir::def_id::LocalDefId;
use rustc_hir::ItemKind;
use rustc_middle::ty::{Instance, TyCtxt, TypingEnv};

use crate::flow::{EdgeKind, FlowGraph};
use crate::lower::{lower_body, static_callees};

pub struct LowerCallbacks {
    pub graphs: Vec<FlowGraph>,
}

impl rustc_driver::Callbacks for LowerCallbacks {
    fn after_analysis<'tcx>(
        &mut self,
        _compiler: &rustc_interface::interface::Compiler,
        tcx: TyCtxt<'tcx>,
    ) -> Compilation {
        for def_id in tcx.hir().body_owners() {
            let kind = tcx.def_kind(def_id);
            if !matches!(kind, DefKind::Fn | DefKind::AssocFn | DefKind::Closure) {
                continue;
            }
            if !tcx.is_mir_available(def_id) {
                continue;
            }
            let g = lower_body(tcx, def_id);
            self.graphs.push(g);
        }
        Compilation::Stop
    }
}

/// Depth-bounded BFS rooted at a fn matched by `root_fn` (HIR ident).
/// Falls back to a flat sweep over `body_owners()` when `root_fn` is
/// empty — same surface as `LowerCallbacks`. Callees are followed via
/// `Instance::resolve` over `(def_id, generic_args)` extracted from
/// each Call terminator, matching what rustc itself does for codegen
/// monomorphisation. Indirect / dyn / unresolvable trait calls return
/// `None` from `resolve` and we leave them as opaque labels (matches
/// `callee_label`'s `<indirect>` rendering).
pub struct DepthCallbacks {
    pub root_fn: String,
    pub depth: u32,
    pub graphs: Vec<FlowGraph>,
}

impl rustc_driver::Callbacks for DepthCallbacks {
    fn after_analysis<'tcx>(
        &mut self,
        _compiler: &rustc_interface::interface::Compiler,
        tcx: TyCtxt<'tcx>,
    ) -> Compilation {
        let root: Option<LocalDefId> = tcx
            .hir_crate_items(())
            .free_items()
            .filter_map(|id| {
                let item = tcx.hir().item(id);
                let name = item.ident.name.as_str();
                let is_fn = matches!(item.kind, ItemKind::Fn(..));
                (is_fn && name == self.root_fn).then_some(item.owner_id.def_id)
            })
            .next();
        let Some(root) = root else { return Compilation::Stop };

        // Collect every reached body, then merge into a single
        // FlowGraph stitched at call boundaries. Cross-fn rendezvous
        // slots (`callee#i`, `<ret:callee>`) align by name in
        // FlowGraph::absorb so caller's edges into them connect with
        // the wires we add below into the callee's interior.
        let mut seen: HashSet<LocalDefId> = HashSet::new();
        let mut order: Vec<(LocalDefId, FlowGraph)> = Vec::new();
        let mut queue: Vec<(LocalDefId, u32)> = vec![(root, self.depth)];
        let env = TypingEnv::fully_monomorphized();
        while let Some((did, d)) = queue.pop() {
            if !seen.insert(did) {
                continue;
            }
            if !tcx.is_mir_available(did) {
                continue;
            }
            order.push((did, lower_body(tcx, did)));
            if d == 0 {
                continue;
            }
            for (callee_def, args) in static_callees(tcx, did) {
                // Instance::try_resolve handles trait dispatch +
                // monomorphisation; returns Ok(None) for unresolvable
                // indirect/dyn calls, which we silently drop (already
                // covered by the opaque <ret:label> edge that
                // `lower_body` emitted).
                let inst = match Instance::try_resolve(tcx, env, callee_def, args) {
                    Ok(Some(i)) => i,
                    _ => continue,
                };
                // Follow only callees whose MIR lives in THIS crate —
                // the BFS budget is meaningless for upstream stdlib
                // calls (no MIR encoded). `is_mir_available` would
                // also catch this but `as_local` is cheaper and
                // avoids spurious queue churn.
                if let Some(local) = inst.def_id().as_local() {
                    queue.push((local, d - 1));
                }
            }
        }
        if order.is_empty() {
            return Compilation::Stop;
        }
        self.graphs.push(merge_call_graph(&order));
        Compilation::Stop
    }
}

/// Merge the BFS-collected bodies into one FlowGraph rooted at the
/// first body (the user's `root_fn`). Root's slots keep their bare
/// names; callee bodies are absorbed under a `<callee_path>::` prefix.
/// For each body that's both reached AND has a corresponding
/// `<callee>#i` / `<ret:<callee>>` rendezvous slot in the merged
/// graph, we stitch:
///   <callee_path>#i        --CallArg-->     <callee_path>::<param_i>
///   <callee_path>::<return> --CallReturn--> <ret:<callee_path>>
///
/// **Phase-2.5 cross-fn ref propagation**: when a caller passed an
/// arg as `&place`, the corresponding callee-param's `(*).<rest>`
/// accesses are rewritten during absorb to land on the same slot
/// tree the caller exposed for `place.<rest>` — closing the gap
/// where reads through `&self` previously lived in an isolated
/// callee namespace. Substitutions are derived from each upstream
/// caller's `call_sites` metadata.
fn merge_call_graph(bodies: &[(LocalDefId, FlowGraph)]) -> FlowGraph {
    use std::collections::HashMap;
    let (_, root_g) = &bodies[0];
    let mut merged = FlowGraph::new(root_g.fn_name.clone());
    // Root's slots get NO prefix so user queries like
    // `g.depends_on("s", "r")` work on the original local names.
    let _ = merged.absorb(root_g, "");
    // Index every absorbed body's call sites so each callee absorb
    // can pick up the caller-side arg origins to derive substitutions.
    // Key by callee fn_name; value is the list of (caller_prefix,
    // call_site) pairs — multiple callers may invoke the same callee.
    let mut sites_by_callee: HashMap<String, Vec<(String, &crate::flow::CallSite)>> =
        HashMap::new();
    for cs in &root_g.call_sites {
        sites_by_callee.entry(cs.fn_name.clone())
            .or_default()
            .push((String::new(), cs));   // empty caller_prefix == root
    }
    let mut seen_paths: std::collections::HashSet<String> =
        std::collections::HashSet::new();
    for (_did, body) in &bodies[1..] {
        let prefix = body.fn_name.clone();
        if !seen_paths.insert(prefix.clone()) {
            continue;
        }
        // Build substitutions from any caller's call site that targets
        // this body. For one direct call: param_i_local_name -> Slot
        // describing the caller's arg origin in the merged graph.
        // Multiple call sites for the same callee with conflicting
        // origins: pick the first (sound under-approximation).
        let mut subs: HashMap<String, crate::flow::Slot> = HashMap::new();
        if let Some(call_sites) = sites_by_callee.get(&prefix) {
            for (_caller_prefix, cs) in call_sites {
                for (i, origin) in cs.arg_origins.iter().enumerate() {
                    let Some(origin_slot) = origin else { continue };
                    let Some(&param_id) = body.params.get(i) else { continue };
                    let param_root = body.slot(param_id).root.clone();
                    subs.entry(param_root).or_insert_with(|| origin_slot.clone());
                }
                break; // first call site wins; revisit if we need union semantics
            }
        }
        let remap = merged.absorb_with(body, &prefix, &subs);
        // Stitch rendezvous → interior. After substitution, the param
        // slot's display name is either the prefixed form (no
        // substitution) or the substituted caller-side display. Either
        // way, `remap[param_id]` resolves to the right merged slot.
        for (i, &param_id) in body.params.iter().enumerate() {
            let arg_name = format!("{prefix}#{i}");
            let arg_id = match merged.slot_id_by_name(&arg_name) {
                Some(id) => id,
                None => continue,
            };
            merged.push_edge(arg_id, remap[param_id.0 as usize], EdgeKind::CallArg);
        }
        if let Some(ret_id) = body.return_slot {
            let ret_name = format!("<ret:{prefix}>");
            if let Some(rendezvous) = merged.slot_id_by_name(&ret_name) {
                merged.push_edge(remap[ret_id.0 as usize], rendezvous, EdgeKind::CallReturn);
            }
        }
        // Index this body's own call sites so its callees (when
        // absorbed later in the same loop) can pick up substitutions
        // recursively. NOTE: we'd need to translate the call_site's
        // arg_origins through `subs` first to make them point at
        // root's slot space — for now we index as-is, which means
        // depth>1 cross-fn ref propagation may stop one level deep.
        for cs in &body.call_sites {
            sites_by_callee.entry(cs.fn_name.clone())
                .or_default()
                .push((prefix.clone(), cs));
        }
    }
    merged
}

/// Flat sweep: one [`FlowGraph`] per fn definition in `source`. Same
/// surface as the original API; depth-bounded BFS lives in
/// [`analyze_source_with`].
pub fn analyze_source(source: &str) -> Result<Vec<FlowGraph>, String> {
    analyze_source_with(source, None, None)
}

/// Like [`analyze_source`], but with optional `root` + `depth` for
/// BFS-walking the call graph from a named root through statically
/// resolvable callees. Both `None` falls back to the flat sweep.
pub fn analyze_source_with(
    source: &str,
    root: Option<&str>,
    depth: Option<u32>,
) -> Result<Vec<FlowGraph>, String> {
    use std::sync::atomic::{AtomicU64, Ordering};
    static UNIQ: AtomicU64 = AtomicU64::new(0);
    let tag = UNIQ.fetch_add(1, Ordering::Relaxed);
    let tmp = std::env::temp_dir().join(format!(
        "lymph_{}_{}.rs",
        std::process::id(),
        tag,
    ));
    std::fs::write(&tmp, source).map_err(|e| e.to_string())?;
    let res = analyze_file_with(&tmp, root, depth);
    let _ = std::fs::remove_file(&tmp);
    res
}

pub fn analyze_file(path: &std::path::Path) -> Result<Vec<FlowGraph>, String> {
    analyze_file_with(path, None, None)
}

pub fn analyze_file_with(
    path: &std::path::Path,
    root: Option<&str>,
    depth: Option<u32>,
) -> Result<Vec<FlowGraph>, String> {
    let args = vec![
        "lymph".to_string(),
        path.to_str().ok_or("non-utf8 path")?.to_string(),
        "--edition".to_string(),
        "2021".to_string(),
        "--crate-type".to_string(),
        "lib".to_string(),
        "-Awarnings".to_string(),
        // Skip MIR optimisations, otherwise intermediate vars die
        "-Zmir-opt-level=0".to_string(),
    ];
    let graphs = match (root, depth) {
        (Some(name), Some(d)) => {
            let mut cb = DepthCallbacks {
                root_fn: name.to_string(),
                depth: d,
                graphs: Vec::new(),
            };
            run_with_cb(&args, &mut cb)?;
            cb.graphs
        }
        _ => {
            let mut cb = LowerCallbacks { graphs: Vec::new() };
            run_with_cb(&args, &mut cb)?;
            cb.graphs
        }
    };
    Ok(graphs)
}

fn run_with_cb<C: rustc_driver::Callbacks + Send>(
    args: &[String],
    cb: &mut C,
) -> Result<(), String> {
    // catch_unwind so fatal compile errors don't kill the caller
    let run = std::panic::AssertUnwindSafe(|| {
        let _ = rustc_driver::RunCompiler::new(args, cb).run();
    });
    if std::panic::catch_unwind(run).is_err() {
        return Err("rustc driver aborted (fatal compile error)".to_string());
    }
    Ok(())
}
