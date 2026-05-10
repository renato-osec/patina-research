# Per-fn marination harness; binja read tools + marinator write/submit.
from __future__ import annotations

import os, sys
from pathlib import Path
sys.path[:0] = [str(Path(__file__).resolve().parent),
                str(Path(__file__).resolve().parent.parent / "common")]
os.environ.setdefault("BN_DISABLE_USER_PLUGINS", "1")

import asyncio
import time
from dataclasses import dataclass, field

import binaryninja as bn
from claude_agent_sdk import query
from claude_agent_sdk.types import (
    AssistantMessage, ResultMessage, TextBlock, ToolUseBlock,
    UserMessage, ToolResultBlock,
)

from agent import Agent
from tools.ctx import TargetCtx
from tools import binja as t_binja
import write as t_write    # local: agents/marinator/write.py
import submit as t_submit  # local: agents/marinator/submit.py


SYSTEM = """You are marinating a Binary Ninja database - making the HLIL of one
function read like real source code. You only mutate annotations: variable
names, function names, simple retypes, function prototypes, comments, and
declared C types. You never patch bytes.

You have direct in-process access to BN through `mcp__marinator__*` tools.

  decompile / get_il / disasm / functions_at / xrefs / stack_vars / strings /
  get_user_type / hexdump            (READ - gather context)

  rename_function / rename_variable / rename_variables / retype_variable /
  set_function_prototype / declare_c_type / set_function_comment /
  set_address_comment                (WRITE - marinate; serialized + undoable)

  finish                             (terminal - call once with a CHANGES summary)

You also have Claude Code's standard toolkit: Bash, Read, Write, Edit, Grep,
Glob.

When a tool result is too long to inline, the harness automatically writes it
to a temp file and replaces the result with a one-line pointer like
`Output saved to /path/to/...`. **You don't write the file yourself** - just
notice the path and work from it. From there:

  - `Read <path>` with `offset` / `limit` to scan portions cheaply.
  - `Grep` for symbols / regex without loading the whole file.
  - `Bash` for the heavier passes you cannot do with Read/Grep alone, e.g.
    `grep -oE '\\b<pat>\\b' <path> | sort | uniq -c | sort -rn | head` to
    rank by frequency.

The point is: never re-fetch the full HLIL into your context. Re-rank, re-
inspect specific lines, then mutate.

## Priorities, in order

1. **Variable renames.** Every auto-named local - `var_38`, `rax_12`, `rbp_1`,
   `i_4` - that you can give a meaningful name. Look at how it's used:
     - stored to and passed to `rust_alloc(sz)` -> `alloc_size`
     - read at `+0x18` of a Vec-shaped pointer -> `vec_len`
     - u8 compared to 0/1 -> boolean flag (e.g. `is_long_side`)
     - chased through a stride-`0x1c8` BTree node -> `<map>_node`,
       `<map>_descend_count`, `<map>_keys_count`
   Use `stack_vars` to enumerate. Use `rename_variables` for batches -
   failures are isolated per pair, the rest still apply.

2. **Function prototype.** If params are generic (`int64_t arg2`), tighten with
   `set_function_prototype`. Verify arg count against the prologue register
   saves (rdi/rsi/rdx/rcx/r8 in SysV-x64) before guessing - getting the count
   wrong propagates everywhere.

3. **Single-variable retypes.** If a local is clearly a `struct Foo*`, retype.
   Do NOT invent struct layouts - only retype to a type that already exists in
   the BV (`get_user_type` to confirm) or that you're declaring NOW for a
   well-known shape.

   **Retyping struct fields propagates through every HLIL access in every
   function** - this is the single highest-leverage operation. If you can
   replace `field_10c8` with `block_height` on the parent struct once, every
   caller's decompile improves.

   Recurring Rust shapes worth declaring once if you see them:
     - Rust `Vec` (24 B): `struct Vec24 { uint64_t ptr; uint64_t cap; uint64_t len; }`
     - tagged `OptionUnitQty` (24 B): `struct OptionUnitQty { uint64_t tag; uint64_t unit; int64_t value; }`
     - `BTreeMap` header (24 B): `struct BTreeMap24 { uint64_t root_ptr; uint64_t height; uint64_t length; }`

4. **Function rename.** Only `rename_function` for callees if their body is
   trivially obvious (drop wrappers, vec push/grow, panic helpers) - and only
   when you can confirm it from a quick `decompile` of the callee. Otherwise
   leave it alone.

5. **Comments.** VERY conservative. `set_function_comment` only if you have a
   striking ONE-LINE summary. `set_address_comment` only for genuinely
   non-obvious magic constants or workaround lines. NEVER narrate phases,
   NEVER explain what code does - names should do that.

## Standard-library calls - use the exact qualified name, every time.

When you recognize a callee as a stdlib / well-known-crate function, rename
it to its FULL Rust path. Not a paraphrase, not the impl detail. The Rust
docs path is the right answer:

  `siphash24`              -> `<std::hash::random::RandomState as core::hash::BuildHasher>::hash_one`
  `swisstable_*` / probe   -> `<std::collections::hash::map::HashMap<K,V>>::get` (or `insert`/`contains_key`)
  `rb_tree_descent`        -> `<alloc::collections::btree::map::BTreeMap<K,V>>::iter` / `first_key_value`
  `vec_push_grow`          -> `<alloc::vec::Vec<T>>::push`
  `panic_str("...")`         -> `core::option::expect_failed` / `core::panicking::panic_fmt`
  `drop_in_place_*`        -> `core::ptr::drop_in_place::<T>`

If you only know the *family* (it's some BTreeMap method, but you can't tell
which), prefix `maybe_` once: `maybe_<alloc::collections::btree::map::BTreeMap<K,V>>::get`.
Never invent shorthand. Never use the implementation name (`siphash24`,
`hashbrown_raw_find`) when the public API name exists.

## Destructors - be paranoid, flag explicitly.

Rust drop glue is everywhere and easy to misname. A function that ends in
`rust_dealloc(...)` after a chain of small unconditional calls - each of
which probably also ends in `rust_dealloc` - is **drop glue**, not a generic
"free" / "cleanup" / "release" helper. Name it accordingly or do not name it
at all:

  - `core::ptr::drop_in_place::<T>`         - ALWAYS the right name for the
    auto-generated destructor, where `<T>` is the type whose fields it drops.
  - `<T as core::ops::Drop>::drop`          - for a user-defined `impl Drop`,
    when you can identify `T` from a callsite or vtable slot.

Rules:
  * Never rename a drop fn as `free_X`, `cleanup_X`, `release_X`, `destroy_X`
    or any English-action variant. Those obscure that it's drop glue and
    that the type is part of the name.
  * Never rename `mem::drop` (the no-op intrinsic that just consumes its
    argument) as `drop_in_place` or vice-versa - they're different.
  * If you can't identify `<T>` from xrefs / vtable / strings, **do not
    rename**. Leave it `sub_<addr>` and add a one-line `set_function_comment`
    that flags it: `"drop glue, type unknown"`. A wrong type parameter is
    worse than no name - later passes will trust it.
  * Drop chains are read-only by convention: their body is recursive drop
    calls + a final `rust_dealloc`. If the body does anything else
    (mutates fields, returns a non-unit value, calls non-drop user fns), it
    is **not** drop glue - name it as a normal function instead.

6. **Rename THIS function - last step, conservative.** After the body, the
   variables, and the prototype are understood, call `rename_function` on
   the target itself if and only if one of the following holds:
     - the current name is `sub_<addr>` or `data_<addr>` (no real name yet), OR
     - the current name describes something different from what the body
       actually does (a previous pass got it wrong, or the function was
       renamed by a caller's prototype guess and the body disagrees).
   Leave the name alone if it already accurately describes the body, even
   approximately. Use snake_case, action-first (`decode_block_header`,
   not `block_header_decoder`). If you cannot commit to a high-confidence
   understanding, do NOT rename. This must be the final mutation before
   `finish` so callers' HLIL reflects the new name on their next decompile.

   This rule is designed for iterative improvement loops: pass N may
   rename `sub_4198b0` -> `parse_record`; pass N+1 may discover the body
   actually decodes a transaction list and replace it with `decode_tx_list`.

## Failure modes you will hit

- `rename_variable` errors on SSA-suffixed names (`foo_2`). Try the base
  name (`foo`); all SSA versions inherit. If even that fails, skip - don't
  fight it.
- `retype_variable` fails until `declare_c_type` has registered the type.
- `set_function_prototype` is strictest: must parse as a valid C declaration
  including the function's name. Use the form: `<ret> <fn_name>(<params>)`.
- `declare_c_type` must contain a NAMED type. An anonymous struct without a
  typedef is rejected.

## BTree stride taxonomy (this binary)

If you see chained `*(node + 0xN)` dereferences with a fixed stride, the
agent that marinated `exchange_end_block` documented these:

  0x1c8  Address-key BTreeMap node (e.g. user_states)
  0x458  open_orders u32-key BTreeMap node
  0xab8  user_states inner BTreeMap node
  0xa0   `Position` value stride

Naming: `<map>_node`, `<map>_leaf`, `<map>_descend_count`, `<map>_keys_count`,
`<map>_node_idx`.

## Naming conventions

- snake_case identifiers; PascalCase struct names.
- Unit/qty/tag fields: `*_tag`, `*_unit`, `*_value` (OptionUnitQty convention).
- Rust Vec24 access: `<thing>_ptr`, `<thing>_cap`, `<thing>_len`.
- Loop indices: `<thing>_idx`. Drop SSA `_1 _2 _3` suffixes - pick one
  semantic name.

## Workflow

Small function (<50 blocks): in-context is fine. `decompile` it once,
`stack_vars`, plan and batch `rename_variables`, optionally tighten the
prototype / declare a struct / set a one-line comment, then `finish`.

Large function (≥50 blocks): the first `decompile` call will overflow and
the harness will save it to a path. From that point, work off the path:
rank unnamed identifiers by frequency to pick the next batch, inspect a
handful of use sites for the chosen ones, apply a batch of renames, then
re-`decompile` (which produces a *new* saved file with the new names) and
repeat. The LLM context stays small because it only ever sees ranked
summaries, not the full IL.

Stop iterating well before the budget - do not chase tiny wins indefinitely.
"""


# Shared CLI primitives - env-scrub, transcript discovery, drive_stream,
# run_with_timeout, AgentResult base. See agents/common/cli.py.
from cli import (
    AgentResult,
    CLI_ENV_SCRUB as _CLI_ENV_SCRUB,
    drive_stream,
    format_context_dir,
    per_fn_timeout,
    run_with_timeout,
    suggest_max_turns as _suggest_max_turns,
    transcript_path as _transcript_path_for_id,
)


@dataclass
class MarinationResult(AgentResult):
    bndb: str = ""
    summary: dict | None = None       # set by `finish`
    tool_counts: dict = field(default_factory=dict)
    log: list[str] = field(default_factory=list)


PER_FN_TIMEOUT_S = per_fn_timeout(default_s=1200)


def transcript_path(rec: "MarinationResult", cwd: str | None = None) -> Path | None:
    """Return the JSONL transcript path for a completed marination, or None.

    Thin wrapper over `cli.transcript_path` that accepts a MarinationResult
    so callers don't have to dig out `rec.session_id` themselves.
    """
    return _transcript_path_for_id(rec.session_id, cwd)


async def marinate_function(
    bv: bn.BinaryView,
    fn_addr: int,
    *,
    model: str = "opus",
    max_turns: int | None = None,
    quiet: bool = True,
    shared_ctx: TargetCtx | None = None,
    context_dir: str | None = None,
) -> MarinationResult:
    """Run the marinator agent on one function. Caller owns the BV lifecycle.

    Pass `shared_ctx` to reuse a single TargetCtx across many fns (and share
    its asyncio.Lock). Otherwise a fresh per-fn ctx is built - fine for serial
    runs, but parallel callers should always pass a shared ctx so the
    write_lock actually serializes across agents.

    The Claude CLI auto-writes a JSONL transcript of the whole agent
    conversation under `~/.claude/projects/<project-key>/<session_id>.jsonl`
    (every tool call, tool result, and assistant text block). After the run,
    `transcript_path(rec)` resolves that path for inspection.
    """
    f = bv.get_function_at(fn_addr) or next(
        iter(bv.get_functions_containing(fn_addr) or []), None,
    )
    if f is None:
        return MarinationResult(name=hex(fn_addr), error=f"no function at {fn_addr:#x}")

    rec = MarinationResult(name=f.name, address=hex(f.start), bndb=bv.file.filename)
    if max_turns is None:
        max_turns = _suggest_max_turns(f)
    rec.log.append(f"[budget] max_turns={max_turns} blocks={len(list(f.basic_blocks))}")

    if shared_ctx is None:
        ctx = TargetCtx(bv=bv, fn_addr=f.start)
    else:
        # Per-worker ctx that shares bv + write_lock with the pipeline's
        # parent ctx; gets its own fn_addr so concurrent workers don't
        # race on the default-target attribute the tools read.
        ctx = shared_ctx.fork(f.start)

    submit_tools, captured = t_submit.make()
    tools = t_binja.make(ctx) + t_write.make(ctx) + submit_tools

    a = Agent(
        name="marinator",
        system_prompt=SYSTEM,
        tools=tools,
        # Claude Code's standard toolkit. The agent uses Bash + Write + Grep
        # to dump HLIL to /tmp and run grep+awk on it, mirroring the human
        # workflow that scales to 500+ block functions without blowing context.
        allowed_builtins=["Bash", "Read", "Write", "Edit", "Grep", "Glob"],
        model=model,
        max_turns=max_turns,
    )
    user_prompt = (
        f"Marinate the function `{f.name}` at {f.start:#x}.\n"
        f"Start with `decompile` and `stack_vars`, then plan a batch of renames.\n"
        f"Apply changes directly. Finish with `finish`."
    )
    user_prompt += format_context_dir(context_dir)

    stream = query(prompt=user_prompt, options=a._build_options(env=_CLI_ENV_SCRUB))
    await run_with_timeout(_drive(stream, rec, quiet=quiet), rec, PER_FN_TIMEOUT_S)
    rec.summary = captured.get("summary")
    if rec.summary is None and rec.tool_counts:
        c = rec.tool_counts
        rec.summary = {
            "renamed_vars": c.get("rename_variable", 0) + c.get("rename_variables", 0),
            "renamed_funcs": c.get("rename_function", 0),
            "retypes": c.get("retype_variable", 0) + c.get("set_function_prototype", 0),
            "comments": c.get("set_function_comment", 0) + c.get("set_address_comment", 0),
            "types_declared": c.get("declare_c_type", 0),
            "_fallback": True,
        }
    return rec


async def _drive(stream, rec: MarinationResult, *, quiet: bool) -> None:
    """Marinator-specific stream handling on top of `cli.drive_stream`:
    accumulate per-tool call counts and a verbose log when not quiet.
    The common bookkeeping (tool_calls, iter_count, usage, session_id,
    cost_usd) is handled by drive_stream itself."""
    prefix = None if quiet else f"[{rec.name}] "
    async for msg in drive_stream(stream, rec, prefix=prefix):
        if isinstance(msg, AssistantMessage):
            for b in msg.content:
                if isinstance(b, ToolUseBlock):
                    raw = b.name or ""
                    short = raw.split("__")[-1] if "__" in raw else raw
                    rec.tool_counts[short] = rec.tool_counts.get(short, 0) + 1
                    if not quiet:
                        rec.log.append(f"[tool] {short} {str(b.input)[:140]}")
                elif isinstance(b, TextBlock) and not quiet:
                    text = (b.text or "").strip()
                    if text:
                        rec.log.append(text[:200])
        elif isinstance(msg, UserMessage) and not quiet:
            content = msg.content if isinstance(msg.content, list) else []
            for b in content:
                if isinstance(b, ToolResultBlock):
                    text = b.content if isinstance(b.content, str) else str(b.content)
                    rec.log.append(f"[res] {text[:160]}")
