# ForgeOne – Operations Cheat Sheet

All commands from `~/ForgeOne` with the venv active: `source .venv/bin/activate`.

## Deploy a change from GitHub
```bash
cd ~/ForgeOne && git pull && pip install -e . -q && systemctl restart forgeone-dashboard
forgeone db migrate                       # only needed when a migration was added; always safe
```

## ForgeOne
```bash
forgeone build --agent voyagerone         # build an agent (old: python -m forgeone.graph, still works)
forgeone eval validate --agent voyagerone # check the eval suite
forgeone eval checks                      # list programmatic checks (HARD = gate)
forgeone db status                        # row counts + latest runs
forgeone db backfill                      # load runs/*/events.jsonl into Postgres (safe to repeat)
forgeone db migrate                       # apply new schema migrations (safe to repeat)
pytest -q                                 # unit tests, no API key needed
```

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
| Agents (goal + eval suite) | `~/ForgeOne/agents/<agent>/` |
| Run logs | `~/ForgeOne/runs/<run_id>/` |
| Secrets | `~/ForgeOne/.env` (template: `.env.example`) |
| Docs | `~/ForgeOne/docs/` and the Project docs page |
