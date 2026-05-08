"""Smoke test for the shared binja read-tool factory in
`agents/common/tools/binja.py`. Loads a fixed .bndb and exercises
both direct `TargetCtx.func()` lookup and the `@tool`-wrapped
`functions_at` surface against a known function.

Fixture: `marinated-braintrust.signed.bndb`, copied from a real
signer pipeline run. Function 0x4079a0 is
`core::option::expect_failed`, retyped by the signer to
`void f(char const* msg_ptr, uint64_t msg_len) __noreturn`.

Run via:
    bash agents/common/tests/run.sh
or directly (with the standard LD_PRELOAD env from .envrc.local):
    .../agents/venv/bin/python test_binja_tools.py
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
# tests/ -> common/ + common/tools (bootstraps `from tools import binja`).
sys.path[:0] = [str(_HERE.parent)]
os.environ.setdefault("BN_DISABLE_USER_PLUGINS", "1")

import binaryninja as bn

from tools import binja as t_binja
from tools.ctx import TargetCtx


BNDB = _HERE / "fixtures" / "marinated-braintrust.signed.bndb"
TARGET_ADDR = 0x4079a0
EXPECTED_NAME = "_ZN4core6option13expect_failed17h95d2432053ef5ebbE"


FAIL = 0


def expect(label: str, got, want):
    global FAIL
    if got != want:
        FAIL += 1
        sys.stderr.write(f"FAIL {label}\n  got:  {got!r}\n  want: {want!r}\n")


def expect_in(label: str, needle, hay):
    global FAIL
    if needle not in hay:
        FAIL += 1
        sys.stderr.write(f"FAIL {label}\n  needle: {needle!r}\n  hay:    {hay!r}\n")


async def main() -> int:
    bv = bn.load(str(BNDB))
    assert bv is not None, f"bn.load failed: {BNDB}"
    ctx = TargetCtx(bv=bv, fn_addr=TARGET_ADDR)

    # 1. Direct lookup via ctx.func — every tool relies on this resolver
    #    accepting both `0xADDR` strings and bare ints.
    f = ctx.func(TARGET_ADDR)
    assert f is not None, f"ctx.func({TARGET_ADDR:#x}) returned None"
    expect("ctx.func name", f.name, EXPECTED_NAME)

    # 2. Function prototype written by the signer agent. Locks in the
    #    apply-pipeline + binja's parse_type_string round-trip.
    proto = str(f.type)
    expect_in("proto: char* msg_ptr", "char const* msg_ptr", proto)
    expect_in("proto: uint64_t msg_len", "uint64_t msg_len", proto)
    expect_in("proto: __noreturn", "__noreturn", proto)
    expect_in("proto: void return", "void", proto)

    # 3. Tool factory smoke — `functions_at` is the simplest @tool to
    #    drive, exercises both the wrapper plumbing and ctx.func.
    tools = {t.name: t for t in t_binja.make(ctx)}
    expect_in("tool registry: functions_at present", "functions_at", set(tools))
    res = await tools["functions_at"].handler({"q": f"{TARGET_ADDR:#x}"})
    text = "".join(blk.get("text", "") for blk in res.get("content", []))
    expect_in("functions_at returns the demangled name",
              "core::option::expect_failed", text)
    expect_in("functions_at returns the address",
              f"{TARGET_ADDR:#x}", text)

    # 4. Sanity: tool returns the empty-match path for a bogus query.
    res = await tools["functions_at"].handler({"q": "definitely_not_a_symbol_xyz"})
    miss_text = "".join(blk.get("text", "") for blk in res.get("content", []))
    expect_in("functions_at miss path",
              "no matches", miss_text)

    bv.file.close()
    return 0


if __name__ == "__main__":
    asyncio.run(main())
    if FAIL:
        print(f"FAIL: binja-tools ({FAIL} case(s))")
        sys.exit(1)
    print("PASS: binja-tools")
