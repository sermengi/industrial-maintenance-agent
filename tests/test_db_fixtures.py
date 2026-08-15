import json
from pathlib import Path
from typing import Any

from maintenance_agent.db.bootstrap import (
    FIXTURE_SPECS,
    PHASE_1_TABLE_NAMES,
    load_all_fixture_records,
)

FIXTURES_DIR = Path("src/maintenance_agent/db/fixtures")


def load_fixture(name: str) -> list[dict[str, Any]]:
    with (FIXTURES_DIR / name).open() as fixture_file:
        data = json.load(fixture_file)

    assert isinstance(data, list)
    return data


def index_by(records: list[dict[str, Any]], key: str) -> dict[str, dict[str, Any]]:
    return {record[key]: record for record in records}


def test_phase_1_seed_fixture_files_and_counts_match_spec() -> None:
    expected_counts = {
        "assets.json": 4,
        "telemetry_snapshots.json": 4,
        "fault_events.json": 5,
        "maintenance_events.json": 10,
        "observations.json": 2,
        "work_orders.json": 2,
        "fault_taxonomy.json": 4,
        "operating_limits.json": 4,
        "plant_policies.json": 2,
    }

    assert {path.name for path in FIXTURES_DIR.glob("*.json")} == set(expected_counts)

    actual_counts = {
        fixture_name: len(load_fixture(fixture_name)) for fixture_name in expected_counts
    }
    assert actual_counts == expected_counts
    assert sum(actual_counts.values()) == 37


def test_phase_1_bootstrap_loader_validates_all_fixture_records() -> None:
    records_by_table = load_all_fixture_records()

    assert set(records_by_table) == set(PHASE_1_TABLE_NAMES)
    assert sum(len(records) for records in records_by_table.values()) == 37
    assert [spec.table.name for spec in FIXTURE_SPECS] == [
        "assets",
        "fault_taxonomy",
        "operating_limits",
        "plant_policies",
        "telemetry_snapshots",
        "fault_events",
        "maintenance_events",
        "observations",
        "work_orders",
    ]

    telemetry_record = records_by_table["telemetry_snapshots"][0]
    assert str(telemetry_record["vibration_mm_s"]) == "2.1"
    assert telemetry_record["timestamp"].tzinfo is not None


def test_phase_1_seed_fixtures_capture_spec_ground_truth_values() -> None:
    assets = index_by(load_fixture("assets.json"), "asset_id")
    telemetry = index_by(load_fixture("telemetry_snapshots.json"), "asset_id")
    faults = index_by(load_fixture("fault_events.json"), "event_id")
    maintenance = index_by(load_fixture("maintenance_events.json"), "maintenance_id")
    observations = index_by(load_fixture("observations.json"), "observation_id")
    work_orders = index_by(load_fixture("work_orders.json"), "work_order_id")

    assert assets["PUMP-101"] == {
        "asset_id": "PUMP-101",
        "asset_type": "centrifugal_pump",
        "model": "CP-200",
        "location": "Line-A",
        "installation_date": "2022-03-15",
        "status": "operational",
    }
    assert assets["PUMP-104"]["status"] == "maintenance_required"

    assert telemetry["PUMP-102"]["vibration_mm_s"] == 8.1
    assert telemetry["PUMP-103"]["bearing_temperature_c"] == 91
    assert telemetry["PUMP-104"]["discharge_pressure_bar"] == 3.9
    assert telemetry["PUMP-104"]["flow_rate_l_min"] == 61

    assert faults["FE-001"]["fault_code"] == "F101"
    assert faults["FE-001"]["status"] == "active"
    assert [
        fault["event_id"]
        for fault in load_fixture("fault_events.json")
        if fault["asset_id"] == "PUMP-103" and fault["fault_code"] == "F102"
    ] == ["FE-002", "FE-003", "FE-004"]

    assert maintenance["ME-003"]["description"] == (
        "Coupling realigned after elevated vibration was reported."
    )
    assert maintenance["ME-008"]["component"] == "lubrication_system"
    assert observations["OBS-001"]["type"] == "seal_leak"
    assert observations["OBS-002"]["severity"] == "moderate"
    assert work_orders["WO-002"]["approved"] is True


def test_phase_1_seed_fixtures_preserve_operating_limits_and_policy_provenance() -> None:
    operating_limits = index_by(load_fixture("operating_limits.json"), "operating_limit_id")
    policies = index_by(load_fixture("plant_policies.json"), "policy_id")

    assert operating_limits["OL-001"]["rule_text"] == (
        "Normal < 4.5; warning 4.5-7.0; critical > 7.0"
    )
    assert operating_limits["OL-002"]["source_type"] == "manufacturer_reference_adopted"
    assert operating_limits["OL-002"]["normal_max"] == 82
    assert operating_limits["OL-002"]["critical_min"] == 82
    assert (
        "not presented as a literal CP-200 manufacturer specification"
        in (operating_limits["OL-002"]["provenance_note"])
    )
    assert operating_limits["OL-003"]["critical_max"] == 4.0
    assert operating_limits["OL-004"]["critical_max"] == 70

    assert policies["PP-001"]["condition"] == "Same fault occurs >=3 times within 12 months"
    assert policies["PP-002"]["required_action"] == (
        "Human approval is required before final submission"
    )


def test_phase_1_seed_fixture_timestamps_are_utc_and_f104_is_taxonomy_only() -> None:
    timestamped_fixtures = [
        "telemetry_snapshots.json",
        "fault_events.json",
        "observations.json",
    ]
    for fixture_name in timestamped_fixtures:
        for record in load_fixture(fixture_name):
            assert record["timestamp"].endswith("Z")

    fault_events = load_fixture("fault_events.json")
    fault_taxonomy = index_by(load_fixture("fault_taxonomy.json"), "fault_code")

    assert all(fault["fault_code"] != "F104" for fault in fault_events)
    assert fault_taxonomy["F104"]["canonical_name"] == "SEAL_LEAK_DETECTED"
