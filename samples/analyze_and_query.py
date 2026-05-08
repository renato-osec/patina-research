"""Analyze a Rust binary end-to-end and print summary stats."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "python_tests"))
from patina_query import Report


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit("usage: analyze_and_query.py <binary>")
    binary = Path(sys.argv[1])
    report = Report.analyze(binary)

    print(f"analyzed {binary}")
    print(f"  functions traced   : {len(report)}")
    print(f"  unique labels      : {len(report.unique_labels())}")

    by_label_counts = [
        (label, len(report.by_label(label)))
        for label in report.unique_labels()
    ]
    by_label_counts.sort(key=lambda x: -x[1])

    print("\n  top 10 labels by function count:")
    for label, n in by_label_counts[:10]:
        print(f"    {label:<46}  {n:>4} fn")


if __name__ == "__main__":
    main()
