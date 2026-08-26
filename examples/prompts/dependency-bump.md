You are performing one unit of a mechanical migration across a repository.

Task: in `{{ path }}`, replace every call to `{{ old_api }}` with `{{ new_api }}`.

Rules:
- Change nothing outside `{{ path }}`. If the call site cannot be migrated
  without touching other files, stop and report `blocked`.
- Run `{{ verify_command }}` and report its real result. Do not report a command
  you did not run.
- If the tests were already failing before your change, that is `blocked`, not
  `completed`.
- Open one pull request titled `{{ pr_title }}` against the default branch.
- Do not change lockfiles, CI configuration, or security policy to make the
  verification pass. If that is the only way forward, report `blocked` and say
  what you would have had to weaken.

Finish by calling `provide_structured_output` with `is_final=true`, filling every
required field of the schema you were given.
