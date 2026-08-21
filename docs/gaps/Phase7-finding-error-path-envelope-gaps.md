# Finding — Route-level error path leaks raw exception text and mis-populates `asset_id`

Captured 2026-08-21, during a notebook walkthrough of the Phase 7 structured-telemetry
scenarios (`phase7structuredtelemetrywalkthrough.ipynb`), Scenario D (section 7, forced
internal error via `FailingGraph`). Two distinct gaps in the same response, both on the
Phase 4 Task 6 route-level try/except path — not a Phase 7 defect (the `RunEvent` itself is
correct: `error` matches `final_output.error` exactly, as designed), but a pre-existing
envelope-correctness gap this telemetry walkthrough happened to surface first. Not yet
fixed.

## What was observed

The request was `POST /agent/query` with body
`{"query": "Check pump vibration.", "asset_id": "PUMP-103"}`, against an app wired to a
`FailingGraph` whose `ainvoke` raises `RuntimeError("graph failed")` immediately, before any
node executes. The response:

```json
{
  "request_id": "7341201d-d90a-4a18-893f-82b6f3abfecf",
  "status": "error",
  "asset_id": "PUMP-103",
  "answer": null,
  "confidence": null,
  "evidence_used": [],
  "structured_evidence": [],
  "document_evidence": [],
  "pending_action": null,
  "error": {
    "code": "unhandled_exception",
    "message": "graph failed"
  }
}
```

The corresponding `RunEvent.tool_calls` is `[]` — confirming no node, including
`resolve_asset`, ever ran before the exception propagated.

Two things stand out against that backdrop:

1. `error.message` is `"graph failed"` — the exact, unmodified string passed to
   `RuntimeError(...)` in the test double. The real exception text reached the API caller
   verbatim.
2. `asset_id` is `"PUMP-103"` — populated — despite `tool_calls` being empty, meaning
   `resolve_asset` never ran and nothing was actually resolved.

## Root cause / gap against locked decisions

### Gap 1 — `error.message` sanitization was never decided for this specific path

Phase 5 Task 5 locked a sanitization rule, but scoped it narrowly:

> "`error.message` in the API response is a fixed, category-specific templated string...
> never the raw exception text... The actual exception detail is preserved in
> `ErrorRecord.message` inside graph state... but is never sent to the API caller."

That rule covers exactly three categorized codes — `structured_output_invalid` (Task 1),
`tool_execution_failed` and `llm_call_failed` (Task 5) — all caught **inside a graph node**
and routed via a conditional edge to the terminal node. Task 5 is explicit that these never
reach the route-level handler: "never bubbled to the Phase 4 Task 6 route-level try/except,
which remains reserved purely for exceptions from outside any anticipated failure category."

Phase 4 Task 6's own decision for that route-level catch-all only says: "Any unhandled
exception maps to the existing `status=\"error\"` envelope from Phase 0... This is the
minimum needed so `/agent/query` never returns a raw unhandled exception" — satisfied
literally (no raw *traceback*, no 500), but no decision was ever made about whether
`error.message` itself should be templated/sanitized the same way the three in-node
categories are. This is arguably the highest-risk path for that omission to matter: it's
reserved for "genuinely unanticipated exceptions (bugs, DB/connection failures)" — precisely
the category most likely to say something internal (a query fragment, a connection string,
a stack-adjacent detail) in its exception message. Empirically, on this path, nothing
sanitizes it.

The `error.code` value used, `"unhandled_exception"`, is also not a value locked in either
Phase 4 or Phase 5's decisions — those only enumerate `structured_output_invalid`,
`tool_execution_failed`, and `llm_call_failed`. Not necessarily wrong, but worth recording
as an observed-but-undocumented fourth code, so it doesn't get silently reinvented under a
different name later.

### Gap 2 — `asset_id` on the error path reflects the request hint, not what was resolved

Two separate locked decisions define what this field should mean:

- Phase 0: `asset_id: Optional[str]` — "the asset actually resolved by `resolve_asset`, if
  any."
- Phase 5 Task 5, specifically for the error path: "`asset_id` reflects whatever was
  resolved before the failure (populated if `resolve_asset` had already succeeded; `None`
  if the failure occurred at or before asset resolution — e.g. a transient DB blip during
  `resolve_asset` itself...)."

In this scenario the failure happened before any node ran at all — strictly earlier than
"at or before asset resolution" — so by Task 5's own rule `asset_id` should be `None`. It
isn't. The one variable that changed between this request and a hint-free one is that the
request body included the optional `asset_id: "PUMP-103"` hint field Phase 0 reserved for
"convenience for testing/debugging without depending on NLU asset extraction." The response
value matches that hint exactly, which strongly suggests the route-level error-handling
path echoes the *request's* `asset_id` hint directly into the response, rather than reading
whatever `resolve_asset` had (or hadn't) actually resolved by the time the failure occurred.
That's a real behavioral gap, not just an undocumented corner: it means a response's
`asset_id` can claim an asset was involved in producing the error when no resolution ever
happened, which would be actively misleading for anyone debugging off the response alone
(as opposed to the correctly-empty `tool_calls` trace, which does tell the truth here).

## Why it matters

- **Gap 1** is an information-boundary concern consistent with the same reasoning Phase 5
  Task 5 already applied elsewhere in this project — "the right habit if this project is
  ever pointed at something less disposable than a debug dataset" — just not yet extended to
  cover the one catch-all path most likely to carry something worth not exposing.
- **Gap 2** is a correctness bug against an explicitly documented field contract, not a
  missing decision. A consumer reading only the response body (not cross-referencing
  `RunEvent.tool_calls`) would reasonably but incorrectly conclude PUMP-103 was resolved and
  somehow implicated in the failure.
- Both gaps live on the same code path (the Phase 4 Task 6 route-level except-handler), so
  they're likely fixed together in one place.

## Recommended fix

- Give the route-level catch-all the same templated, non-leaking `error.message` treatment
  Task 5 already established for the other three codes — e.g. a fixed string like
  `"An unexpected error occurred. Please try again shortly."` — while still logging/recording
  the real exception text server-side (the same `ErrorRecord`-in-state-and-logs pattern, or
  equivalent, since this path currently never touches `state.errors` at all — it fails before
  any node runs).
- Stop sourcing the error-path response's `asset_id` from the raw request hint. Either omit
  it entirely on this path (simplest, and consistent with "the failure occurred at or before
  asset resolution → `None`" since a route-level exception before graph invocation is exactly
  that case), or, if the hint is considered worth surfacing for debugging convenience, expose
  it under a different, honestly-named field rather than overloading `asset_id`'s documented
  "actually resolved" meaning.
- Add a test asserting the response's `asset_id` is `None` (not the request's hint) when a
  route-level exception occurs before any node — including specifically the case where the
  request *did* supply an `asset_id` hint, since that's exactly the case this notebook run
  happened to exercise and the one a same-value coincidence could otherwise mask.
- Add a test asserting `error.message` for a forced route-level exception never contains a
  marker string injected into the mocked exception, mirroring the existing Task 5 test
  pattern used for the other three error codes.

## Status

Confirmed, not yet implemented. Next step: decide the templated message text and the
`asset_id`-on-error-path fix, implement both in the route-level except-handler, and re-run
Scenario D (plus a hint-supplied variant, since the original test only exercises the hint
being present) through the notebook to confirm both fields behave per the corrected
contract.