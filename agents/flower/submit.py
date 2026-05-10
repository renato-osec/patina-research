# Submit tool + PostToolUse validator-bounce hook for the flower agent.
from __future__ import annotations

import os
import re
from typing import Any, Callable

from claude_agent_sdk import tool, HookMatcher


# (perfect, feedback, has_warnings, arity_only, score)
Validator = Callable[[str], tuple[bool, str, bool, bool, float]]
APPLY_SCORE_THRESHOLD = 0.5  # flower is the readability stage; lenient.

_FN_SIG_RE = re.compile(r"\bfn\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(([^)]*)\)", re.DOTALL)


def _signer_sig(recoveries, fn_addr: int) -> str | None:
    try:
        entry = recoveries.get(fn_addr) or {}
    except Exception:
        return None
    sig = (entry.get("signer") or {}).get("rust_signature")
    return sig.strip() if isinstance(sig, str) and sig.strip() else None


def _fn_sig_shape(source: str) -> tuple[str, int] | None:
    m = _FN_SIG_RE.search(source)
    if not m:
        return None
    raw = m.group(2).strip()
    arity = 0 if not raw else sum(1 for p in raw.split(",") if p.strip())
    return (m.group(1), arity)


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
    rust_fn_name: str = "",
    consistency_module: Any = None,
    bv: Any = None,
    prelude: str | None = None,
    fn_addr: int = 0,
):
    """Returns `(tools, captured, hook_matcher_or_None)`."""
    captured: dict = {
        "source": "", "name": "", "decl": "",
        "confidence": 0.0, "rationale": "",
        "attempts": 0, "validations": [],
        "exhausted": False, "scolded": False, "applied": "",
        "signer_bounced": False, "complexity_gated": False,
        "regions": [],
    }

    @tool("submit_region",
          "Persist an ACCEPTED region-level Rust translation. Call "
          "this ONLY after a `check_region` returned `perfect=True` "
          "(or with `binary_over` warnings only) - the harness re-"
          "runs `check_region` and rejects unverified submissions. "
          "`blocks` is a list of BB indices; the set may be non-"
          "contiguous (inlined fns / hot-cold splits). Legacy "
          "`block_start`/`block_end` for a contiguous range still "
          "works. Each accepted snippet is recorded under "
          "`flower.regions[<addr>] = [{blocks, source, score, "
          "note}, ...]` in the cross-stage sidecar for "
          "visualization later.",
          {"source": str, "blocks": list,
           "block_start": int, "block_end": int, "note": str})
    async def submit_region(args):
        src = (args.get("source") or "").strip()
        if not src:
            return _txt("error: empty source")
        note = (args.get("note") or "").strip()
        blocks = args.get("blocks")
        region: tuple[int, int] | list[int]
        if blocks:
            region = sorted({int(b) for b in blocks})
            if not region:
                return _txt("error: blocks list is empty")
            tag = f"blocks={region}"
        else:
            bs = int(args.get("block_start") or 0)
            be = int(args.get("block_end") or 0)
            if be <= bs:
                return _txt("error: pass `blocks=[...]` or "
                            "`block_start`/`block_end` with end > start")
            region = (bs, be)
            tag = f"[{bs},{be})"
        score = 1.0
        if consistency_module is not None and bv is not None:
            full = "\n".join(p for p in (prelude or "", src) if p).strip()
            signer_sig, signer_types = (None, None)
            if apply_ctx is not None and apply_ctx.recoveries is not None:
                signer_sig, signer_types = consistency_module.lookup_signer(
                    apply_ctx.recoveries, apply_ctx.fn_addr)
            try:
                r = consistency_module.check(
                    full, bv=bv, fn_addr=fn_addr,
                    rust_fn_name=rust_fn_name, region=region,
                    signer_sig=signer_sig, signer_types=signer_types)
            except Exception as e:
                return _txt(f"error: re-check raised {type(e).__name__}: {e}")
            if not r.perfect:
                return _txt(f"region {tag} NOT accepted: re-check failed.\n{r.feedback}")
            score = 1.0 if r.perfect else 0.0
        if isinstance(region, tuple):
            blocks_list = list(range(region[0], region[1]))
        else:
            blocks_list = list(region)
        snippet = {"blocks": blocks_list, "source": src,
                   "score": score, "note": note}
        captured["regions"].append(snippet)
        if apply_ctx is not None and apply_ctx.recoveries is not None:
            try:
                entry = apply_ctx.recoveries.get(apply_ctx.fn_addr, "flower") or {}
                regions = list(entry.get("regions") or []) + [snippet]
                apply_ctx.recoveries.update(
                    apply_ctx.fn_addr, "flower", regions=regions)
            except Exception as e:
                print(f"[flower] submit_region sidecar write failed: "
                      f"{type(e).__name__}: {e}", flush=True)
        return _txt(f"region {tag} accepted ({len(src)}B)")

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
        captured["source"] = src
        captured["decl"] = src
        captured["name"] = (args.get("name") or "").strip()
        captured["confidence"] = float(args.get("confidence", 0.0))
        captured["rationale"] = args.get("rationale", "")
        return _txt(f"submitted: {len(src)} bytes "
                    f"(confidence={captured['confidence']:.2f})")

    if validator is None:
        return [submit_region, submit_reconstruction], captured, None

    qualified = f"mcp__{server_name}__submit_reconstruction"

    async def _apply_to_bv(source: str, agent_name: str) -> str:
        # Persist source as fn.comment + optional rename. Sidecar
        # write happens FIRST so a bv transaction failure doesn't lose
        # the recovery.
        if apply_ctx is None or not source.strip():
            return "skip: no apply_ctx" if apply_ctx is None else "skip: empty source"
        bv = apply_ctx.bv
        fn = apply_ctx.target_func()
        if fn is None:
            return f"skip: no fn at {apply_ctx.fn_addr:#x}"
        cur_name = fn.name or ""
        is_library = (cur_name.startswith(("_Z", "j_")) or "::" in cur_name)
        do_rename = bool(agent_name) and agent_name != cur_name and not is_library
        renamed = (f" (renamed: {cur_name!r} -> {agent_name!r})" if do_rename
                   else (f" (rename refused: library symbol)" if agent_name and is_library
                         else ""))
        if apply_ctx.recoveries is not None:
            try:
                apply_ctx.recoveries.update(
                    apply_ctx.fn_addr, "flower",
                    source=source,
                    name=(agent_name if do_rename else cur_name))
            except Exception as e:
                print(f"[flower] recoveries.update failed @ "
                      f"{apply_ctx.fn_addr:#x}: {type(e).__name__}: {e}",
                      flush=True)
        async with apply_ctx.write_lock:
            try:
                with bv.undoable_transaction():
                    if do_rename:
                        fn.name = agent_name
                    fn.comment = source
            except Exception as e:
                return f"err: apply: {type(e).__name__}: {e}"
        return f"ok: comment ({len(source)}B){renamed}"

    def _post_first_blurb() -> str:
        # Forced context after attempt 1: full asm path + full HLIL
        # inlined, so the agent can grep/read instead of paging tools.
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
        rationale = (inp.get("rationale") or "").strip()
        decl = source   # validator gets full source verbatim
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

        # Complexity gate: if the binary fn has many basic blocks AND
        # the agent jumped straight to a whole-fn submission without
        # accepting any regions, bounce ONCE on attempt 1 to push the
        # agent toward `submit_region`. Override: rationale starting
        # with `whole-fn-override:` skips the gate.
        if (attempt == 1
                and not captured["complexity_gated"]
                and apply_ctx is not None
                and not rationale.lower().startswith("whole-fn-override:")
                and not captured.get("regions")):
            try:
                fn = apply_ctx.target_func()
                bb_count = len(list(fn.basic_blocks)) if fn else 0
            except Exception:
                bb_count = 0
            threshold = int(os.environ.get("FLOWER_REGION_GATE_BBS", "25"))
            if bb_count > threshold:
                captured["complexity_gated"] = True
                return {
                    "decision": "block",
                    "reason": (
                        f"Submission DEFERRED (attempt 1/{max_rounds}) "
                        f"- this fn has {bb_count} basic blocks (> {threshold}). "
                        f"Whole-fn reconstructions on big bodies tend to cheese "
                        f"or transport-crash; reconstruct piece-wise instead.\n\n"
                        f"  1. Call `region_blocks` to enumerate BBs.\n"
                        f"  2. Pick a contiguous range (3-8 BBs - a loop body, "
                        f"branch arm, init, etc).\n"
                        f"  3. `submit_region` for each piece (re-validates + "
                        f"persists to sidecar).\n"
                        f"  4. Re-submit the full body once at least one region "
                        f"is accepted (or override with rationale "
                        f"`whole-fn-override:<reason>`).\n\n"
                        f"Region snippets are compositional - paste them into "
                        f"the final body in topo order."
                    ),
                }

        # Signer-sig enforcement: on attempt 1, if signer recovered a
        # signature for this fn and the agent's submission diverges
        # (different fn name or arity), bounce ONCE with signer's exact
        # sig quoted. Override path: prefix `rationale` with
        # `signer-override:<reason>` to skip the check (used when signer
        # got the sig wrong and the agent has reason to correct).
        if (attempt == 1
                and not captured["signer_bounced"]
                and apply_ctx is not None
                and apply_ctx.recoveries is not None
                and not rationale.lower().startswith("signer-override:")):
            signer_sig = _signer_sig(apply_ctx.recoveries, apply_ctx.fn_addr)
            if signer_sig:
                submitted = _fn_sig_shape(source)
                approved = _fn_sig_shape(signer_sig)
                if submitted and approved and submitted != approved:
                    captured["signer_bounced"] = True
                    return {
                        "decision": "block",
                        "reason": (
                            f"Submission REJECTED (attempt 1/{max_rounds}) "
                            f"- your fn signature diverges from the "
                            f"approved signer-stage signature.\n\n"
                            f"  Yours:  fn {submitted[0]}({submitted[1]} args)\n"
                            f"  Signer: fn {approved[0]}({approved[1]} args)\n\n"
                            f"Signer's approved signature (use verbatim):\n"
                            f"```rust\n{signer_sig.strip()}\n```\n\n"
                            f"Re-submit with signer's signature unless you "
                            f"have evidence it's wrong (e.g. rustc rejected "
                            f"it, or HLIL contradicts the param types). To "
                            f"override, prefix `rationale` with "
                            f"`signer-override:<one-line reason>`."
                        ),
                    }

        # Flower is the readability-focused stage; warnings bounce
        # ONCE to give the agent a chance to fix obvious antipatterns,
        # then accept. Don't burn the full max_rounds budget on style.
        if has_warnings and attempt == 1:
            return {
                "decision": "block",
                "reason": (
                    "Submission flagged (attempt 1/2 for warnings) - "
                    "minor antipatterns. Body is otherwise OK. Quick "
                    "pass: replace any `_a: [u8;N]`, `p1/s1/p2/s2`, "
                    "`f48`-style fields with idiomatic Rust types "
                    "(Vec/HashMap/String/Option/Result/&str/&[T]) "
                    "where they fit. If you can't see what to change, "
                    "re-submit the SAME source and the harness will "
                    "accept it.\n\n"
                    f"{feedback}"
                    f"{_post_first_blurb() if attempt == 1 else ''}"
                ),
            }
        if has_warnings:  # attempt >= 2: accept with warnings recorded
            captured["exhausted"] = True
            if score >= APPLY_SCORE_THRESHOLD:
                captured["applied"] = await _apply_to_bv(source, agent_name)
            else:
                captured["applied"] = (
                    f"reject: score={score:.2f} < {APPLY_SCORE_THRESHOLD}"
                )
            return {"hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": (
                    "Warning(s) persisted on attempt 2; accepted as "
                    "final - flower prioritizes readability over "
                    "perfect layout match."
                ),
            }}
        if False:  # placeholder for stale-block typing  # noqa
            return {
                "decision": "block",
                "reason": "",
            }

        # Force-iterate: a non-warning, non-arity, perfect first submit
        # is suspicious - the agent often submits a low-confidence guess
        # just to unlock the wide-tool gate. Bounce the FIRST submit even
        # when it validates clean, with a "now refine if you can" prompt;
        # the second submit either confirms or improves it.
        # Skip when we already burned attempt 1 on a signer-sig bounce.
        if (force_iterate_first and perfect and attempt == 1
                and not arity_only
                and not captured["signer_bounced"]):
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
            # Out of rounds: only commit to the bv + sidecar if score
            # cleared the threshold. Below it we keep the bv clean and
            # mark exhausted so the run shows up as failed (better than
            # leaving a wrong reconstruction downstream stages will
            # treat as authoritative).
            captured["exhausted"] = True
            if score >= APPLY_SCORE_THRESHOLD:
                captured["applied"] = await _apply_to_bv(source, agent_name)
            else:
                captured["applied"] = (
                    f"reject: score={score:.2f} < {APPLY_SCORE_THRESHOLD}"
                )
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
    return [submit_region, submit_reconstruction], captured, matcher


NAMES = frozenset({"submit_reconstruction"})
