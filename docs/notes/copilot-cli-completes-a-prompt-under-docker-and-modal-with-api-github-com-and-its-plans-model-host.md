---
settles: "§ 4 — the hosts adapters/copilot-cli/ allows, what its run exits with, and what it writes outside the workspace; § 2 — that Modal's per-phase network policy holds Copilot CLI to the same hosts as Docker"
issue: [81]
recorded: 2026-11-02
versions: { copilot-cli: 1.0.105, harbor: "0.22.0 (egress sidecar and modal environment, run by hand)", modal: 1.6.3, image: "swe-bench_pro-v2 flipt-io__flipt-02e21636 (Debian 12, amd64)", docker-desktop: "engine 29.9.2, macOS arm64", plan: "Copilot Enterprise seat" }
recheck-when: an arm pins a Copilot CLI version other than 1.0.105, the token belongs to a plan other than Enterprise, or Harbor changes its egress sidecar or Modal's set_outbound_network_policy
---

# Copilot CLI completes a prompt under Docker and on Modal with only api.github.com and its plan's model host

## Intent

- Does Copilot CLI, installed by the adapter's `install.sh` and run by its
  `run.sh`, change a file and exit 0 in the flipt task's image under Docker?
- The same in a Modal sandbox started as Harbor's `modal` environment starts one.
- Which hosts must the agent phase allow, and what else does Copilot CLI try?
- Does an unknown or unavailable `--model` fail or fall back?
- What does `--output-format json` print, and does it reach `run.log` as it runs?
- What does Copilot CLI exit with on success, at `--max-ai-credits`, and with a
  wrong token?
- What does a run write outside the workspace, and inside it beyond the change?

## Decisions

- **The manifest allows `api.github.com` and `api.enterprise.githubcopilot.com`.**
  The token is an Enterprise seat, and `api.github.com/copilot_internal/user`
  gave it `endpoints.api` `https://api.enterprise.githubcopilot.com`. Under
  Docker and on Modal, removing either host made a run in a fresh sandbox exit 1
  with nothing changed. This replaces the earlier inferred `api.individual.githubcopilot.com`,
  which this token never called.
- **A run that completes exits 0, and `run.sh` passes it through.** Under Docker
  and on Modal, `claude-haiku-5.5` at effort `low` created `NOTE.txt` holding
  `hello`, the workspace's only change, and the last line of `run.log` was a
  `result` with `"exitCode":0`.
- **A model Copilot CLI cannot use fails the run; it never falls back.** A
  nonsense name, a retired one (`gpt-5-mini`) and one the catalog hides from the
  picker (`gpt-4o`) each exited 1 with `Error: Model "<name>" from --model flag is
  not available.` `claude-haiku-4.5`, which takes no effort, exited 1 with
  `Error: Model "claude-haiku-4.5" does not support reasoning effort configuration (requested: "low").` Each run that
  completed called only the model it was asked for.
- **`run.log` holds Copilot CLI's JSONL as it arrives.** It filled while
  Copilot CLI ran: 7 lines 4 s in, 24 at 7 s, and 7,880 lines, arriving
  steadily, over the 5 minutes of a long call.
- **`--max-ai-credits` is not a cap suss can rely on, so the adapter still does
  not pass it.** At 30, one call took the session from 24.7 to 64.82 credits.
  Copilot CLI then skipped the tool call that call asked for and exited 0, at
  once. The limit is also shown to the model, which planned around it
  ("given the limited budget", "~15 AI credits left").
- **Modal's per-phase policy enforces the same list.** After setup with the
  network open, `_experimental_set_outbound_network_policy` with the two hosts
  let Copilot CLI complete and refused every other host tried.

## Needs your input

- **Arms on other Copilot plans.** The model host comes from the token's plan,
  and the manifest names only Enterprise's. A Business or Individual token would
  exit 1 with `Error: Failed to load models`. I recommend adding
  `api.business.githubcopilot.com` and `api.individual.githubcopilot.com`
  (both unverified) when someone has such a token to prove them with. Filed as
  #84.
- **Whether a Copilot CLI arm should have a credit limit at all.** `max_seconds`
  is its only cap. `--max-ai-credits` overshot by 116% in one call and changed
  how the model worked. I recommend leaving it out of measured arms, and
  choosing `max_seconds` with the model's price in mind.
- **`effort` with a model that takes no reasoning effort.** `run.sh` requires
  `effort` and always passes `--reasoning-effort`, so a model that takes none,
  such as `claude-haiku-4.5`, cannot run. I recommend leaving it until an arm needs such a model.

## Reference

### Hosts

What Harbor's egress sidecar (gost at debug) logged for a run in a fresh
container, with the two hosts allowed:

```
allowed  api.enterprise.githubcopilot.com:443 x1
allowed  api.github.com:443 x3
```

- When `$HOME/.cache/copilot/copilot-user-cache.json` was left by an earlier
  run, Copilot CLI also tried `exp.enterprise.githubcopilot.com` once, was
  refused, and completed. It never tried the user response's `proxy`,
  `telemetry` or `origin-tracker` hosts, nor `api.githubcopilot.com`.
- With that cache present, a run with `api.github.com` refused completed. In a
  fresh container it exited 1:

  ```
  Error: Authentication token found but could not be validated.

    Failed to fetch PAT user login: network fetch failed: request failed: error sending request for url (https://api.github.com/copilot_internal/user)
  ```

- With the model host refused:

  ```
  Error: Failed to load models

  Error: error sending request for url (https://api.enterprise.githubcopilot.com/models): client error (Connect): unexpected EOF
  ```

- The plan is in that cache file: `copilot_plan` `enterprise`,
  `access_type_sku` `copilot_enterprise_seat_quota`, and `endpoints.api`,
  `endpoints.proxy`, `endpoints.telemetry`, `endpoints.exp` and
  `endpoints.origin-tracker` under `*.enterprise.githubcopilot.com`.

### Tokens

| Token | `run.sh` exits | Copilot CLI prints |
|---|---|---|
| empty | 1 | `copilot-cli adapter: COPILOT_GITHUB_TOKEN is empty; set it in the .env beside suss.toml` (Copilot CLI not run) |
| `garbage`, not shaped like a token | 1 | `Error: No authentication information found.` |
| `github_pat_` shaped, wrong | 1 | `Failed to fetch PAT user login (401): GitHub returned: Bad credentials` |

### The JSONL

One object per line, with `type` among `session.info`, `session.warning`,
`user.message`, `assistant.turn_start`, `model.call_start`,
`assistant.message_delta`, `assistant.reasoning_delta`,
`assistant.tool_call_delta`, `assistant.message`, `tool.execution_start`,
`tool.execution_complete`, `model.call_finished`, `session.usage_checkpoint`
and `result`. The model is `data.model` on `model.call_start` and
`assistant.message`. Spend is `data.totalNanoAiu` on `session.usage_checkpoint`;
1,000,000,000 nano-AIU is one AI credit, which the limit's own message
confirms. The last line:

```json
{"type":"result","exitCode":0,"usage":{"premiumRequests":1,"totalApiDurationMs":2941,"sessionDurationMs":5611,"codeChanges":{"linesAdded":1,"linesRemoved":0,"filesModified":["/app/NOTE.txt"]}}}
```

`codeChanges` counts only Copilot CLI's edit tools. A file written through
`bash` is missing from it.

At the limit, `run.log` had:

```
{"type":"session.info","data":{"infoType":"session_limits","message":"Session limits: 24.7/30 AI credits used."}}
{"type":"session.warning","data":{"warningType":"session_limits","message":"Session limit reached (64.82/30 AI credits used). Future model calls will stop unless the session limit is increased or removed."}}
{"type":"tool.execution_complete","data":{"success":false,"error":{"message":"Tool execution was skipped because the session limits were reached.","code":"session_limits_exhausted"}}}
```

### Files

Outside the workspace, a run writes:

- `$HOME/.cache/copilot/pkg/linux-x64/1.0.105/`, the unpacked package, 236 MB
- `$HOME/.cache/copilot/copilot-user-cache.json`, the `copilot_internal/user`
  response, with the account's login and plan but no token
- `$HOME/.cache/copilot/managed-settings/`, the organisation's policy
- `$HOME/.cache/Microsoft/DeveloperTools/deviceid`
- `/tmp/node-compile-cache/`
- under `COPILOT_HOME`, which `run.sh` deletes: `config.json`, `session-state/`
  and `installed-plugins/`

Inside the workspace, nothing but the task's change.

### Modal

As Harbor 0.22.0 does for a task whose phases differ:

```python
sb = modal.Sandbox.create("sh", "-c", "sleep infinity", app=app,
    image=modal.Image.from_registry(IMAGE), block_network=False,
    outbound_domain_allowlist=["*"], outbound_cidr_allowlist=["0.0.0.0/0"],
    cpu=1, memory=4096, timeout=3600)
# setup: install.sh
sb._experimental_set_outbound_network_policy(
    outbound_domain_allowlist=["api.github.com", "api.enterprise.githubcopilot.com"],
    outbound_cidr_allowlist=[])
```

A refused host gave `http=000 curl_rc=35`, as under Docker.

### Traps

- A container without a user-defined network gets the host's resolver, which
  the sidecar's rules refuse, so every name fails with `curl_rc=6`. Harbor's
  compose network gives `127.0.0.11`, which they exempt.
- The user cache makes `api.github.com` look unneeded on a second run in the
  same container. Test hosts in a fresh one.
- `web_search` is not a tool in 1.0.105. Copilot CLI prints
  `Unknown tool name in the tool excludedlist: "web_search"` and goes on.

## Limits

- One token, on an Enterprise seat. The Individual and Business hosts were not
  seen.
- Copilot CLI ran under the spike's shim, which added `--max-ai-credits 30` to
  `run.sh`'s command; in some runs it also added `--log-level debug`.
- Harbor itself did not run. Its sidecar and its Modal calls were reproduced by
  hand, and `suss run` was not used.
- On Modal, the hosts Copilot CLI tried and was refused could not be seen; only
  the runs' outcomes were.
- Only the flipt image, under amd64 emulation for Docker.
- The runs used 88.47 AI credits, 82 of them in the two limit tests. The Modal
  sandbox ran about 3 minutes.
