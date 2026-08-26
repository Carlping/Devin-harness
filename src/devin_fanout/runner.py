"""Fan a task list out to N Devin sessions and record what happened.

Design constraints:

* Every state change is appended to ``journal.jsonl`` *before* the next API
  call, so a crashed run can be resumed and, more importantly, so the metrics
  are computed from a durable record rather than from process memory.
* A session that never reaches a terminal status is recorded as ``timed_out``,
  never silently dropped. An absent result is a result.
* The harness never edits or "fixes" a structured output that fails schema
  validation; it records the validation error. Repairing agent output inside the
  measurement tool is how a harness ends up measuring itself.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from jsonschema import Draft7Validator

from .client import Transport
from .contract import DEFAULT_STRUCTURED_OUTPUT_SCHEMA
from .spec import RunSpec, Task


@dataclass
class TaskResult:
    task_id: str
    session_id: str | None = None
    session_url: str = ""
    status: str = "not_created"
    status_detail: str | None = None
    acus_consumed: float = 0.0
    wall_seconds: float = 0.0
    polls: int = 0
    structured_output: dict[str, Any] | None = None
    schema_errors: tuple[str, ...] = ()
    pull_requests: tuple[dict[str, Any], ...] = ()
    timed_out: bool = False
    error: str | None = None

    @property
    def schema_valid(self) -> bool:
        return self.structured_output is not None and not self.schema_errors

    def to_json(self) -> dict[str, Any]:
        data = asdict(self)
        data["schema_valid"] = self.schema_valid
        return data


@dataclass
class Journal:
    path: Path
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def append(self, event: str, **payload: Any) -> None:
        record = {"ts": time.time(), "event": event, **payload}
        line = json.dumps(record, sort_keys=True, default=str)
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")


def build_payload(spec: RunSpec, task: Task) -> dict[str, Any]:
    schema = spec.structured_output_schema or DEFAULT_STRUCTURED_OUTPUT_SCHEMA
    payload: dict[str, Any] = {
        "prompt": task.prompt,
        "title": f"[{spec.run_id}] {task.id}",
        "tags": [spec.run_tag, f"fanout-task:{task.id}", *spec.tags],
        "structured_output_schema": schema,
        "structured_output_required": True,
        "resumable": False,
    }
    if task.repos:
        payload["repos"] = list(task.repos)
    if spec.devin_mode:
        payload["devin_mode"] = spec.devin_mode
    if spec.max_acu_limit is not None:
        payload["max_acu_limit"] = spec.max_acu_limit
    if spec.playbook_id:
        payload["playbook_id"] = spec.playbook_id
    return payload


def validate_output(schema: dict[str, Any], output: Any) -> tuple[str, ...]:
    if output is None:
        return ("session produced no structured output",)
    if not isinstance(output, dict):
        return (f"structured output must be an object, got {type(output).__name__}",)
    validator = Draft7Validator(schema)
    return tuple(
        f"{'/'.join(str(part) for part in error.path) or '<root>'}: {error.message}"
        for error in sorted(validator.iter_errors(output), key=lambda err: list(err.path))
    )


def _run_task(
    spec: RunSpec,
    task: Task,
    transport: Transport,
    journal: Journal,
    existing_session_id: str | None,
    clock: Callable[[], float],
    sleeper: Callable[[float], None],
) -> TaskResult:
    result = TaskResult(task_id=task.id)
    schema = spec.structured_output_schema or DEFAULT_STRUCTURED_OUTPUT_SCHEMA
    started = clock()

    try:
        if existing_session_id:
            result.session_id = existing_session_id
            journal.append("session_reused", task_id=task.id, session_id=existing_session_id)
        else:
            payload = build_payload(spec, task)
            journal.append("session_create_requested", task_id=task.id)
            created = transport.create_session(payload)
            result.session_id = created.session_id
            result.session_url = created.url
            journal.append(
                "session_created",
                task_id=task.id,
                session_id=created.session_id,
                url=created.url,
            )
    except Exception as exc:  # noqa: BLE001 - a create failure is data, not a crash
        result.error = f"create failed: {exc}"
        result.status = "create_failed"
        journal.append("session_create_failed", task_id=task.id, error=str(exc))
        return result

    deadline = started + spec.timeout_minutes * 60
    while True:
        try:
            state = transport.get_session(result.session_id)
        except Exception as exc:  # noqa: BLE001 - a poll failure is data too
            result.error = f"poll failed: {exc}"
            result.status = "poll_failed"
            journal.append("poll_failed", task_id=task.id, error=str(exc))
            break

        result.polls += 1
        result.status = state.status
        result.status_detail = state.status_detail
        result.acus_consumed = state.acus_consumed
        result.pull_requests = state.pull_requests
        journal.append(
            "polled",
            task_id=task.id,
            session_id=result.session_id,
            status=state.status,
            status_detail=state.status_detail,
            acus_consumed=state.acus_consumed,
        )

        if state.is_terminal:
            result.structured_output = state.structured_output
            result.schema_errors = validate_output(schema, state.structured_output)
            break
        if clock() >= deadline:
            result.timed_out = True
            result.status = "timed_out"
            journal.append("timed_out", task_id=task.id, session_id=result.session_id)
            break
        sleeper(spec.poll_interval_seconds)

    result.wall_seconds = round(clock() - started, 3)
    journal.append("task_finished", **result.to_json())
    return result


def run(
    spec: RunSpec,
    transport: Transport,
    run_dir: Path,
    *,
    clock: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
    resume: bool = True,
) -> list[TaskResult]:
    run_dir.mkdir(parents=True, exist_ok=True)
    journal = Journal(run_dir / "journal.jsonl")
    journal.append("run_started", run_id=spec.run_id, tasks=len(spec.tasks))

    existing: dict[str, str] = {}
    if resume:
        try:
            existing = transport.find_sessions_by_tag(spec.run_tag)
        except Exception as exc:  # noqa: BLE001 - resume is best effort
            journal.append("resume_lookup_failed", error=str(exc))
        if existing:
            journal.append("resume_lookup", found=len(existing))

    results: list[TaskResult] = []
    lock = threading.Lock()
    pending = list(spec.tasks)
    index = 0

    def worker() -> None:
        nonlocal index
        while True:
            with lock:
                if index >= len(pending):
                    return
                task = pending[index]
                index += 1
            outcome = _run_task(
                spec,
                task,
                transport,
                journal,
                existing.get(f"fanout-task:{task.id}"),
                clock,
                sleeper,
            )
            with lock:
                results.append(outcome)

    threads = [
        threading.Thread(target=worker, daemon=True)
        for _ in range(min(spec.concurrency, len(pending)))
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    order = {task.id: position for position, task in enumerate(spec.tasks)}
    results.sort(key=lambda item: order[item.task_id])
    journal.append("run_finished", run_id=spec.run_id, results=len(results))
    return results
