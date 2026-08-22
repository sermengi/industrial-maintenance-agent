from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import yaml

ROOT = Path(__file__).parents[2]
CI_WORKFLOW_PATH = ROOT / ".github" / "workflows" / "ci.yml"
GOLDEN_WORKFLOW_PATH = ROOT / ".github" / "workflows" / "golden-scenarios.yml"


def test_task_6_existing_push_pr_ci_workflow_is_unchanged() -> None:
    workflow = _load_workflow(CI_WORKFLOW_PATH)

    assert workflow["on"] == {
        "push": {"branches": ["main"]},
        "pull_request": "",
    }
    assert list(workflow["jobs"]) == ["test"]
    assert "ANTHROPIC_API_KEY" not in CI_WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "RUN_GOLDEN_SCENARIOS" not in CI_WORKFLOW_PATH.read_text(encoding="utf-8")


def test_task_6_golden_workflow_is_scheduled_and_manually_dispatchable_only() -> None:
    workflow = _load_workflow(GOLDEN_WORKFLOW_PATH)
    triggers = cast(dict[str, Any], workflow["on"])

    assert set(triggers) == {"schedule", "workflow_dispatch"}
    assert triggers["schedule"] == [{"cron": "17 3 * * *"}]
    assert "push" not in triggers
    assert "pull_request" not in triggers


def test_task_6_anthropic_secret_is_scoped_to_golden_jobs_only() -> None:
    workflow = _load_workflow(GOLDEN_WORKFLOW_PATH)
    jobs = cast(dict[str, Any], workflow["jobs"])

    assert "ANTHROPIC_API_KEY" not in CI_WORKFLOW_PATH.read_text(encoding="utf-8")
    assert {
        job_name: job["env"]["ANTHROPIC_API_KEY"] for job_name, job in jobs.items()
    } == {
        "golden-asgi": "${{ secrets.ANTHROPIC_API_KEY }}",
        "golden-container-variant": "${{ secrets.ANTHROPIC_API_KEY }}",
    }


def test_task_6_golden_workflow_has_no_retry_wrapper_around_pytest() -> None:
    workflow = _load_workflow(GOLDEN_WORKFLOW_PATH)
    steps = _job_steps(workflow, "golden-asgi")
    pytest_steps = [step for step in steps if step.get("run", "").startswith("uv run pytest")]
    workflow_text = GOLDEN_WORKFLOW_PATH.read_text(encoding="utf-8")

    assert [step["run"] for step in pytest_steps] == ["uv run pytest tests/golden -q"]
    assert "continue-on-error" not in workflow_text
    assert "pytest-rerunfailures" not in workflow_text
    assert "--reruns" not in workflow_text
    assert "nick-fields/retry" not in workflow_text


def test_task_6_golden_workflow_uploads_manual_review_report() -> None:
    workflow = _load_workflow(GOLDEN_WORKFLOW_PATH)
    jobs = cast(dict[str, Any], workflow["jobs"])

    for job in jobs.values():
        artifact_steps = [
            step
            for step in job["steps"]
            if step.get("uses") == "actions/upload-artifact@v4"
        ]
        assert len(artifact_steps) == 1
        assert artifact_steps[0]["if"] == "always()"
        assert artifact_steps[0]["with"]["path"] == "tests/golden/manual_review_report.md"


def test_task_6_container_variant_is_workflow_dispatch_only() -> None:
    workflow = _load_workflow(GOLDEN_WORKFLOW_PATH)
    container_job = workflow["jobs"]["golden-container-variant"]

    assert (
        container_job["if"]
        == "github.event_name == 'workflow_dispatch' && inputs.run_container_variant == 'true'"
    )
    assert "docker compose up --build --detach --wait" in [
        step.get("run") for step in container_job["steps"]
    ]
    assert container_job["env"]["GOLDEN_API_BASE_URL"] == "http://127.0.0.1:8000"
    assert container_job["env"]["GOLDEN_RUN_EVENTS_PATH"] == "run-events/golden-events.jsonl"
    assert container_job["env"]["RUN_EVENTS_PATH"] == "/app/run-events/golden-events.jsonl"


def test_task_6_compose_api_exposes_run_events_for_live_http_assertions() -> None:
    compose = _load_workflow(ROOT / "docker-compose.yml")
    api_service = compose["services"]["api"]

    assert api_service["environment"]["RUN_EVENTS_PATH"] == (
        "${RUN_EVENTS_PATH:-run-events/run-events.jsonl}"
    )
    assert api_service["volumes"] == ["${RUN_EVENTS_DIR:-./run-events}:/app/run-events"]


def _load_workflow(path: Path) -> dict[str, Any]:
    return cast(dict[str, Any], yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader))


def _job_steps(workflow: dict[str, Any], job_name: str) -> list[dict[str, Any]]:
    return cast(list[dict[str, Any]], workflow["jobs"][job_name]["steps"])
