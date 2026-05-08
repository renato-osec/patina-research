# Dump the full asm + HLIL of a target function to disk so the signer
# agent can mine it post-first-submit via Bash/grep without paying tool
# tokens to re-page through. The submit hook reveals the file paths +
# inlines the HLIL after the agent's first `submit_signature`, when the
# wide-tool gate also unlocks.
from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path


_DUMP_DIR_ENV = "PATINA_SIGNER_ASM_DIR"


def _safe_name(name: str, addr: int) -> str:
    """Make a filesystem-friendly stem out of `name` + addr fallback.
    Rust mangled names are long but file-safe; sanitize anyway."""
    stem = re.sub(r"[^A-Za-z0-9_.\-]", "_", name or "")[:96]
    return f"{stem}_{addr:x}" if stem else f"sub_{addr:x}"


def dump_function_asm(bv, fn_addr: int, *, name: str | None = None) -> Path:
    """Walk every instruction in the function at `fn_addr` and write a
    one-line-per-insn asm listing. Returns the file path. Idempotent -
    overwrites the same path each call so re-runs see fresh output."""
    funcs = bv.get_functions_at(fn_addr) or []
    f = funcs[0] if funcs else None
    if f is None:
        raise ValueError(f"no function at {fn_addr:#x}")
    out_dir = Path(os.environ.get(_DUMP_DIR_ENV, tempfile.gettempdir())) / "patina_signer"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{_safe_name(name or f.name, fn_addr)}.asm"

    lines: list[str] = []
    addrs: list[int] = []
    for block in f.basic_blocks:
        cur = block.start
        end = block.end
        while cur < end:
            text = bv.get_disassembly(cur) or ""
            try:
                ilen = bv.get_instruction_length(cur) or 0
            except Exception:
                ilen = 0
            if ilen <= 0:
                break
            addrs.append(cur)
            lines.append(f"{cur:08x}  {text}")
            cur += ilen
    # Sort by address so the file reads top-to-bottom in program order
    # (basic_blocks aren't address-sorted).
    paired = sorted(zip(addrs, lines), key=lambda p: p[0])
    path.write_text("\n".join(line for _, line in paired) + "\n")
    return path


def hlil_text(bv, fn_addr: int) -> str:
    """Render HLIL of the function as a string with one line per
    instruction prefixed by its origin address (matches the `decompile`
    tool's format). Returns "" if HLIL isn't available."""
    funcs = bv.get_functions_at(fn_addr) or []
    f = funcs[0] if funcs else None
    if f is None or f.hlil is None:
        return ""
    out: list[str] = []
    try:
        for ins in f.hlil.instructions:
            out.append(f"{ins.address:08x}  {ins}")
    except Exception:
        return ""
    return "\n".join(out)
