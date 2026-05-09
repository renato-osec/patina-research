#!/usr/bin/env python3
"""Score a chain run against the symbolic source.rs ground truth.

Usage:
    whitebox_eval.py <run-dir> <source.rs>
        run-dir: e.g. samples/runs/hl-node2/2026-05-09T1730/
                 (must contain signer/signer.json, flower/flower.json)
        source.rs: ground-truth Rust the binary was compiled from.

Per-fn metrics (each in [0, 1]):
    sig_arity_match:  recovered arg-count == ground truth?
    sig_name_in_gt:   recovered fn name matches a fn in source.rs?
    body_recovered:   flower submitted ANY body that compiled?
    body_real:        flower's body has > 2 meaningful statements (not a stub)?

Outputs a single JSON to stdout summarising every fn the run touched.
Run with PATH including a python that has lymph + binja (only needed
for ground-truth fn extraction; no binary load).
"""
from __future__ import annotations
import json
import re
import sys
from pathlib import Path


# Ground-truth fn signature extractor (regex; not a full parser).
# Captures name, args (parens content), return type if present.
_FN_RE = re.compile(
    r"\bfn\s+([A-Za-z_][\w]*)\s*(?:<[^>]*>)?\s*\(([^)]*)\)"
    r"(?:\s*->\s*([^\{;]+))?",
)


def _gt_fns(source_rs: str) -> dict[str, dict]:
    """Extract `{fn_name: {arity, ret}}` from source.rs."""
    out: dict[str, dict] = {}
    for m in _FN_RE.finditer(source_rs):
        name = m.group(1)
        args_raw = m.group(2).strip()
        ret = (m.group(3) or "()").strip()
        if not args_raw:
            arity = 0
        else:
            arity = sum(1 for p in args_raw.split(",") if p.strip())
        out.setdefault(name, {"arity": arity, "ret": ret})
    return out


def _recovered_sig(decl_or_source: str) -> tuple[str | None, int, str]:
    """Pull (fn_name, arity, return-type) from a recovered Rust string.
    Falls back gracefully on partial matches; signer decls often lack
    a fn name (`(args) -> R`), in which case name=None."""
    if not decl_or_source:
        return None, 0, ""
    m = _FN_RE.search(decl_or_source)
    if m:
        name = m.group(1)
        args_raw = m.group(2).strip()
        ret = (m.group(3) or "()").strip()
        arity = 0 if not args_raw else sum(
            1 for p in args_raw.split(",") if p.strip()
        )
        return name, arity, ret
    # Signer-style decl: `(arg1, arg2) -> R` (no fn keyword)
    paren = re.search(r"\(([^)]*)\)\s*->\s*([^\{;]+)", decl_or_source)
    if paren:
        args_raw = paren.group(1).strip()
        ret = paren.group(2).strip()
        arity = 0 if not args_raw else sum(
            1 for p in args_raw.split(",") if p.strip()
        )
        return None, arity, ret
    return None, 0, ""


def _body_real(source: str, fn_name: str | None) -> bool:
    """True if the recovered fn body has > 2 meaningful statements."""
    if not source or not fn_name:
        return False
    # Match `fn <name>(...) ... { ... }` body greedily to last `}`.
    m = re.search(
        r"\bfn\s+" + re.escape(fn_name) + r"\s*[^{]*\{(.*)\}",
        source, re.DOTALL,
    )
    if not m:
        return False
    body = m.group(1).strip()
    stmts = [s.strip() for s in body.split(";") if s.strip()
             and not s.strip().startswith("//")]
    return len(stmts) > 2


def _strip_abi_hash(name: str) -> str:
    """Trim Rust ABI hash suffix `::h<16hex>` if present."""
    return re.sub(r"::h[0-9a-f]{16}$", "", name)


def _leaf_name(name: str) -> str:
    """Extract just the leaf identifier from a path: `mod::Type::method` -> `method`."""
    n = _strip_abi_hash(name)
    n = n.split("::")[-1]
    # Demangle Rust-mangled `_ZN...` very loosely: accept any [A-Za-z0-9_]+
    if not re.fullmatch(r"[A-Za-z_][\w]*", n):
        return n
    return n


def _score_fn(fn_name: str, signer_decl: str, flower_source: str,
              gt: dict[str, dict]) -> dict:
    """Score one fn's (signer, flower) recovery against ground truth."""
    leaf = _leaf_name(fn_name)
    gt_fn = gt.get(leaf)
    s_name, s_arity, s_ret = _recovered_sig(signer_decl)
    f_name, f_arity, _f_ret = _recovered_sig(flower_source)
    # Coalesce names: prefer recovered, fall back to leaf.
    used_name = f_name or s_name or leaf
    out = {
        "leaf": leaf,
        "in_gt": gt_fn is not None,
        "gt_arity": gt_fn["arity"] if gt_fn else None,
        "gt_ret": gt_fn["ret"] if gt_fn else None,
        "signer_arity": s_arity,
        "signer_ret": s_ret,
        "flower_arity": f_arity,
        "flower_has_body": bool(flower_source),
        "flower_body_real": _body_real(flower_source, used_name),
    }
    if gt_fn:
        out["sig_arity_match_signer"] = (s_arity == gt_fn["arity"])
        out["sig_arity_match_flower"] = (
            f_arity == gt_fn["arity"] if flower_source else None
        )
    return out


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    run_dir, src_path = Path(argv[1]), Path(argv[2])
    if not run_dir.is_dir() or not src_path.is_file():
        print(f"missing: run_dir={run_dir}, source={src_path}", file=sys.stderr)
        return 2
    gt = _gt_fns(src_path.read_text(encoding="utf-8"))
    signer_results: list[dict] = []
    flower_results: list[dict] = []
    sj = run_dir / "signer" / "signer.json"
    fj = run_dir / "flower" / "flower.json"
    if sj.exists():
        signer_results = json.loads(sj.read_text())["results"]
    if fj.exists():
        flower_results = json.loads(fj.read_text())["results"]
    flower_by_name = {r["name"]: r for r in flower_results}
    rows: list[dict] = []
    for sr in signer_results:
        n = sr["name"]
        fr = flower_by_name.get(n, {})
        rows.append(_score_fn(
            n, sr.get("submitted_decl") or "",
            fr.get("submitted_source") or "", gt,
        ))
    # Aggregate signals.
    n_total = len(rows)
    n_in_gt = sum(1 for r in rows if r["in_gt"])
    n_sig_arity_signer = sum(1 for r in rows if r.get("sig_arity_match_signer"))
    n_sig_arity_flower = sum(1 for r in rows if r.get("sig_arity_match_flower"))
    n_body_real = sum(1 for r in rows if r["flower_body_real"])
    summary = {
        "run_dir": str(run_dir),
        "source": str(src_path),
        "ground_truth_fns": len(gt),
        "fns_run": n_total,
        "fns_matched_in_gt": n_in_gt,
        "signer_arity_match": f"{n_sig_arity_signer}/{n_in_gt}" if n_in_gt else "0/0",
        "flower_arity_match": f"{n_sig_arity_flower}/{n_in_gt}" if n_in_gt else "0/0",
        "flower_real_body": f"{n_body_real}/{n_total}",
        "per_fn": rows,
    }
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
