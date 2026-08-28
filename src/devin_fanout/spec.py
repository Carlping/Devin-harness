"""Run specification loading and validation.

Everything here is fail-closed: an unknown key, a missing template variable or a
task list with duplicate ids is an error, never a warning. A harness that
silently drops half a task list produces a metrics table that is wrong in the
most expensive direction — it looks fine.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

TEMPLATE_PATTERN = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")

SPEC_KEYS = {
    "run_id",
    "org_id",
    "prompt_template",
    "tasks",
    "repos",
    "concurrency",
    "poll_interval_seconds",
    "timeout_minutes",
    "devin_mode",
    "max_acu_limit",
    "tags",
    "playbook_id",
    "structured_output_schema",
    "require_pr",
    "mutates_repo",
}
REQUIRED_SPEC_KEYS = {"run_id", "prompt_template", "tasks"}
TASK_KEYS = {"id", "vars", "repos", "notes"}
DEVIN_MODES = {"normal", "fast", "lite", "ultra", "fusion"}
RUN_ID_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$")


class SpecError(ValueError):
    """Raised when a run specification or task list cannot be trusted."""


@dataclass(frozen=True)
class Task:
    id: str
    prompt: str
    repos: tuple[str, ...]
    notes: str = ""
    vars: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RunSpec:
    run_id: str
    tasks: tuple[Task, ...]
    repos: tuple[str, ...] = ()
    org_id: str | None = None
    concurrency: int = 4
    poll_interval_seconds: int = 30
    timeout_minutes: int = 90
    devin_mode: str | None = None
    max_acu_limit: int | None = None
    tags: tuple[str, ...] = ()
    playbook_id: str | None = None
    require_pr: bool = True
    mutates_repo: bool | None = None
    structured_output_schema: dict[str, Any] = field(default_factory=dict)

    @property
    def run_tag(self) -> str:
        return f"fanout-run:{self.run_id}"


def _read_yaml(path: Path) -> Any:
    if not path.is_file():
        raise SpecError(f"file not found: {path}")
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:  # pragma: no cover - message depends on PyYAML
        raise SpecError(f"{path} is not valid YAML: {exc}") from exc


def render_prompt(template: str, variables: dict[str, Any], task_id: str) -> str:
    """Substitute ``{{ name }}`` placeholders, refusing to leave any unresolved."""
    required = set(TEMPLATE_PATTERN.findall(template))
    provided = set(variables)
    missing = sorted(required - provided)
    if missing:
        raise SpecError(f"task {task_id!r} is missing prompt variables: {', '.join(missing)}")
    unused = sorted(provided - required)
    if unused:
        raise SpecError(
            f"task {task_id!r} defines variables the prompt never uses: {', '.join(unused)}"
        )
    return TEMPLATE_PATTERN.sub(lambda m: str(variables[m.group(1)]), template)


def load_tasks(path: Path, template: str, default_repos: tuple[str, ...]) -> tuple[Task, ...]:
    raw = _read_yaml(path)
    if not isinstance(raw, list) or not raw:
        raise SpecError(f"{path} must contain a non-empty YAML list of tasks")

    tasks: list[Task] = []
    seen: set[str] = set()
    for index, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise SpecError(f"{path}[{index}] must be a mapping")
        unknown = sorted(set(entry) - TASK_KEYS)
        if unknown:
            raise SpecError(f"{path}[{index}] has unknown keys: {', '.join(unknown)}")
        task_id = entry.get("id")
        if not isinstance(task_id, str) or not task_id.strip():
            raise SpecError(f"{path}[{index}] needs a non-empty string id")
        if task_id in seen:
            raise SpecError(f"duplicate task id {task_id!r} in {path}")
        seen.add(task_id)

        variables = entry.get("vars", {})
        if not isinstance(variables, dict):
            raise SpecError(f"task {task_id!r}: vars must be a mapping")
        repos = entry.get("repos", list(default_repos))
        if not isinstance(repos, list) or not all(isinstance(item, str) for item in repos):
            raise SpecError(f"task {task_id!r}: repos must be a list of strings")

        tasks.append(
            Task(
                id=task_id,
                prompt=render_prompt(template, variables, task_id),
                repos=tuple(repos),
                notes=str(entry.get("notes", "")),
                vars=dict(variables),
            )
        )
    return tuple(tasks)


def load_spec(path: Path) -> RunSpec:
    raw = _read_yaml(path)
    if not isinstance(raw, dict):
        raise SpecError(f"{path} must contain a YAML mapping")

    unknown = sorted(set(raw) - SPEC_KEYS)
    if unknown:
        raise SpecError(f"{path} has unknown keys: {', '.join(unknown)}")
    missing = sorted(REQUIRED_SPEC_KEYS - set(raw))
    if missing:
        raise SpecError(f"{path} is missing required keys: {', '.join(missing)}")

    run_id = str(raw["run_id"])
    if not RUN_ID_PATTERN.match(run_id):
        raise SpecError(
            f"run_id {run_id!r} must match {RUN_ID_PATTERN.pattern} (it becomes a directory name)"
        )

    base = path.parent
    template_path = base / str(raw["prompt_template"])
    if not template_path.is_file():
        raise SpecError(f"prompt template not found: {template_path}")
    template = template_path.read_text(encoding="utf-8")

    repos = tuple(raw.get("repos", ()) or ())
    if not all(isinstance(item, str) for item in repos):
        raise SpecError("repos must be a list of strings")

    tasks = load_tasks(base / str(raw["tasks"]), template, repos)

    devin_mode = raw.get("devin_mode")
    if devin_mode is not None and devin_mode not in DEVIN_MODES:
        raise SpecError(f"devin_mode must be one of {sorted(DEVIN_MODES)}, got {devin_mode!r}")

    concurrency = int(raw.get("concurrency", 4))
    if concurrency < 1:
        raise SpecError("concurrency must be >= 1")
    poll_interval = int(raw.get("poll_interval_seconds", 30))
    if poll_interval < 5:
        raise SpecError("poll_interval_seconds must be >= 5 to stay within API rate limits")
    timeout_minutes = int(raw.get("timeout_minutes", 90))
    if timeout_minutes < 1:
        raise SpecError("timeout_minutes must be >= 1")

    schema = raw.get("structured_output_schema")
    if schema is not None and not isinstance(schema, dict):
        raise SpecError("structured_output_schema must be a mapping when set")
    mutates_repo = raw.get("mutates_repo")
    if mutates_repo is not None and not isinstance(mutates_repo, bool):
        raise SpecError("mutates_repo must be a boolean when set")

    return RunSpec(
        run_id=run_id,
        tasks=tasks,
        repos=repos,
        org_id=raw.get("org_id"),
        concurrency=concurrency,
        poll_interval_seconds=poll_interval,
        timeout_minutes=timeout_minutes,
        devin_mode=devin_mode,
        max_acu_limit=raw.get("max_acu_limit"),
        tags=tuple(raw.get("tags", ()) or ()),
        playbook_id=raw.get("playbook_id"),
        require_pr=bool(raw.get("require_pr", True)),
        mutates_repo=mutates_repo,
        structured_output_schema=schema or {},
    )
