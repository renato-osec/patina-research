from typing import Any, Optional


class FlowGraph:
    """Per-function MLIL-SSA dataflow graph extracted from a binary."""

    @property
    def fn_name(self) -> str: ...
    @property
    def fn_addr(self) -> int: ...
    @property
    def params(self) -> list[str]: ...
    @property
    def return_slot(self) -> Optional[str]: ...

    def variables(self) -> list[str]:
        """All slot display names in declaration order."""

    def edges(self) -> list[tuple[str, str, str]]:
        """[(kind, src_name, dst_name), ...] for every edge."""

    def successors(self, name: str) -> list[tuple[str, str]]:
        """Direct outgoing edges as [(dst_name, edge_kind), ...]."""

    def predecessors(self, name: str) -> list[tuple[str, str]]:
        """Direct incoming edges as [(src_name, edge_kind), ...]."""

    def depends_on(self, target: str, source: str, depth: int = 0) -> bool:
        """True iff data can flow source -> ... -> target. `depth` is the
        recursion budget for crossing call boundaries; when it runs out
        (or starts at 0), every callsite is treated as fully connected
        (any-arg ↔ any-arg ↔ ret) — the conservative worst case."""

    def transitive_sinks(self, name: str, depth: int = 0) -> list[str]:
        """All slots reachable forward from `name`. See `depends_on` for `depth`."""

    def transitive_sources(self, name: str, depth: int = 0) -> list[str]:
        """All slots that can reach `name`. See `depends_on` for `depth`."""


def analyze(bv: Any, addr: int) -> FlowGraph:
    """Build the MLIL-SSA dataflow graph for the function at `addr` in `bv`."""


def analyze_block(bv: Any, fn_addr: int, block_addr: int) -> FlowGraph:
    """Lower a single MLIL-SSA basic block of the function at `fn_addr`,
    identified by the binary address of its first instruction."""


def analyze_block_at_index(bv: Any, fn_addr: int, start_index: int) -> FlowGraph:
    """Like `analyze_block`, but identifies the block by its first MLIL-SSA
    instruction index. Cheaper — no address scan."""


def analyze_region(
    bv: Any, fn_addr: int, block_start: int, block_end: int,
) -> FlowGraph:
    """Lower a contiguous range of basic blocks `[block_start, block_end)`."""


def analyze_blocks(
    bv: Any, fn_addr: int, block_ids: list[int],
) -> FlowGraph:
    """Lower an arbitrary set of basic-block indices into one FlowGraph.
    Indices may be non-contiguous (inlined fn body lowered to BBs
    7+12+23). Use for region submissions where the agent groups BBs by
    source-level meaning."""


def list_blocks(
    bv: Any, fn_addr: int,
) -> list[tuple[int, int, int, int]]:
    """`[(idx, start_addr, end_addr, instr_count), ...]` for every basic
    block in the fn's MLIL-SSA."""


def check_compatibility(
    rust_edges: list[tuple[str, str, str]],
    anem: FlowGraph,
    mapping: dict[str, str],
    depth: int = 0,
) -> tuple[bool, list[str]]:
    """Cross-check rust (lymph) dependencies against the binary FlowGraph,
    translating names with `mapping[rust_var] = il_var`. Returns
    (compatible, [diffs]). See `FlowGraph.depends_on` for `depth`."""
