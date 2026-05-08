# Exoskeleton-backed inspection tools for the signer agent.
#
# The agent needs an *immediate* view of which arg registers the target
# function actually reads + what shape it derefs through them. That's
# exactly what `exoskeleton.trace_signature_bv` produces - the same
# observation `sigcheck.check_signature` uses on the binary side. Expose
# it as an MCP tool so the agent can ask the binary directly instead of
# squinting at HLIL or stack_vars.
from __future__ import annotations

from typing import Any

from claude_agent_sdk import tool

import exoskeleton

from tools.binja import _parse_int


def _fmt_node(n: dict, indent: int = 0) -> list[str]:
    pad = "  " * indent
    flav = "ptr" if n.get("is_ptr") else "sca" if n.get("is_scalar") else "?"
    counts = []
    if n.get("reads"):
        counts.append(f"r={n['reads']}")
    if n.get("writes"):
        counts.append(f"w={n['writes']}")
    suffix = (" " + " ".join(counts)) if counts else ""
    out = [f"{pad}+{n['offset']:#06x} {n['size']:>2}B {flav}{suffix}"]
    for c in n.get("children", []) or []:
        out.extend(_fmt_node(c, indent + 1))
    return out


_BLIND_SPOT_THRESHOLD = 0x10  # 16 bytes - below this, padding-sized; above, real fields hide here


def _blind_spot_warning(trace: list) -> str | None:
    """If the lowest observed offset is well past 0, the function may
    not exercise the early fields of a larger struct (typical when one
    method only touches a subset, e.g. `jump()` reading the Vec/bool
    suffix while the HashMap prefix sits at offset 0 unused). Warn the
    agent so they go ask the `destructor` subagent - drop_in_place::<T>
    walks every owned field regardless of which methods touch them."""
    if not trace:
        return None
    try:
        lo = min(int(n.get("offset") or 0) for n in trace)
    except (TypeError, ValueError):
        return None
    if lo < _BLIND_SPOT_THRESHOLD:
        return None
    return (
        f"WARNING: lowest observed offset is {lo:#x} - bytes 0..{lo:#x} "
        "weren't touched by this function. They almost certainly hold "
        "REAL FIELDS this method just doesn't use (e.g. a HashMap or "
        "Vec the constructor populated). Spawn the `destructor` "
        "subagent - drop_in_place::<T> walks every field regardless of "
        "which methods touch them."
    )


def _fmt_signature(sig: dict) -> str:
    lines = [f"sret_likely={sig['sret_likely']}", "args:"]
    for a in sig["args"]:
        if not a["used"]:
            lines.append(f"  {a['register']:<4} -- not used")
            continue
        wd = ", writes_dominant" if a.get("writes_dominant") else ""
        n_nodes = len(a["trace"])
        # Distinguish "scalar arg consumed directly" (no deref) from
        # "ptr arg with N fields deref'd through it". Same `used` flag
        # but very different shapes - agents kept misreading the
        # zero-node case as missing data.
        if n_nodes == 0:
            lines.append(
                f"  {a['register']:<4} scalar (value used directly - "
                f"no derefs through this reg{wd})"
            )
            continue
        lines.append(
            f"  {a['register']:<4} ptr-base  ({n_nodes} field"
            f"{'s' if n_nodes != 1 else ''} deref'd through this reg{wd})"
        )
        for n in a["trace"]:
            lines.extend("    " + ln for ln in _fmt_node(n))
        warn = _blind_spot_warning(a["trace"])
        if warn:
            lines.append(f"    {warn}")
    r = sig["ret"]
    lines.append("return:")
    if r["via"] == "sret":
        lines.append(f"  via=sret  ({len(r['access_tree'])} access nodes through rdi)")
        for n in r["access_tree"]:
            lines.extend("    " + ln for ln in _fmt_node(n))
    elif r["via"] == "rax":
        lines.append(
            f"  via=rax  n_returns={r['n_returns']}  "
            f"ptr={r['is_ptr_count']}  scalar={r['is_scalar_count']}  "
            f"unknown={r['unknown_count']}"
        )
    else:
        lines.append(f"  via={r['via']}")
    return "\n".join(lines)


def _fmt_reg_trace(reg: str, trace: list) -> str:
    if not trace:
        return (f"{reg}: no derefs through this reg. Either it's a direct "
                f"scalar arg (value passed/used as-is, no struct behind it) "
                f"or it's not consumed at all - register_trace's `used` "
                f"flag will tell you which.")
    lines = [f"{reg}: {len(trace)} top-level access node(s)"]
    for n in trace:
        lines.extend("  " + ln for ln in _fmt_node(n))
    warn = _blind_spot_warning(trace)
    if warn:
        lines.append(warn)
    return "\n".join(lines)


def make(bv: Any, addr: int):
    """Bind exoskeleton inspection tools to one (bv, addr) pair."""

    @tool("register_trace",
          "Binary-side register usage for the target function. Shows which "
          "SysV-x64 arg regs (rdi..r9) are read at entry, the access tree "
          "behind each (offsets / sizes / ptr-or-scalar / read+write counts "
          "/ children for ptr derefs), sret_likely, and rax-return "
          "classification. This is the EXACT view sigcheck.check_signature "
          "uses on the binary side - call it FIRST to plan your decl.",
          {})
    async def register_trace(_args):
        sig = exoskeleton.trace_signature_bv(bv, addr)
        return {"content": [{"type": "text", "text": _fmt_signature(sig)}]}

    @tool("trace_register",
          "Per-register access tree (offsets dereferenced through one "
          "specific arg reg + the shapes behind any pointer fields). "
          "Useful when register_trace shows a register is used but you "
          "want a deeper / cleaner view of its struct shape. `register` "
          "in {rdi, rsi, rdx, rcx, r8, r9}; default rdi.",
          {"register": str})
    async def trace_register(args):
        reg = (args.get("register") or "rdi").lower()
        if reg not in {"rdi", "rsi", "rdx", "rcx", "r8", "r9"}:
            return {"content": [{"type": "text",
                                 "text": f"unknown register {reg!r}; expected one of "
                                         "rdi/rsi/rdx/rcx/r8/r9"}]}
        trace = exoskeleton.trace_function_bv(bv, addr, reg)
        return {"content": [{"type": "text", "text": _fmt_reg_trace(reg, trace)}]}

    @tool("field_accesses",
          "List every direct load/store at `[register + offset]` in this "
          "function with surrounding asm context - like `grep -A N -B N` "
          "for struct-field touches. For each access, returns the insn "
          "address + kind (read/write) + size + the access's own asm, "
          "plus `context` lines of asm before AND after. **Call this "
          "BEFORE `decompile` for any offset that register_trace flagged "
          "as interesting** - usually the surrounding asm tells you what "
          "callee the loaded value flows into / what constant a write "
          "came from, which is exactly the type evidence you need. "
          "Decompilation only earns its keep when field_accesses leaves "
          "you guessing.\n\n"
          "`register` ∈ rdi..r9, `offset` is the field offset (int), "
          "`context` is the # of nearby asm lines to include on each "
          "side (default 3, max ~10 - bigger windows blow up tokens).\n\n"
          "Limitation: only catches accesses through the entry-version "
          "register directly (`mov rax, [rdi+0x10]`). Accesses through a "
          "derived reg (`mov rcx, rdi; mov rax, [rcx+0x10]`) won't show "
          "up here; register_trace's offset list does cover those, just "
          "without addresses - `disasm` near the function start is the "
          "fallback.",
          {"register": str, "offset": str, "context": str})
    async def field_accesses(args):
        reg = (args.get("register") or "rdi").lower()
        if reg not in {"rdi", "rsi", "rdx", "rcx", "r8", "r9"}:
            return {"content": [{"type": "text",
                                 "text": f"unknown register {reg!r}; expected one of "
                                         "rdi/rsi/rdx/rcx/r8/r9"}]}
        try:
            offset = _parse_int(args.get("offset"), default=0)
        except (TypeError, ValueError):
            return {"content": [{"type": "text",
                                 "text": f"offset must be int (hex or decimal), "
                                         f"got {args.get('offset')!r}"}]}
        try:
            ctx = max(0, min(_parse_int(args.get("context"), default=3), 20))
        except (TypeError, ValueError):
            ctx = 3
        sites = exoskeleton.field_accesses_bv(bv, addr, reg, offset, ctx)
        if not sites:
            return {"content": [{"type": "text",
                                 "text": f"{reg}+{offset:#x}: no direct accesses found "
                                         "(might be reached via a derived reg - see register_trace)"}]}
        lines = [f"{reg}+{offset:#x}: {len(sites)} direct access{'es' if len(sites) != 1 else ''}"]
        for i, s in enumerate(sites):
            if i:
                lines.append("  ---")
            for addr_b, asm_b in (s.get("before") or []):
                lines.append(f"    {addr_b:#x}        {asm_b}")
            asm = (s.get("asm") or "").strip()
            lines.append(
                f"  > {s['address']:#x}  {s['kind']:>5s}  {s['size']}B  {asm}"
            )
            for addr_a, asm_a in (s.get("after") or []):
                lines.append(f"    {addr_a:#x}        {asm_a}")
        return {"content": [{"type": "text", "text": "\n".join(lines)}]}

    return [register_trace, trace_register, field_accesses]


NAMES = frozenset({"register_trace", "trace_register", "field_accesses"})
