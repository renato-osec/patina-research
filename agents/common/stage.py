# Shared pipeline orchestration. Every per-fn agent (signer, flower,
# marinator) runs the same skeleton: load bndb -> Recoveries sidecar
# + TargetCtx -> resolve_targets -> run_targets_gated -> save JSON +
# sidecar + bndb. The agent-specific bits live in `StageSpec`:
#   name              short label for logs + filenames
#   out_bndb_suffix   `.signed.bndb` etc.
#   run_one           async per-fn body returning the result record
#   format_done       agent's `[done ]` log line for one record
#   add_extra_args    optional argparse hook (prelude-file etc.)
from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from threading import Lock
from typing import Awaitable, Callable

import binaryninja as bn

from cli import add_target_args, resolve_targets, run_targets_gated
from recoveries import Recoveries
from tools.ctx import TargetCtx


@dataclass
class StageArtifacts:
    name: str
    out_dir: Path
    out_json: Path
    out_bndb: Path
    out_sidecar: Path
    elapsed_s: float = 0.0
    targets: int = 0
    perfect: int = 0
    failed: int = 0
    cost_usd_total: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    saved_bndb: bool = False     # False if the bndb-save crashed
    return_code: int = 0


@dataclass
class StageSpec:
    name: str
    out_bndb_suffix: str
    run_one: Callable[..., Awaitable]
    format_done: Callable[[object], str]
    add_extra_args: Callable[[argparse.ArgumentParser], None] | None = None
    description: str = ""
    default_model: str = "opus"
    # Per-stage default max-turns ceiling. Flower benefits from a
    # lower cap because opus tends to ramble past the limit and dump
    # 50k+ output tokens trying to wrap up - that's the SDK's "Command
    # failed with exit code 1" path.
    default_max_turns: int = 16
    # Recoveries-sidecar namespace(s) this stage may write to. Empty
    # set = read-only access to the sidecar. None = unrestricted (back-
    # compat). Lower stages (warper / marinator) keep this empty so
    # they can't clobber the rust memories signer / flower own.
    write_namespaces: set[str] = field(default_factory=set)
    extra_run_kwargs: Callable[[argparse.Namespace], dict] = \
        field(default_factory=lambda: (lambda args: {}))


def default_argparser(spec: StageSpec) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=spec.description or f"Run {spec.name}.")
    p.add_argument("bndb", help="Path to a .bndb file (loaded read-only).")
    add_target_args(p)
    p.add_argument("--workers", "-w", type=int, default=4,
                   help=f"Parallel {spec.name} agents")
    p.add_argument("--model", "-m", default=spec.default_model,
                   help=f"LLM model (default: {spec.default_model})")
    p.add_argument("--max-turns", type=int, default=spec.default_max_turns,
                   help=f"Max turns per agent (default {spec.default_max_turns})")
    p.add_argument("--timeout", type=int, default=None,
                   help="Per-fn wall-clock budget in seconds.")
    p.add_argument("--output", "-o", default=f"outputs/{spec.name}",
                   help=f"Output directory (logs + JSON summary + saved bndb)")
    p.add_argument("--out-bndb",
                   help=f"Path for the saved bndb. Default: "
                        f"<output>/<input-stem>{spec.out_bndb_suffix}")
    p.add_argument("--dry-run", action="store_true", help="List targets only")
    p.add_argument("--verbose", action="store_true",
                   help="Stream per-tool log lines from each agent")
    p.add_argument("--clear", action="store_true",
                   help=f"Delete the cross-stage sidecar "
                        f"(<bndb-stem>.patina.json) before running so prior "
                        f"runs don't leak into this one. Stage starts with a "
                        f"clean slate.")
    if spec.add_extra_args:
        spec.add_extra_args(p)
    return p


async def run_stage(spec: StageSpec, args: argparse.Namespace) -> StageArtifacts:
    """The actual orchestration. CLI + chained-call paths share this."""
    name = spec.name
    bndb = Path(args.bndb).resolve()
    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_bndb = Path(args.out_bndb).resolve() if args.out_bndb else (
        out_dir.resolve() / f"{bndb.stem}{spec.out_bndb_suffix}"
    )
    artifacts = StageArtifacts(
        name=name,
        out_dir=out_dir,
        out_json=out_dir / f"{name}.json",
        out_bndb=out_bndb,
        out_sidecar=Recoveries.for_bndb(bndb).path,
    )
    if not bndb.exists():
        sys.stderr.write(f"bndb not found: {bndb}\n")
        artifacts.return_code = 2
        return artifacts
    if out_bndb == bndb:
        sys.stderr.write(f"[{name}] refusing to overwrite the input bndb\n")
        artifacts.return_code = 2
        return artifacts
    out_bndb.parent.mkdir(parents=True, exist_ok=True)
    if getattr(args, "clear", False):
        sidecar_path = Recoveries.for_bndb(bndb).path
        if sidecar_path.exists():
            sidecar_path.unlink()
            print(f"[{name}] cleared sidecar: {sidecar_path}", flush=True)

    # NB: do NOT call `bn._init_plugins()` here. It sets binja's
    # IS_MAIN_THREAD_INITED flag (c6ea260 in libbinaryninjacore) AND
    # registers a main_thread_id. Once the flag is set,
    # BNExecuteOnMainThreadAndWait posts work to a queue and waits
    # on a cv unless `pthread_self() == main_thread_id`. Anemone's
    # Rust-side `binaryninja::headless::init()` (called lazily on
    # first anemone.analyze) and asyncio shuffle threads enough that
    # the registered main thread isn't always the one calling
    # bv.create_database, deadlocking on `cv.wait` with no runner.
    # Leaving the flag at 0 keeps BNExecuteOnMainThreadAndWait on
    # the inline shortcut, which is what we want headless.
    print(f"[{name}] loading {bndb}", flush=True)
    bv = bn.load(str(bndb))
    if bv is None:
        sys.stderr.write(f"bn.load failed: {bndb}\n")
        artifacts.return_code = 2
        return artifacts

    try:
        targets = resolve_targets(
            bv, args,
            log_err=lambda m: sys.stderr.write(
                m.replace("[targets]", f"[{name}]") + "\n"),
        )
        if not targets:
            sys.stderr.write(f"[{name}] no targets resolved\n")
            artifacts.return_code = 2
            return artifacts
        log_path = out_dir / f"{name}.log"
        log_fh = log_path.open("w")
        lk = Lock()

        def log(msg: str) -> None:
            with lk:
                log_fh.write(msg + "\n"); log_fh.flush()
                print(msg, flush=True)

        log(f"[{name}] {len(targets)} targets, workers={args.workers}, "
            f"model={args.model}")
        if args.dry_run:
            for fn_name, addr in targets:
                log(f"  - {addr:#x}\t{fn_name}")
            return artifacts

        recoveries = Recoveries(
            Recoveries.for_bndb(bndb).path,
            write_namespaces=spec.write_namespaces,
        )
        ctx = TargetCtx(bv=bv, fn_addr=targets[0][1], recoveries=recoveries)
        run_kwargs = spec.extra_run_kwargs(args)

        async def work(fn_name: str, addr: int):
            log(f"[start] {fn_name} @ {addr:#x}")
            rec = await spec.run_one(
                bv=bv, ctx=ctx, name=fn_name, addr=addr,
                model=args.model, max_turns=args.max_turns,
                timeout_s=args.timeout, trace=args.verbose, log=log,
                **run_kwargs,
            )
            if rec.error:
                log(f"[done ] {fn_name}  ERROR: {rec.error}  ({rec.elapsed_s}s)")
            else:
                log(spec.format_done(rec))
                if rec.transport_error:
                    log(f"        warning (post-stream): {rec.transport_error}")
            return rec

        t0 = time.time()
        results = await run_targets_gated(
            bv, targets, work, workers=args.workers, log=log,
        )
        artifacts.elapsed_s = round(time.time() - t0, 1)
        artifacts.targets = len(results)
        artifacts.perfect = sum(1 for r in results if getattr(r, "final_perfect", False))
        artifacts.failed = sum(
            1 for r in results
            if getattr(r, "budget_exhausted", False) and not getattr(r, "final_perfect", False)
        )
        total_in = sum(r.input_tokens for r in results)
        total_out = sum(r.output_tokens for r in results)
        artifacts.tokens_in = total_in
        artifacts.tokens_out = total_out
        artifacts.cost_usd_total = round(sum(r.cost_usd for r in results), 4)

        from cli import transcript_path as _transcript_for_id
        results_dicts = []
        for r in results:
            d = asdict(r)
            tp = _transcript_for_id(getattr(r, "session_id", ""))
            d["transcript"] = str(tp) if tp else ""
            results_dicts.append(d)
        summary = {
            "elapsed_s": artifacts.elapsed_s,
            "targets": artifacts.targets,
            "perfect": artifacts.perfect,
            "failed": artifacts.failed,
            "tokens": [total_in, total_out],
            "cost_usd_total": artifacts.cost_usd_total,
            "results": results_dicts,
        }
        artifacts.out_json.write_text(json.dumps(summary, indent=2))
        # Cross-stage sidecar BEFORE the bndb save (the latter has a
        # known crash mode at teardown - sidecar is the durable store).
        try:
            ctx.recoveries.save()
            log(f"[{name}] sidecar -> {ctx.recoveries.path}")
            artifacts.out_sidecar = ctx.recoveries.path
        except Exception as e:
            log(f"[{name}] sidecar save failed: {e}")
        # Pre-save diagnostics: dump live threads / child procs / fds.
        # Default off now that the BNCreateDatabase deadlock is fixed
        # via `bn._init_plugins()` after load. Set
        # `PATINA_SAVE_DIAGNOSTICS=1` to re-enable if save misbehaves.
        if os.environ.get("PATINA_SAVE_DIAGNOSTICS", "0") == "1":
            _dump_save_diagnostics(name, log)
        # Arm faulthandler dumping to a *file* via an unbuffered fd:
        # python's text-mode buffering swallows the dump on SIGSEGV
        # abort, so we need a raw OS fd. Also write the same trace to
        # stderr (free from buffering since faulthandler uses write(2)).
        import faulthandler
        fault_path = out_dir / f"{name}.fault"
        try:
            # O_WRONLY|O_CREAT|O_TRUNC, mode 0644, fully unbuffered
            # (faulthandler talks to the kernel via write(2), no
            # Python-level buffer in the path).
            fault_fd = os.open(str(fault_path),
                               os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
            faulthandler.enable(file=fault_fd, all_threads=True)
            # Also dump to stderr; this is harmless (just a duplicate)
            # and saves us if the file write somehow fails.
            faulthandler.register(signal.SIGUSR1, file=fault_fd,
                                  all_threads=True, chain=False)
            log(f"[{name}] faulthandler armed -> {fault_path} (fd={fault_fd}); "
                f"send SIGUSR1 to pid {os.getpid()} for live dump")
        except Exception as e:
            log(f"[{name}] faulthandler arm failed: {e}")
        # CRITICAL: drain binja's background analysis queue BEFORE save.
        # Each agent's submit handler did `with bv.undoable_transaction():
        # fn.comment = ...; fn.name = ...` (signer also does `f.reanalyze`),
        # which schedules work on binja's C++ analysis worker pool (the
        # ~25 OS threads /proc/self/task shows past the Python-visible
        # ones). Calling create_database while those workers are mid-
        # update on the same bv races their internal state and
        # segfaults the process. update_analysis_and_wait blocks until
        # the queue is empty; from there create_database is safe.
        t_drain = time.time()
        bv.update_analysis_and_wait()
        # Force GC so any dangling per-fn closures (anemone FlowGraphs
        # cached inside the agent's tool registry) are released before
        # save. Pyo3 destructors that touch the BV need to run while
        # the bv is still in a quiescent state, not racing the save.
        import gc
        gc.collect()
        # Drain a second time in case GC's pyo3 destructors poked the bv.
        bv.update_analysis_and_wait()
        log(f"[{name}] analysis drained in {time.time()-t_drain:.1f}s")
        # Unconditional pre-save thread snapshot. Even if libbinaryninjacore
        # installs its own SIGSEGV handler that overrides faulthandler
        # (the most likely reason the post-crash dump file stays empty),
        # this dump fires successfully BEFORE create_database so we at
        # least know what every Python-visible thread was doing at the
        # moment we attempted the save.
        try:
            import faulthandler as _fh
            _fh.dump_traceback(file=fault_fd, all_threads=True)
            os.write(fault_fd, b"\n--- pre-save snapshot complete ---\n")
        except Exception as e:
            log(f"[{name}] pre-save dump failed: {e}")
        if os.environ.get("PATINA_SKIP_BNDB_SAVE") == "1":
            log(f"[{name}] PATINA_SKIP_BNDB_SAVE=1 set; skipping create_database "
                f"(sidecar at {ctx.recoveries.path} already has every recovery)")
        else:
            try:
                t_save = time.time()
                ok = bool(bv.create_database(str(out_bndb)))
                artifacts.saved_bndb = ok
                log(f"[{name}] {'saved' if ok else 'save returned False'} -> {out_bndb} "
                    f"({time.time()-t_save:.1f}s)")
            except Exception as e:
                log(f"[{name}] save failed: {type(e).__name__}: {e}")
        log(f"[{name}] done in {artifacts.elapsed_s:.1f}s, "
            f"{artifacts.perfect}/{artifacts.targets} perfect, "
            f"{artifacts.failed} failed, "
            f"{total_in + total_out:,} tokens, ${artifacts.cost_usd_total:.3f}, "
            f"output {artifacts.out_json}")
        artifacts.return_code = 0
        return artifacts
    finally:
        try:
            bv.file.close()
        except Exception:
            pass


def _dump_save_diagnostics(name: str, log) -> None:
    """Pre-save snapshot: live threads, child processes, open fds.
    Helps bisect why `bv.create_database` hangs - the typical cause is
    SDK / subprocess state lingering past the per-fn agent runs."""
    import threading
    threads = threading.enumerate()
    log(f"[{name}] diag: {len(threads)} live threads:")
    for t in threads:
        log(f"  - {t.name!r} daemon={t.daemon} alive={t.is_alive()}")
    try:
        children = list(Path("/proc/self/task").iterdir())
        log(f"[{name}] diag: /proc/self/task has {len(children)} entries")
    except Exception:
        pass
    try:
        proc_dir = Path(f"/proc/{os.getpid()}")
        # Direct child processes (zombies + alive)
        kids: list[tuple[int, str, str]] = []
        for d in Path("/proc").iterdir():
            if not d.name.isdigit(): continue
            try:
                stat = (d / "stat").read_text()
                # stat: pid (comm) state ppid ...
                rp = stat.find(")")
                ppid = int(stat[rp+2:].split()[1])
                if ppid == os.getpid():
                    state = stat[rp+2:].split()[0]
                    comm = stat[stat.find("(")+1:rp]
                    kids.append((int(d.name), state, comm))
            except Exception:
                pass
        log(f"[{name}] diag: {len(kids)} child processes:")
        for pid, state, comm in kids[:30]:
            log(f"  - pid={pid} state={state} comm={comm!r}")
        # Open fds
        fds = list((proc_dir / "fd").iterdir())
        log(f"[{name}] diag: {len(fds)} open fds")
        # Show non-trivial ones (skip stdin/stdout/stderr, dev/null, library mmaps).
        odd = []
        for f in fds:
            try:
                target = os.readlink(str(f))
                if target.startswith(("pipe:", "socket:", "anon_inode:")):
                    odd.append((f.name, target))
            except OSError:
                pass
        log(f"[{name}] diag: {len(odd)} pipe/socket/anon_inode fds:")
        for n, t in odd[:25]:
            log(f"  - fd={n} -> {t}")
    except Exception as e:
        log(f"[{name}] diag failed: {type(e).__name__}: {e}")


def run_cli(spec: StageSpec) -> int:
    """Convenience for the agent's `__main__` path. Exits via os._exit
    so binja + anemone destructors don't run during Python shutdown -
    the documented segfault path on multi-worker runs."""
    args = default_argparser(spec).parse_args()
    rc = asyncio.run(run_stage(spec, args)).return_code
    sys.stdout.flush(); sys.stderr.flush()
    os._exit(rc)


def run(spec: StageSpec, bndb: str | Path, **kwargs) -> StageArtifacts:
    """Programmatic entry: chain stages without building argparse strings.
    `kwargs` overrides any default_argparser default - typical use:
        run(SPEC, "/path/x.bndb", output="outputs/sig", workers=4, addresses=["0x..."])
    """
    parser = default_argparser(spec)
    base = parser.parse_args([str(bndb)])
    for k, v in kwargs.items():
        if v is None:
            continue
        if not hasattr(base, k):
            raise ValueError(f"unknown arg for {spec.name}: {k}")
        setattr(base, k, v)
    return asyncio.run(run_stage(spec, base))
