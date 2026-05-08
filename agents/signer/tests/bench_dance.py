# A/B benchmark: does the "dance" (PreToolUse wide-tool gate +
# force-iterate-first-submit bounce) actually pay for itself? Runs N
# trials of the live agent on a small target set in two modes:
#
#   "dance"   — gate ON,  force-iterate ON   (current default)
#   "plain"   — gate OFF, force-iterate OFF  (vanilla agent)
#
# For each (mode, target, trial) we subprocess pipeline.py with the
# right env vars and parse its signer.json output. Aggregates: avg
# elapsed, tokens, $, correctness (final_perfect rate), # submits.
# Prints a comparison table at the end.
#
# Cost note: each run is one full agent invocation. With 3 trials × 2
# modes × N targets = 6N runs. A typical run is 30s-3min and
# $0.10-0.50 in API spend. Pick TARGETS conservatively.
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from statistics import mean, stdev


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent.parent
PIPELINE = HERE.parent / "pipeline.py"


# (label, binary path, fn name) — only un-inlined fns survive as
# discoverable symbols in stripped release builds, so the set is
# small. baby-rust::step is a self-recursive scalar fn (medium); the
# brainfuck-VM step is a String round-tripper. braintrust::jump is
# the non-trivial-receiver hard case (HashMap + Vec + Vec + bool
# struct).
DEFAULT_TARGETS = [
    ("baby-rust::step",  "bench/benchmark/2021-baby-rust/binary",
     "_RNvCsgI4UbySRN2Z_6source4step"),
    ("braintrust::jump", "bench/benchmark/2021-braintrust/binary.bndb",
     "_RNvMCsgI4UbySRN2Z_6sourceNtB2_5State4jump"),
]


MODES = {
    "dance": {"SIGNER_NO_GATE": "0", "SIGNER_NO_FORCE_ITERATE": "0"},
    "plain": {"SIGNER_NO_GATE": "1", "SIGNER_NO_FORCE_ITERATE": "1"},
}


def _run_one(target_label: str, bndb: Path, fn: str, mode: str,
             trial: int, out_dir: Path, *, max_turns: int,
             submit_rounds: int, model: str) -> dict | None:
    """Invoke pipeline.py once. Return parsed signer.json's per-target
    record, or None on harness failure."""
    env = {**os.environ, **MODES[mode]}
    run_dir = out_dir / mode / f"{target_label.replace('::','_')}_t{trial}"
    run_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        os.environ.get("VIRTUAL_ENV", "/home/renny/doc/work/research/patina/agents/venv")
        + "/bin/python3",
        str(PIPELINE), str(bndb), fn,
        "--model", model,
        "--max-turns", str(max_turns),
        "--submit-rounds", str(submit_rounds),
        "--output", str(run_dir),
        "--workers", "1",
        "--timeout", "1000",
    ]
    t0 = time.time()
    res = subprocess.run(cmd, capture_output=True, text=True, env=env,
                         cwd=str(ROOT), timeout=1080)
    wall = time.time() - t0
    summary_path = run_dir / "signer.json"
    if not summary_path.exists():
        sys.stderr.write(
            f"[bench] {mode} {target_label} t{trial}: no signer.json "
            f"(wall {wall:.1f}s, rc={res.returncode})\n"
            f"  stderr: {res.stderr[-400:]!r}\n"
        )
        return None
    summary = json.loads(summary_path.read_text())
    if not summary.get("results"):
        return None
    rec = summary["results"][0]
    rec["_wall_s"] = wall
    rec["_summary_elapsed_s"] = summary.get("elapsed_s", 0)
    return rec


def _agg(records: list[dict]) -> dict:
    """Aggregate across trials. None-records (harness fails) drop out."""
    valid = [r for r in records if r is not None]
    if not valid:
        return {"n": 0}
    def col(k, default=0):
        return [r.get(k, default) or default for r in valid]
    elapsed = col("_wall_s")
    in_tok = col("input_tokens")
    out_tok = col("output_tokens")
    cost = col("cost_usd")
    perfect = col("final_perfect")
    score = col("final_score")
    submits = col("submit_attempts")
    iters = col("iterations") or [r.get("iterations", 0) for r in valid]
    return {
        "n": len(valid),
        "perfect_rate": round(sum(1 for p in perfect if p) / len(valid), 3),
        "avg_score": round(mean(score), 3) if score else 0.0,
        "avg_elapsed_s": round(mean(elapsed), 1) if elapsed else 0.0,
        "stdev_elapsed_s": round(stdev(elapsed), 1) if len(elapsed) > 1 else 0.0,
        "avg_in_tokens": int(mean(in_tok)) if in_tok else 0,
        "avg_out_tokens": int(mean(out_tok)) if out_tok else 0,
        "avg_total_tokens": int(mean(in_tok) + mean(out_tok)) if in_tok else 0,
        "avg_cost_usd": round(mean(cost), 4) if cost else 0.0,
        "avg_submits": round(mean(submits), 2) if submits else 0,
        "avg_iters": round(mean(iters), 1) if iters else 0,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=3,
                    help="trials per (mode, target) cell")
    ap.add_argument("--targets", type=int, default=len(DEFAULT_TARGETS),
                    help="how many targets to use from DEFAULT_TARGETS")
    ap.add_argument("--max-turns", type=int, default=24)
    ap.add_argument("--submit-rounds", type=int, default=3)
    ap.add_argument("--model", default="sonnet")
    ap.add_argument("--out", default=None,
                    help="output dir for raw runs (default /tmp/bench_dance/<ts>)")
    ap.add_argument("--modes", default="dance,plain",
                    help="comma-separated modes from {dance,plain}")
    args = ap.parse_args()

    targets = DEFAULT_TARGETS[: args.targets]
    modes = [m.strip() for m in args.modes.split(",") if m.strip() in MODES]
    out_dir = Path(args.out) if args.out else Path(f"/tmp/bench_dance/{int(time.time())}")
    out_dir.mkdir(parents=True, exist_ok=True)

    plan = [
        (mode, label, bndb, fn, trial)
        for mode in modes
        for (label, bndb, fn) in targets
        for trial in range(1, args.trials + 1)
    ]
    print(f"[bench] {len(plan)} runs total: "
          f"{len(modes)} modes × {len(targets)} targets × {args.trials} trials")
    print(f"[bench] out: {out_dir}")

    results: dict[tuple[str, str], list[dict]] = {}
    for i, (mode, label, bndb, fn, trial) in enumerate(plan, 1):
        bndb_path = ROOT / bndb
        print(f"[bench] {i}/{len(plan)}  mode={mode}  {label}  trial={trial}",
              flush=True)
        rec = _run_one(label, bndb_path, fn, mode, trial, out_dir,
                       max_turns=args.max_turns,
                       submit_rounds=args.submit_rounds,
                       model=args.model)
        results.setdefault((mode, label), []).append(rec)
        if rec is None:
            print(f"  -> FAILED")
        else:
            print(f"  -> perfect={rec.get('final_perfect')} "
                  f"score={rec.get('final_score'):.2f} "
                  f"submits={rec.get('submit_attempts')} "
                  f"wall={rec.get('_wall_s'):.1f}s "
                  f"tokens={rec.get('input_tokens',0)+rec.get('output_tokens',0):,} "
                  f"${rec.get('cost_usd',0):.3f}")

    print("\n=== aggregate by (mode, target) ===")
    headers = ("mode", "target", "n", "ok%", "score", "wall", "tok", "$", "subs", "its")
    print("  ".join(f"{h:>14}" for h in headers))
    rows: list[dict] = []
    for (mode, label), recs in sorted(results.items()):
        a = _agg(recs)
        rows.append({"mode": mode, "label": label, **a})
        print("  ".join([
            f"{mode:>14}", f"{label:>14}",
            f"{a['n']:>14}",
            f"{a.get('perfect_rate',0):>14.0%}",
            f"{a.get('avg_score',0):>14.2f}",
            f"{a.get('avg_elapsed_s',0):>13.1f}s",
            f"{a.get('avg_total_tokens',0):>14,}",
            f"{a.get('avg_cost_usd',0):>13.3f}$",
            f"{a.get('avg_submits',0):>14.1f}",
            f"{a.get('avg_iters',0):>14.1f}",
        ]))

    # mode-level aggregate (across all targets), trials only count
    # cells where BOTH modes hit perfect=True for this target — i.e.
    # equal correct outcome per the user's brief.
    by_mode: dict[str, list[dict]] = {m: [] for m in modes}
    for (mode, label), recs in results.items():
        for r in recs:
            if r is None:
                continue
            # Restrict to cells where this target has at least one
            # correct outcome in EVERY mode — otherwise comparing
            # times is apples-to-oranges.
            if all(
                any((rr is not None and rr.get("final_perfect"))
                    for rr in results.get((m, label), []))
                for m in modes
            ):
                by_mode[mode].append(r)

    print("\n=== mode-level (only targets where every mode had at least 1 perfect run) ===")
    for mode in modes:
        a = _agg(by_mode[mode])
        print(f"  {mode:>6}: n={a['n']:>2}  ok%={a.get('perfect_rate',0):.0%}  "
              f"avg_wall={a.get('avg_elapsed_s',0):.1f}s  "
              f"avg_tok={a.get('avg_total_tokens',0):,}  "
              f"avg_$={a.get('avg_cost_usd',0):.3f}  "
              f"avg_submits={a.get('avg_submits',0):.1f}  "
              f"avg_iters={a.get('avg_iters',0):.1f}")

    (out_dir / "summary.json").write_text(json.dumps({
        "plan": [{"mode": m, "label": l, "trial": t}
                 for (m, l, _, _, t) in plan],
        "by_cell": {f"{m}|{l}": _agg(recs)
                    for (m, l), recs in results.items()},
        "by_mode": {m: _agg(by_mode[m]) for m in modes},
    }, indent=2))
    print(f"\n[bench] summary at {out_dir}/summary.json")


if __name__ == "__main__":
    main()
