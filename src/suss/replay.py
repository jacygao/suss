"""
ReplayAgent, the Harbor custom agent that regrades a trial of an agent job.

It applies the trial's model.patch in a fresh sandbox, so that the task's
verifier grades the change and nothing else the harness did to its sandbox.
Harbor constructs it on the host, where the agent job is.
"""

import json
import shlex
from importlib.metadata import version
from pathlib import Path
from typing import Any

from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

from suss.workspace import PATCH_NAME, find_workspace

RECORD_NAME = "replay.json"
APPLY_METHODS = ("git apply --verbose", "git apply --3way", "patch --fuzz=3 -p1 -i")
APPLY_SECONDS = 300


class ReplayError(Exception):
    """A source job in which the regrade cannot find the one trial of its task."""


class ReplayAgent(BaseAgent):
    def __init__(
        self,
        *,
        source_job: Any,
        patch_target: str = "/tmp/suss-replay.patch",
        **kwargs: Any,
    ):
        """
        Regrade from the agent job at `source_job`, a host path relative to the
        directory `harbor run` started in.

        `patch_target` is where the patch goes in the sandbox. Harbor never
        passes it. Raises TypeError when Harbor parsed `source_job` as something
        other than a string, and ReplayError when it is not a directory.
        """
        if not isinstance(source_job, str):
            raise TypeError(
                "--ak source_job must be a string, but Harbor parsed it as JSON into"
                f" {source_job!r}, a {type(source_job).__name__}"
            )
        super().__init__(**kwargs)
        self.source_job = Path(source_job).absolute()
        if not self.source_job.is_dir():
            raise ReplayError(f"source_job {self.source_job} is not a directory")
        self.patch_target = patch_target

    @staticmethod
    def name() -> str:
        return "suss-replay"

    def version(self) -> str | None:
        return version("suss")

    async def setup(self, environment: BaseEnvironment) -> None:
        pass

    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        """
        Apply the source trial's patch in the workspace, and write replay.json.

        A patch that fails to apply is recorded, not raised, so the verifier
        still grades the workspace. Raises ReplayError when the source job holds
        no trial of this task, or more than one.
        """
        # Harbor writes the trial's config.json before it starts the agent.
        task = _task(self.logs_dir.parent / "config.json")
        patch = self._source_trial(task) / "agent" / PATCH_NAME
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        record: dict[str, Any] = {"task": task, "patch": None}
        if patch.is_file() and patch.stat().st_size > 0:
            record = {"task": task, "patch": str(patch), **await self._apply(environment, patch)}
        (self.logs_dir / RECORD_NAME).write_text(json.dumps(record, indent=2) + "\n")

    def _source_trial(self, task: str) -> Path:
        """The one trial directory of the source job that ran `task`."""
        trials = sorted(
            config.parent
            for config in self.source_job.glob("*/config.json")
            if _task(config) == task
        )
        if len(trials) != 1:
            found = ", ".join(str(trial) for trial in trials) or f"no trial of {task}"
            raise ReplayError(
                f"source_job {self.source_job} must hold one trial of {task}, and holds {found}"
            )
        return trials[0]

    async def _apply(self, environment: BaseEnvironment, patch: Path) -> dict[str, Any]:
        """
        Apply the patch with the first method that succeeds, and return its exit
        code and everything the methods tried printed.

        Each method's output, stderr too, follows a line naming it, so apply_out
        shows which ran and what each printed. The patch goes in as a file,
        since exec passes text, which corrupts bytes that are not valid UTF-8.
        """
        await environment.upload_file(patch, self.patch_target)
        target = shlex.quote(self.patch_target)
        command = " || ".join(
            f"{{ echo {shlex.quote('$ ' + method)}; {method} {target} 2>&1; }}"
            for method in APPLY_METHODS
        )
        workspace = await find_workspace(environment)
        result = await environment.exec(command, cwd=workspace, timeout_sec=APPLY_SECONDS)
        return {
            "apply_rc": result.return_code,
            "apply_out": (result.stdout or "") + (result.stderr or ""),
        }


def _task(config: Path) -> str:
    """
    The task a trial ran: the name of the task directory its config.json names.

    Raises ReplayError when the file names none.
    """
    try:
        path = json.loads(config.read_text())["task"]["path"]
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise ReplayError(f"cannot read the task from {config}: {error!r}") from error
    if not isinstance(path, str):
        raise ReplayError(f"cannot read the task from {config}: task.path is {path!r}")
    return Path(path).name
