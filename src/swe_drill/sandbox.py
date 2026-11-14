"""
Commands SWE-drill runs in a trial's sandbox: uploads, and commands it can stop
together with every process they started.

Stopping cannot rely on exec's own timeout or on cancelling it. Docker's stops
only the client on the host, and Modal's kills only the wrapping shell, so
either leaves the command running in the sandbox. Every command SWE-drill means to
stop therefore carries MARKER, set to a token of its own, and a stop kills
every process whose environment holds it, found through /proc.
"""

import asyncio
import math
import secrets
import shlex
import tempfile
from collections.abc import Mapping
from pathlib import Path, PurePosixPath

from harbor.environments.base import BaseEnvironment

# Every process a marked command starts inherits it, whatever session or
# process group it moves to.
MARKER = "SWE_DRILL_START_TOKEN"
# What the in-sandbox limit prints when it stopped the command.
TIMED_OUT = "swe-drill: stopped at the time limit"
# exec's own timeout sits this far past the limit, as a backstop for a sandbox
# where the limit cannot run.
EXEC_TIMEOUT_GRACE_SECONDS = 30
# The return code Harbor's modal environment reports when exec's timeout fires.
# No command's exit code can be negative, so it is never the command's own.
MODAL_EXEC_TIMED_OUT = -1
# How long a stop may take. Harbor waits on a cancelled agent with no limit,
# so a stuck exec here would hang the trial.
STOP_SECONDS = 10
# Each pass of a stop kills what it finds, and the next catches what was
# forked meanwhile. The cap keeps a process that will not die from hanging it.
STOP_PASSES = 50


class SandboxError(Exception):
    """A command SWE-drill needs in the sandbox failed. The message names it and carries its output."""


def new_token() -> str:
    """A fresh value for MARKER, so a stop never acts on a token an earlier command left behind."""
    return secrets.token_hex(8)


async def upload_bytes(env: BaseEnvironment, data: bytes, target: str) -> None:
    """
    Put `data` at `target` in the sandbox, byte for byte.

    It goes as a file, never through a shell, which would expand or re-encode it.
    """
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / "upload"
        source.write_bytes(data)
        await upload(env, source, target)


async def upload(env: BaseEnvironment, source: Path, target: str) -> None:
    """
    Copy a file, or a directory's contents, to `target`, and give it to the
    agent's user.

    Harbor uploads as root, and the directories above the target may be
    writable only by root, so those steps run as root.
    """
    directory = target if source.is_dir() else str(PurePosixPath(target).parent)
    await as_root(env, f"mkdir -p {shlex.quote(directory)}")
    if source.is_dir():
        await env.upload_dir(source, target)
    else:
        await env.upload_file(source, target)
    if env.default_user is not None:
        owner = shlex.quote(str(env.default_user))
        await as_root(env, f"chown -R {owner} {shlex.quote(target)}")


async def as_root(env: BaseEnvironment, command: str) -> str:
    """
    Run a command as root where an agent user is set, and return its output.

    Where none is, exec runs as the sandbox's own user, which owns what it
    uploads, so there is no user to override. Raises SandboxError when it fails.
    """
    return await run(env, command, user=_root(env))


async def run(env: BaseEnvironment, command: str, user: str | None = None) -> str:
    """Run a command, as the agent's user by default, and return its output, raising SandboxError when it fails."""
    result = await env.exec(command, user=user)
    if result.return_code != 0:
        output = (result.stdout or "").strip()
        raise SandboxError(f"`{command}` exited {result.return_code}: {output}")
    return result.stdout or ""


def _root(env: BaseEnvironment) -> str | None:
    return "root" if env.default_user is not None else None


async def run_limited(
    env: BaseEnvironment,
    argv: list[str],
    *,
    cwd: str,
    env_vars: Mapping[str, str],
    log_path: str,
    timeout_sec: float | None = None,
) -> int | None:
    """
    Run `argv` in `cwd` with exactly `env_vars` added, its stdout and stderr in
    `log_path`, and return its exit code, or None when it was stopped at
    `timeout_sec`.

    When the task is cancelled, it stops the command and everything it
    started inside the sandbox, then raises the CancelledError. The command
    runs as the environment's default user. The log's directory must exist.
    """
    # The shell writes the log, not SWE-drill, so the log holds what was printed
    # before a timeout, when exec returns no output at all. It also holds
    # stderr whether or not the environment merges it into stdout.
    command = shlex.join(argv) + f" > {shlex.quote(log_path)} 2>&1"
    token = new_token()
    log = PurePosixPath(log_path)
    group_file = str(log.parent / f"{log.stem}-{token}.pgid")
    command = _limited(f"{MARKER}={token} {command}", timeout_sec, group_file, token)
    exec_timeout = None
    if timeout_sec is not None:
        exec_timeout = math.ceil(timeout_sec) + EXEC_TIMEOUT_GRACE_SECONDS
    try:
        result = await env.exec(command, cwd=cwd, env=dict(env_vars), timeout_sec=exec_timeout)
    except RuntimeError as error:
        if not _is_exec_timeout(error):
            raise
        # The command goes on in the sandbox after exec gives up on it.
        await _stop(env, group_file, token)
        return None
    except asyncio.CancelledError:
        await _stop(env, group_file, token)
        raise
    if result.return_code == MODAL_EXEC_TIMED_OUT:
        # Modal killed the wrapper alone, and left the command running.
        await _stop(env, group_file, token)
        return None
    if TIMED_OUT in (result.stdout or "").splitlines():
        return None
    return result.return_code


def _limited(command: str, seconds: float | None, group_file: str, token: str) -> str:
    """
    Wrap a command so that the sandbox itself can kill it, and every process
    it started: after `seconds`, when given, and through `_stop` at any time.

    The wrapper needs only bash, which Harbor runs every command with, and
    `sleep`. It exits with the command's exit code. At the limit it prints
    TIMED_OUT, then kills the command and every process carrying `token`, and
    exits only once they are dead. While the command runs, `group_file` holds
    the ids of its process group and of the timer's, one per line, and the
    wrapper deletes it once the command exits.
    """
    group = shlex.quote(group_file)
    lines = [
        _KILL_MARKED,
        # Job control puts each background job in a process group of its
        # own, which a kill of the group then reaches all of.
        "set -m",
        f"{command} &",
        "run=$!",
        f'echo "$run" > {group}',
    ]
    if seconds is not None:
        lines += [
            # The timer signals the wrapper rather than killing the command
            # itself, so the kill finishes before the wrapper exits. The
            # handler does the kill and exits, so a signal that arrives before
            # `wait` starts is not lost. `&&`, so that a missing sleep leaves
            # the run to exec's backstop.
            "on_timeout() {",
            f"  echo {shlex.quote(TIMED_OUT)}",
            '  kill -KILL -- "-$run" 2> /dev/null',
            f"  drill_kill_marked {token}",
            f"  rm -f {group}",
            "  exit 137",
            "}",
            "trap on_timeout USR1",
            f"{{ sleep {seconds} > /dev/null 2>&1 && kill -USR1 $$; }} &",
            "timer=$!",
            # So that a stop also ends the timer where the wrapper is gone,
            # which would otherwise signal a process id that may be reused.
            f'echo "$timer" >> {group}',
        ]
    lines += ['wait "$run"', "code=$?"]
    if seconds is not None:
        lines += [
            # Ignored rather than reset, since USR1's default would kill the
            # wrapper before it reports the command's exit code.
            "trap '' USR1",
            'kill -KILL -- "-$timer" 2> /dev/null',
        ]
    lines += [f"rm -f {group}", 'exit "$code"']
    return "\n".join(lines)


# A bash function that kills every process whose environment holds MARKER set
# to the token it is given. It reads /proc with bash alone, since an image need
# not have ps, grep or tr. A process that has exited reads as empty there.
_KILL_MARKED = f"""drill_kill_marked() {{
  local pass p v found
  for ((pass = 0; pass < {STOP_PASSES}; pass++)); do
    found=
    for p in /proc/[0-9]*; do
      while IFS= read -r -d '' v; do
        if [ "$v" = "{MARKER}=$1" ]; then
          kill -KILL "${{p#/proc/}}" 2> /dev/null && found=1
          break
        fi
      done 2> /dev/null < "$p/environ"
    done
    [ -n "$found" ] || return 0
  done
}}"""


async def stop_marked(env: BaseEnvironment, token: str) -> None:
    """
    Kill every process in the sandbox that carries MARKER set to `token`.

    It runs as the agent's user, not root: root in a container without
    CAP_SYS_PTRACE cannot read another user's /proc/<pid>/environ, so it would
    find none of them. Never raises an Exception, and gives up after
    STOP_SECONDS. A stop that fails leaves those processes running, but the
    caller still has a patch to capture and a trial to record.
    """
    await _quietly(env, f"{_KILL_MARKED}\ndrill_kill_marked {token}", None)


async def _stop(env: BaseEnvironment, group_file: str, token: str) -> None:
    """Kill the process groups that `group_file` names, if the command is still running, and every process that carries `token`."""
    group = shlex.quote(group_file)
    command = (
        f"{_KILL_MARKED}\n"
        f"[ -f {group} ] && while read -r g; do kill -KILL -- \"-$g\"; done"
        f" < {group} 2> /dev/null; rm -f {group}\n"
        f"drill_kill_marked {token}"
    )
    await _quietly(env, command, None)


async def _quietly(env: BaseEnvironment, command: str, user: str | None) -> None:
    try:
        await asyncio.wait_for(
            env.exec(command, timeout_sec=STOP_SECONDS, user=user), STOP_SECONDS + 5
        )
    except Exception:
        pass


def _is_exec_timeout(error: RuntimeError) -> bool:
    """
    Whether exec raised because the command was still running at `timeout_sec`.

    This is the form Harbor's docker environment raises, after it has stopped
    its client on the host. The command itself may still be running.
    """
    return str(error).startswith("Command timed out after ")
