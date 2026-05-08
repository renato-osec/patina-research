#!/usr/bin/env python3
# Apply WARP signatures to the stripped braintrust binary and verify
# the State::jump fn (sub_4088b0) gets renamed + typed.
#
# Two paths:
#   1. Auto - WARP runs as part of `update_analysis_and_wait()`.
#   2. Manual - use `binaryninja.warp.WarpContainer` to add a source
#      file (`rust-1.83.0-all.warp`), then `WarpFunction.apply()`
#      per matched fn.
from __future__ import annotations

import sys
from pathlib import Path

import binaryninja as bn

BINARY = Path(
    "/home/renny/doc/work/research/patina/bench/benchmark_stripped_183/2021-braintrust/binary"
)
SIGFILE = Path(
    "/home/renny/.binaryninja/signatures/rust-1.83.0/rust-1.83.0-all.warp"
)
TARGET_ADDR = 0x4088b0


def _named_fraction(bv) -> tuple[int, int]:
    n_named = sum(1 for f in bv.functions if not f.name.startswith("sub_"))
    return n_named, sum(1 for _ in bv.functions)


def _dump(bv, addr: int, label: str) -> None:
    funcs = bv.get_functions_at(addr) or []
    f = funcs[0] if funcs else None
    if f is None:
        print(f"[{label}] no fn at {addr:#x}"); return
    print(f"[{label}] {f.name!r}  type={f.type}")
    if f.hlil is not None:
        for ins in list(f.hlil.instructions)[:4]:
            print(f"  {ins.address:08x}  {ins}")


def _apply_via_python_api(bv) -> int:
    """Walk every fn; if WARP has a match for it via the loaded
    container(s), call WarpFunction.apply(fn). Returns count applied."""
    from binaryninja import warp
    applied = 0
    for f in bv.functions:
        try:
            wf = warp.WarpFunction.get_matched(f)
        except Exception:
            wf = None
        if wf is None:
            continue
        try:
            wf.apply(f)
            applied += 1
        except Exception as e:
            print(f"  apply failed for {f.name}@{f.start:#x}: {e}")
    return applied


def main() -> int:
    if not BINARY.exists():
        sys.stderr.write(f"missing binary: {BINARY}\n"); return 2
    bn._init_plugins()
    print(f"[warp] {len([c for c in bn.PluginCommand if 'WARP' in c.name])}"
          f" WARP plugin commands registered")

    print(f"[warp] loading {BINARY}")
    bv = bn.load(str(BINARY))
    if bv is None:
        sys.stderr.write("bn.load failed\n"); return 2

    n0, total = _named_fraction(bv)
    print(f"[warp] {n0}/{total} named pre-analysis")
    _dump(bv, TARGET_ADDR, "pre")

    print("[warp] update_analysis_and_wait()")
    bv.update_analysis_and_wait()
    n1, _ = _named_fraction(bv)
    print(f"[warp] {n1}/{total} named post-analysis (auto-WARP)")
    _dump(bv, TARGET_ADDR, "post-analysis")

    # If auto-WARP didn't pick up the rust sigs, register the file
    # via the WarpContainer Python API + apply per-fn.
    target = bv.get_function_at(TARGET_ADDR)
    if target and target.name.startswith("sub_"):
        print(f"\n[warp] target still unnamed - registering {SIGFILE.name}")
        from binaryninja import warp
        containers = warp.WarpContainer.all()
        print(f"[warp] containers: {[c.name for c in containers]}")
        # Pick a writable container (fallback: first).
        container = next(
            (c for c in containers if c.is_source_writable),
            containers[0] if containers else None,
        )
        if container is None:
            print("[warp] no WarpContainer available")
            return 2
        print(f"[warp] using container {container.name!r}")
        try:
            src = container.add_source(str(SIGFILE))
            print(f"[warp] add_source -> {src}")
        except Exception as e:
            print(f"[warp] add_source failed: {e}")
        bv.update_analysis_and_wait()
        applied = _apply_via_python_api(bv)
        print(f"[warp] applied via python API: {applied} fn(s)")
        bv.update_analysis_and_wait()
        n2, _ = _named_fraction(bv)
        print(f"[warp] {n2}/{total} named after manual sig + apply")
        _dump(bv, TARGET_ADDR, "post-manual")

    out = BINARY.with_suffix(".warped.bndb")
    try:
        bv.create_database(str(out))
        print(f"\n[warp] saved -> {out}")
    except Exception as e:
        print(f"[warp] save failed: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
