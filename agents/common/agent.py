import asyncio
import tempfile
from dataclasses import dataclass, field
from typing import Any, Callable
from claude_agent_sdk import (
    query, ClaudeAgentOptions, tool, create_sdk_mcp_server,
    HookMatcher, AgentDefinition,
)


# Neutral cwd for the spawned Claude CLI. Default to the process cwd
# leaks the project name ("patina") into the agent's working-directory
# context, and the model rationalises any unfamiliar binary as part of
# that project (e.g. labels a Hangul fn as "Patina-project romanization").
# Use a freshly-mkdtemp'd dir with a generic prefix so no project string
# ends up in the path the agent sees.
_NEUTRAL_CWD = tempfile.mkdtemp(prefix="agent-")

@dataclass
class Agent:
    name: str
    system_prompt: str
    tools: list[Callable] = field(default_factory=list)        # @tool-decorated fns
    allowed_builtins: list[str] = field(default_factory=list)  # ["Read", "Bash", ...]
    hooks: dict[str, list[HookMatcher]] = field(default_factory=dict)
    model: str = "sonnet"
    max_turns: int = 20
    cwd: str | None = None
    # Subagent definitions the parent can spawn via the Task/Agent
    # builtin. Maps name -> AgentDefinition. Whitelist "Task" or
    # "Agent" in `allowed_builtins` to expose the spawn capability.
    agents: dict[str, AgentDefinition] = field(default_factory=dict)
    # Optional per-call stderr capture buffer. SDK only pipes claude
    # CLI stderr when a callback is registered; otherwise it inherits
    # parent stderr and the "Command failed with exit code 1" crashes
    # we keep seeing emit useful diagnostics that get lost. When set,
    # every line of stderr goes here so the harness can dump it on
    # transport_error.
    stderr_buf: list[str] | None = None
    # API-side total-token budget. The model is told its remaining
    # budget mid-run so it paces tool use and wraps up before the
    # limit. Caps run-away iter-cap-exhaust runs (the model rambling
    # to 50k+ output tokens after hitting max_turns crashed the SDK
    # CLI subprocess). 0 = unset.
    task_budget_tokens: int = 0
    # Extended-thinking effort. "high" gives the model a generous
    # reasoning budget per turn - worth it for big-fn reconstruction.
    # Set to None to defer to SDK default; "low"/"medium"/"high" to
    # override. Read from PATINA_EFFORT env if set, else "high".
    effort: str | None = None

    def _build_options(self, **overrides) -> ClaudeAgentOptions:
        mcp_servers = {}
        allowed = list(self.allowed_builtins)
        if self.tools:
            srv = create_sdk_mcp_server(name=self.name, version="1.0.0", tools=self.tools)
            mcp_servers[self.name] = srv
            # custom tools are addressed as mcp__<server>__<tool>
            allowed += [f"mcp__{self.name}__{t.name}" for t in self.tools]

        budget = overrides.pop("task_budget_tokens", self.task_budget_tokens)
        task_budget = {"total": int(budget)} if budget else None
        stderr_buf = overrides.pop("stderr_buf", self.stderr_buf)
        stderr_cb = (lambda s: stderr_buf.append(s)) if stderr_buf is not None else None
        import os as _os
        effort = overrides.pop("effort", self.effort)
        if effort is None:
            effort = _os.environ.get("PATINA_EFFORT", "high") or None
        return ClaudeAgentOptions(
            system_prompt=self.system_prompt,
            allowed_tools=allowed,
            mcp_servers=mcp_servers,
            hooks=self.hooks,
            model=self.model,
            max_turns=self.max_turns,
            cwd=overrides.pop("cwd", self.cwd) or _NEUTRAL_CWD,
            permission_mode="bypassPermissions",
            setting_sources=[],
            skills=[],
            agents=self.agents or None,
            task_budget=task_budget,
            stderr=stderr_cb,
            effort=effort,
            **overrides,
        )

    async def run(self, prompt: str, **overrides) -> str:
        out = []
        async for msg in query(prompt=prompt, options=self._build_options(**overrides)):
            if hasattr(msg, "result") and msg.result:
                out.append(msg.result)
        return "\n".join(out)

    def __call__(self, prompt: str, **overrides) -> str:
        return asyncio.run(self.run(prompt, **overrides))
