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
