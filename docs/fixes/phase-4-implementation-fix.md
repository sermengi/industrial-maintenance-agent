# Fix — Route-level `error.message` leaks raw exception text

Scopes and implements **Gap 1** of
`docs/gaps/Phase7-finding-error-path-envelope-gaps.md` only. Gap 2 in that
finding (`asset_id` on the error path echoing the request hint instead of
`None`) is a separate, unrelated bug in the same `except` block — deferred,
not addressed here.

This refines the Phase 4 Task 6 route-level error-handling decision
(`docs/phase-4-implementation-details.md:207-209`): "Any unhandled exception
maps to the existing `status="error"` envelope... This is the minimum needed
so `/agent/query` never returns a raw unhandled exception." That criterion
was satisfied literally (no 500, no traceback) but not in spirit — the
envelope's `error.message` field still carries the raw exception string
verbatim.

## Location

`src/maintenance_agent/api/agent.py`, `query_agent`'s route-level
`except Exception as exc:` block (currently lines 48-72):

```python
except Exception as exc:
    response = AgentQueryResponse(
        request_id=request_id,
        status="error",
        asset_id=body.asset_id,
        answer=None,
        confidence=None,
        structured_evidence=[],
        document_evidence=[],
        pending_action=None,
        error=AgentError(
            code="unhandled_exception",
            message=str(exc),
        ),
    )
    ...
```

## Root cause

Phase 5 Task 5 locked a sanitization rule for three in-graph error codes
(`structured_output_invalid`, `tool_execution_failed`, `llm_call_failed`):
`error.message` in the API response is always a fixed, category-specific
templated string; the real exception text is preserved separately in
`ErrorRecord.message` inside graph state, never sent to the caller. That
machinery lives in `orchestration/graph.py`:
`_public_error_message(error_code)` (lines 828-835) supplies the templated
string, and `build_response` (line 763) uses it instead of the raw message.

Task 5 explicitly scoped that rule to errors caught **inside a graph node**
and routed to the terminal node — it says these "never bubbled to the Phase 4
Task 6 route-level try/except." Consistent with that, the route-level
catch-all in `agent.py` was never wired into `_public_error_message` or any
`ErrorRecord`/`state.errors` capture. It also has no logger of its own, so
today the only place the real exception text exists at all is the one place
it shouldn't — the outgoing HTTP response.

This path is reserved for "genuinely unanticipated exceptions (bugs, DB/
connection failures)" — the category most likely to say something internal
(a query fragment, a connection string, a stack-adjacent detail) in its
exception message. Nothing sanitizes it today.

## Fix design

Mirror the existing pattern used elsewhere in this codebase rather than
reaching into `graph.py`'s private helper or building new `ErrorRecord`
plumbing — there's no graph `state` at this failure point to record into
(the exception can fire before the graph is ever invoked), so state-based
capture doesn't apply here the way it does in-graph. Logging is the
equivalent server-side capture mechanism already used identically for
run-event-emit failures in `telemetry/run_events.py`:

```python
logger.warning("Failed to emit run event.", exc_info=True)
```

and for DB startup checks in `db/session.py`:

```python
logger = logging.getLogger(__name__)
```

Applying the same two pieces to `agent.py`:

1. Add a module logger: `logger = logging.getLogger(__name__)`.
2. In the `except Exception as exc:` block, call
   `logger.exception("Unhandled exception in /agent/query.")` before
   building the response — captures the real exception and traceback
   server-side via the standard `exc_info` idiom (`logger.exception` is
   `logger.error(..., exc_info=True)`).
3. Replace `message=str(exc)` with a fixed string:
   `"An unexpected error occurred. Please try again shortly."` — matching
   the tone of the three existing `_public_error_message` strings, which all
   end "...Please try again shortly."

`error.code` stays `"unhandled_exception"` — out of scope to rename; the
original finding only flagged it as an observed-but-undocumented fourth
code, not something to change. This fix locks it as intentional: it is now
the fourth valid `error.code` value alongside `structured_output_invalid`,
`tool_execution_failed`, and `llm_call_failed`.

## Test impact

`tests/test_health.py::test_agent_query_maps_unhandled_graph_exception_to_error_response`
(lines 179-216) currently asserts the buggy behavior directly:

```python
assert response.error == AgentError(
    code="unhandled_exception",
    message="graph failed",
)
```

This assertion must change to the new templated string. The same test's
`assert response.asset_id == "PUMP-103"` (line 199) is Gap 2 territory and
is left untouched — the route code for `asset_id` isn't changing, so that
assertion continues to hold either way.

A new regression test is added, mirroring the existing `raw_marker` pattern
used for the three in-graph codes in `tests/test_graph_nodes.py` (e.g. lines
542, 564, 610-611): raise an exception whose message contains a marker
string, assert the marker is absent from `response.error.message`, and
assert it *is* captured server-side via `caplog` (same pattern as
`test_failed_run_event_emission_is_logged_without_changing_response` in
`test_health.py`, which uses
`caplog.at_level(logging.WARNING, logger="maintenance_agent.telemetry.run_events")`).

No other test in the repo asserts on this path's `error.message` value
(`test_tool_bindings.py::test_consequential_guard_error_reaches_api_unhandled_exception_boundary`
and `test_run_event_sink.py::test_unhandled_internal_error_still_emits_one_error_run_event`
only assert `error.code == "unhandled_exception"`, not the message text), so
no other test file needs updating.

## Status

Implemented. `src/maintenance_agent/api/agent.py`'s route-level
`except Exception:` block now logs the real exception via
`logger.exception(...)` and returns a fixed templated `error.message`
instead of the raw exception text. `error.code` remains
`"unhandled_exception"`. Regression coverage: the existing
`test_agent_query_maps_unhandled_graph_exception_to_error_response` asserts
the templated string, and a new
`test_agent_query_never_leaks_raw_exception_text_but_logs_it` asserts a
marker string injected into a forced exception is absent from the response
but present in the captured log output.
