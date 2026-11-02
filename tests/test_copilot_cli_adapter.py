"""
Run `adapters/copilot-cli/` against stand-ins for `copilot`, `curl` and the
install script, which record the arguments they receive.

PATH holds the stand-ins and the system directories only, so the scripts are
tested without `jq` or Python, as a task image may lack both.
"""

import json
import os
import signal
import subprocess
import time
from pathlib import Path

import pytest

from suss.harness import load_manifest

ADAPTER = Path(__file__).resolve().parent.parent / "adapters" / "copilot-cli"
SYSTEM_PATH = "/usr/bin:/bin"
TOKEN = "github_pat_stand_in"

# Lines in the shape of Copilot CLI's `--output-format json`, one JSON object
# each. Their fields are not read by run, only passed through.
FIRST_LINE = '{"type":"assistant.message","data":{"content":"Looking at main.py"}}'
LAST_LINE = '{"type":"result","exitCode":0}'

AUTH_FAILURE = (
    "Error: Authentication token found but could not be validated.\n\n"
    "  Failed to fetch PAT user login (401): GitHub returned: Bad credentials\n"
)


def snapshot(directory: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(directory)): path.read_bytes() if path.is_file() else b""
        for path in sorted(directory.rglob("*"))
    }


def recorded(path: Path) -> list[str]:
    """The arguments a stand-in recorded, one per line. Fails if it never ran."""
    assert path.exists(), f"{path.name}: the stand-in was never invoked"
    return path.read_text().splitlines()


@pytest.fixture
def box(tmp_path):
    """A workspace, a HOME, the files suss gives the scripts, and a directory for stand-ins."""
    root = tmp_path
    for name in ("workspace", "home", "bin", "opt", "records"):
        (root / name).mkdir()
    (root / "workspace" / "main.py").write_text("print('hello')\n")
    (root / "opt" / "instruction.md").write_text("Fix the bug in main.py.\n")
    (root / "opt" / "params.json").write_text(
        json.dumps({"version": "1.0.105", "model": "claude-sonnet-5", "effort": "high"})
    )
    return root


def stand_in(box: Path, name: str, body: str) -> None:
    """Write an executable stand-in for `name` that records its arguments first."""
    record = box / "records" / name
    script = box / "bin" / name
    script.write_text(
        "#!/bin/sh\n"
        f": > '{record}'\n"
        f"for arg in \"$@\"; do printf '%s\\n' \"$arg\" >> '{record}'; done\n" + body
    )
    script.chmod(0o755)


def stand_in_copilot(box: Path, stdout: str = LAST_LINE + "\n", exit_code: int = 0, stderr: str = "") -> None:
    (box / "copilot-stdout").write_text(stdout)
    (box / "copilot-stderr").write_text(stderr)
    stand_in(
        box,
        "copilot",
        f"cat '{box / 'copilot-stdout'}'\n"
        f"cat '{box / 'copilot-stderr'}' >&2\n"
        f"exit {exit_code}\n",
    )


def environment(box: Path, **extra: str) -> dict[str, str]:
    return {
        "PATH": f"{box / 'bin'}:{SYSTEM_PATH}",
        "HOME": str(box / "home"),
        "COPILOT_GITHUB_TOKEN": TOKEN,
        **extra,
    }


def execute(box: Path, script: str, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(ADAPTER / script), *args],
        cwd=box / "workspace",
        env=env or environment(box),
        capture_output=True,
        text=True,
        timeout=30,
    )


def run_args(box: Path) -> list[str]:
    return [
        "--workspace",
        str(box / "workspace"),
        "--instruction",
        str(box / "opt" / "instruction.md"),
        "--params",
        str(box / "opt" / "params.json"),
    ]


def run(box: Path, *extra: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    return execute(box, "run.sh", *run_args(box), *extra, env=env)


def wait_for(condition, seconds: float = 10) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.05)
    return condition()


def flag_value(argv: list[str], flag: str) -> str:
    assert flag in argv, f"copilot was run without {flag}"
    return argv[argv.index(flag) + 1]


def test_the_manifest_loads_and_names_scripts_that_exist():
    manifest = load_manifest(ADAPTER)

    assert manifest.name == "copilot-cli"
    assert manifest.allowed_hosts == ("api.github.com", "api.individual.githubcopilot.com")
    assert manifest.env == ("COPILOT_GITHUB_TOKEN",)
    for script in (manifest.install, manifest.run):
        assert script is not None
        assert os.access(ADAPTER / script, os.X_OK), f"{script} is not an executable file"


def test_the_manifest_says_the_harness_takes_no_budget_in_dollars():
    assert load_manifest(ADAPTER).max_cost_usd is False


def install(box: Path) -> subprocess.CompletedProcess:
    return execute(
        box,
        "install.sh",
        "--params",
        str(box / "opt" / "params.json"),
        "--workspace",
        str(box / "workspace"),
    )


def stand_in_curl(box: Path, exit_code: int = 0) -> None:
    """A curl that serves a stand-in install script to the file named by `-o`."""
    installer = (
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$VERSION\" \"$PREFIX\" > '{box / 'records' / 'installer'}'\n"
        # The real script downloads into its working directory.
        ": > downloaded-by-the-installer\n"
    )
    (box / "installer-script").write_text(installer)
    stand_in(
        box,
        "curl",
        'while [ $# -gt 0 ]; do [ "$1" = -o ] && out=$2; shift; done\n'
        f"cp '{box / 'installer-script'}' \"$out\"\n"
        f"exit {exit_code}\n",
    )


def test_install_runs_the_install_script_at_the_version_and_leaves_the_workspace_alone(box):
    stand_in_curl(box)
    before = snapshot(box / "workspace")

    result = install(box)

    assert result.returncode == 0, result.stderr
    curl = recorded(box / "records" / "curl")
    assert curl[:2] == ["-fsSL", "https://gh.io/copilot-install"]
    assert curl[2] == "-o"
    assert recorded(box / "records" / "installer") == ["1.0.105", str(box / "home" / ".local")]
    assert snapshot(box / "workspace") == before


def test_install_fails_when_the_install_script_cannot_be_downloaded(box):
    stand_in_curl(box, exit_code=22)

    result = install(box)

    assert result.returncode != 0
    assert not (box / "records" / "installer").exists()


def test_install_fails_without_a_version(box):
    stand_in_curl(box)
    (box / "opt" / "params.json").write_text('{"model": "claude-sonnet-5", "effort": "high"}')

    result = install(box)

    assert result.returncode != 0
    assert "version" in result.stderr


def test_run_passes_the_instruction_as_the_prompt_and_the_params_as_flags(box):
    stand_in_copilot(box)

    result = run(box)

    assert result.returncode == 0, result.stderr
    argv = recorded(box / "records" / "copilot")
    assert flag_value(argv, "-p") == "Fix the bug in main.py."
    assert flag_value(argv, "--model") == "claude-sonnet-5"
    assert flag_value(argv, "--reasoning-effort") == "high"
    assert flag_value(argv, "--output-format") == "json"
    for flag in ("--allow-all-tools", "--allow-all-paths", "--no-ask-user", "--no-auto-update",
                 "--disable-client-telemetry", "--disable-builtin-mcps"):
        assert flag in argv, f"copilot was run without {flag}"
    excluded = argv[argv.index("--excluded-tools") + 1 :]
    assert excluded[:2] == ["web_fetch", "web_search"]
    assert LAST_LINE in result.stdout


def test_run_reads_its_flags_by_name_in_any_order(box):
    stand_in_copilot(box)

    result = execute(
        box,
        "run.sh",
        "--params",
        str(box / "opt" / "params.json"),
        "--instruction",
        str(box / "opt" / "instruction.md"),
        "--workspace",
        str(box / "workspace"),
    )

    assert result.returncode == 0, result.stderr
    assert flag_value(recorded(box / "records" / "copilot"), "--model") == "claude-sonnet-5"


@pytest.mark.parametrize("budget", ["5", "0.01"])
def test_run_refuses_a_budget_in_dollars_before_running_copilot(box, budget):
    stand_in_copilot(box)

    result = run(box, "--max-cost-usd", budget)

    assert result.returncode != 0
    assert "budget in dollars" in result.stderr
    assert "max_cost_usd" in result.stderr
    assert not (box / "records" / "copilot").exists()


@pytest.mark.parametrize("token", [None, ""], ids=["unset", "empty"])
def test_run_fails_naming_the_token_when_it_is_missing(box, token):
    stand_in_copilot(box)
    env = environment(box)
    del env["COPILOT_GITHUB_TOKEN"]
    if token is not None:
        env["COPILOT_GITHUB_TOKEN"] = token

    result = run(box, env=env)

    assert result.returncode != 0
    assert "COPILOT_GITHUB_TOKEN" in result.stderr
    assert not (box / "records" / "copilot").exists()


def test_run_gives_copilot_only_its_own_token_and_a_fresh_home(box):
    # Copilot CLI falls back to GH_TOKEN, GITHUB_TOKEN, a login stored in its
    # config home, and gh's login, so each must be out of its reach.
    seen = box / "records" / "copilot-env"
    stand_in(
        box,
        "copilot",
        f"printf '%s\\n' \"${{COPILOT_GITHUB_TOKEN-unset}}\" \"${{GH_TOKEN-unset}}\" "
        f"\"${{GITHUB_TOKEN-unset}}\" \"${{COPILOT_MODEL-unset}}\" \"${{COPILOT_HOME-unset}}\" "
        f"\"${{GH_CONFIG_DIR-unset}}\" \"${{COPILOT_AUTO_UPDATE-unset}}\" > '{seen}'\n"
        "ls -A \"$COPILOT_HOME\" \"$GH_CONFIG_DIR\" >> "
        f"'{seen}'\n",
    )
    stored = box / "home" / ".copilot"
    stored.mkdir()
    (stored / "config.json").write_text('{"logged_in_users": ["someone"]}')

    result = run(
        box,
        env=environment(box, GH_TOKEN="gh-token", GITHUB_TOKEN="github-token", COPILOT_MODEL="gpt-5.4"),
    )

    assert result.returncode == 0, result.stderr
    token, gh_token, github_token, model, home, gh_config, auto_update, *listed = seen.read_text().splitlines()
    assert token == TOKEN
    assert (gh_token, github_token, model) == ("unset", "unset", "unset")
    assert auto_update == "false"
    for directory in (home, gh_config):
        assert directory != "unset"
        assert not Path(directory).is_relative_to(box / "workspace")
        assert not Path(directory).is_relative_to(box / "home")
    assert [line for line in listed if line and not line.endswith(":")] == [], "copilot's homes were not empty"


@pytest.mark.parametrize("param", ["model", "effort"])
def test_run_fails_without_a_param(box, param):
    stand_in_copilot(box)
    params = {"version": "1.0.105", "model": "claude-sonnet-5", "effort": "high"}
    del params[param]
    (box / "opt" / "params.json").write_text(json.dumps(params))

    result = run(box)

    assert result.returncode != 0
    assert param in result.stderr
    assert not (box / "records" / "copilot").exists()


@pytest.mark.parametrize("code", [1, 3])
def test_run_keeps_copilots_own_exit_code(box, code):
    stand_in_copilot(box, stdout="", exit_code=code)

    assert run(box).returncode == code


def test_run_fails_when_copilot_cannot_authenticate(box):
    # What Copilot CLI 1.0.105 printed, and its exit code, for a token GitHub refused.
    stand_in_copilot(box, stdout="", exit_code=1, stderr=AUTH_FAILURE)

    result = run(box)

    assert result.returncode != 0, "a run that never authenticated must not read as ok"
    assert "Bad credentials" in result.stderr


def test_run_leaves_only_copilots_own_change_in_the_workspace(box):
    # The stand-in writes where Copilot CLI keeps its state and logs, and edits
    # one file, as the agent would.
    stand_in(
        box,
        "copilot",
        'while [ $# -gt 0 ]; do [ "$1" = --log-dir ] && logs=$2; shift; done\n'
        'mkdir -p "$logs" "$COPILOT_HOME/session-state" "$GH_CONFIG_DIR"\n'
        ': > "$logs/process.log"\n'
        ': > "$COPILOT_HOME/config.json"\n'
        ': > "$COPILOT_HOME/session-state/workspace.yaml"\n'
        "printf 'print(\"fixed\")\\n' > main.py\n",
    )
    before = snapshot(box / "workspace")

    result = run(box)

    assert result.returncode == 0, result.stderr
    after = snapshot(box / "workspace")
    assert after == {**before, "main.py": b'print("fixed")\n'}


def test_run_streams_copilots_output_as_it_arrives_and_keeps_it_when_killed(box):
    # suss stops run at max_seconds by killing its process group, which leaves
    # run no chance to write out anything it held back.
    stand_in(
        box,
        "copilot",
        f"printf '%s\\n' '{FIRST_LINE}'\n"
        "sleep 0.3\n"
        f"printf '%s\\n' '{LAST_LINE}'\n"
        "exec sleep 60\n",
    )
    log = box / "run.log"

    with open(log, "wb") as out:
        process = subprocess.Popen(
            [str(ADAPTER / "run.sh"), *run_args(box)],
            cwd=box / "workspace",
            env=environment(box),
            stdout=out,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    try:
        first = wait_for(lambda: FIRST_LINE in log.read_text())
        both = wait_for(lambda: LAST_LINE in log.read_text())
    finally:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait()

    assert first and both, "copilot's lines had not reached run's output while copilot was still running"
    assert log.read_text() == f"{FIRST_LINE}\n{LAST_LINE}\n"
