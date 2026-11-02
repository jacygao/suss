---
settles: "§ 3 — that on Modal the setup phase has the whole network and the agent phase only the harness's `allowed_hosts`; § 5 — that `--allow-agent-host` is enough to open a host in a SWE-bench Pro task's `no-network` agent phase on Modal"
issue: [68]
recorded: 2026-11-03
versions: { harbor: 0.22.0, modal: 1.6.3, swe-bench-pro: "v2.0.0 (66f92766)", image: "swe-bench_pro-v2 flipt-io__flipt-02e21636 (Debian 12, amd64)" }
recheck-when: suss upgrades Harbor past 0.22.0, Harbor changes how its modal environment sets the network per phase, or Modal changes or stabilises _experimental_set_outbound_network_policy
---

# Harbor holds a Modal agent phase to its --allow-agent-host hosts, and leaves setup open

## Intent

- In the agent phase of an unmodified pro-go task, run by `suss run --check`
  with `environment = "modal"`, does each `--allow-agent-host` host answer and
  every other host fail?
- Does the setup phase on Modal still have the whole network?
- Which Harbor call sets the agent phase's network on Modal, and when?

## Decisions

- **Run SWE-bench Pro lanes on Modal as suss runs them now, with no change.**
  Through `harbor run -e modal`, the agent phase reached `api.anthropic.com` and
  `api.github.com`, the two hosts suss passed as `--allow-agent-host`, and was
  refused `pypi.org`, `proxy.golang.org`, `www.google.com` and `1.1.1.1`, both
  when `run` started and 20 s later.
- **Count on setup having the whole network on Modal.** The same probe in
  `install` reached all six targets, including the four the agent phase was
  refused, so the probe can tell reached from refused.
- **Harbor sets the agent phase's network with one Modal call, before `run`
  starts.** `ModalEnvironment._apply_network_policy` calls
  `Sandbox._experimental_set_outbound_network_policy` with the agent phase's
  hosts after `install` returns and before suss starts `run`, and calls it
  again with the whole network once `run` returns. The sandbox is created with
  the whole network open.

## Needs your input

Nothing.

## Reference

### The run

From the worktree root, with a `suss.toml` holding `environment = "modal"`,
`k = 1` and `concurrency = 1`, and an arm whose stand-in adapter calls no model
and lists `allowed_hosts = ["api.anthropic.com", "api.github.com"]`:

```bash
uv run suss run network-probe pro-go --check
```

suss ran:

```bash
<env>/bin/harbor run -p lanes/pro-go-check -e modal -n 1 -k 1 \
  -a suss.agent:SussAgent --ak arm=network-probe --ak config=suss.toml \
  --allow-agent-host api.anthropic.com --allow-agent-host api.github.com \
  --disable-verification --jobs-dir checks/pro-go/network-probe --job-name 20261102T214740Z-1
```

The task was `instance_flipt-io__flipt-02e21636c58e86c51119b63e0fb5ca7b813b07b1`,
whose `task.toml` gives `[environment] network_mode = "public"` and
`[agent] network_mode = "no-network"`. `install.sh` probed once; `run.sh`
probed at its start and again 20 s later, with:

```bash
curl -sS -o /dev/null -w '%{http_code}' --connect-timeout 10 --max-time 20 "$url"
```

### What the probes printed

`install.log`, the setup phase:

```
PROBE phase=setup t=21:47:59.206Z url=https://api.anthropic.com/v1/models http=401 curl_rc=0
PROBE phase=setup t=21:47:59.257Z url=https://api.github.com/ http=200 curl_rc=0
PROBE phase=setup t=21:47:59.572Z url=https://pypi.org/simple/ http=200 curl_rc=0
PROBE phase=setup t=21:47:59.624Z url=https://proxy.golang.org/ http=200 curl_rc=0
PROBE phase=setup t=21:47:59.754Z url=https://www.google.com/ http=200 curl_rc=0
PROBE phase=setup t=21:47:59.811Z url=https://1.1.1.1/ http=301 curl_rc=0
```

`run.log`, the agent phase, first probe 3 s after the policy call returned.
The second probe, at 21:48:23, gave the same codes:

```
PROBE phase=agent t=21:48:03.495Z url=https://api.anthropic.com/v1/models http=401 curl_rc=0
PROBE phase=agent t=21:48:03.549Z url=https://api.github.com/ http=200 curl_rc=0
PROBE phase=agent t=21:48:03.607Z url=https://pypi.org/simple/ http=000 curl_rc=35 err=curl: (35) Recv failure: Connection reset by peer
PROBE phase=agent t=21:48:03.647Z url=https://proxy.golang.org/ http=000 curl_rc=35
PROBE phase=agent t=21:48:03.684Z url=https://www.google.com/ http=000 curl_rc=35
PROBE phase=agent t=21:48:03.721Z url=https://1.1.1.1/ http=000 curl_rc=35
```

A 401 from `api.anthropic.com` is a request that arrived without a key.

### Reading a denial

- A refused HTTPS request gives `http=000 curl_rc=35`, `Connection reset by
  peer`, at once. It is the same exit code as Harbor's Docker sidecar gives,
  with a different message.
- DNS still resolves in the agent phase: `getent hosts pypi.org` answered in
  both probes. A `curl_rc=6` would mean DNS failed, not that the host was
  refused.

### Harbor's calls, in order

Logged by wrapping `ModalEnvironment._create_sandbox` and
`ModalEnvironment._apply_network_policy` in the `harbor` process suss
started, without changing what either does. Host clock:

| UTC | Call | Network |
|---|---|---|
| 21:47:42.4 | `Sandbox.create`, from `_create_sandbox` | `outbound_domain_allowlist=["*"]`, `outbound_cidr_allowlist=["0.0.0.0/0"]`, `block_network=False` |
| 21:48:00.43 | `_experimental_set_outbound_network_policy` | `outbound_domain_allowlist=["api.anthropic.com", "api.github.com"]`, `outbound_cidr_allowlist=[]`; returned at 21:48:00.68 |
| 21:48:28.39 | `_experimental_set_outbound_network_policy` | back to `["*"]` and `["0.0.0.0/0"]` |

Harbor's trial record puts `agent_setup` at 21:47:50.59 to 21:48:00.429 and
`agent_execution` at 21:48:00.430 to 21:48:28.64, so the policy is set at the
first instant of `agent_execution`, before `SussAgent.run` is called, and
restored after it returns. suss captures the patch inside `run`, so the
capture also runs under the agent phase's policy.

The path, in Harbor 0.22.0:

- `--allow-agent-host` becomes the trial's `agent.extra_allowed_hosts`.
- `resolve_agent_phase_policy` in `harbor/trial/network_policy.py` merges them
  into the task's `no-network`, which makes an `allowlist` policy of exactly
  those hosts.
- Because that differs from the `public` baseline, `ModalEnvironment` marks the
  network dynamic, and `_create_sandbox` opens it fully at creation.
- `Trial._run_agent_phase` wraps the agent's `run` in `_phase_network_policy`,
  which calls `environment.set_network_policy` and so
  `_apply_network_policy` on entry and on exit.

`Trial._setup_agent`, which runs `install`, has no such wrapper, so setup runs
under the baseline.

### The trial and its regrade

`suss.json` recorded `status` `ok`, exit codes 0 and 0, and a 173-byte patch
adding `NETWORK_PROBE.txt`. The regrade, also on Modal, applied it with
`apply_rc` 0 and gave reward 0, since the patch fixes nothing: `Required tests
that passed: 0`. Both Harbor runs exited 0, and so did `suss run`.

The regrade's agent phase, with no `--allow-agent-host`, was set the same way,
with both lists empty:

```
_experimental_set_outbound_network_policy(outbound_domain_allowlist=[], outbound_cidr_allowlist=[])
```

| Step | Took |
|---|---|
| Agent job, sandbox start to `run` returning | 47 s |
| Regrade, of which the verifier | 4 min, 3.5 min |
| All of `suss run` | 5 min |

## Limits

- One run, one task, one pair of allowed hosts. The other pro-go images, and
  the other lanes, were not run.
- IPv6 was not probed. `pypi.org` resolved to IPv6 addresses in the sandbox,
  and curl reached it in setup, but whether the agent phase's policy closes
  IPv6 was not tested separately.
- UDP, outside DNS servers and ICMP were not probed on Modal. DNS through the
  sandbox's own resolver stays open.
- The first agent-phase probe ran 2.7 s after the policy call returned, so
  whether a request in that gap could get out was not seen. Nor was whether a
  connection opened in setup survives the switch.
- The timeline's Harbor calls use the host clock and the probes the sandbox's
  clock. They agree in order, but the two clocks were not compared.
