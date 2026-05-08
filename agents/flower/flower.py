# Per-function Rust-source reconstruction harness (flower agent).
#
# Reads a binja BinaryView, picks one function, asks an LLM agent to
# emit Rust source for it. The harness validates each submit via
# consistency.check (lymph compile + anemone dataflow + var-name
# binding) and bounces non-perfect submissions back for refinement.
# The agent's iterative tools (`il_vars`, `signer_types`, `bin_depends`,
# `bin_neighbors`, `check_reconstruction`) all share the same
# validation surface as the PostToolUse hook on `submit_reconstruction`.
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parent),
                str(Path(__file__).resolve().parent.parent / "common")]
os.environ.setdefault("BN_DISABLE_USER_PLUGINS", "1")

import asyncio
from dataclasses import asdict, dataclass

import binaryninja as bn
from claude_agent_sdk import query

from agent import Agent
from tools.ctx import TargetCtx
from tools import binja as t_binja
import submit as t_submit             # local
import check_tool as t_check          # local
import exo_tool as t_exo              # local
import decomp_gate as t_gate          # local
import asm_dump                       # local

from claude_agent_sdk import AgentDefinition


SYSTEM_TEMPLATE = """Reconstruct the Rust *body* of one function in a binja database.
The signer stage already gave you the prototype + types; your job is the
implementation. Two hard rules — both checked by the harness:

  1. The Rust source must COMPILE. rustc errors come back unchanged.
  2. Every named local in your fn must match an HLIL variable from
     `il_vars`. Submissions with unbound locals are rejected with the
     list of offending names so you can rename and retry.

After both pass, the harness runs a dataflow check: it lowers your Rust
to MIR via lymph, lowers the binary to MLIL-SSA via anemone, and
compares reachability between every (var_i, var_j) pair using the
1:1 name binding. Diffs are ordered: return + arg boundary first
(usually obvious), then intermediates from each arg outward.

{tools}

`source`: ONE Rust source string with all needed type defs + a `fn`
named exactly `{rust_fn_name}` (matches the binja symbol).

WIN CONDITIONS — what makes a perfect reconstruction:
  - Every Rust local named after a real HLIL var (call `il_vars` first;
    use those names verbatim).
  - **Brevity is scored**: `compression_ratio = rust_loc / hlil_loc` is
    recorded for every submission. Aim for ~0.3-0.5 - inline functions
    RECOVERED, not transcribed. If your `source` ends up the same length
    as `decompile` you've under-reconstructed; the dataflow check passes
    just as well on the high-level form.
  - **The prior signer types + prototype are already inlined at the top
    of THIS prompt** under a `PRIOR STAGE — SIGNER` block. Treat them as
    canonical; copy them verbatim into the prelude of your `source`. Do
    NOT re-derive struct shapes, do NOT call `signer_types` to confirm
    them, do NOT call subagents to "double-check the types" - signer
    already validated layout against the binary. Your only job is the
    fn body.
  - If a `PRIOR STAGE — FLOWER` block is present, a sibling worker
    already recovered this fn; treat it as a baseline to refine
    (or accept unchanged if already perfect). Don't redo work.

ANTIPATTERNS:
  - One-to-one transcription of HLIL into Rust. If your output is the
    same length as `decompile`, you've under-reconstructed.
  - Spelling out stdlib internals (RawTableInner / RawVec / Unique) when
    the matching Vec/HashMap call exists. The dataflow check passes for
    the high-level form; the verbose form usually fails AND eats tokens.
  - Renaming locals away from HLIL names to dodge the binding check.
    Use the `_` prefix for genuinely unused vars; renaming-to-bypass
    leaves the validator bouncing forever.

WORKFLOW:
  0. The PRIOR STAGE block at the top is your starting point - copy
     signer's types/prototype into the prelude as-is. Then `il_vars`
     once to record the binding vocabulary. For OTHER callees the
     binary inlines, `prior_metadata {target}` (or
     `prior_reconstruction {target}`) shows what sibling workers
     already recovered - transcribe their call instead of re-inlining.
  1. `register_trace` + `field_accesses` like signer; types are already
     applied so HLIL is closer to Rust source.
  2. **Probe binary dataflow before drafting**: `bin_depends {of, on}`
     answers "does var X depend on var Y in the binary?" and shows
     the path when yes. `bin_neighbors {var}` lists predecessors +
     successors at one hop. Use these to build a mental model of
     which Rust statements MUST exist (and which can be safely
     collapsed into idiomatic stdlib calls).
  3. `check_reconstruction {source}` - read the unbound list and the
     ordered diffs. Boundary diffs first (return / args); fix those
     before chasing intermediates. Diffs are tagged:
       `binary_over`         - anemone's worst-case at opaque calls;
                                surfaced as WARNING only.
       `missing_return_flow` - binary connects an arg to `<return>`
                                and your Rust does not. This IS a
                                failure: anemone is precise about the
                                rax-bound return path, so a missing
                                arg→return flow means you under-modeled
                                the body (e.g. returning a constant
                                where the binary returns the arg).
       `rust_over`           - your Rust adds flow not in the binary;
                                this IS a failure - remove the edge.
     `_x` is exempt from binding ONLY when `x` is not an HLIL var.
     Renaming an HLIL-bound local to `_x` is detected as a dodge -
     the validator binds it as `x` anyway and warns.
  4. `submit_reconstruction` early. First submit is never final; each
     bounce hands you full asm + HLIL to refine with.

`check_reconstruction` and `submit_reconstruction` validate identically.
You have 16 turns.

RUSTC ERRORS ON SUBMIT ARE TRANSIENT. Concurrent workers stomp a shared
tmp file; if the SAME `source` JUST PASSED `check_reconstruction` and
the submit bounces with "rustc driver aborted", re-submit it unchanged.
"""


# Shared CLI primitives - see agents/common/cli.py for details.
from cli import (
    AgentResult, CLI_ENV_SCRUB, LONG_OUTPUT_NOTE, READONLY_BUILTINS,
    drive_stream, per_fn_timeout, run_with_timeout, tools_block,
    transcript_path as _transcript_path_for_id,
)


def _format_prior(recoveries, fn_addr: int) -> str:
    """Render signer/flower findings stored in the cross-stage sidecar
    as a copy-paste-ready block for the agent's first turn."""
    try:
        entry = recoveries.get(fn_addr) or {}
    except Exception:
        return ""
    parts: list[str] = []
    sg = entry.get("signer") or {}
    if sg.get("rust_signature") or sg.get("rust_types"):
        body = ""
        if sg.get("rust_types"):
            body += sg["rust_types"].rstrip() + "\n\n"
        if sg.get("rust_signature"):
            body += sg["rust_signature"].rstrip() + "\n"
        parts.append(
            "PRIOR STAGE — SIGNER (already applied to the bndb; reuse "
            "verbatim, do NOT re-derive types):\n"
            "```rust\n" + body.rstrip() + "\n```"
        )
    fl = entry.get("flower") or {}
    if fl.get("source"):
        parts.append(
            "PRIOR STAGE — FLOWER (sibling worker's earlier reconstruction "
            "of the same fn; treat as the baseline to refine, not redo):\n"
            "```rust\n" + fl["source"].rstrip() + "\n```"
        )
    return "\n\n".join(parts)


@dataclass
class FlowerResult(AgentResult):
    submitted_source: str = ""    # full Rust source the agent's last submit
    submitted_name: str = ""      # agent-chosen Rust symbol (renames the fn)
    confidence: float = 0.0       # agent's self-reported 0..1
    rationale: str = ""
    final_score: float = 0.0      # 0..1 score on the last validation
    final_perfect: bool = False
    submit_attempts: int = 0
    # True iff `submit_rounds` exhausted without a perfect submit;
    # `final_perfect == False and budget_exhausted == True` ⇒ failed run.
    budget_exhausted: bool = False
    # Per-target dataflow stats from consistency.check on the final submit.
    rust_var_count: int = 0
    binary_var_count: int = 0
    bound_count: int = 0          # rust vars matched to an HLIL var
    diff_count: int = 0
    # Brevity: did the agent ACTUALLY recover inline fns or just transcribe?
    # < 1.0 means submitted Rust source has fewer non-blank lines than the
    # original HLIL listing - the win condition. ratio == 0.0 means
    # uncomputed (e.g. submitted source is empty).
    rust_loc: int = 0
    hlil_loc: int = 0
    compression_ratio: float = 0.0
    # Wide-tool gate accounting (PreToolUse hook).
    wide_blocks: int = 0
    wide_unlocked: bool = False


def transcript_path(rec: "FlowerResult", cwd: str | None = None) -> Path | None:
    """Thin wrapper over `cli.transcript_path` accepting a FlowerResult."""
    return _transcript_path_for_id(rec.session_id, cwd)


PER_FN_TIMEOUT_S = per_fn_timeout(default_s=300)


def _loc(text: str) -> int:
    """Non-blank, non-comment line count. Used for the brevity ratio."""
    n = 0
    for raw in (text or "").splitlines():
        s = raw.strip()
        if not s or s.startswith(("//", "#")):
            continue
        n += 1
    return n


async def sign_function(
    bv: bn.BinaryView,
    fn_addr: int,
    *,
    prelude: str | None = None,
    model: str = "opus",
    max_turns: int = 16,
    submit_rounds: int = 3,
    timeout_s: int | None = None,
    shared_ctx: TargetCtx | None = None,
    trace: bool = False,
) -> FlowerResult:
    """Run the signer agent on one function. `prelude` is an optional Rust
    snippet (struct/use defs) appended to every check_signature call so
    user types can be looked up by name. `submit_rounds` caps how many
    times the harness's PostToolUse validator will bounce a non-perfect
    submission back at the agent for refinement (1 = accept first try)."""
    # Per-worker fork: writing `shared_ctx.fn_addr = fn_addr` raced
    # under asyncio - the submit hook would later read whichever addr
    # the next-scheduled worker stamped, so most recoveries landed in
    # the wrong sidecar slot (or none at all). `fork` shares bv +
    # locks + recoveries but gives each call its own fn_addr.
    if shared_ctx is None:
        ctx = TargetCtx(bv=bv, fn_addr=fn_addr)
        owns_ctx = True
    else:
        ctx = shared_ctx.fork(fn_addr)
        owns_ctx = False

    f = ctx.target_func()
    name = f.name if f else f"sub_{fn_addr:x}"
    rec = FlowerResult(name=name, address=f"{fn_addr:#x}")

    # Validator the PostToolUse hook calls on every submit_signature
    # firing. Returns (perfect, feedback, has_warnings, arity_only).
    #   has_warnings: triggers a free first-round scold (placeholder
    #     structs / skip-arrays / offset-named fields).
    #   arity_only: not perfect, BUT the only mismatch is missing-reg
    #     arity (binary optimized away a small bool/u8 arg). Per the
    #     SYSTEM rule we accept the submission instead of bouncing,
    #     since asking the agent to "refine" would push them to drop
    #     the real arg.
    # The Rust fn name the agent must use in `source`. `f.name` is the
    # raw Rust ABI symbol (`_ZN6source5State4jump17h...E`); use binja's
    # demangled `symbol.short_name`, strip the `::h<16-hex>` ABI tag
    # and keep just the leaf so `fn jump(...)` is what the agent writes.
    import consistency
    short = (f.symbol.short_name if f and f.symbol else None) or name
    rust_fn_name = consistency.clean_fn_name(short)
    def validator(decl: str) -> tuple[bool, str, bool, bool, float]:
        # `decl` here IS the full Rust source the agent submitted.
        full = "\n".join(p for p in (prelude or "", decl) if p).strip()
        try:
            r = consistency.check(full, bv=bv, fn_addr=fn_addr,
                                  rust_fn_name=rust_fn_name)
        except Exception as e:
            return False, f"check_reconstruction raised {type(e).__name__}: {e}", False, False, 0.0
        # Flower has no arity-trap analogue; pass False unconditionally.
        # Score: 1.0 perfect, 0.0 otherwise (consistency.CheckResult
        # only exposes a binary perfect bit; partial-credit scoring
        # could be added later).
        return r.perfect, r.feedback, r.has_warnings, False, (1.0 if r.perfect else 0.0)

    # Pre-dump full asm + HLIL so the submit hook can hand them to the
    # agent post-first-submit (forced ground-truth context). Best-effort:
    # any failure falls back to the no-blurb path.
    try:
        asm_path = str(asm_dump.dump_function_asm(bv, fn_addr, name=name))
    except Exception:
        asm_path = None
    try:
        hlil_text = asm_dump.hlil_text(bv, fn_addr) or None
    except Exception:
        hlil_text = None

    # A/B knobs (env-driven so the bench harness can flip them without
    # code edits): set SIGNER_NO_FORCE_ITERATE=1 to disable the
    # force-iterate-first-submit bounce; SIGNER_NO_GATE=1 to skip the
    # PreToolUse wide-tool gate entirely.
    # Default OFF: force-iterate-first doubled cost on every successful
    # run (every [OK] paid for 2 submits). Set
    # `SIGNER_FORCE_ITERATE_FIRST=1` to opt back in.
    no_force_iter = (os.environ.get("SIGNER_NO_FORCE_ITERATE") == "1"
                     or os.environ.get("SIGNER_FORCE_ITERATE_FIRST") != "1")
    no_gate = os.environ.get("SIGNER_NO_GATE") == "1"

    submit_tools, captured, submit_hook = t_submit.make(
        validator=validator, max_rounds=submit_rounds, server_name="flower",
        asm_path=asm_path, hlil=hlil_text,
        force_iterate_first=not no_force_iter,
        apply_ctx=ctx,
    )
    check_tools = t_check.make(bv, fn_addr, prelude=prelude,
                               rust_fn_name=rust_fn_name,
                               recoveries=ctx.recoveries)
    exo_tools   = t_exo.make(bv, fn_addr)
    # Order matters: register_trace appears first so the agent's prompt
    # tools_block lists it at the top - it's the recommended first call.
    tools = exo_tools + t_binja.make(ctx) + check_tools + submit_tools

    user_prompt = (
        f"Reconstruct the Rust body of `{name}` at {fn_addr:#x}. "
        f"Call `il_vars` first for the binding vocabulary, then iterate "
        f"via `check_reconstruction`, then `submit_reconstruction`."
    )
    # Auto-inject what prior stages already recovered for this fn so
    # the agent doesn't burn turns rederiving the prototype + types
    # signer just produced. The same content is also queryable via
    # `prior_metadata` / `prior_reconstruction`, but inlining it here
    # makes "use what signer gave you" the default and skips the
    # 1-2 tool roundtrips it costs to ask.
    prior_block = _format_prior(ctx.recoveries, fn_addr) if ctx.recoveries else ""
    if prior_block:
        user_prompt += "\n\n" + prior_block
    if no_gate:
        gate_state = {"unlocked": True, "blocks": 0}
        hooks: dict = {}
    else:
        gate_matcher, gate_state = t_gate.make(server_name="flower")
        hooks = {"PreToolUse": [gate_matcher]}
    if submit_hook is not None:
        hooks["PostToolUse"] = [submit_hook]

    # Quick read-only subagent for cross-function context. The parent
    # spawns it via the `Task` builtin to ask "what does function X do?"
    # without paying its own context tokens to read whole HLIL. Haiku
    # model + tight max_turns keep it fast and cheap. The subagent
    # inherits the same MCP tools (so it can register_trace / decompile
    # / xrefs across the binary) but doesn't see signer's submit/check
    # - its only job is to summarize.
    inspect_tool_names = sorted(
        {f"mcp__flower__{t.name}" for t in (exo_tools + t_binja.make(ctx))}
    )
    context_subagent = AgentDefinition(
        description=(
            "Fast cross-function context gatherer. Use to ask 'what "
            "does function X do?' / 'what's the type at offset Y in "
            "callsite Z?' Returns a 2-3 sentence summary."
        ),
        prompt=(
            "You are a fast read-only inspector. The parent is "
            "recovering a Rust signature and needs cross-fn context. "
            "Highest-value signals (look for these first):\n"
            "  - The single caller's HLIL (via `xrefs` then "
            "`hlil_around`) - shows how args are formed (literals, "
            "string lookups, prior calls).\n"
            "  - Concrete callee names (alloc::alloc, hashbrown::*, "
            "Vec::push, RandomState::hash_one) - they name the types.\n"
            "  - For receiver-type questions, route to the `destructor` "
            "subagent instead - it's specialized for that.\n"
            "Return AT MOST 4 short sentences with concrete findings. "
            "Don't submit, don't loop, don't speculate beyond what "
            "callees prove."
        ),
        tools=inspect_tool_names + ["Read", "Grep", "Glob"],
        model="haiku",
        maxTurns=6,
    )

    # Specialist subagent for receiver-type field discovery via the
    # destructor `core::ptr::drop_in_place::<T>`. The destructor is
    # the highest-signal pivot for struct recovery - rustc emits one
    # per type that owns any droppable resource, and its body
    # dispatches to typed drop_in_place calls for each field. Reading
    # those gives exact field types (Vec<u8> not "u8 ptr + 2 usize",
    # HashMap<K,V> not "ctrl/mask/items"). Use this subagent any time
    # the receiver is a non-trivial struct (anything that has fields
    # observed past offset 0 with mixed flavors).
    destructor_subagent = AgentDefinition(
        description=(
            "Walks the destructor of the receiver type T to extract "
            "exact field types. Use for any non-trivial receiver "
            "struct - anything more than a primitive or a bare ptr. "
            "Returns one line per field with offset and exact type. "
            "Works on stripped binaries - uses structural recognition, "
            "not just symbol names."
        ),
        prompt=(
            "You walk Rust destructors to extract exact field types "
            "of a struct T. Names can be stripped - DON'T rely on "
            "`drop_in_place` symbol matching alone.\n\n"
            "How to find T's destructor:\n"
            "  A) NAMED binary: `functions_at \"drop_in_place\"` lists "
            "the dispatcher; match `<T>` to the parent's hint. Quick "
            "win when symbols survived.\n"
            "  B) STRIPPED / unsure: use XREFS. The parent gives you "
            "the receiver's address (or a function that takes T by "
            "ptr). `xrefs {addr}` on T-handling functions surfaces "
            "callers; the destructor is the one whose body matches "
            "the destructor SHAPE:\n"
            "       - takes a single ptr arg (rdi)\n"
            "       - early `if (ptr != 0)` guard\n"
            "       - sequence of `[ptr + 0xN]` loads, each fed to a "
            "sub-callee that itself looks like a destructor (fans "
            "down) OR to `__rust_dealloc` directly\n"
            "       - returns void\n"
            "       - called from many sites where a value of T goes "
            "out of scope\n"
            "     `decompile` (now unlocked for you) is fine for "
            "candidate triage.\n\n"
            "Reading the destructor body:\n"
            "  - For each `[rdi+0x{N}]` -> sub-callee call: the "
            "callee's HLIL/asm tells you the FIELD TYPE.\n"
            "      * Callee is itself a `drop_in_place::<T>` (named): "
            "the `<T>` is the field type verbatim.\n"
            "      * Callee is stripped: identify by structure -\n"
            "          + 3 loads then `__rust_dealloc(ptr, size*cap, "
            "align)` ⇒ `Vec<T>` where T's size = the element size in "
            "the dealloc.\n"
            "          + ctrl/mask loads + RawTable-shaped iteration "
            "+ deallocs ⇒ `HashMap<K, V>` (size of (K,V) = bucket "
            "size in the iterator).\n"
            "          + ptr + len + cap (24B) with byte dealloc ⇒ "
            "`String` if the data is u8 and there's a UTF8 check, "
            "else `Vec<u8>`.\n"
            "          + single ptr passed to dealloc ⇒ `Box<T>`; T "
            "by alignment + dealloc size.\n"
            "  - Fields with NO drop dispatch are Copy/trivial "
            "(scalars, bool, NonZero*, primitive arrays).\n\n"
            "Return ONE LINE PER FIELD:\n"
            "  +0x00: Vec<u8>\n"
            "  +0x18: Vec<u8>\n"
            "  +0x30: HashMap<usize, usize>\n"
            "  +0x60: bool          (no drop = trivially-droppable)\n"
            "Plus: `destructor: <addr>` (where you found it) and "
            "`total drops: N`.\n"
            "If you can't find any destructor candidate after 2-3 "
            "xref/structural attempts, say `T appears Copy/trivial - "
            "no destructor found` and list whatever offsets the "
            "parent already knows. Don't speculate. Don't submit."
        ),
        tools=inspect_tool_names + ["Read", "Grep", "Glob", "Bash"],
        model="haiku",
        maxTurns=10,
    )

    # Inject auto-generated tool schemas + long-output recovery note.
    # str.replace avoids `.format()` choking on the literal `{...}` braces
    # inside the rendered tool schemas.
    block = tools_block(tools, extra_lines=["", LONG_OUTPUT_NOTE])
    rendered_prompt = (SYSTEM_TEMPLATE
                       .replace("{tools}", block)
                       .replace("{rust_fn_name}", rust_fn_name))
    opts = Agent(
        name="flower",
        system_prompt=rendered_prompt,
        tools=tools,
        allowed_builtins=READONLY_BUILTINS + ["Task"],
        hooks=hooks,
        model=model,
        max_turns=max_turns,
        # Per-fn token budget. OFF by default - see signer.py.
        task_budget_tokens=int(os.environ.get("PATINA_TASK_BUDGET", "0")),
        agents={"context": context_subagent, "destructor": destructor_subagent},
    )._build_options(env=CLI_ENV_SCRUB)

    stream = query(prompt=user_prompt, options=opts)
    prefix = f"[{name}] " if trace else None
    try:
        budget = int(timeout_s) if timeout_s is not None else PER_FN_TIMEOUT_S
        await run_with_timeout(_drive(stream, rec, prefix=prefix), rec, budget)
    finally:
        if owns_ctx:
            ctx.close()

    rec.submitted_source = captured.get("source", "")
    rec.submitted_name = captured.get("name", "")
    rec.confidence = captured["confidence"]
    rec.rationale = captured["rationale"]
    rec.submit_attempts = captured["attempts"]
    rec.budget_exhausted = bool(captured.get("exhausted"))
    rec.wide_blocks = int(gate_state.get("blocks", 0))
    rec.wide_unlocked = bool(gate_state.get("unlocked", False))
    # Brevity: count non-blank/non-comment lines on each side.
    rec.rust_loc = _loc(rec.submitted_source)
    rec.hlil_loc = _loc(hlil_text or "")
    if rec.hlil_loc and rec.rust_loc:
        rec.compression_ratio = round(rec.rust_loc / rec.hlil_loc, 3)
    # The PostToolUse hook validated each submit; the last entry of
    # `validations` is authoritative for the final submission. Re-run
    # consistency.check to recover numeric stats - the captured tuple
    # only stores (perfect, feedback) booleans.
    if captured["validations"]:
        last_decl, last_perfect, _last_feedback = captured["validations"][-1]
        rec.final_perfect = last_perfect
        try:
            full = "\n".join(p for p in (prelude or "", last_decl) if p).strip()
            stats = consistency.check(full, bv=bv, fn_addr=fn_addr,
                                      rust_fn_name=rust_fn_name)
            rec.rust_var_count = stats.rust_var_count
            rec.binary_var_count = stats.binary_var_count
            rec.bound_count = stats.rust_var_count - len(stats.unbound)
            rec.diff_count = len(stats.diffs_ordered)
            # Score: 1.0 if perfect, else `bound_ratio * (1 - diff_ratio)`
            # with diff_ratio capped against the number of binding pairs.
            if stats.perfect:
                rec.final_score = 1.0
            elif stats.rust_var_count == 0:
                rec.final_score = 0.0
            else:
                bound_ratio = rec.bound_count / max(stats.rust_var_count, 1)
                pairs = max(rec.bound_count * (rec.bound_count - 1), 1)
                diff_ratio = min(rec.diff_count / pairs, 1.0)
                rec.final_score = round(bound_ratio * (1.0 - diff_ratio), 3)
        except Exception as e:
            rec.error = (rec.error or "") + f"\nfinal-check: {type(e).__name__}: {e}"
            rec.final_score = 1.0 if last_perfect else 0.0
    return rec


async def _drive(stream, rec: FlowerResult, *, prefix: str | None = None) -> None:
    """Signer needs no agent-specific message handling beyond the common
    bookkeeping in `drive_stream`; just consume the wrapped iterator."""
    async for _msg in drive_stream(stream, rec, prefix=prefix):
        pass


if __name__ == "__main__":
    import argparse
    import json

    ap = argparse.ArgumentParser()
    ap.add_argument("bndb")
    ap.add_argument("addr", help="0xADDR or function name")
    ap.add_argument("--model", default="opus")
    ap.add_argument("--max-turns", type=int, default=16)
    ap.add_argument("--submit-rounds", type=int, default=3,
                    help="max times the harness re-prompts the agent after "
                         "a non-perfect submit_signature (default 3)")
    ap.add_argument("--timeout", type=int, default=None,
                    help="per-fn wall-clock budget in seconds. Default: "
                         "PER_FN_TIMEOUT_S env var, or 300s.")
    ap.add_argument("--prelude-file",
                    help="path to a .rs file whose contents are passed as nacre prelude")
    ap.add_argument("--trace", action="store_true")
    args = ap.parse_args()

    bv = bn.load(args.bndb)
    if bv is None:
        sys.exit(f"bn.load failed: {args.bndb}")
    try:
        if args.addr.startswith("0x"):
            addr = int(args.addr, 16)
        else:
            f = next((f for f in bv.functions if f.name == args.addr), None)
            if f is None:
                sym = bv.get_symbols_by_name(args.addr)
                if not sym:
                    sys.exit(f"no function: {args.addr}")
                addr = sym[0].address
            else:
                addr = f.start
        prelude = Path(args.prelude_file).read_text() if args.prelude_file else None
        r = asyncio.run(sign_function(
            bv, addr,
            prelude=prelude,
            model=args.model,
            max_turns=args.max_turns,
            submit_rounds=args.submit_rounds,
            timeout_s=args.timeout,
            trace=args.trace,
        ))
        print(json.dumps(asdict(r), indent=2))
    finally:
        bv.file.close()
