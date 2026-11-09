# Suss leaderboard

Every harness run suss has records for, one trial per task, a section for each
lane. The lanes measure different things, so their numbers never compare
across lanes: `rebench` grades by tests alone, and `senior` adds judges of code
quality.

The resolved share is resolved trials over valid trials, as `suss compare`
prints it. A run with a `baseline` also shows its resolved difference from that
baseline, with a 95% interval. `suss compare` gives an interval only for a
difference, so a run with no baseline has none.

## `rebench`

The lane is SWE-rebench's July 2026 snapshot, 108 tasks, each graded by its
tests in a verifier sandbox of its own. Every run has `copilot-gpt` as its
baseline.

| Arm | Harness | Model | Date | Tasks | Resolved | Unresolved | Invalid | Timed out | Harness failed | Resolved share | vs. `copilot-gpt` | Cost | Agent time, sum of trials |
|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|---:|---:|
| `claude-opus` | Claude Code 2.1.311 | claude-opus-5-5, effort high | 2026-11-09 | 108 | 74 | 34 | 0 | 3 | 0 | 69% | +2% [−5%, +8%] | Claude Max 5x plan, about 1.6 five-hour windows | 17.8 h |
| `copilot-gpt` | Copilot CLI 1.0.107 | gpt-6.1-sol, effort high | 2026-11-08 | 108 | 72 | 36 | 0 | 0 | 0 | 67% | baseline | 4,186 credits (≈ $42) | 11.3 h |
| `copilot-fusion` | Copilot CLI 1.0.107 | Fusion, tier standard | 2026-11-08 | 108 | 67 | 41 | 0 | 0 | 0 | 62% | −5% [−11%, +1%] | 1,612 credits (≈ $16) | 8.5 h |
| `copilot-opus` | Copilot CLI 1.0.107 | claude-opus-5.5, effort high | 2026-11-08 | 108 | 67 | 41 | 0 | 1 | 0 | 62% | −5% [−11%, +2%] | 11,752 credits (≈ $118) | 15.1 h |
| `copilot-fusion-high` | Copilot CLI 1.0.107 | Fusion, tier high | 2026-11-08 | 108 | 64 | 44 | 0 | 0 | 0 | 59% | −7% [−13%, −2%] | 3,218 credits (≈ $32) | 12.2 h |

| Arm | Job | Params | Median trial |
|---|---|---|---:|
| `claude-opus` | `20261109T002622Z-1` | version 2.1.311, effort high | 5.6 min |
| `copilot-gpt` | `20261108T112743Z-1` | version 1.0.107, effort high | 5.2 min |
| `copilot-fusion` | `20261108T124036Z-1` | version 1.0.107 | 3.7 min |
| `copilot-opus` | `20261108T212444Z-1` | version 1.0.107, effort high | 5.7 min |
| `copilot-fusion-high` | `20261108T222220Z-1` | version 1.0.107, `fusion_tier` high | 4.3 min |

`suss compare rebench copilot-gpt copilot-fusion copilot-fusion-high copilot-opus claude-opus` prints:

```
arm                  trials  resolved  failed  timed out
copilot-gpt             108       67%      0%         0%
copilot-fusion          108       62%      0%         0%
copilot-fusion-high     108       59%      0%         0%
copilot-opus            108       62%      0%         1%
claude-opus             108       69%      0%         3%

vs. baseline                        resolved
copilot-fusion - copilot-gpt        -5% [-11%, +1%]
copilot-fusion-high - copilot-gpt   -7% [-13%, -2%]
copilot-opus - copilot-gpt          -5% [-11%, +2%]
claude-opus - copilot-gpt           +2% [-5%, +8%]
```

- **Only `copilot-fusion-high`'s interval excludes zero.** Each arm ran one
  trial per task, so a gap of about 5 points is one suss cannot tell from no
  difference.
- **The same model ran better in its own harness.** Opus 5.5 resolved 74 in
  Claude Code against 67 in Copilot CLI. Compared directly, `claude-opus -
  copilot-opus` is +6% [+1%, +12%]: 9 tasks only Claude Code resolved, 2 only
  Copilot CLI did.
- **Fusion picks the model and effort per task,** and refuses `--effort`. At
  tier standard it ran one model per task, at medium effort: GPT-6.1 Sol on
  73, GPT-6 Luna on 26, GPT-6.1 Terra on 7, and Claude Opus 5.5 on 2. Tier
  high runs HydraFusion, a solver followed by a review pass.
- **Credits per resolved task:** `copilot-fusion` 24, `copilot-fusion-high`
  50, `copilot-gpt` 58, `copilot-opus` 175.
- **Opus thought about as much as Sol at the same effort.** In Copilot CLI, the
  median trial's reasoning tokens were 3,725 for Opus against 3,513 for Sol. Opus
  wrote about 40% more output and read about 40% more context.
- **Some trials ran twice.** In `copilot-gpt`, 11 trials were cut off when the
  host slept, and 12 were scored 0 when their verifier could not download
  Python from GitHub to parse the test output. In `claude-opus`, 50 trials were
  stopped by the plan's 5-hour limit. Each was run again with `harbor jobs
  resume`, and each task has one graded trial. The 12 first attempts' saved
  test logs, parsed offline, give the same result as their reruns on all 12.
- `copilot-opus`'s timed-out trial reports no credits, and its credit total is
  over the other 107 trials.

## `senior`

The lane is Senior SWE-Bench `v2026.06`, 50 tasks: 25 investigate tasks,
graded by their tests, and 25 design tasks, graded by senior's validation agent.
The validation agent runs only on API credit, so no design task has a reward
yet. Every patch is scored for quality by suss's judge: senior's rubric and
taste judges, run through Copilot CLI on `gemini-4-argon` at effort `high`.

### Correctness

| Arm | Harness | Model | Date | Investigate tasks | Resolved | Resolved share | Design tasks graded | vs. baseline | AI credits | Agent time, sum of trials |
|---|---|---|---|---:|---:|---:|---:|---|---:|---:|
| `copilot-gpt` | `copilot-cli` | gpt-6.1-sol | 2026-11-05 | 25 | 8 | 32% | 0 of 25 | none set | 4,739 (≈ $47) | 11.3 h |

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

- **Unresolved** counts every valid trial that did not resolve, timed-out and
  harness-failed ones included.
- **Invalid** counts trials whose verifier wrote an empty reward. No share
  counts them.
- **AI credits** are Copilot CLI's own count. On `rebench`, a trial's credits
  are `usage.ai_credits` in its `agent/suss.json`; on the other lanes, they are
  its `totalNanoAiu` divided by 10⁹, the largest value in its `agent/run.log`.
  A run's are the sum over its trials. One credit is about $0.01. Every run here
  was on Modal, and Modal's cost is recorded nowhere, so no figure includes it.
- **Agent time, sum of trials** is every trial's `run_seconds` added
  together. Modal bills each sandbox for its own time, so this is what tracks
  the run's Modal cost.
- **Elapsed** is how long the agent job took, from its first trial's start to
  its last trial's finish. Up to 32 trials ran at once. The regrade that
  follows is not included. `rebench` gives none, since some of its jobs ran in
  parts.
- **Date** is the UTC day in the job id.

## Retired lane: SWE-bench Pro

Suss ran these on SWE-bench Pro before replacing it with `rebench`. Sonnet 5.5
on plain Copilot CLI resolved 98% of `pro-go`, which leaves no room to tell
harnesses apart. Suss no longer runs or compares these lanes.

| Lane | Arm | Harness | Model | Date | Tasks | Resolved | Unresolved | Invalid | Timed out | Harness failed | Resolved share | vs. baseline | AI credits | Agent time, sum of trials | Elapsed |
|---|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|---:|---:|---:|
| `pro-hard51-rerun113` | `copilot-sonnet` | `copilot-cli` | claude-sonnet-5.5 | 2026-11-03 | 10 | 10 | 0 | 0 | 0 | 0 | 100% | none set | 245 (≈ $2.45) | 18.0 min | 5 min |
| `pro-go` | `copilot-sonnet` | `copilot-cli` | claude-sonnet-5.5 | 2026-11-03 | 256 | 252 | 4 | 0 | 0 | 0 | 98% | none set | 3,562 (≈ $36) | 14.0 h | 0.7 h |
| `pro-hard51` | `copilot-sonnet` | `copilot-cli` | claude-sonnet-5.5 | 2026-11-03 | 51 | 35 | 16 | 0 | 0 | 10 | 69% | none set | 1,055 (≈ $11) | 2.8 h | 0.3 h |

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
checkout of this repository. The shares, intervals and invalid trials come from
`suss compare`:

```bash
uv run --project <suss> suss compare rebench copilot-gpt copilot-fusion copilot-fusion-high copilot-opus claude-opus
```

The `rebench` counts, credits and times come from each trial's records, with
`<job>` a job's directory such as `results/rebench/copilot-gpt/20261108T112743Z-1`:

```bash
jq -r .status <job>/*/agent/suss.json | sort | uniq -c
jq .verifier_result.rewards.reward <job>/*/result.json | sort | uniq -c
jq -s 'map(.usage.ai_credits // empty) | add' <job>/*/agent/suss.json
jq -s 'map(.run_seconds) | add / 3600' <job>/*/agent/suss.json
```

Suss no longer takes the `pro-*` lanes, and `suss compare` takes two arms or
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
