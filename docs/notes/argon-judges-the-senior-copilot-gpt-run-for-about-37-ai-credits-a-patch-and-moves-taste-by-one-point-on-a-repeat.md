---
settles: "§ 3 — what a senior regrade by JudgeAgent records and costs; § 6 — how far suss compare's taste means can be trusted between arms"
issue: [191]
recorded: 2026-11-07
versions: { senior-swe-bench: "v2026.06", copilot-cli: 1.0.105, judge: "gemini-4-argon, effort high", classifier: "claude-haiku-4.5", harbor: 0.22.0 }
recheck-when: the judge's model, effort or prompts change, Copilot CLI is upgraded, or Snorkel changes run_judge.py
---

# Argon judges the senior copilot-gpt run for about 37 AI credits a patch, and moves taste by one point on a repeat

## Intent

- Can JudgeAgent grade a whole senior run with no API key?
- What does it cost, per patch and per run?
- How far do the scores move when the same patch is judged again?

## Decisions

- **A senior run is graded by running JudgeAgent as its regrade, with no API key in Harbor's environment.** The tests give the 25 investigate tasks their reward. The 25 design tasks write an empty reward and count as invalid, because their reward needs the validation agent. Every patch gets judge scores.
- **Give a 50-patch judge job a budget of at least 2,000 AI credits.** Judging the 50 patches cost 1,835 credits over the main and fill jobs, 36.7 a patch against 36 in the spike, and the largest cost 78. The first job's 1,750 left 5 taste judges `skipped:budget`.
- **Run it at `-n 16`.** All 50 trials finished in 33 minutes with no judge timeout. Each session holds its reservation while it runs, 30 credits for a rubric judge and 60 for taste, so at 32 trials the taste reservations alone can pass the budget and make sessions wait.
- **Read a taste difference under about 0.5 between arms as noise.** On repeat, no rubric criterion moved, but 11 of 50 taste dimensions moved by one point.

## Needs your input

Nothing.

## Reference

### The jobs

All three are under `results/senior/copilot-gpt/` in `suss-runs/`, with `.env`'s Anthropic, OpenAI and Portkey keys unset:

```bash
harbor run -y -p lanes/senior -e modal -n 16 -k 1 -a suss.judge:JudgeAgent -m judge \
  --ak source_job=<abs>/results/senior/copilot-gpt/20261105T205511Z-1 --ak credit_budget=1750 \
  --ak adapter=<suss>/adapters/copilot-cli \
  --allow-agent-host api.github.com --allow-agent-host api.enterprise.githubcopilot.com \
  --jobs-dir results/senior/copilot-gpt --job-name 20261105T205511Z-1-regrade
```

| Job | Trials | Credits | Took |
|---|---:|---:|---|
| `20261105T205511Z-1-regrade` | 50 | 1,692.5 | 33 min |
| `20261105T205511Z-1-judge-fill`, the 5 `skipped:budget` trials, `-i` per task, `credit_budget=350` | 5 | 142.6 | |
| `20261105T205511Z-1-judge-repeat`, `credit_budget=300` | 5 | 182.9 | |

The fill job's 5 trials were moved into the regrade, and the trials they replace into `20261105T205511Z-1-regrade.budget-skipped/`. Each test reward was the same in both. The regrade the Anthropic credit stopped is `20261105T205511Z-1-regrade.no-credit/`.

### Statuses

| Segment | Rubric | Taste |
|---|---|---|
| investigate (25) | 16 `ok`, 9 `skipped:no_criteria` | 25 `ok` |
| design (25) | 21 `ok`, 4 `skipped:no_criteria` | 25 `ok` |

2 of the main job's sessions answered on their one retry, a `rubric-retry` and a `taste-retry`. No judge failed.

### The repeat

Each patch judged in the regrade, then again in `-judge-repeat`:

| Task | Rubric | Practice alignment | Relative taste | Taste dimensions moved |
|---|---|---|---|---|
| `better-auth-fix-oauth-provider-return` | 0.8 → 0.8 | 4 → 5 | 3 → 4 | 4 of 10 |
| `firezone-fix-connlib-align-device` | 0.5 → 0.5 | 4 → 4 | 3 → 3 | 1 of 10 |
| `prefect-fix-resolve-race-condition` | 1.0 → 1.0 | 5 → 5 | 4 → 3 | 3 of 10 |
| `electric-fix-resolve-pending-shapes` | 1.0 → 1.0 | 5 → 5 | 4 → 3 | 2 of 10 |
| `paperless-ngx-perf-workflow-queries` | 1.0 → 1.0 | 5 → 5 | 5 → 5 | 1 of 10 |

No rubric criterion moved, out of 18. Every taste dimension that moved, moved by one point.

## Limits

- One arm, so nothing here compares two arms' scores.
- Five repeats, each once, so this is the range of a single repeat, not its spread.
- The scores were not checked against a person's.
- The agent job's trials were not gated against the leak in #186. None of its 50 trials called `api.github.com` for its repository, the model host's `/mcp/`, or read another process's environment.
