"""Show shape, exact matches, and subset candidates for one function."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "python_tests"))
from patina_query import Report


def shape_kind(sub) -> str:
    return next(iter(sub)) if isinstance(sub, dict) else "?"


def main() -> None:
    if len(sys.argv) < 3:
        sys.exit("usage: inspect_function.py <binary> <hex_addr>")
    binary = Path(sys.argv[1])
    addr = int(sys.argv[2], 16)

    report = Report.analyze(binary)
    fn = report.get(addr)
    if fn is None:
        sys.exit(f"no function at {addr:#x} (available: {len(report)} fns)")

    print(f"[{addr:#x}]")
    print(f"exact ({len(fn.exact)}): {fn.exact[:8]}{'...' if len(fn.exact) > 8 else ''}")
    if fn.synthetic:
        print(f"synthetic: {fn.synthetic}")

    if fn.shape and "Struct" in fn.shape:
        print("\nshape:")
        for off, sub in fn.shape["Struct"]:
            kind = shape_kind(sub)
            inner = sub.get(kind) if isinstance(sub, dict) else sub
            print(f"  +{off:<3}  {kind:<7} {inner}")

    if fn.catalog_subset:
        print(f"\nsubset candidates ({len(fn.catalog_subset)}):")
        for h in fn.catalog_subset[:10]:
            print(f"  {h.label:<44}  @+{h.offset:<3}  {h.coverage}B  {h.fields} fields")


if __name__ == "__main__":
    main()
