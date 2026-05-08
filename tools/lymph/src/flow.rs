//! Flow-graph representation.

use std::collections::{BTreeMap, BTreeSet, VecDeque};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub struct SlotId(pub u32);

#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct Slot {
    pub root: String,
    pub path: Vec<String>,
    pub ty: String,
}

impl Slot {
    pub fn new(root: impl Into<String>, path: Vec<String>, ty: impl Into<String>) -> Self {
        Slot { root: root.into(), path, ty: ty.into() }
    }

    pub fn root_only(root: impl Into<String>, ty: impl Into<String>) -> Self {
        Slot { root: root.into(), path: Vec::new(), ty: ty.into() }
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

/// Per-call-site metadata captured at lower-time so the merge step
/// can stitch caller's argument places to the corresponding parameter
/// pointee inside the callee. `arg_origins[i] = Some(slot)` when the
/// caller passed `&place` / `&mut place` (or any chain that chases
/// through `ref_origins`) — `slot` is the caller-side place we want
/// the callee's `(*param_i).<rest>` accesses to alias to.
#[derive(Debug, Clone)]
pub struct CallSite {
    /// Callee's `tcx.def_path_str` — used by merge to match the
    /// receiving body. Same string `lower_terminator` uses for the
    /// `<callee>#i` / `<ret:<callee>>` rendezvous slot roots.
    pub fn_name: String,
    pub arg_origins: Vec<Option<Slot>>,
}

#[derive(Debug, Clone, Default)]
pub struct FlowGraph {
    pub fn_name: String,
    pub params: Vec<SlotId>,
    pub return_slot: Option<SlotId>,
    slots: Vec<Slot>,
    by_display: BTreeMap<String, SlotId>,
    pub edges: Vec<Edge>,
    /// One entry per `TerminatorKind::Call` lowered in this body —
    /// merge consults these to wire cross-fn ref aliasing.
    pub call_sites: Vec<CallSite>,
}

impl FlowGraph {
    pub fn new(fn_name: impl Into<String>) -> Self {
        Self { fn_name: fn_name.into(), ..Default::default() }
    }

    pub fn slot(&self, id: SlotId) -> &Slot {
        &self.slots[id.0 as usize]
    }

    pub fn slots(&self) -> &[Slot] {
        &self.slots
    }

    /// Insert a slot, dedup by display name; typed slot never loses to `"_"`.
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

    /// Absorb every slot + edge from `other` into self, namespacing
    /// `other`'s "internal" slot roots with `prefix::` so they don't
    /// collide with self. "External" rendezvous slots (`<callee>#i`,
    /// `<ret:<callee>>`) keep their names — they're the cross-fn
    /// bridges that callers and callees share. Returns a remap
    /// `other.SlotId -> self.SlotId` so callers can wire stitching
    /// edges (`<callee>#i -> callee::param_i`,
    /// `callee::<return> -> <ret:<callee>>`) without re-walking.
    pub fn absorb(&mut self, other: &FlowGraph, prefix: &str) -> Vec<SlotId> {
        self.absorb_with(other, prefix, &Default::default())
    }

    /// Like [`absorb`] but with **param substitutions**: a slot whose
    /// root name appears in `subs` and whose first path step is a
    /// `Deref` ("(*)") is rewritten to the substitution's root +
    /// path, with the leading `(*)` consumed. This is how Phase-2.5
    /// cross-fn ref aliasing collapses callee accesses through a
    /// reference parameter (e.g. `(*self).lookup`) onto the caller-
    /// side place that was passed as `&m.lookup`'s parent (here, `m`).
    pub fn absorb_with(
        &mut self,
        other: &FlowGraph,
        prefix: &str,
        subs: &std::collections::HashMap<String, Slot>,
    ) -> Vec<SlotId> {
        let mut remap = Vec::with_capacity(other.slots.len());
        for s in &other.slots {
            // 1. Param-substitution: rewrite `<param>.(*).<rest>` into
            //    `<origin_root>.<origin_path>.<rest>` so the resulting
            //    slot lives in the caller's slot space.
            let substituted = if let Some(origin) = subs.get(&s.root) {
                if s.path.first().map(|p| p == "(*)").unwrap_or(false) {
                    let mut new_path = origin.path.clone();
                    new_path.extend(s.path.iter().skip(1).cloned());
                    Some(Slot::new(origin.root.clone(), new_path, s.ty.clone()))
                } else {
                    None
                }
            } else {
                None
            };
            let id = if let Some(slot) = substituted {
                self.intern(slot)
            } else {
                // 2. Otherwise prefix as the basic absorb would.
                let new_root = if prefix.is_empty() || is_external_slot(&s.root) {
                    s.root.clone()
                } else {
                    format!("{prefix}::{}", s.root)
                };
                self.intern(Slot::new(new_root, s.path.clone(), s.ty.clone()))
            };
            remap.push(id);
        }
        for e in &other.edges {
            self.push_edge(remap[e.src.0 as usize], remap[e.dst.0 as usize], e.kind);
        }
        remap
    }

    // Look up a slot by its display form ("foo", "foo.bar", "<return>").
    pub fn slot_id_by_name(&self, name: &str) -> Option<SlotId> {
        self.by_display.get(name).copied()
    }

    // Direct outgoing edges from `src`.
    pub fn successors(&self, src: SlotId) -> impl Iterator<Item = (SlotId, EdgeKind)> + '_ {
        self.edges
            .iter()
            .filter(move |e| e.src == src)
            .map(|e| (e.dst, e.kind))
    }

    // Direct incoming edges to `dst`.
    pub fn predecessors(&self, dst: SlotId) -> impl Iterator<Item = (SlotId, EdgeKind)> + '_ {
        self.edges
            .iter()
            .filter(move |e| e.dst == dst)
            .map(|e| (e.src, e.kind))
    }

    // Does `target` data-depend on `source`? True iff there exists a forward
    // edge path source -> ... -> target (any edge kind counts).
    pub fn depends_on(&self, target: SlotId, source: SlotId) -> bool {
        if source == target {
            return true;
        }
        self.forward_closure(source).contains(&target)
    }

    // All slots reachable forward from `start` (including `start` itself).
    pub fn forward_closure(&self, start: SlotId) -> BTreeSet<SlotId> {
        self.bfs(start, |id| self.successors(id).map(|(d, _)| d))
    }

    // All slots that can reach `start` via forward edges.
    pub fn backward_closure(&self, start: SlotId) -> BTreeSet<SlotId> {
        self.bfs(start, |id| self.predecessors(id).map(|(s, _)| s))
    }

    fn bfs<F, I>(&self, start: SlotId, mut neighbors: F) -> BTreeSet<SlotId>
    where
        F: FnMut(SlotId) -> I,
        I: Iterator<Item = SlotId>,
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

/// True for slot roots that are cross-fn rendezvous points emitted by
/// `lower_terminator` (one CallArg slot per arg, one CallReturn slot
/// per call). These names are the bridge between caller and callee,
/// so absorb() must NOT prefix them — both sides need to land on the
/// same slot for stitching to work.
fn is_external_slot(root: &str) -> bool {
    root.contains('#') || root.starts_with("<ret:")
}

impl std::fmt::Display for FlowGraph {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        writeln!(f, "fn {}:", self.fn_name)?;
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
