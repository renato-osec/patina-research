#!/usr/bin/env python3
"""Aggregate per-run signer/flower stats under a samples/runs/<bin>/ tree
into a single leaderboard. Markdown by default; --html writes alongside.

Usage:
    eval_index.py samples/runs/hl-node2 [--html]
"""
from __future__ import annotations
import json
import sys
from pathlib import Path


def _read(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def _scan(root: Path) -> list[dict]:
    rows: list[dict] = []
    for run_dir in sorted(root.iterdir()):
        if not run_dir.is_dir() or run_dir.name == "viz":
            continue
        signer = (_read(run_dir / "signer.json")
                  or _read(run_dir / "signer" / "signer.json") or {})
        flower = (_read(run_dir / "flower.json")
                  or _read(run_dir / "flower" / "flower.json") or {})
        rows.append({
            "run": run_dir.name,
            "signer_perfect": f"{signer.get('perfect', 0)}/{signer.get('targets', 0)}",
            "signer_cost": signer.get("cost_usd_total", 0.0),
            "flower_perfect": f"{flower.get('perfect', 0)}/{flower.get('targets', 0)}",
            "flower_cost": flower.get("cost_usd_total", 0.0),
            "total_cost": (signer.get("cost_usd_total", 0.0)
                           + flower.get("cost_usd_total", 0.0)),
            "has_eval": (run_dir / "EVAL.md").exists(),
        })
    return rows


def _md(rows: list[dict], target: str) -> str:
    out = [f"# {target} eval index\n",
           f"_{len(rows)} archived run(s)_\n",
           "| run | signer perfect | flower perfect | $ | EVAL |",
           "|---|---|---|---:|:-:|"]
    for r in rows:
        out.append(f"| {r['run']} | {r['signer_perfect']} "
                   f"({r['signer_cost']:.2f}) | {r['flower_perfect']} "
                   f"({r['flower_cost']:.2f}) | {r['total_cost']:.2f} | "
                   f"{'yes' if r['has_eval'] else '-'} |")
    return "\n".join(out)


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    root = Path(sys.argv[1]).resolve()
    if not root.is_dir():
        print(f"not a directory: {root}", file=sys.stderr)
        return 2
    html_out = "--html" in sys.argv[2:]
    rows = _scan(root)
    md = _md(rows, root.name)
    viz_dir = root / "viz"
    viz_dir.mkdir(exist_ok=True)
    (viz_dir / "index.md").write_text(md + "\n")
    if html_out:
        html = ("<!doctype html><html><head><meta charset='utf-8'>"
                f"<title>{root.name} eval</title>"
                "<style>body{font-family:system-ui;max-width:900px;"
                "margin:2em auto;padding:0 1em}"
                "pre{white-space:pre-wrap}</style></head>"
                f"<body><pre>{md}</pre></body></html>")
        (viz_dir / "index.html").write_text(html)
    print(f"wrote {viz_dir / 'index.md'}")
    if html_out:
        print(f"wrote {viz_dir / 'index.html'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
