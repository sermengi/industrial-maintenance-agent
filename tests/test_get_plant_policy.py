from typing import cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from maintenance_agent.db.repositories.records import PlantPolicyRecord
from maintenance_agent.tools.get_plant_policy import GetPlantPolicyResult, get_plant_policy


@pytest.fixture
def policy_records() -> dict[str, list[PlantPolicyRecord]]:
    return {
        "recurring_fault": [
            PlantPolicyRecord(
                policy_id="PP-001",
                type="recurring_fault",
                condition="Same fault occurs >=3 times within 12 months",
                required_action=(
                    "Escalate for root-cause investigation and require human review before "
                    "consequential maintenance action"
                ),
            )
        ],
        "consequential_action": [
            PlantPolicyRecord(
                policy_id="PP-002",
                type="consequential_action",
                condition="Work-order submission changes system state",
                required_action="Human approval is required before final submission",
            )
        ],
    }


@pytest.fixture
def session() -> AsyncSession:
    return cast(AsyncSession, object())


@pytest.fixture
def repository_calls(
    monkeypatch: pytest.MonkeyPatch,
    policy_records: dict[str, list[PlantPolicyRecord]],
) -> list[str]:
    calls: list[str] = []

    async def fake_list_by_type(
        _session: AsyncSession,
        policy_type: str,
    ) -> list[PlantPolicyRecord]:
        calls.append(policy_type)
        return policy_records.get(policy_type, [])

    monkeypatch.setattr(
        "maintenance_agent.tools.get_plant_policy.plant_policies.list_by_type",
        fake_list_by_type,
    )
    return calls


@pytest.mark.asyncio
async def test_get_plant_policy_returns_recurring_fault_policy_verbatim(
    session: AsyncSession,
    repository_calls: list[str],
) -> None:
    result = await get_plant_policy("recurring_fault", session)

    assert isinstance(result, GetPlantPolicyResult)
    assert result.policy_type == "recurring_fault"
    assert [policy.policy_id for policy in result.policies] == ["PP-001"]
    assert result.policies[0].condition == "Same fault occurs >=3 times within 12 months"
    assert result.policies[0].required_action == (
        "Escalate for root-cause investigation and require human review before "
        "consequential maintenance action"
    )
    assert repository_calls == ["recurring_fault"]


@pytest.mark.asyncio
async def test_get_plant_policy_returns_consequential_action_policy_verbatim(
    session: AsyncSession,
    repository_calls: list[str],
) -> None:
    result = await get_plant_policy("consequential_action", session)

    assert result.policy_type == "consequential_action"
    assert [policy.policy_id for policy in result.policies] == ["PP-002"]
    assert result.policies[0].condition == "Work-order submission changes system state"
    assert result.policies[0].required_action == (
        "Human approval is required before final submission"
    )
    assert repository_calls == ["consequential_action"]


@pytest.mark.asyncio
async def test_get_plant_policy_returns_empty_list_for_unknown_type(
    session: AsyncSession,
    repository_calls: list[str],
) -> None:
    result = await get_plant_policy("nonexistent_type", session)

    assert result == GetPlantPolicyResult(policy_type="nonexistent_type", policies=[])
    assert repository_calls == ["nonexistent_type"]
