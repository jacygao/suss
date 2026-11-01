---
settles: "§ 3 — what stopping the harness inside the sandbox kills; § 4 — what suss stops when it stops `run` at `max_seconds`, and what a command's environment holds"
issue: [60]
recorded: 2026-11-01
versions: { harbor: 0.22.0, docker-engine: 29.9.2, host: "Docker Desktop, macOS arm64", image: "ubuntu:24.04 (bash 5.2.21)" }
recheck-when: Harbor upgrades past 0.22.0, a sandbox runs on an environment other than docker, or Claude Code stops passing its environment to the commands its Bash tool runs
---

# A token in the environment finds every process `run` started

## Intent

- How suss can stop the processes `run` started in a session and process group
  of their own, which a kill of `run`'s process group misses.
- What it must leave running.

## Decisions

- suss starts each command with `SUSS_START_TOKEN` set to a new random token.
  Every process the command starts inherits it, whatever session it moves to,
  and keeps it in `/proc/<pid>/environ` after it is reparented to PID 1.
- At `max_seconds`, on a cancel, and when exec's own timeout fires first, suss
  kills `run`'s process group, then every process whose environment holds the
  token. It repeats the sweep until a pass finds none, at most 50 passes, so a
  process forked mid-sweep is caught by the next.
- The stop is over before `harness.run` returns, so nothing it killed can
  change the workspace while suss captures the patch.
- A process without the token survives: the sandbox's PID 1, the wrapper suss
  runs `run` under, suss's own later execs, and another start's processes,
  whose token differs.

## Needs your input

- § 4 says a command's environment "holds only the variables named in `env`".
  The token is a variable suss adds, so the harness sees it. It is not from
  the `.env`, and the image's own variables already reach the harness the same
  way, but the sentence as written excludes it. I recommend § 4 say that suss
  also sets `SUSS_START_TOKEN`, and that the harness must leave it in the
  environment of what it starts.

## Reference

- The sweep is a bash function, `suss_kill_marked`, that reads each
  `/proc/<pid>/environ` with `read -d ''`. It needs no `ps`, `grep` or `tr`.
  A process that has exited reads as empty there, so a zombie never matches.
- The timer sends the wrapper `SIGUSR1` instead of killing `run` itself. The
  wrapper does the kill and the sweep, then exits, so exec does not return
  before the stop is over.
- Claude Code passes its environment to the commands its Bash tool runs: in
  the first real `suss run --check`, every `go build` left running carried
  `IS_SANDBOX=1`, which `run.sh` exports.
- In an `ubuntu:24.04` container, run as `root` and as `ubuntu`, a `run` that
  started `setsid sleep 301 &`, `( setsid sleep 302 & )` and `sleep 303 &`
  left none of them after the stop. A `setsid sleep 399` started by a separate
  `docker exec`, as the same user, was still running.

## Limits

- A process that clears its environment, as `env -i` does, or whose children
  get an environment built afresh, escapes unless it is still in `run`'s
  process group. This is accepted: the grading happens in a fresh sandbox, and
  the stop still completes before the patch is captured.
- `/proc/<pid>/environ` is readable only by the process's own user and root.
  A process the harness starts as another user, through `sudo` or a setuid
  binary, escapes when the agent's user is not root.
- Real Claude Code was not run against the token itself. That it passes the
  token on rests on its passing `IS_SANDBOX` on, which is the same mechanism.
- Only Docker Desktop on macOS arm64 was run.
