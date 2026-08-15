# Phase 1 (Dataset & Database Bootstrap) — Implementation Decisions

Captured from planning discussion, 2026-08-15. These are decisions made ahead of implementation, refining Implementation Plan v1.0 / Phase 1 and Dataset Design Specification v1.1 without contradicting either. Nothing here has been implemented yet. Decisions are locked task-by-task, following the Phase 1 task list from the implementation plan:

1. Translate the Dataset Design Specification v1.1 into migrations/schema definitions. **(locked)**
2. Implement tables/entities for assets, telemetry snapshots, fault events, maintenance events, observations, work orders, fault taxonomy, operating limits, and plant policies. **(locked)**
3. Create deterministic seed fixtures for the exact approved records. **(locked)**
4. Add database bootstrap/reset workflow suitable for local development and tests. **(locked)**
5. Expose internal repository/query functions needed by later tools. **(locked)**

All five Phase 1 tasks are locked. See Test/Validation, Success Criteria, and Status at the bottom.

---

## Task 1 — Migrations / schema definitions

### Tooling

- **ORM**: SQLAlchemy 2.0 async ORM (typed declarative models), consistent with the "modern typed codebase" baseline and the async engine/session already established in Phase 0 (`db/session.py`).
- **Migrations**: Alembic.
- **Location**: models live under `db/models/` (reserved but not created in the Phase 0 skeleton; created now).
- Rejected alternatives: raw SQL migrations with no ORM (more hand-written query surface); SQLModel (couples DB schema shape to API/Pydantic schema shape — Phase 0 already separated `schemas/` from `db/`).

### Primary keys

- **Natural business keys as PKs.** The spec's own identifiers (`asset_id` e.g. `PUMP-101`, `event_id` e.g. `FE-001`, `maintenance_id`, `observation_id`, `work_order_id`, `snapshot_id`, `fault_code`, `policy_id`, an operating-limit ID) are used directly as primary keys.
- No surrogate integer/UUID PK columns are added. Rationale: small, manually-inspectable debug dataset — the spec's IDs already uniquely and stably identify each record, and using them directly keeps fixtures and DB rows 1:1 with the spec tables with no extra columns to reconcile.

### Enum / categorical fields

- **`varchar` + `CHECK` constraint** at the DB layer for all categorical fields (asset `status`, `asset_type`; fault `severity`, fault `status`; maintenance `type`; observation `type`, `severity`; work-order `priority`, `status`; policy `type`; operating-limit `source_type`; etc.).
- Pydantic also validates these at the API/tool boundary (defense in depth), but the DB layer does not use Postgres native `ENUM` types.
- Rationale: native Postgres enums require `ALTER TYPE ... ADD VALUE` migrations to extend, which is more invasive than adjusting a `CHECK` constraint. `varchar` + `CHECK` gets equivalent DB-level enforcement with cheaper evolution.

### `operating_limits` threshold representation

- **Structured nullable threshold columns**, not free text and not JSONB.
- Columns include explicit per-tier numeric bounds (e.g. `normal_max`, `warning_min`, `warning_max`, `critical_min`/`critical_max` as applicable to each row's shape), left `NULL` where a tier doesn't apply (e.g. OL-002's 2-tier normal/high rule vs. the other 3-tier normal/warning/critical rules).
- A `rule_text` column also preserves the original human-readable rule wording from the spec (e.g. `"Normal < 4.5; warning 4.5-7.0; critical > 7.0"`) for inspectability and for evidence surfaced to the LLM.
- Rationale: Phase 2's `get_asset_status` needs to classify a telemetry reading (normal/warning/critical) deterministically. Structured numeric columns allow a direct comparison in code with no text parsing; JSONB was considered (handles variable tier count without nullable-column sprawl) but rejected in favor of plain columns for a dataset this small, since Phase 2 shouldn't need a JSON-parsing Pydantic model just to read four rows.

### Other schema decisions (stated as defaults, not separately debated — flag if any should change)

- Table and column names mirror the spec's entity/attribute names in snake_case exactly: `assets`, `telemetry_snapshots`, `fault_events`, `maintenance_events`, `observations`, `work_orders`, `fault_taxonomy`, `operating_limits`, `plant_policies`.
- Foreign keys: `asset_id` on `telemetry_snapshots`, `fault_events`, `maintenance_events`, `observations`, and `work_orders` references `assets.asset_id`.
- `operating_limits.model` and `fault_events.fault_code` remain plain matching columns, not enforced foreign keys — the spec doesn't define a hard referential link from `operating_limits` to a `models` entity (no such entity exists) or require `fault_events.fault_code` to be FK-constrained against `fault_taxonomy` beyond the lookup relationship.
- `plant_policies.condition` and `plant_policies.required_action` are stored as free text, not decomposed into structured rule fields. The PP-001 recurrence check ("same fault ≥3 times within 12 months") will be implemented as a query against `fault_events` in Phase 2's tool logic, not parsed out of the policy row.
- All timestamp columns are `timestamptz` (UTC-aware), even though the spec's sample timestamps (e.g. `2026-08-14 09:00`) are written without an explicit zone. Resolved in Task 3: naive spec timestamps are treated as UTC directly.
- Phase 1 ships as a single Alembic revision that creates all nine tables together, since they're introduced as one coherent deliverable rather than incrementally.

---

## Task 2 — Table / entity definitions

### Numeric type

- **`NUMERIC(fixed precision/scale)`** (e.g. `NUMERIC(6,2)`) for all decimal measurement columns: telemetry values (`vibration_mm_s`, `bearing_temperature_c`, `inlet_pressure_bar`, `discharge_pressure_bar`, `flow_rate_l_min`) and `operating_limits` threshold columns.
- Rejected: `DOUBLE PRECISION` — simpler but not exact; `NUMERIC` avoids floating-point rounding at threshold-comparison edges, consistent with the deterministic/debug-first principle.

### `telemetry_snapshots` cardinality

- **Timestamped rows, no uniqueness constraint.** PK is `snapshot_id`; `asset_id` (FK) and `timestamp` are plain columns, not constrained to one row per asset.
- v1 seed data still has exactly one row per asset. `get_asset_status` (Phase 2) queries "most recent snapshot by timestamp per asset" rather than relying on a 1:1 guarantee.
- Rationale: avoids a schema change later if/when real time-series telemetry ingestion is added post-v1 (the plan's "no late-stage platform rewrite" principle), without adding any actual scope now.

### `work_orders.status`

- **Narrow `CHECK` constraint scoped to what the frozen dataset needs now**: `status IN ('completed')` (the only value present in WO-001/WO-002).
- Phase 6 will widen this constraint via its own migration when `create_work_order_draft` / `submit_work_order` introduce the draft → pending_approval → approved/rejected → submitted lifecycle. Phase 1 does not pre-design Phase 6's HITL states.

### `operating_limits` provenance

- **Short categorical `source_type` + separate `provenance_note`.**
- `source_type` is `CHECK`-constrained to short codes: `'synthetic_plant_config'`, `'manufacturer_reference_adopted'`.
- A separate free-text `provenance_note` column carries the explanatory nuance — most importantly for OL-002: the 82°C bearing-temperature limit is a manufacturer reference value adopted by the synthetic plant, not a literal CP-200 manufacturer specification.
- Rationale: directly satisfies the implementation plan's explicit Phase 1 constraint to "preserve provenance fields for operating limits where applicable," while keeping the category itself machine-checkable.

### Other per-table decisions (stated as defaults — flag if any should change)

- **`assets`**: `asset_type` CHECK-constrained (`'centrifugal_pump'` only, per v1 scope); `model` also CHECK-constrained (`'CP-200'`, `'CP-300'`) for consistency with `operating_limits.model`, reducing the risk of an unmatched model string breaking the operating-limits join; `location` is plain `varchar`, not constrained (open-ended plant location, not a fixed state enum); `installation_date` is `DATE` (no time component in the spec).
- **`fault_events`**: `fault_name` is stored as its own denormalized column (matching the spec's exact-record table in §7.3), even though it duplicates `fault_taxonomy.canonical_name` for the same `fault_code` — kept because the spec explicitly lists it as a structured record field, not because it's the source of truth (that remains `fault_taxonomy`).
- **`maintenance_events`**: `date` is `DATE` (no time in the spec); `component` is plain `varchar`, **not** CHECK-constrained — the spec's own values are inconsistent (`"lubrication"` on ME-005 vs. `"lubrication_system"` on ME-008), and normalizing them would mean editing the frozen dataset, which Phase 1's constraints prohibit.
- **`observations`**: `reported_by` is plain `varchar`, not CHECK-constrained (only `"operator"` appears in v1, but this is an open-ended "who/what reported it" field, not a fixed state).
- **`operating_limits`**: the spec's "Use" column (e.g. "PUMP-102", "PUMP-104 supporting evidence") is **not** stored as a schema column — it's read as documentation in the spec, not a data relationship. Applicability is derived at query time by matching `assets.model = operating_limits.model` (e.g. CP-200 → PUMP-101/102/103, CP-300 → PUMP-104).
- **`fault_taxonomy`**: `canonical_name` and `description` are plain text — this table *is* the taxonomy definition, so no CHECK constraint applies to its own label column.

---

## Task 3 — Deterministic seed fixtures

### Fixture format & storage

- **JSON files per entity**, kept separate from Alembic migrations. One file per table (`assets.json`, `telemetry_snapshots.json`, `fault_events.json`, `maintenance_events.json`, `observations.json`, `work_orders.json`, `fault_taxonomy.json`, `operating_limits.json`, `plant_policies.json`) under a dedicated fixtures directory (e.g. `src/maintenance_agent/db/fixtures/`).
- Fixtures are parsed and Pydantic-validated before insert by a loading script/module built in Task 4 — schema migrations and frozen data stay cleanly separate, and each file is easy to open and diff directly against the spec's §7 tables.
- Rejected: baking the data into an Alembic data migration (couples the frozen dataset to migration history — a data correction would mean a new migration rather than editing a file); plain SQL insert files (bypasses Pydantic validation, duplicates column typing already defined in the ORM models).
- Insertion order follows FK dependency: `assets` and `fault_taxonomy` (independent/reference tables) load first, then the tables that reference `asset_id` (`telemetry_snapshots`, `fault_events`, `maintenance_events`, `observations`, `work_orders`). `operating_limits` and `plant_policies` are also independent of `assets` and can load at any point.
- Implementation note: the JSON loader parses decimal fields in a way that preserves exact values (e.g. `Decimal`-preserving parsing) rather than routing telemetry/threshold numbers through binary floats, so `NUMERIC` columns get exactly what the spec states.

### Timestamp interpretation

- **Naive spec timestamps are treated as UTC directly.** E.g. `"2026-08-14 09:00"` in the spec is stored as `2026-08-14T09:00:00Z`, with no offset applied.
- No plant location or timezone is stated anywhere in the design or dataset docs, so none is invented — this keeps the fixture data traceable 1:1 back to the spec's literal values, consistent with "do not add data not in the spec."

### Test verification source of truth

- **Later verification tests hardcode expected values drawn from the Dataset Design Specification directly** (§7 exact records, §8 asset ground truth) — not from the fixture files themselves.
- Rationale: this is what the Phase 0 test/validation checklist pattern implies and what the plan's Phase 1 validation bullets literally say ("verify PUMP-101 through PUMP-104 ground-truth records... from the specification"). If tests instead re-read the same JSON fixtures as their expected answer, a typo made once while authoring a fixture would be invisible — the test would just agree with itself. Tests hardcoded against the spec catch that class of error.
- This decision governs Phase 1's own validation tests; it doesn't preclude later phases from also doing lightweight structural checks (e.g. row counts) against the fixtures where appropriate.

### Other fixture decisions (stated as defaults — flag if any should change)

- Fixture content is a direct, literal transcription of Dataset Design Specification v1.1 §7 — all 37 records across the 9 entities, with no added, omitted, or computed/derived values beyond what Task 2 already specified structurally (e.g. `operating_limits` nullable threshold tiers, `source_type` short codes, `provenance_note` text).
- The mechanics of *loading* these fixtures into a running database (idempotency behavior, reset/bootstrap command, CLI entry point) are Task 4's concern, not decided here — Task 3 only fixes what the frozen data looks like and where it lives.

---

## Task 4 — Database bootstrap/reset workflow

### Reset mechanism (idempotency)

- **Truncate all Phase 1 tables + fresh insert from fixtures, inside a transaction.** Tables are truncated in FK-safe order, then repopulated directly from the Task 3 JSON fixtures.
- Bootstrap (empty DB, first run) and reset (already-seeded DB) call the exact same routine — there is no separate code path for "first time" vs. "already has data." Migrations are applied first (`alembic upgrade head`, itself idempotent via Alembic's version tracking), then the truncate+insert step runs unconditionally.
- Rejected: an upsert-based loader (ON CONFLICT DO UPDATE) — avoids destructive truncation but requires insert-vs-update branching per table, and would let a row added outside the fixtures during ad hoc debugging silently survive a "reset," which conflicts with the "reset should exactly match the fixtures" intent. Also rejected: full DB/schema drop-and-recreate — correct but heavier and slower to run repeatedly than truncating known tables, given the whole schema is one migration anyway.

### Test database isolation

- **Dedicated test database on the same Compose-managed Postgres service** — a second database (e.g. `maintenance_agent_test`) alongside the dev database, with its own connection URL (e.g. a `test_database_url` field on `Settings`, sourced from a `TEST_DATABASE_URL` env var).
- The Postgres container provisions both databases declaratively on first start (e.g. via a `docker-entrypoint-initdb.d` init script), rather than having the CLI create the test database at runtime with elevated privileges.
- Tests run the same migrate + truncate + insert routine against `TEST_DATABASE_URL` (e.g. from a pytest fixture/conftest hook) before exercising repository/query functions — fully isolated from whatever state the dev database is in from manual poking.
- Rejected: sharing the dev database between tests and manual work (risk of state bleeding, can't run tests while poking around manually); ephemeral per-run containers via testcontainers (fully isolated but adds a new dependency beyond the Compose Postgres already established in Phase 0).

### Invocation

- **A small CLI exposed via a `project.scripts` entry point, built on stdlib `argparse`** (no new dependency). E.g. `uv run maintenance-agent-db reset` runs migrations to head against the target database, then truncates and reseeds it. A `--database`/`--env` style flag (or simply pointing at `DATABASE_URL` vs `TEST_DATABASE_URL`) selects dev vs. test target.
- Since the reset mechanism is unconditional (Task 4, above), a single `reset` subcommand covers both "bootstrap" and "reset" — there's no separate `bootstrap` command with different behavior.
- Rejected: plain `python -m` scripts (two module paths to remember instead of one discoverable command); a Makefile wrapper (introduces Make as a second command surface alongside the uv-run-based workflow already established in Phase 0).

### Other bootstrap/reset decisions (stated as defaults — flag if any should change)

- `.env.example` gains a `TEST_DATABASE_URL` placeholder alongside the existing `DATABASE_URL`, consistent with Phase 0's config strategy (no real secrets, `.env` gitignored).
- `docker-compose.yml`'s `postgres` service is extended with an init script creating the `maintenance_agent_test` database at container first-start, so `docker compose up` alone is sufficient to make both databases available without a manual `CREATE DATABASE` step.
- The CLI reuses the same SQLAlchemy async engine/session and Pydantic-validated fixture-loading code built in Task 3 — no separate implementation path for "test setup" vs. "dev bootstrap."

---

## Task 5 — Internal repository/query functions

### Return type

- **Repository functions return typed Pydantic read-models**, not raw SQLAlchemy ORM instances. Each entity gets a small Pydantic record class (e.g. `AssetRecord`, `TelemetrySnapshotRecord`, `FaultEventRecord`) that a repository function maps ORM rows into before returning.
- Rationale: decouples callers (Phase 2's tools) from SQLAlchemy internals and avoids detached-instance/lazy-loading pitfalls once results leave the session's scope. Phase 2's own task list already calls for "Pydantic input/output contracts for all implemented tools," so repository-level read-models give that layer already-typed data to assemble rather than raw ORM objects to convert.
- These read-model classes live in one shared module, `db/repositories/records.py`, imported by each per-entity repository module — kept distinct from both the SQLAlchemy models in `db/models/` and the API-facing request/response schemas in `schemas/agent.py`.

### Repository pattern

- **Plain async functions grouped one module per entity** under `db/repositories/` (reserved for Phase 1 in the Phase 0 skeleton) — e.g. `db/repositories/assets.py`, `db/repositories/telemetry.py`, etc. No repository classes, no DI container.
- Rationale: matches the project's debug-first, minimal-ceremony posture; a plain function is trivial to unit test by passing it a session directly, with no instantiation step.

### Session handling

- **Caller-provided `AsyncSession`, passed as the first argument to every repository function** (e.g. `async def get_by_id(session: AsyncSession, asset_id: str) -> AssetRecord | None`). Session lifecycle (open/commit/close) is owned one level up — a FastAPI dependency in later phases, an `async with` block in the bootstrap CLI and tests.
- Rationale: lets Phase 2 tools compose several repository calls into one logical unit of work sharing a single session/transaction — e.g. `get_asset_status` will need `assets` + `telemetry_snapshots` + `fault_events` + `observations` + `operating_limits` all for one asset, in one request.

### Function granularity and scope (stated as the concrete function list — flag if any should change)

Repositories expose narrow, per-table **query** functions only — no aggregation across entities and no business/classification logic (e.g. "is this reading in the warning tier" is Phase 2 tool logic, not a repository concern; the repository only fetches rows). Proposed functions, one module per entity:

- `assets.py`: `get_by_id(session, asset_id) -> AssetRecord | None`
- `telemetry.py`: `get_latest_for_asset(session, asset_id) -> TelemetrySnapshotRecord | None` (ORDER BY timestamp DESC LIMIT 1 — the "latest snapshot" query implied by Task 2's telemetry_snapshots cardinality decision)
- `fault_events.py`: `list_active_for_asset(session, asset_id) -> list[FaultEventRecord]`; `list_for_asset(session, asset_id) -> list[FaultEventRecord]` (all, active + resolved — maintenance history and recurrence counting both need this); `list_by_asset_and_code(session, asset_id, fault_code) -> list[FaultEventRecord]` (recurrence-counting helper, e.g. PP-001's "≥3 times in 12 months")
- `maintenance_events.py`: `list_for_asset(session, asset_id) -> list[MaintenanceEventRecord]`
- `observations.py`: `list_for_asset(session, asset_id) -> list[ObservationRecord]`
- `work_orders.py`: `list_for_asset(session, asset_id) -> list[WorkOrderRecord]`
- `fault_taxonomy.py`: `get_by_code(session, fault_code) -> FaultTaxonomyRecord | None`; `list_all(session) -> list[FaultTaxonomyRecord]`
- `operating_limits.py`: `list_for_model(session, model) -> list[OperatingLimitRecord]`
- `plant_policies.py`: `get_by_id(session, policy_id) -> PlantPolicyRecord | None`; `list_by_type(session, policy_type) -> list[PlantPolicyRecord]`

This matches the dataset spec's own §9 guidance: "narrow operations such as telemetry lookup, fault-history lookup, and observation lookup are responsibilities of broader domain tools" — i.e. these functions are the narrow primitives, and Phase 2 composes them behind the seven canonical tools; Phase 1 does not pre-build any tool-shaped aggregation.

---

## Test / Validation

Concrete, checkable version of the plan's generic Phase 1 test bullets ("assert exact record counts and key values from the specification," "verify PUMP-101 through PUMP-104 ground-truth records," "verify clean database bootstrap from an empty volume/test database"), expanded against the decisions above. Intended as a checklist a coding agent can run through to confirm the implementation matches what was decided here.

**Schema & migrations**
- [ ] Alembic is configured; a single initial revision creates all nine tables: `assets`, `telemetry_snapshots`, `fault_events`, `maintenance_events`, `observations`, `work_orders`, `fault_taxonomy`, `operating_limits`, `plant_policies`.
- [ ] `alembic upgrade head` succeeds against a clean/empty Postgres database.
- [ ] All primary keys use the spec's natural business IDs (`asset_id`, `event_id`, `maintenance_id`, `observation_id`, `work_order_id`, `snapshot_id`, `fault_code`, `policy_id`, an operating-limit ID) — no surrogate integer/UUID PK columns present anywhere.
- [ ] Foreign keys exist from `asset_id` on `telemetry_snapshots`, `fault_events`, `maintenance_events`, `observations`, and `work_orders` to `assets.asset_id`.
- [ ] `CHECK` constraints enforce the locked categorical fields (asset `status`/`asset_type`/`model`; fault `severity`/`status`; maintenance `type`; observation `type`/`severity`; work-order `priority`/`status` restricted to `'completed'`; policy `type`; operating-limit `source_type`).
- [ ] `maintenance_events.component` and `observations.reported_by` have **no** `CHECK` constraint (confirmed unconstrained — preserves the spec's literal inconsistent values, e.g. `"lubrication"` vs `"lubrication_system"`).
- [ ] `operating_limits` has structured nullable threshold columns (normal/warning/critical tiers as applicable per row) plus `rule_text` and `provenance_note`; OL-002's `provenance_note` explicitly states the 82°C value is a manufacturer reference adopted by the synthetic plant, not a literal CP-200 spec.
- [ ] All decimal measurement columns (telemetry values, operating-limit thresholds) are `NUMERIC`, not floating point.
- [ ] All timestamp columns are `timestamptz`.

**Seed fixtures**
- [ ] One JSON fixture file exists per entity, and each file's record count matches the spec exactly: 4 assets, 4 telemetry_snapshots, 5 fault_events, 10 maintenance_events, 2 observations, 2 work_orders, 4 fault_taxonomy, 4 operating_limits, 2 plant_policies (37 records total).
- [ ] Every field value in each fixture file matches Dataset Design Specification v1.1 §7 exactly — spot-checked record by record, not just counted.
- [ ] Fixture timestamps are stored/interpreted as UTC exactly as written in the spec, with no offset applied (e.g. `"2026-08-14 09:00"` → `2026-08-14T09:00:00Z`).
- [ ] F104 (`SEAL_LEAK_DETECTED`) does not appear as a `fault_events` row anywhere in the fixtures — confirmed absent; it exists only as a `fault_taxonomy` entry, with PUMP-104's seal issue represented via OBS-001.

**Bootstrap / reset workflow**
- [ ] `uv run maintenance-agent-db reset` succeeds against an empty/fresh database (the bootstrap case).
- [ ] Running the same reset command a second time against an already-seeded database succeeds and leaves the data identical — no duplicate-key errors, no drift, exact match to the fixtures both times.
- [ ] A dedicated `maintenance_agent_test` database exists alongside the dev database on the same Compose-managed Postgres service, provisioned automatically on `docker compose up`.
- [ ] Running reset against `TEST_DATABASE_URL` does not affect the dev database's data, and vice versa.
- [ ] `.env.example` documents `TEST_DATABASE_URL` alongside the existing `DATABASE_URL`.
- [ ] `docker compose up` still produces a healthy `api` + `postgres` stack after Phase 1's changes land (Phase 0's own health checks still pass, unmodified).

**Repository / query functions**
- [ ] Each entity has a corresponding module under `db/repositories/` exposing the functions listed in the Task 5 function list.
- [ ] Repository functions return Pydantic read-model instances (`AssetRecord`, `TelemetrySnapshotRecord`, etc.) — never raw SQLAlchemy ORM objects — confirmed by type-checking a sample call site.
- [ ] Every repository function accepts an `AsyncSession` as its first parameter; none open or close a session internally.
- [ ] `telemetry.get_latest_for_asset` returns the single most recent row by timestamp when multiple snapshots exist for one asset (exercised by a test that inserts two snapshots for one asset and asserts the later one is returned).
- [ ] `fault_events.list_by_asset_and_code(session, "PUMP-103", "F102")` returns exactly 3 records (FE-002, FE-003, FE-004) — the recurrence case PP-001 is built around.
- [ ] Repository-level tests run against the dedicated test database, not the dev database.

**Ground-truth verification (assertions hardcoded from the Dataset Design Specification, per the Task 3 test-source-of-truth decision — not read from the fixture files)**
- [ ] PUMP-101: no active `fault_events` row; latest telemetry shows vibration 2.1 mm/s, bearing temperature 54°C; two completed preventive `maintenance_events` (ME-001, ME-002).
- [ ] PUMP-102: active F101 fault event (FE-001); telemetry vibration 8.1 mm/s; `abnormal_vibration` observation (OBS-002) present; prior corrective coupling realignment (ME-003) present in maintenance history.
- [ ] PUMP-103: three F102 fault events (FE-002, FE-003 resolved; FE-004 active) within the 12-month window; telemetry bearing temperature 91°C against the adopted 82°C limit (OL-002); two corrective bearing-replacement maintenance events (ME-006, ME-007).
- [ ] PUMP-104: active F103 fault event (FE-005); telemetry discharge pressure 3.9 bar and flow rate 61 L/min against OL-003/OL-004; `seal_leak` observation (OBS-001) present; no F104 `fault_events` row exists anywhere for this asset.
- [ ] An unresolved/unknown asset ID (e.g. `PUMP-999`): `assets.get_by_id` returns `None` — confirmed at the repository layer, ahead of any Phase 2 tool logic.

## Success Criteria

- [ ] All 37 frozen structured records across all nine tables are queryable via the repository layer.
- [ ] `alembic upgrade head` followed by `uv run maintenance-agent-db reset` reproduces an identical database state every time it's run, whether starting from an empty volume or an already-seeded database.
- [ ] No dataset expansion (new assets, faults, telemetry, maintenance events, observations, work orders, operating limits, or plant policies) was required to implement Phase 1 — the dataset remains exactly what Dataset Design Specification v1.1 defines.
- [ ] The Phase 0 API and Docker Compose stack remain healthy and its public contract (`/health`, `/agent/query`) is unchanged after Phase 1's database changes land.
- [ ] Repository functions provide everything Phase 2's canonical tools (`resolve_asset`, `get_asset_status`, `get_maintenance_history`, `get_plant_policy`) will need — no raw SQL required at the tool layer, and no additional repository functions anticipated before Phase 2 begins.
- [ ] Operating-limit provenance (particularly OL-002's manufacturer-reference-adopted status) is visibly preserved and inspectable directly from the database, not only from the source spec document.
- [ ] Repository structure matches the agreed design, so Phase 2 can build the seven canonical tools directly on top of `db/repositories/` without restructuring it.

## Status

All five Phase 1 tasks (migrations/schema definitions; table/entity definitions; deterministic seed fixtures; database bootstrap/reset workflow; internal repository/query functions) are locked, with concrete Test/Validation and Success Criteria checklists above. Phase 1 planning is complete. Next: proceed to implementation, or move on to Phase 2 (Deterministic Tool Layer) planning discussion.