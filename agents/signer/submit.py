# Submit tool + PostToolUse validator-bounce hook for the signer agent.
from __future__ import annotations

from typing import Any, Callable

from claude_agent_sdk import tool, HookMatcher


# (perfect, feedback, has_warnings, arity_only, score in [0,1])
Validator = Callable[[str], tuple[bool, str, bool, bool, float]]
APPLY_SCORE_THRESHOLD = 0.85


def _txt(msg: str) -> dict:
    return {"content": [{"type": "text", "text": msg}]}


def make(
    *,
    validator: Validator | None = None,
    max_rounds: int = 3,
    server_name: str = "signer",
    asm_path: str | None = None,
    hlil: str | None = None,
    force_iterate_first: bool = True,
    apply_ctx: Any = None,
):
    """Returns `(tools, captured, hook_matcher_or_None)`."""
    captured: dict = {
        "types": "", "signature": "", "name": "", "decl": "",
        "confidence": 0.0, "rationale": "",
        "attempts": 0, "validations": [],
        "exhausted": False, "scolded": False, "applied": "",
    }

    @tool("submit_signature",
          "Submit your final answer. `signature` is the parens param list "
          "with optional return type ('(input: &str) -> u32'). `types` "
          "is an optional Rust source string containing struct/enum/use "
          "defs the signature references - same surface as check_signature. "
          "Optional `name` is a Rust-style symbol you'd like the function "
          "renamed to in the bndb (e.g. 'parse_token'); leave empty to "
          "keep the existing symbol. `confidence` is 0..1 (your own "
          "estimate). Optional `rationale` is a one-sentence why. The "
          "harness re-validates exactly the same way check_signature does; "
          "any rejected submission gets bounced back so you can refine.",
          {"types": str, "signature": str, "name": str,
           "confidence": float, "rationale": str})
    async def submit_signature(args):
        types = (args.get("types") or "").strip()
        sig = (args.get("signature") or "").strip()
        captured["types"] = types
        captured["signature"] = sig
        captured["name"] = (args.get("name") or "").strip()
        captured["decl"] = f"{types}\n\n{sig}" if types else sig
        captured["confidence"] = float(args.get("confidence", 0.0))
        captured["rationale"] = args.get("rationale", "")
        return _txt(f"submitted: signature={sig!r} "
                    f"(confidence={captured['confidence']:.2f})")

    if validator is None:
        return [submit_signature], captured, None

    qualified = f"mcp__{server_name}__submit_signature"

    async def _apply_to_bv(types: str, sig: str, agent_name: str) -> str:
        """Translate `(types, sig)` -> C via nacre.c_signature and apply
        to the shared bndb. Mirrors marinator/write.py's primitives:
        parse_types_from_string + define_user_type for the struct
        catalog, parse_type_string + f.type/reanalyze for the prototype,
        all under ctx.write_lock + bv.undoable_transaction(). When
        `agent_name` is non-empty the function is renamed first so the
        prototype binds to the agent's chosen symbol. Returns a short
        status string for the captured record. No-op when apply_ctx is
        None or the submission is empty.
        """
        if apply_ctx is None or not sig.strip():
            return "skip: no apply_ctx" if apply_ctx is None else "skip: empty sig"
        # Sidecar write is unconditional and FIRST: nacre often fails
        # on stdlib types (Box<[u8]>, &str -> String), and downstream
        # stages only need the Rust sig, not the bv-applied C decl.
        if apply_ctx.recoveries is not None:
            try:
                fn_now = apply_ctx.target_func()
                apply_ctx.recoveries.update(
                    apply_ctx.fn_addr, "signer",
                    rust_types=types, rust_signature=sig,
                    name=agent_name or (fn_now.name if fn_now else ""))
            except Exception as e:
                print(f"[signer] recoveries.update failed @ "
                      f"{apply_ctx.fn_addr:#x}: {type(e).__name__}: {e}",
                      flush=True)
        try:
            import nacre
        except Exception as e:
            return f"skip: nacre import: {type(e).__name__}: {e}"
        try:
            res = nacre.c_signature(sig, prelude=(types or None))
        except Exception as e:
            return f"skip: nacre.c_signature: {type(e).__name__}: {e}"
        c_decl = (res.get("decl") or "").strip()
        structs = (res.get("structs") or "").strip()
        if not c_decl:
            return "skip: empty c_decl"
        bv = apply_ctx.bv
        fn = apply_ctx.target_func()
        if fn is None:
            return f"skip: no fn at {apply_ctx.fn_addr:#x}"
        renamed = (f" (renamed: {fn.name!r} -> {agent_name!r})"
                   if agent_name and agent_name != fn.name else "")
        # nacre emits `... f(...)`; patch in the real symbol so binja's
        # type-parser binds to this fn.
        named_decl = c_decl.replace(" f(", f" {agent_name or fn.name}(", 1)
        try:
            import binaryninja as bn
        except Exception as e:
            return f"skip: binja import: {e}"
        defined: list[str] = []
        async with apply_ctx.write_lock:
            try:
                with bv.undoable_transaction():
                    if structs:
                        pr = bv.parse_types_from_string(structs)
                        for name, ty in (pr.types if pr else {}).items():
                            bv.define_user_type(name, ty)
                            defined.append(str(name))
                    if agent_name and agent_name != fn.name:
                        fn.name = agent_name
                    t, _ = bv.parse_type_string(named_decl)
                    fn.type = t
                    fn.reanalyze(bn.FunctionUpdateType.UserFunctionUpdate)
            except Exception as e:
                return f"err: apply: {type(e).__name__}: {e}"
        return f"ok: proto + {len(defined)} type(s){renamed}"

    def _post_first_blurb() -> str:
        """Forced context after attempt 1: full asm path + full HLIL
        whole HLIL inlined. Surfaces both forcefully so the agent can
        cross-reference its draft types against ground-truth callees +
        decompilation without spending tool calls."""
        parts: list[str] = []
        if asm_path:
            parts.append(
                f"\n\n=== full disassembly saved to `{asm_path}` ===\n"
                "Mine it with Bash/Grep/Read - token-cheap searches "
                "across the whole function:\n"
                f"  Bash: `grep -n drop_in_place {asm_path}`\n"
                f"  Bash: `grep -nB2 -A2 'alloc::alloc' {asm_path}`\n"
                f"  Bash: `grep -nE '\\[(rdi|rcx|rax)\\+0x[0-9a-f]+\\]' "
                f"{asm_path}`\n"
                "The asm is ground truth - if a callee name there "
                "contradicts your types, fix the types."
            )
        if hlil:
            parts.append(
                "\n\n=== full HLIL of this function ===\n"
                f"{hlil}\n=== end HLIL ===\n"
                "Read the HLIL: callee names, Rust panic strings, and "
                "field-access patterns are the strongest type signals."
            )
        return "".join(parts)

    async def post_submit_hook(input_data, tool_use_id, ctx):
        if input_data.get("tool_name") != qualified:
            return {}
        inp = input_data.get("tool_input") or {}
        types = (inp.get("types") or "").strip()
        sig = (inp.get("signature") or "").strip()
        agent_name = (inp.get("name") or "").strip()
        decl = f"{types}\n\n{sig}" if types else sig
        try:
            perfect, feedback, has_warnings, arity_only, score = validator(decl)
        except Exception as e:
            perfect, feedback, has_warnings, arity_only, score = (
                False,
                f"validator failed: {type(e).__name__}: {e}",
                False,
                False,
                0.0,
            )

        captured["attempts"] += 1
        attempt = captured["attempts"]
        captured["validations"].append((decl, perfect, feedback))

        async def _accept_or_reject() -> str:
            if score >= APPLY_SCORE_THRESHOLD:
                return await _apply_to_bv(types, sig, agent_name)
            return f"reject: score={score:.2f} < {APPLY_SCORE_THRESHOLD}"

        # Cheese warnings: bounce until max_rounds, then accept-or-reject.
        if has_warnings:
            if attempt >= max_rounds:
                captured["exhausted"] = True
                captured["applied"] = await _accept_or_reject()
                return {"hookSpecificOutput": {
                    "hookEventName": "PostToolUse",
                    "additionalContext": (
                        f"Quality warning persisted across {max_rounds} "
                        f"attempts; budget exhausted, accepted as final."
                    ),
                }}
            return {
                "decision": "block",
                "reason": (
                    f"Submission REJECTED (attempt {attempt}/{max_rounds}) "
                    "- quality warning. The layout may match byte-for-byte, "
                    "but the types you submitted paper over real fields "
                    "rather than recovering them.\n\n"
                    f"{feedback}\n\n"
                    "Refine: try idiomatic Rust types (Vec/HashMap/"
                    "String/Option/Result/&str/&[T]) where the offsets "
                    "fit. If a chunk of bytes is exactly 24B, try "
                    "Vec<u8> / String first; 48B -> HashMap<K,V>. Don't "
                    "submit `_a: [u8; 0xN]`, `p1/s1/p2/s2`, or `f48`-style "
                    "names - those are the antipattern. Use the targeted "
                    "tools (field_accesses, hlil_around) to figure out "
                    "what the struct actually represents, then submit "
                    "again."
                    f"{_post_first_blurb() if attempt == 1 else ''}"
                ),
            }

        # Bounce attempt-1 perfect submits when force_iterate_first
        # is on - first guesses are often unverified.
        if force_iterate_first and perfect and attempt == 1 and not arity_only:
            return {
                "decision": "block",
                "reason": (
                    "First submit accepted at the layout level - but the "
                    "first guess is NEVER the final guess. Use the wide "
                    "tools that just unlocked (`decompile`, `get_il`, "
                    "`disasm` with larger n) to verify your types are "
                    "semantically right (not just byte-aligned), then "
                    "re-submit. If after that pass nothing changes, "
                    "re-submit the same `(types, signature)` and it'll "
                    "be accepted on attempt 2."
                    f"{_post_first_blurb() if attempt == 1 else ''}"
                ),
            }

        if perfect:
            captured["applied"] = await _apply_to_bv(types, sig, agent_name)
            return {}
        if arity_only:
            # Missing-arg-reg arity (optimizer folded a bool/u8 use)
            # is acceptable; SYSTEM says keep the larger sig.
            captured["applied"] = await _accept_or_reject()
            return {"hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": (
                    "Accepted with arity-trap (a u8/bool arg reg isn't "
                    "observed in the binary, likely optimizer-folded). "
                    "final_perfect=False but recorded sig matches intent."
                ),
            }}
        if attempt >= max_rounds:
            captured["exhausted"] = True
            captured["applied"] = await _accept_or_reject()
            return {"hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": (
                    f"Validation failed on attempt {attempt}; "
                    f"max_rounds={max_rounds} exhausted, accepted as final."
                ),
            }}
        # Bounce + feed back validator output.
        return {
            "decision": "block",
            "reason": (
                f"Validation rejected your submission `{decl}` (attempt "
                f"{attempt}/{max_rounds}).\n\n{feedback}\n\n"
                "Refine the decl based on the issues above and call "
                "submit_signature again."
            ),
        }

    matcher = HookMatcher(matcher=qualified, hooks=[post_submit_hook])
    return [submit_signature], captured, matcher


NAMES = frozenset({"submit_signature"})
