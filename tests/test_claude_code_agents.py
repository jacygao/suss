"""
Run SussClaudeCode through Harbor's own run, in the stand-in environment, with
a stand-in `claude` that records how it was started.
"""

import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest
from harbor.agents.factory import AgentFactory
from harbor.agents.installed.claude_code import ClaudeCode
from harbor.models.agent.context import AgentContext
from harbor.models.trial.config import AgentConfig as TrialAgentConfig

from suss.agents import SussClaudeCode
from suss.config import ConfigError

TOKEN = "sk-ant-oat01-from-the-env-file"
MODEL = "claude-sonnet-5-5"
INSTRUCTION = "Fix the bug.\n"

# Records its argv, makes a change in the workspace, and prints a result event.
CLAUDE = """#!{python}
import json, sys
from pathlib import Path
here = Path(__file__).parent
if sys.argv[1:] == ["--version"]:
    print("2.1.311 (Claude Code)")
    sys.exit(0)
(here / "claude.argv").write_text(json.dumps(sys.argv[1:]))
with open("hello.txt", "a") as f:
    f.write("a known change\\n")
print(json.dumps({{"type": "result", "subtype": "success", "is_error": False}}))
"""


def go(coroutine):
    return asyncio.run(coroutine)


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout


async def _no_install(*args, **kwargs):
    pass


@pytest.fixture
def box(tmp_path, local_environment, monkeypatch):
    """suss.toml and its .env on the host, and a workspace, a home and logs in the sandbox."""
    host = tmp_path / "host"
    download = host / "tasks" / "swe-rebench-07-2026" / "owner__repo-1"
    download.mkdir(parents=True)
    (download / "task.toml").write_text("[agent]\ntimeout_sec = 100\n")
    (host / "suss.toml").write_text(
        'swe_rebench = "tasks/swe-rebench-07-2026"\nenvironment = "docker"\nk = 1\nconcurrency = 1\n'
        "\n"
        f'[arms.claude-sonnet]\nagent = "suss.agents:SussClaudeCode"\nmodel = "{MODEL}"\n'
        'kwargs = { version = "2.1.311", reasoning_effort = "high" }\n'
    )
    (host / ".env").write_text(f"CLAUDE_CODE_OAUTH_TOKEN={TOKEN}\nOTHER_KEY=not-for-claude\n")
    workspace = tmp_path / "sandbox" / "gleam"
    workspace.mkdir(parents=True)
    git(workspace, "init", "-q")
    (workspace / "hello.txt").write_text("hello\n")
    git(workspace, "add", "-A")
    git(workspace, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "base")
    bin_dir = tmp_path / "sandbox" / "home" / ".local" / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "claude").write_text(CLAUDE.format(python=sys.executable))
    (bin_dir / "claude").chmod(0o755)
    local_environment.task_env_config.workdir = str(workspace)
    local_environment._persistent_env = {"HOME": str(bin_dir.parent.parent), "PATH": f"{bin_dir}:/usr/bin:/bin"}
    local_environment._resolve_user = lambda user: None if user == "root" else user
    # Harbor's install downloads Claude Code, which no test reaches.
    monkeypatch.setattr(ClaudeCode, "install", _no_install)
    return tmp_path


def logs(box: Path) -> Path:
    return box / "sandbox" / "logs" / "agent"


def agent(box: Path, **kwargs) -> SussClaudeCode:
    kwargs.setdefault("version", "2.1.311")
    kwargs.setdefault("model_name", MODEL)
    return SussClaudeCode(
        arm="claude-sonnet",
        lane="rebench",
        suss_config=str(box / "host" / "suss.toml"),
        logs=str(logs(box)),
        artifacts=str(box / "sandbox" / "logs" / "artifacts"),
        logs_dir=logs(box),
        environment_logs_dir=logs(box),
        **kwargs,
    )


def run(box: Path, env) -> list:
    """Both phases, with each exec's command and env recorded."""
    the_agent = agent(box, reasoning_effort="high")
    seen = []
    exec_as_given = env.exec

    async def recording(command, env=None, **kwargs):
        seen.append((command, dict(env or {})))
        return await exec_as_given(command, env=env, **kwargs)

    env.exec = recording
    try:
        logs(box).mkdir(parents=True, exist_ok=True)
        go(the_agent.setup(env))
        go(the_agent.run(INSTRUCTION, env, AgentContext()))
    finally:
        del env.exec
    return seen


def test_claude_gets_the_env_files_token_alone_whatever_harbors_environment_holds(
    box, local_environment, monkeypatch
):
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN"):
        monkeypatch.setenv(name, f"{name.lower()}-from-harbor")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://gateway.example")
    monkeypatch.setenv("CLAUDE_CODE_MAX_OUTPUT_TOKENS", "1000")

    seen = run(box, local_environment)

    passed = [env for command, env in seen if "| claude " in command]
    assert len(passed) == 1
    assert passed[0]["CLAUDE_CODE_OAUTH_TOKEN"] == TOKEN
    leaked = {name for name in passed[0] if name.startswith("ANTHROPIC_") and name != "ANTHROPIC_MODEL"}
    assert not leaked, f"Claude Code's command was given {leaked}, which would bill the API"
    assert "CLAUDE_CODE_MAX_OUTPUT_TOKENS" not in passed[0]
    setup = [env for command, env in seen if "claude --version" in command]
    assert setup and all("CLAUDE_CODE_OAUTH_TOKEN" not in env for env in setup), (
        "the setup phase was given the token"
    )


def test_claude_runs_the_arms_model_and_effort_and_leaves_its_patch(box, local_environment):
    seen = run(box, local_environment)

    passed = [env for command, env in seen if "| claude " in command]
    assert passed[0]["ANTHROPIC_MODEL"] == MODEL
    argv = json.loads((box / "sandbox" / "home" / ".local" / "bin" / "claude.argv").read_text())
    assert argv[argv.index("--effort") + 1] == "high"
    record = json.loads((logs(box) / "suss.json").read_text())
    assert record["status"] == "ok"
    assert record["agent"]["version"] == "2.1.311"
    assert "a known change" in (logs(box) / "model.patch").read_text()


def test_harbors_preflight_takes_every_kwarg_suss_run_passes(box):
    kwargs = {"version": "2.1.311", "reasoning_effort": "high", "arm": "claude-sonnet",
              "lane": "rebench", "suss_config": str(box / "host" / "suss.toml")}

    AgentFactory.run_preflight(TrialAgentConfig(
        import_path="suss.agents:SussClaudeCode", model_name=MODEL, kwargs=kwargs
    ))


def test_an_env_file_with_no_token_fails_before_a_sandbox_starts(box, monkeypatch):
    (box / "host" / ".env").write_text("ANTHROPIC_API_KEY=x\n")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "from-harbor")

    with pytest.raises(ConfigError, match="CLAUDE_CODE_OAUTH_TOKEN"):
        agent(box)


def test_a_model_that_names_a_provider_fails_before_a_sandbox_starts(box):
    with pytest.raises(ConfigError, match="names a provider"):
        agent(box, model_name=f"anthropic/{MODEL}")
