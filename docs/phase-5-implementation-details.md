# Phase 5 (Reliability, Validation & Guardrails) — Implementation Decisions

Captured from planning discussion, 2026-08-18. These are decisions made ahead of implementation, refining Implementation Plan v1.0 / Phase 5 and building on the Phase 4 (LangGraph Agent Orchestration) decisions without contradicting either. Nothing here has been implemented yet. Decisions are locked task-by-task, following the Phase 5 task list from the implementation plan:

1. Enforce structured output validation with Pydantic/JSON schema. **(locked, amended — see Task 1 amendment notes)**
2. Implement unknown-asset early stop. **(locked)**
3. Add explicit insufficient-evidence/uncertainty behavior. **(locked)**
4. Add controlled retries for transient tool/LLM failures and structured-output regeneration. **(locked)**
5. Define fallback/graceful-error responses after retry exhaustion. **(locked)**
6. Ensure evidence provenance is surfaced in the final structured result. **(locked)**
7. Add deterministic guardrail checks around consequential actions. **(locked)**

All seven Phase 5 tasks are locked. See Success Criteria and Status at the bottom.

---

## Task 1 — Enforce structured output validation with Pydantic/JSON schema

### Scope (the central decision for this task)

- Covers only the two forced-structured-output LLM calls established in Phase 4: the **interpretation node's** intent/asset extraction, and the **synthesis node's** answer/confidence/evidence-used output.
- Tool-call arguments emitted during the evidence-gathering loop (`search_maintenance_docs` query, `create_work_order_draft` fields, etc.) are explicitly **out of scope** for this task. They're left to Phase 2/3/6's existing tool input models to reject at the tool boundary; no separate re-validation layer is introduced for them here.

### Mechanism

- One shared helper, `generate_structured(client, messages, output_model, tool_name) -> T`, used by both nodes. This reuses the forced-tool-use pattern already established in Phase 4 Task 2 (one `generate()` method serves both agentic tool-calling and forced structured output) and Task 3 (LLM-facing schemas generated via Pydantic's `.model_json_schema()`, not hand-written) — no new LLM-interaction mechanism is introduced, just a validation choke point wrapped around the existing one.
- Internally: `generate_structured()` forces the named pseudo-tool via `tool_choice`, pulls the single returned tool-call's args out of `LLMResponse.tool_calls`, and runs `output_model.model_validate(args)`.

### Output models

- `IntentExtractionOutput`: `intent: Literal["troubleshooting", "maintenance_check", "history_query", "procedure_lookup", "work_order_request"]` (mirrors the graph state's 5-value taxonomy from Phase 4 Task 1 exactly), `asset_identifier: str | None`.
- `SynthesisOutput`: `answer: str`, `confidence: Literal["confirmed", "hypothesis"] | None`, `evidence_used: list[str]`.
- Both schemas generated via `.model_json_schema()`, consistent with the project's existing Pydantic-everywhere convention.

> **Amendment (during Task 3 discussion):** `confidence` was originally specified here as a bounded float (`Field(ge=0, le=1)`). Corrected to `Literal["confirmed", "hypothesis"] | None` to match Phase 0's response envelope, which already locked `confidence: Optional[Literal["confirmed", "hypothesis"]]` explicitly reserved for "Phase 5's grounding distinction." The float typing was a planning error caught before implementation; no other part of Task 1 changes.

> **Amendment (during Task 6 discussion):** `generate_structured()`'s signature gains an optional `extra_validator: Callable[[T], None] | None` parameter, called after Pydantic validation succeeds. A raised error from it is treated identically to a `pydantic.ValidationError` for retry/fallback purposes. See Task 6 for the concrete use (citation validation on `SynthesisOutput`). This keeps the validation-and-retry mechanism generic rather than hard-coding a second, parallel check path.

### Failure handling (the second central decision)

- `generate_structured()` raises a typed `StructuredOutputValidationError(code, message, node)` when `pydantic.ValidationError` occurs.
- **Caught inside the calling node, not bubbled to the route level.** The node catches it, builds an `ErrorRecord` (`code="structured_output_invalid"`, `recoverable=True`, `node=<node name>`), and returns a state update containing it — it does not re-raise.
- A conditional edge checks for this error state and routes straight to the terminal node with `status="error"`, the same pattern already used for the unknown-asset stop (GS-07 in Phase 4 Task 4). This keeps every anticipated structural failure mode flowing through the same terminal-node assembly point, consistent with the Phase 4 principle that the terminal node is "always the last node on every path."
- The Phase 4 Task 6 route-level try/except is **demoted to a pure safety net** for genuinely unanticipated exceptions (bugs, DB/connection failures) — it is no longer the primary path for this anticipated failure mode.
- Rejected alternative: pure bubble-up with no in-graph catch (route-level try/except builds the error envelope directly from the exception, `state.errors` stays unused for this failure mode). Rejected because it leaves the `errors: Annotated[list[ErrorRecord], operator.add]` field Phase 4 Task 1 built specifically "so Phase 5's bounded-retry logic can see prior attempts" unused for the very first failure mode meant to populate it.

### Forward compatibility with Task 4

- This task's in-node catch point is exactly what Task 4's bounded-retry logic will wrap: retry `generate_structured()` up to N times before giving up and routing to terminal, rather than inventing a new state-population mechanism later.

### Test / Validation

- [ ] `generate_structured()` is the only call site touching `output_model.model_validate` for these two output types.
- [ ] A forced invalid-args case (mocked LLM response with malformed tool-call args) results in `status="error"` via the terminal node, not a raw 500 or unhandled exception.
- [ ] The route-level try/except is never hit by this specific failure mode in a test — only by a separately injected "unexpected" exception (e.g. simulated DB failure).
- [ ] `ErrorRecord` for a validation failure has `recoverable=True` and identifies the originating node.
- [ ] `SynthesisOutput.confidence` accepts exactly `"confirmed"`, `"hypothesis"`, or `None` — no numeric confidence value anywhere in the schema.

---

## Task 2 — Implement unknown-asset early stop

### What's already built (this task is narrower than it sounds)

- Phase 2 already locked `resolve_asset`'s typed discriminated result (`status: Literal["resolved", "not_found"]`, never an exception, all malformed/empty/nonexistent identifiers collapsing to one `not_found` outcome).
- Phase 4 Task 4 already locked the graph edge: `asset_resolution_status == "not_found"` routes straight to the terminal node with `status="unknown_asset"`, skipping evidence-gathering and synthesis entirely — GS-07's `resolve_asset -> STOP` contract, structurally guaranteed rather than prompted for.
- Phase 0's response envelope already reserves `"unknown_asset"` as its own `status` value, distinct from `"error"`.
- Verified against all eight golden scenarios: none omit an asset identifier entirely (even GS-06's `procedure_lookup` query names PUMP-104), so `resolve_asset` running unconditionally on every intent creates no false-positive stop within the current frozen dataset scope. Noted as a known limitation, not a blocker: if the dataset later gains a genuinely assetless generic query, this would need revisiting — no action taken now, per the plan's "no implementation-time dataset expansion" rule.

### What this task actually adds (the central decision)

- GS-07 requires user-facing text ("state that the asset cannot be found and request a valid identifier"), but the synthesis node — the only node that normally produces `answer` text — never runs on this path. The terminal node itself builds a **deterministic template string**, e.g. `f"I couldn't find an asset matching '{identifier}'. Please provide a valid asset ID."` — **no LLM call anywhere on this path.** Keeps the entire unknown-asset guardrail free of LLM involvement, matching GS-07's prohibited-behavior intent (no invented telemetry, no asset-specific diagnosis).
- Rejected alternative: a short LLM call for more natural phrasing. Rejected because it adds cost/latency and an LLM dependency to what should be the fastest, cheapest, most deterministic stop in the system, for no behavioral benefit the golden scenario requires.
- The attempted identifier is read from the `resolve_asset` `ToolCallRecord.args["identifier"]` (already in `state.tool_calls` per Phase 4 Task 1) — no new state field is introduced. Keeps Phase 4's locked state schema unchanged.

### Envelope field semantics (confirmed, not new)

- `error` stays `None` on this path — per Phase 0's envelope, `error` is populated only when `status == "error"`, and `unknown_asset` is a distinct status.
- `asset_id` stays `None` on this path — per Phase 0's envelope, it reflects "the asset actually resolved," and nothing was resolved here.
- The attempted identifier appears only in the templated `answer` text (and in telemetry via `tool_calls`), not as a dedicated response field.

### Guardrail redundancy

- No additional runtime assertion is added beyond the existing structural graph edge. Consistent with Phase 4 Task 3's precedent (`submit_work_order`'s unreachability is verified by call-site inspection, not a runtime check) — since there is no code path from `not_found` to evidence-gathering/synthesis, the guardrail is already structural. Verified by test, not by adding a second enforcement mechanism.
- **Superseded in part by Task 7**: Task 7 revisits this "no redundant runtime check" precedent specifically for `submit_work_order`, and deliberately carves out an exception there given the asymmetric cost of an irreversible action vs. a wrong response. This task's own guardrail (a read-only response) is not revisited — the exception applies only to consequential, state-changing actions.

### Test / Validation

- [ ] `resolve_asset("PUMP-999")` reaches the terminal node with `status="unknown_asset"`, `error=None`, `asset_id=None`, and a non-empty templated `answer`, without any evidence-gathering or synthesis node executing (extends Phase 4's existing assertion with the envelope-field checks).
- [ ] No LLM call occurs anywhere between `resolve_asset` returning `not_found` and the terminal node returning — confirmed by a test asserting `generate()`/`generate_structured()` is not invoked on this path.
- [ ] The templated message includes the exact identifier that was attempted, sourced from `tool_calls[-1].args["identifier"]`.
- [ ] Code-path inspection confirms no edge exists from the unknown-asset branch into evidence-gathering or synthesis nodes.

---

## Task 3 — Add explicit insufficient-evidence/uncertainty behavior

### Two distinct behaviors, not one (the central decision for this task)

This task's name bundles two genuinely different mechanisms, and conflating them was the main risk in this planning discussion:

**(a) Uncertainty — hypothesis vs. confirmed.** Handles cases where real evidence exists but doesn't support a definitive diagnosis: GS-03 (PUMP-101 — healthy telemetry contradicts the user's overheating claim, no fault to confirm), GS-05 (PUMP-104 — seal leak is a strong hypothesis, root cause explicitly unconfirmed per the dataset spec's asset ground truth), and even GS-01/GS-02/GS-04 to a lesser degree (vibration/overheating is confirmed, but the specific root cause — alignment, recurrence cause — remains a hypothesis). Carried entirely by the `SynthesisOutput.confidence` field locked (corrected) in Task 1. `status` stays `"ok"` for all of these — this is a normal successful response, just an epistemically honest one.

**(b) Insufficient evidence — a distinct terminal status.** Reserved for the structurally different case where the evidence-gathering loop produced essentially *nothing* to reason from at all, as opposed to real-but-inconclusive evidence. Maps to Phase 0's separate `status="insufficient_evidence"` envelope value and bypasses synthesis entirely, following Task 2's precedent exactly.

### Insufficient-evidence trigger rule (intent-aware)

A deterministic check runs after the evidence-gathering loop exits (per Phase 4 Task 5's single-cyclic-node exit condition) and before the synthesis node:

- `procedure_lookup`: insufficient if `document_evidence` is empty.
- `troubleshooting` / `maintenance_check` / `history_query`: insufficient only if **both** `structured_evidence` and `document_evidence` are empty.
- `work_order_request`: same rule as troubleshooting-family intents, checked before `create_work_order_draft` would be considered — not otherwise specified further, since no golden scenario exercises an evidence-free work-order request; can be revisited if one is added later.

Rejected alternative: a blanket "both lists empty" rule regardless of intent. Rejected because it would incorrectly flag a pure `procedure_lookup` (which never populates `structured_evidence` at all, by design per Phase 4 Task 4's intent-filtered tool list) as insufficient even when `document_evidence` alone has good hits.

**Scope note:** none of the eight golden scenarios currently trigger this path — every golden scenario has real evidence, including GS-03's PUMP-101 (which has evidence, just evidence that doesn't confirm a diagnosis, so it correctly stays `status="ok"` + `confidence="hypothesis"`, not `insufficient_evidence`). This check is a defensive guardrail for cases outside the frozen dataset (e.g. a future asset with no recorded history, or a search query matching nothing in the corpus) — it needs to exist and be unit-tested, but doesn't change any golden-scenario expected outcome.

### Mechanism for the insufficient-evidence response

- Deterministic template message, no LLM call — identical philosophy to Task 2's unknown-asset message: e.g. `"Not enough evidence was found to answer this request. Try rephrasing or providing more detail."` Synthesis is skipped entirely, since asking an LLM to explain an absence of evidence risks inventing the exact kind of unsupported content this whole phase exists to prevent.
- `confidence` stays `None` on this path (no diagnosis was attempted). `error` stays `None` (distinct status, same reasoning as Task 2). `asset_id` is populated as normal (the asset *was* resolved — this differs from `unknown_asset`, where resolution itself failed).

### Hypothesis-vs-confirmed judgment mechanism

- **Pure LLM judgment, no deterministic backstop.** `confidence` is set entirely by the synthesis LLM call, guided by prompt instructions distinguishing confirmed facts (evidence directly supports a claim, e.g. an active fault event matching the reported symptom) from hypotheses (evidence is consistent with but doesn't prove a specific cause).
- Rejected alternative: a deterministic backstop rule (e.g. "confirmed" only allowed if `structured_evidence` contains an active fault event directly matching the query). Rejected because this is an inherently epistemic judgment that varies by scenario — GS-01 (fault code given, vibration confirmed, alignment still a hypothesis), GS-03 (fault claimed by user, contradicted by evidence, nothing confirmed), GS-04 (fault confirmed, recurrence pattern raises the stakes but doesn't change the confirmed/hypothesis mechanics), GS-05 (fault confirmed, competing hypotheses for cause) all shade differently enough that a hard-coded rule risks being wrong for a legitimate case Phase 8 hasn't enumerated yet. This is one of the few places in the project where "prefer deterministic over LLM-delegated" is deliberately not applied, because the judgment itself is genuinely about epistemic confidence in a diagnosis, not a checkable business rule.

### Test / Validation

- [ ] A mocked evidence-gathering loop that returns zero tool results for a `troubleshooting` intent reaches the terminal node with `status="insufficient_evidence"`, `confidence=None`, `error=None`, `asset_id` populated, and a non-empty templated `answer`, without the synthesis node executing.
- [ ] A mocked `procedure_lookup` with empty `document_evidence` (even if `structured_evidence` is untouched, as expected) triggers `insufficient_evidence`.
- [ ] A mocked `procedure_lookup` with non-empty `document_evidence` does **not** trigger `insufficient_evidence`, confirming `structured_evidence` being empty is not itself a trigger for this intent.
- [ ] GS-03 (PUMP-101) reaches `status="ok"` with `confidence="hypothesis"` — not `insufficient_evidence` — confirming real-but-inconclusive evidence is routed differently from empty evidence.
- [ ] GS-04 or GS-05 (PUMP-103/PUMP-104) produce `confidence` values consistent with the dataset spec's asset ground truth (confirmed fault, unconfirmed root cause) — manually reviewable per the plan's own "nuanced behavioral assertions... manually reviewable" allowance (Phase 8), not necessarily a hard automated assertion on wording.

---

## Task 4 — Add controlled retries for transient tool/LLM failures and structured-output regeneration

### Three failure categories, one uniform mechanism (the central decision for this task)

Three genuinely different failure sources feed into this task: LLM call failures (Anthropic API errors — rate limits, timeouts, 5xx), tool execution failures (the DB/infra exceptions Phase 2 explicitly deferred to Phase 5), and structured-output regeneration (Task 1's validation-failure catch point).

- **No exception-type classification.** Every LLM call, tool invocation, and structured-output regeneration attempt gets the same fixed retry budget regardless of what exception was raised — no allowlist of "transient" exception types to build or maintain against the Anthropic SDK's evolving error hierarchy or arbitrary DB/ORM exceptions. Even a genuinely permanent failure (a real bug, a bad API key) just costs a couple of extra failed attempts before falling through to Task 5's fallback — no worse than failing immediately except added latency.
- Rejected alternative: classify Anthropic SDK errors specifically (retry rate-limit/timeout/5xx, fail fast on 4xx) while leaving tool exceptions blanket-retried. Rejected for consistency and to avoid the plan's explicitly named scope trap: "no generalized policy engine." A precise classifier for one failure category but not the other would also be an inconsistent mental model to maintain.

### Retry budget and backoff

- Shared constant: `max_retry_attempts: int = 3` (i.e. up to 2 retries after the first attempt), added to `Settings` alongside the existing `anthropic_api_key` pattern — one number reused across all three call sites, not tuned per-category. Exact value is a default, adjustable at implementation time like other deferred numeric parameters (embedding model, evidence-loop iteration cap).
- **Fixed delay between attempts, no exponential backoff or jitter** — e.g. `retry_delay_seconds: float = 0.5`. Rejected exponential/jittered backoff as unnecessary sophistication for a debug-scale project where deterministic, fast tests matter more than production-grade backoff tuning.
- The delay function is injectable (`sleep: Callable[[float], Awaitable[None]] = asyncio.sleep`), overridden to a no-op in tests — same "own the seam, mock the whole thing" pattern already used for the LLM client (Phase 4 Task 2) and embeddings (Phase 3).

### Mechanism placement — function-level, not graph-level

- Retries happen entirely **inside plain Python helper functions**, never by LangGraph re-entering a node. A node either succeeds (after up to `max_retry_attempts` internal tries) or fails once, with a final state to report — it is never externally re-invoked as a retry unit. Keeps retry logic orthogonal to checkpointing (relevant once Phase 6's interrupt/resume machinery exists) and to Phase 4 Task 5's evidence-loop iteration cap, which is a different, unrelated bound (how many distinct tool-calling turns the LLM takes, not how many times one failed call is retried).
- A generic helper, `with_retry(fn, max_attempts, sleep) -> RetryResult[T]`, wraps: (1) tool invocations at the Phase 4 Task 3 binding/adapter layer (including `resolve_asset` itself, for transient DB blips), and (2) plain `generate()` calls (interpretation node's fallback path if any, evidence-gathering loop, any non-structured call).
- `generate_structured()` gets its **own** internal retry loop rather than being wrapped by the generic helper, because its retry needs to inject a corrective message between attempts (see below) — a capability the generic blind-retry helper doesn't need for the other two categories.

### Structured-output regeneration is corrective, not blind

- On a `pydantic.ValidationError`, `generate_structured()` appends a message describing exactly what was wrong (e.g. "your last response was missing required field `confidence`") to the conversation before re-invoking the forced tool-choice call, up to `max_retry_attempts` total tries. Chosen over blind identical-prompt retry because it gives the LLM a concrete, actionable reason a different attempt might succeed — "regeneration" implies correction, not just hoping for different sampling luck.

### Attempt visibility in state (resolves an open question from Task 1)

- Both `with_retry` and `generate_structured()` track a failed-attempt list internally and expose it alongside their result: on eventual success, they return `(value, attempts: list[ErrorRecord])` where `attempts` holds one `ErrorRecord` (`recoverable=True`) per failed try that preceded the success; on final exhaustion, the raised exception carries `.attempts: list[ErrorRecord]` covering all failed tries including the last.
- **The calling node is responsible for merging `attempts` into its own returned state update's `errors` field — on success or failure alike.** This means `state.errors` genuinely reflects retry history (not just final failures), fulfilling the purpose Phase 4 Task 1 built the append-only `errors` field for ("so Phase 5's bounded-retry logic can see prior attempts, not just the latest one") rather than leaving it unused whenever a retry eventually succeeds.

### What happens after exhaustion (deferred, on purpose)

- After `max_retry_attempts` tries are exhausted, the underlying exception (or `StructuredOutputValidationError`) is raised/re-raised as before Task 4 existed — Task 1's in-node catch-and-route-to-terminal logic for structured-output failures is unchanged, just now sitting after the internal retry loop instead of on the first failure. For tool/LLM call failures (the two categories with no prior catch point), what the calling node does with the final exception — graceful fallback content, `status` value, message — is explicitly **Task 5's job**, not decided here.

### Test / Validation

- [ ] A mocked tool call that fails twice then succeeds on the third attempt returns a successful result with exactly two `ErrorRecord`s (`recoverable=True`) merged into `state.errors`, confirmed by inspecting the node's returned state update.
- [ ] A mocked tool call that fails on all `max_retry_attempts` tries raises after the last attempt, not before — confirmed by counting call invocations.
- [ ] `generate_structured()`'s retry sends a follow-up message referencing the specific validation error, confirmed by inspecting the message list passed to the second attempt.
- [ ] No real `asyncio.sleep` delay occurs in the test suite — confirmed by injecting a no-op `sleep` and asserting test runtime stays fast.
- [ ] `max_retry_attempts` and `retry_delay_seconds` are read from `Settings`, not hardcoded at each of the three call sites.

---

## Task 5 — Define fallback/graceful-error responses after retry exhaustion

### Failure posture (the central decision for this task)

- **Hard abort, no graceful degradation.** Any unrecoverable tool/LLM failure — anywhere in the pipeline, after Task 4's retries are exhausted — aborts the entire request to `status="error"`. No partial synthesis is attempted from whatever `structured_evidence`/`document_evidence` had already accumulated before the failure, and the evidence-gathering loop never asks the LLM to route around a failed tool by trying a different one. This applies uniformly, including to `create_work_order_draft` failures — no draft persists, consistent with the consequential-action safety posture Task 7 formalizes.
- Rejected alternative: graceful degradation (synthesize from partial evidence with a caveat, or let the LLM retry with a different tool after one fails). Rejected for two reasons: it adds a second, LLM-mediated recovery path on top of the deterministic one Task 4 already built, undermining "retries/fallbacks are deterministic enough to test"; and it risks producing a less-grounded answer from an incomplete evidence set, in tension with the "no definitive diagnosis when evidence does not support one" constraint carried over from Task 3.

### Mechanism — same catch-and-route pattern as Tasks 1-3

- The exhausted exception (from `with_retry` or from a plain `generate()` call) is caught **inside the calling node** — the same node that would have caught a `StructuredOutputValidationError` in Task 1's pattern — never bubbled to the Phase 4 Task 6 route-level try/except, which remains reserved purely for exceptions from outside any anticipated failure category (a bug in graph wiring itself, something no node was written to expect).
- The node builds a final `ErrorRecord` from the exhausted exception, merges it (plus any `attempts` from Task 4) into its state update, and a conditional edge routes to the terminal node with `status="error"` — extending the same terminal-assembly convergence point used for `unknown_asset` (Task 2) and `insufficient_evidence` (Task 3).

### Error codes and message content

- Three distinct `error.code` values, extending Task 1's `structured_output_invalid`: **`tool_execution_failed`** (any tool invocation exhausted its retries) and **`llm_call_failed`** (a plain, non-structured `generate()` call exhausted its retries). Distinguishing by category costs nothing extra (the calling context already knows which kind of call failed) and is useful for Phase 7 telemetry and debugging without inventing a fourth, generic catch-all code.
- **`error.message` in the API response is a fixed, category-specific templated string** — e.g. `"A tool call failed after multiple attempts. Please try again shortly."` / `"The AI service is temporarily unavailable. Please try again shortly."` — never the raw exception text. No LLM call generates this message either, same no-LLM-on-a-failure-path philosophy as Tasks 2 and 3.
- The actual exception detail is preserved in `ErrorRecord.message` inside graph state (available to Phase 7's telemetry and to server-side logs) but is never sent to the API caller. This was discussed explicitly: sanitizing the boundary costs nothing during local development, since the real detail is one log/telemetry read away rather than embedded in the HTTP response — and it's the right habit if this project is ever pointed at something less disposable than a debug dataset.
- Standard exception logging (full traceback, server-side) on every failure is assumed as baseline hygiene, independent of this decision — not something this task needed to separately decide.

### Envelope field semantics on this path

- `confidence` stays `None` (no diagnosis was attempted or completed). `asset_id` reflects whatever was resolved before the failure (populated if `resolve_asset` had already succeeded; `None` if the failure occurred at or before asset resolution — e.g. a transient DB blip during `resolve_asset` itself, which is a different failure from the deterministic `not_found` outcome Task 2 covers).

### Test / Validation

- [ ] A mocked tool call that exhausts all retries during evidence-gathering (with some evidence already accumulated from an earlier successful tool call) reaches `status="error"`, `error.code="tool_execution_failed"`, and a sanitized templated `error.message` — confirming the accumulated partial evidence is discarded, not synthesized from.
- [ ] A mocked `generate()` failure (non-structured, e.g. in the evidence-gathering loop) that exhausts retries reaches `status="error"` with `error.code="llm_call_failed"`.
- [ ] `error.message` in the response never contains the raw exception string in either case — confirmed by asserting the response body doesn't include a marker string injected into the mocked exception.
- [ ] `ErrorRecord.message` in the (test-inspectable) graph state does contain the real exception detail, confirming nothing is lost, just relocated.
- [ ] A transient `resolve_asset` DB failure that exhausts retries produces `status="error"` (`asset_id=None`), distinct from the deterministic `status="unknown_asset"` path — confirming the two failure modes (infrastructure failure vs. legitimate not-found) are never conflated.
- [ ] The route-level try/except (Phase 4 Task 6) is not hit by any of the above — confirmed the same way as Task 1's equivalent check.

---

## Task 6 — Ensure evidence provenance is surfaced in the final structured result

### Filling a gap Phase 4 left loose (the setup for this task)

- Phase 4 named `StructuredEvidenceItem` as a type ("a classified reading, an active fault, a recurrence entry") but never fully specified its fields — it was unclear whether each item carries a back-reference to the actual underlying database record or just the classified value.
- Resolved by checking the dataset spec's actual schema: every structured entity already has its own natural-key ID as its primary key (`fault_events.event_id` e.g. `FE-001`, `observations.observation_id` e.g. `OBS-001`, `maintenance_events.maintenance_id`, `work_orders.work_order_id`, `telemetry_snapshots.snapshot_id`, `operating_limits`' own ID) — matching the Phase 1 decision to use natural business keys as primary keys throughout, with no surrogate IDs anywhere.
- **`StructuredEvidenceItem` is amended to require `source_type: Literal["fault_event", "maintenance_event", "observation", "work_order", "telemetry_snapshot", "operating_limit"]` and `source_id: str`** (the exact natural-key ID), alongside whatever classified-value fields it already carries (tier, metric, value, etc.). No new ID scheme is invented — the dataset's own IDs are reused directly. `DocSearchHit` (Phase 3) already carries `document_id` (`DOC-01`..`DOC-05`) and needs no change.

### Citation validation (the central decision for this task)

- **Deterministic existence check, extending Task 1's validation mechanism rather than inventing a new one.** After `generate_structured()`'s Pydantic validation of `SynthesisOutput` succeeds, the synthesis node passes an `extra_validator` (the Task 1 amendment above) that checks every string in `evidence_used` against the set of `source_id`/`document_id` values actually present in `state.structured_evidence`/`state.document_evidence` at that point.
- An unknown/fabricated citation raises the same `StructuredOutputValidationError` (`code="structured_output_invalid"` — no fourth error code invented), which flows through the exact same machinery already built: Task 4's corrective retry (the follow-up message names the invalid citation(s) and lists the valid IDs), and Task 5's hard-abort-to-`status="error"` fallback if it's still wrong after retries are exhausted.
- Rejected alternative: trust the LLM's citations without checking. Rejected because citation existence is a mechanically checkable fact (does this ID appear in the accumulated evidence, yes or no) — unlike Task 3's hypothesis/confirmed judgment, which was deliberately left to LLM discretion because *that* judgment isn't rule-checkable. Leaving citations unchecked would be the one place in this phase that skips a guardrail the plan's own "guardrails that can be deterministic should not be delegated solely to the LLM" principle says should exist.

### Non-empty citation requirement

- **`evidence_used` must contain at least one entry whenever synthesis runs.** This is enforced by the same `extra_validator`. It's safe to require unconditionally because Task 3's insufficient-evidence gate already guarantees that by the time synthesis executes, at least one of `structured_evidence`/`document_evidence` is non-empty — so "cite nothing" would only ever mean "evidence existed but the LLM didn't ground its answer in any of it," exactly the silent grounding failure this task exists to catch.
- Rejected alternative: allow empty citations on the theory that evidence might exist but not be load-bearing for the final answer. Rejected because it would leave no mechanism at all to catch an answer that used evidence to reason internally but cited none of it — defeating the task's purpose.

### Scope boundary (what this task does *not* do)

- This task validates that citations reference **real, existing evidence IDs** — it does not verify that every factual claim in the free-text `answer` is actually supported by the cited evidence (that would be fact-checking prose against sources, effectively an LLM-as-judge problem). The plan explicitly rules out LLM-as-judge for Project 1 (Phase 8's "No LLM-as-judge in Project 1"), so sentence-level grounding verification is out of scope here — Task 6 closes the structural gap (fabricated/missing citations), not the harder semantic one.

### Test / Validation

- [ ] `StructuredEvidenceItem` instances produced by the evidence-gathering loop always have a non-empty `source_id` matching an ID that actually exists in the seed fixtures (e.g. an `FE-xxx` value that is a real `fault_events.event_id`).
- [ ] A mocked `SynthesisOutput` citing a nonexistent ID (e.g. `"FE-999"`) triggers the corrective retry with a message naming the invalid citation, confirmed by inspecting the follow-up message sent to the LLM.
- [ ] A mocked `SynthesisOutput` with `evidence_used: []` when `structured_evidence`/`document_evidence` are non-empty triggers the same corrective retry, confirmed the response never reaches `status="ok"` with empty citations.
- [ ] A `SynthesisOutput` citing only real, existing IDs passes validation on the first attempt with no retry triggered.
- [ ] The final API response's `structured_evidence`/`document_evidence` lists contain `source_id`/`document_id` values for every item — confirmed no item lacks a provenance identifier.
- [ ] GS-01 through GS-06's expected evidence (per the dataset spec's §10 "Required evidence" column, e.g. GS-01's F101/vibration/DOC-03/prior coupling realignment) can be cross-referenced by ID between `evidence_used` and `structured_evidence`/`document_evidence` in the final response — supporting Phase 8's "required structured records are present" assertion directly from the response body, not just internal state.

---

## Task 7 — Add deterministic guardrail checks around consequential actions

### Only one truly consequential action (scope framing)

- `submit_work_order` is the only tool in the v1 canonical contract that performs an irreversible, state-changing action. `create_work_order_draft` is explicitly non-consequential per the dataset spec's own wording ("creates a non-consequential structured work-order draft from validated evidence").
- PP-002's approval requirement is not something the agent discovers by reading policy text at runtime and choosing to comply with — it's already baked into Phase 4 Task 3's structural design (`submit_work_order` never appears in any LLM tool-choice list, reachable only via a deterministic post-approval node). `get_plant_policy`'s role regarding PP-002 is purely evidentiary — surfacing the policy as citable evidence (per Task 6's provenance rules) — never the enforcement mechanism itself.
- PP-001 (recurring-fault escalation) doesn't need a new guardrail here either: the "≥3 occurrences in 12 months" recurrence count is already deterministic Phase 2 tool logic (per the Phase 1 Task 1 decision to implement it as a `fault_events` query, not parsed from policy text), and escalation itself is a behavioral/synthesis outcome, not a tool-access gate. So this task has exactly one real subject: `submit_work_order`.

### Consequential marker + automated enforcement (the first central decision)

- Each tool binding (Phase 4 Task 3) gains a `consequential: bool` field, defaulting to `False`. Only `submit_work_order`'s binding sets `consequential=True`.
- The binding-registration function that assembles the LLM-offered tools list for any `generate()`/`generate_structured()` call is written to only ever pull from bindings where `consequential is False` — by construction (a filter over the registry), not by a developer remembering to manually exclude `submit_work_order` by name at each call site.
- A test asserts: (a) exactly one binding has `consequential=True` and it is `submit_work_order`; (b) the LLM-offered tool list construction path structurally excludes any `consequential=True` binding, confirmed by attempting to register one and asserting it's filtered out — not just inspecting today's call sites (which is what Phase 4 Task 3's existing test does).
- This upgrades Phase 4 Task 3's guarantee from "verified once, by inspecting call sites" to "structurally can't be misconfigured by a future refactor or an eighth tool added later." Consistent with the plan's "no generalized policy engine" constraint: this is one boolean field and one filter, not an authorization framework — deliberately the smallest mechanism that closes the gap.

### Defense-in-depth runtime guard (the second central decision — an explicit exception to Task 2's precedent)

- `submit_work_order`'s own implementation checks `approval_status == "approved"` at the top of the function and raises a typed `ConsequentialActionGuardError` if the condition doesn't hold — even though, today, the only code path that calls it already guarantees this.
- **Explicitly framed as an intentional exception to Task 2's "a structural guarantee is sufficient, no redundant runtime assertion" precedent.** The distinguishing factor: Task 2's guardrail (the unknown-asset stop) only ever prevents a wrong *response* — a bug there is correctable by fixing and re-sending. A bug that defeated `submit_work_order`'s structural gate would cause an irreversible *persisted side effect* (a real work order written to the DB) that can't be un-sent. That asymmetry in blast radius justifies paying for a second, cheap check specifically here, not universally — this phase does not walk back Task 2's general philosophy for read-only guardrails.
- The guard lives inside `submit_work_order` itself, not just at its one current call site, so it remains protective even if Phase 6 or a later phase adds a second call path that forgets the invariant.
- Interaction with Tasks 4/5: if this guard trips, it is treated as a genuinely unanticipated failure (a real bug, not a business outcome any node was designed to expect) — **not** retried (Task 4) and **not** given one of the graceful `status="error"` envelopes from Task 5. It is exactly the kind of exception the Phase 4 Task 6 route-level try/except exists to catch, since by definition something upstream broke in a way none of this phase's other guardrails anticipated. This decision is recorded now; it will actually be exercised once Phase 6 implements `submit_work_order`.

### Test / Validation

- [ ] Exactly one tool binding across the full canonical set has `consequential=True`, and it is `submit_work_order` — confirmed by iterating the binding registry.
- [ ] A test that attempts to construct an LLM-offered tools list including a `consequential=True` binding (simulating a future misconfiguration) confirms the binding is filtered out, not just absent by convention.
- [ ] Calling `submit_work_order` directly with `approval_status != "approved"` (bypassing the graph entirely, as a unit test would) raises `ConsequentialActionGuardError` rather than persisting a work order.
- [ ] The `ConsequentialActionGuardError` path, when forced in an integration test, is caught only by the Phase 4 Task 6 route-level try/except — not by Task 4's retry wrapper or Task 5's `tool_execution_failed` fallback.
- [ ] `create_work_order_draft` never triggers this guard (it has no `approval_status` precondition) — confirmed the guard is scoped to `submit_work_order` alone, consistent with the draft being explicitly non-consequential.

---

## Success Criteria

- [ ] `SynthesisOutput.confidence` is a `Literal["confirmed", "hypothesis"] | None` field populated by pure LLM judgment; `evidence_used` cites only real, existing evidence IDs and is never empty when synthesis runs — both enforced through one generic `generate_structured()` validation-and-retry mechanism (Tasks 1, 3, 6).
- [ ] `resolve_asset("PUMP-999")`-style unknown-asset requests and evidence-free requests both terminate safely with dedicated, distinct `status` values (`unknown_asset`, `insufficient_evidence`) and deterministic, LLM-free templated messages — never `status="error"` and never a fabricated diagnosis (Tasks 2, 3).
- [ ] Every LLM call, tool invocation, and structured-output regeneration attempt shares one uniform, bounded, deterministically-testable retry mechanism with no exception-type classification; exhaustion always hard-aborts to a sanitized `status="error"` envelope with category-specific `error.code`, never a partial/degraded synthesis (Tasks 4, 5).
- [ ] Every item in `structured_evidence`/`document_evidence` carries a real provenance ID (`source_id`/`document_id`) traceable to the frozen dataset or RAG corpus, and the final response's `evidence_used` is cross-referenceable against those IDs — supporting Phase 8's evidence assertions directly from the API response body (Task 6).
- [ ] `submit_work_order` — the only consequential action in the v1 tool contract — is protected by two independent layers: a structural/registry-enforced exclusion from every LLM tool-choice list, and a runtime precondition check inside the function itself. This is the one deliberate exception to this phase's general "structural guarantee is enough" philosophy (Task 7).
- [ ] All Phase 0–4 happy paths and the seven non-HITL golden scenarios remain runnable through the real `/agent/query` API, unchanged by any guardrail added in this phase — no phase-5 mechanism alters a golden scenario's expected outcome, only adds safety nets around cases outside them.

## Status

All seven Phase 5 tasks are locked (Task 1 amended twice, during Task 3 and Task 6 discussions; Task 2's guardrail-redundancy stance partially superseded by Task 7 for consequential actions specifically). Phase 5 planning is complete. Next: proceed to implementation, or move on to Phase 6 (HITL & Work-Order Workflow) planning discussion.