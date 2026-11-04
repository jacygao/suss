"""
Run `adapters/copilot-cli-hve/` against the stand-ins `adapters/copilot-cli/`'s
tests use, with a stand-in for hve-core in place of Microsoft's.

What the two adapters share, the build chosen and checked and how params are
read, is tested there. This tests what hve-core adds, and again the rules on
tokens, which a variant must not lose.
"""

import asyncio
import json
import os
import signal
import subprocess
import tarfile
from pathlib import Path

import pytest

from suss.harness import load_manifest
from suss.workspace import capture_patch, read_start_tree
from test_copilot_cli_adapter import (
    COPILOT,
    LAST_LINE,
    RELEASE,
    TOKEN,
    box,  # noqa: F401, a fixture
    environment,
    flag_value,
    install_environment,
    installed,
    publish,
    recorded,
    run_args,
    snapshot,
    stand_in,
    stand_in_copilot,
    stand_in_downloader,
    stand_in_uname,
    wait_for,
)

ADAPTER = Path(__file__).resolve().parent.parent / "adapters" / "copilot-cli-hve"
COMMIT = "55352bb8e9f0622ff40f5c6afa80363adaf6b07f"
HVE_CORE = f"https://codeload.github.com/microsoft/hve-core/tar.gz/{COMMIT}"
AGENT_FILE = Path(".github") / "agents" / "hve-core" / "rpi-agent.agent.md"

FULL_AUTO = (
    "Use automatic mode for the current task and make ordinary Research, Plan, Review,"
    " and follow-up decisions. Continue through RPI loops, automatically selecting"
    " required in-scope review follow-ups until the requested outcome is complete."
    " This is explicit mode authorization; do not ask for mode confirmation. On resume,"
    " preserve any explicitly retained decisions or stop-before-Implementation boundary"
    " unless I explicitly change them. Required safety confirmations, blockers, and"
    " human review still apply."
)

# The frontmatter of hve-core's rpi-agent.agent.md at COMMIT, with two of its
# handoffs left out. The handoff after Full Auto also has a prompt, which must
# not be read in its place.
AGENT = f"""---
name: RPI Agent
description: "User-selected RPI workflow wrapper for Research, Plan, Implement, Review, and Follow-up."
disable-model-invocation: true
handoffs:
  - label: "Research"
    agent: RPI Agent
    prompt: /rpi-research
  - label: "Full Auto"
    agent: RPI Agent
    prompt: "{FULL_AUTO}"
    send: true
  - label: "1️⃣"
    agent: RPI Agent
    prompt: "Select the latest follow-up ranked 1 and start its automatic RPI loop from Research."
    send: true
---

# RPI Agent
"""

PARAMS = {"version": "1.0.105", "model": "claude-sonnet-5.5", "effort": "high", "hve_core_commit": COMMIT}


def write_params(box: Path, **changes) -> None:
    params = {**PARAMS, **changes}
    (box / "opt" / "params.json").write_text(
        json.dumps({key: value for key, value in params.items() if value is not None})
    )


def plugin_dir(box: Path) -> Path:
    return box / "home" / ".local" / "share" / "hve-core" / COMMIT


def plant_hve_core(root: Path, agent: str = AGENT) -> None:
    """Lay out the files of hve-core that the adapter reads under `root`."""
    (root / AGENT_FILE).parent.mkdir(parents=True)
    (root / "plugin.json").write_text('{"name": "hve-core", "version": "3.2.2"}\n')
    (root / AGENT_FILE).write_text(agent)


def publish_hve_core(box: Path, agent: str = AGENT, top: str = f"hve-core-{COMMIT}") -> None:
    """Put hve-core's tarball for COMMIT in `box/release`, laid out as GitHub's archive is."""
    source = box / "hve-source" / top
    plant_hve_core(source, agent)
    (box / "release").mkdir(exist_ok=True)
    with tarfile.open(box / "release" / COMMIT, "w:gz") as tar:
        tar.add(source, arcname=top)


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout


@pytest.fixture
def hve(box):
    """
    The box, with params naming hve-core's commit, that commit installed, and
    the workspace a repository at its base, as every task's is.
    """
    write_params(box)
    plant_hve_core(plugin_dir(box))
    workspace = box / "workspace"
    git(workspace, "init", "-q")
    git(workspace, "add", "-A")
    git(workspace, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-q", "-m", "base")
    return box


def kept(box: Path) -> Path:
    return box / "logs" / "copilot-tracking"


def hve_environment(box: Path, **extra: str) -> dict[str, str]:
    return {**environment(box), "HVE_TRACKING_DIR": str(kept(box)), **extra}


def execute(box: Path, script: str, *args: str, env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(ADAPTER / script), *args],
        cwd=box / "workspace",
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def run(box: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    return execute(box, "run.sh", *run_args(box), env=env or hve_environment(box))


def install(box: Path) -> subprocess.CompletedProcess:
    return execute(
        box,
        "install.sh",
        "--workspace",
        str(box / "workspace"),
        "--params",
        str(box / "opt" / "params.json"),
        env=install_environment(box),
    )


def test_the_manifest_loads_and_names_scripts_that_exist():
    manifest = load_manifest(ADAPTER)

    assert manifest.name == "copilot-cli-hve"
    assert manifest.allowed_hosts == ("api.github.com", "api.enterprise.githubcopilot.com")
    assert manifest.env == ("COPILOT_GITHUB_TOKEN",)
    for script in (manifest.install, manifest.run):
        assert script is not None
        assert os.access(ADAPTER / script, os.X_OK), f"{script} is not an executable file"


def test_install_installs_copilot_and_hve_core_at_its_commit_outside_the_workspace(box):
    write_params(box)
    publish(box)
    publish_hve_core(box)
    stand_in_uname(box)
    stand_in_downloader(box, "curl")
    before = snapshot(box / "workspace")

    result = install(box)

    assert result.returncode == 0, result.stderr
    assert sorted(recorded(box / "records" / "curl-urls")) == sorted(
        [f"{RELEASE}/SHA256SUMS.txt", f"{RELEASE}/copilot-linux-x64.tar.gz", HVE_CORE]
    )
    assert installed(box).read_text() == COPILOT
    assert (plugin_dir(box) / AGENT_FILE).read_text() == AGENT
    assert snapshot(box / "workspace") == before


# A branch or a tag names whatever it points at on the day of the run.
@pytest.mark.parametrize("commit", ["main", "v3.2.2", COMMIT[:12], COMMIT.upper(), None])
def test_install_refuses_anything_but_a_full_commit(box, commit):
    write_params(box, hve_core_commit=commit)
    publish(box)
    publish_hve_core(box)
    stand_in_uname(box)
    stand_in_downloader(box, "curl")

    result = install(box)

    assert result.returncode != 0
    assert '"hve_core_commit"' in result.stderr, result.stderr
    assert not (box / "home" / ".local" / "share" / "hve-core").exists()


def test_install_fails_when_the_rpi_agent_has_no_full_auto_prompt(box):
    write_params(box)
    publish(box)
    publish_hve_core(box, agent=AGENT.replace('"Full Auto"', '"Automatic"'))
    stand_in_uname(box)
    stand_in_downloader(box, "curl")

    result = install(box)

    assert result.returncode != 0
    assert "Full Auto" in result.stderr, result.stderr
    assert not plugin_dir(box).exists()


def test_install_fails_when_the_archive_is_not_of_that_commit(box):
    write_params(box)
    publish(box)
    publish_hve_core(box, top="hve-core-main")
    stand_in_uname(box)
    stand_in_downloader(box, "curl")

    result = install(box)

    assert result.returncode != 0
    assert COMMIT in result.stderr, result.stderr
    assert not plugin_dir(box).exists()


def test_run_loads_hve_core_and_gives_the_rpi_agent_full_auto_then_the_instruction(hve):
    # The prompt spans lines, which the record of arguments splits.
    prompt = hve / "records" / "prompt"
    stand_in(
        hve,
        "copilot",
        f"while [ \"$1\" != -p ]; do shift; done; printf '%s' \"$2\" > '{prompt}'\n"
        f"echo '{LAST_LINE}'\n",
    )

    result = run(hve)

    assert result.returncode == 0, result.stderr
    assert prompt.read_text() == f"{FULL_AUTO}\n\nFix the bug in main.py."
    argv = recorded(hve / "records" / "copilot")
    assert flag_value(argv, "--plugin-dir") == str(plugin_dir(hve))
    assert flag_value(argv, "--agent") == "hve-core:rpi-agent"
    assert flag_value(argv, "--model") == "claude-sonnet-5.5"
    assert flag_value(argv, "--reasoning-effort") == "high"
    assert flag_value(argv, "--output-format") == "json"
    for flag in ("--allow-all-tools", "--allow-all-paths", "--no-ask-user", "--no-auto-update",
                 "--disable-client-telemetry", "--disable-builtin-mcps"):
        assert flag in argv, f"copilot was run without {flag}"
    excluded = argv[argv.index("--excluded-tools") + 1 :]
    assert excluded[:2] == ["web_fetch", "web_search"]
    assert LAST_LINE in result.stdout


def test_run_fails_naming_the_commit_when_hve_core_is_not_installed(box):
    write_params(box)
    stand_in_copilot(box)

    result = run(box)

    assert result.returncode != 0
    assert COMMIT in result.stderr, result.stderr
    assert not (box / "records" / "copilot").exists()


def test_run_gives_copilot_only_its_own_token_and_a_fresh_home(hve):
    seen = hve / "records" / "copilot-env"
    stand_in(
        hve,
        "copilot",
        f"printf '%s\\n' \"${{COPILOT_GITHUB_TOKEN-unset}}\" \"${{GH_TOKEN-unset}}\" "
        f"\"${{GITHUB_TOKEN-unset}}\" \"${{COPILOT_MODEL-unset}}\" \"${{COPILOT_HOME-unset}}\" "
        f"\"${{GH_CONFIG_DIR-unset}}\" > '{seen}'\n"
        f"ls -A \"$COPILOT_HOME\" \"$GH_CONFIG_DIR\" >> '{seen}'\n",
    )
    stored = hve / "home" / ".copilot"
    stored.mkdir()
    (stored / "config.json").write_text('{"logged_in_users": ["someone"]}')

    result = run(
        hve,
        env=hve_environment(hve, GH_TOKEN="gh-token", GITHUB_TOKEN="github-token", COPILOT_MODEL="gpt-5.4"),
    )

    assert result.returncode == 0, result.stderr
    token, gh_token, github_token, model, home, gh_config, *listed = seen.read_text().splitlines()
    assert token == TOKEN
    assert (gh_token, github_token, model) == ("unset", "unset", "unset")
    for directory in (home, gh_config):
        assert directory != "unset"
        assert not Path(directory).is_relative_to(hve / "workspace")
        assert not Path(directory).is_relative_to(hve / "home")
    assert [line for line in listed if line and not line.endswith(":")] == [], "copilot's homes were not empty"


@pytest.mark.parametrize("token", [None, ""], ids=["unset", "empty"])
def test_run_fails_naming_the_token_when_it_is_missing(hve, token):
    stand_in_copilot(hve)
    env = hve_environment(hve)
    del env["COPILOT_GITHUB_TOKEN"]
    if token is not None:
        env["COPILOT_GITHUB_TOKEN"] = token

    result = run(hve, env=env)

    assert result.returncode != 0
    assert "COPILOT_GITHUB_TOKEN" in result.stderr
    assert not (hve / "records" / "copilot").exists()


# What RPI writes, in the shape it writes it.
WRITE_TRACKING = (
    "mkdir -p .copilot-tracking/rpi-sessions/2026-11-04/fix-main .copilot-tracking/research/2026-11-04\n"
    "printf '{\"mode\": \"automatic\"}\\n' > .copilot-tracking/rpi-sessions/2026-11-04/fix-main/fix-main-state.json\n"
    "printf '# Research\\n' > .copilot-tracking/research/2026-11-04/fix-main-research.md\n"
)


def capture(box: Path, local_environment, run_harness) -> bytes:
    """Read the starting tree, run the harness, and return the patch suss captures after it."""
    workspace = str(box / "workspace")
    start = asyncio.run(read_start_tree(local_environment, workspace))
    run_harness()
    logs = box / "logs"
    asyncio.run(capture_patch(local_environment, workspace, start, str(logs)))
    return (logs / "model.patch").read_bytes()


def test_rpis_tracking_is_kept_with_the_logs_and_out_of_the_patch(hve, local_environment):
    stand_in(hve, "copilot", WRITE_TRACKING + "printf 'print(\"fixed\")\\n' > main.py\n")

    patch = capture(hve, local_environment, lambda: run(hve))

    files = [line for line in patch.decode().splitlines() if line.startswith("diff --git")]
    assert files == ["diff --git a/main.py b/main.py"], patch.decode()
    state = kept(hve) / "rpi-sessions" / "2026-11-04" / "fix-main" / "fix-main-state.json"
    assert state.read_text() == '{"mode": "automatic"}\n'
    assert (kept(hve) / "research" / "2026-11-04" / "fix-main-research.md").exists()
    assert git(hve / "workspace", "diff", "HEAD", "--name-only") == "main.py\n", "a tracked file other than the change moved"


# suss stops run at max_seconds by killing every process it started, which
# leaves run no chance to copy anything out.
def test_rpis_tracking_is_kept_when_run_is_killed(hve):
    stand_in(hve, "copilot", WRITE_TRACKING + "exec sleep 60\n")
    state = kept(hve) / "rpi-sessions" / "2026-11-04" / "fix-main" / "fix-main-state.json"

    process = subprocess.Popen(
        [str(ADAPTER / "run.sh"), *run_args(hve)],
        cwd=hve / "workspace",
        env=hve_environment(hve),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        written = wait_for(state.exists)
    finally:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait()

    assert written, "RPI's state never reached the logs directory"
    assert state.read_text() == '{"mode": "automatic"}\n'


# A workspace that already holds it is the task's, and putting a link in its
# place would delete it from the patch.
@pytest.mark.parametrize("kind", ["directory", "file"])
def test_run_refuses_a_workspace_that_already_has_copilot_tracking(hve, kind):
    stand_in_copilot(hve)
    path = hve / "workspace" / ".copilot-tracking"
    if kind == "directory":
        path.mkdir()
    else:
        path.write_text("")

    result = run(hve)

    assert result.returncode != 0
    assert ".copilot-tracking" in result.stderr, result.stderr
    assert not (hve / "records" / "copilot").exists()


# The repository's .gitignore outranks info/exclude, so the link would be in
# the patch.
def test_run_refuses_a_workspace_whose_gitignore_keeps_copilot_tracking(hve):
    stand_in_copilot(hve)
    (hve / "workspace" / ".gitignore").write_text("!.copilot-tracking\n")

    result = run(hve)

    assert result.returncode != 0
    assert ".copilot-tracking" in result.stderr, result.stderr
    assert not (hve / "workspace" / ".copilot-tracking").is_symlink()
    assert not (hve / "records" / "copilot").exists()
