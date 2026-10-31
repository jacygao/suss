"""
Run whole trials through SussAgent, in the stand-in environment, with the fake
adapter in `tests/fake_adapter/` as the harness.

The host holds suss.toml, its .env, a git checkout standing in for
swe_bench_pro, and a copy of the fake adapter. The sandbox is a directory
beside it, with its workspace, its /opt/suss and its logs directory as
siblings, so nothing suss writes to /opt/suss can fall inside the workspace.
"""

import asyncio
import dataclasses
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from harbor.environments.base import ExecResult
from harbor.models.agent.context import AgentContext

from suss.agent import SussAgent
from suss.harness import SandboxPaths

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
    grader_commit: str
    workspace: Path
    base: str
    paths: SandboxPaths
    host_logs: Path

    @property
    def logs(self) -> Path:
        return Path(self.paths.logs)

    @property
    def sandbox_adapter(self) -> Path:
        return Path(self.paths.adapter)

    def then(self, command: str, script: str) -> None:
        """Have the fake `command` run `script` once it has recorded how it was started."""
        (self.adapter / f"{command}.then").write_text(script + "\n")

    def agent(self, arm: str = "fake", config: str | None = None) -> SussAgent:
        return SussAgent(
            arm=arm,
            config=config if config is not None else str(self.config),
            logs_dir=self.host_logs,
            paths=self.paths,
            workspaces=(str(self.workspace),),
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
def trial(tmp_path) -> Trial:
    host = tmp_path / "the host"
    grader_commit = init_repo(host / "SWE-bench_Pro-os", {"README": "the grader\n"})
    adapter = host / "adapters" / "fake"
    shutil.copytree(FAKE_ADAPTER, adapter)
    config = host / "suss.toml"
    config.write_text(
        'swe_bench_pro = "SWE-bench_Pro-os"\n'
        'dataset = "swe-bench-pro-v2"\n'
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

    sandbox = tmp_path / "the sandbox"
    workspace = sandbox / "app"
    base = init_repo(workspace, {"hello.txt": "hello\n"})
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
        grader_commit=grader_commit,
        workspace=workspace,
        base=base,
        paths=paths,
        host_logs=tmp_path / "harbor trial" / "agent",
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
    assert record["grader"] == {"dataset": "swe-bench-pro-v2", "commit": trial.grader_commit}
    assert record["base"] == trial.base
    assert record["status"] == "ok"
    assert record["exit_codes"] == {"install": 0, "run": 0}
    assert isinstance(record["run_seconds"], (int, float))
    assert record["run_seconds"] >= 0
    assert isinstance(record["patch_bytes"], int)


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


def test_config_is_read_relative_to_the_current_directory(
    local_environment, trial, monkeypatch
):
    monkeypatch.chdir(trial.host)

    record = trial.run(local_environment, trial.agent(config="suss.toml"))

    assert record["grader"]["commit"] == trial.grader_commit


@pytest.mark.parametrize("key, value", [("arm", 2026), ("arm", True), ("config", 1.5)])
def test_an_ak_value_harbor_parsed_as_json_is_refused_by_name(trial, key, value):
    given = {"arm": "fake", "config": str(trial.config), key: value}

    with pytest.raises(TypeError) as raised:
        SussAgent(**given, logs_dir=trial.host_logs)

    assert key in str(raised.value)
    assert repr(value) in str(raised.value)


def test_harbors_own_construction_keeps_the_specs_sandbox_paths(trial):
    # Harbor passes these keywords and no others.
    agent = SussAgent(
        arm="fake",
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
    assert agent.workspaces == ("/app", "/testbed")
    assert agent.to_agent_info().name == SussAgent.name()


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
