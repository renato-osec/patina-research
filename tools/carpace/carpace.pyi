FlatField = tuple[str, int, int, str]  # (path, offset, size, type)
Layout = tuple[str, int, int, list[FlatField]]  # (name, size, align, fields)

ResolvedCrate = tuple[str, str, str]  # (name, version, src_path)
MissingCrate = tuple[str, str]         # (name, version)
ResolveResult = tuple[list[ResolvedCrate], list[MissingCrate]]

def build_stub(crates: list[tuple[str, str]]) -> str:
    """Build a stub crate from (name, version) pairs. Returns target/release/deps."""

def probe(target_deps: str) -> list[Layout]:
    """Run nacre::dep_catalog over every `.rlib` in `target_deps`."""

def resolve(
    crates: list[tuple[str, str]],
    auto_fetch: bool,
) -> ResolveResult:
    """Report cache status per (name, version); optionally fetch missing."""

def rustc_version() -> str:
    """Rustc release string carpace was built against."""
