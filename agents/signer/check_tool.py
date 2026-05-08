# Iterative-feedback tools for the signer agent. Two MCP tools:
#
#   check_types {types}              - rustc-validate type defs alone.
#   check_signature {types, sig}     - full validation: rustc + per-slot
#                                      register assignment + layout check.
#
# Both keep `types` and `signature` as separate fields so the agent never
# has to format a multi-line prelude into one string. The harness joins
# them only when forwarding to sigcheck/nacre.
from __future__ import annotations

from claude_agent_sdk import tool

import sigcheck
import nacre

# Surface rustc diagnostics from any nacre call.
from cli import with_compiler_errors


def make(bv, addr: int, prelude: str | None = None):
    """Bind the inspection tools to one (bv, addr) pair plus optional
    harness-supplied static prelude (e.g. from --prelude-file)."""

    @tool("check_types",
          "Compile a block of Rust type definitions and report rustc's "
          "verdict - use this to validate struct/enum/type aliases BEFORE "
          "trying to plug them into a signature. `types` is one Rust "
          "source string with as many `pub struct ... {{ }}`, `pub enum`, "
          "`type ... =`, and `use ...;` statements as you need. Returns "
          "'ok' on success or rustc's error output on failure.",
          {"types": str})
    async def check_types(args):
        t = (args.get("types") or "").strip()
        if not t:
            return {"content": [{"type": "text", "text": "error: types is empty"}]}
        try:
            with_compiler_errors(nacre.signature, "()", prelude=t)
        except Exception as e:
            return {"content": [{"type": "text",
                                 "text": f"types failed to compile:\n{e}"}]}
        return {"content": [{"type": "text", "text": "ok - types compile"}]}

    @tool("check_signature",
          "Check a candidate Rust function signature against the target. "
          "`signature` is the parens param list with optional return type, "
          "e.g. '(input: &str) -> u32'. `types` is an optional Rust source "
          "string containing struct/enum/use defs the signature references "
          "(`pub struct Foo {{ x: u64 }}`, `use std::collections::HashMap;`, "
          "etc.). The two fields are validated TOGETHER - types are "
          "rustc-checked first, then the signature is laid out using them "
          "and compared to the binary's actual register usage + layout.",
          {"types": str, "signature": str})
    async def check_signature(args):
        sig = (args.get("signature") or "").strip()
        if not sig:
            return {"content": [{"type": "text",
                                 "text": "error: signature is empty"}]}
        types = (args.get("types") or "").strip()
        # Pass types via prelude rather than concatenating into decl -
        # types blocks legitimately contain blank lines (between struct
        # defs), and sigcheck.split_decl splits decl on the FIRST blank
        # line, which would silently drop everything past the first def.
        full_prelude = "\n".join(p for p in (prelude or "", types) if p).strip() or None
        try:
            r = sigcheck.check_signature(bv, addr, sig, prelude=full_prelude)
        except Exception as e:
            return {"content": [{"type": "text",
                                 "text": f"check_signature failed: "
                                         f"{type(e).__name__}: {e}"}]}
        return {"content": [{"type": "text", "text": _format(r)}]}

    return [check_types, check_signature]


def _format(r) -> str:
    """Compact output: header + per-slot table + issues. Keeps token cost low."""
    lines = [
        f"signature={r.decl!r}",
        f"score={r.score:.2f}  perfect={r.perfect}  arity={r.arity_match}  "
        f"sret={r.sret_match}  return={r.return_match}",
    ]
    for s in r.slots:
        agree = "OK" if s.agree else "..."
        regs = ",".join(s.expected_regs) or "stack"
        obs  = ",".join(s.observed_regs) or "-"
        lines.append(f"  [{agree}] {s.name}: expected={regs} mode={s.expected_pass_mode} observed={obs}")
        if s.note:
            lines.append(f"        {s.note}")
        # Per-offset comparison: surface what the source-side type
        # exposes vs what the binary actually derefs through this arg.
        # The reg check is "did we land in the right registers?" - this
        # is "does the type's shape cover the offsets the binary touched?"
        if getattr(s, "expected_offsets", None) or getattr(s, "observed_offsets", None):
            sz = getattr(s, "expected_size", 0) or 0
            exp = ", ".join(f"{o:#x}" for o in (s.expected_offsets or [])) or "-"
            obs_off = ", ".join(f"{o:#x}" for o in (s.observed_offsets or [])) or "-"
            lines.append(f"        expected offsets (size={sz:#x}): {exp}")
            lines.append(f"        observed offsets:                 {obs_off}")
            # Highlight observed offsets that aren't in the expected
            # set - these are the smoking-gun "your struct doesn't
            # describe this byte" signals.
            if s.expected_offsets and s.observed_offsets:
                missing = [o for o in s.observed_offsets if o not in set(s.expected_offsets)]
                if missing:
                    lines.append(
                        "        observed but NOT in expected: "
                        + ", ".join(f"{o:#x}" for o in missing)
                    )
    if r.issues:
        lines.append("issues:")
        for i in r.issues:
            lines.append(f"  - {i}")
    return "\n".join(lines)


NAMES = frozenset({"check_types", "check_signature"})
