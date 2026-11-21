# workspace

ForgeOne is the factory; every agent it builds lives in **its own git repository**.

## The agent repository contract
```
<Agent>/
  forgeone.yaml      manifest (name, description, owner)        owner
  goal.md            what ForgeOne should build                  owner
  evals/suite.yaml   the eval suite                              owner ONLY
  agent/spec.yaml    the built agent                             ForgeOne
  agent/BUILD.md     how this version was built                  ForgeOne
```

## Rules enforced in code
- The builder reads `goal.md` only. It never reads or writes `evals/` (Goodhart's law).
- `publish()` writes only inside `agent/`; staging anything else aborts the commit.
- Before a build (and before any tokens are spent) the repo must be a clean checkout of its
  base branch; ForgeOne pulls it first.
- Default publish mode is a branch `forgeone/<run-id>`, pushed to the remote. Merging the
  branch is the approval. On any error the repo is restored to how it was.

## Registry
`agents.yaml` in the ForgeOne root lists the agents and where their checkouts are:
```yaml
agents:
  voyagerone:
    path: ../VoyagerOne
    remote: git@github-voyagerone:jonamvanuden/VoyagerOne.git
    branch: main
```
