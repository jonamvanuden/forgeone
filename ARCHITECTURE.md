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
| `builder/` | goal → AgentSpec; later the test → judge → repair loop. Nodes, prompts (`prompts/*.md`), state | working |
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
3. `control_room` only **reads** from the store. It never imports builder or runtime.
4. **The builder never reads an eval suite.** Suites live in the agent repo's `evals/` and are
   loaded only by `evals`. This keeps evaluation honest (Goodhart's law).
5. **ForgeOne writes only inside an agent repo's `agent/` folder**, always via
   `workspace.AgentRepo.publish`, which refuses anything else.
6. Every schema change is a new file in `store/migrations/`; applied files are never edited.
7. Prompts are files, not strings in code, so prompt changes show up in diffs.

## Data flow of one build
`forgeone build --agent voyagerone` → `workspace`: agent repo must be a clean `main`, pull
(fails here, before any tokens are spent) → `start_run` (run_id, events.jsonl, Postgres sink)
→ `plan` → `build` (structured `AgentSpec`) → `runs/<id>/spec.yaml` → `publish`: commit
`agent/spec.yaml` + `agent/BUILD.md` on branch `forgeone/<run-id>`, push → decision `published`.

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
