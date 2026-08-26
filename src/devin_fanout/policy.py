"""Fail-closed policy checks before a fan-out can spend or call an API.

The policy keeps work that needs a human decision out of an automated batch:
re-running an agent cannot repair an unknown credential, an authorization
choice, or an irreversible production action. It also makes resource ceilings
and evidence requirements reviewable configuration instead of hidden code.
There is no bypass flag: the only way past a rule is editing the policy file,
which makes the exception a reviewable diff.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .spec import RunSpec

POLICY_KEYS = {
    "verification_vars",
    "human_decision_classes",
    "max_concurrency",
    "max_acu_limit",
    "max_timeout_minutes",
    "banned_prompt_phrases",
}
RULE_NAMES = (
    "verification-required",
    "human-decision-classes",
    "repo-allowlist",
    "write-requires-pr",
    "blast-radius-ceiling",
    "no-self-grading",
)


class PolicyError(ValueError):
    """Raised when a policy file cannot be trusted."""


@dataclass(frozen=True)
class Violation:
    rule: str
    message: str
    task_id: str | None = None


@dataclass(frozen=True)
class DecisionClass:
    name: str
    patterns: tuple[str, ...]


@dataclass(frozen=True)
class Policy:
    verification_vars: tuple[str, ...] = ("verify_command",)
    human_decision_classes: tuple[DecisionClass, ...] = ()
    max_concurrency: int = 5
    max_acu_limit: int = 10
    max_timeout_minutes: int = 60
    banned_prompt_phrases: tuple[str, ...] = ()

    @property
    def rules_evaluated(self) -> int:
        return len(RULE_NAMES)


def _read_yaml(path: Path) -> Any:
    if not path.is_file():
        raise PolicyError(f"policy file not found: {path}")
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:  # pragma: no cover - message depends on PyYAML
        raise PolicyError(f"{path} is not valid YAML: {exc}") from exc


def _string_list(value: Any, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise PolicyError(f"{field_name} must be a list of strings")
    if any(not item.strip() for item in value):
        raise PolicyError(f"{field_name} cannot contain empty strings")
    return tuple(value)


def _positive_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise PolicyError(f"{field_name} must be a positive integer")
    return value


def load_policy(path: Path) -> Policy:
    raw = _read_yaml(path)
    if not isinstance(raw, dict):
        raise PolicyError(f"{path} must contain a YAML mapping")
    unknown = sorted(set(raw) - POLICY_KEYS)
    if unknown:
        raise PolicyError(f"{path} has unknown keys: {', '.join(unknown)}")
    if "human_decision_classes" not in raw:
        raise PolicyError(f"{path} is missing required key: human_decision_classes")

    verification_vars = _string_list(
        raw.get("verification_vars", ["verify_command"]), "verification_vars"
    )
    classes_raw = raw["human_decision_classes"]
    if not isinstance(classes_raw, dict):
        raise PolicyError("human_decision_classes must be a mapping")
    classes: list[DecisionClass] = []
    for name, patterns in classes_raw.items():
        if not isinstance(name, str) or not name.strip():
            raise PolicyError("human_decision_classes names must be non-empty strings")
        classes.append(DecisionClass(name=name, patterns=_string_list(patterns, f"class {name}")))

    banned = _string_list(raw.get("banned_prompt_phrases", []), "banned_prompt_phrases")
    return Policy(
        verification_vars=verification_vars,
        human_decision_classes=tuple(classes),
        max_concurrency=_positive_int(raw.get("max_concurrency", 5), "max_concurrency"),
        max_acu_limit=_positive_int(raw.get("max_acu_limit", 10), "max_acu_limit"),
        max_timeout_minutes=_positive_int(
            raw.get("max_timeout_minutes", 60), "max_timeout_minutes"
        ),
        banned_prompt_phrases=banned,
    )


def _is_empty(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def evaluate(spec: RunSpec, policy: Policy) -> tuple[Violation, ...]:
    """Return every policy violation found in ``spec``."""
    violations: list[Violation] = []
    for task in spec.tasks:
        missing = [name for name in policy.verification_vars if _is_empty(task.vars.get(name))]
        if missing:
            violations.append(
                Violation(
                    "verification-required",
                    f"missing non-empty verification vars: {', '.join(missing)}",
                    task.id,
                )
            )

        searchable = f"{task.prompt}\n{task.notes}".casefold()
        for decision_class in policy.human_decision_classes:
            matches = [
                pattern for pattern in decision_class.patterns if pattern.casefold() in searchable
            ]
            if matches:
                violations.append(
                    Violation(
                        "human-decision-classes",
                        f"matches {decision_class.name}: {', '.join(matches)}",
                        task.id,
                    )
                )

        if not task.repos:
            violations.append(
                Violation("repo-allowlist", "task must resolve to at least one repo", task.id)
            )
        for repo in task.repos:
            if "*" in repo:
                violations.append(
                    Violation("repo-allowlist", f"wildcard repo is not allowed: {repo}", task.id)
                )
            if repo not in spec.repos:
                violations.append(
                    Violation(
                        "repo-allowlist",
                        f"task repo is not in the spec allowlist: {repo}",
                        task.id,
                    )
                )

        for phrase in policy.banned_prompt_phrases:
            if phrase.casefold() in searchable:
                violations.append(
                    Violation(
                        "no-self-grading",
                        f"prompt contains banned phrase: {phrase}",
                        task.id,
                    )
                )

    if spec.mutates_repo is None:
        violations.append(
            Violation("write-requires-pr", "declare mutates_repo: the harness will not guess")
        )
    elif spec.mutates_repo and not spec.require_pr:
        violations.append(
            Violation("write-requires-pr", "mutates_repo: true requires require_pr: true")
        )

    if spec.max_acu_limit is None:
        violations.append(Violation("blast-radius-ceiling", "max_acu_limit must be set"))
    elif spec.max_acu_limit > policy.max_acu_limit:
        violations.append(
            Violation(
                "blast-radius-ceiling",
                f"max_acu_limit {spec.max_acu_limit} exceeds policy ceiling {policy.max_acu_limit}",
            )
        )
    if spec.concurrency > policy.max_concurrency:
        violations.append(
            Violation(
                "blast-radius-ceiling",
                f"concurrency {spec.concurrency} exceeds policy ceiling {policy.max_concurrency}",
            )
        )
    if spec.timeout_minutes > policy.max_timeout_minutes:
        violations.append(
            Violation(
                "blast-radius-ceiling",
                f"timeout_minutes {spec.timeout_minutes} exceeds policy ceiling "
                f"{policy.max_timeout_minutes}",
            )
        )
    return tuple(violations)
