# Type-frequency priors. Built offline by tools/build_priors.py
# from rustc stdlib + crates.io top-N via the type_freq Rust binary.
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# Components shipped with rustc; expanded by the "stdlib" scope alias.
STDLIB_CRATES = frozenset({
    "alloc", "alloctests", "core", "coretests", "std", "std_detect",
    "test", "panic_unwind", "panic_abort", "proc_macro",
    "compiler-builtins", "backtrace", "rustc-std-workspace-core",
    "rustc-std-workspace-alloc", "rustc-std-workspace-std",
    "unwind", "profiler_builtins", "sysroot",
})


@dataclass
class Priors:
    raw: dict[str, int] = field(default_factory=dict)
    crates: dict[str, int] = field(default_factory=dict)
    by_crate: dict[str, dict[str, int]] = field(default_factory=dict)
    total_files: int = 0
    failed_files: int = 0
    _global_weights: dict[str, float] = field(default_factory=dict)
    _scope_cache: dict = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path | None = None) -> "Priors":
        path = path or DATA_DIR / "priors.json"
        if not path.exists():
            return cls()
        d = json.loads(path.read_text())
        p = cls(
            raw=d.get("raw", {}),
            crates=d.get("crates", {}),
            by_crate=d.get("by_crate", {}),
            total_files=d.get("total_files", 0),
            failed_files=d.get("failed_files", 0),
        )
        p._global_weights = p._compute(p.crate_ids())
        return p

    def empty(self) -> bool:
        return not self.raw

    def crate_ids(self) -> list[str]:
        return list(self.by_crate.keys())

    def resolve_scope(self, scope: list[str] | str | None) -> list[str]:
        # None -> every crate. "stdlib" -> rustc-shipped components.
        # Any other name matches exactly or by "<name>-" prefix.
        if scope is None:
            return self.crate_ids()
        names = [scope] if isinstance(scope, str) else list(scope)
        out: list[str] = []
        for want in names:
            if want == "stdlib":
                out.extend(c for c in self.crate_ids() if c in STDLIB_CRATES)
            elif want in self.by_crate:
                out.append(want)
            else:
                out.extend(cid for cid in self.crate_ids()
                           if cid.startswith(want + "-"))
        seen: set[str] = set()
        return [c for c in out if not (c in seen or seen.add(c))]

    def _compute(self, scope_crates: list[str]) -> dict[str, float]:
        # weight = log1p(raw_in_scope) * (crates_with_label / max_crates),
        # peak-normalized to [0, 1].
        if not scope_crates:
            return {}
        scope_raw: dict[str, int] = {}
        scope_crates_with: dict[str, int] = {}
        for cid in scope_crates:
            for label, n in self.by_crate.get(cid, {}).items():
                scope_raw[label] = scope_raw.get(label, 0) + n
                scope_crates_with[label] = scope_crates_with.get(label, 0) + 1
        max_crates = max(scope_crates_with.values(), default=1) or 1
        scores = {l: math.log1p(scope_raw.get(l, 0)) * (scope_crates_with.get(l, 0) / max_crates)
                  for l in self.raw}
        peak = max(scores.values(), default=1.0) or 1.0
        return {k: v / peak for k, v in scores.items()}

    def weights_for_scope(self, scope: list[str] | str | None) -> dict[str, float]:
        if scope is None:
            return self._global_weights
        key = scope if isinstance(scope, str) else tuple(scope)
        cached = self._scope_cache.get(key)
        if cached is not None:
            return cached
        out = self._compute(self.resolve_scope(scope))
        self._scope_cache[key] = out
        return out

    def weight(self, label: str, scope: list[str] | str | None = None,
               default: float = 0.01) -> float:
        return self.weights_for_scope(scope).get(label, default)

    def rank(self, labels: list[str],
             scope: list[str] | str | None = None) -> list[tuple[str, float]]:
        ws = self.weights_for_scope(scope)
        return sorted(((l, ws.get(l, 0.01)) for l in labels), key=lambda x: -x[1])


# Module-level cache so processes that hit `weight` on the hot path
# pay one disk read total.
_CACHED: Priors | None = None
def cached() -> Priors:
    global _CACHED
    if _CACHED is None:
        _CACHED = Priors.load()
    return _CACHED


if __name__ == "__main__":
    import sys
    p = cached()
    if p.empty():
        print("no priors loaded - run `python -m tools.build_priors` first")
        raise SystemExit(1)

    if len(sys.argv) > 1:
        scope = sys.argv[1:]
        if scope == ["stdlib"]:
            scope = "stdlib"
        title = f"scope={scope}"
    else:
        scope = None
        title = "global"

    print(f"--- weights ({title}) ---")
    print(f"{'label':<18}{'weight':>10}")
    print("-" * 28)
    for label, w in p.rank(list(p.raw.keys()), scope=scope):
        print(f"{label:<18}{w:>10.3f}")
