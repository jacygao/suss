# SWE-drill Specification: Measuring and Comparing Coding Harnesses

**Version:** 0.1 (draft)

## 1. Purpose

**SWE-drill measures and compares how well coding harnesses do specific kinds of
coding work.** A harness is anything that takes a task's instruction and changes
a repository. Each kind of work is a lane: a set of benchmark tasks and the way
they are graded. SWE-drill runs the same tasks under several arms, grades every
result the same way, and ranks the arms on each lane.

Ranking plain Claude Code against two harnesses that review its change, each
run three times, prints output like this:

```
$ swe-drill leaderboard --lane rebench
arm          runs  resolved rate  pass@k
peer-review     3         66% ±7  78% @3
self-review     3         62% ±7  74% @3
claude-code     3         58% ±8  70% @3
```

## 2. Dependencies

| Dependency | Version | Needed for |
|---|---|---|
| Harbor | 0.26.0, exactly | Runs each task in a sandbox, runs each arm's agent in it, and grades the result with the task's own verifier. SWE-drill's agents and verifiers subclass Harbor's classes, so upgrading Harbor is a change of its own, and it is taken only when `uv run pytest`, a `rebench` check and an `atlas-rf` check, both on Modal, pass on the new version, and each check gives the reward it gave on the old one. |
| Docker | Docker Desktop 4.86.0 or later on macOS. On Linux and Windows, a Docker engine whose kernel has `CONFIG_NFT_FIB_INET`. | The sandboxes, with `environment = "docker"` in `swe-drill.toml`. Without `CONFIG_NFT_FIB_INET`, Harbor refuses every task, because the agent phase has no network. |
| Modal | 1.6.0 or later, which Harbor's own `modal` extra requires at the Harbor version above, and the account Harbor's `modal` environment uses. `uv sync --extra modal` installs its client into SWE-drill's environment, through that extra. A plain `uv sync` leaves it out. | The sandboxes, when `environment` is `modal`. |
| SWE-rebench | `ibragim-badertdinov/swe-rebench-07-2026` from Harbor Hub, as `harbor download` writes it | The `rebench` lane's tasks. |
| SWE Atlas | `scale-ai/swe-atlas-rf` from Harbor Hub, as `harbor download` writes it | The `atlas-rf` lane's tasks. |
| Python | 3.12 | SWE-drill itself, whose agents and verifier Harbor imports. |
| `uv` | any | Installs SWE-drill and Harbor. |
| Azure CLI | any, signed in with `az login` | `scripts/azure-run.sh`, which runs one `swe-drill run` on an Azure VM's Docker and deletes the VM once its results are kept. |
| Azure Blob Storage | a container named by `SWE_DRILL_RUNS_URL`, in the environment or `.env`, that the signed-in user may write to | Keeping runs off any one machine. A VM uploads its `results/` and `checks/` there, at their paths under the checkout, and `scripts/results.sh down <mirror>` downloads what `swe-drill leaderboard` reads, which then runs in `<mirror>`. |
| `azcopy` | 10 | `scripts/results.sh`, and the VMs' uploads. |

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
| **Lane** | A set of tasks that SWE-drill runs and reports as one, such as `rebench`. |
| **Harness** | What is being measured: a coding agent, with whatever plugins and settings it runs with. |
| **Agent** | The Harbor agent class an arm runs on: one of Harbor's own, with SWE-drill's mixin applied. |
| **Arm** | An agent and what it runs with: a model, and kwargs such as a version and a reasoning effort. |
| **Trial** | Harbor's unit of work: one attempt at one task by one arm, in a fresh sandbox. |
| **Base** | The commit the workspace's `HEAD` names when the trial starts. |
| **Starting tree** | What the workspace holds when the trial starts: the base, with every change the image left uncommitted, staged or not, and every untracked file git does not ignore. |

### What a trial does

Every arm runs on a Harbor agent class with `swe_drill.agents.Adapter`
applied before it, such as `swe_drill.agents.copilot_cli:CopilotCli`. Harbor
runs it once per trial, in two phases. The setup phase has network access. The agent phase reaches only
the hosts the agent class lists in `ALLOWED_HOSTS`, and those its task lists in
`[agent] allowed_hosts`.

In the setup phase, SWE-drill:

1. **finds the workspace, and records the base and the starting tree.** The
   workspace is the directory the sandbox starts in, which is the image's
   `WORKDIR`, such as `/gleam`. It must be the top level of a git repository,
   or the trial fails naming it. The base is `git rev-parse HEAD`. Recording the
   starting tree leaves the index, `HEAD`, the refs and `git status` as the
   image left them.
2. **lets the agent install itself**, as its Harbor class does.

In the agent phase, SWE-drill:

3. **runs the agent** on the task's instruction, as its Harbor class does. Every
   command the agent runs carries `SWE_DRILL_START_TOKEN`, set to a new token;
4. **stops every process carrying that token**, however the run ended;
5. **captures the patch**, the whole change from the starting tree;
6. **writes the trial record**, `suss.json`.

The trial's result comes from Harbor's separate verifier, a fresh sandbox
Harbor starts in the same agent job once the agent's sandbox has stopped.

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
Harbor carries to a verifier in a sandbox of its own, and SWE-drill writes it however
the run ends.

### Grading

Harbor grades each trial in its separate verifier: a fresh sandbox built from
the task's own `Dockerfile`, which it starts in the same agent job once the
agent's sandbox has stopped. Each task's copy in the lane sets
`[verifier] environment_mode = "separate"`. That sandbox holds nothing of the
agent's but `/logs/artifacts/`. SWE-drill's verifier applies the `model.patch` there
in the workspace, found as the trial found it, puts every test file back as the
image held it, and runs the task's own `test.sh` there, unchanged. That reward
is the trial's result. On `rebench` the agent job passes
`--verifier swe_drill.replay:ReplayVerifier`. On `atlas-rf` it passes
`--verifier swe_drill.judge:JudgedVerifier`, which grades the same way and then
runs the task's rubric judge on the host, as its lane describes.

It applies the patch with:

```bash
git apply --verbose model.patch
```

That changes the working tree alone, so `test.sh` finds the change uncommitted,
with `HEAD` at the base. Git applies all of the patch or none of it. The patch
runs from the starting tree, which the verifier's sandbox also starts from, so a
patch SWE-drill captured applies there.

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
`[verifier.env]` or `[environment.env]`. No lane's copies do:

```toml
[verifier.env]
GRADER_KEY = "${GRADER_KEY}"
```

SWE-drill runs the agent job's Harbor with each variable the lane's tasks name this
way in `[verifier.env]` alone that the `.env` beside `swe-drill.toml` sets, with its
value there, since Harbor gives `[verifier.env]` to the command that runs
`test.sh` and to no sandbox. It never passes one as an argument. No job's
Harbor takes these variables from swe_drill's own environment. So a variable `.env`
leaves out or leaves empty takes the task's default, and the agent phase gets one only when the arm's agent class gives it to its commands.
`harbor run` loads a `.env.local` in the directory it runs in, so a run exits
with an error before it builds any lane or runs any job when one is beside
`swe-drill.toml`.

### The trial record

SWE-drill writes these files to `/logs/agent/` in the sandbox. Harbor keeps that
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
  "agent": { "class": "swe_drill.agents.copilot_cli:CopilotCli", "name": "copilot-cli", "version": "1.0.105" },
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
- `grader` names the dataset the trial's task came from. SWE-drill fails a trial on
  a lane with no dataset before its sandbox starts.
- On `atlas-rf`, `grader` also holds the model its rubric judge calls, from
  `[lanes.atlas-rf.judge]`:

  ```json
  { "grader": { "dataset": "swe-atlas-rf", "judge_model": "gemini-4-argon" } }
  ```
- `base` is the commit. `model.patch` applies to the starting tree, which is
  the base only when the image left no change in the workspace.
- `status` is `ok`, `harness-failed` or `timed-out`, or for
  `CopilotCliSquiz` also `review-stopped` or `review-timed-out`. A trial runs
  until its task's agent timeout, and is `timed-out` when Harbor stops it
  there. It is `harness-failed` when the agent raises, as Harbor's agents do
  when their coding agent exits non-zero. A `CopilotCliSquiz` trial is
  `review-timed-out` when Harbor stops it there after Copilot CLI has exited,
  while squiz's review is still open, and `review-stopped` when squiz's
  episode stopped short of its close. Whatever the status, SWE-drill stops the
  agent's processes, then captures the patch and writes the record, and the
  verifier grades it.
- `squiz`, in a `CopilotCliSquiz` trial alone, says which squiz ran, whether
  Copilot CLI ran with `--experimental`, whether squiz loaded, and how its
  episode ended:

  ```json
  {
    "squiz": {
      "version": "0.3.3",
      "copilot_experimental": true,
      "loaded": true,
      "outcome": "closed",
      "closed": { "head": "0e7db84", "exitStatus": 0, "openThreads": [] },
      "stopped": null
    }
  }
  ```

  `loaded` is whether `.squiz/local/base.json`, which squiz's `SessionStart`
  hook writes, exists. `outcome` is `closed`, `stopped`, or `null` for an
  episode that did neither, and `closed` and `stopped` hold squiz's
  `closed.json` and `stopped.json`.
- A trial whose patch cannot be written fails with the capture's error, and is
  not graded, whatever the agent did.
- `usage` is what the agent reports it spent, or `null` for an agent whose
  reports SWE-drill does not read.

## 4. Agents

An arm names its agent class in `swe-drill.toml` as `module:Class`. SWE-drill
runs only a class that applies `swe_drill.agents.Adapter` before a Harbor agent class, since
only such a class leaves the patch its trial is graded by. Harbor's own agent
then does everything else: it installs the coding agent, runs it, parses its
output into `trajectory.json`, and records its tokens in `result.json`.

### The mixin

`Adapter` adds to a Harbor agent class what a trial needs:

- It records the base and the starting tree before the agent installs.
- It sets `SWE_DRILL_START_TOKEN` on every command the agent runs in the agent
  phase, and when the run ends it kills every process in the sandbox whose environment
  holds it, as the agent's user, by reading `/proc`. That finds each one
  whatever session or process group it moved to.
- It captures the patch and writes `suss.json` however the run ends.
- It takes `arm`, `lane` and `drill_config` as `--ak`, and reads the arm's
  settings and the `.env` from that `swe-drill.toml` when Harbor constructs it,
  so a bad config fails before a sandbox starts.
- It gives the agent nothing from Harbor's own environment, so no variable of
  the machine running SWE-drill sets one of the agent's flags.

A class names, as class attributes, the hosts its agent phase reaches in
`ALLOWED_HOSTS`, the arm kwargs it takes in `KWARGS`, those it needs in
`REQUIRED_KWARGS`, and whether it needs a model in `REQUIRES_MODEL`.

### Agents in this repository

| Agent | Model and kwargs | Hosts | What it does |
|---|---|---|---|
| `swe_drill.agents.copilot_cli:CopilotCli` | a model, required; `version`, required; `reasoning_effort`; `fusion_tier` | `api.github.com`, `api.enterprise.githubcopilot.com` | Harbor's `copilot-cli` agent. SWE-drill installs Copilot CLI at `version` from npm's registry, as the binary in `@github/copilot-<platform>`, which needs no Node, and Harbor's agent runs `copilot --prompt=<instruction> --yolo --excluded-tools web_fetch web_search --disable-mcp-server github-mcp-server --model=<model> --effort <reasoning_effort> --output-format=json`, excluding the web tools because the agent phase's network is an allowlist. Where `copilot --help` lists `--usage-output-file`, it also passes `--usage-output-file=/logs/agent/copilot-usage.json`. SWE-drill adds `--disable-builtin-mcps`, `--no-auto-update`, `--disable-client-telemetry`, `--no-ask-user`, `--secret-env-vars COPILOT_GITHUB_TOKEN` and `--excluded-tools web_fetch web_search`. Copilot CLI signs in with the token, and removes it from the environment of every shell and MCP server it starts, so the agent's commands never have it. The model is one of Copilot's own, by the name `--model` takes, and Harbor constructs no agent for one that names a provider, as `anthropic/<model>` does, which Harbor would run against that provider's API. Copilot CLI's commands in the agent phase get `COPILOT_GITHUB_TOKEN` from `.env` and no other token: never `GH_TOKEN` or `GITHUB_TOKEN`, and none from Harbor's environment. Harbor constructs it only when `.env` sets that token. With model `fusion`, Copilot picks the model and effort for each task, refuses `--effort`, and takes `fusion_tier`, one of `standard`, `high` or `max`, which SWE-drill writes as `fusionTier` to `~/.copilot/settings.json` before the agent runs, and never passes to Harbor's agent, which would add it as `--fusion-tier`. SWE-drill refuses `fusion_tier` with any other model. |
| `swe_drill.agents.hve:CopilotCliHve` | as `CopilotCli`, and `hve_core_commit`, required | as `CopilotCli` | `CopilotCli` as Microsoft's hve-core RPI Agent. Its install also downloads hve-core at `hve_core_commit`, a full 40-digit commit, from `https://codeload.github.com/microsoft/hve-core/tar.gz/<commit>` to `$HOME/.local/share/hve-core/<commit>/`, and fails when the archive holds no `plugin.json`, or that commit's `rpi-agent.agent.md` has no "Full Auto" handoff prompt. It adds `--plugin-dir` with that directory and `--agent hve-core:rpi-agent`, and gives as the prompt the Full Auto prompt, a blank line, then the instruction. `model` and `reasoning_effort` apply to the RPI Agent, and its subagents' own files name theirs. RPI keeps its state in the workspace's `.copilot-tracking/`, which SWE-drill makes a link to `/logs/agent/copilot-tracking/` and adds to `.git/info/exclude`, so it is kept as it is written and stays out of the patch. The trial is `harness-failed`, with Copilot CLI never started, when the workspace already has `.copilot-tracking`, or its own ignore rules keep git from ignoring it. |
| `swe_drill.agents.squiz:CopilotCliSquiz` | as `CopilotCli`, and `squiz_version`, required, an exact version no earlier than `0.3.3`; `reviewer_model`, a Copilot CLI model name, and `reviewer_effort`, one of `off`, `minimal`, `low`, `medium`, `high`, `xhigh` and `max`, each optional | as `CopilotCli` | `CopilotCli` with squiz reviewing and fixing the change in its local venue, with Copilot CLI as reviewer and fixer. Its install also installs `ps`, Node 24.15.0 from `https://nodejs.org/dist/`, checked against the archive's pinned sha256, to `$HOME/.local/share/swe-drill/node-v24.15.0/`, and squiz from `https://codeload.github.com/jacygao/squiz/tar.gz/refs/tags/v<squiz_version>` to `$HOME/.local/share/squiz/<squiz_version>/`. It replaces squiz's `bin/squiz` with one that runs that Node, links it as `$HOME/.local/bin/squiz`, and fails when `squiz version` names another version. That Node is on no `PATH`, so the task's own `node` is unchanged. Every Copilot CLI command it starts passes `--experimental`, and the agent's adds `--plugin-dir` with squiz's directory. Before the agent runs, it writes the arm's `COPILOT_GITHUB_TOKEN` to `$HOME/.local/share/swe-drill/copilot-token`, readable by the agent's user alone, and `{"venue": "local", "reviewer": "copilot", "fixer": "copilot", "copilotTokenFile": "<that file>"}` to the workspace's `.squiz.json`, with `model` and `thinking` set from `reviewer_model` and `reviewer_effort` where the arm names them. Where it names neither, the reviewer runs on Copilot's own default model and squiz's default effort, and the fixer always does, and adds `/.squiz.json` and `/.squiz/` to `.git/info/exclude`, so neither is in the patch. The trial is `harness-failed`, with Copilot CLI never started, when the workspace already has either, or its own ignore rules keep git from ignoring either. Once Copilot CLI exits, the trial is `harness-failed` where squiz left no `.squiz/local/base.json`, and otherwise waits for `.squiz/local/closed.json` or `stopped.json` before anything is stopped or captured. It then copies `.squiz/local/`, less `objects/`, to `/logs/agent/squiz/`, and each fixer session its state files name to `/logs/agent/squiz/fixer-sessions/<id>/`. Copilot CLI gives its hooks no `COPILOT_GITHUB_TOKEN`, so the reviewer and the fixer sign in from that file, and with no other token. The file is removed before the patch is captured. |
| `swe_drill.agents.claude_code:ClaudeCode` | a model, required; `version`, required; `reasoning_effort` | `api.anthropic.com` | Harbor's `claude-code` agent, on the owner's Claude subscription. It installs Claude Code at `version` with `https://downloads.claude.ai/claude-code-releases/bootstrap.sh`, and runs `claude --verbose --output-format=stream-json --effort <reasoning_effort> --disallowedTools WebSearch,WebFetch --permission-mode=bypassPermissions --print` with the instruction on its standard input and `ANTHROPIC_MODEL` set to the model, disallowing the web tools because the agent phase's network is an allowlist. The model is Anthropic's own, by the name Claude Code's `--model` takes, such as `claude-sonnet-5-5`, and Harbor constructs no agent for one that names a provider. Claude Code's commands in the agent phase get `CLAUDE_CODE_OAUTH_TOKEN` from `.env`, the token `claude setup-token` makes, and no other credential: never `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN` or `ANTHROPIC_BASE_URL`, and none from Harbor's environment. Harbor constructs it only when `.env` sets that token. |

The Copilot CLI agents record its usage from its stream: in `suss.json`'s
`usage`, and in Harbor's `agent_result.metadata` for the trial. `ai_credits`
is the largest `totalNanoAiu` of its `session.usage_checkpoint` events over
1e9. `reported_models` are the models
its `model.call_start` events name, and `reported_efforts` the efforts its
checkpoints name, so a run on a model or effort other than the one asked for
shows there. Where Copilot CLI wrote `/logs/agent/copilot-usage.json`, Harbor
takes `agent_result`'s token counts and `cost_usd` from it, and adds it to
`metadata` as `copilot_usage`. Where neither that file nor Harbor's parse of
the stream gave token counts, as for a GPT model on a Copilot CLI whose
`--help` lists no `--usage-output-file`, `agent_result` takes the last
checkpoint's input, cache-read and output tokens.

`CopilotCliSquiz` adds to `ai_credits` the credits of each squiz reviewer and
fixer session, read the same way from the session's `events.jsonl`, so the
trial's credits are the whole run's. Where the agent or any of those sessions
reported no credits, as one stopped before its first checkpoint does, that
part's `ai_credits` and the trial's are `null`. `usage.squiz` breaks them out,
and `reported_models` stays the coding agent's:

```json
{
  "usage": {
    "ai_credits": 25.9395,
    "reported_models": ["gpt-6.1-sol"],
    "reported_efforts": ["high"],
    "squiz": {
      "reviewer": { "ai_credits": 4.5, "reported_models": ["gpt-6-astra"], "sessions": 2 },
      "fixer": { "ai_credits": 3.0, "reported_models": ["gpt-6.1-sol"], "sessions": 1 }
    }
  }
}
```

## 5. Lanes

SWE-drill has two lanes, `rebench` and `atlas-rf`. Every task gives
the agent a repository at the commit before a real change, and an instruction
about that change. The task's verifier grades it, and a trial resolves when its
reward is 1. Each lane is measured on its own, and nothing compares one lane
with another. On each lane, `swe-drill leaderboard` ranks the arms by the share
of trials that resolve.

Each lane is a directory of copies of its tasks, built from the download that
its key in `swe-drill.toml` names. Each task's image is built from the `Dockerfile`
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
own makes `swe-drill run` exit with an error naming it before it builds any lane or
runs any job. Harbor's `oracle` agent leaves no patch for the separate verifier
to apply, so an oracle check of the lane runs on the download, whose verifier
shares the agent's sandbox:

```bash
harbor run -p swe-rebench-07-2026 -e modal -a oracle
```

Each copy's `tests/test.sh` installs `python3` through apt, on an image that has
none, just before it parses the test log. Otherwise the task's own script runs
its parser with `uv`, which downloads a Python from GitHub on every grade and
fails the grade when GitHub does not answer. A `test.sh` with no line that is
exactly `if command -v python3 >/dev/null 2>&1; then`, or with more than one, or
in a `tests` directory that is a link, makes `swe-drill run` exit with an error
naming it before it builds any lane or runs any job.

### `atlas-rf`

All 70 tasks of SWE Atlas's Refactoring set, `scale-ai/swe-atlas-rf`. Each
instruction asks for a refactoring of a real repository, names the interfaces it
must leave behind, and says the test files are already changed. Every task names
16 CPUs and 16384 MiB, an agent timeout of 3600 seconds and a verifier timeout
of 7200.

A trial's reward is 1 when the regression tests the refactoring must keep
passing pass, and an LLM judge passes every must-have item of the task's rubric
that it gives a verdict on. `verifier/reward.json` holds it with its parts:

```json
{ "reward": 0.0, "tests_reward": 1.0, "must_have_pass": false, "rubrics_agg_score": 0.6, "overall_pass": false }
```

The tests and the judge run in different places:

1. **The tests, in the verifier's sandbox.** Each copy's `tests/test.sh` is the
   task's, which captures the change with `git add -A` and
   `git diff --cached HEAD` into `verifier/agent.patch`, runs the tests, and
   writes `verifier/tests_reward.txt`. The one line that runs the judge,
   `python3 /tests/evaluate_rubrics.py || echo "evaluate_rubrics.py exited non-zero"`,
   is `echo "The rubric judge runs on the host, after this script"` in the copy.
2. **The judge, on the host.** The agent job grades with
   `--verifier swe_drill.judge:JudgedVerifier`, which grades as
   `ReplayVerifier` does, then runs the copy's `tests/evaluate_rubrics.py` in a
   process of its own, with the task's `tests/` directory, a copy of the
   agent's `model.patch`, and `rubrics_results.json` in place of the paths the
   script names in the container. The copy and the judge's files are in a
   directory of the trial's that no sandbox mounts, which is removed once they
   are moved into `verifier/`. A trial whose agent left no `model.patch` is
   judged on `verifier/agent.patch`. A patch or `tests_reward.txt` that is a
   link counts as missing. The process's environment is `PATH` and these, and
   nothing else:

   | Variable | Value |
   |---|---|
   | `EVAL_API_KEY` | `COPILOT_GITHUB_TOKEN` from the `.env` beside `swe-drill.toml` |
   | `EVAL_BASE_URL` | `https://api.enterprise.githubcopilot.com` |
   | `EVAL_MODEL` | `model` in `[lanes.atlas-rf.judge]` |

   `JudgedVerifier` then writes `reward.json` and `reward.txt` from
   `tests_reward.txt` and `rubrics_results.json`, as the task's `test.sh` does,
   and returns that reward to Harbor. A file that is missing or unreadable, or
   a value that is not a finite number, counts as a failure. Each of its four
   files is moved into `verifier/` by renaming it over whatever is there, and
   a name the judge wrote nothing for is removed there. The judge's output is
   in `verifier/judge.log`, and Harbor's verifier timeout covers it.

The judge's verdicts are in `verifier/rubrics_results.json`. An item the judge
gave no verdict on, as when every call to its model failed, is
`"evaluable": false` there and counts neither way, so a judge that reaches no
model leaves `must_have_pass` false and the reward 0.

Each copy's `task.toml` adds `[verifier] environment_mode = "separate"`, and
leaves out three lines of its `[verifier.env]`, so the verifier's sandbox takes
nothing from Harbor's environment:

```toml
EVAL_API_KEY = "${OPENAI_API_KEY}"
EVAL_BASE_URL = "${OPENAI_API_BASE}"
EVAL_MODEL = "${EVAL_MODEL:-anthropic/claude-opus-4-5-20251101}"
```

Copilot's API refuses every call without a `Copilot-Integration-Id` header, so
each copy's `tests/evaluate_rubrics.py` makes its one client send one, and is
otherwise the task's:

```python
client = OpenAI(api_key=api_key, base_url=base_url, default_headers={"Copilot-Integration-Id": "copilot-developer-cli"})
```

Harbor's Docker environment starts a task's image with its own command,
`sh -c "sleep infinity"`, and keeps the image's entrypoint. Fifteen tasks'
images set `ENTRYPOINT ["/bin/bash"]`: `task-69b7c2a04b6f8ff9ed98812c`,
`task-69b7c2a04b6f8ff9ed98812d`, `task-69b7c2a04b6f8ff9ed98812e`,
`task-69b7c2a04b6f8ff9ed98812f`, `task-69b7c2a04b6f8ff9ed988131`,
`task-69b7c2a04b6f8ff9ed988134`, `task-69b7c2a04b6f8ff9ed988137`,
`task-69b7c2a04b6f8ff9ed98813a`, `task-69b7c2a04b6f8ff9ed98813b`,
`task-69b7c2a04b6f8ff9ed98813d`, `task-69d196f015a150488265afc0`,
`task-69d196f015a150488265afc1`, `task-69d196f015a150488265afc2`,
`task-69d196f015a150488265afc3` and `task-69d196f015a150488265afc4`. bash reads
`sh` as a script, and the container exits 126 before the agent starts. Each of
those tasks' copies adds an `environment/docker-compose.yaml`, which Harbor
merges over its own, so the container runs Harbor's command alone:

```yaml
services:
  main:
    entrypoint: []
```

On Modal, a task with that file runs under Docker in Docker.

A task whose `[verifier]` sets another `environment_mode` or an `environment`
of its own, whose `task.toml` has no line that is exactly one of the three
above or has more than one, whose three lines are not all in
`[verifier.env]`, whose `test.sh` has not exactly one line that runs the judge,
whose `evaluate_rubrics.py` calls
`OpenAI(api_key=api_key, base_url=base_url)` other than once, or whose `tests`
directory is a link, makes `swe-drill run` exit with an error naming it before
it builds any lane or runs any job. So does one of the fifteen whose
`environment` directory is missing or a link, or already holds a
`docker-compose.yaml`.

### Network

In the agent phase, a trial reaches the hosts in its agent class's
`ALLOWED_HOSTS` and the hosts in its own task's `[agent] allowed_hosts`, and no
other host. A
`rebench` task names no hosts of its own, so its trials reach the agent's
alone.

SWE-drill passes each host in the agent class's `ALLOWED_HOSTS` to Harbor as
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
`allow_internet = true`. Every `rebench` and `atlas-rf` task does. So the copy's `task.toml`
adds `[agent] network_mode = "no-network"` where `[agent]` sets no
`network_mode`. A `task.toml` whose `[agent]` sets `no-network` or `allowlist`
is copied unchanged. One whose `[agent]` sets any
other `network_mode`, or sets `allowed_hosts` with no `network_mode`, makes
`swe-drill run` exit with an error naming it before it builds any lane or runs any
job. The verifier keeps the task's own network, which a `rebench` copy uses to
install Python through apt on an image that has none. An `atlas-rf` task's
verifier has a public network, through which its `test.sh` installs `openai`
from PyPI on an image that lacks it.

## 6. Commands

### `swe-drill run`

```bash
swe-drill run <arm> <lane>... [--check] [-- <harbor args>]
swe-drill run copilot-gpt rebench
```

It reads the arm from `swe-drill.toml` in the current directory. For each lane, it
builds `lanes/<lane>/`, a directory of copies of the lane's task directories
from the directory its key names: `swe_rebench` for `rebench`, and
`swe_atlas_rf` for `atlas-rf`. Each copy has
`[agent] network_mode = "no-network"` added to its `task.toml` where its
`[agent]` sets no `network_mode`, and the separate verifier its lane describes.
On `atlas-rf` its rubric
judge calls Copilot's API, as its lane describes. A build removes what an
earlier build of the lane left. Harbor builds each task's image from its
`Dockerfile` when its trial starts.

Then, K times, it runs an agent job, which grades its own trials:

```bash
<env>/bin/harbor run -p lanes/rebench -e docker -n 16 -k 1 \
  -a swe_drill.agents.copilot_cli:CopilotCli -m gpt-6.1-sol \
  --ak version=1.0.105 --ak reasoning_effort=high \
  --ak arm=copilot-gpt --ak drill_config=swe-drill.toml --ak lane=rebench \
  --allow-agent-host api.github.com --allow-agent-host api.enterprise.githubcopilot.com \
  --verifier swe_drill.replay:ReplayVerifier \
  --jobs-dir results/rebench/copilot-gpt --job-name <stamp>-1
```

On `atlas-rf` it grades with the judged verifier, which reads the arm's
settings as the agent does:

```bash
<env>/bin/harbor run -p lanes/atlas-rf -e docker -n 16 -k 1 \
  -a swe_drill.agents.copilot_cli:CopilotCli -m gpt-6.1-sol \
  --ak version=1.0.105 --ak reasoning_effort=high \
  --ak arm=copilot-gpt --ak drill_config=swe-drill.toml --ak lane=atlas-rf \
  --allow-agent-host api.github.com --allow-agent-host api.enterprise.githubcopilot.com \
  --verifier swe_drill.judge:JudgedVerifier --verifier-kwarg drill_config=swe-drill.toml \
  --verifier-kwarg arm=copilot-gpt --verifier-kwarg lane=atlas-rf \
  --jobs-dir results/atlas-rf/copilot-gpt --job-name <stamp>-1
```

`<stamp>` is the time `swe-drill run` started, in UTC, as `20261031T142500Z`. Every
job of the run shares it, and `-1` to `-K` tell its agent jobs apart. `<env>`
is the Python environment SWE-drill is installed in, an absolute path SWE-drill forms
itself. Its `harbor` is the one that can import `swe_drill.agents` and SWE-drill's
verifiers, which a `harbor` elsewhere on `PATH` may not be.

When a task in the lane has Harbor fill a variable from Harbor's environment,
every job of the lane takes `-y`, right after `run`. Harbor asks before such a job whether its tasks may load those
variables, and without a terminal it reads no answer and exits 1 with
`Aborted.`. Answering yes is safe because Harbor holds only the values SWE-drill
gives it: those `.env` sets for the verifier alone. `-y` also agrees, without asking, to share an uploaded
job with an organization you are not a member of.

A run of `atlas-rf` exits with an error before it builds any lane or runs any
job, with `DRY_RUN=1` and `--check` too, when `swe-drill.toml` sets no `model`
in `[lanes.atlas-rf.judge]`, or `.env` sets no `COPILOT_GITHUB_TOKEN`. A token
set to an empty value counts as missing:

```
$ swe-drill run copilot-gpt atlas-rf
error: /home/me/evals/swe-drill.toml: no model in [lanes.atlas-rf.judge], which
names the model atlas-rf's rubric judge calls through Copilot, such as
gemini-4-argon
```

Options after `--` go to every agent job. Passing any of these, under any name
or spelling Harbor accepts, is an error that names it and says why, and runs
nothing:

- An option SWE-drill sets on the agent job, even with SWE-drill's own value: `-p`,
  `-e`, `-n`, `-k`, `-a`, `--ak`, `--allow-agent-host`, `--verifier`,
  `--verifier-kwarg`, `--jobs-dir`, `--job-name` and `-y`. Also `-m`, which
  the arm's `model` sets, and `--disable-verification` and its opposite
  `--enable-verification`, since the agent job grades its own trials.
- `--env-file`, which would load the file into the agent job's environment,
  `--ae`, which would reach every command the agent runs, `--ve`, which would
  reach the code the agent wrote through the verifier's `test.sh`, and
  `--share` where the jobs take `-y`.
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
  `--install-only`, `--launch`, `--server`, `--watch`, `--dry-run`, `--diff` and
  `--print-config`.
- A positional argument, which `harbor run` does not take.

```
$ swe-drill run copilot-gpt rebench -- -k 2
error: -k/--n-attempts can't be passed after --: SWE-drill runs one trial per agent
job, and k in swe-drill.toml sets how many jobs
```

`DRY_RUN=1` prints the `harbor run` commands instead of running them. Before an
agent job whose Harbor holds values from `.env`, it prints their names on a line
of their own, as `# from .env: GRADER_KEY`, and never a value. No lane's job
holds any:

```
/home/me/evals/.venv/bin/harbor run -p lanes/atlas-rf -e docker -n 16 -k 1 -a swe_drill.agents.copilot_cli:CopilotCli -m gpt-6.1-sol --ak version=1.0.105 --ak reasoning_effort=high --ak arm=copilot-gpt --ak drill_config=swe-drill.toml --ak lane=atlas-rf --allow-agent-host api.github.com --allow-agent-host api.enterprise.githubcopilot.com --verifier swe_drill.judge:JudgedVerifier --verifier-kwarg drill_config=swe-drill.toml --verifier-kwarg arm=copilot-gpt --verifier-kwarg lane=atlas-rf --jobs-dir results/atlas-rf/copilot-gpt --job-name 20261031T142500Z-1
```

Each agent job runs whatever the one before it did and whatever Harbor's exit
code was, and `swe-drill run` exits 0. The one exception is an agent job that leaves
no trial in its job directory, as when Harbor cannot start at all: `swe-drill run`
then prints the job's name and Harbor's exit code, runs nothing more, and exits
non-zero.

It gives Harbor `results/<lane>/<arm>/` as its jobs directory, so each agent job
is written there. Results stay there across runs, so `swe-drill leaderboard` ranks
an arm run later beside arms graded earlier, without running them again.

`--check` tests that an arm works under SWE-drill, for the cost of one trial per
lane. It builds `lanes/<lane>-check/` with only the lane's first task by name,
and gives that to its jobs in place of `lanes/<lane>/`, which a full run of the
lane may be using. It runs one agent job, whatever `k` is, and writes them to `checks/<lane>/<arm>/` instead of `results/`:

```bash
swe-drill run copilot-gpt rebench --check
```

`swe-drill.toml` names where the tasks are, where the sandboxes run, and the arms:

```toml
swe_rebench = "../tasks/swe-rebench-07-2026"
swe_atlas_rf = "../tasks/swe-atlas-rf"
environment = "docker"
k = 1
concurrency = 16

[arms.copilot-sonnet]
agent = "swe_drill.agents.copilot_cli:CopilotCli"
model = "claude-sonnet-5.5"
kwargs = { version = "1.0.105", reasoning_effort = "high" }

[arms.copilot-gpt]
agent = "swe_drill.agents.copilot_cli:CopilotCli"
model = "gpt-6.1-sol"
kwargs = { version = "1.0.105", reasoning_effort = "high" }

[arms.hve-sol]
agent = "swe_drill.agents.hve:CopilotCliHve"
model = "gpt-6.1-sol"
kwargs = { version = "1.0.105", reasoning_effort = "high", hve_core_commit = "55352bb8e9f0622ff40f5c6afa80363adaf6b07f" }

[lanes.atlas-rf.judge]
model = "gemini-4-argon"
```

- `swe_rebench` and `swe_atlas_rf` name the directories of
  tasks that `harbor download` writes, relative to the directory holding
  `swe-drill.toml`. `swe_rebench` is `<dir>/swe-rebench-07-2026` after
  `harbor download ibragim-badertdinov/swe-rebench-07-2026 -o <dir>`, and
  `swe_atlas_rf` is `<dir>/swe-atlas-rf` after
  `harbor download scale-ai/swe-atlas-rf -o <dir>`. Each is required only by a
  run of its lane, `rebench` or `atlas-rf`. A run of a lane
  whose key is missing exits with an error naming it before it builds any lane
  or runs any job. A `senior_swe_bench` key makes `swe-drill run` exit with an
  error before it builds any lane or runs any job:

  ```
  $ swe-drill run copilot-gpt rebench
  error: /home/me/evals/swe-drill.toml: sets senior_swe_bench, which SWE-drill no
  longer reads, since it has no senior lane; remove it
  ```
- `agent` names the arm's agent class, as `module:Class`. `model` is passed as
  `-m`, and each of `kwargs` as `--ak`, of its own type. A class that is not
  built on `Adapter`, a kwarg it does not take, or a model or kwarg it needs
  and the arm leaves out, makes `swe-drill run` exit with an error before it builds
  any lane or runs any job. Harbor refuses such a kwarg only once the agent job
  has started, and takes any kwarg for an agent class that declares no options:

  ```
  $ swe-drill run copilot-gpt rebench
  error: /home/me/evals/swe-drill.toml: [arms.copilot-gpt] sets kwargs max_ai_credits,
  which swe_drill.agents.copilot_cli:CopilotCli does not take; it takes version,
  reasoning_effort, fusion_tier
  ```

  An arm that sets `harness` or `params` makes `swe-drill run` exit with an error
  saying to use `agent`, `model` and `kwargs`.
- `environment` is passed to `harbor run` as `-e`: `docker` runs the sandboxes
  on this machine, and `modal` runs them on Modal. With `modal`, when Modal's
  client is not installed in SWE-drill's environment, `swe-drill run` exits with an error
  before it builds a lane or runs any job, with `DRY_RUN=1` and `--check` too:

  ```
  $ swe-drill run copilot-gpt rebench
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
  `memory_mb` is, and Docker's is rounded down. A task that gives `memory`
  counts as Harbor reads it: `"8G"` is 8192 MiB. When
  either does not fit, `swe-drill run` exits with an error before it builds any lane
  or runs any job, and does so under `DRY_RUN=1` too. A task that names no
  `cpus`, or neither `memory_mb` nor a `memory` Harbor reads, is an error that
  names it. A `--check` counts only its one task, and one trial at a time.

  ```
  $ swe-drill run copilot-gpt rebench
  error: concurrency = 16 needs 16 CPUs and 65536 MiB for the tasks in rebench,
  but Docker has 10 CPUs and 7934 MiB; set concurrency to 1 or less, or give
  Docker more
  ```

  When a single trial does not fit, the error says so and offers no
  `concurrency`.
- `[lanes.atlas-rf.judge]` gives `model`, the model an `atlas-rf` run's rubric
  judge calls through Copilot's API, by the name that API takes. A model that
  is not a non-empty string, or any other key, makes `swe-drill run` exit with an error
  before it builds any lane or runs any job.

  A `[lanes.<lane>]` table for a lane SWE-drill does not have, or one that sets
  anything but `judge` on `atlas-rf`, makes `swe-drill run` exit
  with an error before it builds any lane or runs any job:

  ```
  $ swe-drill run copilot-gpt rebench
  error: /home/me/evals/swe-drill.toml: sets [lanes.rebnech], but SWE-drill has no lane
  named rebnech; its lanes are rebench, atlas-rf
  ```
- `max_cost_usd`, at the top or on the arm, makes `swe-drill run` exit with an error
  before it builds any lane or runs any job, with `DRY_RUN=1` and `--check` too:

  ```
  $ swe-drill run copilot-gpt rebench
  error: /home/me/evals/swe-drill.toml: [arms.copilot-gpt] sets max_cost_usd, but SWE-drill
  does not read it
  ```
- `baseline`, on any arm, makes `swe-drill run` and `swe-drill leaderboard` exit with an
  error naming the arm, before `swe-drill run` builds any lane or runs any job, with
  `DRY_RUN=1` and `--check` too:

  ```
  $ swe-drill run copilot-gpt rebench
  error: /home/me/evals/swe-drill.toml: [arms.hve-sol] sets baseline, which SWE-drill no
  longer reads; swe-drill leaderboard ranks every arm on its lane
  ```

### `swe-drill leaderboard`

```bash
swe-drill leaderboard --lane rebench
```

It ranks every arm under `results/<lane>/` in the current directory, the one
holding `swe-drill.toml`, for `rebench` or `atlas-rf`. Each directory there is an arm, whether or not `swe-drill.toml`
defines it, and trials under `checks/` never count. It prints one table, best
resolved rate first, and arms with the same rate by name:

```
arm          runs  resolved rate  pass@k
claude-opus     2         68% ±7  78% @2
copilot-gpt     1         67% ±9  67% @1
```

- `runs` is how many of the arm's agent jobs hold a trial.
- `resolved rate` is resolved trials over valid trials, so a task with more
  valid trials weighs more. The ± is half the width of a 95% interval from a
  bootstrap of 10,000 resamples, each drawing the arm's tasks with replacement
  and taking every valid trial of each task drawn, so a task's runs stay
  together. The same results always print the same margin.
- `pass@k` is the share of the arm's tasks that at least one run resolved, and
  k, after the `@`, is the arm's runs.
- Each percent is whole, a half rounded up.

A trial's result is the reward its own verifier gave it, in the agent trial's
`result.json`. A valid trial whose verifier left no reward is unresolved, as
when it timed out or crashed, and `harness-failed` and `timed-out` trials count
as their verifier graded them.

A trial is invalid when its verifier wrote an empty `verifier/reward.txt`,
which Harbor records in the trial's `result.json` as an `exception_info` whose
`exception_type` is `RewardFileEmptyError`. An invalid trial counts nowhere, as
if it had not run, so a task whose every trial in an arm is invalid is not one
of that arm's tasks.

After the table, it names each valid trial whose verifier recorded no test
result, then on `atlas-rf` each valid trial whose judge gave no verdict on some
rubric item, then each invalid trial, by arm in the table's order, then by job
and task.

- A `rebench` verifier records a test result when its `verifier/report.json`
  counts a test as `PASSED`, `FAILED`, `SKIPPED` or `ERROR` in `parsed_counts`,
  and an `atlas-rf` verifier when its `verifier/comparison_results.json` holds a
  number as `tests_reward`.
- An `atlas-rf` judge gave a verdict on every item when each entry of
  `rubric_scores` in `verifier/rubrics_results.json` is `"evaluable": true`. A
  file that is missing, or has no `rubric_scores`, gives none.

```
These trials' verifiers recorded no test result, so their grading may have broken:
  claude-opus  20261109T031500Z-2  marimo-team__marimo-9766

These trials' judges gave no verdict on some rubric item, so their grading may have broken:
  copilot-gpt  20261118T011500Z-1  task-69391d8d1ce51c407be1e531

These trials' verifiers judged them invalid, so the leaderboard counts none of them:
  copilot-gpt  20261108T011500Z-1  gleam-lang__gleam-4890
```

`--lane` is required, and must name one of SWE-drill's lanes:

```
$ swe-drill leaderboard --lane rebnech
error: SWE-drill has no lane named rebnech; its lanes are rebench, atlas-rf
```

It exits with an error, and prints nothing else, when:

- `swe-drill.toml` is missing, or an arm in it sets `baseline`.
- `results/<lane>/` holds no arm, or an arm with no trials or only invalid
  ones:

  ```
  $ swe-drill leaderboard --lane rebench
  error: every trial of arm copilot-gpt under results/rebench/ is invalid: its
  verifier wrote an empty reward
  ```
- A trial whose `suss.json` has `patch_bytes` above 0 has a verifier that
  applied no patch: its `verifier/replay.json` has `"patch": null`, or it has
  no `replay.json` and its `result.json` records an exception.
- A trial's verifier could not apply its patch: its `replay.json` names a
  patch, and its `apply_rc` is not 0.
- Anything follows `--`.

## 7. The project

### Language

Python 3.12, managed with `uv`.

### Structure

```
src/swe_drill/
  agents/           the agent classes arms run on
    __init__.py     Adapter, the contract every agent class keeps
    copilot_cli.py  CopilotCli, its install from npm, and reading its sessions' usage
    claude_code.py  ClaudeCode
    hve.py          CopilotCliHve
    squiz.py        CopilotCliSquiz
  sandbox.py        commands in the sandbox, and stopping what they started
  workspace.py      finding the workspace, and capturing the patch
  lanes/            building a lane's directory from the downloaded tasks
    __init__.py     the lanes, the build, and what every lane changes in a copy
    edits.py        changing one line or key of a task's file, and nothing else
    rebench.py      what the rebench lane alone leaves out and changes
    atlas_rf.py     what the atlas-rf lane alone changes
  config.py         swe-drill.toml
  replay.py         ReplayVerifier, which grades a trial in Harbor's separate verifier
  judge.py          JudgedVerifier, which runs atlas-rf's rubric judge on the host
  leaderboard.py    swe-drill leaderboard
  cli.py            the swe-drill command
scripts/
  azure-run.sh      one swe-drill run on a fresh Azure VM, then the VM deleted
  results.sh        runs to and from Azure Blob Storage
site/               the leaderboard page, static, drawn from leaderboard.json
.github/workflows/
  pages.yml         publishes site/ to GitHub Pages whenever it changes on main
tests/
docs/specs/
```

Each module under `agents/` but `__init__.py` holds one agent class. It may
import any other module in `src/swe_drill/`, and no module outside those agent
modules imports one: the rest loads an arm's class only by the import path its
`agent` names in `swe-drill.toml`.
