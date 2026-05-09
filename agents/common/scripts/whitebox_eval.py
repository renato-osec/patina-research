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
    """True if the recovered fn body has meaningful content. Heuristic:
       (a) >= 2 meaningful statements, OR
       (b) any of `panic!`, `loop {}`, `unreachable!`, real method call
           with arg use - i.e. anything that exercises the inputs.
       Excludes empty body and pure `let _ = X` discards.
    """
    if not source or not fn_name:
        return False
    m = re.search(
        r"\bfn\s+" + re.escape(fn_name) + r"\s*[^{]*\{(.*)\}",
        source, re.DOTALL,
    )
    if not m:
        return False
    body = m.group(1).strip()
    if not body:
        return False
    stmts = [s.strip() for s in body.split(";") if s.strip()
             and not s.strip().startswith("//")]
    # All-discard body: `let _ = ...` only. Cheese.
    real_stmts = [s for s in stmts if not s.startswith("let _")]
    if not real_stmts:
        return False
    if len(real_stmts) >= 2:
        return True
    # Single-stmt body: real iff it's a panic/return/expr, not stub.
    s = real_stmts[0]
    real_tokens = ("panic!", "loop {", "unreachable!", "todo!",
                   "return ", ".expect(", ".unwrap(", "format_args!",
                   "format!", "Some(", "None", "Ok(", "Err(",
                   "if ", "match ")
    return any(t in s for t in real_tokens)


def _strip_abi_hash(name: str) -> str:
    """Trim Rust ABI hash suffix `::h<16hex>` if present."""
    return re.sub(r"::h[0-9a-f]{16}$", "", name)


def _demangle_legacy_v0(name: str) -> list[str] | None:
    """Demangle a legacy Rust mangled name (`_ZN<len><name>..17h<hex>E`)
    into its path components, e.g. `_ZN6source4main17h..E` -> `["source", "main"]`.
    Returns None if not a legacy mangled name."""
    if not name.startswith("_ZN") or not name.endswith("E"):
        return None
    body = name[3:-1]
    parts: list[str] = []
    i = 0
    while i < len(body):
        # parse <len>
        j = i
        while j < len(body) and body[j].isdigit():
            j += 1
        if j == i:
            break
        n = int(body[i:j])
        i = j
        chunk = body[i:i+n]
        i += n
        # `17h<16hex>` is the trailing hash; if this chunk matches that
        # shape, stop (don't include the hash in the demangled path).
        if (n == 17 and chunk.startswith("h")
                and re.fullmatch(r"h[0-9a-f]{16}", chunk)):
            break
        parts.append(chunk)
    return parts or None


def _leaf_name(name: str) -> str:
    """Extract just the leaf identifier:
        `mod::Type::method::h<hash>` -> `method`
        `_ZN<len>core<len>option<len>expect_failed17h..E` -> `expect_failed`
        `<core::ops::drop::Drop for ...>::drop` -> `drop`
    Falls back to the unchanged input if nothing identifiable found.
    """
    if not name:
        return ""
    # Rust legacy ABI mangled.
    parts = _demangle_legacy_v0(name)
    if parts:
        return parts[-1]
    n = _strip_abi_hash(name)
    if "::" in n:
        n = n.rsplit("::", 1)[-1]
    # Strip trait-impl angle brackets if any: `<T as U>::method` already
    # split above; `<...>::name` would have been handled. If it's still
    # `<...>` shape, drop the angles + return contents.
    n = n.strip()
    if n.startswith("<") and ">" in n:
        n = n.rsplit(">", 1)[-1].lstrip(":").strip()
    return n or name


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
