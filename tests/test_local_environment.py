import asyncio
import inspect
import os
import shlex
import time

import pytest
from harbor.environments.base import BaseEnvironment, ExecResult


def run(coroutine):
    return asyncio.run(coroutine)


def is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def dies(pid: int, within: float = 2) -> bool:
    """Whether the process is gone within the time, which allows for its parent reaping it."""
    deadline = time.monotonic() + within
    while is_running(pid):
        if time.monotonic() > deadline:
            return False
        time.sleep(0.05)
    return True


@pytest.mark.parametrize("method", ["exec", "upload_file", "upload_dir"])
def test_methods_match_harbors_signatures(local_environment, method):
    ours = getattr(type(local_environment), method)
    harbors = getattr(BaseEnvironment, method)
    assert inspect.iscoroutinefunction(ours), f"{method} is not async, and Harbor's is"
    assert inspect.signature(ours) == inspect.signature(harbors), (
        f"{method} has drifted from Harbor's signature"
    )


def test_exec_returns_output_as_docker_does(local_environment):
    result = run(local_environment.exec("echo out; echo err >&2; exit 3"))

    assert type(result) is ExecResult
    assert result.stdout == "out\nerr\n", "Docker merges stderr into stdout"
    assert result.stderr is None
    assert result.return_code == 3


def test_exec_with_no_output_gives_none(local_environment):
    result = run(local_environment.exec("true"))

    assert result.stdout is None, "Docker gives None, not '', for no output"
    assert result.stderr is None
    assert result.return_code == 0


def test_exec_honours_cwd_and_env(local_environment, tmp_path):
    result = run(
        local_environment.exec(
            'pwd -P; echo "$SUSS_PROBE"', cwd=str(tmp_path), env={"SUSS_PROBE": "seen"}
        )
    )

    assert result.stdout == f"{tmp_path.resolve()}\nseen\n"


def test_exec_still_sees_the_inherited_environment(local_environment):
    result = run(local_environment.exec('echo "$PATH"', env={"SUSS_PROBE": "seen"}))

    assert result.stdout == f"{os.environ['PATH']}\n"


def test_exec_at_timeout_raises_and_leaves_the_command_running(local_environment, tmp_path):
    # Docker stops only its own client at the timeout, never the command in the container.
    pids = shlex.quote(str(tmp_path / "pids"))
    command = f"sleep 30 & echo $! > {pids}; echo $$ >> {pids}; sleep 30"

    started = time.monotonic()
    with pytest.raises(RuntimeError, match=r"^Command timed out after 1 seconds$"):
        run(local_environment.exec(command, timeout_sec=1))
    elapsed = time.monotonic() - started

    assert elapsed < 3, f"exec took {elapsed:.1f}s to give up on a 1s timeout"
    for pid in map(int, (tmp_path / "pids").read_text().split()):
        assert is_running(pid), f"process {pid} was killed at the timeout, which Docker does not do"


def test_stop_kills_what_a_timeout_left_running(local_environment, tmp_path):
    pids = shlex.quote(str(tmp_path / "pids"))
    command = f"sleep 30 & echo $! > {pids}; echo $$ >> {pids}; sleep 30"
    with pytest.raises(RuntimeError):
        run(local_environment.exec(command, timeout_sec=1))

    run(local_environment.stop(delete=True))

    for pid in map(int, (tmp_path / "pids").read_text().split()):
        assert dies(pid), f"process {pid} outlived stop, as no container's would"


def test_stop_kills_jobs_a_command_put_in_process_groups_of_their_own(local_environment, tmp_path):
    # As suss's time limit does, with job control.
    pids = shlex.quote(str(tmp_path / "pids"))
    command = f"set -m; sleep 30 & echo $! > {pids}; echo $$ >> {pids}; wait"
    with pytest.raises(RuntimeError):
        run(local_environment.exec(command, timeout_sec=1))

    run(local_environment.stop(delete=True))

    for pid in map(int, (tmp_path / "pids").read_text().split()):
        assert dies(pid), f"process {pid} outlived stop, as no container's would"


def test_exec_refuses_a_user_it_cannot_switch_to(local_environment):
    with pytest.raises(NotImplementedError, match="user"):
        run(local_environment.exec("true", user="agent"))


def test_upload_file_creates_missing_parents(local_environment, tmp_path):
    source = tmp_path / "instruction.md"
    source.write_text("fix the bug")
    target = tmp_path / "sandbox" / "logs" / "agent" / "instruction.md"

    run(local_environment.upload_file(source, str(target)))

    assert target.read_text() == "fix the bug"


def test_upload_dir_copies_contents_into_a_new_target(local_environment, tmp_path):
    source = tmp_path / "claude-code"
    (source / "bin").mkdir(parents=True)
    (source / "suss-harness.toml").write_text("name = 'claude-code'")
    (source / "bin" / "run").write_text("#!/bin/sh")
    target = tmp_path / "sandbox" / "opt" / "suss" / "adapter"

    run(local_environment.upload_dir(source, str(target)))

    assert (target / "suss-harness.toml").read_text() == "name = 'claude-code'"
    assert (target / "bin" / "run").read_text() == "#!/bin/sh"
    assert not (target / "claude-code").exists(), "the source dir was nested, not its contents"


def test_upload_dir_merges_into_an_existing_target(local_environment, tmp_path):
    source = tmp_path / "claude-code"
    source.mkdir()
    (source / "suss-harness.toml").write_text("new")
    target = tmp_path / "adapter"
    target.mkdir()
    (target / "suss-harness.toml").write_text("old")
    (target / "kept").write_text("kept")

    run(local_environment.upload_dir(source, str(target)))

    assert (target / "suss-harness.toml").read_text() == "new"
    assert (target / "kept").read_text() == "kept"
    assert not (target / "claude-code").exists(), "the source dir was nested, not its contents"


def test_stop_kills_a_job_its_command_left_behind_when_it_exited(local_environment, tmp_path):
    # The job outlives its shell, so nothing links it to the command by parent any more.
    pids = shlex.quote(str(tmp_path / "pids"))
    command = f"set -m; sleep 30 > /dev/null 2>&1 & echo $! > {pids}"
    run(local_environment.exec(command))

    run(local_environment.stop(delete=True))

    for pid in map(int, (tmp_path / "pids").read_text().split()):
        assert dies(pid), f"process {pid} outlived stop, as no container's would"
