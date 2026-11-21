# runtime

Runs an `AgentSpec` on a task: `run_agent(spec, task, RunConfig(...), browser=...) -> AgentTrace`.

- One small explicit loop: model → tool calls → model … until the agent answers.
- **Budgets enforced by the runtime**, never by the agent: `max_steps` (tool calls) and
  `max_cost_usd`. Hitting one stops the run with `stop_reason` step_limit or cost_limit.
- **Recorded for the knowledge base:** every tool call (args, ok, error, duration), tool errors,
  recovery attempts (the first call after a failed one, and whether it worked), model retries,
  tokens and cost.
- Model API errors are retried twice with backoff; tool errors go back to the model so it can
  recover; anything else ends the run with `stop_reason=error`.
