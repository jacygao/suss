---
settles: "§ 5 — on which senior design tasks a reward can be read when the validation agent alone tells a fix from none, and which the lane reports apart"
issue: [181]
recorded: 2026-11-05
versions: { senior-swe-bench: "v2026.06 (50 tasks)", harbor: 0.22.0, modal: 1.6.3, mini-swe-agent: 2.3.0, litellm: "litellm>=1.0,<1.106", validation-agent: "anthropic/claude-sonnet-4-6", host: "macOS arm64 client" }
recheck-when: Snorkel publishes a new snapshot or changes a design task's validation_spec.toml, run_validate.py or test.sh, mini-swe-agent's pin in test.sh moves off 2.3.0, or the default validation model changes
---

# The senior validation agent fails no change and passes the reference fix on 14 of the 15 design tasks it alone grades

## Intent

- On the 15 design tasks where only the validation agent tells a fix from
  none, does it fail an unchanged repository?
- Does it pass the task's reference solution there?
- Which of the 15 should the lane drop, or report apart?

## Decisions

- **Keep 14 of the 15.** On each, the validation agent failed no change, with
  a `validation_score` from 0.0 to 0.667, and passed the reference solution
  with 1.0, every story. No regrade gave a false pass or a false fail.
- **Report `gitea-add-project-column-picker` apart until a regrade of no
  change is valid.** Its one test passes on no change, so the validation agent
  is its only grader, and two of its three regrades gave no verdict. The one
  valid regrade passed the reference fix, 5 of 5 stories. Whether it fails no
  change is not known. Both invalid regrades came from the agent failing to get
  its own Go integration tests to run.
- **Read a no-change `validation_score` above 0 as the stories that guard
  existing behaviour, not as a partial pass.** Eight stories passed on no
  change, and each checks something the base already does, such as
  `lock_breaker_preserves_active_replication` or `acyclic_passes`.
  `create_exposes_version_one_metadata` passed because the base's `LLMPrompt`
  already has `version = models.PositiveIntegerField(default=1)`. The reward
  stays 0, because it needs every story.

## Needs your input

- **Whether the lane raises the verifier's timeout on design tasks.** This run
  tripled it. Two no-change regrades ran past their task's own
  `[verifier] timeout_sec`: `posthog-feat-schema-ingestion-block` took 2,905 s
  against 2,400, and `gitea-add-project-column-picker` 1,300 s against 1,200.
  With the task's own timeout, Harbor would have killed both and recorded them
  invalid. Four tasks set `va_timeout_sec` above `timeout_sec`:
  `firezone-feat-portal-add-recent`, `plausible-feat-shared-dashboard-deeplink`,
  `prefect-feat-dbt-per-node-hardening` and
  `turborepo-feat-add-circular-package`. I recommend the lane run Harbor with
  `--verifier-timeout-multiplier 3`, as here. It changes no verdict, only
  whether the verifier lives to write one.
- **Whether to spend about $4 on one more no-change regrade of
  `gitea-add-project-column-picker`.** It would settle whether that task can
  be kept. The budget ran out before it. I recommend it, and dropping the task
  if that regrade is invalid too.

## Reference

### Each task, on no change and on the reference fix

The score is `validation_score` from `verifier/validation_results.json`, with
stories passed in brackets. The tests column is `passed/total` from
`verifier/verifier_results.json` on no change.

| Task | Tests on no change | No change | Reference fix | Verdict |
|---|---|---|---|---|
| `firezone-feat-portal-add-recent` | 1/1 | 0.0 (0/5) | 1.0 (5/5) | keep |
| `gitea-add-project-column-picker` | 1/1 | invalid | invalid, then 1.0 (5/5) | report apart |
| `harbor-add-agent-file-retention` | 2/2 | 0.0 (0/3) | 1.0 (3/3) | keep |
| `harbor-add-multi-step-tasks` | 1/1 | 0.0 (0/3) | 1.0 (3/3) | keep |
| `immich-feat-recently-added-assets` | 2/2 | 0.0 (0/3) | 1.0 (3/3) | keep |
| `posthog-feat-llma-enable-tagger` | 2/2 | 0.6 (3/5) | 1.0 (5/5) | keep |
| `prefect-fix-subflow-cancellation` | 1/1 | 0.5 (1/2) | 1.0 (2/2) | keep |
| `teleport-add-traits-matching-logic` | 10/10 | 0.0 (0/2) | 1.0 (2/2) | keep |
| `turborepo-feat-add-circular-package` | 1/1 | 0.25 (1/4) | 1.0 (4/4) | keep |
| `electric-feat-sync-service-start` | none | 0.667 (2/3) | 1.0 (3/3) | keep |
| `harbor-add-windows-tasks-support` | none | 0.0 (0/3) | 1.0 (3/3) | keep |
| `plausible-feat-shared-dashboard-deeplink` | none | 0.0 (0/3) | 1.0 (3/3) | keep |
| `posthog-feat-prompt-versioning` | none | 0.167 (1/6) | 1.0 (6/6) | keep |
| `posthog-feat-schema-ingestion-block` | none | 0.0 (0/7) | 1.0 (7/7) | keep |
| `prefect-feat-dbt-per-node-hardening` | none | 0.0 (0/5) | 1.0 (5/5) | keep |

The stories that passed on no change:

- `electric-feat-sync-service-start`: `lock_breaker_frees_abandoned_lock` and
  `lock_breaker_preserves_active_replication`, whose descriptions say the
  breaker's behaviour "must be preserved"
- `posthog-feat-llma-enable-tagger`: `update_allows_soft_delete`,
  `valid_create_succeeds` and `valid_update_succeeds`
- `posthog-feat-prompt-versioning`: `create_exposes_version_one_metadata`
- `prefect-fix-subflow-cancellation`:
  `non_cancelled_nested_flow_completes_normally`
- `turborepo-feat-add-circular-package`: `acyclic_passes`

Every story that failed on no change passed on the reference fix. All but one
of the no-change failures were assertions about the missing change, such as
`expected users [dave erin], got [dave erin frank grace]` or `Cannot find
module '/repo/posthog/nodejs/src/ingestion/event-preprocess…`. The exception,
`enforcement_toggle_renders_in_event_definition_schema_view`, failed with
`no Jest output (exit 1)` and `execution` `unsure`. No regrade set
`review_judge_discard_recommended`.

### What the gitea regrades did

- **No change.** The agent's first run hit its 900 s `va_timeout_sec`. Its last
  call, by the number of messages it carried, got no answer: the proxy gave
  up on it after 900 s and returned 502. The retry could not get its scripts past `build failed`, so it wrote
  `move_helpers_test.go` and `sidebar_writer_picker_test.go` into
  `/repo/gitea/tests/integration/` to compile them there. It left them there,
  the integrity check found them, and `run_validate.py` exited 3.
- **Reference fix, first regrade.** The smoke test of every story read `not
  found in go test output (exit 1)`, so none of the agent's scripts produced a
  result. The retry used all 125 of its steps checking imports and types and
  ended `LimitsExceeded`, which `run_validate.py` counts as an infrastructure
  failure.
- **Reference fix, rerun.** The first pass took 67 steps and the retry 17, and
  all 5 stories passed.

Each invalid regrade wrote an empty `reward.txt`, which Harbor records as
`RewardFileEmptyError`.

### The runs

From `/Users/jacy/Documents/Dev/suss-runs/m5/181-validation-reliability/`, on
Modal, over a copy of the 15 tasks. Each copy's `test.sh` pins
`litellm>=1.0,<1.106` and skips the rubric and taste judges, which
`reward.txt` does not read. Nothing else in the task changed. With
`ANTHROPIC_API_KEY` set and `OPENAI_API_KEY` and `PORTKEY_API_KEY` unset:

```bash
harbor run -y -p tasks -e modal -n 8 -k 1 -a <nop|oracle> [-i <task> ...] \
  --verifier-timeout-multiplier 3 \
  --ve ANTHROPIC_BASE_URL=<capping proxy URL> \
  --ek app_name=suss-181-<nop|oracle> --jobs-dir jobs --job-name <job>
```

- **No change** is Harbor's `nop` agent. All 15 `verifier/agent.patch` files
  are empty.
- **The reference fix** is Harbor's `oracle` agent. Every `agent/oracle.txt`
  says `Fix applied via oracle.patch`.
- **Every model call went through a capping proxy**, deployed as its own Modal
  app. It logged each call's usage, priced it at list price, and refused any
  call that would take the logged total past a cap. The cap was $35, then $45.
  No call was refused. In each job, the proxy's count of validation-agent
  calls equals the sum of the trajectories' `api_calls`. The no-change job is
  the one exception: its count is 2 higher, for the two calls that returned
  502. Every regrade made at least 7 calls, so none failed for want of the
  model.

The no-change job took 53 minutes. The reference fixes ran in seven smaller
jobs, so the spend could be checked between them.

### Spend

Counted from the proxy's log, which priced every call at Sonnet 4.6's list
price: $3 and $15 per million input and output tokens, $3.75 per million for a
cache write and $0.30 for a cache read.

| Run | Calls | Spend |
|---|---|---|
| No change, 15 regrades | 1,118 | $24.71 |
| Reference fix, 16 regrades | 789 | $17.98 |
| A check call to the proxy | 1 | under $0.01 |
| All | 1,908 | $42.68 |

The validation agent made 1,846 of the calls. The review judge made 61, two in
each regrade but the invalid no-change gitea one, for $5.06. The agent spent
$0.12 to $4.43 a regrade, in 7 to 169 steps. A regrade cost more when the
agent's scripts failed their smoke test and it retried. Modal billed about
$4.25 for the two jobs' apps and the proxy, read soon after the last job.

## Limits

- Each task was regraded once on each side. Whether the agent's verdicts are
  stable is not shown, and one regrade per side cannot say how often it
  would pass no change or fail the fix.
- Only no change and the reference fix were graded. A wrong or partial fix can
  still pass the validation agent on any of the 14.
- Only `create_exposes_version_one_metadata` was checked for a bent test. That
  every story failing on no change passed on the fix shows those stories tell
  the two apart. It does not show that each passing test was faithful to its
  story.
- The verifier ran with three times its timeout, so what the task's own
  timeout does to a regrade is inferred from the verifier's times, not seen.
- Only Modal was run, on amd64.
