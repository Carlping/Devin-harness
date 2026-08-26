"""Metric definitions for a fan-out run.

Every definition here is a choice, and the wrong choice produces a number that
is flattering and useless. The rules this module commits to:

1. **Self-report and external evidence are never merged.** ``self_reported_*``
   comes from the session's structured output; ``pr_*`` comes from the API's
   ``pull_requests`` field. They are reported side by side so the gap is
   visible instead of averaged away.
2. **A claim without evidence is its own bucket.** ``unverified_completions``
   counts sessions that said ``completed`` while failing their own verification
   command, or without a PR when the run requires one. This is the number a
   customer actually cares about: how often the agent thinks it is done and is
   not.
3. **The denominator is always the task list.** Not "sessions created", not
   "sessions that finished". Tasks that failed to start, timed out, or returned
   invalid output stay in the denominator; otherwise a harness can improve its
   completion rate by crashing earlier.
4. **Human attention is a rate, not an anecdote.** Anything that needs a person
   — a stated ``human_action_required``, a blocked outcome, a timeout, an
   invalid report, an API failure — counts once against the task list.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from statistics import mean
from typing import Any

from .runner import TaskResult

MERGED_STATES = {"merged"}
OPEN_STATES = {"open", "opened", "ready", "draft"}


def percentile(values: list[float], fraction: float) -> float:
    """Nearest-rank percentile. Small N makes interpolation false precision."""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 3)
    rank = max(1, min(len(ordered), round(fraction * len(ordered) + 0.5)))
    return round(ordered[rank - 1], 3)


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


@dataclass
class TaskVerdict:
    task_id: str
    session_id: str | None
    status: str
    schema_valid: bool
    self_reported_outcome: str | None
    verification_command: str
    verification_passed: bool
    pr_url: str | None
    pr_state: str | None
    needs_human: bool
    needs_human_reason: str
    unverified_completion: bool
    reached_terminal: bool
    acus_consumed: float
    wall_seconds: float
    polls: int
    error: str | None


@dataclass
class RunMetrics:
    run_id: str
    transport: str
    generated_at: str
    tasks_total: int
    sessions_created: int
    create_failures: int
    poll_failures: int
    timed_out: int
    reached_terminal: int
    schema_valid: int
    self_reported_completed: int
    self_reported_partial: int
    self_reported_blocked: int
    self_reported_not_attempted: int
    verification_passed: int
    unverified_completions: int
    prs_opened: int
    prs_merged: int
    needs_human: int
    schema_valid_rate: float
    verified_completion_rate: float
    unverified_completion_rate: float
    pr_merge_rate: float
    human_attention_rate: float
    acus_total: float
    acus_mean: float
    acus_p90: float
    acus_available: bool
    pr_state_available: bool
    require_pr: bool
    wall_seconds_mean: float
    wall_seconds_p90: float
    polls_mean: float
    verdicts: list[TaskVerdict] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def _first_pr(result: TaskResult, output: dict[str, Any] | None) -> tuple[str | None, str | None]:
    """Prefer the API's PR record; fall back to the self-reported URL with no state."""
    for record in result.pull_requests:
        url = record.get("pr_url") or record.get("url")
        if url:
            return str(url), (record.get("pr_state") or record.get("state") or None)
    if output:
        url = output.get("pr_url")
        if isinstance(url, str) and url:
            return url, None
    return None, None


def _verdict(result: TaskResult, require_pr: bool) -> TaskVerdict:
    output = result.structured_output if result.schema_valid else None
    outcome = str(output.get("outcome")) if output else None
    verification = output.get("verification", {}) if output else {}
    verification_passed = (
        bool(verification.get("passed")) if isinstance(verification, dict) else False
    )
    verification_command = (
        str(verification.get("command", "")) if isinstance(verification, dict) else ""
    )
    pr_url, pr_state = _first_pr(result, output)
    human_action = str(output.get("human_action_required", "")).strip() if output else ""

    unverified = bool(
        outcome == "completed" and (not verification_passed or (require_pr and not pr_url))
    )

    reasons: list[str] = []
    if result.error:
        reasons.append("api failure")
    if result.timed_out:
        reasons.append("timed out")
    if not result.schema_valid:
        reasons.append("invalid or missing structured output")
    if outcome in {"blocked", "not_attempted", "partial"}:
        reasons.append(f"outcome={outcome}")
    if human_action:
        reasons.append("agent asked for a human")
    if unverified:
        reasons.append("claimed completion without evidence")

    return TaskVerdict(
        task_id=result.task_id,
        session_id=result.session_id,
        status=result.status,
        schema_valid=result.schema_valid,
        self_reported_outcome=outcome,
        verification_command=verification_command,
        verification_passed=verification_passed,
        pr_url=pr_url,
        pr_state=pr_state,
        needs_human=bool(reasons),
        needs_human_reason="; ".join(reasons),
        unverified_completion=unverified,
        reached_terminal=result.reached_terminal,
        acus_consumed=result.acus_consumed,
        wall_seconds=result.wall_seconds,
        polls=result.polls,
        error=result.error,
    )


def summarize(
    results: list[TaskResult],
    *,
    run_id: str,
    transport: str,
    generated_at: str,
    require_pr: bool = True,
) -> RunMetrics:
    verdicts = [_verdict(result, require_pr) for result in results]
    total = len(results)

    def count(predicate: Any) -> int:
        return sum(1 for verdict in verdicts if predicate(verdict))

    acus = [verdict.acus_consumed for verdict in verdicts]
    walls = [verdict.wall_seconds for verdict in verdicts]
    polls = [float(verdict.polls) for verdict in verdicts]

    verified_completions = count(
        lambda verdict: (
            verdict.self_reported_outcome == "completed"
            and verdict.verification_passed
            and (not require_pr or bool(verdict.pr_url))
        )
    )

    return RunMetrics(
        run_id=run_id,
        transport=transport,
        generated_at=generated_at,
        tasks_total=total,
        sessions_created=count(lambda v: v.session_id is not None),
        create_failures=count(lambda v: v.status == "create_failed"),
        poll_failures=count(lambda v: v.status == "poll_failed"),
        timed_out=count(lambda v: v.status == "timed_out"),
        reached_terminal=count(lambda v: v.reached_terminal),
        schema_valid=count(lambda v: v.schema_valid),
        self_reported_completed=count(lambda v: v.self_reported_outcome == "completed"),
        self_reported_partial=count(lambda v: v.self_reported_outcome == "partial"),
        self_reported_blocked=count(lambda v: v.self_reported_outcome == "blocked"),
        self_reported_not_attempted=count(lambda v: v.self_reported_outcome == "not_attempted"),
        verification_passed=count(lambda v: v.verification_passed),
        unverified_completions=count(lambda v: v.unverified_completion),
        prs_opened=count(lambda v: bool(v.pr_url)),
        prs_merged=count(lambda v: (v.pr_state or "") in MERGED_STATES),
        needs_human=count(lambda v: v.needs_human),
        schema_valid_rate=_ratio(count(lambda v: v.schema_valid), total),
        verified_completion_rate=_ratio(verified_completions, total),
        unverified_completion_rate=_ratio(count(lambda v: v.unverified_completion), total),
        pr_merge_rate=_ratio(count(lambda v: (v.pr_state or "") in MERGED_STATES), total),
        human_attention_rate=_ratio(count(lambda v: v.needs_human), total),
        acus_total=round(sum(acus), 3),
        acus_mean=round(mean(acus), 3) if acus else 0.0,
        acus_p90=percentile(acus, 0.9),
        acus_available=all(result.acus_available for result in results) if results else True,
        # A PR whose review state the API never reported must not be counted as "not merged".
        pr_state_available=not any(
            verdict.pr_url and verdict.pr_state is None for verdict in verdicts
        ),
        require_pr=require_pr,
        wall_seconds_mean=round(mean(walls), 3) if walls else 0.0,
        wall_seconds_p90=percentile(walls, 0.9),
        polls_mean=round(mean(polls), 3) if polls else 0.0,
        verdicts=verdicts,
    )
