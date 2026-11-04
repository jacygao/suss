---
settles: "§ 5 — which senior tasks a fix can pass, and so which the lane keeps; § 3 — what a senior regrade with no grading key writes for a design task"
issue: [155]
recorded: 2026-11-04
versions: { senior-swe-bench: "v2026.06 (50 tasks)", harbor: 0.22.0, modal: 1.6.3, host: "macOS arm64 client" }
recheck-when: Snorkel publishes a new snapshot or changes test.sh, run_verify.py or run_validate.py, or Harbor upgrades past 0.22.0
---

# Harbor's oracle passes the tests of all 44 senior tasks that have them

## Intent

- Which of the 50 senior tasks does Harbor's oracle fail on Modal, and why?
- What does the oracle's reward mean on a design task when the verifier has no
  grading key?
- What should the lane do with the 6 tasks that have no tests?
- What does a run over the lane cost and take on Modal?

## Decisions

- **Keep every senior task with tests: none fails the oracle.** In all 44
  tasks with tests, the oracle patch applied and every test passed. Nothing
  here is ungradable in the way the three dropped rebench tasks are.
- **Read a design task's test result from `verifier/verifier_results.json`
  when the verifier has no key, not from the reward.** With no key, all 25
  design trials wrote an empty `reward.txt` and Harbor raised
  `RewardFileEmptyError`. `run_validate.py` raises
  `RuntimeError: Validation requires PORTKEY_API_KEY, ANTHROPIC_API_KEY, or OPENAI_API_KEY to be set`
  before the validation agent starts, and `test.sh` empties the reward on its
  exit 2. The tests had already run by then. So a senior regrade without a key
  invalidates every design trial, whatever the patch.
- **Keep the 6 no-test tasks in the lane for now.** Their image builds, the
  oracle patch applies, and the verifier runs to the validation step. What
  they lack is evidence that the validation agent passes the oracle, and
  dropping them for that would drop them for a check not yet run.
- **Budget about $1.60 and 30 minutes on Modal for one oracle pass over the
  lane.** Modal billed $1.52 for the two jobs, which took 3 min 44 s at `-n 2`
  and 23 min 4 s at `-n 8`.

## Needs your input

- **What the lane does with the 6 no-test tasks:**
  `electric-feat-sync-service-start`, `harbor-add-windows-tasks-support`,
  `plausible-feat-shared-dashboard-deeplink`, `posthog-feat-prompt-versioning`,
  `posthog-feat-schema-ingestion-block` and
  `prefect-feat-dbt-per-node-hardening`. Their reward is the validation
  agent's alone, and `verifier_results.json` holds `0/0`, so this run says
  nothing about whether a fix can pass them. I recommend the lane keep them,
  and that #166 regrade the oracle patch on these six first, with the key.
  Then drop any whose validation agent fails the oracle, as rebench dropped
  the tasks whose tests no fix passes.
- **Whether the 19 design tasks' tests catch a missing fix.** Many of them
  read as regression guards: `test_single_step_trial_unchanged`,
  `test_personhog_writer_compiles`, `test_changed_crates_compile`, and 10 of the
  19 have a single test. If an empty patch passes them, the validation agent
  alone tells a fix from no fix on 25 tasks. I recommend an empty-patch regrade
  of the 25 design tasks, with no key, as rebench had. It costs about what this
  run did.

## Reference

### The lane, by language

Language is the first entry of the task's `[metadata.taxonomy] stack`.

| Language | With tests | No tests | All |
|---|---|---|---|
| Elixir | 8 | 2 | 10 |
| Go | 8 | 0 | 8 |
| Python | 14 | 4 | 18 |
| Rust | 6 | 0 | 6 |
| TypeScript | 8 | 0 | 8 |
| All | 44 | 6 | 50 |

- **Elixir:** `electric-feat-add-variadic-function`,
  `electric-fix-classify-admission-control`, `electric-fix-elixir-client-cache`,
  `electric-fix-resolve-pending-shapes`, `electric-perf-array-filter-eval`,
  `firezone-feat-portal-add-recent`, `plausible-fix-cross-site-resource-attach`,
  `plausible-fix-top-pages-comparison`; no tests:
  `electric-feat-sync-service-start`, `plausible-feat-shared-dashboard-deeplink`
- **Go:** `gitea-add-project-column-picker`, `gitea-feat-fast-forward-only`,
  `gitea-fix-codeql-code-scanning`, `gitea-fix-diff-highlight-overlap`,
  `gitea-fix-force-push-timeline`, `gitea-refactor-auth-middleware`,
  `teleport-add-traits-matching-logic`, `teleport-fix-scoped-kube-clusters`
- **Python:** `harbor-add-agent-file-retention`, `harbor-add-multi-step-tasks`,
  `harbor-refactor-optional-sandbox-deps`,
  `paperless-ngx-feat-saved-view-sharing`, `paperless-ngx-perf-document-counts`,
  `paperless-ngx-perf-workflow-queries`, `paperless-ngx-refactor-task-system`,
  `posthog-feat-approval-gating`, `posthog-feat-llma-enable-tagger`,
  `posthog-feat-subscriptions-auto-disable`, `posthog-fix-llm-gateway-add`,
  `prefect-fix-resolve-race-condition`, `prefect-fix-subflow-cancellation`,
  `prefect-fix-task-run-recorder`; no tests:
  `harbor-add-windows-tasks-support`, `posthog-feat-prompt-versioning`,
  `posthog-feat-schema-ingestion-block`, `prefect-feat-dbt-per-node-hardening`
- **Rust:** `firezone-fix-connlib-align-device`,
  `posthog-feat-personhog-writer-add`, `turborepo-feat-add-circular-package`,
  `turborepo-fix-preserve-package-json`, `turborepo-fix-prune-missing-sources`,
  `turborepo-perf-reuse-input-hashes`
- **TypeScript:** `better-auth-fix-api-key-run`,
  `better-auth-fix-api-return-response`, `better-auth-fix-oauth-provider-return`,
  `better-auth-fix-resolve-dynamic-baseurl`, `immich-feat-recently-added-assets`,
  `immich-feat-release-candidate-detection`, `immich-fix-server-live-photo`,
  `posthog-fix-replay-buffering`

The 25 investigate tasks scored `reward` 1.0. The 19 design tasks with tests
were invalid, with every test passed.

### The runs

From a directory holding an unchanged copy of the download as `tasks/`, with
`ANTHROPIC_API_KEY`, `OPENAI_API_KEY` and `PORTKEY_API_KEY` unset, so that
every `[verifier.env]` key resolves empty:

```bash
harbor run -y -p tasks -e modal -n 2 -k 1 -a oracle \
  -i electric-feat-add-variadic-function -i prefect-feat-dbt-per-node-hardening \
  --ek app_name=suss-155-sample --jobs-dir jobs --job-name sample
harbor run -y -p tasks -e modal -n 8 -k 1 -a oracle \
  -x electric-feat-add-variadic-function -x prefect-feat-dbt-per-node-hardening \
  --ek app_name=suss-155-full --jobs-dir jobs --job-name full
modal billing report --for today -r h --json
```

`-i` and `-x` match the directory's name, not the `snorkel-ai/` name in
`task.toml`. Every `solve.sh` is the same script, which runs
`git apply /solution/oracle.patch` and prints `Fix applied via oracle.patch`.
That line is in every trial's `agent/oracle.txt`.

### What each trial wrote

| Tasks | `reward.txt` | `verifier_result` | `exception_info` | `verifier_results.json` |
|---|---|---|---|---|
| 25 investigate | `1` | `reward` 1.0 | none | every test passed |
| 19 design, with tests | empty | `null` | `RewardFileEmptyError` | every test passed |
| 6 design, no tests | empty | `null` | `RewardFileEmptyError` | `passed` 0, `total` 0, `all_pass` true |

Each of the 50 `verifier/test-stdout.txt` files was checked for the
`Validation requires` error. It is in the 25 design trials and in no other.
No `verifier_results.json` holds `runner_errors`, and none of the 44 with
tests holds a failed test. Every `judge_output.json` records
`rubric_status` and `taste_status` as `skipped:no_api_key`, so no judge made a
call. A task with no `tests/verify/` and a `validation_spec.toml` gets the
vacuous `0/0` from `run_verify.py`, which is why the six count as design tasks
with no tests rather than as runner failures.

### Cost and time

| Job | Trials | Wall time | Modal |
|---|---|---|---|
| `suss-155-sample`, `-n 2` | 2 | 3 min 44 s | $0.05 |
| `suss-155-full`, `-n 8` | 48 | 23 min 4 s | $1.47 |

The figures are `modal billing report` by app, read three minutes after the
full job ended. In the same hour it billed $0.49 to `__harbor__`, Harbor's
default app name, which had sandboxes of its own running at the time. How
much of that these jobs caused is not known.

No trial failed to start or raised anything but `RewardFileEmptyError`, so
nothing was rerun. The client logged `App create rate limit exceeded` at the
start of the full job and retried until each sandbox started. The longest
trial was `posthog-feat-subscriptions-auto-disable`, at 12 min 39 s.

## Limits

- Each task ran once. A flaky test that passed this time was not looked for.
- No task was run with an empty patch, so this does not show that a task's
  tests fail unfixed.
- The validation agent did not run. Whether it passes the oracle on any design
  task is not known, and on the six no-test tasks that is the whole question.
- Only Modal was run, on amd64. Whether the `gitea-*`, `teleport-*` and
  `turborepo-*` images build and pass on an arm64 Docker host was not tested.
- An earlier, abandoned attempt ran on the same Modal workspace, and Modal may
  have reused images it built, so a fresh workspace may pay more for builds.
