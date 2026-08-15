from datetime import date
from typing import cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from maintenance_agent.db.repositories.records import AssetRecord
from maintenance_agent.tools.resolve_asset import ResolveAssetResult, resolve_asset


@pytest.fixture
def asset_records() -> dict[str, AssetRecord]:
    return {
        "PUMP-101": AssetRecord(
            asset_id="PUMP-101",
            asset_type="centrifugal_pump",
            model="CP-200",
            location="Building A / Line 1",
            installation_date=date(2021, 3, 15),
            status="operational",
        ),
        "PUMP-102": AssetRecord(
            asset_id="PUMP-102",
            asset_type="centrifugal_pump",
            model="CP-200",
            location="Building A / Line 2",
            installation_date=date(2021, 4, 10),
            status="warning",
        ),
        "PUMP-103": AssetRecord(
            asset_id="PUMP-103",
            asset_type="centrifugal_pump",
            model="CP-200",
            location="Building B / Line 1",
            installation_date=date(2022, 1, 20),
            status="fault_active",
        ),
        "PUMP-104": AssetRecord(
            asset_id="PUMP-104",
            asset_type="centrifugal_pump",
            model="CP-300",
            location="Building B / Line 2",
            installation_date=date(2023, 6, 5),
            status="maintenance_required",
        ),
    }


@pytest.fixture
def repository_calls(
    monkeypatch: pytest.MonkeyPatch,
    asset_records: dict[str, AssetRecord],
) -> list[str]:
    calls: list[str] = []

    async def fake_get_by_id(_session: AsyncSession, asset_id: str) -> AssetRecord | None:
        calls.append(asset_id)
        return asset_records.get(asset_id)

    monkeypatch.setattr("maintenance_agent.tools.resolve_asset.assets.get_by_id", fake_get_by_id)
    return calls


@pytest.fixture
def session() -> AsyncSession:
    return cast(AsyncSession, object())


@pytest.mark.asyncio
async def test_resolve_asset_returns_asset_records_for_known_assets(
    session: AsyncSession,
    repository_calls: list[str],
    asset_records: dict[str, AssetRecord],
) -> None:
    for asset_id, expected_asset in asset_records.items():
        result = await resolve_asset(asset_id, session)

        assert result == ResolveAssetResult(status="resolved", asset=expected_asset)

    assert repository_calls == ["PUMP-101", "PUMP-102", "PUMP-103", "PUMP-104"]


@pytest.mark.asyncio
async def test_resolve_asset_normalizes_case_and_whitespace(
    session: AsyncSession,
    repository_calls: list[str],
) -> None:
    pump_102 = await resolve_asset(" pump-102 ", session)
    pump_103 = await resolve_asset("Pump-103", session)

    assert pump_102.status == "resolved"
    assert pump_102.asset is not None
    assert pump_102.asset.asset_id == "PUMP-102"
    assert pump_103.status == "resolved"
    assert pump_103.asset is not None
    assert pump_103.asset.asset_id == "PUMP-103"
    assert repository_calls == ["PUMP-102", "PUMP-103"]


@pytest.mark.asyncio
async def test_resolve_asset_returns_not_found_for_unknown_and_malformed_identifiers(
    session: AsyncSession,
    repository_calls: list[str],
) -> None:
    for identifier in ["PUMP-999", "PUMP102", "102"]:
        result = await resolve_asset(identifier, session)

        assert result == ResolveAssetResult(status="not_found", asset=None)

    assert repository_calls == ["PUMP-999", "PUMP102", "102"]


@pytest.mark.asyncio
async def test_resolve_asset_returns_not_found_for_missing_identifier_without_querying(
    session: AsyncSession,
    repository_calls: list[str],
) -> None:
    assert await resolve_asset("", session) == ResolveAssetResult(status="not_found")
    assert await resolve_asset(None, session) == ResolveAssetResult(status="not_found")  # type: ignore[arg-type]
    assert repository_calls == []
