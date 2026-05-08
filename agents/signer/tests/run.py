# Tests for the typing pipeline (nacre.signature + exoskeleton.trace_signature_bv
# stitched together by sigcheck.check_signature).
#
# Two fixture sources:
#  1. `fixtures/sigshapes.rs` — built fresh, all fns marked #[inline(never)]
#     so each survives as a testable symbol. Covers the diverse signature
#     patterns observed across the decompetition samples.
#  2. `2021-baby-rust::source::step` — the only decompetition fn that
#     survives un-decorated (recursive, so rustc can't inline it).
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
# agents/signer/tests/ -> agents/signer + agents/common (sigcheck imports cli)
sys.path[:0] = [str(_HERE.parent), str(_HERE.parent.parent / "common")]

import binaryninja as bn
from sigcheck import check_signature


HERE = Path(__file__).resolve().parent
# agents/signer/tests/ -> patina root is three parents up.
ROOT = HERE.parent.parent.parent
BENCH = ROOT / "bench/benchmark"


# (fn_name, decl, expect, [prelude_override])
# `expect`: "perfect" (==1.0), "agree" (>=.66), "weak" (>=.33)
SHAPES_CASES = [
    ("hash_str",      "(input: &str) -> u32",                  "perfect"),
    ("dj_str",        "(input: &str) -> u32",                  "perfect"),
    ("rand_step",     "(state: u64) -> u64",                   "perfect"),
    ("mut_string",    "(tape: &mut String, m: &Match)",        "agree"),
    ("string_step",   "(input: String) -> String",             "agree"),
    ("enco_two_args", "(key: &Vec<char>, input: &str) -> String", "agree"),
    ("trim_string",   "(s: &mut String)",                      "perfect"),
    ("str_to_string", "(input: &str) -> String",               "agree"),
    ("box_dyn_arg",   "(arg: &str) -> Box<dyn Tr>",            "agree"),
    ("copy_big",      "(b: Big) -> Big",                       "agree"),
    ("add_two",       "(a: u64, b: u64) -> u64",               "perfect"),
    ("six_args",      "(a: u64, b: u64, c: u64, d: u64, e: u64, f: u64) -> u64", "perfect"),
    ("opt_ptr",       "(p: Option<&u64>) -> Option<&u64>",     "perfect"),
    ("pair_ret",      "(seed: u64) -> (u64, u64)",             "agree"),
]

DECOMP_CASES = [
    ("2021-baby-rust", "source::step", "(input: String) -> String", "agree"),
]


def _build_shapes() -> Path:
    src = HERE / "fixtures" / "sigshapes.rs"
    out = Path("/tmp/sigcheck-sigshapes")
    cmd = ["rustup", "run", "nightly-2026-02-12", "rustc",
           "-C", "opt-level=1", "-C", "codegen-units=1",
           "--crate-name", "shapes",
           f"--remap-path-prefix={src.parent}=.",
           f"--remap-path-prefix={os.environ.get('HOME', '/home')}=~",
           "-o", str(out), str(src)]
    env = {**os.environ, "RUST_MIN_STACK": "16777216",
           "SOURCE_DATE_EPOCH": "0", "LC_ALL": "C"}
    res = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if res.returncode != 0:
        raise SystemExit(f"sigshapes build failed:\n{res.stderr}")
    return out


def lookup(bv, name: str, crate: str | None = None) -> int | None:
    """Try direct, then `crate::name`, then a linear scan over demangled
    full names. Binja's `get_symbols_by_name` matches the demangled
    `crate::name` form when available."""
    candidates = [name]
    if crate and "::" not in name:
        candidates.append(f"{crate}::{name}")
    for cand in candidates:
        syms = bv.get_symbols_by_name(cand)
        if syms:
            return syms[0].address
    leaf = name.split("::")[-1]
    for f in bv.functions:
        full = f.symbol.full_name or f.name or ""
        if full == name or full.endswith("::" + leaf):
            return f.start
    return None


def _verdict(expect: str, score: float, perfect: bool) -> bool:
    if expect == "perfect": return perfect
    if expect == "agree":   return score >= 0.66
    if expect == "weak":    return score >= 0.33
    return False


def main() -> int:
    fail = 0
    total = 0

    # ---- shape fixture -------------------------------------------------
    print("=== fixtures/sigshapes.rs ===")
    shapes_bin = _build_shapes()
    bv = bn.load(str(shapes_bin))
    src = (HERE / "fixtures" / "sigshapes.rs").read_text()
    try:
        for fn_name, decl, expect in SHAPES_CASES:
            total += 1
            addr = lookup(bv, fn_name, crate="shapes")
            if addr is None:
                print(f"  SKIP  {fn_name}  (inlined or DCE'd)")
                fail += 1
                continue
            r = check_signature(bv, addr, decl, prelude=src)
            ok = _verdict(expect, r.score, r.perfect)
            if not ok: fail += 1
            tag = "PASS" if ok else "FAIL"
            print(f"  {tag}  {fn_name:18s} score={r.score:.2f}  perfect={r.perfect}  ({expect})")
            if not ok:
                for i in r.issues[:3]:
                    print(f"        - {i}")
    finally:
        bv.file.close()

    # ---- raw decompetition --------------------------------------------
    print("\n=== decompetition raw samples ===")
    for sample, fn_name, decl, expect in DECOMP_CASES:
        total += 1
        bin_path = BENCH / sample / "binary"
        src_path = BENCH / sample / "source.rs"
        if not bin_path.exists():
            print(f"  SKIP  {sample}::{fn_name}  (missing fixture)")
            fail += 1
            continue
        bv = bn.load(str(bin_path))
        try:
            addr = lookup(bv, fn_name)
            if addr is None:
                print(f"  SKIP  {sample}::{fn_name}  (not in binary)")
                fail += 1
                continue
            r = check_signature(bv, addr, decl, prelude=src_path.read_text())
        finally:
            bv.file.close()
        ok = _verdict(expect, r.score, r.perfect)
        if not ok: fail += 1
        tag = "PASS" if ok else "FAIL"
        print(f"  {tag}  {sample}/{fn_name:25s} score={r.score:.2f}  perfect={r.perfect}  ({expect})")

    print(f"\n{'='*48}")
    print(f"  {total - fail}/{total} pass")
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
