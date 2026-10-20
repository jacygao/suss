# Suss Specification: Measuring and Comparing Coding Harnesses

**Version:** 0.1 (draft)

## 1. Purpose

**Suss measures and compares how well coding harnesses do specific kinds of
coding work.** A harness is anything that takes a task's instruction and changes
a repository. Each kind of work is a lane: a set of benchmark tasks and the way
they are graded. Suss runs the same tasks under several arms, grades every
result the same way, and reports how each arm's score differs from its
baseline's.

Comparing plain Claude Code with two harnesses that review its change, on the Go
lane, prints output like this:

```
$ suss compare pro-go claude-code self-review peer-review

arm          trials  resolved  failed  timed out
claude-code     256       38%      0%         1%
self-review     256       41%      1%         1%
peer-review     256       44%      1%         2%

vs. baseline                resolved
self-review − claude-code   +3% [−2%, +8%]
peer-review − claude-code   +6% [+1%, +11%]
```

## 2. Dependencies

| Dependency | Version | Needed for |
|---|---|---|
| Harbor | 0.22.0 | Runs each task in a sandbox, runs suss in it, and grades the result with the task's own verifier. |
| Modal | 1.5.1 or later, and the account Harbor's `modal` environment uses | The sandboxes, when `environment` is `modal` (§ 6). Earlier versions cannot take a network policy per phase. |
| Docker | any | The sandboxes, when `environment` is `docker`. |
| SWE-bench Pro | a checkout of `scaleapi/SWE-bench_Pro-os` | The tasks, under `v2/tasks/`, and the regrading agent, under `v2/tooling/` (§ 5). |
| Python | 3.12 | Suss itself, which Harbor loads as a custom agent. |
| `uv` | any | Installs suss and Harbor. |

## 3. A trial

### Terminology

| Term | Meaning |
|---|---|
| **Task** | One benchmark task: a repository in a sandbox image, an instruction, and hidden tests. |
| **Lane** | A set of tasks that suss runs and reports as one (§ 5). |
| **Harness** | What is being measured, reached through its adapter. |
| **Adapter** | The directory that lets suss run a harness: a manifest, `suss-harness.toml`, and the commands it names (§ 4). |
| **Arm** | A harness and the parameters it runs with, such as a model and a reasoning effort. |
| **Baseline** | The arm another arm is measured against, usually the harness's coding agent on its own. |
| **Trial** | Harbor's unit of work: one attempt at one task by one arm, in a fresh sandbox. |
| **Base** | The commit the workspace's `HEAD` names when the trial starts. |

### What a trial does

Suss is a Harbor custom agent, `suss.agent:SussAgent`. Harbor runs it once per
trial, in two phases. The setup phase has network access. The agent phase
reaches only the hosts the harness lists (§ 5).

In the setup phase, suss:

1. **finds the workspace and records the base.** The workspace is `/app`, or
   `/testbed` where `/app` does not exist. The base is `git rev-parse HEAD`.
2. **uploads the harness's adapter** to `/opt/suss/adapter/`.
3. **installs the harness**, if the adapter's manifest names an `install` command
   (§ 4).

In the agent phase, suss:

4. **runs the harness** on the task's instruction, unchanged (§ 4);
5. **captures the patch** (§ 3, Capturing the patch);
6. **writes the trial record** (§ 3, The trial record) and returns.

Harbor then runs the task's verifier in the same sandbox. Suss ignores that
result. The trial's result is its regrade (§ 3, Regrading).

### Capturing the patch

```bash
git add -A
git diff --cached --binary <base> > /logs/agent/model.patch
```

The diff runs from the base, so it holds the whole change, whether the harness
committed it or not.

### Regrading

After each agent job, suss runs a second Harbor job over the same tasks, with
SWE-bench Pro's own `patch_replay:PatchReplayAgent` as the agent. For each
trial, it starts a fresh sandbox from the task's image, applies the trial's
`model.patch`, and runs the verifier there. That verifier's reward is the
trial's result.

A harness cannot change its result by changing the sandbox it worked in, because
the grading happens in another.

`PatchReplayAgent` finds a patch by task name, so an agent job holds one trial
of each task, and suss runs K agent jobs for K trials (§ 6).

### The trial record

Suss writes these files to `/logs/agent/` in the sandbox. Harbor keeps that
directory as `agent/` in the trial's directory under `results/` (§ 6).

| File | Holds |
|---|---|
| `suss.json` | the trial record, below |
| `install.log` | the stdout and stderr of `install`, when the manifest names one |
| `run.log` | the stdout and stderr of `run` |
| `model.patch` | the patch (§ 3, Capturing the patch) |

```json
{
  "arm": "peer-review",
  "harness": { "name": "peer-review", "version": "0.1.0" },
  "params": { "model": "claude-opus-4-6", "effort": "high", "rounds": 3 },
  "grader": { "dataset": "swe-bench-pro-v2", "commit": "4e7a1c90…" },
  "base": "5568b2e0a686cc4d838bddc095b831379143e61b",
  "status": "ok",
  "exit_codes": { "install": 0, "run": 0 },
  "run_seconds": 1290,
  "patch_bytes": 4182
}
```

- `grader` is the dataset and the commit of the checkout `suss run` used.
- `status` is `ok`, `harness-failed` or `timed-out`.
- `exit_codes` holds the exit code of `install` and of `run`. A command suss
  skipped, or stopped at `max_seconds`, has `null`.

## 4. The harness contract

Suss runs a harness through its adapter, a directory the harness's own repository
ships. The adapter holds a manifest, `suss-harness.toml`:

```toml
[harness]
name = "peer-review"
version = "0.1.0"

# Run once, in the setup phase. Optional.
install = "install.sh"

# Run once, in the agent phase, on the task's instruction.
run = "run.sh"

# Hostnames the harness calls in the agent phase, which suss allows.
allowed_hosts = ["api.anthropic.com", "api.deepseek.com"]

# Environment variables the harness reads, passed through from the run's .env.
env = ["ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY"]
```

Suss runs both commands as the agent's user, with the workspace as the current
directory. Their environment holds only the variables named in `env`, taken from
the `.env` file beside `suss.toml`.

Suss writes the two files the commands are given to `/opt/suss/`, outside the
workspace, so neither becomes part of the patch:

| Argument | File | Holds |
|---|---|---|
| `--instruction` | `/opt/suss/instruction.md` | the task's instruction, unchanged |
| `--params` | `/opt/suss/params.json` | the arm's `params` table from `suss.toml` (§ 6), as JSON |

### Installing

```
install --workspace <path> --params <file>
```

The installer sets the harness up while the sandbox still has network access:
it installs the coding agent, its plugins and any packages `run` needs. It
writes nothing inside the workspace.

A non-zero exit marks the trial `harness-failed`, and suss skips `run`. The
patch is then empty, and the trial resolves nothing.

### Running

```
run --workspace <path> --instruction <file> --params <file> [--max-cost-usd <n>]
```

The harness does the task however it does it, and leaves its change in the
workspace, committed or not (§ 3, Capturing the patch). Anything it writes inside
the workspace is part of the patch.

`--max-cost-usd` is the arm's `max_cost_usd`, given when `suss.toml` sets one
(§ 6). The harness stops before its models' spend passes it, across every model
it calls. Suss stops `run` after the arm's `max_seconds`, marks the trial
`timed-out`, and captures the patch as the harness left it.

A non-zero exit marks the trial `harness-failed`. Either way, suss captures the
patch, and the regrade grades it.

### Built-in harnesses

Suss includes an adapter for the baseline other arms are measured against, in
`harnesses/`.

| Harness | Params | What it does |
|---|---|---|
| `claude-code` | `version`, `model`, `effort` | `install` installs Claude Code at `version` with its native installer. `run` runs it headless, with the instruction as its prompt, every tool allowed except `WebSearch` and `WebFetch`, and `--max-cost-usd` as its `--max-budget-usd`. |

## 5. Lanes

Suss runs SWE-bench Pro V2: 642 tasks from 11 repositories, in `v2/tasks/` of the
checkout. Each task gives the agent a repository at the commit before a real
change, and an instruction written from that change: what it does, its
requirements, and any interfaces it adds. The change is a bug fix or a feature.

The task's hidden tests grade it. A trial resolves when the tests the real
change made pass now pass, and the tests that passed before still pass. The share
of trials that resolve is the measure suss compares arms on.

Suss splits the tasks into lanes, so that a run can cover one language:

| Lane | Tasks | Repositories, by the owner in the task's name |
|---|---|---|
| `pro-go` | 256 | `flipt-io`, `gravitational`, `future-architect`, `navidrome` |
| `pro-python` | 237 | `ansible`, `internetarchive`, `qutebrowser` |
| `pro-js` | 149 | `protonmail`, `element-hq`, `NodeBB`, `tutao` |
| `pro-hard51` | 51 | the tasks listed in `v2/hard51_ids.txt` |

A task's owner is the first part of its directory name,
`instance_<owner>__<repo>-<commit>…`.

A lane's result is a result for its repositories. With three or four per
language, a harness that does better on `pro-go` may be doing better on
gravitational's Teleport and flipt-io's Flipt rather than on Go.

Every task's agent phase has no network (`network_mode = "no-network"`). Suss
passes each host in the harness's `allowed_hosts` to Harbor as
`--allow-agent-host`, and nothing else.

## 6. Commands

### `suss run`

```bash
suss run <arm> <lane>... [-- <harbor args>]
suss run peer-review pro-go pro-python
```

It reads the arm from `suss.toml` in the current directory. For each lane, it
builds `lanes/<lane>/` as a directory of links to the lane's task directories in
the checkout. Then, K times, it runs an agent job and its regrade:

```bash
harbor run -p lanes/pro-go -e modal -n 16 -k 1 \
  -a suss.agent:SussAgent --ak arm=peer-review --ak config=suss.toml \
  --allow-agent-host api.anthropic.com --allow-agent-host api.deepseek.com \
  --jobs-dir results/pro-go/peer-review --job-name <stamp>-1

PYTHONPATH=../SWE-bench_Pro-os/v2/tooling harbor run -p lanes/pro-go -e modal -n 16 -k 1 \
  -a patch_replay:PatchReplayAgent -m replay \
  --ak source_job=results/pro-go/peer-review/<stamp>-1 \
  --jobs-dir results/pro-go/peer-review --job-name <stamp>-1-regrade
```

Anything after `--` goes to every agent job. `DRY_RUN=1` prints the commands
instead of running them.

It gives Harbor `results/<lane>/<arm>/` as its jobs directory, so each agent job
and its regrade job are written there. Results stay there across runs, so an arm
run later is compared against a baseline graded earlier, without running the
baseline again.

`suss.toml` names the SWE-bench Pro checkout, where the sandboxes run, the caps
on each trial, and the arms:

```toml
swe_bench_pro = "../SWE-bench_Pro-os"
environment = "modal"
k = 1
concurrency = 16
max_cost_usd = 20
max_seconds = 2700

[arms.claude-code]
harness = "claude-code"
params = { version = "2.1.293", model = "claude-opus-4-6", effort = "high" }

[arms.self-review]
harness = "../self-review"
baseline = "claude-code"
params = { model = "claude-opus-4-6", effort = "high", rounds = 3 }

[arms.peer-review]
harness = "../peer-review"
baseline = "claude-code"
params = { model = "claude-opus-4-6", effort = "high", rounds = 3, reviewer = "deepseek/deepseek-v3.2" }
```

- `harness` is the name of a built-in adapter, or a path to one, relative to
  the directory holding `suss.toml`.
- `params` is passed to the harness as it stands (§ 4). Suss reads none of it.
- `environment` is passed to `harbor run` as `-e`: `modal` runs the sandboxes on
  Modal, and `docker` runs them on this machine.
- `max_cost_usd` and `max_seconds` cap each trial (§ 4, Running). Set at the top,
  they apply to every arm, and an arm may set its own. `max_seconds` must be
  under the task's agent timeout, 3000 seconds in every V2 task, so that suss
  captures the patch before Harbor stops the trial.

### `suss compare`

```bash
suss compare <lane> <arm> <arm>...
```

It reads every trial under `results/<lane>/<arm>/` for the arms it is given,
and each arm's `baseline` from `suss.toml` in the current directory.

A trial's result is the reward of its regrade. Suss pairs each trial in an agent
job, `<stamp>-<i>`, with the trial for the same task in `<stamp>-<i>-regrade`.

It compares the arms over the tasks every one of them has trials for, and prints
the tables shown in § 1:

- Every trial counts as its regrade graded it, `harness-failed` and `timed-out`
  ones included.
- A trial whose regrade has no reward counts as unresolved. A trial with no
  `suss.json`, which a sandbox that never started or a Harbor exception leaves,
  counts as `harness-failed`.
- `failed` is the share of trials whose status is `harness-failed`, and
  `timed out` the share whose status is `timed-out`.
- Each arm whose `baseline` is among the arms given gets a row in the second
  table. The bracketed range is a 95% interval from a bootstrap that resamples
  tasks, so that each task's K trials stay together.

It exits with an error, and prints nothing else, if the arms' trials do not all
carry the same `grader`.

## 7. The project

### Language

Python 3.12, managed with `uv`.

### Structure

```
src/suss/
  agent.py        SussAgent, the Harbor custom agent
  workspace.py    finding the workspace, and capturing the patch
  harness.py      reading a manifest, installing, and running a harness
  lanes.py        building a lane's directory from the checkout
  config.py       suss.toml
  compare.py      suss compare
  cli.py          the suss command
harnesses/
  claude-code/
tests/
docs/specs/
```
