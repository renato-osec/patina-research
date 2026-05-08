"""Build the type-frequency priors dataset for `Priors`.

Pulls crates.io top-N by recent downloads, downloads each .crate tarball,
extracts, runs the syn-based `type_freq` Rust binary against the combined
corpus PLUS the local rustc stdlib source, writes `data/priors.json`.

Usage:
    uv run python -m tools.build_priors                      # default N=100
    uv run python -m tools.build_priors --top 200
    uv run python -m tools.build_priors --top 50 --skip-stdlib
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tarfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent       # agents/common/
DATA_DIR = ROOT / "data"
# type_freq is now a workspace member of the patina root crate.
PATINA_ROOT = ROOT.parent.parent                    # patina/
TYPE_FREQ_BIN = PATINA_ROOT / "target" / "release" / "type_freq"
CRATES_API = "https://crates.io/api/v1/crates"
DL_TEMPLATE = "https://crates.io/api/v1/crates/{name}/{version}/download"
UA = "patina-roe-priors-builder/0.1 (research; renato@osec.io)"


def fetch_top(n: int) -> list[dict]:
    """Return [{name, max_version, downloads, recent_downloads}, ...]."""
    out: list[dict] = []
    page = 1
    per_page = 100
    while len(out) < n:
        url = (f"{CRATES_API}?page={page}&per_page={per_page}"
               f"&sort=recent-downloads")
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=30) as resp:
            d = json.load(resp)
        crates = d.get("crates", [])
        if not crates:
            break
        for c in crates:
            out.append({
                "name": c["name"],
                "max_version": c["max_stable_version"] or c["max_version"],
                "downloads": c["downloads"],
                "recent_downloads": c.get("recent_downloads", 0),
            })
            if len(out) >= n:
                break
        page += 1
    return out


def download_crate(name: str, version: str, dst_dir: Path) -> Path | None:
    """Download a single .crate tarball, return path. None on failure."""
    url = DL_TEMPLATE.format(name=name, version=version)
    out = dst_dir / f"{name}-{version}.crate"
    if out.exists() and out.stat().st_size > 0:
        return out
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=60) as resp, open(out, "wb") as f:
            shutil.copyfileobj(resp, f)
        return out
    except Exception as e:
        print(f"  download fail {name} {version}: {e}", file=sys.stderr)
        return None


def extract(tar: Path, dst: Path) -> None:
    """Extract a .crate (gzipped tar) into dst/. Crate root = dst/<name>-<ver>/."""
    with tarfile.open(tar, "r:gz") as t:
        # Filter to .rs / Cargo.toml / LICENSE only - skip target/, .git/, etc.
        members = [m for m in t.getmembers()
                   if m.isfile() and (
                       m.name.endswith(".rs")
                       or m.name.endswith("Cargo.toml")
                   )]
        # Python 3.12 wants a filter; pass the safest one.
        try:
            t.extractall(dst, members=members, filter="data")  # type: ignore[arg-type]
        except TypeError:
            t.extractall(dst, members=members)


def stdlib_root() -> Path | None:
    try:
        sysroot = subprocess.check_output(
            ["rustc", "--print", "sysroot"], text=True
        ).strip()
    except Exception:
        return None
    p = Path(sysroot) / "lib" / "rustlib" / "src" / "rust" / "library"
    return p if p.exists() else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=100,
                    help="how many crates from crates.io top-by-recent-downloads")
    ap.add_argument("--skip-stdlib", action="store_true",
                    help="exclude rustc stdlib from the corpus")
    ap.add_argument("--cache-dir", type=Path,
                    default=DATA_DIR / "crate_cache",
                    help="where downloaded .crate tarballs + extractions live")
    ap.add_argument("--out", type=Path, default=DATA_DIR / "priors.json")
    ap.add_argument("--out-meta", type=Path, default=DATA_DIR / "priors_meta.json")
    args = ap.parse_args()

    if not TYPE_FREQ_BIN.exists():
        print(f"build the rust binary first: "
              f"cd {PATINA_ROOT} && cargo build -p type_freq --release",
              file=sys.stderr)
        return 1

    DATA_DIR.mkdir(exist_ok=True, parents=True)
    args.cache_dir.mkdir(exist_ok=True, parents=True)
    tarballs_dir = args.cache_dir / "tarballs"
    extracted_dir = args.cache_dir / "extracted"
    tarballs_dir.mkdir(exist_ok=True, parents=True)
    extracted_dir.mkdir(exist_ok=True, parents=True)

    print(f"fetching top {args.top} crates by recent downloads...", flush=True)
    top = fetch_top(args.top)
    print(f"got {len(top)} crates")

    n_ok = 0
    for i, c in enumerate(top, 1):
        tar = download_crate(c["name"], c["max_version"], tarballs_dir)
        if tar is None:
            continue
        crate_dir = extracted_dir / f"{c['name']}-{c['max_version']}"
        if not crate_dir.exists():
            try:
                extract(tar, extracted_dir)
            except Exception as e:
                print(f"  extract fail {c['name']}: {e}", file=sys.stderr)
                continue
        n_ok += 1
        if i % 10 == 0:
            print(f"  [{i}/{len(top)}]  ok={n_ok}", flush=True)
    print(f"prepared {n_ok}/{len(top)} crates in {extracted_dir}")

    # Run type_freq over (stdlib?, extracted_dir).
    roots: list[Path] = []
    if not args.skip_stdlib:
        sl = stdlib_root()
        if sl:
            roots.append(sl)
            print(f"+stdlib: {sl}")
        else:
            print("warn: no rustc stdlib found, skipping", file=sys.stderr)
    roots.append(extracted_dir)

    cmd = [str(TYPE_FREQ_BIN), "--out", str(args.out)] + [str(r) for r in roots]
    print(f"running: {' '.join(cmd)}", flush=True)
    rc = subprocess.call(cmd)
    if rc != 0:
        return rc

    # Side-car metadata: which corpora, which top-N, when.
    meta = {
        "top_n": args.top,
        "stdlib_included": not args.skip_stdlib,
        "stdlib_root": str(stdlib_root()) if not args.skip_stdlib else None,
        "extracted_dir": str(extracted_dir),
        "n_crates_ok": n_ok,
        "crate_list": [{"name": c["name"], "version": c["max_version"],
                        "recent_downloads": c["recent_downloads"]} for c in top],
    }
    args.out_meta.write_text(json.dumps(meta, indent=2))
    print(f"\nwrote {args.out}\nwrote {args.out_meta}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
