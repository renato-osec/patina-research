# Terminal submit tool for the signer agent + a PostToolUse hook that
# validates each submission via `validator(decl)` and bounces it back
# to the agent (with the validation feedback) for up to `max_rounds`
# iterations. After max_rounds the latest submission stands as final
# even if it didn't validate.
from __future__ import annotations

from typing import Any, Awaitable, Callable

from claude_agent_sdk import tool, HookMatcher


# A validator returns (perfect, feedback, has_warnings, arity_only).
#   perfect:      True iff every slot + sret + return + arity all agree.
#   feedback:     verbatim rejection reason if not perfect.
#   has_warnings: soft cheese signals (offset-named, skip-arrays, etc.)
#                 - triggers the free first scold even on perfect=True.
#   arity_only:   not perfect but the only mismatch is missing-reg arity
#                 (binary optimized away a small bool/u8 arg). Treat
#                 like perfect for hook purposes - DON'T bounce the
#                 agent, since the SYSTEM tells them to keep the larger
#                 sig and asking to "refine" would push them to drop
#                 the real arg.
Validator = Callable[[str], tuple[bool, str, bool, bool]]


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
    """Build the submit tool + matching PostToolUse hook.

    Returns `(tools, captured, hook_matcher)`.
      tools         - list of @tool callables to register on the agent
      captured      - dict the harness reads after the loop ends
                      (decl, confidence, rationale, attempts)
      hook_matcher  - None if `validator is None`, else a HookMatcher
                      bound to the submit tool that auto-replies with
                      validator feedback for up to `max_rounds` retries.

    The submit tool always succeeds on its own - the hook is what
    decides whether the agent gets to stop or has to refine.
    """
    captured = {
        "types": "",
        "signature": "",
        "name": "",          # agent-chosen Rust symbol; empty = keep existing
        "decl": "",          # joined types + signature, for downstream consumers
        "confidence": 0.0,
        "rationale": "",
        "attempts": 0,
        "validations": [],   # list of (decl, perfect, feedback) per round
        "exhausted": False,  # set True if max_rounds was hit without a perfect submit
        "scolded": False,    # True once we've sent the free antipattern warning
        "applied": "",       # status of the post-accept bndb write-back (apply_ctx)
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
        name = (args.get("name") or "").strip()
        decl = f"{types}\n\n{sig}" if types else sig
        captured["types"] = types
        captured["signature"] = sig
        captured["name"] = name
        captured["decl"] = decl   # joined, for back-compat with downstream code
        captured["confidence"] = float(args.get("confidence", 0.0))
        captured["rationale"] = args.get("rationale", "")
        return {"content": [{"type": "text",
                             "text": f"submitted: signature={sig!r} "
                                     f"(confidence={captured['confidence']:.2f})"}]}

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
        try:
            import nacre  # heavy first-import (rustc driver); cached after
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
        # Honor the agent's optional `name` arg before applying the
        # prototype, so the C decl binds to the chosen symbol. Empty
        # string keeps the existing function name.
        renamed = ""
        if agent_name and agent_name != fn.name:
            renamed = f" (renamed: {fn.name!r} -> {agent_name!r})"
        # nacre emits the prototype as `... f(...)` - patch in the real
        # function symbol so binja's parser binds the type to this fn.
        fn_name = agent_name or fn.name
        named_decl = c_decl.replace(" f(", f" {fn_name}(", 1)
        # parse_*_string is sync (the binja API). Lock + transact around
        # the whole sequence so a partial apply rolls back cleanly.
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
        # Mirror to the cross-stage sidecar so flower (and any future
        # stage) can read this fn's recovered prototype + types
        # without re-parsing the bndb.
        if apply_ctx.recoveries is not None:
            apply_ctx.recoveries.update(
                apply_ctx.fn_addr, "signer",
                rust_types=types,
                rust_signature=sig,
                name=agent_name or fn.name,
            )
        return f"ok: proto + {len(defined)} type(s){renamed}"

    def _post_first_blurb() -> str:
        """Built-in context the agent gets after its first submission:
        the full asm dumped to a file (greppable via Bash), plus the
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
            perfect, feedback, has_warnings, arity_only = validator(decl)
        except Exception as e:
            perfect, feedback, has_warnings, arity_only = (
                False,
                f"validator failed: {type(e).__name__}: {e}",
                False,
                False,
            )

        captured["attempts"] += 1
        attempt = captured["attempts"]
        captured["validations"].append((decl, perfect, feedback))

        # Cheese / offset-named-fields warnings count as REJECTION now
        # (no free pass). Layout matched but the struct papers over real
        # fields - the agent must refine. Counts toward the submit budget;
        # the bounce loop iterates up to max_rounds.
        if has_warnings:
            if attempt >= max_rounds:
                captured["exhausted"] = True
                captured["applied"] = await _apply_to_bv(types, sig, agent_name)
                return {
                    "hookSpecificOutput": {
                        "hookEventName": "PostToolUse",
                        "additionalContext": (
                            f"Quality warning persisted across "
                            f"{max_rounds} attempts; budget exhausted, "
                            f"submission accepted as final."
                        ),
                    }
                }
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

        # Force-iterate: a non-warning, non-arity, perfect first submit
        # is suspicious - the agent often submits a low-confidence guess
        # just to unlock the wide-tool gate. Bounce the FIRST submit even
        # when it validates clean, with a "now refine if you can" prompt;
        # the second submit either confirms or improves it.
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
            # Arity-only mismatch: binary doesn't observe one of the
            # arg regs (typical for a `bool`/`u8` whose use the optimizer
            # folded into one instruction). The SYSTEM tells the agent to
            # keep the larger sig; bouncing here would push them to drop a
            # real arg. Accept as final and let final_perfect=False
            # surface the discrepancy honestly in the JSON.
            captured["applied"] = await _apply_to_bv(types, sig, agent_name)
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PostToolUse",
                    "additionalContext": (
                        "Submission accepted with arity-trap (one or more "
                        "expected arg regs aren't observed in the binary - "
                        "likely a bool/u8 the optimizer folded). "
                        "final_perfect will be False but the recorded sig "
                        "matches the source's intent."
                    ),
                }
            }
        if attempt >= max_rounds:
            # Out of rounds: accept the last submission as-is, but tag the
            # captured state so the harness can record `budget_exhausted`.
            captured["exhausted"] = True
            captured["applied"] = await _apply_to_bv(types, sig, agent_name)
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PostToolUse",
                    "additionalContext": (
                        f"Validation failed on attempt {attempt} but the "
                        f"max_rounds={max_rounds} budget is exhausted. "
                        f"Submission accepted as final."
                    ),
                }
            }
        # Bounce: reject the submit and feed the model back the validation
        # output as the rejection reason. The agent will get this as a
        # tool-failure surface and is expected to refine + submit again.
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
