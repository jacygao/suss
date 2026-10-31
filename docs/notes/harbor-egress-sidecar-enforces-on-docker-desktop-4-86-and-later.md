---
settles: "§ 2 — which Docker a SWE-bench Pro lane's sandboxes need; § 4 — the package index pip sees in a V2 image's setup phase; § 5 — what a lane's agent phase can reach; § 6 — the `environment` a SWE-bench Pro lane takes in `suss.toml`, and the `-e` value `harbor run` gets"
issue: [3, 29]
recorded: 2026-10-31
versions: { harbor: "0.22.0 run, 0.25.0 source read", docker-desktop: "4.97.0 (engine 29.9.2), and 4.73.0 for the old failure", kernel: "7.0.14-linuxkit, and 6.12.76-linuxkit for the old failure", host: "macOS, arm64" }
recheck-when: Harbor changes its egress sidecar or kernel probe, suss upgrades Harbor past 0.22.0, Docker Desktop changes its kernel config, or the V2 images change their pip config
---

# Harbor's egress sidecar enforces SWE-bench Pro's network rules on Docker Desktop 4.86.0 and later

## Intent

- Why did Harbor's docker environment refuse SWE-bench Pro tasks on Docker
  Desktop, and does it still?
- On a current Docker Desktop, does stock Harbor give each phase the network
  the task asks for?
- Does suss need to carry a fix to Harbor's sidecar?
- How can a user tell whether their Docker host is good enough?
- What gets out of the agent phase that the rules do not close?
- Can a harness's `install` use pip in a V2 image?

## Decisions

- **Run SWE-bench Pro lanes under local Docker, on stock Harbor, with Docker
  Desktop 4.86.0 or later on macOS.** On Docker Desktop 4.97.0, stock Harbor
  0.22.0 gave setup the whole network, and gave the agent phase only the hosts
  passed as `--allow-agent-host`, or none.
- **The old refusal was the kernel, and Docker has fixed it.** Harbor's
  sidecar uses `fib daddr type local` in an `inet` table, which needs
  `CONFIG_NFT_FIB_INET`. Docker Desktop's kernel left that option out before
  4.86.0, and Harbor's kernel probe refused every task that closes the network.
  The kernel in 4.97.0 has `CONFIG_NFT_FIB_INET=y`.
- **suss carries no patched sidecar and no custom environment, and offers
  Harbor no patch.** Harbor 0.25.0, the latest release, still builds its rules
  in an `inet` table and still probes for `CONFIG_NFT_FIB_INET`, so a suss-side
  patch would not have gone away with a Harbor upgrade. Upstream closed the
  problem in Harbor issue #2527 by pointing users at Docker Desktop 4.86.0.
- **A host is good enough when its Docker kernel has `CONFIG_NFT_FIB_INET`.**
  The check is the command under Reference. It is the same option Harbor's
  probe looks for.
- **Accept DNS through Docker's resolver and ICMP echo in the agent phase, for
  now.** Both are open by Harbor's own rules. Neither can fetch a repository or
  a package index, which is what the agent phase is closed against.
- **A harness's `install` must give pip an index of its own.** The V2 image
  sets root's pip index to `http://127.0.0.1:9876/`, where nothing listens, so
  a plain `pip install` fails even in setup. With
  `--index-url https://pypi.org/simple` it installs.

## Needs your input

- How suss gets the amd64-only V2 images on an arm64 host. Setting
  `DOCKER_DEFAULT_PLATFORM=linux/amd64` breaks the sidecar, which Harbor builds
  for the daemon's own arm64 platform. I recommend `suss run` pulling each
  lane's images with `docker pull --platform linux/amd64` before calling Harbor.
- Whether § 4, under Installing, should tell adapter authors about the dead pip
  index. I recommend one sentence there: the image's pip config points at a
  local index that does not answer, so `install` passes `--index-url` or sets
  `PIP_INDEX_URL`.

## Reference

### Checking a Docker host

```bash
docker run --rm alpine:3.19 sh -c 'zcat /proc/config.gz | grep -E "^(# )?CONFIG_NFT_FIB"'
```

On Docker Desktop 4.97.0 (kernel `7.0.14-linuxkit`):

```
CONFIG_NFT_FIB=y
CONFIG_NFT_FIB_INET=y
# CONFIG_NFT_FIB_NETDEV is not set
CONFIG_NFT_FIB_IPV4=y
CONFIG_NFT_FIB_IPV6=y
```

`CONFIG_NFT_FIB_INET=y` or `=m` is what Harbor needs. On Docker Desktop
4.73.0 (kernel `6.12.76-linuxkit`) the line was missing, and Harbor refused
the task before starting it.

Harbor 0.22.0 and 0.25.0 probe the same way, in
`harbor/environments/docker/docker.py`:

```python
"if [ ! -f /proc/config.gz ]; then exit 0; fi; "
"zcat /proc/config.gz 2>/dev/null | "
"grep -qE '^CONFIG_NFT_FIB_INET=[ym]'"
```

### Why the old kernel failed

nftables resolves an expression by its table's family. The old kernel had
`CONFIG_NFT_FIB_IPV4=y` and `CONFIG_NFT_FIB_IPV6=y`, but `fib` in an `inet`
table needs `CONFIG_NFT_FIB_INET`, so Harbor's ruleset failed to load:

```
/dev/stdin:9:5-18: Error: Could not process rule: No such file or directory
    fib daddr type local return
    ^^^^^^^^^^^^^^
```

The same rules split into an `ip` table and an `ip6` table loaded on that
kernel and enforced the policy. With `CONFIG_NFT_FIB_INET` present, the split
is not needed.

### What the probe printed

SWE-bench Pro V2's network probe, built from
`instance_flipt-io__flipt-02e21636c58e86c51119b63e0fb5ca7b813b07b1`, run with
`harbor run -e docker -a setup_probe_oracle:SetupProbeOracle
--disable-verification` on stock Harbor 0.22.0. The agent probes once in
setup and once in the agent phase.

Setup, in every run:

```
NET https://api.github.com http=200 curl_rc=0
NET https://raw.githubusercontent.com/NodeBB/NodeBB/master/README.md http=200 curl_rc=0
NET https://proxy.golang.org http=200 curl_rc=0
NET https://pypi.org/simple/ http=200 curl_rc=0
NET https://registry.npmjs.org http=200 curl_rc=0
NET https://www.google.com http=200 curl_rc=0
NET https://api.anthropic.com/v1/models http=401 curl_rc=0
DNS github.com: 4.237.22.38     github.com
```

The agent phase, with `--allow-agent-host api.anthropic.com`:

```
NET https://api.github.com http=000 curl_rc=35
NET https://raw.githubusercontent.com/NodeBB/NodeBB/master/README.md http=000 curl_rc=35
NET https://proxy.golang.org http=000 curl_rc=35
NET https://pypi.org/simple/ http=000 curl_rc=35
NET https://registry.npmjs.org http=000 curl_rc=35
NET https://www.google.com http=000 curl_rc=35
NET https://api.anthropic.com/v1/models http=401 curl_rc=0
DNS github.com: 4.237.22.38     github.com
```

Without `--allow-agent-host`, every `NET` line in the agent phase is
`http=000 curl_rc=35`, `api.anthropic.com` too.

### pip in a V2 image

In the flipt V2 image, `pip config debug` shows:

```
user:
  /root/.pip/pip.conf, exists: False
  /root/.config/pip/pip.conf, exists: True
    global.index-url: http://127.0.0.1:9876/
```

A plain `pip install six==1.16.0` in setup gives:

```
WARNING: Retrying (Retry(total=0, connect=None, read=None, redirect=None, status=None)) after connection broken by 'NewConnectionError('<pip._vendor.urllib3.connection.HTTPConnection object at 0x7ffffd74fb50>: Failed to establish a new connection: [Errno 111] Connection refused')': /six/
ERROR: Could not find a version that satisfies the requirement six==1.16.0 (from versions: none)
```

With `--index-url https://pypi.org/simple`, setup gives
`Successfully installed six-1.16.0`, and the agent phase, where `pypi.org` is
closed, fails with `SSLZeroReturnError` and `No matching distribution found`.

### Reading a denial

- A denied HTTPS request gives `http=000 curl_rc=35`. gost accepts the TCP
  connection, reads the TLS SNI, and closes it. Its log has `"msg":"bypass"`
  for the destination. A denied plain TCP request gives `curl_rc=52`.
- `curl_rc=6` would be a DNS failure, not a denial. Names resolve in the agent
  phase, through Docker's resolver.
- A denied UDP send fails at once with `Operation not permitted` (`EPERM`).
  The `reject` runs on the output hook, so the send itself is refused. DNS to
  an outside server, such as `1.1.1.1`, is refused the same way.
- `network-policy show` in the sidecar prints `mode: controlled egress` while a
  policy is on, and `mode: allow-all (transparent proxy disabled)` otherwise.

### What stays open in the agent phase

- DNS through Docker's resolver at `127.0.0.11`, a local address, which the
  `fib daddr type local` rule exempts. A random name under `nip.io` resolved,
  so queries reach outside name servers. Any name can be looked up, and data
  can leave in the query.
- ICMP echo, which Harbor's rules accept. `1.1.1.1` answered a ping.

### Upstream

- Harbor issue #2527, "network_mode = "allowlist" fails on Docker Desktop, and
  the kernel probe is skipped when Harbor runs on Linux", was closed as
  completed on 2026-10-07. Its maintainer verified that Docker Desktop for Mac
  4.86.0 and later fully supports Harbor's allowlist. They also said that
  Docker Desktop on Windows works, and that WSL 2.7.8 and later, with kernel
  6.18.33.1 or later, has the `fib` support.
- Harbor issue #2593, "Static no-network Docker policies unnecessarily require
  nftables egress control on macOS", is open. One report there says Harbor
  0.22.0 rejected `no-network` on Docker Desktop 4.87.0 with
  `network_mode='no-network' is not supported`. On Docker Desktop 4.97.0,
  stock Harbor 0.22.0 ran the `no-network` probe above and enforced it.

### Traps

- **`DOCKER_DEFAULT_PLATFORM=linux/amd64` breaks the sidecar on arm64.**
  Harbor builds the sidecar for the daemon's own platform, so `compose up`
  fails with `image with reference
  harbor-prebuilt:harbor-docker-egress-control-sidecar…
  was found but does not provide the specified platform (linux/amd64)`. With
  the V2 image already pulled for `linux/amd64`, and the variable unset, the
  run works.
- The V2 images have no arm64 manifest. Pulling one on an arm64 host without
  `--platform linux/amd64` fails with `no matching manifest for
  linux/arm64/v8`.
- Harbor 0.22.0's oracle reads its environment from `[solution.env]` in
  `task.toml`. The probe script's docstring says `[agent.env]`, which Harbor
  does not read, so `LLM_ENDPOINT` set there is never seen and the probe skips
  the allowed host.
- On this machine, `docker pull` once hung in `docker-credential-desktop get`.
  A `DOCKER_CONFIG` directory whose `config.json` has no `credsStore`, and
  keeps `currentContext`, pulls the public images anonymously.

## Limits

- Run on macOS arm64 only, with Docker Desktop 4.97.0. That 4.86.0 is the
  first good release is the Harbor maintainer's finding, not tested here.
- Nothing was run on Windows or on a Linux Docker host. The WSL and Windows
  claims above are the Harbor maintainer's.
- Only the flipt task ran, and only a stand-in agent, not suss's. The verifier
  did not run on stock Harbor. On the earlier patched sidecar it was online
  throughout, and passed.
- The UDP, outside-DNS, `nip.io` and ping results come from the earlier run on
  Docker Desktop 4.73.0 with the split ruleset. That ruleset has the same rules
  as Harbor's, so they should hold on stock Harbor, but they were not re-run.
- The compose network Harbor creates has no IPv6, so IPv6 egress was not
  tested through Harbor.
- The dead pip index was seen in the flipt V2 image only, and only for root.
  Other V2 images were not checked.
