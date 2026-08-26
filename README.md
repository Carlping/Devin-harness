# devin-harness — measuring what a fleet of agents actually did

*[繁體中文版](README.zh-TW.md)*

A customer asks the question every time: *"we have 200 files to migrate — can an agent
just do it?"* The honest answer is not yes or no, it is **a number, with a stated
definition and a list of what the number excludes**.

This is the harness that produces that number. It takes a task manifest, fans it out to N
[Devin](https://docs.devin.ai) sessions over the v3 API, holds every session to a
machine-checkable reporting contract, and emits a table you can put in front of an
engineering manager:

```
task manifest → N sessions → contract-validated reports → metrics + per-task verdicts
```

It is deliberately small. The interesting part is not the concurrency; it is **which
metrics it refuses to compute**.

## Who needs this, concretely

- You have 12 repositories that all need the same dependency bump, and you have to tell
  your manager on Monday whether the fleet can do it or whether it needs 12 engineers.
- You are about to quote an agent-driven migration to a customer and you need a number
  you can defend when it is wrong.
- You already ran agents on a batch and someone asked *"how many of those actually
  landed?"* — and the only answer available was the agents' own summaries.

If you are running one task, you do not need this: read the session. This exists for the
point where you stop reading sessions and start needing a denominator.

## What you write, and what comes back

Three files, one command. Nothing else.

| You write | Example | What it is |
| --- | --- | --- |
| A task manifest | [`examples/tasks/repo-audit.yaml`](examples/tasks/repo-audit.yaml) | One entry per unit of work, each with the variables its prompt needs and the command that proves it worked. This list is the denominator of every rate. |
| A prompt template | [`examples/prompts/repo-audit.md`](examples/prompts/repo-audit.md) | The instructions every session gets, with `{{variables}}` filled in per task — including the boundaries ("report `blocked` rather than guessing"). |
| A run spec | [`examples/live-smoke.yaml`](examples/live-smoke.yaml) | Blast radius: which repos, how many at once, ACU ceiling per session, timeout, whether a PR is required. |

```bash
python -m devin_fanout run --spec examples/live-smoke.yaml \
  --transport live --api-version v1
```

What comes back is one table per run — this is the real three-task run committed in this
repository, abridged:

| Metric | Value |
| --- | --- |
| Verified completion rate | 66.7% |
| Unverified completion rate | 0.0% |
| Human attention rate | 33.3% |
| ACUs total | not exposed by this API version |

| Task | Outcome | Verification | Needs a human because |
| --- | --- | --- | --- |
| `count-tests` | completed | `pytest -q` pass | — |
| `metric-inventory` | completed | `ruff check .` pass | — |
| `unavailable-credential` | blocked | — | outcome=blocked; agent asked for a human |

Read it as: two of three tasks are done and their own checks back that up, one needs you,
and this API version cannot tell you the cost. The third task was designed to be
unanswerable from the repository; it returned named blockers instead of a plausible
number, which is the behaviour the whole harness exists to detect. Full report:
[`examples/run-output/live-smoke/REPORT.md`](examples/run-output/live-smoke/REPORT.md).

## The three decisions that make the table trustworthy

**1. Self-report and external evidence are never merged.** A session's structured output
is a *claim*. Whether a pull request exists, and whether it is merged, comes from the
API's `pull_requests` field. The report prints both columns side by side and never
averages them into one "success rate", because the gap between them is the finding.

**2. "Claimed done without evidence" is its own metric.** `unverified_completion_rate`
counts sessions that reported `outcome: completed` while their own verification command
failed, or without opening a required PR. This is the number that decides whether a
migration is viable: not how often the agent succeeds, but how often it *believes* it
succeeded and hasn't. See
[`src/devin_fanout/metrics.py`](src/devin_fanout/metrics.py) for the exact predicates.

**3. The denominator is always the task manifest.** Sessions that failed to start, timed
out, or returned output that violated the schema stay in the denominator. A harness whose
completion rate improves when sessions crash earlier is measuring itself, not the agent.

## The reporting contract

Fan-out only produces a table if every session answers the same questions in the same
shape. The schema in [`src/devin_fanout/contract.py`](src/devin_fanout/contract.py) is
sent with each session as `structured_output_schema`, with
`structured_output_required: true`:

| Field | Why it is in the contract |
| --- | --- |
| `outcome` | Four values only — `completed`, `partial`, `blocked`, `not_attempted`. An agent that cannot finish must say so in one word instead of narrating a workaround. |
| `verification.command` + `verification.passed` | Forces the agent to name the command whose result it is reporting. "I believe this works" is not expressible in this schema. |
| `pr_url` | Compared against the API's own PR records, not trusted. |
| `human_action_required` | An explicit place to hand the decision back. Empty string means "nothing needed", and that is a claim the report checks. |
| `blockers` | Missing credentials, ambiguous requirements, broken environment — the things that are the operator's fault, not the agent's. |

Output that fails validation is recorded as invalid. The harness never repairs it.

## Run it

```bash
pip install -e '.[dev]'

# 1. Fail closed before spending anything: unknown keys, duplicate task ids,
#    unresolved prompt variables and unused task vars are all errors.
python -m devin_fanout validate --spec examples/dependency-bump.yaml --show-payloads

# 2. Mock transport — the full pipeline against scripted fixtures, no API key,
#    no network. This is what CI runs on every push.
python -m devin_fanout run --spec examples/dependency-bump.yaml \
  --transport mock --scenarios examples/scenarios/dependency-bump.yaml

# 3. Live transport. There is no fallback from live to mock: if the API is
#    unreachable the run fails, because a mock number must never be quotable
#    as agent performance.
export DEVIN_API_KEY=...   # never committed, never logged, never in a report
export DEVIN_ORG_ID=org-...
python -m devin_fanout run --spec examples/dependency-bump.yaml --transport live

# 4. PR state is a snapshot bounded by human review speed. Recompute it later
#    from the same run without touching the agents again.
python -m devin_fanout report --run runs/dependency-bump-demo
```

A committed example of the output — headline metrics, cost, per-task verdicts, and the
"what this run does not tell you" section — is at
[`examples/run-output/dependency-bump-demo/REPORT.md`](examples/run-output/dependency-bump-demo/REPORT.md).
It is generated by the mock transport and the report says so at the top, in a banner, on
purpose.

The first live-transport evidence is at
[`examples/run-output/live-smoke/`](examples/run-output/live-smoke/). It is a three-task
smoke test against the v1 API, not a benchmark; unlike the mock numbers above, it reflects
an actual run.

## API versions

The default live transport uses the v3 API. It is organisation-scoped and exposes the
create-time repository and execution options, ACU consumption, and pull-request review
state. Use `--api-version v1` when the API key does not have organisation scope:

```bash
python -m devin_fanout run --spec examples/live-smoke.yaml \
  --transport live --api-version v1
```

The v1 API accepts an idempotent create request but does not accept the v3 repository,
mode, resumability, or structured-output-required fields. Its session response exposes
neither ACU consumption nor pull-request review state. The report prints a metric the
selected API cannot supply as `not exposed by this API version`, never as zero: a v1 run
must not be read as “cost 0 ACUs” or “0% merge rate”.

## Operational properties

- **Every state change is journalled first.** `journal.jsonl` is appended before the next
  API call, so metrics are computed from a durable record rather than process memory, and
  a crashed run is auditable.
- **Idempotency without an idempotency key.** The v3 create endpoint has none, so each
  session is tagged `fanout-run:<run_id>` and `fanout-task:<task_id>`. A resumed run looks
  its own sessions up by tag instead of creating a second one for the same task.
- **Retries only where retrying is correct.** `429` and `5xx` back off; `4xx` fails
  immediately and loudly. An expired key should stop the run, not generate 200 retries.
- **A missing result is a result.** A session that never reaches a terminal status is
  recorded as `timed_out` and counted, never dropped.
- **Blast radius is bounded by the spec**, not by hope: `concurrency`, `max_acu_limit`,
  `timeout_minutes`, and an explicit `repos` allowlist per task.

## What this does not do

- It does not decide whether a change is *correct*. It measures whether the agent's own
  verification passed and whether a PR exists. Code review remains the gate — and the
  point of `human_action_required` is that the agent is required to say when it needs one.
- It does not merge anything, ever.
- It does not retry a task by re-prompting the same session. A failed task comes back to a
  human with the reason, because "run it again" is a decision, not a default.
- Committed numbers in this repository come from the mock transport. Live numbers belong
  to whoever runs it against their own organisation and their own repos.

## Layout

| Path | Contents |
| --- | --- |
| [`src/devin_fanout/spec.py`](src/devin_fanout/spec.py) | Fail-closed spec and task-manifest loading, prompt templating |
| [`src/devin_fanout/contract.py`](src/devin_fanout/contract.py) | The structured-output schema every session must satisfy |
| [`src/devin_fanout/client.py`](src/devin_fanout/client.py) | v3 HTTP transport + deterministic mock transport |
| [`src/devin_fanout/runner.py`](src/devin_fanout/runner.py) | Fan-out, polling, timeouts, journalling, schema validation |
| [`src/devin_fanout/metrics.py`](src/devin_fanout/metrics.py) | Metric definitions — the opinionated part |
| [`src/devin_fanout/report.py`](src/devin_fanout/report.py) | Markdown report, including its own caveats |
| [`examples/`](examples/) | A six-task migration manifest, prompt template, and mock fixtures |

MIT licensed.
