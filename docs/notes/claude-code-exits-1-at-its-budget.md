---
settles: "§ 4 — the exit code Claude Code returns when it stops at --max-budget-usd, and how adapters/claude-code/ tells that stop from a crash"
issue: [5]
recorded: 2026-10-31
versions: { claude-code: 2.1.293 }
recheck-when: the example arm pins a Claude Code version other than 2.1.293
---

# Claude Code exits 1 when it stops at its budget

## Intent

- Which exit code Claude Code 2.1.293 returns when it stops at `--max-budget-usd`.
- How `adapters/claude-code/` tells that stop from a crash.

## Decisions

- `adapters/claude-code/` `run` exits 0 when Claude Code stops at the budget.
  Claude Code exits 1 there, which suss would otherwise mark `harness-failed`,
  the same as a crash.
- `run` recognises the budget stop from Claude Code's JSON result, not from the
  exit code. Exit 1 is also what any other error returns, so the code alone
  cannot tell the two apart.

## Needs your input

- § 4 says the harness stops before its models' spend passes `--max-cost-usd`.
  Claude Code does not: it checks the budget after a turn, and its first turn
  spent $0.153664 against a $0.01 cap. I recommend § 4 say the harness stops
  once its spend passes the cap, since no harness can know a turn's cost before
  making it. Until then, `max_cost_usd` caps a Claude Code arm only to within one
  turn.

## Reference

Run headless with `--output-format json`, a budget stop prints one result
object on stdout and nothing on stderr. These are its fields that mark the stop:

```json
"type": "result",
"subtype": "error_max_budget_usd",
"terminal_reason": "budget_exhausted",
"is_error": true,
"errors": ["Reached maximum budget ($0.01)"],
"total_cost_usd": 0.153664,
"num_turns": 1
```

`subtype` is `error_max_budget_usd` only for this stop, so it is the field to
test. `stop_reason` is `end_turn` and does not mark it.

## Limits

- One run, not two: it already spent three times the $0.05 approved for the
  probe, so the code was not shown to be stable across runs.
- Only `--output-format json` was run. The exit code with the default text
  output was not observed.
- Only the budget stop was observed. The JSON an auth failure, a network
  failure or a crash prints was not.
