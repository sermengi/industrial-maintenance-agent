# Phase 0 (Walking Skeleton) — Implementation Decisions

Captured from planning discussion, 2026-08-14. These are decisions made ahead of implementation, refining the Implementation Plan v1.0 / Phase 0 without contradicting it. Nothing here has been implemented yet.

## Repo & package

- Repo name: `industrial-maintenance-agent`
- Import package name: `maintenance_agent` (under `src/`)
- Layout: **src-layout** (`src/maintenance_agent/...`), not flat — prevents accidental imports of uninstalled local code, standard for the "modern typed codebase" baseline.
- Dependency/package manager: **uv** — single tool for venv + lockfile + install, fast Docker layer caching, `pyproject.toml` + committed `uv.lock`.

## Directory skeleton (Phase 0 subset only — later phases add siblings, not created yet)

```
industrial-maintenance-agent/
├── src/maintenance_agent/
│   ├── main.py            # FastAPI app instance, startup/lifespan
│   ├── api/
│   │   ├── health.py      # /health route (liveness-only)
│   │   └── agent.py       # /agent/query route (stub)
│   ├── schemas/
│   │   └── agent.py       # Pydantic request/response models
│   ├── core/
│   │   └── config.py      # Settings (pydantic-settings)
│   └── db/
│       └── session.py     # async engine/session, connectivity check
├── tests/
├── .github/workflows/ci.yml
├── docker/Dockerfile
├── docker-compose.yml
├── pyproject.toml
├── uv.lock
├── .env.example
└── README.md
```

Reserved for later phases (not created in Phase 0): `db/repositories/`, `db/models/` (Phase 1), `tools/` (Phase 2), `rag/` (Phase 3), `graph/`, `llm/` (Phase 4), `telemetry/` (Phase 7).

## Config strategy

- `core/config.py`: one `Settings(BaseSettings)` class (pydantic-settings), exposed via a cached `get_settings()` FastAPI dependency.
- Phase 0 fields: `database_url` (or discrete host/port/user/password/db), `app_env`, `api_host`/`api_port`, `log_level`.
- `.env.example` committed (documents every required var with placeholder/dummy values); `.env` gitignored (real local values).
- `docker-compose.yml` reads local dev config via `env_file: .env`, so Compose and the app share one source of truth locally.
- CI supplies its own env vars directly through the GitHub Actions workflow file, not via any committed `.env`.
- Local Postgres credentials in `docker-compose.yml` are not real secrets (local-only containers) — fine to ship a working default (e.g. `postgres:postgres`) in `.env.example`. The "no committed secrets" rule starts to matter once real credentials (cloud DB, or an LLM API key in Phase 4) enter the picture; the `.env`/`.env.example` pattern is already in place for that transition.

## `/agent/query` request/response envelope

Designed to stay stable through Phase 8 — later phases fill in fields rather than reshaping the envelope. Phase 0 stub returns schema-valid placeholders (see below).

**Request**
- `query: str` (required) — free-text user request
- `asset_id: Optional[str]` — optional explicit asset hint (convenience for testing/debugging without depending on NLU asset extraction)
- `fault_code: Optional[str]` — optional explicit fault code hint

The agent still validates/resolves the asset via `resolve_asset` regardless of whether `asset_id` is supplied, so NLU asset resolution is still exercised end-to-end for golden scenarios; the hint fields just make it possible to test other tools/paths without fighting NLU ambiguity.

**Response**
- `request_id: str` (UUID) — correlates this response to its Phase 7 telemetry event
- `status: Literal["ok", "needs_approval", "insufficient_evidence", "unknown_asset", "error"]` — single mutually-exclusive enum, not boolean flags (deterministically assertable in Phase 8 golden tests)
- `asset_id: Optional[str]` — the asset actually resolved by `resolve_asset`, if any
- `answer: Optional[str]` — synthesized recommendation/answer text
- `confidence: Optional[Literal["confirmed", "hypothesis"]]` — Phase 5's grounding distinction (e.g. PUMP-104 hypothesis vs. confirmed fact)
- `structured_evidence: list[...]` — DB-sourced evidence items, kept as its own typed list (**not** merged with document evidence — mirrors the dataset spec's "3 evidence layers stay conceptually distinct" principle)
- `document_evidence: list[...]` — RAG citations (`document_id`, `section`, `excerpt`), matching the Phase 3 corpus metadata contract
- `pending_action: Optional[...]` — Phase 6 HITL reference (`action_type`, `draft_id`), populated when `status == needs_approval`
- `error: Optional[...]` — `code` + `message`, populated when `status == error`

Phase 0 stub behavior: `status="ok"`, empty evidence lists, static placeholder `answer`, freshly generated `request_id`, everything else `None`.

## CI / linting / type checking

- **ruff**: linting + formatting (replaces flake8/isort/black with one fast tool).
- **mypy**: type checking, consistent with the "modern typed codebase" baseline.
- CI order: `uv sync` → `ruff check` → `ruff format --check` → `mypy` → `pytest`.
- No pre-commit hooks in Phase 0 — not a stated task, and adding one now would be scope beyond what the plan asks for. Can be added later without disrupting anything if desired.

## Health check & DB readiness

- `/health` is **liveness-only**: returns 200 as soon as the process is up, independent of DB state. Matches the plan's own test bullets, which list "/health returns success" and "app can connect to Postgres" as two separate checks. No third endpoint is added — Phase 0 only names `/health` and `/agent/query`.
- DB connectivity is verified via:
  - A FastAPI lifespan startup hook that attempts a DB connection at boot and fails container startup if unreachable (fail-fast behavior).
  - A dedicated pytest integration test that opens an async session against Compose-managed Postgres and runs a trivial query.
- `docker-compose.yml`: the `postgres` service gets a healthcheck (`pg_isready`); the `api` service uses `depends_on: postgres: condition: service_healthy`, so Compose doesn't start the API before Postgres is actually accepting connections (avoids flaky local/CI startup races).
- The startup hook is kept as defense-in-depth even with the Compose healthcheck in place (covers restarts, local dev without healthcheck-aware tooling, etc.).

## Test / Validation

Concrete, checkable version of the plan's generic Phase 0 test bullets, expanded against the decisions above. Intended as a checklist a coding agent can run through to confirm the implementation matches what was decided here.

**Repo & structure**
- [ ] Repo exists at `industrial-maintenance-agent`; package importable as `maintenance_agent` from `src/`.
- [ ] `pyproject.toml` and `uv.lock` present at repo root; `uv sync` succeeds from a clean clone.
- [ ] Directory skeleton matches the Phase 0 subset above (`api/`, `schemas/`, `core/`, `db/` under `src/maintenance_agent/`; `tests/`; `docker/Dockerfile`; `docker-compose.yml`; `.github/workflows/ci.yml`; `.env.example`; `README.md`).

**Config & secrets**
- [ ] `.env.example` committed and lists every required variable with placeholder values; `.env` is gitignored and never appears in `git status` or `git log`.
- [ ] `Settings` loads successfully from environment variables with no import-time crash when required vars are present.
- [ ] No real secret values appear anywhere in the committed repo (spot-check `docker-compose.yml`, CI workflow, `.env.example`).

**API — `/health`**
- [ ] `GET /health` returns HTTP 200 from the containerized service.
- [ ] `GET /health` still returns 200 even if Postgres is briefly stopped — confirms liveness-only semantics (not combined readiness).

**API — `/agent/query`**
- [ ] `POST /agent/query` with a minimal valid body (`{"query": "..."}`) returns HTTP 200.
- [ ] Response validates against the documented schema: `request_id` is a valid UUID string; `status == "ok"`; `structured_evidence == []`; `document_evidence == []`; `pending_action is None`; `error is None`; `answer` is a non-empty placeholder string.
- [ ] Request body also accepts optional `asset_id` and `fault_code` without validation error (stub doesn't need to act on them yet).
- [ ] Two consecutive calls return different `request_id` values (confirms per-request generation, not a hardcoded constant).

**Database connectivity**
- [ ] `docker compose up`: the `postgres` service reports healthy (`pg_isready`) before the `api` service starts (`depends_on: condition: service_healthy` observed in start order/logs).
- [ ] FastAPI lifespan startup hook successfully opens a DB connection against Compose-managed Postgres; visible in container startup logs.
- [ ] If the DB is unreachable at `api` startup, the `api` container fails fast (non-zero exit / clear error) instead of starting in a broken state.
- [ ] A dedicated pytest integration test opens an async session against Compose-managed Postgres and runs a trivial query (e.g. `SELECT 1`) successfully.

**CI / linting / type checking**
- [ ] `ruff check .` passes with zero errors.
- [ ] `ruff format --check .` passes (no formatting diffs).
- [ ] `mypy` passes with zero errors on `src/`.
- [ ] `pytest` passes locally and in GitHub Actions.
- [ ] GitHub Actions workflow runs on push/PR, executing in order: `uv sync` → `ruff check` → `ruff format --check` → `mypy` → `pytest`; green on the initial repository.

**Docker / Compose**
- [ ] `docker compose up` from a fresh clone (only documented pre-steps, e.g. copying `.env.example` to `.env`) brings up a healthy `api` + `postgres` stack.
- [ ] No values beyond what's in `.env.example` are required for `docker compose up` to succeed locally.

**Documentation**
- [ ] README documents the exact local startup command(s) and includes one example request against `/agent/query` with a sample response.

## Success Criteria

- [ ] A fresh clone reaches a valid `/agent/query` response using only documented commands — no undocumented manual steps.
- [ ] `docker compose up` produces a healthy API and database with no manual intervention.
- [ ] CI passes on the initial repository — ruff, mypy, and pytest all green.
- [ ] The `/agent/query` response matches the documented envelope exactly (field names, types, enum values) — this is the contract every later phase (1 through 8) builds on without reshaping it.
- [ ] `/health` and DB connectivity behave as verifiably independent checks (liveness vs. readiness), per the Health check & DB readiness decision.
- [ ] No secrets are committed to the repository.
- [ ] Repository structure matches the agreed skeleton, so Phase 1 can add `db/repositories/` and `db/models/` without restructuring existing code.

## Status

All Phase 0 pre-implementation decisions from this planning conversation are resolved, including concrete Test/Validation and Success Criteria checklists above. Next: proceed to implementation, or move on to Phase 1 planning discussion.