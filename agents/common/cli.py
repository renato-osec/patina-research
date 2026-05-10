# Shared SDK CLI primitives: env scrub, transcript path, max-turns heuristic.
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable


# LD_LIBRARY_PATH from binja libs SIGTRAPs Node CLI; scrub before spawn.
CLI_ENV_SCRUB: dict[str, str] = {
    "LD_LIBRARY_PATH": "",
    "GLIBC_TUNABLES": "",
    "CLAUDECODE": "",
    "CLAUDE_CODE_ENTRYPOINT": "",
}


def transcript_path(session_id: str, cwd: str | None = None) -> Path | None:
    """Return the JSONL transcript path the Claude CLI auto-writes for a
    completed run, or None if `session_id` is empty / the SDK doesn't
    expose its session helpers.

    The CLI saves the full session at
    `~/.claude/projects/<project-key>/<session_id>.jsonl`. The project
    key is derived from `cwd`; pass None for the current directory.
    """
    if not session_id:
        return None
    try:
        from claude_agent_sdk._internal.sessions import (
            project_key_for_directory, _get_projects_dir,
        )
    except Exception:
        return None
    key = project_key_for_directory(cwd)
    return _get_projects_dir() / key / f"{session_id}.jsonl"


def suggest_max_turns(fn: Any) -> int:
    """Pick a per-function turn budget by basic-block count. Tuned by
    marinator; reuse for any agent that processes one binja Function at
    a time. `fn` is a `binaryninja.Function`."""
    n_blocks = len(list(fn.basic_blocks))
    if n_blocks >= 300:
        return 200
    if n_blocks >= 100:
        return 120
    if n_blocks >= 30:
        return 80
    return 50


# --- shared per-fn result + stream-driving primitives --------------------

@dataclass
class AgentResult:
    """Fields every per-fn agent run records. Concrete agents subclass
    this and add their domain-specific fields (e.g. submitted_decl,
    tool_counts, summary).
    """
    name: str = ""
    address: str = ""
    tool_calls: int = 0
    iter_count: int = 0
    elapsed_s: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_create_tokens: int = 0
    cost_usd: float = 0.0
    error: str = ""
    transport_error: str = ""  # post-stream warning; data still valid.
    session_id: str = ""


def drive_stream(
    stream: AsyncIterator[Any],
    rec: AgentResult,
    *,
    prefix: str | None = None,
) -> AsyncIterator[Any]:
    """Async-iter wrapper that updates `rec`'s common fields per SDK
    message and yields each msg unchanged for agent-specific handling.

    Tracks: tool_calls (per ToolUseBlock), iter_count + session_id +
    usage + cost_usd (per ResultMessage). Optional `prefix` enables the
    live trace via `trace_util.livestream`.

    Concrete agents do their own pass over yielded messages to extract
    extra signals (e.g. marinator counts per-tool stats; signer doesn't
    need anything beyond the common fields).
    """
    if prefix:
        from trace_util import livestream  # lazy: avoids hard dep when unused
        stream = livestream(stream, prefix=prefix)
    return _drive_inner(stream, rec)


async def _drive_inner(stream: AsyncIterator[Any], rec: AgentResult) -> AsyncIterator[Any]:
    # Imports kept inside so `cli.py` is importable from any agent dir
    # without claude_agent_sdk being on every PYTHONPATH at import time.
    from claude_agent_sdk.types import (
        AssistantMessage, ResultMessage, ToolUseBlock,
    )
    async for msg in stream:
        if isinstance(msg, AssistantMessage):
            for b in msg.content or []:
                if isinstance(b, ToolUseBlock):
                    rec.tool_calls += 1
        elif isinstance(msg, ResultMessage):
            rec.iter_count = msg.num_turns or 0
            rec.session_id = getattr(msg, "session_id", "") or ""
            u = msg.usage or {}
            rec.input_tokens = int(u.get("input_tokens", 0) or 0)
            rec.output_tokens = int(u.get("output_tokens", 0) or 0)
            rec.cache_read_tokens = int(u.get("cache_read_input_tokens", 0) or 0)
            rec.cache_create_tokens = int(u.get("cache_creation_input_tokens", 0) or 0)
            rec.cost_usd = float(msg.total_cost_usd or 0.0)
        yield msg


async def run_with_timeout(
    awaitable: Awaitable[None],
    rec: AgentResult,
    timeout_s: int,
    *,
    error_prefix: str = "sdk",
) -> None:
    """Run an agent driver under a wall-clock budget, mapping common
    failure modes onto `rec.error` + `rec.elapsed_s`.

    Catches asyncio.TimeoutError, BaseExceptionGroup (the SDK wraps
    transport failures in TaskGroup exceptions), and plain Exception.
    """
    def _record(msg: str) -> None:
        # session_id set => ResultMessage already streamed; demote to warning.
        if rec.session_id:
            rec.transport_error = msg
        else:
            rec.error = msg

    t0 = time.time()
    try:
        await asyncio.wait_for(awaitable, timeout=timeout_s)
    except asyncio.TimeoutError:
        _record(f"timeout after {timeout_s}s")
    except BaseExceptionGroup as eg:
        _record(f"{error_prefix}: " + " | ".join(
            f"{type(s).__name__}: {s}" for s in eg.exceptions
        ))
    except Exception as e:
        _record(f"{error_prefix}: {type(e).__name__}: {e}")
    finally:
        rec.elapsed_s = round(time.time() - t0, 1)


def per_fn_timeout(default_s: int = 600) -> int:
    """Read PER_FN_TIMEOUT_S from env, fall back to `default_s`."""
    import os
    return int(os.environ.get("PER_FN_TIMEOUT_S", str(default_s)))


def scale_timeout_by_bbs(base_s: int, fn: Any) -> int:
    """Scale a base timeout by basic-block count. Big fns get more time."""
    try:
        n = len(list(fn.basic_blocks))
    except Exception:
        return base_s
    if n >= 300:
        return base_s * 3
    if n >= 100:
        return base_s * 2
    if n >= 30:
        return int(base_s * 1.5)
    return base_s


# --- stderr capture for compiler-backed tools (rustc -> native fd 2) ---

import contextlib as _contextlib
import os as _os
import tempfile as _tempfile


@_contextlib.contextmanager
def captured_stderr():
    """fd-level redirect of native stderr to a temp file. Yields a
    dict that gets `.text` filled in on exit. Use this when you need
    rustc's diagnostics; sys.stderr-only redirects miss them."""
    saved = _os.dup(2)
    f = _tempfile.TemporaryFile()
    out: dict[str, str] = {"text": ""}
    try:
        _os.dup2(f.fileno(), 2)
        yield out
    finally:
        _os.dup2(saved, 2)
        _os.close(saved)
        try:
            f.seek(0)
            out["text"] = f.read().decode("utf-8", "replace")
        finally:
            f.close()


def with_compiler_errors(fn: Callable, *args, **kwargs):
    """Call `fn(*args, **kwargs)`. On success return the result. On
    failure re-raise as `RuntimeError` whose message includes whatever
    stderr the call produced - typically rustc's diagnostics.

    Implemented inline (not via `captured_stderr`) so we can read the
    capture buffer *before* re-raising; a context manager only fills
    its yielded dict in `__exit__`, which is too late from the except.

    Example:
        from cli import with_compiler_errors
        layout = with_compiler_errors(nacre.signature, decl, prelude=...)
    """
    saved = _os.dup(2)
    f = _tempfile.TemporaryFile()
    try:
        _os.dup2(f.fileno(), 2)
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            _os.dup2(saved, 2)         # restore so we can print/log normally
            f.seek(0)
            err = f.read().decode("utf-8", "replace").strip()
            if err:
                raise RuntimeError(f"{e}\n\nrustc:\n{err}") from e
            raise
    finally:
        try:
            _os.dup2(saved, 2)
        except OSError:
            pass
        _os.close(saved)
        f.close()


# --- system-prompt schema injection --------------------------------------

def _short_type(t: Any) -> str:
    if isinstance(t, type):
        return t.__name__
    if isinstance(t, dict):
        return str(t.get("type", t))
    return str(t)


def _schema_str(schema: dict) -> str:
    return "{" + ", ".join(f"{k}: {_short_type(v)}" for k, v in schema.items()) + "}"


# Builtins for chunking long binja outputs. Add Write/Edit if agent mutates.
READONLY_BUILTINS: list[str] = ["Bash", "Read", "Grep", "Glob"]


# SYSTEM-prompt note: how to read a tool output paged to disk.
LONG_OUTPUT_NOTE: str = (
    "If a binja tool's response says 'result exceeds maximum allowed tokens. "
    "Output has been saved to <path>', DON'T retry the same tool with the same "
    "args. Read the saved file directly: `Read offset=N limit=M`, "
    "`Bash head -N`, `Bash tail -N`, or `Grep pattern path` to find the slice "
    "you need. For huge functions prefer `disasm n=64` or HLIL on a smaller "
    "address range over a full `get_il`."
)


# --- call-graph helpers: depth-bounded callee closure + topo order ---


def add_depth_arg(parser, dest: str = "depth", short: str = "-d") -> None:
    """Register the standard `--depth/-d` flag on an argparse parser.
    Default 0 (seeds only). Pipelines feed the parsed value into
    `expand_callees` then `topo_callees_first`."""
    parser.add_argument(
        f"--{dest}", short, type=int, default=0,
        help="Call-graph BFS depth from each seed. 0=seeds only, "
             "1=seeds + direct callees, N=callees up to N hops away. "
             "Resolved targets are emitted callees-first so a parent "
             "is never analyzed before its children.",
    )


def expand_callees(bv: Any, seeds: list[Any], depth: int) -> dict[int, Any]:
    """BFS over `Function.callees` up to `depth` hops from each seed.
    Returns a dict `addr -> Function` covering the full closure
    (including the seeds themselves). `bv` is unused but kept in the
    signature so callers can pass it for future cross-bv use; today the
    Function objects already carry their own view."""
    _ = bv
    closure: dict[int, Any] = {}
    frontier = list(seeds)
    for _hop in range(max(0, depth) + 1):
        next_frontier: list[Any] = []
        for f in frontier:
            if f.start in closure:
                continue
            closure[f.start] = f
            next_frontier.extend(f.callees or [])
        frontier = next_frontier
        if not frontier:
            break
    return closure


def add_target_args(parser) -> None:
    """Register the standard target-selection flags every batch pipeline
    accepts. Keeps the surface identical across agents (marinator,
    future signer-batch, ...). Pair with `resolve_targets(bv, args)`.

    Flags: positional `fn` names, `--addresses/-a`, `--from-file`,
    `--filter`, `--only-unnamed`, `--depth/-d`."""
    parser.add_argument("fn", nargs="*", help="Function names")
    parser.add_argument(
        "--addresses", "-a", nargs="*", default=[],
        help="Function addresses (0x... or decimal)",
    )
    parser.add_argument(
        "--from-file", default="",
        help="Read names/addresses one per line",
    )
    parser.add_argument(
        "--filter", default="",
        help="Substring filter on full_name/name",
    )
    parser.add_argument(
        "--only-unnamed", action="store_true",
        help="With --filter, keep only sub_/data_ names",
    )
    add_depth_arg(parser)


def resolve_targets(bv: Any, args, *, log_err=None) -> list[tuple[str, int]]:
    """Resolve the target set from `--addresses`/`fn`/`--filter`/`--from-file`,
    depth-expand the closure via `expand_callees`, then topo-order
    callees-first via `topo_callees_first`. Returns `[(name, addr)]`.

    `log_err` is an optional `(msg) -> None` for unresolved entries; defaults
    to writing to stderr."""
    import sys as _sys
    from pathlib import Path as _Path
    if log_err is None:
        log_err = lambda m: _sys.stderr.write(m + "\n")  # noqa: E731

    seeds: dict[int, Any] = {}

    def _add(f):
        seeds.setdefault(f.start, f)

    def _func_at(addr: int):
        return bv.get_function_at(addr) or next(
            iter(bv.get_functions_containing(addr) or []), None,
        )

    def _func_by_name(name: str):
        for f in bv.functions:
            if (f.symbol.full_name or "") == name or f.name == name:
                return f
        for f in bv.functions:
            if name in (f.symbol.full_name or "") or name in f.name:
                return f
        return None

    if getattr(args, "from_file", "") or getattr(args, "from-file", ""):
        path = getattr(args, "from_file", "") or getattr(args, "from-file", "")
        for raw in _Path(path).read_text(encoding="utf-8").splitlines():
            row = raw.strip()
            if not row or row.startswith("#"):
                continue
            f = None
            if row.startswith("0x"):
                try:
                    f = _func_at(int(row, 16))
                except ValueError:
                    pass
            else:
                f = _func_by_name(row)
            if f is None:
                log_err(f"[targets] no function for {row!r}")
                continue
            _add(f)

    flat_addrs: list[str] = []
    for raw in getattr(args, "addresses", []) or []:
        flat_addrs.extend(p for p in str(raw).split(",") if p.strip())
    for raw in flat_addrs:
        try:
            addr = int(raw, 16) if str(raw).startswith("0x") else int(raw)
        except ValueError:
            log_err(f"[targets] bad address {raw!r}")
            continue
        f = _func_at(addr)
        if f is None:
            log_err(f"[targets] no function at {raw}")
            continue
        _add(f)

    for name in getattr(args, "fn", None) or []:
        f = _func_by_name(name)
        if f is None:
            log_err(f"[targets] no function matches {name!r}")
            continue
        _add(f)

    if getattr(args, "filter", ""):
        only_unnamed = getattr(args, "only_unnamed", False)
        for f in bv.functions:
            full = f.symbol.full_name or ""
            if args.filter not in full and args.filter not in f.name:
                continue
            if only_unnamed and not (
                f.name.startswith("sub_") or f.name.startswith("data_")
            ):
                continue
            _add(f)

    closure = expand_callees(bv, list(seeds.values()), getattr(args, "depth", 0))
    ordered = topo_callees_first(list(closure.values()))
    return [(f.name, f.start) for f in ordered]


async def run_targets_gated(
    bv: Any,
    targets: list[tuple[str, int]],
    work: Callable[..., Awaitable[Any]],
    *,
    workers: int = 4,
    log: Callable[[str], None] | None = None,
) -> list[Any]:
    """Fan out `work(name, addr)` across `targets` with two gates:
    1. asyncio.Semaphore(workers)  - bound concurrency
    2. per-target asyncio.Event    - a parent never starts until every
       callee in `targets` has signalled done.

    `targets` is expected in topo order (callees-first), as produced by
    `resolve_targets`. Forward-edge filtering on topo position keeps the
    dep DAG cycle-free even if the underlying call graph has cycles.

    Returns results in input order. Each `work()` is `(name, addr)
    -> Awaitable[Result]`; exceptions propagate via asyncio.gather."""
    log = log or (lambda _m: None)
    target_set = {addr for _, addr in targets}
    order = {addr: i for i, (_, addr) in enumerate(targets)}
    events: dict[int, asyncio.Event] = {addr: asyncio.Event() for _, addr in targets}

    deps_by_addr: dict[int, list[asyncio.Event]] = {}
    for _name, addr in targets:
        f = bv.get_function_at(addr)
        ds: list[asyncio.Event] = []
        if f is not None:
            for c in (f.callees or []):
                if (c.start in target_set
                        and c.start != addr
                        and order[c.start] < order[addr]):
                    ds.append(events[c.start])
        deps_by_addr[addr] = ds

    sem = asyncio.Semaphore(workers)

    async def _one(name: str, addr: int):
        deps = deps_by_addr[addr]
        done = events[addr]
        if deps:
            await asyncio.gather(*(d.wait() for d in deps))
        try:
            async with sem:
                return await work(name, addr)
        finally:
            done.set()

    return await asyncio.gather(*(_one(n, a) for n, a in targets))


def topo_callees_first(fns: list[Any]) -> list[Any]:
    """Reorder `fns` so every callee appears before its callers (within
    the input set). Kahn's algorithm on intra-set edges; cycles broken
    arbitrarily - every input fn is emitted exactly once. Deterministic
    by entry-address tie-break.

    This is the ordering pipelines want when they want children
    analyzed before parents - a parent's run can read whatever
    annotations/comments/types the children's runs left behind."""
    pool: dict[int, Any] = {f.start: f for f in fns}
    callees_in: dict[int, set[int]] = {
        addr: {c.start for c in (f.callees or []) if c.start in pool and c.start != addr}
        for addr, f in pool.items()
    }
    callers_in: dict[int, set[int]] = {addr: set() for addr in pool}
    for addr, cs in callees_in.items():
        for c in cs:
            callers_in[c].add(addr)
    in_deg = {a: len(cs) for a, cs in callees_in.items()}
    ready = sorted(a for a, d in in_deg.items() if d == 0)
    out: list[Any] = []
    while ready:
        a = ready.pop(0)
        out.append(pool[a])
        for caller in callers_in[a]:
            in_deg[caller] -= 1
            if in_deg[caller] == 0:
                ready.append(caller)
        ready.sort()
    # cycle remainder: any fn whose callees-in-pool include itself
    # transitively. Emit by address for determinism.
    for a in sorted(pool):
        if in_deg[a] > 0:
            out.append(pool[a])
    return out


def tools_block(
    tools: list,
    *,
    header: str = "Tools (exact arg keys - do not guess):",
    extra_lines: list[str] | None = None,
) -> str:
    """Render a one-line-per-tool block listing every `@tool`-decorated
    callable's name, input schema, and description. Inject into the
    agent's SYSTEM prompt so it can't get the arg keys wrong:

        from cli import tools_block
        SYSTEM = SYSTEM_TEMPLATE.replace("{tools}", tools_block(my_tools))

    Use `str.replace`, NOT `str.format`: each rendered schema contains
    literal `{addr: int, ...}` braces that Python's str.format would
    interpret as format placeholders -> KeyError.

    Each tool exposes `.name`, `.description`, `.input_schema` after
    decoration; we pad columns for readability.
    """
    if not tools:
        return header
    pad = max(len(t.name) for t in tools)
    lines = [header]
    for t in tools:
        schema = _schema_str(t.input_schema or {})
        desc = ((t.description or "").strip().splitlines() or [""])[0]
        line = f"  - {t.name:<{pad}}  {schema}"
        if desc:
            line += f"  - {desc}"
        lines.append(line)
    if extra_lines:
        lines.extend(extra_lines)
    return "\n".join(lines)
