# Suss leaderboard

Every harness run suss has records for, one trial per task. The resolved share
is resolved trials over valid trials, as `suss compare` prints it. A run with a
`baseline` also shows its resolved difference from that baseline, with a 95%
interval. `suss compare` gives an interval only for a difference, so a run with
no baseline has none.

| Lane | Arm | Harness | Model | Date | Tasks | Resolved | Unresolved | Invalid | Timed out | Harness failed | Resolved share | vs. baseline | AI credits | Agent time |
|---|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|---:|---:|
| `rebench` | `copilot-sonnet` | `copilot-cli` | claude-sonnet-5.5 | 2026-11-04 | 108 | 71 | 37 | 0 | 0 | 0 | 66% | none set | 3,058 (≈ $31) | 7.1 h |
| `rebench` | `copilot-gpt` | `copilot-cli` | gpt-6.1-sol | 2026-11-04 | 108 | 70 | 38 | 0 | 0 | 0 | 65% | −1% [−10%, +8%] vs. `copilot-sonnet` | 5,500 (≈ $55) | 13.4 h |
| `rebench` | `hve-sol` | `copilot-cli-hve` | gpt-6.1-sol | 2026-11-04 | 108 | 65 | 43 | 0 | 2 | 0 | 60% | −5% [−10%, +1%] vs. `copilot-gpt` | 19,151 (≈ $192) | 34.8 h |
| `rebench`, 2 tasks | `hve-capped` | `copilot-cli-hve` | claude-sonnet-5.5 | 2026-11-04 | 2 | 0 | 2 | 0 | 0 | 0 | 0% | none set | 126 (≈ $1.26) | 12.6 min |
| `rebench`, 2 tasks | `hve-uncapped` | `copilot-cli-hve` | claude-sonnet-5.5 | 2026-11-04 | 2 | 0 | 2 | 0 | 0 | 0 | 0% | none set | 132 (≈ $1.32) | 13.0 min |
| `rebench`, 2 tasks | `hve-gpt` | `copilot-cli-hve` | gpt-6.1-sol | 2026-11-04 | 2 | 0 | 2 | 0 | 0 | 0 | 0% | none set | 332 (≈ $3.32) | 39.5 min |
| `senior` | `copilot-gpt` | `copilot-cli` | gpt-6.1-sol | 2026-11-05 | running | | | | | | | | | |

How to read the columns:

- **Unresolved** counts every valid trial that did not resolve, timed-out and
  harness-failed ones included.
- **Invalid** counts trials whose regrade wrote an empty reward. No share counts
  them.
- **AI credits** are Copilot CLI's own count, its `totalNanoAiu` divided by
  10⁹. A trial's credits are the largest value in its `agent/run.log`, and a
  run's are the sum over its trials. One credit is about $0.01. Every run here
  was on Modal, and Modal's cost is recorded nowhere, so no figure includes it.
- **Agent time** is the sum of every trial's `run_seconds`. Trials ran in
  parallel, so this is not how long the run took.
- **Date** is the UTC day in the job id.

## `rebench`

The lane is SWE-rebench's July 2026 snapshot, 108 tasks. All three runs carry
the same grader: dataset `swe-rebench-07-2026`, digest `1263fe54…`.

| Arm | Job | Params | Median trial |
|---|---|---|---:|
| `copilot-sonnet` | `20261104T004023Z-1` | version 1.0.105, effort high, `max_ai_credits` 1000 | 2.5 min |
| `copilot-gpt` | `20261104T022938Z-1` | version 1.0.105, effort high, `max_ai_credits` 1000 | 5.7 min |
| `hve-sol` | `20261104T205810Z-1` | version 1.0.105, effort high, `max_ai_credits` 1000, `hve_core_commit` `55352bb8` | 16.7 min |

`suss compare rebench copilot-sonnet copilot-gpt hve-sol` prints:

```
arm             trials  resolved  failed  timed out
copilot-sonnet     108       66%      0%         0%
copilot-gpt        108       65%      0%         0%
hve-sol            108       60%      0%         2%

vs. baseline                   resolved
copilot-gpt - copilot-sonnet   -1% [-10%, +8%]
hve-sol - copilot-gpt          -5% [-10%, +1%]
```

- Both intervals include zero. On these runs neither GPT-6.1 Sol against
  Sonnet 5.5, nor hve-core's RPI Agent against plain Copilot CLI, is a
  difference suss can tell from no difference.
- Each arm ran one trial per task. The interval for `copilot-gpt` is 18 points
  wide, so arms a few points apart cannot be ranked from one run each.
- `hve-sol`'s two timed-out trials, `nearai__ironclaw-3694` and
  `ubugeeei-prod__vize-1508`, report no credits. Its credit total is over the
  other 106 trials.
- `hve-sol` used about 3.5 times `copilot-gpt`'s credits and 2.6 times its agent
  time, on the same model.

### The 2-task subset

`hve-capped`, `hve-uncapped` and `hve-gpt` ran on two `rebench` tasks alone:
`kubernetes-sigs__kueue-11559` and `pydantic__pydantic-ai-5842`. `hve-capped`
and `hve-uncapped` differ only in `max_ai_credits`, 1000 against none. None of
the six trials resolved its task, and neither did any of the three full-lane
runs on these two tasks.

| Arm | Job | Params |
|---|---|---|
| `hve-capped` | `20261104T042203Z-1` | version 1.0.105, effort high, `max_ai_credits` 1000, `hve_core_commit` `55352bb8` |
| `hve-uncapped` | `20261104T042205Z-1` | version 1.0.105, effort high, `hve_core_commit` `55352bb8` |
| `hve-gpt` | `20261104T051256Z-1` | version 1.0.105, effort high, `hve_core_commit` `55352bb8` |

Their grader digest, `c0d8b2f8…`, is the two-task lane's, not the full lane's,
so `suss compare` will not put these runs beside the full-lane ones. Two tasks
are too few to measure a share. The runs show that each arm ran, and what a
hard task cost it.

## `senior`

`copilot-gpt` is running on Senior SWE-Bench, job `20261105T205511Z-1`, with
the same params as on `rebench` and `max_seconds` 5400. Its row will be filled
in when it finishes.

## Retired lane: SWE-bench Pro

Suss ran these on SWE-bench Pro before replacing it with `rebench`. Sonnet 5.5
on plain Copilot CLI resolved 98% of `pro-go`, which leaves no room to tell
harnesses apart. Suss no longer runs or compares these lanes.

| Lane | Arm | Harness | Model | Date | Tasks | Resolved | Unresolved | Invalid | Timed out | Harness failed | Resolved share | vs. baseline | AI credits | Agent time |
|---|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|---:|---:|
| `pro-hard51-rerun113` | `copilot-sonnet` | `copilot-cli` | claude-sonnet-5.5 | 2026-11-03 | 10 | 10 | 0 | 0 | 0 | 0 | 100% | none set | 245 (≈ $2.45) | 18.0 min |
| `pro-go` | `copilot-sonnet` | `copilot-cli` | claude-sonnet-5.5 | 2026-11-03 | 256 | 252 | 4 | 0 | 0 | 0 | 98% | none set | 3,562 (≈ $36) | 14.0 h |
| `pro-hard51` | `copilot-sonnet` | `copilot-cli` | claude-sonnet-5.5 | 2026-11-03 | 51 | 35 | 16 | 0 | 0 | 10 | 69% | none set | 1,055 (≈ $11) | 2.8 h |

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
uv run --project <suss> suss compare rebench copilot-sonnet copilot-gpt hve-sol
(cd hve-hard && uv run --project <suss> suss compare rebench hve-capped hve-uncapped hve-gpt)
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

The counts, credits and times come from each trial's records, with `<job>` a
job's directory such as `results/rebench/hve-sol/20261104T205810Z-1`:

```bash
jq -r .status <job>/*/agent/suss.json | sort | uniq -c
jq .verifier_result.rewards.reward <job>-regrade/*/result.json | sort | uniq -c
jq -s '[.[].run_seconds // empty] | add / 3600' <job>/*/agent/suss.json
for f in <job>/*/agent/run.log; do
  grep -o '"totalNanoAiu":[0-9]*' "$f" | cut -d: -f2 | sort -n | tail -1
done | awk '{ s += $1 } END { print s / 1e9 }'
```

The runs under `hve-dev/` and `checks/`, and the oracle, validation-agent and
network checks under `m4-check/`, `m5/` and `senior-swe-survey/`, are checks
rather than harness runs, and are left out. `docs/notes/` holds what they found.
