---
settles: "§ 4 — how adapters/copilot-cli/ installs Copilot CLI, the hosts its manifest allows, which credentials it can see, and what its exit code means"
issue: [77]
recorded: 2026-11-02
versions: { copilot-cli: 1.0.105, image: "swe-bench_pro-v2 flipt-io__flipt-02e21636 (Debian 12, amd64, no Node)", host: "Docker Desktop, macOS arm64" }
recheck-when: an arm pins a Copilot CLI version other than 1.0.105
---

# Copilot CLI exits 1 when it cannot authenticate, and calls api.github.com before anything else

## Intent

- How Copilot CLI installs at an exact version in a task image without Node.
- What it exits with when it has no token, a refused token, or a bad flag.
- Which credentials it falls back to, and how `run` keeps it to the arm's token.
- Which hosts it calls to run a prompt, and which settings keep it off others.

## Decisions

- **`install` runs the install script with `VERSION` and `PREFIX=$HOME/.local`.**
  It downloads the release's standalone binary and checks it against the
  release's `SHA256SUMS.txt`. In the flipt image, which has no Node, it
  installed 1.0.105 and `copilot --version` printed `GitHub Copilot CLI 1.0.105.`
- **`run` passes Copilot CLI's exit code through.** Copilot CLI exits 1 with no
  token, with a token GitHub refuses, and with a classic `ghp_` token, and 1 for
  a flag value it rejects. None of these exits 0, so no output needs reading.
- **`run` gives Copilot CLI an empty `COPILOT_HOME` and `GH_CONFIG_DIR` of its
  own, and unsets `GH_TOKEN` and `GITHUB_TOKEN`.** When a token fails, Copilot
  CLI goes on to the next credential source, ending with `gh`'s login, so
  precedence alone does not keep it to the arm's token.
- **The manifest allows `api.github.com` and `api.individual.githubcopilot.com`.**
  The first is observed. The second is inferred from Copilot CLI's runtime
  library, for a Copilot plan bought by an individual.

## Needs your input

- **Which Copilot plan the token belongs to.** The model host comes from
  GitHub's answer for the token's account. I recommend the first real run read the hosts it
  reaches and set `allowed_hosts` to them. A Business or Enterprise seat
  is expected to need `api.business.githubcopilot.com` or
  `api.enterprise.githubcopilot.com` instead (unverified).

## Reference

- Credential sources, in the order the debug log tried them: `Hmac`, `ApiKey`,
  `CopilotApiToken`, `GitHubToken` (`COPILOT_GITHUB_TOKEN`, `GH_TOKEN`,
  `GITHUB_TOKEN`), `UserToken` (a login stored under `COPILOT_HOME`), `GhCli`.
- With a fine-grained-shaped token GitHub refused, every connection went to
  `api.github.com:443`, through a proxy that logged each `CONNECT`. Copilot CLI
  printed `Failed to fetch PAT user login (401): GitHub returned: Bad credentials`
  on stderr and exited 1.
- The runtime reads its model host from `endpoints.api` in the answer to
  `https://api.github.com/copilot_internal/user`, which also carries
  `endpoints.proxy`, `endpoints.telemetry` and `endpoints.exp`. Its default is
  `https://api.githubcopilot.com`.
- `--no-auto-update` and `COPILOT_AUTO_UPDATE=false` stop the update check.
  `--disable-client-telemetry` stops the CLI's own telemetry, and
  `--disable-builtin-mcps` stops the GitHub MCP server at
  `https://api.githubcopilot.com/mcp`.
- A prompt passed with `-p` is one argument, and a 140,000-byte one failed to
  start with `Argument list too long`, exit 126.
- Copilot CLI writes its package to `$HOME/.cache/copilot/pkg/` and a compile
  cache to `/tmp/node-compile-cache`, both outside the workspace. With
  `COPILOT_HOME` and `--log-dir` set, a run left the workspace unchanged.

## Limits

- No token Copilot accepts was used, so no model was called. The exit code of a
  run that finishes, and what it prints, were not seen.
- Whether an unknown or unavailable `--model` fails or falls back to another
  model was not seen: model resolution happens after authentication.
- Whether `endpoints.telemetry` or `endpoints.exp` is called once the token
  authenticates, with the flags above, was not seen.
- Only the amd64 flipt image, run under emulation, was used.
