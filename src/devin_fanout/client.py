"""Devin API v3 client, plus a mock transport used by the tests and CI.

Endpoints (docs.devin.ai/api-reference/v3):

    POST /v3/organizations/{org_id}/sessions        -> {session_id, url, ...}
    GET  /v3/organizations/{org_id}/sessions/{id}   -> {status, status_detail,
                                                        acus_consumed,
                                                        pull_requests[{pr_url, pr_state}],
                                                        structured_output, ...}

v3 has no idempotency key, so duplicate-suppression lives in the journal and in
the per-run tag: a crashed run can find sessions it already created by tag
instead of creating a second one.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

import requests

API_BASE = os.environ.get("DEVIN_API_BASE", "https://api.devin.ai")
TERMINAL_STATUSES = {"exit", "error"}
REQUEST_TIMEOUT_SECONDS = 60
MAX_ATTEMPTS = 5


class ApiError(RuntimeError):
    pass


@dataclass(frozen=True)
class CreatedSession:
    session_id: str
    url: str


@dataclass(frozen=True)
class SessionState:
    session_id: str
    status: str
    status_detail: str | None
    acus_consumed: float
    structured_output: dict[str, Any] | None
    pull_requests: tuple[dict[str, Any], ...]
    url: str = ""

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES


class Transport(Protocol):
    def create_session(self, payload: dict[str, Any]) -> CreatedSession: ...

    def get_session(self, session_id: str) -> SessionState: ...

    def find_sessions_by_tag(self, tag: str) -> dict[str, str]: ...


def _state_from_payload(payload: dict[str, Any]) -> SessionState:
    return SessionState(
        session_id=str(payload["session_id"]),
        status=str(payload.get("status", "unknown")),
        status_detail=payload.get("status_detail"),
        acus_consumed=float(payload.get("acus_consumed") or 0.0),
        structured_output=payload.get("structured_output"),
        pull_requests=tuple(payload.get("pull_requests") or ()),
        url=str(payload.get("url", "")),
    )


class HttpTransport:
    """Real API access. Retries only on 429 and 5xx, with linear backoff."""

    def __init__(self, org_id: str, api_key: str, session: requests.Session | None = None) -> None:
        if not org_id.startswith("org-"):
            raise ApiError(f"org_id must start with 'org-', got {org_id!r}")
        if not api_key:
            raise ApiError("missing API key")
        self._org_id = org_id
        self._session = session or requests.Session()
        self._session.headers.update(
            {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        )

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        url = f"{API_BASE}{path}"
        for attempt in range(1, MAX_ATTEMPTS + 1):
            response = self._session.request(method, url, timeout=REQUEST_TIMEOUT_SECONDS, **kwargs)
            if response.status_code == 429 or response.status_code >= 500:
                if attempt == MAX_ATTEMPTS:
                    raise ApiError(
                        f"{method} {path} failed after {attempt} attempts: {response.status_code}"
                    )
                time.sleep(min(60, 5 * attempt))
                continue
            if response.status_code >= 400:
                raise ApiError(f"{method} {path} -> {response.status_code}: {response.text[:400]}")
            return response.json()
        raise ApiError(f"{method} {path}: retries exhausted")  # pragma: no cover - unreachable

    def create_session(self, payload: dict[str, Any]) -> CreatedSession:
        body = self._request(
            "POST", f"/v3/organizations/{self._org_id}/sessions", data=json.dumps(payload)
        )
        return CreatedSession(session_id=str(body["session_id"]), url=str(body.get("url", "")))

    def get_session(self, session_id: str) -> SessionState:
        body = self._request("GET", f"/v3/organizations/{self._org_id}/sessions/{session_id}")
        return _state_from_payload(body)

    def find_sessions_by_tag(self, tag: str) -> dict[str, str]:
        """Map task tag -> session_id for sessions already created for this run."""
        body = self._request(
            "GET",
            f"/v3/organizations/{self._org_id}/sessions",
            params={"tags": tag, "limit": 200},
        )
        found: dict[str, str] = {}
        for item in body.get("sessions", body.get("data", [])) or []:
            for item_tag in item.get("tags", []):
                if item_tag.startswith("fanout-task:"):
                    found[item_tag] = str(item["session_id"])
        return found


@dataclass
class MockTransport:
    """Deterministic transport driven by a scripted scenario.

    ``scenarios`` maps a task id to the terminal payload the session should end
    with; ``polls_before_terminal`` controls how many polls each session needs
    before it reports a terminal status, so timeout handling is testable without
    waiting on wall-clock time.
    """

    scenarios: dict[str, dict[str, Any]]
    polls_before_terminal: int = 1
    created: list[dict[str, Any]] = field(default_factory=list)
    _polls: dict[str, int] = field(default_factory=dict)

    def _task_id(self, tags: list[str]) -> str:
        for tag in tags:
            if tag.startswith("fanout-task:"):
                return tag.split(":", 1)[1]
        raise ApiError("mock transport requires a fanout-task tag")

    def create_session(self, payload: dict[str, Any]) -> CreatedSession:
        task_id = self._task_id(list(payload.get("tags", [])))
        self.created.append(payload)
        session_id = f"devin-mock-{task_id}"
        return CreatedSession(session_id=session_id, url=f"https://app.devin.ai/sessions/{task_id}")

    def get_session(self, session_id: str) -> SessionState:
        task_id = session_id.removeprefix("devin-mock-")
        count = self._polls.get(session_id, 0) + 1
        self._polls[session_id] = count
        if count < self.polls_before_terminal:
            return SessionState(
                session_id=session_id,
                status="running",
                status_detail="working",
                acus_consumed=float(count),
                structured_output=None,
                pull_requests=(),
            )
        scenario = self.scenarios.get(task_id)
        if scenario is None:
            raise ApiError(f"mock transport has no scenario for task {task_id!r}")
        payload = dict(scenario)
        payload["session_id"] = session_id
        return _state_from_payload(payload)

    def find_sessions_by_tag(self, tag: str) -> dict[str, str]:
        return {}
