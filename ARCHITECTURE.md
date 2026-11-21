# Architecture

ForgeOne is one Python package (`src/forgeone`) organised by **capability**. Each folder owns
one responsibility and has its own README when it is not built yet.

```
                       ┌──────────────── evals ────────────────┐
 agents/<a>/goal.md    │ suite → trace → checks/judges → score │
        │              │              → gate (promote?)        │
        ▼              └───────────────▲───────────────────────┘
     builder ── spec ──► runtime ── tools          │ traces
        │                   │                      │
        └──── llm ──────────┴──── observability ───┴──► store ──► control_room
```

| Package | Owns | Status |
|---|---|---|
| `builder/` | goal → AgentSpec; later the test → judge → repair loop. Nodes, prompts (`prompts/*.md`), state | working |
| `spec/` | Contracts: `AgentSpec`, the tool allowlist | working |
| `evals/` | **The core.** Suites, traces, programmatic checks, judges + rubrics, scorecards, promotion gate | checks, scoring, gate working; runner + judge next |
| `runtime/` | Runs an AgentSpec as an agent and returns an `AgentTrace` | Day 2 |
| `tools/` | Tool implementations (Playwright browser, report) | Day 2 |
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
4. **The builder never reads an eval suite.** Suites live in `agents/<a>/evals/` and are
   loaded only by `evals`. This keeps evaluation honest (Goodhart's law).
5. Every schema change is a new file in `store/migrations/`; applied files are never edited.
6. Prompts are files, not strings in code, so prompt changes show up in diffs.

## Data flow of one build
`forgeone build` → `builder.run_build` → `start_run` (run_id, events.jsonl, Postgres sink)
→ `plan` node → `build` node (structured `AgentSpec`) → artifact `spec` → `runs/<id>/spec.yaml`.
