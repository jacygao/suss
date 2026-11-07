---
settles: "§ 3 — whether a trial can be graded in Harbor's separate verifier in place of a regrade job, what that sandbox and the agent's see of each other, and what senior's judge would need there; § 5 — what each lane task would have to change; § 6 — what suss run and suss compare would run and read instead of <job>-regrade"
issue: [212]
recorded: 2026-11-08
versions: { harbor: 0.22.0, copilot-cli: 1.0.105, modal: 1.6.3, environment: modal, lane: "rebench, a copy of vuejs__core-14877", model: gpt-6.1-sol }
recheck-when: Harbor upgrades past 0.22.0, ReplayAgent changes how it applies a patch or resets test files, or harbor jobs regrade gains environment kwargs
---

# Harbor's separate verifier grades a suss trial as ReplayAgent does, and can replace it

## Intent

- Does a rebench task set to `[verifier] environment_mode = "separate"` give an
  agent job of `copilot-gpt` the reward its `ReplayAgent` regrade gives, and
  does `harbor jobs regrade` give it again?
- Does the verifier apply the same patch suss captured, and put the test files
  back?
- Does the verifier's sandbox see anything of the agent's beyond the collected
  artifacts, and does the agent phase see the verifier's environment?
- What network does the verifier get?
- Can it work for senior, whose judge runs between the apply and the reset?
- Does it save a sandbox start, and does `harbor jobs regrade` rebuild anything?
- What would `suss compare` read in place of `<job>-regrade`?
- Should it replace `ReplayAgent`, and what would each lane task change?

## Decisions

- **Replace `ReplayAgent` with the separate verifier, and that is #219.** Three
  graders gave the same trial reward 1.0: the agent job's own separate
  verifier, `harbor jobs regrade` over that job, and `ReplayAgent` over it. Each
  ran the same 112 tests and all passed. With the patch emptied, the same
  regrade gave 0.0.
- **Do ReplayAgent's step in a suss verifier class, not in each task's
  `test.sh`.** `--verifier` takes a Python class that Harbor runs on the host
  against the verifier's sandbox, as it runs `ReplayAgent` against the
  regrade's. A class that applied the patch, reset the test files and then ran
  Harbor's own verifier gave the same 1.0, with `test.sh` unchanged. Each task
  then changes only in `task.toml`'s mode and a `tests/Dockerfile` copied from
  `environment/Dockerfile`. A rewrite of `test.sh` works too, as the spike
  showed, but it means 158 test scripts to change.
- **Have `SussAgent` write the patch to `/logs/artifacts/` itself.** It already
  captures it however the run ends. A collect hook would need the starting
  tree, which only exists before the agent runs, so the spike recorded it in a
  task healthcheck, the one task hook that runs before agent setup. The hook's
  `model.patch` was byte for byte the one `SussAgent` captured. But a collect
  hook is best-effort: Harbor logs its failure as a warning and grades the
  unchanged repository, which scores 0 and fails nothing. Harbor's own agents,
  `oracle` among them, would then leave no patch, so an oracle check of a lane
  would need the hook.
- **Trust the verifier's sandbox to hold nothing of the agent's but
  `/logs/artifacts/`.** Harbor stops the agent's sandbox before it starts
  the verifier's. The verifier's had its own boot id, a clean `git status` at
  the base commit, and under `/logs/` only `artifacts/` and `verifier/`.
- **Give the verifier its keys through `[verifier.env]`, and never through
  `[environment.env]` or `--ek secrets`, which reach both sandboxes.** A canary
  passed as `--ve` was in `test.sh`'s environment alone: not in the agent
  sandbox's process 1 or a command run there, and not in the verifier
  sandbox's process 1. `[verifier.env]` and `--ve` go only to the command that
  runs `test.sh`.
- **Give the agent job the grading keys in Harbor's environment, as the regrade
  job gets them now, never with `--ve`.** The agent job becomes the grading
  job, so its Harbor needs senior's keys. `SussAgent` passes the harness only
  the variables the manifest names, from `.env`, so Harbor's environment does
  not reach the harness. A `--ve` value is written into the job's
  `config.json`, `lock.json` and each trial's `result.json`, where the canary
  was found.
- **Expect the verifier's network to be the regrade's: the task's
  `[environment]` baseline, or its `[verifier]` policy where it sets one.**
  `--allow-agent-host` changes only the agent phase. On rebench the verifier is
  public, and on senior `[verifier] network_mode = "public"`.
- **Senior can work the same way, with `JudgeAgent`'s judging moved into the
  verifier class between the apply and the reset.** The class runs on the host,
  so senior's prompts can still be built there by each task's `run_judge.py`,
  and the job's credit ledger can stay a host file. The Copilot token goes to
  the verifier's sandbox only for the judge's commands, and that sandbox never
  holds the agent. Senior's `test.sh` reads nothing from `/logs/` but
  `/logs/verifier`, and commits whatever the workspace holds as the agent's
  change, so it grades an applied patch as it would the agent's own sandbox.
  The judge would run under the verifier's network, which is public on senior.
- **Expect no sandbox start saved.** A trial takes two either way: the agent's and
  the verifier's. What goes is the second Harbor job and each regrade trial's
  agent phase. `harbor jobs regrade` starts one sandbox per trial, with no
  agent phase, and its verifier phase took 26 seconds, as the agent job's took
  30, so nothing was rebuilt.
- **Have `suss compare` read the agent job's own trials.** A trial's reward is
  then its `result.json`'s `verifier_result`, its test result
  `verifier/report.json` in the same trial directory, and the apply record
  `verifier/replay.json` where `agent/replay.json` was. A re-grade by
  `harbor jobs regrade` is a new job directory with one trial per task, read the
  same way, so `compare` would need to be told which job grades a run.

## Needs your input

- **Whether to give up an oracle check on lanes that grade in the separate
  verifier.** With `SussAgent` writing the patch, Harbor's `oracle` agent
  leaves none, and every oracle trial scores 0. I recommend `SussAgent` writes
  it, and a lane check that needs the oracle uses the lane as Harbor downloads
  it, in shared mode.

## Reference

### The lane copy

Under `suss-runs/lanes/spike-separate/`:

| Copy | `task.toml` | `tests/` | Graded by |
|---|---|---|---|
| `separate/` | `[verifier] environment_mode = "separate"`, one `[[verifier.collect]]`, an `[environment.healthcheck]` | `Dockerfile` (`environment/Dockerfile` plus `COPY . /tests/`), `suss_apply.py`, and `test.sh` running it after `cd "$REPO_DIR"` | the agent job, and `harbor jobs regrade` |
| `custom/` | as `separate/` | `Dockerfile`, a copy of `environment/Dockerfile`; `test.sh` unchanged | `harbor jobs regrade --verifier` |
| `replay/` | the lane's own | the lane's own | `ReplayAgent` |

- Harbor builds the verifier's image from `tests/`, as the build context, and
  passes it no test files when the task's own verifier runs: the image must
  hold them under `/tests/`. A verifier class can upload them instead.
- The healthcheck writes `git write-tree` of a copy of the index after
  `git add -A`, as `read_start_tree` does, to `/var/lib/suss-spike/start-tree`.
  The collect hook runs `capture_patch`'s commands against it and writes
  `/logs/artifacts/model.patch`.
- The verifier class copies nothing from `ReplayAgent`. It imports `_created`,
  `_present`, `_reset_tests` and `is_test_file` from `suss.replay`, and
  `find_workspace` and `read_start_tree` from `suss.workspace`, and passes them
  the verifier's sandbox.

### The runs

From `suss-runs/`, with `suss-spike-212.toml` holding the `copilot-gpt` arm
with `max_ai_credits = 30`:

```bash
harbor run -p lanes/spike-separate/separate/vuejs__core-14877 -e modal -n 1 -k 1 \
  -a suss.agent:SussAgent --ak arm=copilot-gpt --ak config=suss-spike-212.toml --ak lane=rebench \
  --allow-agent-host api.github.com --allow-agent-host api.enterprise.githubcopilot.com \
  --ek app_name=suss-212-spike --jobs-dir results/spike-separate --job-name agent-1
harbor run -p lanes/spike-separate/replay/vuejs__core-14877 -e modal -n 1 -k 1 \
  -a suss.replay:ReplayAgent -m replay --ak source_job=$PWD/results/spike-separate/agent-1 \
  --ek app_name=suss-212-spike --jobs-dir results/spike-separate --job-name agent-1-replay
harbor jobs regrade results/spike-separate/agent-1 -p lanes/spike-separate/separate/vuejs__core-14877 \
  -e modal -n 1 -o results/spike-separate --job-name agent-1-regrade
PYTHONPATH=<dir of replay_verifier.py> harbor jobs regrade results/spike-separate/agent-1 \
  -p lanes/spike-separate/custom/vuejs__core-14877 --verifier replay_verifier:ReplayVerifier \
  -e modal -n 1 -o results/spike-separate --job-name agent-1-regrade-verifier
```

| Job | Reward | Tests passed | Patch applied, sha256 | Test files restored | Verifier's boot id |
|---|---:|---:|---|---|---|
| `agent-1`, its separate verifier | 1.0 | 112 of 112 | `38bd27a5…21ed1`, `apply_rc` 0 | both | `a4efc3c0…` |
| `agent-1-replay`, `ReplayAgent` | 1.0 | 112 of 112 | `agent/model.patch`, `apply_rc` 0 | both | not recorded |
| `agent-1-regrade`, `harbor jobs regrade` | 1.0 | 112 of 112 | `38bd27a5…21ed1`, `apply_rc` 0 | both | `af17b2bf…` |
| `agent-1-regrade-verifier`, the class | 1.0 | 112 of 112 | 9128 bytes, `apply_rc` 0 | both | not recorded |
| `control-empty-regrade`, artifact patch emptied | 0.0 | 109 of 112 | none | none | `1391f19c…` |

- The agent's sandbox had boot id `695adb8e…`, at the healthcheck and at the
  collect hook alike. Every sandbox's hostname is `modal`, so the hostname
  tells none apart.
- The agent edited `Suspense.spec.ts` and `Teleport.spec.ts` and fixed
  `src/components/Teleport.ts`. After the reset, `git status` showed only
  `Teleport.ts` changed. Rebench's `test.sh` checks out those two spec files
  itself, so on this task the reset cannot change the reward.
- The control kept the real patch in `agent/model.patch`, so `harbor jobs
  regrade` reads only `artifacts/`.
- `nop-canary` ran `-a nop --ve SPIKE_VERIFIER_ONLY=canary-212` on the
  `separate/` copy, for the environment check.

### Traps

- `harbor jobs regrade` takes no `--ek`, so it runs in Modal's `__harbor__` app
  and cannot be given an app name or any other environment setting.
- A regrade needs the source trial's `artifacts/manifest.json` to list
  `/logs/artifacts`. The existing rebench and senior jobs have no
  `model.patch` there, so none of them can be regraded this way without
  copying it in.
- `RegradeTrial` refuses a task whose `[task] name` differs from the source
  trial's, and a task that is not in separate mode.
- One senior task's Dockerfile copies `proto_plumbing.patch` from
  `environment/`, so its `tests/` needs that file too.

### Cost

- One Copilot CLI session: 18.44 AI credits, from `totalNanoAiu`
  18,439,490,000.
- Modal billed $0.017 to `suss-212-spike` and $0.003 to `__harbor__` in the
  hour of the runs.

## Limits

- One task, one trial. That every rebench and senior task grades the same way
  in separate mode is not shown.
- The reset was shown to put the test files back, not to change a reward.
- The verifier class was run by `harbor jobs regrade` only. That `harbor run
  --verifier` reaches it the same way is read from Harbor's code.
- Senior was not run. Its answers above are read from its tasks and from
  `judge.py`, as is the cost of a second image per senior task, which builds
  from scratch.
- Whether Modal built the verifier's image anew or reused the agent's was not
  read from Modal. The verifier phases' times show no build of any length.
- The Modal spend is read from an hourly report minutes after the runs.
