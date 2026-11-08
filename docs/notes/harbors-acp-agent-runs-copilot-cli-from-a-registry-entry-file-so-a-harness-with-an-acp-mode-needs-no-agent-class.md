---
settles: "whether a harness that Harbor has no agent class for can run through Harbor's generic acp agent, what the agent must be given to launch it, and what Harbor records of such a trial"
issue: [214]
recorded: 2026-11-08
versions: { copilot-cli: 1.0.105, harbor: 0.22.0, agent-client-protocol: "unpinned, installed by Harbor at setup", docker-desktop: "host macOS, arm64", plan: "Copilot Enterprise seat" }
recheck-when: Harbor upgrades past 0.22.0 or changes acp.py or acp_runner.py, an arm pins a Copilot CLI version other than 1.0.105, or a harness's ACP mode has no npx, uvx or binary distribution
---

# Harbor's acp agent runs Copilot CLI from a registry entry file, so a harness with an ACP mode needs no agent class

## Intent

- Can Harbor's generic `acp` agent run Copilot CLI over ACP, finish a task and change the repository?
- What must the agent be given to launch a CLI, and how do the token, the model and the CLI's own flags reach it?
- What does Harbor record of the trial: a trajectory, tokens, credits?
- What blocks it?

## Decisions

- **A harness with an ACP mode can run as `-a acp` with no Python of its own.**
  Copilot CLI 1.0.105 appended `done` to `hello.txt` and the verifier gave reward
  1.0, twice, with no exception. The harness is described by a JSON registry
  entry kept beside the arm, not by a subclass. Under suss it still needs
  `SussMixin` for its patch, which one class applying it to Harbor's
  `AcpAgent` would give every such harness. That class was not built here.
- **Give the agent a registry entry file, not a registry id.** An inline entry
  pins the version and carries the CLI's flags in `args`, and Harbor reads it
  without going to the network. `acp:github-copilot-cli` would also resolve, but
  the public registry pins Copilot CLI 1.0.107 with only `--acp`.
- **Pass the token and the model the way any Harbor agent takes them.**
  `--ae 'COPILOT_GITHUB_TOKEN=${COPILOT_GITHUB_TOKEN}'` reached Copilot CLI, and
  `config.json` kept the template rather than the value. `-m claude-haiku-4.5`
  set the session's model through ACP. A model the plan does not offer is not an
  error: `-m gpt-5-mini` left Copilot CLI on its default, `claude-sonnet-5.5`,
  and only `acp-summary.json` says so.
- **Read credits from Copilot CLI's own events, which come back only when
  `COPILOT_HOME` is under `/logs/agent`.** Harbor records tokens and leaves
  `cost_usd` null. With `--ae COPILOT_HOME=/logs/agent/copilot-home`, the
  session's `events.jsonl` comes back with the trial, and its last
  `session.usage_checkpoint` holds `totalNanoAiu`.

## Needs your input

- **Whether suss's next harness goes through `acp` or through a subclass.** The
  generic agent has no credit cap, no effort setting and no checked binary of its
  own; each would have to be a flag in the entry's `args`, and none was tried. I
  recommend the next harness start as an entry file, and get a subclass only for
  something `args` and `--ae` cannot carry.

## Reference

### The entry and the command

```json
{
  "id": "github-copilot-cli",
  "name": "GitHub Copilot",
  "version": "1.0.105",
  "description": "GitHub Copilot CLI over ACP, pinned to the suss arm version",
  "distribution": {
    "npx": {
      "package": "@github/copilot@1.0.105",
      "args": ["--acp", "--disable-builtin-mcps"]
    }
  }
}
```

```bash
harbor run -p <task> -e docker -a acp -m claude-haiku-4.5 \
  --ak registry_entry_path=<entry>.json \
  --ae 'COPILOT_GITHUB_TOKEN=${COPILOT_GITHUB_TOKEN}' \
  --ae COPILOT_HOME=/logs/agent/copilot-home \
  -o <jobs> -n 1 -k 1
```

The first run had `-m gpt-5-mini` and no `COPILOT_HOME`. The task was an
`ubuntu:24.04` image holding a one-commit git repository, with a `test.sh` that
writes reward 1 when the last line of `hello.txt` is `done`.

### What the acp agent takes

- One of `registry_spec` (`-a acp:<id>[@version]`), `registry_entry` (JSON) or
  `registry_entry_path`, or else `source`, a git checkout.
- `distribution_preference`, from `binary`, `npx` and `uvx`. A `binary` target
  is keyed by platform and needs an `https://` archive, with an optional sha256
  `checksum`.
- `permission_mode`, `allow` by default. Copilot CLI asked once per tool call,
  and the runner allowed each.
- `auth_policy` and `authenticate_method_id`. Copilot CLI offers only
  `copilot-login`, a terminal login, so the runner authenticated with nothing and
  the token in the environment was enough.

Harbor writes the launcher as `exec npx -y @github/copilot@1.0.105 --acp
--disable-builtin-mcps "$@"`. Setup installs Python, a venv with
`agent-client-protocol`, and Node through nvm, so it needs the network.

### What Harbor recorded

- `agent/trajectory.json`, converted from the ACP session updates. The steps
  are the instruction, one per tool call, named by ACP kind (`read`, `edit`,
  `execute`), and the final message. Tokens appear only on the last step.
- `agent_result`: `n_input_tokens`, `n_cache_tokens` and `n_output_tokens` from
  the prompt response. The haiku run recorded 28,869, 14,294 and 240.
- `agent/acp-summary.json`: `requested_model`, `resolved_session_model_id`,
  `set_model_error`, `permissions_requested` and the prompt's `stopReason`.
- `agent/acp-events.jsonl`: every session update and permission request.

### Cost

The haiku run took 2.08 AI credits, from `totalNanoAiu` 2,084,440,000. The sonnet
run recorded no credits; at Anthropic's list prices its tokens come to about 5.6
(unverified). The check spent about 7.7 credits in all. The first run took 1 m
58 s and the second 54 s.

## Limits

- Only the `npx` distribution was tried. A pinned binary with a checksum, which
  suss's retired adapter used, was not.
- `--max-ai-credits`, effort and web-tool exclusion through `args` were not
  tried, nor whether `--disable-builtin-mcps` took effect.
- One trivial task, on local Docker only, and no timeout was reached, so what a
  stopped trial leaves is unknown.
- The agent ran as root, and the agent phase had a public network.
