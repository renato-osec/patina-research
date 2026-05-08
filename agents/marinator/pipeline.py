#!/usr/bin/env python3
# Thin wrapper over agents/common/stage.py - marinator's per-fn fn +
# log line are the only marinator-specific bits.
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parent),
                str(Path(__file__).resolve().parent.parent / "common")]
os.environ.setdefault("BN_DISABLE_USER_PLUGINS", "1")

from stage import StageSpec, run, run_cli  # common.stage
from marinator import marinate_function


async def _run_one(*, bv, ctx, name, addr, model, max_turns, timeout_s,
                   trace, log, **_unused):
    return await marinate_function(
        bv, addr, model=model,
        max_turns=max_turns if max_turns > 0 else None,
        quiet=not trace, shared_ctx=ctx,
    )


def _format_done(rec):
    tag = json.dumps(rec.summary or {})
    return (f"[done ] {rec.name}  {tag}  tools={rec.tool_calls} "
            f"iters={rec.iter_count} ${rec.cost_usd:.3f} t={rec.elapsed_s}s")


SPEC = StageSpec(
    name="marinator",
    out_bndb_suffix=".marinated.bndb",
    description="Marinate (rename + comment) Rust functions in parallel.",
    run_one=_run_one,
    format_done=_format_done,
    default_model="sonnet",   # cheap rename+comment work; opus is overkill
)


def run_stage(bndb, **kwargs):
    """Programmatic entry for the orchestrator."""
    return run(SPEC, bndb, **kwargs)


if __name__ == "__main__":
    sys.exit(run_cli(SPEC))
