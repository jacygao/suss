---
settles: "§ 3 — that a regrade applies the trial's model.patch in a fresh sandbox, and what it records for an empty or missing patch; § 6 — the regrade command's spelling and how its relative paths resolve"
issue: [45]
recorded: 2026-10-31
versions: { harbor: 0.22.0, swe-bench-pro: "v2.0.0 (66f92766)", docker-desktop: "engine 29.9.2", host: "macOS, arm64" }
recheck-when: Harbor upgrades past 0.22.0, or the SWE-bench Pro checkout changes v2/tooling/patch_replay.py
---

# PatchReplayAgent regrades a suss trial's patch in a fresh container

## Intent

- Does § 6's regrade command, with `-e docker`, complete over a suss agent job
  and give its trial a reward?
- Does the regrade start a fresh container from the task image, and apply the
  trial's own `agent/model.patch`?
- What does the regrade's trial directory hold when the source `model.patch` is
  empty, and when it is missing?
- Is anything in § 6's regrade command wrong for Harbor 0.22.0 or this
  `patch_replay.py`?

## Decisions

- **Keep § 6's regrade command as written.** Run over M1's flipt agent job, it
  exited 0 and gave the trial reward 1.0. It started a new container from the
  task image, applied bytes identical to the trial's `model.patch`, and ran the
  verifier in that container.
- **The reward follows the patch.** With the new file
  `internal/cache/redis/client.go` cut out of the source patch, the regrade
  applied exactly those bytes and gave 0.0, because the `redis` package no
  longer built. With the patch emptied or deleted it also gave 0.0. The
  source trial's own `verifier/reward.txt` said 1 in every one of these runs,
  so the regrade does not read it.
- **An empty patch and a missing one leave the same trial directory**, and a
  lookup that finds no source trial would leave it too. The agent applies
  nothing and writes `agent/replay.json` as
  `{"task": "<task dir name>", "patch": null}`. The verifier then runs on the
  unchanged repository and records reward 0.0, with no exception. `suss compare`
  counting that trial as unresolved is correct.
- **`suss run` must run Harbor from the directory holding `suss.toml`.** The
  regrade's `PYTHONPATH` and its `source_job` are both relative, and both
  resolve against Harbor's working directory. Harbor never changes it.

## Needs your input

- A `source_job` that points at the wrong directory would give every trial reward 0,
  and looks exactly like a job whose patches were all empty. Nothing fails. I
  recommend `suss run` pass `source_job` as an absolute path, and that it or
  `suss compare` report an error for a regrade whose `replay.json` has
  `"patch": null` while its agent trial's `suss.json` has `patch_bytes` above 0.

## Reference

### The command

Run from a copy of M1's run directory, with the checkout linked as
`../SWE-bench_Pro-os` so the relative `PYTHONPATH` is spelled as in § 6:

```bash
PYTHONPATH=../SWE-bench_Pro-os/v2/tooling harbor run -p lanes/pro-go-one -e docker -n 16 -k 1 \
  -a patch_replay:PatchReplayAgent -m replay \
  --ak source_job=jobs/attempt-1 \
  --jobs-dir jobs --job-name attempt-1-regrade
```

It took 24 minutes, nearly all of it the verifier. The trial's `result.json`
records `agent_info.name` as `patch-replay-agent` and the model as `replay`.

### How PatchReplayAgent finds and applies the patch

- The task name comes from `task.path` in the regrade trial's `config.json`,
  which Harbor writes before the agent runs: the full task directory name,
  `instance_flipt-io__flipt-02e21636c58e86c51119b63e0fb5ca7b813b07b1`.
- It globs `<source_job>/instance_*/result.json` and matches the last
  `/`-separated part of `task_name` there. suss's trials record `task_name` as
  `swebench-pro/instance_…`, so they match.
- It uploads the patch to `/tmp/replay.patch` and runs, in `/app` or else
  `/testbed`, `git apply --verbose`, then `git apply --3way`, then
  `patch --fuzz=3 -p1`, stopping at the first that succeeds.
- `agent/replay.json` then holds `patch` (the path it found, relative when
  `source_job` is), `apply_rc`, `apply_out` and `post_apply_diffstat`. The code
  records a failed apply there and nowhere else, and the verifier still runs.
  No apply failed in these runs.
- With no patch, the warning
  `no source patch for <task>; applying nothing` goes to the trial's
  `trial.log`.

### What the run showed

- `docker ps -a` listed no containers before the run, so the agent job's was
  gone. Docker's events show the run creating one main container,
  `instance_flipt-io__flipt-02e2163__mahevdw__env-main-1`, at 07:29:46Z from
  image `sha256:1d6965a24432…`, the task image, and destroying it when the
  trial ended at 07:53:56Z.
- `/tmp/replay.patch`, copied out of that container during the run, is
  identical by `cmp` to the source trial's `agent/model.patch`, sha256
  `9440f03c88b7…`. In the run over the cut patch, the copy matched that patch
  instead, sha256 `c10b37f5f804…`. In the empty and missing runs nothing was
  uploaded.
- Before the verifier started, `git status` in that container showed the
  patch's seven files changed, including the new
  `internal/cache/redis/client.go`. A container started straight from the image
  has none of them, and a clean status at base `85bb23a3…`.
- The regrade's `verifier/` files were written at the end of the run, and its
  `run-script-stdout.txt` differs from the source trial's in the test timings.
- In the empty and missing runs, each in a new container of its own,
  `git status` was clean at the base before the verifier applied its test
  patch.

| Source `model.patch` | `replay.json` `patch` | Reward |
|---|---|---|
| the trial's own, 12077 bytes | the path to it, `apply_rc` 0 | 1.0 |
| `client.go` cut out, 10362 bytes | the path to it, `apply_rc` 0 | 0.0 |
| empty | `null` | 0.0 |
| missing | `null` | 0.0 |

### Traps

- `post_apply_diffstat` is `git diff --stat`, which leaves out new files. It
  listed 5 files for a patch of 7. The count after `---`, from
  `git status --short`, includes them.
- Harbor parses `--ak` values as JSON first. A `source_job` that parses as a
  number or a literal would reach `Path()` as one; no path suss writes does.

## Limits

- One task, flipt, and one source trial. Tasks under `/testbed`, and patches
  that need `--3way` or `patch` to apply, were not run.
- That `source_job` and `PYTHONPATH` resolve against Harbor's working
  directory was shown only from the expected directory; the wrong-directory
  case is read from `patch_replay.py` and Harbor's source, not run.
- The regrade ran with one task, so `-n 16` was not exercised.
- Only the `docker` environment was run.
