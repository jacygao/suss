# Suss Specification: Measuring and Comparing Coding Harnesses

**Version:** 0.1 (draft)

## 1. Purpose

**Suss measures and compares how well coding harnesses do specific kinds of
coding work.** A harness is anything that takes a task's instruction and changes
a repository. Each kind of work is a lane: a set of benchmark tasks and the way
they are graded. Suss runs the same tasks under several arms, grades every
result the same way, and reports how each arm's score differs from its
baseline's.

Comparing plain Claude Code with two harnesses that review its change prints
output like this:

```
$ suss compare rebench claude-code self-review peer-review

arm          trials  resolved  failed  timed out
claude-code     108       58%      0%         1%
self-review     108       62%      1%         1%
peer-review     108       66%      1%         2%

vs. baseline                resolved
self-review - claude-code   +4% [-4%, +12%]
peer-review - claude-code   +8% [+1%, +15%]
```

## 2. Dependencies

| Dependency | Version | Needed for |
|---|---|---|
| Harbor | 0.22.0 | Runs each task in a sandbox, runs suss in it, and grades the result with the task's own verifier. |
| Docker | Docker Desktop 4.86.0 or later on macOS. On Linux and Windows, a Docker engine whose kernel has `CONFIG_NFT_FIB_INET`. | The sandboxes, with `environment = "docker"` in `suss.toml`. Without `CONFIG_NFT_FIB_INET`, Harbor refuses every task, because the agent phase has no network. |
| Modal | 1.5.1 or later, and the account Harbor's `modal` environment uses. `uv sync --extra modal` installs its client into suss's environment, through Harbor's own `modal` extra at the Harbor version above. A plain `uv sync` leaves it out. | The sandboxes, when `environment` is `modal`. Versions before 1.5.1 cannot take a network policy per phase. |
| SWE-rebench | `ibragim-badertdinov/swe-rebench-07-2026` from Harbor Hub, as `harbor download` writes it | The tasks. |
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
| **Lane** | A set of tasks that suss runs and reports as one, such as `rebench`. |
| **Harness** | What is being measured, reached through its adapter. |
| **Adapter** | The directory that lets suss run a harness: a manifest, `suss-harness.toml`, and the commands it names. |
| **Arm** | A harness and the parameters it runs with, such as a model and a reasoning effort. |
| **Baseline** | The arm another arm is measured against, named by that arm's `baseline` in `suss.toml`. Any arm can be one. |
| **Trial** | Harbor's unit of work: one attempt at one task by one arm, in a fresh sandbox. |
| **Base** | The commit the workspace's `HEAD` names when the trial starts. |
| **Starting tree** | What the workspace holds when the trial starts: the base, with every change the image left uncommitted, staged or not, and every untracked file git does not ignore. |

### What a trial does

Suss is a Harbor custom agent, `suss.agent:SussAgent`. Harbor runs it once per
trial, in two phases. The setup phase has network access. The agent phase
reaches only the hosts the harness's manifest lists in `allowed_hosts`, and
those its task lists in `[agent] allowed_hosts`.

In the setup phase, suss:

1. **finds the workspace, and records the base and the starting tree.** The
   workspace is the directory the sandbox starts in, which is the image's
   `WORKDIR`, such as `/gleam`. It must be the top level of a git repository,
   or the trial fails naming it. The base is `git rev-parse HEAD`. Recording the
   starting tree leaves the index, `HEAD`, the refs and `git status` as the
   image left them.
2. **uploads the harness's adapter** to `/opt/suss/adapter/`.
3. **installs the harness**, if the adapter's manifest names an `install` command.

In the agent phase, suss:

4. **runs the harness's `run` command** on the task's instruction, unchanged;
5. **captures the patch**, the whole change from the starting tree;
6. **writes the trial record**, `suss.json`, and returns.

Harbor then ends the trial without running the task's verifier, because suss
runs every agent job with `--disable-verification`. The trial's result comes
from a regrade in a fresh sandbox.

### Capturing the patch

```bash
git add -A
git diff --cached --binary <starting tree> > /logs/agent/model.patch
```

The diff runs from the starting tree, so it holds the whole change the harness
made, whether it committed it or not, and none of the changes the image already
had. A regrade starts from the same image, so the patch applies there with
`git apply`. A file of the starting tree that the harness made git ignore, but
left in place, stays out of the patch.

### Regrading

After each agent job, suss runs a second Harbor job over the same tasks, with
suss's own `suss.replay:ReplayAgent` as the agent. For each trial, it starts a
fresh sandbox from the task's image, applies the trial's `model.patch` in the
workspace, found as the trial found it, puts every test file back as the image
held it, and runs the verifier there. That verifier's reward is the trial's
result. It applies the patch with:

```bash
git apply --verbose model.patch
```

Git applies all of the patch or none of it. The patch runs from the starting
tree, which the regrade's sandbox also starts from, so a patch suss captured
applies there.

A harness cannot change its result by changing the sandbox it worked in, because
the grading happens in another.

`ReplayAgent` reads the sandbox's starting tree before applying the patch.
After applying it, it restores from that tree each test file the patch edited,
deleted or renamed, and removes each test file the patch added. So the verifier
grades every test file as the image held it, whatever the harness did to it.

A path is a test file when one of its words is `test`, `tests`, `testdata` or
`mdtest`, or its file name ends in `.spec.` followed by `js`, `jsx`, `ts`, `tsx`,
`mjs`, `cjs`, `mts` or `cts`. A path's words are its runs of letters, each split
before a capital that starts a lowercase word, compared ignoring case:

| Path | Test file |
|---|---|
| `core/src/test/java/io/kestra/core/utils/LogsTest.java` | yes, by `test` |
| `pkg/lines_test.go` | yes, by `test` |
| `packages/api/src/agents/tools.spec.ts` | yes, by its name |
| `internal/spec/spec.go` | no |
| `server/handler/GetTimeSeriesLatestHandler.java` | no: `Latest` is one word |

The rule reads the path alone, so a test inside a source file, such as a Rust
`mod tests`, is graded as the patch left it.

`ReplayAgent` finds a trial's patch by the task the trial ran, among every trial
directory of the agent job. So an agent job holds one trial of each task, and
suss runs K agent jobs for K trials, where K is `k` in `suss.toml`. When the
agent job holds no trial of the task, or more than one, `ReplayAgent` fails the
regrade's trial, and Harbor records the exception in that trial's
`result.json`. It fails the trial the same way when git cannot read the
starting tree, list what changed from it, or put a test file back.

It writes `agent/replay.json` in the regrade's trial. With no patch, or an empty
one, it applies nothing and writes `"patch": null`:

```json
{ "task": "gleam-lang__gleam-5797", "patch": null }
```

Otherwise it writes the patch's path, the exit code of applying it, as
`apply_rc`, what applying it printed, as `apply_out`, and the test files it put
back, as `tests_reset`. Both lists are empty when the patch changed no test
file:

```json
"tests_reset": {
  "restored": ["tests/features/migrations/Migrator.sqlite.test.ts"],
  "removed": []
}
```

### The trial record

Suss writes these files to `/logs/agent/` in the sandbox. Harbor keeps that
directory as `agent/` in the trial's directory under `results/`.

| File | Holds |
|---|---|
| `suss.json` | the trial record, below |
| `install.log` | the stdout and stderr of `install`, when the manifest names one |
| `run.log` | the stdout and stderr of `run` |
| `model.patch` | the patch, the whole change from the starting tree |

```json
{
  "arm": "peer-review",
  "harness": { "name": "peer-review", "version": "0.1.0" },
  "params": { "model": "claude-opus-4-6", "effort": "high", "rounds": 3 },
  "grader": { "dataset": "swe-rebench-07-2026", "digest": "9c2f04b1…" },
  "base": "5568b2e0a686cc4d838bddc095b831379143e61b",
  "status": "ok",
  "exit_codes": { "install": 0, "run": 0 },
  "run_seconds": 1290.4,
  "patch_bytes": 4182
}
```

- `grader` names the dataset the trial's task came from, and a sha256 digest of
  every file of every task in the directory `swe_rebench` names, by path and
  content. A link inside a task counts by its target, and is not followed. A
  task directory that is a link counts as the task it names. File times, modes,
  empty directories, and files outside every task, such as the download's
  `README.md`, leave the digest unchanged, so two downloads of the same tasks
  have the same digest.
- `base` is the commit. `model.patch` applies to the starting tree, which is
  the base only when the image left no change in the workspace.
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

Each takes flat params: `version` and `model` are required, `effort` and the
spending limit optional, and `hve_core_commit` required where the table lists
it. `run` exits 1 naming any param it cannot take, before the coding agent
starts.

| Adapter | Params | Spending limit | What it does |
|---|---|---|---|
| `adapters/claude-code/` | `version`, `model`, `effort`, `max_budget_usd` | `max_budget_usd`, in dollars, as `--max-budget-usd`. One model call can pass it. `run` exits 0 at the limit. | `install` installs Claude Code at `version` with its native installer. `run` runs it headless, with the instruction as its prompt, `model` as `--model`, `effort` as `--effort`, and every tool allowed except `WebSearch` and `WebFetch`. It prints Claude Code's messages as they arrive, so `run.log` holds the token usage of every model call that finished, even when suss stops `run` at `max_seconds`. When Claude Code exits by itself, the last line is its result, which gives its cost in dollars. |
| `adapters/copilot-cli/` | `version`, `model`, `effort`, `max_ai_credits` | `max_ai_credits`, in AI credits, as `--max-ai-credits`, at least 30. One model call can pass it. `run` exits 0 at the limit, and the model sees the limit. | `install` downloads the standalone binary of GitHub Copilot CLI's release `version`, with `curl`, else `wget`, else Python, so the image needs no Node. It takes the musl build where `/lib/ld-musl-*` exists, and the glibc build otherwise. It fails when the download does not match the release's `SHA256SUMS.txt`. `run` runs `copilot -p` with the instruction as its prompt, `model` as `--model`, `effort` as `--reasoning-effort`, every tool allowed except `web_fetch` and `web_search`, and no built-in MCP servers, auto-update or client telemetry. `run` prints Copilot CLI's `--output-format json` lines as they arrive. It exits 1 naming `COPILOT_GITHUB_TOKEN` when that variable is empty. Copilot CLI sees no other token, no stored login and no `gh` login. |
| `adapters/copilot-cli-hve/` | `version`, `model`, `effort`, `max_ai_credits`, `hve_core_commit` | As `adapters/copilot-cli/`. | It runs Copilot CLI as `adapters/copilot-cli/` does, with Microsoft's hve-core plugin and its RPI Agent. `install` also downloads hve-core at `hve_core_commit`, a full 40-digit commit, to `$HOME/.local/share/hve-core/<commit>/`, and fails when that commit's `rpi-agent.agent.md` has no "Full Auto" handoff prompt. `run` adds `--plugin-dir` with that directory and `--agent hve-core:rpi-agent`, and gives as the prompt the Full Auto prompt, a blank line, then the instruction. `model` and `effort` apply to the RPI Agent. `run` sets no model for its subagents, whose own files name theirs. RPI keeps its state and documents in the workspace's `.copilot-tracking/`, which `run` makes a link to `/logs/agent/copilot-tracking/` and adds to `.git/info/exclude`, so they are kept in the trial's `agent/` as they are written, even when suss stops `run`, and stay out of the patch. `run` exits 1 before Copilot CLI starts when the workspace already has `.copilot-tracking`, or when its own ignore rules keep git from ignoring it. |

## 5. Lanes

Every task gives the agent a repository at the commit before a real change, and
an instruction about that change. The task's hidden tests grade it. A trial
resolves when the tests the real change made pass now pass, and the tests that
passed before still pass. The share of trials that resolve is the measure suss
compares arms on.

Suss has one lane, `rebench`: 108 of the 111 tasks of SWE-rebench's July 2026
snapshot, from 64 repositories in Go (21), Java (18), Python (20), Rust (25) and
TypeScript (24). It leaves out `langchain4j__langchain4j-5315`,
`langchain4j__langchain4j-5367` and `openrewrite__rewrite-7784`, whose builds
need Maven or Gradle artifacts that no longer exist, so no fix can pass their
tests. A download that lacks any of the three still builds the lane. The tasks
are made from pull requests merged between 15 May and 15 June 2026. Each instruction is the issue the pull request closed, and an
`Interface` section naming any interface it adds.

Each task's image is built from the `Dockerfile` in its `environment/`, and the
build downloads from the network. Its repository is in the image's `WORKDIR`.

### Network

In the agent phase, a trial reaches the hosts in the harness's `allowed_hosts`
and the hosts in its own task's `[agent] allowed_hosts`, and no other host, so a
harness cannot fetch a task's real change from the web. A `rebench` task names
no hosts of its own, so its trials reach the harness's alone.

Suss passes each host in the harness's `allowed_hosts` to Harbor as
`--allow-agent-host`, and nothing else. Harbor adds them, trial by trial, to the
hosts of that trial's own task. A host that one task names stays closed to
every other task's trials in the same job, and a host the harness and the task
both name is opened once. A task allowing `pypi.org`
and `clickhouse.com`, under a harness allowing `api.anthropic.com` and
`pypi.org`, has an agent phase that reaches `pypi.org`, `clickhouse.com` and
`api.anthropic.com`.

Harbor ignores `--allow-agent-host` when the agent's network is public, which
it is when a task's `[agent]` sets no `network_mode` and the task gives the
network to the whole trial, with `[environment] network_mode = "public"` or with
`allow_internet = true`. Every `rebench` task does. So the lane holds a copy of
each task, and the copy's `task.toml` adds `[agent] network_mode = "no-network"`
where `[agent]` sets no `network_mode`. A `task.toml` whose `[agent]` sets
`no-network` or `allowlist` is copied unchanged. One whose `[agent]` sets any
other `network_mode`, or sets `allowed_hosts` with no `network_mode`, makes
`suss run` exit with an error naming it before it builds any lane or runs any
job. The verifier keeps the task's own network, which it uses to download a
Python on an image that has none.

## 6. Commands

### `suss run`

```bash
suss run <arm> <lane>... [--check] [-- <harbor args>]
suss run peer-review rebench
```

It reads the arm from `suss.toml` in the current directory. For each lane, it
builds `lanes/<lane>/`, a directory of copies of the lane's task directories
from the directory `swe_rebench` names, each with
`[agent] network_mode = "no-network"` added to its `task.toml` where its
`[agent]` sets no `network_mode`. A build removes
what an earlier build of the lane left. Harbor builds each task's image from its
`Dockerfile` when its trial starts.

Then, K times, it runs an agent job and its regrade:

```bash
<env>/bin/harbor run -p lanes/rebench -e docker -n 16 -k 1 \
  -a suss.agent:SussAgent --ak arm=peer-review --ak config=suss.toml \
  --ak lane=rebench \
  --allow-agent-host api.anthropic.com --allow-agent-host api.deepseek.com \
  --disable-verification \
  --jobs-dir results/rebench/peer-review --job-name <stamp>-1

<env>/bin/harbor run -p lanes/rebench -e docker -n 16 -k 1 \
  -a suss.replay:ReplayAgent -m replay \
  --ak source_job=<dir>/results/rebench/peer-review/<stamp>-1 \
  --jobs-dir results/rebench/peer-review --job-name <stamp>-1-regrade
```

`<stamp>` is the time `suss run` started, in UTC, as `20261031T142500Z`. Every
job of the run shares it, and `-1` to `-K` tell its agent jobs apart. Suss
forms two absolute paths itself:

- `<env>` is the Python environment suss is installed in. Its `harbor` is the
  one that can import `suss.agent` and `suss.replay`, which a `harbor` elsewhere
  on `PATH` may not be.
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
$ suss run peer-review rebench -- -k 2
error: -k/--n-attempts can't be passed after --: suss runs one trial per agent
job, and k in suss.toml sets how many jobs
```

`DRY_RUN=1` prints the `harbor run` commands instead of running them.

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
suss run peer-review rebench --check
```

`suss.toml` names where the tasks are, where the sandboxes run, the cap on each
trial, and the arms:

```toml
swe_rebench = "../tasks/swe-rebench-07-2026"
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

- `swe_rebench` is `<dir>/swe-rebench-07-2026`, the directory of tasks that
  `harbor download ibragim-badertdinov/swe-rebench-07-2026 -o <dir>` writes,
  relative to the directory holding `suss.toml`.
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
  $ suss run peer-review rebench
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
  fit, `suss run` exits with an error before it builds any lane or runs any
  job, and does so under `DRY_RUN=1` too. A task that names
  no `cpus` or no `memory_mb` is an error that names it. A `--check` counts
  only its one task, and one trial at a time.

  ```
  $ suss run peer-review rebench
  error: concurrency = 16 needs 16 CPUs and 65536 MiB for the tasks in rebench,
  but Docker has 10 CPUs and 7934 MiB; set concurrency to 1 or less, or give
  Docker more
  ```

  When a single trial does not fit, the error says so and offers no
  `concurrency`.
- `max_seconds` caps each trial: suss stops `run` after it. It can be set at
  the top, in a lane's own table, and on an arm. On each lane, an arm's trials
  take the arm's own value, else the lane's, else the top's. A `--check` takes
  the value of the lane it checks. Here, every arm without its own value gets
  2400 on `rebench`:

  ```toml
  max_seconds = 2700

  [lanes.rebench]
  max_seconds = 2400
  ```

  A `[lanes.<lane>]` table for a lane suss does not have, or one that sets
  anything else, makes `suss run` exit with an error before it builds any lane
  or runs any job:

  ```
  $ suss run peer-review rebench
  error: /home/me/evals/suss.toml: sets [lanes.rebnech], but suss has no lane
  named rebnech; its lanes are rebench
  ```

  The value must be under the agent timeout of every task in the lane, 3000
  seconds in every `rebench` task, so that suss captures the patch before
  Harbor stops the trial. Each lane is checked against its own value. When one
  is not under the timeout, `suss run` exits with an error before running any
  job:

  ```
  $ suss run peer-review rebench
  error: arm peer-review sets max_seconds = 3000, which must be under the
  3000-second agent timeout of the tasks in rebench
  ```
- `max_cost_usd`, at the top or on the arm, makes `suss run` exit with an error
  before it builds any lane or runs any job, with `DRY_RUN=1` and `--check` too:

  ```
  $ suss run peer-review rebench
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
baseline, with an interval. After the tables, it names each trial whose regrade
recorded no test result, by arm, job and task:

```
These trials' regrades recorded no test result, so their grading may have broken:
  claude-code  20261103T204423Z-2  marimo-team__marimo-9766
```

- Every trial counts as its regrade graded it, `harness-failed` and `timed-out`
  ones included.
- A regrade records no test result when its trial has no
  `verifier/report.json`, as the verifier of every `rebench` task writes it, or
  when that report's `parsed_counts` counts no test as `PASSED`, `FAILED`,
  `SKIPPED` or `ERROR`. A trial with no regrade trial records none either.
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
- A trial whose `suss.json` has `patch_bytes` above 0 has a regrade that
  applied no patch: its `agent/replay.json` has `"patch": null`, or it has no
  `agent/replay.json` and its `result.json` records an exception.
- A trial's regrade could not apply its patch: its `agent/replay.json` names a
  patch, and its `apply_rc` is not 0.

## 7. The project

### Language

Python 3.12, managed with `uv`.

### Structure

```
src/suss/
  agent.py        SussAgent, the Harbor custom agent
  workspace.py    finding the workspace, and capturing the patch
  harness.py      reading a manifest, installing, and running a harness
  lanes.py        building a lane's directory from the downloaded tasks
  config.py       suss.toml
  replay.py       ReplayAgent, the regrading agent
  compare.py      suss compare
  cli.py          the suss command
adapters/
  claude-code/
  copilot-cli/
  copilot-cli-hve/
tests/
docs/specs/
```
