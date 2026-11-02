---
settles: "§ 4 — that adapters/copilot-cli/ completes a trial under suss, the hosts its agent phase tries, and where a trial's AI credits are found; § 6 — what `suss run --check` leaves for a copilot-cli arm, and how long a check of pro-go takes under Docker Desktop"
issue: [79]
recorded: 2026-11-03
versions: { copilot-cli: 1.0.105, harbor: 0.22.0, swe-bench-pro: "v2.0.0 (66f92766)", docker-desktop: "engine 29.9.2, kernel 7.0.14-linuxkit, 10 CPUs, 7.7 GiB", host: "macOS, arm64", plan: "Copilot Enterprise seat" }
recheck-when: an arm pins a Copilot CLI version other than 1.0.105, the token belongs to a plan other than Enterprise, Harbor upgrades past 0.22.0, or Harbor changes its egress sidecar
---

# suss run --check of a copilot-cli arm completes ok on Docker Desktop, for 28 AI credits

## Intent

- Does `suss run copilot-cli pro-go --check` complete under Docker, with the trial's `status` `ok`?
- Is the regrade's reward the trial's real result?
- Does `run.log` hold Copilot CLI's messages, and does the agent phase try any host the manifest does not list?
- What does one trial use of the Copilot plan, and where does suss find it?
- How long does the check take on Docker Desktop for Apple Silicon?

## Decisions

- **The adapter works under suss as it is on main.** The trial ended with
  `status` `ok`, `run` exited 0 after 2130 s, and Copilot CLI left a 5987-byte
  patch. `run.log` held its JSONL as it arrived, 544,918 bytes ending in its
  `result` line. Nothing in `adapters/copilot-cli/` changed.
- **The regrade graded the trial's patch, and it resolved the task.** Its
  `replay.json` names this trial's `model.patch` with `apply_rc` 0, and all 5
  files applied cleanly. The reward was 1.0: all 4 required tests passed.
- **The manifest's two hosts are enough, and the agent phase tried no other.**
  Harbor's egress sidecar let through 3 connections to `api.github.com` and 11
  to `api.enterprise.githubcopilot.com`, and refused none. The task's Go builds
  fetched no modules.
- **Read a trial's AI credits from the last `session.usage_checkpoint` in
  `run.log`.** This trial's `totalNanoAiu` was 28,256,090,000, which is 28.26 AI
  credits, about $0.28. Copilot CLI wrote the checkpoint once, 0.2 s before its
  `result` line, so a trial that suss stops at `max_seconds` leaves no figure in
  `run.log`.
- **A check of pro-go now takes about an hour.** The agent job took 36 minutes
  and the regrade 25. Copilot CLI's model calls took 60 s of the 2126-second
  session; the rest was spent waiting on `go build`, `go vet` and `go test`
  under emulation. It ran them one at a time, waiting for or stopping each
  before it started the next.

## Needs your input

- **The credit figure against the plan's usage page.** The issue asks for the
  plan's own record before and after, which could not be seen here. 28.26 AI
  credits, used between 22:13 and 22:49 UTC on 2026-11-02, is Copilot CLI's
  own count. I recommend the owner check it against the page and record both on
  the issue.
- **The spend of a copilot-cli trial that times out.** Copilot CLI reports
  credits only as it finishes, so a `timed-out` trial records none, as Claude
  Code's did before its adapter streamed each call's usage. Copilot CLI's
  JSONL has no per-call usage to stream. Filed as #92. Until it is fixed, I
  recommend reading a timed-out copilot-cli trial's spend from the plan's
  usage page.
- **`effort` in a measured arm.** This arm set none, and Copilot CLI ran
  `claude-sonnet-5.5` at `reasoning_effort` `medium`, its default, which
  `suss.json` does not show. I recommend measured arms set `effort` explicitly,
  so the trial record names the effort the model ran at.

## Reference

### The run

From the worktree root, with `.env` linked beside this `suss.toml`:

```toml
swe_bench_pro = "/Users/jacy/Documents/Dev/SWE-bench_Pro-os"
dataset = "swe-bench-pro-v2"
environment = "docker"
k = 1
concurrency = 1
max_seconds = 2700

[arms.copilot-cli]
harness = "adapters/copilot-cli"
params = { version = "1.0.105", model = "claude-sonnet-5.5", max_ai_credits = 500 }
```

```bash
uv run suss run copilot-cli pro-go --check
```

`copilot help config` lists the catalog's model names, `claude-sonnet-5.5`
among them, without a model call.

| Step | Started (UTC) | Ended (UTC) | Took |
|---|---|---|---|
| Environment and sidecar up | 22:12:50 | 22:12:53 | 3 s |
| Agent setup, Copilot CLI installed | 22:12:53 | 22:13:04 | 11 s |
| `run`, Copilot CLI exited 0 | 22:13:04 | 22:48:37 | 2130 s |
| Regrade | 22:48:47 | 23:13:48 | 25 min |
| All of `suss run` | 22:12:46 | 23:13:49 | 61 min |

The agent job passed `--disable-verification`, so it ran no verifier.
`suss run` exited 0.

### What Copilot CLI did

- 22:13:37, after 16 s of reading, it wrote the change and started
  `go build ./...`. It waited on that build for 17 minutes, then stopped it.
- 22:30 to 22:44, `go vet` on two packages, then `go build ./internal/cmd`,
  about 5 minutes each. It removed a `goredis` import its change had left
  unused, stopping a second `go vet` to do so, and rebuilt in 48 s.
- 22:44:31, `go test ./internal/config -run 'TestJSONSchema|TestLoad'`, which
  passed at 22:48:31.
- 18 model calls, 21 tool calls, and `premiumRequests` 1. No session-limit
  message: the trial never came near the 500-credit limit.

### What `checks/pro-go/copilot-cli/` held

```
20261102T221246Z-1/
  config.json  job.log  lock.json  result.json
  instance_flipt-io__flipt-02e2163__4xiNJ2u/
    agent/  artifacts/  config.json  lock.json  result.json  trial.log  verifier/
    agent/: install.log (284)  model.patch (5987)  run.log (544918)  suss.json (499)
20261102T221246Z-1-regrade/
  config.json  job.log  lock.json  result.json
  instance_flipt-io__flipt-02e2163__ceYW6f5/
    agent/  artifacts/  config.json  lock.json  result.json  trial.log  verifier/
    agent/: replay.json (1041)
```

### The trial record and its spend

`suss.json`:

```json
"status": "ok",
"exit_codes": { "install": 0, "run": 0 },
"run_seconds": 2130.0132066248916,
"patch_bytes": 5987
```

The end of `run.log`, with the checkpoint cut after its first fields:

```json
{"type":"session.usage_checkpoint","data":{"totalNanoAiu":28256090000,"totalPremiumRequests":1,"modelCacheState":[{"modelId":"claude-sonnet-5.5", … 
{"type":"result","timestamp":"2026-11-02T22:48:36.508Z","sessionId":"e50f84b9-5627-441f-83d1-15764b02fb2c","exitCode":0,"usage":{"premiumRequests":1,"totalApiDurationMs":60213,"sessionDurationMs":2125522,"codeChanges":{"linesAdded":0,"linesRemoved":1,"filesModified":["/app/internal/cmd/grpc.go"]}}}
```

The `result` line's `usage` gives no credits, and its `codeChanges` counts only
the one edit made with Copilot CLI's edit tool. The rest of the change was
written through `bash`. `model.call_finished` carries no usage either.

### The regrade

`agent/replay.json`:

```json
"patch": "<dir>/checks/pro-go/copilot-cli/20261102T221246Z-1/instance_flipt-io__flipt-02e2163__4xiNJ2u/agent/model.patch",
"apply_rc": 0
```

`verifier/reward.txt` is `1`, and `test-stdout.txt` ends:

```
Required tests: 4
Passed tests: 15
Required tests that passed: 4

RESULT: PASSED
```

### Hosts

Harbor's sidecar logs at gost's `info` level. An allowed connection has a
`msg` of `<client> <-> <host>:443`. A refused one has `"outputBytes":0`, its
destination only as an IP in `dst`, and is followed by a line with
`"level":"error","msg":"bypass"`. In setup the sidecar is in `allow-all` mode
and logs no connections. Harbor removes the sidecar with the trial, so its log
was kept by following it while the trial ran:

```bash
docker logs -f -t <trial>__env-harbor-docker-egress-control-sidecar-1
```

Every connection it logged:

```
22:13:09 to 22:13:11  api.github.com:443                     x3
22:13:12 to 22:48:31  api.enterprise.githubcopilot.com:443   x11
```

The log held no `bypass` and no `"level":"error"` line.

### Traps

- `docker stats --no-stream` hung on this machine while the trial ran. `docker
  ps` and `docker logs` did not.
- `post_apply_diffstat` in `replay.json` lists 4 files for a 5-file patch. The
  missing one, `internal/cache/redis/client.go`, is new, and the diffstat counts
  only tracked files.

## Limits

- One trial, of one task, at one model. That it passed is not shown to be
  typical, and the hour is not shown to hold for a model that builds more.
- The credit figure is Copilot CLI's own. The plan's usage page was not seen.
- The sidecar's log names an allowed host but shows a refused one only by IP.
  None was refused here, so no refusal had to be read.
- The agent phase ran under the task's 1 CPU and 4 GiB, with amd64 emulated.
  No overrides were passed.
