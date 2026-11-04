---
settles: "§ 3 — what a senior regrade's verifier writes, how an invalid trial and a judge failure look, and that ReplayAgent regrades a senior patch unchanged; § 5 — the senior tasks' agent timeouts and allowed_hosts; § 6 — the keys and options a senior regrade needs, and what one task's trial and regrade cost"
issue: [154]
recorded: 2026-11-04
versions: { senior-swe-bench: "v2026.06 (50 tasks, digest 0e0174e2…)", harbor: 0.22.0, claude-code: 2.1.293, litellm: "1.105.7 works, 1.106.0 to 1.108.0 fail", docker-desktop: "engine 29.9.2, 10 CPUs, 7.7 GiB", host: "macOS, arm64" }
recheck-when: Snorkel publishes a new snapshot or changes test.sh, litellm releases a version past 1.108.0, or an arm runs a design task
---

# A senior regrade's judges cost under a dollar and fail on litellm 1.106 and later

## Intent

- Which keys and `SSB_OVERRIDE_*` settings does the verifier read, which judge and validation-agent models does it use by default, and how does each override change them?
- Where does the verifier write the reward, the rubric score and the taste score, and what does it write for an invalid trial?
- What are each task's agent timeout and `[agent] allowed_hosts`, and are they the same across the 50 tasks?
- On one task graded by its default judges, what do the trial and its regrade cost and take, and how far do the judges' scores move when the same patch is regraded?
- Does a suss trial's patch regrade under `ReplayAgent` unchanged?

## Decisions

- **The lane must pin litellm below 1.106 in each task's `test.sh`, or the
  judges never run.** `test.sh` installs `litellm>=1.0,<2.0`, which resolves
  today to 1.108.0. Every litellm release from 1.106.0 (18 October 2026) raises
  `ModuleNotFoundError: No module named 'mcp'` on a call with tools, which is
  every judge call. The trial still gets a reward, from the tests, so nothing
  fails. The one change `litellm>=1.0,<1.106` made all five later regrades judge
  normally. All 50 tasks share the same `test.sh`.
- **The default judges need only `ANTHROPIC_API_KEY`.** The rubric and taste
  judges run `anthropic/claude-sonnet-4-6`, the patch classifier
  `anthropic/claude-haiku-4-5-20251001`, and the validation agent
  mini-swe-agent with `anthropic/claude-sonnet-4-6`. With no `PORTKEY_API_KEY`,
  litellm calls Anthropic directly.
- **The regrade must give Harbor the keys and `-y`.** Harbor resolves each task's
  `[verifier.env]`, such as `ANTHROPIC_API_KEY = "${ANTHROPIC_API_KEY:-}"`, from
  its own process environment. Before any job, even one run with
  `--disable-verification`, it asks to proceed. Without a terminal it reads
  end-of-file and exits 1 with `Aborted.`. A missing key does not stop the
  regrade. It makes each judge record `skipped:no_api_key`.
- **Read the judges' scores from the regrade's `result.json`.** The verifier
  writes a flat `reward.json`. Harbor prefers that file to `reward.txt` and
  copies every key into `verifier_result.rewards`, the judges' scores included.
- **Tell three kinds of zero apart by these fields, not by `reward`:**

  | Case | `reward.txt` | `verifier_result.rewards` | `exception_info` |
  |---|---|---|---|
  | tests failed | `0` | `reward` 0.0, `verifier_score` below 1 | none |
  | judges failed or skipped | `0` or `1`, from the tests | `rubric_judge_ok` 0 or `taste_judge_ok` 0, no `rubric_score` or `taste_*` | none |
  | invalid trial | empty | `null` | `RewardFileEmptyError` |

  Harbor's own summary counts the invalid trial as 0 in its `Mean`.
- **The lane's `max_seconds` must be under 7200.** That is the agent timeout of
  46 tasks, and the shortest. The other four allow 14400, which a cap for the
  whole lane cannot use, since suss must stop `run` before Harbor's timeout
  stops the shorter tasks.
- **Every task allows the same 32 hosts in its agent phase, and 19 add a few
  more.** The extra hosts depend on the repository.
- **One trial of `paperless-ngx-perf-workflow-queries` cost $0.98 and took 8.5
  minutes. Each judged regrade cost $0.78 to $0.97 and took 2.5 to 4.7
  minutes.** The trial ran a claude-code arm on `claude-sonnet-4-6`.
- **The rubric score did not move, and the taste scores moved by one point at
  most.** Over five judged regrades of the same patch, `rubric_score` was 1.0
  every time, `taste_relative_taste` 3.0 every time, and
  `taste_practice_alignment` 4.0 four times and 3.0 once.
- **`ReplayAgent` regrades a senior patch unchanged.** The patch applied with
  `apply_rc` 0 in all six regrades. The verifier graded the same lines the trial
  wrote, and the reward was 1.0 each time.

## Needs your input

- **Whether suss pins litellm in its lane copy or waits for Snorkel.** I
  recommend the lane copy rewrite the one line in `test.sh` to
  `litellm>=1.0,<1.106`, and the trial's `grader` record it. The alternative,
  installing `mcp` beside litellm, was not tried. Either way the lane changes
  the task's grading environment, which the specification does not yet allow
  for. Until then, check `rubric_judge_ok` and `taste_judge_ok` in every senior
  regrade.
- **What the validation agent costs on a design task.** It was not run here.
  The 25 design tasks run it, with `cost_limit` 0, up to 200 turns and a retry,
  so its cost per regrade has no bound in the code and cannot be estimated from
  it under the $20 this spike had. Filed as #166, which asks for a budget.
- **The blog describes a judge panel the snapshot does not run.** Snorkel's
  blog says rubric and taste scores come from Claude Sonnet 4.6 and GPT-5.4,
  averaged (updated 30 July 2026). The v2026.06 verifier calls one model,
  `judge_model()`, for both. I recommend suss report what the snapshot's code
  runs, and record the models in `grader`.

## Reference

### Keys and overrides

Every task's `[verifier.env]` names the same variables, each defaulting to empty:
`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `PORTKEY_API_KEY`,
`SSB_OVERRIDE_ALL_JUDGE_MODEL`, `SSB_OVERRIDE_CLASSIFIER_MODEL`,
`SSB_OVERRIDE_VA_HARNESS`, `SSB_OVERRIDE_VA_MODEL`, `FORCE_VA_HARNESS`,
`FORCE_VA_MODEL` and `TIMEOUT_MULTIPLIER`, and a fixed `REPO_NAME`. They come
from `tests/ssb_lib/llm_utils.py` and `tests/run_validate.py`:

| Setting | Changes | Default |
|---|---|---|
| `SSB_OVERRIDE_ALL_JUDGE_MODEL` | the rubric judge, the taste judge and the validation-review judge, one model for all three | `anthropic/claude-sonnet-4-6` |
| `SSB_OVERRIDE_CLASSIFIER_MODEL` | the patch classifier, which drops test, doc and formatting files before the taste judge and the bloat ratio | `anthropic/claude-haiku-4-5-20251001` |
| `SSB_OVERRIDE_VA_HARNESS`, else `FORCE_VA_HARNESS` | the validation agent: `miniswe` (mini-swe-agent 2.3.0) or `claude_code`; anything else makes `test.sh` write an empty reward | `miniswe` |
| `SSB_OVERRIDE_VA_MODEL`, else `FORCE_VA_MODEL` | the validation agent's model, over the spec's `va_model`, which no task sets | `anthropic/claude-sonnet-4-6` |
| `PORTKEY_API_KEY` | routes every call through `https://api.portkey.ai/v1`, passing the model slug unchanged, so overrides then take Portkey's `@provider/model` form | unset: litellm calls the provider the slug names |
| `TIMEOUT_MULTIPLIER` | multiplies the validation agent's and its scripts' timeouts, when above 1 | `1` |

A model slug is litellm's, such as `openai/gpt-5.5`, which `OPENAI_API_KEY`
serves. A judge with no key at all records `skipped:no_api_key`.

### What the verifier writes

In `/logs/verifier/`, which Harbor keeps as the trial's `verifier/`:

- `reward.txt`: `1` or `0`, from the tests alone, and from the validation agent on a design task. The judges never change it.
- `reward.json`, from regrade 2:

  ```json
  {
    "reward": 1.0,
    "correctness": 1.0,
    "verifier_score": 1.0,
    "rubric_score": 1.0,
    "rubric_f2p_score": 1.0,
    "rubric_p2p_score": 1.0,
    "taste_patch_bloat": 1.984,
    "taste_practice_alignment": 4.0,
    "taste_relative_taste": 3.0,
    "rubric_judge_ok": 1,
    "taste_judge_ok": 1
  }
  ```

- `judge_output.json`: each rubric criterion as 0 or 1, the ten taste
  dimensions from 1 to 5, and `rubric_status` and `taste_status`, which are
  `ok`, `skipped:<why>` or `failed:<why>`, with `rubric_error` on a failure. Regrade 1's:

  ```json
  "rubric_status": "failed:api_error",
  "rubric_error": "explore/score: ModuleNotFoundError: No module named 'mcp'",
  "taste_status": "failed:api_error"
  ```

- `reward_details.json`: all of the above nested, with the rationales.
- `verifier_results.json`: the task's own tests, as `passed`, `total`,
  `all_pass` and `tests`. There is no `report.json`.
- `run_judge_rubric.stderr` and `run_judge_taste.stderr`: one line per judge turn, as
  `[rubric turn 0] tools=2`, followed by the files it read. An empty file means the judge made no call.

`rubric_score` is the mean of the criteria. Each taste score is the median of
its five dimensions. 13 of the 50 tasks have no rubric criteria, so their
rubric judge records `skipped:no_criteria` and `reward.json` has no
`rubric_score`.

An invalid trial is one whose tests crashed or ran none, or a design task whose
validation agent failed or broke the repository. The verifier then writes an
empty `reward.txt` and no `reward.json`. Its `aggregate` step, run in the task
image with no test results, wrote exactly that, and `reward_details.json` with
`"reward": null`. Harbor then raises, and `result.json` records:

```json
"verifier_result": null,
"exception_info": { "exception_type": "RewardFileEmptyError",
  "exception_message": "Reward file is empty at …/verifier/reward.txt" }
```

That run used a lane copy whose `test.sh` writes only the empty file, under
`-a oracle`.

### The tasks

Read from all 50 `task.toml` files:

| Field | Values |
|---|---|
| `[agent] timeout_sec` | 7200 in 46; 14400 in `harbor-add-agent-file-retention`, `harbor-add-multi-step-tasks`, `harbor-refactor-optional-sandbox-deps`, `teleport-fix-scoped-kube-clusters` |
| `[agent] network_mode` | `allowlist` in all 50 |
| `[environment] network_mode`, `[verifier] network_mode` | `public` in all 50 |
| `[verifier] timeout_sec` | 300 to 4800 |
| `[environment]` `cpus`/`memory` | `4`/`8G` in 37, `2`/`4G` in 8, `2`/`8G` in 5 |
| validation agent | in the 25 tasks whose `metadata.segment` is `design`, which have `tests/validate/validation_spec.toml` |

The 32 hosts every task allows are `api.portkey.ai`, `pypi.org`,
`*.pythonhosted.org`, `*.pypa.io`, `astral.sh`, `*.astral.sh`,
`pypi-mirror.modal.local`, `*.python.org`, `registry.npmjs.org`,
`www.npmjs.com`, `registry.yarnpkg.com`, `pnpm.io`, `get.pnpm.io`,
`nodejs.org`, `deb.nodesource.com`, `www.typescriptlang.org`, `sh.rustup.rs`,
`*.rust-lang.org`, `crates.io`, `*.crates.io`, `hex.pm`, `*.hex.pm`,
`elixir-lang.org`, `www.erlang.org`, `developer.mozilla.org`,
`archive.ubuntu.com`, `security.ubuntu.com`, `*.ubuntu.com`, `deb.debian.org`,
`*.debian.org`, `apt.postgresql.org` and `download.redis.io`. The rest add:

| Tasks | Also allow |
|---|---|
| the 8 `posthog-*` | `clickhouse.com`, `*.clickhouse.com`, `dl.min.io`, `docs.djangoproject.com` |
| the 4 `paperless-ngx-*` | `docs.djangoproject.com`, `docs.celeryq.dev` |
| the 4 `prefect-*` | `docs.sqlalchemy.org` |
| the 3 `plausible-*` | `clickhouse.com`, `*.clickhouse.com` |

Harbor merges each `--allow-agent-host` into a task's `[agent]` allowlist,
because the policy there is not `public`. The trial reached
`api.anthropic.com` that way.

The `Dockerfile`s of the 6 `gitea-*`, 2 `teleport-*` and 4 `turborepo-*` tasks
download amd64 toolchains by name. On an arm64 Docker host, their images build
for arm64 around an amd64 Go or Rust. None of them was built here.

### The run

From `jobs/senior/` in the worktree, holding `suss.toml`, `.env`, and
`lanes/senior-one/` with an unchanged copy of
`paperless-ngx-perf-workflow-queries`, an investigate task with no validation
agent:

```toml
swe_rebench = "/Users/jacy/Documents/Dev/tasks/senior/senior-swe-bench-v2026.06"
environment = "docker"
k = 1
concurrency = 1
max_seconds = 3600

[arms.claude-code]
harness = "../../adapters/claude-code"
params = { version = "2.1.293", model = "claude-sonnet-4-6", effort = "medium", max_budget_usd = 5 }
```

```bash
harbor run -y -p lanes/senior-one -e docker -n 1 -k 1 \
  -a suss.agent:SussAgent --ak arm=claude-code --ak config=suss.toml \
  --allow-agent-host api.anthropic.com --disable-verification \
  --jobs-dir results --job-name agent-1
```

Each regrade ran with `.env` exported into Harbor's environment, and
`lanes/senior-pinned/` in place of `lanes/senior-one/` from regrade 2 on:

```bash
harbor run -y -p lanes/senior-pinned -e docker -n 1 -k 1 \
  -a suss.replay:ReplayAgent -m replay --ak source_job=<run>/results/agent-1 \
  --ve ANTHROPIC_BASE_URL=http://host.docker.internal:8787 \
  --jobs-dir results --job-name agent-1-regrade-<n>
```

`ANTHROPIC_BASE_URL` sent the judges' calls through a proxy on the host. The
proxy passed each call to `api.anthropic.com` unchanged and logged its model
and the `usage` of its response. It logged 100 calls, every one with status
200, 20 inside each judged regrade's verifier window and none in regrade 1's.

### Cost and time

| | Took | Spend |
|---|---|---|
| Agent job | 8 min 29 s: image build and start 154 s, Claude Code install 31 s, `run` 299 s | $0.9756, Claude Code's `total_cost_usd` (`costBasis` `list`): 44 input, 13,516 output, 1,417,706 cache-read and 92,637 cache-write tokens |
| Regrade 1, litellm 1.108.0 | 84 s, verifier 35 s | none: no call reached the proxy |
| Regrades 2 to 6, pinned | 149 to 283 s, verifier 130 to 240 s | $0.7843 to $0.9676, mean $0.86 |

A judged regrade made 18 calls to `claude-sonnet-4-6`, with 238,320 to 297,829
input and 4,105 to 4,710 output tokens, and 2 to
`claude-haiku-4-5-20251001`, with 3,256 input and about 265 output tokens. The
judges cache nothing, so every turn resends the whole thread. The token counts
are measured. The dollars are those counts at list price, $3 and $15 per
million for Sonnet 4.6 and $1 and $5 for Haiku 4.5. Neither run used AI
credits, because both called the Anthropic API directly.

The whole spike spent about $5.28: $0.98 on the trial and $4.30 on five judged
regrades.

### The judges' scores across five regrades

| Regrade | `rubric_score` | `taste_practice_alignment` | `taste_relative_taste` | dimensions that differ from regrade 2 |
|---|---|---|---|---|
| 2 | 1.0 | 4.0 | 3.0 | |
| 3 | 1.0 | 4.0 | 3.0 | `pa_pattern_adherence` 3, `pa_library_usage` 4 |
| 4 | 1.0 | 3.0 | 3.0 | `pa_pattern_adherence` 3, `pa_library_usage` 4, `pa_abstraction_level` 3 |
| 5 | 1.0 | 4.0 | 3.0 | |
| 6 | 1.0 | 4.0 | 3.0 | `pa_pattern_adherence` 3 |

All four rubric criteria scored 1 in every regrade. `taste_patch_bloat` was
1.984 every time: the classifier dropped nothing, so agent and oracle SLOC
stayed 121 and 61. The five relative-taste dimensions never moved. The rubric
judge took 3 or 4 turns. The taste judge took 14 or 15 of its 15, and was then
made to emit.

### The regrade of the trial's patch

`agent/replay.json` in each regrade:

```json
"apply_rc": 0,
"apply_out": "$ git apply --verbose\nChecking patch src/documents/serialisers.py...\nChecking patch src/documents/views.py...\nApplied patch src/documents/serialisers.py cleanly.\nApplied patch src/documents/views.py cleanly.\n",
"tests_reset": { "restored": [], "removed": [] }
```

`test.sh` commits whatever the workspace holds and diffs it from
`/var/lib/devcontainer_base_ref`, so `verifier/agent.patch` is what the
judges read. Its added and removed lines are identical to the trial's
`model.patch`.

### Where this contradicts the specification

- § 6 says the agent timeout is "3000 seconds in every task". For `senior` it is 7200 or 14400.
- § 5 and § 6 have the lane add `[agent] network_mode = "no-network"`. Every senior task already has an `[agent]` table, with `network_mode = "allowlist"`.
- § 6 finds a regrade's test result in `verifier/report.json`, which senior's verifier does not write. It writes `verifier_results.json`.
- § 3's `grader` names the dataset with a constant, so this trial's `suss.json` reads `"dataset": "swe-rebench-07-2026"` over the senior digest.

## Limits

- One task, one trial and one patch. The patch passed its tests and every
  rubric criterion, so a patch that half-meets a rubric, where the judge has
  more to disagree with, was not regraded.
- The validation agent did not run, so its cost, its time and whether the
  litellm break reaches mini-swe-agent's own litellm are not known.
- The litellm pin is a change to the task's grading environment. Regrades 2 to
  6 graded with a `test.sh` that differs from Snorkel's in that one line.
- Docker was shared with another job building senior images throughout, so the
  times are upper bounds. The 154-second image build may have hit layers that
  job had cached.
- Only Docker was run. The judges reached the proxy with the verifier's
  `public` network, which Modal was not shown to allow.
- The judge models' dollar cost is computed from list prices. The Anthropic
  account's own record was not seen.
- Portkey routing, the OpenAI path and the `claude_code` validation harness
  were read from the code, not run.
