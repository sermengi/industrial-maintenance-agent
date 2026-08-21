from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from maintenance_agent.schemas.run_event import RunEvent

logger = logging.getLogger(__name__)

EmitFn = Callable[[RunEvent], Awaitable[None]]


async def noop_emit_run_event(event: RunEvent) -> None:
    del event


async def record_run_event(emit: EmitFn, event: RunEvent) -> None:
    try:
        await emit(event)
    except Exception:
        logger.warning("Failed to emit run event.", exc_info=True)
