"""
Run SussCopilotCli and SussCopilotCliHve through Harbor's own run, in the
stand-in environment, with a stand-in `copilot` that records how it was
started and prints the JSON lines a test gives it.

Harbor's run writes the stream to a fixed path under /logs/agent, so each test
points the agent's paths at its own sandbox directory instead.
"""

import asyncio
import json
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest
from harbor.agents.factory import AgentFactory
from harbor.agents.installed.base import NonZeroAgentExitCodeError
from harbor.agents.installed.copilot_cli import CopilotCli
from harbor.models.agent.context import AgentContext
from harbor.models.trial.config import AgentConfig as TrialAgentConfig

from suss.agents import SussCopilotCli, SussCopilotCliHve, full_auto_prompt
from suss.config import ConfigError

TOKEN = "github_pat_from_the_env_file"
INSTRUCTION = "Fix the bug.\n"
COMMIT = "55352bb8e9f0622ff40f5c6afa80363adaf6b07f"
FULL_AUTO = "Run every phase in full auto mode."
AGENT_FILE = f"""---
name: RPI Agent
handoffs:
  - label: "Research"
    agent: rpi-research
    prompt: "Research only."
  - label: "Full Auto"
    agent: rpi-agent
    prompt: "{FULL_AUTO}"
    send: true
---
The agent.
"""


def checkpoint(nano: int, model: str = "gpt-6.1-sol", effort: str = "high") -> dict:
    return {
        "type": "session.usage_checkpoint",
        "data": {
            "totalNanoAiu": nano,
            "promptCacheBreakState": [{"models": {model: {"reasoning_effort": effort}}}],
            # As Copilot CLI 1.0.105 reports a session's tokens, per model.
            "providerModelMetrics": [
                {"modelId": model, "metrics": {"usage": {
                    "inputTokens": 400002, "outputTokens": 4489, "cacheReadTokens": 395564}}},
                {"modelId": "claude-haiku-4.5", "metrics": {"usage": {
                    "inputTokens": 40000, "outputTokens": 11, "cacheReadTokens": 0}}},
            ],
        },
    }


STREAM = [
    {"type": "user.message", "data": {"content": INSTRUCTION}},
    {"type": "model.call_start", "data": {"model": "gpt-6.1-sol"}},
    {"type": "assistant.message", "data": {"content": "Fixing it.", "outputTokens": 12}},
    checkpoint(12_340_000_000),
    {"type": "model.call_start", "data": {"model": "gpt-6.1-sol"}},
    checkpoint(18_439_490_000),
    {"type": "result", "exitCode": 0},
]

# Records its argv, prints the stream, and makes a change in the workspace.
COPILOT = """#!{python}
import json, sys
from pathlib import Path
here = Path(__file__).parent
(here / "copilot.argv").write_text(json.dumps(sys.argv[1:]))
if sys.argv[1:] == ["--version"]:
    print("GitHub Copilot CLI 1.0.105.")
    sys.exit(0)
print((here / "stream.jsonl").read_text(), end="")
with open("hello.txt", "a") as f:
    f.write("a known change\\n")
sys.exit(int((here / "exit").read_text()) if (here / "exit").exists() else 0)
"""
# macOS has no stdbuf, which Harbor's run pipes the stream through.
STDBUF = '#!/bin/sh\nshift\nexec "$@"\n'
# Saves the archive the test made wherever -o says, and records the URL.
CURL = """#!/bin/sh
here=$(dirname "$0")
while [ $# -gt 1 ]; do
    [ "$1" = -o ] && out=$2
    shift
done
echo "$1" >> "$here/curl.urls"
cp "$here/hve-core.tar.gz" "$out"
"""


def go(coroutine):
    return asyncio.run(coroutine)


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout


@pytest.fixture
def box(tmp_path, local_environment, monkeypatch):
    """suss.toml and its .env on the host, and a workspace, a home and logs in the sandbox."""
    host = tmp_path / "host"
    download = host / "tasks" / "swe-rebench-07-2026" / "owner__repo-1"
    download.mkdir(parents=True)
    (download / "task.toml").write_text("[agent]\ntimeout_sec = 100\n")
    (host / "suss.toml").write_text(
        'swe_rebench = "tasks/swe-rebench-07-2026"\nenvironment = "docker"\nk = 1\nconcurrency = 1\n'
        "max_seconds = 60\n\n"
        '[arms.copilot-gpt]\nagent = "suss.agents:SussCopilotCli"\nmodel = "gpt-6.1-sol"\n'
        'kwargs = { version = "1.0.105", reasoning_effort = "high" }\n'
    )
    (host / ".env").write_text(f"COPILOT_GITHUB_TOKEN={TOKEN}\nOTHER_KEY=not-for-copilot\n")
    workspace = tmp_path / "sandbox" / "gleam"
    workspace.mkdir(parents=True)
    git(workspace, "init", "-q")
    (workspace / "hello.txt").write_text("hello\n")
    git(workspace, "add", "-A")
    git(workspace, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "base")
    home = tmp_path / "sandbox" / "home"
    bin_dir = home / ".local" / "bin"
    bin_dir.mkdir(parents=True)
    for name, text in (("copilot", COPILOT.format(python=sys.executable)), ("stdbuf", STDBUF), ("curl", CURL)):
        (bin_dir / name).write_text(text)
        (bin_dir / name).chmod(0o755)
    write_stream(bin_dir, STREAM)
    local_environment.task_env_config.workdir = str(workspace)
    local_environment._persistent_env = {"HOME": str(home), "PATH": f"{bin_dir}:/usr/bin:/bin"}
    local_environment._resolve_user = lambda user: None if user == "root" else user
    # Harbor's install downloads from GitHub, which no test reaches.
    monkeypatch.setattr(CopilotCli, "install", _no_install)
    monkeypatch.setattr(CopilotCli, "ensure_system_dependencies", _no_install)
    return tmp_path


async def _no_install(*args, **kwargs):
    pass


def write_stream(bin_dir: Path, events: list) -> None:
    (bin_dir / "stream.jsonl").write_text("".join(json.dumps(event) + "\n" for event in events))


def bin_dir(box: Path) -> Path:
    return box / "sandbox" / "home" / ".local" / "bin"


def logs(box: Path) -> Path:
    return box / "sandbox" / "logs" / "agent"


def agent(box: Path, cls=SussCopilotCli, **kwargs):
    kwargs.setdefault("version", "1.0.105")
    found = cls(
        arm="copilot-gpt",
        lane="rebench",
        suss_config=str(box / "host" / "suss.toml"),
        logs=str(logs(box)),
        artifacts=str(box / "sandbox" / "logs" / "artifacts"),
        logs_dir=logs(box),
        model_name="gpt-6.1-sol",
        **kwargs,
    )
    found._TRAJECTORY_PATH = logs(box) / found._TRAJECTORY_FILENAME
    found._OUTPUT_PATH = logs(box) / "copilot-cli.txt"
    return found


def run(box: Path, env, the_agent=None) -> tuple[SussCopilotCli, AgentContext, list]:
    """Both phases and Harbor's post-run parse, with each exec's command and env recorded."""
    the_agent = the_agent or agent(box, reasoning_effort="high")
    seen = []
    exec_as_given = env.exec

    async def recording(command, env=None, **kwargs):
        seen.append((command, dict(env or {})))
        return await exec_as_given(command, env=env, **kwargs)

    env.exec = recording
    try:
        logs(box).mkdir(parents=True, exist_ok=True)
        go(the_agent.setup(env))
        context = AgentContext()
        try:
            go(the_agent.run(INSTRUCTION, env, context))
        finally:
            the_agent.populate_context_post_run(context)
    finally:
        del env.exec
    return the_agent, context, seen


def copilot_argv(box: Path) -> list[str]:
    return json.loads((bin_dir(box) / "copilot.argv").read_text())


def record(box: Path) -> dict:
    return json.loads((logs(box) / "suss.json").read_text())


def test_copilot_runs_with_builtin_mcps_web_tools_and_auto_update_off(box, local_environment):
    run(box, local_environment)

    argv = copilot_argv(box)
    for flag in ("--disable-builtin-mcps", "--no-auto-update", "--disable-client-telemetry",
                 "--no-ask-user", "--yolo"):
        assert flag in argv, f"copilot ran without {flag}"
    tools = argv[argv.index("--excluded-tools") + 1 : argv.index("--excluded-tools") + 3]
    assert tools == ["web_fetch", "web_search"]
    assert "--model=gpt-6.1-sol" in argv
    assert argv[argv.index("--effort") + 1] == "high"
    assert f"--prompt={INSTRUCTION}" in argv


def test_copilot_gets_the_env_files_token_alone_whatever_harbors_environment_holds(
    box, local_environment, monkeypatch
):
    for name, value in (("COPILOT_GITHUB_TOKEN", "from-harbor"), ("GH_TOKEN", "gh"), ("GITHUB_TOKEN", "gh2")):
        monkeypatch.setenv(name, value)

    _, _, seen = run(box, local_environment)

    passed = [env for command, env in seen if "copilot --prompt" in command]
    assert len(passed) == 1
    assert passed[0]["COPILOT_GITHUB_TOKEN"] == TOKEN
    assert set(passed[0]) == {"COPILOT_GITHUB_TOKEN", "SUSS_START_TOKEN"}, (
        "Copilot CLI's command was given a variable beside the arm's token"
    )
    setup = [env for command, env in seen if "copilot --version" in command]
    assert setup and "COPILOT_GITHUB_TOKEN" not in setup[0], "the setup phase was given the token"


@pytest.mark.parametrize("cls", [SussCopilotCli, SussCopilotCliHve])
def test_harbors_preflight_takes_every_kwarg_suss_run_passes(box, cls):
    kwargs = {"version": "1.0.105", "reasoning_effort": "high", "arm": "copilot-gpt",
              "lane": "rebench", "suss_config": str(box / "host" / "suss.toml")}
    if cls is SussCopilotCliHve:
        kwargs["hve_core_commit"] = COMMIT

    AgentFactory.run_preflight(TrialAgentConfig(
        import_path=f"suss.agents:{cls.__name__}", model_name="gpt-6.1-sol", kwargs=kwargs
    ))


def test_an_effort_in_harbors_environment_does_not_reach_copilot(box, local_environment, monkeypatch):
    monkeypatch.setenv("COPILOT_CLI_EFFORT", "low")

    run(box, local_environment, agent(box))

    assert "--effort" not in copilot_argv(box)


def test_a_model_that_names_a_provider_fails_before_a_sandbox_starts(box):
    with pytest.raises(ConfigError, match="names a provider"):
        SussCopilotCli(
            arm="copilot-gpt", lane="rebench", suss_config=str(box / "host" / "suss.toml"),
            logs_dir=logs(box), model_name="anthropic/claude-sonnet-5.5", version="1.0.105",
        )


def test_an_env_file_with_no_token_fails_before_a_sandbox_starts(box):
    (box / "host" / ".env").write_text("OTHER_KEY=x\n")

    with pytest.raises(ConfigError, match="COPILOT_GITHUB_TOKEN"):
        agent(box)


def test_the_credits_models_and_efforts_of_the_stream_reach_the_record_and_harbors_context(
    box, local_environment
):
    _, context, _ = run(box, local_environment)

    usage = {"ai_credits": 18.4395, "reported_models": ["gpt-6.1-sol"], "reported_efforts": ["high"]}
    assert record(box)["usage"] == usage
    assert context.metadata == usage
    assert (context.n_input_tokens, context.n_cache_tokens) == (440002, 395564)
    assert context.n_output_tokens == 12, "Harbor's own count was replaced"


def test_harbors_trajectory_is_kept(box, local_environment):
    run(box, local_environment)

    trajectory = json.loads((logs(box) / "trajectory.json").read_text())
    assert [step["message"] for step in trajectory["steps"]][-1] == "Fixing it."


def test_the_record_names_the_version_copilot_reports_and_the_kwargs_asked_for(box, local_environment):
    run(box, local_environment)

    assert record(box)["agent"]["version"] == "1.0.105"
    assert record(box)["kwargs"] == {"version": "1.0.105", "reasoning_effort": "high"}
    assert record(box)["status"] == "ok"
    assert "a known change" in (logs(box) / "model.patch").read_text()


def test_a_copilot_that_exits_non_zero_is_harness_failed_with_its_change_captured(box, local_environment):
    (bin_dir(box) / "exit").write_text("1")

    with pytest.raises(NonZeroAgentExitCodeError):
        run(box, local_environment)

    assert record(box)["status"] == "harness-failed"
    assert "a known change" in (logs(box) / "model.patch").read_text()


# hve-core


def publish(box: Path, agent_file: str = AGENT_FILE, plugin: bool = True) -> None:
    """Make the archive the stand-in curl serves, laid out as GitHub's archive of a commit."""
    root = box / "archive" / f"hve-core-{COMMIT}"
    (root / ".github" / "agents" / "hve-core").mkdir(parents=True)
    (root / ".github" / "agents" / "hve-core" / "rpi-agent.agent.md").write_text(agent_file)
    if plugin:
        (root / "plugin.json").write_text('{"name": "hve-core"}')
    with tarfile.open(bin_dir(box) / "hve-core.tar.gz", "w:gz") as archive:
        archive.add(root, arcname=root.name)


def hve(box: Path, **kwargs) -> SussCopilotCliHve:
    return agent(box, SussCopilotCliHve, hve_core_commit=COMMIT, reasoning_effort="high", **kwargs)


def plugin_dir(box: Path) -> Path:
    return box / "sandbox" / "home" / ".local" / "share" / "hve-core" / COMMIT


def test_hve_installs_the_commits_archive_and_runs_the_rpi_agent_on_the_full_auto_prompt(
    box, local_environment
):
    publish(box)

    run(box, local_environment, hve(box))

    assert (bin_dir(box) / "curl.urls").read_text() == (
        f"https://codeload.github.com/microsoft/hve-core/tar.gz/{COMMIT}\n"
    )
    assert (plugin_dir(box) / "plugin.json").exists()
    argv = copilot_argv(box)
    assert argv[argv.index("--plugin-dir") + 1] == str(plugin_dir(box))
    assert argv[argv.index("--agent") + 1] == "hve-core:rpi-agent"
    assert f"--prompt={FULL_AUTO}\n\n{INSTRUCTION}" in argv
    assert "--disable-builtin-mcps" in argv


def test_hve_keeps_rpis_files_in_the_logs_and_out_of_the_patch(box, local_environment):
    publish(box)
    copilot = bin_dir(box) / "copilot"
    copilot.write_text(copilot.read_text().replace(
        "print((here", 'Path(".copilot-tracking/plan.md").write_text("the plan\\n")\nprint((here'
    ))

    run(box, local_environment, hve(box))

    workspace = box / "sandbox" / "gleam"
    assert (workspace / ".copilot-tracking").is_symlink()
    assert (logs(box) / "copilot-tracking" / "plan.md").read_text() == "the plan\n"
    patch = (logs(box) / "model.patch").read_text()
    assert ".copilot-tracking" not in patch
    assert "a known change" in patch


def test_hve_refuses_a_workspace_that_already_has_copilot_tracking(box, local_environment):
    publish(box)
    (box / "sandbox" / "gleam" / ".copilot-tracking").mkdir()

    with pytest.raises(NonZeroAgentExitCodeError, match="already has .copilot-tracking"):
        run(box, local_environment, hve(box))

    assert not (bin_dir(box) / "copilot.argv").read_text().count("--prompt"), "copilot ran anyway"
    assert record(box)["status"] == "harness-failed"


def test_hve_refuses_a_workspace_whose_ignore_rules_keep_copilot_tracking(box, local_environment):
    publish(box)
    workspace = box / "sandbox" / "gleam"
    (workspace / ".gitignore").write_text("!.copilot-tracking\n")

    with pytest.raises(NonZeroAgentExitCodeError, match="ignore rules"):
        run(box, local_environment, hve(box))


def test_hve_install_fails_on_an_archive_with_no_plugin(box, local_environment):
    publish(box, plugin=False)

    with pytest.raises(RuntimeError, match="plugin.json"):
        go(hve(box).setup(local_environment))


@pytest.mark.parametrize("commit", ["main", COMMIT[:12], COMMIT.upper(), 55352])
def test_hve_refuses_a_commit_that_is_not_a_full_commit(box, commit):
    with pytest.raises(ValueError, match="40 lowercase hex"):
        agent(box, SussCopilotCliHve, hve_core_commit=commit)


def test_the_full_auto_prompt_is_read_from_its_own_handoff():
    assert full_auto_prompt(AGENT_FILE) == FULL_AUTO


@pytest.mark.parametrize(
    "text",
    [
        AGENT_FILE.replace('"Full Auto"', '"Manual"'),
        AGENT_FILE.replace(f'    prompt: "{FULL_AUTO}"\n', ""),
    ],
)
def test_an_agent_file_with_no_full_auto_prompt_is_refused(text):
    with pytest.raises(RuntimeError, match="Full Auto"):
        full_auto_prompt(text)
