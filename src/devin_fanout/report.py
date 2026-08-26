"""Render a run into Markdown a human can act on.

The report leads with what the numbers do *not* mean. A completion rate computed
from an agent's own summary is a self-assessment; the harness says so in the
document rather than in a footnote nobody reads.
"""

from __future__ import annotations

from .metrics import RunMetrics

MOCK_BANNER = (
    "> **Transport: `mock`.** These numbers come from a scripted scenario file, not from\n"
    "> Devin sessions. They demonstrate that the pipeline and the metric definitions work.\n"
    "> Do not quote them as agent performance.\n"
)


def _pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def render_markdown(metrics: RunMetrics) -> str:
    lines: list[str] = [
        f"# Fan-out run `{metrics.run_id}`",
        "",
        f"Generated {metrics.generated_at} · transport `{metrics.transport}` · "
        f"{metrics.tasks_total} tasks in the manifest.",
        "",
    ]
    if metrics.transport == "mock":
        lines += [MOCK_BANNER, ""]

    lines += [
        "## Headline",
        "",
        "| Metric | Value | Definition |",
        "| --- | --- | --- |",
        f"| Verified completion rate | {_pct(metrics.verified_completion_rate)} | "
        "`outcome=completed` **and** the session's own verification command passed "
        "**and** a PR exists. Denominator is the task list. |",
        f"| Unverified completion rate | {_pct(metrics.unverified_completion_rate)} | "
        "Said `completed` while its verification failed or no PR was opened. "
        "These are the ones a human must catch. |",
        f"| Human attention rate | {_pct(metrics.human_attention_rate)} | "
        "Tasks needing a person: blocked, partial, timed out, invalid report, API failure, "
        "or an explicit `human_action_required`. |",
        f"| PR merge rate | {_pct(metrics.pr_merge_rate)} | "
        "PRs in state `merged` at report time — external evidence, not self-report. "
        "Re-run `report` later to let this catch up with review. |",
        f"| Report-schema validity | {_pct(metrics.schema_valid_rate)} | "
        "Sessions whose structured output satisfied the contract. |",
        "",
        "## Cost and latency",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        f"| ACUs total | {metrics.acus_total} |",
        f"| ACUs mean / p90 per task | {metrics.acus_mean} / {metrics.acus_p90} |",
        f"| Wall-clock mean / p90 per task (s) | {metrics.wall_seconds_mean} / "
        f"{metrics.wall_seconds_p90} |",
        f"| Mean polls per session | {metrics.polls_mean} |",
        "",
        "## Counts",
        "",
        "| Bucket | Tasks |",
        "| --- | --- |",
        f"| Sessions created | {metrics.sessions_created} / {metrics.tasks_total} |",
        f"| Create failures | {metrics.create_failures} |",
        f"| Poll failures | {metrics.poll_failures} |",
        f"| Timed out | {metrics.timed_out} |",
        f"| Reached a terminal status | {metrics.reached_terminal} |",
        f"| Self-reported completed / partial / blocked / not attempted | "
        f"{metrics.self_reported_completed} / {metrics.self_reported_partial} / "
        f"{metrics.self_reported_blocked} / {metrics.self_reported_not_attempted} |",
        f"| PRs opened / merged | {metrics.prs_opened} / {metrics.prs_merged} |",
        "",
        "## Per task",
        "",
        "| Task | Status | Outcome | Verification | PR | Needs a human because |",
        "| --- | --- | --- | --- | --- | --- |",
    ]

    for verdict in metrics.verdicts:
        verification = "—"
        if verdict.verification_command or verdict.verification_passed:
            mark = "pass" if verdict.verification_passed else "fail"
            verification = f"`{verdict.verification_command or 'none'}` {mark}"
        pr_cell = "—"
        if verdict.pr_url:
            pr_cell = f"[{verdict.pr_state or 'state unknown'}]({verdict.pr_url})"
        lines.append(
            f"| `{verdict.task_id}` | {verdict.status} | {verdict.self_reported_outcome or '—'} | "
            f"{verification} | {pr_cell} | {verdict.needs_human_reason or '—'} |"
        )

    lines += [
        "",
        "## What this run does not tell you",
        "",
        "- Nothing here checks whether the change was *correct*, only whether the session's "
        "own verification command passed and a PR exists. Code review is still the gate.",
        "- `pr_merge_rate` is a snapshot. It is bounded by how fast a human reviews, not by "
        "the agent.",
        "- Tasks that fail to start count against every rate, on purpose.",
        "",
    ]
    return "\n".join(lines)
