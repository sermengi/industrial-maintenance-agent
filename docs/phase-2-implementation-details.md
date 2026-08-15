# Phase 2 (Deterministic Tool Layer) — Implementation Decisions

Captured from planning discussion, 2026-08-15. These are decisions made ahead of implementation, refining Implementation Plan v1.0 / Phase 2 and the canonical tool contract (Project Design & Architecture Overview v0.3 §6, Dataset Design Specification v1.1 §9) without contradicting either. Nothing here has been implemented yet. Decisions are locked task-by-task, following the Phase 2 task list from the implementation plan:

1. Implement `resolve_asset`. **(locked)**
2. Implement `get_asset_status` to aggregate asset metadata, active faults, current telemetry, recent observations, and applicable operating limits. **(locked)**
3. Implement `get_maintenance_history` to return maintenance and fault history. **(locked)**
4. Implement `get_plant_policy` with explicit policy lookup semantics. **(locked)**
5. Define Pydantic input/output contracts for all implemented tools. **(locked)**
6. Return explicit typed errors/statuses for unknown assets or missing data. **(locked)**

---

## Task 1 — resolve_asset

### Input contract

- **Candidate identifier only.** `resolve_asset` accepts a single string — the asset identifier the caller believes it's about (later, whatever the Phase 4 graph's interpretation node extracts from the user's query, or the `asset_id` hint passed straight through from the `/agent/query` request per the Phase 0 envelope decision).
- `resolve_asset` does **not** parse free text or accept a raw sentence. No regex/NLU extraction logic lives in this tool.
- Rationale: matches the architecture doc's own description of the tool ("validates or normalizes the referenced identifier"), keeps this layer purely deterministic, and avoids duplicating extraction logic that Phase 4's interpretation node will own. A tool meant to be a debuggable guardrail shouldn't also be guessing what the user meant.
- Rejected alternative: accepting raw text with a regex fallback to pull a `PUMP-\d+`-shaped token out of a full sentence. More defensive against being called incorrectly, but blurs the deterministic-tool / agent-reasoning boundary the plan explicitly wants kept separate ("deterministic layers before agent reasoning").

### Normalization

- **Light normalization only**: trim whitespace, uppercase, then exact-match against `assets.get_by_id` (the Phase 1 repository primitive).
- `"pump-102"` → resolves to `PUMP-102`. `"PUMP102"` (missing dash) or `"102"` (bare number) do **not** resolve — they fall through to the not-found path rather than being guessed at.
- Rejected alternative: loose reconstruction (inserting a missing dash, prefixing a bare number with `PUMP-`, etc.) before retrying the lookup. More forgiving of typos, but adds guessing/correction logic to a tool whose job is to be a simple, inspectable guardrail — and risks silently "fixing" an identifier that should have surfaced as unresolved.

### Output contract — success

- Returns the `AssetRecord` fields already defined in Phase 1 (`asset_id`, `asset_type`, `model`, `location`, `installation_date`, `status`). No new fields are introduced at the tool layer.

### Output contract — failure

- **Typed discriminated result, not an exception.** A single `ResolveAssetResult` Pydantic model:
  - `status: Literal["resolved", "not_found"]`
  - `asset: AssetRecord | None` — populated only when `status == "resolved"`.
- `resolve_asset` never raises for this expected business outcome (an unresolvable identifier is a normal, anticipated result, not an exceptional error). This directly satisfies the plan's Phase 2 instruction to "return explicit typed errors/statuses for unknown assets or missing data," and lets the Phase 4 graph branch on a field instead of wrapping calls in `try`/`except`.
- Rejected alternative: raising a custom `AssetNotFoundError`. More conventional Python error handling, but would make the Phase 4 graph's routing depend on exception handling for an outcome (GS-07's unknown-asset guardrail) that the golden scenarios treat as a normal, testable branch — not an error condition.
- **Failure modes collapse into one outcome.** A well-formed-but-nonexistent identifier (`PUMP-999`), a malformed identifier, and a missing/empty identifier (`""`/`None`) all return `status="not_found"` rather than being split into separate validation-error vs. not-found statuses. Matches the single `unknown_asset` status already defined in the Phase 0 API response envelope — one failure mode to test and reason about, not several.

### Tooling / location (stated as defaults — flag if should change)

- Lives under `tools/` (reserved for Phase 2 in the Phase 0 skeleton), one module per canonical tool — e.g. `tools/resolve_asset.py` — mirroring the one-module-per-entity pattern already used for Phase 1's repositories.
- Implemented as a plain async function (`async def resolve_asset(identifier: str, session: AsyncSession) -> ResolveAssetResult`) with Pydantic input/output models, with no LangGraph tool-calling decorator or binding at this layer. Phase 4 wraps it for graph/tool-calling use later without needing to change this core logic — same separation Phase 1 kept between repositories and the ORM.
- Built directly on `db/repositories/assets.py`'s existing `get_by_id(session, asset_id) -> AssetRecord | None` — no new repository function needed for this tool.

### Test / Validation

- [ ] `resolve_asset("PUMP-101")`, `"PUMP-102"`, `"PUMP-103"`, `"PUMP-104"` each return `status="resolved"` with the correct `AssetRecord` (matching Dataset Design Specification v1.1 §7.1 and §8 ground truth).
- [ ] Case/whitespace variants (`" pump-102 "`, `"Pump-103"`) resolve identically to their canonical form.
- [ ] `resolve_asset("PUMP-999")` returns `status="not_found"`, `asset=None` — satisfies GS-07's "resolve_asset -> STOP" requirement.
- [ ] Malformed identifiers (`"PUMP102"`, `"102"`, `""`, `None`) all return `status="not_found"` — no partial/guessed match, no exception raised.
- [ ] No downstream repository call (telemetry, faults, etc.) is triggered when resolution fails — confirms the guardrail is enforceable before any other tool exists.

---

## Task 2 — get_asset_status

### Input contract

- **Accepts the already-resolved `AssetRecord`, not an `asset_id` string.** Signature: `async def get_asset_status(asset: AssetRecord, session: AsyncSession) -> GetAssetStatusResult`.
- `get_asset_status` does **not** re-validate that the asset exists and has **no** `not_found` branch of its own. It trusts that the caller (the Phase 4 graph) already called `resolve_asset` successfully and is passing that result straight through.
- Taking the `AssetRecord` itself, rather than a bare string, makes this trust boundary explicit at the type level: there is no code path where `get_asset_status` could be called with an identifier that hasn't already been resolved, because the input value can only come from a prior successful `resolve_asset` call.
- **Explicitly accepted trade-off**: this couples `get_asset_status`'s correctness to the Phase 4 graph always calling `resolve_asset` first and only proceeding on `status == "resolved"`. Unlike `resolve_asset`, this tool cannot be unit-tested against `PUMP-999` for an unknown-asset case — that guardrail is fully owned by `resolve_asset` (GS-07). If a future phase finds a reason `get_asset_status` needs to be called independently of that sequence, this decision should be revisited.
- Rejected alternative: `get_asset_status(asset_id: str, session) -> GetAssetStatusResult`, independently re-querying `assets.get_by_id` and returning its own typed `not_found` status (mirroring `resolve_asset`'s pattern). This was the initial recommendation — safer against out-of-order calls and independently testable — but was set aside in favor of trusting the caller, per this decision.

### Telemetry + classification

- Returns **both** the raw current `TelemetrySnapshotRecord` (`telemetry.get_latest_for_asset`, unmodified — all 5 metrics as stored) **and** a separate computed list of classified readings, one entry per telemetry metric:
  - `metric`, `value`, `unit`, `tier: Literal["normal", "warning", "critical"] | None`, `operating_limit_id: str | None`, `rule_text: str | None`.
- Classification is computed by comparing each metric's value against the matching `operating_limits` row's structured threshold columns (Phase 1's `normal_max`/`warning_min`/`warning_max`/`critical_min`/`critical_max`, as applicable per row).
- **Tier vocabulary is normalized to `normal`/`warning`/`critical`**, inheriting Phase 1's schema decision: OL-002's spec wording ("high") and OL-004's spec wording ("low") both map onto the generic `critical_min`/`critical_max` columns already, so this isn't a new normalization choice — it's carrying forward what Phase 1 already locked. The original spec wording is preserved verbatim in `rule_text` for exact citation.
- Metrics with **no applicable operating limit** (only `inlet_pressure_bar`, currently) get `tier=None`, `operating_limit_id=None` — never defaulted to `"normal"`. No safety judgment is invented where the dataset doesn't define one.
- If no telemetry snapshot exists for the asset at all (`get_latest_for_asset` returns `None` — not possible in the current fixtures, but the repository signature allows it), `telemetry=None` and `classified_readings=[]`.
- Rationale for keeping both raw and classified: matches the project's debug-first/inspectability principle — the raw snapshot stays traceable to its source row untouched, while the classification is a clearly separate, auditable computation layered on top, not baked silently into the raw data.

### Active faults, observations, operating limits

- **Active faults only**: `fault_events.list_active_for_asset(session, asset.asset_id)`. Resolved/historical fault events are explicitly out of scope here — that's `get_maintenance_history`'s responsibility (architecture doc draws this line explicitly between the two tools).
- **All observations for the asset**, unfiltered: `observations.list_for_asset(session, asset.asset_id)`. No recency window is applied or invented — the dataset spec doesn't define one (only 2 observation records exist in the whole dataset), and "current operator observations" in the architecture doc is read as "all observations on file" for v1's frozen, tiny dataset.
- **Full raw operating-limits list for the asset's model**: `operating_limits.list_for_model(session, asset.model)`, included in addition to the per-metric classified readings — gives the LLM citable source rows (`rule_text`, `provenance_note`, `source_type`) rather than only the derived tier labels.

### Output contract

- No top-level `status`/`not_found` field (per the Input contract decision above — this tool always returns a populated result for the asset it's given).
- `GetAssetStatusResult` fields: `asset: AssetRecord`, `telemetry: TelemetrySnapshotRecord | None`, `classified_readings: list[ClassifiedReading]`, `active_faults: list[FaultEventRecord]`, `observations: list[ObservationRecord]`, `operating_limits: list[OperatingLimitRecord]`.
- `asset` is echoed back from the input rather than re-fetched — the architecture doc lists "asset metadata" as part of this tool's aggregated output, so it's included directly from the already-resolved record at no extra DB cost.

### Tooling / location (stated as defaults — flag if should change)

- `tools/get_asset_status.py`, same pattern as Task 1.
- Classification comparison logic (the normal/warning/critical threshold check) lives inside this tool module, not in the repository layer — matches Phase 1's explicit decision that "business/classification logic... is Phase 2 tool logic, not a repository concern."

### Test / Validation

- [ ] PUMP-101: `classified_readings` show vibration 2.1 mm/s and bearing temp 54°C both `tier="normal"`; `active_faults == []`.
- [ ] PUMP-102: vibration 8.1 mm/s classifies `tier="critical"` against OL-001 (>7.0); `active_faults` contains FE-001 (F101); `observations` contains OBS-002.
- [ ] PUMP-103: bearing temp 91°C classifies `tier="critical"` against OL-002's adopted 82°C limit (>=82 → "high", normalized to `critical`); `active_faults` contains FE-004 (F102, active) and excludes FE-002/FE-003 (resolved).
- [ ] PUMP-104: discharge pressure 3.9 bar classifies `tier="critical"` against OL-003 (<4.0); flow rate 61 L/min classifies `tier="critical"` against OL-004 (<70, "low" normalized to `critical`); `observations` contains OBS-001 (seal_leak); `active_faults` contains FE-005 (F103) and no fabricated F104 event.
- [ ] `inlet_pressure_bar` classifies `tier=None` for all four assets (no operating limit defined for that metric in the dataset).
- [ ] `operating_limits` returned for a CP-200 asset (PUMP-101/102/103) contains OL-001 and OL-002 only; for a CP-300 asset (PUMP-104) contains OL-003 and OL-004 only.

---

## Task 3 — get_maintenance_history

### Input contract

- **Accepts the already-resolved `AssetRecord`**, same trust-the-caller pattern locked in Task 2. Signature: `async def get_maintenance_history(asset: AssetRecord, session: AsyncSession) -> GetMaintenanceHistoryResult`. No independent `not_found` branch, for the same reasons recorded under Task 2.

### Fault events — full history, not resolved-only

- Returns **all** fault events for the asset (active **and** resolved) via `fault_events.list_for_asset`, not filtered to `status="resolved"`.
- Although the architecture doc phrases this tool's responsibility as "historical/resolved fault events," filtering out active events would break recurrence counting — PUMP-103's PP-001 case needs all three F102 occurrences (FE-002, FE-003 resolved; FE-004 still active) counted together. This is also exactly why Phase 1 built `list_for_asset` to return everything: its own rationale states "maintenance history and recurrence counting both need this."

### Recurrence context

- For each distinct `fault_code` present in the asset's fault-event history, computes a `FaultRecurrence` entry: total occurrences, occurrences within the trailing 12-month window, and whether that count meets PP-001's threshold (`>= 3`).
- **Threshold (3) and window (12 months) are hardcoded constants in this tool's logic**, not parsed from `plant_policies.condition`'s free text — carrying forward Phase 1's explicit decision that the PP-001 recurrence check "will be implemented as a query against `fault_events` in Phase 2's tool logic, not parsed out of the policy row." `get_plant_policy` (Task 4) returns the policy text itself; `get_maintenance_history` independently computes whether that policy's condition is met for this asset. The graph/LLM combines both.
- **Reference time for the 12-month window is anchored to the asset's own latest fault-event timestamp**, not real wall-clock time. Using `datetime.now()` would make the dataset's recurrence evidence silently time-dependent: PUMP-103's three F102 occurrences (Jan 2026 / Apr 2026 / Aug 2026) would drop to two once real time passes January 2027, breaking GS-08 for reasons unrelated to any code change. Anchoring to the asset's own data keeps a frozen, deterministic dataset's golden-scenario evidence stable indefinitely, regardless of when the code actually runs.
  - If an asset has no fault-event history at all (e.g. PUMP-101), there is no reference timestamp and no fault code to evaluate — `recurrence` is simply `[]`.
- Rejected alternative: real wall-clock `datetime.now()` (simple, "realistic," but time-fragile against a frozen dataset). Also rejected: an explicit `reference_time` parameter defaulting to wall-clock (adds flexibility but keeps the same drift problem as the production default unless every call site remembers to override it).
- `FaultRecurrence` fields: `fault_code: str`, `total_occurrences: int`, `occurrences_within_window: int`, `window_months: int` (= 12), `meets_recurrence_threshold: bool`. Both raw counts and the computed boolean are included, mirroring `get_asset_status`'s raw-plus-classified pattern for inspectability.

### Maintenance events and work orders

- **All maintenance events for the asset**, unfiltered: `maintenance_events.list_for_asset`.
- **All work orders for the asset**, unfiltered: `work_orders.list_for_asset`. The dataset only has 2 work-order records total (both `status="completed"`) and the spec defines no filtering criteria, so nothing is filtered — same reasoning as Task 2's unfiltered observations.

### Output contract

- No top-level `status`/`not_found` field, consistent with Task 2.
- `GetMaintenanceHistoryResult` fields: `asset: AssetRecord`, `maintenance_events: list[MaintenanceEventRecord]`, `fault_events: list[FaultEventRecord]`, `work_orders: list[WorkOrderRecord]`, `recurrence: list[FaultRecurrence]`.

### Tooling / location (stated as defaults — flag if should change)

- `tools/get_maintenance_history.py`, same pattern as Tasks 1–2.
- Recurrence computation (windowing, threshold comparison) lives in this tool module, not the repository layer, consistent with Phase 1's decision to keep business/classification logic out of repositories and with Task 2's classification precedent. Internally uses `fault_events.list_by_asset_and_code` per distinct fault code as the windowing helper Phase 1 built specifically for this.

### Test / Validation

- [ ] PUMP-101: `maintenance_events` contains ME-001, ME-002; `fault_events == []`; `work_orders` contains WO-001; `recurrence == []` (no fault codes in history).
- [ ] PUMP-102: `fault_events` contains FE-001 (F101, active); `maintenance_events` contains ME-003, ME-004, ME-005; `recurrence` shows F101 with `total_occurrences=1`, `occurrences_within_window=1`, `meets_recurrence_threshold=False`.
- [ ] PUMP-103: `fault_events` contains all three — FE-002, FE-003 (resolved) and FE-004 (active); `maintenance_events` contains ME-006, ME-007, ME-008; `work_orders` contains WO-002; `recurrence` shows F102 with `occurrences_within_window=3`, `meets_recurrence_threshold=True` (window anchored to FE-004's 2026-08-13 timestamp) — satisfies GS-08's required "recurring issue" evidence.
- [ ] PUMP-104: `fault_events` contains FE-005 (F103, active) only — no fabricated F104 row; `maintenance_events` contains ME-009, ME-010; `recurrence` shows F103 with `total_occurrences=1`, `meets_recurrence_threshold=False`.
- [ ] Recurrence computation is confirmed to use the asset's own latest fault-event timestamp as the window reference, not `datetime.now()` — test passes identically regardless of the actual date it's run on.

---

## Task 4 — get_plant_policy

### Input contract

- **Lookup by `policy_type`**, not by a specific `policy_id` and not a no-argument "return everything." Signature: `async def get_plant_policy(policy_type: str, session: AsyncSession) -> GetPlantPolicyResult`, built on `plant_policies.list_by_type`.
- This is the one canonical tool of the four built so far that is **not** asset-scoped — it takes no `AssetRecord`/`asset_id` at all. Plant policies are global, not tied to a specific asset.
- Rationale: matches the architecture doc's framing of this tool as returning "the deterministic plant policy relevant to the **current decision**" — the caller (the Phase 4 graph) knows *why* it's asking (e.g. "a recurrence was just flagged" or "I'm about to draft a work order"), and expresses that as a category rather than already knowing the specific policy ID in advance. Keeps each call narrowly scoped and keeps the tool-call trajectory itself informative for Phase 8's evidence assertions (a `get_plant_policy("recurring_fault")` call in the trace signals *which* policy question the graph was answering).
- Rejected alternative: no-argument "return all policies." Simpler (only 2 records exist in v1) but stops narrowing to "the current decision," and weakens the tool-call trajectory as evidence.
- Rejected alternative: lookup by `policy_id` (mirroring `resolve_asset`'s single-identifier pattern). Requires the caller to already know the specific policy ID, which pushes the "which policy applies here" judgment upstream instead of having this tool answer it from a category — a bigger ask of the Phase 4 graph/LLM than necessary for something this deterministic.
- `policy_type` is accepted as a plain string, not a restrictive `Literal`, consistent with `resolve_asset`'s precedent: an unrecognized or misspelled type is not a schema-validation error, it's just a lookup that matches nothing (see Output contract).

### Output contract

- `GetPlantPolicyResult` fields: `policy_type: str` (echoes the request), `policies: list[PlantPolicyRecord]`.
- No `not_found` status field. An unrecognized `policy_type` (typo, or a category with no defined policy) simply returns `policies=[]` — an empty list already unambiguously communicates "no policy of this type," the same way `get_asset_status.active_faults=[]` and `get_maintenance_history.recurrence=[]` already do elsewhere in Phase 2, without needing a dedicated failure status.
- `condition` and `required_action` are returned **verbatim as free text**, exactly as stored — per Phase 1's decision not to decompose `plant_policies.condition`/`required_action` into structured rule fields. `get_plant_policy` is a pure lookup; it does not parse or evaluate policy text. (The actual PP-001 threshold check — "has this fault recurred ≥3 times in 12 months for this asset?" — is independently computed by `get_maintenance_history`, Task 3, not derived from this text.)

### Tooling / location (stated as defaults — flag if should change)

- `tools/get_plant_policy.py`, same pattern as Tasks 1–3.
- The simplest of the four tools: no classification or business logic of its own, just a thin typed wrapper over `plant_policies.list_by_type`.

### Test / Validation

- [ ] `get_plant_policy("recurring_fault")` returns `policies=[PP-001]` with `condition="Same fault occurs >=3 times within 12 months"` and the matching `required_action` text, verbatim from Dataset Design Specification v1.1 §5.
- [ ] `get_plant_policy("consequential_action")` returns `policies=[PP-002]` with `condition="Work-order submission changes system state"`.
- [ ] `get_plant_policy("nonexistent_type")` (or any typo/unrecognized value) returns `policies=[]` — no exception raised.
- [ ] This tool is tested with no asset fixture/context at all — confirms it's fully independent of the asset-scoped pattern used by Tasks 1–3.
- [ ] Returned `condition`/`required_action` text matches the spec exactly, untouched — confirms this tool performs no parsing or threshold evaluation of its own.

---

## Task 5 — Pydantic input/output contracts (cross-cutting)

This task doesn't introduce new per-tool behavior; it locks the conventions that Tasks 1–4 already followed consistently, so Phase 4's graph integration and any future tool (Phase 6's `create_work_order_draft`/`submit_work_order`) can rely on one predictable shape instead of four ad hoc ones.

### Naming convention

- Every tool's output model is named `<PascalCaseToolName>Result` — `ResolveAssetResult`, `GetAssetStatusResult`, `GetMaintenanceHistoryResult`, `GetPlantPolicyResult`. (`GetAssetStatusResult` and `GetMaintenanceHistoryResult` are renamed here from the working names used earlier in this doc's Task 2/3 sections — `AssetStatusResult`/`MaintenanceHistoryResult` — for consistency; the sections above already reflect the corrected names.)
- Nested sub-models used by a single tool's result (`ClassifiedReading`, `FaultRecurrence`) are named for what they represent, not prefixed with the tool name — they aren't reused outside their owning tool's result.

### No wrapping "Input" models

- None of the four tools takes a dedicated Pydantic "Input"/"Request" model. Each tool's input is either a single primitive (`identifier: str` for `resolve_asset`, `policy_type: str` for `get_plant_policy`) or an existing Phase 1 record type (`asset: AssetRecord` for `get_asset_status`/`get_maintenance_history`) — wrapping a single parameter in its own request model would add a layer of indirection with no validation benefit these tools don't already get from the parameter's own type.
- This is scoped to the four tools built in Phase 2, not a blanket rule: a later tool with a genuinely multi-field input (e.g. Phase 6's `create_work_order_draft`, which needs several fields to construct a draft) should define its own Pydantic input model at that point. Nothing here prevents that.

### Function signature shape

- Every tool is an `async def`, taking its primary domain input first and `session: AsyncSession` last, returning its `...Result` model directly (never `None`, never a bare primitive, never a raw SQLAlchemy object).
- `resolve_asset(identifier: str, session: AsyncSession) -> ResolveAssetResult`
- `get_asset_status(asset: AssetRecord, session: AsyncSession) -> GetAssetStatusResult`
- `get_maintenance_history(asset: AssetRecord, session: AsyncSession) -> GetMaintenanceHistoryResult`
- `get_plant_policy(policy_type: str, session: AsyncSession) -> GetPlantPolicyResult`

### Field-level typing conventions

- Closed sets of values use `Literal[...]`, never a bare `str` with implied valid values: `ResolveAssetResult.status`, `ClassifiedReading.tier`.
- Numeric measurement fields inherit `Decimal` typing from the Phase 1 record models (`AssetRecord`, `TelemetrySnapshotRecord`, `OperatingLimitRecord`, etc.) — no tool re-types a measurement as `float`, preserving Phase 1's exact-value rationale all the way to the tool boundary.
- Every field that can be legitimately absent is typed `X | None` explicitly (`GetAssetStatusResult.telemetry`, `ClassifiedReading.tier`/`operating_limit_id`) rather than being omitted from the model or defaulted to a sentinel value.
- Collections are always typed `list[...]`, defaulting to `[]` rather than `None`, so callers never need a null-check before iterating (`active_faults`, `observations`, `policies`, etc.).

### Where contracts live

- Each tool's `Result` model (and any tool-specific nested models) is co-located in that tool's own module under `tools/` — there is no shared `tools/schemas.py`. This mirrors Phase 1's one-module-per-entity repository pattern and reflects that, unlike `db/repositories/records.py`'s record types, no Phase 2 tool-specific model is reused by more than one tool. The only types shared across tools are the Phase 1 record types themselves (`AssetRecord`, `FaultEventRecord`, etc.), which correctly stay in `db/repositories/records.py`.

### Test / Validation

- [ ] All four `...Result` model names match the `<PascalCaseToolName>Result` convention exactly.
- [ ] No tool function has a corresponding `...Input`/`...Request` Pydantic model.
- [ ] Every collection field across all four result models defaults to `[]`, never `None`, confirmed by inspecting each model's field defaults.
- [ ] Every optional scalar field (`telemetry`, `tier`, `operating_limit_id`) is typed `X | None` explicitly, not implicitly optional.
- [ ] No measurement field anywhere in a tool's result model is typed `float`.

---

## Task 6 — Explicit typed errors/statuses for unknown assets or missing data (cross-cutting)

This task also locks a cross-cutting pattern rather than adding new per-tool behavior. Tasks 1–4 established two distinct, deliberately different shapes for "absence," plus one explicit scope boundary; this section names all three so future tools (Phase 5 guardrails, Phase 6 HITL tools) extend the same vocabulary instead of inventing new ones.

### Pattern 1 — single-entity resolution: typed discriminated status

- Applies where a tool resolves **one specific named entity that might not exist**: `resolve_asset` only, among the four tools built so far.
- Shape: `status: Literal["resolved", "not_found"]` plus a `None`-able payload field, populated only when `status == "resolved"`.
- This is the only tool in Phase 2 that needs this shape, because it's the only one answering "does this thing exist at all" — every other tool receives an already-resolved `AssetRecord` and never needs to ask that question itself (see Task 2's Input contract decision).

### Pattern 2 — collection absence: empty list, no status field

- Applies to every list-valued field across all four tools: `GetAssetStatusResult.active_faults`/`observations`/`operating_limits`, `GetMaintenanceHistoryResult.maintenance_events`/`fault_events`/`work_orders`/`recurrence`, `GetPlantPolicyResult.policies`.
- An empty list is treated as a complete, self-describing answer ("there are zero matching records"), not a failure requiring its own `not_found`/`empty` status flag. Adding a parallel boolean or enum next to every list field would be redundant with `len(...) == 0` and was deliberately avoided in all four tools' output contracts.
- This is also why `get_plant_policy` needed no `not_found` status for an unrecognized `policy_type` (Task 4): the tool's only output is a list, so "no match" is already representable without inventing a second signal.

### Pattern 3 — per-field missing data within an otherwise-successful result

- Applies to individual **scalar** fields that can be legitimately absent without the whole tool call failing: `GetAssetStatusResult.telemetry` (no snapshot recorded for the asset), `ClassifiedReading.tier`/`operating_limit_id` (no operating limit defined for that metric, e.g. `inlet_pressure_bar`).
- These are typed `X | None` and left `None` rather than defaulted to a misleading value (e.g. `tier` is never defaulted to `"normal"` just because no limit exists to evaluate against) — directly satisfies the "missing data" half of this task's instruction, distinct from the "unknown asset" half covered by Pattern 1.

### Scope boundary: business outcomes vs. infrastructure failures

- All three patterns above cover **expected, anticipated business outcomes** — an asset that legitimately doesn't exist, a metric with no defined limit, a policy category with no records. None of the four tools catches or wraps genuine infrastructure failures (a dropped DB connection, a constraint violation, an unexpected exception from the ORM layer) into one of these typed shapes.
- Those failures propagate as real, unhandled exceptions out of the tool layer. Deciding what to do with them — bounded retries, fallback responses, graceful degradation — is explicitly **Phase 5**'s job ("Reliability, Validation & Guardrails"), not Phase 2's. Phase 2's typed-status work only ever needed to answer questions the frozen dataset and canonical tool contract already define; inventing error-recovery behavior now would be scope creep ahead of Phase 5's own planning discussion.
- The Phase 0 API response envelope's `status: Literal["ok", "needs_approval", "insufficient_evidence", "unknown_asset", "error"]` is a separate, higher-level concern: the Phase 4 graph is responsible for mapping combinations of these tool-level outcomes onto that envelope, not any individual Phase 2 tool.

### Test / Validation

- [ ] `resolve_asset` is the only one of the four tools whose result type has a top-level `status` field.
- [ ] No tool anywhere raises an exception for an anticipated business outcome (unknown asset, empty collection, missing per-field data) — confirmed by a test asserting each such case returns normally rather than raising.
- [ ] A simulated infrastructure failure (e.g. a broken DB session passed to any tool) propagates as an unhandled exception, not a typed result — confirms Phase 2 does not silently swallow or reinterpret infrastructure errors as business outcomes.
- [ ] No list-valued field anywhere in Phase 2's four result models is paired with a redundant boolean/enum "is empty" flag.

## Success Criteria

- [ ] All four canonical structured tools (`resolve_asset`, `get_asset_status`, `get_maintenance_history`, `get_plant_policy`) are implemented and individually unit-testable against the frozen Phase 1 fixtures, with no LangGraph, LLM, or agent runtime required to validate correctness — satisfies the plan's Phase 2 criterion "no LLM is necessary to validate data/tool correctness."
- [ ] Each tool's own Test/Validation checklist (Tasks 1–4 above) passes in full — PUMP-101 through PUMP-104 return their documented ground-truth evidence (Dataset Design Specification v1.1 §8) from every relevant tool, satisfying the plan's criterion that "structured tools return expected evidence for all relevant assets."
- [ ] The unknown-asset guardrail (GS-07) is fully enforced and independently testable at the `resolve_asset` layer alone, before LangGraph exists — confirmed by a test that calls only `resolve_asset("PUMP-999")` and asserts `status="not_found"` with no other repository access triggered, satisfying the plan's criterion that the "unknown-asset guardrail is testable before LangGraph exists."
- [ ] `get_maintenance_history`'s PP-001 recurrence computation for PUMP-103 returns `meets_recurrence_threshold=True`, anchored to the asset's own latest fault-event timestamp rather than wall-clock time (Task 3) — stays correct regardless of the real-world date the test suite is actually run on.
- [ ] `get_plant_policy("recurring_fault")` and `get_plant_policy("consequential_action")` each return PP-001 and PP-002 verbatim — the policy evidence GS-08 requires is retrievable independently of any other tool.
- [ ] No tool exposes raw SQL to a caller and no tool returns a raw SQLAlchemy ORM instance across its boundary — every one of the four tools is Pydantic-typed end to end per Task 5's conventions.
- [ ] Typed-absence handling is applied consistently everywhere per Task 6's three patterns (discriminated status only for `resolve_asset`; empty lists for collection absence; nullable scalars for per-field missing data) — no tool raises an exception for an expected business outcome, and no tool silently reinterprets an infrastructure failure as one.
- [ ] No repository function beyond what Phase 1 already built was required to implement any of the four tools (confirmed in each task's "Tooling / location" section) — validates that Phase 1's repository layer was correctly scoped for Phase 2's needs, with zero rework.
- [ ] The Phase 0 API (`/health`, `/agent/query`) and Docker Compose stack remain healthy and unchanged — Phase 2 adds a new `tools/` layer without touching the public entry point, per the plan's cross-phase Definition of Done ("the same documented FastAPI + Docker Compose entry point remains runnable after every phase").
- [ ] All four tools' signatures, `Result` models, and trust-boundary assumptions (the "accepts an already-resolved `AssetRecord`" decision from Tasks 2–3) are treated as fixed given inputs to Phase 4's LangGraph design — Phase 4 binds these tools as-is rather than renegotiating their contracts.

## Status

All four Phase 2 canonical tools are locked: `resolve_asset`, `get_asset_status`, `get_maintenance_history`, `get_plant_policy`. Cross-cutting tasks 5 (Pydantic contracts) and 6 (typed errors/statuses) are locked with their own conventions and test/validation checklists above. Success Criteria for the milestone are defined above. Phase 2 planning is complete. Next: proceed to implementation, or move on to Phase 3 (RAG Ingestion & Retrieval) planning discussion.