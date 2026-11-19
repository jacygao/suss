# SWE-drill leaderboard

Every harness run SWE-drill has records for, a section for each lane. The lanes
measure different things, so their numbers never compare across lanes.

`site/` draws the same numbers as a web page, from `site/leaderboard.json`. A
change to a row here changes that file in the same commit.

## `rebench`

SWE-rebench's July 2026 snapshot: 108 GitHub issues from open-source
repositories, each graded by its own hidden tests. The table ranks arms by
resolved rate, as `swe-drill leaderboard --lane rebench` does.

| Arm | Agent | Harness | Model | Date | Runs | Resolved rate | pass@k | Timed out | Cost per run, USD at pay-as-you-go prices | Agent time per run, sum of trials |
|---|---|---|---|---|---:|---:|---:|---:|---:|---:|
| `copilot-opus` | Copilot CLI 1.0.107 | Default | claude-opus-5.5, effort high | 2026-11-16, 2026-11-16, 2026-11-16 | 3 | 69% ±8 | 80% @3 | 8 | $118.39 | 22.0 h |
| `claude-opus` | Claude Code 2.1.311 | Default | claude-opus-5-5, effort high | 2026-11-09, 2026-11-11 | 2 | 66% ±9 | 70% @2 | 5 | $120.40 | 17.3 h |
| `copilot-sonnet` | Copilot CLI 1.0.107 | Default | claude-sonnet-5.5, effort high | 2026-11-15, 2026-11-15, 2026-11-15 | 3 | 65% ±8 | 75% @3 | 1 | $35.76 | 13.1 h |
| `squiz-gpt-6.1-sol` | Copilot CLI 1.0.107 | Peer review by squiz 0.3.2, claude-sonnet-5.5 reviewer at effort high | gpt-6.1-sol, effort high | 2026-11-15, 2026-11-15, 2026-11-15 | 3 | 64% ±8 | 72% @3 | 3 | $63.82 | 18.8 h |
| `copilot-fusion-high` | Copilot CLI 1.0.107 | Default | Fusion, tier high | 2026-11-14, 2026-11-14, 2026-11-14 | 3 | 63% ±9 | 69% @3 | 4 | $31.92 | 16.8 h |
| `copilot-fusion` | Copilot CLI 1.0.107 | Default | Fusion, tier standard | 2026-11-13, 2026-11-13, 2026-11-13 | 3 | 62% ±9 | 67% @3 | 0 | $15.23 | 12.4 h |
| `copilot-gpt-6.1-sol` | Copilot CLI 1.0.107 | Default | gpt-6.1-sol, effort high | 2026-11-14, 2026-11-14, 2026-11-14 | 3 | 62% ±9 | 67% @3 | 1 | $44.06 | 14.8 h |

| Arm | Job | Where it ran | Params | Median trial |
|---|---|---|---|---:|
| `copilot-opus` | `20261116T203211Z-1`, `20261116T203507Z-1`, `20261116T203832Z-1` | Docker on Azure VMs | version 1.0.107, effort high | 9.5 min, 9.4 min, 9.0 min |
| `claude-opus` | `20261109T002622Z-1`, `20261111T060421Z-1` | Modal | version 2.1.311, effort high | 5.6 min, 5.8 min |
| `copilot-sonnet` | `20261115T213917Z-1`, `20261115T214236Z-1`, `20261115T214539Z-1` | Docker on Azure VMs | version 1.0.107, effort high | 4.0 min, 4.1 min, 3.9 min |
| `squiz-gpt-6.1-sol` | `20261115T114014Z-1`, `20261115T114339Z-1`, `20261115T114642Z-1` | Docker on Azure VMs | version 1.0.107, effort high, `squiz_version` 0.3.2, `reviewer_model` claude-sonnet-5.5, `reviewer_effort` high | 8.1 min, 7.6 min, 7.7 min |
| `copilot-fusion-high` | `20261114T124819Z-1`, `20261114T125147Z-1`, `20261114T125449Z-1` | Docker on Azure VMs | version 1.0.107, `fusion_tier` high | 5.2 min, 5.5 min, 5.4 min |
| `copilot-fusion` | `20261113T223307Z-1`, `20261113T223410Z-1`, `20261113T223531Z-1` | Docker on Azure VMs | version 1.0.107 | 5.2 min, 4.8 min, 5.6 min |
| `copilot-gpt-6.1-sol` | `20261114T042619Z-1`, `20261114T042923Z-1`, `20261114T043249Z-1` | Docker on Azure VMs | version 1.0.107, effort high | 5.5 min, 5.7 min, 5.9 min |

- **Gaps of a few points may be noise.** One run of an arm moves by a few
  points on its own, and the ± covers one arm's rate, not the gap between two.
- **Runs on Docker are slower than on Modal.** Their trials took longer, and
  on a PC they timed out more often, so every Copilot arm is now ranked on
  Azure VMs. The jobs table says where each run ran.
- **`copilot-fusion`, `copilot-fusion-high`, `copilot-gpt-6.1-sol` and
  `copilot-opus` are ranked on their runs on Docker,** so that all of an arm's
  runs are on one kind of machine. Their runs on Modal are left out:
  `copilot-fusion`'s `20261108T124036Z-1` resolved 62%,
  `copilot-fusion-high`'s `20261108T222220Z-1` 59%, `copilot-gpt`'s
  `20261108T112743Z-1` 67%, and `copilot-opus`'s `20261108T212444Z-1` 62%.
  `copilot-gpt` is `copilot-gpt-6.1-sol`'s name from before the arms were
  named for their agent and model.
- **`copilot-fusion-high`'s, `copilot-sonnet`'s and `copilot-opus`'s runs on
  a PC are left out too,** so that all three of each arm's runs are on Azure.
  `copilot-fusion-high`'s `20261111T035703Z-1` and `20261112T010208Z-1`
  together resolved 63%, `copilot-sonnet`'s `20261109T221503Z-1`,
  `20261110T074227Z-1` and `20261110T214213Z-1` 64%, and `copilot-opus`'s
  `20261112T093414Z-1`, `20261112T212852Z-1` and `20261113T063106Z-1` 65%,
  with 17 trials timed out.
- **Every Copilot arm's runs ran three at a time, one Azure region each.** Copilot CLI never started on 12 of
  `copilot-fusion`'s trials, refused by Copilot's model router or out of time
  in setup, and those 12 ran again on a fresh VM. The other five arms' runs
  started three minutes apart, and none of their trials needed running again.
- **A trial whose grading stopped before any test ran was graded once more,
  from the same patch.** Four had: `copilot-gpt-6.1-sol`'s
  `mikro-orm__mikro-orm-7799` in the first run, whose test database did not
  start, and `apache__pulsar-25953` in its third run and in
  `copilot-fusion-high`'s second and third, whose Gradle daemon died. Graded
  again on a VM of their own, three failed their tests, as the rows count
  them. `copilot-fusion-high`'s second-run `apache__pulsar-25953` stopped the
  same way again, and counts as unresolved. The regrades are under
  `regrades/rebench/` in storage, apart from the runs.
- **Two `copilot-fusion-high` patches do not compile, and count as
  unresolved.** They are `apache__hugegraph-3037`'s in the second run and the
  third, which leave its tests nothing to run.
- **`squiz-gpt-6.1-sol`'s cost counts squiz's reviewer and fixer.** Of its
  $63.82 a run, the coding agent took $43.56, the reviewer $15.64 and the
  fixer $4.62. The fixer ran on 26, 31 and 21 trials of the three runs. Two
  trials of the first run timed out with no cost recorded, and its total
  leaves them out.
- **`copilot-opus`'s eight timed-out trials recorded no cost,** two in each of
  the first two runs and four in the third, and its cost per run leaves them
  out.
- **squiz's review stopped short on seven `squiz-gpt-6.1-sol` trials,** two or
  three a run, each because squiz took the lock on its state file only after
  its deadline had passed. Their patches were graded as they stood.
- **Eleven trials of `squiz-gpt-6.1-sol`, `copilot-sonnet` and
  `copilot-opus` have no test result, and count as unresolved.** They are
  `apache__pulsar-25953` in all three runs of each arm, and
  `apache__hugegraph-3037` in squiz's first and third. They were not graded
  again.

## How to read the columns

- **Agent:** the coding agent and its version. **Harness:** what runs around
  it. Default is the agent alone; peer review is squiz reviewing each change
  the agent makes, with a fixer that answers the findings.
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

## Retired lanes

### SWE-bench Pro

SWE-bench Pro, which SWE-drill no longer runs: Sonnet 5.5 on plain Copilot CLI
resolved 98% of `pro-go`, leaving no room to tell harnesses apart. Later, over
three runs each on plain Copilot CLI, `pro-hard51`, Pro's hardest 51 tasks, was
resolved 92% by Opus 5.5, 85% by GPT-6.1 Sol, 80% by Fusion at tier high and 79%
by Sonnet 5.5. GPT-6.1 Sol as hve-core's RPI Agent resolved 86%.

| Lane | Arm | Agent | Harness | Model | Date | Tasks | Resolved | Unresolved | Invalid | Timed out | Harness failed | Resolved share | AI credits | Agent time, sum of trials | Elapsed |
|---|---|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `pro-hard51-rerun113` | `copilot-sonnet` | `copilot-cli` | Default | claude-sonnet-5.5 | 2026-11-03 | 10 | 10 | 0 | 0 | 0 | 0 | 100% | 245 (≈ $2.45) | 18.0 min | 5 min |
| `pro-go` | `copilot-sonnet` | `copilot-cli` | Default | claude-sonnet-5.5 | 2026-11-03 | 256 | 252 | 4 | 0 | 0 | 0 | 98% | 3,562 (≈ $36) | 14.0 h | 0.7 h |
| `pro-hard51` | `copilot-sonnet` | `copilot-cli` | Default | claude-sonnet-5.5 | 2026-11-03 | 51 | 35 | 16 | 0 | 0 | 10 | 69% | 1,055 (≈ $11) | 2.8 h | 0.3 h |
| `pro-hard51` | `copilot-gpt-6.1-sol` | `copilot-cli` | Default | gpt-6.1-sol | 2026-11-19 | 51 | 43 | 8 | 0 | 0 | 0 | 84% | 1,880 (≈ $19) | 6.3 h | 1.1 h |
| `pro-hard51` | `copilot-gpt-6.1-sol` | `copilot-cli` | Default | gpt-6.1-sol | 2026-11-19 | 51 | 45 | 6 | 0 | 0 | 0 | 88% | 1,926 (≈ $19) | 6.7 h | 1.2 h |
| `pro-hard51` | `copilot-gpt-6.1-sol` | `copilot-cli` | Default | gpt-6.1-sol | 2026-11-19 | 51 | 42 | 9 | 0 | 0 | 0 | 82% | 1,900 (≈ $19) | 6.4 h | 1.2 h |
| `pro-hard51` | `copilot-fusion-high` | `copilot-cli` | Default | Fusion, tier high | 2026-11-19 | 51 | 41 | 10 | 0 | 0 | 0 | 80% | 2,306 (≈ $23) | 14.4 h | 1.8 h |
| `pro-hard51` | `copilot-fusion-high` | `copilot-cli` | Default | Fusion, tier high | 2026-11-19 | 51 | 39 | 12 | 0 | 0 | 0 | 76% | 2,182 (≈ $22) | 14.2 h | 1.6 h |
| `pro-hard51` | `copilot-fusion-high` | `copilot-cli` | Default | Fusion, tier high | 2026-11-19 | 51 | 42 | 9 | 0 | 1 | 0 | 82% | 2,205 (≈ $22) | 14.9 h | 1.8 h |
| `pro-hard51` | `copilot-opus` | `copilot-cli` | Default | claude-opus-5.5 | 2026-11-19 | 51 | 46 | 5 | 0 | 0 | 0 | 90% | 4,081 (≈ $41) | 7.2 h | 1.2 h |
| `pro-hard51` | `copilot-opus` | `copilot-cli` | Default | claude-opus-5.5 | 2026-11-19 | 51 | 47 | 4 | 0 | 0 | 0 | 92% | 3,951 (≈ $40) | 7.0 h | 1.1 h |
| `pro-hard51` | `copilot-opus` | `copilot-cli` | Default | claude-opus-5.5 | 2026-11-19 | 51 | 47 | 4 | 0 | 1 | 0 | 92% | 3,828 (≈ $38) | 7.3 h | 1.7 h |
| `pro-hard51` | `copilot-sonnet` | `copilot-cli` | Default | claude-sonnet-5.5 | 2026-11-19 | 51 | 39 | 12 | 0 | 0 | 0 | 76% | 1,354 (≈ $14) | 5.3 h | 1.3 h |
| `pro-hard51` | `copilot-sonnet` | `copilot-cli` | Default | claude-sonnet-5.5 | 2026-11-19 | 51 | 40 | 11 | 0 | 1 | 0 | 78% | 1,332 (≈ $13) | 6.0 h | 1.1 h |
| `pro-hard51` | `copilot-sonnet` | `copilot-cli` | Default | claude-sonnet-5.5 | 2026-11-19 | 51 | 42 | 9 | 0 | 1 | 0 | 82% | 1,388 (≈ $14) | 6.1 h | 1.6 h |
| `pro-hard51` | `hve-sol` | `copilot-cli` | hve-core RPI Agent | gpt-6.1-sol | 2026-11-19 | 51 | 44 | 7 | 0 | 0 | 0 | 86% | 6,143 (≈ $61) | 12.2 h | 1.5 h |
| `pro-hard51` | `hve-sol` | `copilot-cli` | hve-core RPI Agent | gpt-6.1-sol | 2026-11-19 | 51 | 42 | 9 | 0 | 0 | 0 | 82% | 6,151 (≈ $62) | 12.2 h | 1.5 h |
| `pro-hard51` | `hve-sol` | `copilot-cli` | hve-core RPI Agent | gpt-6.1-sol | 2026-11-19 | 51 | 45 | 6 | 0 | 0 | 0 | 88% | 6,118 (≈ $61) | 12.4 h | 1.6 h |

| Lane | Job | Params | Median trial |
|---|---|---|---:|
| `pro-go` | `20261103T032309Z-1` | version 1.0.105, effort high, no credit cap | 2.7 min |
| `pro-hard51` | `20261103T062913Z-1` | version 1.0.105, effort high, no credit cap | 3.5 min |
| `pro-hard51` | `20261119T045251Z-1`, `20261119T050349Z-1`, `20261119T050700Z-1` | version 1.0.107, effort high | 6.2 min, 7.0 min, 7.0 min |
| `pro-hard51` | `20261119T063914Z-1`, `20261119T064444Z-1`, `20261119T070232Z-1` | version 1.0.107, `fusion_tier` high | 17.0 min, 16.2 min, 16.8 min |
| `pro-hard51` | `20261119T113028Z-1`, `20261119T113342Z-1`, `20261119T113641Z-1` | version 1.0.107, effort high | 7.1 min, 6.9 min, 5.9 min |
| `pro-hard51` | `20261119T124818Z-1`, `20261119T132501Z-1`, `20261119T132530Z-1` | version 1.0.107, effort high | 3.6 min, 3.9 min, 3.6 min |
| `pro-hard51` | `20261119T202751Z-1`, `20261119T203120Z-1`, `20261119T203425Z-1` | version 1.0.107, effort high, `hve_core_commit` 55352bb | 13.9 min, 14.0 min, 13.4 min |
| `pro-hard51-rerun113` | `20261103T120000Z-1` | version 1.0.105, effort high, no credit cap | 1.2 min |

- In `pro-hard51`, Copilot CLI never started on 10 trials (#113), which count as
  unresolved. `pro-hard51-rerun113` ran those 10 again.
- The `pro-hard51` jobs at version 1.0.107 ran on Docker, one Azure VM each,
  three at a time. The jobs at version 1.0.105 ran on Modal.
- Tasks resolved in all three runs, and in at least one: `copilot-opus` 45 and
  48, `copilot-gpt-6.1-sol` 41 and 46, `copilot-fusion-high` 38 and 43, and
  `copilot-sonnet` 37 and 43, and `hve-sol` 42 and 45.
- Four `copilot-gpt-6.1-sol` trials and eight `copilot-fusion-high` trials
  record no test result. Each patch applied and the tests the task needs ran,
  but none of them passed, so they count as unresolved.
- Three `copilot-sonnet` trials, all of `flipt-c1728053`, record no test
  result. Each patch applied but left the package failing to build, so they
  count as unresolved. Every other arm resolved that task at least twice.
- Three `hve-sol` trials, all of `navidrome-69e0a26`, record no test result.
  Each patch applied, but the test the task needs never reported, so they
  count as unresolved.

### `senior`

Senior SWE-Bench `v2026.06`, which SWE-drill no longer runs: its grading needs a
provider key, which SWE-drill could reach only by re-routing every judge through Copilot.

| Lane | Arm | Agent | Harness | Model | Date | Investigate tasks | Resolved | Resolved share | Design tasks graded | AI credits | Agent time, sum of trials |
|---|---|---|---|---|---|---:|---:|---:|---:|---:|---:|
| `senior` | `copilot-gpt` | `copilot-cli` | Default | gpt-6.1-sol | 2026-11-05 | 25 | 8 | 32% | 0 of 25 | 4,739 (≈ $47) | 11.3 h |

| Lane | Job | Params | Regrade |
|---|---|---|---|
| `senior` | `20261105T205511Z-1` | version 1.0.105, effort high, `max_ai_credits` 1000, `max_seconds` 5400 | `20261105T205511Z-1-regrade`, by `JudgeAgent` with no API key |

## How this page was made

Run from the directory holding `swe-drill.toml` and `results/`, with `<checkout>` the
checkout of this repository. The `rebench` resolved rates, margins and pass@k
come from `swe-drill leaderboard`, which ranks every arm under `results/rebench/`.
They come from a mirror of the runs kept in Azure Blob Storage:

```bash
scripts/results.sh down <mirror>
(cd <mirror> && uv run --project <checkout> swe-drill leaderboard --lane rebench)
```

The `rebench` counts, credits and times come from each trial's records, with
`<job>` a job's directory such as `results/rebench/copilot-gpt-6.1-sol/20261114T042619Z-1`:

```bash
jq -r .status <job>/*/agent/suss.json | sort | uniq -c
jq .verifier_result.rewards.reward <job>/*/result.json | sort | uniq -c
jq -s 'map(.usage.ai_credits // empty) | add / 100' <job>/*/agent/suss.json  # Copilot CLI's cost, squiz's reviewer and fixer included
jq -s 'map(.agent_result.cost_usd // empty) | add' <job>/*/result.json      # Claude Code's cost
jq -s 'map(.run_seconds) | add / 3600' <job>/*/agent/suss.json
```

SWE-drill no longer takes the `pro-*` lanes, and `suss compare` took two arms or
more. Their shares come from the `compare` module of commit `b210cc1`, the last
before SWE-drill ran `rebench` alone, called on the one arm:

```bash
mkdir -p /tmp/suss-b210cc1
git -C <checkout> archive b210cc1 | tar -x -C /tmp/suss-b210cc1
uv run --project <checkout> python -c 'import sys; sys.path.insert(0, "/tmp/suss-b210cc1/src")
from pathlib import Path; from suss import compare
print(compare.report(Path("."), sys.argv[1], ["copilot-sonnet"]), end="")' pro-go
```

The other lanes' counts, credits and times come from each trial's records,
with `<job>` a job's directory such as `results/senior/copilot-gpt/20261105T205511Z-1`:

```bash
jq -r .status <job>/*/agent/suss.json | sort | uniq -c
jq .verifier_result.rewards.reward <job>-regrade/*/result.json | sort | uniq -c
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
