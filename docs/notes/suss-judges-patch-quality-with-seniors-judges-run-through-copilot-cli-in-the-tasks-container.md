---
settles: "§ 3 — how a trial's patch gets its rubric and taste scores without an API key, and what the trial records of the judge; § 6 — where suss compare reads the judges' scores from"
issue: [187]
recorded: 2026-11-07
versions: { senior-swe-bench: "v2026.06", copilot-cli: 1.0.105 }
recheck-when: the spike in #188 finds Copilot CLI cannot run the judges, Snorkel changes run_judge.py or its prompts, or API credit for the judges becomes affordable
---

# Suss judges patch quality with senior's judges, run through Copilot CLI in the task's container

## Intent

- How can a run get code-quality scores when it cannot pay for the judges' API calls?
- Which client runs the judge first?
- Where does the judge run?
- What does it reuse from Senior SWE-Bench, and what does it leave out?
- When does the specification change?

## Decisions

- **Suss runs senior's rubric and taste judges itself, through a coding-agent
  CLI on a subscription, not through litellm and an API key.** A senior regrade
  costs about $0.86 of Anthropic credit per task for the judges, and about $1
  more per design task for the validation agent. That is roughly $100 per
  50-task run, which the project cannot fund. A Copilot subscription's AI
  credits are already paid for.
- **Copilot CLI is the first client. Claude Code waits, in #193.** Copilot CLI
  already signs in inside Modal sandboxes for the copilot-cli arms. Running a
  Claude subscription in a cloud sandbox needs Anthropic's terms read first.
- **The judge runs in the task's container with the trial's patch applied, as a
  Harbor agent like `ReplayAgent`.** Both judges explore the repository before
  they score, with `read_file`, `list_directory` and `search_code`, and the
  taste judge takes up to 15 turns. A judge given only the diff would be a
  different judge.
- **The judge reads its prompts, rubric and schemas from each task's `tests/`.**
  Suss keeps no copy, so a new snapshot changes the judge with the tasks.
- **The judge stays out of the verifier, and the dataset is unchanged.** The
  verifier runs after the judge with no key, so it grades by tests alone: the
  test reward of an investigate task, and nothing for the judges.
- **One judge model scores every arm in a comparison.** A judge may favour its
  own model's patches, so arms judged by different models are not compared on
  their scores. The spike measures how far `claude-sonnet-5.5` and
  `gpt-6.1-sol` agree.
- **The scores are suss's, not senior's.** Copilot CLI adds its own system
  prompt and tools, and exposes no temperature or thinking budget. The scores
  compare arms with each other, not with Snorkel's published numbers.
- **The validation agent is out of scope.** Senior's 25 design tasks stay
  ungraded until it has a subscription client too.
- **Runs already finished are judged from what they kept.** Every trial keeps
  its patch at `agent/model.patch`. All 50 of the senior copilot-gpt run
  `20261105T205511Z-1` do, so it needs no agent rerun.
- **`docs/notes/` records the decisions until the work lands, and the
  specification changes last, in #194.** Writing the specification first would
  mean rewriting it after each finding.

## Needs your input

- **Which model judges.** I recommend choosing after the spike in #188, from
  how far the two models agree and how much each costs. `claude-sonnet-5.5` is
  the closer to senior's default, `claude-sonnet-4-6`.
- **The spike's budget.** #188 asks for at most 300 AI credits and $5 of Modal.
  It starts only once you confirm that.

## Reference

- Senior's judges, in each task's `tests/`:
  - `run_judge.py`, with `python3 /tests/run_judge.py rubric` and `taste` as its entry points
  - `ssb_lib/llm_tools.py`, holding the three tools
  - `ssb_lib/taste_judge/taste_judge_prompt.md`
  - `judge/rubric.json` and `judge/oracle.patch`
- Every model call goes through `complete_structured` in
  `ssb_lib/llm_utils.py`, a forced tool call parsed into a pydantic schema.
- The senior copilot-gpt run's stopped regrade, `20261105T205511Z-1-regrade`,
  has to be moved aside before the run is graded.

## Limits

- Nothing here was run. Whether Copilot CLI can act as the judges, read the
  repository without changing it, and end in valid JSON is the spike's
  question.
- Static quality metrics, such as new lint warnings or type errors, were
  discussed as a cheaper signal and not chosen.
