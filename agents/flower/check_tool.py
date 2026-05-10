# Iterative-feedback tools for the flower agent. Three MCP tools:
#
#   il_vars                          - list HLIL variables in the target fn
#                                      so the agent can name Rust locals
#                                      to bind 1:1.
#   check_types {types}              - rustc-validate type defs alone.
#   check_reconstruction {source}    - full validation: rustc compile +
#                                      lymph dataflow + anemone match.
from __future__ import annotations

from claude_agent_sdk import tool

import nacre

from cli import with_compiler_errors

import consistency


def make(bv, addr: int, *, prelude: str | None = None, rust_fn_name: str,
         recoveries=None):
    """Bind tools to one (bv, addr) pair plus the expected Rust fn name.
    `recoveries` is the cross-stage sidecar; when present, prior_metadata
    can serve signer/flower findings without re-parsing the bndb."""

    # Cache the per-fn anemone graph; tool calls reuse it instead of
    # re-lowering MLIL-SSA each time.
    _cached: dict = {}
    def _anem():
        if "g" not in _cached:
            import anemone
            _cached["g"] = anemone.analyze(bv, addr)
        return _cached["g"]

    @tool("il_vars",
          "List the HLIL variables visible in the target function. The "
          "Rust source you submit must name every reconstructed local "
          "after one of these names (so the validator can bind Rust "
          "vars to binary vars 1:1). Underscored names like `_x` and "
          "`<return>` are reserved escape hatches.",
          {})
    async def il_vars(_args):
        try:
            anem = _anem()
            # Strip SSA `#N` suffix; dedup. Agent uses unversioned names.
            names = sorted({
                v.split("#", 1)[0] for v in anem.variables()
                if not v.startswith("<")
            })
        except Exception as e:
            return _err(f"il_vars: {type(e).__name__}: {e}")
        return _ok("\n".join(names))

    @tool("prior_metadata",
          "Read all per-stage findings recorded for a function in the "
          "cross-stage sidecar (`<bndb>.patina.json`). Namespaces today: "
          "`signer` (rust_signature, rust_types, name) and `flower` "
          "(source, name). Use this BEFORE drafting your reconstruction "
          "to pick up the signer-stage prototype + types verbatim, and "
          "to see whether a sibling worker already recovered a callee. "
          "`target` is `0xADDR` or a function name; default is current target.",
          {"target": str})
    async def prior_metadata(args):
        if recoveries is None:
            return _err("no recoveries sidecar bound to this agent")
        target = (args.get("target") or "").strip() or hex(addr)
        try:
            funcs = bv.get_functions_at(int(target, 16)) if target.startswith("0x") else (
                [f for f in bv.functions if target in (f.symbol.full_name or "") or target == f.name]
            )
            f = funcs[0] if funcs else None
            if f is None:
                return _err(f"function not found: {target}")
            entry = recoveries.get(f.start)
            if not entry:
                return _ok(f"{f.name} @ {f.start:#x}: no prior metadata")
            import json as _json
            return _ok(f"{f.name} @ {f.start:#x}:\n{_json.dumps(entry, indent=2)}")
        except Exception as e:
            return _err(f"prior_metadata: {type(e).__name__}: {e}")

    @tool("prior_reconstruction",
          "Read a previously-saved Rust reconstruction stored as the "
          "fn-level comment in the bndb. Use this to (a) check what "
          "another concurrent worker recovered for a callee, or (b) "
          "skip work if the function you were assigned already has a "
          "recovery from a prior pipeline run. `target` is `0xADDR` "
          "or a function name; default is the current target.",
          {"target": str})
    async def prior_reconstruction(args):
        target = (args.get("target") or "").strip() or hex(addr)
        try:
            funcs = bv.get_functions_at(int(target, 16)) if target.startswith("0x") else (
                [f for f in bv.functions if target in (f.symbol.full_name or "") or target == f.name]
            )
            f = funcs[0] if funcs else None
            if f is None:
                return _err(f"function not found: {target}")
            comment = f.comment or ""
            if not comment.strip():
                return _ok(f"{f.name} @ {f.start:#x}: no prior reconstruction")
            return _ok(f"{f.name} @ {f.start:#x}:\n{comment}")
        except Exception as e:
            return _err(f"prior_reconstruction: {type(e).__name__}: {e}")

    @tool("signer_types",
          "Dump the function prototype + every named struct typedef "
          "from the bndb whose name appears in the prototype string. "
          "These are the ground-truth types the signer stage applied; "
          "use them as the foundation of your `source` instead of "
          "guessing offsets/field names. Output is C-syntax (binja's "
          "native form); transliterate to Rust types in the reconstruction.",
          {})
    async def signer_types(_args):
        try:
            funcs = bv.get_functions_at(addr) or []
            f = funcs[0] if funcs else None
            if f is None:
                return _err(f"no fn at {addr:#x}")
            proto = str(f.type)
            lines = [f"// prototype:\n{proto}"]
            # Find every registered type name mentioned by the prototype
            # string and dump its full body. Cheap, syntactic, sufficient.
            for name, ty in bv.types.items():
                key = str(name)
                if key in proto:
                    lines.append(f"\n// type {key}:\n{ty}")
        except Exception as e:
            return _err(f"signer_types: {type(e).__name__}: {e}")
        return _ok("\n".join(lines))

    @tool("bin_depends",
          "Probe the BINARY's dataflow: does `of` depend on `on`? Both "
          "are HLIL var names (unversioned, as listed by `il_vars`). "
          "Returns yes/no plus, when yes, a short path through "
          "intermediate slots so you can see the chain. Use this BEFORE "
          "submitting to verify a flow you intend to model in Rust.",
          {"of": str, "on": str})
    async def bin_depends(args):
        of = (args.get("of") or "").strip()
        on = (args.get("on") or "").strip()
        if not of or not on:
            return _err("both `of` and `on` required")
        try:
            anem = _anem()
            # depends_on uses depth=0 (worst-case at calls); same
            # semantic as the validator's check_compatibility.
            yes = anem.depends_on(of, on, 0)
        except Exception as e:
            return _err(f"bin_depends: {type(e).__name__}: {e}")
        if not yes:
            return _ok(f"no: {of!r} does NOT depend on {on!r} in the binary")
        from consistency import _path_hint
        path = _path_hint(anem, on, of)
        return _ok(f"yes: {of!r} <- {on!r}\n  via: {path}")

    @tool("bin_neighbors",
          "Predecessors + successors of a binary HLIL var. Lists every "
          "slot that flows INTO `var` (predecessors) and every slot "
          "`var` flows INTO (successors). Cheap structural query - "
          "use to navigate the binary graph one hop at a time.",
          {"var": str})
    async def bin_neighbors(args):
        v = (args.get("var") or "").strip()
        if not v:
            return _err("`var` required")
        try:
            anem = _anem()
            preds = anem.predecessors(v)
            succs = anem.successors(v)
        except Exception as e:
            return _err(f"bin_neighbors: {type(e).__name__}: {e}")
        lines = [f"{v!r}: {len(preds)} pred, {len(succs)} succ"]
        if preds:
            lines.append("  predecessors (in -> kind):")
            for s, k in preds[:30]:
                lines.append(f"    {s}  ({k})")
        if succs:
            lines.append("  successors (out -> kind):")
            for s, k in succs[:30]:
                lines.append(f"    {s}  ({k})")
        return _ok("\n".join(lines))

    @tool("check_types",
          "Compile a block of Rust type definitions and report rustc's "
          "verdict. Use this to validate struct/enum/use defs BEFORE "
          "plugging them into a reconstruction. Returns 'ok' or rustc's "
          "error output on failure.",
          {"types": str})
    async def check_types(args):
        t = (args.get("types") or "").strip()
        if not t:
            return _err("types is empty")
        try:
            with_compiler_errors(nacre.signature, "()", prelude=t)
        except Exception as e:
            return _err(f"types failed to compile:\n{e}")
        return _ok("ok - types compile")

    @tool("check_reconstruction",
          "Validate a candidate Rust source reconstruction of the target "
          "function. `source` is one Rust file containing all needed "
          "type defs PLUS a `fn` with the same name as the target "
          "(`" + rust_fn_name + "`). The harness runs three checks: "
          "(1) rustc compiles the source - errors come back unchanged; "
          "(2) every named local in your fn matches an HLIL var name; "
          "(3) the source's MIR-level dataflow agrees with the binary's "
          "MLIL-level dataflow on every (var_i, var_j) pair. Diffs are "
          "ordered: return/argument boundary first, then intermediates.",
          {"source": str})
    async def check_reconstruction(args):
        src = (args.get("source") or "").strip()
        if not src:
            return _err("source is empty")
        full = "\n".join(p for p in (prelude or "", src) if p).strip()
        try:
            r = consistency.check(full, bv=bv, fn_addr=addr,
                                  rust_fn_name=rust_fn_name)
        except Exception as e:
            return _err(f"check_reconstruction failed: {type(e).__name__}: {e}")
        return _ok(_format(r, rust_fn_name))

    @tool("region_blocks",
          "List the basic blocks (BBs) of the target function. Returns "
          "`[(idx, start_addr, end_addr, instr_count), ...]` so you can "
          "pick a contiguous range `[block_start, block_end)` to "
          "reconstruct piece-wise via `check_region` / `submit_region`. "
          "Tight regions (3-8 BBs) are easier to validate than the whole "
          "fn; the validator can give a clean dataflow verdict on a "
          "small region where the same check on the full body would be "
          "noisy.",
          {})
    async def region_blocks(_args):
        import anemone as _anemone
        try:
            blks = _anemone.list_blocks(bv, addr)
        except Exception as e:
            return _err(f"region_blocks: {type(e).__name__}: {e}")
        if not blks:
            return _ok("(no blocks)")
        lines = [f"{len(blks)} basic blocks (idx, start-end, #instr):"]
        for idx, sa, ea, n in blks:
            lines.append(f"  bb[{idx:3d}]  {sa:#x}-{ea:#x}  ({n} insns)")
        return _ok("\n".join(lines))

    @tool("check_region",
          "Validate a Rust source snippet against a SET of basic blocks "
          "of the target function. Pass `blocks` as a list of BB "
          "indices (use `region_blocks` to list them). The set may be "
          "non-contiguous - inlined fns, hot/cold splits, and macro "
          "expansions often span scattered BBs. Validator runs the "
          "same three checks as `check_reconstruction` but scopes the "
          "binary side to just those blocks' flow. Legacy form "
          "`block_start`/`block_end` for a contiguous range still "
          "works; pass either `blocks` OR both range fields.",
          {"source": str, "blocks": list,
           "block_start": int, "block_end": int})
    async def check_region(args):
        src = (args.get("source") or "").strip()
        if not src:
            return _err("source is empty")
        blocks = args.get("blocks")
        region: tuple[int, int] | list[int] | None
        if blocks:
            region = sorted({int(b) for b in blocks})
            if not region:
                return _err("blocks list is empty")
            tag = f"blocks={region}"
        else:
            bs = int(args.get("block_start") or 0)
            be = int(args.get("block_end") or 0)
            if be <= bs:
                return _err("pass `blocks=[...]` or "
                            "`block_start`/`block_end` with end > start")
            region = (bs, be)
            tag = f"region=[{bs},{be})"
        full = "\n".join(p for p in (prelude or "", src) if p).strip()
        try:
            r = consistency.check(full, bv=bv, fn_addr=addr,
                                  rust_fn_name=rust_fn_name,
                                  region=region)
        except Exception as e:
            return _err(f"check_region {tag}: "
                        f"{type(e).__name__}: {e}")
        return _ok(f"{tag}  " + _format(r, rust_fn_name))

    return [il_vars, prior_metadata, prior_reconstruction, signer_types,
            bin_depends, bin_neighbors,
            check_types, check_reconstruction,
            region_blocks, check_region]


def _ok(text: str):
    return {"content": [{"type": "text", "text": text}]}


def _err(text: str):
    return {"content": [{"type": "text", "text": f"error: {text}"}]}


def _format(r: consistency.CheckResult, fn: str) -> str:
    """Compact summary: header + ordered diffs. Brevity keeps tokens low."""
    lines = [
        f"fn={fn!r}  perfect={r.perfect}  "
        f"rust_vars={r.rust_var_count}  bin_vars={r.binary_var_count}",
    ]
    if r.unbound:
        lines.append(f"unbound rust vars: {', '.join(r.unbound)}")
    if r.diffs_ordered:
        lines.append("dataflow diffs (boundary first):")
        for d in r.diffs_ordered:
            lines.append(f"  - {d}")
    if r.perfect:
        lines.append(r.feedback)
    return "\n".join(lines)


NAMES = frozenset({"il_vars", "prior_metadata", "prior_reconstruction",
                   "signer_types", "bin_depends", "bin_neighbors",
                   "check_types", "check_reconstruction"})
