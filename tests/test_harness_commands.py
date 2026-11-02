"""
Run a harness's `install` and `run` through the stand-in environment, against
the fake adapter in `tests/fake_adapter/`.

Each fake script records its arguments, its current directory and its
environment beside itself, then sources `<command>.then` if the test wrote one.
Every path the commands see holds a space.
"""

import asyncio
import dataclasses
import os
import shutil
import time
from pathlib import Path

import pytest

from suss.config import Arm
from suss.harness import HarnessResult, SandboxPaths, install, load_manifest, run

FAKE_ADAPTER = Path(__file__).resolve().parent / "fake_adapter"


def go(coroutine):
    return asyncio.run(coroutine)


def arm(max_seconds=None) -> Arm:
    return Arm(
        name="fake",
        harness=FAKE_ADAPTER,
        baseline=None,
        params={},
        max_seconds=max_seconds,
    )


@dataclasses.dataclass
class Box:
    """A sandbox's directories, on this machine."""

    adapter: Path
    workspace: Path
    paths: SandboxPaths

    @property
    def logs(self) -> Path:
        return Path(self.paths.logs)

    def then(self, command: str, script: str) -> None:
        """Have the fake `command` run `script` once it has recorded how it was started."""
        (self.adapter / f"{command}.then").write_text(script + "\n")

    def recorded(self, command: str, what: str) -> list[str]:
        """What the fake `command` recorded. Fails if it never ran."""
        path = self.adapter / f"{command}.{what}"
        assert path.exists(), f"the fake {command} was never started"
        return path.read_text().splitlines()

    def started(self, command: str) -> bool:
        return (self.adapter / f"{command}.args").exists()


@pytest.fixture
def box(tmp_path) -> Box:
    root = tmp_path / "sand box"
    opt = root / "opt suss"
    adapter = opt / "the adapter"
    shutil.copytree(FAKE_ADAPTER, adapter)
    workspace = root / "the workspace"
    workspace.mkdir()
    return Box(
        adapter=adapter,
        workspace=workspace,
        paths=SandboxPaths(
            adapter=str(adapter),
            instruction=str(opt / "instruction file.md"),
            params=str(opt / "params file.json"),
            logs=str(root / "agent logs"),
        ),
    )


# A harness's own process, and a child it backgrounds, as a coding agent's tools are.
RECORD_PIDS = 'sleep 30 &\necho $! $$ > "$here/run.pids"'


def assert_all_dead(box: Box) -> None:
    """Fail unless the processes RECORD_PIDS named are gone, allowing 2s for them to be reaped."""
    pids = [int(pid) for pid in box.recorded("run", "pids")[0].split()]
    deadline = time.monotonic() + 2
    while alive := [pid for pid in pids if _is_running(pid)]:
        assert time.monotonic() < deadline, f"processes {alive} outlived the run that started them"
        time.sleep(0.05)


def _is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def install_in(env, box: Box, dotenv=None):
    manifest = load_manifest(box.adapter)
    return go(install(env, manifest, str(box.workspace), dotenv or {}, box.paths))


def run_in(env, box: Box, the_arm=None, dotenv=None, install_exit_code=0) -> HarnessResult:
    manifest = load_manifest(box.adapter)
    return go(
        run(
            env,
            manifest,
            the_arm or arm(),
            str(box.workspace),
            dotenv or {},
            install_exit_code,
            box.paths,
        )
    )


def test_install_gets_the_contracts_arguments_in_the_workspace(local_environment, box):
    exit_code = install_in(local_environment, box)

    assert exit_code == 0
    assert box.recorded("install", "args") == [
        "--workspace", str(box.workspace),
        "--params", box.paths.params,
    ]
    assert box.recorded("install", "cwd") == [str(box.workspace)]


def test_run_gets_the_contracts_arguments_in_the_workspace(local_environment, box):
    run_in(local_environment, box)

    assert box.recorded("run", "args") == [
        "--workspace", str(box.workspace),
        "--instruction", box.paths.instruction,
        "--params", box.paths.params,
    ]
    assert box.recorded("run", "cwd") == [str(box.workspace)]


@pytest.mark.parametrize("command", ["install", "run"])
def test_the_environment_holds_only_the_variables_the_manifest_names(
    local_environment, box, command
):
    # The sentinel goes in the .env, since the stand-in passes this machine's
    # own environment through where a sandbox would pass its image's.
    dotenv = {"SUSS_FAKE_KEY": "named value", "SUSS_FAKE_UNNAMED": "leaked"}
    if command == "install":
        install_in(local_environment, box, dotenv)
    else:
        run_in(local_environment, box, dotenv=dotenv)

    variables = box.recorded(command, "env")
    assert "SUSS_FAKE_KEY=named value" in variables
    assert not [line for line in variables if line.startswith("SUSS_FAKE_UNNAMED=")], (
        "a .env variable the manifest does not name reached the command"
    )
    assert not [line for line in variables if line.startswith("SUSS_FAKE_MISSING=")], (
        "a named variable missing from the .env should be absent, not empty"
    )


def test_a_failed_install_is_harness_failed_and_run_never_starts(local_environment, box):
    box.then("install", "exit 3")

    exit_code = install_in(local_environment, box)
    result = run_in(local_environment, box, install_exit_code=exit_code)

    assert exit_code == 3
    assert result == HarnessResult(
        status="harness-failed", exit_codes={"install": 3, "run": None}, run_seconds=None
    )
    assert not box.started("run"), "run was started after install failed"


def test_a_run_that_exits_zero_is_ok(local_environment, box):
    result = run_in(local_environment, box)

    assert result.status == "ok"
    assert result.exit_codes == {"install": 0, "run": 0}


def test_a_run_that_exits_non_zero_is_harness_failed(local_environment, box):
    box.then("run", "exit 5")

    result = run_in(local_environment, box)

    assert result.status == "harness-failed"
    assert result.exit_codes == {"install": 0, "run": 5}


def test_a_manifest_without_install_runs_none(local_environment, box):
    manifest = dataclasses.replace(load_manifest(box.adapter), install=None)

    exit_code = go(install(local_environment, manifest, str(box.workspace), {}, box.paths))
    result = go(
        run(local_environment, manifest, arm(), str(box.workspace), {}, exit_code, box.paths)
    )

    assert exit_code is None
    assert not box.started("install")
    assert not (box.logs / "install.log").exists()
    assert result.status == "ok"
    assert result.exit_codes == {"install": None, "run": 0}


def test_a_run_past_max_seconds_is_stopped_and_timed_out(local_environment, box):
    box.then("run", f"echo before the limit\n{RECORD_PIDS}\nsleep 30")

    started = time.monotonic()
    result = run_in(local_environment, box, arm(max_seconds=1))
    elapsed = time.monotonic() - started

    assert result.status == "timed-out"
    assert result.exit_codes["run"] is None, "a stopped run has no exit code"
    assert 1 <= result.run_seconds < 10
    assert elapsed < 10, "run was not stopped at max_seconds"
    assert "before the limit" in (box.logs / "run.log").read_text(), (
        "what run printed before it was stopped is missing from run.log"
    )
    assert_all_dead(box)


def test_a_run_stopped_as_execs_own_timeout_fires_is_still_timed_out(
    local_environment, box, monkeypatch
):
    # exec's timeout_sec set to max_seconds itself, so the two limits race.
    box.then("run", f"{RECORD_PIDS}\nsleep 30")
    exec_as_given = local_environment.exec

    async def racing(command, **kwargs):
        if kwargs.get("timeout_sec") is not None:
            kwargs["timeout_sec"] = 1
        return await exec_as_given(command, **kwargs)

    monkeypatch.setattr(local_environment, "exec", racing)
    result = run_in(local_environment, box, arm(max_seconds=1))

    assert result.status == "timed-out"
    assert result.exit_codes["run"] is None
    assert_all_dead(box)


def test_a_run_that_only_execs_own_timeout_stops_is_still_stopped(
    local_environment, box, monkeypatch
):
    # exec gives up long before max_seconds, as it does where the sandbox has no `sleep`.
    box.then("run", f"{RECORD_PIDS}\nsleep 30")
    exec_as_given = local_environment.exec

    async def early(command, **kwargs):
        if kwargs.get("timeout_sec") is not None:
            kwargs["timeout_sec"] = 1
        return await exec_as_given(command, **kwargs)

    monkeypatch.setattr(local_environment, "exec", early)
    result = run_in(local_environment, box, arm(max_seconds=60))

    assert result.status == "timed-out"
    assert_all_dead(box)


def test_a_run_under_max_seconds_keeps_its_exit_code(local_environment, box):
    box.then("run", "echo finished\nexit 5")

    result = run_in(local_environment, box, arm(max_seconds=30))

    assert result.status == "harness-failed"
    assert result.exit_codes["run"] == 5
    assert (box.logs / "run.log").read_text() == "finished\n"


def test_each_command_writes_stdout_and_stderr_to_its_log(local_environment, box):
    box.then("install", "echo install out\necho install err >&2")
    box.then("run", "echo run out\necho run err >&2")

    install_in(local_environment, box)
    run_in(local_environment, box)

    assert (box.logs / "install.log").read_text() == "install out\ninstall err\n"
    assert (box.logs / "run.log").read_text() == "run out\nrun err\n"


def test_run_seconds_measures_run_alone(local_environment, box):
    box.then("install", "sleep 1.5")
    box.then("run", "sleep 0.5")

    install_in(local_environment, box)
    result = run_in(local_environment, box)

    assert result.run_seconds is not None
    assert 0.5 <= result.run_seconds < 1.5, "run_seconds includes more than run"


def test_run_seconds_leaves_out_suss_own_commands(local_environment, box, monkeypatch):
    exec_as_given = local_environment.exec

    async def slow_unless_harness(command, **kwargs):
        if box.paths.adapter not in command:
            await asyncio.sleep(1)
        return await exec_as_given(command, **kwargs)

    monkeypatch.setattr(local_environment, "exec", slow_unless_harness)
    result = run_in(local_environment, box)

    assert result.run_seconds < 1, "run_seconds includes a command suss ran itself"


@pytest.mark.parametrize("command", ["install", "run"])
def test_commands_leave_the_user_to_the_environments_default(
    local_environment, box, monkeypatch, command
):
    # Harbor sets the task's agent user as the default before each phase, and
    # exec falls back to it only where no user is given.
    exec_as_given = local_environment.exec
    users = []

    async def recording(command, **kwargs):
        if box.paths.adapter in command:
            users.append(kwargs.get("user"))
        return await exec_as_given(command, **kwargs)

    monkeypatch.setattr(local_environment, "exec", recording)
    if command == "install":
        install_in(local_environment, box)
    else:
        run_in(local_environment, box)

    assert box.started(command)
    assert users == [None], "the harness's command overrode the default user"
