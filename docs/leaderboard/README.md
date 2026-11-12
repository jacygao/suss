# Suss leaderboard

Every harness run suss has records for, one trial per task in each run, a
section for each lane. The lanes measure different things, so their numbers never compare
across lanes: `rebench` grades by tests alone, and `senior` adds judges of code
quality.

The resolved rate is resolved trials over valid trials, with the ± half-width
of its 95% interval, and pass@k is the share of tasks that at least one of an
arm's k runs resolved, as `suss leaderboard --lane rebench` prints them.

## `rebench`

The lane is SWE-rebench's July 2026 snapshot, 108 tasks, each graded by its
tests in a verifier sandbox of its own. No trial was invalid.

| Arm | Harness | Model | Date | Runs | Resolved rate | pass@k | Timed out | Cost per run, USD at pay-as-you-go prices | Agent time per run, sum of trials |
|---|---|---|---|---:|---:|---:|---:|---:|---:|
| `claude-opus` | Claude Code 2.1.311 | claude-opus-5-5, effort high | 2026-11-09 | 1 | 69% | | 3 | $118.98 | 17.8 h |
| `copilot-gpt` | Copilot CLI 1.0.107 | gpt-6.1-sol, effort high | 2026-11-08 | 1 | 67% | | 0 | $41.86 | 11.3 h |
| `copilot-sonnet` | Copilot CLI 1.0.107 | claude-sonnet-5.5, effort high | 2026-11-09, 2026-11-10, 2026-11-10 | 3 | 64% ±8 | 74% @3 | 5 | $35.39 | 15.0 h |
| `copilot-fusion` | Copilot CLI 1.0.107 | Fusion, tier standard | 2026-11-08 | 1 | 62% | | 0 | $16.12 | 8.5 h |
| `copilot-opus` | Copilot CLI 1.0.107 | claude-opus-5.5, effort high | 2026-11-08 | 1 | 62% | | 1 | $117.52 | 15.1 h |
| `copilot-fusion-high` | Copilot CLI 1.0.107 | Fusion, tier high | 2026-11-08, 2026-11-11, 2026-11-12 | 3 | 62% | | 8 | $32.91 | 18.3 h |

| Arm | Job | Where it ran | Params | Median trial |
|---|---|---|---|---:|
| `claude-opus` | `20261109T002622Z-1` | Modal | version 2.1.311, effort high | 5.6 min |
| `copilot-gpt` | `20261108T112743Z-1` | Modal | version 1.0.107, effort high | 5.2 min |
| `copilot-sonnet` | `20261109T221503Z-1`, `20261110T074227Z-1`, `20261110T214213Z-1` | Docker on a PC | version 1.0.107, effort high | 3.9 min, 4.5 min, 4.9 min |
| `copilot-fusion` | `20261108T124036Z-1` | Modal | version 1.0.107 | 3.7 min |
| `copilot-opus` | `20261108T212444Z-1` | Modal | version 1.0.107, effort high | 5.7 min |
| `copilot-fusion-high` | `20261108T222220Z-1`, `20261111T035703Z-1`, `20261112T010208Z-1` | Modal, Docker on a PC, Docker on a PC | version 1.0.107, `fusion_tier` high | 4.3 min, 7.5 min, 6.5 min |

On the PC that holds `copilot-sonnet`'s runs and `copilot-fusion-high`'s two
on Docker, `suss leaderboard --lane rebench` prints:

```
arm                  runs  resolved rate  pass@k
copilot-sonnet          3         64% ±8  74% @3
copilot-fusion-high     2         63% ±9  67% @2
```

- **A margin is one arm's uncertainty, not a difference's.** `copilot-sonnet`'s
  rate over three runs has a margin of ±8 points. Two arms graded on the same
  tasks can differ by less than their margins and still be told apart, as
  `claude-opus` and `copilot-opus` are below, so overlapping margins do not
  settle which arm is ahead.
- **One run of an arm moves by a few points on its own.** `copilot-sonnet`
  resolved 72, 69 and 66 tasks in its three runs, and 23 of the 108 went
  differently across them (21%): 13 resolved in two runs, 10 in one. 57
  resolved in all three, and 28 in none. `copilot-fusion-high` resolved 64,
  70 and 66.
- **Each `copilot-sonnet` run resolved fewer than the one before, and took
  longer:** its median trial was 3.9, 4.5 and 4.9 min. The three ran within a
  day of each other, and three runs cannot tell chance from a change on
  Copilot's side.
- **Trials took longer on Docker on the PC than on Modal.** The PC ran 6
  trials at once, under WSL2 with 16 CPUs and 24.5 GiB for Docker. Without
  their timeouts, `copilot-fusion-high`'s Docker runs had median trials of 7.3
  and 6.4 min against 4.3 min on Modal, and 8 of its trials timed out on
  Docker, none on Modal.
- **The same model ran better in its own harness.** Opus 5.5 resolved 74 in
  Claude Code against 67 in Copilot CLI. Compared directly, `claude-opus -
  copilot-opus` is +6% [+1%, +12%]: 9 tasks only Claude Code resolved, 2 only
  Copilot CLI did.
- **Fusion picks the model and effort per task,** and refuses `--effort`. At
  tier standard it ran one model per task, at medium effort: GPT-6.1 Sol on
  73, GPT-6 Luna on 26, GPT-6.1 Terra on 7, and Claude Opus 5.5 on 2. Tier
  high runs HydraFusion, a solver followed by a review pass.
- **Cost per resolved task:** `copilot-fusion` $0.24, `copilot-fusion-high`
  $0.49, `copilot-sonnet` $0.51, `copilot-gpt` $0.58, `claude-opus` $1.61,
  `copilot-opus` $1.75.
- **Cost is a price, not what was paid.** A Copilot CLI run's is its AI
  credits at $0.01 each: 4,186, 1,612 and 11,752, `copilot-fusion-high`'s
  three 3,218, 3,428 and 3,228, and `copilot-sonnet`'s three 3,618, 3,560 and
  3,440. `claude-opus`'s is Anthropic's API price for its tokens; it ran on a
  Claude Max 5x plan, which it cost about 1.6 five-hour windows and nothing
  more.
- **Opus thought about as much as Sol at the same effort.** In Copilot CLI, the
  median trial's reasoning tokens were 3,725 for Opus against 3,513 for Sol. Opus
  wrote about 40% more output and read about 40% more context.
- **Some trials ran twice.** In `copilot-gpt`, 11 trials were cut off when the
  host slept, and 12 were scored 0 when their verifier could not download
  Python from GitHub to parse the test output. In `claude-opus`, 50 trials were
  stopped by the plan's 5-hour limit. On the PC, 53 trials of `copilot-sonnet`'s
  first run errored when the Docker engine stopped answering, the second time
  because its drive was full, and 2 of its second run and 1 of
  `copilot-fusion-high`'s first errored when Docker could not reach its
  credential helper. Each was run again with `harbor jobs resume`, and each
  task has one graded trial in each run. The 12 first attempts' saved test
  logs, parsed offline, give the same result as their reruns on all 12.
- `copilot-opus`'s timed-out trial reports no credits, and its cost is over the
  other 107 trials.

## `senior`

The lane is Senior SWE-Bench `v2026.06`, 50 tasks: 25 investigate tasks,
graded by their tests, and 25 design tasks, graded by senior's validation agent.
The validation agent runs only on API credit, so no design task has a reward
yet. Every patch is scored for quality by suss's judge: senior's rubric and
taste judges, run through Copilot CLI on `gemini-4-argon` at effort `high`.

### Correctness

| Arm | Harness | Model | Date | Investigate tasks | Runs | Resolved | Resolved share | Design tasks graded | AI credits | Agent time, sum of trials |
|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| `copilot-gpt` | `copilot-cli` | gpt-6.1-sol | 2026-11-05 | 25 | 1 | 8 | 32% | 0 of 25 | 4,739 (≈ $47) | 11.3 h |

### Code quality

| Arm | Judge | Rubric | Practice alignment | Relative taste | Patch bloat, median | Judging, AI credits |
|---|---|---:|---:|---:|---:|---:|
| `copilot-gpt` | `gemini-4-argon`, effort high | 0.66 (37) | 4.50 (50) | 3.58 (50) | 0.65 | 1,835 (≈ $18) |

`copilot-gpt`'s scores by segment:

| Segment | Rubric | Practice alignment | Relative taste | Patch bloat, median |
|---|---:|---:|---:|---:|
| investigate | 0.56 (16) | 4.56 (25) | 3.44 (25) | 0.81 |
| design | 0.74 (21) | 4.44 (25) | 3.72 (25) | 0.55 |

- **Rubric** is the mean share of a task's criteria met, from 0 to 1, over the
  tasks that have criteria; the count is in brackets. 13 tasks have none.
- **Practice alignment** and **relative taste** run from 1 to 5. Relative taste
  compares the patch with the task's reference fix.
- **Patch bloat** is the patch's lines of code over the reference fix's, after
  test and doc files are dropped.
- **These are suss's scores, not senior's published ones.** Copilot CLI runs
  the judges, so compare them only with other runs judged the same way.
- **Taste moves between runs.** Judging 5 patches a second time left every
  rubric criterion where it was, but moved 11 of 50 taste dimensions by one
  point. Treat a taste difference under about 0.5 as noise.

### The runs

| Arm | Job | Params | Regrade |
|---|---|---|---|
| `copilot-gpt` | `20261105T205511Z-1` | version 1.0.105, effort high, `max_ai_credits` 1000, `max_seconds` 5400 | `20261105T205511Z-1-regrade`, by `JudgeAgent` with no API key |

- The resolved share's 95% interval is about 17% to 52%, over 25 tasks.
- The agent job ran in two parts, after Modal's spend limit stopped the first,
  so it has no single elapsed time.
- 5 patches the main judge job's budget did not reach were judged in
  `20261105T205511Z-1-judge-fill` and moved into the regrade. The repeats, 183
  credits more, are in `20261105T205511Z-1-judge-repeat`.

## How to read the columns

- **Runs** is how many times the arm ran every task, each run a job of its own
  with one trial per task. The trial counts pool all of an arm's runs:
  **Resolved**, **Unresolved** and **Invalid** add up to its tasks times its
  runs, and **Timed out** and **Harness failed** count trials already in
  Unresolved.
- **Resolved rate** is resolved trials over valid trials, pooled over an
  arm's runs. The ± is half the width of its 95% interval, from a bootstrap
  that resamples tasks and keeps each task's runs together.
- **pass@k** is the share of an arm's tasks that at least one of its k runs
  resolved. With one run, it equals the resolved rate.
- **Unresolved** counts every valid trial that did not resolve, timed-out and
  harness-failed ones included.
- **Invalid** counts trials whose verifier wrote an empty reward. No share
  counts them.
- **Cost, USD at pay-as-you-go prices** is what a run would cost at its
  provider's own rates. For Copilot CLI it is the run's AI credits at $0.01
  each. For Claude Code it is `agent_result.cost_usd` in each trial's
  `result.json`, which Harbor works out from the trial's tokens at Anthropic's
  API prices. A run's is the sum over its trials, and **cost per run** is the
  mean over an arm's runs. Neither includes what the sandboxes cost.
- **AI credits** are Copilot CLI's own count. On `rebench`, a trial's credits
  are `usage.ai_credits` in its `agent/suss.json`; on the other lanes, they are
  its `totalNanoAiu` divided by 10⁹, the largest value in its `agent/run.log`.
  A run's are the sum over its trials. One credit is about $0.01. Every run here
  was on Modal but `copilot-sonnet`'s three on `rebench` and two of
  `copilot-fusion-high`'s, which ran on Docker on a PC. Modal's cost is
  recorded nowhere, so no figure includes it.
- **Agent time, sum of trials** is every trial's `run_seconds` added
  together. Modal bills each sandbox for its own time, so this is what tracks
  the run's Modal cost. **Agent time per run** is the mean over an arm's runs.
- **Elapsed** is how long the agent job took, from its first trial's start to
  its last trial's finish. Up to 32 trials ran at once. The regrade that
  follows is not included. `rebench` gives none, since some of its jobs ran in
  parts.
- **Date** is the UTC day in the job id, one for each run.
- **Median trial** is over a run's trials, one for each run.

## Retired lane: SWE-bench Pro

Suss ran these on SWE-bench Pro before replacing it with `rebench`. Sonnet 5.5
on plain Copilot CLI resolved 98% of `pro-go`, which leaves no room to tell
harnesses apart. Suss no longer runs or compares these lanes.

| Lane | Arm | Harness | Model | Date | Tasks | Resolved | Unresolved | Invalid | Timed out | Harness failed | Resolved share | AI credits | Agent time, sum of trials | Elapsed |
|---|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `pro-hard51-rerun113` | `copilot-sonnet` | `copilot-cli` | claude-sonnet-5.5 | 2026-11-03 | 10 | 10 | 0 | 0 | 0 | 0 | 100% | 245 (≈ $2.45) | 18.0 min | 5 min |
| `pro-go` | `copilot-sonnet` | `copilot-cli` | claude-sonnet-5.5 | 2026-11-03 | 256 | 252 | 4 | 0 | 0 | 0 | 98% | 3,562 (≈ $36) | 14.0 h | 0.7 h |
| `pro-hard51` | `copilot-sonnet` | `copilot-cli` | claude-sonnet-5.5 | 2026-11-03 | 51 | 35 | 16 | 0 | 0 | 10 | 69% | 1,055 (≈ $11) | 2.8 h | 0.3 h |

| Lane | Job | Params | Median trial |
|---|---|---|---:|
| `pro-go` | `20261103T032309Z-1` | version 1.0.105, effort high, no credit cap | 2.7 min |
| `pro-hard51` | `20261103T062913Z-1` | version 1.0.105, effort high, no credit cap | 3.5 min |
| `pro-hard51-rerun113` | `20261103T120000Z-1` | version 1.0.105, effort high, no credit cap | 1.2 min |

- All three carry the grader dataset `swe-bench-pro-v2` at commit `66f92766`.
- In `pro-hard51`, Copilot CLI never started on 10 trials, 6 ansible and 4
  qutebrowser, whose install log ends:

  ```
  copilot-cli adapter: the image has neither curl nor wget to download Copilot CLI with
  ```

  That was #113. Each scores as unresolved, so 69% is 35 of 51; the 41 trials
  that ran resolved 35. The credit total and agent time are over those 41.
- `pro-hard51-rerun113` ran those 10 tasks again, and Copilot CLI ran and
  resolved all 10. Its 100% is over 10 trials picked because they had failed,
  so it is no share of any lane.

## How this page was made

Run from the directory holding `suss.toml` and `results/`, with `<suss>` the
checkout of this repository. The `rebench` resolved rates, margins and pass@k
come from `suss leaderboard`, which ranks every arm under `results/rebench/`:

```bash
uv run --project <suss> suss leaderboard --lane rebench
```

The `rebench` counts, credits and times come from each trial's records, with
`<job>` a job's directory such as `results/rebench/copilot-gpt/20261108T112743Z-1`:

```bash
jq -r .status <job>/*/agent/suss.json | sort | uniq -c
jq .verifier_result.rewards.reward <job>/*/result.json | sort | uniq -c
jq -s 'map(.usage.ai_credits // empty) | add / 100' <job>/*/agent/suss.json  # Copilot CLI's cost
jq -s 'map(.agent_result.cost_usd // empty) | add' <job>/*/result.json      # Claude Code's cost
jq -s 'map(.run_seconds) | add / 3600' <job>/*/agent/suss.json
```

Suss no longer takes the `pro-*` lanes, and `suss compare` took two arms or
more. Their shares come from the `compare` module of commit `b210cc1`, the last
before suss ran `rebench` alone, called on the one arm:

```bash
mkdir -p /tmp/suss-b210cc1
git -C <suss> archive b210cc1 | tar -x -C /tmp/suss-b210cc1
uv run --project <suss> python -c 'import sys; sys.path.insert(0, "/tmp/suss-b210cc1/src")
from pathlib import Path; from suss import compare
print(compare.report(Path("."), sys.argv[1], ["copilot-sonnet"]), end="")' pro-go
```

The other lanes' counts, credits and times come from each trial's records,
with `<job>` a job's directory such as `results/senior/copilot-gpt/20261105T205511Z-1`:

```bash
jq -r .status <job>/*/agent/suss.json | sort | uniq -c
jq .verifier_result.rewards.reward <job>-regrade/*/result.json | sort | uniq -c
jq -s 'map(.scores.rubric_score // empty) | add / length' <job>-regrade/*/agent/judge.json
jq -s 'map(.credits) | add' <job>-regrade/*/agent/judge.json
jq -s '[.[].run_seconds // empty] | add / 3600' <job>/*/agent/suss.json
jq -s '((map(.finished_at) | max | sub("\\.[0-9]+";"") | fromdateiso8601) - (map(.started_at) | min | sub("\\.[0-9]+";"") | fromdateiso8601)) / 3600' <job>/*/result.json
for f in <job>/*/agent/run.log; do
  grep -o '"totalNanoAiu":[0-9]*' "$f" | cut -d: -f2 | sort -n | tail -1
done | awk '{ s += $1 } END { print s / 1e9 }'
```

The runs under `hve-dev/`, `hve-hard/` and `checks/`, and the oracle, validation-agent and
network checks under `m4-check/`, `m5/` and `senior-swe-survey/`, and the
judge checks under `spike-judge/`, `judge-check/` and `spike-leak/`, are checks
rather than harness runs, and are left out. `docs/notes/` holds what they found.
