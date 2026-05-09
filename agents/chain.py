#!/usr/bin/env python3
# Sequentially run marinator -> signer -> flower on one bndb; sidecar
# accumulates per-stage findings keyed by fn addr.
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parent / "common")]
os.environ.setdefault("BN_DISABLE_USER_PLUGINS", "1")

from stage import StageArtifacts


# NO_COLOR=1 or non-tty disables.
_STAGE_COLOR = {
    "warper":    "\x1b[32m",
    "marinator": "\x1b[33m",
    "signer":    "\x1b[36m",
    "flower":    "\x1b[38;5;208m",
    "chain":     "\x1b[38;5;245m",
}
_RESET = "\x1b[0m"
_active_stage_color: str | None = None


class _ColorStdout:
    """Line-buffered stdout wrapper; colors `[stage]`/`[token]` prefixes."""
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
        i = 0
        while i < len(line) and line[i] in " \t":
            i += 1
        if i >= len(line) or line[i] != "[":
            return line
        end = line.find("]", i)
        if end < 0:
            return line
        token = line[i+1:end]
        c = _STAGE_COLOR.get(token)
        if c is None:
            c = _active_stage_color
        if not c:
            return line
        return line[:i] + c + line[i:end+1] + _RESET + line[end+1:]
    def flush(self):
        if self._buf:
            self._real.write(self._buf)
            self._buf = ""
        self._real.flush()
    def __getattr__(self, name):
        return getattr(self._real, name)


if os.environ.get("NO_COLOR") != "1" and sys.stdout.isatty():
    sys.stdout = _ColorStdout(sys.stdout)


_STAGE_DIRS = {Path(__file__).resolve().parent / s
               for s in ("warper", "marinator", "signer", "flower")}


def _purge_stage_modules() -> None:
    """Drop cached stage-local modules. Each stage has its own
    submit.py/write.py that collide on the top-level module name."""
    for n, m in list(sys.modules.items()):
        f = getattr(m, "__file__", None) or ""
        if any(str(d) in f for d in _STAGE_DIRS):
            del sys.modules[n]


def _load_stage(name: str):
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
    # Register before exec so @dataclass `cls.__module__` lookup works.
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
    """Run each stage in turn; bndb output -> next input. `model=None`
    keeps each stage's own default. `clear=True` deletes the sidecar."""
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
        global _active_stage_color
        _active_stage_color = _STAGE_COLOR.get(stage_name)
        try:
            a = run_stage(cur_bndb, **kwargs)
        finally:
            _active_stage_color = None
        artifacts.append(a)
        if a.return_code != 0:
            print(f"[chain] {stage_name} returned {a.return_code}; aborting",
                  flush=True)
            break
        if stop_on_failure and a.failed:
            print(f"[chain] {stage_name} had {a.failed} failures; "
                  f"--stop-on-failure", flush=True)
            break
        # Carry sidecar forward so the next stage sees prior findings.
        if a.saved_bndb and a.out_bndb.exists():
            from recoveries import Recoveries
            src_sc = Path(a.out_sidecar) if a.out_sidecar else None
            dst_sc = Recoveries.for_bndb(a.out_bndb).path
            # is_file(): warper leaves out_sidecar="", which exists()
            # returns True for (resolves to cwd) and copy2 chokes on.
            if src_sc and src_sc.is_file() and src_sc.resolve() != dst_sc.resolve():
                import shutil
                dst_sc.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_sc, dst_sc)
            cur_bndb = a.out_bndb
    _print_totals(artifacts)
    return artifacts


def _print_totals(artifacts: list[StageArtifacts]) -> None:
    """One-line summary across every stage that ran."""
    if not artifacts:
        return
    cost = sum(a.cost_usd_total for a in artifacts)
    tin = sum(a.tokens_in for a in artifacts)
    tout = sum(a.tokens_out for a in artifacts)
    elapsed = sum(a.elapsed_s for a in artifacts)
    perfect = sum(a.perfect for a in artifacts)
    targets = sum(a.targets for a in artifacts)
    parts = [f"{a.name}=${a.cost_usd_total:.3f}" for a in artifacts if a.cost_usd_total]
    by_stage = (" (" + ", ".join(parts) + ")") if parts else ""
    print(f"[chain] total: ${cost:.3f}{by_stage}  "
          f"tokens={tin:,}+{tout:,}  "
          f"perfect={perfect}/{targets}  elapsed={elapsed:.1f}s",
          flush=True)


def main() -> int:
    p = argparse.ArgumentParser(
        description="Run marinator/signer/flower sequentially on one bndb.")
    p.add_argument("bndb", help="Path to a .bndb file.")
    p.add_argument("--stages", default=",".join(_DEFAULT_ORDER),
                   help="Comma-separated stage names; default: "
                        + ",".join(_DEFAULT_ORDER))
    p.add_argument("--addresses", nargs="*", default=[],
                   help="Function addresses; space- or comma-separated.")
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
                   help="Delete the input sidecar before running.")
    args = p.parse_args()

    addrs: list[str] = []
    for a in (args.addresses or []):
        addrs.extend(p for p in str(a).split(",") if p.strip())
    addresses: list[str] | None = addrs or None
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
    # os._exit: skip binja/anemone destructors that segfault on shutdown.
    os._exit(rc)
