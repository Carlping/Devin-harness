from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
import yaml

from devin_fanout.__main__ import main
from devin_fanout.client import CreatedSession, MockTransport, SessionState, V1Transport
from devin_fanout.contract import DEFAULT_STRUCTURED_OUTPUT_SCHEMA
from devin_fanout.metrics import percentile, summarize
from devin_fanout.report import MOCK_BANNER, render_markdown
from devin_fanout.runner import TaskResult, build_payload, run, validate_output
from devin_fanout.spec import SpecError, load_spec, render_prompt

ROOT = Path(__file__).parents[1]
DEMO_SPEC = ROOT / "examples" / "dependency-bump.yaml"
DEMO_SCENARIOS = ROOT / "examples" / "scenarios" / "dependency-bump.yaml"


def write_spec(
    tmp_path: Path,
    *,
    spec_updates: dict[str, Any] | None = None,
    tasks: Any = None,
    template: str = (
        "Migrate {{ path }} from {{ old_api }} to {{ new_api }} with {{ verify_command }}."
    ),
) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "prompt.md").write_text(template, encoding="utf-8")
    task_data = (
        tasks
        if tasks is not None
        else [
            {
                "id": "task-one",
                "vars": {
                    "path": "src",
                    "old_api": "old",
                    "new_api": "new",
                    "verify_command": "pytest",
                },
            }
        ]
    )
    (tmp_path / "tasks.yaml").write_text(yaml.safe_dump(task_data), encoding="utf-8")
    spec_data: dict[str, Any] = {
        "run_id": "test-run",
        "prompt_template": "prompt.md",
        "tasks": "tasks.yaml",
    }
    spec_data.update(spec_updates or {})
    path = tmp_path / "spec.yaml"
    path.write_text(yaml.safe_dump(spec_data), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("spec_updates", "tasks", "match"),
    [
        ({"unexpected": True}, None, "unknown keys"),
        ({"run_id": "bad id"}, None, "run_id"),
        ({"run_id": "../bad"}, None, "run_id"),
        ({"prompt_template": None}, None, "missing required keys"),
        ({"poll_interval_seconds": 4}, None, "poll_interval"),
        ({"devin_mode": "unknown"}, None, "devin_mode"),
    ],
)
def test_spec_rejects_invalid_top_level_values(
    tmp_path: Path,
    spec_updates: dict[str, Any],
    tasks: Any,
    match: str,
) -> None:
    updates = dict(spec_updates)
    if "prompt_template" not in updates:
        updates = {key: value for key, value in updates.items() if value is not None}
    path = write_spec(tmp_path, spec_updates=updates, tasks=tasks)
    if spec_updates == {"prompt_template": None}:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        del data["prompt_template"]
        path.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(SpecError, match=match):
        load_spec(path)


@pytest.mark.parametrize(
    ("tasks", "match"),
    [
        (
            [
                {
                    "id": "same",
                    "vars": {"path": "a", "old_api": "o", "new_api": "n", "verify_command": "v"},
                },
                {
                    "id": "same",
                    "vars": {"path": "b", "old_api": "o", "new_api": "n", "verify_command": "v"},
                },
            ],
            "duplicate",
        ),
        (
            [{"id": "missing", "vars": {"path": "a", "old_api": "o", "new_api": "n"}}],
            "missing prompt",
        ),
        (
            [
                {
                    "id": "extra",
                    "vars": {
                        "path": "a",
                        "old_api": "o",
                        "new_api": "n",
                        "verify_command": "v",
                        "unused": "x",
                    },
                }
            ],
            "never uses",
        ),
        ({"id": "not-a-list"}, "non-empty YAML list"),
    ],
)
def test_spec_rejects_invalid_task_files(tmp_path: Path, tasks: Any, match: str) -> None:
    path = write_spec(tmp_path, tasks=tasks)
    with pytest.raises(SpecError, match=match):
        load_spec(path)


def test_render_prompt_substitutes_variables() -> None:
    assert render_prompt(
        "Use {{ old }} in {{ path }}.", {"old": "requests", "path": "src"}, "demo"
    ) == ("Use requests in src.")


def valid_output() -> dict[str, Any]:
    return {
        "outcome": "completed",
        "summary": "Changed the code and verified the result.",
        "verification": {"command": "pytest -q", "passed": True},
        "pr_url": "https://github.com/example/repo/pull/1",
        "human_action_required": "",
        "files_changed": 1,
    }


@pytest.mark.parametrize(
    "output",
    [
        {key: value for key, value in valid_output().items() if key != "summary"},
        {**valid_output(), "outcome": "finished"},
        {**valid_output(), "extra": True},
        None,
    ],
)
def test_validate_output_rejects_invalid_payloads(output: Any) -> None:
    errors = validate_output(DEFAULT_STRUCTURED_OUTPUT_SCHEMA, output)
    assert errors


def test_validate_output_accepts_valid_payload() -> None:
    assert validate_output(DEFAULT_STRUCTURED_OUTPUT_SCHEMA, valid_output()) == ()


def test_build_payload_includes_run_and_task_tags() -> None:
    spec = load_spec(DEMO_SPEC)
    payload = build_payload(spec, spec.tasks[0])
    assert payload["tags"][:2] == ["fanout-run:dependency-bump-demo", "fanout-task:auth-service"]
    assert payload["structured_output_required"] is True
    assert payload["resumable"] is False
    assert payload["repos"] == ["github.com/example-org/example-monorepo"]
    assert payload["devin_mode"] == "fast"
    assert payload["max_acu_limit"] == 10


class FakeResponse:
    def __init__(self, payload: dict[str, Any], status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code
        self.text = ""

    def json(self) -> dict[str, Any]:
        return self.payload


class FakeSession:
    def __init__(self, *responses: FakeResponse) -> None:
        self.headers: dict[str, str] = {}
        self.responses = list(responses)
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append((method, url, kwargs))
        return self.responses.pop(0)


def test_v1_create_strips_v3_fields_and_sets_idempotency() -> None:
    session = FakeSession(FakeResponse({"session_id": "v1-session"}))
    transport = V1Transport(api_key="test-key", session=session)
    payload = {
        "prompt": "audit the repository",
        "title": "audit",
        "tags": ["fanout-task:repo-audit"],
        "repos": ["github.com/example/repo"],
        "devin_mode": "fast",
        "resumable": False,
        "structured_output_required": True,
    }

    created = transport.create_session(payload)

    assert created.session_id == "v1-session"
    method, url, kwargs = session.calls[0]
    assert method == "POST"
    assert url.endswith("/v1/sessions")
    body = json.loads(kwargs["data"])
    assert body["prompt"] == "audit the repository"
    assert body["idempotent"] is True
    assert not {"repos", "devin_mode", "resumable", "structured_output_required"} & body.keys()


@pytest.mark.parametrize(
    ("status", "is_terminal"),
    [("finished", True), ("blocked", True), ("expired", True), ("working", False)],
)
def test_v1_get_normalizes_terminal_status_and_missing_metrics(
    status: str, is_terminal: bool
) -> None:
    session = FakeSession(
        FakeResponse(
            {
                "session_id": "v1-session",
                "status_enum": status,
                "status": "working on the task",
                "pull_request": {"url": "https://example.test/pull/7"},
            }
        )
    )
    transport = V1Transport(api_key="test-key", session=session)

    state = transport.get_session("v1-session")

    assert state.status == status
    assert state.is_terminal is is_terminal
    assert state.acus_available is False
    assert state.acus_consumed == 0.0
    assert state.pull_requests == ({"pr_url": "https://example.test/pull/7", "pr_state": None},)


def test_v1_find_sessions_by_tag_returns_task_mapping() -> None:
    session = FakeSession(
        FakeResponse(
            {
                "sessions": [
                    {
                        "session_id": "v1-one",
                        "tags": ["fanout-run:demo", "fanout-task:one"],
                    },
                    {
                        "session_id": "v1-two",
                        "tags": ["fanout-task:two"],
                    },
                    {"session_id": "ignored", "tags": ["other-tag"]},
                ]
            }
        )
    )
    transport = V1Transport(api_key="test-key", session=session)

    found = transport.find_sessions_by_tag("fanout-run:demo")

    assert found == {"fanout-task:one": "v1-one", "fanout-task:two": "v1-two"}
    method, url, kwargs = session.calls[0]
    assert method == "GET"
    assert url.endswith("/v1/sessions")
    assert kwargs["params"] == {"tags": "fanout-run:demo", "limit": 200}


def test_v1_report_marks_unavailable_metrics_instead_of_zero() -> None:
    result = TaskResult(
        task_id="v1-task",
        session_id="v1-session",
        status="finished",
        reached_terminal=True,
        acus_available=False,
        pull_requests=({"pr_url": "https://example.test/pull/7", "pr_state": None},),
    )
    metrics = summarize([result], run_id="v1-run", transport="live-v1", generated_at="now")

    report = render_markdown(metrics)

    assert "not exposed by this API version" in report
    assert "| ACUs total | 0" not in report
    assert "| PR merge rate | not exposed by this API version |" in report
    assert "| PRs opened / merged | 1 / not exposed by this API version |" in report
    assert "The v1 API does not expose ACU consumption or pull-request review state" in report


def test_mock_run_metrics_and_journal(tmp_path: Path) -> None:
    spec = load_spec(DEMO_SPEC)
    scenarios = yaml.safe_load(DEMO_SCENARIOS.read_text(encoding="utf-8"))
    transport = MockTransport(
        scenarios=scenarios["scenarios"],
        polls_before_terminal=scenarios["polls_before_terminal"],
    )
    run_dir = tmp_path / "run"
    results = run(spec, transport, run_dir, clock=lambda: 0.0, sleeper=lambda _: None)
    metrics = summarize(
        results,
        run_id=spec.run_id,
        transport="mock",
        generated_at="2026-01-01 00:00:00Z",
        require_pr=spec.require_pr,
    )
    assert len(results) == 6
    assert len(transport.created) == 6
    assert metrics.tasks_total == 6
    assert metrics.sessions_created == 6
    assert metrics.schema_valid == 5
    assert metrics.self_reported_completed == 4
    assert metrics.unverified_completions == 2
    assert metrics.verified_completion_rate == pytest.approx(2 / 6, abs=0.0001)
    assert metrics.prs_opened == 3
    assert metrics.prs_merged == 1
    assert metrics.needs_human == 5

    journal = run_dir / "journal.jsonl"
    assert journal.is_file()
    records = [json.loads(line) for line in journal.read_text(encoding="utf-8").splitlines()]
    assert records
    assert all("event" in record for record in records)


class AdvancingClock:
    def __init__(self, step: float) -> None:
        self.value = 0.0
        self.step = step

    def __call__(self) -> float:
        current = self.value
        self.value += self.step
        return current


def test_timeout_is_counted_as_needing_human(tmp_path: Path) -> None:
    spec = replace(
        load_spec(DEMO_SPEC), tasks=load_spec(DEMO_SPEC).tasks[:1], concurrency=1, timeout_minutes=1
    )
    transport = MockTransport(scenarios={"auth-service": {}}, polls_before_terminal=100)
    results = run(
        spec,
        transport,
        tmp_path / "timeout",
        clock=AdvancingClock(61),
        sleeper=lambda _: None,
    )
    metrics = summarize(results, run_id=spec.run_id, transport="mock", generated_at="now")
    assert results[0].status == "timed_out"
    assert metrics.timed_out == 1
    assert metrics.needs_human == 1


class CreateFailureTransport:
    def find_sessions_by_tag(self, _: str) -> dict[str, str]:
        return {}

    def create_session(self, _: dict[str, Any]) -> CreatedSession:
        raise RuntimeError("simulated create failure")

    def get_session(self, _: str) -> SessionState:
        raise AssertionError("poll must not happen after create failure")


def test_create_failure_is_recorded_in_denominator(tmp_path: Path) -> None:
    full_spec = load_spec(DEMO_SPEC)
    spec = replace(full_spec, tasks=full_spec.tasks[:1], concurrency=1)
    results = run(spec, CreateFailureTransport(), tmp_path / "create-failure")
    metrics = summarize(results, run_id=spec.run_id, transport="mock", generated_at="now")
    assert results[0].status == "create_failed"
    assert metrics.tasks_total == 1
    assert metrics.sessions_created == 0
    assert metrics.create_failures == 1
    assert metrics.needs_human == 1


def test_percentile_edges() -> None:
    assert percentile([], 0.9) == 0.0
    assert percentile([2.5], 0.9) == 2.5


def test_report_mock_banner_is_transport_specific() -> None:
    metrics = summarize([], run_id="demo", transport="mock", generated_at="now")
    assert MOCK_BANNER in render_markdown(metrics)
    live = summarize([], run_id="demo", transport="live", generated_at="now")
    assert MOCK_BANNER not in render_markdown(live)


def test_validate_cli_returns_success_and_spec_error(tmp_path: Path) -> None:
    valid = write_spec(tmp_path)
    assert main(["validate", "--spec", str(valid)]) == 0
    invalid = write_spec(tmp_path / "bad", spec_updates={"unexpected": True})
    assert main(["validate", "--spec", str(invalid)]) == 2
