"""List every function whose shape matches Vec / String / PathBuf / OsString."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "python_tests"))
from patina_query import Report


TARGET_LABELS = ("Vec", "String", "PathBuf", "OsString")


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit("usage: find_vecs.py <binary>")
    binary = Path(sys.argv[1])
    report = Report.analyze(binary)

    for label in TARGET_LABELS:
        hits = report.by_label(label)
        print(f"{label:<10}  {len(hits):>4} functions")
        for fn in hits[:5]:
            offsets = fn.field_offsets()
            print(f"  {fn.addr:#010x}  offsets={offsets}")
        if len(hits) > 5:
            print(f"  ... {len(hits) - 5} more")
        print()


if __name__ == "__main__":
    main()
