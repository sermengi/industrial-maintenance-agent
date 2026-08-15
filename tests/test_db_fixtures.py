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


def test_phase_1_seed_fixtures_match_spec_record_by_record() -> None:
    expected_fixtures: dict[str, list[dict[str, Any]]] = {
        "assets.json": [
            {
                "asset_id": "PUMP-101",
                "asset_type": "centrifugal_pump",
                "model": "CP-200",
                "location": "Line-A",
                "installation_date": "2022-03-15",
                "status": "operational",
            },
            {
                "asset_id": "PUMP-102",
                "asset_type": "centrifugal_pump",
                "model": "CP-200",
                "location": "Line-A",
                "installation_date": "2021-11-08",
                "status": "degraded",
            },
            {
                "asset_id": "PUMP-103",
                "asset_type": "centrifugal_pump",
                "model": "CP-200",
                "location": "Line-B",
                "installation_date": "2020-06-20",
                "status": "degraded",
            },
            {
                "asset_id": "PUMP-104",
                "asset_type": "centrifugal_pump",
                "model": "CP-300",
                "location": "Line-B",
                "installation_date": "2023-01-12",
                "status": "maintenance_required",
            },
        ],
        "telemetry_snapshots.json": [
            {
                "snapshot_id": "TS-001",
                "asset_id": "PUMP-101",
                "timestamp": "2026-08-14T09:00:00Z",
                "vibration_mm_s": 2.1,
                "bearing_temperature_c": 54,
                "inlet_pressure_bar": 2.4,
                "discharge_pressure_bar": 6.8,
                "flow_rate_l_min": 98,
            },
            {
                "snapshot_id": "TS-002",
                "asset_id": "PUMP-102",
                "timestamp": "2026-08-14T09:00:00Z",
                "vibration_mm_s": 8.1,
                "bearing_temperature_c": 58,
                "inlet_pressure_bar": 2.3,
                "discharge_pressure_bar": 6.4,
                "flow_rate_l_min": 94,
            },
            {
                "snapshot_id": "TS-003",
                "asset_id": "PUMP-103",
                "timestamp": "2026-08-14T09:00:00Z",
                "vibration_mm_s": 4.2,
                "bearing_temperature_c": 91,
                "inlet_pressure_bar": 2.5,
                "discharge_pressure_bar": 6.6,
                "flow_rate_l_min": 96,
            },
            {
                "snapshot_id": "TS-004",
                "asset_id": "PUMP-104",
                "timestamp": "2026-08-14T09:00:00Z",
                "vibration_mm_s": 2.8,
                "bearing_temperature_c": 61,
                "inlet_pressure_bar": 2.2,
                "discharge_pressure_bar": 3.9,
                "flow_rate_l_min": 61,
            },
        ],
        "fault_events.json": [
            {
                "event_id": "FE-001",
                "asset_id": "PUMP-102",
                "fault_code": "F101",
                "fault_name": "HIGH_VIBRATION",
                "timestamp": "2026-08-14T08:42:00Z",
                "severity": "medium",
                "status": "active",
            },
            {
                "event_id": "FE-002",
                "asset_id": "PUMP-103",
                "fault_code": "F102",
                "fault_name": "HIGH_BEARING_TEMPERATURE",
                "timestamp": "2026-01-14T10:20:00Z",
                "severity": "high",
                "status": "resolved",
            },
            {
                "event_id": "FE-003",
                "asset_id": "PUMP-103",
                "fault_code": "F102",
                "fault_name": "HIGH_BEARING_TEMPERATURE",
                "timestamp": "2026-04-02T14:05:00Z",
                "severity": "high",
                "status": "resolved",
            },
            {
                "event_id": "FE-004",
                "asset_id": "PUMP-103",
                "fault_code": "F102",
                "fault_name": "HIGH_BEARING_TEMPERATURE",
                "timestamp": "2026-08-13T16:40:00Z",
                "severity": "high",
                "status": "active",
            },
            {
                "event_id": "FE-005",
                "asset_id": "PUMP-104",
                "fault_code": "F103",
                "fault_name": "LOW_DISCHARGE_PRESSURE",
                "timestamp": "2026-08-14T08:15:00Z",
                "severity": "medium",
                "status": "active",
            },
        ],
        "maintenance_events.json": [
            {
                "maintenance_id": "ME-001",
                "asset_id": "PUMP-101",
                "date": "2026-02-15",
                "type": "preventive",
                "component": "bearing",
                "description": "Routine bearing inspection completed; no abnormal condition found.",
            },
            {
                "maintenance_id": "ME-002",
                "asset_id": "PUMP-101",
                "date": "2026-05-20",
                "type": "preventive",
                "component": "coupling",
                "description": "Alignment checked and found within plant tolerance.",
            },
            {
                "maintenance_id": "ME-003",
                "asset_id": "PUMP-102",
                "date": "2025-06-10",
                "type": "corrective",
                "component": "coupling",
                "description": "Coupling realigned after elevated vibration was reported.",
            },
            {
                "maintenance_id": "ME-004",
                "asset_id": "PUMP-102",
                "date": "2025-12-18",
                "type": "preventive",
                "component": "bearing",
                "description": "Bearing inspected; condition acceptable.",
            },
            {
                "maintenance_id": "ME-005",
                "asset_id": "PUMP-102",
                "date": "2026-04-05",
                "type": "preventive",
                "component": "lubrication",
                "description": "Bearing lubrication completed during scheduled maintenance.",
            },
            {
                "maintenance_id": "ME-006",
                "asset_id": "PUMP-103",
                "date": "2026-01-15",
                "type": "corrective",
                "component": "bearing",
                "description": "Bearing replaced after high bearing temperature event.",
            },
            {
                "maintenance_id": "ME-007",
                "asset_id": "PUMP-103",
                "date": "2026-04-03",
                "type": "corrective",
                "component": "bearing",
                "description": "Bearing replaced following repeated overheating.",
            },
            {
                "maintenance_id": "ME-008",
                "asset_id": "PUMP-103",
                "date": "2026-06-12",
                "type": "inspection",
                "component": "lubrication_system",
                "description": "Lubrication level checked; no immediate defect identified.",
            },
            {
                "maintenance_id": "ME-009",
                "asset_id": "PUMP-104",
                "date": "2026-03-21",
                "type": "preventive",
                "component": "mechanical_seal",
                "description": "Mechanical seal inspected; minor wear documented.",
            },
            {
                "maintenance_id": "ME-010",
                "asset_id": "PUMP-104",
                "date": "2026-07-05",
                "type": "inspection",
                "component": "discharge_line",
                "description": "Discharge line inspected; no blockage identified.",
            },
        ],
        "observations.json": [
            {
                "observation_id": "OBS-001",
                "asset_id": "PUMP-104",
                "timestamp": "2026-08-14T08:05:00Z",
                "type": "seal_leak",
                "severity": "minor",
                "description": "Minor fluid leakage observed near the mechanical seal.",
                "reported_by": "operator",
            },
            {
                "observation_id": "OBS-002",
                "asset_id": "PUMP-102",
                "timestamp": "2026-08-14T08:35:00Z",
                "type": "abnormal_vibration",
                "severity": "moderate",
                "description": "Operator reported stronger-than-normal vibration during operation.",
                "reported_by": "operator",
            },
        ],
        "work_orders.json": [
            {
                "work_order_id": "WO-001",
                "asset_id": "PUMP-101",
                "issue": "Scheduled coupling alignment inspection",
                "priority": "low",
                "status": "completed",
                "created_at": "2026-05-18",
                "approved": True,
            },
            {
                "work_order_id": "WO-002",
                "asset_id": "PUMP-103",
                "issue": "Investigate repeated bearing overheating",
                "priority": "high",
                "status": "completed",
                "created_at": "2026-04-02",
                "approved": True,
            },
        ],
        "fault_taxonomy.json": [
            {
                "fault_code": "F101",
                "canonical_name": "HIGH_VIBRATION",
                "description": "Plant alert for abnormal pump vibration.",
            },
            {
                "fault_code": "F102",
                "canonical_name": "HIGH_BEARING_TEMPERATURE",
                "description": "Plant alert for abnormal bearing temperature.",
            },
            {
                "fault_code": "F103",
                "canonical_name": "LOW_DISCHARGE_PRESSURE",
                "description": "Plant alert for discharge pressure below operating limit.",
            },
            {
                "fault_code": "F104",
                "canonical_name": "SEAL_LEAK_DETECTED",
                "description": "Plant fault category for confirmed mechanical seal leakage.",
            },
        ],
        "operating_limits.json": [
            {
                "operating_limit_id": "OL-001",
                "model": "CP-200",
                "metric": "vibration_mm_s",
                "unit": "mm/s",
                "normal_min": None,
                "normal_max": 4.5,
                "warning_min": 4.5,
                "warning_max": 7.0,
                "critical_min": 7.0,
                "critical_max": None,
                "rule_text": "Normal < 4.5; warning 4.5-7.0; critical > 7.0",
                "source_type": "synthetic_plant_config",
                "provenance_note": "Synthetic plant operating limit for the debug environment.",
            },
            {
                "operating_limit_id": "OL-002",
                "model": "CP-200",
                "metric": "bearing_temperature_c",
                "unit": "C",
                "normal_min": None,
                "normal_max": 82,
                "warning_min": None,
                "warning_max": None,
                "critical_min": 82,
                "critical_max": None,
                "rule_text": "Normal < 82; high >= 82",
                "source_type": "manufacturer_reference_adopted",
                "provenance_note": (
                    "The 82 C bearing-temperature value is a manufacturer reference adopted "
                    "by the synthetic plant for the debug environment; it is not presented "
                    "as a literal CP-200 manufacturer specification."
                ),
            },
            {
                "operating_limit_id": "OL-003",
                "model": "CP-300",
                "metric": "discharge_pressure_bar",
                "unit": "bar",
                "normal_min": 5.0,
                "normal_max": None,
                "warning_min": 4.0,
                "warning_max": 5.0,
                "critical_min": None,
                "critical_max": 4.0,
                "rule_text": "Normal >= 5.0; warning 4.0-<5.0; critical < 4.0",
                "source_type": "synthetic_plant_config",
                "provenance_note": "Synthetic plant operating limit for the debug environment.",
            },
            {
                "operating_limit_id": "OL-004",
                "model": "CP-300",
                "metric": "flow_rate_l_min",
                "unit": "L/min",
                "normal_min": 85,
                "normal_max": None,
                "warning_min": 70,
                "warning_max": 85,
                "critical_min": None,
                "critical_max": 70,
                "rule_text": "Normal >= 85; warning 70-<85; low < 70",
                "source_type": "synthetic_plant_config",
                "provenance_note": "Synthetic plant operating limit for the debug environment.",
            },
        ],
        "plant_policies.json": [
            {
                "policy_id": "PP-001",
                "type": "recurring_fault",
                "condition": "Same fault occurs >=3 times within 12 months",
                "required_action": (
                    "Escalate for root-cause investigation and require human review before "
                    "consequential maintenance action"
                ),
            },
            {
                "policy_id": "PP-002",
                "type": "consequential_action",
                "condition": "Work-order submission changes system state",
                "required_action": "Human approval is required before final submission",
            },
        ],
    }

    for fixture_name, expected_records in expected_fixtures.items():
        assert load_fixture(fixture_name) == expected_records


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
