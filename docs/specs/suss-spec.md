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
| Harbor | 0.25.0, exactly | Runs each task in a sandbox, runs each arm's agent in it, and grades the result with the task's own verifier. Suss's agents and verifiers subclass Harbor's classes, so upgrading Harbor is a change of its own, and it is taken only when `uv run pytest`, `SUSS_MODAL_TESTS=1 uv run pytest tests/test_modal_sandbox.py`, a `rebench` check and a `senior` check, both on Modal, pass on the new version, and each check gives the reward it gave on the old one. |
| Docker | Docker Desktop 4.86.0 or later on macOS. On Linux and Windows, a Docker engine whose kernel has `CONFIG_NFT_FIB_INET`. | The sandboxes, with `environment = "docker"` in `suss.toml`. Without `CONFIG_NFT_FIB_INET`, Harbor refuses every task, because the agent phase has no network. |
| Modal | 1.5.1 or later, and the account Harbor's `modal` environment uses. `uv sync --extra modal` installs its client into suss's environment, through Harbor's own `modal` extra at the Harbor version above. A plain `uv sync` leaves it out. | The sandboxes, when `environment` is `modal`. Versions before 1.5.1 cannot take a network policy per phase. |
| SWE-rebench | `ibragim-badertdinov/swe-rebench-07-2026` from Harbor Hub, as `harbor download` writes it | The `rebench` lane's tasks. |
| Senior SWE-Bench | `snorkel-ai/senior-swe-bench-v2026.06` from Harbor Hub, as `harbor download` writes it | The `senior` lane's tasks. |
| Python | 3.12 | Suss itself, whose agents and verifier Harbor imports. |
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
| **Harness** | What is being measured: a coding agent, with whatever plugins and settings it runs with. |
| **Agent** | The Harbor agent class an arm runs on: one of Harbor's own, with suss's mixin applied. |
| **Arm** | An agent and what it runs with: a model, and kwargs such as a version and a reasoning effort. |
| **Baseline** | The arm another arm is measured against, named by that arm's `baseline` in `suss.toml`. Any arm can be one. |
| **Trial** | Harbor's unit of work: one attempt at one task by one arm, in a fresh sandbox. |
| **Base** | The commit the workspace's `HEAD` names when the trial starts. |
| **Starting tree** | What the workspace holds when the trial starts: the base, with every change the image left uncommitted, staged or not, and every untracked file git does not ignore. |

### What a trial does

Every arm runs on a Harbor agent class with `suss.agents.SussMixin` applied
before it, such as `suss.agents:SussCopilotCli`. Harbor runs it once per trial,
in two phases. The setup phase has network access. The agent phase reaches only
the hosts the agent class lists in `ALLOWED_HOSTS`, and those its task lists in
`[agent] allowed_hosts`.

In the setup phase, suss:

1. **finds the workspace, and records the base and the starting tree.** The
   workspace is the directory the sandbox starts in, which is the image's
   `WORKDIR`, such as `/gleam`. It must be the top level of a git repository,
   or the trial fails naming it. The base is `git rev-parse HEAD`. Recording the
   starting tree leaves the index, `HEAD`, the refs and `git status` as the
   image left them.
2. **lets the agent install itself**, as its Harbor class does.

In the agent phase, suss:

3. **runs the agent** on the task's instruction, as its Harbor class does. Every
   command the agent runs carries `SUSS_START_TOKEN`, set to a new token;
4. **stops every process carrying that token**, however the run ended;
5. **captures the patch**, the whole change from the starting tree;
6. **writes the trial record**, `suss.json`.

The trial's result comes from Harbor's separate verifier, a fresh sandbox
Harbor starts in the same agent job once the agent's sandbox has stopped. On
`senior`, suss's judge also scores the patch there.

### Capturing the patch

```bash
git add -A
git diff --cached --binary <starting tree> > /logs/agent/model.patch
cp /logs/agent/model.patch /logs/artifacts/model.patch
```

The diff runs from the starting tree, so it holds the whole change the agent
made, whether it committed it or not, and none of the changes the image already
had. The verifier's sandbox starts from the same image, so the patch applies
there with `git apply`. A file of the starting tree that the agent made git ignore, but
left in place, stays out of the patch. The copy in `/logs/artifacts/` is the one
Harbor carries to a verifier in a sandbox of its own, and suss writes it however
the run ends.

### Grading

Harbor grades each trial in its separate verifier: a fresh sandbox built from
the task's own `Dockerfile`, which it starts in the same agent job once the
agent's sandbox has stopped. Each task's copy in the lane sets
`[verifier] environment_mode = "separate"`. That sandbox holds nothing of the
agent's but `/logs/artifacts/`. Suss's verifier applies the `model.patch` there
in the workspace, found as the trial found it, puts every test file back as the
image held it, and runs the task's own `test.sh` there, unchanged. That reward
is the trial's result.

| Lane | `--verifier` |
|---|---|
| `rebench` | `suss.replay:ReplayVerifier` |
| `senior` | `suss.judge:JudgeVerifier`, which grades as `ReplayVerifier` does and judges the patch first |

It applies the patch with:

```bash
git apply --verbose model.patch
```

Git applies all of the patch or none of it. The patch runs from the starting
tree, which the verifier's sandbox also starts from, so a patch suss captured
applies there.

A harness cannot change its result by changing the sandbox it worked in, because
the grading happens in another.

The verifier reads the sandbox's starting tree before applying the patch. After
applying it, it restores from that tree each test file the patch edited,
deleted or renamed, and removes each test file the patch added. So `test.sh`
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

The verifier writes `replay.json` in the trial's `verifier/`. It runs no test,
and fails the trial with `ReplayError`, when the sandbox holds no
`/logs/artifacts/model.patch`, or one whose bytes differ from the agent trial's
`agent/model.patch`, and writes:

```json
{ "patch": null }
```

An empty patch it grades as the agent's unchanged workspace, and writes the
same. It fails the trial with no test run when git cannot read the starting
tree, list what changed from it, or put a test file back.

Otherwise it writes the patch's path, its size and sha256, as `patch_bytes`
and `sha256`, the exit code of applying it, as `apply_rc`, what applying it
printed, as `apply_out`, and the test files it put back, as `tests_reset`.
Both lists are empty when the patch changed no test file:

```json
"tests_reset": {
  "restored": ["tests/features/migrations/Migrator.sqlite.test.ts"],
  "removed": []
}
```

A task can have Harbor fill a variable from Harbor's own environment, in its
`[verifier.env]` or `[environment.env]`, as each Senior SWE-Bench task's
verifier takes its keys:

```toml
[verifier.env]
ANTHROPIC_API_KEY = "${ANTHROPIC_API_KEY:-}"
SSB_OVERRIDE_ALL_JUDGE_MODEL = "${SSB_OVERRIDE_ALL_JUDGE_MODEL:-}"
```

Suss runs the agent job's Harbor with each variable the lane's tasks name this
way in `[verifier.env]` alone that the `.env` beside `suss.toml` sets, with its
value there, since Harbor gives `[verifier.env]` to the command that runs
`test.sh` and to no sandbox. It never passes one as an argument. No job's
Harbor takes these variables from suss's own environment. So a variable `.env`
leaves out or leaves empty takes the task's default, and the agent phase gets one only when the arm's agent class gives it to its commands.
`harbor run` loads a `.env.local` in the directory it runs in, so a run exits
with an error before it builds any lane or runs any job when one is beside
`suss.toml`.

Every `senior` task has Senior SWE-Bench's verifier, which runs LLM judges and,
on a design task, a validation agent. They call their models with
`ANTHROPIC_API_KEY`, `OPENAI_API_KEY` or `PORTKEY_API_KEY`, and suss gives the
job none of the three, whatever `.env` sets. So they never run, and suss's own
judge scores the patch in their place. The settings that would choose their
models, and the validation agent's harness, still reach the verifier, and
`grader` records them:

| Grades with | In `grader` as | Chosen by | Default |
|---|---|---|---|
| the rubric and taste judges' model | `judge_model` | `SSB_OVERRIDE_ALL_JUDGE_MODEL` | `anthropic/claude-sonnet-4-6` |
| the patch classifier's model | `classifier_model` | `SSB_OVERRIDE_CLASSIFIER_MODEL` | `anthropic/claude-haiku-4-5-20251001` |
| the validation agent's harness | `validation_harness` | `SSB_OVERRIDE_VA_HARNESS`, else `FORCE_VA_HARNESS` | `miniswe` |
| the validation agent's model | `validation_model` | `SSB_OVERRIDE_VA_MODEL`, else `FORCE_VA_MODEL` | `anthropic/claude-sonnet-4-6` |

### Judging

On `senior`, `JudgeVerifier` scores each trial's patch with senior's rubric and
taste judges, run through Copilot CLI on a Copilot subscription, with no API
key. In the verifier's sandbox, after the whole patch is applied, test files
included, and before the test files are put back, it runs the judges. Then it
grades as `ReplayVerifier` does, so the test reward is the same whatever the
judges do.

Copilot CLI runs there read-only. It is offered `view`, `glob`, `grep` and `rg`
alone, and denied `shell` and `write`. A session that changes the workspace
fails, and the change is undone. When it cannot be undone, the trial fails
with no test run.

The prompts, the rubric and the answer schemas are the task's own: the task's
`tests/run_judge.py` builds them from its `tests/`, on the host. Suss keeps no
copy. An answer that is not valid JSON, or fails its schema, is retried once in
the same session.

The judge reads `COPILOT_GITHUB_TOKEN` from the `.env` beside `suss.toml`, and
gives it to each session's command alone. Harbor's environment, `test.sh` and
every argument go without it. Before `test.sh` runs, every file under the
judge's directories in the sandbox that holds the token is deleted, and
`judge.json` has it replaced with `[redacted]`.

Every session of a job draws on one credit budget, kept in
`judge-credits.jsonl` in the job's directory on the host. A session holds a
reservation while it runs, and the budget must hold what was spent, what
running sessions hold and its own reservation. A session the budget cannot hold
is `skipped:budget`.

The judge stops after `judge_seconds`, its install included, and has 300
seconds more to put the workspace back. Each `senior` task's copy in the lane
adds 2100 seconds, the default `judge_seconds` and those 300, to its
`[verifier] timeout_sec`, or to Harbor's default of 600 where it sets none, so
its tests keep the time the task gives them. `JudgeVerifier` fails a trial
before it applies anything when the task's verifier timeout is no more than
`judge_seconds` and those 300 together. A judge that Harbor stops at its verifier timeout records
`failed:cancelled` for each judge it had not finished, and runs no test.

`suss run` gives it the lane's judge from `suss.toml` as `--verifier-kwarg`:

| Key | Default | |
|---|---|---|
| `credit_budget` | required | the AI credits every judge session of the job may spend together |
| `model` | `gemini-4-argon` | the judges' model, of neither family the arms use, GPT or Claude |
| `effort` | `high` | its reasoning effort, empty for a model that takes none |

`suss run` passes none of the others:

| Key | Default | |
|---|---|---|
| `classifier_model` | `claude-haiku-4.5` | the model of senior's patch classifier, which the taste judge needs |
| `copilot_version` | `1.0.105` | the Copilot CLI release it installs, as Harbor's `copilot-cli` agent installs it |

| `dotenv` | `.env` | the file that sets `COPILOT_GITHUB_TOKEN` |
| `session_seconds` | 1200 | when a session is stopped |
| `judge_seconds` | 1800 | when the judge is stopped |

It writes `judge.json` in the trial's `verifier/`, and each session's logs in
`verifier/judge/`. Beside each session's own record, `judge.json` holds the
judge, a status per judge, the scores in the shape senior's `reward.json` uses,
and the AI credits the trial's sessions spent:

```json
{
  "judge": {
    "client": "copilot-cli",
    "version": "1.0.105",
    "requested_version": "1.0.105",
    "model": "gemini-4-argon",
    "effort": "high",
    "classifier_model": "claude-haiku-4.5"
  },
  "rubric_status": "skipped:no_criteria",
  "taste_status": "ok",
  "scores": {
    "taste_patch_bloat": 1.12,
    "taste_practice_alignment": 4.0,
    "taste_relative_taste": 3.0,
    "rubric_judge_ok": 0,
    "taste_judge_ok": 1
  },
  "credits": 28.4
}
```

- `version` is what the installed Copilot CLI reports.
- A status is `ok`, `skipped:<why>` or `failed:<why>`, as `skipped:no_criteria`
  for a task with no rubric criteria, or `failed:timeout`.
- A judge that is not `ok` records no score of its own, and its flag is 0. The
  patch bloat comes from senior's code, not a judge, so it can be recorded
  beside a taste judge that failed.

### The trial record

Suss writes these files to `/logs/agent/` in the sandbox. Harbor keeps that
directory as `agent/` in the trial's directory under `results/`, and the copy of
`model.patch` in `/logs/artifacts/` as `artifacts/logs/artifacts/model.patch`.
The agent's own files go there too, such as Copilot CLI's stream,
`copilot-cli.jsonl`, and Harbor's `trajectory.json`.

| File | Holds |
|---|---|
| `suss.json` | the trial record, below |
| `model.patch` | the patch, the whole change from the starting tree |

```json
{
  "arm": "copilot-gpt",
  "agent": { "class": "suss.agents:SussCopilotCli", "name": "copilot-cli", "version": "1.0.105" },
  "model": "gpt-6.1-sol",
  "kwargs": { "version": "1.0.105", "reasoning_effort": "high" },
  "grader": { "dataset": "swe-rebench-07-2026" },
  "base": "5568b2e0a686cc4d838bddc095b831379143e61b",
  "status": "ok",
  "run_seconds": 1290.4,
  "patch_bytes": 4182,
  "usage": { "ai_credits": 18.4395, "reported_models": ["gpt-6.1-sol"], "reported_efforts": ["high"] }
}
```

- `agent` names the arm's agent class, Harbor's name for it, and the version
  the installed agent reports, which `kwargs` may ask for and not get. `model`
  and `kwargs` are what the arm passed Harbor.
- `grader` names the dataset the trial's task came from. Suss fails a trial on
  a lane with no dataset before its sandbox starts.
- On `senior`, `grader` also holds the litellm requirement the lane's
  `test.sh` installs, and the models and validation harness in the table
  above. Each is what the verifier is given: the value `.env` sets, where the
  lane's tasks read that setting, or else the default. A setting `.env` leaves
  empty takes the default. A trial is judged by the judge its `judge.json`
  names, whatever `judge_model` says.

  ```json
  {
    "grader": {
      "dataset": "senior-swe-bench-v2026.06",
      "litellm": "litellm>=1.0,<1.106",
      "judge_model": "anthropic/claude-sonnet-4-6",
      "classifier_model": "anthropic/claude-haiku-4-5-20251001",
      "validation_harness": "miniswe",
      "validation_model": "anthropic/claude-sonnet-4-6"
    }
  }
  ```
- `base` is the commit. `model.patch` applies to the starting tree, which is
  the base only when the image left no change in the workspace.
- `status` is `ok`, `harness-failed` or `timed-out`. A trial runs until its
  task's agent timeout, and is `timed-out` when Harbor stops it there. It is
  `harness-failed` when the agent raises, as Harbor's agents do when their
  coding agent exits non-zero. Either way, suss stops the agent's processes,
  then captures the patch and writes the record, and the verifier grades it.
- A trial whose patch cannot be written fails with the capture's error, and is
  not graded, whatever the agent did.
- `usage` is what the agent reports it spent, or `null` for an agent whose
  reports suss does not read.

## 4. Agents

An arm names its agent class in `suss.toml` as `module:Class`. Suss runs only a
class that applies `suss.agents.SussMixin` before a Harbor agent class, since
only such a class leaves the patch its trial is graded by. Harbor's own agent
then does everything else: it installs the coding agent, runs it, parses its
output into `trajectory.json`, and records its tokens in `result.json`.

### The mixin

`SussMixin` adds to a Harbor agent class what a trial needs:

- It records the base and the starting tree before the agent installs.
- It sets `SUSS_START_TOKEN` on every command the agent runs in the agent phase,
  and when the run ends it kills every process in the sandbox whose environment
  holds it, as the agent's user, by reading `/proc`. That finds each one
  whatever session or process group it moved to.
- It captures the patch and writes `suss.json` however the run ends.
- It takes `arm`, `lane` and `suss_config` as `--ak`, and reads the arm's
  settings and the `.env` from that `suss.toml` when Harbor constructs it, so a
  bad config fails before a sandbox starts.
- It gives the agent nothing from Harbor's own environment, so no variable of
  the machine running suss sets one of the agent's flags.

A class names, as class attributes, the hosts its agent phase reaches in
`ALLOWED_HOSTS`, the arm kwargs it takes in `KWARGS`, those it needs in
`REQUIRED_KWARGS`, and whether it needs a model in `REQUIRES_MODEL`.

### Agents in this repository

| Agent | Model and kwargs | Hosts | What it does |
|---|---|---|---|
| `suss.agents:SussCopilotCli` | a model, required; `version`, required; `reasoning_effort` | `api.github.com`, `api.enterprise.githubcopilot.com` | Harbor's `copilot-cli` agent. Suss installs Copilot CLI at `version` from npm's registry, as the binary in `@github/copilot-<platform>`, which needs no Node, and Harbor's agent runs `copilot --prompt=<instruction> --yolo --deny-tool web_fetch --deny-tool web_search --model=<model> --effort <reasoning_effort> --output-format=json`, denying the web tools because the agent phase's network is an allowlist. Suss adds `--disable-builtin-mcps`, `--no-auto-update`, `--disable-client-telemetry`, `--no-ask-user` and `--excluded-tools web_fetch web_search`. The model is one of Copilot's own, by the name `--model` takes, and Harbor constructs no agent for one that names a provider, as `anthropic/<model>` does, which Harbor would run against that provider's API. Copilot CLI's commands in the agent phase get `COPILOT_GITHUB_TOKEN` from `.env` and no other token: never `GH_TOKEN` or `GITHUB_TOKEN`, and none from Harbor's environment. Harbor constructs it only when `.env` sets that token. |
| `suss.agents:SussCopilotCliHve` | as `SussCopilotCli`, and `hve_core_commit`, required | as `SussCopilotCli` | `SussCopilotCli` as Microsoft's hve-core RPI Agent. Its install also downloads hve-core at `hve_core_commit`, a full 40-digit commit, from `https://codeload.github.com/microsoft/hve-core/tar.gz/<commit>` to `$HOME/.local/share/hve-core/<commit>/`, and fails when the archive holds no `plugin.json`, or that commit's `rpi-agent.agent.md` has no "Full Auto" handoff prompt. It adds `--plugin-dir` with that directory and `--agent hve-core:rpi-agent`, and gives as the prompt the Full Auto prompt, a blank line, then the instruction. `model` and `reasoning_effort` apply to the RPI Agent, and its subagents' own files name theirs. RPI keeps its state in the workspace's `.copilot-tracking/`, which suss makes a link to `/logs/agent/copilot-tracking/` and adds to `.git/info/exclude`, so it is kept as it is written and stays out of the patch. The trial is `harness-failed`, with Copilot CLI never started, when the workspace already has `.copilot-tracking`, or its own ignore rules keep git from ignoring it. |
| `suss.agents:SussClaudeCode` | a model, required; `version`, required; `reasoning_effort` | `api.anthropic.com` | Harbor's `claude-code` agent, on the owner's Claude subscription. It installs Claude Code at `version` with `https://downloads.claude.ai/claude-code-releases/bootstrap.sh`, and runs `claude --verbose --output-format=stream-json --effort <reasoning_effort> --disallowedTools WebSearch,WebFetch --permission-mode=bypassPermissions --print` with the instruction on its standard input and `ANTHROPIC_MODEL` set to the model, disallowing the web tools because the agent phase's network is an allowlist. The model is Anthropic's own, by the name Claude Code's `--model` takes, such as `claude-sonnet-5-5`, and Harbor constructs no agent for one that names a provider. Claude Code's commands in the agent phase get `CLAUDE_CODE_OAUTH_TOKEN` from `.env`, the token `claude setup-token` makes, and no other credential: never `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN` or `ANTHROPIC_BASE_URL`, and none from Harbor's environment. Harbor constructs it only when `.env` sets that token. |

The two Copilot CLI agents record its usage from its stream, which Harbor's agent does not
parse: in `suss.json`'s `usage`, and in Harbor's `agent_result.metadata` for
the trial. `ai_credits` is the largest `totalNanoAiu` of its
`session.usage_checkpoint` events over 1e9. `reported_models` are the models
its `model.call_start` events name, and `reported_efforts` the efforts its
checkpoints name, so a run on a model or effort other than the one asked for
shows there. Where Harbor's parse found no token counts, as for a GPT model,
`agent_result` takes the last checkpoint's input, cache-read and output tokens.

## 5. Lanes

Suss has two lanes, `rebench` and `senior`. Every task gives the agent a
repository at the commit before a real change, and an instruction about that
change. The task's verifier grades it, and a trial resolves when its reward is
1. Each lane reports its own measures, and nothing compares one lane with
another. `rebench` reports the share of trials that resolve, and each arm's
difference from its baseline. `senior` reports the same, over the investigate
tasks unless a validation agent graded the design tasks, and the judges' rubric
and taste scores.

Each lane is a directory of copies of its tasks, built from the download that
its key in `suss.toml` names. Each task's image is built from the `Dockerfile`
in its `environment/`, and the build downloads from the network. Its repository
is in the image's `WORKDIR`.

### `rebench`

108 of the 111 tasks of SWE-rebench's July 2026 snapshot, from 64 repositories
in Go (21), Java (18), Python (20), Rust (25) and TypeScript (24). It leaves out
`langchain4j__langchain4j-5315`, `langchain4j__langchain4j-5367` and
`openrewrite__rewrite-7784`, whose builds need Maven or Gradle artifacts that no
longer exist, so no fix can pass their tests. A download that lacks any of the
three still builds the lane. The tasks are made from pull requests merged
between 15 May and 15 June 2026. Each instruction is the issue the pull request
closed, and an `Interface` section naming any interface it adds.

The verifier runs the task's hidden tests. A trial resolves when the tests the
real change made pass now pass, and the tests that passed before still pass.

Each copy's `task.toml` adds `[verifier] environment_mode = "separate"`. A task
whose `[verifier]` sets another `environment_mode` or an `environment` of its
own makes `suss run` exit with an error naming it before it builds any lane or
runs any job. Harbor's `oracle` agent leaves no patch for the separate verifier
to apply, so an oracle check of the lane runs on the download, whose verifier
shares the agent's sandbox:

```bash
harbor run -p swe-rebench-07-2026 -e modal -a oracle
```

### `senior`

All 50 tasks of Snorkel's Senior SWE-Bench snapshot `senior-swe-bench-v2026.06`,
from 12 repositories:

| Language | Tasks | With no tests |
|---|---|---|
| Elixir | 10 | 2 |
| Go | 8 | 0 |
| Python | 18 | 4 |
| Rust | 6 | 0 |
| TypeScript | 8 | 0 |

The verifier runs the task's tests, a validation agent on the 25 tasks whose
`metadata.segment` is `design`, and LLM judges. The reward is 1 when the tests
pass and, on a design task, the validation agent passes the change too. The
judges' scores never change it. The validation agent and the judges need an API
key, and suss gives the verifier none, so it writes an empty reward for every
design task, which `suss compare` counts as invalid, and suss's own judge scores
every patch. Six design tasks have no tests, so the
validation agent alone sets their reward: `electric-feat-sync-service-start`,
`harbor-add-windows-tasks-support`, `plausible-feat-shared-dashboard-deeplink`,
`posthog-feat-prompt-versioning`, `posthog-feat-schema-ingestion-block` and
`prefect-feat-dbt-per-node-hardening`.

The agent timeout is 7200 seconds in 46 tasks and 14400 in the other four. The
`Dockerfile`s of the 6 `gitea-*`, 2 `teleport-*` and 4 `turborepo-*` tasks
download amd64 Go or Rust toolchains by name, whatever the host's architecture.

Each copy's `task.toml` adds `[verifier] environment_mode = "separate"` and the
judge's 2100 seconds to its `[verifier] timeout_sec`. A task whose `[verifier]`
sets another `environment_mode`, an `environment` of its own, or a `timeout_sec`
that is not a positive number makes `suss run` exit with an error naming it
before it builds any lane or runs any job.

Each copy's `tests/test.sh` installs `litellm>=1.0,<1.106` where the task's
installs `litellm>=1.0,<2.0`.
Every litellm from 1.106.0, on PyPI since 18 October 2026, raises
`No module named 'mcp'` on every judge call. The verifier still writes a reward
from the tests, so without the pin no trial's judges would score it and nothing
would fail. A `test.sh` with no line that is exactly `litellm>=1.0,<2.0`, or
with more than one, or in a `tests` directory that is a link, makes `suss run`
exit with an error naming it before it builds any lane or runs any job.

### Network

In the agent phase, a trial reaches the hosts in its agent class's
`ALLOWED_HOSTS` and the hosts in its own task's `[agent] allowed_hosts`, and no
other host. A
`rebench` task names no hosts of its own, so its trials reach the agent's
alone. Every `senior`
task names 32, such as `pypi.org` and `registry.npmjs.org`, and 19 of them name
a few more for their repository.

Suss passes each host in the agent class's `ALLOWED_HOSTS` to Harbor as
`--allow-agent-host`, and nothing else. Harbor adds them, trial by trial, to the
hosts of that trial's own task. A host that one task names stays closed to
every other task's trials in the same job, and a host the agent and the task
both name is opened once. A task allowing `pypi.org`
and `clickhouse.com`, under an agent allowing `api.anthropic.com` and
`pypi.org`, has an agent phase that reaches `pypi.org`, `clickhouse.com` and
`api.anthropic.com`.

Harbor ignores `--allow-agent-host` when the agent's network is public, which
it is when a task's `[agent]` sets no `network_mode` and the task gives the
network to the whole trial, with `[environment] network_mode = "public"` or with
`allow_internet = true`. Every `rebench` task does. So the copy's `task.toml`
adds `[agent] network_mode = "no-network"` where `[agent]` sets no
`network_mode`. A `task.toml` whose `[agent]` sets `no-network` or `allowlist`,
as every `senior` task's does, is copied unchanged. One whose `[agent]` sets any
other `network_mode`, or sets `allowed_hosts` with no `network_mode`, makes
`suss run` exit with an error naming it before it builds any lane or runs any
job. The verifier keeps the task's own network, which it uses to download a
Python on an image that has none. A `senior` task's verifier has a public
network, through which the judge reaches Copilot.

## 6. Commands

### `suss run`

```bash
suss run <arm> <lane>... [--check] [-- <harbor args>]
suss run copilot-gpt rebench
```

It reads the arm from `suss.toml` in the current directory. For each lane, it
builds `lanes/<lane>/`, a directory of copies of the lane's task directories
from the directory its key names: `swe_rebench` for `rebench`, and
`senior_swe_bench` for `senior`. Each copy has
`[agent] network_mode = "no-network"` added to its `task.toml` where its
`[agent]` sets no `network_mode`, and the separate verifier its lane describes.
On `senior` it also has `litellm>=1.0,<1.106` in place of `litellm>=1.0,<2.0`
in its `tests/test.sh`. A build removes what an
earlier build of the lane left. Harbor builds each task's image from its
`Dockerfile` when its trial starts.

Then, K times, it runs an agent job, which grades its own trials:

```bash
<env>/bin/harbor run -p lanes/rebench -e docker -n 16 -k 1 \
  -a suss.agents:SussCopilotCli -m gpt-6.1-sol \
  --ak version=1.0.105 --ak reasoning_effort=high \
  --ak arm=copilot-gpt --ak suss_config=suss.toml --ak lane=rebench \
  --allow-agent-host api.github.com --allow-agent-host api.enterprise.githubcopilot.com \
  --verifier suss.replay:ReplayVerifier \
  --jobs-dir results/rebench/copilot-gpt --job-name <stamp>-1
```

On `senior` it judges them too, with the lane's judge from `suss.toml`:

```bash
<env>/bin/harbor run -y -p lanes/senior -e docker -n 16 -k 1 \
  -a suss.agents:SussCopilotCli -m gpt-6.1-sol \
  --ak version=1.0.105 --ak reasoning_effort=high \
  --ak arm=copilot-gpt --ak suss_config=suss.toml --ak lane=senior \
  --allow-agent-host api.github.com --allow-agent-host api.enterprise.githubcopilot.com \
  --verifier suss.judge:JudgeVerifier --verifier-kwarg credit_budget=2000 \
  --jobs-dir results/senior/copilot-gpt --job-name <stamp>-1
```

`<stamp>` is the time `suss run` started, in UTC, as `20261031T142500Z`. Every
job of the run shares it, and `-1` to `-K` tell its agent jobs apart. `<env>`
is the Python environment suss is installed in, an absolute path suss forms
itself. Its `harbor` is the one that can import `suss.agents` and suss's
verifiers, which a `harbor` elsewhere on `PATH` may not be.

When a task in the lane has Harbor fill a variable from Harbor's environment,
as every `senior` task does, every job of the lane takes `-y`, right after
`run`. Harbor asks before such a job whether its tasks may load those
variables, and without a terminal it reads no answer and exits 1 with
`Aborted.`. Answering yes is safe because Harbor holds only the values suss
gives it: those `.env` sets for the verifier alone. `-y` also agrees, without asking, to share an uploaded
job with an organization you are not a member of.

A run of `senior` exits with an error before it builds any lane or runs any
job, with `DRY_RUN=1` and `--check` too, when `suss.toml` has no
`[lanes.senior.judge]`, or `.env` sets no `COPILOT_GITHUB_TOKEN`. A token set to
an empty value counts as missing:

```
$ suss run copilot-gpt senior
error: /home/me/evals/.env sets no COPILOT_GITHUB_TOKEN, which suss judges the
trials of senior with
```

Options after `--` go to every agent job. Passing any of these, under any name
or spelling Harbor accepts, is an error that names it and says why, and runs
nothing:

- An option suss sets on the agent job, even with suss's own value: `-p`,
  `-e`, `-n`, `-k`, `-a`, `--ak`, `--allow-agent-host`, `--verifier`,
  `--verifier-kwarg`, `--jobs-dir`, `--job-name` and `-y`. Also `-m`, which
  the arm's `model` sets, and `--disable-verification` and its opposite
  `--enable-verification`, since the agent job grades its own trials.
- `--env-file`, which would load the file into the agent job's environment,
  `--ae`, which would reach every command the agent runs, and `--share`
  where the jobs take `-y`.
- An option that changes how the agent reaches its model, or what the verifier
  reaches: `--effort`, which the arm's `kwargs` set, `--base-url`,
  `--api-key-name`, `--api-format`, `--sm` and `--allow-verifier-host`.
- An option that changes the tasks or the agent the agent job runs, or keeps it
  from leaving a trial on this machine for its verifier: `-c`, `-d`, `-t`, `-i`,
  `-x`, `-l`, `--registry-url`, `--registry-path`, `--repo`, `--task-git-url`,
  `--task-git-commit`, `--agent-import-path`, `--verifier-import-path`,
  `--environment-import-path`,
  `--user-agent`, `--user-model`, `--uk`, `--user-persona-path`,
  `--user-prompt-template-path`, `--bridge`, `--bridge-prompt-path`, `--bk`,
  `--install-only`, `--launch`, `--dry-run`, `--diff` and `--print-config`.
- A positional argument, which `harbor run` does not take.

```
$ suss run copilot-gpt rebench -- -k 2
error: -k/--n-attempts can't be passed after --: suss runs one trial per agent
job, and k in suss.toml sets how many jobs
```

`DRY_RUN=1` prints the `harbor run` commands instead of running them. Before an
agent job whose Harbor holds values from `.env`, it prints their names, and
never a value:

```
# from .env: SSB_OVERRIDE_ALL_JUDGE_MODEL
/home/me/evals/.venv/bin/harbor run -y -p lanes/senior -e docker -n 16 -k 1 -a suss.agents:SussCopilotCli -m gpt-6.1-sol --ak version=1.0.105 --ak reasoning_effort=high --ak arm=copilot-gpt --ak suss_config=suss.toml --ak lane=senior --allow-agent-host api.github.com --allow-agent-host api.enterprise.githubcopilot.com --verifier suss.judge:JudgeVerifier --verifier-kwarg credit_budget=2000 --jobs-dir results/senior/copilot-gpt --job-name 20261031T142500Z-1
```

Each agent job runs whatever the one before it did and whatever Harbor's exit
code was, and `suss run` exits 0. The one exception is an agent job that leaves
no trial in its job directory, as when Harbor cannot start at all: `suss run`
then prints the job's name and Harbor's exit code, runs nothing more, and exits
non-zero.

It gives Harbor `results/<lane>/<arm>/` as its jobs directory, so each agent job
is written there. Results stay there across runs, so an arm
run later is compared against a baseline graded earlier, without running the
baseline again.

`--check` tests that an arm works under suss, for the cost of one trial per
lane. It builds `lanes/<lane>-check/` with only the lane's first task by name,
and gives that to its jobs in place of `lanes/<lane>/`, which a full run of the
lane may be using. It runs one agent job, whatever `k` is, and writes them to `checks/<lane>/<arm>/` instead of `results/`:

```bash
suss run copilot-gpt rebench --check
```

`suss.toml` names where the tasks are, where the sandboxes run, and the arms:

```toml
swe_rebench = "../tasks/swe-rebench-07-2026"
senior_swe_bench = "../tasks/senior/senior-swe-bench-v2026.06"
environment = "docker"
k = 1
concurrency = 16

[arms.copilot-sonnet]
agent = "suss.agents:SussCopilotCli"
model = "claude-sonnet-5.5"
kwargs = { version = "1.0.105", reasoning_effort = "high" }

[arms.copilot-gpt]
agent = "suss.agents:SussCopilotCli"
model = "gpt-6.1-sol"
baseline = "copilot-sonnet"
kwargs = { version = "1.0.105", reasoning_effort = "high" }

[arms.hve-sol]
agent = "suss.agents:SussCopilotCliHve"
model = "gpt-6.1-sol"
baseline = "copilot-gpt"
kwargs = { version = "1.0.105", reasoning_effort = "high", hve_core_commit = "55352bb8e9f0622ff40f5c6afa80363adaf6b07f" }

[lanes.senior.judge]
credit_budget = 2000
```

- `swe_rebench` and `senior_swe_bench` name the directories of tasks that
  `harbor download` writes, relative to the directory holding `suss.toml`.
  `swe_rebench` is `<dir>/swe-rebench-07-2026` after
  `harbor download ibragim-badertdinov/swe-rebench-07-2026 -o <dir>`, and
  `senior_swe_bench` is `<dir>/senior-swe-bench-v2026.06` after
  `harbor download snorkel-ai/senior-swe-bench-v2026.06 -o <dir>`. Each is
  required only by a run of its lane, `rebench` or `senior`. A run of a lane
  whose key is missing exits with an error naming it before it builds any lane
  or runs any job.
- `agent` names the arm's agent class, as `module:Class`. `model` is passed as
  `-m`, and each of `kwargs` as `--ak`, of its own type. A class that is not
  built on `SussMixin`, a kwarg it does not take, or a model or kwarg it needs
  and the arm leaves out, makes `suss run` exit with an error before it builds
  any lane or runs any job. Harbor refuses such a kwarg only once the agent job
  has started, and takes any kwarg for an agent class that declares no options:

  ```
  $ suss run copilot-gpt rebench
  error: /home/me/evals/suss.toml: [arms.copilot-gpt] sets kwargs max_ai_credits,
  which suss.agents:SussCopilotCli does not take; it takes version,
  reasoning_effort
  ```

  An arm that sets `harness` or `params` makes `suss run` exit with an error
  saying to use `agent`, `model` and `kwargs`.
- `environment` is passed to `harbor run` as `-e`: `docker` runs the sandboxes
  on this machine, and `modal` runs them on Modal. With `modal`, when Modal's
  client is not installed in suss's environment, `suss run` exits with an error
  before it builds a lane or runs any job, with `DRY_RUN=1` and `--check` too:

  ```
  $ suss run copilot-gpt rebench
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
  `memory_mb` is, and Docker's is rounded down. A task that gives `memory`, as
  every `senior` task does, counts as Harbor reads it: `"8G"` is 8192 MiB. When
  either does not fit, `suss run` exits with an error before it builds any lane
  or runs any job, and does so under `DRY_RUN=1` too. A task that names no
  `cpus`, or neither `memory_mb` nor a `memory` Harbor reads, is an error that
  names it. A `--check` counts only its one task, and one trial at a time.

  ```
  $ suss run copilot-gpt rebench
  error: concurrency = 16 needs 16 CPUs and 65536 MiB for the tasks in rebench,
  but Docker has 10 CPUs and 7934 MiB; set concurrency to 1 or less, or give
  Docker more
  ```

  When a single trial does not fit, the error says so and offers no
  `concurrency`.
- `[lanes.senior.judge]` gives the judge a `senior` run is judged with:
  `credit_budget`, which it requires, and `model` and `effort`, which take the
  judge's defaults where it leaves them out. They are the `--verifier-kwarg`s
  the agent job gives `JudgeVerifier`. A budget that is not a positive, finite
  number, a model or effort that is not a string, or any other key, makes
  `suss run` exit with an error before it builds any lane or runs any job.

  ```toml
  [lanes.senior.judge]
  credit_budget = 2000
  model = "gpt-6.1-sol"
  effort = "high"
  ```

  A `[lanes.<lane>]` table for a lane suss does not have, or one that sets
  anything else, but `judge` on `senior`, makes `suss run` exit with an error
  before it builds any lane or runs any job:

  ```
  $ suss run copilot-gpt rebench
  error: /home/me/evals/suss.toml: sets [lanes.rebnech], but suss has no lane
  named rebnech; its lanes are rebench, senior
  ```
- `max_cost_usd`, at the top or on the arm, makes `suss run` exit with an error
  before it builds any lane or runs any job, with `DRY_RUN=1` and `--check` too:

  ```
  $ suss run copilot-gpt rebench
  error: /home/me/evals/suss.toml: [arms.copilot-gpt] sets max_cost_usd, but suss
  does not read it
  ```

### `suss compare`

```bash
suss compare <lane> <arm> <arm>...
```

It reads every trial under `results/<lane>/<arm>/` for the arms it is given,
and each arm's `baseline` from `suss.toml` in the current directory. Trials
under `checks/` never count. It compares arms on one lane at a time, never one
lane with another.

A trial's result is the reward its own verifier gave it, in the agent trial's
`result.json`. Every other file this section reads is in the agent trial's
`verifier/`, where the verifier wrote it.

It compares the arms over the tasks every one of them has a valid trial of. It
prints two tables. The first gives each arm's trials and its resolved, failed
and timed-out shares. The second gives each arm's resolved difference from its
baseline, with an interval. After the tables, it names each trial whose verifier
recorded no test result, then each invalid trial, by arm, job and task:

```
These trials' verifiers recorded no test result, so their grading may have broken:
  claude-code  20261103T204423Z-2  marimo-team__marimo-9766

These trials' verifiers judged them invalid, so no table counts them:
  claude-code  20261103T204423Z-1  paperless-ngx-perf-workflow-queries
```

- Every valid trial counts as its verifier graded it, `harness-failed` and
  `timed-out` ones included.
- A trial is invalid when its verifier wrote an empty `verifier/reward.txt`,
  which Harbor records in the trial's `result.json` as an `exception_info` whose `exception_type` is
  `RewardFileEmptyError`. An invalid trial counts in no table, as if it
  had not run. The task's other trials, in every arm, still count. Where every
  trial of a task in one arm is invalid, that task counts in no arm. The list
  names the invalid trials of every task each arm has a trial of. An invalid
  trial is never also named as recording no test result.
- A verifier records a test result in the file its lane's verifier writes, as
  the table below says. It records none when that file is missing.

  | Lane | File | Records a test result when |
  |---|---|---|
  | `rebench` | `verifier/report.json` | its `parsed_counts` counts a test as `PASSED`, `FAILED`, `SKIPPED` or `ERROR` |
  | `senior` | `verifier/verifier_results.json` | its `total` is above 0, or is 0 with `all_pass` true and no `runner_errors`, as the verifier writes for a task that has no tests and grades by its validation agent alone |
- A valid trial whose verifier left no reward counts as unresolved, as when it
  timed out or crashed. A trial with no `suss.json`, which a sandbox
  that never started or a Harbor exception leaves, counts as `harness-failed`.
- `failed` is the share of trials whose status is `harness-failed`, and
  `timed out` the share whose status is `timed-out`.
- Each arm whose `baseline` is among the arms given gets a row in the second
  table. The bracketed range is a 95% interval from a bootstrap of 10,000
  resamples, each drawing tasks so that a task's trials stay together. A share
  is resolved trials over trials, so a task left with fewer valid trials weighs
  less in that arm. The same results always print the same interval.
- Shares print as whole percents, and every difference and interval end
  carries a sign.

On the `senior` lane, a third table follows the second. It gives each arm's mean
score from each judge, over the valid trials the shares count, and in
parentheses how many trials each mean is over. When a verifier wrote
`judge.json`, a last column names each arm's judge:

```
mean (trials)     rubric  practice alignment  relative taste  patch bloat  judge
claude-code    0.71 (34)           3.62 (47)       3.04 (47)    1.48 (47)  copilot-cli gemini-4-argon high
self-review    0.78 (33)           3.81 (46)       3.17 (46)    1.31 (46)  copilot-cli gemini-4-argon high
```

After the list of invalid trials, it names each counted trial whose judges
failed or were skipped, by arm, job and task, then the judges:

```
These trials' judges failed or were skipped, so the means leave out their scores:
  claude-code  20261103T204423Z-1  posthog-feat-approval-gating         taste
  self-review  20261103T204423Z-1  paperless-ngx-perf-workflow-queries  rubric, taste
```

- A trial's scores are its `verifier/judge.json` when it has one, and
  otherwise its `verifier_result.rewards`. Each column is a key of
  them:

  | Column | Key | Scale |
  |---|---|---|
  | `rubric` | `rubric_score` | the share of the task's rubric criteria the patch meets, 0 to 1 |
  | `practice alignment` | `taste_practice_alignment` | the median of five dimensions, each 1 to 5 |
  | `relative taste` | `taste_relative_taste` | the median of five dimensions, each 1 to 5, against the task's reference patch |
  | `patch bloat` | `taste_patch_bloat` | the patch's source lines over the reference patch's, so 1 is the same size |

- A score counts only when its judge's flag is 1: `rubric_judge_ok` for the
  rubric, and `taste_judge_ok` for the other three. In `judge.json`, a flag is 1
  exactly when that judge's status is `ok`. A trial whose judge failed, was
  skipped or never ran takes no part in that mean, whatever scores it holds. A
  task with no rubric criteria has no rubric score in any arm.
- A judge is named when its flag is not 1, except a rubric judge whose status is
  `skipped:no_criteria`, in `judge.json` or in the verifier's
  `verifier/judge_output.json` as `rubric_status`. A trial whose verifier left
  neither `judge.json` nor a reward is not named.
- A judge is named in the last column by the client, model and effort its
  `judge.json` gives, and senior's verifier by the grader's `judge_model`, as
  `senior verifier anthropic/claude-sonnet-4-6`.
- An arm whose trials more than one judge scored has no mean. Its row is `-`
  throughout, with `more than one` as its judge, and a line after the table
  names each judge and its trials:

  ```
  beta was judged by copilot-cli gemini-4-argon high on 2 trials and copilot-cli gpt-6.1-sol high on 1 trial, so it has no mean.
  ```
- Arms judged by different judges each keep their means, and a line after the
  table says they do not compare:

  ```
  The means do not compare across arms: base was judged by senior verifier anthropic/claude-sonnet-4-6, beta by copilot-cli gemini-4-argon high.
  ```
- Means print to two decimal places, a half rounded away from zero, and as `-`
  when no trial has the score.
- On every other lane, neither the third table nor the list prints, unless a
  verifier wrote `judge.json`.

It exits with an error, and prints nothing else, when:

- An arm it is given has no trials under `results/<lane>/<arm>/`, has only
  invalid ones, or is not in `suss.toml`:

  ```
  $ suss compare rebench claude-code self-review
  error: every trial of arm self-review under results/rebench/ is invalid: its
  verifier wrote an empty reward
  ```
- The arms' trials that have a `suss.json` do not all carry the same `grader`.
  A trial with no `suss.json` has no `grader` to compare, and still counts as
  `harness-failed`. Two graders are named by the fields they differ in:

  ```
  $ suss compare senior claude-code self-review
  error: trials carry different graders: judge_model "anthropic/claude-sonnet-4-6"
  in results/senior/claude-code/20261031T142500Z-1, judge_model "openai/gpt-5.5"
  in results/senior/self-review/20261103T090000Z-1
  ```
- Two trials of one task, in any of the arms, carry different `task_checksum`
  in their `result.json`, which is Harbor's hash of the task's files as the
  trial ran them:

  ```
  $ suss compare rebench claude-code self-review
  error: the trials of ArcadeData__arcadedb-4281 in
  results/rebench/claude-code/20261031T142500Z-1 and
  results/rebench/self-review/20261103T090000Z-1 ran different versions of the
  task, as their task_checksum differs
  ```
- A trial whose `suss.json` has `patch_bytes` above 0 has a verifier that
  applied no patch: its `replay.json` has `"patch": null`, or it has no
  `replay.json` and its `result.json` records an exception.
- A trial's verifier could not apply its patch: its `replay.json` names a
  patch, and its `apply_rc` is not 0.

## 7. The project

### Language

Python 3.12, managed with `uv`.

### Structure

```
src/suss/
  agents.py       SussMixin, and the agent classes arms run on
  sandbox.py      commands in the sandbox, and stopping what they started
  workspace.py    finding the workspace, and capturing the patch
  lanes.py        building a lane's directory from the downloaded tasks
  config.py       suss.toml
  replay.py       ReplayVerifier, which grades a trial in Harbor's separate verifier
  judge.py        JudgeVerifier, which judges a senior trial there, then grades it
  compare.py      suss compare
  cli.py          the suss command
tests/
docs/specs/
```
