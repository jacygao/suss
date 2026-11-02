"""
Run exec's timeout, and suss's stop, in a real Modal sandbox started as
Harbor starts one.

Modal bills for the sandbox, so these run only with SUSS_MODAL_TESTS=1, Modal's
client installed and a Modal token. The stand-in in conftest.py copies what
these show, and the rest of the suite runs against it. A Harbor or Modal
upgrade that changes the behaviour fails here, and only here.
"""

import asyncio
import importlib.util
import os
import shlex
from pathlib import Path

import pytest
from harbor.models.task.config import EnvironmentConfig
from harbor.models.trial.paths import TrialPaths

from suss import harness
from suss.config import Arm
from suss.harness import SandboxPaths, load_manifest

FAKE_ADAPTER = Path(__file__).resolve().parent / "fake_adapter"
ADAPTER = "/opt/suss/adapter"
WORKSPACE = "/app"
PATHS = SandboxPaths()

pytestmark = pytest.mark.skipif(
    os.environ.get("SUSS_MODAL_TESTS") != "1" or importlib.util.find_spec("modal") is None,
    reason="set SUSS_MODAL_TESTS=1, with Modal's client installed, to run in a billed sandbox",
)


@pytest.fixture(scope="module")
def loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="module")
def sandbox(loop, tmp_path_factory):
    from harbor.environments.modal import ModalEnvironment

    root = tmp_path_factory.mktemp("modal-sandbox")
    environment = ModalEnvironment(
        environment_dir=root,
        environment_name="suss-tests",
        session_id="suss-tests",
        trial_paths=TrialPaths(root / "trial"),
        task_env_config=EnvironmentConfig(docker_image="debian:bookworm-slim"),
        app_name="suss-tests",
        # Short lives, so that a run killed before its teardown bills minutes, not a day.
        sandbox_timeout_secs=300,
        sandbox_idle_timeout_secs=60,
    )
    try:
        loop.run_until_complete(environment.start(force_build=False))
        yield environment
    finally:
        loop.run_until_complete(environment.stop(delete=True))


def alive(loop, env, pids: list[int]) -> list[int]:
    """The pids among `pids` still running in the sandbox, after 2s for them to die."""
    listed = " ".join(map(str, pids))
    result = loop.run_until_complete(
        env.exec(f"sleep 2; for p in {listed}; do kill -0 $p 2> /dev/null && echo $p; done")
    )
    return [int(pid) for pid in result.stdout.split()]


def sleeping(loop, env, seconds: int) -> bool:
    """Whether a `sleep <seconds>` is running in the sandbox."""
    script = (
        "for p in /proc/[0-9]*; do"
        f' [ "$(tr "\\0" " " < $p/cmdline 2> /dev/null)" = "sleep {seconds} " ] && echo yes;'
        " done"
    )
    return "yes" in loop.run_until_complete(env.exec(script)).stdout


def test_exec_at_timeout_returns_minus_one_and_kills_only_the_shell(loop, sandbox):
    pids = "/tmp/timeout.pids"
    command = f"sleep 30 & echo $! > {pids}; echo $$ >> {pids}; echo before; sleep 30"

    result = loop.run_until_complete(sandbox.exec(command, timeout_sec=2))

    assert (result.return_code, result.stdout, result.stderr) == (-1, "before\n", "")
    child, shell = map(int, loop.run_until_complete(sandbox.exec(f"cat {pids}")).stdout.split())
    assert alive(loop, sandbox, [child, shell]) == [child], (
        "Modal no longer kills the shell alone at exec's timeout"
    )


def start_run(loop, env, script: str, max_seconds: int) -> harness.HarnessResult:
    loop.run_until_complete(env.exec(f"rm -rf {ADAPTER} /logs/agent; mkdir -p {WORKSPACE}"))
    loop.run_until_complete(env.upload_dir(FAKE_ADAPTER, ADAPTER))
    then = shlex.quote(f"{ADAPTER}/run.then")
    loop.run_until_complete(
        env.exec(f"chmod +x {ADAPTER}/*.sh && printf '%s\\n' {shlex.quote(script)} > {then}")
    )
    the_arm = Arm(
        name="fake", harness=FAKE_ADAPTER, baseline=None, params={}, max_seconds=max_seconds
    )
    return loop.run_until_complete(
        harness.run(env, load_manifest(FAKE_ADAPTER), the_arm, WORKSPACE, {}, 0, PATHS)
    )


# A child in run's process group, and one in a session of its own.
ESCAPES = 'sleep 301 &\necho $! > /tmp/run.pids\nsetsid sleep 302 &\necho $! >> /tmp/run.pids\n'


def test_a_run_past_max_seconds_is_stopped_and_timed_out(loop, sandbox):
    result = start_run(
        loop, sandbox, f"echo before the limit\necho on stderr >&2\n{ESCAPES}sleep 300", 3
    )

    assert result.status == "timed-out"
    assert result.exit_codes["run"] is None
    log = loop.run_until_complete(sandbox.exec("cat /logs/agent/run.log")).stdout
    assert log == "before the limit\non stderr\n"
    pids = loop.run_until_complete(sandbox.exec("cat /tmp/run.pids")).stdout.split()
    assert alive(loop, sandbox, list(map(int, pids))) == []


def test_a_run_that_only_execs_own_timeout_stops_is_timed_out(loop, sandbox, monkeypatch):
    # exec's timeout at 3s, long before max_seconds, so that Modal's fires first.
    monkeypatch.setattr(harness, "EXEC_TIMEOUT_GRACE_SECONDS", 3 - 60)

    result = start_run(loop, sandbox, f"{ESCAPES}sleep 300", 60)

    assert result.status == "timed-out"
    assert result.exit_codes["run"] is None
    pids = loop.run_until_complete(sandbox.exec("cat /tmp/run.pids")).stdout.split()
    assert alive(loop, sandbox, list(map(int, pids))) == []
    assert not sleeping(loop, sandbox, 60), "the wrapper's timer outlived the stop"
