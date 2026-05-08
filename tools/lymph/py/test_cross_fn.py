"""Phase 2 (cross-fn slot stitching) regression tests for lymph.

When `analyze` is called with `root` + `depth`, the BFS-collected
bodies are merged into a single FlowGraph: root's slots keep their
bare names, callee bodies are absorbed under `<callee_path>::`
prefixes, and rendezvous slots (`<callee>#i`, `<ret:<callee>>`) are
shared by name so caller's call edges land on the same slots as the
callee's stitched param/return edges.

The merge is *additive*: it adds the callee body's interior to the
caller's graph and wires it up. It does NOT retract the conservative
"every arg taints the return / every &mut pointee" edges
`lower_terminator` already emitted, because those remain a sound
over-approximation when a deeper callee further down the BFS is
opaque. So the right correctness bar is "merge exposes the callee's
named locals + transitive flow inside them", not "merge tightens
caller-side queries".
"""

import sys
import lymph


FAIL = 0


def expect(label: str, want_true: bool, *, on, of, in_g):
    global FAIL
    got = in_g.depends_on(of, on)
    if got != want_true:
        FAIL += 1
        print(
            f"FAIL [{label}] depends_on(of={of!r}, on={on!r}) "
            f"in {in_g.fn_name!r}: got={got}, want={want_true}",
            file=sys.stderr,
        )


def expect_var(label: str, want_present: bool, *, name, in_g):
    global FAIL
    has = name in in_g.variables()
    if has != want_present:
        FAIL += 1
        print(
            f"FAIL [{label}] variable {name!r} present={has} want={want_present}",
            file=sys.stderr,
        )


def merged(src: str, root: str, depth: int) -> "lymph.FlowGraph":
    out = lymph.analyze(src, root=root, depth=depth)
    if not out:
        raise AssertionError(f"no graph returned for root={root!r} depth={depth}")
    if len(out) != 1:
        raise AssertionError(
            f"expected merged single graph, got {len(out)}: "
            f"{[g.fn_name for g in out]}"
        )
    return out[0]


# ---------------------------------------------------------------------- 1
# depth=0 = just entry's body; callee internals NOT visible.
# depth>=1 = callee body absorbed; callee's named locals show up
# under its `<callee_path>::` namespace prefix.
src = (
    "#[inline(never)] pub fn forward(arg_x: u64) -> u64 { arg_x }\n"
    "pub fn entry(seed: u64) -> u64 {\n"
    "    forward(seed)\n"
    "}\n"
)
g0 = merged(src, "entry", 0)
g1 = merged(src, "entry", 1)
expect_var("d=0: entry's own param visible", True, name="seed", in_g=g0)
expect_var("d=0: callee's param NOT in graph", False,
           name="forward::arg_x", in_g=g0)
expect_var("d=1: callee's param visible under prefix", True,
           name="forward::arg_x", in_g=g1)
expect_var("d=1: callee's <return> visible under prefix", True,
           name="forward::<return>", in_g=g1)


# ---------------------------------------------------------------------- 2
# Stitch wiring: caller's `<callee>#i` rendezvous flows into callee's
# i-th param; callee's <return> flows into caller's `<ret:<callee>>`.
# We can observe this directly via `successors` / `predecessors`.
g = merged(src, "entry", 1)
succ_of_arg0 = dict(g.successors("forward#0"))
assert "forward::arg_x" in succ_of_arg0, \
    f"forward#0 should fan into forward::arg_x, got {succ_of_arg0!r}"
preds_of_ret = dict(g.predecessors("<ret:forward>"))
assert "forward::<return>" in preds_of_ret, \
    f"<ret:forward> should be fed by forward::<return>, got {preds_of_ret!r}"


# ---------------------------------------------------------------------- 3
# Two-hop chain stitched all the way through: entry -> mid -> leaf.
# At depth=2 every body is in the graph; the named locals from each
# layer are reachable under their prefixes.
src = (
    "#[inline(never)] pub fn leaf(leaf_arg: u64) -> u64 { leaf_arg }\n"
    "#[inline(never)] pub fn mid(mid_arg: u64) -> u64 { leaf(mid_arg) }\n"
    "pub fn entry(top: u64) -> u64 { mid(top) }\n"
)
g2 = merged(src, "entry", 2)
expect_var("d=2: leaf::leaf_arg", True, name="leaf::leaf_arg", in_g=g2)
expect_var("d=2: mid::mid_arg",  True, name="mid::mid_arg",  in_g=g2)
expect("d=2: top reaches leaf::<return>", True,
       of="leaf::<return>", on="top", in_g=g2)
expect("d=2: top reaches entry's <return>", True,
       of="<return>", on="top", in_g=g2)


# ---------------------------------------------------------------------- 4
# Stitch survives the conservative `<ret:callee>` rule: a body that
# returns a constant still has the caller-side over-approx edge, AND
# the callee's interior shows the constant has no slot-level dep on
# the param. Document both.
src = (
    "#[inline(never)] pub fn drop_arg(_x: u64) -> u64 { 7 }\n"
    "pub fn entry(z: u64) -> u64 { drop_arg(z) }\n"
)
g = merged(src, "entry", 1)
# Caller-side conservative edge: z -> <ret:drop_arg> -> entry's <return>.
expect("conservative caller-side edge survives merge",
       True, of="<return>", on="z", in_g=g)
# Inside drop_arg, _x doesn't reach drop_arg::<return> — but the merged
# graph still has the conservative `<callee>#0 -> <ret:<callee>>` edge
# `lower_terminator` emitted on the caller side. The callee-precise
# edge would be `drop_arg::_x -> drop_arg::<return>`, which IS absent
# (asserting that the body's interior was correctly ingested).
deps_of_callee_ret = set(g.transitive_sources("drop_arg::<return>"))
assert "drop_arg::_x" not in deps_of_callee_ret, \
    f"drop_arg::_x must NOT reach drop_arg::<return>, but transitive_sources={deps_of_callee_ret!r}"


# ---------------------------------------------------------------------- 5
# Callee with `&mut` arg + Phase-1 ref aliasing: caller passes a ref,
# callee writes through the deref. Both layers contribute edges; the
# combined graph captures the cross-fn write.
src = (
    "#[inline(never)] pub fn write(dst: &mut u64, val: u64) { *dst = val }\n"
    "pub fn entry(seed: u64) -> u64 {\n"
    "    let mut local = 0;\n"
    "    write(&mut local, seed);\n"
    "    local\n"
    "}\n"
)
g0 = merged(src, "entry", 0)
g1 = merged(src, "entry", 1)
# Phase-1 ref aliasing inside `entry` already connects this at d=0
# (no need to descend into `write`'s body): the caller's
# &mut local + mut-ref-side-effect rule wires `seed -> local`.
expect("d=0: phase-1 alone connects seed -> local",
       True, of="local", on="seed", in_g=g0)
# At d=1 the callee's body is also present; same conclusion holds and
# the callee's interior slots show up.
expect("d=1: same conclusion + interior visible",
       True, of="local", on="seed", in_g=g1)
expect_var("d=1: write::val visible", True, name="write::val", in_g=g1)
expect_var("d=1: write::dst visible", True, name="write::dst", in_g=g1)


if FAIL:
    print(f"FAIL: cross-fn ({FAIL} case(s))")
    sys.exit(1)
print("PASS: cross-fn")
