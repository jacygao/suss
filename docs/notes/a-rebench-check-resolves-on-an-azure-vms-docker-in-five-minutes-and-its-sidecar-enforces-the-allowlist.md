---
settles: "§ 2 — that a Docker engine on an Azure Ubuntu 24.04 VM has `CONFIG_NFT_FIB_INET` and enforces the agent phase's allowlist; § 6 — that `suss run --check` with `environment = \"docker\"` completes on such a VM, and how long a check of rebench takes there"
recorded: 2026-11-13
versions: { harbor: 0.25.0, docker: 29.1.3, docker-compose: 2.40.3, kernel: 7.0.0-1017-azure, os: "Ubuntu 24.04.5 LTS", copilot-cli: 1.0.107, vm: Standard_E4as_v5 }
recheck-when: Harbor upgrades past 0.25.0, Harbor changes its egress sidecar, the VM image moves off Ubuntu 24.04, or Azure's kernel drops CONFIG_NFT_FIB_INET
---

# A rebench check resolves on an Azure VM's Docker in five minutes, and its sidecar enforces the allowlist

## Intent

- Can suss run its sandboxes on Azure, in place of Modal and local Docker?
- Does a `rebench` check complete there, graded as it is on Modal?
- Does Harbor's egress sidecar hold the agent phase to its allowed hosts there?

## Decisions

- **Run suss on Azure as a Linux VM with `environment = "docker"`, with suss
  itself on the VM.** It needs no code. Container Instances and Container Apps
  have no Docker engine and no privileged containers, and Harbor 0.25.0 has no
  backend for them. Its Kubernetes backends are tied to GKE or Alibaba, and none
  of them can limit egress to a list of hosts, which only `docker` and `modal`
  can.
- **Never point a remote `DOCKER_HOST` at the VM from another machine.** Harbor
  bind-mounts each trial's log directories from the Docker host's filesystem,
  which is then the VM's, not the client's.
- **Set `concurrency` from the lane's largest task.** The
  Visual Studio Enterprise subscription allows 20 vCPUs a region, so its largest
  VM is a Standard_E20as_v5 with 160 GB. That runs 20 rebench trials at once,
  at 1 CPU and 4 GB each, but only 5 senior trials, whose largest task takes
  4 CPUs and 8 GB.
- **Run three arms in parallel in `australiaeast`, `newzealandnorth` and
  `koreacentral`, one VM each, rather than raising the quota.** The quota is
  per region, and all three create a Standard_E20as_v5. Three rebench checks
  ran there at once and each resolved its task. `australiasoutheast`,
  `australiacentral` and `southeastasia` offer this subscription no VM sizes,
  whatever their quota says. One `suss run` per VM keeps parallel arms off
  each other's lane directory. Copilot arms running at once share one token's
  AI credits and rate limit.
- **Never run a Claude model's arm from `eastasia`.** Copilot CLI there exits 1
  with `Error: Model "claude-opus-5.5" from --model flag is not available.`
  before doing anything.

- **Keep every run in Azure Blob Storage, and rank from a mirror of it.** No
  run then depends on the machine that started it, and the VM uploads at
  Azure's speed rather than over the caller's connection.
- **Delete each VM's resource group once its results are kept.** A
  deallocated VM still bills for its disk and public IP, and the image the
  check built from cold took 51 s.

## Needs your input

Nothing.

## Reference

### Running one

```bash
cd suss-runs        # a checkout with a .env, signed in with `az login`
scripts/azure-run.sh koreacentral copilot-opus rebench --check
```

A subscription's first VM needs `Microsoft.Compute` and `Microsoft.Network`
registered, with `az provider register -n <name> --wait`.

The script creates resource group `suss-run-<region>`, with SSH open only to
the caller's IP. It copies suss's tracked files, the lane's tasks and `.env`,
sets `environment = "docker"` and a `concurrency` the VM holds, and runs the
arm in `tmux`. The VM uploads `results/` and `checks/` to the container
`SUSS_RUNS_URL` names, and the console output comes back as
`logs/azure-<region>-<arm>.out`. Then the script removes the VM's storage role
and deletes the group. A second run in a region whose group still exists is
refused.

### Storage

```
SUSS_RUNS_URL=https://sussae63383b.blob.core.windows.net/runs
```

Account `sussae63383b`, in group `suss-storage` in `australiaeast`, with
shared keys and public access off, so every caller signs in with `az login`.
Its blob paths are the paths under a checkout: `results/…`, `checks/…`, and
`excluded/results/…` for runs the leaderboard leaves out. Each VM gets a
system identity with Storage Blob Data Contributor on the `runs` container
alone. A check's upload worked about 6 minutes after the role was granted.

```bash
scripts/results.sh down <mirror>
(cd <mirror> && uv run --project <suss> suss leaderboard --lane rebench)
```

The download is the 5 files per trial the leaderboard reads: 28 MB in 33 s,
against 6.2 GB for every run kept.

Three runs at once, one per region:

```
22:46:53  three VMs created, each in about 70 s
22:48:17  australiaeast    copilot-sonnet   started
22:55:25  australiaeast    exited 0, reward 1.0, 24.6 AI credits, deleted
22:59:55  newzealandnorth  copilot-gpt      exited 0, reward 1.0, 37.4 AI credits, deleted
23:04:46  koreacentral     copilot-opus     exited 0, reward 1.0, 57.4 AI credits, deleted
```

### The first check

On a Standard_E4as_v5, `uv run suss run copilot-sonnet rebench --check` exited 0. The trial on
`ArcadeData__arcadedb-4281` is `ok`, with a 2991-byte patch, 21.96 AI credits
and `run_seconds` 130.5. The regrade applied that patch with `apply_rc` 0 and
gave reward 1.0.

| Phase | Time |
|---|---|
| Environment setup, image built from cold | 51 s |
| Agent execution | 2 min 14 s |
| Verifier | 1 min 46 s |
| All of Harbor's job | 5 min 14 s |

### The allowlist

A throwaway agent curled three hosts in the agent phase of the same task, run
with `harbor run -e docker --allow-agent-host api.github.com
--disable-verification`:

```
NET https://api.github.com http=200 curl_rc=0
NET https://pypi.org/simple/ http=000 curl_rc=35
NET https://www.google.com http=000 curl_rc=35
```

`curl_rc=35` is the sidecar refusing the connection after reading its TLS SNI,
as on Docker Desktop. The kernel's config has `CONFIG_NFT_FIB_INET=m`.

## Limits

- Each check ran one task. No full lane has run on a VM, so its time, its
  image builds at `concurrency = 20` and its disk use were not measured.
- The allowlist was probed on the Standard_E4as_v5 in `australiaeast` only.
- Of the other regions, only VM sizes were listed, and only on 2026-11-13.
- The VM's cost per hour was not measured against Modal's.
- The senior lane was not run on the VM.
