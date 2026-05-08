# Cross-check a candidate Rust signature against the function actually
# present at an address in a binja BinaryView.
#
# Source side: nacre.signature(decl) - rustc's own FnAbi, authoritative
# for PassMode / sret / register assignment.
# Binary side: exoskeleton.trace_signature_bv(bv, addr) - per-reg used
# flag, sret heuristic, and rax/sret return-value classification.
#
# The result is a flat, agent-friendly dict (or dataclass): per-slot
# agreement, an overall score, and a short list of specific issues.
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import nacre
import exoskeleton

# Surface rustc diagnostics to agent-facing failures. See cli.py docstring.
from cli import with_compiler_errors


@dataclass
class SlotCheck:
    """Agreement between one nacre slot (expected) and the binary observation."""
    name: str                       # "arg0".."argN" or "ret"
    expected_regs: list[str]
    expected_pass_mode: str
    observed_regs: list[str]        # subset of arg regs that were `used`
    agree: bool
    note: str = ""                  # single-line explanation when agree=False
    # Offsets (deref'd through the primary reg) the source-side type
    # exposes vs what the binary actually touched. Sorted ascending.
    # Empty for non-pointer args / on-stack args.
    expected_offsets: list[int] = field(default_factory=list)
    observed_offsets: list[int] = field(default_factory=list)
    # Total byte-size of the expected pointee. observed_offsets exceeding
    # this value mean the agent's struct is too small for what the binary
    # touched - concrete out-of-bounds, not just a missing field.
    expected_size: int = 0


@dataclass
class SignatureCheck:
    function_addr: int
    function_name: str
    decl: str                       # the Rust decl that was checked
    arity_match: bool
    sret_match: bool
    return_match: bool              # observed ret semantics line up with nacre's
    slots: list[SlotCheck] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)

    @property
    def score(self) -> float:
        if not self.slots:
            return 0.0
        weight = sum(1 for _ in self.slots) + 1   # +1 for sret/return bundle
        hits = sum(1 for s in self.slots if s.agree) \
               + (1 if (self.sret_match and self.return_match) else 0)
        return hits / weight

    @property
    def perfect(self) -> bool:
        return (self.arity_match and self.sret_match and self.return_match
                and all(s.agree for s in self.slots))

    def summary(self) -> str:
        if self.perfect:
            return f"{self.function_name}: signature MATCHES `{self.decl}`"
        head = f"{self.function_name}: {self.score:.2f} fit on `{self.decl}`"
        if self.issues:
            return head + "\n  - " + "\n  - ".join(self.issues)
        return head

    def to_dict(self) -> dict:
        return {
            "function_addr": self.function_addr,
            "function_name": self.function_name,
            "decl": self.decl,
            "arity_match": self.arity_match,
            "sret_match": self.sret_match,
            "return_match": self.return_match,
            "score": round(self.score, 3),
            "perfect": self.perfect,
            "issues": list(self.issues),
            "slots": [dataclasses.asdict(s) for s in self.slots],
        }


# --- helpers -------------------------------------------------------------

def _used_regs(observed_args: list[dict]) -> list[str]:
    return [a["register"] for a in observed_args if a["used"]]


def _unwrap_outer_ptr(tree):
    """Strip nacre's outer 8-byte pointer wrapper(s) so the resulting
    top-level matches exoskeleton's already-flat per-offset trace.

    Nacre returns the access tree of `&T` as `[{offset:0, size:8,
    children: [<T's fields>]}]` - the &-ref is itself a ptr node and
    T's fields hang off as children. Exoskeleton on the binary side
    delivers the per-offset list deref'd directly through the arg
    register, i.e. `[<T's fields>]` flat. Unwrap so they're at the
    same depth before comparing offsets / coverage.

    Iterates: nested `&&T` etc. unwrap until the top isn't a single
    8B pointer-with-children node.
    """
    while (
        isinstance(tree, list)
        and len(tree) == 1
        and isinstance(tree[0], dict)
        and int(tree[0].get("size") or 0) == 8
        and tree[0].get("children")
    ):
        tree = tree[0]["children"]
    return tree or []


def _flatten_offsets(nodes) -> list[int]:
    """Top-level offsets in an access tree (one level only - children
    are sub-fields of pointer derefs, not the same arg's primary deref
    surface). Returned sorted, deduplicated."""
    if not nodes:
        return []
    seen: set[int] = set()
    for n in nodes:
        try:
            seen.add(int(n["offset"]))
        except (KeyError, TypeError):
            continue
    return sorted(seen)


def _expected_coverage(access_tree) -> int:
    """The max byte extent the source-side type's access tree covers
    at its top level. Used as the bounds reference for OOB checks: any
    observed offset >= this is concretely outside the type's reach.
    For pointer args this is the POINTEE's coverage (since access_tree
    is the deref surface), not the 8B pointer itself."""
    if not access_tree:
        return 0
    extent = 0
    for n in access_tree:
        try:
            off = int(n.get("offset") or 0)
            sz = int(n.get("size") or 0)
        except (TypeError, ValueError):
            continue
        end = off + max(sz, 1)
        if end > extent:
            extent = end
    return extent


def _layout_compatible(observed_arg: dict, expected: dict) -> tuple[bool, str]:
    """Layout check between source-side expected access tree and the
    binary's observed access tree at the primary register's deref
    surface (top-level offsets only).

    Hard signal: any observed offset >= the expected coverage extent
    means the agent's declared type is concretely too small for what
    the binary touched. Computed from access_tree (pointee surface),
    not from `size` (which is the pointer's own 8 bytes for refs).
    """
    observed_top = (observed_arg or {}).get("trace") or []
    expected_tree = _unwrap_outer_ptr((expected or {}).get("access_tree") or [])
    if not observed_top or not expected_tree:
        return True, ""
    coverage = _expected_coverage(expected_tree)
    if coverage <= 0:
        return True, ""
    out_of_bounds = [
        int(n["offset"]) for n in observed_top
        if "offset" in n and int(n["offset"]) >= coverage
    ]
    if out_of_bounds:
        ofs = ", ".join(f"{o:#x}" for o in sorted(out_of_bounds))
        return False, (
            f"observed offsets {ofs} fall past the declared type's "
            f"deref-surface extent of {coverage:#x} bytes - the type "
            "is too small for what the binary touched"
        )
    return True, ""


def _arg_slot_check(
    i: int,
    expected: dict,
    observed_args: list[dict],
) -> SlotCheck:
    expected_regs = list(expected["regs"])
    observed_regs = _used_regs(observed_args)
    # Arg agrees when every register nacre claims it lands on is actually
    # observed as `used` on the binary side. We don't require the *only*
    # used set to match because callers may have spilled extra regs.
    missing = [r for r in expected_regs if r not in observed_regs]
    agree = expected_regs and not missing
    note = ""
    if not expected_regs and expected["on_stack"]:
        # On-stack args are out of scope for the per-reg check; treat as
        # agreement (we don't observe stack args).
        agree = True
        note = "stack-passed (not observed)"
    elif not agree:
        note = f"expected {expected_regs} used, missing {missing}"
    elif expected_regs:
        # Reg assignment matches - now verify the candidate TYPE actually
        # accounts for what the binary derefs through the primary reg.
        primary = expected_regs[0]
        primary_obs = next((a for a in observed_args if a["register"] == primary), None)
        if primary_obs is not None:
            ok, layout_note = _layout_compatible(primary_obs, expected)
            if not ok:
                agree = False
                note = layout_note
    # Always populate offset lists (even when agree=True) so the agent
    # can see expected vs observed at a glance.
    primary_obs_for_offsets = None
    if expected_regs:
        primary_obs_for_offsets = next(
            (a for a in observed_args if a["register"] == expected_regs[0]),
            None,
        )
    expected_tree = _unwrap_outer_ptr(expected.get("access_tree") or [])
    expected_offsets = _flatten_offsets(expected_tree)
    observed_offsets = _flatten_offsets(
        (primary_obs_for_offsets or {}).get("trace") or []
    )
    # Coverage of the deref surface for pointer args; falls back to the
    # arg's own size for scalars / by-value structs. This is the value
    # used by the OOB check, surfaced verbatim so the agent can see how
    # far their declared type reaches.
    coverage = _expected_coverage(expected_tree) or int(expected.get("size") or 0)
    return SlotCheck(
        name=f"arg{i}",
        expected_regs=expected_regs,
        expected_pass_mode=expected["pass_mode"],
        observed_regs=observed_regs,
        agree=bool(agree),
        note=note,
        expected_offsets=expected_offsets,
        observed_offsets=observed_offsets,
        expected_size=coverage,
    )


def _return_check(observed_ret: dict, sret: bool) -> tuple[bool, bool, str]:
    """Returns (sret_match, return_match, note).

    Only the *shape* of the return - sret vs non-sret - is checked
    strictly. The ptr/scalar verdict counts on rax-write sites are
    informational only: the per-site classifier is too noisy for a
    clean go/no-go check (intermediate rax stomps in `()` returns,
    mis-classified single sites for trivial scalars). Callers wanting
    a soft signal can read the raw counts from `observed_ret`.
    """
    via = observed_ret["via"]
    if sret:
        sret_match = via == "sret"
        return_match = sret_match and bool(observed_ret.get("access_tree"))
        note = "" if return_match else \
            f"nacre says sret but observed via={via}, tree_nodes={len(observed_ret.get('access_tree', []))}"
        return sret_match, return_match, note

    # Non-sret. The binary shouldn't look sret-shaped; that's the only
    # hard requirement. Return value flavor is too noisy to enforce.
    sret_match = via != "sret"
    if not sret_match:
        return False, False, "nacre says non-sret but observed sret-shaped"
    return True, True, ""


# --- main entry point ----------------------------------------------------

def _resolve_bv(bv_or_path):
    if isinstance(bv_or_path, (str, Path)):
        import binaryninja as bn
        return bn.load(str(bv_or_path)), True
    return bv_or_path, False


_SELF_RE = __import__("re").compile(r"\bself\b")
_BLANK_LINE_RE = __import__("re").compile(r"\n\s*\n")
# Reject placeholder zero-field structs in the prelude - those are the
# canonical reward-hack pattern (no fields ⇒ trivially "matches" any
# observed deref since there's nothing to disagree with).
_EMPTY_STRUCT_NAME_RE = __import__("re").compile(
    r"""
    \bpub\s+struct\s+(\w+)      # capture the struct name
    (?:\s*<[^>]*>)?             # optional generics
    \s*
    (?:
        ;                       # `pub struct Name;`            (unit)
      | \{\s*\}                 # `pub struct Name {}`          (zero-field)
      | \(\s*\)\s*;             # `pub struct Name();`          (unit tuple)
    )
    """,
    __import__("re").VERBOSE,
)


def _lint_types(types: str, decl: str) -> str | None:
    """Catch the empty-placeholder cheese: a `pub struct X;` (or
    equivalent) in the prelude AND `X` named in the signature as a
    pointer / reference target. Both halves matter - legitimate unit
    structs (ZST marker types, trait-impl witnesses) are fine when not
    passed through a pointer.

    Returns a rejection message when both halves hit, else None.
    """
    if not types:
        return None
    empties = [m.group(1) for m in _EMPTY_STRUCT_NAME_RE.finditer(types)]
    if not empties:
        return None
    import re as _re
    referenced = [
        n for n in empties
        if _re.search(rf"\b{_re.escape(n)}\b", decl or "")
    ]
    if not referenced:
        return None
    names = ", ".join(f"`{n}`" for n in referenced)
    return (f"signature references zero-field placeholder struct(s) "
            f"({names}). That's not type recovery - declare the actual "
            f"fields the binary derefs through, even as raw `*mut u8` "
            f"/ `usize` if you can't yet identify the high-level Rust "
            f"types.")


# Field-name patterns that smell like raw-offset reconstruction rather
# than type recovery: `f30`, `f38`, `field0`, `field_8`, `_pad0`, `_unk1`,
# `_30`, etc. Used by `_warn_offset_named_fields` to soft-flag (NOT
# reject) decls that hit `perfect=True` only because their fields were
# enumerated by offset to match the binary byte-for-byte.
_OFFSET_NAMED_FIELD_RE = __import__("re").compile(
    r"""
    \b
    (?:
        f[0-9a-fA-F]{1,4}        # f0, f30, f1A0
      | field[_]?\d+             # field1, field_8
      | _?pad\d*                 # _pad, _pad0, pad1
      | _?unk\w*                 # _unk, unk5
      | _\d+                     # _0, _16
      | [a-z]\d+                 # p1, p2, s1, s2, q9 - short letter+digit
                                 # positional naming. Flags real fields too
                                 # (e.g. `x1`), but in the context of a struct
                                 # majority-vote it's a strong cheese signal.
      | _[a-z]                   # _a, _b, _c - single-letter underscore
                                 # placeholders the agent reaches for when
                                 # papering over bytes.
    )
    \s*:                         # field-decl colon
    """,
    __import__("re").VERBOSE,
)
_STRUCT_FIELDS_RE = __import__("re").compile(
    r"""
    \bstruct\s+\w+                # struct Foo
    (?:\s*<[^>]*>)?               # optional generics
    \s*\{([^{}]*)\}               # body braces (single-level only - fine
                                  # for the common case; nested braces
                                  # would need a real parser).
    """,
    __import__("re").VERBOSE,
)
# `_pad: [u64; 6]`, `_skipped: [u8; 16]`, `_reserved: [u8; 32]` - array
# fields with skip-y names that paper over a chunk of bytes the agent
# didn't recognize. Even ONE of these in a struct is suspicious; it's
# usually where a Vec/String/HashMap should have gone.
_SKIP_ARRAY_RE = __import__("re").compile(
    r"""
    \b
    # Named like a placeholder: `_pad`, `_skip`, `_reserved`, `_unknown`,
    # OR any underscore-prefixed short identifier (`_a`, `_b`, `_x_chunk`)
    # - agents use these to paper over bytes they didn't recognize.
    (?:
        _?pad\w*
      | _?skip\w*
      | _?reserved\w*
      | _?unknown\w*
      | _[a-zA-Z]\w*
    )
    \s*:\s*
    \[\s*[ui][0-9]+\s*;\s*(?:0[xX][0-9a-fA-F]+|\d+)\s*\]
    """,
    __import__("re").VERBOSE,
)


# Decomposed-wrapper detection (generic - not pinned to specific
# stdlib types). When an agent matches the binary byte-for-byte by
# inlining a wrapper's bytes, three structural tells appear:
#
#   1. Raw pointers as struct fields. Idiomatic Rust uses
#      `&T`/`Box<T>`/`Rc<T>`/`Arc<T>`/`Vec<T>`/etc. - `*const T`/`*mut T`
#      in a NAMED-field struct is a code smell almost everywhere except
#      FFI shim types. Two or more raw ptrs ≈ multiple wrappers inlined.
#   2. Pointer + companion-size triples. `(data/ptr/buf, cap/capacity,
#      len/length)` is the universal Vec/String/slice shape. Even when
#      the names are prefixed (`keys_data, keys_cap, keys_len`), the
#      stem-grouped triple gives it away.
#   3. Pointer + single size field. A `(ptr, len)` pair is `&[T]`/`&str`
#      decomposed.
#
# These are STRUCTURAL - they don't care whether the wrapper is from
# the stdlib, hashbrown, smallvec, or a third-party crate.
_RAW_PTR_TYPE_RE = __import__("re").compile(r"^\s*\*\s*(?:const|mut)\b")
_INT_TYPE_RE = __import__("re").compile(
    r"^\s*(?:[ui](?:8|16|32|64|128|size)|c_(?:u?int|u?long|u?short|u?char))\s*$"
)
# Names typically used for buffer-shaped fields. Includes general-CS
# vocabulary (ctrl/head/tail/root/table/base) plus the obvious
# pointer-y stems. Generic - these stems show up across stdlib types,
# hashbrown, slabs, arenas, linked lists, BTrees, ring buffers.
_BUFFER_NAME_RE = __import__("re").compile(
    r"^(?:.*_)?(?:"
    r"data|ptr|buf|buffer|array|elements?|"
    r"ctrl|table|head|tail|root|base|"
    r"items_ptr"
    r")$"
)
# Names typically used for size/index/state fields. Same generality
# rationale as the buffer-name list.
_SIZE_NAME_RE = __import__("re").compile(
    r"^(?:.*_)?(?:"
    r"len|length|cap|capacity|count|size|n|"
    r"mask|items?|buckets?|slots?|growth_?(?:left|right|free)?|"
    r"used|free|occupied|index|idx|offset"
    r")$"
)


def _decomposed_wrapper_signature(fields: list[tuple[str, str]]) -> str | None:
    """Return a short hint string when `fields` (list of (name, type)
    pairs) looks like one or more wrappers spelled out as primitives.
    Returns None when the struct passes.

    Generic - keys off STRUCTURAL features (raw-pointer count, integer
    companions, buffer/size name stems) instead of any wrapper-specific
    field-name list. Works equally well on Vec, HashMap (hashbrown
    RawTableInner), SmallVec, BTreeMap nodes, or third-party crate
    internals an agent stumbles into.
    """
    raw_ptrs = [n for n, t in fields if _RAW_PTR_TYPE_RE.match(t)]
    int_companions = [n for n, t in fields if _INT_TYPE_RE.match(t)]
    buffer_names = [n for n, _ in fields if _BUFFER_NAME_RE.match(n)]
    size_names = [n for n, _ in fields if _SIZE_NAME_RE.match(n)]

    # (1) Multiple raw pointers - agent likely turned each
    # &T/Box<T>/Vec<T>/Rc<T> into a `*const T`/`*mut T`. Single raw ptr
    # is sometimes legitimate (FFI handle, fd, vtable) so we don't fire
    # on count==1 alone.
    if len(raw_ptrs) >= 2:
        return (
            f"{len(raw_ptrs)} raw-pointer fields ({', '.join(raw_ptrs[:4])}"
            f"{'...' if len(raw_ptrs) > 4 else ''}) - idiomatic Rust uses "
            "&T/Box<T>/Rc<T>/Arc<T>/Vec<T> instead"
        )

    # (2) Raw pointer + ≥2 scalar-int companions: classic shape of a
    # wrapper (ptr + metadata) inlined. Catches hashbrown's
    # RawTableInner (ctrl + bucket_mask + growth_left + items),
    # RawVec (ptr + cap + len), Bump-style arenas, custom slabs, etc.
    if raw_ptrs and len(int_companions) >= 2:
        return (
            f"raw pointer `{raw_ptrs[0]}` plus {len(int_companions)} integer "
            f"fields ({', '.join(int_companions[:3])}"
            f"{'...' if len(int_companions) > 3 else ''}) - looks like a "
            "wrapper (Vec, HashMap, SmallVec, ...) decomposed into its bytes"
        )

    # (3) Buffer+size NAMED-stem grouping when types alone don't tell -
    # `keys_data + keys_cap + keys_len` triple even if keys_data was
    # typed as something other than a raw ptr.
    if buffer_names and len(size_names) >= 2:
        return (
            f"buffer+size fields ({buffer_names[0]} + "
            f"{', '.join(size_names[:2])}) - looks like a Vec/String/&[T] "
            "decomposed into raw fields"
        )

    # (4) (ptr-named, len-named) pair = fat slice / &str inlined.
    if buffer_names and size_names:
        return (
            f"({buffer_names[0]}, {size_names[0]}) pair - probably "
            "`&[T]`/`&str`/`Box<[T]>` decomposed; try the wrapper"
        )

    return None


def _warn_offset_named_fields(types: str) -> str | None:
    """Soft warning for layout-match-not-type-recovery patterns:

      A. Most fields named by offset (`f30`, `_pad0`, `field5`, `_8`).
      B. Any `_pad: [u64; N]` / `_skipped: [u8; M]` skip-arrays - those
         paper over bytes the agent didn't recognize. If the binary
         touches them, real fields are missing; if it doesn't, a
         wrapper type (Vec/String/HashMap/...) covers the same bytes
         with semantic content.
      C. Spelled-out stdlib internals - fields named like
         hashbrown::RawTableInner (`ctrl, bucket_mask, growth_left,
         items, k0, k1, ...`) or RawVec (`ptr/data, cap/capacity,
         len/length`). These match the binary byte-for-byte because
         they ARE the wrapper's guts; submit the wrapper instead.

    Returns a warning string callers attach to SignatureCheck.issues,
    or None if the types look clean.
    """
    if not types:
        return None
    suspect: list[str] = []
    for body_m in _STRUCT_FIELDS_RE.finditer(types):
        body = body_m.group(1) or ""
        all_fields = [
            ln.strip() for ln in body.split(",") if ":" in ln
        ]
        # (A) majority-or-3+ offset-named fields.
        if len(all_fields) >= 3:
            offset_named = sum(
                1 for f in all_fields if _OFFSET_NAMED_FIELD_RE.search(f)
            )
            if offset_named >= max(3, len(all_fields) // 2 + 1):
                suspect.append(
                    f"{offset_named}/{len(all_fields)} fields are offset-named"
                )
        # (B) skip-arrays of size > 1 in any struct.
        for f in all_fields:
            if _SKIP_ARRAY_RE.search(f):
                suspect.append(f"skip-array field `{f.split(':')[0].strip()}`")
                break  # one is enough to flag this struct
        # (C) decomposed-wrapper detection (raw-pointer density,
        # buffer+size groupings). Generic - not pinned to any specific
        # wrapper; keys off field-name shape + raw-pointer types.
        nt: list[tuple[str, str]] = []
        for f in all_fields:
            head, _, tail = f.partition(":")
            n = head.strip()
            for pfx in ("pub ", "pub(crate) ", "pub(super) "):
                if n.startswith(pfx):
                    n = n[len(pfx):].strip()
            t = tail.strip()
            if n:
                nt.append((n, t))
        hit = _decomposed_wrapper_signature(nt)
        if hit:
            suspect.append(hit)
    if not suspect:
        return None
    return ("types contain layout-match-not-type-recovery patterns ("
            + "; ".join(suspect) + "). The harness validates layout, "
            "not semantic correctness - these structs match the binary "
            "byte-for-byte but tell the user nothing about the source. "
            "Recover real types: Vec<T> / String / HashMap<K,V> / "
            "Option<T> / Result<T,E> / &[T] all compile to specific "
            "layouts via rustc; if a chunk of bytes you don't yet "
            "recognize is exactly 24B, try Vec<u8> / String first; "
            "if it's 48B, try HashMap<K, V>. Don't paper over with "
            "`_pad: [u64; N]` arrays, and DON'T spell out the wrapper's "
            "internals (`ctrl/bucket_mask/items` IS HashMap; "
            "`ptr/cap/len` IS Vec).")


def split_decl(s: str) -> tuple[str, str]:
    """Split a Rust signature string into `(prelude, decl)`.

    Convention: everything up to the LAST blank line is prelude (use
    lines, struct/enum/type/trait defs, possibly with blank lines BETWEEN
    items); the final block is the decl (parens param list with
    optional return type). If the input has no blank line, the entire
    string is the decl and there's no prelude.

    Why last-blank-line and not first: multi-item preludes naturally
    contain blank lines between items, e.g.

        use std::collections::HashMap;          <-- blank line here

        pub struct Foo { x: u64 }               <-- blank line here

        (h: &mut HashMap<String, Foo>, k: &str) -> Option<Foo>

    A first-blank-line split would put only `use ...;` in prelude and
    leak the struct def into the decl, where nacre's `normalize_fn_decl`
    strips everything before the first `(` - silently losing the type
    and yielding `cannot find type Foo in this scope` from rustc.
    """
    s = s.strip()
    parts = _BLANK_LINE_RE.split(s)
    if len(parts) == 1:
        return "", s
    return "\n\n".join(p.strip() for p in parts[:-1]).strip(), parts[-1].strip()


def check_signature(
    bv_or_path: Any,
    addr: int,
    decl: str,
    *,
    prelude: str | None = None,
    target: str | None = None,
) -> SignatureCheck:
    """Compare `decl` against the function at `addr` in the given BV.

    `bv_or_path`: a `binaryninja.BinaryView` or a path the function loads.
    `decl`: a Rust function declaration. May optionally be prefixed with
    a prelude block (use/struct/etc.) separated from the decl by a
    blank line - see `split_decl`. Inline prelude is concatenated AFTER
    any caller-supplied `prelude=` so agent-side imports take priority.
    """
    # Pull any inline prelude out of the decl string. Agents are
    # encouraged to write self-contained snippets; the explicit
    # `prelude=` arg is a fallback for harness-side static defs.
    inline_prelude, decl = split_decl(decl)
    full_prelude = "\n".join(p for p in (prelude or "", inline_prelude) if p).strip() or None

    # Reject placeholder structs referenced by the signature - see _lint_types.
    lint = _lint_types(full_prelude or "", decl)
    if lint:
        raise ValueError(lint)
    # Soft warning for offset-named reconstruction (informational only).
    soft_warning = _warn_offset_named_fields(full_prelude or "")

    # Nacre wraps `decl` as a free fn (`pub fn __nacre_sig_probe(...)`),
    # so `self`-style receivers won't compile. Bounce with a clear hint
    # - the wire ABI is identical for `&self`/`(this: &T)`.
    if _SELF_RE.search(decl):
        raise ValueError(
            "decl uses `self`; nacre compiles it as a free fn so `self` "
            "isn't allowed. Rewrite `&self` -> `(this: &T)`, "
            "`&mut self` -> `(this: &mut T)`, `self` -> `(this: T)` where "
            "T is the receiver type (e.g. `RawVec<usize>`). The ABI is "
            "identical."
        )

    bv, opened = _resolve_bv(bv_or_path)
    try:
        observed = exoskeleton.trace_signature_bv(bv, addr)
    finally:
        if opened:
            bv.file.close()
    expected = with_compiler_errors(
        nacre.signature, decl, prelude=full_prelude, target=target,
    )

    issues: list[str] = []
    slots: list[SlotCheck] = []
    for i, exp_arg in enumerate(expected["args"]):
        slot = _arg_slot_check(i, exp_arg, observed["args"])
        slots.append(slot)
        if not slot.agree and slot.note:
            issues.append(f"arg{i} {exp_arg['ty']}: {slot.note}")

    # Arity check: every non-stack expected arg should have at least one
    # used reg on the binary side. We don't penalize *extra* used regs
    # (a calling convention may consume scratch regs unrelated to args).
    expected_arg_count = sum(1 for a in expected["args"] if not a["on_stack"])
    observed_used_count = sum(1 for a in observed["args"] if a["used"])
    arity_match = observed_used_count >= expected_arg_count
    if not arity_match:
        issues.append(
            f"arity: nacre expects {expected_arg_count} reg-args, "
            f"observed {observed_used_count} used"
        )

    # Sret + return.
    sret_match, return_match, ret_note = _return_check(
        observed["ret"], expected["sret"])
    if not return_match and ret_note:
        issues.append(f"return: {ret_note}")

    # Build a synthetic "ret" slot for the report so callers can see it.
    slots.append(SlotCheck(
        name="ret",
        expected_regs=list(expected["ret"]["regs"]),
        expected_pass_mode=expected["ret"]["pass_mode"],
        observed_regs=[observed["ret"]["via"]] if observed["ret"]["via"] != "none" else [],
        agree=return_match,
        note=ret_note,
    ))

    # Append the offset-named-fields warning as an issue. This does NOT
    # affect `perfect` / `score` (the reg-assignment + sret check is
    # legitimately green) but the agent and downstream consumers see it.
    if soft_warning:
        issues.append(f"warning: {soft_warning}")

    return SignatureCheck(
        function_addr=addr,
        function_name=observed.get("function_name") or "",
        decl=decl,
        arity_match=arity_match,
        sret_match=sret_match,
        return_match=return_match,
        slots=slots,
        issues=issues,
    )


# --- batch helper --------------------------------------------------------

def check_many(
    bv_or_path: Any,
    cases: Iterable[tuple[int, str]],
    *,
    prelude: str | None = None,
) -> list[SignatureCheck]:
    """Run `check_signature` against each (addr, decl) pair, sharing one BV
    load. Useful when probing the same binary with many candidate decls."""
    bv, opened = _resolve_bv(bv_or_path)
    try:
        return [check_signature(bv, addr, decl, prelude=prelude) for addr, decl in cases]
    finally:
        if opened:
            bv.file.close()
