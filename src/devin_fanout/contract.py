"""The reporting contract every fanned-out session must satisfy.

This schema is the whole reason the harness can produce a table instead of a
pile of prose. Two properties matter more than the field list:

* ``outcome`` is deliberately coarse and includes ``blocked``. An agent that
  cannot finish should say so in one word rather than describing a workaround.
* ``verification`` forces the agent to name the command it ran and its exit
  status. "I believe this works" is not a value this schema can express.

Nothing in here is trusted as proof: it is the agent's *self-report*, and the
report compares it against external evidence (PR state) on purpose.
"""

from __future__ import annotations

from typing import Any

DEFAULT_STRUCTURED_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["outcome", "summary", "verification", "human_action_required"],
    "properties": {
        "outcome": {
            "type": "string",
            "enum": ["completed", "partial", "blocked", "not_attempted"],
            "description": (
                "completed = the task is done and verified; partial = some of it landed; "
                "blocked = something outside your control stopped you; "
                "not_attempted = you decided the task should not be done as written."
            ),
        },
        "summary": {
            "type": "string",
            "minLength": 1,
            "description": "What you changed and why, in at most three sentences.",
        },
        "verification": {
            "type": "object",
            "additionalProperties": False,
            "required": ["command", "passed"],
            "properties": {
                "command": {
                    "type": "string",
                    "description": (
                        "The exact command whose result you are reporting, e.g. 'pytest -q'. "
                        "Use the empty string only if you ran nothing, and then passed must "
                        "be false."
                    ),
                },
                "passed": {"type": "boolean"},
                "detail": {"type": "string", "description": "Failure output or a short note."},
            },
        },
        "pr_url": {
            "type": ["string", "null"],
            "description": "The pull request you opened, or null if you opened none.",
        },
        "human_action_required": {
            "type": "string",
            "description": (
                "What a human must do before this task can be considered finished. "
                "Use the empty string if nothing is needed."
            ),
        },
        "blockers": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Missing credentials, ambiguous requirements, broken environment, etc.",
        },
        "files_changed": {"type": "integer", "minimum": 0},
    },
}
