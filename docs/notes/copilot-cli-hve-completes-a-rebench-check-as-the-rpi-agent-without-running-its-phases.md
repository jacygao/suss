---
settles: "§ 4 — how adapters/copilot-cli-hve/ loads hve-core and selects its RPI Agent, where RPI's .copilot-tracking/ goes, and the hosts its manifest allows; § 3 — that RPI's files stay out of model.patch"
issue: [148, 149]
recorded: 2026-11-04
versions: { copilot-cli: 1.0.105, hve-core: "55352bb8e9f0622ff40f5c6afa80363adaf6b07f (plugin.json 3.2.2)", harbor: 0.22.0, environment: modal, task: ArcadeData__arcadedb-4281, plan: "Copilot Enterprise seat" }
recheck-when: an arm pins another hve-core commit or Copilot CLI version, or the check lane's first task changes
---

# copilot-cli-hve completes a rebench check as the RPI Agent, without running its phases

## Intent

- Which `--agent` name selects hve-core's RPI Agent from `--plugin-dir`?
- Does a check trial on `rebench` complete `ok`, and does `run.log` show RPI's phases running to implementation?
- Which models ran, and do the trial's AI credits include them all?
- What did RPI write in the workspace, and did any of it reach `model.patch`?
- Which hosts does the agent phase need?

## Decisions

- **`run` passes `--agent hve-core:rpi-agent`.** Copilot CLI names a plugin's
  agents `<plugin>:<file stem>`. An unknown name exits 1 before any model call,
  listing the names it has, so a wrong one cannot leave plain Copilot CLI doing
  the task.
- **The adapter works under suss, and the trial resolves the task.** Two check
  trials ended `ok`, `run` exiting 0, and both regrades gave reward 1.0.
- **RPI's phases did not run, in either trial.** The RPI Agent was selected, but
  it never activated `rpi-research`, `rpi-plan`, `rpi-implement` or
  `rpi-review`, started no subagent and wrote no state. It read the code, made
  the change and tested it, as plain Copilot CLI does. Whether that is what the
  arm should measure is #149.
- **Only the arm's model ran, and the checkpoint counts it.** Every
  `model.call_start` named `claude-sonnet-5.5`, and the last
  `session.usage_checkpoint` lists that model alone. No Haiku subagent ran, so
  whether their credits are counted was not seen.
- **`.copilot-tracking/` is a link to `/logs/agent/copilot-tracking/`, ignored
  through `.git/info/exclude`.** Each trial's `agent/` held an empty
  `copilot-tracking/`, and each `model.patch` held only the task's files. The
  tests show RPI's files reach the logs through the link, even when `run` is
  killed, and stay out of the captured patch.
- **The manifest keeps `copilot-cli`'s two hosts.** Both trials completed with
  only `api.github.com` and `api.enterprise.githubcopilot.com` allowed. hve-core
  defines no MCP server, and its two canvas extensions load only with
  `--experimental`, which `run` does not pass.

## Needs your input

- **Whether a copilot-cli-hve trial must run RPI's phases.** Filed as #149. I
  recommend running the full lane before deciding: one easy task, solved in two
  minutes, is weak evidence about what RPI does on a harder one.
- **`max_ai_credits` on this arm.** Copilot CLI shows the limit to the model. In
  a local run on a one-line bug, with a limit of 100, Claude Haiku 5.5 as the RPI Agent skipped the
  loop it was asked for, "Given the budget constraints". The second check
  trial, with no limit, behaved as the first. I recommend measured arms of this
  adapter set none and rely on `max_seconds`.

## Reference

### The trials

`suss.toml`, with `.env` and `swe-rebench-07-2026` linked beside it; the second
trial dropped `max_ai_credits`:

```toml
environment = "modal"
k = 1
concurrency = 1
max_seconds = 2700

[arms.copilot-hve]
harness = "<worktree>/adapters/copilot-cli-hve"
params = { version = "1.0.105", model = "claude-sonnet-5.5", effort = "high", max_ai_credits = 1000, hve_core_commit = "55352bb8e9f0622ff40f5c6afa80363adaf6b07f" }
```

```bash
suss run copilot-hve rebench --check
```

| Trial | `max_ai_credits` | `run_seconds` | `totalNanoAiu` | Model calls | Patch | Reward |
|---|---|---|---|---|---|---|
| `20261104T030323Z-1` | 1000 | 158.1 | 36,808,110,000 | 14, all `claude-sonnet-5.5` | 5222 bytes, 3 files | 1.0 |
| `20261104T030918Z-1` | none | 135.2 | 29,332,460,000 | 15, all `claude-sonnet-5.5` | 3164 bytes, 2 files | 1.0 |

### What shows the RPI Agent was selected

- The checkpoint's `promptCacheBreakState[].models.claude-sonnet-5.5.system_segments`
  holds `selected_agent_instructions`, 8,537 tokens, beside the task repository's
  `custom_instructions`, 6,563.
- The first `assistant.message`: "Starting with Research: finding the
  JUL-to-SLF4J bridge." Its reasoning: "keeping things frugal rather than
  setting up heavy state tracking. Best to just investigate directly and apply a
  minimal fix."
- No `tool.execution_start` has `toolName` `skill` or `task`, and no event type
  starts with `subagent`. These are what a phase or a subagent would leave.

### Copilot CLI and the plugin

- `copilot --plugin-dir <hve-core> plugin list` lists `hve-core (v3.2.2)` under
  "External Plugins (via --plugin-dir)".
- hve-core's root `plugin.json` lists 61 agents, 86 skills, 53 rules and one
  extensions directory, `.github/extensions/hve-core`, holding the
  `rpi-workflow` and `session-log` canvases.
- The RPI Agent's file sets no `model:`, so it runs on `--model`. `RPI
  Researcher`, `RPI Reviewer` and `RPI Plan Critic` set
  `model: Claude Haiku 5.5 (copilot)`. `RPI Researcher` also lists the tools
  `web` and `microsoft-docs/*`, an MCP server nothing configures.
- GitHub's archive of a commit,
  `https://codeload.github.com/microsoft/hve-core/tar.gz/<commit>`, is 12 MB and
  unpacks to `hve-core-<commit>/`, 50 MB.

### Traps

- The `-p` JSONL never names the selected agent. Only the checkpoint's
  `selected_agent_instructions` segment shows it.
- On Modal, `agent/` is downloaded when the trial ends, so `copilot-tracking/`
  cannot be watched while RPI writes it.

## Limits

- One task, which both trials solved in under three minutes. A harder task may
  make RPI run its phases.
- On Modal, the hosts a run tried and was refused cannot be seen, only that it
  completed with the two allowed. Maven's attempt to download Surefire online
  failed, which shows the agent phase was held to the list.
- That RPI's files reach the trial's `agent/` through the link was shown by the
  tests alone, since RPI wrote none in either trial.
- The credits are Copilot CLI's own count. The plan's usage page was not seen.
