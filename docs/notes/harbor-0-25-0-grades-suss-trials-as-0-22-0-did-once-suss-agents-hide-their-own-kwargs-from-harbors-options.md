---
settles: "§ 2 — the Harbor version suss pins, and what an upgrade of it must pass; § 4 — what SussMixin gives Harbor's option parse, the models SussCopilotCli takes, and the command it runs; § 6 — the options suss run refuses after --, and the .env.local it refuses beside suss.toml"
issue: [227, 223]
recorded: 2026-11-08
versions: { harbor: "0.22.0 → 0.25.0", copilot-cli: 1.0.105, modal: 1.6.3, python-dotenv: unchanged, litellm: unchanged, environment: modal, lanes: "rebench ArcadeData__arcadedb-4281; senior paperless-ngx-perf-workflow-queries" }
recheck-when: suss upgrades Harbor past 0.25.0
---

# Harbor 0.25.0 grades suss's trials as 0.22.0 did, once suss's agents hide their own kwargs from Harbor's options

## Intent

- Which changes from Harbor 0.22.0 to 0.25.0 touch what suss imports,
  subclasses or runs, and what must suss change for each?
- Does a `rebench` check and a `senior` check, judged in the verifier, give the
  reward each gave on 0.22.0?
- Does Harbor 0.25.0 still load a `.env` from above its install into the agent
  job's environment (#223)?
- What is new in 0.25.0 that suss could use?

## Decisions

- **Pin `harbor==0.25.0`.** The `rebench` check gave reward 1.0 on
  `ArcadeData__arcadedb-4281`, as on 0.22.0, and the `senior` check gave 1.0
  and correctness 1.0 on `paperless-ngx-perf-workflow-queries`, as judge-1 did
  on 0.22.0, with rubric 1.0 again. The lock changed only `harbor` and a new `semver`; `litellm`,
  `modal` and `python-dotenv` stayed where they were.
- **`SussMixin.parse_options` passes Harbor only the kwargs its agent's options
  name.** 0.25.0 validates every `--ak` against a pydantic options model with
  `extra="forbid"`, in the job's preflight and again in the constructor. On
  0.22.0's code every `suss run` would fail before a trial starts with
  `Unknown option 'arm'`, and `SussCopilotCliHve` also on `hve_core_commit`.
- **`SussMixin.parse_options` gives each option it is not passed its default.**
  0.25.0 fills an absent option from `os.environ` directly, not through
  `_env_sources`, so a `COPILOT_CLI_EFFORT` in suss's environment would have
  set `--effort` on an arm that sets no `reasoning_effort`.
- **`SussCopilotCli` refuses a model that names a provider.** 0.25.0 runs
  `anthropic/<model>` or `openai/<model>` through Copilot CLI's own-key mode,
  with the provider's key taken from Harbor's environment, and passes
  `github-copilot/<model>` to `--model` whole. 0.22.0 dropped the prefix. A
  bare name, which every arm uses, still goes to Copilot's own service.
- **Keep suss's `--excluded-tools web_fetch web_search`.** 0.25.0 adds
  `--deny-tool web_fetch --deny-tool web_search` itself whenever the agent
  phase's network is not public, which on suss's lanes is always. Both ran on
  both checks.
- **Refuse the new `harbor run` options that change what a job runs**:
  `--effort`, `--base-url`, `--api-key-name`, `--api-format`, `--sm`,
  `--allow-verifier-host` and `--diff`. `--share-org` is now `--share`, and
  `--init` is gone.
- **Refuse a run when `.env.local` is beside `suss.toml`.** `harbor run` 0.25.0
  loads `./.env.local` with `override=False`, which would refill any variable
  suss withholds from the agent job.
- **Leave #223 as it is.** `harbor/registry/client/harbor/config.py` still
  runs `load_dotenv()` on import, unchanged, but a `harbor run -p <local dir>`
  job never imports it, on 0.22.0 or 0.25.0.
- **File the two features worth having**: #236, building the
  separate verifier from `environment/` rather than a copy in `tests/`, and
  #237, filling `agent_result.model_usage` from Copilot CLI's
  per-model checkpoint.
- **Track the one Modal V2 failure as #238, and take the upgrade anyway.** In
  the `senior` check the taste session's exec came back 15 minutes after its
  command exited, with `exec not found`, so taste went unscored. A regrade of
  the same job scored all four sessions, and no other exec in either check
  failed.

## Needs your input

Nothing.

## Reference

### Each change that touches suss

| Harbor 0.25.0 | What suss did |
|---|---|
| Agent kwargs are an options model, `CopilotCliOptions(InstalledAgentOptions)`, `extra="forbid"`, validated by `BaseAgent.parse_options` in `AgentFactory.run_preflight` and in `BaseAgent.__init__`. `BaseInstalledAgent.__init__` no longer pops `CLI_FLAGS` kwargs. | `SussMixin.parse_options` drops `SUSS_KWARGS` and the class's own `KWARGS` that the model does not name. |
| `apply_env_fallbacks` fills an absent option from the agent's `extra_env`, then `os.environ`. An explicit value, `None` included, stops it. | `SussMixin.parse_options` sets every absent optional field to its default. |
| `CopilotCli.run` takes `--model` from `_parse_model_name(model)[1]`, and `resolve_model_connection_config` routes a name with a provider slug to `COPILOT_PROVIDER_*` with that provider's key. `parse_model_name("claude-sonnet-5.5")` is `("anthropic", "claude-sonnet-5.5")`, still a native name; `parse_model_name("github-copilot/gpt-5")` is `(None, "github-copilot/gpt-5")`. | `SussCopilotCli` raises `ConfigError` for a model holding `/`. |
| `set_web_tools_disabled` sets `disable_web_search=True` under a restricted agent phase; `CopilotCli` then adds `--deny-tool web_fetch --deny-tool web_search`, and logs `automatically applied disable_web_search=True`. | Spec's command line updated; suss's own flags kept. |
| `Trial._network_plan` adds the inference host to a restricted agent phase, unless the trial has `extra_allowed_hosts`. | Nothing: suss always passes `--allow-agent-host`. A `SussMixin` class with empty `ALLOWED_HOSTS` would get Copilot's inferred `api.githubcopilot.com` added. |
| `resolve_agent_env_baseline` and `resolve_agent_phase_policy` are gone; `resolve_trial_network_plan` takes a `VerifierConfig` too. | `tests/test_suss_run.py` reads the agent phase from `resolve_trial_network_plan(...).agent_phase`. |
| `ModalEnvironment` sets `MODAL_SANDBOX_V2=1` on every sandbox; `modal_sandbox_v2` is gone. | Nothing but #238. `SUSS_MODAL_TESTS=1` passed all three tests, and a sandbox set to the allowlist `api.github.com`, `api.enterprise.githubcopilot.com` reached both and got `curl: (35) Connection reset by peer` from `pypi.org`, `www.google.com` and `1.1.1.1`, at once and 20 s later; restored, it reached all five. |
| The separate verifier is built from `tests/` when it holds a `Dockerfile`, with `skip_tests_upload=True`, as on 0.22.0; otherwise from `environment/`, with `tests/` uploaded. | Nothing; lanes still copy `environment/` into `tests/`. #236 would drop the copy. |
| `Verifier.verify` deletes `reward.txt` and `reward.json` before `test.sh`, and refuses a non-finite or non-numeric reward. `Task` refuses a single-step task with no `tests/test.sh`. | `tests/test_compare.py`'s fixture task gains a `test.sh`. Every task in both downloads has one. |
| `harbor run`: `--share-org` → `--share` (same `share_org` parameter), `--init` removed; new `--diff`, `--base-url`, `--api-key-name`, `--api-format`, `--sm`, `--effort`, `--allow-verifier-host`, `--stream`, `--skip-credential-checks`, `--use-static-ip`, `--no-secrets`. | The first seven of the new ones are refused. The last four change no grading, and the last two need `--launch`, which is refused. |
| `cli/jobs.py` runs `load_dotenv(Path.cwd() / ".env.local", override=False)` when that file exists. | `suss run` refuses it. |
| `--agent-import-path`, `--verifier-import-path` and `--environment-import-path` still warn as deprecated, and are not removed. | Nothing: suss passes `-a module:Class` and `--verifier module:Class`, and refuses the three. |
| `cpus` and `--override-cpus` take a fraction. | `suss run`'s Docker count still takes whole numbers; its error now says suss refuses the value, not Harbor. |
| `DockerEnvironment` bind-mounts `/logs/agent`, `/logs/verifier` and `/logs/artifacts` only with `--ek auto_mounting=true`, and downloads them otherwise, as Modal always did. | Nothing; the container tests pass. |
| The egress sidecar also allows DNS to the `nameserver`s in `/etc/resolv.conf`, for Podman. | Nothing; on Docker Desktop that is `127.0.0.11`, which `fib daddr type local` already allowed (unverified). |
| `result.json` keeps `task_name`, `verifier_result.rewards` and `exception_info.exception_type`, which `suss compare` reads, and `agent_result` gains `model_usage`, null for Copilot CLI. `ExceptionInfo.policy` classifies failures in code, not in the record. | Nothing. |
| Senior tasks' `[environment] memory` and `storage` now warn `DeprecationWarning: The 'memory' field is deprecated. Use 'memory_mb' instead.` | Nothing; `lanes.needs` still reads `memory` as Harbor does. |
| `ClaudeCode` has an options model too. | Nothing builds on it; a `SussMixin` class on it would take the same `parse_options`. |

### #223

`.venv/bin/python -I` running `harbor.cli.main.app(["run", "-p", <one local
task>, "-a", "oracle", "-e", "docker", ...], standalone_mode=False)` in-process,
then checking `sys.modules`, printed
`harbor.registry.client.harbor.config imported by harbor run -p: False` on both
versions. The job ran and gave reward 1.0. Only
`harbor.registry.client.factory` imports that module, for a registry or repo
dataset, which suss never passes.

### The checks

| | 0.22.0 | 0.25.0 |
|---|---|---|
| `rebench` job | `checks/rebench/copilot-sonnet/20261108T020957Z-1` | `checks/rebench/copilot-sonnet/20261108T032436Z-1` |
| reward | 1.0 | 1.0 |
| `suss.json` status, patch bytes, `ai_credits` | ok, 2927, 20.6729 | ok, 5197, 22.8836 |
| `replay.json` `apply_rc`, `tests_reset` | 0, none | 0, removed `engine/src/test/java/com/arcadedb/LogFormatterTest.java` |
| patch sha256, agent = artifacts = `replay.json` | `c1af7c10…` | `420b07c7…` |
| `github-mcp-server` in `session.mcp_servers_loaded` | disabled | disabled |
| `trajectory.json` | ATIF | ATIF-v1.6, 14 steps |
| `senior` job | `results/senior-verifier-check/judge-1` | `check-227-senior/checks/senior/copilot-gpt/20261108T032527Z-1` |
| reward, correctness | 1.0, 1.0 | 1.0, 1.0 |
| `suss.json` status, patch bytes, `ai_credits` | source job's own agent | ok, 16201, 38.9198 |
| `replay.json` `apply_rc` | 0 | 0, restored `src/documents/tests/test_api_workflows.py` |
| patch sha256, agent = artifacts = `replay.json` | | `c5fbf2a1…` |
| judge rubric, `rubric_score` | ok, 1.0 | ok, 1.0 |
| judge taste, practice alignment / relative taste | ok, 5 / 5 | `failed:error`, `ConflictError: … exec not found` (#238) |
| regrade of the 0.25.0 job, `regrade/judge-2` | | reward 1.0; rubric 1.0; taste ok, 4 / 5; 19.5623 credits |
| verifier time | 4 min 44 s | 19 min 41 s; 4 min 12 s in the regrade |

`harbor jobs regrade` on 0.25.0 imports the source job's agent class, so the
0.22.0 source job, whose `suss.agent:SussAgent` no longer exists, could not be
regraded: `Failed to import module 'suss.agent'`.

The `rebench` job took 4 min 17 s, the agent phase 2 min 22 s. Both 0.25.0
checks were run with `suss run <arm> <lane> --check`, the `senior` one from a
project whose `suss.toml` adds `[lanes.senior.judge] credit_budget = 100` and
points `senior_swe_bench` at a download holding only that task.

## Limits

- One trial on each lane. Each 0.25.0 agent wrote its own patch, so the
  rewards are compared, not the patches or the taste scores.
- What held the taste session's exec open was not found.
- Docker was exercised only by the container tests and one oracle job, not by
  a `suss run` on Docker, and the egress sidecar's enforcement on Docker
  Desktop was not re-run.
- `SussCopilotCliHve` was checked by the tests and Harbor's preflight, not by a
  live trial.
- IPv6 and UDP were not probed under the Modal allowlist.
