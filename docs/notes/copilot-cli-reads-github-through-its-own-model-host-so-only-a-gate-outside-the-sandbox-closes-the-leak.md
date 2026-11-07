---
settles: "§ 4 — the hosts and paths a Copilot CLI harness needs, and which credential it holds during a run; § 5 — why a host allowlist cannot keep a Copilot CLI trial from GitHub, and what can"
issue: [186]
recorded: 2026-11-07
versions: { copilot-cli: 1.0.105, harbor: 0.22.0, modal: 1.6.3, mitmproxy: 11.0.2, swe-rebench: "swe-rebench-07-2026, task ArcadeData__arcadedb-4281", senior-swe-bench: v2026.06, plan: "Copilot Enterprise seat, fine-grained token" }
recheck-when: an arm pins a Copilot CLI version other than 1.0.105, the token is not a fine-grained one, GitHub moves its MCP server off the Copilot model hosts, or Harbor gains a network policy by path
---

# Copilot CLI reads GitHub through its own model host, so only a gate outside the sandbox closes the leak

## Intent

- What Copilot CLI 1.0.105 requests from `api.github.com` during a run, and when.
- Whether the token Copilot CLI uses expires during a run.
- Which fixes keep Copilot CLI working while the agent's shell can no longer
  read repositories, issues or pull requests from GitHub.
- What the recommended fix costs the comparison.
- Whether a senior task's own `allowed_hosts` opens GitHub.

## Decisions

- **Dropping `api.github.com` from the manifests does not close the leak.** The
  model host `api.enterprise.githubcopilot.com` serves GitHub's MCP server at
  `/mcp/`. With the arm's token, its `pull_request_read` tool returned
  pydantic-ai #5580's title and description. Copilot CLI hides the token from
  its shell's environment, but the agent runs as root, and three processes hold
  it in `/proc/<pid>/environ`.
- **A model call can read GitHub too, so the gate must read request bodies.**
  `POST /responses` with `"tools": [{"type": "web_search"}]` ran OpenAI's
  search on the model host. It opened
  `https://github.com/pydantic/pydantic-ai/pull/5580` and
  `https://api.github.com/repos/pydantic/pydantic-ai/pulls/5580`, and answered
  with the fix's title. Copilot CLI itself sends only `function` and `custom`
  tools, so a gate can refuse every other tool type without changing its calls.
- **Run Copilot CLI through a gate outside the sandbox that holds the token.**
  The sandbox gets a key of the gate's own, shaped like a fine-grained token,
  and only the gate's host. The gate passes Copilot CLI's own paths and swaps
  in the real token. It refuses every other path, `/mcp/` and `/repos/…`
  included, and every model request asking for a server-side tool. A prototype
  gate on Modal, filtering paths only, ran a `copilot-gpt` rebench check to
  exit 0 and reward 1. In the same agent phase, the agent's own
  `curl -s https://api.github.com/repos/pydantic/pydantic-ai/pulls/5580` failed
  with `rc=35`, Modal's refusal. With the tool filter added, a second check on
  Modal refused `web_search` and `web_fetch` at the gate, and a GPT model still
  completed through it.
- **Build the gate as a follow-up, #204, not in the adapters now.** It is a
  service that has to run beside every Copilot CLI job, which the adapters
  cannot start, and the spec lets a harness name only hosts.
- **A pre-exchanged token cannot work.** Copilot CLI 1.0.105 never exchanges the
  token. It sends the fine-grained token itself, as `Bearer github_pat_…`, to
  `api.github.com` and to the model host alike. `copilot_internal/v2/token`
  answers 404 for it.
- **Nothing expires during a run.** The only credential is the fine-grained
  token, which expires on the date GitHub set for it, 2026-12-02 10:21:39 UTC
  for this one. A 36-minute session with `api.github.com` refused, and a
  6.6-minute check through the gate, refreshed nothing.
- **Harbor 0.22.0 cannot filter by path or by process.** Its `NetworkPolicy`
  takes hostnames, `*.` wildcards, IPs and CIDRs. Modal's policy is a domain and
  CIDR list, and the Docker sidecar filters by TLS SNI.
- **The gate leaves the model and its calls as they were.** The gated check
  used 38.4 AI credits in 401 s and resolved the task, against 42.7 credits in
  413 s and a resolve for the ungated check of the same task on 2026-11-04. GPT
  models lose the WebSocket transport: the gate refuses the upgrade, and Copilot
  CLI falls back to `POST /responses` for the rest of the session.
- **Senior opens no GitHub host of its own.** All 50 senior tasks'
  `[agent] allowed_hosts`, read from the lane's `task.toml` files, are package
  registries, language and database documentation, and `api.portkey.ai`. None
  is a GitHub host.

## Needs your input

- **Where the gate runs, and the spec it needs.** The spec's Network section
  has the agent phase reach "the hosts in the harness's `allowed_hosts`", and
  says that keeps a harness from a task's real change. For Copilot CLI the
  hosts it needs serve that change, so the sentence is false for it today. I
  recommend that suss start the gate with each job that runs a Copilot CLI
  arm, as a Modal web endpoint on Modal and a local process under Docker, and
  that the spec say a harness may name a gate suss runs. That is a spec
  decision, and it is not made here.
- **The `copilot-cli` arms' existing rebench results.** Three `copilot-gpt` and
  two `hve-sol` trials read GitHub. I recommend leaving them, as #186 found no
  comparison turned on them, and rerunning once the gate exists.

## Reference

### What Copilot CLI requests

Logged by mitmproxy in a fresh container. Copilot CLI honours `HTTPS_PROXY`,
and trusts a CA given in `SSL_CERT_FILE` or `NODE_EXTRA_CA_CERTS`, logging a
`forceLoginOrgs` warning for it. Every request carries the fine-grained token
as `Authorization: Bearer`.

At startup, in this order:

| Host | Request | Answer |
|---|---|---|
| `api.github.com` | `GET /copilot_internal/user` | 200, with `endpoints.api` |
| `api.github.com` | `GET /copilot_internal/managed_settings`, once or twice | 200, keys `model` and `permissions` |
| `api.github.com` | `GET /copilot_internal/team_customizations` | 404 |
| `api.github.com` | `GET /copilot/mcp_registry` | 200 |
| model host | `GET /models` | 200 |
| model host | `GET /responses` with a WebSocket upgrade, GPT models only | 101 direct, 403 from the gate |

Then, per model call, one `POST /v1/messages` for an Anthropic model, or one
`response.create` message on that WebSocket for a GPT model, and nothing else
to either host. Without the WebSocket, a GPT model sends `POST /responses`.

- With `$HOME/.cache/copilot/copilot-user-cache.json` left by an earlier run,
  Copilot CLI skips `copilot_internal/user` and `managed_settings`. It then
  calls `POST https://exp.enterprise.githubcopilot.com/api/v1/assignments`,
  with no token.
- With `api.github.com` refused, Copilot CLI retries `team_customizations` and
  `mcp_registry`, logs a warning, and goes on. Only `copilot_internal/user`
  must answer, or be cached.
- `--model no-such-model` authenticates, loads `/models`, writes the user cache
  and exits 1 before any model call. That is enough to prime the cache in
  `install`.

### The model host's MCP server

```
POST https://api.enterprise.githubcopilot.com/mcp/   initialize          200
POST https://api.enterprise.githubcopilot.com/mcp/   tools/call pull_request_read
     {"method":"get","owner":"pydantic","repo":"pydantic-ai","pullNumber":5580}
→ 200 {"number":5580,"title":"Don't auto-promote `strict=None` tools to strict mode with Bedrock, …","body":"- Closes #5579 …
```

`/mcp/readonly` answered the same. `copilot_internal/user` gives
`is_mcp_enabled: true` for this account. `--disable-builtin-mcps` stops
Copilot CLI calling it, not a shell.

### Server-side tools on the model host

| Request | Answer |
|---|---|
| `POST /v1/messages`, tool `web_fetch_20250910` | 400 `rejected tool(s): web_fetch` |
| `POST /v1/messages`, tool `web_search_20250305` | 400 `The use of the web search tool is not supported.` |
| `POST /responses`, `gpt-5.6-luna`, tool `web_search` or `web_search_preview` | 200, after `web_search_call` items opening the pull request on `github.com` and `api.github.com` |

The `web_search` answer: "The title of PR **#5580** is: **“Don’t auto-promote
`strict=None` tools to strict mode with Bedrock, and skip `strict` field when
`botocore` is too old.”**"

The tools Copilot CLI sent, logged by mitmproxy:

| Model | Path | Tool types |
|---|---|---|
| `claude-haiku-4.5` | `POST /v1/messages` | none set, 20 tools such as `bash`, `edit`, `view` |
| `gpt-5.6-luna` | WebSocket `/responses`, `response.create` | `function` and `custom` (`apply_patch`) |

From a shell Copilot CLI started, as root:

```
in_shell_env=0
procs_holding_it=3
```

### The options

| Option | Works? | Why |
|---|---|---|
| Drop `api.github.com`, prime the user cache in `install` | Copilot CLI completes, the leak stays | The token still reads GitHub through the model host's `/mcp/` and its `web_search` tool |
| Exchange the token in `install` for a Copilot API token | No | `copilot_internal/v2/token` answers 404 to a fine-grained token, and Copilot CLI never asks for one |
| A path-filtering proxy in the sandbox | No | The agent is root, and can read the token from the proxy's environment or go around it |
| Harbor filtering by path or process | No | Harbor 0.22.0 has neither |
| Refusing `curl` in the shell | No | The agent can use any other client |
| A gate outside the sandbox holding the token | Yes | The sandbox holds only the gate's key, and the gate passes only Copilot CLI's paths |

### The gate

Copilot CLI is pointed at the gate by two settings:

- `COPILOT_DEBUG_GITHUB_API_URL=<gate URL>`, in place of `https://api.github.com`;
- the gate's answer to `copilot_internal/user`, with `endpoints.api` replaced by
  the gate's own URL.

The gate passes these paths, and refuses every other with 403:

| Upstream | Paths |
|---|---|
| `https://api.github.com` | `/copilot_internal/user`, `/copilot_internal/managed_settings`, `/copilot_internal/team_customizations`, `/copilot/mcp_registry` |
| `https://api.enterprise.githubcopilot.com` | `/models`, `/models/<id>`, `/v1/messages`, `/chat/completions`, `/responses` |

A request whose bearer is not the gate's key gets 401. A `POST` to the model
host gets 403 when its JSON has a tool whose `type` is set and is neither
`function` nor `custom`, or has the key `web_search_options`, `mcp_servers` or
`container`. The gate drops `Accept-Encoding` on the way out, so it can stream
the reply back unchanged.

The prototype was a Starlette app on Modal, `modal serve` with
`@modal.asgi_app(label="suss-186-gate")`, at
`https://jacygao--suss-186-gate-dev.us-east.modal.run`. The adapter was a copy
of `adapters/copilot-cli/` with:

```toml
allowed_hosts = ["jacygao--suss-186-gate-dev.us-east.modal.run"]
env = ["COPILOT_GITHUB_TOKEN", "COPILOT_GATE_URL"]
```

`COPILOT_GITHUB_TOKEN` held the gate's key, and `run.sh` exported
`COPILOT_DEBUG_GITHUB_API_URL="$COPILOT_GATE_URL"`.

### The proof on Modal

From `suss-runs/results/spike-leak/`, with `suss.toml` holding
`environment = "modal"` and `copilot-gpt`'s params, `version = "1.0.105"`,
`model = "gpt-6.1-sol"`, `effort = "high"`, `max_ai_credits = 1000`:

```bash
suss run copilot-gpt rebench --check -- --ek app_name=suss-186-gated
```

Harbor set the agent phase to:

```
NetworkPolicy(network_mode=<NetworkMode.ALLOWLIST: 'allowlist'>, allowed_hosts=['jacygao--suss-186-gate-dev.us-east.modal.run'])
```

`suss run` exited 0. `suss.json` gave `status` `ok`, exit codes 0 and 0, and a
9890-byte patch. The regrade's `reward.txt` was `1`, and `run.log` ended with
`"exitCode":0`. Copilot CLI's debug log named no host but the gate's.

`run.sh` ran these from the agent phase, as uid 0, before Copilot CLI started
and again 90 s into the run. Both passes printed the same:

```
dns api.github.com: 140.82.114.6    api.github.com
direct api.github.com pulls/5580: http=000 rc=35 | curl: (35) Recv failure: Connection reset by peer
direct api.github.com pulls/5580 with key: http=000 rc=35
direct model host /mcp/: http=000 rc=35
direct github.com: http=000 rc=35
gate pulls/5580 with key: http=403 rc=0 refused by gate: path not allowed
gate /mcp/ with key: http=403 rc=0 refused by gate: path not allowed
gate copilot_internal/user without key: http=401 rc=0 refused by gate: wrong key
gate copilot_internal/user with key: http=200 rc=0 login_present=1 api=https://jacygao--suss-186-gate-dev.us-east.modal.run
```

The name resolved, so `rc=35` is the policy's reset, not a DNS failure or
GitHub's rate limit, which answers 403 with a body.

That gate filtered paths only. Two more checks ran as the arm `gated-haiku`,
with `model = "claude-haiku-5.5"`, `effort = "low"`, and a test prompt in
place of the task's, the first through the same gate and the second through
the gate with the tool filter added:

```bash
suss run gated-haiku rebench --check -- --ek app_name=suss-186-gated-haiku
```

Each exited 0. In each, the agent ran the curl itself and its shell gave
`http=000 rc=35`. It refused to run the step that read the key from `/proc` and
called the gate's `/mcp/`, calling it credential harvesting. The image has no
`python3`, so the agent's Python attempt printed `command not found`. With the
tool filter, `run.sh`'s probe also printed:

```
gate /responses with web_search: http=403 rc=0 refused by gate: server-side tool web_search
gate /v1/messages with web_fetch: http=403 rc=0 refused by gate: server-side tool web_fetch_20250910
```

Under Docker, with mitmproxy standing in for the sandbox's policy and refusing
both GitHub hosts, `gpt-5.6-luna` completed through the filtering gate, sending
`POST /responses` twice, and the gate refused neither.

### A long session

Under Docker, with mitmproxy refusing `api.github.com` inside TLS, a
`claude-haiku-4.5` session primed in a first run with `--model no-such-model`
ran four `sleep 540` calls and a curl, and exited 0 after 2178 s. After its
startup it sent nothing to `api.github.com`, only one `POST /v1/messages` at
each step:

```
11:50:29 to 11:50:30  team_customizations x4 and mcp_registry, refused
11:50:34  POST /v1/messages
11:59:37  POST /v1/messages
12:08:39  POST /v1/messages
12:17:42  POST /v1/messages
12:26:45  POST /v1/messages, then the agent's curl to pulls/5580, refused
```

## Limits

- The gate was a prototype served by `modal serve` for one hour. Its uptime,
  its cost over a full run, and a gate under Docker were not tried.
- One gated check, one task, one attempt. The comparison with the ungated check
  is one run each, so the credit and time differences are not shown to be real.
- No session ran past 36 minutes, against senior's 90. Nothing in Copilot CLI
  refreshed during that time, but a refresh at a longer interval was not ruled
  out.
- Only `api.enterprise.githubcopilot.com` was probed. Whether the Business and
  Individual model hosts serve `/mcp/` too was not seen, though the public
  `api.githubcopilot.com/mcp/` answered the same tool call.
- The model host's paths other than `/mcp/` were not surveyed. The gate refuses
  them all, so this limits the case against dropping `api.github.com`, not the
  gate.
- The tool filter refuses the server-side features tried here and any tool type
  Copilot CLI does not send. A server-side feature turned on by some other
  field of a request body would pass it. The rule refuses
  `web_search_options` on `/chat/completions`, but whether the model host
  would search for it was not tried.
- The full `copilot-gpt` check ran through the gate before it filtered tools.
  Its rerun with the filter was not paid for. Copilot CLI's own tool types, and
  the GPT model completing through the filtering gate, rest on the smaller runs.
- Senior's package registries can serve a release that already holds a task's
  fix. That is outside #186 and was not examined.
- Every proof ran on Modal. Under Docker only mitmproxy stood in for Harbor's
  sidecar, and no `suss run` used the gate under Docker.
