# ForgeOne – Operations Cheat Sheet

All commands from `~/ForgeOne` with the venv active: `source .venv/bin/activate`.

## Deploy a change from GitHub
```bash
cd ~/ForgeOne && git pull && pip install -e . -q && systemctl restart forgeone-dashboard
forgeone db migrate                       # only needed when a migration was added; always safe (004 = goals)
```

## Agent repositories
ForgeOne builds into each agent's own repo (registered in `agents.yaml`). VoyagerOne lives in
`~/VoyagerOne`, next to `~/ForgeOne`.
```bash
forgeone agent list                       # registered agents and whether they are cloned
forgeone agent status voyagerone          # branch, clean/dirty, goal/suite/spec present
cd ~/VoyagerOne && git pull               # after merging a forgeone/<run-id> branch on GitHub
```
A build needs the agent repo on a clean `main`. ForgeOne pulls it, builds, and pushes a branch
`forgeone/<run-id>` with `agent/spec.yaml` + `agent/BUILD.md`. Merge it on GitHub to approve.
`--publish main` commits straight to main; `--publish none` leaves the repo untouched.

## ForgeOne
```bash
forgeone build --agent voyagerone         # build + publish to VoyagerOne (old: python -m forgeone.graph)
forgeone eval validate --agent voyagerone # check the eval suite
forgeone eval checks                      # list programmatic checks (HARD = gate)
forgeone db status                        # row counts + latest runs
forgeone db backfill                      # load runs/*/events.jsonl into Postgres (safe to repeat)
forgeone db migrate                       # apply new schema migrations (safe to repeat)
pytest -q                                 # unit tests, no API key needed
```

## Goals (the main way to give ForgeOne work)
```bash
forgeone goal new "What does WHO advise on free sugars?" --agent voyagerone --budget 1 \
     --criteria "A 150-word summary with the limits and a source" --constraint "Only official WHO sources"
forgeone goal new "..." --file brief.md --autonomy autonomous --run   # plan and start right away
forgeone goal list                         # all goals with progress and spend
forgeone goal show G-xxxxxx [--actions]    # milestones, tasks, stages (E V T A), what waits for you
forgeone goal run G-xxxxxx [--max-tasks 3] # work until done, a human approval, the budget or the limit
forgeone approve T-xxxxxx [--note "..."]   # a task or milestone waiting for you (or the goal id if one waits)
forgeone reject T-xxxxxx --note "use the 2015 guideline"   # runs again with your note as feedback
forgeone goal replan G-xxxxxx --reason "M3 was the wrong approach"
forgeone goal budget G-xxxxxx 2.50         # raise the budget of a paused goal, then goal run again
forgeone goal cancel G-xxxxxx
```
Supervised (default): you approve each milestone, plus every high-risk task. Autonomous: an AI
approver signs off milestones and the goal may replan itself twice. Without `--agent` there is no web
access: tasks are done by plain model calls. Approvals never continue the goal by themselves: run
`forgeone goal run` again. The dashboard's **Needs you** page lists everything waiting, with the command.
Optional: `FORGEONE_STRONG_MODEL` (difficulty 4–5, default claude-sonnet-5-5), `FORGEONE_JUDGE_MODEL`
(verify, edge test, approve; default the builder model).

## Running and evaluating agents
One-time on the server: `pip install -e . && playwright install --with-deps chromium`.
The agent version used is `agent/spec.yaml` on VoyagerOne's main (merge a build first),
or pass `--spec runs/<run-id>/spec.yaml` to try an unmerged build.
```bash
forgeone run "What does WHO advise on free sugars?"       # real task; report in runs/<run-id>/
forgeone label <run-id> good --note "checked the sources" # your verdict (bad also works)
forgeone eval run --agent voyagerone                       # every variant x case x repeats (costs money!)
forgeone eval run --variant haiku --case factual-who-sugar --repeats 1   # cheap smoke test
forgeone eval report --agent voyagerone                    # what works per task type, per variant
```
Variants live in `~/VoyagerOne/evals/variants.yaml` (owner-only, like the suite).
A full suite run is 10 cases x 3 repeats per variant: start with `--case` and `--repeats 1`.
Agents can't open local or private addresses, or file://. For local tests only:
`FORGEONE_ALLOW_PRIVATE_URLS=1`.

## Dashboard (systemd service)
```bash
systemctl restart forgeone-dashboard
systemctl status forgeone-dashboard --no-pager
journalctl -u forgeone-dashboard -f
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8501   # 200 = up
```
Service file `/etc/systemd/system/forgeone-dashboard.service`, ExecStart:
`/root/ForgeOne/.venv/bin/uvicorn forgeone.control_room.server:app --host 127.0.0.1 --port 8501 --proxy-headers`
After editing it: `systemctl daemon-reload && systemctl restart forgeone-dashboard`

## nginx (public: https://cashgrab.online)
```bash
nginx -t && systemctl reload nginx
tail -n 30 /var/log/nginx/error.log
```

## Database (Postgres in Docker)
```bash
docker compose up -d
docker compose ps                         # want: healthy
docker exec -it forgeone-db psql -U forgeone
```
⚠️ `docker compose down -v` deletes ALL data.

## Where things live
| What | Where |
|---|---|
| Code | `~/ForgeOne/src/forgeone/` |
| Agents (goal, eval suite, built spec) | their own repos, e.g. `~/VoyagerOne/` |
| Which agents exist | `~/ForgeOne/agents.yaml` |
| Run logs | `~/ForgeOne/runs/<run_id>/` |
| Secrets | `~/ForgeOne/.env` (template: `.env.example`) |
| Docs | `~/ForgeOne/docs/` and the Project docs page |
