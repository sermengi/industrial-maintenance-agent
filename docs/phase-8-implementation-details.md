# Phase 8 (Golden Scenario Integration & Hardening) — Implementation Decisions

Captured from planning discussion, 2026-08-22. These are decisions made ahead of implementation, refining Implementation Plan v1.0 / Phase 8 and Dataset Design Specification v1.1 §10 (Golden Scenarios) / §11 (Debug Evaluation Contract), building on the Phase 4 (LangGraph Agent Orchestration), Phase 5 (Reliability, Validation & Guardrails), Phase 6 (HITL & Work-Order Workflow), and Phase 7 (Structured Telemetry Seam) decisions without contradicting any of them. Decisions are locked task-by-task, following the Phase 8 task list from the implementation plan:

1. Encode deterministic assertions for expected asset resolution, required/forbidden tools, HITL state, and output schema. **(locked)**
2. Encode evidence assertions for required structured records and RAG document IDs where practical. **(locked)**
3. Keep nuanced behavioral assertions manually reviewable or deterministic where safely expressible. **(locked)**
4. Run all eight scenarios through the public API, not only internal graph calls. **(locked)**
5. Fix only defects required to satisfy the frozen contracts; do not expand the dataset or scenario suite by default. **(locked)**
6. Add end-to-end regression coverage to CI where stable and cost-appropriate. **(locked)**

---

## Task 1 — Encode deterministic assertions for expected asset resolution, required/forbidden tools, HITL state, and output schema

### Assertion source for tool trajectory (the central decision for this task)

- Tool-call-order and containment assertions read from the **Phase 7 `RunEvent.tool_calls`** (`ToolCallSummary`: `tool_name`, `sequence`), not from direct LangGraph state access (`graph.aget_state(config)`).
- Rationale: Phase 4 Task 1 explicitly reserved `state.tool_calls` as "the shared source of truth for Phase 8's tool-call-order/containment assertions and Phase 7's telemetry event," and Phase 7 already built the read path — including reading `tool_calls` from a *paused* (turn-1, GS-08) run via `graph.aget_state(config).values` (Phase 7 Task 2). Reusing it keeps Phase 8's assertions genuinely "through the public API" (this phase's own key design constraint): `RunEvent` is emitted as a side effect of the route call the test already made, not a reach into graph internals.
- Mechanism: tests use the same in-memory `EmitFn` stub Phase 7 Task 3 established for CI (collecting emitted `RunEvent`s into a list), rather than the real JSONL sink, for per-scenario assertions — fast, no filesystem I/O. At least one separate test (already specified in Phase 7 Task 4/5) continues to exercise the real JSONL sink end-to-end; Phase 8 does not duplicate that, it only consumes the emitted events.
- Rejected alternative: direct `graph.aget_state()` access in the golden-scenario test suite for full `ToolCallRecord` detail (args/results). Rejected because it duplicates a read path Phase 7 already built for exactly this purpose, and reaches past the API boundary Phase 8's own task list says to test against ("not only internal graph calls").

### Required / optional / forbidden tools — closed-world derivation (the second central decision)

- Each scenario declares `required_tools` and `optional_tools` only. **Any canonical tool not in either set is implicitly forbidden** — no separate, hand-authored `forbidden_tools` list is maintained per scenario.
- Rationale: matches this project's repeated preference for a structural guarantee over an authored list that can drift (e.g. the intent-filtered tool list in Phase 4 Task 4, the `consequential` binding filter in Phase 5 Task 7). A forbidden list would need to be kept in sync with the tool contract by hand across 8 scenarios; the closed-world rule makes it self-maintaining — e.g. GS-01 not listing `create_work_order_draft` as required or optional already forbids it, with nothing extra to write down.
- `resolve_asset` is `required` for every scenario (deterministic first node, always logged into `tool_calls` regardless of LLM involvement per Phase 4 Task 4). For GS-07 it is the *only* required tool; every other canonical tool is forbidden by the same closed-world rule, with no special-cased "STOP" assertion needed.
- `submit_work_order` is `required` only in GS-08's `approval_step` (approve case) — asserted post-resume, not on the turn-1 response. It is forbidden (closed-world default) on every other scenario and on GS-08's own turn 1.
- Tool *count* is not asserted, only presence/absence — e.g. GS-05's two `search_maintenance_docs` calls with different queries satisfy `required_tools: [search_maintenance_docs, ...]` the same as one call would; Phase 4 Task 5 already locked that call count is fully LLM-determined.

### Fixture representation

- Golden scenarios are stored as a **YAML data file** (`tests/golden/scenarios.yaml`), per Dataset Design Specification v1.1 §12's explicit recommendation ("keep the golden scenarios in a machine-readable YAML/JSON fixture so tests can load expected intents, required tools, evidence IDs, prohibited behavior flags, and HITL requirements").
- Loaded through one `GoldenScenario` Pydantic model (`model_config = {"extra": "forbid"}`, consistent with the project's Pydantic-everywhere convention and its other frozen-contract schemas' `extra="forbid"` precedent) — the YAML is validated once at load time, so a malformed or stale fixture entry fails fast in a fixture-loading test, not silently inside a scenario's assertions.
- Rejected alternative: native Python fixtures (Pydantic instances defined directly in a test/fixtures module). Would avoid a serialization layer, but departs from the dataset spec's explicit §12 recommendation without a concrete reason to; the YAML file is also easier for a non-code reviewer to read against the dataset spec's own §10 tables when auditing golden-scenario coverage.

### `GoldenScenario` schema

```
GoldenScenario (extra="forbid"):
  id: str                              # "GS-01".."GS-08"
  query: str
  asset_id_hint: str | None            # -> AgentQueryRequest.asset_id, where the scenario supplies it
  expected_intent: Literal["troubleshooting","maintenance_check","history_query","procedure_lookup","work_order_request"]
  expected_asset_id: str | None        # None only for GS-07
  required_tools: list[str]
  optional_tools: list[str]            # forbidden = canonical_7 - required - optional; not asserted either way
  expected_status: Literal["ok","needs_approval","unknown_asset","insufficient_evidence","error"]
  required_evidence_ids: list[str]     # placeholder for Task 2; populated there
  hitl: bool
  approval_step: ApprovalStep | None   # set only for GS-08

ApprovalStep (extra="forbid"):
  decision: Literal["approve","reject"]
  expected_status: Literal["ok"]
  required_tools_after_resume: list[str]   # ["submit_work_order"] for approve, [] for reject
```

- A scenario with `approval_step: None` runs as one `POST /agent/query` call; assertions run against that single response and its one `RunEvent`.
- A scenario with `approval_step` set (GS-08 only, in the frozen file) runs turn 1 first — asserting `status == "needs_approval"` and capturing `pending_action.draft_id` — then calls `POST /agent/approvals/{draft_id}` with `approval_step.decision`, asserting turn 2 against `approval_step.expected_status` / `required_tools_after_resume`. This produces exactly two `RunEvent`s sharing one `run_id`, matching Phase 7 Task 1's design.

### Asset resolution assertion

- `response.asset_id == scenario.expected_asset_id` for every scenario.
- Additionally, sourced from the same `RunEvent.tool_calls` used for tool-trajectory checks: `tool_calls[0].tool_name == "resolve_asset"` for every scenario (deterministic-first-node guarantee, Phase 4 Task 4), reusing one read rather than adding a second mechanism for this half of the check.

### HITL state assertion

- One shared check driven by `scenario.hitl`: `response.status == "needs_approval"` if and only if `hitl` is true, and `response.pending_action is not None` if and only if `hitl` is true. Applied uniformly across all 8 scenarios rather than special-cased for GS-08 — this doubles as the negative assertion the spec's "HITL: No" column implies for GS-01–GS-03, GS-05–GS-07.

### Output schema assertion

- FastAPI's `response_model=AgentQueryResponse` (locked Phase 0) already makes a schema-invalid response impossible to receive over the wire, so Task 1 does not add a second, redundant validation layer for that.
- What Task 1 actually adds: `response.status == scenario.expected_status` (exact literal match) — the one thing HTTP-level schema validity alone doesn't guarantee, and the field Phase 8's own "no exact NL matching, assert structure/evidence/actions" principle designates as the structural stand-in for correctness.

### GS-08 reject-path scope

- Dataset Design Specification v1.1 §10.8 defines GS-08 as the approve flow only; §13's freeze rule prohibits adding scenarios without a blocking defect. The frozen `scenarios.yaml` entry for GS-08 therefore stays approve-only (`approval_step.decision: "approve"`).
- The reject-path behavior Phase 6 Task 4 already specifies as its own test (`{"decision": "reject"}` → `status="ok"`, no work order persisted) remains a separate regression test alongside the golden suite — not a 9th golden scenario, and not a variant that changes GS-08 itself.

### Interaction with open Phase 7 findings

- Neither open Phase 7 finding (route-level error-path `asset_id`/message leak; checkpoint-serializer unregistered-type warnings) is exercised by GS-01–GS-08 directly — the first lives on the forced-error path (no golden scenario forces an unhandled route-level exception), the second is a checkpoint round-trip concern orthogonal to golden-scenario outcomes. Per Task 5's "fix only defects required to satisfy the frozen contracts" rule, neither blocks Task 1 regardless of their current fix status.

### Test / Validation

- [ ] `scenarios.yaml` loads and validates against `GoldenScenario` with `extra="forbid"` — an unrecognized key or missing required field fails fixture loading, not an individual scenario's test.
- [ ] All 8 entries (`GS-01`..`GS-08`) are present in the fixture file; exactly one (`GS-08`) has `approval_step` set.
- [ ] For every scenario, `tool_calls[0].tool_name == "resolve_asset"` and `response.asset_id == expected_asset_id` (`None` only for `GS-07`).
- [ ] For every scenario, every tool in `required_tools` appears at least once in `RunEvent.tool_calls`, and no tool outside `required_tools ∪ optional_tools` appears at all (closed-world forbidden check).
- [ ] `GS-07`'s `RunEvent.tool_calls` contains exactly one entry (`resolve_asset`) and no others.
- [ ] For every scenario, `response.status == expected_status`; `response.status == "needs_approval"` and `response.pending_action is not None` hold iff `hitl` is true.
- [ ] `GS-08` turn 1 produces `status="needs_approval"` with a non-empty `pending_action.draft_id`; the subsequent `POST /agent/approvals/{draft_id}` call with `approval_step.decision` produces `status == approval_step.expected_status`, and post-resume `RunEvent.tool_calls` satisfies `required_tools_after_resume` (`submit_work_order` present for approve, absent for reject).
- [ ] All tool-trajectory and HITL-state assertions are read from `RunEvent`s collected via the Phase 7 in-memory `EmitFn` test stub — no test in this suite calls `graph.aget_state()` directly.
- [ ] A GS-08 reject-path regression test exists alongside the golden suite (per Phase 6 Task 4) and is not represented as a 9th entry in `scenarios.yaml`.

### Status

Task 1 is locked.

---

## Task 2 — Encode evidence assertions for required structured records and RAG document IDs where practical

### Assertion level: retrieval, not citation (the central decision for this task)

- `required_evidence_ids` are checked against **presence in the response body's `structured_evidence`/`document_evidence` lists** (i.e. the evidence was retrieved and surfaced), **not** against `evidence_used` (i.e. that the synthesis LLM specifically cited it).
- Rationale: `claude/phase5-finding-evidence-used-not-exposed.md` already established, empirically, that the synthesis LLM legitimately grounds an answer in a subset of retrieved evidence — GS-06's real walkthrough retrieved DOC-01/DOC-02/DOC-05 but cited only DOC-01, and that's correct behavior, not a bug (Phase 5 Task 6 validates that whatever *is* cited is real and non-empty, never that citations are exhaustive). Asserting `required_evidence_ids ⊆ evidence_used` would make Task 2 fail on exactly the kind of correct, selective grounding this project's own finding documented — a citation-level assertion would be testing LLM stylistic choice, not retrieval correctness. Retrieval-level presence is also the more genuinely deterministic reading of dataset spec §11.2's own wording ("required structured records are present **in the trajectory or agent state**" — not "cited").
- Consequence: Task 2 adds no new check on `evidence_used` itself — Phase 5 Task 6's existing citation-existence/non-empty validator already covers that, and re-asserting it here would duplicate a guarantee that's already structural.
- "Unsupported evidence is not fabricated" (dataset spec §11.2's third bullet) needs no new mechanism either: `structured_evidence`/`document_evidence` are populated only from real tool-call results (Phase 4/5 design — the LLM never writes to these lists directly), so fabrication is already structurally impossible, not something Task 2 needs to separately test for.

### ID scheme: reuse natural keys, extended with one new case for recurrence

- Every `required_evidence_ids` entry is a natural-key ID already established elsewhere in the project: `source_id` for `structured_evidence` items (fault event, maintenance event, observation, telemetry snapshot, operating limit, plant policy, work order — per Phase 5 Task 6's `source_type`/`source_id` amendment) or `document_id` for `document_evidence` items (`DOC-01`..`DOC-05`, per Phase 3). A scenario's `required_evidence_ids` is checked as a subset of `{item.source_id for item in structured_evidence} | {item.document_id for item in document_evidence}` — one flat set, since the two ID namespaces (`FE-`/`TS-`/`ME-`/`OBS-`/`OL-`/`PP-`/`WO-` vs. `DOC-`) never collide.
- **New case, resolved this task**: `get_maintenance_history`'s "recurrence context" (GS-04/GS-08's "three F102 occurrences") has no natural DB-row ID — a recurrence is a derived fact, not a fixture record. Per the locked answer above, `FaultRecurrence.source_id` reuses the triggering **`fault_code`** itself (e.g. `"F102"`), `source_type="fault_recurrence"` — consistent with Phase 5 Task 6's "no new ID scheme invented, reuse the dataset's own IDs" precedent (`fault_taxonomy.fault_code` is already a real, stable ID). This is now the locked answer to the gap Phase 4/5 left open (`FaultRecurrence` was named as a `StructuredEvidenceItem` member but never given a `source_id` scheme).
- GS-04/GS-08 therefore each require **both** `FE-004` (the current active fault event, surfaced via `get_asset_status`) **and** `"F102"` (the recurrence marker, surfaced via `get_maintenance_history`) — two distinct evidence items from two distinct tools, not one collapsed into the other.

### GS-03 evidence (resolves the spec discrepancy)

- Per the locked answer above, `get_maintenance_history` is added to GS-03's `optional_tools` (not `required_tools`, since the trajectory column never lists it; not omitted either, since the evidence column's "healthy maintenance history" implies it's a legitimate thing to call). Because it's optional, its output (`ME-001`, `ME-002`) is **not** added to `required_evidence_ids` — consistent with Task 1's general rule that optional tools' effects aren't asserted either way. "Healthy maintenance history" becomes a Task 3 qualitative note (nothing contradicts the healthy read) rather than a Task 2 structural requirement.

### GS-08's turn-2 evidence: relative check, not a hardcoded ID

- The approved work order's evidence item (`WorkOrderRecord`, `source_type="work_order"`) is asserted **relatively**: exactly one new `structured_evidence` item with `source_type="work_order"` appears in the turn-2 response that was not present in turn 1. The exact `work_order_id` (`WO-003` under a freshly bootstrapped fixture DB, per Phase 6 Task 2's `MAX()+1` scheme) is deliberately not hardcoded in `scenarios.yaml`.
- Rationale: whether golden-scenario tests run against a DB reset between scenarios (making `WO-003` deterministic) or a shared DB across the suite (where the exact next ID depends on run order) is a test-isolation question that belongs to Task 4 ("run all eight scenarios through the public API"), not this task. A relative check is correct under either answer, so Task 2 doesn't need to wait on that decision.
- `approval_step` therefore gains one more field, populated only for GS-08's approve case: `required_evidence_source_types_after_resume: list[str]` (here, `["work_order"]`) — asserted as "at least one item of this `source_type` is present in turn 2 that wasn't in turn 1," alongside the existing `required_tools_after_resume` check from Task 1.

### Per-scenario `required_evidence_ids`

| Scenario | Required evidence IDs | Notes |
| --- | --- | --- |
| GS-01 | `FE-001`, `TS-002`, `OL-001`, `DOC-03`, `ME-003` | F101 active, vibration 8.1 mm/s, CP-200 vibration limit, DOC-03, prior coupling realignment |
| GS-02 | `OBS-002`, `TS-002`, `DOC-03`, `ME-003` | Operator vibration observation, vibration 8.1 mm/s, DOC-03, prior alignment history |
| GS-03 | `TS-001` | Bearing temp 54°C only; no active fault is an absence, not an ID; maintenance history is optional/unasserted (see above) |
| GS-04 | `TS-003`, `OL-002`, `FE-004`, `F102`, `ME-006`, `ME-007`, `ME-008`, `DOC-04`, `PP-001` | Bearing temp 91°C, adopted 82°C limit, active fault, recurrence marker, two replacements, lubrication inspection, DOC-04, PP-001 |
| GS-05 | `TS-004`, `FE-005`, `OBS-001`, `ME-009`, `ME-010`, `DOC-01`, `DOC-02`, `DOC-05` | Discharge pressure/flow snapshot, F103 active, seal-leak observation, prior seal wear, no-blockage inspection, three docs |
| GS-06 | `DOC-01` | Asset existence is covered by Task 1's `expected_asset_id` check, not a `structured_evidence` item |
| GS-07 | *(none)* | No asset resolved; empty `structured_evidence`/`document_evidence` expected on the terminal template response |
| GS-08 (turn 1) | `TS-003`, `FE-004`, `F102`, `ME-006`, `ME-007`, `PP-002` | Same recurrence/temperature pattern as GS-04, plus PP-002 (consequential-action policy) in place of PP-001, matching the dataset spec's own evidence column for GS-08 |
| GS-08 (turn 2 / approve) | *(relative check only — see above)* | New `source_type="work_order"` item vs. turn 1 |

### Test / Validation

- [ ] For every scenario, every ID in `required_evidence_ids` is present in `{structured_evidence[*].source_id} ∪ {document_evidence[*].document_id}` of the (turn-1, for GS-08) response.
- [ ] `GS-07`'s response has empty `structured_evidence` and `document_evidence`.
- [ ] `GS-04` and `GS-08` each assert both `FE-004` (active fault, from `get_asset_status`) and `F102` (recurrence marker, from `get_maintenance_history`) are present as distinct evidence items.
- [ ] `FaultRecurrence.source_type == "fault_recurrence"` and `source_id` equals the triggering `fault_code` exactly (e.g. `"F102"`), confirmed by a unit test independent of any golden scenario.
- [ ] `GS-03`'s `optional_tools` includes `get_maintenance_history`; a run that calls it and one that doesn't both pass Task 2's evidence assertions unchanged (evidence from that call is never required).
- [ ] No test in this suite asserts anything about `evidence_used` — confirmed by code inspection, keeping Task 2 strictly at the retrieval layer and leaving citation-existence validation to Phase 5 Task 6's own tests.
- [ ] `GS-08`'s turn-2 assertion locates the new work-order evidence item by `source_type == "work_order"` and set-difference against turn 1's `structured_evidence`, never by a hardcoded `work_order_id` string.
- [ ] `scenarios.yaml` gains `required_evidence_ids` (all scenarios) and `required_evidence_source_types_after_resume` (GS-08 only) fields, both validated by the same `extra="forbid"` `GoldenScenario`/`ApprovalStep` models from Task 1.

### Status

Tasks 1–2 are locked.

---

## Task 3 — Keep nuanced behavioral assertions manually reviewable or deterministic where safely expressible

### Governing split (the central decision for this task)

For each of dataset spec §11.3's four behavioral-assertion bullets, the decision is which of three buckets it falls into — **already covered** (nothing new needed), **newly deterministic** (a safe structural/negative check added here), or **stays manual** (captured for human review, never auto-graded) — rather than treating "behavioral assertions" as one undifferentiated manual pile:

| Spec bullet | Bucket | Mechanism |
| --- | --- | --- |
| Unknown assets stop the workflow early | Already covered | Task 1's closed-world tool check + `status=="unknown_asset"`; nothing new |
| Diagnostic uncertainty is preserved when root cause is not proven | Newly deterministic (partial) | `confidence != "confirmed"` negative check, see below |
| Contradictory user claims do not override structured evidence | Already covered + newly deterministic (partial) | No fabricated evidence (structural, Task 2) + no `create_work_order_draft` call (Task 1 closed-world) + the same `confidence` negative check |
| Recurring failures trigger escalation rather than a naive repeat repair | Already covered + stays manual | Priority floor + PP-001/evidence presence (Phase 6 Task 1, Task 2) already deterministic; whether `recommended_action` text actually *reads* as root-cause investigation stays manual |

This mirrors the project's existing pattern of preferring a **negative/structural** check ("must not claim X") over a **positive content** check ("must say Y") wherever the dataset spec's own wording is itself a prohibition — consistent with Phase 5 Task 3's rejected keyword-backstop for `confidence` and Phase 6 Task 5's rejected keyword-backstop for `recommended_action`'s content. Task 3 does not reopen either of those rejections; it only asks, freshly, whether a *different*, safely-negative version of the same idea exists — and for uncertainty preservation, it does.

### Deterministic negative check: `confidence != "confirmed"`

- For the five troubleshooting-intent scenarios (`GS-01`, `GS-02`, `GS-03`, `GS-04`, `GS-05`), assert `response.confidence != "confirmed"` — i.e. it must be `"hypothesis"` (or, structurally, could never legitimately be `None` here since Phase 5 Task 3 already guarantees synthesis runs and evidence is non-empty on all five). This is safe to assert exactly, not just "manually reviewable," because Dataset Design Specification v1.1 §8 (Asset Ground Truth) states unconfirmed root cause for all four assets without exception — there is no golden scenario whose ground truth would make `"confirmed"` correct, so asserting its absence never risks penalizing a genuinely right answer.
- Deliberately **not** asserting the positive value (`confidence == "hypothesis"` specifically, as opposed to ruling out `"confirmed"`) as a blanket rule — `GS-03`'s exact expected value (`"hypothesis"`) is already locked by Phase 5 Task 3's own test bullet and is carried forward unchanged, but `GS-01`/`GS-02`/`GS-04`/`GS-05` get only the negative check here; asserting the positive value everywhere would be indistinguishable from grading the LLM's phrasing choice between two epistemically similar labels, which is exactly what dataset spec §11's "no exact NL matching... behavioral checks may be manual" carve-out exists to avoid.
- `GS-06` (`procedure_lookup`) is excluded from this check entirely — the asset ground truth table (§8) scopes "root cause" to diagnosis, and a procedure-lookup answer isn't diagnosing anything; `confidence` is left fully unconstrained (and unasserted) for `GS-06`, consistent with `SynthesisOutput.confidence` being legitimately `Optional`.
- `GS-07` and `GS-08` are excluded because their `answer` is never LLM-generated at all (see below) — `confidence` stays structurally `None` on both (no synthesis node runs on either path), asserted as a hard equality, not a negative check, since it's fully deterministic.

### Deterministic template-answer assertions (a second, previously-untapped source of determinism)

- `GS-07`'s `answer` and both of `GS-08`'s `answer`s (turn 1 `needs_approval`, turn 2 approve/reject) are **not LLM output** — they're deterministic templates already locked in Phase 5 Task 2 (`f"I couldn't find an asset matching '{identifier}'..."`) and Phase 6 Tasks 3–4 (draft-field-derived turn-1 text; `"Work order {id} has been submitted..."` / rejection text for turn 2). Because the exact template shape is already a locked implementation decision from earlier phases, Task 3 asserts these **precisely**, not manually:
  - `GS-07`: `answer` contains the literal attempted identifier (`"PUMP-999"`) and the fixed phrase confirming an invalid/not-found asset.
  - `GS-08` turn 1: `answer` references the draft's `issue` and `priority` fields (cross-checked against the actual `WorkOrderDraft` returned in `pending_action`, not a separately hardcoded string).
  - `GS-08` turn 2 (approve): `answer` contains the new work order's `source_id` and `priority`, matching Task 2's relative work-order-evidence check.
  - `GS-08` turn 2 (reject, regression test only, per Task 1): `answer` states nothing was created and contains no work-order identifier.
- This is new only in the sense that Task 3 is the first place these get asserted as *behavioral* correctness (not just "a well-formed response happened") — the underlying templates themselves were fixed elsewhere, so nothing here revisits Phase 5/6.

### What stays manual, and why nothing more is squeezed into "deterministic"

- **Recurring-failure escalation content** (`GS-04`, `GS-08`): whether `recommended_action`/`answer` actually reads as genuine root-cause investigation rather than "replace the bearing again" is pure prose judgment. The structural supports (priority floor, PP-001/`F102`/`ME-006`/`ME-007` evidence presence) are already locked and already asserted (Phase 6 Task 1, Phase 8 Task 2) — Task 3 adds no keyword/substring check on top, deliberately, per Phase 6 Task 5's already-rejected alternative.
- **Diagnostic hedging naturalness / hypothesis ranking quality** (`GS-01` prioritizing alignment among hypotheses, `GS-05` ranking seal leakage as the stronger hypothesis while preserving uncertainty): the *fact* that competing evidence exists is already asserted structurally (Task 2's required evidence IDs), but which hypothesis the answer foregrounds and how it's worded is content judgment, left manual.
- **GS-06's safety-guidance completeness** ("do not omit relevant safety guidance"): `DOC-01`'s presence is asserted (Task 2); whether the answer text actually surfaces the safety caveat is manual.

### Manual-review mechanism: a captured report, not a separate test

- No new, parallel "manual" test function is added. The **same** per-scenario golden test that already makes the real API call for Tasks 1–2's deterministic assertions also appends `response.answer`, `response.confidence`, and a short evidence-ID summary to a regenerated `tests/golden/manual_review_report.md` — one row per scenario (two for `GS-08`), rewritten fresh on every run, not diffed or asserted against.
- Nothing about the report generation step can fail the build: it's a side effect of a test that already ran and already passed its real (deterministic) assertions. This keeps CI's pass/fail gate strictly on the deterministic checks from Tasks 1–2 and this task's negative/template checks, while still producing a standing, reviewable artifact — never an LLM-graded one — for the four qualitative items above.
- The report doubles as prep material for Phase 9's scripted demo (which already needs to show "normal/contradiction handling, multi-source troubleshooting, recurring-fault reasoning, and HITL work-order submission" — the same four behavioral categories this task addresses), so it isn't a single-purpose artifact.

### Test / Validation

- [ ] `GS-01`, `GS-02`, `GS-03`, `GS-04`, `GS-05` each assert `response.confidence != "confirmed"`; none asserts a specific positive value except `GS-03` (`"hypothesis"`, carried forward unchanged from Phase 5 Task 3).
- [ ] `GS-06` has no `confidence` assertion at all — confirmed by code inspection, not merely by the value happening to pass.
- [ ] `GS-07`'s `answer` is asserted to contain the exact attempted identifier and the fixed not-found phrase from Phase 5 Task 2's template.
- [ ] `GS-08` turn 1's `answer` is cross-checked against `pending_action`'s draft fields, not a second hardcoded string that could drift from the actual template.
- [ ] `GS-08` turn 2 (approve)'s `answer` references the new work order's `source_id`/`priority`; the reject-path regression test's `answer` contains no work-order identifier.
- [ ] `tests/golden/manual_review_report.md` is regenerated (not appended-to) on every full golden-suite run, contains exactly one row per scenario (two for `GS-08`), and its generation step has no assertion that can fail the test it's attached to.
- [ ] No test anywhere in the Phase 8 suite performs keyword/substring matching against `answer`/`recommended_action` free text as a stand-in for content correctness — confirmed by code inspection, consistent with Phase 5 Task 3's and Phase 6 Task 5's prior rejections of that approach.

### Status

Tasks 1–3 are locked.

---

## Task 4 — Run all eight scenarios through the public API, not only internal graph calls

### LLM backend: real Anthropic API only (the central decision for this task)

- The golden-scenario suite calls the **real Anthropic API** — it does not reuse Phase 4 Task 2's mocked-LLM CI policy.
- Rationale: Phase 4 Task 2's mocked-client policy governs the project's *general* test suite (node/unit-level tests validating graph wiring), decided before Phase 8 existed. Phase 8's own task list, though, explicitly instructs asserting "structure/evidence/actions rather than prose wording... when model nondeterminism exists" — wording that presupposes genuine nondeterminism, which a scripted mock doesn't have. A mocked run would validate only that the graph correctly routes to whatever tools the mock was scripted to call — it can't validate whether the real model actually produces GS-01's expected trajectory, GS-03's contradiction-handling, or GS-04's escalation framing, which is the entire point of "the frozen eight-scenario suite as the final functional contract for Agent v1" (Phase 8's own goal statement).
- Scope is narrow and doesn't touch what's already decided: Phase 4–7's existing mocked-LLM node/unit tests are unchanged — this is additive, not a reversal, and applies only to the golden-scenario suite introduced in this phase.
- Consequence: the golden-suite CI job needs a real `ANTHROPIC_API_KEY` secret, unlike every other existing CI job (which Phase 4 Task 2 explicitly runs "with zero external network calls and no API key present"). This is a new, scoped exception to that policy, not a change to it.
- Consequence for flakiness: real model calls introduce genuine run-to-run variance the mocked suite never had — a scenario could occasionally call an unexpected tool or land on a different (but still valid) intent, tripping Task 1's closed-world check. Task 4 doesn't resolve a retry/flake policy for this — that's explicitly left to Task 6, since it's a CI-scoping question ("cost-appropriate"), not a scenario-encoding one.

### Test transport: in-process ASGI client against real Compose-managed Postgres

- The suite uses an ASGI-transport `httpx.AsyncClient` bound directly to the FastAPI app object (`ASGITransport(app=app)`), not a bare `graph.ainvoke()` call and not, by default, a live HTTP socket against a separately-running container.
- This satisfies "through the public API, not only internal graph calls" in the sense that matters: requests go through the actual FastAPI routes, `AgentQueryRequest`/`AgentQueryResponse` Pydantic validation, the route-level try/except, and `record_run_event` — the full Phase 0–7 request pipeline — not a direct graph invocation that skips all of that.
- The Postgres side is the **real Compose-managed database**, not sqlite-in-memory or a mocked repository layer — this matches the pattern Phase 0 already established for its own DB-connectivity integration test, so Task 4 isn't introducing a new testing tier, just applying the existing one to the golden scenarios.
- A secondary benefit specific to `GS-08`: because both the turn-1 (`create_work_order_draft` → pause) and turn-2 (`POST /agent/approvals/{draft_id}` → resume) calls hit the *same in-process app instance*, Phase 6 Task 3's `MemorySaver` checkpointer (bound to the one compiled graph built once at startup) persists correctly between the two calls without needing any cross-process or cross-container state sharing.
- Rejected alternative (as the default): spinning up the full `docker compose` stack and hitting it over real HTTP for every run. Not wrong, but slower and heavier than needed for the mechanism this task is actually verifying (API-layer correctness) — reserved instead for the plan's own "rebuild containers from clean state and rerun the suite" validation bullet, addressed separately below.

### Satisfying "rebuild containers from clean state and rerun the suite"

- The identical golden-scenario suite (same fixtures, same assertions) also runs, unmodified, against a **live `docker compose up` stack** by swapping the test client's base URL/transport — a real HTTP client instead of the ASGI transport, everything else unchanged. This is not a second suite to maintain, just a different fixture for how the client reaches the app.
- This path is **not** part of the default CI gate — it's the concrete mechanism behind Phase 8's own "rebuild containers from clean state and rerun the suite" test bullet, exercised manually or pre-release, with exact cadence left to Task 6 (consistent with that task deciding CI scope generally) and to Phase 9 (which needs this same clean-clone-to-healthy-stack path documented for reviewers regardless of Phase 8's own testing cadence).

### DB isolation and execution order

- **One session-scoped reset**, using Phase 1's existing bootstrap/reset workflow, at the start of the golden-scenario test session — not a per-scenario reset. This is safe because only one call in the entire eight-scenario suite ever writes to the database (`GS-08`'s approve step calling `submit_work_order`); every other scenario, including `GS-08`'s own draft creation, is read-only (`create_work_order_draft` performs zero DB writes, per Phase 6 Task 1).
- Scenarios run **serially, in a fixed order** (`GS-01`through `GS-08`, with the `GS-08` reject-path regression test — Task 1's supplementary, non-golden test — running after it), not in parallel and not order-randomized. Given only one write exists in the whole suite, this is more about keeping a single, readable log of a real-model run (useful input to Task 3's manual-review report) than about correctness — Task 2's relative work-order-evidence check already doesn't depend on ordering or on hitting the DB's exact `MAX()+1` value.

### Test / Validation

- [ ] The golden-scenario suite's LLM client fixture is the real `AsyncAnthropic`-backed `generate()`/`generate_structured()` implementation from Phase 4 Task 2, not the mocked stub used elsewhere in the test suite — confirmed by fixture inspection, not just by the tests happening to pass.
- [ ] The suite's CI job is configured with a real `ANTHROPIC_API_KEY` secret; every other existing CI job remains unchanged (no key, no network access).
- [ ] Every scenario's request goes through `app`'s actual FastAPI routes via `ASGITransport` — confirmed no test in this suite imports or calls the compiled graph object directly.
- [ ] The suite runs against Compose-managed Postgres, reset exactly once per test session via Phase 1's existing bootstrap/reset mechanism — confirmed by a test asserting `work_orders` contains exactly `WO-001`/`WO-002` immediately after the session-scoped reset and before any scenario runs.
- [ ] `GS-08`'s two calls (turn 1, turn 2) share one `httpx.AsyncClient`/app instance, confirmed the in-memory checkpointer round-trips the pending draft correctly between them without any manual state injection.
- [ ] The identical suite, pointed at a live `docker compose up` stack via a swapped client fixture, passes with no test code changes — confirmed by running it against a fresh `docker compose up` at least once during Phase 8 implementation.
- [ ] After a full suite run, `work_orders` contains exactly one new row beyond the seeded `WO-001`/`WO-002` — confirming the single-write invariant this task's DB-isolation reasoning depends on.

### Status

Tasks 1–4 are locked.

---

## Task 5 — Fix only defects required to satisfy the frozen contracts; do not expand the dataset or scenario suite by default

### A triage taxonomy (the central decision for this task)

Task 5 isn't itself a mechanism to build — it's a scope-control policy for what happens when the Task 1–4 suite, run for real against the real model (Task 4), actually fails something. Every failure gets sorted into exactly one of five buckets, each with a different, predetermined response:

1. **Deterministic-assertion failure (Tasks 1/2/3's negative/structural checks) → always in scope, always fixed.** Wrong tool called, wrong `status`, missing required evidence ID, `confidence == "confirmed"` where it shouldn't be, a template mismatch. The fix is either an application bug fix or a prompt-engineering change to the interpretation/synthesis node's instructions (Phase 4 Task 2's `generate()`/`generate_structured()` prompts) — never a change to the dataset or the tool contract, so it never conflicts with the freeze.
2. **Manual-review content weakness (Task 3's qualitative bucket) → prompt-only fix path, capped at judgment, not a hard iteration limit.** If `manual_review_report.md` shows genuinely weak reasoning (e.g. a GS-04 recommendation with no root-cause framing at all), the only sanctioned fix is refining prompt instructions — never a new deterministic keyword/content check, per Phase 5 Task 3's and Phase 6 Task 5's already-locked rejections of that approach. If prompt iteration doesn't reliably fix it, that's logged as a known limitation for Phase 9's README rather than blocking Phase 8 completion — dataset spec §11.3 already accepts "manually reviewable" as a legitimate end state, not just an interim one.
3. **A dataset-spec discrepancy surfaces during implementation (the GS-03/`FaultRecurrence` pattern from Tasks 2–3) → resolved by a documented decision recorded here, then implemented to match — never resolved by silently editing the dataset or the golden-scenario trajectory tables.** Task 5 formalizes what Tasks 2 and 3 already did in practice as the standard procedure for any further such gap found once real implementation starts.
4. **A defect outside the 8 scenarios' direct execution path (e.g. either of the two open Phase 7 findings) → fixed only if a golden scenario's deterministic assertion actually depends on it; otherwise stays a tracked, separate finding, not pulled into Phase 8's scope.** Neither currently-open Phase 7 finding sits on a golden scenario's required path: the route-level error-message/`asset_id` leak only fires on a forced unhandled exception, which none of GS-01–GS-08 trigger; the checkpoint-serializer warning fires on every run (all eight scenarios use the `MemorySaver` checkpointer) but is a warning, not a failure — it doesn't break any deterministic assertion today. Neither is therefore *required* by this task's own rule, independent of whether either has already been fixed elsewhere.
5. **A "wouldn't it also be good to test X" idea surfaces → explicitly out of scope, always.** No new golden scenario, asset, fault, document, or tool is added, per dataset spec §13's freeze and this task's own plan wording. The one narrow exception dataset spec §2.2 allows — "unless a blocking implementation defect proves that one scenario is impossible to test" — is read strictly: a scenario must be *structurally impossible* to execute as specified (e.g. the canonical tool contract genuinely cannot produce evidence the spec requires), not merely difficult or occasionally flaky against a real, nondeterministic model. Any such idea is captured as a Project 2 / future-v2 backlog note instead, never folded into the frozen 8-scenario suite.

### Prompt iteration is a fix, not an expansion

- Worth stating explicitly since it's easy to conflate with "changing the dataset": the freeze (§13, and this task's own wording) is about the **data and scenario surface** — assets, faults, documents, tool contract, the 8 scenarios themselves. It says nothing about the LLM-facing prompts in the interpretation/synthesis nodes, which Phase 4 always expected to be tuned. Bucket 1 and (within limits) bucket 2 above both treat prompt changes as ordinary, in-scope Phase 8 work, not as the kind of expansion this task exists to prevent.

### No new bounded-attempt mechanism for prompt tuning

- Phase 5 Task 4 already built a bounded-retry mechanism (`max_retry_attempts`), but that's a **request-time** guardrail against transient failures, not a **design-time** iteration cap on prompt engineering — a categorically different kind of "how many tries." Task 5 doesn't invent a parallel cap on how many times a developer can revise a prompt before giving up; that's ordinary development judgment, not something to encode as a testable rule.

### Test / Validation

- [ ] Every fix made during Phase 8 implementation is traceable to exactly one of the five buckets above — confirmed by each fix's commit/PR referencing which bucket applied (a lightweight discipline check, not a new mechanism).
- [ ] No commit made during Phase 8 implementation adds a 9th golden scenario, a 5th equipment asset, a 6th RAG document, or an 8th canonical tool — confirmed by diffing `scenarios.yaml`, the seed fixtures, and the tool contract module before/after Phase 8 implementation.
- [ ] Any dataset-spec discrepancy found during implementation (beyond the two already resolved in Tasks 2–3) is recorded in this document with the same rationale/rejected-alternative structure used throughout, before being implemented — not fixed silently in code alone.
- [ ] Neither open Phase 7 finding blocks Phase 8 completion regardless of fix status, confirmed by the golden suite passing its deterministic assertions with both findings either fixed or still open (i.e. Phase 8's own tests don't accidentally start depending on either fix).

### Phase 7 finding status confirmed during implementation

The current repository state confirms both Phase 7 findings remain independently tracked
and do not block Phase 8:

- `docs/gaps/Phase7-finding-error-path-envelope-gaps.md` now records both route-level
  error-envelope gaps as implemented, pointing to
  `docs/fixes/phase-4-implementation-fix.md` and
  `docs/fixes/phase-4-asset-id-error-path-fix.md` for the concrete route changes.
- `docs/fixes/phase-7-implementation-fix.md` records the checkpoint serializer finding
  as implemented, with regression coverage around the explicit
  `JsonPlusSerializer` allow-list and warning-free checkpoint round trip.

This confirmation does not change Task 5's bucket-4 rule: either finding's fixed or
unfixed status is still outside Phase 8's golden-scenario scope unless a deterministic
GS-01–GS-08 assertion depends on it.

### Status

Tasks 1–5 are locked.

---

## Task 6 — Add end-to-end regression coverage to CI where stable and cost-appropriate

### CI cadence: manual dispatch, not per-push (the central decision for this task)

- The golden-scenario suite (Task 4's real-LLM, ASGI-in-process job) runs as a **separate CI workflow, triggered by `workflow_dispatch` only** — not on every push/PR.
- The existing Phase 0–7 CI job (`uv sync → ruff check → ruff format --check → mypy → pytest`, mocked LLM, no API key) is **unchanged** and continues to run on every push/PR exactly as it does today — this task adds a second, differently-paced job, it doesn't touch the first.
- Rationale: a per-push real-LLM gate would multiply API cost/latency by commit frequency during active development, and — because the model is genuinely nondeterministic — could put a red X on an unrelated PR from ordinary model variance rather than a real regression, which erodes trust in the signal over time (the opposite of "debug-first, optimize for inspectability"). `workflow_dispatch` keeps the expensive live-model regression available on demand before anything that actually needs fresh confirmation (a release, a demo, or a change to the interpretation/synthesis prompts).

### No retry on scenario failure

- A failed scenario fails the job immediately — CI does not re-run it. A human then applies Task 5's triage taxonomy (prompt fix, genuine defect, dataset-spec discrepancy, or acceptable model variance worth a follow-up discussion) rather than CI silently absorbing the failure.
- This deliberately doesn't reuse Phase 5 Task 4's `max_retry_attempts` mechanism at this layer: that mechanism exists for transient technical failures (a dropped connection, a rate limit) where a retry has a real chance of succeeding for a reason unrelated to correctness. A golden-scenario assertion failure is a claim about whether the *agent's actual behavior* matched the frozen contract — retrying and hoping for a different roll would quietly launder exactly the kind of signal this whole phase exists to surface.

### The docker-compose full-stack variant stays opt-in manual-only

- Task 4's second suite variant (identical tests, pointed at a live `docker compose up` stack instead of the ASGI transport) is behind the `workflow_dispatch` input `run_container_variant=true`, run before a release or a demo when the container/startup path also needs validation. It exercises the container/startup path (Phase 9's "fresh-clone walkthrough" concern) more than agent behavior, so it remains opt-in even within the manual golden workflow.

### `manual_review_report.md` as a CI artifact

- Every golden-suite run uploads `tests/golden/manual_review_report.md` via `actions/upload-artifact`, so Task 3's report is downloadable from the Actions run without needing a local re-run. This is the natural connective step between Task 3 (what the report contains) and Task 6 (when it's produced) — no new content or mechanism, just making an existing output retrievable.

### No new alerting mechanism

- A failed manual run surfaces through GitHub Actions' own default behavior (a red run in the Actions tab, standard notification to whoever has Actions failure notifications enabled) — no custom Slack/email/webhook integration is added. Consistent with Phase 7's "no observability feature creep" precedent applied here to CI tooling rather than telemetry.

### Test / Validation

- [ ] The existing Phase 0–7 CI job's trigger conditions (push/PR) and content are unchanged by this task — confirmed by diffing the workflow file.
- [ ] A new workflow (or job within the existing workflow file) runs the golden-scenario suite via `workflow_dispatch`, with no `schedule`, `push`, or `pull_request` trigger.
- [ ] The golden-suite job's `ANTHROPIC_API_KEY` secret is scoped only to that job/workflow, not exposed to the per-push job.
- [ ] A forced single-scenario failure (mocked at the assertion level for this test only) results in exactly one job run with no automatic re-invocation of that scenario — confirmed by checking the job only calls the API the expected number of times.
- [ ] `manual_review_report.md` appears as a downloadable artifact on every golden-suite workflow run.
- [ ] The docker-compose-backed suite variant is opt-in through `workflow_dispatch` only, confirmed by workflow file inspection.

### Status

Tasks 1–6 are locked.

---

## Phase 8 Success Criteria

- [ ] All 8 golden scenarios pass their deterministic contracts (asset resolution, required/forbidden tools via closed-world derivation, HITL state, output schema — Task 1) when run through the real public API against the real model (Task 4).
- [ ] Every scenario's required structured-record and RAG-document evidence is present in the response body, asserted at the retrieval layer rather than the citation layer, with the previously-undefined `FaultRecurrence` ID scheme now locked (Task 2).
- [ ] The four behavioral-assertion categories from dataset spec §11.3 are each handled by the most deterministic mechanism that's actually safe to apply — negative `confidence` checks, exact template-answer checks, or (only where genuinely necessary) a captured, non-blocking manual-review report — with no LLM-as-judge and no keyword/content backstop anywhere (Task 3).
- [ ] The golden suite runs through the actual FastAPI routes (ASGI in-process, real Compose Postgres) by default, and identically against a live `docker compose up` stack for pre-release verification, satisfying "through the public API, not only internal graph calls" without requiring container-level testing on every run (Task 4).
- [ ] A five-bucket triage policy governs every fix made during Phase 8 implementation, keeping prompt iteration in scope while keeping the dataset, tool contract, and 8-scenario suite frozen exactly as designed (Task 5).
- [ ] CI gains a manual real-LLM regression job (no retry-on-failure, no push/PR trigger) alongside the unchanged per-push mocked suite — genuine regression coverage without multiplying cost or noise (Task 6).
- [ ] Two Phase 7 findings remain tracked independently of Phase 8 and don't block it either way, per Task 5's bucket-4 reasoning; the current repo state confirms both are implemented and documented.

## Status

All six Phase 8 tasks are locked. Phase 8 planning is complete. Next: proceed to implementation, or move on to Phase 9 (Portfolio & Demo Readiness) planning discussion.
