# Fix — Route-level `asset_id` echoes the request hint instead of what was resolved

Scopes and implements **Gap 2** of
`docs/gaps/Phase7-finding-error-path-envelope-gaps.md` only. Gap 1 in that
finding (`error.message` leaking raw exception text) is a separate,
unrelated bug in the same `except` block, covered independently by
`docs/fixes/phase-4-implementation-fix.md` — this fix does not assume that
one has landed, and is written against the `except` block as it exists on
disk today.

## Location

`src/maintenance_agent/api/agent.py`, `query_agent`'s route-level
`except Exception as exc:` block (lines 48-72), specifically:

```python
        response = AgentQueryResponse(
            request_id=request_id,
            status="error",
            asset_id=body.asset_id,
            ...
```

## Root cause

Two locked decisions define what `asset_id` should mean:

- Phase 0: "the asset actually resolved by `resolve_asset`, if any."
- Phase 5 Task 5 (`docs/phase-5-implementation-details.md:203`), for its own
  in-graph error path: "`asset_id` reflects whatever was resolved before the
  failure (populated if `resolve_asset` had already succeeded; `None` if the
  failure occurred at or before asset resolution...)."

That Task 5 rule was written for errors caught *inside* a graph node
(`tool_execution_failed`/`llm_call_failed`), which never reach the route
level (`phase-5-implementation-details.md:191`). It was never explicitly
extended to the route-level catch-all — but it should be, by the same Phase
0 contract, because the docs name the one exception type that's designed to
land in this exact handler:

> `phase-5-implementation-details.md:270` — `ConsequentialActionGuardError`
> "is exactly the kind of exception the Phase 4 Task 6 route-level
> try/except exists to catch."

For example, `_invoke_agent_graph` raises `RuntimeError("Agent graph
completed without a response.")` at line 126 after a full graph run completes
but returns an unexpected state shape. When that exception fires, `resolve_asset`
and evidence gathering will have already completed, so the asset **was already
resolved** and sitting in the checkpoint — the opposite of what the test's
synthetic `FailingGraph` (which raises before any node runs) exercises.
Today the route-level handler ignores this distinction entirely and always
echoes `body.asset_id` — the client-supplied hint, Phase 0's "convenience
for testing/debugging" field — regardless of what actually happened.

## Fix design

Read the actually-resolved asset back from the graph's own checkpoint
rather than trusting the request hint, mirroring the same
resolved-or-`None` logic `_response_asset_id` already implements in-graph
(`orchestration/graph.py:838-846`). This is possible because the compiled
graph carries a `MemorySaver` checkpointer unconditionally
(`orchestration/graph.py:149-151,332`; confirmed behavior in
`docs/fixes/phase-7-implementation-fix.md`) — every node's state update is
checkpointed regardless of whether the run ever pauses, so a checkpoint for
the current `request_id`/`thread_id` reflects state as of the last node
that completed *before* the failure, even when the failure itself was never
caught in-graph.

Add one helper to `src/maintenance_agent/api/agent.py`:

```python
async def _asset_id_from_checkpoint(request: Request, request_id: str) -> str | None:
    try:
        graph = cast(Any, request.app.state.agent_graph)
        checkpoint = await graph.aget_state({"configurable": {"thread_id": request_id}})
    except Exception:
        return None
    values = getattr(checkpoint, "values", None) or {}
    asset = values.get("asset")
    return asset.asset_id if asset is not None else None
```

And use it in the `except` block:

```python
            asset_id=await _asset_id_from_checkpoint(request, request_id),
```

Design notes:

- **The config passed to `aget_state` only needs `thread_id`.** The existing
  `_thread_config(request_id, session)` helper also stuffs a live `session`
  into `configurable` for tool bindings to use *during node execution* —
  irrelevant here, since `aget_state` only reads checkpoint metadata and
  runs no node/tool code. Building a fresh inline dict avoids needing a
  `session` object in the `except` block, which matters because if
  `_request_session()` itself raised (session acquisition failed), no
  `session` variable would even be bound at that point.
- **The whole read is wrapped in its own `try/except Exception: return
  None`.** This is a safety net around a safety net: we're already inside
  the route's catch-all for unanticipated failures, so a *second* failure
  while trying to introspect state (no checkpoint yet, or a test double /
  future graph implementation that doesn't implement `get_state`/
  `aget_state` at all) must degrade to `None`, never raise past the
  error-response construction. This also means every existing test double
  that only implements `ainvoke` (e.g. `_FailingGraph` in
  `test_health.py`/`test_run_event_sink.py`, `GuardFailingGraph` in
  `test_tool_bindings.py`) keeps working unmodified — the `AttributeError`
  they'd raise on `.aget_state(...)` is caught and treated the same as "no
  checkpoint exists," which is the correct answer for all of them anyway
  (their `ainvoke` raises immediately, before any node — including
  `resolve_asset` — could run).
- **No new production dependencies or types.** `AgentGraph.aget_state`
  (`orchestration/graph.py:220-221`) already exists and is already used the
  same way, synchronously, in `_invoke_agent_graph`
  (`api/agent.py:117`) and asynchronously in `resolve_pending_action`
  (`api/agent.py:86`) — this fix reuses the exact same call, just from the
  `except` block instead of the happy path.

## Test impact

`tests/test_health.py::test_agent_query_maps_unhandled_graph_exception_to_error_response`
currently asserts the buggy behavior directly:

```python
    assert response.asset_id == "PUMP-103"
```

`_FailingGraph` (the test double this test uses) only implements
`ainvoke`, so under the fix `_asset_id_from_checkpoint` hits its
`except Exception: return None` branch. This assertion changes to:

```python
    assert response.asset_id is None
```

A new regression test is added covering the branch this fix actually
exists for: a graph that fails *after* `resolve_asset` has already
succeeded should still report the resolved `asset_id`. This needs a new
test double — `_FailingGraph`-style but also implementing `aget_state` to
return a checkpoint whose `values["asset"]` is populated, mirroring the
existing `_ResolvedCheckpointGraph`/`_NoCheckpointGraph` pattern already in
`tests/test_health.py` (lines 532-545).

`tests/test_tool_bindings.py::test_consequential_guard_error_reaches_api_unhandled_exception_boundary`
does not assert on `asset_id` today and needs no change — its
`GuardFailingGraph` test double also only implements `ainvoke` (raises
immediately, without a real graph run), so it continues to correctly
resolve to `None` under this fix, same as before.

## Status

Implemented. `src/maintenance_agent/api/agent.py`'s route-level
`except Exception as exc:` block now sources `asset_id` from
`_asset_id_from_checkpoint`, which reads the graph's checkpoint for the
current `thread_id` and returns the resolved asset's ID if `resolve_asset`
had already succeeded, `None` otherwise (including when no checkpoint
exists at all, or when the graph implementation doesn't support
`get_state`/`aget_state`). Regression coverage: the existing
`test_agent_query_maps_unhandled_graph_exception_to_error_response` now
asserts `asset_id is None` for a pre-resolution failure, and a new
`test_agent_query_reports_resolved_asset_id_on_post_resolution_failure`
asserts the checkpointed asset is reported even when the request supplied
a different (or no) `asset_id` hint.
