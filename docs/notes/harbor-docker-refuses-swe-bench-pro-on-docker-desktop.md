---
settles: "§ 2 — which environment a SWE-bench Pro lane's sandboxes need; § 6 — the `environment` a SWE-bench Pro lane takes in `suss.toml`"
issue: [3, 29]
recorded: 2026-10-31
versions: { harbor: 0.22.0, docker-desktop: "4.73.0 (engine 29.4.3)", kernel: 6.12.76-linuxkit, host: "macOS, arm64" }
recheck-when: Harbor upgrades past 0.22.0, Docker Desktop ships a kernel with CONFIG_NFT_FIB_INET, or a Linux Docker host is tried
---

# Harbor's docker environment refuses SWE-bench Pro tasks on Docker Desktop

## Intent

- Does `harbor run -e docker` keep a V2 task's agent phase offline except for
  the hosts `--allow-agent-host` names?
- Does the same task's setup phase keep network access, enough to install a
  package?

## Decisions

Run every SWE-bench Pro lane with `environment = "modal"`. On Docker Desktop,
Harbor 0.22.0 refuses any V2 task before a container starts, so neither phase
runs at all.

## Needs your input

- Whether the docker environment works for V2 on a Linux host is untested. I
  recommend leaving lanes on Modal until someone runs the probe on one, since
  the spec already names Modal for them.
- `suss run` could exit with an error when a SWE-bench Pro lane has
  `environment = "docker"`, rather than leaving Harbor to refuse each trial. I
  recommend adding that to the spec as a separate change.

## Reference

- Harbor's docker environment enforces a non-public policy only through its
  egress sidecar: a gost transparent proxy, with nftables rules that redirect
  TCP to it and reject other protocols. It turns the sidecar off when
  `DockerEnvironment._egress_control_kernel_support()` is false, and then
  reports no `disable_internet` or `network_allowlist` capability.
- That check runs `alpine:3.23.4` on the daemon and passes when
  `/proc/config.gz` is absent or has `CONFIG_NFT_FIB_INET=y` or `=m`. Docker
  Desktop's kernel `6.12.76-linuxkit` has `# CONFIG_NFT_FIB_INET is not set`.
- Without the capability, the trial fails in `DockerEnvironment.__init__`, exit
  code 1, with no agent or verifier output:
  - with `--allow-agent-host api.anthropic.com`: `ValueError:
    network_mode='allowlist' is not supported by EnvironmentType.DOCKER
    environment. Environment providers must enforce the requested network
    policy or reject the task.`
  - without it: the same, with `network_mode='no-network'`.
- The refusal is loud: Harbor does not fall back to an open network.
- The V2 images have no arm64 manifest. `docker pull` of one on an arm64 host
  fails with `no matching manifest for linux/arm64/v8` unless given
  `--platform linux/amd64`.
- Harbor 0.22.0's oracle reads its environment from `[solution.env]` in
  `task.toml`. The probe script's docstring says `[agent.env]`, which Harbor
  does not read, so `LLM_ENDPOINT` set there is never seen and the probe skips
  the allowed host.
- On this machine, `docker pull` hung forever in `docker-credential-desktop
  get`. A `DOCKER_CONFIG` directory whose `config.json` has no `credsStore`
  pulls the public images anonymously.

## Limits

- Tested on Docker Desktop on macOS arm64 only. Linux Docker, with its own
  kernel config, may pass Harbor's check and enforce the policy; nothing here
  says whether it would.
- The probe never ran, so no host was reached or refused from inside a
  sandbox. How the sidecar treats DNS, and which curl exit code a denied host
  gets, is read from source only (unverified).
- The Modal environment was not tested here.
