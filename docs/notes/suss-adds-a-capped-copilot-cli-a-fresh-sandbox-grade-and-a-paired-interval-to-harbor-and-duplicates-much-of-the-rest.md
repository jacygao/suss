---
settles: "§ 3 — which parts of a trial, its regrade and its record Harbor 0.22.0 already provides; § 4 — what adapters/copilot-cli/ and adapters/claude-code/ add over Harbor's own agents; § 5 — what building a lane adds over a Harbor dataset; § 6 — what suss compare adds over Harbor's metrics, viewer and Hub"
issue: [211]
recorded: 2026-11-08
versions: { harbor: 0.22.0, copilot-cli: 1.0.105, environment: modal, lane: "rebench, 108 tasks; check on vuejs__core-14877", model: gpt-6.1-sol }
recheck-when: Harbor upgrades past 0.22.0, Harbor's copilot_cli agent gains a credit cap or a way to turn off built-in MCP servers, or a rebench task sets [verifier] environment_mode
---

# Suss adds a capped Copilot CLI, a fresh-sandbox grade and a paired interval to Harbor, and duplicates much of the rest

## Intent

- What each part of suss does that Harbor 0.22.0 does not.
- Whether Harbor's built-in `copilot-cli` agent can run the `copilot-gpt` arm,
  and what it records.
- Whether Harbor records a Copilot CLI trial's AI credits.
- Whether `suss compare`'s paired interval changes a conclusion that each run's
  own mean and spread would reach.
- What suss should stop doing.

## Decisions

- **Suss is for three things Harbor does not do: run Copilot CLI under a
  credit cap with its built-in GitHub and web tools off, grade the patch alone
  in a fresh sandbox, and compare two arms task by task.** Everything else it
  does is either in Harbor already or would be in Harbor with a small change.
  The map below says which, part by part. Neither suss nor Harbor closes
  GitHub to a Copilot CLI trial: the agent's shell can still reach GitHub's
  MCP server on the allowed model host with the arm's token, which only a gate
  outside the sandbox stops, and that gate is #204.
- **Keep `adapters/copilot-cli/`. Harbor's agent cannot stand in for it.**
  Harbor's agent takes the model, the effort and the version, and the job takes
  the hosts. It has no credit cap, and it leaves Copilot CLI's built-in GitHub
  MCP server on. In the check, GPT-6.1 Sol used that server to download a file
  from GitHub, through the allowed model host, while the agent phase was
  limited to the arm's two hosts. Copilot CLI had 39 tools under Harbor's
  agent and 19 under the adapter. The adapter shuts the tools the model is
  offered, not the host, so it narrows the leak rather than closing it.
- **Harbor records no AI credits, and for a GPT model no tokens either.** Its
  `copilot_cli` agent skips every `session.*` event, and
  `session.usage_checkpoint` is the only event that holds the credits and the
  token counts. The trial's `result.json` had `n_output_tokens` 0,
  `n_input_tokens` null and `cost_usd` null. The stream held 19.53 credits and
  440,002 input tokens. Suss does not do better in the record: `suss.json` holds
  no credits, and SussAgent leaves Harbor's `agent_result` empty. The
  leaderboard's credits are read from `run.log` by hand.
- **`suss compare`'s paired interval changed no conclusion on these runs.**
  For both pairs, the unpaired difference interval and the paired one both
  include zero, so either way the difference cannot be told from none. Neither
  shows the arms are equal: the paired intervals still allow about ten points
  either way. The paired interval is narrower: 18 points for `copilot-gpt − copilot-sonnet`,
  where the unpaired one is 25, and 11 for `hve-sol − copilot-gpt`, where the
  unpaired one is 26. That precision would decide a difference of about 6 to
  12 points, which the unpaired interval cannot tell from zero. No such
  difference occurred here. Harbor shows no spread at all.
- **Stop keeping three things Harbor already has.** The second regrade job
  could give way to Harbor's separate verifier and collect hooks. Harbor's
  per-trial `task_checksum` covers the lane digest. And `adapters/claude-code/`
  duplicates Harbor's `claude-code` agent, so an arm should be able to name a
  Harbor agent instead. Each has an issue, and so does each change worth
  contributing to Harbor. The arms in `suss.toml` mostly duplicate a Harbor job
  config too, but they hold the baseline and drive suss's checks before a run,
  so they stay.

## Needs your input

- **Whether to replace the regrade with Harbor's separate verifier.** It needs
  every lane task to take `[verifier] environment_mode = "separate"`, a
  `tests/Dockerfile`, a collect hook writing the patch to `/logs/artifacts/`,
  and a `test.sh` that applies it and resets the tests. That is a rewrite of
  each task, which is what suss avoids now. In return the agent job grades
  itself, `harbor jobs regrade` re-grades any job, and Hub shows the real
  reward. I recommend trying it on one rebench task before deciding.
- **Whether to contribute the Copilot CLI changes to Harbor, and then drop the
  adapter.** If Harbor's agent took a credit cap, `--disable-builtin-mcps`,
  `--excluded-tools`, `--no-auto-update` and a token kept from `GH_TOKEN`, and
  read the usage checkpoint, `adapters/copilot-cli/` would duplicate it. I
  recommend contributing them. They are small, and they would help every
  Harbor user who runs Copilot CLI on a benchmark.

## Reference

### The map

| Part | Suss | Harbor 0.22.0's nearest | Adds |
|---|---|---|---|
| `adapters/copilot-cli/` | `install.sh` downloads the release's standalone binary and checks it against `SHA256SUMS.txt`. `run.sh` passes `--model`, `--reasoning-effort` and `--max-ai-credits`, turns off built-in MCP servers, auto-update and telemetry, excludes `web_fetch` and `web_search`, and gives Copilot CLI an empty `COPILOT_HOME` and `GH_CONFIG_DIR` with only `COPILOT_GITHUB_TOKEN`. | `agents/installed/copilot_cli.py`, `CopilotCli`. `install` pipes `https://gh.io/copilot-install` into `bash` with `VERSION`. `run` passes `--model`, `--effort` from `--ak reasoning_effort`, and `--yolo`. The token is the first of `COPILOT_GITHUB_TOKEN`, `GH_TOKEN` and `GITHUB_TOKEN` in Harbor's own environment. | Yes: the credit cap, the built-in GitHub MCP server and web tools turned off, the checked binary with no `curl` or `bash` needed, and a token no fallback can replace. |
| `adapters/claude-code/` | Installs Claude Code at `version`; runs it with `model`, `effort` and `max_budget_usd`, and `WebSearch` and `WebFetch` denied. | `agents/installed/claude_code.py`, `ClaudeCode`, whose `CLI_FLAGS` include `reasoning_effort`, `max_budget_usd` and `disallowed_tools`, and which takes `version`. | Little. It exists because an arm can only name an adapter. |
| `adapters/copilot-cli-hve/` | Copilot CLI with hve-core's RPI Agent, from `--plugin-dir` and `--agent`. | `CopilotCli._build_register_skills_command`, which copies a skills directory into `~/.copilot/`. No plugin or agent option. | Yes. |
| `SussAgent` and the manifest | `agent.py` runs any harness from a directory and two shell commands, records the starting tree, captures the whole change as `model.patch`, and on a timeout kills every process `run` started, then still captures. | `agents/installed/base.py`, `BaseInstalledAgent`: a Python class per agent, with `install` and `run`. No patch capture. | Yes: a harness needs no Python and no Harbor code, and every trial leaves a patch. |
| Arms and baselines in `suss.toml` | Each arm is an adapter path and params. `baseline` names the arm it is compared with. `max_seconds` is per arm and per lane. | `models/job/config.py`, `JobConfig`, kept in git as a file `harbor run -c` reads. Its `agents` hold name, `model_name`, `kwargs`, `env` and `extra_allowed_hosts`; `agent_timeout_multiplier` caps time. | In part: `baseline`, and suss's checks before a run, of capacity, keys and `max_seconds`. The rest is a job config in another shape. |
| The regrade, `ReplayAgent` | `replay.py`. A second Harbor job applies each trial's `model.patch` in a fresh sandbox, puts back every test file, then runs the verifier. | `harbor run` runs the verifier in the agent's own sandbox, as the check did, unless the task sets `[verifier] environment_mode = "separate"` (`models/task/verifier_mode.py`). Then `Trial._run_separate_verifier` starts a fresh sandbox, uploads the trial's artifacts and grades there. `[[verifier.collect]]` commands can write the artifacts after the agent phase, and `harbor jobs regrade` (`trial/regrade.py`, `RegradeTrial`) re-grades a recorded job. | In part. Harbor has the mechanism. Suss uses it without rewriting each task, and adds the test-file reset. Rebench's `test.sh` already checks out the test files it runs. |
| Lanes, `lanes.py` | Copies the download, adds `[agent] network_mode = "no-network"` where `[agent]` sets none, pins senior's litellm, leaves out three rebench tasks, and digests the lane. | `harbor download`, `DatasetConfig` with `exclude_task_names`, and `task_checksum` in each trial's `result.json`. | In part: the network rewrite and the litellm pin. Harbor ignores `--allow-agent-host` on a public network, so the rewrite is needed. The exclusion and the digest duplicate Harbor. |
| The secrets split | The agent phase gets only the `.env` variables the manifest names. The regrade's Harbor gets only those its tasks fill, from `.env`. Neither job takes suss's own environment. | `--ae` for the agent, `--ve` for the verifier, `--env-file` for both. `TrialConfig` stores env values templatized. | Little: reading the names from the manifest and the tasks. `--ae` and `--ve` would put the values on the command line. |
| The trial record, `suss.json` | `arm`, `harness`, `params`, `grader`, `base`, `status`, `exit_codes`, `run_seconds`, `patch_bytes`. No credits. | `config.json` holds the agent, model and kwargs. `result.json` holds `agent_info`, `agent_execution` start and end, `exception_info`, and `task_checksum`. `agent_result` holds tokens and `cost_usd`, which SussAgent leaves null. | In part: `base`, `patch_bytes`, the senior grader's models, and `timed-out` told apart from `harness-failed`. Timing, params and harness name duplicate Harbor. |
| `suss compare` | Pairs each trial with its regrade, drops invalid trials, refuses mixed graders, and gives each arm's difference from its baseline with a 95% interval from a bootstrap that resamples tasks. | `metrics/`: `mean`, `max`, `min`, `sum` and a `uv-script` that receives rewards without task names. `job.py` adds `pass_at_k`. The viewer's `/api/compare` grid gives each job's average reward per task and overall. Hub's leaderboards rank by a column of `metrics`. None gives a spread. | Yes: the paired interval, and the checks that keep a broken regrade from counting. |
| `JudgeAgent` | Runs senior's own rubric and taste prompts through Copilot CLI, read-only, in the task's container after the patch is applied, under a credit budget. | `harbor analyze` runs any agent over a trial directory with a rubric, by default `reward_hacking` and `task_specification` on Claude Code and `claude-haiku-4-5`. | Yes: senior's judges, in the repository with the patch, on a Copilot plan. |

### Harbor's `copilot-cli` agent on one rebench task

Run from `suss-runs/` on Modal, with `COPILOT_GITHUB_TOKEN` exported from
`.env` alone, on a copy of the lane's `vuejs__core-14877`:

```bash
harbor run -p results/audit/tasks/vuejs__core-14877 -e modal -n 1 -k 1 \
  -a copilot-cli -m gpt-6.1-sol \
  --ak version=1.0.105 --ak reasoning_effort=high \
  --allow-agent-host api.github.com \
  --allow-agent-host api.enterprise.githubcopilot.com \
  --agent-timeout-multiplier 0.12 \
  --jobs-dir results/audit --job-name harbor-copilot-cli-1
```

The timeout multiplier stood in for the credit cap Harbor's agent lacks: 360
seconds, about 40 credits at the arm's usual rate.

| | Harbor's `copilot-cli` agent | Suss, `copilot-gpt` `20261104T022938Z-1` |
|---|---|---|
| Model, effort | `gpt-6.1-sol`, `high`, as the usage checkpoint confirms | the same |
| Version | 1.0.105, through `gh.io/copilot-install` | 1.0.105, the checked release binary |
| Credit cap | none possible | `--max-ai-credits 1000` |
| Allowed hosts | the same two, as the job's `extra_allowed_hosts` | the same two, from the manifest |
| Built-in MCP servers | `github-mcp-server` connected, `configSource` `builtin`; one call to `github-mcp-server-get_file_contents` downloaded `tests/teleport-unmount.test.js` from the issue's reproduction repository | off |
| Tools offered | 39 | 19 |
| Completed | yes, exit 0, session 158 s | yes, `ok`, `run_seconds` 104 |
| Reward | 1.0, graded in the agent's own sandbox | 1.0, graded by the regrade in a fresh one |
| Credits | 19.53, in the stream only | 15.35, in `run.log` only |
| Tokens in `result.json` | `n_output_tokens` 0, the rest null | all null |

The usage checkpoint held `inputTokens` 440,002, `outputTokens` 4,489,
`cacheReadTokens` 395,564, `cacheWriteTokens` 43,973 and `reasoningTokens`
1,910. Harbor kept the `result` event whole in `trajectory.json`, under
`final_metrics.extra.copilot_result`. That event has `premiumRequests`, the
API and session durations and the lines changed, but no credits and no
tokens.

The verifier started 7 seconds after the agent phase ended, in the same
sandbox: the task sets no `[verifier] environment_mode`, so
`resolve_task_verifier_mode` returns `shared`. No sandbox was left on Modal.

### The comparison on the existing rebench runs

108 tasks, one trial each, every trial valid. Each run's own interval is a
Wilson 95% interval. The unpaired interval treats the two runs as independent
samples. The paired one is `suss compare`'s.

| Arm | Resolved | Own 95% interval |
|---|---:|---|
| `copilot-sonnet` | 71, 66% | [56%, 74%] |
| `copilot-gpt` | 70, 65% | [55%, 73%] |
| `hve-sol` | 65, 60% | [51%, 69%] |

| Difference | Estimate | Unpaired 95% | `suss compare` | Resolved by both, by neither, by the arm alone, by the baseline alone |
|---|---:|---|---|---|
| `copilot-gpt − copilot-sonnet` | −1% | [−14%, +12%] | [−10%, +8%] | 57, 24, 13, 14 |
| `hve-sol − copilot-gpt` | −5% | [−18%, +8%] | [−10%, +1%] | 63, 36, 2, 7 |

Pairing narrows `hve-sol − copilot-gpt` the most because the two arms
disagreed on only 9 tasks.

## Limits

- One task, one trial, for Harbor's agent. That the model reached for GitHub
  once is observed. How often it does across the lane is not.
- The file the agent fetched came from the issue's reproduction repository,
  not the upstream fix. The check shows the channel is open, not that a fix
  leaked through it.
- Harbor's separate verifier was read in the code, not run. Whether a rebench
  task's `test.sh` works there unchanged is not known.
- Hub's server side was not read. The client in `hub/` receives `metrics` as
  each leaderboard defines them, and the viewer bundle shows no spread. A
  spread computed by the server and shown only on the web would not appear in
  either.
- The Modal spend was not read from Modal's bill. One 1-CPU, 4 GiB sandbox ran
  for about 3.5 minutes.
