from dataclasses import dataclass
from typing import Any, Iterable


@dataclass
class SlotCheck:
    name: str               # "arg0".."argN" or "ret"
    expected_regs: list[str]
    expected_pass_mode: str
    observed_regs: list[str]
    agree: bool
    note: str               # populated when agree=False, else ""


@dataclass
class SignatureCheck:
    function_addr: int
    function_name: str
    decl: str
    arity_match: bool
    sret_match: bool
    return_match: bool
    slots: list[SlotCheck]
    issues: list[str]       # short, human-readable; safe to print to an agent

    @property
    def score(self) -> float: ...    # 0..1, equal-weight per slot + bonus
    @property
    def perfect(self) -> bool: ...   # arity + sret + return + every slot agree
    def summary(self) -> str: ...    # 1-3 line text suitable for agent feedback
    def to_dict(self) -> dict: ...


def check_signature(
    bv_or_path: Any,
    addr: int,
    decl: str,
    *,
    prelude: str | None = None,
    target: str | None = None,
) -> SignatureCheck:
    """Compare a candidate Rust function declaration against the function
    actually present at `addr` in `bv_or_path`.

    `bv_or_path` can be a `binaryninja.BinaryView` or a path the function
    will load (and close on its own). `decl` is anything `nacre.signature`
    accepts (`(a: u64, b: &str) -> Result<usize, E>` etc.). Pass struct
    definitions or `use` statements via `prelude` if `decl` references
    user-defined types.

    Returns a `SignatureCheck` with per-slot register-assignment
    agreement, sret/non-sret consistency, an overall `score`, and a
    short `issues` list intended for direct surfacing to an LLM agent.
    """


def check_many(
    bv_or_path: Any,
    cases: Iterable[tuple[int, str]],
    *,
    prelude: str | None = None,
) -> list[SignatureCheck]:
    """Run `check_signature` against each `(addr, decl)` pair, sharing
    one BV load. Useful when probing the same binary with many candidate
    decls."""
