---
settles: "§ 3 — what a design task's regrade spends and takes in its validation agent, how far its validation score moves on the same patch, and what an unpinned litellm does to it; § 5 — that a senior lane's litellm pin covers the validation agent as well as the judges"
issue: [166]
recorded: 2026-11-04
versions: { senior-swe-bench: "v2026.06 (digest 0e0174e2…)", harbor: 0.22.0, modal: 1.6.3, claude-code: 2.1.293, mini-swe-agent: 2.3.0, litellm: "1.105.7 works, 1.108.0 fails" }
recheck-when: Snorkel publishes a new snapshot or changes test.sh or run_validate.py, mini-swe-agent's pin in test.sh moves off 2.3.0, or litellm releases a version past 1.108.0
---

# A senior validation agent costs about a dollar a regrade, and passed a story once by bending its test

## Intent

- What does a design task's validation agent spend per regrade, in tokens and dollars, and how long does it take?
- Does its validation score move across three regrades of the same patch?
- Does mini-swe-agent's litellm hit the `No module named 'mcp'` error the judges hit on litellm 1.106.0 and later?

## Decisions

- **Budget about $1.10 a regrade for the validation agent, on top of the
  judges.** On `harbor-refactor-optional-sandbox-deps`, three regrades of one
  patch spent $0.86 to $0.99 on mini-swe-agent and $0.14 on the validation-review
  judge, $0.99 to $1.13 in all. The agent ran twice in each, a first pass and a
  retry, for 6.3 to 6.6 minutes, and the verifier took 8 to 9 minutes.
- **Do not read one regrade's `validation_score` as the patch's.** It was 0.0,
  0.0 and 0.333 on the same patch. The 0.333 is a false pass: in regrade 3 the
  agent wrote a test that gave its plain string a `.value` attribute, which the
  story's procedure forbids, and so worked around the very defect the story
  checks. The review judge scored all three runs alike, 2.2, and discarded none.
  `reward` was 0 every time, because the task's own tests failed.
- **The lane's litellm pin must hold for the validation agent too, and it
  does.** mini-swe-agent 2.3.0 pins no litellm. It uses the litellm that
  `test.sh` installed for the judges in the same Python, so the one line
  `litellm>=1.0,<1.106` fixes both. Unpinned, every call raises
  `ModuleNotFoundError: No module named 'mcp'` before it leaves the sandbox.
  The agent then fails as infrastructure, and the trial is invalid, not 0.

## Needs your input

- **Whether suss reports `validation_score` at all, given it moved on the same
  patch.** I recommend `suss compare` show it beside `reward` but not rank arms
  on it until a passing patch has been regraded several times. One false pass in
  three cannot say how often the agent bends a test.
- **Whether to spend about $3.50 more regrading a patch that passes.** The patch
  here failed one of the task's two tests, so most of the stories had nothing
  to pass. A good patch gives the agent more to get wrong. Regrading the task's
  reference solution three times, without the rubric and taste judges, would
  cost about that much.

## Reference

### What a design task's verifier writes about its validation agent

In `/logs/verifier/`, beside what an investigate task writes:

- `validation_results.json`: `validation_score`, `passed_stories`,
  `total_stories`, `passed_cases`, `total_cases`, `review_judge_overall`,
  `review_judge_per_criterion`, `review_judge_discard_recommended`, and under
  `stories` each story's `pass`, `execution` and `reason`. On an infrastructure
  failure it adds `validation_agent_infrastructure_failure`, such as
  `"miniswe exited with ModuleNotFoundError"`.
- `validation_agent_invocations.jsonl`: one line per run of the agent, with
  `invocation`, `exit_status` (`Submitted` when it finished) and `elapsed_ms`.
- `miniswe/NN.trajectory.json`: each run's messages, and under
  `info.model_stats` its `api_calls` and `instance_cost`.
- `validation_scripts/`: the test it wrote for each story.
- `reward.json` gains `validation_score`. A failed validation agent leaves an
  empty `reward.txt` and no `reward.json`, which Harbor records as
  `RewardFileEmptyError`.

`validation_score` is the share of stories that passed. `reward` is 1 only when
the tests pass and `validation_score` is 1.

### The run

From `/Users/jacy/Documents/Dev/suss-runs/m5/166-validation/`, all on Modal.
The task has a small image, `python:3.12-slim` with one clone, and
`va_max_turns = 60`, the lowest any design task sets.

- **The trial**: `adapters/claude-code/` on `claude-haiku-4-5-20251001` with
  `max_budget_usd = 0.4`. It stopped at its budget after 39 turns and 2 min 13 s,
  spent $0.4038, and left a 7,285-byte patch to `pyproject.toml`, `daytona.py`
  and a new `lazy_loader.py`. The patch fails `test_local_backends_not_gated`
  with `NameError: name 'AsyncDaytona' is not defined`.
- **The regrades** ran with `suss.replay:ReplayAgent`, each from a lane copy
  whose `test.sh` differs from Snorkel's in two places. It pins
  `litellm>=1.0,<1.106`, and it drops stages 2 and 3, the rubric and taste
  judges, which the budget could not hold and which `reward.txt` does not read.
  The unpinned regrade kept `litellm>=1.0,<2.0`.
- **Every model call of the regrades went through a proxy** deployed as its own
  Modal app, given to the verifier as `--ve ANTHROPIC_BASE_URL=<proxy URL>`. It
  forwarded each call to `api.anthropic.com`, logged its model, tools and
  `usage`, and priced it at list price. It refused with a 400 any call that would
  take the logged total past a cap, which no call reached.

The proxy's call counts equal the trajectories' `api_calls`: 24 and 30, 19 and
30, 18 and 26. No call was refused, so no regrade was cut off.

### The three regrades

| | Regrade 1 | Regrade 2 | Regrade 3 |
|---|---|---|---|
| validation agent calls, first pass and retry | 24 + 30 | 19 + 30 | 18 + 26 |
| its time, first pass and retry | 133 s + 265 s | 122 s + 261 s | 100 s + 279 s |
| input, cache-read, cache-write, output tokens | 61, 1,361,817, 81,006, 18,588 | 56, 1,243,343, 80,022, 17,969 | 51, 961,304, 71,944, 19,997 |
| its cost | $0.9913 | $0.9428 | $0.8583 |
| review judge: 2 calls, input and output tokens, cost | 30,203, 3,501, $0.1431 | 30,339, 3,220, $0.1393 | 30,348, 2,931, $0.1350 |
| verifier, whole job | 8 min 54 s, 9 min 9 s | 8 min 28 s, 8 min 47 s | 8 min 2 s, 8 min 21 s |
| `validation_score` (cases passed of 11) | 0.0 (0) | 0.0 (0) | 0.333 (2) |
| `verifier_score`, `reward` | 0.5, 0.0 | 0.5, 0.0 | 0.5, 0.0 |

The agent used under a fifth of its step limit, 150 for the first pass and 125
for the retry. Its calls cache: almost all its input was read from cache, at a
tenth of the input price. The review judge's calls do not cache.

Every regrade's first pass failed its smoke test. The pre-retry judge then asked
for 4 to 6 changes, and the retry ran each time. The story that passed in
regrade 3, `custom_string_environment_passes_validation`, says `type()` returns
"a plain str that is NOT an `EnvironmentType` member". Regrades 1 and 2 returned
one and failed on `'str' object has no attribute 'value'`, the defect the patch
left in `base.py`. Regrade 3 returned a `str` subclass with a `value` property.

### The unpinned regrade

With litellm resolved from `<2.0`, each of the agent's two runs tried its first
call 10 times, as mini-swe-agent's retry allows, and each try raised
`ModuleNotFoundError: No module named 'mcp'` from
`litellm/proxy/_experimental/mcp_server/catalog.py`. Each run exited after 253
seconds with `exit_status` `ModuleNotFoundError`. No call reached the proxy.
`run_validate.py` exited 2, `test.sh` wrote an empty `reward.txt`, and Harbor
recorded `RewardFileEmptyError`, 9 min 41 s after the job began. A local
mini-swe-agent 2.3.0 call with tools raises the same error on litellm 1.108.0
and reaches the network on 1.105.7.

### Spend

The issue's budget was $10. An earlier attempt at this spike, on the same task,
was cut off by a reboot after its trial and part of one regrade. Its spend counts
here:

| | Counted from | Spend |
|---|---|---|
| Earlier trial, `claude-sonnet-4-6` | Claude Code's `total_cost_usd` | $1.9868 |
| Earlier regrade, unfinished | the same proxy's log | $2.8167 |
| This trial | Claude Code's `total_cost_usd` | $0.4038 |
| Three regrades and the unpinned one | the proxy's log | $3.2099 |
| Modal, both attempts | `modal billing report`, hourly | under $0.80 |

About $9.20 in all. The Modal figure is an upper bound: Harbor's sandboxes run
under one shared app, `__harbor__`, which other jobs used in the same hours.

The earlier regrade's log is a fourth data point, for a different patch of
27,993 bytes. Its validation agent made 66 calls for $1.1443 and its review judge
2 for $0.1562, close to this run's. Its rubric and taste judges spent $1.5162,
more than the $0.78 to $0.97 an investigate task's judges cost. Its scores were
lost.

## Limits

- One task and one patch, and the patch was incomplete. The agent's behaviour
  on a patch that passes its tests was not seen.
- The regrades skipped the rubric and taste judges, so a full design-task
  regrade's cost is this run's plus theirs, measured once at $1.52.
- Three regrades show that the score can move. They cannot say how often.
- The dollars are token counts at list price, $3 and $15 per million for
  Sonnet 4.6 input and output, $3.75 for a cache write and $0.30 for a cache
  read. mini-swe-agent's own `instance_cost` came to 3 to 4 percent less. The
  Anthropic account's own record was not seen.
- The exact litellm the unpinned regrade installed was not printed. The
  traceback's path exists only from 1.106.0, and 1.108.0 was the newest
  release.
- The `claude_code` validation harness was not run.
