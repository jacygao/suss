"""
Read a harness's manifest from its adapter directory, and run its `install`
and `run` in the sandbox as the harness contract fixes.
"""

import shlex
import time
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from harbor.environments.base import BaseEnvironment

from suss.config import Arm
from suss.workspace import LOGS_DIR

MANIFEST_NAME = "suss-harness.toml"
INSTALL_LOG = "install.log"
RUN_LOG = "run.log"


class ManifestError(Exception):
    """A manifest suss cannot read. The message names the manifest's path."""


class HarnessError(Exception):
    """The sandbox cannot run a harness's command, for a reason that is not the harness's."""


@dataclass(frozen=True)
class Manifest:
    name: str
    version: str
    # None when the manifest has no `install`, which is not the same as "".
    install: str | None
    run: str
    allowed_hosts: tuple[str, ...]
    env: tuple[str, ...]


REQUIRED = ("name", "version", "run")
LISTS = ("allowed_hosts", "env")
FIELDS = (*REQUIRED, "install", *LISTS)


def load_manifest(adapter_dir: Path) -> Manifest:
    """
    Read `suss-harness.toml` from the adapter directory.

    Raises ManifestError when the file is missing or is not TOML, when it has
    no `[harness]` table, or when a field is missing, unknown or of the wrong
    type. An absent `allowed_hosts` or `env` is empty.
    """
    path = adapter_dir / MANIFEST_NAME
    try:
        with open(path, "rb") as f:
            document = tomllib.load(f)
    except FileNotFoundError:
        raise ManifestError(f"{path}: no such file") from None
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as error:
        raise ManifestError(f"{path}: not valid TOML: {error}") from None

    table = document.get("harness")
    if not isinstance(table, dict):
        raise ManifestError(f"{path}: no [harness] table")

    unknown = sorted(set(table) - set(FIELDS))
    if unknown:
        raise ManifestError(f"{path}: unknown field {unknown[0]!r} in [harness]")
    for field in REQUIRED:
        if field not in table:
            raise ManifestError(f"{path}: [harness] has no {field!r}")

    for field in (*REQUIRED, "install"):
        if field in table and not isinstance(table[field], str):
            raise ManifestError(f"{path}: {field!r} must be a string")
    for field in LISTS:
        value = table.get(field, [])
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise ManifestError(f"{path}: {field!r} must be a list of strings")

    return Manifest(
        name=table["name"],
        version=table["version"],
        install=table.get("install"),
        run=table["run"],
        allowed_hosts=tuple(table.get("allowed_hosts", [])),
        env=tuple(table.get("env", [])),
    )


@dataclass(frozen=True)
class SandboxPaths:
    """Where the adapter, the files given to the commands, and the logs are in the sandbox."""

    adapter: str = "/opt/suss/adapter"
    instruction: str = "/opt/suss/instruction.md"
    params: str = "/opt/suss/params.json"
    logs: str = LOGS_DIR


Status = Literal["ok", "harness-failed", "timed-out"]


@dataclass(frozen=True)
class HarnessResult:
    status: Status
    # Keyed "install" and "run". None for a command skipped or stopped.
    exit_codes: dict[str, int | None]
    # None when run was skipped.
    run_seconds: float | None


async def install(
    env: BaseEnvironment,
    manifest: Manifest,
    workspace: str,
    dotenv: Mapping[str, str],
    paths: SandboxPaths = SandboxPaths(),
) -> int | None:
    """
    Run the manifest's `install`, and return its exit code.

    Returns None, and runs nothing, when the manifest names no `install`.
    Raises HarnessError when the logs directory cannot be made.
    """
    if manifest.install is None:
        return None
    return await _start(
        env,
        manifest,
        manifest.install,
        ["--workspace", workspace, "--params", paths.params],
        workspace,
        dotenv,
        paths,
        INSTALL_LOG,
    )


async def run(
    env: BaseEnvironment,
    manifest: Manifest,
    arm: Arm,
    workspace: str,
    dotenv: Mapping[str, str],
    install_exit_code: int | None,
    paths: SandboxPaths = SandboxPaths(),
) -> HarnessResult:
    """
    Run the manifest's `run`, stopping it after the arm's `max_seconds`, and
    return the trial's status.

    `install_exit_code` is what `install` returned. When it is non-zero, `run`
    is not started and the trial is `harness-failed`. Raises HarnessError when
    the logs directory cannot be made, and lets any other failure of `exec`
    through, since neither is the harness's doing.
    """
    if install_exit_code not in (0, None):
        return HarnessResult(
            status="harness-failed",
            exit_codes={"install": install_exit_code, "run": None},
            run_seconds=None,
        )

    args = ["--workspace", workspace, "--instruction", paths.instruction, "--params", paths.params]
    # 0 is a cap the arm set, so the test is against None.
    if arm.max_cost_usd is not None:
        args += ["--max-cost-usd", str(arm.max_cost_usd)]

    started = time.monotonic()
    exit_code = await _start(
        env, manifest, manifest.run, args, workspace, dotenv, paths, RUN_LOG, arm.max_seconds
    )
    run_seconds = time.monotonic() - started

    if exit_code is None:
        status = "timed-out"
    elif exit_code == 0:
        status = "ok"
    else:
        status = "harness-failed"
    return HarnessResult(
        status=status,
        exit_codes={"install": install_exit_code, "run": exit_code},
        run_seconds=run_seconds,
    )


async def _start(
    env: BaseEnvironment,
    manifest: Manifest,
    script: str,
    args: list[str],
    workspace: str,
    dotenv: Mapping[str, str],
    paths: SandboxPaths,
    log: str,
    timeout_sec: float | None = None,
) -> int | None:
    """
    Run an adapter's script in the workspace, with its output in the log, and
    return its exit code, or None when it was stopped at the timeout.

    No `user` is passed to exec, so the command runs as the environment's
    default user, which Harbor sets to the task's agent user.
    """
    made = await env.exec(f"mkdir -p {shlex.quote(paths.logs)}")
    if made.return_code != 0:
        raise HarnessError(
            f"cannot make the logs directory {paths.logs}: {(made.stdout or '').strip()}"
        )

    executable = f"{paths.adapter.rstrip('/')}/{script}"
    log_path = f"{paths.logs.rstrip('/')}/{log}"
    # The shell writes the log, not suss, so the log holds what was printed
    # before a timeout, when exec returns no output at all. It also holds
    # stderr whether or not the environment merges it into stdout.
    command = (
        " ".join(shlex.quote(part) for part in [executable, *args])
        + f" > {shlex.quote(log_path)} 2>&1"
    )
    # Only the named variables, so nothing else in the .env reaches the harness.
    passed = {name: dotenv[name] for name in manifest.env if name in dotenv}
    try:
        result = await env.exec(command, cwd=workspace, env=passed, timeout_sec=timeout_sec)
    except RuntimeError as error:
        if _is_exec_timeout(error):
            return None
        raise
    return result.return_code


def _is_exec_timeout(error: RuntimeError) -> bool:
    """
    Whether exec raised because the command was still running at `timeout_sec`.

    This is the form Harbor's docker environment raises, after it has stopped
    the command. Other environments may report a timeout differently.
    """
    return str(error).startswith("Command timed out after ")

