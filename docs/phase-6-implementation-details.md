# Phase 6 (HITL & Work-Order Workflow) — Implementation Decisions

Captured from planning discussion, 2026-08-19/20. These are decisions made ahead of implementation, refining Implementation Plan v1.0 / Phase 6 and building on the Phase 4 (LangGraph Agent Orchestration) and Phase 5 (Reliability, Validation & Guardrails) decisions without contradicting either. Decisions are locked task-by-task, following the Phase 6 task list from the implementation plan:

1. Implement `create_work_order_draft` with a validated structured draft schema. **(locked)**
2. Implement `submit_work_order` as the only persistence path for a new final work order. **(locked, amended — see Task 2 amendment note)**
3. Add LangGraph checkpoint/pause behavior between draft and submission. **(locked)**
4. Expose an API mechanism to approve/reject/resume the pending action. **(locked, amended — see Task 4 amendment note)**
5. Apply PP-001 recurring-fault and PP-002 consequential-action policies. **(locked)**
6. Ensure the original user request to create a work order does not implicitly bypass approval. **(locked)**

All six Phase 6 tasks are locked. Tasks 1 and 3 are unimplemented so far (planning only); Tasks 2 and 4 touch already-implemented code and their corrections reflect discoveries made against the real codebase during Task 5's discussion. See Success Criteria and Status at the bottom.

---

## Task 1 — Implement `create_work_order_draft` with a validated structured draft schema

### No persistence (the central decision for this task)

- `create_work_order_draft` performs **zero DB writes**. It constructs and returns a validated `WorkOrderDraft` Pydantic object that is stored into `state["work_order_draft"]` (the field Phase 4 Task 1 already reserved), checkpointed by whatever mechanism Task 3 sets up. It never touches the `work_orders` table.
- Rationale: the architecture doc calls this tool's output "non-consequential," and Phase 6's own test list requires "assert no row is created before approval" — the only way that assertion is trivially and structurally true (rather than true-by-convention) is if the draft path never reaches the database at all. `submit_work_order` (Task 2) becomes the sole INSERT path, consistent with the plan's "the only persistence path for a new final work order" framing.
- Consequence: `WorkOrderDraft` does **not** carry its own `status` field. The lifecycle state (`none` / `pending_approval` / `approved` / `rejected` / `submitted`) already lives in the separate `approval_status` graph-state field Phase 4 Task 1 locked; giving the draft object a second, parallel status field would create two sources of truth for the same thing.
- Consequence: `work_orders.status`'s CHECK constraint (currently scoped to `'completed'` only, per Phase 1 Task 2) only needs to grow for whatever value(s) `submit_work_order` actually writes — not for draft/pending/rejected states, since those never reach the table. Resolved in Task 2.

### `WorkOrderDraft` schema fields

- `asset_id: str` — injected from state (the already-resolved `AssetRecord`), never LLM-supplied. Same pattern as `get_asset_status`/`get_maintenance_history` taking `asset` from state rather than trusting the LLM to pass it.
- `issue: str` — LLM-authored, free text.
- `recommended_action: str` — LLM-authored, free text, deliberately separate from `issue`. Exists specifically so GS-08's "root-cause investigation, not a simplistic bearing replacement" requirement has its own field to land in, rather than being smuggled into `issue`.
- `priority: Literal["low", "high"]` — matches exactly the two values attested in the frozen dataset (WO-001=`low`, WO-002=`high`); no `medium` added speculatively. Can be expanded later if a golden scenario or dataset revision needs it — not pre-built now, consistent with the plan's "no implementation-time dataset expansion" posture.
- `supporting_evidence: list[str]` — see provenance mechanism below.
- `draft_id: str` — reuses `request_id` directly (no second ID minted). `request_id` already doubles as the LangGraph `thread_id` per Phase 4 Task 6, and since `work_order_draft` is a single last-write-wins field (not a list), one run can only ever have one pending draft, so a distinct draft ID would just be a second name for the same run. Satisfies Phase 0's envelope, which already reserves `pending_action: {action_type, draft_id}` for `status="needs_approval"`.
- **`model_config = {"extra": "forbid"}`** (locked in Task 6) — any tool-call argument the LLM supplies outside this exact field set is a validation error, not a silently-dropped extra key. Closes the theoretical case of a model attempting to inject an approval-adjacent field (e.g. `{"approved": true}`) as a no-op that could later be misread as meaningful.

### Priority floor (the second central decision)

- If any of the asset's currently **active** faults meets the PP-001 recurrence threshold (≥3 occurrences within 12 months), `create_work_order_draft` silently clamps `priority` to `"high"` regardless of what the LLM supplied. No error is raised, no retry is triggered — same "deterministic guardrails don't need LLM involvement" philosophy as the unknown-asset stop (Phase 5 Task 2) and the unconditional `resolve_asset` guardrail.
- **Mechanism**: the recurrence computation is extracted into a shared helper, used by both `get_maintenance_history` (Phase 2 Task 3, reporting) and this tool (floor enforcement) — a single copy of the `>=3 occurrences / 12-month window` constants rather than two that could drift apart. The tool queries the asset's active faults directly via the injected `session`, not by trusting whatever happens to already be sitting in `state.structured_evidence` — this keeps the floor correct even in a hypothetical future path where `create_work_order_draft` is called without `get_maintenance_history` having run first.
- Rejected alternative: reading recurrence status off `state.structured_evidence` instead of re-querying. Rejected because the evidence-gathering loop's tool order/count is fully LLM-determined (Phase 4 Task 5) — nothing guarantees `get_maintenance_history` ran before `create_work_order_draft` on every possible trajectory, only on GS-08's specific one.
- The override is not silent-silent: it's traceable because the triggering recurrence evidence item is included in `supporting_evidence` (below), so anyone inspecting the draft can see why priority ended up `"high"` even if the LLM had supplied `"low"`.
- Scope note: no golden scenario currently exercises the case where the floor actually changes the outcome (GS-08's user already asks for "high-priority" directly) — this is a defensive guardrail for a future request that omits priority or under-specifies it, same category as Phase 5 Task 3's insufficient-evidence trigger being real but currently untested by the golden set.

### Evidence provenance (`supporting_evidence`)

- **Deterministic state snapshot, not an LLM-supplied argument.** The tool populates `supporting_evidence` with every ID currently present in `state.structured_evidence` + `state.document_evidence` at the moment it's called — no separate citation-existence validator is needed, because every ID in those lists is already guaranteed to exist (it only got into state via a prior, already-validated tool call).
- Rejected alternative: have the LLM supply `supporting_evidence` itself as a tool argument (mirroring `SynthesisOutput.evidence_used`), validated by a citation-existence check. Rejected because that would require building a corrective-retry path for the plain tool-calling loop equivalent to what Task 1 of Phase 5 built specifically for `generate_structured()`'s forced-output path — a new mechanism for a case the deterministic-snapshot approach avoids needing entirely.
- Matches the same "expose everything looked at" precedent the Phase 5 finding fix (`evidence_used` not exposed) just established: `supporting_evidence` reflects everything accumulated so far, not a curated subset. If the LLM calls `create_work_order_draft` early, before much evidence has been gathered, `supporting_evidence` will legitimately be thin — that's a real signal about the draft's grounding, not a bug to guard against.

### Test / Validation

- [ ] `create_work_order_draft` never issues an INSERT/UPDATE against `work_orders` — confirmed by a test asserting zero DB writes occur during draft creation.
- [ ] `WorkOrderDraft` has no `status` field; the lifecycle is read from `state["approval_status"]` alone.
- [ ] `priority` only accepts `"low"` / `"high"`; any other value is a Pydantic validation error at the tool boundary.
- [ ] `WorkOrderDraft`'s LLM-facing schema has `extra="forbid"` — a tool-call argument outside the defined field set raises a validation error rather than being silently dropped.
- [ ] A mocked asset with an active fault meeting the PP-001 threshold (e.g. PUMP-103's F102: FE-002, FE-003, FE-004) produces `priority="high"` even when the LLM's tool-call args specify `"low"`.
- [ ] The recurrence-threshold constants (3 occurrences / 12-month window) exist in exactly one shared helper function, called by both `get_maintenance_history` and `create_work_order_draft` — confirmed by code inspection, not duplicated inline in either tool.
- [ ] `supporting_evidence` on a drafted `WorkOrderDraft` exactly equals the set of `source_id`/`document_id` values present in `state.structured_evidence`/`state.document_evidence` at call time — confirmed by a test that accumulates known evidence, calls the tool, and diffs the two sets.
- [ ] `draft_id` on the returned `WorkOrderDraft` equals `state["request_id"]` for that run — no separate UUID is generated.
- [ ] GS-08's PUMP-103 draft has `priority="high"`, `recommended_action` text reflecting recurrence/root-cause investigation (not a bearing-replacement-only recommendation), and `supporting_evidence` containing the recurrence-relevant IDs (e.g. FE-004 and/or the ME-006/007/008 corrective-history records, plus PP-001 if `get_plant_policy` was called).

---

## Task 2 — Implement `submit_work_order` as the only persistence path for a new final work order

### Scope framing (what's already decided elsewhere)

- The consequential-action guard for this tool was already locked in Phase 5 Task 7, not re-decided here: `submit_work_order`'s binding has `consequential=True` and is structurally excluded from every LLM tool-choice list, and the function itself checks `approval_status == "approved"` at the top and raises `ConsequentialActionGuardError` if that doesn't hold — treated as an unanticipated failure caught only by the Phase 4 Task 6 route-level try/except, never retried (Phase 5 Task 4) or given a graceful fallback (Phase 5 Task 5).
- This task is purely about what happens **after** that guard passes: how the row actually gets written.
- Called only by the deterministic post-approval node with `state["work_order_draft"]` — no re-validation of evidence happens here; Task 1 already validated the draft's contents (schema, priority floor, evidence snapshot) at draft-creation time. This tool's only job is the guard check plus the INSERT.

### `work_order_id` generation

- **`MAX(work_order_id)` + 1, parsed from the existing `WO-NNN` numeric suffix** — no dedicated Postgres sequence. Rejected the sequence alternative: it solves a concurrent-write race condition that doesn't exist in this project's actual usage pattern (single-request-at-a-time, debug-scale), and the plan's own principle is "debug-first: optimize for inspectability and completion, not realism or scale." A `MAX`+1 query needs no new migration and resets deterministically between test runs the same way the rest of the fixtures already do.
- Produces `WO-003` as the first new ID given the two existing historical rows (WO-001, WO-002).

### `work_orders.status` widening

- **Exactly one new value added**: `status IN ('completed', 'submitted')`. `submit_work_order` always writes `status='submitted'`. `'completed'` remains reserved for the two pre-existing historical rows (WO-001/WO-002) and is never a value this tool produces — there is no "complete this work order" tool anywhere in the canonical v1 contract, so no further lifecycle state needs to be pre-built.
- This resolves the CHECK-constraint widening Phase 1 Task 2 explicitly deferred to Phase 6.

### `approved` column

- **Always written `true`.** Since `submit_work_order` is both structurally and run-time unreachable without `approval_status == "approved"` (Phase 5 Task 7's guard), there is no v1 code path that could ever persist a row with `approved=false`. The column is kept (a real historical-record field a future CMMS integration would care about) but does no enforcement work itself here — `approval_status` already did that upstream.

### Return type — AMENDED

- **Originally decided**: a bespoke `SubmitWorkOrderResult` type mirroring the persisted row.
- **Amended (during Task 5 discussion)**: `submit_work_order` returns the **existing `WorkOrderRecord`** type (`src/maintenance_agent/db/repositories/records.py:95`) directly — no separate, duplicate result type. `WorkOrderRecord` already extends `RepositoryRecord` and exposes `source_type -> "work_order"` / `source_id -> self.work_order_id` as computed properties, which is exactly the shape needed for Task 4's evidence-surfacing mechanism (see Task 4 amendment) and matches whatever else in the codebase already reads historical `work_orders` rows through this same type. One canonical work-order record, reused for the submission result, evidence-union membership, and historical lookups — not three shapes carrying the same fields.
- Consequence: the injectable clock (below) must produce a value compatible with `WorkOrderRecord.created_at: date`, not a `datetime`.

### Clock injection — AMENDED

- **Originally decided**: `clock: Callable[[], datetime] = datetime.utcnow`.
- **Amended**: `clock: Callable[[], date] = date.today`. `WorkOrderRecord.created_at` is typed `date`, matching the frozen dataset's own granularity (WO-001/WO-002 are recorded as plain dates, e.g. `2026-05-18`, with no time component) — a `datetime`-producing clock would need silent truncation at the point of construction, which is worse than just injecting the right-shaped clock in the first place. Still overridden to a fixed value in tests, same "own the seam" pattern as before.

### Function shape

- `submit_work_order(draft: WorkOrderDraft, session: AsyncSession, clock: Callable[[], date] = date.today) -> WorkOrderRecord`.

### Test / Validation

- [ ] Calling `submit_work_order` with `approval_status="approved"` and a valid `WorkOrderDraft` inserts exactly one `work_orders` row with a fresh `WO-NNN` ID one greater than the current max.
- [ ] The inserted row has `status="submitted"` and `approved=true` unconditionally.
- [ ] `work_orders.status` CHECK constraint accepts exactly `'completed'` and `'submitted'` — no other value, confirmed by a test that a third value is rejected at the DB layer.
- [ ] `created_at` on the inserted row reflects the injected `clock()` value, not real wall-clock time, in tests.
- [ ] Calling `submit_work_order` directly with `approval_status != "approved"` raises `ConsequentialActionGuardError` and inserts no row — re-confirms the Phase 5 Task 7 guard is exercised through this concrete implementation, not just asserted abstractly.
- [ ] The returned `WorkOrderRecord`'s fields match the persisted row exactly; no field is silently dropped or renamed relative to the `work_orders` table.
- [ ] `submit_work_order`'s return type is `WorkOrderRecord`, not a separate/duplicate result type — confirmed by code inspection.

---

## Task 3 — Add LangGraph checkpoint/pause behavior between draft and submission

### Checkpointer backend

- **`MemorySaver` (in-memory), not Postgres-backed.** Explicitly rejecting cross-process/cross-restart persistence for pending approvals: this is a debug-scale portfolio project, not something redeployed mid-demo, and the plan's own "debug-first: optimize for inspectability and completion, not realism or scale" principle applies directly here. No new checkpoint tables, no `AsyncPostgresSaver.setup()` step folded into the bootstrap workflow. A pending approval that outlives the running process is an accepted limitation, not a gap to engineer around.
- This resolves the checkpointer question Phase 4 Task 6 explicitly deferred ("in-memory vs. Postgres-backed... deferred entirely to Phase 6").

### Pause primitive

- **LangGraph's dynamic `interrupt()` call**, made from inside a node, resumed via `Command(resume=<value>)` — not the static `interrupt_before`/`interrupt_after` compile-time node-name list (that mechanism is better suited to step-debugging a graph than to a real approve/reject exchange).
- The human's approve/reject decision flows back in as the literal return value of `interrupt()` inside the node, so no separate manual state-patch step is needed before resuming. Keeps the already-locked "no LLM reasoning sits between 'human approved' and 'work order submitted'" property (Phase 4 Task 3) trivially true — the resume value drives a plain conditional, nothing else.

### Node shape

- **Dedicated `await_approval` node.** Reached when the evidence-gathering loop exits with `state.work_order_draft` set (bypassing synthesis entirely — same no-second-LLM-call reasoning already used for the `unknown_asset`/`insufficient_evidence` deterministic templates in Phase 5 Tasks 2/3). This node:
  1. Sets `approval_status = "pending_approval"`.
  2. Calls `interrupt(...)`, pausing the graph.
  3. On resume, sets `approval_status` to `"approved"` or `"rejected"` based on the resume value.
- A separate `submit_work_order_node` follows: calls `submit_work_order` only when `approval_status == "approved"`. Either branch (approved-and-submitted, or rejected-with-nothing-submitted) then reaches the existing terminal node — preserving "terminal node is always the last node on every path that actually completes" (Phase 4 Task 4).

### Response construction on turn 1 (amendment to a Phase 4 invariant)

- Because `interrupt()` pauses the graph *before* it ever reaches the terminal node, nothing builds the `AgentQueryResponse` on turn 1 through the normal node path. **The terminal node's response-assembly logic is extracted into a shared pure function** — `build_response(state, status, ...) -> AgentQueryResponse` — called by (a) the terminal node itself on every path that actually completes a graph run, and (b) the API route layer when it detects the graph returned in an interrupted state (via `graph.get_state(config).next` being non-empty), to construct the turn-1 `needs_approval` response directly from checkpointed state.
- This amends Phase 4 Task 4's test bullet ("the terminal node is the only node that constructs an `AgentQueryResponse` instance") — still true in the narrow sense (no *node* other than the terminal node builds one), but the API route layer now also can, via the same shared helper, specifically for the interrupted/turn-1 case.
- Turn-1's `answer` text is a deterministic template assembled directly from the draft's own fields (issue/priority/recommended_action) — no LLM call, consistent with every other terminal-adjacent status built so far. `status="needs_approval"`, `pending_action={action_type: "submit_work_order", draft_id}`.

### Test / Validation

- [ ] The graph is compiled with `MemorySaver` as its checkpointer; no Postgres checkpoint tables are created.
- [ ] Invoking the graph on a `work_order_request` that reaches `create_work_order_draft` pauses at `await_approval` — confirmed via `graph.get_state(config).next` being non-empty and no terminal-node execution having occurred, before any resume.
- [ ] The turn-1 response (built via `build_response` from the API route, not a node) has `status="needs_approval"`, `pending_action.draft_id == request_id`, and a non-empty deterministic `answer` referencing the draft's `issue`/`priority`.
- [ ] Resuming with `Command(resume="approve")` sets `approval_status="approved"`, routes through `submit_work_order_node`, persists exactly one row, and the resulting turn-2 response is built by the terminal node itself (not the route-level fallback).
- [ ] Resuming with `Command(resume="reject")` sets `approval_status="rejected"`, skips `submit_work_order_node` entirely (confirmed via a test asserting the tool function is never invoked), and reaches the terminal node with no row persisted.
- [ ] `build_response` is the only place `AgentQueryResponse` is constructed from graph state — confirmed no other call site (terminal node included) duplicates the assembly logic inline.

---

## Task 4 — Expose an API mechanism to approve/reject/resume the pending action

### Endpoint shape

- **One endpoint, decision in the request body**: `POST /agent/approvals/{draft_id}` with body `{"decision": Literal["approve", "reject"]}` — not two separate `/approve` and `/reject` routes. One handler, one Pydantic request model (`decision` validated the same Pydantic-everywhere way as the rest of the project), rather than two near-duplicate route functions.
- The path parameter is named `draft_id`, not `request_id`/`thread_id`, even though all three are the same value internally (per Task 1's `draft_id = request_id` decision, and `request_id` doubling as the LangGraph `thread_id` per Phase 4 Task 6). The public API vocabulary matches what a client actually received in turn 1's `pending_action.draft_id`, rather than exposing the internal LangGraph naming.

### Not-found vs. already-resolved

- Two distinct failure shapes, checked via `graph.aget_state(config)` before attempting the resume:
  - **No checkpoint history exists at all** for `draft_id` (never existed, wrong ID, or — since Task 3 locked `MemorySaver` — a draft from before a process restart) → **`404`**.
  - **Checkpoint exists but `.next` is already empty** (the pending action was already approved/rejected/submitted) → **`409 Conflict`**.
- This makes double-submission structurally safe without a general idempotency/replay-caching layer (which the design doc already flags as optional/future scope): a second `approve` call on an already-resolved draft cannot produce a second work order, it just fails with a clear conflict. Retries are **safe but not idempotent** — a genuine duplicate call gets a `409`, not a replay of the original success response. Accepted as sufficient for v1; true idempotent replay would need persisting and re-serving the original response, which is unneeded complexity here.

### Response envelope

- **Reuses `AgentQueryResponse` as-is** for this endpoint's response — no second response schema. Consistent with Phase 0's "later phases fill in fields rather than reshaping the envelope" rule, applied here to a second endpoint rather than a second call to the first one.
- **On approval**: `status="ok"`, `pending_action=None` (resolved), deterministic templated `answer` (e.g. "Work order WO-003 has been submitted for PUMP-103 (priority: high)."), no LLM call — same no-LLM-on-a-terminal-status philosophy used everywhere else in this project.
- **On rejection**: also `status="ok"` — a human declining the draft is a normal, successful completion of the workflow, not an error. Templated `answer` states nothing was created. Phase 0's status enum has no dedicated `"rejected"` value and none is added; `answer` text carries that distinction.

### Surfacing the submission in evidence — AMENDED

- **Originally decided**: construct a generic `StructuredEvidenceItem(source_type="work_order", source_id="WO-003", ...)` wrapper.
- **Amended (during Task 5 discussion)**: no wrapper is constructed. `structured_evidence` is a `Union` of self-describing `RepositoryRecord` subclasses (see Task 5), and `submit_work_order` already returns a `WorkOrderRecord` (Task 2, amended) which natively exposes `source_type -> "work_order"` / `source_id -> self.work_order_id`. The route simply does `structured_evidence.append(result)` with the `WorkOrderRecord` `submit_work_order` returned — no separate construction step.

### Mechanics

- The route resolves `draft_id` → `thread_id` (identical value), calls `graph.ainvoke(Command(resume=decision), config={"configurable": {"thread_id": draft_id}})`, and the graph runs from `await_approval` through to the terminal node exactly as Task 3 described. This task only wraps that flow in the HTTP contract above — it does not change the graph-level mechanism itself.

### Test / Validation

- [ ] `POST /agent/approvals/{draft_id}` with `{"decision": "approve"}` on a valid pending draft returns `status="ok"`, `structured_evidence` containing the `WorkOrderRecord` for the new `work_order_id`, and `pending_action=None`.
- [ ] The same call with `{"decision": "reject"}` returns `status="ok"`, no `work_order` evidence item, and no `work_orders` row persisted.
- [ ] `POST /agent/approvals/{draft_id}` with an unknown `draft_id` returns `404`.
- [ ] Calling the endpoint twice in a row for the same `draft_id` (approve, then approve or reject again) returns `409` on the second call, and confirms only one `work_orders` row exists in total.
- [ ] The response body in all success cases validates against `AgentQueryResponse` — no second/parallel response schema is introduced for this endpoint.

---

## Task 5 — Apply PP-001 recurring-fault and PP-002 consequential-action policies

### PP-002 — no new mechanism

- Fully covered by decisions already locked elsewhere: Phase 5 Task 7's guard (structural tool exclusion from every LLM tool-choice list, plus the runtime `approval_status == "approved"` check) together with this phase's entire draft → checkpoint → approve → submit flow (Tasks 1-4) **is** PP-002's enforcement. No separate policy-engine layer is added on top of what's already structural.

### PP-001 — mostly already covered, one clause left to LLM judgment

- The "require human review before consequential maintenance action" clause is subsumed by PP-002's blanket approval requirement — every submission requires human review regardless of recurrence status, so this doesn't need its own gate.
- The "escalate for root-cause investigation" clause is covered procedurally by the priority floor (Task 1: recurrence ≥3-in-12-months forces `priority="high"`), but the *content* of the escalation — whether `recommended_action` actually reads as root-cause investigation rather than "replace the bearing again" — is left entirely to LLM judgment guided by prompt instructions, with **no deterministic content/keyword check**. Consistent with Phase 5 Task 3's precedent for not deterministically backstopping `confidence`: this is a semantic judgment, not a mechanically checkable fact, and a keyword-based backstop would be brittle in both directions (satisfiable by rote phrasing, or falsely rejecting a genuinely good recommendation worded differently).

### Confirmed implementation gap, closed here

- Verified against the actual codebase (not just the planning docs): `get_plant_policy` results already flow into `state.tool_calls` (via `GetPlantPolicyResult` being part of `ToolResult`, `state.py:38`), but `evidence_gathering_node`'s extraction logic (`graph.py:448`) has no branch handling `GetPlantPolicyResult` — only `GetAssetStatusResult` and `GetMaintenanceHistoryResult` results are extracted into `state.structured_evidence` (currently typed `ClassifiedReading | FaultEventRecord | FaultRecurrence`, `state.py:46`). An existing test (`tests/test_graph_state.py:64`) explicitly asserts policy records are *excluded* from `structured_evidence` — this was a deliberate (if now outdated) narrower design that predates this phase, not an oversight introduced here. Left unfixed, PP-001/PP-002 could never appear in `structured_evidence`, `evidence_used`, or (per Task 1 of this phase) a draft's `supporting_evidence` — a real problem since GS-04 (already in Phase 4/5 scope) requires PP-001 as evidence and GS-08 requires PP-002.
- **Fix**: extend the union — `StructuredEvidenceItem = ClassifiedReading | FaultEventRecord | FaultRecurrence | PlantPolicyRecord`. `PlantPolicyRecord` gets the same `RepositoryRecord`-based `source_type -> "plant_policy"` / `source_id -> self.policy_id` properties `WorkOrderRecord` already has (added now if not already present). Add the missing branch in `evidence_gathering_node`: `elif isinstance(result, GetPlantPolicyResult): structured_evidence.extend(result.policies)`, mirroring the existing branches for the other two result types. Replace the outdated exclusion assertion at `tests/test_graph_state.py:64` with a positive test.

### Test / Validation

- [ ] `PlantPolicyRecord` is a member of the `StructuredEvidenceItem` union and exposes `source_type == "plant_policy"` / `source_id == policy_id`.
- [ ] Calling `get_plant_policy("consequential_action")` during a graph run results in the `PP-002` record appearing in `state.structured_evidence` — confirmed by a new test replacing the old exclusion assertion at `tests/test_graph_state.py:64`.
- [ ] GS-04's run includes `PP-001` in `state.structured_evidence` (recurrence scenario) and GS-08's run includes `PP-002` (consequential-action scenario) — both cross-referenceable from the final API response, not just internal state, per Phase 5 Task 6's original provenance goal.
- [ ] No deterministic check inspects `recommended_action`'s text content for root-cause-investigation language — confirmed no such validator exists; only the `priority` floor is deterministic.
- [ ] PP-002 is never the mechanism that gates `submit_work_order` reachability in code — that remains Phase 5 Task 7's guard; `get_plant_policy`'s role stays purely evidentiary.

---

## Task 6 — Ensure the original user request to create a work order does not implicitly bypass approval

### The bypass is already structurally impossible, not just discouraged

- No field anywhere in the LLM-facing surface can ever set `approval_status="approved"`. The interpretation node's output (`IntentExtractionOutput`: `intent`, `asset_identifier`) has no such field; `WorkOrderDraft`'s tool-facing schema (`issue`, `recommended_action`, `priority`) has no such field. `approval_status` is only ever written by the `await_approval` node's `interrupt()` resume value (Task 3), which only ever originates from the separate `POST /agent/approvals/{draft_id}` call (Task 4). Even a user request phrased as "create **and submit** a high-priority work order for PUMP-103" — the exact adversarial phrasing GS-08's prohibited-behavior line names — has no lever to pull: the LLM can be arbitrarily eager to comply, but nothing in the graph reads intent to mean approval.
- Layered on top: `submit_work_order` is structurally excluded from every LLM tool-choice list (Phase 4 Task 3), runtime-guarded on `approval_status == "approved"` (Phase 5 Task 7), and only reachable via the deterministic `submit_work_order_node` after a real resume (Task 3). Three independent layers, any one of which alone already prevents the bypass this task names.

### Defense-in-depth addition

- `WorkOrderDraft`'s LLM-facing schema gets `model_config = {"extra": "forbid"}` (recorded against Task 1 above). Not required for correctness — an ignored extra field was already harmless, since nothing reads it — but it turns a theoretical injection attempt (e.g. a model trying to pass `{"approved": true}` as a tool-call argument) into an explicit validation error rather than a silently-dropped no-op.

### Deliberately not built: approval-intent detection on `/agent/query`

- A second, brand-new `/agent/query` call phrased like "approve the PUMP-103 work order" cannot resume anything — every `/agent/query` call gets a fresh `request_id`/`thread_id` (Phase 4 Task 6) and never resumes an existing one, so this would just start an unrelated new graph run (most likely producing a redundant second draft, not touching the first).
- **Decision: no classifier or detection is added to `/agent/query` for "this looks like an approval attempt."** The dedicated `POST /agent/approvals/{draft_id}` endpoint remains the only sanctioned path; this is documented for the end user in Phase 9's README rather than enforced by new in-graph mechanism now. Matches the same "optional duplicate-work-order checks... may be added later" deferred-scope language the architecture doc already uses for the adjacent problem of redundant simultaneous drafts for the same asset — both are explicitly out of v1 scope, not oversights.

### Test / Validation

- [ ] A `work_order_request` whose user text explicitly claims prior approval or asks for immediate submission (e.g. "create and submit...") still pauses at `await_approval` exactly like GS-08's baseline phrasing — confirmed by a test using this adversarial phrasing as input.
- [ ] Code inspection confirms no field on `IntentExtractionOutput` or `WorkOrderDraft` can influence `approval_status`.
- [ ] `WorkOrderDraft`'s Pydantic model rejects an unrecognized field (e.g. a mocked tool-call attempting `{"approved": true, ...}`) with a validation error rather than silently accepting the call.
- [ ] A second `/agent/query` call referencing an existing pending draft's asset does not resume, approve, or reject the original draft — confirmed by asserting the original draft's `approval_status` is unchanged and the new call gets its own independent `request_id`.

---

## Success Criteria

- [ ] `create_work_order_draft` never writes to the database; only `submit_work_order` does, and only after `approval_status=="approved"` — enforced by Task 1's no-persistence design, Phase 5 Task 7's structural + runtime guard, and Task 6's confirmation that no LLM-facing field can set approval status (Tasks 1, 2, 6).
- [ ] The recurrence-driven priority floor and the plant-policy evidence-extraction fix mean PP-001 and PP-002 are both fully enforced (procedurally) and fully citable (evidentially) — closing a real gap that predated this phase's own planning (Task 1, Task 5).
- [ ] The graph genuinely suspends mid-run via LangGraph's `interrupt()`/`Command(resume=...)` primitive on an in-memory checkpointer, with a shared `build_response` helper bridging the one case (turn-1 `needs_approval`) where the terminal node itself never executes (Task 3).
- [ ] A single, minimal HTTP surface (`POST /agent/approvals/{draft_id}`) resumes a suspended run, reusing the existing `AgentQueryResponse` envelope and distinguishing not-found (`404`) from already-resolved (`409`) without a general idempotency layer (Task 4).
- [ ] Golden Scenario 8 is fully specified end-to-end: draft creation reflects recurrence/root-cause investigation (not a simplistic bearing replacement) via the `recommended_action` field and priority floor; the checkpoint is real and inspectable; approval produces exactly one valid, evidence-linked work order; rejection produces none; and no phrasing of the original request can skip the checkpoint (Tasks 1-6).
- [ ] Two implementation-time corrections were made against already-written code rather than left to drift: `submit_work_order`'s return type and clock granularity (Task 2), and how the submitted work order is surfaced as evidence (Task 4) — both reconciled with the real `WorkOrderRecord`/`RepositoryRecord` pattern rather than the generic-wrapper shape originally assumed from Phase 5's planning-doc wording.

## Status

All six Phase 6 tasks are locked. Phase 6 planning is complete. Next: proceed to implementation (Tasks 1 and 3 are net-new work; Tasks 2, 4, and 5 are corrections/extensions to already-implemented Phase 2/4/5 code), or move on to Phase 7 (Structured Telemetry Seam) planning discussion.