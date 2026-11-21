# {title}

{description}

Built and evaluated by [ForgeOne](https://github.com/jonamvanuden/ForgeOne).

| Path | What | Who edits it |
|---|---|---|
| `goal.md` | What ForgeOne should build | You |
| `evals/suite.yaml` | The eval suite that decides which version is better | You, never ForgeOne |
| `agent/` | The agent ForgeOne built (`spec.yaml`, `BUILD.md`) | ForgeOne, via branches |
| `forgeone.yaml` | Manifest | You |

## How a new version arrives
1. On the server: `forgeone build --agent {name}`
2. ForgeOne pushes a branch `forgeone/<run-id>` with the new `agent/spec.yaml`.
3. Review the diff on GitHub and merge it to approve.
