# work/

How ForgeOne plans and does work, the same for every kind of agent:

```
Goal ──► Milestones (1..10, each with "done when")
           └─► Tasks (type, difficulty, risk, acceptance criteria, dependencies, estimates)
                 └─► Actions: one row per attempt at a stage
                       execute ─► verify ─► edge_test ─► approve (AI or human)
```

| File | What |
|---|---|
| `model.py` | The records (Goal, Milestone, Task, Action) and the fixed lists: task types, failure reasons, statuses |
| `policy.py` | Rules with a reason: stages per risk, who approves, which model, estimates, actual difficulty |
| `planner.py` | Turns the planner model's drafts into records (codes like 2.3, dependency ids) |
| `brain.py` | Every model call: prompt files, prompt version hash, tokens, cost |
| `prompts/` | One file per call; their hash is stored on each action |
| `executors.py` | Execute stage: one model call, or a run of the goal's agent with browser tools |
| `engine.py` | Plan, run, retry with feedback, budgets, human approvals, replanning |
| `playbooks.py` | Fixed plans for known processes (e.g. `build_agent` in builder/playbook.py) and their code steps |
| `approvals.py` | `forgeone approve` / `reject`, for sign-offs and go-aheads |
| `store.py` | Postgres (migration 004) and an in-memory store for tests |
| `support.py` | Read-only analysis for the Support page and `forgeone support`: failures, harder than planned, planner calibration, what might not need AI, what the AI checks missed |

Policy, first version:

| Risk | Stages | Approver |
|---|---|---|
| low | execute, verify | none |
| medium | execute, verify, edge_test, approve | AI signs off the output |
| high | execute, verify, edge_test, approve | you sign off the output |
| cannot be undone | approve, execute, verify | you give the go-ahead before it runs |

Playbook tasks declare their own stages; a step task can be verified by its checks alone.

Milestones end with your approval for `supervised` goals, with an AI approval for `autonomous` ones.
Autonomous goals may replan themselves up to 2 times when a milestone fails.
