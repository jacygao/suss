---
settles: "§ 3 — that a regrade under `suss run` grades the trial's own patch; § 6 — what `suss run --check` leaves under `checks/`, Harbor's exit codes, and how long a check of pro-go takes under Docker Desktop"
issue: [50]
recorded: 2026-11-01
versions: { harbor: 0.22.0, claude-code: 2.1.293, swe-bench-pro: "v2.0.0 (66f92766)", docker-desktop: "engine 29.9.2, 10 CPUs, 7.7 GiB", host: "macOS, arm64" }
recheck-when: Harbor upgrades past 0.22.0, the example arm changes, or #60 changes how suss stops run
---

# suss run --check completes on Docker Desktop, and its regrade grades the trial's own patch

## Intent

- Does `suss run claude-code pro-go --check` complete under Docker with the example arm?
- Does it leave the agent job and its regrade under `checks/pro-go/claude-code/`, paired and named as § 6 says?
- Is the regrade's reward the trial's real result, rather than a grade of the unchanged repository?
- What does a real agent job directory hold, and could suss's no-trial test misread it?
- What do Harbor's two runs exit with?

## Decisions

- **The command works end to end on Docker Desktop for Apple Silicon.** It
  pulled the image for amd64, ran the agent job and then its regrade, and
  exited 0. The trial's own sandbox raised an exception, and the regrade still
  graded it.
- **The regrade graded the trial's patch.** Its `replay.json` names the agent
  trial's `model.patch` by absolute path, with `apply_rc` 0, and all 6 files
  applied cleanly. The reward was 0.0 because the patch passed 3 of the task's
  4 required tests, and `TestLoad` failed.
- **The trial did not reach `status` `ok`.** suss stopped Claude Code at
  `max_seconds`, 2700, and recorded `timed-out` with a 6490-byte patch. Under
  the task's 1 CPU and 4 GiB, with amd64 emulated by Rosetta, each `go build`
  took over ten minutes. Claude Code stacked five of them at once, and the
  compilers were OOM-killed.
- **Keep the no-trial test as it is.** The agent job directory held only
  Harbor's four files and one trial directory, named for its task. Harbor wrote
  no dot directory, and nothing else that would pass for a trial.
- **Keep exiting 1 only on a regrade's non-zero exit.** Harbor exited 0 for both
  jobs, including the agent job whose only trial ended in
  `VerifierTimeoutError`. A non-zero exit from Harbor here would mean Harbor
  itself failed, not that a trial did.

## Needs your input

- **Whether agent jobs should pass `--disable-verification`.** The agent job's
  own verifier ran 50 minutes, to its 3000-second timeout, and its result is
  never used. Even on the stand-in run, where nothing was left running, it took
  24 minutes. I recommend passing it once #60 is fixed, which would take each
  check of a Go task from about 2 hours to about 1.
- **How a SWE-bench Pro Go lane should run on Apple Silicon.** At the task's own
  1 CPU and 4 GiB under emulation, a Claude Code arm can spend its whole
  `max_seconds` waiting on the compiler. I recommend § 2 say that Docker
  Desktop on Apple Silicon runs the V2 images under emulation, and that
  `-- --override-cpus <n>` is how to give the agent job more CPUs. Overrides
  change the conditions an arm is measured under, so whether a lane allows
  them, and at what value, is a decision about comparability.

## Reference

### The run

From the worktree root, with `suss.toml` holding the § 6 example arm,
`concurrency = 1`, `k = 1`, `max_seconds = 2700` and `max_cost_usd = 9`, and
`.env` linked beside it:

```bash
uv run suss run claude-code pro-go --check
```

| Step | Started (UTC) | Ended (UTC) | Took |
|---|---|---|---|
| Agent setup, Claude Code installed | 00:53:25 | 00:53:49 | 24 s |
| `run`, stopped at `max_seconds` | 00:53:49 | 01:40:38 | 2730 s |
| Agent job's verifier, timed out | 01:41 | 02:31:10 | 3000 s |
| Regrade | 02:31:40 | 02:56:47 | 25 min |
| All of `suss run` | 00:53:19 | 02:56:49 | 2 h 3 min |

Harbor exited 0 for the agent job and 0 for the regrade, and `suss run` exited 0.

### What `checks/pro-go/claude-code/` held

```
20261101T005320Z-1/
  config.json  job.log  lock.json  result.json
  instance_flipt-io__flipt-02e2163__kY8wt23/
    agent/  artifacts/  config.json  exception.txt  lock.json  result.json  trial.log  verifier/
    agent/: install.log (721)  model.patch (6490)  run.log (0)  suss.json (501)
20261101T005320Z-1-regrade/
  config.json  job.log  lock.json  result.json
  instance_flipt-io__flipt-02e2163__NfDX2zK/
    agent/  artifacts/  config.json  lock.json  result.json  trial.log  verifier/
    agent/: replay.json (1208)
```

Both trials' `config.json` give `task.path` as
`lanes/pro-go-check/instance_flipt-io__flipt-02e21636c58e86c51119b63e0fb5ca7b813b07b1`,
and both `result.json` give `task_name` as
`swebench-pro/instance_flipt-io__flipt-02e21636c58e86c51119b63e0fb5ca7b813b07b1`.

### The trial record and its spend

`suss.json`:

```json
"status": "timed-out",
"exit_codes": { "install": 0, "run": null },
"run_seconds": 2730.022991665872,
"patch_bytes": 6490
```

`run.log` is empty, and Harbor's `cost_usd` is `null`. The spend came from
Claude Code's transcript, copied out of the container before Harbor removed it.
Its last `budget_usd` attachment, before the final turn, reads
`"used": 1.6023662499999995`. Pricing the final turn's tokens at Opus 4.6
rates brings it to about $1.63. #61 is about recording it in the trial.

### The regrade

`agent/replay.json`:

```json
"task": "instance_flipt-io__flipt-02e21636c58e86c51119b63e0fb5ca7b813b07b1",
"patch": "<dir>/checks/pro-go/claude-code/20261101T005320Z-1/instance_flipt-io__flipt-02e2163__kY8wt23/agent/model.patch",
"apply_rc": 0,
"post_apply_diffstat": " … 5 files changed, 25 insertions(+), 22 deletions(-)\n---\n6\n"
```

`verifier/reward.txt` is `0`, and `test-stdout.txt` ends:

```
Required tests: 4
Passed tests: 14
Required tests that passed: 3

RESULT: FAILED
Missing tests: ['TestLoad']
```

### What kept the CPU busy

When suss stopped `run`, every `go build`, `go vet` and `go test` Claude Code
had started was still running 40 minutes later, during the verifier. The
container sat at 100% CPU and 3.78 GiB of its 4 GiB. Claude Code's Bash tool
runs each command in a session of its own, so the kill of `run`'s process group
misses them. #60 is the fix.

### The stand-in run

Before any model call, the same command ran with an arm whose adapter installs
Claude Code, prints `claude --version`, and appends a line to `README.md` and
adds one new file. It installed `2.1.293`, reached `api.anthropic.com`
(`http=401` with no key), was refused `pypi.org` (`http=000`), and recorded
`status` `ok`. Its regrade's `replay.json` named its 551-byte patch with
`apply_rc` 0. The agent job took 25 minutes and the regrade 25, and `suss run`
exited 0.

### Traps

- Through a pipe, Harbor prints nothing until a job ends, then its summary
  table. The image pull is the only output before that. `docker ps` and the job
  directory show progress.
- Harbor exits 0 when a trial raises in the verifier. Its exit code says nothing
  about the trials.

## Limits

- One attempt, at a spend of about $1.63, so the trial's `timed-out` is not
  shown to be typical. The owner stopped further attempts while the spend was
  unknown.
- `status` `ok` with a real model was not reached, so a regrade of an `ok`
  trial under `suss run` was seen only with the stand-in.
- The task's resources were not overridden, so whether more CPUs would have let
  Claude Code finish was not tested.
- Why Claude Code wrote nothing to its transcript after 01:13:16, while it
  waited on a `go build` it had given a 600-second timeout, was not established.
- `max_cost_usd` was 9 rather than the owner's 10, to keep one turn's
  overshoot inside the $10 budget.
