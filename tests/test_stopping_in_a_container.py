"""
Stop a harness's `run` in a real container, and look for what it left running.

A harness's tools start commands in sessions and process groups of their own,
which a kill of `run`'s process group misses. Only a listing of the container's
processes after the stop shows whether they are gone.
"""

import asyncio
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from suss.config import Arm
from suss.harness import SandboxPaths, load_manifest, run

FAKE_ADAPTER = Path(__file__).resolve().parent / "fake_adapter"

# Each `sleep` has a length of its own, so a listing shows which one survived.
# 301 is in a session of its own, 302 is double-forked into one and reparented
# to PID 1, and 303 stays in `run`'s process group.
ESCAPES = (
    "setsid sleep 301 > /dev/null 2>&1 &\n"
    "( setsid sleep 302 > /dev/null 2>&1 & )\n"
    "sleep 303 &\n"
    'echo started > "$here/run.started"\n'
    "sleep 300"
)
HARNESS_SLEEPS = ("sleep 300", "sleep 301", "sleep 302", "sleep 303")


def go(coroutine):
    return asyncio.run(coroutine)


def arm(max_seconds=None) -> Arm:
    return Arm(
        name="fake",
        harness=FAKE_ADAPTER,
        baseline=None,
        params={},
        max_cost_usd=None,
        sets_max_cost_usd=False,
        max_seconds=max_seconds,
    )


def sandbox(env, name: str, script: str) -> tuple[Path, Path, SandboxPaths]:
    """An adapter whose `run` runs `script`, a workspace, and the paths, under the mount."""
    root = Path(env.environment_dir) / name
    adapter = root / "adapter"
    shutil.copytree(FAKE_ADAPTER, adapter)
    (adapter / "run.then").write_text(script + "\n")
    workspace = root / "workspace"
    workspace.mkdir()
    for path in (root, adapter, workspace):
        path.chmod(0o777)
    paths = SandboxPaths(
        adapter=str(adapter),
        instruction=str(root / "instruction.md"),
        params=str(root / "params.json"),
        logs=str(root / "logs"),
    )
    return adapter, workspace, paths


def start_run(env, adapter: Path, workspace: Path, paths: SandboxPaths, the_arm: Arm):
    return run(env, load_manifest(adapter), the_arm, str(workspace), {}, 0, paths)


def running(env) -> list[str]:
    """Every process in the container that has not exited, as `ps` lists it."""
    listed = subprocess.run(
        ["docker", "exec", env.container, "ps", "-eo", "pid=,ppid=,pgid=,sid=,stat=,args="],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    # A killed process stays a zombie, since PID 1 here never reaps.
    return [line for line in listed.splitlines() if line.split()[4][0] != "Z"]


def left_running(env, commands) -> list[str]:
    return [line for line in running(env) if any(line.endswith(c) for c in commands)]


def bystander(env, user: str | None) -> None:
    """Start a process that suss did not start, as the same user, outside any exec of suss's."""
    argv = ["docker", "exec", "-d"]
    if user is not None:
        argv += ["-u", user]
    subprocess.run([*argv, env.container, "setsid", "sleep", "399"], check=True)


async def wait_for(path: Path) -> None:
    deadline = time.monotonic() + 15
    while not path.exists():
        assert time.monotonic() < deadline, f"{path} never appeared"
        await asyncio.sleep(0.05)


@pytest.mark.parametrize("user", [None, "ubuntu"])
def test_a_run_stopped_at_max_seconds_leaves_no_process_it_started(container_environment, user):
    env = container_environment
    env.default_user = user
    adapter, workspace, paths = sandbox(env, "trial", ESCAPES)
    bystander(env, user)

    result = go(start_run(env, adapter, workspace, paths, arm(max_seconds=3)))

    assert result.status == "timed-out"
    # Listed at once: the stop must be over before `run` returns and suss captures the patch.
    left = left_running(env, HARNESS_SLEEPS)
    assert not left, "processes the harness started outlived the stop:\n" + "\n".join(left)
    assert left_running(env, ["sleep 399"]), "the stop killed a process suss did not start"
    assert left_running(env, ["sleep infinity"]), "the stop killed the container's PID 1"


@pytest.mark.parametrize("user", [None, "ubuntu"])
def test_a_cancelled_run_leaves_no_process_it_started(container_environment, user):
    env = container_environment
    env.default_user = user
    adapter, workspace, paths = sandbox(env, "trial", ESCAPES)
    bystander(env, user)

    async def scenario():
        going = asyncio.create_task(start_run(env, adapter, workspace, paths, arm()))
        await wait_for(adapter / "run.started")
        going.cancel()
        result = await going
        assert result.status == "timed-out"

    go(scenario())

    left = left_running(env, HARNESS_SLEEPS)
    assert not left, "processes the harness started outlived the cancel:\n" + "\n".join(left)
    assert left_running(env, ["sleep 399"]), "the stop killed a process suss did not start"
    assert left_running(env, ["sleep infinity"]), "the stop killed the container's PID 1"


def test_stopping_one_run_leaves_another_runs_processes(container_environment):
    env = container_environment
    stopped = sandbox(env, "stopped", ESCAPES)
    other = sandbox(
        env,
        "other",
        'setsid sleep 305 > /dev/null 2>&1 &\necho started > "$here/run.started"\nsleep 306',
    )

    async def scenario():
        going = asyncio.create_task(start_run(env, *other, arm()))
        await wait_for(other[0] / "run.started")
        await start_run(env, *stopped, arm(max_seconds=3))
        alive = left_running(env, ["sleep 305", "sleep 306"])
        going.cancel()
        await going
        return alive

    alive = go(scenario())

    assert len(alive) == 2, "stopping one run killed another run's processes"
