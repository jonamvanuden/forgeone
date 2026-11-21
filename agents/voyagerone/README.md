# VoyagerOne

An autonomous web discovery agent: it takes a research command, searches and reads the web,
cross-checks facts across sources and writes a sourced markdown report.

| Path | What | Who edits it |
|---|---|---|
| `goal.md` | The goal ForgeOne builds from | You |
| `evals/suite.yaml` | The fixed eval suite that decides promotion | You, never the builder |

Build it: `forgeone build --agent voyagerone`
Check the suite: `forgeone eval validate --agent voyagerone`
