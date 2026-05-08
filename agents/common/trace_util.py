# Live stream-of-consciousness tracer for any agent built on
# claude_agent_sdk. Two integration shapes:
#
#   # 1. plain callback (cheapest):
#   from trace import print_message
#   ...
#   async for msg in query(...):
#       print_message(msg, prefix="[marinator] ")
#       # ...your existing handling
#
#   # 2. iterator-wrapper:
#   from trace import livestream
#   async for msg in livestream(query(...), prefix="[marinator] "):
#       # msg is yielded after being printed; identical otherwise
#       ...
#
# Wire either behind an agent-side flag (`--trace`, `quiet=False`, etc.).
from __future__ import annotations

import json
import sys
import time
from typing import Any, AsyncIterator, Callable

# Soft import: avoid hard dep when this module is loaded by code paths
# that don't actually call print_message (e.g. tests, doc tooling).
try:
    from claude_agent_sdk.types import (
        AssistantMessage, ResultMessage, TextBlock, ToolResultBlock,
        ToolUseBlock, UserMessage,
    )
except Exception:  # pragma: no cover - SDK absent
    AssistantMessage = ResultMessage = TextBlock = ToolResultBlock = ()
    ToolUseBlock = UserMessage = ()

# ThinkingBlock isn't always exported by the SDK depending on version.
# Resolve it by name lookup so the case can fire when present without
# making it a hard import.
try:
    from claude_agent_sdk.types import ThinkingBlock  # type: ignore
except Exception:  # pragma: no cover
    ThinkingBlock = None  # type: ignore


# ANSI dim/colors. Off automatically when stdout is not a tty.
def _color(code: str, s: str, *, enabled: bool) -> str:
    return f"\x1b[{code}m{s}\x1b[0m" if enabled else s


def _shorten(s: str, n: int) -> str:
    s = s.replace("\r", " ")
    if len(s) <= n:
        return s
    return s[: max(0, n - 1)] + "..."


def _stringify(v: Any, max_len: int) -> str:
    if isinstance(v, (dict, list)):
        try:
            v = json.dumps(v, ensure_ascii=False, default=str)
        except Exception:
            v = repr(v)
    if not isinstance(v, str):
        v = str(v)
    return _shorten(v, max_len)


def print_message(
    msg: Any,
    *,
    prefix: str = "",
    text_max: int = 4000,
    tool_arg_max: int = 200,
    tool_result_max: int = 400,
    color: bool | None = None,
    out=None,
) -> None:
    # Pretty-print one SDK message. Lossy by design (long blocks are
    # truncated) so a 558-block-fn agent doesn't drown the terminal.
    out = out or sys.stdout
    color = (color if color is not None
             else getattr(out, "isatty", lambda: False)())

    def emit(line: str) -> None:
        out.write(prefix + line + "\n")
        out.flush()

    # AssistantMessage: walk content blocks.
    if AssistantMessage and isinstance(msg, AssistantMessage):
        for b in msg.content or []:
            if TextBlock and isinstance(b, TextBlock):
                tag = _color("36", "[asst]", enabled=color)  # cyan
                emit(f"{tag} {_shorten(b.text, text_max)}")
            elif ToolUseBlock and isinstance(b, ToolUseBlock):
                tag = _color("33", "[tool]", enabled=color)  # yellow
                args = _stringify(b.input, tool_arg_max)
                # Trim mcp__server__ prefix for readability.
                name = b.name.split("__")[-1] if b.name else "?"
                emit(f"{tag} {name}({args})")
            elif ThinkingBlock is not None and isinstance(b, ThinkingBlock):
                # Extended-thinking content. Show the leading slice so
                # the operator sees the agent's reasoning take shape.
                tag = _color("90", "[think]", enabled=color)  # gray
                txt = getattr(b, "thinking", None) or getattr(b, "text", "") or ""
                emit(f"{tag} {_shorten(str(txt), text_max)}")
            else:
                tag = _color("90", "[asst.?]", enabled=color)
                txt = getattr(b, "text", None) or getattr(b, "thinking", None) or ""
                if txt:
                    emit(f"{tag} {type(b).__name__}: {_shorten(str(txt), text_max)}")
                else:
                    emit(f"{tag} {type(b).__name__}")
        return

    # UserMessage typically carries ToolResultBlocks back to the model.
    if UserMessage and isinstance(msg, UserMessage):
        content = msg.content if isinstance(msg.content, list) else []
        for b in content:
            if ToolResultBlock and isinstance(b, ToolResultBlock):
                tag = _color("32", "[ret ]", enabled=color)  # green
                payload = b.content
                if isinstance(payload, list):
                    parts: list[str] = []
                    for p in payload:
                        if isinstance(p, dict):
                            t = p.get("text")
                            if isinstance(t, str):
                                parts.append(t)
                            else:
                                # non-text part (image, etc.) - best-effort label
                                parts.append(f"<{p.get('type','?')}>")
                        elif p is not None:
                            parts.append(str(p))
                    text = " ".join(parts)
                else:
                    text = "" if payload is None else str(payload)
                emit(f"{tag} {_shorten(text, tool_result_max)}")
        return

    # ResultMessage: final usage summary.
    if ResultMessage and isinstance(msg, ResultMessage):
        u = msg.usage or {}
        cost = float(msg.total_cost_usd or 0.0)
        tag = _color("35", "[done]", enabled=color)  # magenta
        emit(
            f"{tag} turns={msg.num_turns} "
            f"in={u.get('input_tokens',0)} out={u.get('output_tokens',0)} "
            f"cache_r={u.get('cache_read_input_tokens',0)} "
            f"cache_w={u.get('cache_creation_input_tokens',0)} "
            f"${cost:.3f}"
        )
        return

    # Stream-error sentinel sent by the SDK reader on subprocess crash.
    if isinstance(msg, dict) and msg.get("type") == "error":
        tag = _color("31", "[err ]", enabled=color)  # red
        emit(f"{tag} {msg.get('error','')}")
        return

    # Recognise SDK meta-events by class name so we don't depend on imports.
    cls = type(msg).__name__
    if cls == "SystemMessage":
        sub = getattr(msg, "subtype", "?")
        sid = (getattr(msg, "data", {}) or {}).get("session_id", "")
        tag = _color("90", "[sys ]", enabled=color)
        emit(f"{tag} {sub} session={sid[:8]}")
        return
    if cls == "RateLimitEvent":
        info = getattr(msg, "rate_limit_info", None)
        status = getattr(info, "status", "?")
        kind = getattr(info, "rate_limit_type", "?")
        overage = getattr(info, "overage_status", "?")
        tag = _color("31", "[rate]", enabled=color)
        emit(f"{tag} {kind} status={status} overage={overage}")
        return

    # Fallback: anything else.
    emit(f"[?]    {cls}: {_shorten(str(msg), 200)}")


async def livestream(
    it: AsyncIterator[Any],
    *,
    prefix: str = "",
    on_message: Callable[[Any], None] | None = None,
    **print_kwargs: Any,
) -> AsyncIterator[Any]:
    # Yields each message after printing it. Drop-in replacement for the
    # raw `query(...)` async iterator. `on_message` (if given) runs
    # before printing - useful for record-keeping.
    async for msg in it:
        if on_message is not None:
            try:
                on_message(msg)
            except Exception as e:
                sys.stderr.write(f"[trace] on_message: {type(e).__name__}: {e}\n")
        print_message(msg, prefix=prefix, **print_kwargs)
        yield msg
