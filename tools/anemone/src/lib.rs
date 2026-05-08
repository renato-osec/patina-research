// anemone: binary-side dataflow verifier. Counterpart of lymph.

pub mod flow;
mod lower;

pub use flow::{Edge, EdgeKind, FlowGraph, Slot, SlotId};
pub use lower::{lower_block_at_addr, lower_block_at_index, lower_function};

use binaryninja::binary_view::{BinaryView, BinaryViewExt};
use binaryninja::function::Function as BnFunction;

pub fn analyze_function_at(bv: &BinaryView, addr: u64) -> Option<FlowGraph> {
    with_func_at(bv, addr, lower_function)
}

pub fn analyze_block_at_index(bv: &BinaryView, fn_addr: u64, start_index: usize) -> Option<FlowGraph> {
    with_func_at(bv, fn_addr, |f| lower_block_at_index(f, start_index))
}

pub fn analyze_block_at_addr(bv: &BinaryView, fn_addr: u64, block_addr: u64) -> Option<FlowGraph> {
    with_func_at(bv, fn_addr, |f| lower_block_at_addr(f, block_addr))
}

fn with_func_at(
    bv: &BinaryView,
    addr: u64,
    f: impl FnOnce(&BnFunction) -> Option<FlowGraph>,
) -> Option<FlowGraph> {
    let funcs = bv.functions_at(addr);
    let func = funcs.iter().next()?;
    f(&func)
}

#[cfg(feature = "python")]
mod py {
    use std::collections::{BTreeMap, BTreeSet, VecDeque};

    use binaryninja::binary_view::BinaryView;
    use binaryninjacore_sys::{BNBinaryView, BNFreeBinaryView, BNNewViewReference};
    use pyo3::exceptions::{PyKeyError, PyRuntimeError};
    use pyo3::prelude::*;
    use pyo3::types::PyAny;

    use crate::flow::{EdgeKind, FlowGraph, SlotId};

    /// Owns a ref-count bump on a python-supplied BV handle.
    struct OwnedBv {
        handle: *mut BNBinaryView,
    }
    impl OwnedBv {
        unsafe fn from_ptr(raw: usize) -> PyResult<Self> {
            if raw == 0 {
                return Err(PyRuntimeError::new_err("BinaryView handle is null"));
            }
            let p = raw as *mut BNBinaryView;
            Ok(Self { handle: BNNewViewReference(p) })
        }
        fn view(&self) -> BinaryView {
            unsafe { BinaryView::from_raw(self.handle) }
        }
    }
    impl Drop for OwnedBv {
        fn drop(&mut self) {
            unsafe { BNFreeBinaryView(self.handle) };
        }
    }

    fn edge_kind_name(k: EdgeKind) -> &'static str {
        match k {
            EdgeKind::Assign => "assign",
            EdgeKind::Ref => "ref",
            EdgeKind::FieldInit => "field_init",
            EdgeKind::CallArg => "call_arg",
            EdgeKind::CallReturn => "call_return",
            EdgeKind::Return => "return",
        }
    }

    /// Forward closure honouring the recursion budget.
    /// `depth == 0` ⇒ recursion budget exhausted ⇒ worst-case at every call
    /// (any-arg ↔ any-arg ↔ ret of the same call group are connected).
    /// `depth > 0` reserved for future recursion-into-callees; currently
    /// behaves like `depth == 0`.
    fn forward_closure(g: &FlowGraph, start: SlotId, depth: usize) -> BTreeSet<SlotId> {
        let _ = depth;
        g.forward_closure_through_calls(start)
    }

    fn backward_closure(g: &FlowGraph, start: SlotId, depth: usize) -> BTreeSet<SlotId> {
        let _ = depth;
        g.backward_closure_through_calls(start)
    }

    #[pyclass(name = "FlowGraph", module = "anemone")]
    pub struct PyFlowGraph {
        inner: FlowGraph,
    }

    impl PyFlowGraph {
        fn slots_for(&self, name: &str) -> PyResult<Vec<SlotId>> {
            let ids = self.inner.slot_ids_for(name);
            if ids.is_empty() {
                Err(PyKeyError::new_err(format!(
                    "no slot named or matching {name:?} in fn {}",
                    self.inner.fn_name
                )))
            } else {
                Ok(ids)
            }
        }

        fn slot_name(&self, id: SlotId) -> String {
            self.inner.slot(id).display()
        }
    }

    #[pymethods]
    impl PyFlowGraph {
        #[getter]
        fn fn_name(&self) -> &str {
            &self.inner.fn_name
        }

        #[getter]
        fn fn_addr(&self) -> u64 {
            self.inner.fn_addr
        }

        #[getter]
        fn params(&self) -> Vec<String> {
            self.inner.params.iter().map(|&id| self.slot_name(id)).collect()
        }

        #[getter]
        fn return_slot(&self) -> Option<String> {
            self.inner.return_slot.map(|id| self.slot_name(id))
        }

        fn variables(&self) -> Vec<String> {
            self.inner.slots().iter().map(|s| s.display()).collect()
        }

        fn edges(&self) -> Vec<(&'static str, String, String)> {
            self.inner.edges.iter().map(|e| {
                (edge_kind_name(e.kind), self.slot_name(e.src), self.slot_name(e.dst))
            }).collect()
        }

        fn successors(&self, name: &str) -> PyResult<Vec<(String, &'static str)>> {
            let mut out = Vec::new();
            for id in self.slots_for(name)? {
                out.extend(self.inner.successors(id)
                    .map(|(d, k)| (self.slot_name(d), edge_kind_name(k))));
            }
            Ok(out)
        }

        fn predecessors(&self, name: &str) -> PyResult<Vec<(String, &'static str)>> {
            let mut out = Vec::new();
            for id in self.slots_for(name)? {
                out.extend(self.inner.predecessors(id)
                    .map(|(s, k)| (self.slot_name(s), edge_kind_name(k))));
            }
            Ok(out)
        }

        /// True iff data can flow `source -> ... -> target`. `depth` is the
        /// recursion budget for crossing calls; when it runs out the call
        /// is treated as fully connected (every arg <-> every arg <-> ret).
        /// Default `depth=0` -> conservative worst-case at every call.
        #[pyo3(signature = (target, source, depth=0))]
        fn depends_on(&self, target: &str, source: &str, depth: usize) -> PyResult<bool> {
            let targets: BTreeSet<SlotId> =
                self.slots_for(target)?.into_iter().collect();
            for s in self.slots_for(source)? {
                let fwd = forward_closure(&self.inner, s, depth);
                if fwd.iter().any(|t| targets.contains(t)) {
                    return Ok(true);
                }
            }
            Ok(false)
        }

        #[pyo3(signature = (name, depth=0))]
        fn transitive_sinks(&self, name: &str, depth: usize) -> PyResult<Vec<String>> {
            let seeds: BTreeSet<SlotId> = self.slots_for(name)?.into_iter().collect();
            let mut reached: BTreeSet<SlotId> = BTreeSet::new();
            for s in &seeds {
                reached.extend(forward_closure(&self.inner, *s, depth));
            }
            let mut out: Vec<_> = reached.difference(&seeds)
                .map(|x| self.slot_name(*x)).collect();
            out.sort();
            Ok(out)
        }

        #[pyo3(signature = (name, depth=0))]
        fn transitive_sources(&self, name: &str, depth: usize) -> PyResult<Vec<String>> {
            let seeds: BTreeSet<SlotId> = self.slots_for(name)?.into_iter().collect();
            let mut reached: BTreeSet<SlotId> = BTreeSet::new();
            for s in &seeds {
                reached.extend(backward_closure(&self.inner, *s, depth));
            }
            let mut out: Vec<_> = reached.difference(&seeds)
                .map(|x| self.slot_name(*x)).collect();
            out.sort();
            Ok(out)
        }

        fn __repr__(&self) -> String {
            format!("FlowGraph(fn={:?}, addr={:#x}, slots={}, edges={})",
                    self.inner.fn_name, self.inner.fn_addr,
                    self.inner.slots().len(), self.inner.edges.len())
        }

        fn __str__(&self) -> String {
            format!("{}", self.inner)
        }
    }

    /// Resolve a python BV handle to a raw `BNBinaryView*` integer.
    fn bv_ptr_of(py: Python<'_>, obj: &Bound<'_, PyAny>) -> PyResult<usize> {
        if let Ok(n) = obj.extract::<usize>() {
            return Ok(n);
        }
        if let Ok(h) = obj.getattr("handle") {
            let ctypes = py.import("ctypes")?;
            let c_void_p = ctypes.getattr("c_void_p")?;
            let cast = ctypes.getattr("cast")?;
            let casted = cast.call1((h, c_void_p))?;
            let value = casted.getattr("value")?;
            if value.is_none() {
                return Err(pyo3::exceptions::PyValueError::new_err(
                    "BinaryView.handle is null",
                ));
            }
            return value.extract::<usize>();
        }
        Err(pyo3::exceptions::PyTypeError::new_err(
            "expected int pointer or object with `.handle` (binaryninja.BinaryView)",
        ))
    }

    static INIT_RUST_BINJA: std::sync::Once = std::sync::Once::new();
    fn ensure_binja_inited() {
        INIT_RUST_BINJA.call_once(|| {
            // No main-thread handler: default spawns a HeadlessMainThread that bv.create_database segfaults on (thread affinity).
            let opts = binaryninja::headless::InitializationOptions::default()
                .with_main_thread_handler(false);
            let _ = binaryninja::headless::init_with_opts(opts);
        });
    }

    fn run_lowering<F>(
        py: Python<'_>,
        py_bv: &Bound<'_, PyAny>,
        not_found_msg: String,
        body: F,
    ) -> PyResult<PyFlowGraph>
    where
        F: FnOnce(&BinaryView) -> Option<FlowGraph> + Send,
    {
        ensure_binja_inited();
        let raw = bv_ptr_of(py, py_bv)?;
        let owned = unsafe { OwnedBv::from_ptr(raw)? };
        let view = owned.view();
        let g = py.allow_threads(|| body(&view));
        let g = g.ok_or_else(|| PyRuntimeError::new_err(not_found_msg))?;
        Ok(PyFlowGraph { inner: g })
    }

    #[pyfunction]
    fn analyze(py: Python<'_>, py_bv: &Bound<'_, PyAny>, addr: u64) -> PyResult<PyFlowGraph> {
        run_lowering(
            py, py_bv,
            format!("no function at {addr:#x} or no MLIL available"),
            |view| crate::analyze_function_at(view, addr),
        )
    }

    /// Lower one MLIL-SSA basic block; `block_addr` = first instr's binary addr.
    #[pyfunction]
    fn analyze_block(
        py: Python<'_>,
        py_bv: &Bound<'_, PyAny>,
        fn_addr: u64,
        block_addr: u64,
    ) -> PyResult<PyFlowGraph> {
        run_lowering(
            py, py_bv,
            format!("no MLIL-SSA block at {block_addr:#x} in fn {fn_addr:#x}"),
            move |view| crate::analyze_block_at_addr(view, fn_addr, block_addr),
        )
    }

    /// Like `analyze_block`, but identifies the block by its first instr index.
    #[pyfunction]
    fn analyze_block_at_index(
        py: Python<'_>,
        py_bv: &Bound<'_, PyAny>,
        fn_addr: u64,
        start_index: usize,
    ) -> PyResult<PyFlowGraph> {
        run_lowering(
            py, py_bv,
            format!("no MLIL-SSA block starting at index {start_index} in fn {fn_addr:#x}"),
            move |view| crate::analyze_block_at_index(view, fn_addr, start_index),
        )
    }

    /// Cross-check rust-side dependencies (lymph edges) against a binary-side
    /// FlowGraph; `mapping[rust_var] = il_var`. Returns `(ok, [diffs])`.
    #[pyfunction]
    #[pyo3(signature = (rust_edges, anem, mapping, depth=0))]
    fn check_compatibility(
        rust_edges: Vec<(String, String, String)>,
        anem: &PyFlowGraph,
        mapping: BTreeMap<String, String>,
        depth: usize,
    ) -> PyResult<(bool, Vec<String>)> {
        let rust_succ = adjacency(&rust_edges);
        let mut diffs: Vec<String> = Vec::new();
        let names: Vec<&String> = mapping.keys().collect();
        for x in &names {
            for y in &names {
                if x == y {
                    continue;
                }
                let r_dep = reachable(&rust_succ, x, y);
                let il_x = &mapping[*x];
                let il_y = &mapping[*y];
                let ts = anem.inner.slot_ids_for(il_x);
                let ss = anem.inner.slot_ids_for(il_y);
                if ts.is_empty() || ss.is_empty() {
                    diffs.push(format!(
                        "missing IL slot for {x}->{il_x} or {y}->{il_y}"
                    ));
                    continue;
                }
                let b_dep = ss.iter().any(|s| {
                    let fwd = forward_closure(&anem.inner, *s, depth);
                    ts.iter().any(|t| fwd.contains(t))
                });
                if r_dep != b_dep {
                    diffs.push(format!(
                        "{x} <- {y}: rust={r_dep} binary={b_dep}"
                    ));
                }
            }
        }
        Ok((diffs.is_empty(), diffs))
    }

    fn adjacency(edges: &[(String, String, String)]) -> BTreeMap<String, Vec<String>> {
        let mut m: BTreeMap<String, Vec<String>> = BTreeMap::new();
        for (_kind, src, dst) in edges {
            m.entry(src.clone()).or_default().push(dst.clone());
        }
        m
    }

    fn reachable(succ: &BTreeMap<String, Vec<String>>, target: &str, source: &str) -> bool {
        if target == source {
            return true;
        }
        let mut seen: BTreeSet<&str> = BTreeSet::new();
        let mut q: VecDeque<&str> = VecDeque::new();
        seen.insert(source);
        q.push_back(source);
        while let Some(n) = q.pop_front() {
            if n == target {
                return true;
            }
            if let Some(neis) = succ.get(n) {
                for nx in neis {
                    if seen.insert(nx) {
                        q.push_back(nx);
                    }
                }
            }
        }
        false
    }

    #[pymodule]
    fn anemone(m: &Bound<'_, PyModule>) -> PyResult<()> {
        m.add_class::<PyFlowGraph>()?;
        m.add_function(wrap_pyfunction!(analyze, m)?)?;
        m.add_function(wrap_pyfunction!(analyze_block, m)?)?;
        m.add_function(wrap_pyfunction!(analyze_block_at_index, m)?)?;
        m.add_function(wrap_pyfunction!(check_compatibility, m)?)?;
        Ok(())
    }
}
