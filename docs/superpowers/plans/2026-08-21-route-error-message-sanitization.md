# Route Error Message Sanitization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop `POST /agent/query`'s route-level unhandled-exception handler from leaking raw exception text into `AgentQueryResponse.error.message`, while still capturing the real exception server-side.

**Architecture:** Add a module-level `logging.getLogger(__name__)` to `src/maintenance_agent/api/agent.py` (mirroring the existing pattern in `db/session.py` and `telemetry/run_events.py`). In the route's `except Exception as exc:` block, log the exception via `logger.exception(...)` before building the response, and replace the response's `error.message` with a fixed templated string instead of `str(exc)`.

**Tech Stack:** Python 3, FastAPI, pytest, pytest-asyncio, stdlib `logging`.

## Global Constraints

- Scope is Gap 1 only (`docs/fixes/phase-4-implementation-fix.md`). Do not touch `asset_id` sourcing on the error path (Gap 2) — that assertion in the existing test stays as-is.
- `error.code` stays `"unhandled_exception"` — no rename.
- The templated message text is exactly: `"An unexpected error occurred. Please try again shortly."`
- No new files, no new dependencies, no `ErrorRecord`/graph-state plumbing — this path has no graph state to record into.

---

### Task 1: Sanitize the route-level error message and log the real exception

**Files:**
- Modify: `src/maintenance_agent/api/agent.py:1-72`
- Modify: `tests/test_health.py:179-216` (update existing assertion)
- Modify: `tests/test_health.py` (add new regression test near line 216, before `test_approval_endpoint_resumes_pending_approval`)

**Interfaces:**
- Consumes: nothing new — uses existing `AgentError`, `AgentQueryResponse` from `maintenance_agent.schemas.agent`, existing `_request_with_graph`, `_collecting_emitter`, `_SequenceClock`, `_fake_session_context` test helpers already defined in `tests/test_health.py`.
- Produces: no new public names. `agent.py` gains a module-level `logger` (private, not imported elsewhere).

- [ ] **Step 1: Update the existing test's message assertion to the new templated string**

In `tests/test_health.py`, in `test_agent_query_maps_unhandled_graph_exception_to_error_response`, change:

```python
    assert response.error == AgentError(
        code="unhandled_exception",
        message="graph failed",
    )
```

to:

```python
    assert response.error == AgentError(
        code="unhandled_exception",
        message="An unexpected error occurred. Please try again shortly.",
    )
```

Leave every other line in that test (including `assert response.asset_id == "PUMP-103"` on line 199) unchanged.

- [ ] **Step 2: Add a new regression test asserting the raw exception is never leaked, and is logged**

Add this test in `tests/test_health.py`, directly after
`test_agent_query_maps_unhandled_graph_exception_to_error_response` (after line 216, before the blank lines preceding `test_approval_endpoint_resumes_pending_approval`):

```python
@pytest.mark.asyncio
async def test_agent_query_never_leaks_raw_exception_text_but_logs_it(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    raw_marker = "RAW_ROUTE_FAILURE_MARKER"

    class _MarkerFailingGraph:
        async def ainvoke(
            self,
            state: dict[str, Any],
            config: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            del state, config
            raise RuntimeError(raw_marker)

    request = _request_with_graph(_MarkerFailingGraph())
    monkeypatch.setattr(agent_api, "_request_session", _fake_session_context)

    with caplog.at_level(logging.ERROR, logger="maintenance_agent.api.agent"):
        response = await query_agent(
            request,
            AgentQueryRequest(query="Check pump vibration."),
        )

    assert response.status == "error"
    assert response.error is not None
    assert raw_marker not in response.error.message
    assert response.error.message == "An unexpected error occurred. Please try again shortly."
    assert raw_marker in caplog.text
```

This mirrors the `raw_marker` pattern already used in
`tests/test_graph_nodes.py` (e.g. `assert raw_marker not in
final_state["response"].error.message`) and the `caplog.at_level(...,
logger="maintenance_agent.telemetry.run_events")` pattern already used in
`test_failed_run_event_emission_is_logged_without_changing_response` in this
same file — just pointed at the new `maintenance_agent.api.agent` logger.

- [ ] **Step 3: Run both tests to verify they fail against current code**

Run: `uv run pytest tests/test_health.py::test_agent_query_maps_unhandled_graph_exception_to_error_response tests/test_health.py::test_agent_query_never_leaks_raw_exception_text_but_logs_it -v`

Expected: both FAIL — the first because `response.error.message` is still
`"graph failed"`, not the templated string; the second because
`response.error.message` still contains `raw_marker` (assertion
`raw_marker not in response.error.message` fails) and/or because
`caplog.text` is empty (nothing logs today).

- [ ] **Step 4: Implement the fix in `agent.py`**

Add `import logging` to the top of `src/maintenance_agent/api/agent.py`
(alongside the existing imports), and add a module logger after the
existing `router = APIRouter()` / `Clock = ...` lines:

```python
import logging
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from langgraph.types import Command
from sqlalchemy.ext.asyncio import AsyncSession

from maintenance_agent.db import session as db_session
from maintenance_agent.orchestration.graph import build_response
from maintenance_agent.orchestration.state import GraphState
from maintenance_agent.schemas.agent import (
    AgentApprovalRequest,
    AgentError,
    AgentQueryRequest,
    AgentQueryResponse,
)
from maintenance_agent.schemas.run_event import RunEvent, ToolCallSummary
from maintenance_agent.telemetry.run_events import EmitFn, noop_emit_run_event, record_run_event

router = APIRouter()
logger = logging.getLogger(__name__)
Clock = Callable[[], datetime]
```

Then change the `except Exception as exc:` block in `query_agent` (currently
lines 48-72) from:

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
```

to:

```python
    except Exception:
        logger.exception("Unhandled exception in /agent/query.")
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
                message="An unexpected error occurred. Please try again shortly.",
            ),
        )
```

Note `except Exception as exc:` becomes `except Exception:` — `exc` is no
longer referenced anywhere in the block (`logger.exception(...)` reads the
active exception from `sys.exc_info()` automatically), so keeping an unused
`as exc` binding would fail lint.

- [ ] **Step 5: Run the two targeted tests again to verify they pass**

Run: `uv run pytest tests/test_health.py::test_agent_query_maps_unhandled_graph_exception_to_error_response tests/test_health.py::test_agent_query_never_leaks_raw_exception_text_but_logs_it -v`

Expected: both PASS.

- [ ] **Step 6: Run the full test file to confirm no other test regressed**

Run: `uv run pytest tests/test_health.py -v`

Expected: all tests PASS, including
`test_consequential_guard_error_reaches_api_unhandled_exception_boundary`-style
coverage that lives in other files (see Step 7).

- [ ] **Step 7: Run the full test suite**

Run: `uv run pytest -q`

Expected: all tests PASS, including
`tests/test_tool_bindings.py::test_consequential_guard_error_reaches_api_unhandled_exception_boundary`
and
`tests/test_run_event_sink.py::test_unhandled_internal_error_still_emits_one_error_run_event`
(neither asserts on the message text, only on `error.code`, so both remain
green without modification).

- [ ] **Step 8: Update the fix doc's status**

In `docs/fixes/phase-4-implementation-fix.md`, change the final `## Status`
section from:

```markdown
## Status

Not yet implemented. Implementation plan: see
`docs/superpowers/plans/2026-08-21-route-error-message-sanitization.md`.
```

to:

```markdown
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
```

- [ ] **Step 9: Commit**

```bash
git add src/maintenance_agent/api/agent.py tests/test_health.py docs/fixes/phase-4-implementation-fix.md
git commit -m "fix: sanitize route-level unhandled exception message, log real error"
```

## Success Criteria

- [ ] `POST /agent/query`'s route-level catch-all never returns raw exception text in `error.message`.
- [ ] The real exception (message + traceback) is captured server-side via the standard logger, at `maintenance_agent.api.agent`.
- [ ] `error.code` remains `"unhandled_exception"`, unchanged.
- [ ] `asset_id` sourcing on the error path is untouched (Gap 2 deferred).
- [ ] Full test suite passes (`uv run pytest -q`).

## Status

Planning complete. Not yet implemented.
