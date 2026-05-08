# Mutating tools for the marinator agent. Each acquires `ctx.write_lock`
# before touching the BV (per Vector35 issue #6109: concurrent BV mutations
# can crash; do "bulk analysis without blocking, apply changes in a blocking
# way") and runs the actual write inside `bv.undoable_transaction()` so a
# failure rolls back rather than poisoning the next agent.
#
# Past failure modes baked into the tool docstrings - the agent reads them:
#   * `rename_variable` errors on SSA-suffixed names (foo_2). Try the BASE.
#   * `retype_variable` needs the type to already exist in the BV - declare
#     first via `declare_c_type`.
#   * `set_function_prototype` needs a complete C decl with the fn name.
from __future__ import annotations

from claude_agent_sdk import tool

import binaryninja as bn

from tools.ctx import TargetCtx


def _tx(text: str):
    return {"content": [{"type": "text", "text": text}]}


def make(ctx: TargetCtx):
    @tool("rename_function",
          "Rename a function. Identify by current name or 0xADDR.",
          {"target": str, "new_name": str})
    async def rename_function(args):
        target = args.get("target") or ""
        new = (args.get("new_name") or "").strip()
        if not new:
            return _tx("error: new_name required")
        f = ctx.func(target or hex(ctx.fn_addr))
        if f is None:
            return _tx(f"error: function not found: {target}")
        old = f.name
        async with ctx.write_lock:
            try:
                with ctx.bv.undoable_transaction():
                    f.name = new
            except Exception as e:
                return _tx(f"error: {e}")
        return _tx(f"ok: {old!r} -> {new!r} @ {f.start:#x}")

    @tool("rename_variable",
          "Rename a single local variable inside a function. Default target is ctx.fn_addr. "
          "If this errors for an SSA-suffixed name (foo_2), retry with the BASE name (foo).",
          {"fn": str, "old": str, "new": str})
    async def rename_variable(args):
        target = (args.get("fn") or "").strip() or hex(ctx.fn_addr)
        old = (args.get("old") or "").strip()
        new = (args.get("new") or "").strip()
        if not old or not new:
            return _tx("error: old and new required")
        f = ctx.func(target)
        if f is None:
            return _tx(f"error: function not found: {target}")
        v = f.get_variable_by_name(old)
        if v is None:
            return _tx(f"error: variable {old!r} not found in {f.name!r}")
        async with ctx.write_lock:
            try:
                with ctx.bv.undoable_transaction():
                    v.name = new
            except Exception as e:
                return _tx(f"error: {e}")
        return _tx(f"ok: {old!r} -> {new!r} in {f.name!r}")

    @tool("rename_variables",
          "Batch rename locals: pairs is a list of [old, new] entries. Per-pair success "
          "reported. Failures are isolated - the rest still apply within ONE undo transaction.",
          {"fn": str, "pairs": list})
    async def rename_variables(args):
        target = (args.get("fn") or "").strip() or hex(ctx.fn_addr)
        pairs = args.get("pairs") or []
        f = ctx.func(target)
        if f is None:
            return _tx(f"error: function not found: {target}")
        applied = 0
        rows: list[str] = []
        async with ctx.write_lock:
            try:
                with ctx.bv.undoable_transaction():
                    for entry in pairs:
                        try:
                            if isinstance(entry, dict):
                                old = entry.get("old") or entry.get("from")
                                new = entry.get("new") or entry.get("to")
                            elif isinstance(entry, (list, tuple)) and len(entry) == 2:
                                old, new = entry
                            else:
                                rows.append(f"skip: bad entry {entry!r}")
                                continue
                        except Exception as e:
                            rows.append(f"skip: {e}")
                            continue
                        old = (old or "").strip(); new = (new or "").strip()
                        if not old or not new:
                            rows.append(f"skip: empty {entry!r}")
                            continue
                        v = f.get_variable_by_name(old)
                        if v is None:
                            rows.append(f"miss: {old!r} (not in fn)")
                            continue
                        try:
                            v.name = new
                            applied += 1
                            rows.append(f"ok:   {old!r} -> {new!r}")
                        except Exception as e:
                            rows.append(f"err:  {old!r}: {e}")
            except Exception as e:
                return _tx(f"error: txn: {e}")
        return _tx(f"applied {applied}/{len(pairs)}\n" + "\n".join(rows))

    @tool("retype_variable",
          "Retype a single local variable (e.g. 'struct Foo*', 'int64_t'). The type must "
          "already be parseable by BN - use declare_c_type first if you need a new struct.",
          {"fn": str, "name": str, "c_type": str})
    async def retype_variable(args):
        target = (args.get("fn") or "").strip() or hex(ctx.fn_addr)
        name = (args.get("name") or "").strip()
        c_type = (args.get("c_type") or "").strip()
        if not name or not c_type:
            return _tx("error: name and c_type required")
        f = ctx.func(target)
        if f is None:
            return _tx(f"error: function not found: {target}")
        v = f.get_variable_by_name(name)
        if v is None:
            return _tx(f"error: variable {name!r} not found in {f.name!r}")
        try:
            t, _ = ctx.bv.parse_type_string(c_type)
        except Exception as e:
            return _tx(f"error: parse_type_string: {e}")
        async with ctx.write_lock:
            try:
                with ctx.bv.undoable_transaction():
                    v.type = t
            except Exception as e:
                return _tx(f"error: assign type: {e}")
        return _tx(f"ok: {name!r} :: {c_type}")

    @tool("set_function_prototype",
          "Set the function prototype. Pass a complete C declaration with the function's name "
          "(e.g. 'int foo(int a, struct Bar* b)'). Reanalyzes the function on success.",
          {"target": str, "prototype": str})
    async def set_function_prototype(args):
        target = (args.get("target") or "").strip() or hex(ctx.fn_addr)
        proto = (args.get("prototype") or "").strip()
        if not proto:
            return _tx("error: prototype required")
        f = ctx.func(target)
        if f is None:
            return _tx(f"error: function not found: {target}")
        if proto.endswith(";"):
            proto = proto[:-1].strip()
        t = None
        last_err: Exception | None = None
        try:
            t, _ = ctx.bv.parse_type_string(proto)
        except Exception as e:
            last_err = e
        if t is None:
            try:
                pr = ctx.bv.parse_types_from_string(proto)
                if pr and getattr(pr, "types", None):
                    chosen = pr.types.get(f.name)
                    if chosen is None:
                        chosen = next(iter(pr.types.values()), None)
                    if chosen is not None:
                        t = chosen
            except Exception as e:
                last_err = e
        if t is None:
            return _tx(f"error: parse failed: {last_err}")
        async with ctx.write_lock:
            try:
                with ctx.bv.undoable_transaction():
                    f.type = t
                    f.reanalyze(bn.FunctionUpdateType.UserFunctionUpdate)
            except Exception as e:
                return _tx(f"error: apply: {e}")
        return _tx(f"ok: {f.name} :: {t}")

    @tool("declare_c_type",
          "Declare or update one or more named C types in the BV (struct/union/enum/typedef). "
          "Use this before retype_variable when you need a new struct.",
          {"c_decl": str})
    async def declare_c_type(args):
        decl = (args.get("c_decl") or "").strip()
        if not decl:
            return _tx("error: c_decl required")
        try:
            pr = ctx.bv.parse_types_from_string(decl)
        except Exception as e:
            return _tx(f"error: parse: {e}")
        if not pr or not getattr(pr, "types", None):
            return _tx("error: no named types found")
        defined: list[str] = []
        async with ctx.write_lock:
            try:
                with ctx.bv.undoable_transaction():
                    for name, ty in pr.types.items():
                        ctx.bv.define_user_type(name, ty)
                        defined.append(str(name))
            except Exception as e:
                return _tx(f"error: define: {e}")
        return _tx(f"ok: defined {', '.join(defined)}")

    @tool("set_function_comment",
          "Set the function-level comment. ONE LINE if you can - describe the *why* / a "
          "striking summary, never narrate phases. Pass an empty string to clear.",
          {"target": str, "comment": str})
    async def set_function_comment(args):
        target = (args.get("target") or "").strip() or hex(ctx.fn_addr)
        text = args.get("comment", "")
        f = ctx.func(target)
        if f is None:
            return _tx(f"error: function not found: {target}")
        async with ctx.write_lock:
            try:
                with ctx.bv.undoable_transaction():
                    f.comment = text
            except Exception as e:
                return _tx(f"error: {e}")
        return _tx(f"ok: comment on {f.name!r} ({len(text)}B)")

    @tool("set_address_comment",
          "Address-level comment. Use ONLY for non-obvious magic constants or explicit "
          "workarounds. Empty string clears.",
          {"addr": int, "comment": str})
    async def set_address_comment(args):
        a = args["addr"]
        f = ctx.func(a) or next(iter(ctx.bv.get_functions_containing(a) or []), None)
        async with ctx.write_lock:
            try:
                with ctx.bv.undoable_transaction():
                    if f is not None:
                        f.set_comment_at(a, args.get("comment", ""))
                    else:
                        ctx.bv.set_comment_at(a, args.get("comment", ""))
            except Exception as e:
                return _tx(f"error: {e}")
        return _tx(f"ok: comment @ {a:#x}")

    return [
        rename_function,
        rename_variable,
        rename_variables,
        retype_variable,
        set_function_prototype,
        declare_c_type,
        set_function_comment,
        set_address_comment,
    ]


NAMES = frozenset({
    "rename_function", "rename_variable", "rename_variables",
    "retype_variable", "set_function_prototype", "declare_c_type",
    "set_function_comment", "set_address_comment",
})
