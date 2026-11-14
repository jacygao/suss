"""
Run a command under `run_limited` in the stand-in environments, and check what
it leaves running when its limit or exec's own timeout stops it.

The command is a script the test writes, which records its own process id and
that of a child it backgrounds, as a coding agent's tools are.
"""

import asyncio
import os
import subprocess
import time
from pathlib import Path

import pytest

from swe_drill.sandbox import run_limited


def go(coroutine):
    return asyncio.run(coroutine)


RECORD_PIDS = 'sleep 30 &\necho $! $$ > "$(dirname "$0")/pids"'


@pytest.fixture
def box(tmp_path) -> Path:
    root = tmp_path / "sand box"
    (root / "logs").mkdir(parents=True)
    (root / "workspace").mkdir()
    return root


def script(box: Path, body: str) -> list[str]:
    path = box / "the script.sh"
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(0o755)
    return [str(path)]


def limited(env, box: Path, body: str, timeout_sec=None, env_vars=None) -> int | None:
    return go(run_limited(
        env, script(box, body), cwd=str(box / "workspace"), env_vars=env_vars or {},
        log_path=str(box / "logs" / "session.log"), timeout_sec=timeout_sec,
    ))


def is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def assert_all_dead(box: Path) -> None:
    """Fail unless the processes RECORD_PIDS named are gone, allowing 2s for them to be reaped."""
    pids = [int(pid) for pid in (box / "pids").read_text().split()]
    deadline = time.monotonic() + 2
    while alive := [pid for pid in pids if is_running(pid)]:
        assert time.monotonic() < deadline, f"processes {alive} outlived the command that started them"
        time.sleep(0.05)


def early_exec_timeout(env, monkeypatch) -> None:
    """Have exec give up on the command after 1s, whatever timeout is asked for."""
    exec_as_given = env.exec

    async def early(command, **kwargs):
        if kwargs.get("timeout_sec") is not None:
            kwargs["timeout_sec"] = 1
        return await exec_as_given(command, **kwargs)

    monkeypatch.setattr(env, "exec", early)


def left_running(env) -> list[str]:
    """Every live process in a session that one of env's commands started, allowing 2s to die."""
    deadline = time.monotonic() + 2
    while True:
        listed = subprocess.run(["ps", "-A", "-o", "pid=,stat=,command="], capture_output=True, text=True).stdout
        alive = []
        for line in listed.splitlines():
            pid, stat, command = line.split(None, 2)
            try:
                in_session = os.getsid(int(pid)) in env._sessions
            except ProcessLookupError:
                continue
            if in_session and not stat.startswith("Z"):
                alive.append(f"{pid} {command}")
        if not alive or time.monotonic() > deadline:
            return alive
        time.sleep(0.05)


def test_a_command_past_its_limit_is_stopped_with_what_it_started(local_environment, box):
    started = time.monotonic()
    code = limited(local_environment, box, f"echo before the limit\n{RECORD_PIDS}\nsleep 30", 1)

    assert code is None, "a stopped command has no exit code"
    assert time.monotonic() - started < 10
    assert "before the limit" in (box / "logs" / "session.log").read_text()
    assert_all_dead(box)


def test_a_command_that_only_execs_own_timeout_stops_is_still_stopped(local_environment, box, monkeypatch):
    early_exec_timeout(local_environment, monkeypatch)

    assert limited(local_environment, box, f"{RECORD_PIDS}\nsleep 30", 60) is None
    assert_all_dead(box)


def test_a_command_past_its_limit_under_modal_is_stopped_and_its_log_kept(modal_environment, box):
    code = limited(modal_environment, box, f"echo before the limit\necho on stderr >&2\n{RECORD_PIDS}\nsleep 30", 1)

    assert code is None
    assert (box / "logs" / "session.log").read_text() == "before the limit\non stderr\n"
    assert_all_dead(box)
    assert left_running(modal_environment) == []


def test_modals_minus_one_at_execs_own_timeout_is_not_taken_for_an_exit_code(
    modal_environment, box, monkeypatch
):
    early_exec_timeout(modal_environment, monkeypatch)

    assert limited(modal_environment, box, f"{RECORD_PIDS}\nsleep 30", 60) is None
    assert_all_dead(box)
    assert left_running(modal_environment) == [], "the wrapper's timer outlived the wrapper Modal killed"


def test_a_cancelled_command_is_stopped_with_what_it_started(local_environment, box):
    async def scenario():
        going = asyncio.create_task(run_limited(
            local_environment, script(box, f"{RECORD_PIDS}\nsleep 30"), cwd=str(box / "workspace"),
            env_vars={}, log_path=str(box / "logs" / "session.log"),
        ))
        deadline = time.monotonic() + 10
        while not (box / "pids").exists() or not (box / "pids").read_text().strip():
            assert time.monotonic() < deadline, "the command never started"
            await asyncio.sleep(0.05)
        going.cancel()
        with pytest.raises(asyncio.CancelledError):
            await going

    go(scenario())

    assert_all_dead(box)


def test_a_command_under_its_limit_keeps_its_exit_code_and_its_variables(local_environment, box):
    code = limited(local_environment, box, 'echo "$SWE_DRILL_TEST_KEY"\nexit 5', 30, {"SWE_DRILL_TEST_KEY": "given"})

    assert code == 5
    assert (box / "logs" / "session.log").read_text() == "given\n"
    assert sorted(path.name for path in (box / "logs").iterdir()) == ["session.log"], (
        "a finished command left a file a later stop could act on"
    )
