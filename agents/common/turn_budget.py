# PostToolUse hook that warns the agent when its tool-call budget runs
# low. Counts tool calls (rough proxy for SDK iter_count) and injects
# `additionalContext` at two thresholds: 4 calls before max, and 1
# before. Stops the agent from rambling past max_turns and triggering
# the SDK CLI exit-1 wrap-up crash.
from __future__ import annotations
from claude_agent_sdk import HookMatcher


def make(*, max_turns: int, warn_at_remaining: int = 4):
    """Return (matcher, state). Track tool calls in `state['calls']`."""
    state = {"calls": 0, "warned_low": False, "warned_final": False}

    async def on_tool(input_data, tool_use_id, ctx):
        state["calls"] += 1
        remaining = max_turns - state["calls"]
        if remaining <= 1 and not state["warned_final"]:
            state["warned_final"] = True
            return {"hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": (
                    "FINAL TURN: tool budget exhausted next iteration. "
                    "Submit your best current answer NOW, or your work "
                    "won't be recorded. Do not start new investigation."
                ),
            }}
        if remaining <= warn_at_remaining and not state["warned_low"]:
            state["warned_low"] = True
            return {"hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": (
                    f"Budget warning: ~{remaining} tool calls remain "
                    f"(used {state['calls']}/{max_turns}). Wrap up: "
                    "submit your current best answer or one targeted "
                    "verification call, then submit. Don't start a new "
                    "line of inquiry. If you've already submitted and "
                    "validated, ignore this."
                ),
            }}
        return {}

    return HookMatcher(matcher="*", hooks=[on_tool]), state
