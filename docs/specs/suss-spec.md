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
self-review - claude-code   +3% [-2%, +8%]
peer-review - claude-code   +6% [+1%, +11%]
```

## 2. Dependencies

| Dependency | Version | Needed for |
|---|---|---|
| Harbor | 0.22.0 | Runs each task in a sandbox, runs suss in it, and grades the result with the task's own verifier. |
| Docker | Docker Desktop 4.86.0 or later on macOS. On Linux and Windows, a Docker engine whose kernel has `CONFIG_NFT_FIB_INET`. | The sandboxes, including every SWE-bench Pro lane's, with `environment = "docker"` in `suss.toml`. Without `CONFIG_NFT_FIB_INET`, Harbor refuses every SWE-bench Pro task. |
| Modal | 1.5.1 or later, and the account Harbor's `modal` environment uses. `uv sync --extra modal` installs its client into suss's environment, through Harbor's own `modal` extra at the Harbor version above. A plain `uv sync` leaves it out. | The sandboxes, when `environment` is `modal`. Versions before 1.5.1 cannot take a network policy per phase. |
| SWE-bench Pro | a checkout of `scaleapi/SWE-bench_Pro-os` | The tasks, under `v2/tasks/`, and the regrading agent, under `v2/tooling/`. |
| Python | 3.12 | Suss itself, which Harbor loads as a custom agent. |
| `uv` | any | Installs suss and Harbor. |

A Docker engine's kernel has the option when this prints `CONFIG_NFT_FIB_INET=y`
or `CONFIG_NFT_FIB_INET=m`:

```bash
docker run --rm alpine:3.19 sh -c 'zcat /proc/config.gz | grep NFT_FIB_INET'
```

## 3. A trial

### Terminology

| Term | Meaning |
|---|---|
| **Task** | One benchmark task: a repository in a sandbox image, an instruction, and hidden tests. |
| **Lane** | A set of tasks that suss runs and reports as one, such as `pro-go`. |
| **Harness** | What is being measured, reached through its adapter. |
| **Adapter** | The directory that lets suss run a harness: a manifest, `suss-harness.toml`, and the commands it names. |
| **Arm** | A harness and the parameters it runs with, such as a model and a reasoning effort. |
| **Baseline** | The arm another arm is measured against, named by that arm's `baseline` in `suss.toml`. Any arm can be one. |
| **Trial** | Harbor's unit of work: one attempt at one task by one arm, in a fresh sandbox. |
| **Base** | The commit the workspace's `HEAD` names when the trial starts. |

### What a trial does

Suss is a Harbor custom agent, `suss.agent:SussAgent`. Harbor runs it once per
trial, in two phases. The setup phase has network access. The agent phase
reaches only the hosts the harness's manifest lists in `allowed_hosts`.

In the setup phase, suss:

1. **finds the workspace and records the base.** The workspace is `/app`, or
   `/testbed` where `/app` does not exist. The base is `git rev-parse HEAD`.
2. **uploads the harness's adapter** to `/opt/suss/adapter/`.
3. **installs the harness**, if the adapter's manifest names an `install` command.

In the agent phase, suss:

4. **runs the harness's `run` command** on the task's instruction, unchanged;
5. **captures the patch**, the whole change from the base;
6. **writes the trial record**, `suss.json`, and returns.

Harbor then ends the trial without running the task's verifier, because suss
runs every agent job with `--disable-verification`. The trial's result comes
from a regrade in a fresh sandbox.

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
of each task, and suss runs K agent jobs for K trials, where K is `k` in `suss.toml`.

### The trial record

Suss writes these files to `/logs/agent/` in the sandbox. Harbor keeps that
directory as `agent/` in the trial's directory under `results/`.

| File | Holds |
|---|---|
| `suss.json` | the trial record, below |
| `install.log` | the stdout and stderr of `install`, when the manifest names one |
| `run.log` | the stdout and stderr of `run` |
| `model.patch` | the patch, the whole change from the base |

```json
{
  "arm": "peer-review",
  "harness": { "name": "peer-review", "version": "0.1.0" },
  "params": { "model": "claude-opus-4-6", "effort": "high", "rounds": 3 },
  "grader": { "dataset": "swe-bench-pro-v2", "commit": "4e7a1c90…" },
  "base": "5568b2e0a686cc4d838bddc095b831379143e61b",
  "status": "ok",
  "exit_codes": { "install": 0, "run": 0 },
  "run_seconds": 1290.4,
  "patch_bytes": 4182
}
```

- `grader` is the `dataset` that `suss.toml` names, and the commit of the
  checkout `suss run` used.
- `status` is `ok`, `harness-failed` or `timed-out`. A trial is `timed-out`
  when suss stops `run` at `max_seconds`, and when Harbor's agent timeout stops
  the trial while `run` is going. Either way, suss stops the harness inside the
  sandbox, then captures the patch and writes the record.
- `exit_codes` holds the exit code of `install` and of `run`. A command suss
  skipped or stopped has `null`.

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
directory. Their environment holds the variables named in `env`, taken from the
`.env` file beside `suss.toml`, and `SUSS_START_TOKEN`, by which suss finds every
process the command started when it stops it. A harness leaves it in place.

Suss writes the two files the commands are given to `/opt/suss/`, outside the
workspace, so neither becomes part of the patch:

| Argument | File | Holds |
|---|---|---|
| `--instruction` | `/opt/suss/instruction.md` | the task's instruction, unchanged |
| `--params` | `/opt/suss/params.json` | the arm's `params` table from `suss.toml`, as JSON |

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
run --workspace <path> --instruction <file> --params <file>
```

The harness does the task however it does it, and leaves its change in the
workspace, committed or not. Anything it writes inside the workspace is part of
the patch.

Suss stops `run` after the arm's `max_seconds`, marks the trial `timed-out`, and
captures the patch as the harness left it. Suss gives the harness no spending
limit. A harness that takes one reads it from its params, under the name and in
the unit its adapter chooses.

A non-zero exit marks the trial `harness-failed`. Either way, suss captures the
patch, and the regrade grades it.

### Adapters in this repository

The repository publishes adapters under `adapters/`. An arm names one by its
path, as it names any other adapter, and suss runs it the same way.

Both take flat params: `version` and `model` are required, `effort` and the
spending limit optional. `run` exits 1 naming any param it cannot take, before
the coding agent starts.

| Adapter | Params | Spending limit | What it does |
|---|---|---|---|
| `adapters/claude-code/` | `version`, `model`, `effort`, `max_budget_usd` | `max_budget_usd`, in dollars, as `--max-budget-usd`. One model call can pass it. `run` exits 0 at the limit. | `install` installs Claude Code at `version` with its native installer. `run` runs it headless, with the instruction as its prompt, `model` as `--model`, `effort` as `--effort`, and every tool allowed except `WebSearch` and `WebFetch`. It prints Claude Code's messages as they arrive, so `run.log` holds the token usage of every model call that finished, even when suss stops `run` at `max_seconds`. When Claude Code exits by itself, the last line is its result, which gives its cost in dollars. |
| `adapters/copilot-cli/` | `version`, `model`, `effort`, `max_ai_credits` | `max_ai_credits`, in AI credits, as `--max-ai-credits`, at least 30. One model call can pass it. `run` exits 0 at the limit, and the model sees the limit. | `install` downloads the standalone binary of GitHub Copilot CLI's release `version`, with `curl`, else `wget`, else Python, so the image needs no Node. It takes the musl build where `/lib/ld-musl-*` exists, and the glibc build otherwise. It fails when the download does not match the release's `SHA256SUMS.txt`. `run` runs `copilot -p` with the instruction as its prompt, `model` as `--model`, `effort` as `--reasoning-effort`, every tool allowed except `web_fetch` and `web_search`, and no built-in MCP servers, auto-update or client telemetry. `run` prints Copilot CLI's `--output-format json` lines as they arrive. It exits 1 naming `COPILOT_GITHUB_TOKEN` when that variable is empty. Copilot CLI sees no other token, no stored login and no `gh` login. |

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
suss run <arm> <lane>... [--check] [-- <harbor args>]
suss run peer-review pro-go pro-python
```

It reads the arm from `suss.toml` in the current directory. For each lane, it
builds `lanes/<lane>/` as a directory of links to the lane's task directories in
the checkout.

When `environment` is `docker`, it then pulls the image each of the lane's tasks
names in `[environment] docker_image` of its `task.toml`. The V2 images are
built for amd64 only, so on a Docker engine that runs on arm64, such as Docker
Desktop on Apple Silicon, it pulls them for amd64:

```bash
docker pull --platform linux/amd64 <docker_image>
```

Then, K times, it runs an agent job and its regrade:

```bash
<env>/bin/harbor run -p lanes/pro-go -e docker -n 16 -k 1 \
  -a suss.agent:SussAgent --ak arm=peer-review --ak config=suss.toml \
  --allow-agent-host api.anthropic.com --allow-agent-host api.deepseek.com \
  --disable-verification \
  --jobs-dir results/pro-go/peer-review --job-name <stamp>-1

PYTHONPATH=<checkout>/v2/tooling <env>/bin/harbor run -p lanes/pro-go -e docker -n 16 -k 1 \
  -a patch_replay:PatchReplayAgent -m replay \
  --ak source_job=<dir>/results/pro-go/peer-review/<stamp>-1 \
  --jobs-dir results/pro-go/peer-review --job-name <stamp>-1-regrade
```

`<stamp>` is the time `suss run` started, in UTC, as `20261031T142500Z`. Every
job of the run shares it, and `-1` to `-K` tell its agent jobs apart. Suss
forms three absolute paths itself:

- `<env>` is the Python environment suss is installed in. Its `harbor` is the
  one that can import `suss.agent`, which a `harbor` elsewhere on `PATH` may
  not be.
- `<checkout>` is the SWE-bench Pro checkout that `swe_bench_pro` names.
- `<dir>` is the directory holding `suss.toml`, so the regrade's `source_job`
  is the agent job's directory.

Options after `--` go to every agent job. Passing any of these, under any name
or spelling Harbor accepts, is an error that names it and says why, and runs
nothing:

- An option suss sets on the agent job or its regrade, even with suss's own
  value: `-p`, `-e`, `-n`, `-k`, `-a`, `-m`, `--ak`, `--allow-agent-host`,
  `--disable-verification` and its opposite `--enable-verification`,
  `--jobs-dir` and `--job-name`.
- An option that changes the tasks or the agent the agent job runs, or keeps it
  from leaving a trial on this machine for its regrade: `-c`, `-d`, `-t`, `-i`,
  `-x`, `-l`, `--registry-url`, `--registry-path`, `--repo`, `--task-git-url`,
  `--task-git-commit`, `--agent-import-path`, `--environment-import-path`,
  `--user-agent`, `--user-model`, `--uk`, `--user-persona-path`,
  `--user-prompt-template-path`, `--bridge`, `--bridge-prompt-path`, `--bk`,
  `--install-only`, `--launch`, `--dry-run`, `--init` and `--print-config`.
- A positional argument, which `harbor run` does not take.

```
$ suss run peer-review pro-go -- -k 2
error: -k/--n-attempts can't be passed after --: suss runs one trial per agent
job, and k in suss.toml sets how many jobs
```

`DRY_RUN=1` prints the `harbor run` commands instead of running them, and pulls
nothing.

Every agent job is followed by its regrade, whatever its trials did and whatever
Harbor's exit code was. A trial that failed or errored is graded as its regrade
grades it. The one exception is an agent job that leaves no trial in its job
directory, as when Harbor cannot start at all: `suss run` then prints the job's
name and Harbor's output, runs nothing more, and exits non-zero.

It gives Harbor `results/<lane>/<arm>/` as its jobs directory, so each agent job
and its regrade job are written there. Results stay there across runs, so an arm
run later is compared against a baseline graded earlier, without running the
baseline again.

`--check` tests that an arm works under suss, for the cost of one trial per
lane. It builds `lanes/<lane>-check/` with only the lane's first task by name,
and gives that to both jobs in place of `lanes/<lane>/`, which a full run of the
lane may be using. It runs one agent job and its regrade whatever `k` is, and
writes both to `checks/<lane>/<arm>/` instead of `results/`:

```bash
suss run peer-review pro-go --check
```

`suss.toml` names the SWE-bench Pro checkout and its dataset, where the
sandboxes run, the cap on each trial, and the arms:

```toml
swe_bench_pro = "../SWE-bench_Pro-os"
dataset = "swe-bench-pro-v2"
environment = "docker"
k = 1
concurrency = 16
max_seconds = 2700

[arms.claude-code]
harness = "adapters/claude-code"
params = { version = "2.1.293", model = "claude-opus-4-6", effort = "high", max_budget_usd = 20 }

[arms.self-review]
harness = "../self-review"
baseline = "claude-code"
params = { model = "claude-opus-4-6", effort = "high", rounds = 3 }

[arms.peer-review]
harness = "../peer-review"
baseline = "claude-code"
params = { model = "claude-opus-4-6", effort = "high", rounds = 3, reviewer = "deepseek/deepseek-v3.2" }
```

- `dataset` names the dataset the checkout holds. Suss copies it into each
  trial's `grader` as it stands.
- `harness` is the path to an adapter, relative to the directory holding
  `suss.toml`.
- `params` is passed to the harness as it stands, as the JSON file `--params`
  names. Suss reads none of it. An arm's spending limit goes here, under the
  name its adapter takes, such as `max_budget_usd` for `adapters/claude-code/`.
- `environment` is passed to `harbor run` as `-e`: `docker` runs the sandboxes
  on this machine, and `modal` runs them on Modal. With `modal`, when Modal's
  client is not installed in suss's environment, `suss run` exits with an error
  before it builds a lane or runs any job, with `DRY_RUN=1` and `--check` too:

  ```
  $ suss run peer-review pro-go
  error: environment is modal, but Modal's client is not installed: run uv sync
  --extra modal
  ```
- `concurrency` is passed to `harbor run` as `-n`: how many trials run at once.
  When `environment` is `docker`, `concurrency` times the largest
  `[environment] cpus` among the lane's tasks, and `concurrency` times their
  largest `memory_mb`, must fit in the CPUs and memory that
  `docker info --format '{{.NCPU}} {{.MemTotal}}'` reports. An
  `--override-cpus` or `--override-memory-mb` passed after `--` counts in place
  of the tasks' own where it is larger. Memory is counted in MiB, as
  `memory_mb` is, and Docker's is rounded down. When either does not
  fit, `suss run` exits with an error before it builds any lane, pulls any
  image or runs any job, and does so under `DRY_RUN=1` too. A task that names
  no `cpus` or no `memory_mb` is an error that names it. A `--check` counts
  only its one task, and one trial at a time.

  ```
  $ suss run peer-review pro-go
  error: concurrency = 16 needs 16 CPUs and 65536 MiB for the tasks in pro-go,
  but Docker has 10 CPUs and 7934 MiB; set concurrency to 1 or less, or give
  Docker more
  ```

  When a single trial does not fit, the error says so and offers no
  `concurrency`.
- `max_seconds` caps each trial: suss stops `run` after it. Set at the top, it
  applies to every arm, and an arm may set its own. It must be under the task's
  agent timeout, 3000 seconds in every V2 task, so that suss captures the patch
  before Harbor stops the trial. When it is not under the agent timeout of every
  task in a lane, `suss run` exits with an error before running any job:

  ```
  $ suss run peer-review pro-go
  error: arm peer-review sets max_seconds = 3000, which must be under the
  3000-second agent timeout of the tasks in pro-go
  ```
- `max_cost_usd`, at the top or on the arm, makes `suss run` exit with an error
  before it builds any lane or runs any job, with `DRY_RUN=1` and `--check` too:

  ```
  $ suss run peer-review pro-go
  error: /home/me/evals/suss.toml: [arms.peer-review] sets max_cost_usd, but suss
  does not read it; set the arm's spending limit in its params, under the name
  its adapter takes, such as max_budget_usd for adapters/claude-code
  ```

### `suss compare`

```bash
suss compare <lane> <arm> <arm>...
```

It reads every trial under `results/<lane>/<arm>/` for the arms it is given,
and each arm's `baseline` from `suss.toml` in the current directory. Trials
under `checks/` never count.

A trial's result is the reward of its regrade. Suss pairs each trial in an agent
job, `<stamp>-<i>`, with the trial for the same task in `<stamp>-<i>-regrade`.

It compares the arms over the tasks every one of them has trials for. It prints
two tables. The first gives each arm's trials and its resolved, failed and
timed-out shares. The second gives each arm's resolved difference from its
baseline, with an interval.

- Every trial counts as its regrade graded it, `harness-failed` and `timed-out`
  ones included.
- A trial whose regrade has no reward counts as unresolved. A trial with no
  `suss.json`, which a sandbox that never started or a Harbor exception leaves,
  counts as `harness-failed`.
- `failed` is the share of trials whose status is `harness-failed`, and
  `timed out` the share whose status is `timed-out`.
- Each arm whose `baseline` is among the arms given gets a row in the second
  table. The bracketed range is a 95% interval from a bootstrap of 10,000
  resamples, each drawing tasks so that a task's K trials stay together. The
  same results always print the same interval.
- Shares print as whole percents, and every difference and interval end
  carries a sign.

It exits with an error, and prints nothing else, when:

- An arm it is given has no trials under `results/<lane>/<arm>/`, or is not in
  `suss.toml`.
- The arms' trials that have a `suss.json` do not all carry the same `grader`.
  A trial with no `suss.json` has no `grader` to compare, and still counts as
  `harness-failed`.
- A trial whose `suss.json` has `patch_bytes` above 0 has a regrade whose
  `agent/replay.json` has `"patch": null`.

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
adapters/
  claude-code/
  copilot-cli/
tests/
docs/specs/
```
