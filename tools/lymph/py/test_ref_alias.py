"""Phase 1 (ref aliasing) regression tests for lymph.

Each case exercises a different shape of `_x = &place` introduction
where the dataflow now needs to cross a deref. Without ref aliasing,
slots `(*_x).f` and `place.f` are independent and no edge connects
them, so `depends_on` would miss the flow.
"""

import sys
import lymph


FAIL = 0


def expect(label: str, want_true: bool, *, depends_on, of, on, in_fn):
    """`expect("...", True, depends_on=g, of='a', on='b', in_fn='f')`."""
    global FAIL
    got = depends_on.depends_on(of, on)
    if got != want_true:
        FAIL += 1
        print(
            f"FAIL [{in_fn}] {of} {'depends_on' if want_true else 'must_NOT_depend_on'} {on}: got={got}",
            file=sys.stderr,
        )


def graphs_for(src: str) -> dict[str, "lymph.FlowGraph"]:
    return {g.fn_name.split("::")[-1]: g for g in lymph.analyze(src)}


# ---------------------------------------------------------------------- 1
# &mut field write through a mutable ref. r = &mut s; r.a = x; should
# connect x -> s.a but not x -> s.b. Bind both fields from named locals
# so the field slots both get interned (constant inits don't intern).
gs = graphs_for(
    "pub struct S { pub a: u64, pub b: u64 }\n"
    "pub fn f(x: u64, y: u64, z: u64) -> S {\n"
    "    let mut s = S { a: y, b: z };\n"
    "    let r = &mut s;\n"
    "    r.a = x;\n"
    "    s\n"
    "}\n"
)
g = gs["f"]
expect("ref-mut field write", True, depends_on=g, of="s.a", on="x", in_fn="f")
expect("ref-mut field write isolation", False,
       depends_on=g, of="s.b", on="x", in_fn="f")


# ---------------------------------------------------------------------- 2
# Mut-ref side effect through opaque callee. write(&mut a, x) should
# taint a with x via the existing mut-ref deref-write rule + aliasing.
gs = graphs_for(
    "#[inline(never)] pub fn write(dst: &mut u64, val: u64) { *dst = val }\n"
    "pub fn f(x: u64) -> u64 {\n"
    "    let mut a: u64 = 0;\n"
    "    write(&mut a, x);\n"
    "    a\n"
    "}\n"
)
g = gs["f"]
expect("mut-ref callee side effect", True, depends_on=g, of="a", on="x", in_fn="f")


# ---------------------------------------------------------------------- 3
# Ref re-binding chain: _y = _x after _x = &p should still let
# `(*_y).f` resolve to `p.f`.
gs = graphs_for(
    "pub struct S { pub a: u64 }\n"
    "pub fn f(x: u64, init: u64) -> S {\n"
    "    let mut s = S { a: init };\n"
    "    let r1 = &mut s;\n"
    "    let r2 = r1;\n"
    "    r2.a = x;\n"
    "    s\n"
    "}\n"
)
g = gs["f"]
expect("ref chain (mut)", True, depends_on=g, of="s.a", on="x", in_fn="f")


# ---------------------------------------------------------------------- 4
# Read through &T should connect into the underlying place. Take `s`
# by ref to keep it from being destructively moved before the field
# read; both fields get named-local inits so both slots intern.
gs = graphs_for(
    "pub struct S { pub a: u64, pub b: u64 }\n"
    "pub fn f(a: u64, b: u64) -> u64 {\n"
    "    let s = S { a, b };\n"
    "    let r = &s;\n"
    "    r.a\n"
    "}\n"
)
g = gs["f"]
expect("ref read", True, depends_on=g, of="<return>", on="s.a", in_fn="f")
expect("ref read isolation", False,
       depends_on=g, of="<return>", on="s.b", in_fn="f")


if FAIL:
    print(f"FAIL: ref-alias ({FAIL} case(s))")
    sys.exit(1)
print("PASS: ref-alias")
