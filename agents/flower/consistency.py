# Validator: rustc-compile (lymph) + binary dataflow (anemone) +
# cross-graph compatibility check by var-name.
from __future__ import annotations

import re
from dataclasses import dataclass, field

import lymph
import anemone

from cli import with_compiler_errors


# Strip Rust ABI hash like ::h<16-hex> appended to demangled symbols.
_RUST_HASH_RE = re.compile(r"::h[0-9a-f]{16}$")


def clean_fn_name(symbol_short_name: str) -> str:
    """`source::State::jump::hc4be...` -> `jump`; hash-stripped leaf."""
    s = _RUST_HASH_RE.sub("", symbol_short_name or "")
    if "::" in s:
        s = s.rsplit("::", 1)[-1]
    return s or symbol_short_name


_ALLOW_WHITELIST = {"dead_code"}


def _bad_inner_allow(source: str) -> list[str]:
    """File-level `#![allow(...)]` lints not in whitelist."""
    import re
    bad: list[str] = []
    for m in re.finditer(r"#!\[allow\(([^)]+)\)\]", source):
        for lint in m.group(1).split(","):
            lint = lint.strip()
            if lint and lint not in _ALLOW_WHITELIST:
                bad.append(lint)
    return bad


def _is_trivial_body(rust_source: str, rust_fn_name: str,
                    bin_block_count: int) -> tuple[bool, str]:
    """Cheese-body detector: empty fn, or high-density `let _` binding."""
    import re
    m = re.search(r"\bfn\s+" + re.escape(rust_fn_name)
                  + r"\s*[^{]*\{(.*)\}", rust_source, re.DOTALL)
    if not m:
        return False, ""
    body = m.group(1).strip()
    stmts = [s.strip() for s in body.split(";") if s.strip()]
    stmts = [s for s in stmts if not s.startswith("//")]
    if not stmts and bin_block_count > 4:
        return True, (
            f"submission rejected: empty fn body but the binary has "
            f"{bin_block_count} basic blocks. Reconstruct the actual "
            f"logic."
        )
    # Multi-arg `let _ = (X, Y, ...)` cheeses binding-check: counts as
    # used, dataflow sees no flow. Reject tuples >=2 idents.
    multi_underscore_re = re.compile(
        r"let\s+_\s*(?::\s*[^=]+)?=\s*\(\s*([A-Za-z_][\w]*\s*"
        r"(?:,\s*[A-Za-z_][\w]*\s*){1,})\)\s*"
    )
    bad_tuples: list[str] = []
    for m in multi_underscore_re.finditer(body):
        idents = [s.strip() for s in m.group(1).split(",") if s.strip()]
        if len(idents) >= 2:
            bad_tuples.append("(" + ", ".join(idents) + ")")
    if bad_tuples and bin_block_count > 4:
        return True, (
            f"submission rejected: {len(bad_tuples)} multi-arg "
            f"`let _ = (X, Y, ...)` discard(s) detected: "
            f"{'; '.join(bad_tuples[:3])}. That binds the HLIL var "
            f"names without using them - dataflow trivially passes "
            f"because nothing flows. Use those vars in real "
            f"computation; if any are truly unused, drop them from "
            f"the signature."
        )
    # Stub-body trap: any `let _ = ...` cheese paired with a too-small
    # body for the binary's complexity. Catches both the high-density
    # case (5x let_) and the single-line-cheese case
    # (`let result = ...; let _ = ch; result` for a 558-BB fn).
    let_underscore = sum(1 for s in stmts if s.startswith("let _ ="))
    if let_underscore >= 1 and bin_block_count > 30 and len(stmts) < 5:
        return True, (
            f"submission rejected: body has only {len(stmts)} statement(s) "
            f"with {let_underscore} `let _ = ...` discard(s) but the binary "
            f"has {bin_block_count} basic blocks. That's a stub - args "
            f"mentioned but never flowed. Use the signer-recovered struct "
            f"fields in real computation; one match arm or field read on "
            f"a real path beats `let _ = ...` discards."
        )
    if (let_underscore >= 3
            and let_underscore >= len(stmts) // 2
            and bin_block_count > 8):
        return True, (
            f"submission rejected: {let_underscore}/{len(stmts)} "
            f"statements are `let _ = ...`. The body is binding "
            f"theater - args mentioned but never flowed."
        )
    return False, ""


@dataclass
class CheckResult:
    perfect: bool
    feedback: str
    has_warnings: bool = False
    # Diffs ordered: return/args first, then intermediates (chained from args).
    diffs_ordered: list[str] = field(default_factory=list)
    # Vars in the Rust source that aren't bound to any HLIL var.
    unbound: list[str] = field(default_factory=list)
    rust_var_count: int = 0
    binary_var_count: int = 0


def check(
    rust_source: str,
    *,
    bv,
    fn_addr: int,
    rust_fn_name: str,
    region: tuple[int, int] | list[int] | None = None,
) -> CheckResult:
    """Verify `rust_source` compiles and its dataflow matches the binary
    fn at `fn_addr`. Var binding is by name: every Rust var must be
    named after an HLIL var (`arg1`, `var_28`, etc.) so the mapping
    `rust_name -> il_name` is the identity intersection.

    `region` scopes the binary lowering to a subset of basic blocks:
      - `(block_start, block_end)` tuple: contiguous range
      - `list[int]`: arbitrary BB indices, may be non-contiguous (use
        for inlined fn bodies that lower to scattered blocks)
    """
    # 0a. Bug 5: reject `#![allow(...)]` attribute spam unless it's
    # only `dead_code` (the one allow that's legitimately useful in
    # signature-only stubs). Agents were silencing every warning as
    # a workaround instead of fixing the underlying issue.
    bad = _bad_inner_allow(rust_source)
    if bad:
        return CheckResult(False, (
            f"submission rejected: `#![allow({', '.join(bad)})]` papers "
            f"over warnings instead of fixing them. Only `dead_code` is "
            f"permitted at the inner-attribute level. Remove the allow "
            f"and address the underlying warning (rename unused vars "
            f"with leading `_`, drop the unused mut, etc)."
        ), False)
    # Enforce safe-Rust contract on the MAIN fn: no `unsafe fn`, no
    # `extern "C"` qualifier, no raw-pointer params/return in its
    # signature. Signer already gave a safe prototype; flower must
    # use it verbatim. Opaque callees should be declared as safe fn
    # stubs with `unimplemented!()` bodies, not pulled in via extern.
    import re as _re
    main_fn_re = _re.compile(
        r"(?:pub\s+)?(?:(unsafe)\s+)?(?:(extern\s+\"[^\"]+\")\s+)?"
        r"fn\s+" + _re.escape(rust_fn_name) + r"\s*\(([^)]*)\)([^{]*)\{",
        _re.DOTALL,
    )
    mf = main_fn_re.search(rust_source)
    if mf:
        is_unsafe, is_extern, params, ret = mf.groups()
        if is_unsafe:
            return CheckResult(False,
                f"submission rejected: `unsafe fn {rust_fn_name}` - flower "
                "submits must be safe Rust. Drop the `unsafe` keyword and "
                "move any unsafe-needing work into typed helpers with "
                "`unimplemented!()` bodies.", False)
        if is_extern:
            return CheckResult(False,
                f"submission rejected: `extern \"C\" fn {rust_fn_name}` "
                "- the main fn must be plain `fn`, not extern. Signer's "
                "recovered prototype is already safe Rust; copy it "
                "verbatim and drop the extern qualifier.", False)
        sig = (params or "") + " " + (ret or "")
        if "*mut " in sig or "*const " in sig:
            return CheckResult(False,
                f"submission rejected: main fn `{rust_fn_name}` signature "
                "has raw pointers (`*mut` / `*const`). Use signer's "
                "high-level types verbatim (`&Foo`, `&mut Foo`, `Vec<T>`, "
                "etc). Raw pointers belong inside opaque-callee stubs, "
                "not the public prototype.", False)
    # 1. Compile via lymph; with_compiler_errors captures rustc stderr
    #    and appends it to the exception so the agent sees real
    #    diagnostics instead of "rustc driver aborted".
    try:
        rust_graphs = with_compiler_errors(lymph.analyze, rust_source)
    except Exception as e:
        return CheckResult(False, f"rustc rejected the source:\n{e}", False)
    rust_g = next(
        (g for g in rust_graphs
         if g.fn_name == rust_fn_name or g.fn_name.endswith(f"::{rust_fn_name}")),
        None,
    )
    if rust_g is None:
        return CheckResult(
            False,
            f"compiled but no fn named {rust_fn_name!r} found in the source",
            False,
        )

    # 2. Lower the binary fn (worst-case at calls; depth=0). Either
    # the whole fn or a contiguous BB range, depending on `region`.
    if region is None:
        anem = anemone.analyze(bv, fn_addr)
    elif isinstance(region, tuple) and len(region) == 2:
        bs, be = int(region[0]), int(region[1])
        anem = anemone.analyze_region(bv, fn_addr, bs, be)
    else:
        ids = sorted({int(i) for i in region})
        anem = anemone.analyze_blocks(bv, fn_addr, ids)

    # 2a. Bug 6: reject trivial-body submissions for non-trivial fns.
    # `let _ = (...)` stubs trivially pass dataflow (no flow to compare)
    # but contribute nothing. For region-scoped checks we use the
    # region's BB count (smaller) so a 2-line region doesn't get
    # rejected for being short.
    f = bv.get_function_at(fn_addr) or next(
        iter(bv.get_functions_containing(fn_addr) or []), None,
    )
    if region is None:
        bb_count = len(list(f.basic_blocks)) if f else 0
    else:
        bb_count = max(0, int(region[1]) - int(region[0]))
    is_stub, stub_msg = _is_trivial_body(rust_source, rust_fn_name, bb_count)
    if is_stub:
        return CheckResult(False, stub_msg, False)

    # 3. Map by name. anemone uses SSA-versioned slot names (`s#0`,
    #    `var_28#1`); strip the version suffix. Skip lymph-internal:
    #      < prefix      <return>, <ret:callee>
    #      contains #    call-arg rendezvous (`callee#0`)
    #      contains (*)  Phase-1 ref-alias projections (`x.(*).f`)
    #    `_x` is exempt only when `x` is NOT a real HLIL var; otherwise
    #    `_x` is treated as a dodge - the agent renamed `x` to `_x` to
    #    skip a binding check. Bind it as `x` and surface a dodge warning.
    il_vars = {_unversioned(v) for v in anem.variables() if not v.startswith("<")}
    rust_vars = list(rust_g.variables())
    unbound: list[str] = []
    mapping: dict[str, str] = {}
    dodged: list[str] = []
    for rv in rust_vars:
        if rv.startswith("<") or "#" in rv or "(*)" in rv:
            continue
        if rv.startswith("_"):
            stripped = rv.lstrip("_")
            if stripped and stripped in il_vars:
                # Dodge: agent prefixed an HLIL var with `_` to skip binding.
                mapping[rv] = stripped
                dodged.append(f"{rv!r}->{stripped!r}")
            continue
        if rv in il_vars:
            mapping[rv] = rv
        else:
            unbound.append(rv)
    # The boundary `<return>` slot is the spec's HIGHEST-priority diff
    # ("start from return-value/arg relationships"). Both lymph and
    # anemone expose it under the same display name; include it in the
    # mapping so check_compatibility iterates `<return>` <-> every arg.
    if rust_g.return_slot and anem.return_slot:
        mapping["<return>"] = "<return>"
    if unbound:
        return CheckResult(
            False,
            "the following Rust variables aren't named after any HLIL var "
            "(rename them so each Rust local matches an HLIL local):\n  "
            + ", ".join(unbound),
            has_warnings=False,
            unbound=unbound,
            rust_var_count=len(rust_vars),
            binary_var_count=len(il_vars),
        )

    # 4. Cross-check dataflow. Classify each diff. `rust_over` (Rust
    #    adds flow not in the binary) is a real bug - bounces the
    #    submission. `binary_over` (binary has flow Rust doesn't) is
    #    almost always anemone's worst-case at opaque calls; surface
    #    as a warning, don't fail. When anemone gains call-graph
    #    recursion this will tighten and binary_over will rarely fire
    #    as false positive.
    rust_edges = list(rust_g.edges())
    _ok, raw_diffs = anemone.check_compatibility(rust_edges, anem, mapping)
    enriched = [_enrich_diff(d, rust_g, anem, mapping) for d in raw_diffs]
    diffs_ordered = _order_diffs(enriched, rust_g)
    has_rust_over = any("rust_over" in d for d in diffs_ordered)
    has_binary_over = any("binary_over" in d for d in diffs_ordered)
    has_missing_return = any("missing_return_flow" in d for d in diffs_ordered)
    cheese = _detect_cheese(rust_source)
    # Flower is the readability stage; perfect now just means
    # 'compiles + every ident binds to a real HLIL var' (the binding
    # check earlier already enforces unbound==[]). The dataflow
    # comparison is informational - rust_over/missing_return surface
    # in feedback but DO NOT gate `perfect`. Strict dataflow
    # rewarded stub bodies (empty body = no flow = trivially
    # compatible); the cheese detectors below are the real
    # stub-stoppers, not the dataflow check.
    # `dodged` = the agent prefixed a real HLIL var with `_` to evade
    # the binding check; that's an explicit evasion, kill perfect.
    perfect = not bool(dodged)
    has_warnings = bool(dodged) or bool(cheese)
    parts: list[str] = []
    if dodged:
        parts.append(
            f"{len(dodged)} variable(s) dodged the binding check "
            f"with a `_` prefix even though the unprefixed name IS a "
            f"real HLIL var. Treated as bound. Drop the underscore "
            f"and submit again so future iterations are clean: "
            + ", ".join(dodged)
        )
    if cheese:
        parts.append(
            f"{len(cheese)} antipattern(s) in the source - the prompt "
            f"warns against these because they paper over real fields "
            f"the signer stage already typed. Replace each with the "
            f"idiomatic equivalent (24B chunks => Vec<T>/String, 48B "
            f"=> HashMap, fat pointer pairs => &[T]/&str, etc.):\n  - "
            + "\n  - ".join(cheese)
        )
    if diffs_ordered:
        head = (
            f"{len(diffs_ordered)} dataflow disagreement(s) "
            f"({len(mapping)} vars bound). Boundary diffs first, then "
            f"intermediates by BFS depth from nearest arg. "
            f"`rust_over` = real bug to fix; `binary_over` = anemone's "
            f"opaque-call worst-case, surfaced as warning only:"
        )
        parts.append(head + "\n" + "\n".join(f"  - {d}" for d in diffs_ordered))
    if perfect and not parts:
        parts.append(f"perfect: {len(mapping)} vars bound, dataflow agrees")
    return CheckResult(
        perfect, "\n\n".join(parts), has_warnings,
        diffs_ordered=diffs_ordered,
        unbound=unbound,
        rust_var_count=len(rust_vars), binary_var_count=len(il_vars),
    )


_CHEESE_PATTERNS = (
    # `_pad: [u8; 0x30]`, `pad0: [u64; 6]` — opaque byte/word skip-arrays.
    (r"\b(?:_?pad\w*|_a\w*|_+)\s*:\s*\[\s*u(?:8|16|32|64|128)\s*;",
     "skip-array field papering over bytes (try Vec<T>/String/HashMap)"),
    # Offset-named scalar fields: `f30: u8`, `_8: u32`, `p1: *mut u8`.
    (r"\b(?:f\d+|_\d+|p[0-9]+|s[0-9]+)\s*:\s*[*&]?",
     "offset-named field (rename to the recovered semantic name)"),
)


def _detect_cheese(source: str) -> list[str]:
    """Surface antipattern field shapes from the prompt's warning list.
    These are not failures - they coexist with `has_warnings` so the
    submit hook bounces them on early attempts and accepts them on
    budget-exhaust, exactly the behavior the original spec had for
    has_warnings."""
    out: list[str] = []
    for pattern, msg in _CHEESE_PATTERNS:
        m = re.search(pattern, source)
        if m:
            sample = m.group(0)
            out.append(f"{sample.strip()}: {msg}")
    return out


def _unversioned(name: str) -> str:
    """`s#3` -> `s`; `<ret:fn_0x...>` and `var_e8.0#1` left alone past the `#`."""
    h = name.find("#")
    return name if h < 0 else name[:h]


def _enrich_diff(diff: str, rust_g, anem, mapping: dict) -> str:
    """Attach a short tag + path hint to a raw `x <- y: rust=R binary=B`
    line. Tags: `binary_over` (anemone's worst-case at opaque calls),
    `rust_over` (Rust source claims flow the binary doesn't have).
    Path hint: surface up to 4 intermediate slots on the side where
    the flow exists, so the agent can see WHICH callsite/intermediate
    glued them. `missing IL slot` lines pass through unchanged."""
    if "<-" not in diff or "rust=" not in diff:
        return diff
    head, rest = diff.split("<-", 1)
    x = head.strip()
    y = rest.split(":", 1)[0].strip()
    rust_true = "rust=true" in diff
    binary_true = "binary=true" in diff
    return_slot = rust_g.return_slot
    if binary_true and not rust_true:
        if return_slot and (x == return_slot or y == return_slot):
            # `<return>` ↔ arg missing flow: the binary's path from arg
            # to rax is generally precise (no opaque callees can sever
            # an arg from `<return>` without anemone seeing it), so
            # missing this means the Rust under-models the body.
            # Arg↔arg `binary_over` stays a warning - that path can
            # legitimately go through an opaque hash/index call.
            tag = ("missing_return_flow (binary connects an arg to "
                   "<return> but your Rust does not - usually a real "
                   "missed flow, not anemone over-approx)")
        else:
            tag = "binary_over (likely opaque-call worst-case; safe to ignore unless real)"
        hint = _path_hint(anem, mapping.get(y, y), mapping.get(x, x))
    elif rust_true and not binary_true:
        # Rust claims a flow the binary doesn't show — agent's source
        # has a stray data movement. Drop the offending edge.
        tag = "rust_over (your Rust adds data movement absent from the binary; remove it)"
        hint = _path_hint(rust_g, y, x)
    else:
        tag = ""
        hint = ""
    extra = f"  [{tag}]" if tag else ""
    if hint:
        extra += f"\n      via: {hint}"
    return diff + extra


def _path_hint(graph, src: str, dst: str, max_depth: int = 4, fanout: int = 16) -> str:
    """Bounded BFS in `graph` from `src` to `dst`; renders the first
    path found (up to `max_depth` hops, `fanout` per node). Both
    rust_g and anemone PyFlowGraph expose `successors(name)`."""
    from collections import deque
    if src == dst:
        return src
    parent: dict[str, str] = {src: ""}
    q: "deque[tuple[str, int]]" = deque([(src, 0)])
    while q:
        cur, d = q.popleft()
        if d >= max_depth:
            continue
        try:
            nexts = [s for (s, _k) in graph.successors(cur)][:fanout]
        except Exception:
            continue
        for n in nexts:
            if n in parent:
                continue
            parent[n] = cur
            if n == dst:
                # Reconstruct path src -> ... -> dst.
                path = [n]
                while parent[path[-1]]:
                    path.append(parent[path[-1]])
                return " -> ".join(reversed(path))
            q.append((n, d + 1))
    return f"{src} -> ... -> {dst} (>{max_depth} hops; trace via decompile)"


def _order_diffs(diffs: list[str], rust_g) -> list[str]:
    """Boundary first (return + args), then intermediates ordered by
    BFS depth from the nearest arg in the Rust graph. Per the spec:
    'start with return/args, then continue with intermediates from
    the arg outward'."""
    boundary = set(rust_g.params)
    if rust_g.return_slot:
        boundary.add(rust_g.return_slot)
    depth_of = _bfs_depths_from_args(rust_g)
    high, low = [], []
    for d in diffs:
        head = d.split("<-", 1)[0].strip()
        head = head.split()[0] if head else head
        if head in boundary:
            high.append((0, d))
        else:
            low.append((depth_of.get(head, 1_000_000), d))
    high.sort(key=lambda t: t[0])
    low.sort(key=lambda t: t[0])
    return [d for _, d in high] + [d for _, d in low]


def _bfs_depths_from_args(rust_g) -> dict[str, int]:
    """BFS over `successors` starting from every Rust arg simultaneously
    so each slot's depth is its closest distance to any arg."""
    from collections import deque
    seeds = list(rust_g.params)
    depths: dict[str, int] = {s: 0 for s in seeds}
    q: "deque[str]" = deque(seeds)
    while q:
        cur = q.popleft()
        d = depths[cur]
        try:
            nexts = [s for (s, _k) in rust_g.successors(cur)]
        except Exception:
            continue
        for n in nexts:
            if n not in depths:
                depths[n] = d + 1
                q.append(n)
    return depths
