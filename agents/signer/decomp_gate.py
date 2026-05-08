# PreToolUse hook that locks WIDE untargeted dump tools - full-function
# `decompile`/`get_il` AND large-window `disasm` - until the agent has
# made its FIRST submission via `submit_signature`. The point: force
# the workflow to start from the cheap targeted tools (register_trace ->
# field_accesses -> il_around/il_range), draft a candidate signature,
# submit, and only escalate to whole-function dumps when refining a
# rejected submission. The system prompt already says this; the hook
# ENFORCES it.
#
# When the gate is closed and the agent reaches for a wide tool, the
# hook BLOCKS the call (decision: block) and tells the agent to submit
# its current best guess immediately. It does NOT halt the run - bouncing
# the agent toward submit is the whole point.
#
# Lock state:
#   unlocked = False   - wide tools blocked
#   unlocked = True    - wide tools allowed (any submit_signature has fired)
#
# Special-case `disasm`: legitimately targeted when n is small (a few
# instructions around an address from field_accesses). It's only WIDE
# when n exceeds DISASM_BYTES_LIMIT - that's the threshold above which
# the agent is using it as a backdoor for full-function dumps.
#
# Returns (matcher, state) so the harness can both register the hook
# and read the block-count after the run for telemetry.
from __future__ import annotations

from claude_agent_sdk import HookMatcher


# Always-wide: whole-function dumps, no useful narrow form.
ALWAYS_WIDE = ("decompile", "get_il")
# Wide-when-large: disasm with n > DISASM_BYTES_LIMIT bytes is treated
# the same as a full-function dump.
DISASM_BYTES_LIMIT = 128
GATE = ("submit_signature",)


def _parse_n(v) -> int:
    """Lenient int parser - mirrors common/tools/binja._parse_int. Inlined
    here so this module doesn't import the binja tools (avoids tool-load
    ordering quirks in the harness)."""
    if isinstance(v, int):
        return v
    if v is None or v == "":
        return 0
    s = str(v).strip().replace("_", "").replace(" ", "")
    if s.startswith(("0x", "0X")):
        try:
            return int(s[2:], 16)
        except ValueError:
            return 0
    if s and all(c in "0123456789abcdefABCDEF" for c in s):
        if any(c in "abcdefABCDEF" for c in s) or (len(s) == 8 and s[0] == "0"):
            try:
                return int(s, 16)
            except ValueError:
                return 0
    try:
        return int(s)
    except ValueError:
        return 0


def make(server_name: str = "signer"):
    state: dict = {"unlocked": False, "blocks": 0}
    wide_qual = {f"mcp__{server_name}__{n}" for n in ALWAYS_WIDE}
    disasm_qual = f"mcp__{server_name}__disasm"
    gate_qual = {f"mcp__{server_name}__{n}" for n in GATE}

    BLOCK_REASON_TAIL = (
        "STOP investigating and **call `submit_signature` now** with "
        "your current best guess. The targeted tools you've already "
        "used (register_trace, field_accesses, "
        "hlil_around / mlil_around / llil_around, "
        "hlil_range / mlil_range / llil_range, "
        f"disasm with n <= {DISASM_BYTES_LIMIT}) plus check_signature "
        "give you enough to submit. A wrong submission is fine - the "
        "harness bounces it back with feedback so you can refine, AND "
        "the wide tools unlock once you've submitted at least once."
    )

    async def pre_hook(input_data, tool_use_id, ctx):
        name = input_data.get("tool_name", "") or ""
        if name in gate_qual:
            state["unlocked"] = True
            return {}
        if state["unlocked"]:
            return {}
        # Always-wide tools: hard block pre-guess.
        if name in wide_qual:
            state["blocks"] += 1
            short = name.rsplit("__", 1)[-1]
            return {
                "decision": "block",
                "reason": (
                    f"`{short}` is BLOCKED before your first "
                    f"`submit_signature`. Whole-function decompilation is "
                    f"forbidden pre-submission - period. "
                    f"{BLOCK_REASON_TAIL}"
                ),
            }
        # disasm: only wide when n exceeds the byte limit.
        if name == disasm_qual:
            n = _parse_n((input_data.get("tool_input") or {}).get("n", 0))
            if n > DISASM_BYTES_LIMIT:
                state["blocks"] += 1
                return {
                    "decision": "block",
                    "reason": (
                        f"`disasm` with n={n} bytes is BLOCKED before "
                        "your first `submit_signature` - that's a "
                        f"whole-function dump. {BLOCK_REASON_TAIL}"
                    ),
                }
        return {}

    # matcher=None means PreToolUse fires on every tool; the in-hook
    # filter routes per-tool. Same pattern submit.py's PostToolUse uses.
    matcher = HookMatcher(matcher=None, hooks=[pre_hook])
    return matcher, state
