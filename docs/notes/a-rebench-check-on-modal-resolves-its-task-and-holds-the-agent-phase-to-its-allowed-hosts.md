---
settles: "§ 3 — that on Modal a rebench trial records its grader and its regrade grades its own patch; § 5 — that a rebench task's agent phase on Modal reaches only the harness's `allowed_hosts`; § 6 — what `suss run claude-code rebench --check` leaves under `checks/` with `environment = \"modal\"`, and what it costs"
issue: [124]
recorded: 2026-11-03
versions: { harbor: 0.22.0, modal: 1.6.3, claude-code: 2.1.293, swe-rebench: "swe-rebench-07-2026 (111 tasks)", task: "ArcadeData__arcadedb-4281 (Java, Maven)", host: "macOS arm64 client" }
recheck-when: Harbor upgrades past 0.22.0, Modal changes or stabilises _experimental_set_outbound_network_policy, the rebench download changes, or the claude-code adapter changes
---

# A rebench check on Modal resolves its task and holds the agent phase to its allowed hosts

## Intent

- Does `suss run claude-code rebench --check` on Modal leave an agent job and
  its regrade under `checks/rebench/claude-code/`, with an `ok` trial whose
  result is its regrade's reward?
- In the agent phase of a rebench task on Modal, does the harness reach each
  host in its `allowed_hosts` and no other?
- What does each check cost?

## Decisions

- **Run rebench on Modal as suss runs it now, with no change.** The check
  exited 0 in 8 min 46 s. The trial is `ok` with a 4487-byte patch, records
  the grader `{"dataset": "swe-rebench-07-2026", "digest": "1263fe54…"}`, and
  its regrade applied that patch with `git apply --verbose` and scored 1.
- **Count on the lane's `no-network` to close a rebench agent phase on Modal.**
  A stand-in harness listing `api.anthropic.com` and `api.github.com` reached
  both in its agent phase, and was refused `github.com`, `pypi.org`,
  `repo.maven.apache.org`, `www.google.com` and `1.1.1.1`, at the phase's start
  and 20 s later. In setup, the same probe reached all seven. Claude Code's own
  trial shows the same refusal: its `mvn test` could not download a plugin from
  Maven Central.
- **Expect a claude-code check of rebench to cost about $1.65.** That is $1.62
  of model and about $0.03 of Modal. The probe's check cost under a cent.

## Needs your input

Nothing.

## Reference

### The runs

From `~/Documents/Dev/suss-runs/m4-check/`, which holds `.env` as a link to
the repository's, the download copied in as `swe-rebench-07-2026/`, and
`suss.toml`:

```toml
swe_rebench = "swe-rebench-07-2026"
environment = "modal"
k = 1
concurrency = 32
max_seconds = 2700

[arms.claude-code]
harness = "/Users/jacy/Documents/Dev/suss/adapters/claude-code"
params = { version = "2.1.293", model = "claude-opus-4-6", effort = "high", max_budget_usd = 9 }

[arms.network-probe]
harness = "network-probe"
params = {}
```

```bash
suss run claude-code rebench --check -- --ek app_name=suss-124-claude-code
suss run network-probe rebench --check -- --ek app_name=suss-124-network-probe
```

`--ek app_name` puts each agent job in its own Modal app, so the billing
report gives its cost. It reaches only the agent job, so both regrades ran in
Harbor's default app, `__harbor__`.

The check's one task is `ArcadeData__arcadedb-4281`. The download's `[agent]`
holds only `timeout_sec`. In the built lane it reads:

```toml
[agent]
network_mode = "no-network"
timeout_sec = 3000.0
```

### The claude-code check

```
20261103T094131Z-1/ArcadeData__arcadedb-4281__wmFkywP/agent/
  install.log (721)  model.patch (4487)  run.log (864269)  suss.json (544)
20261103T094131Z-1-regrade/ArcadeData__arcadedb-4281__os5zTeT/
  agent/replay.json (728)  verifier/reward.txt  verifier/report.json  verifier/test-stdout.txt
```

`suss.json`:

```json
"grader": { "dataset": "swe-rebench-07-2026",
            "digest": "1263fe545324a36f34aedf7438f48f1bc22b3aa84f57b9b1ec0dc91a458ebf80" },
"base": "8d169627b2cab041e346e0b79569f734ed94267d",
"status": "ok",
"exit_codes": { "install": 0, "run": 0 },
"run_seconds": 390.43951245886274,
"patch_bytes": 4487
```

`replay.json`:

```json
"task": "ArcadeData__arcadedb-4281",
"patch": "<dir>/checks/rebench/claude-code/20261103T094131Z-1/ArcadeData__arcadedb-4281__wmFkywP/agent/model.patch",
"apply_rc": 0,
"apply_out": "$ git apply --verbose\n… Applied patch engine/src/main/java/com/arcadedb/log/LogFormatter.java cleanly. …"
```

The patch changes `LogFormatter.java` and `AnsiLogFormatter.java` and adds two
tests to `LoggerTest.java`. All three applied cleanly. `reward.txt` is `1`,
and `report.json` gives `"resolved": true`, with every fail-to-pass and
pass-to-pass test passing.

The last line of `run.log` gives `"subtype": "success"`, `"num_turns": 25`
and `"total_cost_usd": 1.6150092499999995`.

| Step | Took |
|---|---|
| Sandbox started | 4 s |
| Agent setup, Claude Code installed | 18 s |
| `run`, Claude Code ended by itself | 6 min 36 s |
| Regrade, sandbox to verifier's end | 1 min 31 s |
| of which the verifier | 1 min 14 s |
| All of `suss run` | 8 min 46 s |

In its agent phase, Claude Code's `mvn test -Dtest=LoggerTest` failed with:

```
Could not transfer artifact org.apache.maven.surefire:surefire-junit-platform:pom:3.5.5 from/to central (https://repo.maven.apache.org/maven2): Connection reset
```

It then compiled offline with `mvn -o` and checked the fix with a class of its
own, which it deleted.

### The network probe

The stand-in adapter, in `network-probe/`, calls no model:

```toml
[harness]
name = "network-probe"
version = "0.1.0"
install = "install.sh"
run = "run.sh"
allowed_hosts = ["api.anthropic.com", "api.github.com"]
env = []
```

`install.sh` runs `probe.sh setup` once. `run.sh` runs `probe.sh agent`,
sleeps 20 s, runs it again, and writes `NETWORK_PROBE.txt` in the workspace.
`probe.sh` runs this for each of the seven URLs below, after
`getent hosts "$host"`:

```bash
curl -sS -o /dev/null -w '%{http_code}' --connect-timeout 10 --max-time 20 "$url"
```

suss passed `--allow-agent-host api.anthropic.com --allow-agent-host api.github.com`.

| URL | Setup | Agent, 09:51:02 | Agent, 09:51:23 |
|---|---|---|---|
| `https://api.anthropic.com/v1/models` | 401 | 401 | 401 |
| `https://api.github.com/` | 200 | 200 | 200 |
| `https://github.com/` | 200 | refused | refused |
| `https://pypi.org/simple/` | 200 | refused | refused |
| `https://repo.maven.apache.org/maven2/` | 200 | refused | refused |
| `https://www.google.com/` | 200 | refused | refused |
| `https://1.1.1.1/` | 301 | refused | refused |

A 401 from `api.anthropic.com` is a request that arrived without a key.
Refused is `http=000 curl_rc=35`, `Recv failure: Connection reset by peer`.
DNS answered for every host in both phases, as in `github.com` →
`140.82.114.4` in the agent phase, so no refusal was a DNS failure.

The trial was `ok` with a 166-byte patch. Its regrade applied it with
`git apply --verbose` and scored 0.

### Harbor's calls, in order

Logged by a `sitecustomize.py`, on `PYTHONPATH` for the probe's `suss run`,
that wraps `ModalEnvironment._create_sandbox` and
`ModalEnvironment._apply_network_policy` without changing either. Host clock:

```
09:50:46.355Z _create_sandbox start                      (agent job)
09:50:59.950Z set_outbound_network_policy {'outbound_domain_allowlist': ['api.anthropic.com', 'api.github.com'], 'outbound_cidr_allowlist': []}
09:51:00.200Z   returned
09:51:27.911Z set_outbound_network_policy {'outbound_domain_allowlist': ['*'], 'outbound_cidr_allowlist': ['0.0.0.0/0']}
09:51:39.225Z _create_sandbox start                      (regrade)
09:51:42.147Z set_outbound_network_policy {'outbound_domain_allowlist': [], 'outbound_cidr_allowlist': []}
09:51:45.396Z set_outbound_network_policy {'outbound_domain_allowlist': ['*'], 'outbound_cidr_allowlist': ['0.0.0.0/0']}
```

Harbor's trial record puts `agent_execution` at 09:50:59.950 to 09:51:28.161,
so the agent phase's policy is set at its first instant and restored at its
end, as for a SWE-bench Pro task. The first agent-phase probe ran 2.7 s after
the policy call returned.

### Cost

`modal billing report --for today -r h --show-resources --json`, read at
09:56 UTC, 3 minutes after the last run ended:

| App | Holds | CPU | Memory | Total |
|---|---|---|---|---|
| `suss-124-claude-code` | the claude-code agent job | $0.0167 | $0.0113 | $0.028 |
| `suss-124-network-probe` | the probe's agent job | $0.0019 | $0.0013 | $0.003 |
| `__harbor__` | both regrades | $0.0066 | $0.0044 | $0.011 |

Network egress was under $0.0001 in each app. With $1.62 of model, both checks
together cost $1.66.

## Limits

- One attempt at one task, so neither the `ok` nor the 1 is shown to be
  typical. Only the lane's first task by name was run.
- The probe's agent phase was run on one task image. IPv6 was not probed apart
  from the addresses DNS returned, and UDP and ICMP not at all.
- The task's pass-to-pass tests include `LoggerTest`, which the patch changes,
  and the verifier runs the patched file. The fail-to-pass tests are the
  verifier's own, so the 1 does not rest on that.
- `__harbor__` is Harbor's shared default app, so its charge could include
  another job run in the same hour (unverified that none did). The report is
  hourly and was read soon after the runs ended.
- Neither `max_seconds` nor `max_budget_usd` was reached.
