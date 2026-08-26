You are performing one small, read-only audit task against
https://github.com/Carlping/Devin-harness (clone it if it is not already in your
workspace). Do not change any files, do not commit, and do not open a pull
request.

Task: {{ question }}

Rules:
- Answer only from the repository contents. If the answer is not in the repo,
  report `blocked` rather than guessing.
- Run `{{ verify_command }}` and report its real result in the `verification`
  field. If the command is not runnable in this environment, report `blocked`
  and say why. Never report a command you did not run.
- If completing this task would require a credential, a network resource or an
  approval you do not have, report `blocked` and list the missing thing in
  `blockers`. Do not work around it.
- Keep `summary` to at most three sentences and put the actual answer in it.

Finish by calling `provide_structured_output` with `is_final=true`, filling every
required field of the schema you were given. Leave `pr_url` as null.
