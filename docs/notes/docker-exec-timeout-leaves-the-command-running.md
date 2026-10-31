---
settles: "§ 4 — how suss stops `run` at `max_seconds`"
issue: [35]
recorded: 2026-10-31
versions: { harbor: 0.22.0, docker-desktop: "4.73.0 (engine 29.4.3)", docker-compose: v5.1.3, host: "macOS, arm64" }
recheck-when: Harbor upgrades past 0.22.0, or a sandbox runs on an environment other than docker
---

# Docker's exec timeout leaves the command running in the container

## Intent

- Whether a command that Harbor's `docker` environment stops at `exec`'s
  `timeout_sec` is still running in the container afterwards.

## Decisions

- suss stops `run` inside the sandbox. `harness.py` runs it under a bash
  wrapper that kills its whole process group with SIGKILL after `max_seconds`.
  Harbor's own timeout raises on the host and leaves the command, and every
  child it started, running in the container until they finish.
- The wrapper needs only bash and `sleep`. Harbor runs every command in the
  main container as `bash -c`, so bash is there in any task image Harbor can
  run. `timeout` is not used, since an image need not have it.
- `exec` still gets a `timeout_sec`, 30 seconds past `max_seconds`, as a
  backstop for an image with no `sleep`. Whichever limit fires, the run is
  `timed-out` with exit code `null`.

## Needs your input

- A run that exits on its own can leave background processes running, which
  can still change the workspace while suss captures the patch. Nothing kills
  them today. I recommend § 4 say whether suss kills what `run` left behind
  when it exits, since the same wrapper could do it in one line.

## Reference

- At the timeout, `_collect_buffered_output` and `_collect_streamed_output` in
  `harbor/environments/docker/docker.py` both call `_terminate_process` on the
  local `docker compose exec` process: SIGTERM, then SIGKILL after 5 seconds.
  Nothing is sent into the container, and Docker does not pass the client's
  death on to the process it started.
- The command keeps running even while it writes to stdout. Docker goes on
  reading its output after the client is gone.
- `exec` gives the command no tty: `tty` prints `not a tty`, and stdin is a
  pipe. Job control (`set -m`) therefore cannot stop a background job with
  SIGTTIN or SIGTTOU.
- `ps` right after `await env.exec("sleep 30; touch /tmp/late", timeout_sec=2)`
  raised `RuntimeError('Command timed out after 2 seconds')`:

  ```
    PID  PPID  PGID STAT     ELAPSED COMMAND
     14     0    14 Ss         00:02 bash -c sleep 30; touch /tmp/late
     20    14    14 S          00:02 sleep 30
  ```

  32 seconds later `/tmp/late` existed.
- The command `exec` runs is a session leader with its own process group, and
  the harness's children inherit it unless they start a group of their own.

## Limits

- Only the `docker` environment, on Docker Desktop for macOS arm64, was run.
  Linux Docker was not, though the client-side kill is the same code.
- A child that calls `setsid`, or otherwise starts its own process group,
  escapes the kill. Whether Claude Code, node or git do that for the commands
  they spawn was not checked.
- An image with bash but no `sleep` was not tried. There the run is stopped
  only by exec's backstop, which, as above, leaves it running.
- The wrapper ran on bash 5.2 (Debian 12), on Alpine's bash with busybox
  `sleep`, and on bash 3.2 locally. No other bash was tried.
