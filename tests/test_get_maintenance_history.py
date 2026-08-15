from datetime import UTC, date, datetime
from typing import cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from maintenance_agent.db.repositories.records import (
    AssetRecord,
    FaultEventRecord,
    MaintenanceEventRecord,
    WorkOrderRecord,
)
from maintenance_agent.tools.get_maintenance_history import (
    FaultRecurrence,
    GetMaintenanceHistoryResult,
    get_maintenance_history,
)


@pytest.fixture
def asset_records() -> dict[str, AssetRecord]:
    return {
        "PUMP-101": _asset("PUMP-101", "CP-200", "Building A / Line 1", "operational"),
        "PUMP-102": _asset("PUMP-102", "CP-200", "Building A / Line 2", "warning"),
        "PUMP-103": _asset("PUMP-103", "CP-200", "Building B / Line 1", "fault_active"),
        "PUMP-104": _asset("PUMP-104", "CP-300", "Building B / Line 2", "maintenance_required"),
        "PUMP-TIME": _asset("PUMP-TIME", "CP-200", "Test Area", "fault_active"),
    }


@pytest.fixture
def maintenance_records() -> dict[str, list[MaintenanceEventRecord]]:
    return {
        "PUMP-101": [
            _maintenance("ME-001", "PUMP-101", date(2026, 2, 15), "preventive", "bearing"),
            _maintenance("ME-002", "PUMP-101", date(2026, 5, 20), "preventive", "coupling"),
        ],
        "PUMP-102": [
            _maintenance("ME-003", "PUMP-102", date(2025, 6, 10), "corrective", "coupling"),
            _maintenance("ME-004", "PUMP-102", date(2025, 12, 18), "preventive", "bearing"),
            _maintenance("ME-005", "PUMP-102", date(2026, 4, 5), "preventive", "lubrication"),
        ],
        "PUMP-103": [
            _maintenance("ME-006", "PUMP-103", date(2026, 1, 15), "corrective", "bearing"),
            _maintenance("ME-007", "PUMP-103", date(2026, 4, 3), "corrective", "bearing"),
            _maintenance(
                "ME-008",
                "PUMP-103",
                date(2026, 6, 12),
                "inspection",
                "lubrication_system",
            ),
        ],
        "PUMP-104": [
            _maintenance("ME-009", "PUMP-104", date(2026, 3, 21), "preventive", "mechanical_seal"),
            _maintenance("ME-010", "PUMP-104", date(2026, 7, 5), "inspection", "discharge_line"),
        ],
        "PUMP-TIME": [],
    }


@pytest.fixture
def fault_records() -> dict[str, list[FaultEventRecord]]:
    return {
        "PUMP-101": [],
        "PUMP-102": [
            _fault(
                "FE-001",
                "PUMP-102",
                "F101",
                "HIGH_VIBRATION",
                datetime(2026, 8, 14, 8, 42, tzinfo=UTC),
                "medium",
                "active",
            )
        ],
        "PUMP-103": [
            _fault(
                "FE-002",
                "PUMP-103",
                "F102",
                "HIGH_BEARING_TEMPERATURE",
                datetime(2026, 1, 14, 10, 20, tzinfo=UTC),
                "high",
                "resolved",
            ),
            _fault(
                "FE-003",
                "PUMP-103",
                "F102",
                "HIGH_BEARING_TEMPERATURE",
                datetime(2026, 4, 2, 14, 5, tzinfo=UTC),
                "high",
                "resolved",
            ),
            _fault(
                "FE-004",
                "PUMP-103",
                "F102",
                "HIGH_BEARING_TEMPERATURE",
                datetime(2026, 8, 13, 16, 40, tzinfo=UTC),
                "high",
                "active",
            ),
        ],
        "PUMP-104": [
            _fault(
                "FE-005",
                "PUMP-104",
                "F103",
                "LOW_DISCHARGE_PRESSURE",
                datetime(2026, 8, 14, 8, 15, tzinfo=UTC),
                "medium",
                "active",
            )
        ],
        "PUMP-TIME": [
            _fault(
                "FE-T01",
                "PUMP-TIME",
                "F900",
                "TIME_TEST",
                datetime(2020, 1, 1, tzinfo=UTC),
                "low",
                "resolved",
            ),
            _fault(
                "FE-T02",
                "PUMP-TIME",
                "F900",
                "TIME_TEST",
                datetime(2020, 6, 1, tzinfo=UTC),
                "low",
                "resolved",
            ),
            _fault(
                "FE-T03",
                "PUMP-TIME",
                "F900",
                "TIME_TEST",
                datetime(2020, 12, 1, tzinfo=UTC),
                "low",
                "active",
            ),
        ],
    }


@pytest.fixture
def work_order_records() -> dict[str, list[WorkOrderRecord]]:
    return {
        "PUMP-101": [_work_order("WO-001", "PUMP-101", "low", date(2026, 5, 18))],
        "PUMP-102": [],
        "PUMP-103": [_work_order("WO-002", "PUMP-103", "high", date(2026, 4, 2))],
        "PUMP-104": [],
        "PUMP-TIME": [],
    }


@pytest.fixture
def session() -> AsyncSession:
    return cast(AsyncSession, object())


@pytest.fixture
def repository_calls(
    monkeypatch: pytest.MonkeyPatch,
    maintenance_records: dict[str, list[MaintenanceEventRecord]],
    fault_records: dict[str, list[FaultEventRecord]],
    work_order_records: dict[str, list[WorkOrderRecord]],
) -> list[tuple[str, str, str | None]]:
    calls: list[tuple[str, str, str | None]] = []

    async def fake_list_maintenance_for_asset(
        _session: AsyncSession,
        asset_id: str,
    ) -> list[MaintenanceEventRecord]:
        calls.append(("maintenance", asset_id, None))
        return maintenance_records[asset_id]

    async def fake_list_faults_for_asset(
        _session: AsyncSession,
        asset_id: str,
    ) -> list[FaultEventRecord]:
        calls.append(("faults", asset_id, None))
        return fault_records[asset_id]

    async def fake_list_faults_by_asset_and_code(
        _session: AsyncSession,
        asset_id: str,
        fault_code: str,
    ) -> list[FaultEventRecord]:
        calls.append(("faults_by_code", asset_id, fault_code))
        return [fault for fault in fault_records[asset_id] if fault.fault_code == fault_code]

    async def fake_list_work_orders_for_asset(
        _session: AsyncSession,
        asset_id: str,
    ) -> list[WorkOrderRecord]:
        calls.append(("work_orders", asset_id, None))
        return work_order_records[asset_id]

    monkeypatch.setattr(
        "maintenance_agent.tools.get_maintenance_history.maintenance_events.list_for_asset",
        fake_list_maintenance_for_asset,
    )
    monkeypatch.setattr(
        "maintenance_agent.tools.get_maintenance_history.fault_events.list_for_asset",
        fake_list_faults_for_asset,
    )
    monkeypatch.setattr(
        "maintenance_agent.tools.get_maintenance_history.fault_events.list_by_asset_and_code",
        fake_list_faults_by_asset_and_code,
    )
    monkeypatch.setattr(
        "maintenance_agent.tools.get_maintenance_history.work_orders.list_for_asset",
        fake_list_work_orders_for_asset,
    )

    return calls


@pytest.mark.asyncio
async def test_get_maintenance_history_returns_pump_101_history_without_recurrence(
    session: AsyncSession,
    repository_calls: list[tuple[str, str, str | None]],
    asset_records: dict[str, AssetRecord],
) -> None:
    result = await get_maintenance_history(asset_records["PUMP-101"], session)

    assert isinstance(result, GetMaintenanceHistoryResult)
    assert result.asset == asset_records["PUMP-101"]
    assert [event.maintenance_id for event in result.maintenance_events] == ["ME-001", "ME-002"]
    assert result.fault_events == []
    assert [order.work_order_id for order in result.work_orders] == ["WO-001"]
    assert result.recurrence == []
    assert ("faults_by_code", "PUMP-101", "F101") not in repository_calls


@pytest.mark.asyncio
async def test_get_maintenance_history_returns_single_fault_nonrecurring_context(
    session: AsyncSession,
    repository_calls: list[tuple[str, str, str | None]],
    asset_records: dict[str, AssetRecord],
) -> None:
    result = await get_maintenance_history(asset_records["PUMP-102"], session)

    assert [event.maintenance_id for event in result.maintenance_events] == [
        "ME-003",
        "ME-004",
        "ME-005",
    ]
    assert [fault.event_id for fault in result.fault_events] == ["FE-001"]
    assert result.work_orders == []
    assert result.recurrence == [
        FaultRecurrence(
            fault_code="F101",
            total_occurrences=1,
            occurrences_within_window=1,
            meets_recurrence_threshold=False,
        )
    ]
    assert ("faults_by_code", "PUMP-102", "F101") in repository_calls


@pytest.mark.asyncio
async def test_get_maintenance_history_counts_pump_103_recurring_fault(
    session: AsyncSession,
    repository_calls: list[tuple[str, str, str | None]],
    asset_records: dict[str, AssetRecord],
) -> None:
    result = await get_maintenance_history(asset_records["PUMP-103"], session)

    assert [fault.event_id for fault in result.fault_events] == ["FE-002", "FE-003", "FE-004"]
    assert [fault.status for fault in result.fault_events] == ["resolved", "resolved", "active"]
    assert [event.maintenance_id for event in result.maintenance_events] == [
        "ME-006",
        "ME-007",
        "ME-008",
    ]
    assert [order.work_order_id for order in result.work_orders] == ["WO-002"]
    assert result.recurrence == [
        FaultRecurrence(
            fault_code="F102",
            total_occurrences=3,
            occurrences_within_window=3,
            meets_recurrence_threshold=True,
        )
    ]
    assert ("faults_by_code", "PUMP-103", "F102") in repository_calls


@pytest.mark.asyncio
async def test_get_maintenance_history_returns_pump_104_history_without_fabricated_faults(
    session: AsyncSession,
    repository_calls: list[tuple[str, str, str | None]],
    asset_records: dict[str, AssetRecord],
) -> None:
    result = await get_maintenance_history(asset_records["PUMP-104"], session)

    assert [fault.event_id for fault in result.fault_events] == ["FE-005"]
    assert all(fault.fault_code != "F104" for fault in result.fault_events)
    assert [event.maintenance_id for event in result.maintenance_events] == ["ME-009", "ME-010"]
    assert result.work_orders == []
    assert result.recurrence == [
        FaultRecurrence(
            fault_code="F103",
            total_occurrences=1,
            occurrences_within_window=1,
            meets_recurrence_threshold=False,
        )
    ]
    assert ("faults_by_code", "PUMP-104", "F103") in repository_calls


@pytest.mark.asyncio
async def test_recurrence_window_is_anchored_to_asset_latest_fault_not_wall_clock(
    session: AsyncSession,
    repository_calls: list[tuple[str, str, str | None]],
    asset_records: dict[str, AssetRecord],
) -> None:
    result = await get_maintenance_history(asset_records["PUMP-TIME"], session)

    assert result.recurrence == [
        FaultRecurrence(
            fault_code="F900",
            total_occurrences=3,
            occurrences_within_window=3,
            meets_recurrence_threshold=True,
        )
    ]
    assert ("faults_by_code", "PUMP-TIME", "F900") in repository_calls


def _asset(asset_id: str, model: str, location: str, status: str) -> AssetRecord:
    return AssetRecord(
        asset_id=asset_id,
        asset_type="centrifugal_pump",
        model=model,
        location=location,
        installation_date=date(2021, 1, 1),
        status=status,
    )


def _maintenance(
    maintenance_id: str,
    asset_id: str,
    maintenance_date: date,
    maintenance_type: str,
    component: str,
) -> MaintenanceEventRecord:
    return MaintenanceEventRecord(
        maintenance_id=maintenance_id,
        asset_id=asset_id,
        date=maintenance_date,
        type=maintenance_type,
        component=component,
        description="Maintenance description.",
    )


def _fault(
    event_id: str,
    asset_id: str,
    fault_code: str,
    fault_name: str,
    timestamp: datetime,
    severity: str,
    status: str,
) -> FaultEventRecord:
    return FaultEventRecord(
        event_id=event_id,
        asset_id=asset_id,
        fault_code=fault_code,
        fault_name=fault_name,
        timestamp=timestamp,
        severity=severity,
        status=status,
    )


def _work_order(
    work_order_id: str,
    asset_id: str,
    priority: str,
    created_at: date,
) -> WorkOrderRecord:
    return WorkOrderRecord(
        work_order_id=work_order_id,
        asset_id=asset_id,
        issue="Work order issue.",
        priority=priority,
        status="completed",
        created_at=created_at,
        approved=True,
    )
