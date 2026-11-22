# memory

What ForgeOne learned from earlier work, given to the next model call through `context/`.

| File | What |
|---|---|
| `lessons.py` | Lessons from failed checks and rejections of similar tasks (same type, similar title), in this goal or earlier ones: the reason, the fix that was asked for, and whether it passed afterwards. Your rejection notes rank first. No model call: every lesson points to its source task. |

Also part of memory, built into the execute context: the feedback of **every** earlier attempt
of the same task, not only the last one. Both are switched by the context policy
(`attempt_history`, `lessons`; off in `lean`) and are the first parts trimmed by a token budget.
