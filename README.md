# ForgeOne

An agent that **builds, evaluates and improves agents**. First agent: **VoyagerOne**, an
autonomous web discovery agent.

The differentiator is not building agents (anyone can) but **evaluating** them: a fixed eval
suite, programmatic checks, trustworthy judges and a promotion gate decide which agent
version is better, and why. See `docs/` (Day 1, chapter 12) and `src/forgeone/evals/`.

## Quick start
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env            # fill in the key and passwords
docker compose up -d            # Postgres
forgeone db migrate
forgeone build --agent voyagerone
forgeone control-room           # dashboard on 127.0.0.1:8501
pytest
```

## Layout
```
agents/<agent>/          one folder per agent: goal.md, evals/suite.yaml
src/forgeone/            the product (see ARCHITECTURE.md)
docs/                    Day-by-day documents (HTML + PDF), OPERATIONS.md
scripts/                 maintenance scripts (docs PDF)
tests/                   unit tests (no API key, no network)
```
