---
settles: "§ 3 — which model the judge runs and at what effort, where the judge reads that effort back, what one judged patch costs, and whether the judge retries an answer that fails the schema"
issue: [199]
recorded: 2026-11-07
versions: { copilot-cli: 1.0.105, harbor: 0.22.0, modal: 1.6.3, senior-swe-bench: "v2026.06", plan: "Copilot Enterprise seat", models: "gemini-4-argon; gpt-6.1-sol, gpt-6-astra, claude-sonnet-5.5 from stage 2" }
recheck-when: Copilot's model catalog or its prices change, gemini-4-argon's catalog entry changes, an arm pins a Copilot CLI version other than 1.0.105, or Snorkel changes run_judge.py, its prompts or its schemas
---

# Gemini 4 Argon can be the default judge, for about 35 AI credits a patch, if the judge retries an answer that is not JSON

## Intent

- Does `gemini-4-argon` take effort `high`, and where does a session record the effort it ran at?
- Does its taste judge explore the repository without a floor, as `gpt-6.1-sol` does, or skip it, as `claude-sonnet-5.5` did?
- How often does it agree with Sol and `gpt-6-astra` on rubric criteria and on taste dimensions?
- How far do its scores move between two runs of the same patch?
- What does it cost per patch?
- Can it be the default judge?

## Decisions

- **The judge runs `gemini-4-argon` at effort `high` for both judges.** It comes
  from neither family the arms use, and it judged as an exploring judge should.
  With no floor, its taste judge read 3 to 18 files the patch does not change
  in the first run, against Sol's 6 to 20. Its rubric scores matched Sol's on 16 of 17 criteria.
  It cost 34.9 credits a patch for both judges, against Sol's 30.8.
- **`high` is Argon's highest effort, and the judge reads it back where it reads
  GPT's and Claude's.** Copilot's catalog lists `"reasoning_effort": ["low",
  "medium", "high"]` for `gemini-4-argon`. Every session's usage checkpoint
  holds it, in `session.usage_checkpoint` →
  `data.promptCacheBreakState[].models["gemini-4-argon"].reasoning_effort`, as
  `"high"`. Every wire request also carries it as a top-level
  `"reasoning_effort": "high"`.
- **The judge retries once, in the same session, when an answer fails the
  schema.** One of Argon's 16 answers was not JSON. The prefect taste repeat put
  `"events"` inside a rationale string without escaping the quotes, so it would
  have scored nothing. The other 15 were the bare object, with no fence and no
  prose around it.
- **The taste judge needs no exploration floor for Argon.** Without one, its
  taste judge read fewer than 6 such files only once in 7 sessions: 3 on the
  paperless patch, where its repeat read 6. Sonnet's first answer, by contrast,
  came every time before it had read any such file.
- **Argon's taste scores sit about as far from the GPT models' as Sonnet's do,
  and a second run moves them about half as far.** Argon matched Sol on 21 of
  50 taste dimensions and was within one point on 41, a mean difference of 0.76
  points. Sonnet matched Sol on 14 of them, and Astra on 44. A repeat run moved
  8 of Argon's 20 repeated dimensions, each by one point, or 0.4 on average.
  Argon scores `relative_taste` one point above Sol on 3 of the 5 patches.
  On the two patches senior's own judge scored, Argon's taste scores
  match senior's on 3 of 4 headline values, and Sol's match on none.
- **A 50-patch senior run judged by Argon costs about 1,700 AI credits.** That
  is 34.9 credits a patch for both judges and about 1.4 for the classifier,
  less the rubric judge's 8.6 on the 13 tasks with no rubric. Sol, counted the
  same way, comes to about 1,500.

## Needs your input

- **Whether #189's judge runs `gemini-4-argon`, at about 1,700 credits for the
  50-patch run.** I recommend it. It costs about 13% more than Sol and is from
  neither arm's family. Its rubric judge agrees with Sol's, and its
  taste judge explores without a floor.
- **Whether the judge checks its neutrality before the first comparison.** These
  5 patches are all GPT-written, so they cannot show whether Argon scores GPT and
  Claude patches alike. I recommend judging 5 patches from a Claude arm with
  Argon and Sol, for about 340 credits, before any cross-family comparison is
  published.

## Reference

### The patches and the prompts

The 5 copilot-gpt patches of stage 2, from
`results/senior/copilot-gpt/20261105T205511Z-1`, applied whole, tests
included. Each prompt is the file stage 2 sent, read from the job that built
it, and its SHA-256 equalled the hash stage 2 recorded in every session.

| Patch | Size | Criteria | Rubric prompt | Taste prompt |
|---|---|---|---|---|
| `paperless-ngx-perf-workflow-queries` | 14.8 KB | 4 | `8569ed89…` | `6cac67f8…` |
| `firezone-fix-connlib-align-device` | 5.5 KB | 2 | `c19993a9…` | `a0787ff7…` |
| `prefect-fix-task-run-recorder` | 23.0 KB | 2 | `c9505e26…` | `ec8c7896…` |
| `electric-fix-classify-admission-control` | 28.0 KB | 4 | `3f5944e9…` | `5cea8729…` |
| `better-auth-fix-oauth-provider-return` | 48.0 KB | 5 | `d8f3e299…` | `4688491b…` |

Sizes are of `agent/model.patch`, in thousands of bytes.

### Scores and cost

Credits, files read that the patch does not change, and scores. A rubric
score lists each criterion in order. A taste score is
`practice_alignment`/`relative_taste`. Sol, Astra and Sonnet are stage 2's
sessions, Sonnet's taste judge floored.

| Patch | Judge | Rubric credits | Rubric files | Rubric | Taste credits | Taste files | Taste |
|---|---|---|---|---|---|---|---|
| paperless | Argon | 4.34 | 1 | 1.0 (1 1 1 1) | 18.67 | 3 | 5/5 |
| paperless | Sol | 6.03 | 1 | 1.0 (1 1 1 1) | 13.90 | 6 | 5/5 |
| paperless | Astra | 33.55 | 1 | 1.0 (1 1 1 1) | 79.91 | 8 | 5/5 |
| paperless | Sonnet | 5.00 | 0 | 1.0 (1 1 1 1) | 11.33 | 4 | 3/3 |
| firezone | Argon | 14.18 | 1 | 0.5 (1 0) | 28.29 | 14 | 4/3 |
| firezone | Sol | 5.27 | 1 | 0.5 (1 0) | 20.55 | 18 | 5/2 |
| firezone | Astra | 26.00 | 1 | 0.5 (1 0) | 104.17 | 18 | 5/2 |
| firezone | Sonnet | 4.32 | 0 | 0.5 (1 0) | 15.69 | 7 | 4/3 |
| prefect | Argon | 3.31 | 0 | 0.0 (0 0) | 24.15 | 11 | 4/2 |
| prefect | Sol | 7.33 | 1 | 0.0 (0 0) | 21.49 | 19 | 5/2 |
| prefect | Astra | 34.32 | 1 | 0.0 (0 0) | 97.49 | 15 | 5/2 |
| prefect | Sonnet | 3.54 | 0 | 0.0 (0 0) | 17.44 | 5 | 3/2 |
| electric | Argon | 16.64 | 3 | 0.5 (1 0 0 1) | 30.24 | 14 | 5/4 |
| electric | Sol | 9.04 | 3 | 0.75 (1 1 0 1) | 25.70 | 20 | 5/3 |
| electric | Astra | 57.08 | 4 | 0.75 (1 1 0 1) | 117.37 | 15 | 5/3 |
| electric | Sonnet | 8.40 | 3 | 0.75 (1 1 0 1) | 15.27 | 6 | 4/4 |
| better-auth | Argon | 4.70 | 0 | 0.8 (1 1 0 1 1) | 30.10 | 18 | 5/4 |
| better-auth | Sol | 15.23 | 7 | 0.8 (1 1 0 1 1) | 29.36 | 19 | 5/3 |
| better-auth | Astra | 102.83 | 9 | 0.6 (0 1 0 1 1) | 153.57 | 22 | 4/3 |
| better-auth | Sonnet | 7.68 | 0 | 0.8 (1 1 0 1 1) | 15.02 | 7 | 4/3 |

Sol's paperless file counts are stage 1's sessions, counted from their JSONL.

- **Argon's one rubric disagreement is a reading, not noise.** It scored
  electric's `no_request_handle_mutation` 0 in both runs, where the other three
  models gave 1. Both times it gave the same reason: the patch caches the
  lookup in `request.shape_info`, which skips `get_or_create_shape_handle/1`,
  so a request without a handle can get a 409 when the shape is cleaned in
  between. It also said the agent's own test asserts that behaviour.
- **Argon's rubric judge sometimes scores from the patch alone.** On
  better-auth, in both runs, it answered in one model call without opening a
  file. On prefect it opened only a file the patch changes. Its scores on both
  equalled Sol's, which opened 7 and 1 such files.

Mean per session over the 5 patches, with time from copilot's own
`sessionDurationMs`:

| | Rubric credits | Rubric time | Taste credits | Taste time |
|---|---|---|---|---|
| `gemini-4-argon` | 8.64 | 69 s | 26.29 | 183 s |
| `gpt-6.1-sol`, stage 2 | 8.58 | 23 s | 22.20 | 94 s |

Sol's times are from exec's start to its return, which adds a few seconds.

### Agreement

Over the 17 rubric criteria and the 50 taste dimensions, 10 for each patch:

| Pair | Rubric criteria equal | Taste dimensions equal | Within one point | Mean difference |
|---|---|---|---|---|
| Argon–Sol | 16 | 21 | 41 | 0.76 |
| Argon–Astra | 15 | 23 | 45 | 0.64 |
| Argon–Sonnet | 16 | 13 | 40 | 0.94 |
| Sol–Astra | 16 | 44 | 50 | 0.12 |
| Sol–Sonnet | 17 | 14 | 42 | 0.90 |

Argon differs from Sol most on `abstraction_level`, by 1.6 points on average,
and on `approach_quality`, by 1.2. It rated electric's and better-auth's
`approach_quality` 4 where Sol gave 2. On `practice_alignment` it differs from
Sol by 0.4 on average, and on `relative_taste` by 0.6.

Senior's own judge, `claude-sonnet-4-6`, scored firezone 4/3, and prefect 4/3
with the test files restored. Argon gave 4/3 and 4/2, and Sol 5/2 both times.

### Repeats

The second run fitted 6 of the 10 sessions under the credit stop. Firezone's
taste and rubric judges, and electric's and better-auth's taste judges, were
not repeated.

| Patch | Judge | First | Second | Moved | Files read, first then second | Credits, first then second |
|---|---|---|---|---|---|---|
| paperless | rubric | 1.0 (1 1 1 1) | 1.0 (1 1 1 1) | none | 1, 1 | 4.34, 4.28 |
| prefect | rubric | 0.0 (0 0) | 0.0 (0 0) | none | 0, 0 | 3.31, 3.96 |
| electric | rubric | 0.5 (1 0 0 1) | 0.5 (1 0 0 1) | none | 3, 5 | 16.64, 18.90 |
| better-auth | rubric | 0.8 (1 1 0 1 1) | 0.8 (1 1 0 1 1) | none | 0, 0 | 4.70, 4.65 |
| paperless | taste | 5/5 | 4/4 | 3 of 10 dimensions, each by 1 | 3, 6 | 18.67, 21.92 |
| prefect | taste | 4/2 | 4/3, from the repaired answer | 5 of 10 dimensions, each by 1 | 11, 22 | 24.15, 39.29 |

The prefect repeat's answer scored nothing as it was. Escaping its one
unescaped quote pair gave the scores in the table, which are shown only to
measure movement. That session also read `.git/HEAD` and `.git/packed-refs`,
which the count leaves out. No other session in either stage read under
`.git/`.

### What every session showed

All 16 of Argon's sessions passed every check:

- The repository was unchanged after each one, by stage 1's checks.
- Every wire request named `"model": "gemini-4-argon"`.
- The tools offered were `view`, `grep` and `glob`, the names Claude gets.
- Each session reached only `api.github.com` and
  `api.enterprise.githubcopilot.com`, and none had a tool that could fetch
  anything, so none could reach the upstream fix.

### Traps

- **Argon's wire request is a chat completion, not GPT's `response.create` or
  Claude's messages call.** The effort is a top-level
  `"reasoning_effort": "high"`, which stage 2's check for `"effort":` does not
  match.
- **The usage checkpoint names Argon's vendor `"openai"`.** That entry, beside
  its `reasoning_effort`, holds `"vendor": "openai"` and
  `"api_endpoint": "/chat/completions"`. Copilot's catalog names the vendor
  `Google`. A judge that checks the vendor would fail every Argon session.
- **The host's monotonic clock can stop while a session runs.** The first
  prefect job took 29 minutes, though copilot's own `sessionDurationMs` was
  251 s. Exec returned minutes after copilot had exited. Read a session's time
  from `sessionDurationMs`.

### How it ran

`spike-judge/run3.sh` in `suss-runs/` runs stage 2's `JudgeSpike2Agent` with
the prompts of a stage-2 job, its own ledger
`results/spike-judge/ledger-s3.jsonl`, a stop at 290 credits and a floor of 0:

```bash
./spike-judge/run3.sh <job> <patch> "rubric:gemini-4-argon,taste:gemini-4-argon" \
  --ak 'estimates={"rubric:gemini-4-argon": 17, "taste:gemini-4-argon": 32}'
```

The jobs ran one after another, so each session's check of the ledger saw
every earlier charge. The check refused the last job's first session, at 267.63
credits with a 32-credit estimate. The jobs are `s3-firezone-probe`, `s3-*-1`
and `s3-*-2` in `results/spike-judge/`. `spike-judge/analyse3.py` reads each
trial, and `spike-judge/compare3.py` builds the tables beside stage 2's.

## Where this contradicts the stage 2 note

The stage 2 note is
`gpt-6-1-sol-should-judge-for-about-31-ai-credits-a-patch-as-gpt-6-astra-agrees-at-five-times-the-cost-and-sonnet-will-not-explore.md`.

- It decides the judge runs `gpt-6.1-sol`. This note makes `gemini-4-argon`
  the default, because the arms use both GPT and Claude models.
- It says Sol and Astra never failed the schema, so a retry was a
  recommendation. Argon failed once in 16 answers, so the retry is now needed.

## Limits

- **The 5 patches were all written by GPT.** They say nothing about whether
  Argon scores GPT and Claude patches alike, which needs patches from both
  arms.
- That Argon disagrees with Sol does not show which one is right. Senior's own
  judge scored only two of these patches for taste, one with the test files
  restored.
- 6 of 10 sessions were repeated once. How far firezone's, electric's and
  better-auth's taste scores move is not measured, and two runs give a range,
  not a spread.
- No design-task patch was judged, and the run's largest patches are nearly
  three times better-auth's.
- The stage spent 267.63 credits of its 300. Credits are Copilot CLI's own
  `totalNanoAiu` count, and the plan's usage page was not seen.
- Modal's billing gives no figure per sandbox. Its `__harbor__` app cost $1.08
  between 08:00 and 11:00 UTC, which includes another agent's jobs. This
  stage's 11 jobs held one 2- or 4-CPU, 8 GiB sandbox each for 68 minutes in
  all, which at Modal's list rates is about $0.30 (unverified).
