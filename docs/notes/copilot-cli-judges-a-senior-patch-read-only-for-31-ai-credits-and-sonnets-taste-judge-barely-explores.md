---
settles: "§ 3 — the Copilot CLI flags that make it a read-only judge in the task's container, what one judged patch costs, and which of senior's prompt inputs the judge builds itself"
issue: [188]
recorded: 2026-11-07
versions: { copilot-cli: 1.0.105, harbor: 0.22.0, modal: 1.6.3, senior-swe-bench: "v2026.06", task: paperless-ngx-perf-workflow-queries, plan: "Copilot Enterprise seat" }
recheck-when: an arm pins a Copilot CLI version other than 1.0.105, Snorkel changes run_judge.py, its prompts or its schemas, or the judge's models change
---

# Copilot CLI judges a senior patch read-only for 31 AI credits, and Sonnet's taste judge barely explores

## Intent

- Which Copilot CLI flags let it read the repository, never change it, and end in JSON that matches each judge's schema?
- Is the repository byte for byte as the patch left it after both judges have run?
- What AI credits and time does each judge take on a patch?
- Stage 1: how do `claude-sonnet-5.5` and `gpt-6.1-sol` score the copilot-gpt patch of `paperless-ngx-perf-workflow-queries`?
- Stage 2: how often do the two models agree over 4 more patches, judged twice each?
- Does the taste prompt render for a rebench task, and what does it lack there?
- Which prompt inputs does senior's code build, and must the judge build them itself?

## Decisions

- **Run each judge as `copilot -p` with `--available-tools` set to the read
  tools alone, and deny `shell` and `write` as well.** The wire request lists
  every tool the model is offered: `view` for the rubric judge, and `view`,
  `glob` and `grep` or `rg` for the taste judge. No session called any other
  tool, and `codeChanges` was empty in every `result` line.
- **The repository was unchanged after all six sessions.** After each session,
  the hash of `git status --porcelain`, of `git status --ignored`, of
  `git diff HEAD`, the tree `git add -A` would write in a copy of the index,
  and `HEAD` all equalled their values right after the patch applied. `find`
  found no file outside `.git` newer than the apply.
- **Both models, at effort `high`, end in JSON that senior's own pydantic
  schemas accept.** All four judge answers were the bare object, with no prose
  and no fence. The classifier, on `claude-haiku-4.5`, fenced its answer both
  times, so the judge must strip a fence before it validates.
- **One patch judged by both judges costs about 31 AI credits and 4 minutes
  for both models together.** `claude-sonnet-5.5` took 10.3 credits for both
  judges and `gpt-6.1-sol` 19.9, besides 1.3 for the classifier. The whole job,
  including the sandbox and the Copilot CLI install, took 3 min 45 s.
- **`claude-sonnet-5.5` is not yet a usable taste judge.** It scored after
  2 `grep` calls in 2 model calls. Senior's taste judge takes 14 or 15 turns
  and refuses to score before its third. `gpt-6.1-sol` made 33 tool calls in
  7 model calls, starting from `CONTRIBUTING.md` and `docs/` as the prompt
  asks. The two scored the patch 4 and 3 against 5 and 5. Sonnet's
  `abstraction_level` of 2 judged the patch "heavier than the problem needs".
  GPT gave the same choice 5, as matching "existing specialized list
  serializers" it had searched for. Copilot CLI has no turn floor, so the
  judge would have to enforce one, for instance by resuming the session with
  senior's nudge.
- **The rubric judges agree and explore as senior's does.** Both scored every
  criterion 1, for a `rubric_score` of 1.0. Sonnet read 4 file ranges in 4 model
  calls and GPT 8 in 3, against senior's 3 or 4 turns.
- **The judge builds every input senior builds, with senior's own code, before
  it starts Copilot CLI.** The prompt text is then senior's to the byte, and the
  next snapshot changes it with the tasks. The classifier is a Copilot session
  of its own, run before the taste prompt can be built. The inputs, and how far
  the spike's matched senior's, are listed under Reference.
- **The taste prompt renders for a rebench task only if the judge carries
  senior's taste prompt and `run_judge.py`, which rebench tasks lack.** The
  reference patch is in `tests/config.json` as `patch`, identical to the patch
  in `solution/solve.sh` in all 111 tasks. It is the upstream pull request's
  patch, so it may hold docs, which the classifier drops. Before filtering, 10
  of the 111 are over the 12,000-character cap, and a patch still over it after
  filtering arrives truncated, with senior's notice.
  Rebench has no rubric, so only the taste judge applies.
- **Stage 2 is not yet measured.** How often the models agree over more
  patches, and how far one model moves between two runs of the same patch, are
  still open.

## Needs your input

- **Whether stage 2 runs with Sonnet's taste judge as it is.** At stage 1's
  rates, 32 more judge sessions cost about 241 credits, plus about 5 for the
  classifier. With the 32.8 spent, that is about 279 of the 300. A
  Sonnet taste judge made to explore as senior's does would cost more, and
  could pass 300. I recommend stage 2 add a turn floor to the taste judge, and
  judge each patch twice with GPT but once with Sonnet. At stage 1's rates
  that is about 205 credits, which leaves room for a Sonnet taste judge that
  explores.
- **Whether the judge sees the test files the trial changed.** Senior's
  verifier judges everything the agent left, tests included. `ReplayAgent`
  restores every test file the patch changed before the verifier runs, so a
  judge built on it would see a smaller patch. This one had 183 lines of new
  tests. The spike applied the whole patch. I recommend the judge apply the
  whole patch, judge it, and only then restore the tests for the verifier.
- **Which model judges.** Not settled by one patch. GPT explored as the prompt
  asks, and Sonnet did not.

## Reference

### The judge command

As run in `/repo/paperless-ngx`, with `COPILOT_GITHUB_TOKEN` passed through
exec's environment, a fresh `COPILOT_HOME` and `GH_CONFIG_DIR`, `GH_TOKEN`,
`GITHUB_TOKEN` and `COPILOT_MODEL` unset, and `COPILOT_AUTO_UPDATE=false`:

```bash
timeout -k 15 1500 copilot -p "$(cat /opt/judge/prompts/<session>.md)" \
  --model <model> --reasoning-effort high \
  --available-tools <tools> \
  --output-format json --allow-all-tools --deny-tool shell --deny-tool write \
  --no-ask-user --no-custom-instructions --no-auto-update \
  --disable-client-telemetry --disable-builtin-mcps \
  --secret-env-vars COPILOT_GITHUB_TOKEN \
  --log-level debug --log-dir /logs/agent/<session>/logs \
  > /logs/agent/<session>/out.jsonl
```

- `<tools>` is `view` for the rubric judge and the classifier, and `view glob grep rg`
  for the taste judge. The model is offered only the names its family has:
  Claude got `view`, `grep` and `glob`, GPT `view`, `rg` and `glob`.
- The classifier ran on `claude-haiku-4.5` with no `--reasoning-effort`,
  which that model refuses.
- `--available-tools` decides what the model sees. `--deny-tool` only refuses
  a call, so it is the second guard.
- `--no-custom-instructions` keeps an `AGENTS.md` out of Copilot's system
  prompt, where senior's judge has none. This repository has none anyway.
- The prompt is one argument, of 20,302 bytes for the rubric judge and 22,269
  for the taste judge. Nothing was truncated. Linux allows 128 KiB for one
  argument.

### The prompt

Each prompt is senior's system prompt, `---`, senior's user prompt, and this
ending, where `{emit}` is `submit_scores`, `submit_taste_scores` or
`classify_files`, and `{schema}` is that schema's `model_json_schema()`:

```
---

# How to answer in this session

Your tools in this session are not the ones named above. Use `view` to read a file or list a directory, and `glob`, `grep` or `rg` to search: they take the place of read_file, list_directory and search_code. Do not change any file.

You cannot call `{emit}`. Where the instructions above say to call it, end
instead with a final reply that is only the JSON object you would have passed
to it: no other text, and no code fence. It must validate against this JSON
schema:

{schema}
```

The rubric judge's tools line names only `view`, for read_file and
list_directory. Senior's text was captured by running the task's own
`run_judge.py` functions, `_rubric_explore_and_score`,
`classify_patch` and `assess_taste_with_llm`, with
`llm_tools.run_explore_and_emit` and `llm_utils.complete_structured` replaced
by functions that keep their arguments. The answers were validated with the
`RubricResult`, `TasteScores` and `_PatchClassifications` imported from the
task, and scored with its `_flatten_taste_scores`.

### The inputs senior builds

| Input | Senior | The spike |
|---|---|---|
| agent patch | `git diff` from `/var/lib/devcontainer_base_ref` to a commit of the workspace | `model.patch`, applied with `git apply` exit 0, and byte-identical to `git diff HEAD` afterwards; `HEAD` was the trial's `base`, `a1981b06…` |
| changed files, extra reference files | from the patch's `+++` lines and `rubric.json` | the same code: 4 changed, no extra |
| criteria block and names | from `rubric.json` | the same code |
| classified patches | `claude-haiku-4-5-20251001` through litellm, with no thinking | the same prompt on `claude-haiku-4.5` through Copilot CLI, which sent thinking with a 1024-token budget; it dropped `docs/changelog.md` and the test file, as senior's rules say |
| filtered patches | `filter_patch_to_behavioral` | the same code: 4,787 and 3,569 characters, under the 12,000 cap |
| reference patch | `/tests/judge/oracle.patch` | the same file, read on the host |
| `taste_patch_bloat` | `compute_patch_sloc` on the filtered patches | the same code: 68 over 61 SLOC, 1.115, and 3.869 unfiltered; it is not in either prompt |
| system prompt | litellm's `system` message | Copilot CLI's own system prompt first, then senior's at the top of the user message |
| tools | `read_file` with line numbers and a cap of 1000 or 300 lines, `list_directory`, `search_code` | Copilot's `view`, `glob`, `grep` and `rg`; their caps were not read |
| turns | rubric at most 10; taste at least 3 and at most 15 | no limit and no floor |
| answer | a forced `submit_*` tool call | the final message, parsed as JSON |
| sampling | litellm's defaults, `max_tokens` 2048 or 4096 | `temperature` 1, `max_tokens` 128000, Claude `thinking` `adaptive` with `output_config.effort` `high`, GPT `reasoning.effort` `high` |

### The four sessions

| Session | AI credits | Took | Model calls | Tool calls | Scores | Schema | Repo unchanged |
|---|---|---|---|---|---|---|---|
| rubric, `claude-sonnet-5.5` | 5.00 | 11.7 s | 4 | 4 `view` | `rubric_score` 1.0, all 4 criteria 1 | valid, clean | yes |
| taste, `claude-sonnet-5.5` | 5.29 | 14.6 s | 2 | 2 `grep` | practice alignment 4, relative taste 3 | valid, clean | yes |
| rubric, `gpt-6.1-sol` | 6.03 | 43.4 s | 3 | 8 `view` | `rubric_score` 1.0, all 4 criteria 1 | valid, clean | yes |
| taste, `gpt-6.1-sol` | 13.90 | 58.8 s | 7 | 22 `view`, 10 `rg`, 1 `glob` | practice alignment 5, relative taste 5 | valid, clean | yes |

The classifier took 0.65 and 0.63 credits, in one model call each. Credits are
the largest `totalNanoAiu` of a session's `session.usage_checkpoint` events,
over 1e9. A session's time is from exec's start to its return. Every session
exited 0.

The per-dimension taste scores, Sonnet against GPT:

| | Sonnet | GPT |
|---|---|---|
| `style_consistency` | 4 | 5 |
| `pattern_adherence` | 3 | 4 |
| `library_usage` | 4 | 5 |
| `abstraction_level` | 2 | 5 |
| `documentation_fit` | 4 | 5 |
| `minimality` | 3 | 4 |
| `approach_quality` | 3 | 5 |
| `hygiene` | 4 | 4 |
| `fluency` | 4 | 5 |
| `craftsmanship` | 3 | 5 |

### Where to confirm the model and effort

The debug log under `--log-dir` holds each call's body after
`[rust:model_wire] Wire request:`. A Claude call has `"model"`,
`"output_config": { "effort": "high" }` and a `copilotToolsFingerprint` whose
`tools` lists every tool offered, as `view:db6d94fa`. A GPT call is a
`response.create` with `"model": "gpt-6.1-sol"` and
`"reasoning": { "effort": "high" }`. Every call in the four sessions named the
model and effort asked for. The logs also show every connection: only
`api.github.com` and `api.enterprise.githubcopilot.com`, so no session fetched
the upstream fix.

### How it ran

A throwaway Harbor agent, `JudgeSpikeAgent`, in `suss-runs/spike-judge/`, run
from `suss-runs/` with suss's own Harbor:

```bash
PYTHONPATH=spike-judge /Users/jacy/Documents/Dev/suss/.venv/bin/harbor run -y \
  -p lanes/senior/paperless-ngx-perf-workflow-queries -e modal -n 1 -k 1 \
  -a judge_spike:JudgeSpikeAgent -m judge \
  --ak patch=results/senior/copilot-gpt/20261105T205511Z-1/paperless-ngx-perf-workflow-quer__Jy9UrUs/agent/model.patch \
  --ak tests=lanes/senior/paperless-ngx-perf-workflow-queries/tests \
  --ak adapter=/Users/jacy/Documents/Dev/suss/adapters/copilot-cli \
  --ak dotenv=.env --ak base=a1981b06b9aac0761ece1e4b094b8e2e68f0055c \
  --allow-agent-host api.github.com --allow-agent-host api.enterprise.githubcopilot.com \
  --disable-verification --jobs-dir results/spike-judge --job-name stage1-1
```

- `setup` uploads `adapters/copilot-cli/` and runs its `install.sh` with
  `{"version": "1.0.105"}`.
- `run` applies the patch, takes the fingerprint, and runs the classifier
  twice, then the rubric and taste judges for each model. It fingerprints the
  repository again after every session.
- Senior's prompts are built on the host, importing the task's `run_judge.py`
  and `ssb_lib` with `unidiff` 0.7.5 and `pygments` 2.21.0 vendored beside it.
- The record of each session is in the trial's `judge-host/judge_spike.json`,
  and each session's JSONL and debug log in `agent/<session>/`.

### Traps

- A local `copilot` 1.0.105 binary runs a newer downloaded package unless it
  is given `--no-auto-update` or `COPILOT_AUTO_UPDATE=false`. Its `--help` then
  describes that version.
- The rebench taste prompt calls the reference patch an "expert human
  baseline". For rebench that is the upstream pull request's patch, not a
  hand-curated one.

## Limits

- One patch, judged once by each model. Neither model was rerun, so how far
  one model's scores move between runs is not known.
- The patch passed every rubric criterion, so the rubric judges had little to
  disagree about.
- No senior judge scored this patch, so the scores cannot be set beside
  senior's own.
- The Modal spend was not read from Modal's billing. The two jobs held one
  2-CPU, 8 GiB sandbox for about 5 minutes in all. At Modal's list rates that
  is a few cents (unverified).
- The credits are Copilot CLI's own count. The plan's usage page was not seen.
- Whether Copilot's `view` truncates a long file, as senior's `read_file`
  does, was not read.
- The rebench answer comes from reading the tasks. No rebench task was judged.
