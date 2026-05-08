# Context shared by every tool factory: one BV + fn addr + optional crate scope.
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import binaryninja as bn

from recoveries import Recoveries


@dataclass
class TargetCtx:
    bv: bn.BinaryView
    fn_addr: int
    # crates the binary links; None -> global priors.
    crates: list[str] | None = None
    # Mutating tools (marinator_write) acquire this before touching the BV.
    # Read-only tools and the recovery agents leave it alone - no overhead.
    # Vector35 (issue #6109) confirm that concurrent BV mutations can crash;
    # this is the official-guidance "apply changes in a blocking way" lock.
    write_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    # Optional sidecar metadata (loaded by the pipeline; agents that
    # have one can read prior-stage findings + record their own).
    recoveries: Recoveries | None = None

    @classmethod
    def load(cls, binary_path: str, fn_addr: int,
             crates: list[str] | None = None) -> "TargetCtx":
        return cls(bv=bn.load(binary_path), fn_addr=fn_addr, crates=crates)

    def close(self) -> None:
        self.bv.file.close()

    def func(self, x: Any) -> bn.Function | None:
        if isinstance(x, int):
            return self.bv.get_function_at(x) or next(
                iter(self.bv.get_functions_containing(x) or []), None,
            )
        s = str(x).strip()
        if s.startswith("0x"):
            try:
                return self.func(int(s, 16))
            except ValueError:
                pass
        return next((f for f in self.bv.functions
                     if s in (f.symbol.full_name or "") or s == f.name), None)

    def target_func(self) -> bn.Function | None:
        return self.func(self.fn_addr)

    def fork(self, fn_addr: int) -> "TargetCtx":
        # Sibling ctx for a different function on the same BV. Shares bv
        # and write_lock with self so concurrent workers still serialize
        # mutations; gets its own fn_addr so per-worker reads don't race.
        return TargetCtx(
            bv=self.bv,
            fn_addr=fn_addr,
            crates=self.crates,
            write_lock=self.write_lock,
            recoveries=self.recoveries,
        )
