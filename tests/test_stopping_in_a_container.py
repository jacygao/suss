"""
Stop an agent's run in a real container, and look for what it left running.

An agent's tools start commands in sessions and process groups of their own,
which a kill of the command's process group misses. Only a listing of the
container's processes after the stop shows whether they are gone, and only a
file that stops growing shows nothing writes to the workspace after the patch.
"""

import asyncio
import subprocess
import time
from pathlib import Path

import pytest
from harbor.models.agent.context import AgentContext

from test_agents import SussScripted

# node:24 has git, bash and ps, as the task images do.
IMAGE = "node:24"
# Each `sleep` has a length of its own, so a listing shows which one survived.
# 301 is in a session of its own, 302 is double-forked into one and reparented
# to PID 1, and 303 stays in the command's process group. The writer appends
# to the workspace until it is killed.
ESCAPES = (
    "setsid sleep 301 > /dev/null 2>&1 &\n"
    "( setsid sleep 302 > /dev/null 2>&1 & )\n"
    "sleep 303 &\n"
    "( setsid sh -c 'while :; do echo x >> late.txt; sleep 0.1; done' > /dev/null 2>&1 & )\n"
    'echo "a known change" >> hello.txt\n'
    "sleep 300"
)
AGENT_SLEEPS = ("sleep 300", "sleep 301", "sleep 302", "sleep 303")


@pytest.fixture
def container_image() -> str:
    return IMAGE


def go(coroutine):
    return asyncio.run(coroutine)


def in_container(env, command: str, user: str | None = None) -> None:
    argv = ["docker", "exec", *(["-u", user] if user else []), env.container, "bash", "-c", command]
    subprocess.run(argv, check=True, capture_output=True)


def trial(env, user: str | None, arm: str):
    """suss.toml on the host, and a workspace the agent's user owns in the container."""
    root = Path(env.environment_dir)
    host = root / "host"
    download = host / "tasks" / "swe-rebench-07-2026" / "owner__repo-1"
    download.mkdir(parents=True)
    (download / "task.toml").write_text("")
    (host / "suss.toml").write_text(
        'swe_rebench = "tasks/swe-rebench-07-2026"\nenvironment = "docker"\nk = 1\nconcurrency = 1\n\n'
        '[arms.quick]\nagent = "test_agents:SussScripted"\nmax_seconds = 3\n\n'
        '[arms.unlimited]\nagent = "test_agents:SussScripted"\n'
    )
    workspace = root / "workspace"
    logs = root / "logs"
    in_container(
        env,
        f"mkdir -p {workspace} {logs} && cd {workspace} && git init -q && echo hello > hello.txt"
        " && git add -A && git -c user.name=t -c user.email=t@t commit -q -m base",
        user,
    )
    env.task_env_config.workdir = str(workspace)
    env.default_user = user
    agent = SussScripted(
        arm=arm, lane="rebench", suss_config=str(host / "suss.toml"),
        logs=str(logs / "agent"), artifacts=str(logs / "artifacts"), logs_dir=root / "harbor-logs",
    )
    SussScripted.script = ESCAPES
    return agent, workspace


def running(env) -> list[str]:
    """Every process in the container that has not exited, as `ps` lists it."""
    listed = subprocess.run(
        ["docker", "exec", env.container, "ps", "-eo", "pid=,stat=,args="],
        capture_output=True, text=True, check=True,
    ).stdout
    # A killed process stays a zombie, since PID 1 here never reaps.
    return [line for line in listed.splitlines() if line.split()[1][0] != "Z"]


def left_running(env, commands) -> list[str]:
    return [line for line in running(env) if any(line.endswith(c) for c in commands)]


def bystander(env, user: str | None) -> None:
    """Start a process the agent did not start, as the same user, outside any exec of suss's."""
    argv = ["docker", "exec", "-d", *(["-u", user] if user else []), env.container, "setsid", "sleep", "399"]
    subprocess.run(argv, check=True)


def assert_nothing_left(env, workspace: Path) -> None:
    # Listed at once: the stop must be over before the patch is captured.
    left = left_running(env, AGENT_SLEEPS)
    assert not left, "processes the agent started outlived the stop:\n" + "\n".join(left)
    late = workspace / "late.txt"
    size = late.stat().st_size
    time.sleep(1)
    assert late.stat().st_size == size, "something went on writing to the workspace after the patch"
    assert left_running(env, ["sleep 399"]), "the stop killed a process suss did not start"
    assert left_running(env, ["sleep infinity"]), "the stop killed the container's PID 1"


@pytest.mark.parametrize("user", [None, "node"])
def test_a_run_stopped_at_max_seconds_leaves_no_process_it_started(container_environment, user):
    env = container_environment
    agent, workspace = trial(env, user, "quick")
    bystander(env, user)

    go(agent.setup(env))
    go(agent.run("an instruction", env, AgentContext()))

    assert_nothing_left(env, workspace)
    patch = (Path(env.environment_dir) / "logs" / "agent" / "model.patch").read_text()
    assert "a known change" in patch


@pytest.mark.parametrize("user", [None, "node"])
def test_a_cancelled_run_leaves_no_process_it_started(container_environment, user):
    env = container_environment
    agent, workspace = trial(env, user, "unlimited")
    bystander(env, user)

    async def scenario():
        await agent.setup(env)
        going = asyncio.create_task(agent.run("an instruction", env, AgentContext()))
        deadline = time.monotonic() + 15
        while not (workspace / "late.txt").exists():
            assert time.monotonic() < deadline, "the agent never started its writer"
            await asyncio.sleep(0.05)
        going.cancel()
        with pytest.raises(asyncio.CancelledError):
            await going

    go(scenario())

    assert_nothing_left(env, workspace)
