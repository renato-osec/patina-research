# Shared pipeline orchestration; per-agent specialization via StageSpec.
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
    default_max_turns: int = 16  # flower lower; opus ramble = sdk exit-1.
    # Sidecar namespaces this stage may write. Empty = read-only.
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
    p.add_argument("--context-dir",
                   help="Project source dir the agent can grep/read for "
                        "Rust analogues (e.g. /home/renny/hl).")
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

    # Don't call bn._init_plugins() — it makes BNExecuteOnMainThread-
    # AndWait queue work to a thread the inline path expects to be
    # the caller, deadlocking with anemone's headless runner.
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
                context_dir=getattr(args, "context_dir", None),
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
        # Save the sidecar before the bndb (sidecar is the durable store).
        try:
            ctx.recoveries.save()
            log(f"[{name}] sidecar -> {ctx.recoveries.path}")
            artifacts.out_sidecar = ctx.recoveries.path
        except Exception as e:
            log(f"[{name}] sidecar save failed: {e}")
        if os.environ.get("PATINA_SAVE_DIAGNOSTICS", "0") == "1":
            _dump_save_diagnostics(name, log)
        # faulthandler must use a raw fd; Python buffering swallows
        # the dump on abort.
        import faulthandler
        fault_path = out_dir / f"{name}.fault"
        try:
            fault_fd = os.open(str(fault_path),
                               os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
            faulthandler.enable(file=fault_fd, all_threads=True)
            faulthandler.register(signal.SIGUSR1, file=fault_fd,
                                  all_threads=True, chain=False)
            log(f"[{name}] faulthandler armed -> {fault_path} (fd={fault_fd})")
        except Exception as e:
            log(f"[{name}] faulthandler arm failed: {e}")
        # Drain binja's analysis queue + drop pyo3 closures so
        # create_database doesn't race their destructors.
        t_drain = time.time()
        bv.update_analysis_and_wait()
        import gc; gc.collect()
        bv.update_analysis_and_wait()
        log(f"[{name}] analysis drained in {time.time()-t_drain:.1f}s")
        try:
            faulthandler.dump_traceback(file=fault_fd, all_threads=True)
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
    """Pre-save snapshot: live threads, child processes, open fds."""
    import threading
    threads = threading.enumerate()
    log(f"[{name}] diag: {len(threads)} live threads:")
    for t in threads:
        log(f"  - {t.name!r} daemon={t.daemon} alive={t.is_alive()}")
    try:
        log(f"[{name}] diag: /proc/self/task has "
            f"{len(list(Path('/proc/self/task').iterdir()))} entries")
    except Exception:
        pass
    try:
        proc_dir = Path(f"/proc/{os.getpid()}")
        kids: list[tuple[int, str, str]] = []
        for d in Path("/proc").iterdir():
            if not d.name.isdigit():
                continue
            try:
                stat = (d / "stat").read_text()
                rp = stat.find(")")
                ppid = int(stat[rp+2:].split()[1])
                if ppid == os.getpid():
                    kids.append((int(d.name),
                                 stat[rp+2:].split()[0],
                                 stat[stat.find("(")+1:rp]))
            except Exception:
                pass
        log(f"[{name}] diag: {len(kids)} child processes:")
        for pid, state, comm in kids[:30]:
            log(f"  - pid={pid} state={state} comm={comm!r}")
        fds = list((proc_dir / "fd").iterdir())
        odd = []
        for f in fds:
            try:
                target = os.readlink(str(f))
                if target.startswith(("pipe:", "socket:", "anon_inode:")):
                    odd.append((f.name, target))
            except OSError:
                pass
        log(f"[{name}] diag: {len(fds)} fds, {len(odd)} pipe/socket/anon_inode:")
        for n, t in odd[:25]:
            log(f"  - fd={n} -> {t}")
    except Exception as e:
        log(f"[{name}] diag failed: {type(e).__name__}: {e}")


def run_cli(spec: StageSpec) -> int:
    """`os._exit` skips binja+anemone destructors that segfault on shutdown."""
    args = default_argparser(spec).parse_args()
    rc = asyncio.run(run_stage(spec, args)).return_code
    sys.stdout.flush(); sys.stderr.flush()
    os._exit(rc)


def run(spec: StageSpec, bndb: str | Path, **kwargs) -> StageArtifacts:
    """Programmatic entry; kwargs override default_argparser defaults."""
    parser = default_argparser(spec)
    base = parser.parse_args([str(bndb)])
    for k, v in kwargs.items():
        if v is None:
            continue
        if not hasattr(base, k):
            raise ValueError(f"unknown arg for {spec.name}: {k}")
        setattr(base, k, v)
    return asyncio.run(run_stage(spec, base))
