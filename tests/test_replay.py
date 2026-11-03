"""
Regrade trials through ReplayAgent, in the stand-in environment.

Each test lays out, on the host, an agent job as Harbor leaves it: one directory
per trial, holding the config.json Harbor wrote and the trial's agent/model.patch.
The regrade's own trial directory holds only its config.json, as it does when
Harbor starts the agent. The sandbox's workspace is a git repository beside them.
"""

import asyncio
import json
import subprocess
from pathlib import Path

import pytest
from harbor.agents.factory import AgentFactory
from harbor.cli.utils import parse_kwargs
from harbor.models.agent.context import AgentContext
from harbor.models.trial.config import AgentConfig

import suss.replay
from suss.replay import ReplayAgent, ReplayError

TASK = "gleam-lang__gleam-5797"
# Git sees no NUL as text, and writes these bytes into the patch raw, where
# passing the patch through decoded text corrupts them.
HIGH_BYTES = bytes(range(0x80, 0x100)) + b"\n"
LINES = "".join(f"line {n}\n" for n in range(1, 21))


def go(coroutine):
    return asyncio.run(coroutine)


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout


@pytest.fixture
def workspace(tmp_path) -> Path:
    """The task's repository at its base, with one file of twenty numbered lines."""
    repo = tmp_path / "sandbox" / "gleam"
    repo.mkdir(parents=True)
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.com")
    (repo / "lines.txt").write_text(LINES)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "base")
    return repo


@pytest.fixture
def found(monkeypatch, workspace) -> list[tuple]:
    """Stand find_workspace in for the sandbox's starting directory, and record each call."""
    calls: list[tuple] = []

    async def find_workspace(*args, **kwargs):
        calls.append((args, kwargs))
        return str(workspace)

    monkeypatch.setattr(suss.replay, "find_workspace", find_workspace)
    return calls


def patch_of(workspace: Path, change) -> bytes:
    """The patch SussAgent would capture for `change`, leaving the workspace at its base."""
    change(workspace)
    git(workspace, "add", "-A")
    patch = subprocess.run(
        ["git", "diff", "--cached", "--binary", "HEAD"],
        cwd=workspace,
        check=True,
        capture_output=True,
    ).stdout
    git(workspace, "reset", "-q", "--hard", "HEAD")
    git(workspace, "clean", "-qfd")
    return patch


def edit_line(number: int, text: str):
    def change(repo: Path) -> None:
        lines = (repo / "lines.txt").read_text().splitlines(keepends=True)
        lines[number - 1] = f"{text}\n"
        (repo / "lines.txt").write_text("".join(lines))

    return change


def trial(job: Path, directory: str, task: str, patch: bytes | None) -> Path:
    """A trial of `task` in `job`, in a directory named `directory`."""
    trial = job / directory
    (trial / "agent").mkdir(parents=True)
    config = {"task": {"path": f"lanes/rebench/{task}", "source": "rebench"}}
    (trial / "config.json").write_text(json.dumps(config))
    if patch is not None:
        (trial / "agent" / "model.patch").write_bytes(patch)
    return trial


def replay_agent(tmp_path: Path, source_job, task: str = TASK) -> ReplayAgent:
    """A ReplayAgent for the regrade trial of `task`, constructed as Harbor constructs it."""
    regrade = tmp_path / "regrade-job" / "gleam-lang__gleam-57__XyZ1234"
    regrade.mkdir(parents=True)
    config = {"task": {"path": f"lanes/rebench/{task}", "source": "rebench"}}
    (regrade / "config.json").write_text(json.dumps(config))
    return ReplayAgent(
        source_job=source_job,
        patch_target=str(tmp_path / "sandbox" / "tmp" / "replay.patch"),
        logs_dir=regrade / "agent",
    )


def replay(agent: ReplayAgent, environment) -> dict:
    go(agent.setup(environment))
    go(agent.run("an instruction", environment, AgentContext()))
    return json.loads((agent.logs_dir / "replay.json").read_text())


def test_finds_the_patch_by_the_task_the_trial_ran_not_its_directory_name(
    tmp_path, local_environment, workspace, found
):
    job = tmp_path / "agent-job"
    # Harbor shortens the task's name for the directory, and a directory may
    # even carry another task's name.
    ours = trial(job, "gleam-lang__gleam-57__AbCdEfG", TASK, patch_of(workspace, edit_line(3, "ours")))
    trial(job, TASK, "other-org__other-1", patch_of(workspace, edit_line(3, "theirs")))

    record = replay(replay_agent(tmp_path, str(job)), local_environment)

    assert record["task"] == TASK
    assert record["patch"] == str(ours / "agent" / "model.patch")
    assert record["apply_rc"] == 0
    assert (workspace / "lines.txt").read_text().splitlines()[2] == "ours"


def test_harbor_loads_it_from_its_import_path_and_source_job(tmp_path):
    job = tmp_path / "agent-job"
    job.mkdir()
    config = AgentConfig(
        name="suss.replay:ReplayAgent",
        model_name="replay",
        kwargs=parse_kwargs([f"source_job={job}"]),
    )

    agent = AgentFactory.create_agent_from_config(config, logs_dir=tmp_path / "agent")

    assert isinstance(agent, ReplayAgent)
    assert agent.source_job == job


def test_finds_the_workspace_with_the_environment_alone(
    tmp_path, local_environment, workspace, found
):
    job = tmp_path / "agent-job"
    trial(job, "t1", TASK, patch_of(workspace, edit_line(3, "ours")))

    replay(replay_agent(tmp_path, str(job)), local_environment)

    assert found == [((local_environment,), {})]


@pytest.mark.parametrize("patch", [b"", None], ids=["empty", "missing"])
def test_an_empty_or_missing_patch_applies_nothing_and_records_null(
    tmp_path, local_environment, workspace, found, patch
):
    job = tmp_path / "agent-job"
    trial(job, "t1", TASK, patch)

    agent = replay_agent(tmp_path, str(job))
    record = replay(agent, local_environment)

    assert record == {"task": TASK, "patch": None}
    assert git(workspace, "status", "--porcelain") == ""
    assert not Path(agent.patch_target).exists()


def test_a_source_job_that_is_not_a_directory_is_an_error(tmp_path):
    missing = tmp_path / "no-such-job"

    with pytest.raises(ReplayError, match=str(missing)):
        replay_agent(tmp_path, str(missing))


def test_a_source_job_harbor_parsed_as_json_is_an_error(tmp_path):
    with pytest.raises(TypeError, match="source_job"):
        replay_agent(tmp_path, 2026)


def test_a_source_job_with_no_trial_of_the_task_is_an_error(
    tmp_path, local_environment, workspace, found
):
    job = tmp_path / "agent-job"
    trial(job, TASK, "other-org__other-1", patch_of(workspace, edit_line(3, "theirs")))
    agent = replay_agent(tmp_path, str(job))
    go(agent.setup(local_environment))

    with pytest.raises(ReplayError, match=f"no trial of {TASK}"):
        go(agent.run("an instruction", local_environment, AgentContext()))
    assert git(workspace, "status", "--porcelain") == ""
    assert not (agent.logs_dir / "replay.json").exists()


def test_two_trials_of_the_task_are_an_error_naming_both(
    tmp_path, local_environment, workspace, found
):
    job = tmp_path / "agent-job"
    trial(job, "first__AAA", TASK, patch_of(workspace, edit_line(3, "one")))
    trial(job, "second__BBB", TASK, patch_of(workspace, edit_line(3, "two")))
    agent = replay_agent(tmp_path, str(job))
    go(agent.setup(local_environment))

    with pytest.raises(ReplayError, match="first__AAA.*second__BBB"):
        go(agent.run("an instruction", local_environment, AgentContext()))
    assert git(workspace, "status", "--porcelain") == ""


def test_a_trial_whose_config_cannot_be_read_is_an_error(
    tmp_path, local_environment, workspace, found
):
    job = tmp_path / "agent-job"
    trial(job, "t1", TASK, patch_of(workspace, edit_line(3, "ours")))
    broken = trial(job, "t2", "other-org__other-1", None)
    (broken / "config.json").write_text("{ not json")
    agent = replay_agent(tmp_path, str(job))
    go(agent.setup(local_environment))

    with pytest.raises(ReplayError, match=str(broken / "config.json")):
        go(agent.run("an instruction", local_environment, AgentContext()))


def test_applies_bytes_that_are_not_utf8_exactly(tmp_path, local_environment, workspace, found):
    def add_high_bytes(repo: Path) -> None:
        (repo / "latin1.txt").write_bytes(HIGH_BYTES)

    job = tmp_path / "agent-job"
    trial(job, "t1", TASK, patch_of(workspace, add_high_bytes))

    record = replay(replay_agent(tmp_path, str(job)), local_environment)

    assert record["apply_rc"] == 0
    assert (workspace / "latin1.txt").read_bytes() == HIGH_BYTES


def methods_run(record: dict) -> list[str]:
    return [line[2:] for line in record["apply_out"].splitlines() if line.startswith("$ ")]


def test_a_patch_that_applies_cleanly_runs_only_git_apply(
    tmp_path, local_environment, workspace, found
):
    job = tmp_path / "agent-job"
    trial(job, "t1", TASK, patch_of(workspace, edit_line(13, "ours")))

    record = replay(replay_agent(tmp_path, str(job)), local_environment)

    assert methods_run(record) == ["git apply --verbose"]
    assert record["apply_rc"] == 0
    assert "Applied patch lines.txt cleanly" in record["apply_out"]


def test_a_patch_whose_context_moved_falls_back_to_a_three_way_merge(
    tmp_path, local_environment, workspace, found
):
    job = tmp_path / "agent-job"
    trial(job, "t1", TASK, patch_of(workspace, edit_line(13, "ours")))
    # A context line of the hunk differs from the patch's, but merges cleanly.
    edit_line(10, "moved")(workspace)
    git(workspace, "commit", "-qam", "moved")

    record = replay(replay_agent(tmp_path, str(job)), local_environment)

    assert methods_run(record) == ["git apply --verbose", "git apply --3way"]
    assert record["apply_rc"] == 0
    lines = (workspace / "lines.txt").read_text().splitlines()
    assert (lines[9], lines[12]) == ("moved", "ours")


def test_a_patch_git_cannot_merge_falls_back_to_patch_with_fuzz(
    tmp_path, local_environment, workspace, found
):
    # No index line, so git has no blob to merge from.
    hand_written = (
        b"--- a/lines.txt\n+++ b/lines.txt\n@@ -10,7 +10,7 @@\n"
        b" line 10\n line 11\n line 12\n-line 13\n+ours\n line 14\n line 15\n line 16\n"
    )
    job = tmp_path / "agent-job"
    trial(job, "t1", TASK, hand_written)
    edit_line(10, "moved")(workspace)
    git(workspace, "commit", "-qam", "moved")

    record = replay(replay_agent(tmp_path, str(job)), local_environment)

    assert methods_run(record) == [
        "git apply --verbose",
        "git apply --3way",
        "patch --fuzz=3 -p1 -i",
    ]
    assert record["apply_rc"] == 0
    assert (workspace / "lines.txt").read_text().splitlines()[12] == "ours"


def test_a_patch_nothing_applies_records_the_failure_and_returns(
    tmp_path, local_environment, workspace, found
):
    hand_written = b"--- a/absent.txt\n+++ b/absent.txt\n@@ -1,1 +1,1 @@\n-gone\n+ours\n"
    job = tmp_path / "agent-job"
    trial(job, "t1", TASK, hand_written)

    record = replay(replay_agent(tmp_path, str(job)), local_environment)

    assert methods_run(record) == [
        "git apply --verbose",
        "git apply --3way",
        "patch --fuzz=3 -p1 -i",
    ]
    assert record["apply_rc"] != 0
    assert "absent.txt" in record["apply_out"]


def test_each_methods_output_follows_its_own_line_where_stderr_is_kept_apart(
    tmp_path, modal_environment, workspace, found
):
    hand_written = b"--- a/absent.txt\n+++ b/absent.txt\n@@ -1,1 +1,1 @@\n-gone\n+ours\n"
    job = tmp_path / "agent-job"
    trial(job, "t1", TASK, hand_written)

    record = replay(replay_agent(tmp_path, str(job)), modal_environment)

    first_method_output = record["apply_out"].split("$ git apply --3way")[0]
    assert "error: absent.txt" in first_method_output, record["apply_out"]
