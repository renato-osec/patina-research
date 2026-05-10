# Per-fn Rust-source reconstruction harness; lymph + anemone validator.
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parent),
                str(Path(__file__).resolve().parent.parent / "common")]
os.environ.setdefault("BN_DISABLE_USER_PLUGINS", "1")

import asyncio
from dataclasses import asdict, dataclass, field

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
implementation. Flower is the *readability* stage: aim for clean, idiomatic
Rust that a reader can understand at a glance. Two hard rules:

  1. The Rust source must COMPILE. rustc errors come back unchanged.
  2. Every named local in your fn must match an HLIL variable from
     `il_vars`. Submissions with unbound locals are rejected with the
     list of offending names so you can rename and retry.

That's it for hard rules. The harness ALSO runs a dataflow check (lymph
MIR vs anemone MLIL-SSA) and surfaces diffs in the feedback as guidance,
but a non-perfect dataflow match no longer rejects the submission. What
DOES reject:

  - Stub bodies. Empty fn body, or a body that's just `let _ = arg;
    let _ = arg2; Result { ...all zeros... }` for a fn the binary
    actually does work in. The validator computes a stub-trap based on
    bin_block_count vs body length: if the binary has 30+ basic blocks
    and your body has fewer than 5 statements with `let _ =` discards,
    you're cheesing - bounce.
  - Antipattern struct fields (`p1/s1/p2/s2`, `_a: [u8; 0xN]`,
    `f48`-style names). Treat signer's types as canonical and copy them
    verbatim instead of inventing your own shapes.

So: write a body that compiles, binds every local to an HLIL var, and
shows real flow even when imperfect. A 5-line body that reads two
fields and returns one of them is much better than a 50-line body
spelling out every micro-op or a 0-line body returning zeros.

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

SAFE-RUST CONTRACT (hard requirement):
  - The main fn MUST use signer's exact prototype, verbatim, with the
    exact param names + types and the exact return type signer gave.
    The validator parses signer's recovered signature and rejects any
    submission whose `fn {rust_fn_name}(...)` deviates. No `extern
    "C"`, no `unsafe fn`, no raw pointers (`*mut T` / `*const T`) in
    its arguments or return type. If signer typed it `&State`, you
    write `&State` - not `*const u8` or `&AssetState` or `&MyState`.
  - Every struct signer named in `signer_types` MUST appear in your
    prelude with the EXACT same field set, or be referenced without
    redefinition. Don't invent `pub struct Position { assets }` if
    signer recovered `pub struct OracleAsset { oracle_entries, ... }`
    - copy signer's struct verbatim. The validator rejects redefined
    structs with different fields.
  - The body itself is safe Rust: no `unsafe { ... }` blocks. If you
    feel the urge to write one, you're modeling at the wrong layer -
    use the high-level signer types instead of raw memory.
  - For opaque callees the binary calls (no signer recovery for them),
    declare a SAFE fn stub in the prelude with placeholder behaviour:
        fn opaque_callee(x: &Foo) -> Bar { unimplemented!() }
    The validator only looks at the call signature, not the body, so
    `unimplemented!()` is fine. NEVER pull them in via `extern "C"`.
  - For inlined fns (stdlib calls expanded into the body), write the
    high-level form: `vec.push(x)`, not the lowered RawVec dance.

WARP-RECOVERED CALLEES — STUB THEM, DON'T INLINE:
  Any callee whose binja name contains `::` or starts with `_Z` (Rust
  ABI mangled) is WARP-recovered: binja already knows it's a stdlib
  fn (HashMap insert, SipHash, RawVec, format::write, etc). DO NOT
  reconstruct its inlined body — declare a stub with the canonical
  stdlib signature and move on:

      // RandomState::hash_one specialised to usize
      fn hash_one_random_state_usize(this: &RandomState, x: u64) -> u64 {
          unimplemented!()
      }

  The agent's job is the USER fn body, not re-implementing stdlib.
  If you find yourself transcribing 20+ lines of XOR/rotate/add (that
  is *always* SipHash), `vec.push`/raw_vec alloc dance, or HashMap
  bucket-walk code, STOP. Identify which stdlib fn the inline came
  from, write its stub, and call the stub from your body.

  Common inlines and their stub names:
    - 30+ lines of `rol`/XOR/add bit math → `RandomState::hash_one*`
      or `BuildHasher::hash_one*`
    - `[ptr + 0]/[ptr + 8]/[ptr + 16] = ...; reserve_for_push` →
      `Vec::push` (or `with_capacity_in`)
    - control/mask byte walks + `tombstone` constants → `HashMap::insert`
      or `RawTable::find`
    - `__rust_alloc` / `alloc::alloc` shape → `Box::new` or `Vec::with_capacity`
    - `Formatter::write_str` / `Arguments::new_v1` → `format!` macro
      (treat the args as the high-level format arguments)

  When in doubt, look at the surrounding context: a hash_one in a
  HashMap path is a hasher call; a hash_one as part of a raw probe
  loop is also a hasher call. Either way, you write
  `state.hasher.hash_one(x)` (or your stub), not the SipHash math.

ANTIPATTERNS:
  - One-to-one transcription of HLIL into Rust. If your output is the
    same length as `decompile`, you've under-reconstructed.
  - Spelling out stdlib internals (RawTableInner / RawVec / Unique) when
    the matching Vec/HashMap call exists.
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

REGION-FIRST FOR LARGE FNS (preferred for anything > ~10 BBs):
  Whole-fn reconstruction is hard and noisy on big bodies. Instead:
    a. Call `region_blocks` once to list every BB and its instr range.
    b. Pick a SET of BBs that maps to a logical unit (one inlined fn,
       one match arm, one loop, one error path). The set CAN be
       non-contiguous: an inlined `Vec::push` is often BBs `[7, 12, 23]`
       (hot path + cold realloc + tail). A `match` arm with early-
       returns is often `[3, 8, 14]`. Pass `blocks=[3, 8, 14]` to
       `check_region` / `submit_region`. For a contiguous range you can
       still use `block_start`/`block_end`.
    c. Write ONLY that set's Rust as a standalone fn taking the live-in
       HLIL vars and returning the live-out one(s). Validate with
       `check_region {source, blocks}` - the binary side is scoped to
       just those blocks, diff output is tight.
    d. When the region is `perfect=True`, call
       `submit_region {source, blocks, note}`. The harness re-checks
       before persisting; accepted snippets land in the sidecar under
       `flower.regions[<addr>] = [{blocks: [int], source, score, note}]`
       so visualisation can map binary blocks 1-to-1 to recovered Rust.
    e. Aim for COVERAGE: by run-end, the union of submitted
       `region.blocks` should equal every BB index. Overlap is bad
       (ambiguous mapping); gaps are bad (untranslated logic). Treat
       each region as one source-level statement worth of work.
    f. After you've covered every meaningful region, assemble them in
       a final `submit_reconstruction`. Region snippets compose: paste
       in the body in topo order and check the full fn.

`check_reconstruction` / `submit_reconstruction` validate the WHOLE
function. `check_region` / `submit_region` validate just the BB set.
Pick the right tool for the size of the work; over-reach on big fns
is the most common cheese trap.

You have 24 turns. The harness will warn you at -4 and -1 left.

RUSTC ERRORS ON SUBMIT ARE TRANSIENT. Concurrent workers stomp a shared
tmp file; if the SAME `source` JUST PASSED `check_reconstruction` and
the submit bounces with "rustc driver aborted", re-submit it unchanged.
"""


# Shared CLI primitives - see agents/common/cli.py for details.
from cli import (
    AgentResult, CLI_ENV_SCRUB, LONG_OUTPUT_NOTE, READONLY_BUILTINS,
    drive_stream, format_context_dir, per_fn_timeout, run_with_timeout,
    tools_block, transcript_path as _transcript_path_for_id,
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
    bound_count: int = 0
    diff_count: int = 0
    # Brevity ratio: rust LOC / hlil LOC; < 1.0 = recovery, 0.0 = uncomputed.
    rust_loc: int = 0
    hlil_loc: int = 0
    compression_ratio: float = 0.0
    wide_blocks: int = 0
    wide_unlocked: bool = False
    regions: list = field(default_factory=list)  # per-region BB->Rust snippets
    complexity_gated: bool = False
    signer_bounced: bool = False
    bb_total: int = 0           # total BBs in fn (for region coverage)
    bb_covered: int = 0         # union of region.blocks
    bb_overlap: int = 0         # blocks claimed by 2+ regions (ambiguous map)


def transcript_path(rec: "FlowerResult", cwd: str | None = None) -> Path | None:
    """Thin wrapper over `cli.transcript_path` accepting a FlowerResult."""
    return _transcript_path_for_id(rec.session_id, cwd)


PER_FN_TIMEOUT_S = per_fn_timeout(default_s=300)


_NONTRIVIAL_HINTS = ("HashMap", "BTreeMap", "Vec<", "VecDeque", "Box<",
                     "Rc<", "Arc<", "String", "HashSet", "BTreeSet")


def _detect_nontrivial_struct(rust_types: str) -> str | None:
    """If `rust_types` defines a struct with at least one non-trivial
    field (heap-owning Rust type), return its name. None otherwise."""
    if not rust_types:
        return None
    import re as _re
    for m in _re.finditer(r"\bstruct\s+([A-Z][A-Za-z0-9_]*)\s*\{([^}]*)\}",
                          rust_types):
        body = m.group(2)
        if any(h in body for h in _NONTRIVIAL_HINTS):
            return m.group(1)
    return None


async def _destructor_preflight(bv, fn_addr, ctx, struct_name: str,
                                prior_block: str, model: str = "haiku") -> str:
    """One-shot destructor walk before flower's main agent starts. Returns
    the subagent's final text or empty on failure. Same prompt as the
    Task-spawnable destructor_subagent, just always-on."""
    pre_tools = t_exo.make(bv, fn_addr) + t_binja.make(ctx)
    pre_block = tools_block(pre_tools, extra_lines=[])
    sys_prompt = (
        "You walk Rust destructors to extract exact field types of a "
        "struct T. Names can be stripped; don't rely on drop_in_place "
        "symbol matching alone. Find the destructor via xrefs/HLIL "
        "shape (single ptr arg, early null-guard, sequence of "
        "[rdi+0xN] -> sub-callee dispatch ending in __rust_dealloc). "
        "For each [rdi+0xN] dispatch identify the field type from "
        "the callee's shape: 3 loads + dealloc(size*cap)=Vec<T>; "
        "ctrl/mask + iter + dealloc=HashMap<K,V>; ptr+len+cap with "
        "u8/UTF8=String else Vec<u8>; single ptr to dealloc=Box<T>. "
        "Fields with no drop dispatch are Copy/trivial. Return ONE "
        "LINE PER FIELD: `+0x{N}: <type>`. Plus `destructor: <addr>` "
        "and `total drops: N`. If no destructor candidate is found "
        "after 2-3 xref/structural attempts, return `T appears "
        "Copy/trivial - no destructor found`. Don't speculate.\n\n"
        f"Tools:\n{pre_block}"
    )
    user = (
        f"Walk the destructor of T = `{struct_name}`, receiver of the "
        f"function at {fn_addr:#x}. Return the field map.\n\n{prior_block}"
    )
    a = Agent(
        name="destructor_pre",
        system_prompt=sys_prompt,
        tools=pre_tools,
        allowed_builtins=["Read", "Grep", "Glob", "Bash"],
        model=model,
        max_turns=10,
    )
    try:
        return await asyncio.wait_for(
            a.run(user, env=CLI_ENV_SCRUB),
            timeout=180,
        )
    except Exception as e:
        return f"# destructor preflight failed: {type(e).__name__}: {e}"


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
    max_turns: int = 24,
    submit_rounds: int = 5,
    timeout_s: int | None = None,
    shared_ctx: TargetCtx | None = None,
    trace: bool = False,
    context_dir: str | None = None,
) -> FlowerResult:
    """Run flower on one fn. `prelude` appended to each check_signature.
    `submit_rounds` caps PostToolUse bounces (1 = accept first try)."""
    # Per-worker fork: shared fn_addr races under asyncio.
    if shared_ctx is None:
        ctx = TargetCtx(bv=bv, fn_addr=fn_addr)
        owns_ctx = True
    else:
        ctx = shared_ctx.fork(fn_addr)
        owns_ctx = False

    f = ctx.target_func()
    name = f.name if f else f"sub_{fn_addr:x}"
    rec = FlowerResult(name=name, address=f"{fn_addr:#x}")

    import consistency  # noqa: F401
    short = (f.symbol.short_name if f and f.symbol else None) or name
    rust_fn_name = consistency.clean_fn_name(short)
    def validator(decl: str) -> tuple[bool, str, bool, bool, float]:
        full = "\n".join(p for p in (prelude or "", decl) if p).strip()
        signer_sig, signer_types = consistency.lookup_signer(
            ctx.recoveries, fn_addr)
        try:
            r = consistency.check(full, bv=bv, fn_addr=fn_addr,
                                  rust_fn_name=rust_fn_name,
                                  signer_sig=signer_sig,
                                  signer_types=signer_types)
        except Exception as e:
            return False, f"check_reconstruction raised {type(e).__name__}: {e}", False, False, 0.0
        if r.perfect:
            score = 1.0
        elif not r.has_warnings:
            score = 0.6
        else:
            score = 0.55
        return r.perfect, r.feedback, r.has_warnings, False, score

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

    # Env knobs: SIGNER_FORCE_ITERATE_FIRST=1 to enable bounce on first-submit;
    # SIGNER_NO_GATE=1 to skip the PreToolUse wide-tool gate.
    no_force_iter = (os.environ.get("SIGNER_NO_FORCE_ITERATE") == "1"
                     or os.environ.get("SIGNER_FORCE_ITERATE_FIRST") != "1")
    stderr_buf: list[str] = []
    no_gate = os.environ.get("SIGNER_NO_GATE") == "1"

    submit_tools, captured, submit_hook = t_submit.make(
        validator=validator, max_rounds=submit_rounds, server_name="flower",
        asm_path=asm_path, hlil=hlil_text,
        force_iterate_first=not no_force_iter,
        apply_ctx=ctx,
        rust_fn_name=rust_fn_name, consistency_module=consistency,
        bv=bv, prelude=prelude, fn_addr=fn_addr,
    )
    check_tools = t_check.make(bv, fn_addr, prelude=prelude,
                               rust_fn_name=rust_fn_name,
                               recoveries=ctx.recoveries)
    exo_tools   = t_exo.make(bv, fn_addr)
    # Order: register_trace first so it's the recommended first call.
    tools = exo_tools + t_binja.make(ctx) + check_tools + submit_tools

    # Pull signer's recovery to build a copy-paste skeleton. If present,
    # we hand the agent prelude + types + signature pre-filled and frame
    # the task as 'complete the body'. Removes the chance to mismatch
    # the signature or invent struct shapes.
    sig_info, types_info = ("", "")
    if ctx.recoveries is not None:
        _sg = ctx.recoveries.get(fn_addr, "signer") or {}
        sig_info = _sg.get("rust_signature", "") or ""
        types_info = _sg.get("rust_types", "") or ""

    if sig_info:
        skeleton_lines: list[str] = []
        if types_info.strip():
            skeleton_lines.append(types_info.rstrip())
            skeleton_lines.append("")
        skeleton_lines.append(
            f"pub fn {rust_fn_name}{sig_info.strip()} {{")
        skeleton_lines.append("    // YOUR BODY HERE")
        skeleton_lines.append("}")
        skeleton = "\n".join(skeleton_lines)
        user_prompt = (
            f"Complete the body of `{name}` at {fn_addr:#x}.\n\n"
            "The prelude, struct definitions, and fn signature below are "
            "signer's recovery and MUST be preserved verbatim. Your job "
            "is only the body between the braces.\n\n"
            f"```rust\n{skeleton}\n```\n\n"
            "Workflow: `il_vars` once to learn the HLIL var names; iterate "
            "via `check_reconstruction` or `check_region`; submit via "
            "`submit_reconstruction` with the FULL source (skeleton + "
            "your body). The validator rejects any submission whose "
            "fn signature differs from the skeleton or whose struct "
            "definitions differ from the prelude."
        )
    else:
        user_prompt = (
            f"Reconstruct the Rust body of `{name}` at {fn_addr:#x}. "
            "Signer has not recovered this fn's prototype - infer both "
            "signature and types from HLIL. Call `il_vars` first, then "
            "iterate via `check_reconstruction`, then `submit_reconstruction`."
        )
    # Optional supplementary prior-stage block (formatted nicely) for
    # readers; also useful for fns with extra metadata beyond signer.
    prior_block = _format_prior(ctx.recoveries, fn_addr) if ctx.recoveries else ""
    if prior_block and not sig_info:
        user_prompt += "\n\n" + prior_block
    user_prompt += format_context_dir(context_dir)

    # Auto-spawn destructor walker if signer recovered a non-trivial struct.
    signer_types = ""
    if ctx.recoveries is not None:
        signer_types = (ctx.recoveries.get(fn_addr, "signer") or {}).get(
            "rust_types", "")
    struct_name = _detect_nontrivial_struct(signer_types)
    if struct_name:
        print(f"[flower] preflight destructor walk: T={struct_name} @ "
              f"{fn_addr:#x}", flush=True)
        walk = await _destructor_preflight(
            bv, fn_addr, ctx, struct_name, prior_block)
        if walk:
            user_prompt += (
                f"\n\n=== preflight destructor walk for `{struct_name}` ===\n"
                f"{walk}\n=== end preflight ===\n"
                "These offsets/types come from the destructor body and "
                "are AUTHORITATIVE. Use them verbatim in your "
                "reconstruction; do NOT redefine the struct."
            )
    if no_gate:
        gate_state = {"unlocked": True, "blocks": 0}
        hooks: dict = {}
    else:
        gate_matcher, gate_state = t_gate.make(server_name="flower")
        hooks = {"PreToolUse": [gate_matcher]}
    import turn_budget as t_budget
    budget_matcher, _budget_state = t_budget.make(max_turns=max_turns)
    post_hooks = [budget_matcher]
    if submit_hook is not None:
        post_hooks.append(submit_hook)
    hooks["PostToolUse"] = post_hooks

    # Read-only subagent for cross-fn context (haiku, no submit tools).
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

    # Subagent that walks `drop_in_place::<T>` to recover field types.
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
        stderr_buf=stderr_buf,
    )._build_options(env=CLI_ENV_SCRUB)

    stream = query(prompt=user_prompt, options=opts)
    prefix = f"[{name}] " if trace else None
    try:
        base = int(timeout_s) if timeout_s is not None else PER_FN_TIMEOUT_S
        from cli import scale_timeout_by_bbs
        budget = scale_timeout_by_bbs(base, f) if f else base
        await run_with_timeout(_drive(stream, rec, prefix=prefix), rec, budget)
    finally:
        if owns_ctx:
            ctx.close()
    if rec.transport_error and stderr_buf:
        tail = "".join(stderr_buf)[-4000:]
        print(f"[{name}] cli stderr (last 4KB):\n{tail}", flush=True)

    rec.submitted_source = captured.get("source", "")
    rec.submitted_name = captured.get("name", "")
    rec.regions = list(captured.get("regions") or [])
    rec.complexity_gated = bool(captured.get("complexity_gated"))
    rec.signer_bounced = bool(captured.get("signer_bounced"))
    # Region partition coverage: union(region.blocks) vs total BBs.
    try:
        rec.bb_total = len(list(f.basic_blocks)) if f else 0
    except Exception:
        rec.bb_total = 0
    if rec.regions:
        seen: set[int] = set()
        overlap: set[int] = set()
        for r in rec.regions:
            for b in (r.get("blocks") or []):
                if b in seen:
                    overlap.add(int(b))
                seen.add(int(b))
        rec.bb_covered = len(seen)
        rec.bb_overlap = len(overlap)
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
    # Re-check final submission to recover numeric stats not captured.
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
    ap.add_argument("--submit-rounds", type=int, default=5,
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
