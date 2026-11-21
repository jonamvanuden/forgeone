# runtime

Turns an `AgentSpec` into a running agent: one generic LangGraph loop
(`agent -> tools -> agent ...`) that loads the spec's system prompt and allowlisted tools.

**Planned interface**
- `run(spec: AgentSpec, task: str) -> AgentTrace`: run one task, fully logged, return the trace evals consume

**Rules**
- One runtime for every generated agent. The builder never writes runtime code.
- Enforces `max_steps` and cost budgets itself; the agent cannot raise its own limits.
- Every tool call is logged with forgeone.observability so evals can check it.
