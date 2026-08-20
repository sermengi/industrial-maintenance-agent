from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from maintenance_agent.db.repositories import fault_events
from maintenance_agent.db.repositories.records import FaultEventRecord

RECURRENCE_WINDOW_MONTHS = 12
RECURRENCE_THRESHOLD = 3


class FaultRecurrence(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_type: Literal["fault_event"] = "fault_event"
    source_id: str
    fault_code: str
    total_occurrences: int
    occurrences_within_window: int
    window_months: int = RECURRENCE_WINDOW_MONTHS
    meets_recurrence_threshold: bool


async def compute_fault_recurrence(
    asset_id: str,
    reference_fault_events: list[FaultEventRecord],
    session: AsyncSession,
) -> list[FaultRecurrence]:
    if not reference_fault_events:
        return []

    reference_time = max(event.timestamp for event in reference_fault_events)
    window_start = _subtract_months(reference_time, RECURRENCE_WINDOW_MONTHS)
    recurrence: list[FaultRecurrence] = []

    for fault_code in _distinct_fault_codes(reference_fault_events):
        events_for_code = await fault_events.list_by_asset_and_code(session, asset_id, fault_code)
        occurrences_within_window = sum(
            1 for event in events_for_code if event.timestamp >= window_start
        )
        recurrence.append(
            FaultRecurrence(
                source_id=max(events_for_code, key=lambda event: event.timestamp).event_id,
                fault_code=fault_code,
                total_occurrences=len(events_for_code),
                occurrences_within_window=occurrences_within_window,
                meets_recurrence_threshold=occurrences_within_window >= RECURRENCE_THRESHOLD,
            )
        )

    return recurrence


def _distinct_fault_codes(fault_events_for_asset: list[FaultEventRecord]) -> list[str]:
    return list(dict.fromkeys(event.fault_code for event in fault_events_for_asset))


def _subtract_months(value: datetime, months: int) -> datetime:
    month_index = value.year * 12 + value.month - 1 - months
    year = month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, _days_in_month(year, month))
    return value.replace(year=year, month=month, day=day)


def _days_in_month(year: int, month: int) -> int:
    if month == 2:
        if year % 400 == 0 or (year % 4 == 0 and year % 100 != 0):
            return 29
        return 28
    if month in {4, 6, 9, 11}:
        return 30
    return 31
