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
        "source": "",        # full Rust source (types + fn body in one string)
        "name": "",          # agent-chosen Rust symbol; empty = keep existing
        "decl": "",          # alias of source for legacy downstream consumers
        "confidence": 0.0,
        "rationale": "",
        "attempts": 0,
        "validations": [],   # list of (source, perfect, feedback) per round
        "exhausted": False,  # set True if max_rounds was hit without a perfect submit
        "scolded": False,    # True once we've sent the free antipattern warning
        "applied": "",       # status of the post-accept bndb write-back (apply_ctx)
    }

    @tool("submit_reconstruction",
          "Submit your final Rust reconstruction of the target function. "
          "`source` is one Rust source string containing every needed "
          "type def AND a `fn` with the same name as the target. Every "
          "named local in your fn must match an HLIL variable from "
          "`il_vars`. Optional `name` renames the function in the bndb. "
          "`confidence` is 0..1. Optional `rationale` is one sentence. "
          "The harness re-runs the same compile + dataflow check as "
          "`check_reconstruction`; rejected submissions bounce so you "
          "can refine.",
          {"source": str, "name": str,
           "confidence": float, "rationale": str})
    async def submit_reconstruction(args):
        src = (args.get("source") or "").strip()
        name = (args.get("name") or "").strip()
        captured["source"] = src
        captured["name"] = name
        captured["decl"] = src   # alias for legacy harness fields
        captured["confidence"] = float(args.get("confidence", 0.0))
        captured["rationale"] = args.get("rationale", "")
        return {"content": [{"type": "text",
                             "text": f"submitted: {len(src)} bytes "
                                     f"(confidence={captured['confidence']:.2f})"}]}

    if validator is None:
        return [submit_reconstruction], captured, None

    qualified = f"mcp__{server_name}__submit_reconstruction"

    async def _apply_to_bv(source: str, agent_name: str) -> str:
        """Persist the accepted Rust reconstruction: store `source` as
        the function-level comment + optionally rename. Types/prototype
        were already applied by the signer stage upstream - flower only
        adds the body-level recovery on top.
        """
        if apply_ctx is None or not source.strip():
            return "skip: no apply_ctx" if apply_ctx is None else "skip: empty source"
        bv = apply_ctx.bv
        fn = apply_ctx.target_func()
        if fn is None:
            return f"skip: no fn at {apply_ctx.fn_addr:#x}"
        renamed = ""
        if agent_name and agent_name != fn.name:
            renamed = f" (renamed: {fn.name!r} -> {agent_name!r})"
        async with apply_ctx.write_lock:
            try:
                with bv.undoable_transaction():
                    if agent_name and agent_name != fn.name:
                        fn.name = agent_name
                    fn.comment = source
            except Exception as e:
                return f"err: apply: {type(e).__name__}: {e}"
        # Mirror into the cross-stage sidecar (best-effort - flower's
        # canonical store is fn.comment, the sidecar is a queryable
        # secondary view). Future stages read this without needing
        # binja loaded.
        if apply_ctx.recoveries is not None:
            apply_ctx.recoveries.update(
                apply_ctx.fn_addr, "flower",
                source=source,
                name=agent_name or fn.name,
            )
        return f"ok: comment ({len(source)}B){renamed}"

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
        source = (inp.get("source") or "").strip()
        agent_name = (inp.get("name") or "").strip()
        decl = source   # validator gets full source verbatim
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
                captured["applied"] = await _apply_to_bv(source, agent_name)
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
            captured["applied"] = await _apply_to_bv(source, agent_name)
            return {}
        if arity_only:
            # Arity-only mismatch: binary doesn't observe one of the
            # arg regs (typical for a `bool`/`u8` whose use the optimizer
            # folded into one instruction). The SYSTEM tells the agent to
            # keep the larger sig; bouncing here would push them to drop a
            # real arg. Accept as final and let final_perfect=False
            # surface the discrepancy honestly in the JSON.
            captured["applied"] = await _apply_to_bv(source, agent_name)
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
            captured["applied"] = await _apply_to_bv(source, agent_name)
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
                "submit_reconstruction again."
            ),
        }

    matcher = HookMatcher(matcher=qualified, hooks=[post_submit_hook])
    return [submit_reconstruction], captured, matcher


NAMES = frozenset({"submit_reconstruction"})
