import asyncio
import os
import shutil
import signal
import subprocess
import threading
from concurrent.futures import Future
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
        """Kill every command still running, as deleting a container does."""
        for group in getattr(self, "_groups", []):
            try:
                os.killpg(group, signal.SIGKILL)
            except ProcessLookupError:
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
        output is None rather than "". At the timeout, RuntimeError is raised
        and the command is left running, since Docker stops only its own client
        there. `stop` kills it.

        Raises NotImplementedError for a user, since this machine cannot run
        the command as anyone else.
        """
        user = self._resolve_user(user)
        if user is not None:
            raise NotImplementedError(f"cannot run a command as user {user!r} locally")

        process = subprocess.Popen(
            ["bash", "-c", command],
            cwd=cwd or self.task_env_config.workdir,
            env={**os.environ, **(self._merge_env(env) or {})},
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            # Its own process group, so that stop can kill what it started too.
            start_new_session=True,
        )
        self._groups = [*getattr(self, "_groups", []), process.pid]
        # Read on a thread rather than the event loop, since a command left
        # running at a timeout outlives the loop. Docker goes on reading what
        # it writes, so it never blocks on a full pipe.
        output: Future[tuple[bytes, bytes]] = Future()
        threading.Thread(
            target=lambda: output.set_result(process.communicate()), daemon=True
        ).start()
        # asyncio.wait, not wait_for, which would cancel the future the thread still sets.
        # Docker treats a timeout of 0 as none.
        done, _ = await asyncio.wait({asyncio.wrap_future(output)}, timeout=timeout_sec or None)
        if not done:
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


@pytest.fixture
def local_environment(tmp_path_factory):
    root = tmp_path_factory.mktemp("local-environment")
    environment = LocalEnvironment(
        environment_dir=root,
        environment_name="local",
        session_id="local",
        trial_paths=TrialPaths(root / "trial"),
        task_env_config=EnvironmentConfig(),
    )
    yield environment
    asyncio.run(environment.stop(delete=True))
