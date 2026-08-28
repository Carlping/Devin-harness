You are performing one small, read-only task against
https://github.com/Carlping/Devin-harness (clone it if it is not already in your
workspace). Do not change any files, do not commit, and do not open a pull
request.

Task: {{ question }}

Rules:
- Answer only from the repository contents.
- Then run this command exactly as written and report its real result in the
  `verification` field: `{{ verify_command }}`
- Report `verification.passed` as whatever the command actually did. If it
  exited non-zero, or could not run at all, `passed` must be false and `detail`
  must contain the real error output. Never report a command you did not run,
  and never substitute a different command that succeeds.
- Choose `outcome` for the task itself, not for the command: `completed` if you
  answered the question, `blocked` if something outside your control stopped you
  from answering it.
- Put the actual answer in `summary`, in at most three sentences.

Finish by calling `provide_structured_output` with `is_final=true`, filling every
required field of the schema you were given. Leave `pr_url` as null.
