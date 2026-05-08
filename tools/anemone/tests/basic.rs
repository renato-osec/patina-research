// Pure-Rust tests of the FlowGraph query API. No binja required.
use anemone::{EdgeKind, FlowGraph, Slot};

fn fg() -> (FlowGraph, [anemone::SlotId; 4]) {
    let mut g = FlowGraph::new("f", 0x1000);
    let a = g.intern(Slot::root_only("a", "u64"));
    let b = g.intern(Slot::root_only("b", "u64"));
    let c = g.intern(Slot::root_only("c", "u64"));
    let d = g.intern(Slot::root_only("d", "u64"));
    g.params.push(a);
    g.return_slot = Some(d);
    g.push_edge(a, b, EdgeKind::Assign);
    g.push_edge(b, c, EdgeKind::Assign);
    g.push_edge(c, d, EdgeKind::Return);
    (g, [a, b, c, d])
}

#[test]
fn slot_lookup_by_name() {
    let (g, [a, _, _, _]) = fg();
    assert_eq!(g.slot_id_by_name("a"), Some(a));
    assert!(g.slot_id_by_name("missing").is_none());
}

#[test]
fn depends_on_traces_full_chain() {
    let (g, [a, _b, _c, d]) = fg();
    assert!(g.depends_on(d, a));
    assert!(!g.depends_on(a, d));
}

#[test]
fn forward_closure_includes_chain() {
    let (g, [a, b, c, d]) = fg();
    let fwd = g.forward_closure(a);
    assert!(fwd.contains(&a));
    assert!(fwd.contains(&b));
    assert!(fwd.contains(&c));
    assert!(fwd.contains(&d));
}

#[test]
fn predecessors_includes_immediate_source() {
    let (g, [_, b, c, _]) = fg();
    let preds: Vec<_> = g.predecessors(c).map(|(s, _)| s).collect();
    assert!(preds.contains(&b));
}

#[test]
fn unrelated_slots_dont_depend() {
    let mut g = FlowGraph::new("f", 0);
    let x = g.intern(Slot::root_only("x", "u64"));
    let y = g.intern(Slot::root_only("y", "u64"));
    assert!(!g.depends_on(x, y));
    assert!(!g.depends_on(y, x));
    // Self-dep is true by definition.
    assert!(g.depends_on(x, x));
}

#[test]
fn intern_dedups_distinct_paths() {
    let mut g = FlowGraph::new("f", 0);
    let s1 = g.intern(Slot::new("s", vec!["a".into()], "u64"));
    let s2 = g.intern(Slot::new("s", vec!["a".into()], "u64"));
    let s3 = g.intern(Slot::new("s", vec!["b".into()], "u64"));
    assert_eq!(s1, s2);
    assert_ne!(s1, s3);
}

#[test]
fn slot_ids_for_resolves_unversioned_to_all_versions() {
    let mut g = FlowGraph::new("f", 0);
    let v0 = g.intern(Slot::root_only("x#0", "u64"));
    let v1 = g.intern(Slot::root_only("x#1", "u64"));
    let _other = g.intern(Slot::root_only("y#0", "u64"));
    let exact = g.slot_ids_for("x#0");
    assert_eq!(exact, vec![v0]);
    let bare = g.slot_ids_for("x");
    assert!(bare.contains(&v0) && bare.contains(&v1));
    assert_eq!(bare.len(), 2);
}

#[test]
fn slot_ids_for_handles_unversioned_roots() {
    let mut g = FlowGraph::new("f", 0);
    let r = g.intern(Slot::root_only("<return>", "u64"));
    assert_eq!(g.slot_ids_for("<return>"), vec![r]);
    assert!(g.slot_ids_for("missing").is_empty());
}

#[test]
fn graph_display_round_trip() {
    let (g, _) = fg();
    let s = format!("{g}");
    assert!(s.contains("fn f"));
    assert!(s.contains("Assign"));
    assert!(s.contains("a"));
    assert!(s.contains("d"));
}
