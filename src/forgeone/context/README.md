# context/

What each model call sees, and a record of it.

| File | What |
|---|---|
| `pack.py` | `ContextPack`: build a prompt's context by named sections (task, criteria, dependency outputs, feedback, output under review...), with character limits. Produces the **context manifest**: estimated tokens per section, what was cut and how much was lost, and what was left out by rule. |
| `budget.py` | Token budget per kind of call (`TARGETS`: execute 24k, verify 16k, agent 40k, …), scaled by the policy and capped at 60% of the model's context window. `pack.fit(budget)` shortens, then leaves out low-priority parts (lessons, earlier attempts, dependencies), then cuts required parts. |
| `policy.py` | Context policies: `lean` (0.5x budget, no memory, 1 page in full), `default`, `rich` (1.5x, 5 lessons, 6 pages in full), or overrides like `{base: default, keep_full: 5}`. Stored on the goal (`--context`) and on eval variants (`context:`). |
| `history.py` | Keeps an agent's conversation short: the last 3 tool results stay in full, older results over 1,200 characters become one line. Marks the tools and system prompt for prompt caching. |

The manifest is stored on every action (`actions.context`, migration 007), next to the tokens
really billed. `v_context` and the Support page aggregate it: tokens per kind of call, how often
context is cut, and the pass rate with and without cuts.

Rules a context manager must keep (recorded as `excluded` in the manifest):
- reviewers (verify, edge test, approve) never see the executor's reasoning, only the output and facts;
- the builder never sees the agent's eval suite;
- an executor sees only its direct dependencies and what went wrong in its earlier attempts, not earlier outputs.

Memory goes in through here too: every earlier failed attempt of the task (`history`) and lessons
from similar failed tasks of earlier goals (`lessons`, from `memory/lessons.py`). Policies are
compared on the Support page and in `v_context_policy`.
