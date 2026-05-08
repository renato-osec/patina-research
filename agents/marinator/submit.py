# Terminal-tool pattern for the marinator agent. Mirrors tools/submit.py but
# captures a CHANGES summary (counts of mutations) rather than Rust source.
# The harness reads the captured dict via the closure.
from __future__ import annotations

import json
from typing import Any

from claude_agent_sdk import tool


def make() -> tuple[list, dict[str, Any]]:
    captured: dict[str, Any] = {"summary": None, "raw": ""}

    @tool("finish",
          "Call ONCE when done marinating. Pass a JSON-style summary like "
          "{\"renamed_vars\":N,\"renamed_funcs\":N,\"retypes\":N,\"comments\":N,\"types_declared\":N}.",
          {"summary": dict})
    async def finish(args):
        summary = args.get("summary") or {}
        captured["summary"] = summary
        captured["raw"] = json.dumps(summary)
        return {"content": [{"type": "text", "text": f"finished: {captured['raw']}"}]}

    return [finish], captured


NAMES = frozenset({"finish"})
