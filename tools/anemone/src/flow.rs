// Per-function dataflow graph; mirrors lymph::flow for cross-graph compare.
use std::collections::{BTreeMap, BTreeSet, VecDeque};

fn unversioned(name: &str) -> &str {
    match name.rsplit_once('#') {
        Some((base, ver)) if !ver.is_empty() && ver.chars().all(|c| c.is_ascii_digit()) => base,
        _ => name,
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub struct SlotId(pub u32);

#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct Slot {
    pub root: String,
    pub path: Vec<String>,
    pub ty: String,
}

impl Slot {
    pub fn root_only(root: impl Into<String>, ty: impl Into<String>) -> Self {
        Slot { root: root.into(), path: Vec::new(), ty: ty.into() }
    }

    pub fn new(root: impl Into<String>, path: Vec<String>, ty: impl Into<String>) -> Self {
        Slot { root: root.into(), path, ty: ty.into() }
    }

    pub fn display(&self) -> String {
        if self.path.is_empty() {
            self.root.clone()
        } else {
            format!("{}.{}", self.root, self.path.join("."))
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum EdgeKind {
    Assign,
    Ref,
    FieldInit,
    CallArg,
    CallReturn,
    Return,
}

#[derive(Debug, Clone)]
pub struct Edge {
    pub src: SlotId,
    pub dst: SlotId,
    pub kind: EdgeKind,
}

/// One callsite: every CallArg sink + the CallReturn source. The
/// worst-case bridge is *directional*: forward, every arg sink can
/// reach the ret (the call's output may depend on any arg); backward,
/// the ret can reach every arg sink (so backward closures pick up
/// every contributor). Cross-arg bridges are NOT added - `f(a, b)`
/// does not store `b` into the caller's `a` slot, so connecting the
/// arg sinks to each other was a false-positive source.
#[derive(Debug, Clone)]
pub struct CallGroup {
    pub args: Vec<SlotId>,
    pub ret: Option<SlotId>,
}

#[derive(Debug, Clone, Default)]
pub struct FlowGraph {
    pub fn_name: String,
    pub fn_addr: u64,
    pub params: Vec<SlotId>,
    pub return_slot: Option<SlotId>,
    slots: Vec<Slot>,
    by_display: BTreeMap<String, SlotId>,
    pub edges: Vec<Edge>,
    pub call_groups: Vec<CallGroup>,
}

impl FlowGraph {
    pub fn new(fn_name: impl Into<String>, fn_addr: u64) -> Self {
        Self {
            fn_name: fn_name.into(),
            fn_addr,
            ..Default::default()
        }
    }

    pub fn slot(&self, id: SlotId) -> &Slot {
        &self.slots[id.0 as usize]
    }

    pub fn slots(&self) -> &[Slot] {
        &self.slots
    }

    pub fn intern(&mut self, slot: Slot) -> SlotId {
        let key = slot.display();
        if let Some(&id) = self.by_display.get(&key) {
            if self.slots[id.0 as usize].ty == "_" {
                self.slots[id.0 as usize].ty = slot.ty;
            }
            return id;
        }
        let id = SlotId(self.slots.len() as u32);
        self.slots.push(slot);
        self.by_display.insert(key, id);
        id
    }

    pub fn push_edge(&mut self, src: SlotId, dst: SlotId, kind: EdgeKind) {
        self.edges.push(Edge { src, dst, kind });
    }

    pub fn register_call_group(&mut self, args: Vec<SlotId>, ret: Option<SlotId>) {
        self.call_groups.push(CallGroup { args, ret });
    }

    pub fn slot_id_by_name(&self, name: &str) -> Option<SlotId> {
        self.by_display.get(name).copied()
    }

    /// Version-agnostic resolution: SSA produces one slot per (var, version);
    /// unversioned `name` returns every slot whose unversioned root matches.
    /// Exception: function parameters always resolve to *only* their `#0`
    /// version. Binja's MLIL-SSA reuses param names at later memory-load
    /// defs (e.g. `i#0` is the param, `i#4 = [rcx+8].q` is the hashmap
    /// lookup result, both stored under the same binja Variable identifier).
    /// Without this narrowing, `arg_x <- arg_y` checks see `arg_x`'s loaded-
    /// value version reachable from `arg_y` and report a spurious dependency.
    pub fn slot_ids_for(&self, name: &str) -> Vec<SlotId> {
        if let Some(&id) = self.by_display.get(name) {
            return vec![id];
        }
        for &p in &self.params {
            if unversioned(&self.slots[p.0 as usize].root) == name
                && self.slots[p.0 as usize].path.is_empty()
            {
                return vec![p];
            }
        }
        self.slots
            .iter()
            .enumerate()
            .filter(|(_, s)| s.path.is_empty() && unversioned(&s.root) == name)
            .map(|(i, _)| SlotId(i as u32))
            .collect()
    }

    pub fn successors(&self, src: SlotId) -> impl Iterator<Item = (SlotId, EdgeKind)> + '_ {
        self.edges.iter().filter(move |e| e.src == src).map(|e| (e.dst, e.kind))
    }

    pub fn predecessors(&self, dst: SlotId) -> impl Iterator<Item = (SlotId, EdgeKind)> + '_ {
        self.edges.iter().filter(move |e| e.dst == dst).map(|e| (e.src, e.kind))
    }

    pub fn depends_on(&self, target: SlotId, source: SlotId) -> bool {
        if source == target {
            return true;
        }
        self.forward_closure(source).contains(&target)
    }

    pub fn forward_closure(&self, start: SlotId) -> BTreeSet<SlotId> {
        self.bfs(start, |id| self.successors(id).map(|(d, _)| d).collect::<Vec<_>>())
    }

    pub fn backward_closure(&self, start: SlotId) -> BTreeSet<SlotId> {
        self.bfs(start, |id| self.predecessors(id).map(|(s, _)| s).collect::<Vec<_>>())
    }

    /// Forward closure with the directional opaque-call worst-case:
    /// reaching an arg sink bridges to that call's ret (call output
    /// may depend on the arg). Args do NOT bridge to other args -
    /// `f(a,b)` does not write `b` into the caller's `a` slot.
    pub fn forward_closure_through_calls(&self, start: SlotId) -> BTreeSet<SlotId> {
        let arg_to_ret = self.arg_to_ret_index();
        self.bfs(start, |id| {
            let mut v: Vec<SlotId> = self.successors(id).map(|(d, _)| d).collect();
            if let Some(rets) = arg_to_ret.get(&id) {
                v.extend(rets.iter().copied());
            }
            v
        })
    }

    /// Backward closure with the directional opaque-call worst-case:
    /// reaching the ret bridges back to every arg sink of that call.
    pub fn backward_closure_through_calls(&self, start: SlotId) -> BTreeSet<SlotId> {
        let ret_to_args = self.ret_to_args_index();
        self.bfs(start, |id| {
            let mut v: Vec<SlotId> = self.predecessors(id).map(|(s, _)| s).collect();
            if let Some(args) = ret_to_args.get(&id) {
                v.extend(args.iter().copied());
            }
            v
        })
    }

    fn arg_to_ret_index(&self) -> BTreeMap<SlotId, Vec<SlotId>> {
        let mut m: BTreeMap<SlotId, Vec<SlotId>> = BTreeMap::new();
        for g in &self.call_groups {
            if let Some(r) = g.ret {
                for &a in &g.args {
                    m.entry(a).or_default().push(r);
                }
            }
        }
        m
    }

    fn ret_to_args_index(&self) -> BTreeMap<SlotId, Vec<SlotId>> {
        let mut m: BTreeMap<SlotId, Vec<SlotId>> = BTreeMap::new();
        for g in &self.call_groups {
            if let Some(r) = g.ret {
                m.entry(r).or_default().extend(g.args.iter().copied());
            }
        }
        m
    }

    fn bfs<F>(&self, start: SlotId, mut neighbors: F) -> BTreeSet<SlotId>
    where
        F: FnMut(SlotId) -> Vec<SlotId>,
    {
        let mut seen = BTreeSet::new();
        let mut q = VecDeque::new();
        seen.insert(start);
        q.push_back(start);
        while let Some(n) = q.pop_front() {
            for nx in neighbors(n) {
                if seen.insert(nx) {
                    q.push_back(nx);
                }
            }
        }
        seen
    }
}

impl std::fmt::Display for FlowGraph {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        writeln!(f, "fn {} @ {:#x}:", self.fn_name, self.fn_addr)?;
        for p in &self.params {
            let s = self.slot(*p);
            writeln!(f, "  param  {} : {}", s.display(), s.ty)?;
        }
        if let Some(r) = self.return_slot {
            let s = self.slot(r);
            writeln!(f, "  return {} : {}", s.display(), s.ty)?;
        }
        for e in &self.edges {
            let s = self.slot(e.src);
            let d = self.slot(e.dst);
            writeln!(
                f,
                "  {:?}: {} [{}] -> {} [{}]",
                e.kind,
                s.display(),
                s.ty,
                d.display(),
                d.ty
            )?;
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn intern_dedups_by_display() {
        let mut g = FlowGraph::new("f", 0);
        let a = g.intern(Slot::root_only("x", "u64"));
        let b = g.intern(Slot::root_only("x", "u64"));
        assert_eq!(a, b);
    }

    #[test]
    fn depends_on_walks_forward() {
        let mut g = FlowGraph::new("f", 0);
        let a = g.intern(Slot::root_only("a", "u64"));
        let b = g.intern(Slot::root_only("b", "u64"));
        let c = g.intern(Slot::root_only("c", "u64"));
        g.push_edge(a, b, EdgeKind::Assign);
        g.push_edge(b, c, EdgeKind::Assign);
        assert!(g.depends_on(c, a));
        assert!(!g.depends_on(a, c));
    }

    #[test]
    fn forward_closure_excludes_unreachable() {
        let mut g = FlowGraph::new("f", 0);
        let a = g.intern(Slot::root_only("a", "u64"));
        let b = g.intern(Slot::root_only("b", "u64"));
        let c = g.intern(Slot::root_only("c", "u64"));
        g.push_edge(a, b, EdgeKind::Assign);
        let closure = g.forward_closure(a);
        assert!(closure.contains(&b));
        assert!(!closure.contains(&c));
    }

    #[test]
    fn through_calls_bridges_arg_to_ret() {
        let mut g = FlowGraph::new("f", 0);
        let key = g.intern(Slot::root_only("key", "u64"));
        let arg0 = g.intern(Slot::root_only("hash#0", "_"));
        let ret = g.intern(Slot::root_only("<ret:hash>", "_"));
        let hash = g.intern(Slot::root_only("hash", "u64"));
        g.push_edge(key, arg0, EdgeKind::CallArg);
        g.push_edge(ret, hash, EdgeKind::CallReturn);
        g.register_call_group(vec![arg0], Some(ret));
        assert!(!g.depends_on(hash, key), "strict: no bridge");
        let fc = g.forward_closure_through_calls(key);
        assert!(fc.contains(&hash), "worst-case: bridge through call group");
    }

    /// Regression: binja's MLIL-SSA reuses a param's variable identity
    /// at later memory-load defs (e.g. `i#0` is the param, `i#4` is a
    /// hashmap-lookup result both bound to binja's `i`). Looking up
    /// "i" must return only the param `#0` for cross-graph checks;
    /// the load-defined version would otherwise create a false
    /// `arg <- self` dependency through whatever the load reads.
    #[test]
    fn slot_ids_for_param_narrows_to_zero_version() {
        let mut g = FlowGraph::new("f", 0);
        let i0 = g.intern(Slot::root_only("i#0", "u64"));
        g.params.push(i0);
        let i4 = g.intern(Slot::root_only("i#4", "u64"));
        let other = g.intern(Slot::root_only("rcx#1", "_"));
        g.push_edge(other, i4, EdgeKind::Assign);
        let ids = g.slot_ids_for("i");
        assert_eq!(ids, vec![i0],
                   "param 'i' must resolve to only its #0 version, not later reuses");
        // Non-param locals still resolve version-wide.
        let r0 = g.intern(Slot::root_only("rdx#0", "u64"));
        let r1 = g.intern(Slot::root_only("rdx#1", "u64"));
        let mut got = g.slot_ids_for("rdx");
        got.sort();
        let mut want = vec![r0, r1];
        want.sort();
        assert_eq!(got, want);
    }

    /// Regression: opaque call with two args used to falsely bridge
    /// arg_a <-> arg_b (and so caller-side `a` <-> caller-side `b`).
    /// Forward closure of caller `a` must NOT include caller `b`,
    /// because `f(a, b)`'s opaque worst case writes only to ret.
    #[test]
    fn through_calls_does_not_bridge_args() {
        let mut g = FlowGraph::new("f", 0);
        let a = g.intern(Slot::root_only("a", "u64"));
        let b = g.intern(Slot::root_only("b", "u64"));
        let arg0 = g.intern(Slot::root_only("h#0", "_"));
        let arg1 = g.intern(Slot::root_only("h#1", "_"));
        let ret = g.intern(Slot::root_only("<ret:h>", "_"));
        let out = g.intern(Slot::root_only("out", "u64"));
        g.push_edge(a, arg0, EdgeKind::CallArg);
        g.push_edge(b, arg1, EdgeKind::CallArg);
        g.push_edge(ret, out, EdgeKind::CallReturn);
        g.register_call_group(vec![arg0, arg1], Some(ret));
        let fa = g.forward_closure_through_calls(a);
        let fb = g.forward_closure_through_calls(b);
        assert!(fa.contains(&out), "a -> ret -> out");
        assert!(fb.contains(&out), "b -> ret -> out");
        assert!(!fa.contains(&b), "no false a -> b bridge");
        assert!(!fb.contains(&a), "no false b -> a bridge");
        // Backward: `out` should depend on both args (correct).
        let bw = g.backward_closure_through_calls(out);
        assert!(bw.contains(&a) && bw.contains(&b),
                "out backward -> both args via ret_to_args");
    }
}
