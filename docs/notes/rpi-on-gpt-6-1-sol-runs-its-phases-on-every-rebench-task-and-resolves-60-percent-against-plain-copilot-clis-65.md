---
settles: "§ 4 — that adapters/copilot-cli-hve/ on gpt-6.1-sol runs RPI's phases, and how a trial's run.log shows it; § 6 — what a suss compare of copilot-gpt and hve-sol on rebench measures"
issue: [149]
recorded: 2026-11-05
versions: { copilot-cli: 1.0.105, hve-core: "55352bb8e9f0622ff40f5c6afa80363adaf6b07f", harbor: 0.22.0, environment: modal, lane: "rebench, 108 tasks", model: gpt-6.1-sol }
recheck-when: an arm pins another hve-core commit, Copilot CLI version or model, or the rebench lane changes
---

# RPI on gpt-6.1-sol runs its phases on every rebench task, and resolves 60% against plain Copilot CLI's 65%

## Intent

- Did the RPI Agent run its phases in the full `hve-sol` run, or do the tasks as plain Copilot CLI would?
- How does `hve-sol` compare with `copilot-gpt`, plain Copilot CLI on the same model?
- What does an `hve-sol` trial cost?

## Decisions

- **Read `hve-sol`'s result as RPI's, not as Copilot CLI's.** Every one of
  the 108 trials selected the RPI Agent and ran `rpi-research`, `rpi-plan`,
  `rpi-plan-critique` and `rpi-implement`. 99 also ran `rpi-review`, and 107
  wrote RPI's state to `copilot-tracking/`. On Claude Sonnet 5.5 the same
  adapter ran no phase, so whether the phases run depends on the model.
- **RPI did not improve on plain Copilot CLI on this lane.** `hve-sol`
  resolved 60%, against 65% for `copilot-gpt`: a difference of −5% with a 95%
  interval of [−10%, +1%]. One trial per task per arm cannot rule out that the
  two are equal, but it makes a gain unlikely.
- **Budget about 175 AI credits a trial for this arm.** The median trial used
  168 and the most expensive 356. None came near `max_ai_credits = 1000`. The
  whole lane took 19,023 credits over the 106 trials that report them, and the
  agent job took 1 h 27 min on Modal at 32 trials at a time.

## Needs your input

- **Whether to run `hve-sol` and `copilot-gpt` again, k = 2 or more, before
  concluding.** A second trial per task would roughly halve the interval's
  width. I recommend it before anything rests on RPI being worse; it costs about
  $190 in AI credits and $15–25 of Modal per arm.

## Reference

### How a trial's `run.log` shows RPI

- **The agent was selected:** the model call's system prompt holds a
  `selected_agent_instructions` segment. The `-p` JSONL never names the agent
  otherwise.
- **A phase ran:** a `tool.execution_start` event whose `toolName` is `skill`
  and whose `arguments.skill` names the phase:

  ```json
  {"type":"tool.execution_start","data":{"toolName":"skill","arguments":{"skill":"rpi-research"},"model":"gpt-6.1-sol"}}
  ```

- **RPI kept its state:** files under `agent/copilot-tracking/`, such as
  `rpi-sessions/<date>/<slug>/<slug>-state.json`, whose `active_phase` moves
  from `Research` to `Plan` and on.
- **A subagent ran:** an event whose `type` starts with `subagent`, and model
  calls naming a model other than the arm's.

### The run

```toml
[arms.hve-sol]
harness = "<suss>/adapters/copilot-cli-hve"
baseline = "copilot-gpt"
params = { version = "1.0.105", model = "gpt-6.1-sol", effort = "high", hve_core_commit = "55352bb8e9f0622ff40f5c6afa80363adaf6b07f", max_ai_credits = 1000 }
```

`suss run hve-sol rebench`, with `environment = "modal"`, `concurrency = 32`,
`k = 1` and `max_seconds = 2700`. `copilot-gpt` is `adapters/copilot-cli/` with
the same version, model, effort and `max_ai_credits`.

```
arm          trials  resolved  failed  timed out
copilot-gpt     108       65%      0%         0%
hve-sol         108       60%      0%         2%

vs. baseline            resolved
hve-sol - copilot-gpt   -5% [-10%, +1%]
```

### The exceptions

| Trials | What differed |
|---|---|
| `nearai__ironclaw-3694`, `ubugeeei-prod__vize-1508` | `timed-out` at `max_seconds`. `ironclaw-3694` had not reached `rpi-review`. |
| `Gentleman-Programming__gentle-ai-595`, `apache__dubbo-go-3357`, `astral-sh__ruff-25414`, `fallow-rs__fallow-762`, `floci-io__floci-1142`, `microsoft__typescript-go-4062`, `tailscale__tailscale-19847`, `ubugeeei-prod__vize-774` | ended `ok` without `rpi-review`. |
| `better-auth__better-auth-9756` | ran every phase, but its `copilot-tracking/` came back empty. |
| `ubugeeei-prod__vize-774`, `nearai__ironclaw-3694` | started a subagent, on `claude-sonnet-5` and `claude-haiku-5.5`. Every other model call was `gpt-6.1-sol`. |

Trials with `rpi-review` resolved 60 of 99, and those without it 5 of 9.

## Limits

- One trial per task per arm. The two arms' difference is within what a rerun
  could move.
- Only `gpt-6.1-sol`. On Claude Sonnet 5.5 the RPI Agent ran no phase in an
  earlier check, so this result does not carry to another model.
- Whether `better-auth-9756`'s RPI state was written somewhere the link did not
  reach was not found.
- The AI credits are Copilot CLI's own `totalNanoAiu`, divided by 10⁹. Two
  trials report none. GitHub's bill was not checked against them.
- Modal's cost for the run was not final when this was written.
