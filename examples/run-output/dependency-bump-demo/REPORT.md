# Fan-out run `dependency-bump-demo`

Generated 2026-08-26 15:55:46Z · transport `mock` · 6 tasks in the manifest.

> **Transport: `mock`.** These numbers come from a scripted scenario file, not from
> Devin sessions. They demonstrate that the pipeline and the metric definitions work.
> Do not quote them as agent performance.


## Headline

| Metric | Value | Definition |
| --- | --- | --- |
| Verified completion rate | 33.3% | `outcome=completed` **and** the session's own verification command passed **and** a PR exists. Denominator is the task list. |
| Unverified completion rate | 33.3% | Said `completed` while its verification failed or no PR was opened. These are the ones a human must catch. |
| Human attention rate | 83.3% | Tasks needing a person: blocked, partial, timed out, invalid report, API failure, or an explicit `human_action_required`. |
| PR merge rate | 16.7% | PRs in state `merged` at report time — external evidence, not self-report. Re-run `report` later to let this catch up with review. |
| Report-schema validity | 83.3% | Sessions whose structured output satisfied the contract. |

## Cost and latency

| Metric | Value |
| --- | --- |
| ACUs total | 37.0 |
| ACUs mean / p90 per task | 6.167 / 11.8 |
| Wall-clock mean / p90 per task (s) | 30.091 / 30.103 |
| Mean polls per session | 2.0 |

## Counts

| Bucket | Tasks |
| --- | --- |
| Sessions created | 6 / 6 |
| Create failures | 0 |
| Poll failures | 0 |
| Timed out | 0 |
| Reached a terminal status | 6 |
| Self-reported completed / partial / blocked / not attempted | 4 / 0 / 1 / 0 |
| PRs opened / merged | 3 / 1 |

## Per task

| Task | Status | Outcome | Verification | PR | Needs a human because |
| --- | --- | --- | --- | --- | --- |
| `auth-service` | exit | completed | `pytest services/auth -q` pass | [merged](https://github.com/example-org/example-monorepo/pull/101) | — |
| `billing-worker` | exit | completed | `pytest workers/billing -q` pass | [open](https://github.com/example-org/example-monorepo/pull/102) | agent asked for a human |
| `legacy-reports` | exit | blocked | — | — | outcome=blocked; agent asked for a human |
| `notifications` | exit | completed | `pytest services/notifications -q` fail | [open](https://github.com/example-org/example-monorepo/pull/103) | claimed completion without evidence |
| `data-export` | exit | completed | `pytest jobs/export -q` pass | — | claimed completion without evidence |
| `admin-ui` | error | — | — | — | invalid or missing structured output |

## What this run does not tell you

- Nothing here checks whether the change was *correct*, only whether the session's own verification command passed and a PR exists. Code review is still the gate.
- `pr_merge_rate` is a snapshot. It is bounded by how fast a human reviews, not by the agent.
- Tasks that fail to start count against every rate, on purpose.
