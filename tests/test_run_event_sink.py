from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from maintenance_agent.api import agent as agent_api
from maintenance_agent.api.agent import router as agent_router
from maintenance_agent.core.config import Settings, get_settings
from maintenance_agent.schemas.agent import AgentQueryResponse
from maintenance_agent.schemas.run_event import RunEvent
from maintenance_agent.telemetry.run_events import make_jsonl_emitter, read_run_events


@pytest.mark.asyncio
async def test_jsonl_emitter_creates_parent_directory_and_appends_events(tmp_path: Path) -> None:
    path = tmp_path / "missing" / "events.jsonl"
    emit = make_jsonl_emitter(path)

    await emit(_run_event("event-1"))
    await emit(_run_event("event-2"))

    assert path.exists()
    assert len(path.read_text(encoding="utf-8").splitlines()) == 2
    assert [event.run_id for event in read_run_events(path)] == ["event-1", "event-2"]


@pytest.mark.asyncio
async def test_jsonl_emitter_raises_write_failures(tmp_path: Path) -> None:
    path = tmp_path / "events-as-directory"
    path.mkdir()
    emit = make_jsonl_emitter(path)

    with pytest.raises(OSError):
        await emit(_run_event("event-1"))


def test_read_run_events_validates_each_jsonl_line(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    event = _run_event("event-1")
    path.write_text(event.model_dump_json() + "\n", encoding="utf-8")

    assert read_run_events(path) == [event]


def test_settings_include_run_events_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    get_settings.cache_clear()
    configured_path = tmp_path / "configured" / "events.jsonl"
    monkeypatch.delenv("RUN_EVENTS_PATH", raising=False)

    try:
        assert Settings(RUN_EVENTS_PATH=str(configured_path)).run_events_path == configured_path
        assert get_settings().run_events_path.parent == Path("run-events")
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_agent_query_writes_run_event_to_real_jsonl_sink(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "events" / "run-events.jsonl"
    app = FastAPI()
    app.state.agent_graph = _FakeGraph()
    app.state.emit_run_event = make_jsonl_emitter(path)
    app.state.run_event_clock = _SequenceClock(
        [
            datetime(2026, 8, 21, 10, 0, 0, tzinfo=UTC),
            datetime(2026, 8, 21, 10, 0, 0, 250000, tzinfo=UTC),
        ]
    )
    app.include_router(agent_router, prefix="/agent")
    monkeypatch.setattr(agent_api, "_request_session", _fake_session_context)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post("/agent/query", json={"query": "Check pump vibration."})

    assert response.status_code == 200
    events = read_run_events(path)
    assert len(events) == 1
    assert events[0].request == "Check pump vibration."
    assert events[0].status == "ok"
    assert events[0].latency_ms == 250
    assert events[0].final_output == AgentQueryResponse.model_validate(response.json())


def _run_event(run_id: str) -> RunEvent:
    return RunEvent(
        event_id=UUID("11111111-1111-4111-8111-111111111111"),
        run_id=run_id,
        emitted_at=datetime(2026, 8, 21, 10, 0, tzinfo=UTC),
        latency_ms=10,
        status="ok",
        request="Check PUMP-103.",
        tool_calls=[],
        final_output=AgentQueryResponse(request_id=run_id, status="ok"),
        error=None,
    )


class _FakeGraph:
    def __init__(self) -> None:
        self.state: dict[str, Any] | None = None

    async def ainvoke(
        self,
        state: dict[str, Any],
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        del config
        self.state = state
        return {
            "response": AgentQueryResponse(
                request_id=state["request_id"],
                status="ok",
                asset_id="PUMP-103",
                answer="Inspect the bearing and follow the maintenance procedure.",
                confidence="confirmed",
            )
        }

    def get_state(self, config: dict[str, Any]) -> Any:
        del config
        return SimpleNamespace(next=(), values=self.state)


class _SequenceClock:
    def __init__(self, values: list[datetime]) -> None:
        self._values = values

    def __call__(self) -> datetime:
        if not self._values:
            raise AssertionError("Unexpected clock call.")
        return self._values.pop(0)


@asynccontextmanager
async def _fake_session_context() -> AsyncGenerator[AsyncSession]:
    yield cast(AsyncSession, object())
