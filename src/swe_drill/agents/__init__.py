"""
The contract every arm's agent class keeps. Each adapter in this package is
one of Harbor's own agent classes with Adapter applied, which makes every
trial leave a patch the verifier grades. Nothing here imports an adapter: an
arm's class is loaded by the import path its `agent` names.

Harbor constructs an agent on the host, then calls `setup` in the setup phase
and `run` in the agent phase, in the same sandbox. Everything read from the
host, swe-drill.toml and its .env, is read in the constructor, so a bad config
fails before a sandbox starts.
"""

import asyncio
import inspect
import json
import shlex
import time
from pathlib import Path
from typing import Any, ClassVar

from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext
from harbor.utils.import_path import import_symbol

from swe_drill import config, lanes, sandbox
from swe_drill.config import ConfigError
from swe_drill.workspace import (
    ARTIFACTS_DIR,
    LOGS_DIR,
    PATCH_NAME,
    capture_patch,
    find_workspace,
    read_base,
    read_start_tree,
)

RECORD_NAME = "suss.json"
# The dataset each lane's tasks come from, which names its trials' grader.
DATASETS = {lanes.REBENCH: "swe-rebench-07-2026", lanes.SENIOR: "senior-swe-bench-v2026.06"}
# How long a cancelled run may take to capture the patch and write suss.json.
CLEANUP_SECONDS = 60
# The kwargs SWE-drill itself gives every agent, which an arm's own cannot take.
DRILL_KWARGS = ("arm", "lane", "drill_config")


class Adapter:
    """
    Applied before a Harbor agent class, it records the starting tree before
    the agent runs and, however the run ends, stops every process the agent
    started and writes model.patch to the logs and artifacts directories, the
    same bytes.

    A run whose patch cannot be written raises the capture's error, which
    Harbor does not treat as the agent's, so the trial fails rather than
    being graded as the unchanged repository.

    A class takes the arm kwargs in KWARGS, and needs those in REQUIRED_KWARGS
    and, where REQUIRES_MODEL is set, a model. ALLOWED_HOSTS are the hosts its
    agent phase reaches.
    """

    ALLOWED_HOSTS: ClassVar[tuple[str, ...]] = ()
    KWARGS: ClassVar[tuple[str, ...]] = ()
    REQUIRED_KWARGS: ClassVar[tuple[str, ...]] = ()
    REQUIRES_MODEL: ClassVar[bool] = False

    def __init__(
        self,
        *,
        arm: Any,
        lane: Any,
        drill_config: Any,
        logs: str = LOGS_DIR,
        artifacts: str = ARTIFACTS_DIR,
        **kwargs: Any,
    ):
        """
        Read the arm's settings on `lane` from the swe-drill.toml at `drill_config`,
        a host path relative to the directory `harbor run` started in.

        `logs` and `artifacts` are where the patch goes in the sandbox. Harbor
        never passes them. Raises TypeError when Harbor parsed `arm`, `lane` or
        `drill_config` as something other than a string, ConfigError when the
        settings cannot be read, and LaneError for a lane with no dataset in
        DATASETS, and as `grader` does.
        """
        for key, value in (("arm", arm), ("lane", lane), ("drill_config", drill_config)):
            if not isinstance(value, str):
                quoted = shlex.quote(f"{key}={json.dumps(json.dumps(value))}")
                raise TypeError(
                    f"--ak {key} must be a string, but Harbor parsed it as JSON into"
                    f" {value!r}, a {type(value).__name__}. Quote it as a JSON string:"
                    f" --ak {quoted}"
                )
        self.settings = config.load(Path(drill_config), arm, lane)
        if lane not in DATASETS:
            raise lanes.LaneError(
                f"SWE-drill cannot name the grader of a trial on {lane}, so it runs none there"
            )
        self.grader = grader(self.settings, lane)
        self.arm_kwargs = {key: kwargs[key] for key in self.KWARGS if key in kwargs}
        self.sandbox_logs = logs
        self.sandbox_artifacts = artifacts
        self._prepared: tuple[str, str, str] | None = None
        self._token: str | None = None
        self.installed_version: str | None = None
        super().__init__(**kwargs)

    @classmethod
    def parse_options(cls, kwargs=None, env=None):
        """
        Harbor's parse of the kwargs its agent's options name, with no fallback
        from the host's environment.

        Harbor refuses any other kwarg, in the job's preflight and in the
        constructor, so SWE-drill's own and the arm's own are left out. An option
        not given takes its default, since Harbor fills an absent one from
        Harbor's own environment.
        """
        fields = cls.options_model.model_fields if cls.options_model is not None else {}
        own = (*DRILL_KWARGS, *cls.KWARGS)
        kept = {key: value for key, value in (kwargs or {}).items() if key in fields or key not in own}
        for name, info in fields.items():
            if name not in kept and not info.is_required():
                kept[name] = info.get_default(call_default_factory=True)
        return super().parse_options(kept, env)

    def _env_sources(self):
        """Leave out Harbor's own environment, so no variable of the host's reaches the agent."""
        return (self._resolved_env_vars, self._extra_env)

    async def setup(self, environment: BaseEnvironment) -> None:
        """Record the workspace, the base and the starting tree, then let the agent install."""
        workspace = await find_workspace(environment)
        base = await read_base(environment, workspace)
        start_tree = await read_start_tree(environment, workspace)
        self._prepared = (workspace, base, start_tree)
        await super().setup(environment)
        self.installed_version = await self._installed(environment)

    async def _installed(self, environment: BaseEnvironment) -> str | None:
        """
        The version the installed agent reports, which may differ from the one
        asked for, or None where it reports none.
        """
        command = self.get_version_command()
        if not command:
            return None
        result = await environment.exec(command)
        if result.return_code != 0 or not result.stdout:
            return None
        return self.parse_version(result.stdout)

    async def _exec(self, environment, command, user=None, env=None, cwd=None, timeout_sec=None):
        """Mark every command run in the agent phase, so that a stop finds all it started."""
        if self._token is not None:
            env = {**(env or {}), sandbox.MARKER: self._token}
        return await super()._exec(
            environment, command, user=user, env=env, cwd=cwd, timeout_sec=timeout_sec
        )

    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        """
        Run the agent, stop everything it started, capture the patch, and
        write suss.json.

        The trial is `timed-out` when Harbor cancels the run at its agent
        timeout, `harness-failed` when the agent raises, and `ok` otherwise. The agent's exception, and Harbor's
        cancel, are raised once the record is written, the cancel within
        CLEANUP_SECONDS so Harbor still sees its timeout.
        """
        if self._prepared is None:
            raise RuntimeError(f"{type(self).__name__}.run was called before setup")
        self._token = sandbox.new_token()
        status = "ok"
        raised: BaseException | None = None
        started = time.monotonic()
        try:
            await self._before_agent(environment)
            await super().run(instruction, environment, context)
        except asyncio.CancelledError as cancelled:
            status, raised = "timed-out", cancelled
        except BaseException as error:
            status, raised = "harness-failed", error
        run_seconds = time.monotonic() - started
        token, self._token = self._token, None
        finishing = asyncio.ensure_future(self._finish(environment, token, status, run_seconds, raised))
        # A cancel that arrives during the stop or the capture waits for them,
        # for CLEANUP_SECONDS at most, since Harbor waits on a cancelled agent
        # with no limit. It is raised once they are done.
        late: asyncio.CancelledError | None = None
        deadline: float | None = None
        loop = asyncio.get_running_loop()
        while not finishing.done():
            if deadline is not None and loop.time() >= deadline:
                finishing.cancel()
                # Retrieved once the cancel settles it, so a failure it
                # raises meanwhile is never reported as unretrieved.
                finishing.add_done_callback(lambda task: task.cancelled() or task.exception())
                raise late
            try:
                if deadline is None:
                    await asyncio.shield(finishing)
                else:
                    await asyncio.wait({finishing}, timeout=deadline - loop.time())
            except asyncio.CancelledError as cancel:
                late = late or cancel
                deadline = deadline or loop.time() + CLEANUP_SECONDS
        if finishing.cancelled() or (finishing.exception() is None and late is not None):
            raise late or asyncio.CancelledError()
        finishing.result()

    async def _finish(
        self,
        environment: BaseEnvironment,
        token: str,
        status: str,
        run_seconds: float,
        raised: BaseException | None,
    ) -> None:
        """
        Stop everything the agent started, capture the patch and write
        suss.json, then raise what the run raised.
        """
        await sandbox.stop_marked(environment, token)
        try:
            # Harbor waits on a cancelled agent with no limit.
            cleanup = CLEANUP_SECONDS if isinstance(raised, asyncio.CancelledError) else None
            async with asyncio.timeout(cleanup):
                await self._record(environment, status, run_seconds)
        except BaseException as error:
            # Harbor grades a trial whose agent raised its own error, which an
            # empty or missing patch would then pass as the unchanged repository.
            if raised is not None and not isinstance(raised, asyncio.CancelledError):
                raise error from raised
            raise
        if raised is not None:
            raise raised

    async def _record(self, environment: BaseEnvironment, status: str, run_seconds: float) -> None:
        """Capture the patch, copy it to the artifacts directory, then write suss.json beside it."""
        workspace, base, start_tree = self._prepared
        patch_bytes = await capture_patch(environment, workspace, start_tree, self.sandbox_logs)
        logs = f"{self.sandbox_logs.rstrip('/')}/{PATCH_NAME}"
        # Harbor collects only the artifacts directory for a verifier in a
        # sandbox of its own, so the copy is the patch that verifier grades.
        await sandbox.run(
            environment,
            f"mkdir -p {shlex.quote(self.sandbox_artifacts)}"
            f" && cp {shlex.quote(logs)} {shlex.quote(self.sandbox_artifacts)}",
        )
        record = {
            "arm": self.settings.arm.name,
            "agent": {
                "class": self.settings.arm.agent,
                "name": self.name(),
                "version": self.installed_version,
            },
            "model": self.model_name,
            "kwargs": self.arm_kwargs,
            "grader": self.grader,
            "base": base,
            "status": status,
            "run_seconds": run_seconds,
            "patch_bytes": patch_bytes,
            "usage": await self._usage(environment),
        }
        await sandbox.upload_bytes(
            environment,
            (json.dumps(record, indent=2) + "\n").encode(),
            f"{self.sandbox_logs.rstrip('/')}/{RECORD_NAME}",
        )

    async def _before_agent(self, environment: BaseEnvironment) -> None:
        """Prepare the workspace for the agent. A failure here is the agent's, as its run's would be."""

    async def _usage(self, environment: BaseEnvironment) -> dict[str, Any] | None:
        """What the agent reports it spent, for suss.json. None for an agent that reports nothing SWE-drill reads."""
        return None


def grader(settings: config.Config, lane: str) -> dict[str, str]:
    """
    What grades a trial of `lane`, a lane in DATASETS: its dataset.

    On senior it adds the litellm requirement the lane's test.sh installs, and
    the models and validation harness the regrade's verifier will use. Those
    follow from the variables `swe-drill run` gives the regrade's Harbor, which only
    the settings the lane's tasks read can reach. Raises LaneError when a
    task.toml in the download cannot be read.
    """
    found = {"dataset": DATASETS[lane]}
    if lane == lanes.SENIOR:
        download = settings.download(lane)
        reads = config.host_variables(lanes.read_tasks(lanes.lane_tasks(lane, download)))
        found["litellm"] = lanes.senior.PINNED_LITELLM.decode()
        found |= config.grading_choices(config.verifier_env(settings, reads))
    return found


def agent_class(import_path: str, path: Path, arm: str) -> type[Adapter]:
    """
    The class an arm's `agent` names, which must apply Adapter.

    Raises ConfigError, naming the file and the arm, for one that cannot be
    imported, is not a class, or leaves no patch for the verifier to grade.
    """
    try:
        found = import_symbol(import_path)
    except ValueError as error:
        raise ConfigError(f"{path}: [arms.{arm}] agent = {import_path!r}: {error}") from None
    concrete = isinstance(found, type) and issubclass(found, BaseAgent) and not inspect.isabstract(found)
    if not concrete or not issubclass(found, Adapter):
        raise ConfigError(
            f"{path}: [arms.{arm}] agent = {import_path!r} is not an agent built on "
            "swe_drill.agents.Adapter, and only such an agent leaves the patch its trial is "
            "graded by; use swe_drill.agents.copilot_cli:CopilotCli, swe_drill.agents.hve:CopilotCliHve or"
            " swe_drill.agents.claude_code:ClaudeCode"
        )
    return found


def check_arm(cls: type[Adapter], settings: config.Config) -> None:
    """
    Refuse an arm whose kwargs or model its agent class cannot take.

    Harbor refuses a kwarg its agent's options do not name only once `harbor
    run` has started, and an agent with no options takes any. Raises
    ConfigError naming the file, the arm, and what the class takes.
    """
    arm = settings.arm
    where = f"{settings.path}: [arms.{arm.name}]"
    for key in arm.kwargs:
        if key not in cls.KWARGS:
            takes = ", ".join(cls.KWARGS) or "none"
            raise ConfigError(
                f"{where} sets kwargs {key}, which {arm.agent} does not take; it takes {takes}"
            )
    for key in cls.REQUIRED_KWARGS:
        if key not in arm.kwargs:
            raise ConfigError(f"{where} sets no kwargs {key}, which {arm.agent} needs")
    if cls.REQUIRES_MODEL and not arm.model:
        raise ConfigError(f"{where} sets no model, which {arm.agent} needs")
