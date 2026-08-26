# Fan-out run `live-smoke`

Generated 2026-08-26 16:20:32Z · transport `live-v1` · 3 tasks in the manifest.

## Headline

| Metric | Value | Definition |
| --- | --- | --- |
| Verified completion rate | 66.7% | `outcome=completed` **and** the session's own verification command passed (this run set `require_pr: false`, so no PR was expected). Denominator is the task list. |
| Unverified completion rate | 0.0% | Said `completed` while its verification failed or no PR was opened. These are the ones a human must catch. |
| Human attention rate | 33.3% | Tasks needing a person: blocked, partial, timed out, invalid report, API failure, or an explicit `human_action_required`. |
| PR merge rate | no PRs opened | PRs in state `merged` at report time — external evidence, not self-report. Re-run `report` later to let this catch up with review. |
| Report-schema validity | 100.0% | Sessions whose structured output satisfied the contract. |

## Cost and latency

| Metric | Value |
| --- | --- |
| ACUs total | not exposed by this API version |
| ACUs mean / p90 per task | not exposed by this API version / not exposed by this API version |
| Wall-clock mean / p90 per task (s) | 81.016 / 91.111 |
| Mean polls per session | 3.667 |

## Counts

| Bucket | Tasks |
| --- | --- |
| Sessions created | 3 / 3 |
| Create failures | 0 |
| Poll failures | 0 |
| Timed out | 0 |
| Reached a terminal status | 3 |
| Self-reported completed / partial / blocked / not attempted | 2 / 0 / 1 / 0 |
| PRs opened / merged | 0 / 0 |

## Per task

| Task | Status | Outcome | Verification | PR | Needs a human because |
| --- | --- | --- | --- | --- | --- |
| `count-tests` | blocked | completed | `.venv/bin/python -m pytest -q` pass | — | — |
| `metric-inventory` | blocked | completed | `ruff check .` pass | — | — |
| `unavailable-credential` | blocked | blocked | `ruff check .` pass | — | outcome=blocked; agent asked for a human |

## What this run does not tell you

- Nothing here checks whether the change was *correct*, only whether the session's own verification command passed and a PR exists. Code review is still the gate.
- `pr_merge_rate` is a snapshot. It is bounded by how fast a human reviews, not by the agent.
- Tasks that fail to start count against every rate, on purpose.
- This API version does not expose ACU consumption, so cost is reported as unavailable rather than as zero.
