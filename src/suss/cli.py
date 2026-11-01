"""The suss command, and the `harbor run` command lines it runs."""

import argparse
import codecs
import fcntl
import functools
import importlib.util
import io
import json
import os
import pty
import shlex
import subprocess
import sys
import termios
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

import typer.main
from harbor.cli.main import app as harbor_app
from harbor.cli.utils import parse_kwargs

from suss import config, lanes
from suss.config import Config, ConfigError
from suss.harness import Manifest, ManifestError, load_manifest
from suss.lanes import LaneError


@dataclass(frozen=True)
class Command:
    argv: list[str]
    # Variables to set on top of the environment suss runs in.
    env: dict[str, str] = field(default_factory=dict)


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
    manifest: Manifest,
    lane: str,
    run_stamp: str,
    trial: int,
    harbor_args: Sequence[str] = (),
    check: bool = False,
) -> tuple[Command, Command]:
    """
    The `harbor run` commands for agent job `trial` of the arm on `lane`, and
    for its regrade.

    Both must run in the directory holding suss.toml, which their relative
    paths resolve from. `harbor_args` go to the agent job only. With `check`,
    both take the lane's check directory and write under `checks/` instead of
    `results/`.

    Raises ValueError, with a message for the user, when `harbor_args` include
    a positional argument, an option suss sets on either job, an option that
    changes what the agent job runs, or anything Harbor's parser rejects.
    """
    lane_dir = f"lanes/{lane}-check" if check else f"lanes/{lane}"
    jobs_dir = PurePosixPath("checks" if check else "results", lane, config.arm.name)
    job_name = f"{run_stamp}-{trial}"
    common = [
        "harbor", "run",
        "-p", lane_dir,
        "-e", config.environment,
        "-n", str(config.concurrency),
        "-k", "1",
    ]

    agent = [
        *common,
        "-a", "suss.agent:SussAgent",
        "--ak", _kwarg("arm", config.arm.name),
        "--ak", _kwarg("config", "suss.toml"),
    ]
    for host in manifest.allowed_hosts:
        agent += ["--allow-agent-host", host]
    agent += ["--jobs-dir", str(jobs_dir), "--job-name", job_name]

    # Absolute, so that it names the agent job whatever directory Harbor is in.
    # A source_job that names no job gives every trial reward 0 and fails nothing.
    source_job = config.path.parent / jobs_dir / job_name
    regrade = [
        *common,
        "-a", "patch_replay:PatchReplayAgent",
        "-m", "replay",
        "--ak", _kwarg("source_job", str(source_job)),
        "--jobs-dir", str(jobs_dir),
        "--job-name", f"{job_name}-regrade",
    ]
    _refuse(harbor_args)
    agent += harbor_args
    # Absolute, so that Harbor imports patch_replay whatever directory it is in.
    tooling = config.swe_bench_pro / "v2" / "tooling"
    return Command(agent), Command(regrade, {"PYTHONPATH": str(tooling)})


def _kwarg(key: str, value: str) -> str:
    """
    A `--ak` value that Harbor hands to the agent as the string `value`.

    Harbor parses each value as JSON first, then as a Python literal, and
    strips it, so a value it would not return unchanged is written as a JSON
    string. An ordinary one stays as it is.
    """
    plain = f"{key}={value}"
    if parse_kwargs([plain]) == {key: value}:
        return plain
    return f"{key}={json.dumps(value)}"


_ALL_TASKS = "a run covers every task in its lanes, and the regrade grades them all"
_NO_SIMULATED_USER = "suss runs SussAgent alone, with no simulated user"
_HARBOR_STOPS = "it stops harbor before the job runs; DRY_RUN=1 prints suss's commands instead"

# Every option refused after --, keyed by Harbor's name for it, with where its
# setting comes from instead, or why suss cannot honour it.
_REFUSED = {
    # Those suss sets on either job, even one a job leaves out, such as
    # --allow-agent-host for a harness that allows no hosts.
    "path": "suss gives each job its lane's directory",
    "environment": "set environment in suss.toml",
    "n_concurrent_trials": "set concurrency in suss.toml",
    "n_attempts": "suss runs one trial per agent job, and k in suss.toml sets how many jobs",
    "agent_name": "suss runs every agent job with SussAgent",
    "model_names": "set the arm's model in its params in suss.toml",
    "agent_kwargs": "set the arm's params in suss.toml",
    "allow_agent_hosts": "list the hosts in allowed_hosts in the harness's manifest",
    "jobs_dir": "suss writes each job under results/<lane>/<arm>/, or checks/ with --check",
    "job_name": "suss names each job from the run's stamp and its trial number",
    # Those that would change what the agent job runs. The regrade still grades
    # every task in the lane, so a task the agent job skips scores 0.
    "config_path": "a job config file can change anything suss sets on the job",
    "agent_import_path": "suss runs every agent job with SussAgent",
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
    "install_only": "it skips the agent run, so the regrade would find no patch to grade",
    "launch": "suss runs each agent job locally, where its regrade can read it",
    "dry_run": "it validates a --launch, and suss runs each agent job locally",
    "init": _HARBOR_STOPS,
    "print_config": _HARBOR_STOPS,
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
                f"{'/'.join(param.opts)} can't be passed after --: {_REFUSED[param.name]}"
            )


class _Stop(Exception):
    """An error suss prints as `error: <message>` before it exits non-zero."""


class _Parser(argparse.ArgumentParser):
    def error(self, message: str):
        raise _Stop(message)


@dataclass(frozen=True)
class _Lane:
    name: str
    # Empty when the sandboxes do not run on this machine's Docker.
    images: list[str]
    pairs: list[tuple[Command, Command]]


def harbor_executable() -> Path:
    """
    The `harbor` installed beside this Python, in suss's own environment.

    Harbor imports suss.agent in its own process, so only a Harbor that can
    import suss can run an agent job. One installed as a tool of its own
    cannot, and it may be the one on PATH.
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
    except (_Stop, ConfigError, LaneError, ManifestError) as error:
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
        help="run an arm's agent jobs and their regrades on one or more lanes",
        description="Run K agent jobs on each lane, each followed by its regrade. "
        "Arguments after -- go to every agent job. "
        "With DRY_RUN=1, print the harbor commands instead of running them.",
    )
    run.add_argument("arm", help="an arm defined in suss.toml")
    run.add_argument("lanes", nargs="+", metavar="lane", help=", ".join(lanes.LANES))
    run.add_argument(
        "--check",
        action="store_true",
        help="run one agent job and its regrade on each lane's first task, under checks/",
    )
    return parser.parse_args(argv), harbor_args


def _dry_run() -> bool:
    value = os.environ.get("DRY_RUN", "")
    if value not in ("", "0", "1"):
        raise _Stop(f"DRY_RUN is {value!r}: set it to 1 for a dry run, or leave it unset")
    return value == "1"


def _run(argv: list[str], directory: Path, harbor: Path, docker: str, clock) -> int:
    args, harbor_args = _parse(argv)
    dry_run = _dry_run()
    run_stamp = stamp(clock())
    settings = config.load(directory / "suss.toml", args.arm)
    if settings.environment == "modal":
        _require_modal()
    root = settings.path.parent
    manifest = load_manifest(settings.arm.harness)
    for at, lane in enumerate(args.lanes):
        if lane in args.lanes[:at]:
            raise _Stop(f"{lane} is named twice: each lane runs once in a suss run")

    # Everything that can refuse the run does so here, before any lane is built.
    planned = [
        _plan(settings, manifest, lane, run_stamp, harbor_args, args.check, harbor)
        for lane in args.lanes
    ]
    # A dry run builds no lane: a build removes the links a running job may be reading.
    if dry_run:
        for lane in planned:
            for pair in lane.pairs:
                for command in pair:
                    print(_shell(command))
        return 0

    platform_args = None
    failed_regrades = []
    for lane in planned:
        lanes.build(settings.swe_bench_pro, lane.name, root / "lanes", args.check)
        if lane.images and platform_args is None:
            platform_args = _platform_args(docker, root)
        for image in lane.images:
            code = _call([docker, "pull", *platform_args, image], root)
            if code != 0:
                raise _Stop(f"docker pull of {image} exited {code}, so lane {lane.name} cannot run")
        for agent, regrade in lane.pairs:
            code, output = _stream(agent, root)
            job = root / _option(agent.argv, "--jobs-dir") / _option(agent.argv, "--job-name")
            if not _has_trial(job):
                raise _Stop(
                    f"agent job {job.name} on {lane.name} left no trial in {job}, so suss "
                    f"runs nothing more. Harbor exited {code}, and printed:\n{output.rstrip()}"
                )
            # Whatever the agent job's exit code, which is not its trials' result.
            code, _ = _stream(regrade, root)
            if code != 0:
                name = _option(regrade.argv, "--job-name")
                failed_regrades.append(f"regrade {name} on {lane.name} exited {code}")
    for failure in failed_regrades:
        print(f"error: {failure}, so its trials may not be graded", file=sys.stderr)
    return 1 if failed_regrades else 0


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
    manifest: Manifest,
    lane: str,
    run_stamp: str,
    harbor_args: list[str],
    check: bool,
    harbor: Path,
) -> _Lane:
    """
    The images to pull for the lane, and its pairs of commands, each run as `harbor`.

    Raises LaneError, or _Stop for the arguments after `--`, for a lane the
    arm cannot run.
    """
    # The whole lane even for a check, which tests that the arm can run it.
    found = lanes.read_tasks(lanes.tasks(settings.swe_bench_pro, lane))
    lanes.check_max_seconds(settings.arm, lane, found)
    lanes.check_buildable(settings.path.parent / "lanes", lane, check)
    used = dict(list(found.items())[:1]) if check else found
    images = lanes.images(used) if settings.environment == "docker" else []
    pairs = []
    for trial in range(1, 2 if check else settings.k + 1):
        try:
            commands = harbor_commands(settings, manifest, lane, run_stamp, trial, harbor_args, check)
        except ValueError as error:
            raise _Stop(str(error)) from None
        pairs.append(
            tuple(Command([str(harbor), *command.argv[1:]], command.env) for command in commands)
        )
    return _Lane(lane, images, pairs)


def _environment(extra: dict[str, str]) -> dict[str, str]:
    """
    suss's own environment with `extra` set on top.

    An existing PYTHONPATH is kept after the one in `extra`, so that both
    stay importable and `extra`'s is searched first.
    """
    env = dict(os.environ)
    for name, value in extra.items():
        if name == "PYTHONPATH" and env.get(name):
            value = f"{value}{os.pathsep}{env[name]}"
        env[name] = value
    return env


def _shell(command: Command) -> str:
    """The command as a shell line that runs it as suss would, its variables in front."""
    env = _environment(command.env)
    assignments = [f"{name}={shlex.quote(env[name])}" for name in command.env]
    return " ".join([*assignments, shlex.join(command.argv)])


def _platform_args(docker: str, root: Path) -> list[str]:
    """
    The `docker pull` options that get an amd64 image on this Docker engine.

    The V2 images are amd64 only. The engine's architecture is what counts,
    not this machine's, since the engine may run elsewhere.
    """
    try:
        result = subprocess.run(
            [docker, "info", "--format", "{{.Architecture}}"],
            cwd=root,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        raise _Stop(f"{docker}: no such program, and the sandboxes run on Docker") from None
    if result.returncode != 0:
        raise _Stop(f"docker info exited {result.returncode}: {result.stderr.strip()}")
    if result.stdout.strip() in ("aarch64", "arm64"):
        return ["--platform", "linux/amd64"]
    return []


def _call(argv: list[str], cwd: Path) -> int:
    try:
        return subprocess.run(argv, cwd=cwd).returncode
    except FileNotFoundError:
        raise _Stop(f"{argv[0]}: no such program") from None


def _stream(command: Command, cwd: Path, terminal: bool | None = None) -> tuple[int, str]:
    """
    Run the command, passing its output on as it comes, and return its exit
    code and that output.

    Its stderr is merged into its stdout, and both go to suss's stdout. When
    that is a terminal, or `terminal` is true, the command writes to a
    terminal of its own, so that what draws itself only on a terminal, such
    as Harbor's progress, still shows while the command runs.
    """
    if terminal is None:
        terminal = sys.stdout.isatty()
    if terminal:
        reader, writer = pty.openpty()
        _copy_size(sys.stdout, writer)
    else:
        reader, writer = os.pipe()
    try:
        process = subprocess.Popen(
            command.argv,
            cwd=cwd,
            env=_environment(command.env),
            stdout=writer,
            stderr=writer,
        )
    except FileNotFoundError:
        os.close(reader)
        raise _Stop(f"{command.argv[0]}: no such program") from None
    finally:
        os.close(writer)
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    kept = []
    while True:
        try:
            chunk = os.read(reader, 65536)
        # A terminal's reader fails, rather than reading nothing, once every
        # process writing to it has exited.
        except OSError:
            chunk = b""
        kept.append(decoder.decode(chunk, final=not chunk))
        sys.stdout.write(kept[-1])
        sys.stdout.flush()
        if not chunk:
            break
    os.close(reader)
    return process.wait(), "".join(kept)


def _copy_size(source, terminal: int) -> None:
    """Give the terminal the size of `source`, so that the command lays its output out to fit."""
    try:
        size = fcntl.ioctl(source.fileno(), termios.TIOCGWINSZ, b"\0" * 8)
        fcntl.ioctl(terminal, termios.TIOCSWINSZ, size)
    # Not a terminal, so the command keeps the default size.
    except (OSError, ValueError, io.UnsupportedOperation):
        pass


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

