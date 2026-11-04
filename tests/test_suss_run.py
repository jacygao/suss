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
import typer.main
from harbor.cli.main import app as harbor_app
from harbor.models.task.config import NetworkMode, TaskConfig
from harbor.models.trial.config import AgentConfig as TrialAgentConfig
from harbor.models.trial.config import EnvironmentConfig as TrialEnvironmentConfig
from harbor.trial.network_policy import resolve_agent_env_baseline, resolve_agent_phase_policy

from suss import cli, lanes

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

# The download's tasks, as it names them. By code point, the first is the one a
# check runs. One names a docker_image, which suss must still not pull.
TASKS = ["ArcadeData__arcadedb-4281", "apache__pulsar-25793", "owner__repo-3"]
IMAGE = ("apache__pulsar-25793", "ghcr.io/x/pulsar:latest")


@dataclass
class Project:
    directory: Path
    tasks: Path
    harbor: Path
    docker: Path
    log: Path
    plan: Path

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
        lane_tables: str = "",
    ) -> None:
        cap = f"max_seconds = {max_seconds}\n" if max_seconds else ""
        (self.directory / "suss.toml").write_text(
            'swe_rebench = "../tasks/swe-rebench-07-2026"\n'
            f'environment = "{environment}"\n'
            f"k = {k}\n"
            "concurrency = 4\n"
            f"{cap}\n"
            f"{lane_tables}\n"
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
    tasks: Path,
    name: str,
    timeout: float = 3000.0,
    cpus: int | None = 1,
    memory_mb: int | None = 4096,
    image: str | None = None,
) -> Path:
    """A task as the download lays it out, with `cpus` and `memory_mb` left out where None."""
    task = tasks / name
    (task / "environment").mkdir(parents=True)
    (task / "environment" / "Dockerfile").write_text("FROM scratch\n")
    environment = 'network_mode = "public"\n'
    environment += f'docker_image = "{image}"\n' if image else ""
    environment += f"cpus = {cpus}\n" if cpus is not None else ""
    environment += f"memory_mb = {memory_mb}\n" if memory_mb is not None else ""
    (task / "task.toml").write_text(f"[agent]\ntimeout_sec = {timeout}\n\n[environment]\n{environment}")
    return task


@pytest.fixture
def project(tmp_path, monkeypatch) -> Project:
    # Two levels down, so that swe_rebench read from the current directory,
    # one level down, names a directory that is not the download.
    tasks = tmp_path / "evals" / "tasks" / "swe-rebench-07-2026"
    for name in TASKS:
        write_task(tasks, name, image=IMAGE[1] if name == IMAGE[0] else None)

    directory = tmp_path / "evals" / "project"
    (directory / "adapter").mkdir(parents=True)
    (directory / "adapter" / "suss-harness.toml").write_text(
        '[harness]\nname = "peer-review"\nversion = "0.1.0"\nrun = "run.sh"\n'
        'allowed_hosts = ["api.anthropic.com"]\n'
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    created = Project(
        directory=directory,
        tasks=tasks,
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


def test_the_lane_runs_k_agent_jobs_each_followed_by_its_regrade(project):
    assert project.run("peer-review", "rebench") == 0

    runs = [
        (value(record["argv"], "-p"), value(record["argv"], "-a"), value(record["argv"], "--job-name"))
        for record in project.records("harbor")
    ]
    assert runs == [
        ("lanes/rebench", "suss.agent:SussAgent", f"{STAMP}-1"),
        ("lanes/rebench", "suss.replay:ReplayAgent", f"{STAMP}-1-regrade"),
        ("lanes/rebench", "suss.agent:SussAgent", f"{STAMP}-2"),
        ("lanes/rebench", "suss.replay:ReplayAgent", f"{STAMP}-2-regrade"),
    ]


def test_the_lane_holds_a_copy_of_each_downloaded_task_with_the_agents_network_off(project):
    assert project.run("peer-review", "rebench") == 0

    lane = project.directory / "lanes" / "rebench"
    assert sorted(entry.name for entry in lane.iterdir()) == sorted(TASKS)
    for name in TASKS:
        assert not (lane / name).is_symlink()
        assert 'network_mode = "no-network"' in (lane / name / "task.toml").read_text()


def agent_phase_hosts(task: Path, agent_argv: list[str]) -> list[str]:
    """
    The hosts Harbor 0.22.0 opens in the agent phase of a trial of `task`, a
    task directory, in the agent job run as `agent_argv`, by Harbor's own code.

    Fails the test when that phase's network is not an allowlist.
    """
    run = typer.main.get_command(harbor_app).commands["run"]
    parser = run.make_parser(run.context_class(run, info_name="run"))
    given, _, _ = parser.parse_args(agent_argv[1:])
    task_config = TaskConfig.model_validate_toml((task / "task.toml").read_text())
    agent = TrialAgentConfig(extra_allowed_hosts=list(given.get("allow_agent_hosts") or []))
    baseline = resolve_agent_env_baseline(task_config, TrialEnvironmentConfig())
    policy = resolve_agent_phase_policy(task_config, agent, baseline)
    assert policy.network_mode == NetworkMode.ALLOWLIST, f"{task.name}: {policy}"
    return policy.allowed_hosts


# Every Senior SWE-Bench v2026.06 task allows these in its agent phase.
SENIOR_HOSTS = [
    "api.portkey.ai", "pypi.org", "*.pythonhosted.org", "*.pypa.io", "astral.sh",
    "*.astral.sh", "pypi-mirror.modal.local", "*.python.org", "registry.npmjs.org",
    "www.npmjs.com", "registry.yarnpkg.com", "pnpm.io", "get.pnpm.io", "nodejs.org",
    "deb.nodesource.com", "www.typescriptlang.org", "sh.rustup.rs", "*.rust-lang.org",
    "crates.io", "*.crates.io", "hex.pm", "*.hex.pm", "elixir-lang.org", "www.erlang.org",
    "developer.mozilla.org", "archive.ubuntu.com", "security.ubuntu.com", "*.ubuntu.com",
    "deb.debian.org", "*.debian.org", "apt.postgresql.org", "download.redis.io",
]


def write_allowlist_task(tasks: Path, name: str, hosts: list[str]) -> Path:
    task = write_task(tasks, name)
    listed = "".join(f'    "{host}",\n' for host in hosts)
    toml = task / "task.toml"
    toml.write_text(
        toml.read_text().replace(
            "[agent]\n", f'[agent]\nnetwork_mode = "allowlist"\nallowed_hosts = [\n{listed}]\n'
        )
    )
    return task


def test_each_trials_agent_phase_reaches_its_tasks_hosts_and_the_manifests_and_no_other(project):
    write_allowlist_task(
        project.tasks, "posthog-fix-a", [*SENIOR_HOSTS, "clickhouse.com", "*.clickhouse.com"]
    )
    write_allowlist_task(project.tasks, "prefect-fix-b", [*SENIOR_HOSTS, "docs.sqlalchemy.org"])
    # pypi.org is the tasks' as well, and must be opened once.
    (project.directory / "adapter" / "suss-harness.toml").write_text(
        '[harness]\nname = "peer-review"\nversion = "0.1.0"\nrun = "run.sh"\n'
        'allowed_hosts = ["api.anthropic.com", "pypi.org"]\n'
    )

    assert project.run("peer-review", "rebench") == 0

    lane = project.directory / "lanes" / "rebench"
    agent = project.records("harbor")[0]["argv"]
    assert [host for flag, host in zip(agent, agent[1:]) if flag == "--allow-agent-host"] == [
        "api.anthropic.com",
        "pypi.org",
    ]
    assert agent_phase_hosts(lane / "posthog-fix-a", agent) == [
        *SENIOR_HOSTS, "clickhouse.com", "*.clickhouse.com", "api.anthropic.com",
    ]
    assert agent_phase_hosts(lane / "prefect-fix-b", agent) == [
        *SENIOR_HOSTS, "docs.sqlalchemy.org", "api.anthropic.com",
    ]
    for name in TASKS:
        assert agent_phase_hosts(lane / name, agent) == ["api.anthropic.com", "pypi.org"]


def test_swe_rebench_is_read_from_the_directory_holding_suss_toml_not_the_current_one(
    project, tmp_path
):
    # Where swe_rebench would point from the current directory.
    write_task(tmp_path / "tasks" / "swe-rebench-07-2026", "decoy__decoy-1")

    assert project.run("peer-review", "rebench") == 0

    lane = project.directory / "lanes" / "rebench"
    assert sorted(entry.name for entry in lane.iterdir()) == sorted(TASKS)


@pytest.mark.parametrize("dry_run", [False, True], ids=["run", "dry-run"])
def test_an_old_suss_toml_naming_swe_bench_pro_is_refused_before_anything_runs(
    project, capfd, monkeypatch, dry_run
):
    if dry_run:
        monkeypatch.setenv("DRY_RUN", "1")
    path = project.directory / "suss.toml"
    path.write_text(
        'swe_bench_pro = "../SWE-bench_Pro-os"\ndataset = "swe-bench-pro-v2"\n'
        + path.read_text().replace('swe_rebench = "../tasks/swe-rebench-07-2026"\n', "")
    )

    code = project.run("peer-review", "rebench")

    out, err = capfd.readouterr()
    assert code != 0
    assert out == ""
    assert err == (
        f"error: {path}: sets swe_bench_pro and dataset, which suss no longer reads; name the "
        "directory of downloaded SWE-rebench tasks with swe_rebench\n"
    )
    assert_nothing_ran(project)


def test_every_job_shares_the_stamp_taken_once_when_the_run_starts(project):
    times = iter(STARTED + timedelta(hours=hour) for hour in range(100))

    project.run("peer-review", "rebench", clock=lambda: next(times))

    assert {name.split("-")[0] for name in project.job_names()} == {STAMP}


def test_the_stamp_is_utc_whatever_zone_the_clock_gives(project):
    sydney = timezone(timedelta(hours=11))

    project.run("peer-review", "rebench", clock=lambda: STARTED.astimezone(sydney))

    assert project.job_names()[0] == f"{STAMP}-1"


def test_harbor_runs_in_the_directory_holding_suss_toml_not_the_current_one(project):
    project.run("peer-review", "rebench")

    assert {record["cwd"] for record in project.records()} == {str(project.directory.resolve())}


@pytest.mark.parametrize("pythonpath", [None, "/somewhere/else"], ids=["unset", "set"])
def test_both_jobs_run_in_suss_own_environment_unchanged(project, monkeypatch, pythonpath):
    monkeypatch.setenv("SUSS_TEST_KEY", "kept")
    if pythonpath is not None:
        monkeypatch.setenv("PYTHONPATH", pythonpath)

    project.run("peer-review", "rebench")

    agent, regrade = project.records("harbor")[:2]
    for record in (agent, regrade):
        assert record["env"]["SUSS_TEST_KEY"] == "kept"
        assert record["env"]["PATH"] == os.environ["PATH"]
        assert record["env"]["HOME"] == os.environ["HOME"]
        assert record["env"].get("PYTHONPATH") == pythonpath


def test_arguments_after_the_separator_reach_every_agent_job_and_no_regrade(project):
    assert project.run("peer-review", "rebench", "--", "--max-retries", "2") == 0

    assert len(project.records("harbor")) == 4
    for record in project.records("harbor"):
        has_them = record["argv"][-2:] == ["--max-retries", "2"]
        is_agent = "suss.agent:SussAgent" in record["argv"]
        assert has_them == is_agent, record["argv"]


def test_harbors_output_is_shown_as_it_runs(project, capfd):
    project.run("peer-review", "rebench")

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

    project.run("peer-review", "rebench")

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

    code = project.run("peer-review", "rebench")

    out, err = capfd.readouterr()
    assert code != 0
    assert project.job_names() == [f"{STAMP}-1", f"{STAMP}-1-regrade", f"{STAMP}-2"]
    message = err[err.index("error: "):]
    assert f"{STAMP}-2" in message.splitlines()[0]
    assert f"stand-in harbor ran {STAMP}-2" in message
    assert f"stand-in harbor complained about {STAMP}-2" in message


def test_a_regrade_that_exits_non_zero_is_reported_after_the_run_finishes(project, capfd):
    project.plan_jobs(**{f"{STAMP}_1_regrade": {"exit": 2}})

    code = project.run("peer-review", "rebench")

    _, err = capfd.readouterr()
    assert code != 0
    assert len(project.job_names()) == 4
    assert f"error: regrade {STAMP}-1-regrade on rebench exited 2" in err


# Refused before anything runs


def assert_nothing_ran(project: Project) -> None:
    assert project.records() == []
    assert not (project.directory / "lanes").exists()
    assert not (project.directory / "results").exists()


def test_a_max_seconds_over_any_tasks_timeout_is_refused_before_anything_runs(project, capfd):
    write_task(project.tasks, "zzz__short-1", timeout=2000)

    code = project.run("peer-review", "rebench")

    assert code != 0
    assert_nothing_ran(project)
    assert "2000-second agent timeout of the tasks in rebench" in capfd.readouterr().err


@pytest.mark.parametrize("check", [False, True], ids=["full", "check"])
def test_the_lane_checks_pass_over_a_task_the_lane_leaves_out(project, monkeypatch, check):
    # Too short a timeout for max_seconds, too large for Docker, and first by
    # name, so that a check would take it.
    write_task(project.tasks, "AAA__never-1", timeout=2000, cpus=64)
    monkeypatch.setattr(lanes, "UNGRADEABLE", ("AAA__never-1",))

    assert project.run("peer-review", "rebench", *(["--check"] if check else [])) == 0

    lane = project.directory / "lanes" / ("rebench-check" if check else "rebench")
    assert sorted(task.name for task in lane.iterdir()) == (TASKS[:1] if check else sorted(TASKS))


@pytest.mark.parametrize("k", ["0", "-1", "1.5", '"2"'])
def test_a_k_that_is_not_a_whole_number_of_jobs_is_refused_before_anything_runs(project, capfd, k):
    project.write_toml(k=k)

    code = project.run("peer-review", "rebench", "--", "-k", "2")

    err = capfd.readouterr().err
    assert code != 0
    assert err.startswith("error: ") and "'k'" in err, err
    assert_nothing_ran(project)


@pytest.mark.parametrize("dry_run", [False, True], ids=["run", "dry-run"])
@pytest.mark.parametrize(
    "path, kind",
    [
        ("lanes/rebench", "file"),
        ("lanes", "file"),
        ("lanes/rebench", "link"),
        ("lanes/rebench-check", "link"),
    ],
    ids=["lane-a-file", "lanes-a-file", "lane-a-link", "check-lane-a-link"],
)
def test_a_lane_path_suss_will_not_build_at_is_refused_before_anything_runs(
    project, capfd, monkeypatch, path, kind, dry_run
):
    if dry_run:
        monkeypatch.setenv("DRY_RUN", "1")
    stray = project.directory / path
    stray.parent.mkdir(parents=True, exist_ok=True)
    kept = project.directory / "kept"
    if kind == "file":
        stray.write_text("not a directory")
    else:
        kept.mkdir()
        (kept / "mine.txt").write_text("mine")
        stray.symlink_to(kept)

    code = project.run("peer-review", "rebench", *(["--check"] if "check" in path else []))

    out, err = capfd.readouterr()
    assert code != 0
    assert out == ""
    assert err.startswith("error: ") and str(stray) in err, err
    assert project.records("harbor") == []
    if kind == "link":
        assert [entry.name for entry in kept.iterdir()] == ["mine.txt"]


def test_what_an_earlier_build_or_a_person_left_in_the_lane_is_replaced(project):
    stray = project.directory / "lanes" / "rebench" / "notes.txt"
    stray.parent.mkdir(parents=True)
    stray.write_text("left here")

    assert project.run("peer-review", "rebench") == 0

    assert not stray.exists()


@pytest.mark.parametrize("dry_run", [False, True], ids=["run", "dry-run"])
def test_a_task_that_sets_the_agents_network_is_refused_before_anything_runs(
    project, capfd, monkeypatch, dry_run
):
    if dry_run:
        monkeypatch.setenv("DRY_RUN", "1")
    toml = write_task(project.tasks, "owner__repo-4") / "task.toml"
    toml.write_text(toml.read_text().replace("[agent]\n", '[agent]\nnetwork_mode = "public"\n'))

    code = project.run("peer-review", "rebench")

    out, err = capfd.readouterr()
    assert code != 0
    assert out == ""
    assert err.startswith("error: ") and str(toml) in err, err
    assert_nothing_ran(project)


# With no max_seconds nothing reads the timeout, so a task.toml with no agent
# table reaches the check of its CPUs and memory.
@pytest.mark.parametrize(
    "contents, max_seconds, message",
    [
        ("[agent\n", "2700", "task.toml: not valid TOML"),
        ('environment = "docker"\n', None, "task.toml: no [environment] cpus"),
        ("agent = 3000\n[environment]\ncpus = 1\n", "2700", "task.toml: no [agent] timeout_sec"),
    ],
    ids=["not-toml", "environment-not-a-table", "agent-not-a-table"],
)
def test_a_task_toml_suss_cannot_read_is_an_error_naming_it(
    project, capfd, contents, max_seconds, message
):
    task = project.tasks / "owner__repo-ggg"
    task.mkdir()
    (task / "task.toml").write_text(contents)
    project.write_toml(max_seconds=max_seconds)

    code = project.run("peer-review", "rebench")

    err = capfd.readouterr().err
    assert code != 0
    assert err.startswith("error: ") and f"owner__repo-ggg/{message}" in err, err
    assert_nothing_ran(project)


@pytest.mark.parametrize("check", [False, True], ids=["run", "check"])
def test_a_broken_task_toml_after_the_first_is_refused_on_modal_with_no_max_seconds(
    project, capfd, check
):
    # Sorts after the lane's first task, which is all a check runs.
    task = project.tasks / "zzz__repo-1"
    task.mkdir()
    (task / "task.toml").write_text("[agent\n")
    project.write_toml(environment="modal", max_seconds=None)

    code = project.run("peer-review", "rebench", *(["--check"] if check else []))

    err = capfd.readouterr().err
    assert_nothing_ran(project)
    assert code != 0
    assert err.startswith("error: ") and "zzz__repo-1/task.toml" in err, err


def test_the_max_seconds_error_is_printed_as_the_spec_shows(project, capfd):
    project.write_toml(arm_extra="max_seconds = 3000")

    code = project.run("peer-review", "rebench")

    assert code != 0
    assert capfd.readouterr().err == (
        "error: arm peer-review sets max_seconds = 3000, which must be under the "
        "3000-second agent timeout of the tasks in rebench\n"
    )
    assert_nothing_ran(project)


@pytest.fixture
def two_lanes(monkeypatch) -> None:
    """A second lane beside rebench, built from the same download."""
    monkeypatch.setattr(lanes, "LANES", ("rebench", "senior"))


# Either lane alone over the timeout, so that a value read once for the run,
# from the first lane or the last, passes one of them.
@pytest.mark.parametrize("over", ["rebench", "senior"])
@pytest.mark.parametrize("check", [False, True], ids=["run", "check"])
def test_each_lane_in_a_run_is_checked_against_its_own_max_seconds(
    project, capfd, two_lanes, over, check
):
    project.write_toml(lane_tables=f"[lanes.{over}]\nmax_seconds = 3000\n")

    code = project.run("peer-review", "rebench", "senior", *(["--check"] if check else []))

    assert code != 0
    assert capfd.readouterr().err == (
        "error: arm peer-review sets max_seconds = 3000, which must be under the "
        f"3000-second agent timeout of the tasks in {over}\n"
    )
    assert_nothing_ran(project)


def test_an_arms_own_max_seconds_is_checked_in_place_of_its_lanes(project, capfd):
    project.write_toml(
        arm_extra="max_seconds = 1800", lane_tables="[lanes.rebench]\nmax_seconds = 3000\n"
    )

    assert project.run("peer-review", "rebench") == 0, capfd.readouterr().err


@pytest.mark.parametrize("check", [False, True], ids=["run", "check"])
def test_each_lanes_agent_jobs_name_their_lane_to_the_agent(
    project, capfd, monkeypatch, two_lanes, check
):
    monkeypatch.setenv("DRY_RUN", "1")

    code = project.run("peer-review", "rebench", "senior", *(["--check"] if check else []))

    out, err = capfd.readouterr()
    assert code == 0, err
    agents = [shlex.split(line) for line in out.splitlines() if "suss.agent:SussAgent" in line]
    suffix = "-check" if check else ""
    assert {
        (value(argv, "-p"), tuple(token for flag, token in zip(argv, argv[1:]) if flag == "--ak"))
        for argv in agents
    } == {
        (f"lanes/rebench{suffix}", ("arm=peer-review", "config=suss.toml", "lane=rebench")),
        (f"lanes/senior{suffix}", ("arm=peer-review", "config=suss.toml", "lane=senior")),
    }


@pytest.mark.parametrize(
    "dry_run, check", [(False, False), (True, False), (False, True)], ids=["run", "dry-run", "check"]
)
def test_an_old_max_cost_usd_is_refused_as_the_spec_shows(project, capfd, monkeypatch, dry_run, check):
    if dry_run:
        monkeypatch.setenv("DRY_RUN", "1")
    project.write_toml(arm_extra="max_cost_usd = 5")

    code = project.run("peer-review", "rebench", *(["--check"] if check else []))

    out, err = capfd.readouterr()
    assert code != 0
    assert err == (
        f"error: {project.directory / 'suss.toml'}: [arms.peer-review] sets max_cost_usd, but suss "
        "does not read it; set the arm's spending limit in its params, under the name its "
        "adapter takes, such as max_budget_usd for adapters/claude-code\n"
    )
    assert out == ""
    assert_nothing_ran(project)


@pytest.mark.parametrize(
    "dry_run, check", [(False, False), (True, False), (False, True)], ids=["run", "dry-run", "check"]
)
def test_a_lane_suss_does_not_have_in_suss_toml_is_refused_as_the_spec_shows(
    project, capfd, monkeypatch, dry_run, check
):
    if dry_run:
        monkeypatch.setenv("DRY_RUN", "1")
    project.write_toml(lane_tables="[lanes.rebnech]\nmax_seconds = 2400\n")

    code = project.run("peer-review", "rebench", *(["--check"] if check else []))

    out, err = capfd.readouterr()
    assert code != 0
    assert err == (
        f"error: {project.directory / 'suss.toml'}: sets [lanes.rebnech], but suss has no lane "
        "named rebnech; its lanes are rebench\n"
    )
    assert out == ""
    assert_nothing_ran(project)


def test_a_refused_argument_after_the_separator_is_printed_as_the_spec_shows(project, capfd):
    code = project.run("peer-review", "rebench", "--", "-k", "2")

    assert code != 0
    assert capfd.readouterr().err == (
        "error: -k/--n-attempts can't be passed after --: suss runs one trial per agent "
        "job, and k in suss.toml sets how many jobs\n"
    )
    assert_nothing_ran(project)


@pytest.mark.parametrize(
    "argv, message",
    [
        (["peer-review", "rebench", "--no-such-option"], "--no-such-option"),
        (["peer-review", "rebench", "-x"], "-x"),
        (["peer-review"], "lane"),
        ([], "arm"),
        (["peer-review", "pro-go"], "no lane named pro-go; the only lane is rebench"),
        (["peer-review", "rebench", "pro-go"], "no lane named pro-go; the only lane is rebench"),
        (["peer-review", "rebench", "rebench"], "rebench is named twice"),
        (["no-such-arm", "rebench"], "arm no-such-arm is not defined"),
    ],
    ids=[
        "unknown-option",
        "unknown-short-option",
        "no-lane",
        "no-arm",
        "pro-lane",
        "pro-lane-after-rebench",
        "lane-twice",
        "unknown-arm",
    ],
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

    code = project.run("peer-review", "rebench")

    err = capfd.readouterr().err
    assert code != 0
    assert err.startswith("error: ") and "suss.toml: no such file" in err


def test_a_dry_run_value_other_than_1_is_refused(project, capfd, monkeypatch):
    monkeypatch.setenv("DRY_RUN", "true")

    code = project.run("peer-review", "rebench")

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

    code = project.run("peer-review", "rebench", *(["--check"] if check else []))

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

    code = project.run("peer-review", "rebench")

    assert code == 0, capfd.readouterr().err
    assert len(project.records("harbor")) == 4


def test_docker_runs_without_modals_client(project, monkeypatch):
    monkeypatch.setitem(sys.modules, "modal", None)

    assert project.run("peer-review", "rebench") == 0

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

    code = project.run("peer-review", "rebench")

    assert code != 0
    assert capfd.readouterr().err == (
        "error: concurrency = 4 needs 4 CPUs and 16384 MiB for the tasks in rebench, but Docker "
        "has 3 CPUs and 65536 MiB; set concurrency to 3 or less, or give Docker more\n"
    )
    assert_refused_before_any_lane(project)


def test_docker_desktops_8gb_fits_one_trial_of_4096_mib_when_memory_binds_before_cpus(
    project, capfd, monkeypatch
):
    monkeypatch.setenv("STAND_IN_DOCKER_CAPACITY", DOCKER_DESKTOP_8GB)

    code = project.run("peer-review", "rebench")

    assert code != 0
    assert capfd.readouterr().err == (
        "error: concurrency = 4 needs 4 CPUs and 16384 MiB for the tasks in rebench, but Docker "
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

    code = project.run("peer-review", "rebench")

    err = capfd.readouterr().err
    if fits is None:
        assert code == 0, err
    else:
        assert code != 0
        assert f"set concurrency to {fits} or less" in err, err


def test_the_lanes_largest_cpus_and_largest_memory_set_its_need(project, capfd, monkeypatch):
    write_task(project.tasks, "owner__big-1", cpus=2, memory_mb=1024)
    write_task(project.tasks, "owner__fat-1", cpus=1, memory_mb=8192)
    monkeypatch.setenv("STAND_IN_DOCKER_CAPACITY", f"6 {48 * GIB}")

    code = project.run("peer-review", "rebench")

    assert code != 0
    assert capfd.readouterr().err == (
        "error: concurrency = 4 needs 8 CPUs and 32768 MiB for the tasks in rebench, but Docker "
        "has 6 CPUs and 49152 MiB; set concurrency to 3 or less, or give Docker more\n"
    )
    assert_refused_before_any_lane(project)


def test_a_task_too_large_for_docker_alone_is_refused_with_no_concurrency_offered(
    project, capfd, monkeypatch
):
    monkeypatch.setenv("STAND_IN_DOCKER_CAPACITY", f"10 {3 * GIB}")

    code = project.run("peer-review", "rebench")

    assert code != 0
    assert capfd.readouterr().err == (
        "error: one trial of the tasks in rebench needs 1 CPU and 4096 MiB, but Docker has "
        "10 CPUs and 3072 MiB; give Docker more\n"
    )
    assert_refused_before_any_lane(project)


def test_a_check_counts_only_its_one_task_and_one_trial_at_a_time(project, monkeypatch):
    # Sorts after the lane's first task, which is all a check runs.
    write_task(project.tasks, "zzz__repo-1", cpus=64)
    monkeypatch.setenv("STAND_IN_DOCKER_CAPACITY", f"1 {4 * GIB}")

    assert project.run("peer-review", "rebench", "--check") == 0

    assert len(project.records("harbor")) == 2


def test_a_check_whose_task_does_not_fit_in_docker_is_refused_before_anything_runs(
    project, capfd, monkeypatch
):
    monkeypatch.setenv("STAND_IN_DOCKER_CAPACITY", f"10 {4 * GIB - 1}")

    code = project.run("peer-review", "rebench", "--check")

    assert code != 0
    assert capfd.readouterr().err == (
        "error: one trial of the first task in rebench needs 1 CPU and 4096 MiB, but Docker has "
        "10 CPUs and 4095 MiB; give Docker more\n"
    )
    assert_refused_before_any_lane(project)


def test_an_override_of_the_agent_jobs_cpus_counts_toward_what_it_needs(project, capfd, monkeypatch):
    monkeypatch.setenv("STAND_IN_DOCKER_CAPACITY", f"6 {64 * GIB}")

    code = project.run("peer-review", "rebench", "--", "--override-cpus", "2")

    assert code != 0
    assert capfd.readouterr().err == (
        "error: concurrency = 4 needs 8 CPUs and 16384 MiB for the tasks in rebench under "
        "--override-cpus 2, but Docker has 6 CPUs and 65536 MiB; set concurrency to 3 or "
        "less, or give Docker more\n"
    )
    assert_refused_before_any_lane(project)


def test_a_checks_override_of_the_agent_jobs_memory_counts_toward_its_one_trial(
    project, capfd, monkeypatch
):
    monkeypatch.setenv("STAND_IN_DOCKER_CAPACITY", DOCKER_DESKTOP_8GB)

    code = project.run("peer-review", "rebench", "--check", "--", "--override-memory-mb=8192")

    assert code != 0
    assert capfd.readouterr().err == (
        "error: one trial of the first task in rebench under --override-memory-mb 8192 needs "
        "1 CPU and 8192 MiB, but Docker has 10 CPUs and 7934 MiB; give Docker more\n"
    )
    assert_refused_before_any_lane(project)


def test_an_override_below_the_tasks_own_need_still_leaves_the_regrade_needing_it(
    project, capfd, monkeypatch
):
    monkeypatch.setenv("STAND_IN_DOCKER_CAPACITY", f"10 {3 * GIB}")

    code = project.run("peer-review", "rebench", "--check", "--", "--override-memory-mb", "1024")

    assert code != 0
    assert "needs 1 CPU and 4096 MiB" in capfd.readouterr().err
    assert_refused_before_any_lane(project)


def test_an_override_that_is_not_a_whole_number_is_an_error_before_anything_runs(project, capfd):
    code = project.run("peer-review", "rebench", "--", "--override-cpus", "two")

    err = capfd.readouterr().err
    assert code != 0
    assert err.startswith("error: ") and "--override-cpus" in err and "'two'" in err, err
    assert project.records() == []


def test_on_modal_docker_is_never_asked_and_no_task_needs_cpus_or_memory(project, monkeypatch):
    write_task(project.tasks, "owner__bare-1", cpus=None, memory_mb=None)
    monkeypatch.setenv("STAND_IN_DOCKER_CAPACITY", "1 1024")
    project.write_toml(environment="modal")

    assert project.run("peer-review", "rebench") == 0

    assert project.records("docker") == []


def test_a_dry_run_still_refuses_a_concurrency_that_does_not_fit(project, capfd, monkeypatch):
    monkeypatch.setenv("DRY_RUN", "1")
    monkeypatch.setenv("STAND_IN_DOCKER_CAPACITY", DOCKER_DESKTOP_8GB)

    code = project.run("peer-review", "rebench")

    out, err = capfd.readouterr()
    assert code != 0
    assert out == ""
    assert "set concurrency to 1 or less" in err, err
    assert_refused_before_any_lane(project)


@pytest.mark.parametrize("missing", ["cpus", "memory_mb"])
def test_a_task_with_no_cpus_or_memory_mb_is_an_error_naming_it(project, capfd, missing):
    needs = {"cpus": 1, "memory_mb": 4096, missing: None}
    write_task(project.tasks, "owner__bare-1", **needs)

    code = project.run("peer-review", "rebench")

    err = capfd.readouterr().err
    assert code != 0
    assert err.startswith("error: ")
    assert f"owner__bare-1/task.toml: no [environment] {missing}" in err, err
    assert not (project.directory / "lanes").exists()
    assert project.records("harbor") == []


@pytest.mark.parametrize("value", ["0", "-1", "1.5", '"1"', "true"])
def test_a_task_whose_cpus_is_not_a_positive_whole_number_is_an_error_naming_it(project, capfd, value):
    task = project.tasks / "owner__odd-1"
    task.mkdir()
    (task / "task.toml").write_text(
        f"[agent]\ntimeout_sec = 3000\n\n[environment]\ncpus = {value}\nmemory_mb = 4096\n"
    )

    code = project.run("peer-review", "rebench")

    err = capfd.readouterr().err
    assert code != 0
    assert "owner__odd-1/task.toml: [environment] cpus" in err, err
    assert project.records("harbor") == []


def test_a_docker_engine_that_is_not_running_is_an_error_before_any_lane_is_built(
    project, capfd, monkeypatch
):
    monkeypatch.setenv("STAND_IN_DOCKER_INFO_FAIL", "Cannot connect to the Docker daemon")

    code = project.run("peer-review", "rebench")

    assert code != 0
    assert capfd.readouterr().err == "error: docker info exited 1: Cannot connect to the Docker daemon\n"
    assert_refused_before_any_lane(project)


def test_docker_info_output_suss_cannot_read_is_an_error_quoting_it(project, capfd, monkeypatch):
    monkeypatch.setenv("STAND_IN_DOCKER_CAPACITY", "<no value> <no value>")

    code = project.run("peer-review", "rebench")

    err = capfd.readouterr().err
    assert code != 0
    assert err.startswith("error: ") and "'<no value> <no value>'" in err, err
    assert_refused_before_any_lane(project)


def test_no_docker_program_is_an_error_before_any_lane_is_built(project, capfd, tmp_path):
    code = cli.main(
        ["run", "peer-review", "rebench"],
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


# Images


def test_no_image_is_pulled_on_docker_even_for_a_task_that_names_one(project):
    assert project.run("peer-review", "rebench") == 0

    assert [record["argv"][:1] for record in project.records("docker")] == [["info"]]
    assert len(project.records("harbor")) == 4


def test_nothing_is_pulled_when_the_sandboxes_run_on_modal(project):
    project.write_toml(environment="modal")

    assert project.run("peer-review", "rebench") == 0

    assert project.records("docker") == []
    assert len(project.records("harbor")) == 4


# Dry run


def test_a_dry_run_prints_each_pair_in_run_order_and_runs_nothing(project, capfd, monkeypatch):
    monkeypatch.setenv("DRY_RUN", "1")

    code = project.run("peer-review", "rebench")

    out, err = capfd.readouterr()
    assert code == 0, err
    assert [record["argv"][0] for record in project.records()] == ["info"]
    assert not (project.directory / "lanes").exists()
    root = project.directory.resolve()
    expected = []
    for lane in ("rebench",):
        for trial in (1, 2):
            jobs = f"results/{lane}/peer-review"
            common = f"{project.harbor} run -p lanes/{lane} -e docker -n 4 -k 1"
            expected.append(
                f"{common} -a suss.agent:SussAgent --ak arm=peer-review --ak config=suss.toml "
                f"--ak lane={lane} --allow-agent-host api.anthropic.com --disable-verification "
                f"--jobs-dir {jobs} --job-name {STAMP}-{trial}"
            )
            expected.append(
                f"{common} -a suss.replay:ReplayAgent -m replay "
                f"--ak source_job={root}/{jobs}/{STAMP}-{trial} --jobs-dir {jobs} "
                f"--job-name {STAMP}-{trial}-regrade"
            )
    assert out.splitlines() == expected


def test_a_dry_run_quotes_what_a_shell_would_split(project, capfd, monkeypatch):
    monkeypatch.setenv("DRY_RUN", "1")

    project.run("peer-review", "rebench", "--", "--agent-env", "NOTE=two words")

    agent = capfd.readouterr().out.splitlines()[0]
    assert shlex.split(agent)[-2:] == ["--agent-env", "NOTE=two words"]


# Check


def test_a_check_runs_one_pair_on_the_first_task_and_writes_under_checks(project):
    assert project.run("peer-review", "rebench", "--check") == 0

    records = project.records("harbor")
    assert [value(record["argv"], "--job-name") for record in records] == [f"{STAMP}-1", f"{STAMP}-1-regrade"]
    assert {value(record["argv"], "-p") for record in records} == {"lanes/rebench-check"}
    assert {value(record["argv"], "--jobs-dir") for record in records} == {"checks/rebench/peer-review"}
    check = project.directory / "lanes" / "rebench-check"
    # By code point, so the capitalised owner sorts first.
    assert [task.name for task in check.iterdir()] == ["ArcadeData__arcadedb-4281"]
    assert not (project.directory / "lanes" / "rebench").exists()
    assert [record["argv"][:1] for record in project.records("docker")] == [["info"]]
    assert (project.directory / "checks" / "rebench" / "peer-review" / f"{STAMP}-1").is_dir()
    assert not (project.directory / "results").exists()


# Results


def test_a_second_run_leaves_the_first_runs_jobs_in_place(project):
    project.run("peer-review", "rebench")
    jobs = project.directory / "results" / "rebench" / "peer-review"
    assert jobs.is_dir(), "the first run wrote no jobs"
    first = sorted(path.name for path in jobs.iterdir())
    trials = sorted(str(path) for path in jobs.glob("*/*__*"))

    project.run("peer-review", "rebench", clock=lambda: STARTED + timedelta(days=1))

    after = sorted(path.name for path in jobs.iterdir())
    assert first == [f"{STAMP}-1", f"{STAMP}-1-regrade", f"{STAMP}-2", f"{STAMP}-2-regrade"]
    assert set(first) < set(after) and len(after) == 8
    assert all(Path(trial).is_dir() for trial in trials)


# The verifier's environment

# Every Senior SWE-Bench task's [verifier.env], as the v2026.06 download writes it.
SENIOR_VERIFIER_ENV = """
[verifier.env]
ANTHROPIC_API_KEY = "${ANTHROPIC_API_KEY:-}"
OPENAI_API_KEY = "${OPENAI_API_KEY:-}"
PORTKEY_API_KEY = "${PORTKEY_API_KEY:-}"
SSB_OVERRIDE_ALL_JUDGE_MODEL = "${SSB_OVERRIDE_ALL_JUDGE_MODEL:-}"
SSB_OVERRIDE_CLASSIFIER_MODEL = "${SSB_OVERRIDE_CLASSIFIER_MODEL:-}"
SSB_OVERRIDE_VA_HARNESS = "${SSB_OVERRIDE_VA_HARNESS:-}"
SSB_OVERRIDE_VA_MODEL = "${SSB_OVERRIDE_VA_MODEL:-}"
FORCE_VA_HARNESS = "${FORCE_VA_HARNESS:-}"
FORCE_VA_MODEL = "${FORCE_VA_MODEL:-}"
REPO_NAME = "repo"
TIMEOUT_MULTIPLIER = "${TIMEOUT_MULTIPLIER:-1}"
"""
JUDGE_VARIABLES = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "PORTKEY_API_KEY",
    "SSB_OVERRIDE_ALL_JUDGE_MODEL",
    "SSB_OVERRIDE_CLASSIFIER_MODEL",
    "SSB_OVERRIDE_VA_HARNESS",
    "SSB_OVERRIDE_VA_MODEL",
    "FORCE_VA_HARNESS",
    "FORCE_VA_MODEL",
    "TIMEOUT_MULTIPLIER",
)


@pytest.fixture
def senior(project, two_lanes) -> Project:
    """The download's tasks graded by Senior SWE-Bench's judges, run as the senior lane."""
    for name in TASKS:
        with (project.tasks / name / "task.toml").open("a") as toml:
            toml.write(SENIOR_VERIFIER_ENV)
    return project


def write_dotenv(project: Project, text: str) -> None:
    (project.directory / ".env").write_text(text)


def test_the_agent_job_takes_no_key_its_tasks_read_even_from_suss_own_environment(
    senior, monkeypatch
):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-the-shell")
    write_dotenv(senior, "ANTHROPIC_API_KEY=from-dotenv\n")

    assert senior.run("peer-review", "senior") == 0

    agent, regrade = senior.records("harbor")[:2]
    assert "suss.agent:SussAgent" in agent["argv"]
    assert not set(JUDGE_VARIABLES) & set(agent["env"])
    assert agent["env"]["PATH"] == os.environ["PATH"]
    assert regrade["env"]["ANTHROPIC_API_KEY"] == "from-dotenv"
    assert regrade["env"]["PATH"] == os.environ["PATH"]


def test_the_regrade_gets_an_override_env_sets_and_none_from_suss_own_environment(
    senior, monkeypatch
):
    monkeypatch.setenv("SSB_OVERRIDE_CLASSIFIER_MODEL", "anthropic/claude-opus-4-6")
    write_dotenv(
        senior, "ANTHROPIC_API_KEY=sk-ant-1\nSSB_OVERRIDE_ALL_JUDGE_MODEL=anthropic/claude-opus-4-6\n"
    )

    assert senior.run("peer-review", "senior") == 0

    regrade = senior.records("harbor")[1]
    assert regrade["env"]["SSB_OVERRIDE_ALL_JUDGE_MODEL"] == "anthropic/claude-opus-4-6"
    assert "SSB_OVERRIDE_CLASSIFIER_MODEL" not in regrade["env"]


@pytest.mark.parametrize(
    "dry_run, check", [(False, False), (True, False), (False, True)], ids=["run", "dry-run", "check"]
)
@pytest.mark.parametrize("dotenv", ["", "ANTHROPIC_API_KEY=\n"], ids=["unset", "empty"])
def test_a_missing_grading_key_is_refused_as_the_spec_shows_before_anything_runs(
    senior, capfd, monkeypatch, dry_run, check, dotenv
):
    if dry_run:
        monkeypatch.setenv("DRY_RUN", "1")
    # suss's own environment does not count: the regrade takes the key from .env alone.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-the-shell")
    write_dotenv(senior, dotenv)

    code = senior.run("peer-review", "senior", *(["--check"] if check else []))

    out, err = capfd.readouterr()
    assert code != 0
    assert err == (
        f"error: {senior.directory / '.env'} sets no ANTHROPIC_API_KEY, which the verifier of "
        "the tasks in senior needs for anthropic/claude-sonnet-4-6 and "
        "anthropic/claude-haiku-4-5-20251001, or its judges cannot run; set it, or set "
        "PORTKEY_API_KEY to reach every model through Portkey\n"
    )
    assert out == ""
    assert_nothing_ran(senior)


def test_a_dry_run_names_each_variable_the_regrade_is_given_and_never_its_value(
    senior, capfd, monkeypatch
):
    monkeypatch.setenv("DRY_RUN", "1")
    write_dotenv(
        senior,
        "ANTHROPIC_API_KEY=sk-ant-secret\nSSB_OVERRIDE_ALL_JUDGE_MODEL=anthropic/claude-opus-4-6\n",
    )

    assert senior.run("peer-review", "senior") == 0

    lines = capfd.readouterr().out.splitlines()
    assert "sk-ant-secret" not in "\n".join(lines)
    assert "claude-opus-4-6" not in "\n".join(lines)
    assert len(lines) == 6
    agent, names, regrade = lines[:3]
    assert "suss.agent:SussAgent" in agent and agent.split()[1:3] == ["run", "-y"]
    assert names == "# from .env: ANTHROPIC_API_KEY SSB_OVERRIDE_ALL_JUDGE_MODEL"
    assert "suss.replay:ReplayAgent" in regrade and regrade.split()[1:3] == ["run", "-y"]


def test_a_rebench_regrade_runs_as_before_whatever_env_holds(project, capfd, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-the-shell")
    write_dotenv(project, "ANTHROPIC_API_KEY=from-dotenv\n")

    assert project.run("peer-review", "rebench") == 0

    for record in project.records("harbor")[:2]:
        assert "-y" not in record["argv"]
        assert record["env"]["ANTHROPIC_API_KEY"] == "from-the-shell"

    monkeypatch.setenv("DRY_RUN", "1")
    capfd.readouterr()
    project.run("peer-review", "rebench")
    assert not any(line.startswith("#") for line in capfd.readouterr().out.splitlines())
