# Route Error-Path Asset ID Checkpoint Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop `POST /agent/query`'s route-level unhandled-exception handler from echoing the request's `asset_id` hint into `AgentQueryResponse.asset_id`; instead report the asset actually resolved by `resolve_asset` before the failure (or `None` if none was), read back from the graph's own checkpoint.

**Architecture:** Add an `_asset_id_from_checkpoint(request, request_id)` helper to `src/maintenance_agent/api/agent.py` that reads the compiled graph's checkpoint for the current `thread_id` via `graph.aget_state(...)` (the same call already used in `resolve_pending_action`), extracts `values["asset"]` if present, and defaults to `None` on any failure (no checkpoint, or a graph implementation without `get_state`/`aget_state`). Call it from the route's `except Exception as exc:` block instead of echoing `body.asset_id`.

**Tech Stack:** Python 3, FastAPI, LangGraph (`AgentGraph.aget_state`), pytest, pytest-asyncio.

## Global Constraints

- Scope is Gap 2 only (`docs/fixes/phase-4-asset-id-error-path-fix.md`). This plan is written against `src/maintenance_agent/api/agent.py` as it currently exists on disk — it does not assume Gap 1's fix (`docs/fixes/phase-4-implementation-fix.md`) has been implemented, and does not touch `error.message` or `error.code`.
- No new production dependencies. Reuse `AgentGraph.aget_state`, already used elsewhere in this file.
- The checkpoint-read helper must never raise — any failure (missing checkpoint, test double without `get_state`) resolves to `None`, matching Phase 0's "the asset actually resolved, if any" contract when nothing was.

---

### Task 1: Read `asset_id` from the graph checkpoint on the route-level error path

**Files:**
- Modify: `src/maintenance_agent/api/agent.py:48-72` (except block), plus a new helper function
- Modify: `tests/test_health.py:199` (update existing assertion)
- Modify: `tests/test_health.py` (add `AssetRecord` import, add a new test double, add a new regression test)

**Interfaces:**
- Consumes: `AgentGraph.aget_state(config: dict[str, object]) -> Any` (already defined in `orchestration/graph.py:220-221`, returns an object with `.values` and `.next` attributes — see `SimpleNamespace(next=..., values=...)` used by existing test doubles).
- Produces: `_asset_id_from_checkpoint(request: Request, request_id: str) -> str | None`, a new private module-level async function in `agent.py`. No other module imports or calls it.

- [ ] **Step 1: Update the existing test's assertion to the new expected behavior**

In `tests/test_health.py`, in `test_agent_query_maps_unhandled_graph_exception_to_error_response`
(around line 199), change:

```python
    assert response.asset_id == "PUMP-103"
```

to:

```python
    assert response.asset_id is None
```

This reflects that `_FailingGraph` (this test's double) only implements
`ainvoke`, so no checkpoint exists for it to read — the correct answer per
Phase 0's "the asset actually resolved" contract is `None`, since nothing
was resolved before `_FailingGraph.ainvoke` raised immediately.

- [ ] **Step 2: Add the `AssetRecord` import needed for the new test double**

In `tests/test_health.py`, change:

```python
from maintenance_agent.db.repositories.records import WorkOrderRecord
```

to:

```python
from maintenance_agent.db.repositories.records import AssetRecord, WorkOrderRecord
```

- [ ] **Step 3: Add a new test double that fails after the asset was already resolved**

Add this class in `tests/test_health.py`, directly after the existing
`class _FailingGraph:` block (after line 434, before `class
_InterruptedGraph:`):

```python
class _FailingGraphWithResolvedAsset:
    async def ainvoke(
        self,
        state: dict[str, Any],
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        del state, config
        raise RuntimeError("guard tripped after asset resolution")

    async def aget_state(self, config: dict[str, Any]) -> Any:
        del config
        return SimpleNamespace(
            next=(),
            values={
                "asset": AssetRecord(
                    asset_id="PUMP-103",
                    asset_type="centrifugal_pump",
                    model="CP-200",
                    location="Line 3",
                    installation_date=date(2021, 6, 1),
                    status="operational",
                )
            },
        )
```

- [ ] **Step 4: Add the new regression test**

Add this test in `tests/test_health.py`, directly after
`test_agent_query_maps_unhandled_graph_exception_to_error_response` (after
line 216, before `test_approval_endpoint_resumes_pending_approval`):

```python
@pytest.mark.asyncio
async def test_agent_query_reports_resolved_asset_id_on_post_resolution_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request_with_graph(_FailingGraphWithResolvedAsset())
    monkeypatch.setattr(agent_api, "_request_session", _fake_session_context)

    response = await query_agent(
        request,
        AgentQueryRequest(query="Submit the work order.", asset_id="PUMP-999"),
    )

    assert response.status == "error"
    assert response.asset_id == "PUMP-103"
```

Note the request hint is deliberately `"PUMP-999"` while the checkpointed,
actually-resolved asset is `"PUMP-103"` — this proves the response is
sourced from the checkpoint, not echoed from the request body.

- [ ] **Step 5: Run the three targeted tests to verify the new/changed ones fail against current code**

Run: `uv run pytest tests/test_health.py::test_agent_query_maps_unhandled_graph_exception_to_error_response tests/test_health.py::test_agent_query_reports_resolved_asset_id_on_post_resolution_failure -v`

Expected: the first FAILs (`response.asset_id` is still `"PUMP-103"` from
`body.asset_id`, not `None`); the second FAILs (`response.asset_id` is
`"PUMP-999"`, the request hint, not `"PUMP-103"`, the checkpointed asset).

- [ ] **Step 6: Add the checkpoint-reading helper to `agent.py`**

In `src/maintenance_agent/api/agent.py`, add this function directly after
`_invoke_agent_graph` (after its closing line, before `_capture_run_event`):

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

- [ ] **Step 7: Use the helper in the route-level except block**

In `query_agent`, change:

```python
    except Exception as exc:
        response = AgentQueryResponse(
            request_id=request_id,
            status="error",
            asset_id=body.asset_id,
            answer=None,
```

to:

```python
    except Exception as exc:
        response = AgentQueryResponse(
            request_id=request_id,
            status="error",
            asset_id=await _asset_id_from_checkpoint(request, request_id),
            answer=None,
```

Leave every other line in the `except` block (including
`message=str(exc)`) unchanged — `error.message` sanitization is Gap 1's
fix, tracked separately.

- [ ] **Step 8: Run the three targeted tests again to verify they pass**

Run: `uv run pytest tests/test_health.py::test_agent_query_maps_unhandled_graph_exception_to_error_response tests/test_health.py::test_agent_query_reports_resolved_asset_id_on_post_resolution_failure -v`

Expected: both PASS.

- [ ] **Step 9: Run the full test file to confirm no other test regressed**

Run: `uv run pytest tests/test_health.py -v`

Expected: all tests PASS.

- [ ] **Step 10: Run the full test suite**

Run: `uv run pytest -q`

Expected: all tests PASS, including
`tests/test_tool_bindings.py::test_consequential_guard_error_reaches_api_unhandled_exception_boundary`
(its `GuardFailingGraph` double only implements `ainvoke`, so it hits the
`except Exception: return None` fallback in `_asset_id_from_checkpoint` and
needs no changes — it doesn't assert on `asset_id` anyway) and
`tests/test_run_event_sink.py::test_unhandled_internal_error_still_emits_one_error_run_event`
(also doesn't assert on `asset_id`).

- [ ] **Step 11: Update the fix doc's status**

In `docs/fixes/phase-4-asset-id-error-path-fix.md`, change the final
`## Status` section from:

```markdown
## Status

Not yet implemented. Implementation plan: see
`docs/superpowers/plans/2026-08-21-route-asset-id-checkpoint-fix.md`.
```

to:

```markdown
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
```

- [ ] **Step 12: Commit**

```bash
git add src/maintenance_agent/api/agent.py tests/test_health.py docs/fixes/phase-4-asset-id-error-path-fix.md
git commit -m "fix: source route-level error asset_id from graph checkpoint, not request hint"
```

## Success Criteria

- [ ] `POST /agent/query`'s route-level catch-all reports `asset_id=None` when the failure occurred before `resolve_asset` ever succeeded (or before the graph was ever invoked).
- [ ] The same handler reports the actually-resolved `asset_id` when `resolve_asset` had already succeeded before the failure, even if the request's `asset_id` hint differs or is absent.
- [ ] No production code path can raise out of `_asset_id_from_checkpoint` — any read failure degrades to `None`.
- [ ] `error.message`/`error.code` behavior is untouched (Gap 1 deferred).
- [ ] Full test suite passes (`uv run pytest -q`).

## Status

Planning complete. Not yet implemented.
