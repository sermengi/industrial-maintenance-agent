from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from pathlib import Path

from maintenance_agent.schemas.run_event import RunEvent

logger = logging.getLogger(__name__)

EmitFn = Callable[[RunEvent], Awaitable[None]]


async def noop_emit_run_event(event: RunEvent) -> None:
    del event


def make_jsonl_emitter(path: Path) -> EmitFn:
    path.parent.mkdir(parents=True, exist_ok=True)

    async def emit(event: RunEvent) -> None:
        with path.open("a", encoding="utf-8") as file:
            file.write(event.model_dump_json() + "\n")

    return emit


def read_run_events(path: Path) -> list[RunEvent]:
    return [
        RunEvent.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


async def record_run_event(emit: EmitFn, event: RunEvent) -> None:
    try:
        await emit(event)
    except Exception:
        logger.warning("Failed to emit run event.", exc_info=True)
