---
settles: "§ 3 — what a claude-code trial's run.log says it spent, however run ended; § 4 — the output format adapters/claude-code/ runs Claude Code with"
issue: [61]
recorded: 2026-11-01
versions: { claude-code: 2.1.293 }
recheck-when: the example arm pins a Claude Code version other than 2.1.293
---

# Claude Code's stream keeps each model call's token usage in run.log up to a kill

## Intent

- What can a claude-code trial stopped at `max_seconds` record of its spend?
- Which of Claude Code's output formats carries that spend before it exits?
- Does the budget stop still exit 1, and still show as `error_max_budget_usd`, in that format?

## Decisions

- **A stopped trial records tokens, not dollars.** Claude Code prints no dollar
  figure until its result, at exit, and a kill at `max_seconds` never reaches
  it. Its token usage per model call is what reaches `run.log` before the kill.
- **`run` passes `--output-format stream-json --verbose --include-partial-messages`.**
  Without partial messages, the stream's `assistant` lines carry each call's
  output tokens as they stood when the call began: 3 where the call made 196.
  Only the `message_delta` event at the end of each call has the final count.
- **`run` still tests the result's `subtype` for the budget stop, now on the
  stream's last line.** Claude Code exits 1 at its budget in this format too,
  and its last line is the result with `"subtype":"error_max_budget_usd"`.
  Testing only the last line keeps an earlier message, such as a tool's input,
  from passing a crash off as a budget stop.

## Needs your input

- **Whether a stopped trial should also record a cost in dollars.** That needs a
  price per model and token type, which Claude Code's stream does not carry. I
  recommend leaving it to the issue that prices every arm's tokens from one
  table, and keeping `suss.json` as § 3 lists it until then.

## Reference

Each model call's final usage, in its `message_delta` event:

```json
{"type":"stream_event","event":{"type":"message_delta","delta":{"stop_reason":"tool_use",…},
 "usage":{"input_tokens":9,"cache_creation_input_tokens":3206,"cache_read_input_tokens":17758,"output_tokens":196,…}},…}
```

- Sum usage over the `message_delta` events, one per call. Summing the
  `assistant` lines' `message.usage` counts a call once per content block, since
  each block is a line of its own with the same `message.id`, and undercounts
  its output tokens.
- The result's `total_cost_usd` is the run's cost in dollars. On the budget stop
  the result's own `usage` read all zeros while `total_cost_usd` was 0.0067723,
  so read the tokens from the `message_delta` events, not from the result.
- A call cut off by the kill has a `message_start` event, with its input and
  cache tokens, and no `message_delta`. Its output tokens are not in `run.log`.

## Limits

- Observed on claude-haiku-4-5 only, in a Debian container. The stream's shape
  was taken not to depend on the model.
- The killed run was one model call long, so a `run.log` with many calls before
  a kill was not seen.
- How large `run.log` grows over a 45-minute run with partial messages was not
  measured. Each partial message, a few tokens of text or tool input, adds a line of about
  250 bytes.
