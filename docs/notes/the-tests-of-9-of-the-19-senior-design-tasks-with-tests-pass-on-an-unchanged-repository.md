---
settles: "§ 5 — on which senior design tasks the tests alone tell a fix from no fix, and so on which the validation agent alone decides the reward"
issue: [179]
recorded: 2026-11-05
versions: { senior-swe-bench: "v2026.06 (50 tasks)", harbor: 0.22.0, modal: 1.6.3, host: "macOS arm64 client" }
recheck-when: Snorkel publishes a new snapshot or changes a design task's tests/verify/, test.sh or run_verify.py, or Harbor upgrades past 0.22.0
---

# The tests of 9 of the 19 senior design tasks with tests pass on an unchanged repository

## Intent

- For each of the 19 design tasks with tests, do the tests fail when nothing
  is changed?
- On which design tasks does only the validation agent tell a fix from none?

## Decisions

- **Treat the validation agent as the only grader that tells a fix from none
  on 15 of the 25 design tasks.** On 9 tasks every test passed with no
  change, and the other 6 have no tests. On these 15, a trial that changes
  nothing gets reward 1 if the validation agent passes it, so a reward there
  measures the validation agent as much as the change.
- **Count the tests as a check on the other 10 design tasks.** On each, at
  least one test failed on the unchanged repository, and every failure is an
  assertion about the missing change, not a build, download or runner error.

## Needs your input

- **Whether the validation agent fails an unchanged repository on the 15
  tasks where nothing else can.** This run did not start it. A validation agent
  that passed a story once by bending its test may pass an empty patch too, and
  on these 15 nothing would catch it. I recommend regrading the empty patch on
  those 15 with the key, at about a dollar a regrade, before the lane's design
  rewards are read as resolving anything. Drop any task whose validation agent
  passes no change.

## Reference

### Each design task with tests, on no change

`passed/total` is from `verifier/verifier_results.json`. No file held
`runner_errors`.

| Task | Passed | Tests catch no fix |
|---|---|---|
| `electric-feat-add-variadic-function` | 0/12 | yes |
| `gitea-feat-fast-forward-only` | 0/1 | yes |
| `harbor-refactor-optional-sandbox-deps` | 0/2 | yes |
| `paperless-ngx-feat-saved-view-sharing` | 0/1 | yes |
| `paperless-ngx-refactor-task-system` | 0/2 | yes |
| `posthog-feat-approval-gating` | 0/1 | yes |
| `posthog-feat-personhog-writer-add` | 0/1 | yes |
| `posthog-feat-subscriptions-auto-disable` | 0/1 | yes |
| `gitea-refactor-auth-middleware` | 1/4 | yes |
| `immich-feat-release-candidate-detection` | 5/10 | yes |
| `firezone-feat-portal-add-recent` | 1/1 | no |
| `gitea-add-project-column-picker` | 1/1 | no |
| `harbor-add-agent-file-retention` | 2/2 | no |
| `harbor-add-multi-step-tasks` | 1/1 | no |
| `immich-feat-recently-added-assets` | 2/2 | no |
| `posthog-feat-llma-enable-tagger` | 2/2 | no |
| `prefect-fix-subflow-cancellation` | 1/1 | no |
| `teleport-add-traits-matching-logic` | 10/10 | no |
| `turborepo-feat-add-circular-package` | 1/1 | no |

The 9 that pass are regression guards by name:
`test_single_step_trial_unchanged`, `test_changed_crates_compile`,
`TestVerifySearchNoRegression/*` with `verify_build`,
`test_run_launcher_help_still_works`, and `timeline default ordering preserved
(pass-to-pass)` among them.

The six design tasks with no tests hold `0/0` with `all_pass` true, as under
the oracle: `electric-feat-sync-service-start`,
`harbor-add-windows-tasks-support`, `plausible-feat-shared-dashboard-deeplink`,
`posthog-feat-prompt-versioning`, `posthog-feat-schema-ingestion-block` and
`prefect-feat-dbt-per-node-hardening`.

`paperless-ngx-feat-saved-view-sharing` counts one test, `shell::verify`, for a
`verify.sh` that runs three pytest tests. All three failed, the first with
`No migration exists after '0014_alter_paperlesstask_task_name'`.

### The run

From a directory holding a copy of the 25 design tasks as `tasks/` (those with
a `tests/validate/`), with `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` and
`PORTKEY_API_KEY` unset:

```bash
harbor run -y -p tasks -e modal -n 8 -k 1 -a nop \
  --ek app_name=suss-179-nop --jobs-dir jobs --job-name full
```

Harbor's `nop` agent has an empty `setup` and `run`. That it changed nothing
shows in each trial's `verifier/agent.patch`, which `test.sh` writes as the
diff from the image's base commit to everything in `/repo` at the end of the
agent phase: all 25 are empty, where the oracle's were not. As with the
oracle, every trial raised `RewardFileEmptyError` after `run_validate.py`
refused to start with `Validation requires PORTKEY_API_KEY, ANTHROPIC_API_KEY,
or OPENAI_API_KEY to be set`, and the tests had run before it.

The job took 11 min 42 s. Modal billed $0.78 to `suss-179-nop`. The longest
trial was `posthog-feat-subscriptions-auto-disable`, at 702 s, so it set the
job's length.

## Limits

- Each task ran once, so a flaky test is not ruled out. A test that passes on
  no change only some of the time would move a task between the two groups.
- The 10 tasks whose tests catch no fix may still pass a wrong fix. This shows
  only that no change fails them.
- The validation agent did not run, so whether it fails an unchanged
  repository on any task is not known.
- Only Modal was run, on amd64.
