"""CLI: `python -m devin_fanout <run|report|validate> ...`.

The live transport is only constructed when `--transport live` is passed *and*
both `DEVIN_API_KEY` and an org id are present. There is no fallback from live
to mock: a run that cannot reach the API must fail loudly, or a mock number ends
up in a slide deck.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

try:
    from datetime import UTC
except ImportError:  # pragma: no cover - Python 3.10 compatibility
    UTC = timezone.utc  # noqa: UP017
from pathlib import Path

import yaml

from .client import HttpTransport, MockTransport
from .contract import DEFAULT_STRUCTURED_OUTPUT_SCHEMA
from .metrics import summarize
from .report import render_markdown
from .runner import TaskResult, build_payload, run
from .spec import SpecError, load_spec


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%SZ")


def _load_scenarios(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "scenarios" not in data:
        raise SpecError(f"{path} must be a mapping with a 'scenarios' key")
    return data


def _write_outputs(
    run_dir: Path, results: list[TaskResult], *, run_id: str, transport: str, require_pr: bool
) -> Path:
    metrics = summarize(
        results,
        run_id=run_id,
        transport=transport,
        generated_at=_now(),
        require_pr=require_pr,
    )
    (run_dir / "results.json").write_text(
        json.dumps([result.to_json() for result in results], indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )
    (run_dir / "metrics.json").write_text(
        json.dumps(metrics.to_json(), indent=2, sort_keys=True, default=str), encoding="utf-8"
    )
    report_path = run_dir / "REPORT.md"
    report_path.write_text(render_markdown(metrics), encoding="utf-8")
    return report_path


def cmd_validate(args: argparse.Namespace) -> int:
    spec = load_spec(Path(args.spec))
    print(f"run_id: {spec.run_id}")
    print(f"tasks: {len(spec.tasks)}")
    print(f"concurrency: {spec.concurrency}  timeout: {spec.timeout_minutes}m")
    print(f"schema: {'custom' if spec.structured_output_schema else 'default contract'}")
    if args.show_payloads:
        payloads = [build_payload(spec, task) for task in spec.tasks]
        print(json.dumps(payloads, indent=2, sort_keys=True))
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    spec = load_spec(Path(args.spec))
    run_dir = Path(args.out) / spec.run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    if args.transport == "live":
        org_id = spec.org_id or os.environ.get("DEVIN_ORG_ID", "")
        api_key = os.environ.get("DEVIN_API_KEY", "")
        if not org_id:
            raise SpecError(
                "live transport needs org_id in the spec or DEVIN_ORG_ID in the environment"
            )
        if not api_key:
            raise SpecError("live transport needs DEVIN_API_KEY in the environment")
        transport = HttpTransport(org_id=org_id, api_key=api_key)
    else:
        if not args.scenarios:
            raise SpecError("mock transport needs --scenarios")
        data = _load_scenarios(Path(args.scenarios))
        transport = MockTransport(
            scenarios=data["scenarios"],
            polls_before_terminal=int(data.get("polls_before_terminal", 1)),
        )

    results = run(spec, transport, run_dir, resume=not args.no_resume)
    report_path = _write_outputs(
        run_dir, results, run_id=spec.run_id, transport=args.transport, require_pr=spec.require_pr
    )
    print(f"wrote {report_path}")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    run_dir = Path(args.run)
    payload = json.loads((run_dir / "results.json").read_text(encoding="utf-8"))
    results = [
        TaskResult(
            **{
                key: (tuple(value) if key in {"schema_errors", "pull_requests"} else value)
                for key, value in item.items()
                if key != "schema_valid"
            }
        )
        for item in payload
    ]
    report_path = _write_outputs(
        run_dir,
        results,
        run_id=args.run_id or run_dir.name,
        transport=args.transport,
        require_pr=not args.no_pr_required,
    )
    print(f"wrote {report_path}")
    return 0


def cmd_schema(_: argparse.Namespace) -> int:
    print(json.dumps(DEFAULT_STRUCTURED_OUTPUT_SCHEMA, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="devin_fanout", description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate", help="load a spec and refuse anything ambiguous")
    validate.add_argument("--spec", required=True)
    validate.add_argument("--show-payloads", action="store_true")
    validate.set_defaults(func=cmd_validate)

    runner = subparsers.add_parser("run", help="fan the task list out and write a report")
    runner.add_argument("--spec", required=True)
    runner.add_argument("--transport", choices=["mock", "live"], default="mock")
    runner.add_argument("--scenarios", help="scenario file for the mock transport")
    runner.add_argument("--out", default="runs")
    runner.add_argument(
        "--no-resume", action="store_true", help="do not reuse sessions found by tag"
    )
    runner.set_defaults(func=cmd_run)

    report = subparsers.add_parser("report", help="recompute metrics from a finished run")
    report.add_argument("--run", required=True, help="path to runs/<run_id>")
    report.add_argument("--run-id")
    report.add_argument("--transport", default="live")
    report.add_argument("--no-pr-required", action="store_true")
    report.set_defaults(func=cmd_report)

    schema = subparsers.add_parser("schema", help="print the default structured-output contract")
    schema.set_defaults(func=cmd_schema)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except SpecError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
