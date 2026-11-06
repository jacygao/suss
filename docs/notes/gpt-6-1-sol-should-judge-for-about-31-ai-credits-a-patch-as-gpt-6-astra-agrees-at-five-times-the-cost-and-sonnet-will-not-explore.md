---
settles: "§ 3 — which model the judge runs, what one judged patch costs, and whether the taste judge needs an exploration floor"
issue: [188]
recorded: 2026-11-07
versions: { copilot-cli: 1.0.105, harbor: 0.22.0, senior-swe-bench: "v2026.06", plan: "Copilot Enterprise seat", models: "claude-sonnet-5.5, gpt-6.1-sol, gpt-6-astra, claude-haiku-4.5 (classifier)" }
recheck-when: Copilot's model catalog or its prices change, an arm pins a Copilot CLI version other than 1.0.105, or Snorkel changes run_judge.py, its prompts or its schemas
---

# GPT-6.1 Sol should judge, for about 31 AI credits a patch: GPT-6 Astra agrees at five times the cost, and Sonnet will not explore

## Intent

- Can the harness make the taste judge explore before it scores, and what does that cost?
- How do `claude-sonnet-5.5`, `gpt-6.1-sol` and a stronger model score the same 5 patches, on both judges?
- How far do the models agree, per taste dimension and per rubric criterion?
- How far does one model move between two runs of the same patch?
- What do credits and time come to per judge and model, and is the stronger model worth its cost?
- Which model should #189's judge run, and is a floored Sonnet acceptable?

## Decisions

- **The judge runs `gpt-6.1-sol` at effort `high` for both judges.** Its scores
  match `gpt-6-astra`'s on every rubric criterion but one, and on 44 of 50 taste
  dimensions, the other 6 differing by one point. Its taste judge explored
  without a floor on every patch, reading 18 to 20 files the patch did not
  change on the 4 patches where that was counted. It cost 30.8 credits a patch for both judges on average, against
  161.3 for Astra.
- **The stronger model is `gpt-6-astra`, and it adds nothing worth 5 times the
  cost.** Copilot's catalog lists `gpt-6-astra`, enabled for this seat with
  effort up to `max`, at 5 times `gpt-6.1-sol`'s token prices. The strongest
  Opus listed is `claude-opus-5.5`. Astra's one rubric disagreement scored
  `missing_vs_unsupported_distinguished` 0 on the better-auth patch, where
  Sol, Sonnet and senior's own judge all gave 1.
- **A floored `claude-sonnet-5.5` is not an acceptable judge.** The floor was
  6 distinct files that the patch does not change, the fewest senior's own
  taste judge read over 11 sessions on this run's patches. Sonnet's first
  answer came every time before it had read one such file. Resumed with a
  nudge, it read more and returned the same answer: in 4 of its 6 floored
  sessions, no dimension of any answer changed. It met the floor in 3 of 6
  sessions. In one, every answer failed senior's schema, missing
  `relative_taste`. On 2 of 5 patches its rubric judge scored without opening
  a file. Its `practice_alignment` differed from both GPT models by 1.2 to 1.4
  points on average, and by 3 on the paperless patch's `abstraction_level`.
- **The judge does not need an exploration floor for `gpt-6.1-sol`, and a floor
  cannot fix Sonnet.** The floor and its nudge were enforced as senior
  enforces its own, by resuming the same session. They were never triggered for either GPT model.
  For Sonnet they cost 61.1 credits over 6 sessions, tripling its taste
  judge's cost from about 5 to about 15 credits, and changed no score.
- **A 50-patch senior run judged by `gpt-6.1-sol` costs about 1,500 to 1,700
  AI credits.** 31 credits a patch, with the classifier at about 1.4, gives
  about 1,600. Cost grows with patch size: the 48 KB better-auth patch took
  44.6 credits, and the run's patches average 34 KB, up to 136 KB. 13 of its 50
  tasks have no rubric, so their rubric judge is skipped.
- **The model matters for the taste judge, not for the rubric judge.** The
  three models gave the same score on 16 of the 17 rubric criteria, Astra's
  being the exception. Sol and Sonnet also matched senior's own judge on every
  criterion of the 4 patches it scored.

## Needs your input

- **Whether #189 judges with `gpt-6.1-sol`, at about 1,600 credits for the
  50-patch run.** I recommend it. `gpt-6-astra` would cost about 8,000 for the
  same scores, and Sonnet's taste scores are not anchored to the repository.
- **How far one model moves between runs is still unmeasured.** The noise step
  was not run, because step 1 alone took the spike past its credit cap. I recommend #189
  judge 5 patches twice with `gpt-6.1-sol` before the full run, for about 155
  credits.
- **Whether the judge retries an answer that fails the schema.** Sonnet's
  invalid answer would have scored nothing. Sol and Astra never failed. I
  recommend one retry in the same session, with the validation error.

## Reference

### The patches

The copilot-gpt patches of investigate tasks with rubric criteria, from
`results/senior/copilot-gpt/20261105T205511Z-1`, applied whole, tests
included:

| Patch | Trial | Size | Criteria |
|---|---|---|---|
| `paperless-ngx-perf-workflow-queries` | `__Jy9UrUs` | 14.8 KB | 4 |
| `firezone-fix-connlib-align-device` | `__LZtM57Z` | 5.5 KB | 2 |
| `prefect-fix-task-run-recorder` | `__KQc8RvH` | 23.0 KB | 2 |
| `electric-fix-classify-admission-control` | `__ytoTsPv` | 28.0 KB | 4 |
| `better-auth-fix-oauth-provider-return` | `__KmdwG3z` | 48.0 KB | 5 |

Every model on a patch got the same prompt bytes. On paperless they are stage
1's, with SHA-256 `8569ed89…` for the rubric and `6cac67f8…` for taste, so stage
1's Sol sessions and Sonnet rubric were reused. On the others, one job built
the prompts once, after one classifier run, and gave them to all three models.

The repository was unchanged after all 36 sessions, by stage 1's checks, and
every call used the model and effort asked for. No session reached a host but
`api.github.com` and `api.enterprise.githubcopilot.com`, and none had a tool
that could fetch anything.

### Scores and cost

Credits, files read that the patch does not change, and scores. A rubric
score lists each criterion in order. A taste score is
`practice_alignment`/`relative_taste`.

| Patch | Model | Rubric credits | Rubric files | Rubric | Taste credits | Taste files | Nudges | Taste |
|---|---|---|---|---|---|---|---|---|
| paperless | Sonnet | 5.00 | stage 1 | 1.0 (1 1 1 1) | 11.33 | 4 | 2, floor unmet | 3/3 |
| paperless | Sol | 6.03 | stage 1 | 1.0 (1 1 1 1) | 13.90 | stage 1 | 0 | 5/5 |
| paperless | Astra | 33.55 | 1 | 1.0 (1 1 1 1) | 79.91 | 8 | 0 | 5/5 |
| firezone | Sonnet | 4.32 | 0 | 0.5 (1 0) | 15.69 | 7 | 3 | 4/3 |
| firezone | Sol | 5.27 | 1 | 0.5 (1 0) | 20.55 | 18 | 0 | 5/2 |
| firezone | Astra | 26.00 | 1 | 0.5 (1 0) | 104.17 | 18 | 0 | 5/2 |
| prefect | Sonnet | 3.54 | 0 | 0.0 (0 0) | 17.44 | 5 | 4, floor unmet | 3/2 |
| prefect | Sol | 7.33 | 1 | 0.0 (0 0) | 21.49 | 19 | 0 | 5/2 |
| prefect | Astra | 34.32 | 1 | 0.0 (0 0) | 97.49 | 15 | 0 | 5/2 |
| electric | Sonnet | 8.40 | 3 | 0.75 (1 1 0 1) | 15.27 | 6 | 2 | 4/4 |
| electric | Sol | 9.04 | 3 | 0.75 (1 1 0 1) | 25.70 | 20 | 0 | 5/3 |
| electric | Astra | 57.08 | 4 | 0.75 (1 1 0 1) | 117.37 | 15 | 0 | 5/3 |
| better-auth | Sonnet | 7.68 | 0 | 0.8 (1 1 0 1 1) | 15.02 | 7 | 2 | 4/3 |
| better-auth | Sol | 15.23 | 7 | 0.8 (1 1 0 1 1) | 29.36 | 19 | 0 | 5/3 |
| better-auth | Astra | 102.83 | 9 | 0.6 (0 1 0 1 1) | 153.57 | 22 | 0 | 4/3 |

A second floored Sonnet taste run on paperless, with the nudge naming the
count read so far and up to 4 nudges, read 4 such files and gave answers
that all failed the schema, for 18.37 credits. Stage 1's unfloored Sonnet scored that patch 4/3.

Mean per session, over the 5 patches:

| | Rubric credits | Rubric time | Taste credits | Taste time |
|---|---|---|---|---|
| `claude-sonnet-5.5`, floored taste | 5.79 | 11 s | 14.95 | 47 s |
| `gpt-6.1-sol` | 8.58 | 23 s | 22.20 | 94 s |
| `gpt-6-astra` | 50.76 | 24 s | 110.50 | 105 s |

The classifier took 0.59 to 1.30 credits for each of a patch's two sessions.

Mean absolute difference per taste dimension, over the 5 patches:

| | Sonnet–Sol | Sonnet–Astra | Sol–Astra |
|---|---|---|---|
| `style_consistency` | 1.2 | 1.2 | 0.0 |
| `pattern_adherence` | 0.6 | 0.6 | 0.0 |
| `library_usage` | 1.0 | 1.0 | 0.0 |
| `abstraction_level` | 1.2 | 1.2 | 0.4 |
| `documentation_fit` | 1.0 | 0.8 | 0.2 |
| `minimality` | 0.8 | 0.6 | 0.2 |
| `approach_quality` | 0.8 | 0.8 | 0.0 |
| `hygiene` | 0.6 | 0.4 | 0.2 |
| `fluency` | 1.2 | 1.2 | 0.0 |
| `craftsmanship` | 0.6 | 0.4 | 0.2 |
| `practice_alignment` | 1.4 | 1.2 | 0.2 |
| `relative_taste` | 0.8 | 0.8 | 0.0 |

Senior's own judge, `claude-sonnet-4-6` through litellm, graded two of these
patches in the run's stopped regrade, with each criterion as here. On
firezone, whose patch has no test files, it gave taste 4/3. On prefect,
judged with the test files restored, it gave 4/3.

### The floor

After each taste session, the harness lists the paths of the session's
successful `view` calls from the JSONL's `tool.execution_start` and
`tool.execution_complete` events. It keeps those that `test -f` finds in the
workspace, then drops the files the patch changes. Fewer than 6 left, and the
session is resumed with this message, at most 4 times:

```
Explore the codebase with the read-only tools first (read at least 6 files that the patch does not change; you have read N so far) before scoring, then end with the final reply the instructions ask for: only the JSON object.
```

The first floored session used 2 nudges, without the count. The first call is
`copilot -p` with `--session-id <uuid>`. A nudge is `copilot -p "<nudge>" --resume <uuid>`
with the same flags and the same `COPILOT_HOME`, which keeps the
conversation. `totalNanoAiu` counts on across a resume, so a session's credits
are its last attempt's largest checkpoint. That held in every resumed
session.

### Traps

- **`git diff HEAD` leaves out files the patch adds.** Comparing it with the
  patch flagged the electric and better-auth patches as not applied. `git
  apply --verbose` had applied every file, and the judges read the added test
  file.
- **Sonnet put prose before its JSON once.** The firezone rubric answer began
  with a sentence, so the judge has to find the object, not only strip a fence.
- **Astra's credits grow with patch size faster than Sol's.** It took 79.9 for
  the paperless taste judge and 153.6 for better-auth, three times the size.

### How it ran

`JudgeSpike2Agent` in `suss-runs/spike-judge/judge_spike2.py`, outside the
repository, is stage 1's agent with the floor, a list of `judge:model`
sessions, reuse of an earlier job's prompts, and a credit ledger. One Harbor job per
patch on Modal, from `suss-runs/`:

```bash
./spike-judge/run2.sh <job> <patch> "rubric:claude-sonnet-5.5,taste:claude-sonnet-5.5,rubric:gpt-6.1-sol,taste:gpt-6.1-sol,rubric:gpt-6-astra,taste:gpt-6-astra"
```

The jobs are `s2-paperless-1`, `s2-paperless-2`, `s2-firezone-1`,
`s2-prefect-1`, `s2-electric-1` and `s2-betterauth-1` in
`results/spike-judge/`. Each trial's `judge-host/judge_spike.json` holds every
session and attempt. `spike-judge/analyse2.py` checks the wire model, effort
and hosts from each attempt's debug log, and `spike-judge/compare.py` builds
the tables.

## Where this contradicts the stage 1 note

The stage 1 note is
`copilot-cli-judges-a-senior-patch-read-only-for-31-ai-credits-and-sonnets-taste-judge-barely-explores.md`.

- It says one patch costs about 31 credits for both models together. Over 5
  patches, `gpt-6.1-sol` alone averaged 30.8. The paperless patch is the
  cheapest of the five for Sol's taste judge.
- It suggests a judge could make Sonnet explore by resuming the session with
  senior's nudge. Resuming made it read more files, but it did not change the
  answer.
- It says every judge answer was the bare object. Sonnet's firezone rubric
  answer was not.

## Limits

- **The stage spent 1,065.07 credits, past its cap.** The cap was 1,000 in
  total, and the stop was about 980. Stage 1 had spent 32.78, so the spike has
  spent 1,097.85. Step 1's projection, made from one Astra
  session on the smallest-cost patch, assumed about 115 credits per patch for
  Astra, and it took 161 on average. The five jobs ran at once, so each
  passed the ledger's check before the others had charged.
- No repeat run was made, so how far one model moves between runs is not
  measured. The only repeat, Sonnet on paperless, changed its nudge and failed
  the schema, so it says nothing about noise.
- Five patches, all passing or partly passing the rubric, from five
  repositories. No design-task patch was judged, and the run's largest patches
  are nearly three times better-auth's.
- Senior's own judge scored only two of these patches for taste, and one of
  those with the test files restored.
- That GPT models agree with each other does not show they are right. They
  share a family, and they diverge from senior's Sonnet 4.6 by one point
  on `practice_alignment` and on `relative_taste`, on both patches it scored.
- The Modal spend was not read from Modal's billing. The six jobs held one
  2- or 4-CPU, 8 GiB sandbox each, for 44 minutes in all, which at Modal's list
  rates is about $0.20 (unverified).
- Credits are Copilot CLI's own `totalNanoAiu` count. The plan's usage page was
  not seen.
