# Industrial Maintenance Agent

Phase 0 scaffold for a FastAPI-based industrial maintenance assistant.

## Development

```bash
uv sync
uv run uvicorn maintenance_agent.main:app --reload
```

The API is available at `http://localhost:8000`.

If you copied `.env.example` to `.env`, the default `DATABASE_URL` is empty so
the Phase 0 stub endpoints can run without Postgres. Set it to a local Postgres
URL only when you want direct host-local database connectivity.

## Endpoints

- `GET /health`
- `POST /agent/query`

Example request:

```bash
curl -X POST http://127.0.0.1:8000/agent/query \
  -H "Content-Type: application/json" \
  -d '{"query":"Why is pump A vibrating?","asset_id":"PUMP-104","fault_code":"F-101"}'
```

Example Phase 0 response:

```json
{
  "request_id": "8b6a690d-86e5-4e13-aec2-c718b9f26f4f",
  "status": "ok",
  "asset_id": null,
  "answer": "Agent query handling is not implemented yet.",
  "confidence": null,
  "structured_evidence": [],
  "document_evidence": [],
  "pending_action": null,
  "error": null
}
```

## Tests

```bash
uv run pytest
```

To run the optional database integration test against Compose-managed Postgres:

```bash
cp .env.example .env
docker compose up -d postgres
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/maintenance_agent \
  RUN_DB_INTEGRATION=1 uv run pytest tests/test_db.py
```

To migrate and reseed the deterministic Phase 1 fixtures:

```bash
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/maintenance_agent \
  uv run maintenance-agent-db reset

TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/maintenance_agent_test \
  uv run maintenance-agent-db reset --database test
```

CI also runs:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src
```

## Docker

```bash
cp .env.example .env
docker compose up --build
```

Compose overrides the API container's `DATABASE_URL` to use the internal
`postgres` service hostname while reusing the same local database credentials
from `.env`.

The checked-in `.env.example` contains local-only placeholder values. Put real
credentials, API keys, and deployment-specific values in an untracked `.env` or
your deployment secret store.
