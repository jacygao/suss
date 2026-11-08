"""
Run whole trials through suss's agents, in the stand-in environment.

SussMixin is tested on Scripted, a Harbor installed agent whose run is a shell
script the test writes, so what the mixin does is seen apart from any coding
agent. The host holds suss.toml, its .env, and a download of one task for the
grader's digest. The sandbox is a directory beside it, with its workspace and
its logs and artifacts directories as siblings.
"""

import asyncio
import dataclasses
import json
import re
import shlex
import subprocess
import time
from pathlib import Path
from typing import ClassVar

import pytest
from harbor.agents.installed.base import BaseInstalledAgent, NonZeroAgentExitCodeError
from harbor.cli.utils import parse_kwargs
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

from suss import lanes
from suss.agents import SussMixin
from suss.sandbox import MARKER
from suss.workspace import WorkspaceError

SPEC = Path(__file__).resolve().parent.parent / "docs" / "specs" / "suss-spec.md"
INSTRUCTION = "Fix the naïve café bug ✓\n`$HOME` \"$(id)\" 'it's'\nEOF\n\n"
KNOWN_CHANGE = 'echo "a known change" >> hello.txt'


def go(coroutine):
    return asyncio.run(coroutine)


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout


def init_repo(repo: Path, files: dict[str, str]) -> str:
    repo.mkdir(parents=True)
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.com")
    for name, text in files.items():
        (repo / name).write_text(text)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "base")
    return git(repo, "rev-parse", "HEAD").strip()


def spec_trial_record() -> dict:
    """The example suss.json from the spec, so the record and the spec cannot drift apart."""
    blocks = re.findall(r"```json\n(.*?)```", SPEC.read_text(), re.DOTALL)
    examples = [block for block in blocks if '"patch_bytes"' in block]
    assert len(examples) == 1, f"expected one example suss.json in {SPEC}, found {len(examples)}"
    return json.loads(examples[0].replace("…", ""))


class Scripted(BaseInstalledAgent):
    """A Harbor installed agent that runs the script in `script` in the workspace."""

    script: ClassVar[str] = ""

    @staticmethod
    def name() -> str:
        return "scripted"

    def get_version_command(self) -> str | None:
        return "echo scripted 0.0.7"

    async def install(self, environment: BaseEnvironment) -> None:
        await self.exec_as_agent(environment, "echo installing")

    async def run(self, instruction, environment, context) -> None:
        await self.exec_as_agent(environment, self.script)


class SussScripted(SussMixin, Scripted):
    KWARGS = ("rounds",)


@dataclasses.dataclass
class Trial:
    host: Path
    config: Path
    workspace: Path
    base: str
    logs: Path
    artifacts: Path
    host_logs: Path
    download: Path

    def agent(self, cls=SussScripted, arm: str = "fake", config: str | None = None, **kwargs):
        return cls(
            arm=arm,
            lane="rebench",
            suss_config=config if config is not None else str(self.config),
            logs=str(self.logs),
            artifacts=str(self.artifacts),
            logs_dir=self.host_logs,
            **kwargs,
        )

    def run(self, env, agent=None, script: str = KNOWN_CHANGE) -> dict:
        """Run both phases, as Harbor does, and return suss.json."""
        agent = agent or self.agent()
        type(agent).script = script
        go(agent.setup(environment=env))
        go(agent.run(instruction=INSTRUCTION, environment=env, context=AgentContext()))
        return self.record()

    def record(self) -> dict:
        return json.loads((self.logs / "suss.json").read_text())

    def patch(self) -> str:
        return (self.logs / "model.patch").read_text()


@pytest.fixture
def trial(tmp_path, local_environment) -> Trial:
    host = tmp_path / "the host"
    host.mkdir()
    config = host / "suss.toml"
    config.write_text(
        'swe_rebench = "tasks/swe-rebench-07-2026"\n'
        'environment = "docker"\n'
        "k = 1\n"
        "concurrency = 1\n"
        "max_seconds = 60\n"
        "\n"
        "[arms.fake]\n"
        'agent = "test_agents:SussScripted"\n'
        "kwargs = { rounds = 3 }\n"
        "\n"
        "[arms.quick]\n"
        'agent = "test_agents:SussScripted"\n'
        "max_seconds = 1\n"
    )
    (host / ".env").write_text("COPILOT_GITHUB_TOKEN=from-the-env-file\n")
    download = host / "tasks" / "swe-rebench-07-2026"
    (download / "owner__repo-1").mkdir(parents=True)
    (download / "owner__repo-1" / "task.toml").write_text('[environment]\nnetwork_mode = "public"\n')

    sandbox = tmp_path / "the sandbox"
    workspace = sandbox / "gleam"
    base = init_repo(workspace, {"hello.txt": "hello\n"})
    # The sandbox starts in the workspace, as an image's WORKDIR makes it.
    local_environment.task_env_config.workdir = str(workspace)
    # Harbor's own setup runs a command as root, which here is this machine's user.
    local_environment._resolve_user = lambda user: None if user == "root" else user
    return Trial(
        host=host,
        config=config,
        workspace=workspace,
        base=base,
        logs=sandbox / "logs" / "agent",
        artifacts=sandbox / "logs" / "artifacts",
        host_logs=tmp_path / "harbor trial" / "agent",
        download=download,
    )


def assert_patch_carries_the_known_change(trial: Trial, tmp_path: Path) -> None:
    clone = tmp_path / "clone"
    git(tmp_path, "clone", "-q", str(trial.workspace), str(clone))
    git(clone, "checkout", "-q", trial.base)
    git(clone, "apply", str(trial.logs / "model.patch"))
    assert (clone / "hello.txt").read_text() == "hello\na known change\n", (
        "model.patch does not carry the change the agent made"
    )


# The mixin


def test_a_whole_trial_leaves_the_patch_and_a_record_with_the_specs_fields(
    local_environment, trial, tmp_path
):
    record = trial.run(local_environment)

    assert_patch_carries_the_known_change(trial, tmp_path)
    assert set(record) == set(spec_trial_record()), "suss.json's keys are not the spec's"
    assert record["arm"] == "fake"
    assert record["agent"] == {
        "class": "test_agents:SussScripted", "name": "scripted", "version": "scripted 0.0.7",
    }
    assert record["kwargs"] == {}
    assert record["grader"] == {"dataset": "swe-rebench-07-2026", "digest": lanes.digest(trial.download)}
    assert record["base"] == trial.base
    assert record["status"] == "ok"
    assert record["run_seconds"] >= 0
    assert record["patch_bytes"] == len((trial.logs / "model.patch").read_bytes()) > 0
    assert record["usage"] is None


def test_the_record_keeps_the_arms_kwargs_and_model_as_harbor_gave_them(local_environment, trial):
    agent = trial.agent(rounds=3, model_name="fake/model-1")

    record = trial.run(local_environment, agent)

    assert record["kwargs"] == {"rounds": 3}
    assert record["model"] == "fake/model-1"


def test_changes_the_image_already_had_stay_out_of_the_patch(local_environment, trial):
    (trial.workspace / "hello.txt").write_text("hello from the image\n")
    (trial.workspace / "pom.xml").write_text("<version>1.18.38</version>\n")

    record = trial.run(local_environment)

    patch = trial.patch()
    assert "pom.xml" not in patch, "model.patch holds a file only the image changed"
    assert "-hello\n" not in patch, "model.patch holds the image's own edit"
    assert "+a known change\n" in patch, "model.patch lost the agent's change"
    assert record["base"] == trial.base, "the record's base is not the commit HEAD named"


def test_a_run_that_exits_non_zero_is_harness_failed_its_change_captured_and_its_error_raised(
    local_environment, trial
):
    agent = trial.agent()
    SussScripted.script = f"{KNOWN_CHANGE}\nexit 3"
    go(agent.setup(environment=local_environment))

    with pytest.raises(NonZeroAgentExitCodeError):
        go(agent.run(instruction=INSTRUCTION, environment=local_environment, context=AgentContext()))

    assert trial.record()["status"] == "harness-failed"
    assert "a known change" in trial.patch()


def test_a_run_past_max_seconds_is_timed_out_and_returns(local_environment, trial):
    record = trial.run(local_environment, trial.agent(arm="quick"), f"{KNOWN_CHANGE}\nsleep 30")

    assert record["status"] == "timed-out"
    assert record["run_seconds"] < 10
    assert "a known change" in trial.patch()


def test_every_command_of_the_run_carries_a_token_the_setup_phase_does_not(
    local_environment, trial, monkeypatch
):
    seen = []
    exec_as_given = local_environment.exec

    async def recording(command, env=None, **kwargs):
        seen.append((command, dict(env or {})))
        return await exec_as_given(command, env=env, **kwargs)

    monkeypatch.setattr(local_environment, "exec", recording)
    trial.run(local_environment)

    install = [env for command, env in seen if "echo installing" in command]
    run = [env for command, env in seen if KNOWN_CHANGE in command]
    assert install and MARKER not in install[0], "a setup command carried the run's token"
    assert run and run[0].get(MARKER), "the run's command carries no token a stop could find"


# A run mid-way with a child it backgrounded, which writes to the workspace
# after the run is stopped.
LATE_WRITE = "a late write"
STILL_WORKING = (
    f"{KNOWN_CHANGE}\n"
    f'( setsid sh -c \'sleep 2; echo "{LATE_WRITE}" >> hello.txt\' & )\n'
    "sleep 30"
)


def cancel_mid_run(trial: Trial, env, agent) -> None:
    """Cancel run once the agent has made its change, as Harbor's agent timeout does."""
    SussScripted.script = STILL_WORKING

    async def scenario():
        await agent.setup(environment=env)
        running = asyncio.create_task(
            agent.run(instruction=INSTRUCTION, environment=env, context=AgentContext())
        )
        deadline = time.monotonic() + 10
        while "a known change" not in (trial.workspace / "hello.txt").read_text():
            assert time.monotonic() < deadline, "the agent never made its change"
            await asyncio.sleep(0.05)
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running

    go(scenario())


def test_a_cancelled_run_writes_a_timed_out_record_and_raises_the_cancel(local_environment, trial):
    cancel_mid_run(trial, local_environment, trial.agent())

    record = trial.record()
    assert record["status"] == "timed-out"
    assert record["patch_bytes"] == len((trial.logs / "model.patch").read_bytes())


@pytest.mark.parametrize("end", ["max_seconds", "cancel"])
def test_a_stopped_run_leaves_nothing_writing_to_the_workspace(
    local_environment, trial, tmp_path, end
):
    if end == "cancel":
        cancel_mid_run(trial, local_environment, trial.agent())
    else:
        trial.run(local_environment, trial.agent(arm="quick"), STILL_WORKING)

    # Past the moment the child would have written.
    time.sleep(3)
    assert LATE_WRITE not in (trial.workspace / "hello.txt").read_text(), (
        "the agent's processes went on changing the workspace after the run was stopped"
    )
    assert_patch_carries_the_known_change(trial, tmp_path)


def test_harbors_agent_timeout_still_sees_the_cancel(local_environment, trial):
    agent = trial.agent()
    SussScripted.script = STILL_WORKING

    async def under_harbors_timeout():
        await agent.setup(environment=local_environment)
        await asyncio.wait_for(
            agent.run(instruction=INSTRUCTION, environment=local_environment, context=AgentContext()),
            timeout=1,
        )

    with pytest.raises(TimeoutError):
        go(under_harbors_timeout())
    assert trial.record()["status"] == "timed-out"


def end_normally(trial: Trial, env) -> None:
    trial.run(env)


def end_at_max_seconds(trial: Trial, env) -> None:
    trial.run(env, trial.agent(arm="quick"), f"{KNOWN_CHANGE}\nsleep 30")


def end_at_harbors_timeout(trial: Trial, env) -> None:
    agent = trial.agent()
    SussScripted.script = f"{KNOWN_CHANGE}\nsleep 30"

    async def under_harbors_timeout():
        await agent.setup(environment=env)
        await asyncio.wait_for(
            agent.run(instruction=INSTRUCTION, environment=env, context=AgentContext()), timeout=1
        )

    with pytest.raises(TimeoutError):
        go(under_harbors_timeout())


def end_by_raising(trial: Trial, env) -> None:
    exec_as_given = env.exec

    async def failing_after_run(command, **kwargs):
        result = await exec_as_given(command, **kwargs)
        if KNOWN_CHANGE in command:
            raise ConnectionError("the sandbox went away")
        return result

    agent = trial.agent()
    SussScripted.script = KNOWN_CHANGE
    go(agent.setup(environment=env))
    env.exec = failing_after_run
    try:
        with pytest.raises(ConnectionError):
            go(agent.run(instruction=INSTRUCTION, environment=env, context=AgentContext()))
    finally:
        del env.exec


@pytest.mark.parametrize(
    "end", [end_normally, end_at_max_seconds, end_at_harbors_timeout, end_by_raising]
)
def test_the_patch_in_artifacts_is_the_agents_patch_however_the_run_ends(
    local_environment, trial, end
):
    end(trial, local_environment)

    patch = (trial.logs / "model.patch").read_bytes()
    assert b"a known change" in patch
    assert (trial.artifacts / "model.patch").read_bytes() == patch, (
        "the verifier would grade a patch other than the one the trial captured"
    )


@pytest.mark.parametrize("script", [KNOWN_CHANGE, f"{KNOWN_CHANGE}\nexit 3"])
def test_a_patch_that_cannot_be_written_fails_the_trial_rather_than_the_agent(
    local_environment, trial, tmp_path, script
):
    # A file where the logs directory should be, so neither patch can be written.
    blocked = tmp_path / "blocked"
    blocked.write_text("")
    agent = trial.agent()
    agent.sandbox_logs = str(blocked / "agent")
    SussScripted.script = script
    go(agent.setup(environment=local_environment))

    with pytest.raises(WorkspaceError):
        go(agent.run(instruction=INSTRUCTION, environment=local_environment, context=AgentContext()))

    assert not (trial.artifacts / "model.patch").exists()


def test_run_before_setup_is_refused(local_environment, trial):
    with pytest.raises(RuntimeError, match="setup"):
        go(trial.agent().run(instruction=INSTRUCTION, environment=local_environment, context=AgentContext()))


def test_a_trial_that_starts_outside_a_repository_fails_naming_the_directory(
    local_environment, trial, tmp_path
):
    elsewhere = tmp_path / "the sandbox" / "elsewhere"
    elsewhere.mkdir()
    local_environment.task_env_config.workdir = str(elsewhere)

    with pytest.raises(WorkspaceError) as raised:
        go(trial.agent().setup(environment=local_environment))

    assert str(elsewhere) in str(raised.value), "the error does not name the directory"


@pytest.mark.parametrize(
    "key, value", [("arm", 2026), ("arm", True), ("lane", 7), ("suss_config", 1.5)]
)
def test_an_ak_value_harbor_parsed_as_json_is_refused_by_name(trial, key, value):
    given = {"arm": "fake", "lane": "rebench", "suss_config": str(trial.config), key: value}

    with pytest.raises(TypeError) as raised:
        SussScripted(**given, logs_dir=trial.host_logs)

    assert key in str(raised.value)
    assert repr(value) in str(raised.value)


@pytest.mark.parametrize("value", [2026, True, ["x"], {"it's": "a \"name\""}])
def test_the_quoting_an_error_suggests_gives_harbor_a_string(trial, value):
    with pytest.raises(TypeError) as raised:
        SussScripted(arm=value, lane="rebench", suss_config=str(trial.config), logs_dir=trial.host_logs)

    suggested = shlex.split(str(raised.value).split("--ak ")[-1])
    assert len(suggested) == 1, f"the suggestion is not one shell word: {suggested}"
    assert parse_kwargs(suggested) == {"arm": json.dumps(value)}


def test_a_trial_on_a_lane_with_no_grader_fails_before_a_sandbox_starts(trial):
    with pytest.raises(lanes.LaneError, match="grader"):
        SussScripted(arm="fake", lane="nowhere", suss_config=str(trial.config), logs_dir=trial.host_logs)


def test_harbors_own_construction_writes_the_patch_where_harbor_collects_it(trial):
    # Harbor passes these keywords, and the arm's own --ak values.
    agent = SussScripted(
        arm="fake", lane="rebench", suss_config=str(trial.config),
        logs_dir=trial.host_logs, model_name=None, logger=None, extra_env={},
    )

    assert (agent.sandbox_logs, agent.sandbox_artifacts) == ("/logs/agent", "/logs/artifacts")


def test_harbors_own_environment_does_not_reach_the_agent(trial, monkeypatch):
    from harbor.agents.installed.base import CliFlag

    class Flagged(SussScripted):
        CLI_FLAGS = [CliFlag("effort", cli="--effort", env_fallback="SUSS_TEST_EFFORT")]

    monkeypatch.setenv("SUSS_TEST_EFFORT", "max")

    assert Flagged(arm="fake", lane="rebench", suss_config=str(trial.config),
                   logs_dir=trial.host_logs).build_cli_flags() == ""


def test_a_cancel_during_the_stop_still_captures_the_patch_then_raises(local_environment, trial):
    agent = trial.agent()
    SussScripted.script = KNOWN_CHANGE
    exec_as_given = local_environment.exec
    stopping = asyncio.Event()

    async def slow_stop(command, **kwargs):
        if "suss_kill_marked" in command and not stopping.is_set():
            stopping.set()
            await asyncio.sleep(2)
        return await exec_as_given(command, **kwargs)

    async def scenario():
        await agent.setup(environment=local_environment)
        local_environment.exec = slow_stop
        running = asyncio.create_task(
            agent.run(instruction=INSTRUCTION, environment=local_environment, context=AgentContext())
        )
        await stopping.wait()
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running

    try:
        go(scenario())
    finally:
        del local_environment.exec

    assert "a known change" in trial.patch(), "a cancel during the stop skipped the patch"
    assert (trial.artifacts / "model.patch").exists()
    assert trial.record()["status"] == "ok"
