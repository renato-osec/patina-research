# Shared sidecar metadata store for patina pipelines.
#
# Each function in a bndb gets a dict of namespaced payloads written
# by the agents that touched it: `signer.*` for prototype/types,
# `flower.*` for body recovery, future stages add their own keys.
# The store is a plain JSON file next to the input bndb (or wherever
# the pipeline points it) so any stage can read past stages' work
# without loading the bndb. Pipelines call `load`/`save` once per
# run; in-flight updates ride on `set`.
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any


class Recoveries:
    def __init__(
        self,
        path: str | Path,
        *,
        write_namespaces: set[str] | None = None,
    ):
        """`write_namespaces=None` (default) lets any namespace be written
        - back-compat for callers that don't care. Pass an explicit set
        to scope a stage's writes: `Recoveries(p, write_namespaces={"flower"})`
        rejects updates targeting any other namespace, so a marinator-tier
        stage can't accidentally clobber signer/flower's rust memories."""
        self.path = Path(path)
        self._lock = threading.Lock()
        self.write_namespaces = write_namespaces
        self.data: dict[str, dict[str, Any]] = {}
        if self.path.exists():
            try:
                self.data = json.loads(self.path.read_text())
            except Exception:
                self.data = {}

    def _check_writable(self, namespace: str) -> None:
        if self.write_namespaces is None:
            return
        if namespace not in self.write_namespaces:
            raise PermissionError(
                f"recoveries: this stage is scoped to write {sorted(self.write_namespaces)!r}; "
                f"refusing write to {namespace!r}"
            )

    @staticmethod
    def for_bndb(bndb_path: str | Path) -> "Recoveries":
        """Sidecar next to the bndb: `<stem>.patina.json` (replacing the
        `.bndb` suffix). Survives create_database failures because it's
        written separately."""
        p = Path(bndb_path)
        return Recoveries(p.with_suffix(".patina.json"))

    def _key(self, addr: int | str) -> str:
        return addr if isinstance(addr, str) else f"{addr:#x}"

    def get(self, addr: int | str, namespace: str | None = None) -> dict:
        with self._lock:
            entry = self.data.get(self._key(addr), {})
            return dict(entry.get(namespace, {})) if namespace else dict(entry)

    def set(self, addr: int | str, namespace: str, payload: dict) -> None:
        self._check_writable(namespace)
        with self._lock:
            self.data.setdefault(self._key(addr), {})[namespace] = dict(payload)

    def update(self, addr: int | str, namespace: str, **fields) -> None:
        """Shallow-merge `fields` into the existing namespace payload."""
        self._check_writable(namespace)
        with self._lock:
            ns = self.data.setdefault(self._key(addr), {}).setdefault(namespace, {})
            ns.update(fields)

    def save(self) -> None:
        """Atomic write so a crash mid-save can't corrupt the file."""
        with self._lock:
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(self.data, indent=2, sort_keys=True))
            os.replace(tmp, self.path)

    def addrs(self, namespace: str | None = None) -> list[str]:
        """Every addr that has any data (or any data in `namespace`)."""
        with self._lock:
            if namespace is None:
                return list(self.data.keys())
            return [a for a, e in self.data.items() if namespace in e]
