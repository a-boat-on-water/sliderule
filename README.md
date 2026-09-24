# sliderule

Small licensed-profession firms (MEP, structural, architecture) use sliderule to source, filter
and contact part-time / contract engineers, starting from public permit-filing data. Multi-tenant
from day one; a human approves every person before any outreach, and the state machine in
`sliderule/transition.py` is the only writer of pipeline stage.

## Setup

```sh
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env   # fill in the Neon dev-branch DATABASE_URL and keys
```

## Migrations

Alembic reads `DATABASE_URL` from the environment (`.env` is loaded). Migrations are raw SQL —
no models, no autogenerate.

```sh
alembic upgrade head       # apply
alembic downgrade base     # roll back
```

## Run

```sh
uvicorn sliderule.api.main:app --reload    # API
python -m sliderule.worker                 # worker (polls jobs)
```

To pull NYC DOB filings, enqueue a `sync_filings` job (payload
`{"source": "nyc_dob", "since": "YYYY-MM-DD"}`) — the worker does the rest.

## Environments

| | database (Neon branch) | code | runs |
|---|---|---|---|
| dev | `dev` | your working tree | locally (`uvicorn` + `next dev`) |
| ci | `ci` (child of dev) | the PR | GitHub Actions, serialized |
| prod | `production` | `main` only | docker compose (`docker-compose.prod.yml`) |

`main` is protected: PRs merge only when CI is green. Deploy is
`docker compose -f docker-compose.prod.yml up -d --build` on the prod host
with secrets in `.env.production` (see `.env.production.example`), after
`... run --rm api alembic upgrade head` for migrations.

## Tests

Tests run against the Neon **dev** branch via `DATABASE_URL` and truncate every table between
tests — never point them at production. They are skipped when `DATABASE_URL` is unset. No test
hits a paid API or the network.

```sh
pytest
```
