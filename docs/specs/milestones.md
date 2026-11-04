# Suss Milestones

The milestones that build suss, in order. Each ends in something that can be
run.

## M1 — One trial, end to end

`SussAgent` runs one arm on one task. It records the base, installs and runs the
harness through its adapter, captures the patch and writes the trial record.
The tests prove it with a fake adapter, and `adapters/claude-code/` is the first
real one. The arm is read from `suss.toml`.

### Acceptance criteria

- [ ] The repository is a `uv` project for Python 3.12, with suss under
      `src/suss/`, and `uv run pytest` passes in a fresh clone
- [ ] The tests run `SussAgent` through a fake adapter under `tests/`, whose
      `run` writes a known change and calls no model
- [ ] This completes, and leaves `model.patch` and `suss.json` in the trial's
      agent logs:
      ```bash
      harbor run -p <one pro-go task> -e docker -k 1 \
        -a suss.agent:SussAgent --ak arm=claude-code --ak config=suss.toml \
        --allow-agent-host api.anthropic.com
      ```
- [ ] `suss.json` holds `arm`, the harness's `name` and `version`, `params`, the
      grader's `dataset` and `commit`, `base`, the `exit_codes` of `install` and
      `run`, `run_seconds` and `patch_bytes`, with `status` `ok`
- [ ] The agent logs also hold `run.log`, and `install.log` where the manifest
      names `install`, each with that command's stdout and stderr
- [ ] The workspace is `/app`, or `/testbed` where `/app` does not exist
- [ ] `model.patch` holds the harness's whole change from the base, whether the
      harness committed it or not
- [ ] An `install` that exits non-zero marks the trial `harness-failed`, `run` is
      skipped, and the patch is empty
- [ ] A `run` that exits non-zero marks the trial `harness-failed`, and its patch
      is still captured
- [ ] A `run` still going after `max_seconds` is stopped, the trial is marked
      `timed-out`, and its patch is captured
- [ ] `install` and `run` see only the variables the manifest's `env` names,
      from the `.env` beside `suss.toml`
- [ ] `--instruction` and `--params` name `/opt/suss/instruction.md` and
      `/opt/suss/params.json`, outside the workspace, and neither file appears
      in the patch
- [ ] In `adapters/claude-code/`, `install` installs Claude Code at `version`
      with its native installer, and `run` runs Claude Code headless with the
      instruction as its prompt, every tool allowed except `WebSearch` and
      `WebFetch`, and the params' `max_budget_usd` passed as `--max-budget-usd`

## M2 — `suss run`

`suss run` builds each lane's directory, then runs K agent jobs, each followed by
its regrade. It writes both under `results/<lane>/<arm>/`. It runs them under
Docker on this machine with `environment = "docker"`, or on Modal with
`environment = "modal"`, and a trial and its record are the same under either.

### Acceptance criteria

- [ ] `lanes/<lane>/` holds links to exactly the lane's tasks: 256 for `pro-go`,
      237 for `pro-python`, 149 for `pro-js` and 51 for `pro-hard51`
- [ ] With `DRY_RUN=1`, `suss run peer-review pro-go` runs nothing, and prints K
      pairs of commands: a `harbor run` of `suss.agent:SussAgent`, then a
      `harbor run` of `patch_replay:PatchReplayAgent` whose `source_job` is that
      agent job
- [ ] Every job of a run is named for the time the run started, in UTC, as
      `20261031T142500Z-1`, with `-regrade` after the agent job's name for its
      regrade
- [ ] Each agent job gets one `--allow-agent-host` for each host in the
      harness's `allowed_hosts`, and no others
- [ ] Each agent job runs with Harbor's verification disabled, so its regrade
      is the only grading of its trials
- [ ] Options after `--` reach every agent job, except an option suss sets
      itself or one that changes the tasks or the agent the job runs, which is
      an error naming it
- [ ] `max_seconds` set on an arm takes precedence over the one set at the top
      of `suss.toml`
- [ ] An arm whose `max_seconds` is not under the agent timeout of every task in
      a lane makes `suss run` exit with an error before it runs any job
- [ ] Under Docker, a `concurrency` whose trials need more CPUs or memory than
      the Docker engine has makes `suss run` exit with an error before it runs
      any job
- [ ] A `run` stopped at `max_seconds` leaves no process it started running in
      the sandbox, and its trial records what the harness spent
- [ ] `harness` resolves as a path to an adapter, relative to the directory
      holding `suss.toml`
- [ ] Trials already under `results/<lane>/<arm>/` are still there after another
      run of the same arm
- [ ] With `--check`, `suss run` runs one agent job and its regrade over
      `lanes/<lane>-check/`, which holds only the lane's first task by name,
      whatever `k` is, and writes them under `checks/<lane>/<arm>/`.
      `lanes/<lane>/` is left as it was
- [ ] `suss run claude-code pro-go --check` under Docker leaves an agent job and its
      regrade under `checks/pro-go/claude-code/`, and the regrade's reward is
      the trial's result
- [ ] On Modal, a `run` still going after `max_seconds` is stopped, the trial is
      marked `timed-out`, and its patch is captured
- [ ] On Modal, in the agent phase of a `no-network` task, the harness reaches
      each host in its `allowed_hosts` and no other host
- [ ] `suss run claude-code pro-go --check` on Modal leaves an agent job and its
      regrade under `checks/pro-go/claude-code/`, the trial's `suss.json` has
      `status` `ok`, and the regrade's reward is the trial's result
- [ ] `uv run pytest` passes

## M3 — `suss compare`

`suss compare` reads every graded trial of the arms it is given, and prints how
each arm did and how it differs from its baseline.

### Acceptance criteria

- [x] `suss compare pro-go claude-code self-review peer-review`, over results in
      the layout `suss run` writes, prints two tables. The first gives each arm's
      trials and its resolved, failed and timed-out shares. The second gives each
      arm's resolved difference from its baseline, with an interval:
      ```
      arm          trials  resolved  failed  timed out
      claude-code     256       38%      0%         1%
      self-review     256       41%      1%         1%
      peer-review     256       44%      1%         2%

      vs. baseline                resolved
      self-review - claude-code   +3% [-2%, +8%]
      peer-review - claude-code   +6% [+1%, +11%]
      ```
- [x] Only tasks that every given arm has trials for are counted, and nothing
      under `checks/` is read
- [x] Each trial in `<stamp>-<i>` is paired with the trial for the same task in
      `<stamp>-<i>-regrade`, and its result is that regrade's reward
- [x] `harness-failed` and `timed-out` trials count as their regrade graded them
- [x] A trial whose regrade has no reward counts as unresolved, and a trial with
      no `suss.json` counts as `harness-failed`
- [x] Each arm's `baseline` is read from `suss.toml` in the current directory
- [x] An arm gets a row in the second table only when its `baseline` is among
      the arms given
- [x] The interval is a 95% bootstrap interval that resamples tasks, keeping each
      task's K trials together, and a fixture with a known difference lands
      inside it. The same results always print the same interval
- [x] An arm with no results under the lane, or not defined in `suss.toml`,
      makes the command exit non-zero and print only an error naming the arm
- [x] When the arms' trials that have a `suss.json` do not all carry the same
      `grader`, the command exits non-zero and prints only the error. A trial
      with no `suss.json` takes no part in that check
- [x] A trial whose `suss.json` has `patch_bytes` above 0, and whose regrade
      applied no patch, makes the command exit non-zero and print only the error
- [x] `uv run pytest` passes

## M4 — The SWE-rebench lane

Suss runs one lane, `rebench`, built from SWE-rebench's July 2026 snapshot, and
regrades it with its own `ReplayAgent`. SWE-bench Pro, its lanes and its
regrading agent are gone.

### Acceptance criteria

- [ ] `lanes/rebench/` holds a copy of each of the lane's 108 tasks, whose
      `task.toml` differs from the download's only by
      `[agent] network_mode = "no-network"`
- [ ] `suss.toml` names the tasks with `swe_rebench`, and `swe_bench_pro` and
      `dataset` are gone
- [ ] With `DRY_RUN=1`, `suss run` prints a regrade of
      `suss.replay:ReplayAgent`, with no `PYTHONPATH`
- [ ] `ReplayAgent` finds a trial's patch by the task the trial ran, applies it
      in the workspace, and writes `agent/replay.json`, with `"patch": null` for
      a missing or empty patch
- [ ] The workspace is the directory the sandbox starts in, and a trial whose
      starting directory holds no git repository fails naming it
- [ ] A trial's `grader` is
      `{ "dataset": "swe-rebench-07-2026", "digest": "<sha256>" }`, and two
      downloads of the same tasks give the same digest
- [ ] Nothing in suss reads a SWE-bench Pro checkout, or pulls an image
- [ ] On Modal, in the agent phase of a `rebench` task, the harness reaches each
      host in its `allowed_hosts` and no other host
- [ ] `suss run claude-code rebench --check` on Modal leaves an agent job and
      its regrade under `checks/rebench/claude-code/`, the trial's `suss.json`
      has `status` `ok`, and the regrade's reward is the trial's result
- [ ] `uv run pytest` passes

## M5 — Claude Code on a subscription

`adapters/claude-code/` runs Claude Code on a Claude subscription as well as on
an API key, so a Claude Code arm can be measured on a plan, as a `copilot-cli`
arm is on a Copilot plan.

### Acceptance criteria

- [ ] The adapter takes the token `claude setup-token` makes, as
      `CLAUDE_CODE_OAUTH_TOKEN`, or `ANTHROPIC_API_KEY`, from the run's `.env`,
      and `run` exits 1 naming the problem when both are set or neither is,
      before Claude Code starts
- [ ] An arm on a subscription refuses `max_budget_usd`, and its trials record
      each call's token usage as an API-key arm's trials do
- [ ] A trial that stops at the plan's usage limit has `status`
      `usage-limited`, not `harness-failed`
- [ ] On Modal, in the agent phase of a `rebench` task, a subscription arm
      reaches each host in the manifest's `allowed_hosts` and no other host
- [ ] `suss run <arm> rebench --check` on Modal, with only the subscription
      token in `.env`, leaves a trial with `status` `ok` and its regrade
- [ ] `uv run pytest` passes

## M6 — The Senior SWE-Bench lane

Suss runs a second lane, `senior`, built from Snorkel's Senior SWE-Bench
snapshot `senior-swe-bench-v2026.06`, which holds 50 tasks. Each task's verifier
runs its tests, a validation agent and LLM judges, and `suss compare` reports
the judges' scores beside each arm's resolved share.

### Acceptance criteria

- [ ] A note in `docs/notes/` gives, for one task graded by its default judges,
      what the trial and its regrade cost, in AI credits and in API spend, how
      long each took, and how far the judges' scores move when the same patch
      is regraded
- [ ] `lanes/senior/` holds a copy of each task Harbor's oracle passes, and
      `suss.toml` names the download with `senior_swe_bench`
- [ ] The regrade passes the verifier the grading keys and `SSB_OVERRIDE_*`
      settings from the run's `.env`, and the agent phase gets none of them
- [ ] A `senior` trial's `grader` holds the dataset, its digest, and the judge
      and validation-agent models its regrade used
- [ ] In the agent phase of a `senior` task, the harness reaches the hosts in
      its manifest's `allowed_hosts` and in the task's `[agent] allowed_hosts`,
      and no other host
- [ ] `max_seconds` can be set per lane in `suss.toml`
- [ ] A trial whose regrade writes an empty reward, as the verifier does for an
      invalid trial, counts in no share, and `suss compare` names it
- [ ] For the `senior` lane, `suss compare` gives each arm's mean rubric and
      taste scores
- [ ] `suss run <arm> senior --check` on Modal leaves a trial and its regrade,
      with a reward and the judges' scores
- [ ] `uv run pytest` passes
