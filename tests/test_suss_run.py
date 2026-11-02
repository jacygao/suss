import json
import os
import shlex
import subprocess
import sys
import types
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from importlib.machinery import ModuleSpec
from pathlib import Path

import pytest

from suss import cli

STANDINS = Path(__file__).resolve().parent / "stand_ins"
REPO = Path(__file__).resolve().parent.parent
STARTED = datetime(2026, 10, 31, 14, 25, tzinfo=timezone.utc)
STAMP = "20261031T142500Z"
GIB = 2**30
# A Docker engine with room for every run the fixture's suss.toml asks for.
ROOMY = f"16 {64 * GIB}"
# What a Docker Desktop VM given 8 GB of memory and 10 CPUs reports: a little
# under 8 GiB, so that it fits one 4096 MiB trial and not two.
DOCKER_DESKTOP_8GB = "10 8319504384"

# Each task by lane, with the image its task.toml names. Two pro-go tasks share
# an image, so that a lane's pulls can be seen to be de-duplicated.
TASKS = {
    "pro-go": [
        ("instance_flipt-io__flipt-aaa", "ghcr.io/x/v2:flipt"),
        ("instance_flipt-io__flipt-bbb", "ghcr.io/x/v2:flipt"),
        ("instance_navidrome__navidrome-ccc", "ghcr.io/x/v2:navidrome"),
    ],
    "pro-python": [
        ("instance_ansible__ansible-ddd", "ghcr.io/x/v2:ansible"),
    ],
}


@dataclass
class Project:
    directory: Path
    checkout: Path
    harbor: Path
    docker: Path
    log: Path
    plan: Path

    @property
    def tooling(self) -> Path:
        return self.checkout / "v2" / "tooling"

    def run(self, *argv: str, clock=None) -> int:
        return cli.main(
            ["run", *argv],
            directory=self.directory,
            harbor=self.harbor,
            docker=self.docker,
            clock=clock or (lambda: STARTED),
        )

    def records(self, program: str | None = None) -> list[dict]:
        if not self.log.exists():
            return []
        found = [json.loads(line) for line in self.log.read_text().splitlines()]
        return [record for record in found if program in (None, record["program"])]

    def job_names(self) -> list[str]:
        return [value(record["argv"], "--job-name") for record in self.records("harbor")]

    def plan_jobs(self, **jobs: dict) -> None:
        """Make each named job behave as given, with job names' `-` written as `_`."""
        self.plan.write_text(json.dumps({name.replace("_", "-"): job for name, job in jobs.items()}))

    def write_toml(
        self,
        arm_extra: str = "",
        environment: str = "docker",
        k: str = "2",
        max_seconds: str | None = "2700",
        max_cost_usd: str | None = None,
    ) -> None:
        cap = f"max_seconds = {max_seconds}\n" if max_seconds else ""
        cap += f"max_cost_usd = {max_cost_usd}\n" if max_cost_usd else ""
        (self.directory / "suss.toml").write_text(
            f'swe_bench_pro = "{self.checkout}"\n'
            'dataset = "swe-bench-pro-v2"\n'
            f'environment = "{environment}"\n'
            f"k = {k}\n"
            "concurrency = 4\n"
            f"{cap}\n"
            "[arms.peer-review]\n"
            'harness = "adapter"\n'
            f"{arm_extra}\n"
        )


def value(argv: list[str], option: str) -> str:
    return argv[argv.index(option) + 1]


def stand_in(source: Path, target: Path) -> Path:
    # An absolute interpreter, so that the stand-in starts whatever PATH it is given.
    target.write_text(f"#!{sys.executable}\n" + source.read_text())
    target.chmod(0o755)
    return target


def write_task(
    checkout: Path,
    name: str,
    image: str | None,
    timeout: float = 3000.0,
    cpus: int | None = 1,
    memory_mb: int | None = 4096,
) -> None:
    """A task.toml with each of `image`, `cpus` and `memory_mb` left out where it is None."""
    task = checkout / "v2" / "tasks" / name
    task.mkdir(parents=True)
    environment = f'docker_image = "{image}"\n' if image else ""
    environment += f"cpus = {cpus}\n" if cpus is not None else ""
    environment += f"memory_mb = {memory_mb}\n" if memory_mb is not None else ""
    (task / "task.toml").write_text(f"[agent]\ntimeout_sec = {timeout}\n\n[environment]\n{environment}")


@pytest.fixture
def project(tmp_path, monkeypatch) -> Project:
    checkout = tmp_path / "checkout"
    for tasks in TASKS.values():
        for name, image in tasks:
            write_task(checkout, name, image)
    (checkout / "v2" / "tooling").mkdir(parents=True)

    directory = tmp_path / "project"
    (directory / "adapter").mkdir(parents=True)
    (directory / "adapter" / "suss-harness.toml").write_text(
        '[harness]\nname = "peer-review"\nversion = "0.1.0"\nrun = "run.sh"\n'
        'allowed_hosts = ["api.anthropic.com"]\n'
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    created = Project(
        directory=directory,
        checkout=checkout,
        harbor=stand_in(STANDINS / "harbor.py", bin_dir / "harbor"),
        docker=stand_in(STANDINS / "docker.py", bin_dir / "docker"),
        log=tmp_path / "log.jsonl",
        plan=tmp_path / "plan.json",
    )
    created.write_toml()

    # Anywhere but the project, so that a command run in the current directory
    # is seen to be run in the wrong one.
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    monkeypatch.setenv("STAND_IN_LOG", str(created.log))
    monkeypatch.setenv("STAND_IN_HARBOR_PLAN", str(created.plan))
    monkeypatch.setenv("STAND_IN_DOCKER_ARCH", "x86_64")
    monkeypatch.setenv("STAND_IN_DOCKER_CAPACITY", ROOMY)
    monkeypatch.delenv("STAND_IN_DOCKER_INFO_FAIL", raising=False)
    monkeypatch.delenv("DRY_RUN", raising=False)
    monkeypatch.delenv("PYTHONPATH", raising=False)
    # Installed, whether or not this environment has the extra. A test that
    # needs it missing puts None in its place.
    modal = types.ModuleType("modal")
    modal.__spec__ = ModuleSpec("modal", None)
    monkeypatch.setitem(sys.modules, "modal", modal)
    return created


# The command


def test_the_installed_suss_command_runs():
    result = subprocess.run(
        ["uv", "run", "suss", "run", "--help"], cwd=REPO, capture_output=True, text=True
    )

    assert result.returncode == 0, result.stderr
    assert "--check" in result.stdout


def test_the_default_harbor_is_the_one_beside_this_python():
    harbor = cli.harbor_executable()

    assert harbor == Path(sys.executable).parent / "harbor"
    assert harbor.is_file(), f"{harbor} is not installed, so it cannot import suss.agent"


# What runs, and in what order


def test_each_lane_runs_k_agent_jobs_each_followed_by_its_regrade_in_lane_order(project):
    assert project.run("peer-review", "pro-go", "pro-python") == 0

    runs = [
        (value(record["argv"], "-p"), value(record["argv"], "-a"), value(record["argv"], "--job-name"))
        for record in project.records("harbor")
    ]
    assert runs == [
        ("lanes/pro-go", "suss.agent:SussAgent", f"{STAMP}-1"),
        ("lanes/pro-go", "patch_replay:PatchReplayAgent", f"{STAMP}-1-regrade"),
        ("lanes/pro-go", "suss.agent:SussAgent", f"{STAMP}-2"),
        ("lanes/pro-go", "patch_replay:PatchReplayAgent", f"{STAMP}-2-regrade"),
        ("lanes/pro-python", "suss.agent:SussAgent", f"{STAMP}-1"),
        ("lanes/pro-python", "patch_replay:PatchReplayAgent", f"{STAMP}-1-regrade"),
        ("lanes/pro-python", "suss.agent:SussAgent", f"{STAMP}-2"),
        ("lanes/pro-python", "patch_replay:PatchReplayAgent", f"{STAMP}-2-regrade"),
    ]


def test_every_job_shares_the_stamp_taken_once_when_the_run_starts(project):
    times = iter(STARTED + timedelta(hours=hour) for hour in range(100))

    project.run("peer-review", "pro-go", "pro-python", clock=lambda: next(times))

    assert {name.split("-")[0] for name in project.job_names()} == {STAMP}


def test_the_stamp_is_utc_whatever_zone_the_clock_gives(project):
    sydney = timezone(timedelta(hours=11))

    project.run("peer-review", "pro-go", clock=lambda: STARTED.astimezone(sydney))

    assert project.job_names()[0] == f"{STAMP}-1"


def test_harbor_runs_in_the_directory_holding_suss_toml_not_the_current_one(project):
    project.run("peer-review", "pro-go")

    assert {record["cwd"] for record in project.records()} == {str(project.directory.resolve())}


def test_the_regrade_keeps_the_environment_and_adds_the_tooling_to_pythonpath(project, monkeypatch):
    monkeypatch.setenv("SUSS_TEST_KEY", "kept")

    project.run("peer-review", "pro-go")

    agent, regrade = project.records("harbor")[:2]
    for record in (agent, regrade):
        assert record["env"]["SUSS_TEST_KEY"] == "kept"
        assert record["env"]["PATH"] == os.environ["PATH"]
        assert record["env"]["HOME"] == os.environ["HOME"]
    assert "PYTHONPATH" not in agent["env"]
    assert regrade["env"]["PYTHONPATH"] == str(project.tooling)


def test_the_regrade_puts_the_tooling_before_an_existing_pythonpath(project, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", "/somewhere/else")

    project.run("peer-review", "pro-go")

    agent, regrade = project.records("harbor")[:2]
    assert agent["env"]["PYTHONPATH"] == "/somewhere/else"
    assert regrade["env"]["PYTHONPATH"] == f"{project.tooling}{os.pathsep}/somewhere/else"


def test_arguments_after_the_separator_reach_every_agent_job_and_no_regrade(project):
    assert project.run("peer-review", "pro-go", "--", "--max-retries", "2") == 0

    assert len(project.records("harbor")) == 4
    for record in project.records("harbor"):
        has_them = record["argv"][-2:] == ["--max-retries", "2"]
        is_agent = "suss.agent:SussAgent" in record["argv"]
        assert has_them == is_agent, record["argv"]


def test_harbors_output_is_shown_as_it_runs(project, capfd):
    project.run("peer-review", "pro-go")

    out, err = capfd.readouterr()
    assert f"stand-in harbor ran {STAMP}-1\n" in out
    assert f"stand-in harbor ran {STAMP}-2-regrade\n" in out
    assert f"stand-in harbor complained about {STAMP}-1\n" in out + err


def test_on_a_terminal_harbor_writes_to_one_so_its_live_progress_shows(tmp_path, capfd):
    # Harbor's progress is a rich Live display, which shows nothing until the
    # job ends when it writes to anything but a terminal.
    script = "import sys; print(sys.stdout.isatty(), sys.stderr.isatty())"

    code, output = cli._stream(cli.Command([sys.executable, "-c", script]), tmp_path, terminal=True)

    assert code == 0
    assert output.split() == ["True", "True"]
    assert capfd.readouterr().out.split() == ["True", "True"]


# When a job fails


def test_a_regrade_follows_an_agent_job_that_exits_non_zero(project):
    project.plan_jobs(**{f"{STAMP}_1": {"exit": 1}})

    project.run("peer-review", "pro-go")

    assert project.job_names() == [f"{STAMP}-1", f"{STAMP}-1-regrade", f"{STAMP}-2", f"{STAMP}-2-regrade"]


@pytest.mark.parametrize(
    "job",
    [
        {"trial": False, "exit": 1},
        {"job_dir": False, "exit": 1},
        {"trial": False, "exit": 0},
        {"trial": False, "sources": True, "exit": 1},
    ],
    ids=["job-directory-without-a-trial", "no-job-directory", "no-trial-and-exit-0", "only-harbors-own-directory"],
)
def test_an_agent_job_that_leaves_no_trial_stops_the_run(project, capfd, job):
    project.plan_jobs(**{f"{STAMP}_2": job})

    code = project.run("peer-review", "pro-go", "pro-python")

    out, err = capfd.readouterr()
    assert code != 0
    assert project.job_names() == [f"{STAMP}-1", f"{STAMP}-1-regrade", f"{STAMP}-2"]
    message = err[err.index("error: "):]
    assert f"{STAMP}-2" in message.splitlines()[0]
    assert f"stand-in harbor ran {STAMP}-2" in message
    assert f"stand-in harbor complained about {STAMP}-2" in message


def test_a_regrade_that_exits_non_zero_is_reported_after_the_run_finishes(project, capfd):
    project.plan_jobs(**{f"{STAMP}_1_regrade": {"exit": 2}})

    code = project.run("peer-review", "pro-go")

    _, err = capfd.readouterr()
    assert code != 0
    assert len(project.job_names()) == 4
    assert f"error: regrade {STAMP}-1-regrade on pro-go exited 2" in err


# Refused before anything runs


def assert_nothing_ran(project: Project) -> None:
    assert project.records() == []
    assert not (project.directory / "lanes").exists()
    assert not (project.directory / "results").exists()


def test_a_max_seconds_over_any_lanes_timeout_is_refused_before_any_lane_runs(project, capfd):
    write_task(project.checkout, "instance_ansible__ansible-eee", "ghcr.io/x/v2:ansible", timeout=2000)

    code = project.run("peer-review", "pro-go", "pro-python")

    assert code != 0
    assert_nothing_ran(project)
    assert "pro-python" in capfd.readouterr().err


@pytest.mark.parametrize("k", ["0", "-1", "1.5", '"2"'])
def test_a_k_that_is_not_a_whole_number_of_jobs_is_refused_before_anything_runs(project, capfd, k):
    project.write_toml(k=k)

    code = project.run("peer-review", "pro-go", "--", "-k", "2")

    err = capfd.readouterr().err
    assert code != 0
    assert err.startswith("error: ") and "'k'" in err, err
    assert_nothing_ran(project)


def test_a_later_lane_that_cannot_be_built_is_refused_before_any_lane_runs(project, capfd):
    stray = project.directory / "lanes" / "pro-python" / "notes.txt"
    stray.parent.mkdir(parents=True)
    stray.write_text("not a link")

    code = project.run("peer-review", "pro-go", "pro-python")

    err = capfd.readouterr().err
    assert code != 0
    assert err.startswith("error: ") and str(stray) in err, err
    assert project.records() == []
    assert not (project.directory / "lanes" / "pro-go").exists()


@pytest.mark.parametrize("path", ["lanes/pro-python", "lanes"])
def test_a_file_where_a_lane_directory_goes_is_refused_before_any_lane_runs(project, capfd, path):
    stray = project.directory / path
    stray.parent.mkdir(parents=True, exist_ok=True)
    stray.write_text("not a directory")

    code = project.run("peer-review", "pro-go", "pro-python")

    err = capfd.readouterr().err
    assert code != 0
    assert err.startswith("error: ") and str(stray) in err, err
    assert project.records() == []


def test_a_check_lane_that_cannot_be_built_is_refused_before_anything_runs(project, capfd):
    stray = project.directory / "lanes" / "pro-go-check" / "notes.txt"
    stray.parent.mkdir(parents=True)
    stray.write_text("not a link")

    code = project.run("peer-review", "pro-python", "pro-go", "--check")

    assert code != 0
    assert str(stray) in capfd.readouterr().err
    assert project.records() == []


# With no max_seconds nothing reads the timeout, so a task.toml with none reaches the image reader.
@pytest.mark.parametrize(
    "contents, max_seconds, message",
    [
        (None, None, "task.toml: no such file"),
        ("[agent\n", "2700", "task.toml: not valid TOML"),
        ("directory", "2700", "task.toml: cannot be read"),
        ('environment = "docker"\n', None, "task.toml: no [environment] docker_image"),
        ('agent = 3000\n[environment]\ndocker_image = "x"\n', "2700", "task.toml: no [agent] timeout_sec"),
    ],
    ids=["missing", "not-toml", "a-directory", "environment-not-a-table", "agent-not-a-table"],
)
def test_a_task_toml_suss_cannot_read_is_an_error_naming_it(
    project, capfd, contents, max_seconds, message
):
    task = project.checkout / "v2" / "tasks" / "instance_ansible__ansible-ggg"
    task.mkdir()
    if contents == "directory":
        (task / "task.toml").mkdir()
    elif contents is not None:
        (task / "task.toml").write_text(contents)
    project.write_toml(max_seconds=max_seconds)

    code = project.run("peer-review", "pro-python")

    err = capfd.readouterr().err
    assert code != 0
    assert err.startswith("error: ") and f"instance_ansible__ansible-ggg/{message}" in err, err
    assert_nothing_ran(project)


@pytest.mark.parametrize("check", [False, True], ids=["run", "check"])
def test_a_later_lanes_broken_task_toml_is_refused_on_modal_with_no_max_seconds(
    project, capfd, check
):
    # Sorts after the lane's first task, which is all a check runs.
    task = project.checkout / "v2" / "tasks" / "instance_ansible__ansible-zzz"
    task.mkdir()
    (task / "task.toml").write_text("[agent\n")
    project.write_toml(environment="modal", max_seconds=None)

    code = project.run("peer-review", "pro-go", "pro-python", *(["--check"] if check else []))

    err = capfd.readouterr().err
    assert_nothing_ran(project)
    assert code != 0
    assert err.startswith("error: ") and "instance_ansible__ansible-zzz/task.toml" in err, err


def test_the_max_seconds_error_is_printed_as_the_spec_shows(project, capfd):
    project.write_toml(arm_extra="max_seconds = 3000")

    code = project.run("peer-review", "pro-go")

    assert code != 0
    assert capfd.readouterr().err == (
        "error: arm peer-review sets max_seconds = 3000, which must be under the "
        "3000-second agent timeout of the tasks in pro-go\n"
    )
    assert_nothing_ran(project)


def takes_no_budget(project: Project) -> None:
    """Give the arm a harness whose manifest sets max_cost_usd = false, named apart from the arm."""
    (project.directory / "adapter" / "suss-harness.toml").write_text(
        '[harness]\nname = "copilot-cli"\nversion = "0.1.0"\nrun = "run.sh"\n'
        "max_cost_usd = false\n"
    )


@pytest.mark.parametrize(
    "dry_run, check", [(False, False), (True, False), (False, True)], ids=["run", "dry-run", "check"]
)
def test_an_arms_own_budget_on_a_harness_that_takes_none_is_refused_as_the_spec_shows(
    project, capfd, monkeypatch, dry_run, check
):
    if dry_run:
        monkeypatch.setenv("DRY_RUN", "1")
    takes_no_budget(project)
    project.write_toml(arm_extra="max_cost_usd = 5")

    code = project.run("peer-review", "pro-go", *(["--check"] if check else []))

    out, err = capfd.readouterr()
    assert code != 0
    assert err == (
        "error: arm peer-review sets max_cost_usd = 5, but copilot-cli takes no budget in "
        "dollars; remove it, and max_seconds alone caps each trial\n"
    )
    assert out == ""
    assert_nothing_ran(project)


def test_a_top_level_budget_does_not_stop_a_harness_that_takes_none(project):
    takes_no_budget(project)
    project.write_toml(max_cost_usd="20")

    assert project.run("peer-review", "pro-go") == 0
    assert len(project.job_names()) == 4


def test_an_arms_own_budget_runs_on_a_harness_that_takes_one(project):
    project.write_toml(arm_extra="max_cost_usd = 5")

    assert project.run("peer-review", "pro-go") == 0
    assert len(project.job_names()) == 4


def test_a_refused_argument_after_the_separator_is_printed_as_the_spec_shows(project, capfd):
    code = project.run("peer-review", "pro-go", "--", "-k", "2")

    assert code != 0
    assert capfd.readouterr().err == (
        "error: -k/--n-attempts can't be passed after --: suss runs one trial per agent "
        "job, and k in suss.toml sets how many jobs\n"
    )
    assert_nothing_ran(project)


@pytest.mark.parametrize(
    "argv, message",
    [
        (["peer-review", "pro-go", "--no-such-option"], "--no-such-option"),
        (["peer-review", "pro-go", "-x"], "-x"),
        (["peer-review"], "lane"),
        ([], "arm"),
        (["peer-review", "pro-rust"], "no lane named pro-rust"),
        (["peer-review", "pro-go", "pro-go"], "pro-go"),
        (["no-such-arm", "pro-go"], "arm no-such-arm is not defined"),
    ],
    ids=["unknown-option", "unknown-short-option", "no-lane", "no-arm", "unknown-lane", "lane-twice", "unknown-arm"],
)
def test_what_suss_cannot_honour_is_an_error_and_runs_nothing(project, capfd, argv, message):
    code = project.run(*argv)

    err = capfd.readouterr().err
    assert code != 0
    assert err.startswith("error: ") and err.count("\n") == 1, err
    assert message in err
    assert_nothing_ran(project)


def test_an_unknown_command_is_an_error(project, capfd):
    code = cli.main(["frobnicate"], directory=project.directory, harbor=project.harbor, docker=project.docker)

    err = capfd.readouterr().err
    assert code != 0
    assert err.startswith("error: ") and "frobnicate" in err


def test_a_missing_suss_toml_is_an_error(project, capfd):
    (project.directory / "suss.toml").unlink()

    code = project.run("peer-review", "pro-go")

    err = capfd.readouterr().err
    assert code != 0
    assert err.startswith("error: ") and "suss.toml: no such file" in err


def test_a_dry_run_value_other_than_1_is_refused(project, capfd, monkeypatch):
    monkeypatch.setenv("DRY_RUN", "true")

    code = project.run("peer-review", "pro-go")

    err = capfd.readouterr().err
    assert code != 0
    assert err.startswith("error: ") and "DRY_RUN" in err
    assert_nothing_ran(project)


# Modal


@pytest.mark.parametrize(
    "dry_run, check", [(False, False), (True, False), (False, True)], ids=["run", "dry-run", "check"]
)
def test_modal_without_its_client_is_refused_naming_the_install_before_anything_runs(
    project, capfd, monkeypatch, dry_run, check
):
    if dry_run:
        monkeypatch.setenv("DRY_RUN", "1")
    # A None entry makes the module unfindable, as though it were not installed.
    monkeypatch.setitem(sys.modules, "modal", None)
    project.write_toml(environment="modal")

    code = project.run("peer-review", "pro-go", *(["--check"] if check else []))

    out, err = capfd.readouterr()
    assert code != 0
    assert err == (
        "error: environment is modal, but Modal's client is not installed: "
        "run uv sync --extra modal\n"
    )
    assert out == ""
    assert_nothing_ran(project)


def test_a_modal_client_that_fails_to_import_is_not_reported_as_missing(
    project, capfd, monkeypatch, tmp_path
):
    broken = tmp_path / "site" / "modal"
    broken.mkdir(parents=True)
    (broken / "__init__.py").write_text("raise RuntimeError('broken install')\n")
    monkeypatch.delitem(sys.modules, "modal")
    monkeypatch.syspath_prepend(str(broken.parent))
    project.write_toml(environment="modal")

    code = project.run("peer-review", "pro-go")

    assert code == 0, capfd.readouterr().err
    assert len(project.records("harbor")) == 4


def test_docker_runs_without_modals_client(project, monkeypatch):
    monkeypatch.setitem(sys.modules, "modal", None)

    assert project.run("peer-review", "pro-go") == 0

    assert len(project.records("harbor")) == 4


# Docker's CPUs and memory


def assert_refused_before_any_lane(project: Project) -> None:
    """Nothing but `docker info` ran, and no lane was built."""
    assert [record["argv"][0] for record in project.records()] == ["info"]
    assert not (project.directory / "lanes").exists()
    assert not (project.directory / "results").exists()


def test_a_concurrency_that_needs_more_cpus_than_docker_has_is_refused_naming_what_fits(
    project, capfd, monkeypatch
):
    monkeypatch.setenv("STAND_IN_DOCKER_CAPACITY", f"3 {64 * GIB}")

    code = project.run("peer-review", "pro-go")

    assert code != 0
    assert capfd.readouterr().err == (
        "error: concurrency = 4 needs 4 CPUs and 16384 MiB for the tasks in pro-go, but Docker "
        "has 3 CPUs and 65536 MiB; set concurrency to 3 or less, or give Docker more\n"
    )
    assert_refused_before_any_lane(project)


def test_docker_desktops_8gb_fits_one_trial_of_4096_mib_when_memory_binds_before_cpus(
    project, capfd, monkeypatch
):
    monkeypatch.setenv("STAND_IN_DOCKER_CAPACITY", DOCKER_DESKTOP_8GB)

    code = project.run("peer-review", "pro-go")

    assert code != 0
    assert capfd.readouterr().err == (
        "error: concurrency = 4 needs 4 CPUs and 16384 MiB for the tasks in pro-go, but Docker "
        "has 10 CPUs and 7934 MiB; set concurrency to 1 or less, or give Docker more\n"
    )
    assert_refused_before_any_lane(project)


@pytest.mark.parametrize(
    "capacity, fits",
    [
        (f"4 {16 * GIB}", None),
        (f"4 {16 * GIB - 1}", 3),
        (f"3 {16 * GIB}", 3),
    ],
    ids=["exactly-enough", "one-byte-short", "one-cpu-short"],
)
def test_four_trials_of_one_cpu_and_4096_mib_need_exactly_4_cpus_and_16_gib(
    project, capfd, monkeypatch, capacity, fits
):
    monkeypatch.setenv("STAND_IN_DOCKER_CAPACITY", capacity)

    code = project.run("peer-review", "pro-go")

    err = capfd.readouterr().err
    if fits is None:
        assert code == 0, err
    else:
        assert code != 0
        assert f"set concurrency to {fits} or less" in err, err


def test_the_lanes_largest_task_sets_its_need_and_a_later_lane_is_refused_before_any_lane_runs(
    project, capfd, monkeypatch
):
    write_task(project.checkout, "instance_ansible__ansible-big", "ghcr.io/x/v2:ansible", cpus=2, memory_mb=1024)
    write_task(project.checkout, "instance_ansible__ansible-fat", "ghcr.io/x/v2:ansible", cpus=1, memory_mb=8192)
    monkeypatch.setenv("STAND_IN_DOCKER_CAPACITY", f"6 {48 * GIB}")

    code = project.run("peer-review", "pro-go", "pro-python")

    assert code != 0
    assert capfd.readouterr().err == (
        "error: concurrency = 4 needs 8 CPUs and 32768 MiB for the tasks in pro-python, but Docker "
        "has 6 CPUs and 49152 MiB; set concurrency to 3 or less, or give Docker more\n"
    )
    assert_refused_before_any_lane(project)


def test_a_task_too_large_for_docker_alone_is_refused_with_no_concurrency_offered(
    project, capfd, monkeypatch
):
    monkeypatch.setenv("STAND_IN_DOCKER_CAPACITY", f"10 {3 * GIB}")

    code = project.run("peer-review", "pro-go")

    assert code != 0
    assert capfd.readouterr().err == (
        "error: one trial of the tasks in pro-go needs 1 CPU and 4096 MiB, but Docker has "
        "10 CPUs and 3072 MiB; give Docker more\n"
    )
    assert_refused_before_any_lane(project)


def test_a_check_counts_only_its_one_task_and_one_trial_at_a_time(project, monkeypatch):
    # Sorts after the lane's first task, which is all a check runs.
    write_task(project.checkout, "instance_navidrome__navidrome-zzz", "ghcr.io/x/v2:navidrome", cpus=64)
    monkeypatch.setenv("STAND_IN_DOCKER_CAPACITY", f"1 {4 * GIB}")

    assert project.run("peer-review", "pro-go", "--check") == 0

    assert len(project.records("harbor")) == 2


def test_a_check_whose_task_does_not_fit_in_docker_is_refused_before_anything_runs(
    project, capfd, monkeypatch
):
    monkeypatch.setenv("STAND_IN_DOCKER_CAPACITY", f"10 {4 * GIB - 1}")

    code = project.run("peer-review", "pro-go", "--check")

    assert code != 0
    assert capfd.readouterr().err == (
        "error: one trial of the first task in pro-go needs 1 CPU and 4096 MiB, but Docker has "
        "10 CPUs and 4095 MiB; give Docker more\n"
    )
    assert_refused_before_any_lane(project)


def test_an_override_of_the_agent_jobs_cpus_counts_toward_what_it_needs(project, capfd, monkeypatch):
    monkeypatch.setenv("STAND_IN_DOCKER_CAPACITY", f"6 {64 * GIB}")

    code = project.run("peer-review", "pro-go", "--", "--override-cpus", "2")

    assert code != 0
    assert capfd.readouterr().err == (
        "error: concurrency = 4 needs 8 CPUs and 16384 MiB for the tasks in pro-go under "
        "--override-cpus 2, but Docker has 6 CPUs and 65536 MiB; set concurrency to 3 or "
        "less, or give Docker more\n"
    )
    assert_refused_before_any_lane(project)


def test_a_checks_override_of_the_agent_jobs_memory_counts_toward_its_one_trial(
    project, capfd, monkeypatch
):
    monkeypatch.setenv("STAND_IN_DOCKER_CAPACITY", DOCKER_DESKTOP_8GB)

    code = project.run("peer-review", "pro-go", "--check", "--", "--override-memory-mb=8192")

    assert code != 0
    assert capfd.readouterr().err == (
        "error: one trial of the first task in pro-go under --override-memory-mb 8192 needs "
        "1 CPU and 8192 MiB, but Docker has 10 CPUs and 7934 MiB; give Docker more\n"
    )
    assert_refused_before_any_lane(project)


def test_an_override_below_the_tasks_own_need_still_leaves_the_regrade_needing_it(
    project, capfd, monkeypatch
):
    monkeypatch.setenv("STAND_IN_DOCKER_CAPACITY", f"10 {3 * GIB}")

    code = project.run("peer-review", "pro-go", "--check", "--", "--override-memory-mb", "1024")

    assert code != 0
    assert "needs 1 CPU and 4096 MiB" in capfd.readouterr().err
    assert_refused_before_any_lane(project)


def test_an_override_that_is_not_a_whole_number_is_an_error_before_anything_runs(project, capfd):
    code = project.run("peer-review", "pro-go", "--", "--override-cpus", "two")

    err = capfd.readouterr().err
    assert code != 0
    assert err.startswith("error: ") and "--override-cpus" in err and "'two'" in err, err
    assert project.records() == []


def test_on_modal_docker_is_never_asked_and_no_task_needs_cpus_or_memory(project, monkeypatch):
    write_task(project.checkout, "instance_ansible__ansible-bare", "ghcr.io/x/v2:ansible", cpus=None, memory_mb=None)
    monkeypatch.setenv("STAND_IN_DOCKER_CAPACITY", "1 1024")
    project.write_toml(environment="modal")

    assert project.run("peer-review", "pro-python") == 0

    assert project.records("docker") == []


def test_a_dry_run_still_refuses_a_concurrency_that_does_not_fit(project, capfd, monkeypatch):
    monkeypatch.setenv("DRY_RUN", "1")
    monkeypatch.setenv("STAND_IN_DOCKER_CAPACITY", DOCKER_DESKTOP_8GB)

    code = project.run("peer-review", "pro-go")

    out, err = capfd.readouterr()
    assert code != 0
    assert out == ""
    assert "set concurrency to 1 or less" in err, err
    assert_refused_before_any_lane(project)


@pytest.mark.parametrize("missing", ["cpus", "memory_mb"])
def test_a_task_with_no_cpus_or_memory_mb_is_an_error_naming_it(project, capfd, missing):
    needs = {"cpus": 1, "memory_mb": 4096, missing: None}
    write_task(project.checkout, "instance_ansible__ansible-bare", "ghcr.io/x/v2:ansible", **needs)

    code = project.run("peer-review", "pro-go", "pro-python")

    err = capfd.readouterr().err
    assert code != 0
    assert err.startswith("error: ")
    assert f"instance_ansible__ansible-bare/task.toml: no [environment] {missing}" in err, err
    assert not (project.directory / "lanes").exists()
    assert project.records("harbor") == []


@pytest.mark.parametrize("value", ["0", "-1", "1.5", '"1"', "true"])
def test_a_task_whose_cpus_is_not_a_positive_whole_number_is_an_error_naming_it(project, capfd, value):
    task = project.checkout / "v2" / "tasks" / "instance_ansible__ansible-odd"
    task.mkdir()
    (task / "task.toml").write_text(
        f'[agent]\ntimeout_sec = 3000\n\n[environment]\ndocker_image = "x"\ncpus = {value}\nmemory_mb = 4096\n'
    )

    code = project.run("peer-review", "pro-python")

    err = capfd.readouterr().err
    assert code != 0
    assert "instance_ansible__ansible-odd/task.toml: [environment] cpus" in err, err
    assert project.records("harbor") == []


def test_a_docker_engine_that_is_not_running_is_an_error_before_any_lane_is_built(
    project, capfd, monkeypatch
):
    monkeypatch.setenv("STAND_IN_DOCKER_INFO_FAIL", "Cannot connect to the Docker daemon")

    code = project.run("peer-review", "pro-go")

    assert code != 0
    assert capfd.readouterr().err == "error: docker info exited 1: Cannot connect to the Docker daemon\n"
    assert_refused_before_any_lane(project)


def test_docker_info_output_suss_cannot_read_is_an_error_quoting_it(project, capfd, monkeypatch):
    monkeypatch.setenv("STAND_IN_DOCKER_CAPACITY", "<no value> <no value>")

    code = project.run("peer-review", "pro-go")

    err = capfd.readouterr().err
    assert code != 0
    assert err.startswith("error: ") and "'<no value> <no value>'" in err, err
    assert_refused_before_any_lane(project)


def test_no_docker_program_is_an_error_before_any_lane_is_built(project, capfd, tmp_path):
    code = cli.main(
        ["run", "peer-review", "pro-go"],
        directory=project.directory,
        harbor=project.harbor,
        docker=tmp_path / "no-such-docker",
        clock=lambda: STARTED,
    )

    err = capfd.readouterr().err
    assert code != 0
    assert "no-such-docker: no such program" in err, err
    assert not (project.directory / "lanes").exists()
    assert project.records() == []


# Pulling images


def pulls(project: Project) -> list[list[str]]:
    return [record["argv"] for record in project.records("docker") if record["argv"][0] == "pull"]


def test_each_lanes_images_are_pulled_once_after_it_is_built_and_before_its_first_job(project):
    project.run("peer-review", "pro-go", "pro-python")

    sequence = [
        record["argv"][-1] if record["program"] == "docker" else value(record["argv"], "--job-name")
        for record in project.records()
        if record["argv"][0] != "info"
    ]
    assert sequence == [
        "ghcr.io/x/v2:flipt",
        "ghcr.io/x/v2:navidrome",
        f"{STAMP}-1",
        f"{STAMP}-1-regrade",
        f"{STAMP}-2",
        f"{STAMP}-2-regrade",
        "ghcr.io/x/v2:ansible",
        f"{STAMP}-1",
        f"{STAMP}-1-regrade",
        f"{STAMP}-2",
        f"{STAMP}-2-regrade",
    ]


def test_a_lane_is_built_before_its_images_are_pulled(project, monkeypatch):
    monkeypatch.setenv("STAND_IN_DOCKER_FAIL", "ghcr.io/x/v2:flipt")

    project.run("peer-review", "pro-go")

    assert (project.directory / "lanes" / "pro-go" / "instance_flipt-io__flipt-aaa").is_symlink()


@pytest.mark.parametrize("arch", ["aarch64", "arm64"])
def test_images_are_pulled_for_amd64_when_the_docker_engine_runs_on_arm64(project, monkeypatch, arch):
    monkeypatch.setenv("STAND_IN_DOCKER_ARCH", arch)

    project.run("peer-review", "pro-python")

    assert pulls(project) == [["pull", "--platform", "linux/amd64", "ghcr.io/x/v2:ansible"]]


def test_images_are_pulled_for_the_engines_own_platform_when_it_runs_on_amd64(project, monkeypatch):
    monkeypatch.setenv("STAND_IN_DOCKER_ARCH", "x86_64")

    project.run("peer-review", "pro-python")

    assert pulls(project) == [["pull", "ghcr.io/x/v2:ansible"]]


def test_the_platform_follows_the_docker_engine_not_this_machine(project, monkeypatch):
    monkeypatch.setattr("platform.machine", lambda: "arm64")
    monkeypatch.setenv("STAND_IN_DOCKER_ARCH", "x86_64")

    project.run("peer-review", "pro-python")

    assert pulls(project) == [["pull", "ghcr.io/x/v2:ansible"]]


def test_a_failed_pull_is_an_error_and_runs_no_job(project, capfd, monkeypatch):
    monkeypatch.setenv("STAND_IN_DOCKER_FAIL", "ghcr.io/x/v2:navidrome")

    code = project.run("peer-review", "pro-go")

    err = capfd.readouterr().err
    assert code != 0
    assert "error: " in err and "ghcr.io/x/v2:navidrome" in err
    assert project.records("harbor") == []


def test_a_task_that_names_no_image_is_an_error_before_anything_runs(project, capfd):
    write_task(project.checkout, "instance_ansible__ansible-fff", None)

    code = project.run("peer-review", "pro-go", "pro-python")

    err = capfd.readouterr().err
    assert code != 0
    assert err.startswith("error: ") and "instance_ansible__ansible-fff" in err
    assert_nothing_ran(project)


def test_nothing_is_pulled_when_the_sandboxes_run_on_modal(project):
    project.write_toml(environment="modal")

    assert project.run("peer-review", "pro-go") == 0

    assert project.records("docker") == []
    assert len(project.records("harbor")) == 4


# Dry run


def test_a_dry_run_prints_each_pair_in_run_order_and_runs_nothing(project, capfd, monkeypatch):
    monkeypatch.setenv("DRY_RUN", "1")
    monkeypatch.setenv("STAND_IN_DOCKER_ARCH", "aarch64")

    code = project.run("peer-review", "pro-go", "pro-python")

    out, err = capfd.readouterr()
    assert code == 0, err
    assert [record["argv"][0] for record in project.records()] == ["info"]
    assert not (project.directory / "lanes").exists()
    root = project.directory.resolve()
    expected = []
    for lane in ("pro-go", "pro-python"):
        for trial in (1, 2):
            jobs = f"results/{lane}/peer-review"
            common = f"{project.harbor} run -p lanes/{lane} -e docker -n 4 -k 1"
            expected.append(
                f"{common} -a suss.agent:SussAgent --ak arm=peer-review --ak config=suss.toml "
                f"--allow-agent-host api.anthropic.com --disable-verification "
                f"--jobs-dir {jobs} --job-name {STAMP}-{trial}"
            )
            expected.append(
                f"PYTHONPATH={project.tooling} {common} -a patch_replay:PatchReplayAgent -m replay "
                f"--ak source_job={root}/{jobs}/{STAMP}-{trial} --jobs-dir {jobs} "
                f"--job-name {STAMP}-{trial}-regrade"
            )
    assert out.splitlines() == expected


def test_a_dry_run_quotes_what_a_shell_would_split(project, capfd, monkeypatch):
    monkeypatch.setenv("DRY_RUN", "1")

    project.run("peer-review", "pro-python", "--", "--agent-env", "NOTE=two words")

    agent = capfd.readouterr().out.splitlines()[0]
    assert shlex.split(agent)[-2:] == ["--agent-env", "NOTE=two words"]


# Check


def test_a_check_runs_one_pair_on_the_first_task_and_writes_under_checks(project):
    assert project.run("peer-review", "pro-go", "--check") == 0

    records = project.records("harbor")
    assert [value(record["argv"], "--job-name") for record in records] == [f"{STAMP}-1", f"{STAMP}-1-regrade"]
    assert {value(record["argv"], "-p") for record in records} == {"lanes/pro-go-check"}
    assert {value(record["argv"], "--jobs-dir") for record in records} == {"checks/pro-go/peer-review"}
    check = project.directory / "lanes" / "pro-go-check"
    assert [link.name for link in check.iterdir()] == ["instance_flipt-io__flipt-aaa"]
    assert not (project.directory / "lanes" / "pro-go").exists()
    assert pulls(project) == [["pull", "ghcr.io/x/v2:flipt"]]
    assert (project.directory / "checks" / "pro-go" / "peer-review" / f"{STAMP}-1").is_dir()
    assert not (project.directory / "results").exists()


# Results


def test_a_second_run_leaves_the_first_runs_jobs_in_place(project):
    project.run("peer-review", "pro-go")
    jobs = project.directory / "results" / "pro-go" / "peer-review"
    assert jobs.is_dir(), "the first run wrote no jobs"
    first = sorted(path.name for path in jobs.iterdir())
    trials = sorted(str(path) for path in jobs.glob("*/instance_*"))

    project.run("peer-review", "pro-go", clock=lambda: STARTED + timedelta(days=1))

    after = sorted(path.name for path in jobs.iterdir())
    assert first == [f"{STAMP}-1", f"{STAMP}-1-regrade", f"{STAMP}-2", f"{STAMP}-2-regrade"]
    assert set(first) < set(after) and len(after) == 8
    assert all(Path(trial).is_dir() for trial in trials)
