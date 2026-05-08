"""Regression test for the user's HashMap-mutation case:

    m.lookup.insert(1, r);                     // Phase 1 wires r -> m.lookup
    let s = m.lookup_or_passthrough(...);      // Phase 2 absorbs callee body
                                               // Phase 2.5 substitutes
                                               //   `(*self).lookup` -> `m.lookup`
                                               // so the callee's reads route
                                               // through caller's slot tree

Net: `s.depends_on(r)` connects through the caller-side `m.lookup`
slot — no `m.lookup -> m` leaf-to-root edge needed; the
substitution bypasses `m` entirely.
"""

import sys
import lymph

src = """
use std::collections::HashMap;

pub struct Map {
    pub passthrough: bool,
    pub lookup: HashMap<u64, u64>,
}
impl Map {
    pub fn new(passthrough: bool) -> Map {
        Map { passthrough, lookup: HashMap::new() }
    }
    pub fn lookup_or_passthrough(&self, key: u64, passthrough_flag: bool) -> u64 {
        if self.passthrough == passthrough_flag { key }
        else { self.lookup[&key] }
    }
}

pub fn entry(r: u64) {
    let mut m = Map::new(true);
    m.lookup.insert(1, r);
    let s = m.lookup_or_passthrough(1, false);
    let _ = s;
}
"""

g = lymph.analyze(src, root="entry", depth=2)[0]

FAIL = 0
def expect(label, want, *, of, on):
    global FAIL
    got = g.depends_on(of, on)
    if got != want:
        FAIL += 1
        print(f"FAIL {label}: depends_on({of!r}, {on!r}) = {got}, want={want}",
              file=sys.stderr)


# --- guarantees Phase 1+2 already provide ---
expect("phase-1 wires r -> m.lookup",      True,  of="m.lookup", on="r")
expect("conservative wires s -> m",        True,  of="s",        on="m")
expect("callee param visible (s -> via &m)",  True,
       of="s", on="Map::lookup_or_passthrough::self")

# --- Phase 2.5: cross-fn ref propagation ---
# Caller passed `&m`, so callee's `(*self).lookup` reads aliased to
# caller's `m.lookup` slot. Combined with Phase 1's mut-ref-into-
# m.lookup write, `s.depends_on(r)` closes.
expect("phase-2.5: r flows through callee body to s",
       True, of="s", on="r")
# `m.lookup -> m` is still NOT a leaf-to-root edge — and we don't need
# it because the substitution routes around `m`. Pin this so we don't
# regress into adding noisy leaf-to-root flow that breaks isolation.
expect("no leaf-to-root over-approximation",
       False, of="m", on="m.lookup")

if FAIL:
    print(f"FAIL: phase25-boundary ({FAIL} case(s))")
    sys.exit(1)
print("PASS: phase25-boundary")
