"""The suss command, and the `harbor run` command lines it runs."""

import argparse
import functools
import importlib.util
import json
import os
import shlex
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

import typer.main
from harbor.cli.main import app as harbor_app
from harbor.cli.utils import parse_kwargs

from suss import agents, compare, config, lanes
from suss.compare import CompareError
from suss.config import GRADING_KEYS, TOKEN_NAME, Config, ConfigError, verifier_env
from suss.lanes import LaneError


VERIFIER = "suss.replay:ReplayVerifier"
JUDGE_VERIFIER = "suss.judge:JudgeVerifier"


@dataclass(frozen=True)
class Command:
    argv: list[str]
    # Variables of suss's own environment that Harbor's must not take.
    withheld: tuple[str, ...] = ()
    # Values from .env that Harbor's environment holds in their place.
    given: Mapping[str, str] = field(default_factory=dict, repr=False)


def stamp(started: datetime) -> str:
    """
    The run's stamp: the time it started, in UTC, as `20261031T142500Z`.

    Raises ValueError for a time with no zone, which would be read as local
    time and stamped as though it were UTC.
    """
    if started.utcoffset() is None:
        raise ValueError(f"{started} has no time zone, so it cannot be put in UTC")
    return started.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def harbor_commands(
    config: Config,
    allowed_hosts: Sequence[str],
    lane: str,
    run_stamp: str,
    trial: int,
    harbor_args: Sequence[str] = (),
    check: bool = False,
    reads: Sequence[str] = (),
    verifier_reads: Sequence[str] = (),
) -> Command:
    """
    The `harbor run` command for agent job `trial` of the arm on `lane`, which
    grades each trial itself in Harbor's separate verifier: ReplayVerifier on
    rebench, and on senior JudgeVerifier, given the lane's judge.

    It must run in the directory holding suss.toml, which its relative paths
    resolve from. `allowed_hosts` are those the arm's agent class lists.
    `harbor_args` are added to it. With `check`, it takes the lane's check
    directory and writes under `checks/` instead of `results/`.

    `reads` are the variables the lane's tasks read from Harbor's environment,
    as `config.host_variables` gives them. The job does not take them from
    suss's own environment. It is given those of `verifier_reads` that .env
    sets, which the tasks read in `[verifier.env]` alone and so reach the
    verifier's test.sh and no sandbox. On senior that leaves out GRADING_KEYS,
    and it withholds the judge's token too.

    Raises ValueError, with a message for the user, when `harbor_args` include
    a positional argument, an option suss sets on the job, an option that
    changes what it runs, `--share` where suss passes `-y`, or anything
    Harbor's parser rejects.
    """
    lane_dir = f"lanes/{lane}-check" if check else f"lanes/{lane}"
    jobs_dir = PurePosixPath("checks" if check else "results", lane, config.arm.name)
    job_name = f"{run_stamp}-{trial}"
    common = [
        "harbor", "run",
        # Harbor asks before a job whose tasks read its environment, and
        # without a terminal it reads no answer and aborts the job.
        *(["-y"] if reads else []),
        "-p", lane_dir,
        "-e", config.environment,
        "-n", str(config.concurrency),
        "-k", "1",
    ]

    arm = config.arm
    agent = [*common, "-a", arm.agent, *(["-m", arm.model] if arm.model else [])]
    for key, value in arm.kwargs.items():
        agent += ["--ak", _kwarg(key, value)]
    agent += [
        "--ak", _kwarg("arm", arm.name),
        "--ak", _kwarg("suss_config", "suss.toml"),
        "--ak", _kwarg("lane", lane),
    ]
    for host in allowed_hosts:
        agent += ["--allow-agent-host", host]
    if lane == lanes.SENIOR:
        agent += ["--verifier", JUDGE_VERIFIER, *_judge_kwargs(config)]
    else:
        agent += ["--verifier", VERIFIER]
    agent += ["--jobs-dir", str(jobs_dir), "--job-name", job_name]
    _refuse(harbor_args)
    if reads:
        _refuse_sharing(harbor_args, lane)
    agent += harbor_args
    given = verifier_env(config, verifier_reads)
    withheld = tuple(reads)
    if lane == lanes.SENIOR:
        given = {name: value for name, value in given.items() if name not in GRADING_KEYS}
        # The judge reads it from .env itself, and gives it to its sessions alone.
        withheld += (TOKEN_NAME,)
    return Command(agent, withheld, given)


def _judge_kwargs(config: Config) -> list[str]:
    """
    The `--verifier-kwarg`s that give JudgeVerifier the lane's judge.

    The token is not among them: Harbor writes each into the job's records,
    so the verifier reads it from .env itself.
    """
    judge = config.judge
    # A number, which Harbor parses as JSON into one.
    kwargs = [f"credit_budget={judge.credit_budget}"]
    for key in ("model", "effort"):
        value = getattr(judge, key)
        if value is not None:
            kwargs.append(_kwarg(key, value))
    return [part for kwarg in kwargs for part in ("--verifier-kwarg", kwarg)]


def _refuse_sharing(harbor_args: Sequence[str], lane: str) -> None:
    """Raise ValueError when `harbor_args` share the job with an organization, which -y confirms."""
    run = _harbor_run()
    parser = run.make_parser(run.context_class(run, info_name="run"))
    given, _, _ = parser.parse_args(list(harbor_args))
    if given.get("share_org"):
        raise ValueError(
            f"--share can't be passed after -- on {lane}: suss passes -y there, which would "
            "share the job with an organization you are not a member of without asking"
        )


def _kwarg(key: str, value: str | int | float | bool) -> str:
    """
    A `--ak` value that Harbor hands to the agent as `value`, of its own type.

    Harbor parses each value as JSON first, then as a Python literal, and
    strips it, so a value it would not return unchanged is written as JSON.
    An ordinary one stays as it is.
    """
    plain = f"{key}={value}"
    parsed = parse_kwargs([plain])
    if parsed == {key: value} and type(parsed[key]) is type(value):
        return plain
    return f"{key}={json.dumps(value)}"


_ALL_TASKS = "a run covers every task in its lanes"
_NO_SIMULATED_USER = "suss runs the arm's agent alone, with no simulated user"
_HARBOR_STOPS = "it stops harbor before the job runs; DRY_RUN=1 prints suss's commands instead"
_OWN_MODELS = "the arm's agent reaches its model through the harness's own service, as it ships"
_GRADED = (
    "suss grades each trial with its own verifier, which applies the trial's patch, and on"
    " senior judges it"
)

# Every option refused after --, keyed by Harbor's name for it, with where its
# setting comes from instead, or why suss cannot honour it.
_REFUSED = {
    # Those suss sets on the job, even one a job leaves out, such as
    # --allow-agent-host for an agent that allows no hosts.
    "path": "suss gives each job its lane's directory",
    "environment": "set environment in suss.toml",
    "n_concurrent_trials": "set concurrency in suss.toml",
    "n_attempts": "suss runs one trial per agent job, and k in suss.toml sets how many jobs",
    "agent_name": "set the arm's agent in suss.toml",
    "model_names": "set the arm's model in suss.toml",
    "agent_kwargs": "set the arm's kwargs in suss.toml",
    "effort": "set the arm's kwargs in suss.toml",
    "allow_agent_hosts": "suss allows the hosts the arm's agent class lists in ALLOWED_HOSTS",
    "agent_env": (
        "the arm's agent takes only the variables its class names, from .env, and an --ae"
        " value would reach every command it runs"
    ),
    "jobs_dir": "suss writes each job under results/<lane>/<arm>/, or checks/ with --check",
    "job_name": "suss names each job from the run's stamp and its trial number",
    "disable_verification": "suss grades each trial in the agent job's own verifier, in a fresh sandbox",
    "verifier": _GRADED,
    "verifier_import_path": _GRADED,
    "verifier_kwargs": _GRADED,
    "yes": "suss answers Harbor's question itself, on a lane whose tasks read Harbor's environment",
    # Those that would change what the agent job runs.
    "config_path": "a job config file can change anything suss sets on the job",
    "agent_import_path": "set the arm's agent in suss.toml",
    "environment_import_path": "set environment in suss.toml",
    "dataset_name_version": _ALL_TASKS,
    "registry_url": _ALL_TASKS,
    "registry_path": _ALL_TASKS,
    "repo": _ALL_TASKS,
    "task_git_url": _ALL_TASKS,
    "task_git_commit_id": _ALL_TASKS,
    "task_ref": _ALL_TASKS,
    "dataset_task_names": _ALL_TASKS,
    "dataset_exclude_task_names": _ALL_TASKS,
    "n_tasks": _ALL_TASKS,
    "user_agent_name": _NO_SIMULATED_USER,
    "user_model": _NO_SIMULATED_USER,
    "user_agent_kwargs": _NO_SIMULATED_USER,
    "user_persona_path": _NO_SIMULATED_USER,
    "user_prompt_template_path": _NO_SIMULATED_USER,
    "bridge_kind": _NO_SIMULATED_USER,
    "bridge_prompt_path": _NO_SIMULATED_USER,
    "bridge_kwargs": _NO_SIMULATED_USER,
    "install_only": "it skips the agent run, so the verifier would find no patch to grade",
    "launch": "suss runs each agent job locally, where its verifier reads the trial on the host",
    "dry_run": "it validates a --launch, and suss runs each agent job locally",
    "print_config": _HARBOR_STOPS,
    "diff_sources": "it reuses or regrades another job's trials in place of running the arm's agent",
    "base_url": _OWN_MODELS,
    "api_key_name": _OWN_MODELS,
    "api_format": _OWN_MODELS,
    "skip_model_parsing": _OWN_MODELS,
    "allow_verifier_hosts": (
        "suss grades each trial with the network its task gives the verifier, and no more"
    ),
    "env_file": (
        "it would give the agent job every variable its tasks read from Harbor's environment; "
        "suss gives it only those .env sets that reach its verifier alone"
    ),
}


@functools.cache
def _harbor_run() -> typer.core.TyperCommand:
    return typer.main.get_command(harbor_app).commands["run"]


def _refuse(harbor_args: Sequence[str]) -> None:
    """
    Raise ValueError, naming the argument, when `harbor_args` include a
    positional argument or an option in `_REFUSED`, whatever its value.

    Harbor's own `run` parser reads them, so every spelling of an option counts,
    and an option's value is never taken for an option. Raises ValueError with
    the parser's message for arguments it rejects.
    """
    run = _harbor_run()
    # The parser alone, so that no option's callback runs and no value is checked.
    parser = run.make_parser(run.context_class(run, info_name="run"))
    try:
        _, positional, found = parser.parse_args(list(harbor_args))
    except Exception as error:
        message = getattr(error, "format_message", lambda: str(error))()
        raise ValueError(f"harbor run would refuse the arguments after --: {message}") from None
    if positional:
        raise ValueError(f"{positional[0]} can't be passed after --: harbor run takes only options")
    passed = {param.name for param in found}
    for param in run.params:
        if param.name in passed and param.name in _REFUSED:
            raise ValueError(
                f"{'/'.join(param.opts + param.secondary_opts)} can't be passed after --: "
                f"{_REFUSED[param.name]}"
            )


# The agent job's options that change what each of its trials takes from Docker.
_OVERRIDES = ("override_cpus", "override_memory_mb")


def _overrides(harbor_args: Sequence[str]) -> dict[str, int]:
    """
    The CPUs and memory the agent job's trials take in place of their tasks',
    from `harbor_args`, keyed by the option that sets each.

    `harbor_args` must have passed `_refuse`. Raises _Stop for a value that is
    not a whole number, which harbor run would refuse.
    """
    run = _harbor_run()
    parser = run.make_parser(run.context_class(run, info_name="run"))
    given, _, _ = parser.parse_args(list(harbor_args))
    found = {}
    for param in run.params:
        if param.name in _OVERRIDES and given.get(param.name) is not None:
            option = param.opts[0]
            try:
                found[option] = int(given[param.name])
            except ValueError:
                raise _Stop(
                    f"suss can't take {option} {given[param.name]!r}: it counts what"
                    " Docker holds in whole numbers"
                ) from None
    return found


class _Stop(Exception):
    """An error suss prints as `error: <message>` before it exits non-zero."""


class _Parser(argparse.ArgumentParser):
    def error(self, message: str):
        raise _Stop(message)


@dataclass(frozen=True)
class _Lane:
    name: str
    # The most CPUs and MiB any one trial takes, or None off this machine's Docker.
    needs: tuple[int, int] | None
    # Which trials those are, as the capacity error names them.
    trials: str
    # Each agent job, which grades its own trials.
    jobs: list[Command]
    # The download the lane is built from.
    download: Path


def harbor_executable() -> Path:
    """
    The `harbor` installed beside this Python, in suss's own environment.

    Harbor imports suss.agents and suss's verifiers in its own process, so only
    a Harbor that can import suss can run an agent job. One
    installed as a tool of its own cannot, and it may be the one on PATH.
    """
    # Not resolved: the environment's python links to an interpreter outside
    # it, and the harbor beside that one is not suss's.
    return Path(sys.executable).parent / "harbor"


def main(
    argv: Sequence[str] | None = None,
    *,
    directory: Path | None = None,
    harbor: Path | None = None,
    docker: str | Path = "docker",
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> int:
    """
    Run the suss command on `argv`, the arguments after the program's name,
    and return its exit code.

    `directory` is where suss.toml is read from, the current directory by
    default. `harbor` and `docker` are the programs run.
    """
    try:
        return _run(
            list(sys.argv[1:] if argv is None else argv),
            Path.cwd() if directory is None else Path(directory),
            harbor_executable() if harbor is None else Path(harbor),
            str(docker),
            clock,
        )
    except (_Stop, ConfigError, LaneError, CompareError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


def _parse(argv: list[str]) -> tuple[argparse.Namespace, list[str]]:
    """The arguments before the first `--`, parsed, and those after it as they stand."""
    # Split here, since argparse would take what follows `--` as more lanes.
    harbor_args: list[str] = []
    if "--" in argv:
        at = argv.index("--")
        argv, harbor_args = argv[:at], argv[at + 1 :]
    parser = _Parser(prog="suss", allow_abbrev=False)
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser(
        "run",
        allow_abbrev=False,
        usage="suss run <arm> <lane>... [--check] [-- <harbor args>]",
        help="run an arm's agent jobs on one or more lanes",
        description="Run K agent jobs on each lane, each graded in its own verifier, "
        "and on senior judged there too. "
        "Arguments after -- go to every agent job. "
        "With DRY_RUN=1, print the harbor commands instead of running them.",
    )
    run.add_argument("arm", help="an arm defined in suss.toml")
    run.add_argument("lanes", nargs="+", metavar="lane", help=", ".join(lanes.LANES))
    run.add_argument(
        "--check",
        action="store_true",
        help="run one agent job on each lane's first task, under checks/",
    )
    compare_ = commands.add_parser(
        "compare",
        allow_abbrev=False,
        usage="suss compare <lane> <arm> <arm>...",
        help="compare arms' results on a lane, each against its baseline",
        description="Print each arm's resolved, failed and timed-out shares, and each "
        "arm's resolved difference from its baseline where that is given too.",
    )
    compare_.add_argument("lane", help=", ".join(lanes.LANES))
    compare_.add_argument("arms", nargs="+", metavar="arm", help="an arm defined in suss.toml")
    return parser.parse_args(argv), harbor_args


def _dry_run() -> bool:
    value = os.environ.get("DRY_RUN", "")
    if value not in ("", "0", "1"):
        raise _Stop(f"DRY_RUN is {value!r}: set it to 1 for a dry run, or leave it unset")
    return value == "1"


def _run(argv: list[str], directory: Path, harbor: Path, docker: str, clock) -> int:
    args, harbor_args = _parse(argv)
    if args.command == "compare":
        return _compare(args.lane, args.arms, harbor_args, directory)
    dry_run = _dry_run()
    run_stamp = stamp(clock())
    # Per lane, since a lane may set its own max_seconds. Nothing else differs between them.
    on_lane = {lane: config.load(directory / "suss.toml", args.arm, lane) for lane in args.lanes}
    settings = on_lane[args.lanes[0]]
    if settings.environment == "modal":
        _require_modal()
    root = settings.path.parent
    # harbor run loads it into its own environment, where it would refill what suss withholds.
    if (root / ".env.local").exists():
        raise _Stop(
            f"{root / '.env.local'} exists, and harbor run, which runs in {root}, would load"
            " it, giving the agent job variables suss keeps from it; move it out"
        )
    agent_class = agents.agent_class(settings.arm.agent, settings.path, args.arm)
    agents.check_arm(agent_class, settings)
    for at, lane in enumerate(args.lanes):
        if lane in args.lanes[:at]:
            raise _Stop(f"{lane} is named twice: each lane runs once in a suss run")

    # Everything that can refuse the run does so here, before any lane is built.
    planned = [
        _plan(on_lane[lane], agent_class.ALLOWED_HOSTS, lane, run_stamp, harbor_args, args.check, harbor)
        for lane in args.lanes
    ]
    if settings.environment == "docker":
        capacity = _docker_capacity(docker, root)
        # A check runs one trial, whatever concurrency is.
        concurrency = 1 if args.check else settings.concurrency
        for lane in planned:
            lanes.check_capacity(lane.trials, lane.needs, concurrency, capacity)
    # A dry run builds no lane: a build empties the lane, which a running job may be reading.
    if dry_run:
        for lane in planned:
            for command in lane.jobs:
                if command.given:
                    print(f"# from .env: {' '.join(command.given)}")
                print(shlex.join(command.argv))
        return 0

    for lane in planned:
        # Harbor builds each task's image when its trial starts, so suss pulls none.
        lanes.build(lane.name, lane.download, root / "lanes", args.check)
        for agent in lane.jobs:
            code = _harbor(agent, root)
            job = root / _option(agent.argv, "--jobs-dir") / _option(agent.argv, "--job-name")
            if not _has_trial(job):
                raise _Stop(
                    f"agent job {job.name} on {lane.name} left no trial in {job}, so suss "
                    f"runs nothing more. Harbor exited {code}; its output is above."
                )
    # Whatever each agent job's exit code, which is not its trials' result.
    return 0


def _compare(lane: str, arms: list[str], harbor_args: list[str], directory: Path) -> int:
    if harbor_args:
        raise _Stop("suss compare takes nothing after --")
    if len(arms) < 2:
        raise _Stop("suss compare takes a lane and at least two arms")
    for at, arm in enumerate(arms):
        if arm in arms[:at]:
            raise _Stop(f"{arm} is named twice: each arm is compared once")
    # Printed whole, after every check, so that an error leaves no partial table.
    print(compare.report(directory, lane, arms), end="")
    return 0


def _require_modal() -> None:
    """
    Raise _Stop, naming the command that installs it, when Modal's client is
    not installed beside suss, where the harbor suss runs imports it from.

    It looks the module up without importing it, so an installed client that
    fails to import is not reported as missing: Harbor's own import of it
    shows the real error.
    """
    if importlib.util.find_spec("modal") is None:
        raise _Stop(
            "environment is modal, but Modal's client is not installed: run uv sync --extra modal"
        )


def _plan(
    settings: Config,
    allowed_hosts: Sequence[str],
    lane: str,
    run_stamp: str,
    harbor_args: list[str],
    check: bool,
    harbor: Path,
) -> _Lane:
    """
    The lane's commands, each run as `harbor`, and what its trials need.

    Raises LaneError, or _Stop for the arguments after `--`, for a lane the
    arm cannot run.
    """
    lanes.check_lane(lane)
    download = settings.download(lane)
    # The whole lane even for a check, which tests that the arm can run it.
    found = lanes.read_tasks(lanes.lane_tasks(lane, download))
    lanes.check_max_seconds(settings.arm, lane, found)
    lanes.check_buildable(lane, download, settings.path.parent / "lanes", check)
    used = dict(list(found.items())[:1]) if check else found
    reads = config.host_variables(used)
    # A variable [environment.env] reads would reach the agent's sandbox too.
    in_sandbox = config.host_variables(used, ("environment",))
    verifier_reads = [
        name for name in config.host_variables(used, ("verifier",)) if name not in in_sandbox
    ]
    config.check_judge(settings, lane)
    on_docker = settings.environment == "docker"
    jobs = []
    for trial in range(1, 2 if check else settings.k + 1):
        try:
            command = harbor_commands(
                settings, allowed_hosts, lane, run_stamp, trial, harbor_args, check, reads,
                verifier_reads,
            )
        except ValueError as error:
            raise _Stop(str(error)) from None
        jobs.append(replace(command, argv=[str(harbor), *command.argv[1:]]))
    needs = None
    trials = f"the first task in {lane}" if check else f"the tasks in {lane}"
    if on_docker:
        cpus, memory_mb = lanes.needs(used)
        overrides = _overrides(harbor_args)
        # The larger of the two, which errs on the side of what Docker can hold.
        cpus = max(cpus, overrides.get("--override-cpus", 0))
        memory_mb = max(memory_mb, overrides.get("--override-memory-mb", 0))
        needs = (cpus, memory_mb)
        trials += "".join(f" under {option} {value}" for option, value in overrides.items())
    return _Lane(lane, needs, trials, jobs, download)


def _docker_capacity(docker: str, root: Path) -> tuple[int, int]:
    """The Docker engine's CPUs, and its memory in bytes."""
    output = _docker_info(docker, root, "{{.NCPU}} {{.MemTotal}}")
    try:
        cpus, memory = (int(field) for field in output.split())
    except ValueError:
        raise _Stop(
            f"docker info gave {output!r} for the engine's CPUs and memory, "
            "which suss cannot read as two whole numbers"
        ) from None
    return cpus, memory


def _docker_info(docker: str, root: Path, format: str) -> str:
    """What `docker info` prints for `format`, stripped, raising _Stop when it cannot run."""
    try:
        result = subprocess.run(
            [docker, "info", "--format", format],
            cwd=root,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        raise _Stop(f"{docker}: no such program, and the sandboxes run on Docker") from None
    if result.returncode != 0:
        raise _Stop(f"docker info exited {result.returncode}: {result.stderr.strip()}")
    return result.stdout.strip()


def _harbor(command: Command, cwd: Path) -> int:
    """Run the command on suss's own stdout and stderr, and return its exit code."""
    sys.stdout.flush()
    try:
        return subprocess.run(command.argv, cwd=cwd, env=_environment(command)).returncode
    except FileNotFoundError:
        raise _Stop(f"{command.argv[0]}: no such program") from None


def _environment(command: Command) -> dict[str, str]:
    """suss's own environment, less what the command withholds, with what it is given."""
    env = {name: value for name, value in os.environ.items() if name not in command.withheld}
    return {**env, **command.given}


def _option(argv: list[str], name: str) -> str:
    return argv[argv.index(name) + 1]


def _has_trial(job: Path) -> bool:
    """
    Whether the job directory holds a trial's directory.

    Harbor names each trial's directory for its task, and starts the name of
    any directory of its own with a `.`.
    """
    return job.is_dir() and any(
        entry.is_dir() and not entry.name.startswith(".") for entry in job.iterdir()
    )

