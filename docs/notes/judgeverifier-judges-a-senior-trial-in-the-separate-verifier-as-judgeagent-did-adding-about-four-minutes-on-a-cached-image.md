---
settles: "§ 3 — whether a senior trial can be judged inside Harbor's separate verifier, what that adds to the verifier's time, and what the verifier timeout must make room for; § 5 — how much each senior task's verifier timeout gains"
issue: [221]
recorded: 2026-11-08
versions: { harbor: 0.22.0, copilot-cli: 1.0.105, modal: 1.6.3, environment: modal, lane: "senior, paperless-ngx-perf-workflow-queries", judge: "gemini-4-argon, effort high", classifier: "claude-haiku-4.5" }
recheck-when: Harbor upgrades past 0.22.0, the judge's model, effort or prompts change, Copilot CLI is upgraded, or a senior task's Dockerfile changes
---

# JudgeVerifier judges a senior trial in the separate verifier as JudgeAgent did, adding about four minutes on a cached image

## Intent

- Does judging inside the verifier class, between the apply and the test-file
  reset, give the test reward and the scores `JudgeAgent`'s regrade gave the
  same patch?
- Does the Copilot token stay with the judge's commands, and the ledger on the
  host?
- How long does the task's second image take to build, and does Modal reuse its
  cache?
- How much does judging add to the verifier's time, and so how much room must
  each senior task's verifier timeout gain?

## Decisions

- **Judge senior in `suss.judge:JudgeVerifier`, in the agent job, and drop
  `JudgeAgent`.** It gave reward 1.0, as the regrade did, and the same scores:
  rubric 1.0 on all four criteria, practice alignment 5, relative taste 5, patch
  bloat 1.115. The argon note's repeat moved taste by at most one point.
- **Add 2100 seconds to every senior task's verifier timeout: a judge limit of
  1800 and 300 to put the workspace back.** Judging took 229 seconds here, and
  the longest `JudgeAgent` trial of the copilot-gpt run took 563 with its apply
  and reset. 1800 is three times that, and the task's tests keep the time the
  task gave them, as low as 300 seconds on `electric-fix-elixir-client-cache`.
- **Count on no image build for the verifier on Modal.** The verifier's sandbox
  was up and the patch applied 23 seconds after the trial started. The task's
  `tests/Dockerfile` is its `environment/Dockerfile`, which copies nothing from
  its context, so Modal had the agent job's image cached.

## Needs your input

Nothing.

## Reference

### The check

From `suss-runs/`, with the regrade's source a copy of the copilot-gpt trial
whose `agent/model.patch` was put in `artifacts/logs/artifacts/`, and the
one-task lane built by `lanes.build`:

```bash
ANTHROPIC_API_KEY= OPENAI_API_KEY= PORTKEY_API_KEY= \
  <worktree>/.venv/bin/harbor jobs regrade results/senior-verifier-check/source-job \
  -p results/senior-verifier-check/lanes/senior/paperless-ngx-perf-workflow-queries \
  -e modal -n 1 --verifier suss.judge:JudgeVerifier --verifier-kwarg credit_budget=75 \
  -o results/senior-verifier-check --job-name judge-1
```

The keys are set empty, not unset: Harbor's registry config runs
`load_dotenv()` from its install, which reloads an unset key from the suss
checkout's `.env` (#223).

| | `JudgeAgent` regrade, `20261105T205511Z-1-regrade` | `JudgeVerifier`, `judge-1` |
|---|---|---|
| Reward | 1.0 | 1.0 |
| `rubric_score` | 1.0 | 1.0 |
| `taste_practice_alignment` | 5.0 | 5.0 |
| `taste_relative_taste` | 5.0 | 5.0 |
| `taste_patch_bloat` | 1.115 | 1.115 |
| Credits | 25.69 | 21.39 |
| Sessions, seconds | 12.0, 9.3, 34.9, 159.9 | 10.0, 8.4, 39.7, 124.7 |

- The judged files include `src/documents/tests/test_api_workflows.py`, which
  `tests_reset.restored` then put back before `test.sh`.
- Senior's own judges wrote `skipped:no_api_key`, so the verifier's reward
  carries `rubric_judge_ok` 0 and `taste_judge_ok` 0, as the regrade's did.
- The ledger, `judge-1/judge-credits.jsonl` on the host, holds the four
  reservations and their settlements.
- None of the job's 44 files, nor Harbor's output, holds the token, and the
  job's `config.json` gives the verifier `{"credit_budget": 75}` alone.

### Where the time went

| Step | Seconds |
|---|---:|
| Verifier's sandbox up, patch applied | 23 |
| Judge: install, prompts, four sessions, cleanup | 229 |
| Senior's `test.sh` | 28 |
| Verifier phase in all | 284.6 |

The regrade's verifier phase took 37 seconds for the same `test.sh`.

### Cost

- 21.39 AI credits.
- One verifier sandbox of 2 CPUs and 8 GB for under five minutes on Modal, in
  the `__harbor__` app, since `harbor jobs regrade` takes no app name. The bill
  was not read.

## Limits

- One task, one trial, one judging. That every senior task judges the same way
  is not shown.
- Run by `harbor jobs regrade`, not `suss run`, so the agent phase that writes
  `/logs/artifacts/model.patch` was not run with it.
- How long a senior image takes to build from nothing was not measured, since
  none was built. A task whose `Dockerfile` copies a file from its context, as
  `teleport-add-traits-matching-logic`'s does, may hash differently in Modal.
- That `test.sh`'s environment lacks the token is shown by the unit test, not
  read from the sandbox.
- A Harbor verifier timeout multiplier under 1 would shorten the room the lane
  adds; nothing refuses one.
