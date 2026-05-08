# Validator smoke test for the flower agent. Hand-rolled Rust source
# for source::State::jump in benchmark_183/2021-braintrust; asserts
# binding ok + diff ordering puts boundary entries first.
from __future__ import annotations

import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE.parent), str(_HERE.parent.parent / "common")]
os.environ.setdefault("BN_DISABLE_USER_PLUGINS", "1")

import binaryninja as bn
import consistency

ROOT = _HERE.parent.parent.parent
BNDB = ROOT / "bench/benchmark_183/2021-braintrust/binary.symtypes"
JUMP_ADDR = 0x4088b0

SOURCE = """
use std::collections::HashMap;
pub struct State { pub jt: HashMap<usize, usize>, pub tl: Vec<u8>, pub tr: Vec<u8>, pub tc: u8 }
impl State {
    fn jump(&mut self, i: usize, fwd: bool) -> usize {
        if fwd ^ (self.tc != 0) { self.jt[&i] } else { i }
    }
}
"""


def main() -> int:
    if not BNDB.exists():
        sys.stderr.write(f"missing fixture: {BNDB}\n")
        return 2
    bv = bn.load(str(BNDB))
    f = next(iter(bv.get_functions_at(JUMP_ADDR)))
    clean = consistency.clean_fn_name(f.symbol.short_name)
    assert clean == "jump", f"expected leaf 'jump', got {clean!r}"

    r = consistency.check(SOURCE, bv=bv, fn_addr=JUMP_ADDR, rust_fn_name=clean)

    # Binding works: self/i/fwd all in HLIL.
    assert not r.unbound, f"expected 0 unbound, got {r.unbound!r}"
    assert r.rust_var_count >= 3 and r.binary_var_count > 10

    # A correct reconstruction with HLIL-matched names must pass clean:
    # perfect, no warnings, no diffs. anemone's old behavior surfaced
    # spurious `binary_over` diffs from cross-variable phi inputs and
    # param-name reuse on memory-load defs; with those gone, the
    # validator should agree with itself end-to-end.
    assert r.perfect, f"correct submission must be perfect; feedback:\n{r.feedback}"
    assert not r.has_warnings, \
        f"correct submission should have no warnings; feedback:\n{r.feedback}"
    assert r.diffs_ordered == [], \
        f"correct submission should produce 0 diffs; got:\n" + "\n".join(r.diffs_ordered)

    # Dodge detection: the agent's escape-hatch attempt with `_i/_fwd`
    # must NO LONGER pass silently - it binds them as i/fwd and warns.
    DODGE = (SOURCE
             .replace("i: usize, fwd: bool", "_i: usize, _fwd: bool")
             .replace("fwd ^", "_fwd ^")
             .replace("self.jt[&i]", "self.jt[&_i]")
             .replace("else { i }", "else { _i }"))
    rd = consistency.check(DODGE, bv=bv, fn_addr=JUMP_ADDR, rust_fn_name=clean)
    assert rd.has_warnings, "dodge must surface a warning"
    assert "dodged" in rd.feedback, f"dodge warning missing:\n{rd.feedback}"

    # Antipattern detection: `_pad: [u8; 0x30]` is the explicit
    # cheese the prompt warns against - must surface a warning even
    # when dataflow agrees, so the submit hook bounces it for refinement.
    CHEESE = (
        "use std::collections::HashMap;\n"
        "#[repr(C)] pub struct State {\n"
        "    _pad: [u8; 0x30], jt: HashMap<usize, usize>, tc: u8,\n"
        "}\n"
        "impl State {\n"
        "  fn jump(&self, i: usize, fwd: bool) -> usize {\n"
        "    if (self.tc != 0) == fwd { return i; }\n"
        "    *self.jt.get(&i).expect(\"\")\n"
        "  }\n"
        "}\n"
    )
    rc = consistency.check(CHEESE, bv=bv, fn_addr=JUMP_ADDR, rust_fn_name=clean)
    assert rc.has_warnings, "antipattern must surface a warning"
    assert "antipattern" in rc.feedback or "skip-array" in rc.feedback, \
        f"antipattern warning missing:\n{rc.feedback[:300]}"

    print(f"PASS: jump - bound={r.rust_var_count}/{r.binary_var_count} "
          f"diffs={len(r.diffs_ordered)} perfect-clean  dodge_detected  cheese_detected")
    return 0


if __name__ == "__main__":
    sys.exit(main())
