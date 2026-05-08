# Per-function signature recovery harness.
#
# Reads a binja BinaryView, picks one function by address, and asks an
# LLM agent to propose its Rust source-level signature. The agent has
# read-only inspection tools (decompile, get_il, stack_vars, xrefs)
# plus an iterative `check_signature` tool that compares its current
# guess against the target's actual SysV-x64 register usage via
# sigcheck (nacre fn ABI ↔ exoskeleton trace). The agent finishes by
# calling `submit_signature` with its highest-confidence decl.
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


SYSTEM_TEMPLATE = """Recover the Rust *signature + the `types` it references* for one
function in a binja database. Layout-only matches don't count: the harness
compares your declared types' offsets against the binary's actual derefs
and rejects when the type is too small or papered over.

{tools}

`signature`: parens param list ± return, e.g. `(input: &str) -> u32`.
`types`:     Rust source - `use ...;`, `pub struct/enum`, `type X = ...`.
NEVER `self`/`&self`/`&mut self` - use `(this: &T)`, `(this: &mut T)`, `(this: T)`.

ANTIPATTERNS the harness rejects (even on layout match):
  1. Offset-named fields (`f30`, `_pad0`, `p1`, `s2`, `_a`, `_8`).
  2. Skip-arrays papering over bytes (`_pad: [u64; 6]`, `_a: [u8; 0x30]`).
     24B chunk -> try Vec/String. 48B -> HashMap. 16B -> &str / fat ref.
  3. Decomposed wrappers - turning a wrapper's bytes into raw fields:
       `*const T` / `*mut T` field      -> use `&T` / `&mut T` / `Box<T>`
       `(ptr, len)` pair                -> `&[T]` / `&str` / `Box<[T]>`
       `(ptr, cap, len)` triple         -> `Vec<T>` / `String`
       `(ctrl, bucket_mask, items, ...)`  -> `HashMap<K,V>` (those are the
                                          guts of `hashbrown::RawTableInner`,
                                          which lives INSIDE `HashMap`)
       `(left, right, parent, key)`     -> `BTreeMap<K,V>` node - submit the map
     Lifetimes are fine: `pub struct S<'a> { x: &'a Foo }` compiles. The
     harness pretty-prints `&S<'_>` happily. Don't drop a lifetime to
     dodge a perceived rustc error - concurrency artifacts retry clean.

  Heuristic for #3: if you find yourself writing `*const`/`*mut` plus
  two or more `usize`/`u64`/`u32` fields in the SAME struct, you're
  decomposing something. Look for the wrapper that owns those bytes.

WORKFLOW (cheap -> expensive; bail to `submit_signature` early):
  0. **Fire the `context` subagent in parallel BEFORE you start.** It
     gathers the single caller's HLIL + concrete callee names while
     you do steps 1-3. Don't block on it; check its result back when
     you need cross-fn evidence. Spawn it as your first action.
  1. `register_trace` once - offsets, flavors, sret hint.
  2. Per interesting offset: `field_accesses {register, offset, context:3}`.
     The asm window's callees (drop_in_place::<T>, alloc::alloc, panic_*)
     name the type. Bump context to 5-8 if needed.
  3. Stuck on a callee/range? `hlil_around {addr, context}` (or
     mlil_around/llil_around). `hlil_range {start, end}` for spans.
  4. **Walk the destructor early - non-trivial receiver types.** If
     the receiver is a struct (any `&T`/`*mut T` with multiple field
     offsets observed), spawn the `destructor` subagent via Task. It
     finds T's destructor by xref / structural shape (works on
     stripped binaries - does NOT need `drop_in_place::<T>` symbols)
     and returns one line per field with EXACT types like `+0x00:
     Vec<u8>`, `+0x30: HashMap<usize, usize>`. This is the highest-
     signal pivot for struct recovery and saves your context.
  5. Other cross-fn questions (what is sub_X? what's the type at
     callsite Y?): spawn the `context` subagent via Task. Don't block.
  6. `check_signature {types, signature}` - read `issues:` AND the
     per-arg "expected vs observed offsets" lines.
  7. `submit_signature` early. First submit is never final; each bounce
     hands you the full asm-file path + full HLIL to refine with.

ARITY-TRAP: if the only `check_signature` complaint is one missing reg
(e.g. `missing ['rdx']`) and every other slot is OK, the binary likely
optimized away a small bool/u8 arg. Keep the arg - don't drop it.

NEVER use whole-function `decompile`/`get_il` or `disasm` with n>128
BEFORE the first submit. They're locked.

`check_signature` and `submit_signature` validate identically - don't
loop on check_signature, just SUBMIT. You have 16 turns.

RUSTC ERRORS ON SUBMIT ARE TRANSIENT. If `submit_signature` bounces with
`rustc driver aborted (fatal compile error)` and the SAME `(types,
signature)` JUST PASSED `check_signature` with `perfect=True`, that's a
concurrency artifact - concurrent workers stomping a shared tmp file -
NOT a real compile error. Re-submit the EXACT same `(types, signature)`
unchanged; it will succeed. NEVER respond to a transient rustc bounce by
flattening idiomatic types (`Vec<T>` -> `*mut T + cap + len`, `HashMap` ->
spelled-out RawTableInner, `&[T]` -> `(*const T, usize)` pair, lifetimes
-> removed). Layout score 1.00 with idiomatic types beats layout score
1.00 with primitive soup every time - the whole point of this agent is
to RECOVER the high-level Rust shape. If you're tempted to "collapse to
primitives to dodge a compile error", you're losing the run.
"""


# Shared CLI primitives - see agents/common/cli.py for details.
from cli import (
    AgentResult, CLI_ENV_SCRUB, LONG_OUTPUT_NOTE, READONLY_BUILTINS,
    drive_stream, per_fn_timeout, run_with_timeout, tools_block,
    transcript_path as _transcript_path_for_id,
)


@dataclass
class SignerResult(AgentResult):
    submitted_decl: str = ""      # types + "\n\n" + signature (joined for legacy)
    submitted_types: str = ""     # the agent's `types` field on submit
    submitted_signature: str = "" # the agent's `signature` field on submit
    confidence: float = 0.0       # agent's self-reported number
    rationale: str = ""
    final_score: float = 0.0      # ground-truth score on the last submission
    final_perfect: bool = False
    submit_attempts: int = 0      # times the agent called submit_signature
    # True iff `submit_rounds` was exhausted without a perfect submit:
    # the harness gave up bouncing and accepted the last (failing) decl.
    # `final_perfect == False and budget_exhausted == True` ⇒ run failed.
    budget_exhausted: bool = False
    # Number of times the PreToolUse hook blocked a wide untargeted
    # decompilation call (`decompile`/`get_il`) BEFORE the first
    # `check_signature`. High counts = agent leaning on whole-fn decomp
    # instead of the targeted register_trace/field_accesses/il_around
    # workflow. `wide_unlocked == False` ⇒ agent never validated, the
    # gate stayed closed for the whole run.
    wide_blocks: int = 0
    wide_unlocked: bool = False


def transcript_path(rec: "SignerResult", cwd: str | None = None) -> Path | None:
    """Thin wrapper over `cli.transcript_path` accepting a SignerResult."""
    return _transcript_path_for_id(rec.session_id, cwd)


PER_FN_TIMEOUT_S = per_fn_timeout(default_s=300)


async def sign_function(
    bv: bn.BinaryView,
    fn_addr: int,
    *,
    prelude: str | None = None,
    model: str = "sonnet",
    max_turns: int = 16,
    submit_rounds: int = 3,
    timeout_s: int | None = None,
    shared_ctx: TargetCtx | None = None,
    trace: bool = False,
) -> SignerResult:
    """Run the signer agent on one function. `prelude` is an optional Rust
    snippet (struct/use defs) appended to every check_signature call so
    user types can be looked up by name. `submit_rounds` caps how many
    times the harness's PostToolUse validator will bounce a non-perfect
    submission back at the agent for refinement (1 = accept first try)."""
    if shared_ctx is None:
        ctx = TargetCtx(bv=bv, fn_addr=fn_addr)
        owns_ctx = True
    else:
        ctx = shared_ctx
        ctx.fn_addr = fn_addr
        owns_ctx = False

    f = ctx.target_func()
    name = f.name if f else f"sub_{fn_addr:x}"
    rec = SignerResult(name=name, address=f"{fn_addr:#x}")

    # Validator the PostToolUse hook calls on every submit_signature
    # firing. Returns (perfect, feedback, has_warnings, arity_only).
    #   has_warnings: triggers a free first-round scold (placeholder
    #     structs / skip-arrays / offset-named fields).
    #   arity_only: not perfect, BUT the only mismatch is missing-reg
    #     arity (binary optimized away a small bool/u8 arg). Per the
    #     SYSTEM rule we accept the submission instead of bouncing,
    #     since asking the agent to "refine" would push them to drop
    #     the real arg.
    import sigcheck
    def validator(decl: str) -> tuple[bool, str, bool, bool]:
        try:
            r = sigcheck.check_signature(bv, fn_addr, decl, prelude=prelude)
        except Exception as e:
            return False, f"check_signature raised {type(e).__name__}: {e}", False, False
        has_warnings = any(i.startswith("warning:") for i in r.issues)
        arity_only = (
            not r.perfect
            and r.sret_match
            and r.return_match
            and all(s.agree or "missing" in (s.note or "") for s in r.slots)
        )
        return r.perfect, r.summary(), has_warnings, arity_only

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
    no_force_iter = os.environ.get("SIGNER_NO_FORCE_ITERATE") == "1"
    no_gate = os.environ.get("SIGNER_NO_GATE") == "1"

    submit_tools, captured, submit_hook = t_submit.make(
        validator=validator, max_rounds=submit_rounds, server_name="signer",
        asm_path=asm_path, hlil=hlil_text,
        force_iterate_first=not no_force_iter,
        # On every accept branch, write the recovered Rust signature
        # back to the shared bndb (same primitives as marinator/write).
        apply_ctx=ctx,
    )
    check_tools = t_check.make(bv, fn_addr, prelude=prelude)
    exo_tools   = t_exo.make(bv, fn_addr)
    # Order matters: register_trace appears first so the agent's prompt
    # tools_block lists it at the top - it's the recommended first call.
    tools = exo_tools + t_binja.make(ctx) + check_tools + submit_tools

    user_prompt = (
        f"Recover the Rust signature of `{name}` at {fn_addr:#x}. "
        f"Inspect the function, propose a decl, iterate via "
        f"`check_signature`, then `submit_signature`."
    )
    if no_gate:
        gate_state = {"unlocked": True, "blocks": 0}
        hooks: dict = {}
    else:
        gate_matcher, gate_state = t_gate.make(server_name="signer")
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
        {f"mcp__signer__{t.name}" for t in (exo_tools + t_binja.make(ctx))}
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
    opts = Agent(
        name="signer",
        system_prompt=SYSTEM_TEMPLATE.replace("{tools}", block),
        tools=tools,
        allowed_builtins=READONLY_BUILTINS + ["Task"],
        hooks=hooks,
        model=model,
        max_turns=max_turns,
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

    rec.submitted_decl = captured["decl"]
    rec.submitted_types = captured.get("types", "")
    rec.submitted_signature = captured.get("signature", "")
    rec.confidence = captured["confidence"]
    rec.rationale = captured["rationale"]
    rec.submit_attempts = captured["attempts"]
    rec.budget_exhausted = bool(captured.get("exhausted"))
    rec.wide_blocks = int(gate_state.get("blocks", 0))
    rec.wide_unlocked = bool(gate_state.get("unlocked", False))
    # The PostToolUse hook validated each submit; the last entry of
    # `validations` is authoritative for the final submission.
    if captured["validations"]:
        last_decl, last_perfect, _last_feedback = captured["validations"][-1]
        rec.final_perfect = last_perfect
        if last_decl and not rec.error:
            import sigcheck
            try:
                r = sigcheck.check_signature(bv, fn_addr, last_decl,
                                             prelude=prelude)
                rec.final_score = round(r.score, 3)
            except Exception as e:
                rec.error = f"final-check: {type(e).__name__}: {e}"
    return rec


async def _drive(stream, rec: SignerResult, *, prefix: str | None = None) -> None:
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
    ap.add_argument("--model", default="sonnet")
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
