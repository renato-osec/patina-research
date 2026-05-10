#!/usr/bin/env python3
"""Read a saved .bndb and enumerate the propagation footprint of one
signer recovery: what user prototype was set, every caller + callee,
field-access sites for each user-defined struct mentioned in the
prototype.

Usage:
    bndb_propagation.py <path.bndb> <fn_addr_or_name> [--json]

Prints a markdown report by default; --json dumps the same data as
JSON. Works on any bndb that already had signer applied; doesn't
need the live chain run.
"""
from __future__ import annotations
import json
import os
import re
import sys
from pathlib import Path

os.environ.setdefault("BN_DISABLE_USER_PLUGINS", "1")
import binaryninja as bn  # noqa: E402


_TYPE_NAME_RE = re.compile(r"\b([A-Z][A-Za-z0-9_]*)\b")


def _resolve_fn(bv: bn.BinaryView, target: str) -> bn.Function | None:
    if target.startswith("0x"):
        try:
            return bv.get_function_at(int(target, 16))
        except Exception:
            return None
    matches = [f for f in bv.functions if f.name == target or
               (f.symbol and f.symbol.short_name == target)]
    return matches[0] if matches else None


def _proto_str(fn: bn.Function) -> str:
    try:
        return str(fn.type)
    except Exception:
        return ""


def _user_types_referenced(proto: str, bv: bn.BinaryView) -> list[str]:
    """Best-effort: pull TitleCase tokens from the prototype, intersect
    with user-defined types in the bv."""
    user = {str(n) for n in bv.user_type_container.types.keys()} if hasattr(
        bv, "user_type_container") else set()
    if not user:
        try:
            user = {str(n) for n, _ in bv.types.items()}
        except Exception:
            user = set()
    return sorted({m for m in _TYPE_NAME_RE.findall(proto) if m in user})


def _hlil_line_at(fn: bn.Function, addr: int) -> str:
    try:
        for inst in fn.hlil.instructions:
            if inst.address == addr:
                return str(inst).strip()
    except Exception:
        pass
    return ""


def _propagation(bv: bn.BinaryView, fn: bn.Function) -> dict:
    proto = _proto_str(fn)
    user_types = _user_types_referenced(proto, bv)
    callers = []
    for c in (fn.callers or []):
        sample_addr = next((x.address for x in bv.get_code_refs(fn.start)
                            if bv.get_function_at(x.function.start) == c),
                           None) if hasattr(bv, "get_code_refs") else None
        line = _hlil_line_at(c, sample_addr) if sample_addr is not None else ""
        callers.append({"addr": f"{c.start:#x}", "name": c.name,
                        "callsite": (f"{sample_addr:#x}"
                                     if sample_addr is not None else ""),
                        "hlil": line})
    callees = [{"addr": f"{c.start:#x}", "name": c.name}
               for c in (fn.callees or [])]
    type_refs: dict[str, list[str]] = {}
    for name in user_types:
        try:
            refs = list(bv.get_code_refs_for_type(name) or [])
        except Exception:
            refs = []
        type_refs[name] = [f"{r.address:#x}" for r in refs[:64]]
    return {
        "fn_addr": f"{fn.start:#x}",
        "fn_name": fn.name,
        "user_prototype": proto,
        "user_types_in_proto": user_types,
        "callers": callers,
        "callees": callees,
        "type_refs": type_refs,
    }


def _md(p: dict) -> str:
    out = [f"# {p['fn_name']} @ {p['fn_addr']}\n",
           f"**Prototype:** `{p['user_prototype']}`\n",
           f"**User types in prototype:** {', '.join(p['user_types_in_proto']) or '(none)'}\n",
           f"\n## Callers ({len(p['callers'])})\n"]
    for c in p["callers"]:
        line = f"- `{c['addr']}` `{c['name']}`"
        if c["callsite"]:
            line += f"  callsite `{c['callsite']}`"
        if c["hlil"]:
            line += f"\n  ```\n  {c['hlil']}\n  ```"
        out.append(line)
    out.append(f"\n## Callees ({len(p['callees'])})\n")
    for c in p["callees"]:
        out.append(f"- `{c['addr']}` `{c['name']}`")
    out.append(f"\n## Type-ref sites ({sum(len(v) for v in p['type_refs'].values())})\n")
    for name, refs in p["type_refs"].items():
        out.append(f"- `{name}`: {len(refs)} ref(s)")
        for r in refs[:8]:
            out.append(f"    - `{r}`")
        if len(refs) > 8:
            out.append(f"    - ... +{len(refs)-8} more")
    return "\n".join(out)


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__, file=sys.stderr)
        return 2
    path = Path(sys.argv[1])
    target = sys.argv[2]
    json_out = "--json" in sys.argv[3:]
    bv = bn.load(str(path))
    if bv is None:
        print(f"could not load {path}", file=sys.stderr)
        return 2
    fn = _resolve_fn(bv, target)
    if fn is None:
        print(f"fn not found: {target}", file=sys.stderr)
        return 2
    p = _propagation(bv, fn)
    if json_out:
        print(json.dumps(p, indent=2))
    else:
        print(_md(p))
    return 0


if __name__ == "__main__":
    sys.exit(main())
