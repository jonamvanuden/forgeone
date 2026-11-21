# ForgeOne – Operations Cheat Sheet

## Dashboard (Streamlit service)
```bash
systemctl start forgeone-dashboard       # start
systemctl stop forgeone-dashboard        # stop
systemctl restart forgeone-dashboard     # restart (after changing dashboard code)
systemctl status forgeone-dashboard --no-pager   # is it running?
journalctl -u forgeone-dashboard -f      # live logs (Ctrl+C to exit)
journalctl -u forgeone-dashboard -n 50   # last 50 log lines
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8501   # 200 = up
```
After editing the service file itself (`/etc/systemd/system/forgeone-dashboard.service`):
```bash
systemctl daemon-reload && systemctl restart forgeone-dashboard
```

## nginx (public site: https://cashgrab.online)
```bash
nginx -t                       # check config first, ALWAYS
systemctl reload nginx         # apply config changes (no downtime)
systemctl restart nginx        # full restart (only if reload isn't enough)
tail -n 30 /var/log/nginx/error.log
```
Config: `/etc/nginx/sites-available/cashgrab.online`

## Database (Postgres in Docker), run from ~/ForgeOne
```bash
docker compose up -d           # start
docker compose stop            # stop (data kept)
docker compose restart         # restart
docker compose ps              # status (want: healthy)
docker compose logs db --tail 30
docker exec -it forgeone-db psql -U forgeone   # SQL shell (\q to exit)
```
⚠️ `docker compose down -v` deletes ALL data.

## ForgeOne, run from ~/ForgeOne
```bash
source .venv/bin/activate                 # every new terminal
python -m forgeone.graph                  # run a build
python -m forgeone.store status           # DB row counts + latest runs
python -m forgeone.store backfill         # load runs/ JSONL into DB (safe to repeat)
python -m py_compile