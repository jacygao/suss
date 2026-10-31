---
settles: "§ 2 — which environment a SWE-bench Pro lane's sandboxes need; § 5 — what a lane's agent phase can reach; § 6 — the `environment` a SWE-bench Pro lane takes in `suss.toml`, and the `-e` value `harbor run` gets"
issue: [3, 29]
recorded: 2026-10-31
versions: { harbor: 0.22.0, docker-desktop: "4.73.0 (engine 29.4.3)", kernel: 6.12.76-linuxkit, nftables: 1.1.5, host: "macOS, arm64" }
recheck-when: Harbor upgrades past 0.22.0, Harbor changes its egress sidecar or kernel probe, or Docker Desktop changes its kernel
---

# Harbor's egress sidecar enforces SWE-bench Pro's network rules on Docker Desktop once its ruleset is in ip and ip6 tables

## Intent

- Why does Harbor's docker environment refuse SWE-bench Pro tasks on Docker
  Desktop?
- Can the same policy be enforced on Docker Desktop's kernel, and with what
  ruleset?
- With that ruleset, does the agent phase reach only the allowed hosts, while
  setup and verify keep network access?
- Does any non-TCP or IPv6 traffic get out in the agent phase?
- Where should the fix live?
- What must a Windows (WSL) machine's kernel have for the fix to work there?

## Decisions

- **Run SWE-bench Pro lanes under local Docker, with the sidecar's ruleset in
  one `ip` table and one `ip6` table.** Docker Desktop's kernel has everything
  that ruleset needs. The refusal came from one kernel option the ruleset does
  not have to use.
- **The root cause is that `fib` in an `inet` table needs
  `CONFIG_NFT_FIB_INET`.** Docker Desktop 4.73.0's kernel has
  `CONFIG_NFT_FIB_IPV4=y` and `CONFIG_NFT_FIB_IPV6=y` but not
  `CONFIG_NFT_FIB_INET`. nftables resolves an expression by the table's family,
  so `fib` in an `inet` table fails to load even when the per-family modules
  are built in, and Harbor's kernel probe refuses the task before that.
- **The split ruleset keeps every rule's meaning.** `fib daddr type local`
  still exempts the container's own addresses, its `eth0` address included, on
  both families. TCP still goes through gost, and other protocols are still
  rejected.
- **On a patched run, each phase gets the network the task asks for.** In the
  agent phase, only `api.anthropic.com` answers. Without `--allow-agent-host`,
  nothing does. Setup installs a package, and the verifier is online.
- **Ship the fix in suss as a custom Harbor environment, passed as
  `-e <module>:<Class>`, and offer the same change to Harbor.** That uses
  Harbor's supported import-path seam rather than editing or monkeypatching
  it, and it is deleted once a Harbor release carries the fix.

## Needs your input

- Whether suss ships the environment subclass now (option b below) or waits
  for Harbor (option a). I recommend both: ship b, and offer a upstream. The
  diff is small and b is gone once a release has it.
- The spec contradicts this note in § 2 ("Every SWE-bench Pro lane needs
  `environment = "modal"`"), § 5 and § 6 (`-e modal` in the commands, and "A
  SWE-bench Pro lane needs `modal`"). Reconciling it is a separate change.
- Two egress paths stay open in the agent phase. Both are in Harbor's own
  ruleset, not added by the split, and neither is a Docker Desktop issue:
  - DNS through Docker's resolver at `127.0.0.11`, a local address, which the
    `fib` rule exempts. A random name under `nip.io` resolved, so queries reach
    outside name servers. Any name can be looked up, and data can leave in the
    query.
  - ICMP echo, which both rulesets accept. `1.1.1.1` answered a ping.

  I recommend accepting both for now and recording them in the spec. Neither
  can fetch a repository or a package index, which is what the agent phase is
  closed against.
- How suss gets the amd64-only V2 images on an arm64 host. Setting
  `DOCKER_DEFAULT_PLATFORM=linux/amd64` breaks the sidecar, which Harbor builds
  for the daemon's own arm64 platform. I recommend `suss run` pulling each lane's images with
  `docker pull --platform linux/amd64` before calling Harbor.

## Reference

### The working ruleset

The rules in Harbor 0.22.0's `bin/network-policy`, with values expanded, put
in one table per family:

```
table ip gost_egress {
  chain output {
    type nat hook output priority dstnat; policy accept;
    meta mark 114514 return
    fib daddr type local return
    meta l4proto tcp redirect to :12345
  }
  chain egress {
    type filter hook output priority filter; policy accept;
    meta mark 114514 accept
    fib daddr type local accept
    ip protocol icmp accept
    meta l4proto != tcp reject
  }
}
table ip6 gost_egress {
  chain output {
    type nat hook output priority dstnat; policy accept;
    meta mark 114514 return
    fib daddr type local return
    meta l4proto tcp redirect to :12345
  }
  chain egress {
    type filter hook output priority filter; policy accept;
    meta mark 114514 accept
    fib daddr type local accept
    ip6 nexthdr icmpv6 accept
    meta l4proto != tcp reject
  }
}
```

Harbor's original, in an `inet` table, fails in the sidecar image on Docker
Desktop with:

```
/dev/stdin:9:5-18: Error: Could not process rule: No such file or directory
    fib daddr type local return
    ^^^^^^^^^^^^^^
/dev/stdin:17:5-18: Error: Could not process rule: No such file or directory
    fib daddr type local accept
    ^^^^^^^^^^^^^^
```

`meta nfproto ipv4 fib daddr type local accept` in an `inet` table fails the
same way. `meta oifname "lo" accept` in an `inet` table loads, but was not run
end to end.

### Kernel options the split ruleset needs

`NF_TABLES_IPV4`, `NF_TABLES_IPV6`, `NFT_NAT`, `NFT_REDIR`, `NFT_REJECT`,
`NFT_REJECT_IPV4`, `NFT_REJECT_IPV6`, `NFT_FIB_IPV4` and `NFT_FIB_IPV6`, each
`=y`, or `=m` with the module loadable. It does not need `NF_TABLES_INET`,
`NFT_REJECT_INET` or `NFT_FIB_INET`.

On a Windows machine with Docker Desktop's WSL backend, in any WSL shell (all
WSL distros share the kernel Docker Desktop runs on):

```bash
zcat /proc/config.gz | grep -E '^(# )?CONFIG_(NF_TABLES_IPV4|NF_TABLES_IPV6|NFT_NAT|NFT_REDIR|NFT_REJECT|NFT_REJECT_IPV4|NFT_REJECT_IPV6|NFT_FIB_IPV4|NFT_FIB_IPV6|NFT_FIB_INET)[= ]'
```

A line `# CONFIG_... is not set` for any option but `NFT_FIB_INET` means the
split ruleset will not load. To test the kernel itself, with the ruleset above
saved as `split-family.nft`:

```bash
docker run --rm -i --cap-add NET_ADMIN --entrypoint nft gogost/gost:3.2.7-nightly.20260602 -c -f - < split-family.nft; echo "exit=$?"
```

`nft -c` asks the kernel to validate the ruleset without applying it, and needs
`NET_ADMIN`. On Docker Desktop it exits 0 for the split ruleset, and 1 with the
same `Could not process rule` errors for the `inet` one.

### Where the fix can live

- **(a) Upstream to Harbor.** Two sidecar files and the kernel probe change:
  - `harbor/environments/docker/harbor-docker-egress-control-sidecar/bin/network-policy`:
    the ruleset as one table per family, and `inet` replaced in delete, list
    and rules.
  - `harbor/environments/docker/harbor-docker-egress-control-sidecar/entrypoint.sh`:
    the cleanup deletes both tables.
  - `harbor/environments/docker/docker.py`: `_EGRESS_CONTROL_KERNEL_PROBE_SCRIPT`
    tests `CONFIG_NFT_FIB_IPV4` and `CONFIG_NFT_FIB_IPV6` instead of
    `CONFIG_NFT_FIB_INET`. A stronger probe runs the sidecar image with
    `NET_ADMIN` and `nft -c` on the ruleset. That tests what the rules need,
    modules and all, and still works where `/proc/config.gz` is absent.

  The diff, below, adds or removes 39 lines. It fixes every Harbor user, and suss carries
  nothing. When a release would include it is not suss's to decide.
- **(b) A custom environment in suss.** A subclass of `DockerEnvironment`
  that overrides `_EGRESS_CONTROL_SIDECAR_CONTEXT_PATH` (pointing at a copy of
  the sidecar context with the patch applied), `_EGRESS_CONTROL_SIDECAR_DOCKER_NAME`
  and `_egress_control_kernel_support`. Harbor loads it with
  `harbor run -e suss.<module>:<Class>`, which the stock CLI accepts, and a
  probe run that way gave the same agent-phase result as the patched run below.
  Costs:
  - suss carries Harbor's five sidecar files, 225 lines, under Harbor's
    Apache-2.0 licence.
  - It depends on three private attributes of a pinned Harbor. A rename on
    upgrade turns it back into the refusal, which is loud.
  - `_egress_control_kernel_support` must be replaced whole, because Harbor's
    version reads `DockerEnvironment._EGRESS_CONTROL_KERNEL_PROBE_SCRIPT`, not
    `cls`.

  Patching the same attributes at runtime, in a wrapper that then calls
  Harbor's CLI, works too but needs suss to stop calling `harbor` directly. The
  subclass needs neither.
- **(c) Other routes.**
  - Docker Desktop could enable `CONFIG_NFT_FIB_INET`. That is Docker's to do,
    and users on older releases would still be refused.
  - The exemption could be `meta oifname "lo"` in the one `inet` table, since
    Linux routes traffic to any local address through `lo`. That is a smaller
    diff, but its meaning differs from `fib` at the edges, and it was not run
    end to end.

The diff for (a), against Harbor 0.22.0:

```diff
--- a/harbor/environments/docker/harbor-docker-egress-control-sidecar/bin/network-policy
+++ b/harbor/environments/docker/harbor-docker-egress-control-sidecar/bin/network-policy
@@ -30,12 +30,17 @@
   touch "$ALLOWLIST"
 }
 
-setup_nftables() {
-  nft --file - <<EOF
-add table inet $NFTABLES_RULESET_NAME
-flush table inet $NFTABLES_RULESET_NAME
+# One table per family, not one inet table: fib in an inet table needs
+# CONFIG_NFT_FIB_INET, which some kernels (Docker Desktop's) leave out, while
+# fib in ip and ip6 tables needs only CONFIG_NFT_FIB_IPV4 and _IPV6.
+family_table() {
+  family="$1"
+  icmp_rule="$2"
+  cat <<EOF
+add table $family $NFTABLES_RULESET_NAME
+flush table $family $NFTABLES_RULESET_NAME
 
-table inet $NFTABLES_RULESET_NAME {
+table $family $NFTABLES_RULESET_NAME {
   chain output {
     type nat hook output priority dstnat; policy accept;
 
@@ -49,20 +54,27 @@
 
     meta mark $GOST_MARK accept
     fib daddr type local accept
-    ip protocol icmp accept
-    ip6 nexthdr icmpv6 accept
+    $icmp_rule
     meta l4proto != tcp reject
   }
 }
 EOF
 }
 
+setup_nftables() {
+  {
+    family_table ip "ip protocol icmp accept"
+    family_table ip6 "ip6 nexthdr icmpv6 accept"
+  } | nft --file -
+}
+
 remove_nftables() {
-  nft delete table inet "$NFTABLES_RULESET_NAME" 2>/dev/null || true
+  nft delete table ip "$NFTABLES_RULESET_NAME" 2>/dev/null || true
+  nft delete table ip6 "$NFTABLES_RULESET_NAME" 2>/dev/null || true
 }
 
 nftables_enabled() {
-  nft list table inet "$NFTABLES_RULESET_NAME" >/dev/null 2>&1
+  nft list table ip "$NFTABLES_RULESET_NAME" >/dev/null 2>&1
 }
 
 show() {
@@ -120,7 +132,8 @@
     ;;
   rules)
     if nftables_enabled; then
-      nft list table inet "$NFTABLES_RULESET_NAME"
+      nft list table ip "$NFTABLES_RULESET_NAME"
+      nft list table ip6 "$NFTABLES_RULESET_NAME"
     else
       echo "mode: allow-all"
     fi
--- a/harbor/environments/docker/harbor-docker-egress-control-sidecar/entrypoint.sh
+++ b/harbor/environments/docker/harbor-docker-egress-control-sidecar/entrypoint.sh
@@ -14,7 +14,8 @@
 
 cleanup() {
   rm -f "$READY_FILE"
-  nft delete table inet "$NFTABLES_RULESET_NAME" 2>/dev/null || true
+  nft delete table ip "$NFTABLES_RULESET_NAME" 2>/dev/null || true
+  nft delete table ip6 "$NFTABLES_RULESET_NAME" 2>/dev/null || true
 }
 
 mkdir -p "$(dirname "$ALLOWLIST")"
--- a/harbor/environments/docker/docker.py
+++ b/harbor/environments/docker/docker.py
@@ -113,5 +113,7 @@
     _EGRESS_CONTROL_KERNEL_PROBE_SCRIPT = (
         "if [ ! -f /proc/config.gz ]; then exit 0; fi; "
         "zcat /proc/config.gz 2>/dev/null | "
-        "grep -qE '^CONFIG_NFT_FIB_INET=[ym]'"
+        "grep -qE '^CONFIG_NFT_FIB_IPV4=[ym]' && "
+        "zcat /proc/config.gz 2>/dev/null | "
+        "grep -qE '^CONFIG_NFT_FIB_IPV6=[ym]'"
     )
```

### What the probe printed

SWE-bench Pro V2's network probe, made with
`make_probe_task.py --kind net` from
`instance_flipt-io__flipt-02e21636c58e86c51119b63e0fb5ca7b813b07b1`, with
`LLM_ENDPOINT = "https://api.anthropic.com"` added to `[solution.env]`, run
with `harbor run -e docker -a oracle` on the patched sidecar.

With `--allow-agent-host api.anthropic.com`:

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

Without `--allow-agent-host`, every line is `http=000 curl_rc=35`, and
`api.anthropic.com` too.

### Reading a denial

- A denied HTTPS request gives `http=000 curl_rc=35`. gost accepts the TCP
  connection, reads the TLS SNI, and closes it. Its log has `"msg":"bypass"`
  for the destination. A denied plain TCP request gives `curl_rc=52`.
- `curl_rc=6` would be a DNS failure, not a denial. Names resolve in the agent
  phase, through Docker's resolver.
- A denied UDP send fails at once with `Operation not permitted` (`EPERM`).
  The `reject` runs on the output hook, so the send itself is refused.
- `network-policy show` in the sidecar prints `mode: controlled egress` while a
  policy is on, and `mode: allow-all (transparent proxy disabled)` otherwise.

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
- On this machine, `docker pull` hung in `docker-credential-desktop get`. A
  `DOCKER_CONFIG` directory whose `config.json` has no `credsStore`, and keeps
  `currentContext`, pulls the public images anonymously.

## Limits

- Tested on Docker Desktop 4.73.0 on macOS arm64 only. Nothing was run on
  Windows or on a Linux Docker host. The WSL kernel config Microsoft publishes
  for `linux-msft-wsl-6.6.y` builds the needed options as modules (`=m`).
  Whether Docker Desktop's WSL VM loads them was not tested.
- The compose network Harbor creates has no IPv6, so the ip6 table was tested
  in the sidecar image on a separate `docker network create --ipv6` network:
  UDP to `2606:4700:4700::1111` was refused, TCP to it went to gost and was
  denied, and the container's own IPv6 address stayed reachable. An IPv6 run
  through Harbor was not done.
- Setup was shown with a stand-in agent whose `setup()` ran `apt-get install
  jq`, which succeeded, and whose `run()` then failed to install `tree`. The
  oracle does nothing in setup.
- The verifier's network was sampled from outside the trial, every few seconds
  across its 18 minutes: each of 125 samples had the sidecar reporting
  `allow-all` and `curl https://pypi.org/simple/` from the trial's container
  returning 200. The verifier passed, reward 1.0. Whether flipt's tests use the
  network was not checked.
- Only one task, flipt, was run. Only the oracle and two stand-in agents ran,
  not suss's agent.
- `meta oifname "lo"` was only loaded, not enforced.
