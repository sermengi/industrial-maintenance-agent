# Phase 7 (Structured Telemetry Seam) — Implementation Decisions

Captured from planning discussion, 2026-08-20. These are decisions made ahead of implementation, refining Implementation Plan v1.0 / Phase 7 and building on the Phase 4 (LangGraph Agent Orchestration), Phase 5 (Reliability, Validation & Guardrails), and Phase 6 (HITL & Work-Order Workflow) decisions without contradicting any of them. Decisions are locked task-by-task, following the Phase 7 task list from the implementation plan:

1. Define a stable run-event schema. **(locked)**
2. Capture request/input, ordered tool-call chain, final output summary/payload as appropriate, end-to-end latency, success/failure, and error category/message when present. **(locked)**
3. Implement a small emitter interface decoupled from the sink. **(locked)**
4. Use a simple JSON/JSONL local/file sink for v1. **(locked)**
5. Ensure one event is emitted for successful and failed completed runs, including HITL lifecycle handling as defined. **(locked — no new mechanism; see Task 5)**

All five Phase 7 tasks are locked. See Success Criteria and Status at the bottom.

---

## Task 1 — Define a stable run-event schema

### Format

- A Pydantic model, `RunEvent`, following the project's Pydantic-everywhere convention (Phase 2 Task 5, `WorkOrderDraft`, `SynthesisOutput`, etc.).
- `model_config = {"extra": "forbid"}` — same rationale as `WorkOrderDraft` (Phase 6 Task 1/6): this schema is explicitly meant to be Project 2's stable ingestion contract, so a stray field should be a validation error, not a silent pass-through.
- Serializes to one JSON line per event for the JSONL sink (Task 4 will cover the sink mechanics; this task only fixes the shape being serialized).

### One `RunEvent` per `build_response` call (the central decision)

- Exactly one `RunEvent` is emitted each time `build_response` is invoked — whether that's the terminal node completing normally (`ok`, `unknown_asset`, `insufficient_evidence`, `error`), or the API route layer building the turn-1 `needs_approval` response for an interrupted graph (Phase 6 Task 3's route-level fallback case).
- Rationale: `build_response` is already the single choke point for constructing every `AgentQueryResponse` (Phase 6 Task 3: "`build_response` is the only place `AgentQueryResponse` is constructed from graph state"). Hooking the emitter there covers every completion shape — including both turns of a HITL work-order flow — with no new branching logic, consistent with the project's repeated preference for reusing an existing seam over inventing a new one.
- Rejected alternative: hold a HITL work order's telemetry until final resolution and emit one merged event per logical workflow. Rejected because (a) it makes `latency_ms` ambiguous — does it include the human's wall-clock wait between draft and decision, or not — and (b) a draft that's approved days later would have no telemetry at all until then, which is a worse property for a "raw execution telemetry" seam than two events with a shared correlation key.
- **Refined under Task 2**: the practical emission point is the route-handler layer, not literally inside `build_response` itself — see Task 2's timing decision. Event count and timing semantics are unchanged; this is a more precise description of where the call lives, not a reversal.

### Consequence: `event_id` and `run_id` are separate fields

- Because a HITL work-order flow produces two `RunEvent`s against the same `request_id`/`thread_id`/`draft_id` (per the Phase 6 unification of those three), a single ID can no longer serve as both the record's own primary key and the correlation key across the pause. Two fields:
  - `event_id: UUID` — fresh per emitted record, the record's actual unique key.
  - `run_id: str` — equals `request_id` (and therefore `thread_id`/`draft_id` in the HITL case). Shared across a HITL pair's two events; for any non-HITL run, `event_id` and `run_id` each correspond to exactly one event, so nothing is lost there.
- Rejected alternative: reuse a single ID for both purposes, mirroring Phase 6 Task 1's `draft_id = request_id` ("no second ID minted"). Rejected because that precedent assumed one ID maps to at most one meaningful record; Task 1's own decision above breaks that assumption for HITL runs specifically.

### `latency_ms` scope

- Measures the duration of the specific `build_response` call this event is attached to — graph-start-to-interrupt for a turn-1 pause, or approval-endpoint-received-to-terminal-node for a turn-2 resume. Human decision time between the two is structurally excluded, since each event only ever spans one API call.

### `request` field does double duty

- `request: str` holds the original user query text for a normal run or a turn-1 pause, and holds the literal decision (`"approve"`/`"reject"`) for a turn-2 resume event. No separate `resolution` field, no `Optional` needed anywhere.
- Rationale: the type stays a plain, non-nullable `str` in both cases, so splitting into two fields would add a shape without adding type safety. Distinguishing "original query" vs "resume" events downstream doesn't depend on this field anyway — it falls out of `status` (turn 1 lands on `needs_approval`; any resume lands on `ok`) plus, if approve-vs-reject specifically matters, `final_output` (an approved resume's `structured_evidence` contains the new `WorkOrderRecord`; a rejected one doesn't).
- Explicitly accepted trade-off, not an oversight: a reader of raw events needs `status` and/or `final_output` alongside `request` to fully disambiguate a resume event's meaning; `request` alone doesn't self-label as "this is a decision, not a query."

### `tool_calls` — thin projection of the existing `ToolCallRecord`, not a new decoupled type

- `tool_calls: list[ToolCallSummary]`, where `ToolCallSummary` has exactly two fields: `tool_name: str`, `sequence: int`.
- Built directly from Phase 4's already-existing `state.tool_calls: Annotated[list[ToolCallRecord], operator.add]`, which Phase 4 Task 1 explicitly reserved as the shared source of truth for both Phase 8's tool-call-order assertions and Phase 7's telemetry event. `ToolCallRecord`'s own `sequence: int` field is the authoritative order marker — the emitter sorts/verifies by `sequence` rather than trusting raw list position, since `operator.add` list-concatenation order is only guaranteed to match true call order in the absence of any future concurrent-execution change to the evidence-gathering loop.
- `args`, `result`, and `timestamp` from `ToolCallRecord` are deliberately not carried into `ToolCallSummary`. Rationale: anything a tool returned on success is already reachable through `final_output`'s evidence lists (which is stored whole — see below), so duplicating it here doesn't add information, only a second copy to keep in sync.
- No per-call `status`/`error_message` field, despite one being proposed and initially accepted earlier in this discussion. Reasoning for the reversal: an entry only ever lands in `tool_calls` once a tool call has completed and returned its own typed `Result` model — per Phase 2's "no tool raises an exception for an expected business outcome" convention, an outcome like `resolve_asset` returning `not_found` is a legitimate, successful entry, not a failure. Genuine technical failures (retried/exhausted calls) go into the separate `errors: list[ErrorRecord]` (Phase 4 Task 1), never into `tool_calls`. A coarse success/failure label at the tool-call level therefore has nothing meaningful to distinguish — every entry that exists there already "succeeded" in the only sense `tool_calls` tracks.
- **Deviation from the project design doc, noted explicitly**: `industrial_maintenance_agent_project_design_v0.3.docx` §10 states the minimum run-event fields include "ordered tool-call chain **with status**." This decision knowingly departs from that literal wording, for the reasoning above. Recorded here as a conscious deviation rather than a silent gap, consistent with how Phase 6 recorded its own amendments against earlier planning-doc wording (e.g. Task 2's return-type amendment, Task 4's evidence-surfacing amendment).

### `final_output` — the full `AgentQueryResponse`, not a curated summary

- `final_output: AgentQueryResponse`, stored whole.
- Rationale: consistent with the project's debug-first philosophy (already used to justify `MemorySaver` over Postgres-backed checkpointing, `MAX()+1` over a dedicated sequence, etc. in Phase 6) — at this dataset's scale there's no size concern, and a separately-curated "summary" shape would be a second representation of the same data with its own drift risk, for no offsetting benefit.
- Satisfies the implementation plan's "final output summary/payload as appropriate" language by choosing "payload" over "summary."

### `error` — reuses `AgentQueryResponse`'s own error envelope

- `error: {code: str, message: str} | None`, identical in shape to the existing, already-public `AgentQueryResponse.error` field (locked in Phase 0, refined in Phase 5's category-specific `error.code` work).
- Unlike `tool_calls`, reuse here doesn't create unwanted coupling: `AgentQueryResponse.error` is a stable, external, already-frozen contract, not an internal type free to drift — so embedding it directly is the same "don't build a second shape for a case that doesn't need one" move, without the stability risk that motivated decoupling `tool_calls` from the internal `ToolResult` union.

### Full schema

```
RunEvent (extra="forbid"):
  event_id: UUID
  run_id: str                                   # == request_id/thread_id/draft_id
  emitted_at: datetime                          # UTC
  latency_ms: int                               # duration of this build_response call only
  status: Literal["ok","needs_approval","unknown_asset","insufficient_evidence","error"]
  request: str                                  # original query text, or "approve"/"reject" for a resume
  tool_calls: list[ToolCallSummary]              # sorted by sequence, not raw list order
  final_output: AgentQueryResponse
  error: {code: str, message: str} | None        # reuses AgentQueryResponse's own error shape

ToolCallSummary (extra="forbid"):
  tool_name: str
  sequence: int
```

### Test / Validation

- [ ] `RunEvent` and `ToolCallSummary` both reject any field outside their defined set (`extra="forbid"`), confirmed by a test passing an unrecognized key.
- [ ] A HITL work-order run (GS-08) produces exactly two `RunEvent`s sharing one `run_id` but distinct `event_id`s: the first with `status="needs_approval"`, the second with `status="ok"` from the resume — confirmed for both the approve and reject resume paths.
- [ ] A non-HITL run (any of GS-01 through GS-07) produces exactly one `RunEvent`.
- [ ] `tool_calls`, sorted by `sequence`, matches the actual LLM-requested tool order for GS-01 and GS-04, which the Phase 4 golden set exercises in opposite relative order.
- [ ] `latency_ms` on a HITL resume event reflects only the resume call's own processing time, not the elapsed wall-clock time since the original pause — confirmed by a test that inserts an artificial delay between pause and resume and asserts `latency_ms` is unaffected.
- [ ] `error` on an error-status event matches `AgentQueryResponse.error` exactly (`code`, `message`) — confirmed no separate/parallel error shape is introduced.
- [ ] `final_output` on any event round-trips as a valid `AgentQueryResponse` — confirmed no field is dropped or reshaped relative to the actual API response for that call.

---

## Task 2 — Capture: field-by-field sourcing and the timing seam

### Scope of this task

Task 1 fixed the schema's shape. Task 2 confirms, for every field, where its value actually comes from during a real run — and resolves the one field with no existing home. This is deliberately kept separate from Task 3 (the emitter's interface/wiring shape): Task 2 answers "what value, from where," Task 3 answers "how does it get assembled and handed to a sink."

### Most fields already exist — no new instrumentation

- `run_id` — `state["request_id"]`, already set per request (Phase 4 Task 6), already doubling as `thread_id`/`draft_id` (Phase 6).
- `status`, `final_output`, `error` — these *are* the `AgentQueryResponse` already being returned to the client; `error` in particular is already collapsed from `state["errors"]` into `{code, message}` by the terminal node (Phase 4 Task 1).
- `tool_calls` — `state["tool_calls"]`, retrievable even in the interrupted turn-1 case via the same `graph.aget_state(config)` call the approval route already uses to check `.next` (Phase 6 Task 4); `.values` carries the full state dict, `tool_calls` included.
- `request` — the incoming request body: `query` text for `/agent/query`, `decision` for `POST /agent/approvals/{draft_id}`.
- `event_id` — not sourced from state at all; generated fresh (UUID) at emission time.

None of the above require new plumbing. The schema drawn up in Task 1 is fully populatable from data that already exists somewhere in the running system.

### Timing capture — the one real gap (central decision)

- `emitted_at` and the start-of-interval reference for `latency_ms` are measured at the **route-handler level**, not inside the graph or any node.
- Mechanism: each of the two route handlers (`/agent/query` and `POST /agent/approvals/{draft_id}`) captures a `start` timestamp at function entry, before invoking the graph, and computes elapsed time right before returning its response to the client. This covers every case uniformly — a normal completion, an error, and an interrupted turn-1 pause all still funnel through one route-handler invocation before a response goes out; a turn-2 resume is its own separate route-handler invocation with the same shape.
- No new graph-state field carries a start time between a node and the route. The route already holds both ends of the interval in its own local scope; threading it through `state` would add a bookkeeping field to a `TypedDict` that's otherwise entirely domain-relevant (per Phase 4 Task 1's field categories), for no benefit.
- Rejected alternative: measure timing inside the graph (e.g., in the terminal node, or via a dedicated timing node). Rejected because the terminal node has no visibility into when the *route* first received the request — only the route spans the full interval a client actually experiences, including any pre-graph request validation.
- Clock: an injectable `clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)`, following the same "own the seam" pattern as `submit_work_order`'s injected clock (Phase 6 Task 2), overridable to a fixed value in tests so `latency_ms` assertions are deterministic. Tz-aware from the start (`datetime.now(timezone.utc)`, not a naive `utcnow()`) — deliberately avoids repeating the naive-timestamp ambiguity Phase 1 had to resolve after the fact for the seed data.

### Consequence for Task 1's "per `build_response` call" framing

- Restated precisely: one `RunEvent` per **route-handler completion**. The terminal node still just populates `state["response"]` exactly as Phase 4 designed it, with no awareness of timing or emission; the route either reads that response or calls `build_response` itself for the interrupted case, and either way ends up holding a finished response object plus a measured `latency_ms` at the same point. Event count and timing semantics are identical to Task 1's original framing — this only corrects *where in the code* the measurement and emission naturally live.

### Explicitly deferred to Task 3

- The exact call shape the route uses to hand off a fully-assembled `RunEvent` (a plain function call, a context manager wrapping the handler body, or something else) is emitter-interface territory, not capture semantics, and is left for Task 3.

### Test / Validation

- [ ] Every `RunEvent` field can be traced to an existing state/request/response value except `event_id` and `emitted_at`/`latency_ms`, confirmed by code inspection — no field silently requires instrumentation added outside the route-handler timing wrapper.
- [ ] `latency_ms` is measured from route-handler entry to response-ready, confirmed by a test that injects a mocked slow tool call and asserts the reported latency reflects it.
- [ ] The injected `clock` is overridden to a fixed sequence of values in tests, producing a deterministic `latency_ms` independent of real wall-clock time.
- [ ] No new field is added to the LangGraph state `TypedDict` to support timing — confirmed by diffing Phase 4's state schema against whatever Phase 7 actually ships.
- [ ] `state["tool_calls"]` is successfully read at the route layer for an interrupted (turn-1) run via `graph.aget_state(config).values`, not only for completed runs — confirmed by a test asserting a paused GS-08 run's `RunEvent.tool_calls` is non-empty when `create_work_order_draft` was called before the pause.

---

## Task 3 — Implement a small emitter interface decoupled from the sink

### Interface shape: a plain async callable, not a class/Protocol (the central decision)

- `EmitFn = Callable[[RunEvent], Awaitable[None]]` — a bare async function type, matching the project's established pattern for every other external seam: `embed()` (Phase 3) and the LLM `generate()` wrapper (Phase 4 Task 2) are both thin, bare functions swapped wholesale for a deterministic stub in tests, with no class hierarchy.
- The v1 JSONL sink (Task 4) is one concrete async function satisfying this signature — open, append one line, close. No connection pool, no batching, no long-lived resource to manage at this scale.
- Rejected alternative: a `Protocol`/ABC-based `RunEventEmitter` class with an `emit()` method. Not wrong in general, but it would be new ceremony this project hasn't needed for any other seam, for a sink that holds no state. Reconsider only if a future sink (Postgres, Langfuse) genuinely needs a persistent connection — even then, that connection could still be closed over inside a plain async function rather than requiring a class.

### Safety net: one shared helper wraps every emission, not each sink's own responsibility

- `async def record_run_event(emit: EmitFn, event: RunEvent) -> None`, wrapping the `await emit(event)` call in a broad `try/except Exception`, logging the failure (`logger.warning(..., exc_info=True)`), and never re-raising or retrying.
- Both route handlers call `record_run_event` exclusively — never the raw `EmitFn` directly. This puts the plan's "emission failure should be controlled and non-catastrophic" guarantee in exactly one place, rather than trusting every current and future sink implementation to remember to protect itself.
- No exception-type classification and no retry, matching Phase 5's "no exception-type classification" precedent (retries elsewhere don't differentiate exception types either) — a failed emission is simply logged and abandoned, consistent with telemetry being explicitly non-critical.

### Blocking (awaited inline), not deferred as a background task (locked)

- `record_run_event` is `await`-ed inline in each route handler, immediately before the response is returned to the client — not scheduled via FastAPI `BackgroundTasks`.
- Rationale: the literal constraint ("success must not depend on telemetry persistence") is already satisfied by the try/except in `record_run_event` — a caught failure cannot affect the response either way. The small added latency of a local file write is accepted as the simpler option, consistent with the project's repeated preference for the less sophisticated mechanism over one solving a problem this project doesn't have at debug scale (no exponential backoff, `MAX()+1` over a dedicated sequence, `MemorySaver` over a Postgres checkpointer).
- Rejected alternative: `BackgroundTasks`, deferring emission until after the response is sent. Would give a stronger guarantee that telemetry can never add latency to a response, at the cost of more moving parts and a real (if debug-scale-acceptable) risk of silently losing an event if the process exits between response-send and background execution. Rejected as unnecessary sophistication for this project's stated scope, not as categorically wrong — worth reconsidering only if a future sink's write latency becomes non-trivial.

### Injection / lifecycle

- The concrete `EmitFn` is constructed once at FastAPI startup (the same `lifespan` pattern already used for the DB engine and the compiled LangGraph graph, Phase 4 Task 6) — not rebuilt per request. Both route handlers receive it the same way they already receive the `AsyncSession`/graph.
- The concrete JSONL implementation itself (file path configuration, append mechanics) is Task 4's job; Task 3 only fixes that whatever satisfies `EmitFn` is wired in exactly once, at startup.

### Test substitution

- In tests, the injected `EmitFn` is replaced with an in-memory stub that appends each `RunEvent` to a list, mirroring exactly how `embed()`/`generate()` are swapped for deterministic mocks in CI (Phase 3/Phase 4 Task 2). This lets tests assert "a `RunEvent` with these fields was emitted" without touching the filesystem.

### Test / Validation

- [ ] `EmitFn` is a plain callable type alias; no class or `Protocol` is introduced for the interface — confirmed by code inspection.
- [ ] `record_run_event` never raises, even when the injected `emit` callable raises — confirmed by a test using a mocked failing `emit` function.
- [ ] A failed emission is logged, not silently dropped — confirmed by a test capturing log output.
- [ ] Both route handlers call `record_run_event` exclusively; no call site invokes a raw `EmitFn` directly — confirmed by code inspection.
- [ ] The concrete `EmitFn` is constructed once at app startup (`lifespan`) and not rebuilt per request — confirmed by test/inspection mirroring Phase 4 Task 6's graph-compiled-once assertion.
- [ ] In tests, the injected `EmitFn` is a stub collecting emitted events into a list — confirmed by a test asserting the golden scenarios produce the expected `RunEvent`(s) without touching the filesystem.
- [ ] A forced `emit()` failure (e.g. a simulated disk-full error) does not change the client-facing API response — confirmed by a test asserting identical status code and body whether or not emission succeeds.
- [ ] `record_run_event` is awaited inline before the route returns its response — confirmed no `BackgroundTasks` (or equivalent deferred-execution mechanism) is used for emission.

---

## Task 4 — Use a simple JSON/JSONL local/file sink for v1

### Concrete `EmitFn`: a factory closure over a configured path

- `EmitFn` has no room for a path parameter, so the sink is built by a small factory that closes over a configured path, constructed once at startup (same lifecycle as Task 3's injection decision):

```
def make_jsonl_emitter(path: Path) -> EmitFn:
    path.parent.mkdir(parents=True, exist_ok=True)   # once, at construction
    async def _emit(event: RunEvent) -> None:
        with open(path, "a") as f:
            f.write(event.model_dump_json() + "\n")
    return _emit
```

- Parent-directory creation happens once, at construction time inside the factory — not per call — avoiding a redundant `mkdir` on every request.

### Path configuration

- The file path comes from environment-based configuration (design doc §11's "environment-based configuration, avoid hard-coded secrets" principle, same treatment as the DB connection string), e.g. `RUN_EVENTS_PATH`, with a sensible default.
- The default should live under a directory that's volume-mounted in Docker Compose, so a developer can inspect the file from the host without shelling into the container — a Phase 9 documentation/compose-wiring concern, but worth choosing the default path with that in mind now rather than retrofitting later.

### Blocking file I/O — no `aiofiles`, no thread executor

- Plain synchronous `open()`/`write()`/`close()` directly inside the async `_emit`. Direct consequence of Task 3's inline-await decision: emission's small latency cost was already accepted there rather than deferring to a background task, so introducing a new dependency purely to make that already-accepted blocking window non-blocking-at-the-event-loop-level would undercut that reasoning. A one-line JSON append is on the order of microseconds at this project's scale.

### No file locking

- No advisory locking (`fcntl`/`portalocker`) around the append. Mirrors Phase 6 Task 2's reasoning for skipping a dedicated Postgres sequence: a concurrent-write race condition that doesn't exist in this project's actual usage pattern (single-request-at-a-time, debug-scale). A single short `write()` call is atomic enough under POSIX append semantics for this project's real concurrency profile.

### The sink stays "dumb" — no internal try/except

- Any `IOError`/`PermissionError` propagates straight out of `_emit`. Intentional: Task 3 already put the sole responsibility for catching emission failures in `record_run_event`; duplicating a safety net inside the sink would just be a second place that could silently diverge from the first.

### Companion reader, for tests and manual inspection

- `read_run_events(path: Path) -> list[RunEvent]` — parses each line and validates it back into a `RunEvent`. Used by at least one integration-level test that exercises the *real* sink end-to-end (not the Task 3 in-memory stub) for a representative set of scenarios, and doubles as the basis for whatever Phase 9 documents about inspecting telemetry.

### Test / Validation

- [ ] `make_jsonl_emitter` creates the parent directory if it doesn't exist, confirmed by a test pointing at a path under a nonexistent directory.
- [ ] Each call to the returned `_emit` appends exactly one line to the file; the file is never truncated or overwritten across calls.
- [ ] Every written line, when parsed with `read_run_events`, round-trips into a valid `RunEvent` with all fields intact.
- [ ] `_emit` raises (not swallows) on a forced write failure — confirmed by a test that the sink itself has no internal exception handling, distinguishing it from `record_run_event`'s behavior.
- [ ] At least one integration test runs a representative scenario through the real `/agent/query` (or approvals) endpoint with the real JSONL sink active (not the stub), then reads the file back and asserts on the resulting `RunEvent`.
- [ ] The configured path is read from environment configuration with a working default, confirmed by a test asserting the default path's parent directory is creatable in a clean environment.

---

## Task 5 — Ensure one event is emitted for successful and failed completed runs, including HITL lifecycle handling as defined

### No new mechanism needed

- Fully covered by decisions already locked in Tasks 1–3: exactly one `RunEvent` per route-handler completion (Task 1, refined in Task 2) means every terminal outcome — `ok`, `unknown_asset`, `insufficient_evidence`, `error` — already produces an event with no additional branching required. A HITL work-order flow already produces two events (pause, then resume) sharing one `run_id` but distinct `event_id`s, per Task 1's central decision. Emission never silently fails to happen or silently corrupts the response, because Task 3's `record_run_event` guarantees the attempt is always made and any failure is logged rather than swallowed without a trace.
- Same "no new mechanism" pattern as Phase 6 Task 5's treatment of PP-002 — this task's requirement is the natural consequence of earlier decisions, not a separate feature to build.

### Test / Validation

- [ ] Each of the eight golden scenarios (GS-01 through GS-08), run through the real API with the real JSONL sink active, produces the expected `RunEvent` count: one for GS-01–GS-07, two (sharing `run_id`) for GS-08.
- [ ] A forced internal error (mocked exception inside a node) still produces exactly one `RunEvent` with `status="error"` and a populated `error` field — confirmed emission happens on the failure path, not only on success.
- [ ] GS-07's unknown-asset stop produces exactly one `RunEvent` with `status="unknown_asset"`.
- [ ] Rejecting GS-08's draft (instead of approving it) still produces a second `RunEvent` with `status="ok"` and no `WorkOrderRecord` in `final_output.structured_evidence` — confirmed the reject path emits telemetry identically to the approve path, just with a different outcome.
- [ ] Running all eight scenarios back-to-back against the real sink and reading the file with `read_run_events` yields exactly nine `RunEvent`s in total (seven single-event scenarios + GS-08's two), confirmed by an end-to-end count assertion.

---

## Success Criteria

- [ ] Every completed API call (`/agent/query`, `POST /agent/approvals/{draft_id}`) emits exactly one structured `RunEvent`, whether the outcome is `ok`, `unknown_asset`, `insufficient_evidence`, `needs_approval`, or `error` — with a HITL work-order flow producing two events sharing one `run_id` but distinct `event_id`s (Tasks 1, 2).
- [ ] Every `RunEvent` field is sourced from data that already exists in graph state or the request/response objects, with the sole exception of `event_id` and timing, both resolved at the route-handler layer with no new LangGraph state field added (Task 2).
- [ ] The emitter is a small, sink-agnostic interface (`EmitFn`) that the JSONL sink is only one implementation of — swapping in a future Postgres/Langfuse sink requires no change to route-handler code, only a different function passed at startup (Tasks 3, 4).
- [ ] Telemetry emission can never affect a client-facing response: failures are caught, logged, and abandoned in exactly one place (`record_run_event`), and emission is deliberately synchronous/inline rather than deferred, consistent with the project's debug-first philosophy (Task 3).
- [ ] `tool_calls`, sorted by the existing `ToolCallRecord.sequence` field, faithfully represents true call order for every golden scenario, including GS-01/GS-04's differing relative orders (Tasks 1, 2).
- [ ] The full eight-scenario golden set, plus at least one forced-error run, can be exercised through the real `/agent/query` and `/agent/approvals` endpoints with the real JSONL sink active, and every resulting file line parses back into a valid `RunEvent` via `read_run_events` (Tasks 4, 5).

## Status

All five Phase 7 tasks are locked. Phase 7 planning is complete. Next: proceed to implementation, or move on to Phase 8 (Golden Scenario Integration & Hardening) planning discussion.