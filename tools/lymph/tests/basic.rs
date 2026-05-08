#![feature(rustc_private)]

use std::sync::Mutex;

use indoc::indoc;
use lymph::{analyze_source, EdgeKind, FlowGraph};

// rustc_driver has global per-process state; concurrent drivers deadlock.
static DRIVER_LOCK: Mutex<()> = Mutex::new(());

fn graph_containing(src: &str, needle: &str) -> FlowGraph {
    let _guard = DRIVER_LOCK.lock().unwrap();
    let graphs = analyze_source(src).expect("rustc driver ok");
    graphs
        .into_iter()
        .find(|g| g.fn_name.contains(needle))
        .unwrap_or_else(|| panic!("no fn whose name contains {needle:?} in:\n{src}"))
}

fn edge_strings(g: &FlowGraph, kind: EdgeKind) -> Vec<(String, String)> {
    g.edges
        .iter()
        .filter(|e| e.kind == kind)
        .map(|e| (g.slot(e.src).display(), g.slot(e.dst).display()))
        .collect()
}

#[test]
fn let_move_carries_value() {
    let src = indoc! {r#"
        fn f(x: u64) -> u64 {
            let y = x;
            y
        }
    "#};
    let g = graph_containing(src, "f");
    let x = g.slot_id_by_name("x").expect("x");
    let y = g.slot_id_by_name("y").expect("y");
    let ret = g.slot_id_by_name("<return>").expect("<return>");
    assert!(g.depends_on(y, x), "y should flow from x in:\n{g}");
    assert!(g.depends_on(ret, x), "<return> should flow from x in:\n{g}");
}

#[test]
fn struct_literal_field_init_flows_per_field() {
    let src = indoc! {r#"
        pub struct S { pub a: u64, pub b: u64 }
        pub fn f(x: u64, y: u64) -> S {
            S { a: x, b: y }
        }
    "#};
    let g = graph_containing(src, "f");
    let x = g.slot_id_by_name("x").expect("x");
    let y = g.slot_id_by_name("y").expect("y");
    let f_a = g.slots().iter().enumerate()
        .find(|(_, s)| s.display().ends_with(".a"))
        .map(|(i, _)| lymph::SlotId(i as u32))
        .expect(".a slot");
    let f_b = g.slots().iter().enumerate()
        .find(|(_, s)| s.display().ends_with(".b"))
        .map(|(i, _)| lymph::SlotId(i as u32))
        .expect(".b slot");
    assert!(g.depends_on(f_a, x), "x should reach the .a field in:\n{g}");
    assert!(g.depends_on(f_b, y), "y should reach the .b field in:\n{g}");
}

#[test]
fn field_read_resolves_subpath() {
    let src = indoc! {r#"
        pub struct S { pub inner: u64 }
        pub fn f(s: S) -> u64 {
            let v = s.inner;
            v
        }
    "#};
    let g = graph_containing(src, "f");
    let s_inner = g.slot_id_by_name("s.inner").expect("s.inner");
    let ret = g.slot_id_by_name("<return>").expect("<return>");
    assert!(g.depends_on(ret, s_inner), "s.inner should reach <return> in:\n{g}");
}

#[test]
fn call_arg_edge_uses_callee_path() {
    let src = indoc! {r#"
        pub fn helper(_a: u64) {}
        pub fn f(x: u64) {
            helper(x);
        }
    "#};
    let g = graph_containing(src, "f");
    let x = g.slot_id_by_name("x").expect("x");
    let helper_arg = g.slots().iter().enumerate()
        .find(|(_, s)| s.display().contains("helper") && s.display().ends_with("#0"))
        .map(|(i, _)| lymph::SlotId(i as u32))
        .expect("helper#0 slot");
    assert!(g.depends_on(helper_arg, x), "x should reach helper#0 in:\n{g}");
}

#[test]
fn impl_methods_get_separate_graphs() {
    let src = indoc! {r#"
        pub struct T;
        impl T {
            pub fn a(&self) -> u64 { 0 }
            pub fn b(&self, x: u64) -> u64 { x }
        }
    "#};
    let _guard = DRIVER_LOCK.lock().unwrap();
    let graphs = analyze_source(src).expect("rustc driver ok");
    let names: Vec<_> = graphs.iter().map(|g| g.fn_name.clone()).collect();
    assert!(
        names.iter().any(|n| n.contains("T") && n.ends_with("::a")),
        "T::a not among {:?}", names
    );
    assert!(
        names.iter().any(|n| n.contains("T") && n.ends_with("::b")),
        "T::b not among {:?}", names
    );
}

#[test]
fn depends_on_traces_field_to_return() {
    // Aliasing lets collapse in MIR; field reads and struct fields survive.
    let src = indoc! {r#"
        pub struct S { pub a: u64, pub b: u64 }
        pub fn f(s: S) -> u64 {
            s.a
        }
    "#};
    let g = graph_containing(src, "f");
    let s_a = g.slot_id_by_name("s.a").expect("s.a");
    let s_b = g.slot_id_by_name("s.b");
    let ret = g.slot_id_by_name("<return>").expect("<return>");
    assert!(g.depends_on(ret, s_a), "<return> should depend on s.a in:\n{g}");
    assert!(!g.depends_on(s_a, ret), "s.a must not depend on <return>");
    if let Some(b) = s_b {
        assert!(!g.depends_on(ret, b), "b is unread; <return> must not depend on it");
    }
}

#[test]
fn forward_closure_separates_independent_inputs() {
    let src = indoc! {r#"
        pub struct Out { pub r: u64 }
        pub fn f(a: u64, _b: u64) -> Out {
            Out { r: a }
        }
    "#};
    let g = graph_containing(src, "f");
    let a = g.slot_id_by_name("a").expect("a");
    let b = g.slot_id_by_name("_b");
    let closure = g.forward_closure(a);
    assert!(closure.contains(&a), "closure must contain its seed");
    if let Some(bid) = b {
        assert!(!closure.contains(&bid), "_b is independent of a in:\n{g}");
    }
}

#[test]
fn predecessors_lists_direct_inputs() {
    let src = indoc! {r#"
        pub fn helper(_a: u64) {}
        pub fn f(x: u64) {
            helper(x);
        }
    "#};
    let g = graph_containing(src, "f");
    let x = g.slot_id_by_name("x").expect("x");
    let helper_arg = g.slots().iter().enumerate()
        .find(|(_, s)| s.display().contains("helper") && s.display().ends_with("#0"))
        .map(|(i, _)| lymph::SlotId(i as u32))
        .expect("helper#0 slot");
    let sources: Vec<_> = g.backward_closure(helper_arg).into_iter().collect();
    assert!(sources.contains(&x), "x must be in helper#0's backward closure in:\n{g}");
}

#[test]
fn field_types_are_resolved() {
    let src = indoc! {r#"
        pub struct S { pub a: u64 }
        pub fn f(s: S) -> u64 {
            s.a
        }
    "#};
    let g = graph_containing(src, "f");
    let s_a = g.slots().iter().find(|s| s.display() == "s.a");
    assert!(s_a.is_some(), "s.a slot missing from\n{}", g);
    assert_eq!(s_a.unwrap().ty, "u64", "s.a type != u64 in\n{}", g);
}

// --- signature-trust tests: return depends on all args, &mut pointee
// depends on other args. The body is irrelevant — these MUST hold even
// when the callee is opaque.

#[test]
fn return_depends_on_every_arg_through_opaque_callee() {
    // The driver inlines bodies aggressively; #[inline(never)] keeps
    // `combine` opaque so the dataflow has to come from the signature
    // assumption, not from MIR-level constant-propagation.
    let src = indoc! {r#"
        #[inline(never)]
        pub fn combine(x: u64, y: u64) -> u64 { x ^ y }
        pub fn f(a: u64, b: u64) -> u64 {
            let r = combine(a, b);
            r
        }
    "#};
    let g = graph_containing(src, "f");
    let a = g.slot_id_by_name("a").expect("a");
    let b = g.slot_id_by_name("b").expect("b");
    let r = g.slot_id_by_name("r").expect("r");
    let ret = g.slot_id_by_name("<return>").expect("<return>");
    assert!(g.depends_on(r, a), "r must depend on a in:\n{g}");
    assert!(g.depends_on(r, b), "r must depend on b in:\n{g}");
    assert!(g.depends_on(ret, a), "<return> must depend on a in:\n{g}");
    assert!(g.depends_on(ret, b), "<return> must depend on b in:\n{g}");
}

#[test]
fn return_chain_propagates_dependency_transitively() {
    let src = indoc! {r#"
        #[inline(never)] pub fn inner(x: u64) -> u64 { x }
        #[inline(never)] pub fn outer(y: u64) -> u64 { y }
        pub fn f(a: u64) -> u64 {
            let b = inner(a);
            let c = outer(b);
            c
        }
    "#};
    let g = graph_containing(src, "f");
    let a = g.slot_id_by_name("a").expect("a");
    let c = g.slot_id_by_name("c").expect("c");
    assert!(g.depends_on(c, a),
            "c should depend on a through inner→outer chain in:\n{g}");
}

// Find any slot whose display ends in `.(*)` and check that `from`
// reaches it. The mut-ref-pointee slot sits on a MIR temp (e.g.
// `_4.(*)`) — looking it up by exact name is brittle; reachability is
// the meaningful property.
fn any_deref_slot_reachable_from(g: &FlowGraph, from: lymph::SlotId) -> bool {
    let closure = g.forward_closure(from);
    closure.iter().any(|sid| {
        let s = g.slot(*sid);
        s.path.last().map(|p| p == "(*)").unwrap_or(false)
    })
}

#[test]
fn mut_ref_pointee_depends_on_other_args() {
    // The callee writes nothing in the body — but lymph must still
    // assume `*out` depends on `x` because the signature permits it.
    let src = indoc! {r#"
        #[inline(never)]
        pub fn write_into(_out: &mut u64, _x: u64) {}
        pub fn f(out: &mut u64, x: u64) {
            write_into(out, x);
        }
    "#};
    let g = graph_containing(src, "f");
    let x = g.slot_id_by_name("x").expect("x");
    assert!(any_deref_slot_reachable_from(&g, x),
            "x must reach some (*) slot post-call in:\n{g}");
}

#[test]
fn raw_mut_ptr_pointee_depends_on_other_args() {
    let src = indoc! {r#"
        #[inline(never)]
        pub unsafe fn write_into(_out: *mut u64, _x: u64) {}
        pub fn f(out: *mut u64, x: u64) {
            unsafe { write_into(out, x); }
        }
    "#};
    let g = graph_containing(src, "f");
    let x = g.slot_id_by_name("x").expect("x");
    assert!(any_deref_slot_reachable_from(&g, x),
            "x must reach some (*) slot post-call (raw *mut) in:\n{g}");
}

#[test]
fn shared_ref_pointee_does_not_get_dep_edges() {
    // `&T` is a SHARED ref — the callee can't write through it, so we
    // do NOT widen dependencies into the pointee. If an "out.(*)" slot
    // gets created at all, it must not have edges from `x`.
    let src = indoc! {r#"
        #[inline(never)]
        pub fn read_only(_out: &u64, _x: u64) -> u64 { 0 }
        pub fn f(out: &u64, x: u64) -> u64 {
            read_only(out, x)
        }
    "#};
    let g = graph_containing(src, "f");
    let x = g.slot_id_by_name("x").expect("x");
    // For a `&T` ref the callee can't write through it, so x must not
    // reach the pointee via a CallArg widening. The graph may still
    // build an `out.(*)` slot from the underlying place machinery, but
    // x SHOULD NOT have a path to it.
    assert!(!any_deref_slot_reachable_from(&g, x),
            "&T pointee must NOT be reachable from x (read_only signature) in:\n{g}");
    // Return still depends on both, since rule (1) applies to `&T`/`&mut T` alike.
    let ret = g.slot_id_by_name("<return>").expect("<return>");
    assert!(g.depends_on(ret, x),
            "<return> still depends on x via fan-in to <ret:read_only> in:\n{g}");
}

#[test]
fn mixed_return_and_mut_ref_dependencies() {
    // `process(&mut state, key, flag)` — both `*state` and the return
    // depend on every other arg.
    let src = indoc! {r#"
        #[inline(never)]
        pub fn process(_state: &mut u64, _key: u64, _flag: bool) -> u64 { 0 }
        pub fn f(state: &mut u64, key: u64, flag: bool) -> u64 {
            process(state, key, flag)
        }
    "#};
    let g = graph_containing(src, "f");
    let key = g.slot_id_by_name("key").expect("key");
    let flag = g.slot_id_by_name("flag").expect("flag");
    let ret = g.slot_id_by_name("<return>").expect("<return>");
    assert!(g.depends_on(ret, key),  "<return> depends on key in:\n{g}");
    assert!(g.depends_on(ret, flag), "<return> depends on flag in:\n{g}");
    assert!(any_deref_slot_reachable_from(&g, key),
            "(*state) reachable from key (mut-ref pointee) in:\n{g}");
    assert!(any_deref_slot_reachable_from(&g, flag),
            "(*state) reachable from flag (mut-ref pointee) in:\n{g}");
}

#[test]
fn unrelated_args_remain_independent_across_calls() {
    // `c` flows from `a` only; `b` is a dead arg. Even with the
    // signature-trust widening, `a` must not contaminate `b` or vice
    // versa unless they share a call frame.
    let src = indoc! {r#"
        #[inline(never)] pub fn id(x: u64) -> u64 { x }
        pub fn f(a: u64, b: u64) -> u64 {
            let _unused = id(b);
            id(a)
        }
    "#};
    let g = graph_containing(src, "f");
    let a = g.slot_id_by_name("a").expect("a");
    let b = g.slot_id_by_name("b").expect("b");
    assert!(!g.depends_on(a, b), "a must not depend on b in:\n{g}");
    assert!(!g.depends_on(b, a), "b must not depend on a in:\n{g}");
}
