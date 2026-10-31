"""The suss command, and the `harbor run` command lines it runs."""

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import PurePosixPath

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

    Paths in them are relative to the directory holding suss.toml, which both
    must run in, except the regrade's PYTHONPATH. `harbor_args` go to the
    agent job only. With `check`, both take the lane's check directory and
    write under `checks/` instead of `results/`.
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
    agent += ["--jobs-dir", str(jobs_dir), "--job-name", job_name, *harbor_args]

    regrade = [
        *common,
        "-a", "patch_replay:PatchReplayAgent",
        "-m", "replay",
        "--ak", _kwarg("source_job", str(jobs_dir / job_name)),
        "--jobs-dir", str(jobs_dir),
        "--job-name", f"{job_name}-regrade",
    ]
    # Absolute, so that Harbor imports patch_replay whatever directory it is in.
    tooling = config.swe_bench_pro / "v2" / "tooling"
    return Command(agent), Command(regrade, {"PYTHONPATH": str(tooling)})


def _kwarg(key: str, value: str) -> str:
    """
    A `--ak` value that Harbor hands to the agent as the string `value`.

    Harbor parses each value as JSON first, then as a Python literal, and
    strips it, so a value it would not return unchanged is written as a JSON
    string. An ordinary one stays as it is, as the spec writes it.
    """
    plain = f"{key}={value}"
    if parse_kwargs([plain]) == {key: value}:
        return plain
    return f"{key}={json.dumps(value)}"
