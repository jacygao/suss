---
settles: "§ 3 — that a regrade under `suss run` grades an `ok` trial's own patch on Modal; § 4 — what a claude-code trial that ends by itself records of its spend; § 6 — what `suss run --check` leaves under `checks/` with `environment = \"modal\"`, and how long a check of pro-go takes there"
issue: [69]
recorded: 2026-11-03
versions: { harbor: 0.22.0, modal: 1.6.3, claude-code: 2.1.293, swe-bench-pro: "v2.0.0 (66f92766)", host: "macOS arm64 client" }
recheck-when: Harbor upgrades past 0.22.0, Modal's client upgrades past 1.6.3, or the example arm changes
---

# suss run --check completes on Modal with an ok trial in twelve minutes

## Intent

- Does `suss run claude-code pro-go --check` complete on Modal with the example arm?
- Does the trial reach `status` `ok`, and does it record what Claude Code spent?
- Does the regrade grade that trial's own patch, so its reward is the trial's result?
- What does the check cost, in model spend and in Modal time?

## Decisions

- **Run Claude Code arms on Modal as suss runs them now, with no change.** The
  check exited 0 in 12 min 33 s. The agent job took 10 minutes and the regrade
  2.5, against 2 hours under Docker Desktop, where the task's amd64 image runs
  emulated on one CPU.
- **Take an `ok` claude-code trial's spend from `run.log`'s last line.** Claude
  Code ended by itself, and its result gives `total_cost_usd` 1.26. Harbor's own
  `cost_usd` stays `null`, and `suss.json` holds no spend.
- **Read the regrade's reward as the trial's result.** Its `replay.json` names
  the trial's `model.patch` with `apply_rc` 0. The reward is 0 because 3 of the
  4 required tests passed: the patch accepts both `ca_cert_bytes` and
  `ca_cert_path` where the task wants an error.
- **Expect the check to cost about $1.30.** That is $1.26 of model and about 12.5
  minutes of sandbox at 1 CPU and 4 GiB on Modal.

## Needs your input

- **Whether `suss.json` should record a finished trial's spend.** Today the
  dollars of an `ok` claude-code trial live only in the last line of an 840 KB
  `run.log`, and a reader comparing arms has to know that. I recommend the issue
  that prices every arm's tokens from one table also copy a finished run's own
  figure into `suss.json`.

## Reference

### The run

From the worktree root, with `.env` linked beside a `suss.toml` holding:

```toml
swe_bench_pro = "<checkout>"
dataset = "swe-bench-pro-v2"
environment = "modal"
k = 1
concurrency = 1
max_seconds = 2700

[arms.claude-code]
harness = "adapters/claude-code"
params = { version = "2.1.293", model = "claude-opus-4-6", effort = "high", max_budget_usd = 9 }
```

```bash
uv run suss run claude-code pro-go --check
```

`DRY_RUN=1` first printed the two `harbor run` commands, with `-e modal`,
`--allow-agent-host api.anthropic.com` and `--disable-verification` on the
agent job, and nothing was wrong in them.

| Step | Started (UTC) | Ended (UTC) | Took |
|---|---|---|---|
| Sandbox started | 22:11:55 | 22:12:08 | 14 s |
| Agent setup, Claude Code installed | 22:12:08 | 22:12:30 | 21 s |
| `run`, Claude Code ended by itself | 22:12:30 | 22:21:40 | 544 s |
| Regrade, sandbox to verifier's end | 22:21:56 | 22:24:24 | 2 min 28 s |
| of which the verifier | 22:22:11 | 22:24:23 | 2 min 12 s |
| All of `suss run` | 22:11:53 | 22:24:26 | 12 min 33 s |

Harbor exited 0 for both jobs, and `suss run` exited 0. In 32 turns, Claude Code
ran `go build` three times, `go vet` once and `go test` five times, and every
one finished.

### What `checks/pro-go/claude-code/` held

```
20261102T221153Z-1/
  config.json  job.log  lock.json  result.json
  instance_flipt-io__flipt-02e2163__vdcEAAU/
    agent/  artifacts/  config.json  lock.json  result.json  trial.log  verifier/
    agent/: install.log (721)  model.patch (5901)  run.log (841217)  suss.json (516)
20261102T221153Z-1-regrade/
  config.json  job.log  lock.json  result.json
  instance_flipt-io__flipt-02e2163__g89ZxvF/
    agent/  config.json  lock.json  result.json  trial.log  verifier/
    agent/: replay.json (1073)
```

The agent trial's `verifier/` is empty, since the agent job runs with
verification disabled, and it has no `exception.txt`.

### The trial record and its spend

`suss.json`:

```json
"status": "ok",
"exit_codes": { "install": 0, "run": 0 },
"run_seconds": 543.6493996249046,
"patch_bytes": 5901
```

The last line of `run.log`:

```json
"type": "result", "subtype": "success", "num_turns": 32,
"total_cost_usd": 1.2633222499999999,
"modelUsage": { "claude-opus-4-6": { "inputTokens": 35, "outputTokens": 10297,
  "cacheReadInputTokens": 995207, "cacheCreationInputTokens": 81299, … } }
```

Modal time was about 10 minutes for the agent sandbox and 2.5 for the regrade's,
each at the task's 1 CPU and 4 GiB. At Modal's list rates that comes to a few
cents (unverified: the bill was not read).

### The regrade

`agent/replay.json`:

```json
"task": "instance_flipt-io__flipt-02e21636c58e86c51119b63e0fb5ca7b813b07b1",
"patch": "<dir>/checks/pro-go/claude-code/20261102T221153Z-1/instance_flipt-io__flipt-02e2163__vdcEAAU/agent/model.patch",
"apply_rc": 0,
"post_apply_diffstat": " … 4 files changed, 20 insertions(+), 22 deletions(-)\n---\n5\n"
```

All 5 files applied cleanly. `verifier/reward.txt` is `0`, and
`test-stdout.txt` ends:

```
Required tests: 4
Passed tests: 14
Required tests that passed: 3

RESULT: FAILED
Missing tests: ['TestLoad']
```

`TestLoad` failed on two subtests, the YAML and ENV forms of
`cache_redis_with_ca_path_and_bytes_bundle`:

```
expected "please provide exclusively one of ca_cert_bytes or ca_cert_path", found <nil>
```

### Traps

- Harbor's summary for the agent job shows `Trials 0` and `Mean 0.000`. It
  counts trials with a reward, and verification is disabled, so the job's real
  trial is not in it. Read `suss.json` instead.
- Modal left no container running. Harbor's `__harbor__` app stays deployed
  with 0 tasks.

## Limits

- One attempt, so neither the `ok` nor the reward is shown to be typical.
- Modal's charge was estimated from the sandboxes' durations, not read from
  the bill.
- The run stayed far inside both `max_seconds` and `max_budget_usd`, so neither
  stop was seen on Modal with a real model.
- Only the lane's first task by name was run.
