#!/usr/bin/env python3
"""Archive a chain run from agents/outputs/<chain_dir>/ into a non-temp
samples/runs/<bin>/<timestamp>/ tree, preserving everything needed to
re-open the recovery in binja AND render the visualization.

What gets copied:
- {warper,marinator,signer,flower}/<stage>.json
- final flower-stage .bndb (the most recovered) + sidecar .patina.json
- the live flower.log + signer.log

Usage:
    archive_run.py <agents/outputs/chain_dir> <samples/runs/<bin>> [--name <suffix>]
"""
from __future__ import annotations
import shutil
import sys
from datetime import datetime
from pathlib import Path


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__, file=sys.stderr)
        return 2
    src = Path(sys.argv[1]).resolve()
    dst_root = Path(sys.argv[2]).resolve()
    name = ""
    if "--name" in sys.argv:
        name = sys.argv[sys.argv.index("--name") + 1]
    if not src.is_dir():
        print(f"src not found: {src}", file=sys.stderr)
        return 2
    ts = datetime.now().strftime("%Y-%m-%dT%H%M")
    if name:
        ts = f"{ts}-{name}"
    dst = dst_root / ts
    dst.mkdir(parents=True, exist_ok=False)

    flower_bndb = next(src.glob("flower/*.flowered.bndb"), None)
    flower_sidecar = next(src.glob("flower/*.flowered.patina.json"), None)
    signer_bndb = next(src.glob("signer/*.signed.bndb"), None)
    signer_sidecar = next(src.glob("signer/*.signed.patina.json"), None)

    chosen_bndb = flower_bndb or signer_bndb
    chosen_sidecar = flower_sidecar or signer_sidecar
    if chosen_bndb:
        shutil.copy2(chosen_bndb, dst / "recovered.bndb")
    if chosen_sidecar:
        shutil.copy2(chosen_sidecar, dst / "sidecar.json")

    for stage in ("warper", "marinator", "signer", "flower"):
        sd = src / stage
        if not sd.is_dir():
            continue
        out_sd = dst / stage
        out_sd.mkdir(exist_ok=True)
        for fname in (f"{stage}.json", f"{stage}.log"):
            sf = sd / fname
            if sf.is_file():
                shutil.copy2(sf, out_sd / fname)

    print(f"archived -> {dst}")
    bytes_total = sum(f.stat().st_size for f in dst.rglob("*") if f.is_file())
    print(f"  {bytes_total / 1024 / 1024:.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
