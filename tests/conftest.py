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
    Harbor's environment, run on this machine, so SWE-drill's parts can be tested
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
        """
        Kill every process in the session of a command this ran, as deleting
        a container does.

        The session, not the process group, since a command's job control puts
        jobs in groups of their own, and those keep the session when they
        outlive the command.
        """
        sessions = set(getattr(self, "_sessions", []))
        listed = subprocess.run(["ps", "-A", "-o", "pid="], capture_output=True, text=True).stdout
        for pid in map(int, listed.split()):
            try:
                if os.getsid(pid) in sessions:
                    os.kill(pid, signal.SIGKILL)
            # Gone since it was listed.
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
        process = self._start(command, cwd, self._merge_env(env), self._resolve_user(user))
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

    def _start(
        self, command: str, cwd: str | None, env: dict[str, str] | None, user: str | int | None
    ) -> subprocess.Popen:
        if user is not None:
            raise NotImplementedError(f"cannot run a command as user {user!r} locally")
        process = subprocess.Popen(
            ["bash", "-c", command],
            cwd=cwd or self.task_env_config.workdir,
            env={**os.environ, **(env or {})},
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            # A session of its own, so that stop can kill what it started too.
            start_new_session=True,
        )
        self._sessions = [*getattr(self, "_sessions", []), process.pid]
        return process

    async def upload_file(self, source_path: Path | str, target_path: str):
        Path(target_path).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, target_path)

    async def upload_dir(self, source_dir: Path | str, target_dir: str):
        """Copy the contents of source_dir into target_dir, which is created if missing."""
        shutil.copytree(source_dir, target_dir, symlinks=True, dirs_exist_ok=True)

    async def download_file(self, source_path: str, target_path: Path | str):
        """Copy the file, failing as Docker does when it is missing."""
        Path(target_path).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, target_path)

    async def download_dir(self, source_dir: str, target_dir: Path | str):
        raise NotImplementedError


def start_directory(root: Path) -> EnvironmentConfig:
    """
    Start every command in an empty directory, as a sandbox starts in its
    image's WORKDIR.

    Left unset, a command would start where pytest did, in this checkout,
    which is a git repository a test could find in place of its own.
    """
    start = root / "start"
    start.mkdir()
    return EnvironmentConfig(workdir=str(start))


@pytest.fixture
def local_environment(tmp_path_factory):
    root = tmp_path_factory.mktemp("local-environment")
    environment = LocalEnvironment(
        environment_dir=root,
        environment_name="local",
        session_id="local",
        trial_paths=TrialPaths(root / "trial"),
        task_env_config=start_directory(root),
    )
    yield environment
    asyncio.run(environment.stop(delete=True))


# Small, with bash, ps and setsid, as the task images are.
CONTAINER_IMAGE = "ubuntu:24.04"


class ContainerEnvironment(LocalEnvironment):
    """
    The stand-in's commands, run in a real Docker container as Harbor's
    `docker exec` runs them, for what only a container can show: `/proc`, the
    user a command runs as, and what is left running after a stop.

    The container mounts a directory of this machine at the same path, so
    paths mean the same on both sides. Process ids do not: they are the
    container's, and only a command run in it can look them up.
    """

    container: str

    def _start(
        self, command: str, cwd: str | None, env: dict[str, str] | None, user: str | int | None
    ) -> subprocess.Popen:
        argv = ["docker", "exec"]
        cwd = cwd or self.task_env_config.workdir
        if cwd:
            argv += ["-w", cwd]
        for name, value in (env or {}).items():
            argv += ["-e", f"{name}={value}"]
        if user is not None:
            argv += ["-u", str(user)]
        return subprocess.Popen(
            [*argv, self.container, "bash", "-c", command],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )

    async def stop(self, delete: bool):
        subprocess.run(["docker", "rm", "-f", self.container], capture_output=True)


def require_docker() -> None:
    if shutil.which("docker") is None or subprocess.run(
        ["docker", "info"], capture_output=True
    ).returncode != 0:
        pytest.skip("Docker is not running")


@pytest.fixture
def container_image() -> str:
    """The image container_environment runs. A test module overrides it for an image of its own."""
    return CONTAINER_IMAGE


@pytest.fixture
def container_environment(tmp_path_factory, container_image):
    """A ContainerEnvironment whose mounted directory is `environment.environment_dir`."""
    require_docker()
    root = tmp_path_factory.mktemp("container-environment").resolve()
    # Open to every user, so a command run as a user other than root can write here.
    root.chmod(0o777)
    started = subprocess.run(
        ["docker", "run", "-d", "--rm", "-v", f"{root}:{root}", container_image,
         "sh", "-c", "sleep infinity"],
        capture_output=True,
        text=True,
    )
    assert started.returncode == 0, f"cannot start a container: {started.stderr}"
    environment = ContainerEnvironment(
        environment_dir=root,
        environment_name="container",
        session_id="container",
        trial_paths=TrialPaths(root / "trial"),
        task_env_config=EnvironmentConfig(),
    )
    environment.container = started.stdout.strip()
    yield environment
    asyncio.run(environment.stop(delete=True))


class ModalEnvironment(LocalEnvironment):
    """
    The stand-in, returning and failing where Harbor's modal environment does.

    Modal keeps stderr apart from stdout, and gives "" rather than None for
    empty output. At the timeout, exec returns rather than raises: Modal kills
    the `bash -c` it started, and nothing else, and reports a return code of
    -1. A process killed by a signal reports 128 plus the signal's number.
    """

    async def exec(
        self,
        command: str,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout_sec: int | None = None,
        user: str | int | None = None,
    ) -> ExecResult:
        if user is not None:
            raise NotImplementedError(f"cannot run a command as user {user!r} locally")
        process = subprocess.Popen(
            ["bash", "-c", command],
            cwd=cwd or self.task_env_config.workdir,
            env={**os.environ, **(self._merge_env(env) or {})},
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        self._sessions = [*getattr(self, "_sessions", []), process.pid]
        stdout: list[bytes] = []
        stderr: list[bytes] = []
        readers = [_read_into(process.stdout, stdout), _read_into(process.stderr, stderr)]

        def finished() -> None:
            for reader in readers:
                reader.join()
            process.wait()

        # A thread rather than the event loop, since a command left running at
        # a timeout outlives the loop.
        done: Future[None] = Future()
        threading.Thread(target=lambda: done.set_result(finished()), daemon=True).start()
        waited, _ = await asyncio.wait({asyncio.wrap_future(done)}, timeout=timeout_sec or None)
        if not waited:
            process.kill()
            process.wait()
            # What the command printed before the timeout, and no stderr at all.
            return ExecResult(
                stdout=b"".join(stdout).decode(errors="replace"), stderr="", return_code=-1
            )
        code = process.returncode
        return ExecResult(
            stdout=b"".join(stdout).decode(errors="replace"),
            stderr=b"".join(stderr).decode(errors="replace"),
            return_code=128 - code if code < 0 else code,
        )


def _read_into(pipe, chunks: list[bytes]) -> threading.Thread:
    """Start a thread that appends what `pipe` gives to `chunks` until it closes."""

    def read() -> None:
        while chunk := os.read(pipe.fileno(), 65536):
            chunks.append(chunk)

    thread = threading.Thread(target=read, daemon=True)
    thread.start()
    return thread


@pytest.fixture
def modal_environment(tmp_path_factory):
    root = tmp_path_factory.mktemp("modal-environment")
    environment = ModalEnvironment(
        environment_dir=root,
        environment_name="modal",
        session_id="modal",
        trial_paths=TrialPaths(root / "trial"),
        task_env_config=start_directory(root),
    )
    yield environment
    asyncio.run(environment.stop(delete=True))
