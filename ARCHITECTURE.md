# Architecture

ForgeOne is one Python package (`src/forgeone`) organised by **capability**. Each folder owns
one responsibility and has its own README when it is not built yet.

**ForgeOne is the factory, not the product.** Every agent lives in its own git repository
(VoyagerOne: `jonamvanuden/VoyagerOne`), registered in `agents.yaml`. ForgeOne reads the agent's
`goal.md` and `evals/`, and publishes each build into the agent's `agent/` folder on a branch
`forgeone/<run-id>`. Merging that branch is the human approval.

```
  agent repo           ┌──────────────── evals ────────────────┐
  goal.md, evals/      │ suite → trace → checks/judges → score │
        │              │              → gate (promote?)        │
        ▼              └───────────────▲───────────────────────┘
     builder ── spec ──► runtime ── tools          │ traces
        │                   │                      │
        └──── llm ──────────┴──── observability ───┴──► store ──► control_room
```

| Package | Owns | Status |
|---|---|---|
| `work/` | **How work is done, for every agent type.** Goal → milestones → tasks → actions: planner, policy (stages per risk, routing, estimates), engine (execute → verify → edge test → approve, retries with feedback, budgets, human approvals, replanning) | working |
| `builder/` | goal.md → checked AgentSpec → published. The build playbook (design, spec, publish steps and their checks), prompts (`prompts/*.md`); the original LangGraph graph as `--legacy` | working |
| `workspace/` | Agent repositories: registry (`agents.yaml`), readiness checks, publishing to `agent/` on a branch, scaffolding new agent repos | working |
| `spec/` | Contracts: `AgentSpec`, the tool allowlist | working |
| `evals/` | **The core.** Suites, traces, checks, judges + rubrics, scorecards, promotion gate, **variants** and the **runner** | working; LLM judge next |
| `runtime/` | The agent loop: runs an AgentSpec, enforces step/cost budgets, retries, records tool errors and recoveries, returns an `AgentTrace` | working |
| `tools/` | Deterministic tools: Playwright browser (SSRF-blocked), web_search, open_page, click, find_on_page, write_report | working |
| `registry/` | Agent versions, champion/challenger, publishing | later |
| `safety/` | Limits on agents: allowlist, budgets, injection defences, sandbox | partly |
| `memory/` | What persists across runs | later |
| `llm/` | Model access (`chat_model`) and pricing (`PRICES`) | working |
| `observability/` | Structured events: context, events, tracing, LLM logger, redaction | working |
| `store/` | Postgres sink, writer, backfill, **versioned migrations** | working |
| `control_room/` | Dashboard: Starlette JSON API + single-page UI | working |
| `config.py` | All settings and paths, from the environment | working |
| `cli.py` | The `forgeone` command | working |

## Dependency rules
1. `spec`, `llm`, `config` and `observability` depend on nothing else in ForgeOne (except `config`).
2. `builder`, `runtime` and `evals` may use those, never each other's internals.
   The builder talks to evals only through `evals` public functions (scorecard, gate).
3. `control_room` only **reads** from the store. It never imports builder or runtime, and never runs the work
   engine; it reads goals through `work.store` and `work.views`.
4. **The builder never reads an eval suite.** Suites live in the agent repo's `evals/` and are
   loaded only by `evals`. This keeps evaluation honest (Goodhart's law).
5. **ForgeOne writes only inside an agent repo's `agent/` folder**, always via
   `workspace.AgentRepo.publish`, which refuses anything else.
6. Every schema change is a new file in `store/migrations/`; applied files are never edited.
7. Prompts are files, not strings in code, so prompt changes show up in diffs.
8. `work` uses `runtime`, `tools` and the evals' checks to execute and verify tasks; nothing in
   `runtime`, `tools` or `evals` imports `work`.

## Data flow of one build
A build is a goal like any other, with a fixed plan: the playbook `build_agent`
(`builder/playbook.py`), run by the work engine (`builder/run.py`).

`forgeone build --agent voyagerone` → `workspace`: agent repo must be a clean `main`, pull
(fails here, before any tokens are spent) → a Goal with playbook `build_agent` → one session named
`forgeone.build` (so the Builds pages, spec versions and decisions work as before):

| Milestone | Task (code step) | Stages | Checks |
|---|---|---|---|
| M1 Design ready | 1.1 design (`plan` prompt) | execute, verify | reviewer model on 5 criteria |
| M2 Agent spec written and checked | 2.1 spec (`build` prompt → `AgentSpec`) | execute, verify, edge_test | hard checks first: name, write_report, open_page, untrusted-content rule; then reviewer and edge tests |
| M3 Published | 3.1 publish (`AgentRepo.publish`) | execute, verify (checks only); `--publish main`: your go-ahead first | committed or unchanged, pushed |

A failed hard check fails the attempt without a model call and its fix goes into the next attempt.
The accepted spec is written to `runs/<id>/spec.yaml` and logged as the `spec` artifact.
`forgeone build --legacy` runs the original two-node LangGraph graph (`builder/graph.py`).

## Data flow of one agent run
`forgeone run "task"` or `forgeone eval run` → `evals.runner` loads `agent/spec.yaml` (the merged
version; `--spec` for an unmerged one) and the variant → `start_run("agent.run")` → `runtime.run_agent`:
model → tool calls → model ... until the agent answers or a budget runs out. Every tool call is an
event (`tool.call` / `tool.error`, with `recovery_attempt` when it follows a failure), every model
call goes to `llm_calls`. The `AgentTrace` goes through the checks → a verdict.

## The feedback loop: how knowledge accumulates
1. **Traces with verdicts.** Every run ends up in Postgres with a verdict: eval runs get the
   suite checks, real runs get the generic checks automatically (`run_labels`, source `checks`) and
   your own judgement with `forgeone label RUN_ID good|bad` (source `human`, which wins).
2. **Variants.** `evals/variants.yaml` in the agent repo defines what is compared: model, step
   budget, cost budget, tool subset, a prompt addition. Change one thing per variant.
3. **Knowledge table.** The view `v_knowledge` aggregates per agent × suite version × variant ×
   task category: pass^k, pass rate, cost, duration, tool calls, tool errors, recoveries, hard
   failures. `forgeone eval report` and the Control Room's Quality page show it as a matrix with
   the best safe choice per category. That table is the input for routing rules later.
4. **Gate.** Each eval run is compared with the champion of the same suite version; only a
   variant without hard failures and without a category regression is promoted.
5. **Deterministic tools first.** Every tool is plain code (`deterministic=True`); the numbers
   show where a tool can replace model steps.

## Data flow of one goal
`forgeone goal new "..." --agent voyagerone` → `work.Engine.create`: checks the agent (fails before any
tokens are spent) → plan: one model call breaks the goal into milestones (1–10, each with "done when"),
one call per milestone breaks it into tasks (type, difficulty, risk, acceptance criteria, dependencies,
estimates), and a task rated difficulty 5 is split into subtasks. `policy` adds to each task, with a
stored reason: its stages, its approver, its model, its budget.

`forgeone goal run G-...` → milestone by milestone, every task whose dependencies are done goes
through its stages. Each attempt at a stage is one **Action** row (who, model, prompt version, cost,
tokens, time, verdict, confidence, per-criterion results, feedback, failure reason):

| Stage | Done by | Fails when |
|---|---|---|
| execute | the agent with its tools (research, read, extract) or one model call | the tool or model fails, the budget runs out |
| verify | the automatic checks first (no model call when a hard check fails), then a reviewer model that sees the criteria and the output, never the executor's reasoning | any acceptance criterion is not met |
| edge_test | a model that tests 2–4 edge cases | an edge case that matters is not handled |
| approve | an AI approver (medium risk) or you (high risk, irreversible) | rejected |

Work that cannot be undone (publish, send, pay) gets your **go-ahead before it runs**: its stages
are approve → execute → verify. The planner puts the preparation (the draft) in a separate,
reversible task, so that still gets verified and edge-tested first.

Known processes use a **playbook** instead of the planner: a fixed plan in code with explicit
stages, criteria and checks, and code steps (Python functions) for the work. Building an agent is
the first playbook.

A failed stage sends its feedback into the next attempt (max 3, 2 for high risk). A task that runs out
of attempts fails, tasks that depend on it are blocked, the milestone fails. Supervised goals stop for
your approval at every milestone; autonomous goals get an AI approver and may replan twice.

The knowledge views of migration 004 turn this into routing data: `v_stage_knowledge` (pass rate and
cost per task type × stage × model), `v_task_calibration` (predicted vs. actual difficulty, cost and
time per task type) and `v_failure_reasons`.

## Support: where the system tells you what to improve
`work/support.py` reads the goal records (never a model) and answers four questions, shown on the
Control Room's Support page and by `forgeone support`:

| Question | Signal |
|---|---|
| What failed? | failed and blocked tasks, with the last verdict and the next step |
| What goes wrong most? | failed actions by failure reason, split into infrastructure (tool, model, timeout, budget) and quality |
| What was harder than planned? | retries, rejections, cost over twice the estimate; predicted vs. actual difficulty per task type |
| What might not need AI? | tasks the planner marked `automatable` (with how), tasks that repeat 3+ times and always pass first try, AI checks with 5+ runs that never caught anything |

Plus "missed by the AI checks": work you rejected although the AI verify, edge test or approval had
passed it. That is the most direct measure of how much the AI reviewers can be trusted.
