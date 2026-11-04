"""
Run whole trials through SussAgent, in the stand-in environment, with the fake
adapter in `tests/fake_adapter/` as the harness.

The host holds suss.toml, its .env, a copy of the fake adapter, and a
download of one task for the grader's digest. The sandbox is a directory
beside it, with its workspace, its /opt/suss and its logs directory as
siblings, so nothing suss writes to /opt/suss can fall inside the workspace.
"""

import asyncio
import dataclasses
import json
import os
import re
import shlex
import shutil
import subprocess
import time
from pathlib import Path

import pytest
from harbor.cli.utils import parse_kwargs
from harbor.environments.base import ExecResult
from harbor.models.agent.context import AgentContext

from suss import lanes
from suss.agent import SussAgent
from suss.harness import SandboxPaths
from suss.workspace import WorkspaceError

FAKE_ADAPTER = Path(__file__).resolve().parent / "fake_adapter"
SPEC = Path(__file__).resolve().parent.parent / "docs" / "specs" / "suss-spec.md"

# Bytes a shell or an encoding would change: non-ASCII, quoting, expansion,
# a heredoc terminator, and the trailing newline.
INSTRUCTION = "Fix the naïve café bug ✓\n`$HOME` \"$(id)\" 'it's'\nEOF\n\n"
PARAMS = {"model": "fake-model", "effort": "high", "rounds": 3, "note": "naïve ✓"}


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


@dataclasses.dataclass
class Trial:
    host: Path
    config: Path
    adapter: Path
    workspace: Path
    base: str
    paths: SandboxPaths
    host_logs: Path
    download: Path

    @property
    def logs(self) -> Path:
        return Path(self.paths.logs)

    @property
    def sandbox_adapter(self) -> Path:
        return Path(self.paths.adapter)

    def then(self, command: str, script: str) -> None:
        """Have the fake `command` run `script` once it has recorded how it was started."""
        (self.adapter / f"{command}.then").write_text(script + "\n")

    def agent(
        self, arm: str = "fake", config: str | None = None, lane: str = "rebench"
    ) -> SussAgent:
        return SussAgent(
            arm=arm,
            lane=lane,
            config=config if config is not None else str(self.config),
            logs_dir=self.host_logs,
            paths=self.paths,
        )

    def run(self, env, agent: SussAgent | None = None) -> dict:
        """Run both phases, as Harbor does, and return suss.json."""
        agent = agent or self.agent()
        go(agent.setup(environment=env))
        go(agent.run(instruction=INSTRUCTION, environment=env, context=AgentContext()))
        return json.loads((self.logs / "suss.json").read_text())

    def patch(self) -> str:
        return (self.logs / "model.patch").read_text()


@pytest.fixture
def trial(tmp_path, local_environment) -> Trial:
    host = tmp_path / "the host"
    adapter = host / "adapters" / "fake"
    shutil.copytree(FAKE_ADAPTER, adapter)
    config = host / "suss.toml"
    config.write_text(
        'swe_rebench = "tasks/swe-rebench-07-2026"\n'
        'environment = "docker"\n'
        "k = 1\n"
        "concurrency = 1\n"
        "max_seconds = 60\n"
        "\n"
        "[arms.fake]\n"
        'harness = "adapters/fake"\n'
        "\n"
        "[arms.fake.params]\n"
        + "".join(f"{key} = {json.dumps(value)}\n" for key, value in PARAMS.items())
        + "\n"
        "[arms.quick]\n"
        'harness = "adapters/fake"\n'
        "max_seconds = 1\n"
    )
    (host / ".env").write_text("SUSS_FAKE_KEY=from the env file\n")
    download = host / "tasks" / "swe-rebench-07-2026"
    (download / "owner__repo-1").mkdir(parents=True)
    (download / "owner__repo-1" / "task.toml").write_text('[environment]\nnetwork_mode = "public"\n')

    sandbox = tmp_path / "the sandbox"
    workspace = sandbox / "gleam"
    base = init_repo(workspace, {"hello.txt": "hello\n"})
    # The sandbox starts in the workspace, as an image's WORKDIR makes it.
    local_environment.task_env_config.workdir = str(workspace)
    opt = sandbox / "opt" / "suss"
    paths = SandboxPaths(
        adapter=str(opt / "adapter"),
        instruction=str(opt / "instruction.md"),
        params=str(opt / "params.json"),
        logs=str(sandbox / "logs" / "agent"),
    )
    return Trial(
        host=host,
        config=config,
        adapter=adapter,
        workspace=workspace,
        base=base,
        paths=paths,
        host_logs=tmp_path / "harbor trial" / "agent",
        download=download,
    )


KNOWN_CHANGE = 'echo "a known change" >> hello.txt'


def assert_patch_carries_the_known_change(trial: Trial, tmp_path: Path) -> None:
    clone = tmp_path / "clone"
    git(tmp_path, "clone", "-q", str(trial.workspace), str(clone))
    git(clone, "checkout", "-q", trial.base)
    git(clone, "apply", str(trial.logs / "model.patch"))
    assert (clone / "hello.txt").read_text() == "hello\na known change\n", (
        "model.patch does not carry the change the harness made"
    )


def test_a_whole_trial_leaves_the_patch_the_record_and_both_logs(
    local_environment, trial, tmp_path
):
    trial.then("install", "echo installing")
    trial.then("run", f"echo running\n{KNOWN_CHANGE}")

    record = trial.run(local_environment)

    assert_patch_carries_the_known_change(trial, tmp_path)
    assert record["status"] == "ok"
    assert record["exit_codes"] == {"install": 0, "run": 0}
    assert record["patch_bytes"] == len((trial.logs / "model.patch").read_bytes())
    assert record["patch_bytes"] > 0
    assert (trial.logs / "install.log").read_text() == "installing\n"
    assert (trial.logs / "run.log").read_text() == "running\n"


def test_the_record_has_exactly_the_specs_fields_with_their_values(
    local_environment, trial
):
    trial.then("run", KNOWN_CHANGE)

    record = trial.run(local_environment)

    assert set(record) == set(spec_trial_record()), "suss.json's keys are not the spec's"
    assert record["arm"] == "fake"
    assert record["harness"] == {"name": "fake", "version": "0.0.1"}
    assert record["params"] == PARAMS
    assert record["grader"] == {
        "dataset": "swe-rebench-07-2026",
        "digest": lanes.digest(trial.download),
    }
    assert record["base"] == trial.base
    assert record["status"] == "ok"
    assert record["exit_codes"] == {"install": 0, "run": 0}
    assert isinstance(record["run_seconds"], (int, float))
    assert record["run_seconds"] >= 0
    assert isinstance(record["patch_bytes"], int)


def test_the_graders_digest_is_the_downloads_and_not_the_lanes(local_environment, trial):
    lane = lanes.build_rebench(trial.download, trial.host / "lanes")
    first = trial.run(local_environment)["grader"]["digest"]
    lanes.build_rebench(trial.download, trial.host / "lanes")

    second = trial.run(local_environment)["grader"]["digest"]

    assert first == second == lanes.digest(trial.download)
    assert first != lanes.digest(lane)


def test_a_change_to_the_download_changes_the_next_trials_digest(local_environment, trial):
    before = trial.run(local_environment)["grader"]["digest"]
    (trial.download / "owner__repo-1" / "instruction.md").write_text("Fix it.\n")

    after = trial.run(local_environment)["grader"]["digest"]

    assert after != before


def test_a_download_with_no_tasks_fails_before_a_sandbox_starts(trial):
    shutil.rmtree(trial.download)

    with pytest.raises(lanes.LaneError) as raised:
        trial.agent()

    assert str(raised.value) == f"{trial.download.resolve()}: no such directory"


def test_a_failed_install_skips_run_and_still_writes_an_empty_patch(
    local_environment, trial
):
    trial.then("install", "exit 3")
    trial.then("run", KNOWN_CHANGE)

    record = trial.run(local_environment)

    assert record["status"] == "harness-failed"
    assert record["exit_codes"] == {"install": 3, "run": None}
    assert record["run_seconds"] is None
    assert record["patch_bytes"] == 0
    assert (trial.logs / "model.patch").read_bytes() == b""
    assert not (trial.sandbox_adapter / "run.args").exists(), "run started after install failed"


def test_a_failed_run_is_harness_failed_and_its_change_is_captured(
    local_environment, trial, tmp_path
):
    trial.then("run", f"{KNOWN_CHANGE}\nexit 5")

    record = trial.run(local_environment)

    assert record["status"] == "harness-failed"
    assert record["exit_codes"] == {"install": 0, "run": 5}
    assert record["patch_bytes"] > 0
    assert_patch_carries_the_known_change(trial, tmp_path)


def test_a_run_past_max_seconds_is_timed_out_and_its_change_is_captured(
    local_environment, trial, tmp_path
):
    trial.then("run", f"{KNOWN_CHANGE}\nsleep 30")

    record = trial.run(local_environment, trial.agent(arm="quick"))

    assert record["status"] == "timed-out"
    assert record["exit_codes"] == {"install": 0, "run": None}
    assert record["patch_bytes"] > 0
    assert_patch_carries_the_known_change(trial, tmp_path)


def test_a_lanes_max_seconds_stops_a_run_on_that_lane(
    local_environment, trial, monkeypatch
):
    monkeypatch.setattr(lanes, "LANES", ("rebench", "senior"))
    toml = trial.config.read_text()
    trial.config.write_text(toml.replace("[arms.fake]", "[lanes.senior]\nmax_seconds = 1\n\n[arms.fake]"))
    trial.then("run", "sleep 30")

    record = trial.run(local_environment, trial.agent(lane="senior"))

    assert record["status"] == "timed-out"


def test_a_run_that_raises_still_captures_the_patch(local_environment, trial, monkeypatch):
    trial.then("run", KNOWN_CHANGE)
    exec_as_given = local_environment.exec

    async def failing_after_run(command, **kwargs):
        result = await exec_as_given(command, **kwargs)
        if "run.sh" in command:
            raise ConnectionError("the sandbox went away")
        return result

    agent = trial.agent()
    go(agent.setup(environment=local_environment))
    monkeypatch.setattr(local_environment, "exec", failing_after_run)
    with pytest.raises(ConnectionError):
        go(agent.run(instruction=INSTRUCTION, environment=local_environment, context=AgentContext()))

    assert "a known change" in trial.patch(), "the patch was lost when run raised"


def test_the_instruction_and_params_reach_the_sandbox_unchanged(local_environment, trial):
    trial.then("run", 'cp "$4" "$(dirname "$0")/seen-instruction"')

    trial.run(local_environment)

    assert Path(trial.paths.instruction).read_bytes() == INSTRUCTION.encode()
    assert Path(trial.paths.params).read_bytes() == json.dumps(PARAMS).encode()
    assert (trial.sandbox_adapter / "seen-instruction").read_bytes() == INSTRUCTION.encode(), (
        "run was not given the instruction file suss wrote"
    )


def test_the_commands_get_the_env_files_variables(local_environment, trial):
    trial.run(local_environment)

    for command in ("install", "run"):
        recorded = (trial.sandbox_adapter / f"{command}.env").read_text().splitlines()
        assert "SUSS_FAKE_KEY=from the env file" in recorded, f"{command} missed the .env"


def test_nothing_suss_writes_reaches_the_workspace_or_the_patch(local_environment, trial):
    trial.then("run", KNOWN_CHANGE)

    trial.run(local_environment)

    patch = trial.patch()
    for name in ("instruction.md", "params.json", "suss-harness.toml", "run.sh", "suss.json"):
        assert name not in patch, f"{name} is in model.patch"
    assert sorted(path.name for path in trial.workspace.iterdir()) == [".git", "hello.txt"]
    assert (trial.sandbox_adapter / "suss-harness.toml").exists(), "the adapter was not uploaded"
    assert (trial.sandbox_adapter / "run.sh").exists()


def test_changes_the_image_already_had_stay_out_of_the_patch(local_environment, trial):
    (trial.workspace / "hello.txt").write_text("hello from the image\n")
    (trial.workspace / "pom.xml").write_text("<version>1.18.38</version>\n")
    trial.then("run", 'echo "a known change" >> hello.txt')

    record = trial.run(local_environment)

    patch = trial.patch()
    assert "pom.xml" not in patch, "model.patch holds a file only the image changed"
    assert "-hello\n" not in patch, "model.patch holds the image's own edit"
    assert "+a known change\n" in patch, "model.patch lost the harness's change"
    assert record["base"] == trial.base, "the record's base is not the commit HEAD named"


def test_config_is_read_relative_to_the_current_directory(
    local_environment, trial, monkeypatch
):
    monkeypatch.chdir(trial.host)

    record = trial.run(local_environment, trial.agent(config="suss.toml"))

    assert record["params"] == PARAMS


@pytest.mark.parametrize(
    "key, value", [("arm", 2026), ("arm", True), ("lane", 7), ("config", 1.5)]
)
def test_an_ak_value_harbor_parsed_as_json_is_refused_by_name(trial, key, value):
    given = {"arm": "fake", "lane": "rebench", "config": str(trial.config), key: value}

    with pytest.raises(TypeError) as raised:
        SussAgent(**given, logs_dir=trial.host_logs)

    assert key in str(raised.value)
    assert repr(value) in str(raised.value)


@pytest.mark.parametrize("value", [2026, True, ["x"], {"it's": "a \"name\""}])
def test_the_quoting_an_error_suggests_gives_harbor_a_string(trial, value):
    with pytest.raises(TypeError) as raised:
        SussAgent(arm=value, lane="rebench", config=str(trial.config), logs_dir=trial.host_logs)

    suggested = shlex.split(str(raised.value).split("--ak ")[-1])
    assert len(suggested) == 1, f"the suggestion is not one shell word: {suggested}"
    assert parse_kwargs(suggested) == {"arm": json.dumps(value)}


def test_harbors_own_construction_keeps_the_specs_sandbox_paths(trial):
    # Harbor passes these keywords and no others.
    agent = SussAgent(
        arm="fake",
        lane="rebench",
        config=str(trial.config),
        logs_dir=trial.host_logs,
        model_name=None,
        logger=None,
        extra_env={},
    )

    assert agent.paths == SandboxPaths(
        adapter="/opt/suss/adapter",
        instruction="/opt/suss/instruction.md",
        params="/opt/suss/params.json",
        logs="/logs/agent",
    )
    assert agent.to_agent_info().name == SussAgent.name()


def test_a_trial_that_starts_outside_a_repository_fails_naming_the_directory(
    local_environment, trial, tmp_path
):
    elsewhere = tmp_path / "the sandbox" / "elsewhere"
    elsewhere.mkdir()
    local_environment.task_env_config.workdir = str(elsewhere)

    with pytest.raises(WorkspaceError) as raised:
        go(trial.agent().setup(environment=local_environment))

    assert str(elsewhere) in str(raised.value), "the error does not name the directory"
    assert not trial.sandbox_adapter.exists(), "setup went on past the workspace"


def test_run_before_setup_is_refused(local_environment, trial):
    with pytest.raises(RuntimeError, match="setup"):
        go(
            trial.agent().run(
                instruction=INSTRUCTION, environment=local_environment, context=AgentContext()
            )
        )


def test_files_go_into_opt_suss_as_root_and_belong_to_the_agent_user(
    local_environment, trial, monkeypatch
):
    # The stand-in cannot switch users, so each command runs as this machine's
    # user, and the test records who it was meant to run as.
    exec_as_given = local_environment.exec
    calls = []

    async def recording(command, user=None, **kwargs):
        calls.append((command, local_environment._resolve_user(user)))
        # There is no agent user here to give the files to.
        if command.startswith("chown"):
            return ExecResult(stdout=None, stderr=None, return_code=0)
        with local_environment.with_default_user(None):
            return await exec_as_given(command, **kwargs)

    monkeypatch.setattr(local_environment, "exec", recording)
    agent = trial.agent()
    with local_environment.with_default_user("agent"):
        go(agent.setup(environment=local_environment))
        go(agent.run(instruction=INSTRUCTION, environment=local_environment, context=AgentContext()))

    as_root = [command for command, user in calls if user == "root"]
    assert any("mkdir" in command and trial.paths.adapter in command for command in as_root)
    assert any("chown" in command and "agent" in command for command in as_root)
    harness = [user for command, user in calls if "install.sh" in command or "run.sh" in command]
    assert harness == ["agent", "agent"], "the harness did not run as the agent user"


# A harness mid-run with a child it backgrounded, which writes to the workspace
# after the test has cancelled run.
LATE_WRITE = "a late write"
STILL_WORKING = (
    f"{KNOWN_CHANGE}\n"
    f'(sleep 2; echo "{LATE_WRITE}" >> hello.txt) &\n'
    'echo $! $$ > "$here/run.pids"\n'
    "sleep 30"
)


def without_max_seconds(trial: Trial) -> str:
    config = trial.host / "unlimited.toml"
    config.write_text(trial.config.read_text().replace("max_seconds = 60\n", ""))
    return str(config)


def cancel_mid_run(trial: Trial, env, agent: SussAgent) -> None:
    """Cancel run once the harness has started its child, as Harbor's agent timeout does."""

    async def scenario():
        await agent.setup(environment=env)
        running = asyncio.create_task(
            agent.run(instruction=INSTRUCTION, environment=env, context=AgentContext())
        )
        pids = trial.sandbox_adapter / "run.pids"
        deadline = time.monotonic() + 10
        while not (pids.exists() and pids.read_text().strip()):
            assert time.monotonic() < deadline, "the harness never started"
            await asyncio.sleep(0.05)
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running

    go(scenario())


def is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_a_cancelled_run_writes_a_timed_out_record(local_environment, trial):
    trial.then("run", STILL_WORKING)

    cancel_mid_run(trial, local_environment, trial.agent())

    record = json.loads((trial.logs / "suss.json").read_text())
    assert set(record) == set(spec_trial_record()), "suss.json's keys are not the spec's"
    assert record["status"] == "timed-out"
    assert record["exit_codes"] == {"install": 0, "run": None}
    assert isinstance(record["run_seconds"], float)
    assert record["run_seconds"] > 0
    assert record["patch_bytes"] == len((trial.logs / "model.patch").read_bytes())


@pytest.mark.parametrize("limit", ["max_seconds", "no max_seconds"])
def test_a_cancelled_run_stops_the_harness_before_the_patch(
    local_environment, trial, tmp_path, limit
):
    trial.then("run", STILL_WORKING)
    config = str(trial.config) if limit == "max_seconds" else without_max_seconds(trial)

    cancel_mid_run(trial, local_environment, trial.agent(config=config))

    pids = [int(pid) for pid in (trial.sandbox_adapter / "run.pids").read_text().split()]
    alive = [pid for pid in pids if is_running(pid)]
    assert not alive, f"harness processes {alive} outlived the cancelled run"
    # Past the moment the child would have written.
    time.sleep(3)
    assert LATE_WRITE not in (trial.workspace / "hello.txt").read_text(), (
        "the harness went on changing the workspace after run was cancelled"
    )
    assert LATE_WRITE not in trial.patch()
    assert_patch_carries_the_known_change(trial, tmp_path)


def test_harbors_agent_timeout_still_sees_the_cancel(local_environment, trial):
    trial.then("run", STILL_WORKING)
    agent = trial.agent()

    async def under_harbors_timeout():
        await agent.setup(environment=local_environment)
        await asyncio.wait_for(
            agent.run(
                instruction=INSTRUCTION, environment=local_environment, context=AgentContext()
            ),
            timeout=1,
        )

    with pytest.raises(TimeoutError):
        go(under_harbors_timeout())
    assert json.loads((trial.logs / "suss.json").read_text())["status"] == "timed-out"


def test_a_run_that_finishes_leaves_no_process_group_behind(local_environment, trial):
    trial.then("run", KNOWN_CHANGE)

    trial.run(local_environment)

    left = sorted(path.name for path in trial.logs.iterdir())
    assert left == ["install.log", "model.patch", "run.log", "suss.json"], (
        "a finished run left a file a later cancel could act on"
    )
