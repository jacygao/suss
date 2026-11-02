---
settles: "§ 3 — the status and exit code of a trial whose `run` exec's own timeout stopped; § 4 — how suss stops `run` at `max_seconds` under `modal`"
issue: [27]
recorded: 2026-11-03
versions: { harbor: 0.22.0, modal: 1.6.3, image: "debian:bookworm-slim (bash 5.2)", host: "macOS arm64 client" }
recheck-when: Harbor upgrades past 0.22.0, or Modal's client upgrades past 1.6.3
---

# Modal's exec timeout returns -1 and kills only the shell

## Intent

- What Harbor's `modal` environment does when a command is still running at
  `exec`'s `timeout_sec`: whether it raises or returns, with what, and whether
  the command is killed.
- Whether suss still marks such a run `timed-out`, stops everything it
  started, and captures the patch.

## Decisions

- suss reads a return code of `-1` from exec as exec's own timeout. It then
  stops `run` as it does after Docker's `RuntimeError`, and records the trial
  `timed-out` with exit code `null`. No exit code a command gives can be
  negative, so `-1` is never `run`'s own.
- The stop also kills the timer's process group. Modal kills suss's wrapper
  at the timeout, so the wrapper cannot end the timer itself, and the timer
  would later signal a process id that may be reused by then.
- At `max_seconds` nothing changes. suss's own limit fires inside the sandbox
  long before exec's timeout, which sits 30 seconds later, and it worked
  unchanged on Modal: `timed-out`, every process holding the token gone, and
  the patch captured.

## Needs your input

Nothing.

## Reference

- `exec("sleep 30; touch /tmp/late", timeout_sec=2)` returned after 2.28s:

  ```
  return_code=-1  stdout=''  stderr=''
  ```

  `ps` straight after showed `sleep 30` reparented to PID 1, still in the dead
  shell's session. `/tmp/late` never appeared: the shell was killed, its
  children were not.
- The `-1` is Modal's client, not Harbor's: `_ContainerProcess.wait` in
  `modal/container_process.py` sets it when the exec's deadline passes.
  Harbor's `_sdk_exec` returns whatever `wait` gives. A command killed by a
  signal reports `128 + n` (`kill -KILL $$` gave `137`), and `exit 7` gave `7`.
- Output printed before the timeout comes back: `echo out; echo err >&2; sleep 31`
  returned `stdout='out\n'`. Its stderr came back as `''`, so what went to
  stderr before a timeout is lost from the result.
- `setsid sleep 32 & wait` under a 2s timeout left `sleep 32` running in a
  session of its own.
- Before the fix, a `run` stopped only by exec's timeout came back as
  `harness-failed` with exit code `-1`. `run.sh`, its children, the `setsid`
  ones and the timer were all still running 12s later.
- `run.log` held both streams on Modal, since the wrapper redirects stderr
  into it in the sandbox: `before the limit` and `to stderr`, in order.
- `tests/test_modal_sandbox.py` runs these against a real sandbox when
  `SUSS_MODAL_TESTS=1` is set, and costs a few cents.

## Limits

- Only `ModalEnvironment`'s single-container mode was run, not its
  docker-compose mode, which runs commands through `docker compose exec`
  inside the sandbox and may report a timeout as Docker does.
- The issue named Modal 1.5.1. The lock file has 1.6.3, and only that was run.
- `gke` and `ack` report a timeout as `return_code=124` with a message in
  stderr (unverified). suss does not read that form, since 124 can be a
  command's own exit code.
