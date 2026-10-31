"""
Run `adapters/claude-code/` against stand-ins for `claude`, `curl` and the
native installer, which record the arguments they receive.

PATH holds the stand-ins and the system directories only, so the scripts are
tested without `jq` or Python, as a task image may lack both.
"""

import json
import os
import subprocess
from pathlib import Path

import pytest

from suss.harness import load_manifest

ADAPTER = Path(__file__).resolve().parent.parent / "adapters" / "claude-code"
SYSTEM_PATH = "/usr/bin:/bin"

BUDGET_STOP = (
    '{"type":"result","subtype":"error_max_budget_usd","is_error":true,'
    '"errors":["Reached maximum budget ($0.01)"],"total_cost_usd":0.153664}'
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
        json.dumps({"version": "2.1.293", "model": "claude-sonnet-5", "effort": "high"})
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


def stand_in_claude(box: Path, stdout: str, exit_code: int) -> None:
    (box / "claude-stdout").write_text(stdout)
    stand_in(
        box,
        "claude",
        f"cat > '{box / 'records' / 'claude-stdin'}'\n"
        f"cat '{box / 'claude-stdout'}'\n"
        f"exit {exit_code}\n",
    )


def execute(box: Path, script: str, *args: str) -> subprocess.CompletedProcess:
    env = {"PATH": f"{box / 'bin'}:{SYSTEM_PATH}", "HOME": str(box / "home")}
    return subprocess.run(
        [str(ADAPTER / script), *args],
        cwd=box / "workspace",
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def run(box: Path, *extra: str) -> subprocess.CompletedProcess:
    return execute(
        box,
        "run.sh",
        "--workspace",
        str(box / "workspace"),
        "--instruction",
        str(box / "opt" / "instruction.md"),
        "--params",
        str(box / "opt" / "params.json"),
        *extra,
    )


EXPECTED_CLAUDE_ARGV = [
    "-p",
    "--output-format",
    "json",
    "--model",
    "claude-sonnet-5",
    "--effort",
    "high",
    "--dangerously-skip-permissions",
    "--disallowedTools",
    "WebSearch",
    "WebFetch",
]


def test_the_manifest_loads_and_names_scripts_that_exist():
    manifest = load_manifest(ADAPTER)

    assert manifest.name == "claude-code"
    assert manifest.allowed_hosts == ("api.anthropic.com",)
    assert manifest.env == ("ANTHROPIC_API_KEY",)
    for script in (manifest.install, manifest.run):
        assert script is not None
        assert os.access(ADAPTER / script, os.X_OK), f"{script} is not an executable file"


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
    """A curl that serves a stand-in native installer to the file named by `-o`."""
    installer = (
        "#!/bin/sh\n"
        f": > '{box / 'records' / 'installer'}'\n"
        f"for arg in \"$@\"; do printf '%s\\n' \"$arg\" >> '{box / 'records' / 'installer'}'; done\n"
        # The real installer downloads into its working directory.
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


def test_install_runs_the_native_installer_at_the_version_and_leaves_the_workspace_alone(box):
    stand_in_curl(box)
    before = snapshot(box / "workspace")

    result = install(box)

    assert result.returncode == 0, result.stderr
    curl = recorded(box / "records" / "curl")
    assert curl[:2] == ["-fsSL", "https://claude.ai/install.sh"]
    assert curl[2] == "-o"
    assert recorded(box / "records" / "installer") == ["2.1.293"]
    assert snapshot(box / "workspace") == before


def test_install_fails_when_the_installer_cannot_be_downloaded(box):
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


def test_run_passes_the_instruction_on_stdin_and_the_params_as_flags(box):
    stand_in_claude(box, '{"type":"result","subtype":"success","is_error":false}', 0)

    result = run(box)

    assert result.returncode == 0, result.stderr
    assert recorded(box / "records" / "claude") == EXPECTED_CLAUDE_ARGV
    assert (box / "records" / "claude-stdin").read_text() == "Fix the bug in main.py.\n"
    assert '"subtype":"success"' in result.stdout


def test_run_reads_its_flags_by_name_in_any_order(box):
    stand_in_claude(box, '{"type":"result","subtype":"success"}', 0)

    result = execute(
        box,
        "run.sh",
        "--max-cost-usd",
        "2.5",
        "--params",
        str(box / "opt" / "params.json"),
        "--instruction",
        str(box / "opt" / "instruction.md"),
        "--workspace",
        str(box / "workspace"),
    )

    assert result.returncode == 0, result.stderr
    assert recorded(box / "records" / "claude") == [*EXPECTED_CLAUDE_ARGV, "--max-budget-usd", "2.5"]


def test_run_passes_the_budget_only_when_it_is_given(box):
    stand_in_claude(box, '{"type":"result","subtype":"success"}', 0)

    run(box, "--max-cost-usd", "0.75")
    with_budget = recorded(box / "records" / "claude")
    run(box)
    without_budget = recorded(box / "records" / "claude")

    assert with_budget == [*EXPECTED_CLAUDE_ARGV, "--max-budget-usd", "0.75"]
    assert "--max-budget-usd" not in without_budget
    assert without_budget == EXPECTED_CLAUDE_ARGV


def test_run_fails_without_a_model(box):
    stand_in_claude(box, '{"type":"result","subtype":"success"}', 0)
    (box / "opt" / "params.json").write_text('{"version": "2.1.293", "effort": "high"}')

    result = run(box)

    assert result.returncode != 0
    assert "model" in result.stderr
    assert not (box / "records" / "claude").exists()


@pytest.mark.parametrize(
    "stdout",
    [
        BUDGET_STOP,
        BUDGET_STOP.replace('":', '": ').replace(",", ", "),
        json.dumps(json.loads(BUDGET_STOP), indent=2),
    ],
    ids=["compact", "spaced", "indented"],
)
def test_run_exits_0_when_claude_stops_at_its_budget(box, stdout):
    stand_in_claude(box, stdout, 1)

    result = run(box, "--max-cost-usd", "0.01")

    assert result.returncode == 0, "a budget stop is not a harness failure"
    recorded(box / "records" / "claude")


@pytest.mark.parametrize(
    "stdout",
    [
        '{"type":"result","subtype":"error_during_execution","is_error":true}',
        "",
        "Error: Invalid API key\n",
        # The budget subtype appears only inside the model's own result text.
        json.dumps(
            {
                "type": "result",
                "subtype": "error_during_execution",
                "result": 'I saw {"subtype":"error_max_budget_usd"} in the logs',
            }
        ),
    ],
    ids=["other-error", "no-output", "text", "subtype-inside-a-string"],
)
def test_run_fails_when_claude_fails_for_any_other_reason(box, stdout):
    stand_in_claude(box, stdout, 1)

    result = run(box, "--max-cost-usd", "0.01")

    assert result.returncode != 0, "a crash must not pass for a budget stop"
    recorded(box / "records" / "claude")


def test_run_keeps_claudes_own_exit_code(box):
    stand_in_claude(box, "", 3)

    assert run(box).returncode == 3


def test_run_writes_nothing_inside_the_workspace(box):
    stand_in_claude(box, BUDGET_STOP, 1)
    before = snapshot(box / "workspace")

    run(box, "--max-cost-usd", "0.01")

    recorded(box / "records" / "claude")
    assert snapshot(box / "workspace") == before
