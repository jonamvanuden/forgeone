# context/

What each model call sees, and a record of it.

| File | What |
|---|---|
| `pack.py` | `ContextPack`: build a prompt's context by named sections (task, criteria, dependency outputs, feedback, output under review...), with character limits. Produces the **context manifest**: estimated tokens per section, what was cut and how much was lost, and what was left out by rule. |
| `history.py` | Keeps an agent's conversation short: the last 3 tool results stay in full, older results over 1,200 characters become one line. Marks the tools and system prompt for prompt caching. |

The manifest is stored on every action (`actions.context`, migration 007), next to the tokens
really billed. `v_context` and the Support page aggregate it: tokens per kind of call, how often
context is cut, and the pass rate with and without cuts.

Rules a context manager must keep (recorded as `excluded` in the manifest):
- reviewers (verify, edge test, approve) never see the executor's reasoning, only the output and facts;
- the builder never sees the agent's eval suite;
- an executor sees only its direct dependencies and the feedback on its previous attempt, not earlier outputs.

Next: token budgets per model and purpose, all feedback from every attempt, lessons from earlier
goals, and context variants in the evals.
