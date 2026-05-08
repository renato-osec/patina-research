#!/usr/bin/env python3
# Sequentially run marinator -> signer -> flower (or any subset) on
# the same target set. Each stage is a function: takes the previous
# stage's saved bndb (or the input if no prior bndb) and produces a
# new bndb + JSON summary + the cross-stage `<bndb>.patina.json`
# sidecar. The sidecar accumulates per-stage findings keyed by addr,
# so a later stage's tools (`prior_metadata`, `signer_types`, etc.)
# automatically see whatever earlier stages recovered.
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parent / "common")]
os.environ.setdefault("BN_DISABLE_USER_PLUGINS", "1")

from stage import StageArtifacts


# Color stage-prefixed log lines so a chained run is easy to scan:
# warper green, marinator yellow, signer cyan, flower orange (256-color).
# Set NO_COLOR=1 (or pipe to a non-tty) to disable.
_STAGE_COLOR = {
    "[warper]":    "\x1b[32m",
    "[marinator]": "\x1b[33m",
    "[signer]":    "\x1b[36m",
    "[flower]":    "\x1b[38;5;208m",
    "[chain]":     "\x1b[38;5;245m",
}
_RESET = "\x1b[0m"


class _ColorStdout:
    """Wrap a TextIO; line-buffered, colors any line whose first non-
    whitespace token matches a known [stage] prefix."""
    def __init__(self, real):
        self._real = real
        self._buf = ""
    def write(self, s):
        if not s:
            return 0
        self._buf += s
        out = []
        while True:
            nl = self._buf.find("\n")
            if nl < 0:
                break
            line, self._buf = self._buf[:nl], self._buf[nl+1:]
            out.append(self._colorize(line) + "\n")
        if out:
            self._real.write("".join(out))
        return len(s)
    def _colorize(self, line):
        s = line.lstrip()
        for prefix, color in _STAGE_COLOR.items():
            if s.startswith(prefix):
                return color + line + _RESET
        return line
    def flush(self):
        if self._buf:
            self._real.write(self._buf)
            self._buf = ""
        self._real.flush()
    def __getattr__(self, name):
        return getattr(self._real, name)


if os.environ.get("NO_COLOR") != "1" and sys.stdout.isatty():
    sys.stdout = _ColorStdout(sys.stdout)


# Lazy imports so each stage's heavy deps (binja, lymph, anemone) only
# load when actually used.
_STAGE_DIRS = {Path(__file__).resolve().parent / s
               for s in ("warper", "marinator", "signer", "flower")}


def _purge_stage_modules() -> None:
    """Drop any sys.modules entry whose __file__ lives inside a stage
    dir. Each stage has its own `submit.py` / `write.py` / etc. that
    all collide on the top-level `submit` / `write` module name; if
    we let the cache linger, the next stage's `import submit` returns
    the prior stage's module and crashes on signature mismatch."""
    for n, m in list(sys.modules.items()):
        f = getattr(m, "__file__", None) or ""
        if any(str(d) in f for d in _STAGE_DIRS):
            del sys.modules[n]


def _load_stage(name: str):
    # Load each stage's pipeline.py as a *file* via importlib so the
    # agent-private sys.path setup at the top of each pipeline.py
    # works the same way it does standalone. Purge any cached
    # stage-local modules first so submit.py / write.py / etc. from
    # the prior stage don't shadow this one's.
    _purge_stage_modules()
    import importlib.util
    path = Path(__file__).resolve().parent / name / "pipeline.py"
    if not path.exists():
        raise ValueError(f"unknown stage: {name} (no {path})")
    mod_name = f"_stage_{name}"
    spec = importlib.util.spec_from_file_location(mod_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"could not load {path}")
    mod = importlib.util.module_from_spec(spec)
    # Register in sys.modules BEFORE exec — `@dataclass` machinery
    # looks up `cls.__module__` there and crashes with `NoneType has
    # no attribute '__dict__'` otherwise.
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod.SPEC, mod.run_stage


_DEFAULT_ORDER = ["warper", "marinator", "signer", "flower"]


def chain(
    bndb: str | Path,
    *,
    output_dir: str | Path = "outputs/chain",
    stages: list[str] | None = None,
    addresses: list[str] | None = None,
    depth: int | None = None,
    workers: int = 4,
    model: str | None = None,
    max_turns: int = 16,
    timeout: int | None = None,
    submit_rounds: int = 3,
    prelude_file: str | None = None,
    verbose: bool = False,
    stop_on_failure: bool = False,
    clear: bool = False,
) -> list[StageArtifacts]:
    """Run each stage in turn, threading bndb output -> next input.
    `model=None` lets each stage use its own default (marinator=sonnet,
    signer/flower=opus). Pass an explicit value to override every stage.
    `clear=True` deletes the input bndb's sidecar before the chain runs
    so stale prior-run data doesn't leak into stage 0; subsequent stages
    keep accumulating into the freshly-cleared file."""
    stages = stages or _DEFAULT_ORDER
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    cur_bndb = Path(bndb).resolve()
    if clear:
        from recoveries import Recoveries
        sidecar = Recoveries.for_bndb(cur_bndb).path
        if sidecar.exists():
            sidecar.unlink()
            print(f"[chain] cleared sidecar: {sidecar}", flush=True)
    artifacts: list[StageArtifacts] = []

    common_kwargs = dict(
        addresses=addresses, depth=depth, workers=workers, model=model,
        max_turns=max_turns, timeout=timeout, verbose=verbose,
    )
    # Strip None so default_argparser keeps its defaults.
    common_kwargs = {k: v for k, v in common_kwargs.items() if v is not None}

    for stage_name in stages:
        _, run_stage = _load_stage(stage_name)
        kwargs = dict(common_kwargs)
        kwargs["output"] = str(out_dir / stage_name)
        if stage_name in ("signer", "flower"):
            kwargs["submit_rounds"] = submit_rounds
            if prelude_file:
                kwargs["prelude_file"] = prelude_file
        a = run_stage(cur_bndb, **kwargs)
        artifacts.append(a)
        if a.return_code != 0:
            print(f"[chain] {stage_name} returned {a.return_code}; aborting",
                  flush=True)
            break
        if stop_on_failure and a.failed:
            print(f"[chain] {stage_name} had {a.failed} failures; "
                  f"--stop-on-failure", flush=True)
            break
        # Next stage reads from this stage's saved bndb if it landed,
        # otherwise stays on the original input (sidecar still
        # propagates findings either way).
        if a.saved_bndb and a.out_bndb.exists():
            cur_bndb = a.out_bndb
    return artifacts


def main() -> int:
    p = argparse.ArgumentParser(
        description="Run marinator/signer/flower sequentially on one bndb.")
    p.add_argument("bndb", help="Path to a .bndb file.")
    p.add_argument("--stages", default=",".join(_DEFAULT_ORDER),
                   help="Comma-separated stage names; default: "
                        + ",".join(_DEFAULT_ORDER))
    p.add_argument("--addresses",
                   help="Comma-separated function addresses (`0x...`)")
    p.add_argument("--depth", type=int)
    p.add_argument("--output", "-o", default="outputs/chain",
                   help="Per-stage outputs go to <output>/<stage>/")
    p.add_argument("--workers", "-w", type=int, default=4)
    p.add_argument("--model", "-m", default=None,
                   help="Override every stage's model. Default: each "
                        "stage's own (marinator=sonnet, signer/flower=opus)")
    p.add_argument("--max-turns", type=int, default=16)
    p.add_argument("--submit-rounds", type=int, default=3)
    p.add_argument("--timeout", type=int, default=None)
    p.add_argument("--prelude-file")
    p.add_argument("--verbose", action="store_true")
    p.add_argument("--stop-on-failure", action="store_true",
                   help="Abort the chain at the first stage with failures")
    p.add_argument("--clear", action="store_true",
                   help="Delete the input bndb's `<stem>.patina.json` sidecar "
                        "before running so prior-run data doesn't leak in. "
                        "Stages still accumulate into the file as the chain "
                        "progresses.")
    args = p.parse_args()

    addresses = args.addresses.split(",") if args.addresses else None
    artifacts = chain(
        args.bndb,
        output_dir=args.output,
        stages=[s.strip() for s in args.stages.split(",") if s.strip()],
        addresses=addresses,
        depth=args.depth,
        workers=args.workers,
        model=args.model,
        max_turns=args.max_turns,
        submit_rounds=args.submit_rounds,
        timeout=args.timeout,
        prelude_file=args.prelude_file,
        verbose=args.verbose,
        stop_on_failure=args.stop_on_failure,
        clear=args.clear,
    )
    rc = artifacts[-1].return_code if artifacts else 2
    return rc


if __name__ == "__main__":
    rc = main()
    sys.stdout.flush(); sys.stderr.flush()
    # os._exit so binja/anemone destructors don't run during Python
    # shutdown - same reason run_cli does it (multi-worker shutdown
    # races segfault the interpreter post-success).
    os._exit(rc)
