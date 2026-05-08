#![feature(rustc_private, box_patterns)]

//! lymph - Rust-side dataflow verifier over MIR.

extern crate rustc_driver;
extern crate rustc_hir;
extern crate rustc_interface;
extern crate rustc_middle;
extern crate rustc_span;

mod driver;
mod flow;
mod lower;

pub use driver::{analyze_file, analyze_file_with, analyze_source, analyze_source_with};
pub use flow::{Edge, EdgeKind, FlowGraph, Slot, SlotId};

#[cfg(feature = "python")]
mod py {
    use pyo3::exceptions::{PyKeyError, PyRuntimeError};
    use pyo3::prelude::*;
    use pyo3::types::PyList;

    use crate::driver::analyze_source_with;
    use crate::flow::{EdgeKind, FlowGraph, SlotId};

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

    /// One analyzed function: dataflow graph + queries.
    #[pyclass(name = "FlowGraph", module = "lymph")]
    pub struct PyFlowGraph {
        inner: FlowGraph,
    }

    impl PyFlowGraph {
        fn require_slot(&self, name: &str) -> PyResult<SlotId> {
            self.inner.slot_id_by_name(name).ok_or_else(|| {
                PyKeyError::new_err(format!("no slot named {name:?} in fn {}", self.inner.fn_name))
            })
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
        fn params(&self) -> Vec<String> {
            self.inner.params.iter().map(|&id| self.slot_name(id)).collect()
        }

        #[getter]
        fn return_slot(&self) -> Option<String> {
            self.inner.return_slot.map(|id| self.slot_name(id))
        }

        /// Display names of every slot, in declaration order.
        fn variables(&self) -> Vec<String> {
            self.inner.slots().iter().map(|s| s.display()).collect()
        }

        /// Slot type by name.
        fn type_of(&self, name: &str) -> PyResult<String> {
            let id = self.require_slot(name)?;
            Ok(self.inner.slot(id).ty.clone())
        }

        /// Every edge as (kind, src_name, dst_name).
        fn edges(&self) -> Vec<(&'static str, String, String)> {
            self.inner
                .edges
                .iter()
                .map(|e| (edge_kind_name(e.kind), self.slot_name(e.src), self.slot_name(e.dst)))
                .collect()
        }

        /// Direct successors: slots that `name` flows into.
        fn successors(&self, name: &str) -> PyResult<Vec<(String, &'static str)>> {
            let id = self.require_slot(name)?;
            Ok(self
                .inner
                .successors(id)
                .map(|(d, k)| (self.slot_name(d), edge_kind_name(k)))
                .collect())
        }

        /// Direct predecessors: slots that flow into `name`.
        fn predecessors(&self, name: &str) -> PyResult<Vec<(String, &'static str)>> {
            let id = self.require_slot(name)?;
            Ok(self
                .inner
                .predecessors(id)
                .map(|(s, k)| (self.slot_name(s), edge_kind_name(k)))
                .collect())
        }

        /// True iff there is a forward path source -> ... -> target.
        fn depends_on(&self, target: &str, source: &str) -> PyResult<bool> {
            let t = self.require_slot(target)?;
            let s = self.require_slot(source)?;
            Ok(self.inner.depends_on(t, s))
        }

        /// All slots reachable forward from `name` (BFS, excludes `name`).
        fn transitive_sinks(&self, name: &str) -> PyResult<Vec<String>> {
            let id = self.require_slot(name)?;
            let mut out: Vec<_> = self
                .inner
                .forward_closure(id)
                .into_iter()
                .filter(|x| *x != id)
                .map(|x| self.slot_name(x))
                .collect();
            out.sort();
            Ok(out)
        }

        /// All slots that can reach `name` (BFS over reversed edges).
        fn transitive_sources(&self, name: &str) -> PyResult<Vec<String>> {
            let id = self.require_slot(name)?;
            let mut out: Vec<_> = self
                .inner
                .backward_closure(id)
                .into_iter()
                .filter(|x| *x != id)
                .map(|x| self.slot_name(x))
                .collect();
            out.sort();
            Ok(out)
        }

        fn __repr__(&self) -> String {
            format!("FlowGraph(fn={:?}, slots={}, edges={})",
                    self.inner.fn_name,
                    self.inner.slots().len(),
                    self.inner.edges.len())
        }

        fn __str__(&self) -> String {
            format!("{}", self.inner)
        }
    }

    /// Analyze Rust source. With no `root`, returns one FlowGraph per
    /// fn definition (flat sweep). With `root` + `depth`, BFS-walks
    /// from `root`'s body through statically resolvable callees up to
    /// `depth` levels — same renderer per body, just a different set
    /// of bodies in the result.
    #[pyfunction]
    #[pyo3(signature = (source, root=None, depth=None))]
    fn analyze<'py>(
        py: Python<'py>,
        source: String,
        root: Option<&str>,
        depth: Option<u32>,
    ) -> PyResult<Bound<'py, PyList>> {
        let graphs = analyze_source_with(&source, root, depth)
            .map_err(PyRuntimeError::new_err)?;
        let list = PyList::empty(py);
        for g in graphs {
            list.append(Py::new(py, PyFlowGraph { inner: g })?)?;
        }
        Ok(list)
    }

    /// Convenience: analyze and return the human-readable text dump.
    /// Same `root` / `depth` surface as [`analyze`].
    #[pyfunction]
    #[pyo3(signature = (source, root=None, depth=None))]
    fn dump(
        source: String,
        root: Option<&str>,
        depth: Option<u32>,
    ) -> PyResult<String> {
        let graphs = analyze_source_with(&source, root, depth)
            .map_err(PyRuntimeError::new_err)?;
        let mut s = String::new();
        for g in graphs {
            s.push_str(&format!("{g}"));
        }
        Ok(s)
    }

    #[pymodule]
    fn lymph(m: &Bound<'_, PyModule>) -> PyResult<()> {
        m.add_class::<PyFlowGraph>()?;
        m.add_function(wrap_pyfunction!(analyze, m)?)?;
        m.add_function(wrap_pyfunction!(dump, m)?)?;
        Ok(())
    }
}
