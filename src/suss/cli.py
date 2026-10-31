"""The suss command, and the `harbor run` command lines it runs."""

import functools
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import PurePosixPath

import typer.main
from harbor.cli.main import app as harbor_app
from harbor.cli.utils import parse_kwargs

from suss.config import Config
from suss.harness import Manifest


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
    an option suss sets on either job, or one Harbor's parser rejects.
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
    _refuse_options_suss_sets(harbor_args)
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


# Every option suss sets on either job, even one a job leaves out, such as
# --allow-agent-host for a harness that allows no hosts. Each maps Harbor's name
# for it to where that setting comes from instead.
_SET_BY_SUSS = {
    "path": "suss gives each job its lane's directory",    "environment": "set environment in suss.toml",
    "n_concurrent_trials": "set concurrency in suss.toml",
    "n_attempts": "suss runs one trial per agent job, and k in suss.toml sets how many jobs",
    "agent_name": "suss runs every agent job with SussAgent",
    "model_names": "set the arm's model in its params in suss.toml",
    "agent_kwargs": "set the arm's params in suss.toml",
    "allow_agent_hosts": "list the hosts in allowed_hosts in the harness's manifest",
    "jobs_dir": "suss writes each job under results/<lane>/<arm>/, or checks/ with --check",
    "job_name": "suss names each job from the run's stamp and its trial number",
}


@functools.cache
def _harbor_run() -> typer.core.TyperCommand:
    return typer.main.get_command(harbor_app).commands["run"]


def _options(argv: Sequence[str]) -> set[str]:
    """
    The name of each option Harbor's `run` parser finds in the `harbor run`
    command `argv`.

    Raises ValueError with the parser's own message for an argv it rejects.
    """
    run = _harbor_run()
    # The parser alone, so that no option's callback runs and no value is checked.
    parser = run.make_parser(run.context_class(run, info_name="run"))
    try:
        _, _, found = parser.parse_args(list(argv[2:]))
    except Exception as error:
        message = getattr(error, "format_message", lambda: str(error))()
        raise ValueError(f"harbor run would refuse the arguments after --: {message}") from None
    return {param.name for param in found}


def _refuse_options_suss_sets(harbor_args: Sequence[str]) -> None:
    """
    Raise ValueError naming an option suss sets that `harbor_args` include,
    whatever its value, since Harbor would take theirs over suss's.
    """
    passed = _options(["harbor", "run", *harbor_args]) & _SET_BY_SUSS.keys()
    for param in _harbor_run().params:
        if param.name in passed:
            raise ValueError(
                f"{'/'.join(param.opts)} can't be passed after --: {_SET_BY_SUSS[param.name]}"
            )
