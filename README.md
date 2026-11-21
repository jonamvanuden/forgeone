# ForgeOne

An agent that **builds, evaluates and improves agents**. ForgeOne is the factory; every agent
it builds lives in **its own repository**. First agent: **VoyagerOne**
([jonamvanuden/VoyagerOne](https://github.com/jonamvanuden/VoyagerOne)), an autonomous web
discovery agent.

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
git clone git@github-voyagerone:jonamvanuden/VoyagerOne.git ../VoyagerOne   # agent repo, see agents.yaml
forgeone build --agent voyagerone   # pushes branch forgeone/<run-id> to VoyagerOne
forgeone control-room           # dashboard on 127.0.0.1:8501
pytest
```

## Layout
```
agents.yaml              which agent repositories ForgeOne manages
src/forgeone/            the product (see ARCHITECTURE.md)
docs/                    Day-by-day documents (HTML + PDF), OPERATIONS.md
scripts/                 maintenance scripts (docs PDF)
tests/                   unit tests (no API key, no network)
```
