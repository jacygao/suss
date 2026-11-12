# Suss leaderboard

Every harness run suss has records for, a section for each lane. The lanes
measure different things, so their numbers never compare across lanes.

## `rebench`

SWE-rebench's July 2026 snapshot: 108 GitHub issues from open-source
repositories, each graded by its own hidden tests. The table ranks arms by
resolved rate, as `suss leaderboard --lane rebench` does.

| Arm | Harness | Model | Date | Runs | Resolved rate | pass@k | Timed out | Cost per run, USD at pay-as-you-go prices | Agent time per run, sum of trials |
|---|---|---|---|---:|---:|---:|---:|---:|---:|
| `claude-opus` | Claude Code 2.1.311 | claude-opus-5-5, effort high | 2026-11-09 | 1 | 69% | | 3 | $118.98 | 17.8 h |
| `copilot-opus` | Copilot CLI 1.0.107 | claude-opus-5.5, effort high | 2026-11-12 | 1 | 68% | | 5 | $125.70 | 26.6 h |
| `copilot-gpt` | Copilot CLI 1.0.107 | gpt-6.1-sol, effort high | 2026-11-08 | 1 | 67% | | 0 | $41.86 | 11.3 h |
| `copilot-sonnet` | Copilot CLI 1.0.107 | claude-sonnet-5.5, effort high | 2026-11-09, 2026-11-10, 2026-11-10 | 3 | 64% ±8 | 74% @3 | 5 | $35.39 | 15.0 h |
| `copilot-fusion-high` | Copilot CLI 1.0.107 | Fusion, tier high | 2026-11-11, 2026-11-12 | 2 | 63% ±9 | 67% @2 | 8 | $33.28 | 21.4 h |
| `copilot-fusion` | Copilot CLI 1.0.107 | Fusion, tier standard | 2026-11-08 | 1 | 62% | | 0 | $16.12 | 8.5 h |

| Arm | Job | Where it ran | Params | Median trial |
|---|---|---|---|---:|
| `claude-opus` | `20261109T002622Z-1` | Modal | version 2.1.311, effort high | 5.6 min |
| `copilot-opus` | `20261112T093414Z-1` | Docker on a PC | version 1.0.107, effort high | 10.8 min |
| `copilot-gpt` | `20261108T112743Z-1` | Modal | version 1.0.107, effort high | 5.2 min |
| `copilot-sonnet` | `20261109T221503Z-1`, `20261110T074227Z-1`, `20261110T214213Z-1` | Docker on a PC | version 1.0.107, effort high | 3.9 min, 4.5 min, 4.9 min |
| `copilot-fusion-high` | `20261111T035703Z-1`, `20261112T010208Z-1` | Docker on a PC | version 1.0.107, `fusion_tier` high | 7.5 min, 6.5 min |
| `copilot-fusion` | `20261108T124036Z-1` | Modal | version 1.0.107 | 3.7 min |

- **Gaps of a few points may be noise.** One run of an arm moves by a few
  points on its own, and the ± covers one arm's rate, not the gap between two.
- **Runs on Docker on a PC are slower than on Modal.** Their trials took
  longer and timed out more often. The jobs table says where each run ran.
- **`copilot-fusion-high` and `copilot-opus` are ranked on their runs on
  Docker,** so that all of an arm's runs are on one machine. Their runs on
  Modal are left out: `copilot-fusion-high`'s `20261108T222220Z-1` resolved
  59%, and `copilot-opus`'s `20261108T212444Z-1` resolved 62%.
- **One `copilot-opus` trial, `vuejs__core-14897`, has no grade,** since
  Docker Compose failed to start its environment. It counts as unresolved.

## `senior`

Senior SWE-Bench `v2026.06`: 50 tasks, 25 investigate tasks graded by their
tests and 25 design tasks graded by senior's validation agent. No design task
has a reward yet, since that agent needs API credit. Suss's judge scores every
patch for quality with senior's rubric and taste judges.

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

- **Rubric** is the share of a task's criteria met, from 0 to 1; the count in
  brackets is the tasks that have criteria. **Practice alignment** and
  **relative taste** run from 1 to 5. **Patch bloat** is the patch's source
  lines over the reference fix's, with test and doc files left out of both.
- **These are suss's scores, not senior's published ones,** and taste moves by
  up to a point between judgings, so treat a gap under about 0.5 as noise.

### The runs

| Arm | Job | Params | Regrade |
|---|---|---|---|
| `copilot-gpt` | `20261105T205511Z-1` | version 1.0.105, effort high, `max_ai_credits` 1000, `max_seconds` 5400 | `20261105T205511Z-1-regrade`, by `JudgeAgent` with no API key |

- Over 25 tasks, the resolved share's 95% interval is about 17% to 52%.

## How to read the columns

- **Runs:** how many times the arm ran every task, one trial per task each
  time. Trial counts and rates pool all runs; cost and agent time are per run.
- **Resolved rate:** resolved trials over valid trials. The ± is half its 95%
  interval.
- **pass@k:** the share of tasks that at least one of the arm's k runs
  resolved.
- **Timed out:** trials stopped at the agent's time limit, counted as
  unresolved. **Invalid:** trials whose verifier wrote an empty reward, counted
  nowhere.
- **Cost:** AI credits at $0.01 each for Copilot CLI, and Anthropic's API price
  for Claude Code's tokens. A price, not what was paid, and no sandbox cost.
- **Agent time:** every trial's run time added up.

## Retired lane: SWE-bench Pro

SWE-bench Pro, which suss no longer runs: Sonnet 5.5 on plain Copilot CLI
resolved 98% of `pro-go`, leaving no room to tell harnesses apart.

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

- In `pro-hard51`, Copilot CLI never started on 10 trials (#113), which count as
  unresolved. `pro-hard51-rerun113` ran those 10 again.

## How this page was made

Run from the directory holding `suss.toml` and `results/`, with `<suss>` the
checkout of this repository. The `rebench` resolved rates, margins and pass@k
come from `suss leaderboard`, which ranks every arm under `results/rebench/`.
`copilot-fusion-high`'s and `copilot-opus`'s come from the PC holding only
their runs on Docker:

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
