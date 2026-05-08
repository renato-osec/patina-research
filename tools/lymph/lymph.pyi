from typing import Optional


class FlowGraph:
    """Per-function dataflow graph extracted from Rust MIR."""

    @property
    def fn_name(self) -> str: ...
    @property
    def params(self) -> list[str]: ...
    @property
    def return_slot(self) -> Optional[str]: ...

    def variables(self) -> list[str]:
        """All slot display names in declaration order."""

    def type_of(self, name: str) -> str:
        """Look up a slot's type by display name. Raises KeyError if absent."""

    def edges(self) -> list[tuple[str, str, str]]:
        """[(kind, src_name, dst_name), ...] for every edge."""

    def successors(self, name: str) -> list[tuple[str, str]]:
        """Direct outgoing edges as [(dst_name, edge_kind), ...]."""

    def predecessors(self, name: str) -> list[tuple[str, str]]:
        """Direct incoming edges as [(src_name, edge_kind), ...]."""

    def depends_on(self, target: str, source: str) -> bool:
        """True iff data can flow source -> ... -> target."""

    def transitive_sinks(self, name: str) -> list[str]:
        """All slots reachable forward from `name` (excludes `name` itself)."""

    def transitive_sources(self, name: str) -> list[str]:
        """All slots that can reach `name` (excludes `name` itself)."""


def analyze(
    source: str,
    root: Optional[str] = None,
    depth: Optional[int] = None,
) -> list[FlowGraph]:
    """Build per-function MIR dataflow graphs from Rust source.

    With `root=None`, returns one FlowGraph per fn definition (flat
    sweep over all body owners). With `root` + `depth`, BFS-walks from
    the function named `root` through statically resolvable callees up
    to `depth` hops — `depth=0` returns `[root]` only, `depth=1` adds
    its direct callees, etc. Indirect / dyn / unresolvable trait calls
    are not followed (rustc's `Instance::try_resolve` returns None).
    """


def dump(
    source: str,
    root: Optional[str] = None,
    depth: Optional[int] = None,
) -> str:
    """Human-readable text dump of every analyzed graph.

    Same `root` / `depth` surface as `analyze` — flat by default,
    depth-bounded BFS from `root` when both are supplied.
    """
