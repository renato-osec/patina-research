#!/usr/bin/env python3
# Thin wrapper over agents/common/pipeline.py - flower's per-fn fn +
# log line are the only flower-specific bits.
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parent),
                str(Path(__file__).resolve().parent.parent / "common")]
os.environ.setdefault("BN_DISABLE_USER_PLUGINS", "1")

from stage import StageSpec, run, run_cli  # common.pipeline
from flower import sign_function


def _add_args(p):
    p.add_argument("--prelude-file",
                   help="Path to a .rs file passed as nacre prelude on every check")
    p.add_argument("--submit-rounds", type=int, default=3,
                   help="Max times the harness re-prompts the agent after "
                        "a non-perfect submit (default 3)")


def _extra_run_kwargs(args):
    prelude: str | None = None
    if args.prelude_file:
        prelude = Path(args.prelude_file).read_text(encoding="utf-8")
    return {"prelude": prelude, "submit_rounds": args.submit_rounds}


async def _run_one(*, bv, ctx, name, addr, model, max_turns, timeout_s,
                   trace, log, prelude, submit_rounds):
    return await sign_function(
        bv, addr,
        prelude=prelude, model=model, max_turns=max_turns,
        submit_rounds=submit_rounds, timeout_s=timeout_s,
        shared_ctx=ctx, trace=trace,
    )


def _format_done(rec):
    tag = "FAILED" if (rec.budget_exhausted and not rec.final_perfect) else (
        "OK" if rec.final_perfect else "imperfect")
    src = (rec.submitted_source or "")[:120]
    return (f"[done ] {rec.name}  [{tag}]  source={src!r}  "
            f"final_score={rec.final_score:.2f}  perfect={rec.final_perfect}  "
            f"exhausted={rec.budget_exhausted}  "
            f"submits={rec.submit_attempts}  tools={rec.tool_calls} "
            f"iters={rec.iter_count} ${rec.cost_usd:.3f} t={rec.elapsed_s}s")


SPEC = StageSpec(
    name="flower",
    out_bndb_suffix=".flowered.bndb",
    description="Reconstruct Rust function bodies in parallel.",
    run_one=_run_one,
    format_done=_format_done,
    add_extra_args=_add_args,
    extra_run_kwargs=_extra_run_kwargs,
    write_namespaces={"flower"},
    # Tighter cap than other stages: opus on big fns ran past 16
    # turns last chain, dumped 50k+ tokens trying to wrap up, and
    # crashed the SDK CLI with exit code 1. 12 turns is enough for
    # any submission that's actually going to land; bigger is wasted.
    default_max_turns=12,
)


def run_stage(bndb, **kwargs):
    """Programmatic entry for the orchestrator."""
    return run(SPEC, bndb, **kwargs)


if __name__ == "__main__":
    sys.exit(run_cli(SPEC))
