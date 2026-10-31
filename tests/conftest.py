import asyncio
import os
import shutil
import signal
from pathlib import Path

import pytest
from harbor.environments.base import BaseEnvironment, ExecResult
from harbor.models.task.config import EnvironmentConfig
from harbor.models.trial.paths import TrialPaths


class LocalEnvironment(BaseEnvironment):
    """
    Harbor's environment, run on this machine, so suss's parts can be tested
    without a sandbox.

    It returns what Harbor's Docker environment returns, and fails where Docker
    fails. A stand-in that is kinder than Docker passes tests that production
    then fails. Paths are used as given: tests pass temporary directories in
    place of the sandbox's.
    """

    @staticmethod
    def type() -> str:
        return "local"

    def _validate_definition(self):
        pass

    async def start(self, force_build: bool) -> None:
        pass

    async def stop(self, delete: bool):
        pass

    async def exec(
        self,
        command: str,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout_sec: int | None = None,
        user: str | int | None = None,
    ) -> ExecResult:
        """
        Run the command with bash, as Docker does.

        Docker merges stderr into stdout, so stderr is always None, and empty
        output is None rather than "". At the timeout, the command and every
        process it started are killed, and RuntimeError is raised.

        Raises NotImplementedError for a user, since this machine cannot run
        the command as anyone else.
        """
        user = self._resolve_user(user)
        if user is not None:
            raise NotImplementedError(f"cannot run a command as user {user!r} locally")

        process = await asyncio.create_subprocess_exec(
            "bash",
            "-c",
            command,
            cwd=cwd or self.task_env_config.workdir,
            env={**os.environ, **(self._merge_env(env) or {})},
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            # Its own process group, so a timeout can kill what it started too.
            start_new_session=True,
        )
        # The reader is never cancelled. Output written while the command dies
        # would otherwise fill the pipe and block it from dying.
        output = asyncio.ensure_future(process.communicate())
        # Docker treats a timeout of 0 as none.
        done, _ = await asyncio.wait({output}, timeout=timeout_sec or None)
        if not done:
            await _kill_group(process)
            # Bounded, since a process that left the group can hold the pipe open.
            done, _ = await asyncio.wait({output}, timeout=5)
            if not done:
                output.cancel()
            raise RuntimeError(f"Command timed out after {timeout_sec} seconds")
        stdout, _ = output.result()

        return ExecResult(
            stdout=stdout.decode(errors="replace") if stdout else None,
            stderr=None,
            return_code=process.returncode or 0,
        )

    async def upload_file(self, source_path: Path | str, target_path: str):
        Path(target_path).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, target_path)

    async def upload_dir(self, source_dir: Path | str, target_dir: str):
        """Copy the contents of source_dir into target_dir, which is created if missing."""
        shutil.copytree(source_dir, target_dir, symlinks=True, dirs_exist_ok=True)

    async def download_file(self, source_path: str, target_path: Path | str):
        raise NotImplementedError

    async def download_dir(self, source_dir: str, target_dir: Path | str):
        raise NotImplementedError


async def _kill_group(process: asyncio.subprocess.Process) -> None:
    """Terminate the process group, then kill whatever is left of it after 5s, as Docker does."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            await asyncio.wait_for(process.wait(), 5)
        except asyncio.TimeoutError:
            pass
        # Children that ignored SIGTERM, or outlived the leader.
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


@pytest.fixture
def local_environment(tmp_path_factory):
    root = tmp_path_factory.mktemp("local-environment")
    return LocalEnvironment(
        environment_dir=root,
        environment_name="local",
        session_id="local",
        trial_paths=TrialPaths(root / "trial"),
        task_env_config=EnvironmentConfig(),
    )
