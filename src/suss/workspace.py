"""
Find the workspace, and capture everything a harness changed in it as one patch.

Every command runs in the sandbox, through Harbor's environment, since the
workspace is there and not on the machine running suss.
"""

import shlex

from harbor.environments.base import BaseEnvironment

LOGS_DIR = "/logs/agent"
PATCH_NAME = "model.patch"


class WorkspaceError(Exception):
    """A workspace suss cannot read or capture. The message carries git's output."""


async def find_workspace(env: BaseEnvironment) -> str:
    """
    Return the directory the sandbox starts in, which is the image's WORKDIR.

    It must be the top level of a git repository. A directory inside one is
    refused as well, because `git apply` there skips every path outside it,
    so a regrade would drop part of the patch. Raises WorkspaceError naming
    the directory when it is not.
    """
    # Only the newline pwd ends with, since a directory's name can end in whitespace.
    start = (await _run(env, "pwd")).removesuffix("\n")
    # Into stdout, because Modal keeps stderr apart and git explains a failure there.
    result = await env.exec(f"git -C {shlex.quote(start)} rev-parse --show-prefix 2>&1")
    if result.return_code != 0:
        raise WorkspaceError(
            f"the sandbox starts in {start}, where git finds no repository it can read:"
            f" {(result.stdout or '').strip()}"
        )
    prefix = (result.stdout or "").strip()
    if prefix:
        raise WorkspaceError(
            f"the sandbox starts in {start}, which is {prefix} inside a git repository,"
            " not its top level"
        )
    return start


async def read_base(env: BaseEnvironment, workspace: str) -> str:
    """
    Return the commit the workspace's HEAD names.

    Read it before the harness runs, since a harness that commits moves HEAD.
    Raises WorkspaceError when git cannot read it.
    """
    output = await _run(env, f"git -C {shlex.quote(workspace)} rev-parse --verify HEAD")
    return output.strip()


async def capture_patch(
    env: BaseEnvironment, workspace: str, base: str, logs_dir: str = LOGS_DIR
) -> int:
    """
    Write the whole change from the base to model.patch in the logs directory,
    and return its size in bytes.

    The change is the same whether the harness committed it, staged it or left
    it in the working tree. A workspace with no change gives an empty patch.
    Raises WorkspaceError when git fails, so an empty patch only ever means no
    change.
    """
    git = f"git -C {shlex.quote(workspace)}"
    patch = shlex.quote(f"{logs_dir.rstrip('/')}/{PATCH_NAME}")
    # Git writes the file itself, because exec returns stdout as decoded text,
    # which corrupts any byte that is not valid UTF-8. The flags after --binary
    # stop the repository's config writing a patch that does not apply.
    await _run(
        env,
        f"mkdir -p {shlex.quote(logs_dir)}"
        f" && {git} add -A"
        f" && {git} diff --cached --binary"
        " --no-color --no-ext-diff --no-textconv --src-prefix=a/ --dst-prefix=b/"
        f" {shlex.quote(base)} -- > {patch}",
    )
    return int(await _run(env, f"wc -c < {patch}"))


async def _run(env: BaseEnvironment, command: str) -> str:
    result = await env.exec(command)
    if result.return_code != 0:
        raise WorkspaceError(
            f"`{command}` exited {result.return_code}: {(result.stdout or '').strip()}"
        )
    return result.stdout or ""
