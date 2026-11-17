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
import secrets
import shlex
import tempfile
from pathlib import Path, PurePosixPath

from harbor.environments.base import BaseEnvironment

# Every process a marked command starts inherits it, whatever session or
# process group it moves to.
MARKER = "SWE_DRILL_START_TOKEN"
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
    command = f"{_KILL_MARKED}\ndrill_kill_marked {token}"
    try:
        await asyncio.wait_for(env.exec(command, timeout_sec=STOP_SECONDS), STOP_SECONDS + 5)
    except Exception:
        pass
