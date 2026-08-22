from __future__ import annotations

import json
from pathlib import Path

import yaml

from maintenance_agent.orchestration.tool_bindings import CANONICAL_TOOL_NAMES

ROOT = Path(__file__).parents[2]
SCENARIOS_PATH = ROOT / "tests" / "golden" / "scenarios.yaml"
ASSETS_PATH = ROOT / "src" / "maintenance_agent" / "db" / "fixtures" / "assets.json"
RAG_MANIFEST_PATH = ROOT / "rag" / "corpus" / "sources" / "manifest.json"
PHASE_7_ERROR_GAP_PATH = ROOT / "docs" / "gaps" / "Phase7-finding-error-path-envelope-gaps.md"
PHASE_7_SERIALIZER_FIX_PATH = ROOT / "docs" / "fixes" / "phase-7-implementation-fix.md"


def test_task_5_freezes_the_eight_golden_scenarios() -> None:
    payload = yaml.safe_load(SCENARIOS_PATH.read_text(encoding="utf-8"))
    scenario_ids = [scenario["id"] for scenario in payload["scenarios"]]

    assert scenario_ids == [
        "GS-01",
        "GS-02",
        "GS-03",
        "GS-04",
        "GS-05",
        "GS-06",
        "GS-07",
        "GS-08",
    ]


def test_task_5_freezes_the_phase_1_asset_surface() -> None:
    assets = json.loads(ASSETS_PATH.read_text(encoding="utf-8"))

    assert [asset["asset_id"] for asset in assets] == [
        "PUMP-101",
        "PUMP-102",
        "PUMP-103",
        "PUMP-104",
    ]


def test_task_5_freezes_the_phase_3_rag_document_surface() -> None:
    manifest = json.loads(RAG_MANIFEST_PATH.read_text(encoding="utf-8"))
    document_ids = [document["document_id"] for document in manifest["documents"]]

    assert document_ids == ["DOC-01", "DOC-02", "DOC-03", "DOC-04", "DOC-05"]
    for document in manifest["documents"]:
        assert (RAG_MANIFEST_PATH.parent / document["path"]).is_file()


def test_task_5_freezes_the_canonical_tool_contract() -> None:
    assert CANONICAL_TOOL_NAMES == (
        "resolve_asset",
        "get_asset_status",
        "get_maintenance_history",
        "search_maintenance_docs",
        "get_plant_policy",
        "create_work_order_draft",
        "submit_work_order",
    )


def test_task_5_phase_7_findings_remain_independently_tracked() -> None:
    error_gap = PHASE_7_ERROR_GAP_PATH.read_text(encoding="utf-8")
    serializer_fix = PHASE_7_SERIALIZER_FIX_PATH.read_text(encoding="utf-8")

    assert "## Status\n\nImplemented." in error_gap
    assert "## Status\n\nImplemented." in serializer_fix
