"""
Run `adapters/copilot-cli/` against stand-ins for `copilot`, `curl`, `wget`
and `uname`, which record the arguments they receive.

PATH holds the stand-ins and the system directories only, so the scripts are
tested without `jq` or Python, as a task image may lack both.
"""

import hashlib
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


PARAMS = {"version": "1.0.105", "model": "claude-sonnet-5", "effort": "high"}


def write_params(box: Path, **changes) -> None:
    """Write params.json as the fixture's, with each change made, and a None removing its key."""
    params = {**PARAMS, **changes}
    (box / "opt" / "params.json").write_text(
        json.dumps({key: value for key, value in params.items() if value is not None})
    )


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
    assert manifest.allowed_hosts == ("api.github.com", "api.enterprise.githubcopilot.com")
    assert manifest.env == ("COPILOT_GITHUB_TOKEN",)
    for script in (manifest.install, manifest.run):
        assert script is not None
        assert os.access(ADAPTER / script, os.X_OK), f"{script} is not an executable file"


def install(box: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    return execute(
        box,
        "install.sh",
        "--params",
        str(box / "opt" / "params.json"),
        "--workspace",
        str(box / "workspace"),
        env=env or install_environment(box),
    )


RELEASE = "https://github.com/github/copilot-cli/releases/download/v1.0.105"
COPILOT = "#!/bin/sh\necho 'GitHub Copilot CLI 1.0.105'\n"


def install_environment(box: Path) -> dict[str, str]:
    """
    The stand-ins, then the system's tools without `curl` or `wget`, so each
    test stands in exactly the downloaders it means the image to have.
    """
    tools = box / "tools"
    if not tools.exists():
        tools.mkdir()
        for directory in ("/usr/bin", "/bin", "/usr/sbin", "/sbin"):
            for tool in Path(directory).iterdir():
                if tool.name not in ("curl", "wget") and not (tools / tool.name).exists():
                    (tools / tool.name).symlink_to(tool)
        # macOS before 15 has only shasum, which prints the same line.
        if not (tools / "sha256sum").exists():
            (tools / "sha256sum").write_text('#!/bin/sh\nexec shasum -a 256 "$@"\n')
            (tools / "sha256sum").chmod(0o755)
    return {**environment(box), "PATH": f"{box / 'bin'}:{tools}"}


def publish(box: Path, build: str = "copilot-linux-x64.tar.gz", listed: str | None = None) -> Path:
    """
    Put a release in `box/release`: the build, a tarball holding a `copilot`
    that prints its version, and SHA256SUMS.txt, which gives the build's true
    sum under the name `listed`, the build's own by default.
    """
    release = box / "release"
    release.mkdir(exist_ok=True)
    staging = box / "staging"
    staging.mkdir(exist_ok=True)
    (staging / "copilot").write_text(COPILOT)
    (staging / "copilot").chmod(0o755)
    subprocess.run(["tar", "-czf", str(release / build), "-C", str(staging), "copilot"], check=True)
    digest = hashlib.sha256((release / build).read_bytes()).hexdigest()
    (release / "SHA256SUMS.txt").write_text(
        f"{'0' * 64}  copilot-win32-x64.zip\n{digest}  {listed or build}\n"
    )
    return release


def stand_in_uname(box: Path, machine: str = "x86_64") -> None:
    stand_in(box, "uname", f"echo {machine}\n")


def stand_in_downloader(box: Path, name: str, exit_code: int = 0) -> None:
    """
    A `curl` or `wget` that serves the file of `box/release` named by the URL's
    last part to the file named by `-o` or `-O`, and logs each URL to
    `records/<name>-urls`. It refuses any option busybox `wget` lacks, since
    Alpine's `wget` is busybox's.
    """
    known = "-fsSL" if name == "curl" else "-q"
    urls = box / "records" / f"{name}-urls"
    stand_in(
        box,
        name,
        "while [ $# -gt 0 ]; do\n"
        "  case $1 in\n"
        "    -o|-O) out=$2; shift ;;\n"
        f"    {known}) ;;\n"
        '    -*) echo "unknown option $1" >&2; exit 2 ;;\n'
        f"    *) url=$1; echo \"$1\" >> '{urls}' ;;\n"
        "  esac\n"
        "  shift\n"
        "done\n"
        f"[ {exit_code} = 0 ] || exit {exit_code}\n"
        f"cp '{box / 'release'}'/\"${{url##*/}}\" \"$out\" || exit 8\n",
    )


def installed(box: Path) -> Path:
    return box / "home" / ".local" / "bin" / "copilot"


@pytest.mark.parametrize("downloader", ["curl", "wget"])
def test_install_unpacks_the_checked_build_and_leaves_the_workspace_alone(box, downloader):
    publish(box)
    stand_in_uname(box)
    stand_in_downloader(box, downloader)
    before = snapshot(box / "workspace")

    result = install(box)

    assert result.returncode == 0, result.stderr
    assert sorted(recorded(box / "records" / f"{downloader}-urls")) == [
        f"{RELEASE}/SHA256SUMS.txt",
        f"{RELEASE}/copilot-linux-x64.tar.gz",
    ]
    assert installed(box).read_text() == COPILOT
    assert os.access(installed(box), os.X_OK)
    assert snapshot(box / "workspace") == before


def test_install_prefers_curl_when_both_are_there(box):
    publish(box)
    stand_in_uname(box)
    stand_in_downloader(box, "curl")
    stand_in_downloader(box, "wget")

    result = install(box)

    assert result.returncode == 0, result.stderr
    assert (box / "records" / "curl").exists()
    assert not (box / "records" / "wget").exists()


def test_install_fails_naming_curl_and_wget_when_the_image_has_neither(box):
    publish(box)
    stand_in_uname(box)

    result = install(box)

    assert result.returncode != 0
    assert "curl" in result.stderr and "wget" in result.stderr
    assert not installed(box).exists()


def build_for(box: Path, root: Path, machine: str) -> subprocess.CompletedProcess:
    """lib.sh's choice of build for a filesystem rooted at `root` and a `uname -m` of `machine`."""
    return subprocess.run(
        ["sh", "-c", f'. "{ADAPTER / "lib.sh"}" && copilot_build "$1" "$2"', "sh", str(root), machine],
        env=install_environment(box),
        capture_output=True,
        text=True,
    )


# The glibc build on musl installs cleanly, and `run` then exits 127 with
# `fcntl64: symbol not found`.
@pytest.mark.parametrize(
    "loader, machine, build",
    [
        ("ld-musl-x86_64.so.1", "x86_64", "copilot-linuxmusl-x64.tar.gz"),
        ("ld-musl-aarch64.so.1", "aarch64", "copilot-linuxmusl-arm64.tar.gz"),
        ("ld-linux-x86-64.so.2", "x86_64", "copilot-linux-x64.tar.gz"),
        ("ld-linux-x86-64.so.2", "amd64", "copilot-linux-x64.tar.gz"),
        ("ld-linux-aarch64.so.1", "aarch64", "copilot-linux-arm64.tar.gz"),
        ("ld-linux-aarch64.so.1", "arm64", "copilot-linux-arm64.tar.gz"),
    ],
)
def test_install_chooses_the_musl_build_only_where_musls_loader_is_111(box, loader, machine, build):
    root = box / "root"
    (root / "lib").mkdir(parents=True)
    (root / "lib" / loader).write_text("")

    result = build_for(box, root, machine)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == build


def test_install_downloads_the_build_uname_names(box):
    publish(box, build="copilot-linux-arm64.tar.gz")
    stand_in_uname(box, "aarch64")
    stand_in_downloader(box, "curl")

    result = install(box)

    assert result.returncode == 0, result.stderr
    assert f"{RELEASE}/copilot-linux-arm64.tar.gz" in recorded(box / "records" / "curl-urls")
    assert installed(box).read_text() == COPILOT


def test_install_fails_naming_a_machine_copilot_has_no_build_for(box):
    publish(box)
    stand_in_uname(box, "riscv64")
    stand_in_downloader(box, "curl")

    result = install(box)

    assert result.returncode != 0
    assert "riscv64" in result.stderr
    assert not installed(box).exists()


def test_install_refuses_a_build_whose_sum_does_not_match(box):
    release = publish(box)
    # A tarball that unpacks, so only the check can refuse it.
    (box / "staging" / "copilot").write_text("#!/bin/sh\necho tampered\n")
    subprocess.run(
        ["tar", "-czf", str(release / "copilot-linux-x64.tar.gz"), "-C", str(box / "staging"), "copilot"],
        check=True,
    )
    stand_in_uname(box)
    stand_in_downloader(box, "curl")

    result = install(box)

    assert result.returncode != 0
    assert "copilot-linux-x64.tar.gz does not match" in result.stderr
    assert not installed(box).exists()


# A name the build's only contains, or one that contains the build's, gives no sum for it.
@pytest.mark.parametrize(
    "listed", ["copilot-linuxmusl-x64.tar.gz", "copilot-linux-x64.tar.gz.sig", "linux-x64.tar.gz"]
)
def test_install_refuses_a_build_missing_from_the_sums(box, listed):
    publish(box, listed=listed)
    stand_in_uname(box)
    stand_in_downloader(box, "curl")

    result = install(box)

    assert result.returncode != 0
    assert "SHA256SUMS.txt" in result.stderr
    assert not installed(box).exists()


@pytest.mark.parametrize("downloader", ["curl", "wget"])
def test_install_fails_when_a_download_fails(box, downloader):
    publish(box)
    stand_in_uname(box)
    stand_in_downloader(box, downloader, exit_code=22)

    result = install(box)

    assert result.returncode != 0
    assert f"cannot download {RELEASE}/SHA256SUMS.txt" in result.stderr
    assert not installed(box).exists()


def test_install_fails_without_a_version(box):
    publish(box)
    stand_in_uname(box)
    stand_in_downloader(box, "curl")
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


def test_run_refuses_a_budget_given_as_an_argument(box):
    stand_in_copilot(box)

    result = run(box, "--max-cost-usd", "5")

    assert result.returncode != 0
    assert "unknown argument --max-cost-usd" in result.stderr
    assert not (box / "records" / "copilot").exists()


@pytest.mark.parametrize("credits, given", [(30, "30"), (30.0, "30.0"), (250, "250"), (45.5, "45.5")])
def test_run_passes_max_ai_credits_from_the_params_as_its_limit(box, credits, given):
    stand_in_copilot(box)
    write_params(box, max_ai_credits=credits)

    result = run(box)

    assert result.returncode == 0, result.stderr
    assert flag_value(recorded(box / "records" / "copilot"), "--max-ai-credits") == given


def test_run_passes_no_limit_when_the_params_set_none(box):
    stand_in_copilot(box)

    result = run(box)

    assert result.returncode == 0, result.stderr
    assert "--max-ai-credits" not in recorded(box / "records" / "copilot")


# Copilot CLI refuses a limit under 30.
@pytest.mark.parametrize("credits", [29, 29.99, 0, 5])
def test_run_refuses_max_ai_credits_under_30_before_running_copilot(box, credits):
    stand_in_copilot(box)
    write_params(box, max_ai_credits=credits)

    result = run(box)

    assert result.returncode != 0
    assert '"max_ai_credits"' in result.stderr
    assert "30" in result.stderr
    assert not (box / "records" / "copilot").exists()


@pytest.mark.parametrize(
    "credits", ["50", True, -40, None, [50]], ids=["string", "bool", "negative", "null", "list"]
)
def test_run_refuses_max_ai_credits_that_is_not_a_number(box, credits):
    stand_in_copilot(box)
    (box / "opt" / "params.json").write_text(json.dumps({**PARAMS, "max_ai_credits": credits}))

    result = run(box)

    assert result.returncode != 0
    assert '"max_ai_credits"' in result.stderr
    assert not (box / "records" / "copilot").exists()


# Copilot CLI refuses a model that takes no reasoning effort, such as
# claude-haiku-4.5, when --reasoning-effort is passed.
def test_run_passes_no_reasoning_effort_when_the_params_set_none(box):
    stand_in_copilot(box)
    write_params(box, effort=None, model="claude-haiku-4.5")

    result = run(box)

    assert result.returncode == 0, result.stderr
    argv = recorded(box / "records" / "copilot")
    assert "--reasoning-effort" not in argv
    assert flag_value(argv, "--model") == "claude-haiku-4.5"


@pytest.mark.parametrize("effort", [3, "", True], ids=["number", "empty", "bool"])
def test_run_refuses_an_effort_that_is_not_a_string(box, effort):
    stand_in_copilot(box)
    write_params(box, effort=effort)

    result = run(box)

    assert result.returncode != 0
    assert '"effort"' in result.stderr
    assert not (box / "records" / "copilot").exists()


# A key inside another's object must not be read as the arm's own.
@pytest.mark.parametrize(
    "changes",
    [
        {"max_ai_credits": {"max_ai_credits": 40}},
        {"effort": {"effort": "high"}},
        {"effort": None, "extra": {"effort": "high"}},
        {"extra": [{"max_ai_credits": 40}]},
    ],
    ids=["limit-in-itself", "effort-in-itself", "effort-in-another", "limit-in-a-list"],
)
def test_run_refuses_params_holding_an_object(box, changes):
    stand_in_copilot(box)
    write_params(box, **changes)

    result = run(box)

    assert result.returncode != 0
    assert "flat object" in result.stderr
    assert not (box / "records" / "copilot").exists()


# A brace inside a string is text, not an object.
def test_run_reads_params_whose_strings_hold_braces(box):
    stand_in_copilot(box)
    write_params(box, note='{"effort": "low"} \\ { "q\\"" }')

    result = run(box)

    assert result.returncode == 0, result.stderr


# A key whose name begins with another's must not be read as that one.
@pytest.mark.parametrize(
    "params, effort, credits",
    [
        ({"effort_x": "low"}, None, None),
        ({"effort_x": "low", "effort": "high"}, "high", None),
        ({"effort": "high", "effort_x": "low"}, "high", None),
        ({"max_ai_credits_x": 5}, None, None),
        ({"max_ai_credits": 40, "max_ai_credits_x": 5}, None, "40"),
        ({"max_ai_credits_x": 5, "max_ai_credits": 40}, None, "40"),
    ],
)
def test_run_reads_a_param_apart_from_one_its_name_is_a_prefix_of(box, params, effort, credits):
    stand_in_copilot(box)
    (box / "opt" / "params.json").write_text(
        json.dumps({"version": "1.0.105", "model": "claude-sonnet-5", **params})
    )

    result = run(box)

    assert result.returncode == 0, result.stderr
    argv = recorded(box / "records" / "copilot")
    for flag, value in (("--reasoning-effort", effort), ("--max-ai-credits", credits)):
        if value is None:
            assert flag not in argv
        else:
            assert flag_value(argv, flag) == value


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


def test_run_fails_without_a_model(box):
    stand_in_copilot(box)
    write_params(box, model=None)

    result = run(box)

    assert result.returncode != 0
    assert "model" in result.stderr
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
