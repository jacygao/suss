"""
SussAgent, the Harbor custom agent that runs one trial of one arm.

Harbor constructs it on the host, then calls `setup` in the setup phase and
`run` in the agent phase, in the same sandbox. Everything read from the host,
suss.toml and the manifest, is read in the constructor, so a bad config fails
before a sandbox starts.
"""

import asyncio
import json
import shlex
import tempfile
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path, PurePosixPath
from typing import Any

from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

from suss import config as suss_config
from suss import harness, lanes
from suss.harness import SandboxPaths
from suss.workspace import capture_patch, find_workspace, read_base, read_start_tree

RECORD_NAME = "suss.json"
# The dataset each lane's tasks come from, which names its trials' grader.
DATASETS = {lanes.REBENCH: "swe-rebench-07-2026", lanes.SENIOR: "senior-swe-bench-v2026.06"}
# How long a cancelled run may take to capture the patch and write suss.json.
CLEANUP_SECONDS = 60


@dataclass(frozen=True)
class _Prepared:
    """What the setup phase found, which the agent phase needs."""

    workspace: str
    base: str
    start_tree: str
    install_exit_code: int | None


class SussAgent(BaseAgent):
    def __init__(
        self,
        *,
        arm: Any,
        lane: Any,
        config: Any,
        paths: SandboxPaths = SandboxPaths(),
        **kwargs: Any,
    ):
        """
        Read the arm's settings on `lane` from the suss.toml at `config`, a
        host path relative to the directory `harbor run` started in.

        `paths` is where the sandbox keeps things. Harbor never passes it, so
        production gets the default. Raises TypeError when Harbor parsed `arm`,
        `lane` or `config` as something other than a string, ConfigError or
        ManifestError when the settings cannot be read, and LaneError for a
        lane with no dataset in DATASETS, and as `grader` does.
        """
        for key, value in (("arm", arm), ("lane", lane), ("config", config)):
            if not isinstance(value, str):
                quoted = shlex.quote(f"{key}={json.dumps(json.dumps(value))}")
                raise TypeError(
                    f"--ak {key} must be a string, but Harbor parsed it as JSON into"
                    f" {value!r}, a {type(value).__name__}. Quote it as a JSON string:"
                    f" --ak {quoted}"
                )
        super().__init__(**kwargs)
        self.paths = paths
        self._config = suss_config.load(Path(config), arm, lane)
        self._manifest = harness.load_manifest(self._config.arm.harness)
        if lane not in DATASETS:
            raise lanes.LaneError(
                f"suss cannot name the grader of a trial on {lane}, so it runs none there"
            )
        self._grader = grader(self._config, lane)
        self._prepared: _Prepared | None = None

    @staticmethod
    def name() -> str:
        return "suss"

    def version(self) -> str | None:
        return version("suss")

    async def setup(self, environment: BaseEnvironment) -> None:
        workspace = await find_workspace(environment)
        base = await read_base(environment, workspace)
        start_tree = await read_start_tree(environment, workspace)
        await _upload(environment, self._config.arm.harness, self.paths.adapter)
        await _upload_bytes(
            environment, json.dumps(self._config.arm.params).encode(), self.paths.params
        )
        install_exit_code = await harness.install(
            environment, self._manifest, workspace, self._config.env, self.paths
        )
        self._prepared = _Prepared(workspace, base, start_tree, install_exit_code)

    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        """
        Run the harness, then capture the patch and write suss.json.

        The patch is captured however run ends, even when it raises, since a
        lost change reads exactly like a harness that changed nothing. suss.json
        is written only when run returned a status.

        When the task is cancelled, as Harbor's agent timeout does, the harness
        is stopped first and the trial is `timed-out`. The CancelledError is
        raised once suss.json is written, or once CLEANUP_SECONDS have passed,
        so Harbor still sees the timeout.
        """
        prepared = self._prepared
        if prepared is None:
            raise RuntimeError("SussAgent.run was called before setup")

        await _upload_bytes(environment, instruction.encode(), self.paths.instruction)
        try:
            result = await harness.run(
                environment,
                self._manifest,
                self._config.arm,
                prepared.workspace,
                self._config.env,
                prepared.install_exit_code,
                self.paths,
            )
        except BaseException:
            await capture_patch(
                environment, prepared.workspace, prepared.start_tree, self.paths.logs
            )
            raise

        try:
            # Harbor waits on a cancelled agent with no limit.
            async with asyncio.timeout(CLEANUP_SECONDS if result.cancelled else None):
                await self._record(environment, prepared, result)
        finally:
            if result.cancelled is not None:
                raise result.cancelled

    async def _record(
        self, environment: BaseEnvironment, prepared: _Prepared, result: harness.HarnessResult
    ) -> None:
        """Capture the patch, then write suss.json beside it."""
        patch_bytes = await capture_patch(
            environment, prepared.workspace, prepared.start_tree, self.paths.logs
        )
        record = {
            "arm": self._config.arm.name,
            "harness": {"name": self._manifest.name, "version": self._manifest.version},
            "params": self._config.arm.params,
            "grader": self._grader,
            "base": prepared.base,
            "status": result.status,
            "exit_codes": result.exit_codes,
            "run_seconds": result.run_seconds,
            "patch_bytes": patch_bytes,
        }
        await _upload_bytes(
            environment,
            (json.dumps(record, indent=2) + "\n").encode(),
            f"{self.paths.logs.rstrip('/')}/{RECORD_NAME}",
        )


def grader(settings: suss_config.Config, lane: str) -> dict[str, str]:
    """
    What grades a trial of `lane`, a lane in DATASETS: its dataset, and the
    digest of the download it is built from.

    On senior it adds the litellm requirement the lane's test.sh installs, and
    the models and validation harness the regrade's verifier will use. Those
    follow from the variables `suss run` gives the regrade's Harbor, which only
    the settings the lane's tasks read can reach. Raises LaneError when the
    download cannot be digested or a task.toml in it cannot be read.
    """
    download = settings.download(lane)
    found = {"dataset": DATASETS[lane], "digest": lanes.digest(download)}
    if lane == lanes.SENIOR:
        reads = suss_config.host_variables(lanes.read_tasks(lanes.lane_tasks(lane, download)))
        found["litellm"] = lanes.PINNED_LITELLM.decode()
        found |= suss_config.grading_choices(suss_config.verifier_env(settings, reads))
    return found


async def _upload_bytes(env: BaseEnvironment, data: bytes, target: str) -> None:
    """
    Put `data` at `target` in the sandbox, byte for byte.

    It goes as a file, never through a shell, which would expand or re-encode it.
    """
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / "upload"
        source.write_bytes(data)
        await _upload(env, source, target)


async def _upload(env: BaseEnvironment, source: Path, target: str) -> None:
    """
    Copy a file, or a directory's contents, to `target`, and give it to the
    agent's user.

    Harbor uploads as root, and the directories above /opt/suss/ may be
    writable only by root, so those steps run as root.
    """
    directory = target if source.is_dir() else str(PurePosixPath(target).parent)
    await _as_root(env, f"mkdir -p {shlex.quote(directory)}")
    if source.is_dir():
        await env.upload_dir(source, target)
    else:
        await env.upload_file(source, target)
    if env.default_user is not None:
        owner = shlex.quote(str(env.default_user))
        await _as_root(env, f"chown -R {owner} {shlex.quote(target)}")


async def _as_root(env: BaseEnvironment, command: str) -> None:
    """
    Run a command as root where an agent user is set.

    Where none is, exec runs as the sandbox's own user, which owns what it
    uploads, so there is no user to override.
    """
    user = "root" if env.default_user is not None else None
    result = await env.exec(command, user=user)
    if result.return_code != 0:
        output = (result.stdout or "").strip()
        raise harness.HarnessError(f"`{command}` exited {result.return_code}: {output}")
