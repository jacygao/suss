# Suss Milestones

The milestones that build suss, in order. Each ends in something that can be
run.

## M1 — One trial, end to end

`SussAgent` runs one arm on one task. It records the base, installs and runs the
harness through its adapter, captures the patch and writes the trial record. The
built-in `claude-code` adapter is the harness it is proven with, and the arm is
read from `suss.toml`.

### Acceptance criteria

- [ ] The repository is a `uv` project for Python 3.12, with suss under
      `src/suss/`, and `uv run pytest` passes in a fresh clone
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
- [ ] The `claude-code` adapter's `install` installs Claude Code at `version`
      with its native installer. Its `run` runs Claude Code headless with the
      instruction as its prompt, every tool allowed except `WebSearch` and
      `WebFetch`, and `--max-cost-usd` passed as `--max-budget-usd`

## M2 — `suss run`

`suss run` builds each lane's directory, then runs K agent jobs, each followed by
its regrade. It writes both under `results/<lane>/<arm>/`.

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
- [ ] Arguments after `--` reach every agent job
- [ ] `max_cost_usd` and `max_seconds` set on an arm take precedence over the
      ones set at the top of `suss.toml`
- [ ] An arm whose `max_seconds` is not under the agent timeout of every task in
      a lane makes `suss run` exit with an error before it runs any job
- [ ] `harness` resolves to a built-in adapter by name, or to a path to one
      relative to the directory holding `suss.toml`
- [ ] Trials already under `results/<lane>/<arm>/` are still there after another
      run of the same arm
- [ ] With `--check`, `suss run` runs one agent job and its regrade over
      `lanes/<lane>-check/`, which holds only the lane's first task by name,
      whatever `k` is, and writes them under `checks/<lane>/<arm>/`.
      `lanes/<lane>/` is left as it was
- [ ] `suss run claude-code pro-go --check` on Modal leaves an agent job and its
      regrade under `checks/pro-go/claude-code/`, and the regrade's reward is
      the trial's result
- [ ] `uv run pytest` passes

## M3 — `suss compare`

`suss compare` reads every graded trial of the arms it is given, and prints how
each arm did and how it differs from its baseline.

### Acceptance criteria

- [ ] `suss compare pro-go claude-code self-review peer-review`, over results in
      the layout `suss run` writes, prints two tables. The first gives each arm's
      trials and its resolved, failed and timed-out shares. The second gives each
      arm's resolved difference from its baseline, with an interval:
      ```
      arm          trials  resolved  failed  timed out
      claude-code     256       38%      0%         1%
      self-review     256       41%      1%         1%
      peer-review     256       44%      1%         2%

      vs. baseline                resolved
      self-review − claude-code   +3% [−2%, +8%]
      peer-review − claude-code   +6% [+1%, +11%]
      ```
- [ ] Only tasks that every given arm has trials for are counted, and nothing
      under `checks/` is read
- [ ] Each trial in `<stamp>-<i>` is paired with the trial for the same task in
      `<stamp>-<i>-regrade`, and its result is that regrade's reward
- [ ] `harness-failed` and `timed-out` trials count as their regrade graded them
- [ ] A trial whose regrade has no reward counts as unresolved, and a trial with
      no `suss.json` counts as `harness-failed`
- [ ] Each arm's `baseline` is read from `suss.toml` in the current directory
- [ ] An arm gets a row in the second table only when its `baseline` is among
      the arms given
- [ ] The interval is a 95% bootstrap interval that resamples tasks, keeping each
      task's K trials together, and a fixture with a known difference lands
      inside it
- [ ] When the arms' trials that have a `suss.json` do not all carry the same
      `grader`, the command exits non-zero and prints only the error. A trial
      with no `suss.json` takes no part in that check
- [ ] `uv run pytest` passes
