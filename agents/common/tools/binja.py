# Binja inspection tools. Read the binary directly. Both arms get these.
from __future__ import annotations

import json

from claude_agent_sdk import tool

from .ctx import TargetCtx


def _tx(text: str):
    return {"content": [{"type": "text", "text": text}]}


def _parse_int(v, *, default=None) -> int:
    """Lenient address/integer parser. Accepts:
      - real ints                    (4286512)
      - "0x..." / "0X..."            ("0x416830")
      - BN listing-style hex zero-padded ("00416830")
      - underscores                  ("0x41_6830")
      - plain decimal                ("4286512")
    Falls back to `default` if the value is empty/None and a default is
    supplied; otherwise raises ValueError. Revvers think in hex; the
    agent shouldn't have to learn which JSON tool wants which encoding.
    """
    if v is None or v == "":
        if default is not None:
            return default
        raise ValueError("empty address")
    if isinstance(v, bool):
        raise ValueError(f"bool is not an address: {v!r}")
    if isinstance(v, int):
        return v
    s = str(v).strip().replace("_", "").replace(" ", "")
    if s.startswith(("0x", "0X")):
        return int(s[2:], 16)
    # BN listing-style: any non-decimal hex digit OR an 8-char form with a
    # leading zero (typical zero-padded address). If neither applies, treat
    # as decimal - that matches the JSON-numeric case roundtripped to str.
    if s and all(c in "0123456789abcdefABCDEF" for c in s):
        if any(c in "abcdefABCDEF" for c in s) or (len(s) == 8 and s[0] == "0"):
            return int(s, 16)
    return int(s)


def _comment_at(ctx: TargetCtx, func, addr: int) -> str:
    """Function-scoped comment first (matches BN UI), then BV-level fallback."""
    text = ""
    if func is not None:
        try:
            text = func.get_comment_at(addr) or ""
        except Exception:
            text = ""
    if not text:
        try:
            text = ctx.bv.get_comment_at(addr) or ""
        except Exception:
            text = ""
    return text


def _annotate(ctx: TargetCtx, func, addr: int, line: str) -> list[str]:
    """Append `  // comment` to `line`. Multi-line comments continue on their
    own lines, padded to align the `//` under the first one - same shape as
    BN's listing view."""
    text = _comment_at(ctx, func, addr)
    if not text:
        return [line]
    parts = text.splitlines() or [text]
    out = [f"{line}  // {parts[0]}"]
    pad = " " * (len(line) + 2)  # align under the "//" of the first line
    for cont in parts[1:]:
        out.append(f"{pad}// {cont}")
    return out


def make(ctx: TargetCtx):
    @tool("disasm",
          "Disassemble n bytes at addr. `addr` and `n` accept hex "
          "(`0x416830`, `00416830`) or decimal - anything a revver would "
          "type. Address-level comments are inlined after each "
          "instruction as `// ...`, matching the BN UI.",
          {"addr": str, "n": str})
    async def disasm(args):
        a = _parse_int(args["addr"])
        n = _parse_int(args["n"])
        f = next(iter(ctx.bv.get_functions_containing(a) or []), None)
        # Walk one instruction at a time so we can pair each address with its
        # comment. `bv.get_disassembly(addr, max_length=...)` only returns one
        # instruction's text per call.
        out: list[str] = []
        cur = a
        end = a + n
        while cur < end:
            text = ctx.bv.get_disassembly(cur) or ""
            if not text:
                break
            try:
                ilen = ctx.bv.get_instruction_length(cur) or 0
            except Exception:
                ilen = 0
            if ilen <= 0:
                # Best-effort fallback: emit current line then stop.
                out.extend(_annotate(ctx, f, cur, f"{cur:08x}  {text}"))
                break
            out.extend(_annotate(ctx, f, cur, f"{cur:08x}  {text}"))
            cur += ilen
        return _tx("\n".join(out))

    @tool("decompile",
          "HLIL decompilation of an ENTIRE function at addr or symbol. "
          "Heavy - for big functions this is hundreds of lines. Prefer "
          "`il_around` (focus on one asm address) or `il_range` (focus on "
          "an address range) when you only need semantics for a specific "
          "spot. Address-level comments are inlined after each line as "
          "`// ...`.",
          {"target": str})
    async def decompile(args):
        t = args["target"]
        f = ctx.func(int(t, 16) if t.startswith("0x") else t)
        if f is None:
            return _tx("not found")
        il = f.hlil
        out: list[str] = []
        if il:
            for ins in il.instructions:
                out.extend(_annotate(ctx, f, ins.address, f"{ins.address:08x}  {ins}"))
        else:
            out.append(str(f))
        return _tx("\n".join(out))

    def _il_view(f, view: str):
        return {"llil": f.llil, "mlil": f.mlil, "hlil": f.hlil}.get(view)

    async def _il_around_impl(args, view: str):
        addr = _parse_int(args["addr"])
        try:
            ctx_n = max(0, min(_parse_int(args.get("context"), default=5), 50))
        except (TypeError, ValueError):
            ctx_n = 5
        f = next(iter(ctx.bv.get_functions_containing(addr) or []), None)
        if f is None:
            return _tx(f"no function contains {addr:#x}")
        il = _il_view(f, view)
        if il is None or not hasattr(il, "instructions"):
            return _tx(f"no {view} for function at {f.start:#x}")
        insts = list(il.instructions)
        if not insts:
            return _tx("empty IL")
        best = min(range(len(insts)), key=lambda i: abs(insts[i].address - addr))
        lo = max(0, best - ctx_n)
        hi = min(len(insts), best + ctx_n + 1)
        out: list[str] = []
        for i in range(lo, hi):
            ins = insts[i]
            marker = ">" if i == best else " "
            out.extend(_annotate(
                ctx, f, ins.address,
                f"{marker} {ins.address:08x}  {ins}",
            ))
        return _tx("\n".join(out))

    async def _il_range_impl(args, view: str):
        start = _parse_int(args["start"])
        end = _parse_int(args["end"])
        if end <= start:
            return _tx(f"empty range: end {end:#x} <= start {start:#x}")
        f = next(iter(ctx.bv.get_functions_containing(start) or []), None)
        if f is None:
            return _tx(f"no function contains {start:#x}")
        il = _il_view(f, view)
        if il is None or not hasattr(il, "instructions"):
            return _tx(f"no {view} for function at {f.start:#x}")
        out: list[str] = []
        for ins in il.instructions:
            if start <= ins.address < end:
                out.extend(_annotate(
                    ctx, f, ins.address,
                    f"{ins.address:08x}  {ins}",
                ))
        return _tx("\n".join(out) or f"no {view} instructions in {start:#x}..{end:#x}")

    _AROUND_DOC = (
        "Targeted {VIEW} slice around ONE asm address - cheap focused "
        "counterpart to `decompile`. Pass an asm `addr` (typically one "
        "returned by `field_accesses`) and `context` = number of {VIEW} "
        "instructions to include on each side (default 5). The {VIEW} "
        "line whose origin address most-closely matches `addr` is "
        "marked `>` in the output. {DESC}"
    )
    _RANGE_DOC = (
        "{VIEW} for every instruction whose origin address falls in "
        "`[start, end)`. Use when you want {VIEW} for a contiguous span "
        "- e.g. one basic block from `disasm`, or the body of a loop "
        "spanning several asm lines - without dumping the whole function."
    )
    _DESC = {
        "hlil": "HLIL = highest-level, C-like decompilation. Default "
                "choice when you want semantics (callees, args, "
                "assignments).",
        "mlil": "MLIL = mid-level IL with named variables but ops still "
                "close to asm. Use when HLIL collapses too much.",
        "llil": "LLIL = lifted asm in expression-tree form. Use when "
                "you need register-level detail without raw asm.",
    }

    @tool("hlil_around", _AROUND_DOC.format(VIEW="HLIL", DESC=_DESC["hlil"]),
          {"addr": str, "context": str})
    async def hlil_around(args):
        return await _il_around_impl(args, "hlil")

    @tool("mlil_around", _AROUND_DOC.format(VIEW="MLIL", DESC=_DESC["mlil"]),
          {"addr": str, "context": str})
    async def mlil_around(args):
        return await _il_around_impl(args, "mlil")

    @tool("llil_around", _AROUND_DOC.format(VIEW="LLIL", DESC=_DESC["llil"]),
          {"addr": str, "context": str})
    async def llil_around(args):
        return await _il_around_impl(args, "llil")

    @tool("hlil_range", _RANGE_DOC.format(VIEW="HLIL"),
          {"start": str, "end": str})
    async def hlil_range(args):
        return await _il_range_impl(args, "hlil")

    @tool("mlil_range", _RANGE_DOC.format(VIEW="MLIL"),
          {"start": str, "end": str})
    async def mlil_range(args):
        return await _il_range_impl(args, "mlil")

    @tool("llil_range", _RANGE_DOC.format(VIEW="LLIL"),
          {"start": str, "end": str})
    async def llil_range(args):
        return await _il_range_impl(args, "llil")

    @tool("get_il",
          "Function IL: view in {llil,mlil,hlil}, ssa optional. `addr` "
          "accepts hex or decimal. Address-level comments are inlined "
          "after each line as `// ...`.",
          {"addr": str, "view": str, "ssa": bool})
    async def get_il(args):
        f = ctx.func(_parse_int(args["addr"]))
        if f is None:
            return _tx("not found")
        il = {"llil": f.llil, "mlil": f.mlil, "hlil": f.hlil}.get(args["view"], f.hlil)
        if args.get("ssa") and il and hasattr(il, "ssa_form"):
            il = il.ssa_form
        out: list[str] = []
        if il and hasattr(il, "instructions"):
            for ins in il.instructions:
                out.extend(_annotate(ctx, f, ins.address, f"{ins.address:08x}  {ins}"))
        else:
            out.append(str(f))
        return _tx("\n".join(out))

    @tool("functions_at",
          "Function start + name for symbol or 0xADDR match", {"q": str})
    async def functions_at(args):
        q = args["q"]
        if q.startswith("0x"):
            f = ctx.func(int(q, 16))
            out = [f"{f.start:#x}  {f.symbol.full_name}"] if f else []
        else:
            out = [
                f"{f.start:#x}  {f.symbol.full_name}"
                for f in ctx.bv.functions
                if q in (f.symbol.full_name or "")
            ]
        return _tx("\n".join(out[:50]) or "no matches")

    @tool("xrefs",
          "Code xrefs to an address: list of {from_function, from_address}. "
          "`addr` accepts hex (`0x416830`, `00416830`) or decimal.",
          {"addr": str})
    async def xrefs(args):
        out = []
        for ref in ctx.bv.get_code_refs(_parse_int(args["addr"])) or []:
            try:
                out.append({
                    "from_function": ref.function.name if ref.function else "",
                    "from_address": hex(ref.address),
                })
            except Exception:
                continue
        return _tx(json.dumps(out, indent=2) if out else "no xrefs")

    @tool("stack_vars",
          "Stack-frame variables of a function (name, storage, type). "
          "Default target is ctx.fn_addr.",
          {"target": str})
    async def stack_vars(args):
        t = (args.get("target") or "").strip() or hex(ctx.fn_addr)
        f = ctx.func(int(t, 16) if t.startswith("0x") else t)
        if f is None:
            return _tx(f"not found: {t}")
        rows = []
        for v in (f.vars or []):
            try:
                rows.append({
                    "name": v.name,
                    "storage": getattr(v, "storage", None),
                    "type": str(v.type),
                })
            except Exception:
                continue
        return _tx(json.dumps(rows, indent=2) if rows else "no variables")

    @tool("strings",
          "Search defined strings (substring filter). Up to count rows.",
          {"filter": str, "count": str})
    async def strings(args):
        sub = args.get("filter") or ""
        n = _parse_int(args.get("count"), default=200)
        out = []
        for s in ctx.bv.get_strings():
            try:
                txt = s.value
            except Exception:
                continue
            if sub and sub not in txt:
                continue
            out.append(f"{s.start:#x}  {txt[:160]!r}")
            if len(out) >= n:
                break
        return _tx("\n".join(out) or "no matches")

    @tool("get_user_type",
          "Definition of a user-defined type as it would appear in C.",
          {"name": str})
    async def get_user_type(args):
        name = (args.get("name") or "").strip()
        if not name:
            return _tx("empty name")
        ty = ctx.bv.types.get(name)
        if ty is None:
            return _tx(f"type not found: {name}")
        return _tx(f"{name}: {ty}")

    @tool("hexdump", "Hexdump bytes at addr (length default 64). `addr` "
          "and `length` accept hex or decimal.",
          {"addr": str, "length": str})
    async def hexdump(args):
        a = _parse_int(args["addr"])
        n = _parse_int(args.get("length"), default=64)
        data = ctx.bv.read(a, n) or b""
        if not data:
            return _tx(f"no data at {a:#x}")
        rows = []
        for i in range(0, len(data), 16):
            chunk = data[i : i + 16]
            hexs = " ".join(f"{b:02x}" for b in chunk)
            ascii_ = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
            rows.append(f"{a + i:016x}  {hexs:<47s}  {ascii_}")
        return _tx("\n".join(rows))

    return [disasm, decompile,
            hlil_around, mlil_around, llil_around,
            hlil_range, mlil_range, llil_range,
            get_il, functions_at,
            xrefs, stack_vars, strings, get_user_type, hexdump]


NAMES = frozenset({
    "disasm", "decompile",
    "hlil_around", "mlil_around", "llil_around",
    "hlil_range", "mlil_range", "llil_range",
    "get_il", "functions_at",
    "xrefs", "stack_vars", "strings", "get_user_type", "hexdump",
})
