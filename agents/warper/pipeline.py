#!/usr/bin/env python3
# Deterministic stage: register rust stdlib WARP signatures, apply
# every match, save the bndb. No LLM, no per-fn agent loop. Same
# StageArtifacts surface as the agent stages so chain.py can call it
# identically.
from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parent),
                str(Path(__file__).resolve().parent.parent / "common")]
os.environ.setdefault("BN_DISABLE_USER_PLUGINS", "1")

import binaryninja as bn
import os

from stage import StageArtifacts

dir_path = Path(__file__).resolve().parent
DEFAULT_SIGS = [
    dir_path / "sigs" / "rust-1.83.0-all.warp",
]


@dataclass
class _Spec:
    name: str = "warper"
    out_bndb_suffix: str = ".warped.bndb"


SPEC = _Spec()


def _resolve_sigs(extra: list[str] | None) -> list[Path]:
    paths: list[Path] = []
    for p in (extra or []) + DEFAULT_SIGS:
        if p:
            pp = Path(p)
            if pp.exists() and pp not in paths:
                paths.append(pp)
    return paths


def _apply_warp(bv, sigs: list[Path], log) -> tuple[int, int]:
    """Register sigs into a writable WarpContainer + apply matches.
    Returns (registered_count, applied_count)."""
    from binaryninja import warp
    container = next(
        (c for c in warp.WarpContainer.all() if c.is_source_writable),
        None,
    )
    if container is None:
        log("[warper] no writable WarpContainer; skipping")
        return 0, 0
    log(f"[warper] container={container.name!r}")
    registered = 0
    for s in sigs:
        try:
            ref = container.add_source(str(s))
            log(f"[warper] add_source {s.name} -> {ref}")
            registered += 1
        except Exception as e:
            log(f"[warper] add_source({s.name}) failed: {type(e).__name__}: {e}")
    bv.update_analysis_and_wait()
    applied = 0
    for f in bv.functions:
        try:
            wf = warp.WarpFunction.get_matched(f)
        except Exception:
            wf = None
        if wf is None:
            continue
        try:
            wf.apply(f)
            applied += 1
        except Exception as e:
            log(f"[warper] apply({f.name}@{f.start:#x}) failed: {e}")
    bv.update_analysis_and_wait()
    return registered, applied


def run_stage(bndb, **kwargs) -> StageArtifacts:
    """Programmatic entry. Recognized kwargs:
       output       output directory (default outputs/warper)
       out_bndb     override saved bndb path
       sigs         extra `.warp` source paths (in addition to DEFAULT_SIGS)
    All other kwargs are accepted + ignored so chain.py's shared
    kwargs surface (addresses, depth, workers, model, ...) flows
    through cleanly."""
    bndb = Path(bndb).resolve()
    output = Path(kwargs.get("output") or "outputs/warper")
    output.mkdir(parents=True, exist_ok=True)
    out_bndb = Path(kwargs["out_bndb"]).resolve() if kwargs.get("out_bndb") else (
        output.resolve() / f"{bndb.stem}{SPEC.out_bndb_suffix}"
    )
    sigs = _resolve_sigs(kwargs.get("sigs"))
    artifacts = StageArtifacts(
        name=SPEC.name,
        out_dir=output,
        out_json=output / f"{SPEC.name}.json",
        out_bndb=out_bndb,
        out_sidecar=Path(""),   # warper doesn't write to the patina sidecar
    )
    if not bndb.exists():
        sys.stderr.write(f"bndb not found: {bndb}\n")
        artifacts.return_code = 2
        return artifacts
    if out_bndb == bndb:
        sys.stderr.write(f"[{SPEC.name}] refusing to overwrite the input bndb\n")
        artifacts.return_code = 2
        return artifacts
    out_bndb.parent.mkdir(parents=True, exist_ok=True)

    log_fh = (output / f"{SPEC.name}.log").open("w")
    def log(msg: str) -> None:
        log_fh.write(msg + "\n"); log_fh.flush(); print(msg, flush=True)

    log(f"[warper] sigs: {[s.name for s in sigs] or '<none>'}")
    if not sigs:
        log("[warper] no sigs to register; skipping (just saves bndb)")
    log(f"[warper] loading {bndb}")
    bv = bn.load(str(bndb))
    if bv is None:
        sys.stderr.write(f"bn.load failed: {bndb}\n")
        artifacts.return_code = 2
        return artifacts
    try:
        bn._init_plugins()
        bv.update_analysis_and_wait()
        named_pre = sum(1 for f in bv.functions if not f.name.startswith("sub_"))
        total = sum(1 for _ in bv.functions)
        log(f"[warper] {named_pre}/{total} named pre-WARP")

        t0 = time.time()
        registered, applied = (0, 0)
        if sigs:
            registered, applied = _apply_warp(bv, sigs, log)
        artifacts.elapsed_s = round(time.time() - t0, 1)
        named_post = sum(1 for f in bv.functions if not f.name.startswith("sub_"))
        log(f"[warper] {named_post}/{total} named post-WARP "
            f"(applied {applied} match(es) from {registered} sig(s))")

        # Drop common bookkeeping into the JSON so chain.py + downstream
        # tooling can consume the same shape as the agent stages.
        import json
        (artifacts.out_json).write_text(json.dumps({
            "elapsed_s": artifacts.elapsed_s,
            "sigs": [str(s) for s in sigs],
            "registered": registered,
            "applied": applied,
            "named_pre": named_pre,
            "named_post": named_post,
            "total_fns": total,
        }, indent=2))

        try:
            bv.create_database(str(out_bndb))
            log(f"[warper] saved -> {out_bndb}")
            artifacts.saved_bndb = True
        except Exception as e:
            log(f"[warper] save failed: {e}")
        artifacts.targets = total
        artifacts.perfect = applied
        artifacts.return_code = 0
        return artifacts
    finally:
        try:
            bv.file.close()
        except Exception:
            pass
        log_fh.close()


def main() -> int:
    p = argparse.ArgumentParser(description="Apply WARP signatures (deterministic).")
    p.add_argument("bndb", help="Path to a binary or .bndb file.")
    p.add_argument("--output", "-o", default=f"outputs/{SPEC.name}",
                   help="Output directory for the saved bndb + JSON")
    p.add_argument("--out-bndb",
                   help=f"Saved bndb path. Default: <output>/<input-stem>"
                        f"{SPEC.out_bndb_suffix}")
    p.add_argument("--sig", action="append", default=[],
                   help=f"Extra `.warp` source path (repeatable). Defaults: "
                        + ", ".join(DEFAULT_SIGS))
    args = p.parse_args()
    a = run_stage(args.bndb, output=args.output, out_bndb=args.out_bndb, sigs=args.sig)
    return a.return_code


if __name__ == "__main__":
    sys.exit(main())
