---
settles: "§ 3 — that on Modal a senior regrade's judges score the trial's patch and its grader names their models; § 5 — that a senior task's agent phase on Modal reaches the harness's and the task's allowed_hosts, wildcards included, and no other host; § 6 — what `suss run claude-code senior --check` leaves under `checks/` with `environment = \"modal\"`, and what it costs and takes"
issue: [163]
recorded: 2026-11-04
versions: { harbor: 0.22.0, modal: 1.6.3, claude-code: 2.1.293, senior-swe-bench: "v2026.06 (digest 0e0174e2…)", litellm: "litellm>=1.0,<1.106", task: "better-auth-fix-api-key-run (TypeScript, investigate)", host: "macOS arm64 client" }
recheck-when: Harbor upgrades past 0.22.0, Modal changes or stabilises _experimental_set_outbound_network_policy, Snorkel publishes a new snapshot or changes test.sh, or the claude-code adapter changes
---

# A senior check on Modal is judged, and reaches only the harness's and the task's hosts

## Intent

- Does `suss run claude-code senior --check` on Modal leave a trial and its
  regrade, with a reward and both judges' scores?
- Does the trial's `suss.json` have `status` `ok` and a `grader` naming the
  judge and validation-agent models the regrade used?
- In the agent phase of a senior task on Modal, does the harness reach a host
  only the task names, including one matched by a wildcard, and is it refused a
  host neither the task nor the manifest names?
- What does the check cost, and how long does it take?

## Decisions

- **Run the senior lane on Modal as suss runs it now, with no change.** The
  check exited 0 in 9 minutes. The trial is `ok` with an 11,075-byte patch. Its
  regrade applied the patch with `git apply --verbose`, passed all 4 tests and
  scored `reward` 1.0, with `rubric_judge_ok` and `taste_judge_ok` both 1.
- **Read the grader in `suss.json` as what the regrade ran.** It names
  `anthropic/claude-sonnet-4-6` for the judges and the validation agent, and
  `anthropic/claude-haiku-4-5-20251001` for the classifier. The regrade's Harbor
  held only `ANTHROPIC_API_KEY` from `.env`, so every `SSB_OVERRIDE_*` was
  empty and the verifier took the defaults in its `llm_utils.py`, which are the
  same models.
- **Count on the task's `[agent] allowed_hosts` to open, and only open, the
  task's hosts on Modal.** A stand-in harness whose manifest lists
  `api.anthropic.com` reached `files.pythonhosted.org` and `docs.python.org`,
  which only the task's wildcards `*.pythonhosted.org` and `*.python.org`
  match, as well as `pypi.org` and `registry.npmjs.org`. It was refused
  `github.com`, `api.github.com`, `www.google.com`, `repo.maven.apache.org`
  and `1.1.1.1`, each of which resolved and each of which it reached in setup.
- **Expect a claude-code check of senior, on an investigate task, to cost about
  $2.** The trial cost $0.87 of model. The judges were not metered here, and
  cost $0.78 to $0.97 a regrade on another investigate task. Modal billed
  $0.09 to this check's own apps and $0.47 to the shared `__harbor__` app in the
  same hours.

## Needs your input

Nothing.

## Reference

### The run

From `~/Documents/Dev/suss-runs/m5/163-check/`, whose `.env` holds
`ANTHROPIC_API_KEY` alone:

```toml
senior_swe_bench = "/Users/jacy/Documents/Dev/tasks/senior/senior-swe-bench-v2026.06"
environment = "modal"
k = 1
concurrency = 1

[lanes.senior]
max_seconds = 2700

[arms.claude-code]
harness = "<worktree>/adapters/claude-code"
params = { version = "2.1.293", model = "claude-sonnet-4-6", effort = "medium", max_budget_usd = 3 }
```

```bash
suss run claude-code senior --check -- --ek app_name=suss-163-claude-code
```

`DRY_RUN=1` printed `# from .env: ANTHROPIC_API_KEY` before the regrade, and
`-y` on both jobs. The check's task, the lane's first by name, is
`better-auth-fix-api-key-run`. Its `segment` is `investigate`, so its verifier
runs no validation agent. Its `test.sh` in `lanes/senior-check/` installs
`litellm>=1.0,<1.106`.

### What it left

```
checks/senior/claude-code/20261104T125149Z-1/better-auth-fix-api-key-run__grbJfn4/agent/
  install.log (721)  model.patch (11075)  run.log (1234772)  suss.json (798)
checks/senior/claude-code/20261104T125149Z-1-regrade/better-auth-fix-api-key-run__8ZvJg4x/
  agent/replay.json  verifier/reward.txt  verifier/reward.json  verifier/judge_output.json
  verifier/verifier_results.json  verifier/run_judge_rubric.stderr  verifier/run_judge_taste.stderr
```

`suss.json`:

```json
"grader": {
  "dataset": "senior-swe-bench-v2026.06",
  "digest": "0e0174e2542a1a0097a784785d20b1bbb6281bc480827c1314aaa4a16e81d4f3",
  "litellm": "litellm>=1.0,<1.106",
  "judge_model": "anthropic/claude-sonnet-4-6",
  "classifier_model": "anthropic/claude-haiku-4-5-20251001",
  "validation_harness": "miniswe",
  "validation_model": "anthropic/claude-sonnet-4-6"
},
"status": "ok",
"exit_codes": { "install": 0, "run": 0 },
"run_seconds": 332.9,
"patch_bytes": 11075
```

The last line of `run.log` gives `"subtype": "success"`, `"num_turns": 19` and
`"total_cost_usd": 0.8728987499999998`.

`replay.json` gives `apply_rc` 0. The patch changes `adapter.ts` and
`api-key.test.ts`, and the regrade put the test file back:
`"tests_reset": { "restored": ["packages/api-key/src/api-key.test.ts"], "removed": [] }`.

The regrade's `verifier_result.rewards`, with `exception_info` null:

```json
{
  "reward": 1.0,
  "correctness": 1.0,
  "verifier_score": 1.0,
  "rubric_score": 0.0,
  "rubric_f2p_score": 0.0,
  "taste_patch_bloat": 0.971,
  "taste_practice_alignment": 3.0,
  "taste_relative_taste": 3.0,
  "rubric_judge_ok": 1,
  "taste_judge_ok": 1
}
```

`judge_output.json` gives `rubric_status` and `taste_status` as `ok`. The task
has one rubric criterion, `route_parallel_storage_groups`, which asks for the
storage groups in `routes/list-api-keys.ts` to be queried concurrently. The
patch does not touch that file, so the rubric's 0.0 is the judge's verdict, not
a failure. `run_judge_rubric.stderr` shows 2 turns and `run_judge_taste.stderr`
15. No file the verifier wrote names a model, so the models above are what
suss recorded and what the verifier's defaults are, not a logged call.

`suss compare senior claude-code claude-code-copy`, over a copy of the check
moved under `results/` with a second arm holding the same trial:

```
arm               trials  resolved  failed  timed out
claude-code            1      100%      0%         0%
claude-code-copy       1      100%      0%         0%

vs. baseline                     resolved
claude-code-copy - claude-code   +0% [+0%, +0%]

mean (trials)       rubric  practice alignment  relative taste  patch bloat
claude-code       0.00 (1)            3.00 (1)        3.00 (1)     0.97 (1)
claude-code-copy  0.00 (1)            3.00 (1)        3.00 (1)     0.97 (1)
```

| Step | Took |
|---|---|
| Sandbox started | 5 s |
| Agent setup, Claude Code installed | 19 s |
| `run`, Claude Code ended by itself | 5 min 33 s |
| Regrade, sandbox to verifier's end | 2 min 43 s |
| of which the verifier | 2 min 24 s |
| All of `suss run` | about 9 min |

The image was already on Modal from an earlier oracle run of the lane, so the
5-second start included no build.

### The network probe

From `163-check/probe/`, with the same `senior_swe_bench`, `environment` and
task, an arm `network-probe` on an adapter there, and a `.env` holding only a
`PORTKEY_API_KEY` that is not a key, so the regrade's judges failed at Portkey
with `Invalid API Key` and spent nothing:

```toml
[harness]
name = "network-probe"
version = "0.1.0"
install = "install.sh"
run = "run.sh"
allowed_hosts = ["api.anthropic.com"]
env = []
```

`install.sh` runs `probe.sh setup` once. `run.sh` runs `probe.sh agent`,
sleeps 20 s, runs it again, and writes `NETWORK_PROBE.txt` in the workspace.
`probe.sh` runs, for each URL below, `getent hosts "$host"` and then:

```bash
curl -sS -o /dev/null -w '%{http_code}' --connect-timeout 10 --max-time 20 "$url"
```

suss passed `--allow-agent-host api.anthropic.com` alone. The task's
`[agent]` has `network_mode = "allowlist"` and its 32 hosts.

| URL | Named by | Setup | Agent, 12:49:42 | Agent, 12:50:03 |
|---|---|---|---|---|
| `https://api.anthropic.com/v1/models` | the manifest | 401 | 401 | 401 |
| `https://files.pythonhosted.org/` | the task, as `*.pythonhosted.org` | 404 | 404 | 404 |
| `https://docs.python.org/3/` | the task, as `*.python.org` | 200 | 200 | 200 |
| `https://pypi.org/simple/` | the task | 200 | 200 | 200 |
| `https://registry.npmjs.org/` | the task | 200 | 200 | 200 |
| `https://github.com/` | neither | 200 | refused | refused |
| `https://api.github.com/` | neither | 200 | refused | refused |
| `https://www.google.com/` | neither | 200 | refused | refused |
| `https://repo.maven.apache.org/maven2/` | neither | 200 | refused | refused |
| `https://1.1.1.1/` | neither | 301 | refused | refused |

A 401 or 404 is a response from the host, so the request arrived. Refused is
`http=000 curl_rc=35`, `OpenSSL SSL_connect: Connection reset by peer`. DNS
answered for every host in the agent phase, as in `github.com` →
`140.82.112.3` and `www.google.com` → `2001:4860:482b:7700::`, so no refusal
was a DNS failure. Harbor's record puts `agent_execution` at 12:49:39.310 to
12:50:10.999, so both probes ran inside it.

The trial was `ok` with a 166-byte patch, which its regrade applied with
`apply_rc` 0. The regrade's `rubric_judge_ok` and `taste_judge_ok` were 0, as
intended.

### Cost

| | Counted from | Spend |
|---|---|---|
| The trial | Claude Code's `total_cost_usd` | $0.873 |
| The regrade's judges | not metered | $0.78 to $0.97 on another investigate task (unverified here) |
| The probe's judges | Portkey refused every call | none |
| `suss-163-claude-code` | `modal billing report`, hourly | $0.078 |
| `suss-163-network-probe` | the same | $0.011 |
| `__harbor__`, both regrades | the same, 12:00 and 13:00 UTC | $0.466 |

About $2.30 in all. `__harbor__` is Harbor's shared default app and had a
sandbox of another job running throughout, so its figure is an upper bound on
the two regrades.

## Limits

- One trial of one investigate task, the lane's first by name. The validation
  agent did not run, so the check says nothing about a design task on Modal.
- The judges' models are inferred from the regrade's environment and the
  verifier's defaults. The verifier logs no model name, and the Anthropic
  account's usage was not read.
- The judges' dollar cost was not measured.
- The probe ran on one task image. IPv6 was not probed apart from the
  addresses DNS returned, and UDP and ICMP not at all.
- `max_seconds` and `max_budget_usd` were not reached.
