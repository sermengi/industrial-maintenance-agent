# Phase 4 (LangGraph Agent Orchestration) — Implementation Decisions

Captured from planning discussion, 2026-08-18. These are decisions made ahead of implementation, refining Implementation Plan v1.0 / Phase 4 and the canonical tool contract (Project Design & Architecture Overview v0.3, Dataset Design Specification v1.1 §10 Golden Scenarios) without contradicting either. Nothing here has been implemented yet. Decisions are locked task-by-task, following the Phase 4 task list from the implementation plan:

1. Define a typed graph state including request, resolved asset, intent/task context, accumulated evidence, tool results, draft action state, errors, and final response. **(locked)**
2. Implement the thin LLM client abstraction and configure one initial provider/model. **(locked)**
3. Define tool bindings using the canonical seven-tool names. **(locked)**
4. Build graph nodes/edges for request interpretation, asset resolution, evidence gathering, synthesis, and terminal response. **(locked)**
5. Support conditional routing so not every request follows an identical hard-coded pipeline. **(locked)**
6. Return a validated structured response through the existing `/agent/query` endpoint. **(locked)**

---

## Task 1 — Typed graph state

### State representation mechanism (the central decision for this task)

- **`TypedDict`-based LangGraph state**, not a Pydantic `BaseModel` as the state container. This is the first LangGraph-specific decision in the project (nothing in Phases 0-3 needed to choose between the two), and `TypedDict` was chosen because it's the more battle-tested pattern for LangGraph's reducer (`Annotated[list[X], operator.add]`) mechanism and for the checkpointer/interrupt machinery Phase 6 will build on.
- Pydantic is still used throughout — as the type of individual state fields and list entries (e.g. `AssetRecord`, `ToolCallRecord`, `DocSearchHit`) — it just doesn't wrap the top-level state container itself.

### Field list

**Request**
- `query: str`
- `asset_id_hint: str | None`
- `fault_code_hint: str | None`

**Intent / task context**
- `intent: Literal["troubleshooting", "maintenance_check", "history_query", "procedure_lookup", "work_order_request"] | None` — full 5-value taxonomy from the Project Design doc's Capability Model (§4), even though only 3 of the 5 (`troubleshooting`, `procedure_lookup`, `work_order_request`) are directly exercised by the eight golden scenarios today. Decision: keep all 5 now; expand the golden-scenario dataset later if `maintenance_check`/`history_query` coverage is wanted, rather than trimming the taxonomy to only what's currently tested.

**Resolved asset**
- `asset: AssetRecord | None`
- `asset_resolution_status: Literal["resolved", "not_found"] | None` — mirrors Phase 2's `ResolveAssetResult` exactly rather than reinventing it. `None` until `resolve_asset` has actually run.

**Tool results** (raw, ordered, append-only trace)
- `tool_calls: Annotated[list[ToolCallRecord], operator.add]`
- `ToolCallRecord`: `tool_name: str`, `args: dict`, `result: <the tool's own Result model>`, `timestamp`, `sequence: int`. Shared source of truth for Phase 8's tool-call-order/containment assertions and Phase 7's telemetry event — both read from this same field rather than re-deriving order elsewhere.

**Accumulated evidence** (synthesis-facing, split by source per the architecture doc's "keep evidence conceptually distinct" principle)
- `structured_evidence: Annotated[list[StructuredEvidenceItem], operator.add]` — extracted items from `get_asset_status`/`get_maintenance_history` results (a classified reading, an active fault, a recurrence entry), not the full raw tool payload again.
- `document_evidence: Annotated[list[DocSearchHit], operator.add]` — accumulates across possibly multiple `search_maintenance_docs` calls (e.g. GS-05 needs DOC-01 + DOC-02/DOC-05), reusing Phase 3's `DocSearchHit` shape as-is.
- Confirmed distinct from `tool_calls`: `tool_calls` is the full raw trace (telemetry/order-assertion layer); `structured_evidence`/`document_evidence` are the narrower synthesis-facing extraction, split the same way the Phase 0 response envelope already splits `structured_evidence`/`document_evidence`.

**Draft action state**
- `work_order_draft: WorkOrderDraft | None`
- `approval_status: Literal["none", "pending_approval", "approved", "rejected", "submitted"]`, defaulting to `"none"`. Reserved now even though Phase 6 implements the tools themselves, so GS-08's pause-at-checkpoint has somewhere to live from the start.

**Errors**
- `errors: Annotated[list[ErrorRecord], operator.add]` — `ErrorRecord`: `code: str`, `message: str`, `node: str | None`, `recoverable: bool`. Append-only so Phase 5's bounded-retry logic can see prior attempts, not just the latest one. The terminal node collapses this list into the single `error: {code, message}` the Phase 0 envelope expects.

**Final response**
- `response: AgentQueryResponse | None` — the actual Phase 0 envelope type, populated by the terminal node and returned as-is. No parallel shape invented, per the cross-phase rule that later phases fill in fields rather than reshaping the envelope.

### Reducer summary

- `operator.add` reducers: `tool_calls`, `structured_evidence`, `document_evidence`, `errors` (all append-only, multi-write fields).
- Plain last-write-wins: `intent`, `asset`, `asset_resolution_status`, `work_order_draft`, `approval_status`, `response` — exactly one node is responsible for setting each.

### Test / Validation

- [ ] State schema is a `TypedDict`; no top-level Pydantic `BaseModel` wraps the whole state.
- [ ] `intent` accepts all 5 taxonomy values without validation error, even though only 3 are exercised by current golden scenarios.
- [ ] `asset_resolution_status` mirrors `ResolveAssetResult.status` exactly (`"resolved"`/`"not_found"`).
- [ ] `tool_calls`, `structured_evidence`, `document_evidence`, `errors` use `Annotated[list[X], operator.add]` reducers; no other field does.
- [ ] `response` field type is exactly `AgentQueryResponse` from Phase 0's schema module — not a redefined/parallel type.

---

## Task 2 — Thin LLM client abstraction and provider/model configuration

### Provider (not a real fork — follows Phase 3's ecosystem reasoning)

- **Anthropic (Claude)**, continuing Phase 3's Voyage AI decision rationale ("staying in one ecosystem for both the future LLM provider and the embedding provider keeps key/provider management simpler"). Exact model string deferred to implementation time, same treatment as Voyage's exact model — default candidate tier: Sonnet (better tool-use reliability for a multi-step agentic trajectory than Haiku; Opus cost/latency not justified for a debug-scale portfolio project). Any current Claude model supports tool use / forced tool-choice through the Messages API, so this deferral carries no real risk.

### Implementation mechanism (the central decision for this task)

- **Hand-rolled thin wrapper over the raw `anthropic` SDK (`AsyncAnthropic`)**, not `langchain-anthropic`. One core method — `generate(messages, tools=None, tool_choice=None) -> LLMResponse` — normalizing text content and/or tool-use requests into one typed response shape (`LLMResponse.tool_calls: list[ToolCallRequest]`, `LLMResponse.text: str | None`). LangGraph nodes call this wrapper directly as a plain function; no LangChain chat-model dependency.
- Rationale: matches the plan's own "thin provider-agnostic interface" principle, stated independently of LangGraph, and follows the same own-the-seam pattern already used for tools (Phase 2, plain async functions) and embeddings (Phase 3's bare `embed()` interface). Keeps CI mocking identical in shape to Phase 3's embedding mock (swap the whole client for a deterministic stub).
- Rejected alternative: `langchain-anthropic`'s `ChatAnthropic` with `.bind_tools()`/`.with_structured_output()` — less code to write and closer to typical LangGraph examples, but couples core agent logic to a framework abstraction and complicates CI mocking relative to a project-owned seam.
- **Provider-swap implication (explicitly discussed)**: the "no tool_use blocks in the response = stop calling tools" signal the evidence-gathering loop (Task 4/5) relies on is read off `LLMResponse.tool_calls` (empty list = stop), never off Anthropic-specific raw response fields (`stop_reason`, content-block types). A future provider swap only requires rewriting `generate()`'s internal translation logic, not any graph/node code, since the loop is written against the project's own `LLMResponse` shape. This pattern (tool calls present vs. absent signaling continue-vs-stop) is also not Anthropic-specific — it's the same shape OpenAI/Gemini-style tool-calling APIs use.

### CI strategy

- **Mocked/stubbed LLM client in CI** — identical shape to Phase 3's `embed()` mock decision. Real Anthropic API used only locally/manually.

### Config

- `anthropic_api_key` added to the existing Phase 0 `Settings` class, same `.env`/`.env.example` pattern already established.

### Interface scope

- One `generate()` method serves both agentic tool-calling (evidence-gathering loop) and forced structured output (synthesis node's final answer) — Claude's Messages API models both through the same tool-use mechanism, so no second "structured extraction" method is needed.

### Test / Validation

- [ ] No code path outside `generate()`'s implementation calls the `anthropic` SDK directly.
- [ ] CI runs against the mocked LLM client with zero external network calls and no API key present.
- [ ] `LLMResponse` is the only shape graph nodes read from — no node inspects raw Anthropic response objects.
- [ ] Swapping the underlying provider (hypothetically) requires changes only inside `generate()`, confirmed by code review at implementation time (no `LLMResponse`-consuming code references Anthropic-specific fields).

---

## Task 3 — Tool bindings for the canonical seven-tool names

### Core mechanism (the central decision for this task)

- Each of the seven canonical tools gets a **dedicated LLM-facing input model**, separate from its real Python signature (which may require graph-state context — `AssetRecord`, `AsyncSession` — that the LLM cannot and should not supply directly). A binding/adapter function maps the LLM's supplied args (if any) plus graph-state context onto the actual Phase 2/3/6 tool function call.
- LLM-facing schemas:
  - `resolve_asset`: `identifier: str`
  - `get_asset_status`, `get_maintenance_history`: no arguments — model can only decide to call, not supply data; `asset` injected from state.
  - `search_maintenance_docs`: `query: str`
  - `get_plant_policy`: `policy_type: str`
  - `create_work_order_draft`: draft-relevant fields (finalized in Phase 6); `asset`/`session` injected from state.
  - `submit_work_order`: not LLM-facing at all (see below).
- `session: AsyncSession` is injected from the request-scoped session for every tool; never an LLM-supplied argument.

### Which tools are ever offered to the LLM (the second central decision)

- **`resolve_asset` and `submit_work_order` are never placed in the LLM's tool-choice list.** Both are invoked only by deterministic graph nodes:
  - `resolve_asset` runs unconditionally as the graph's first node, before any LLM tool-choice exists — makes GS-07's `resolve_asset -> STOP` guardrail a hard graph edge rather than a hope that the model calls it first.
  - `submit_work_order` is reachable only from the Phase 6 post-approval resume path, invoked directly by a deterministic node. There is no code path from LLM output to this function, before or after approval — a structural guardrail rather than a behavioral one (prompt instructions alone were rejected as insufficient), consistent with the architecture doc's "the model may recommend and draft, but deterministic application logic controls execution authority."
- **LLM-offered set**: `get_asset_status`, `get_maintenance_history`, `search_maintenance_docs`, `get_plant_policy`, `create_work_order_draft` — further filtered per-request by `intent` (see Task 4/5).
- **HITL activation mechanism (clarified during planning)**: creating a draft (turn 1, `/agent/query`) and submitting it (turn 2, a separate approval endpoint resuming the paused graph run) are two distinct HTTP interactions, not one conversation continuing. Turn 1 ends with the graph paused at the HITL checkpoint and `status="needs_approval"` returned immediately (not blocking). Turn 2 is an explicit out-of-band approval call that resumes the specific suspended run from its checkpoint and routes straight to a deterministic node that calls `submit_work_order` if approved (or terminates with nothing submitted if rejected) — no LLM reasoning sits between "human approved" and "work order submitted."

### Schema generation

- LLM-facing input models' JSON schema is derived via Pydantic's `.model_json_schema()`, not hand-written — consistent with the project's Pydantic-everywhere convention; not considered a framework-coupling concession the way a LangChain chat-model wrapper would be.

### Naming discipline

- The `name` field the LLM sees for each bound tool is exactly the canonical name (`get_asset_status`, `search_maintenance_docs`, etc.) — extends the existing "no parallel aliases" tool-contract rule to the LLM-facing surface.

### Test / Validation

- [ ] `resolve_asset` and `submit_work_order` never appear in any `tools` list passed to an LLM `generate()` call, at any point in the graph, confirmed by inspecting every call site.
- [ ] Every LLM-offered tool's schema is generated from its dedicated LLM-facing input model, not its internal Python signature.
- [ ] `get_asset_status`/`get_maintenance_history`'s LLM-facing schema has zero fields.
- [ ] A test simulating an approved work order confirms `submit_work_order` is only reachable via the resume-path node, never via a tool-call content block in any LLM response.

---

## Task 4 — Graph nodes/edges

### Node set

- **Request interpretation**: one LLM call (structured output) sets `intent` (always) and extracts a candidate asset identifier from free text — skipped when `asset_id_hint` is present on the request (the hint is used directly), consistent with Phase 0's framing of the hint as a convenience for testing paths other than NLU extraction.
- **Asset resolution**: deterministic `resolve_asset` call (never LLM-tool-choice-driven, per Task 3). Followed by a hard conditional edge: `asset_resolution_status == "not_found"` routes straight to the terminal node with `status="unknown_asset"`, skipping evidence gathering and synthesis — GS-07's `resolve_asset -> STOP` contract.
- **Evidence gathering** (tool-calling loop — see Task 5 for the cyclic-node decision):
  - Tools offered are filtered by `intent`: `procedure_lookup` → `search_maintenance_docs` only; `troubleshooting`/`maintenance_check`/`history_query` → `get_asset_status`, `get_maintenance_history`, `search_maintenance_docs`, `get_plant_policy`; `work_order_request` → those four plus `create_work_order_draft`.
  - Stop condition: `LLMResponse.tool_calls` empty → proceed to synthesis.
  - Exception: if `create_work_order_draft` is called, the loop breaks to the Phase 6 HITL checkpoint path instead of looping again or falling through to synthesis.
- **Synthesis**: one more LLM call, no tools bound, forced structured output — populates `answer`/`confidence`/evidence-used. Does not set `status`.
- **Terminal response**: always the last node on every path (happy, unknown-asset, and — once Phase 6 exists — needs-approval). Assembles the existing Phase 0 `AgentQueryResponse` from state; makes no further routing decisions itself.

### Test / Validation

- [ ] `procedure_lookup` intent never results in a `get_asset_status` tool call (GS-06 requirement) — enforced structurally by the intent-filtered tool list, not by prompting alone.
- [ ] `resolve_asset("PUMP-999")` (or any not-found identifier) reaches the terminal node with `status="unknown_asset"` without any evidence-gathering or synthesis node executing.
- [ ] The interpretation node skips identifier extraction when `asset_id_hint` is present, confirmed by a test asserting no LLM call references the raw query text for identifier extraction in that case.
- [ ] Terminal node is the only node that constructs an `AgentQueryResponse` instance.

---

## Task 5 — Conditional routing

### Evidence-gathering as a single cyclic node (the central decision for this task)

- Implemented as **one self-looping node** (LangGraph conditional self-edge), not four fixed sequential sub-nodes (one per evidence tool). Tool order and count are entirely delegated to LLM choice within the intent-filtered set — the graph makes no assumption about which tool comes first or how many get called.
- Rationale: GS-01 and GS-04 call `search_maintenance_docs`/`get_maintenance_history` in opposite relative order; GS-02/GS-03/GS-06 skip tools the golden spec marks "optional"; GS-05 calls `search_maintenance_docs` twice with different queries. A fixed sequential implementation would produce correct-looking results for some scenarios while silently violating the actual design intent (and breaking the "optional"/"skip" scenarios). Explicitly locking the single-cyclic-node shape prevents that drift.

### Iteration cap

- A structural bound exists on the evidence-gathering loop (LangGraph `recursion_limit` or an explicit counter in state) — exact number deferred to implementation, same treatment as the deferred LLM model string.
- **What happens when the cap is hit (graceful fallback vs. hard error) is explicitly deferred to Phase 5's guardrail/retry work**, not decided in Phase 4. Phase 4 only guarantees the loop cannot run unbounded.

### Explicitly out of scope for this task

- Insufficient-evidence detection/handling (e.g. a search returning nothing, an asset with no data at all) is Phase 5's "insufficient-evidence/uncertainty behavior" task. Synthesis works with whatever evidence accumulated by the time the loop exits; no graph-level "is this enough evidence" check exists yet in Phase 4.

### Terminal convergence

- Carried from Task 4: all exit paths (unknown-asset stop, normal synthesis completion, draft-created-so-go-to-HITL) funnel into the one shared terminal node, which packages whatever `status`/`answer`/`pending_action` was already set earlier in state — it does not make routing decisions itself.

### Test / Validation

- [ ] Evidence-gathering is implemented as a single node with a conditional self-edge, confirmed by graph structure inspection (not N separate per-tool nodes).
- [ ] A forced pathological case (LLM keeps requesting tools) is stopped by the iteration cap rather than running indefinitely, confirmed by a test that mocks `generate()` to always return a tool call.
- [ ] GS-01 and GS-04's differing tool-call orders both execute successfully through the same graph structure, with no code path assuming a fixed order.

---

## Task 6 — Return a validated structured response through `/agent/query`

### Graph lifecycle

- The compiled LangGraph graph is built once at FastAPI startup (lifespan), mirroring Phase 0's DB-engine pattern — not recompiled per request. Each request gets a fresh initial state dict and a request-scoped `AsyncSession`.

### Checkpointer (the central decision for this task)

- **No checkpointer is configured in Phase 4.** Nothing in this phase pauses execution (Phase 4's own success criteria only require the non-HITL golden paths to run end-to-end), so there's nothing to checkpoint yet. Checkpointer setup (in-memory vs. Postgres-backed — the latter matters for surviving a process restart between draft-creation and approval) is deferred entirely to Phase 6, when the interrupt/resume mechanism is actually introduced.
- Rejected alternative: wiring a no-op/in-memory checkpointer now so Phase 6 doesn't touch the `.compile()` call. Rejected because it would force a backend choice before any real requirement drives it.

### Correlation ID

- **`request_id` is generated at the top of the route, before graph invocation**, and seeded into initial state — not generated late by the terminal node. Reserved to double as the future LangGraph `thread_id` Phase 6's checkpointer will key runs by, and as the correlation ID Phase 7's telemetry event will use, avoiding a second ID scheme being invented later.

### Error handling at the boundary

- The graph invocation is wrapped in a try/except at the route level. Any unhandled exception maps to the existing `status="error"` envelope from Phase 0 — no retries, no bounded-attempt logic (that's explicitly Phase 5's job). This is the minimum needed so `/agent/query` never returns a raw unhandled exception, keeping Phase 0's "always returns a schema-valid response" test true after Phase 4.

### Response validation scope

- FastAPI's existing `response_model=AgentQueryResponse` declaration (Phase 0) remains the validation layer for this task — nothing new added here. Phase 5's "enforce structured output validation" task is a distinct, earlier layer (validating the LLM's own generated structured output before it lands in state), not duplicated at the HTTP boundary.

### Test / Validation

- [ ] Graph is compiled exactly once at app startup; a test confirms the same compiled graph object handles multiple sequential requests.
- [ ] `.compile()` is called with no `checkpointer` argument in Phase 4.
- [ ] `request_id` is generated before the graph is invoked and is present in the graph's initial state, not only in the final response.
- [ ] A forced unhandled exception inside a node results in an HTTP response with `status="error"`, not a 500 with a raw traceback.
- [ ] GS-01, GS-02, GS-03, GS-04, GS-05, GS-06, GS-07 (all seven non-HITL golden scenarios) run successfully through the real running `/agent/query` endpoint, not only via internal graph calls. GS-08 is explicitly out of scope until Phase 6.

## Success Criteria

- [ ] Graph state is a single `TypedDict` covering all eight required categories (request, intent, resolved asset, tool results, accumulated evidence, draft action state, errors, final response), with `operator.add` reducers only on the four genuinely multi-write fields.
- [ ] The LLM client is a hand-rolled thin wrapper over the raw Anthropic SDK, mockable in CI exactly like Phase 3's `embed()` interface, with no LangChain chat-model dependency anywhere in the graph.
- [ ] All seven canonical tools have LLM-facing bindings distinct from their internal Python signatures; `resolve_asset` and `submit_work_order` are structurally unreachable from any LLM tool-call decision.
- [ ] Evidence gathering is a single cyclic node whose tool order/count is fully LLM-determined within an intent-filtered set — verified against GS-01 through GS-06's varying trajectories.
- [ ] GS-07's unknown-asset stop and the (Phase 6-completing) work-order-draft-to-HITL branch are both structurally reserved in the graph's edges, even though HITL execution itself isn't tested until Phase 6.
- [ ] The seven non-HITL golden scenarios run end-to-end through the real `/agent/query` API, not only through internal graph invocation — satisfying the plan's Phase 4 success criterion "at least the non-HITL golden paths can run end-to-end through the same containerized API."
- [ ] The Phase 0 API contract (`AgentQueryRequest`/`AgentQueryResponse` shapes, `/health`, Docker Compose) remains unchanged — Phase 4 replaces the stub's internals only.
- [ ] No checkpointer, retry policy, or insufficient-evidence guardrail is implemented in Phase 4 — all three are explicitly deferred to Phase 5/6, keeping this phase's scope from creeping into later phases' work.

## Status

All six Phase 4 tasks are locked: typed graph state; thin LLM client abstraction; canonical tool bindings; graph nodes/edges; conditional routing; `/agent/query` integration. Nothing has been implemented yet. Success Criteria for the milestone are defined above. Phase 4 planning is complete. Next: proceed to implementation, or move on to Phase 5 (Reliability, Validation & Guardrails) planning discussion.