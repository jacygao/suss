"""
suss compare: reading an arm's trials under `results/`, each with its regrade's
result, and the difference between two arms' resolved shares, with an interval.

Reading never raises on what a trial left or failed to leave. A trial with no
readable record is `harness-failed`, and one whose regrade has no readable
reward is unresolved.
"""

import json
import random
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

REGRADE = "-regrade"


@dataclass(frozen=True)
class Trial:
    """One trial of an agent job, with what its regrade made of it."""

    # The agent job's name, `<stamp>-<i>`.
    job: str
    # The task's directory name, as PatchReplayAgent matches it.
    task: str
    status: str
    # None when the trial has no suss.json.
    grader: dict | None
    patch_bytes: int
    resolved: bool
    # False when the regrade's replay.json names no patch, or there is none.
    regrade_applied: bool
    # The agent trial's directory.
    path: Path


def read_trials(directory: Path, lane: str, arm: str) -> list[Trial]:
    """
    Every trial in the arm's agent jobs under `results/<lane>/<arm>/`, in the
    directory holding suss.toml, sorted by job and task.

    An arm with no such directory has no trials.
    """
    arm_directory = Path(directory) / "results" / lane / arm
    if not arm_directory.is_dir():
        return []
    trials = []
    for job in sorted(_directories(arm_directory)):
        if job.name.endswith(REGRADE):
            continue
        regrades = _by_task(arm_directory / f"{job.name}{REGRADE}")
        for trial in _directories(job):
            task = _task(trial)
            # A trial that names no task cannot be paired or compared.
            if task is None:
                continue
            trials.append(_trial(job.name, task, trial, regrades.get(task)))
    return sorted(trials, key=lambda trial: (trial.job, trial.task))


def _trial(job: str, task: str, trial: Path, regrade: Path | None) -> Trial:
    record = _json(trial / "agent" / "suss.json")
    if not isinstance(record, dict):
        record = {"status": "harness-failed"}
    patch_bytes = record.get("patch_bytes")
    replay = _json(regrade / "agent" / "replay.json") if regrade else None
    return Trial(
        job=job,
        task=task,
        status=record.get("status", "harness-failed"),
        grader=record.get("grader"),
        patch_bytes=patch_bytes if isinstance(patch_bytes, int) else 0,
        resolved=regrade is not None and _reward(regrade) == 1,
        regrade_applied=isinstance(replay, dict) and replay.get("patch") is not None,
        path=trial,
    )


def _by_task(job: Path) -> dict[str, Path]:
    """The job's trial directories by task, or none when the job is missing."""
    if not job.is_dir():
        return {}
    trials = {}
    for trial in _directories(job):
        task = _task(trial)
        if task is not None:
            trials[task] = trial
    return trials


def _directories(parent: Path) -> list[Path]:
    """
    The directories in parent, leaving out a job's own files.

    Harbor names each trial's directory for its task, and starts the name of
    any directory of its own with a `.`.
    """
    return [entry for entry in parent.iterdir() if entry.is_dir() and not entry.name.startswith(".")]


def _task(trial: Path) -> str | None:
    """
    The trial's task, as the last part of `task_name` in its result.json, which
    is how PatchReplayAgent finds a trial's patch.

    A trial Harbor stopped before writing result.json still names its task in
    config.json. None when neither does.
    """
    result = _json(trial / "result.json")
    if isinstance(result, dict) and isinstance(result.get("task_name"), str):
        return result["task_name"].rsplit("/", 1)[-1]
    config = _json(trial / "config.json")
    task = config.get("task") if isinstance(config, dict) else None
    if isinstance(task, dict) and isinstance(task.get("path"), str):
        return task["path"].rstrip("/").rsplit("/", 1)[-1]
    return None


def _reward(trial: Path) -> float | int | None:
    """`verifier_result.rewards.reward` in the trial's result.json, or None."""
    result = _json(trial / "result.json")
    verifier = result.get("verifier_result") if isinstance(result, dict) else None
    rewards = verifier.get("rewards") if isinstance(verifier, dict) else None
    reward = rewards.get("reward") if isinstance(rewards, dict) else None
    return reward if isinstance(reward, int | float) and not isinstance(reward, bool) else None


def _json(path: Path):
    """The file's JSON, or None when it is missing or not JSON."""
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


RESAMPLES = 10_000

# Fixed, so the same results always print the same interval.
SEED = 0


@dataclass(frozen=True)
class Difference:
    """An arm's resolved share minus its baseline's, and a 95% interval on it."""

    estimate: float
    low: float
    high: float


def resolved_difference(
    arm: Mapping[str, Sequence[bool]], baseline: Mapping[str, Sequence[bool]]
) -> Difference:
    """
    The arm's resolved share minus the baseline's, with a 95% interval.

    Each arm maps a task to its trials' outcomes, True where the trial resolved.
    The estimate comes from the outcomes as given. The interval comes from a
    bootstrap: each resample draws tasks with replacement, once for both arms,
    and takes every trial of each task drawn. A share is resolved trials over
    trials, so a task with fewer trials than another weighs less.

    Raises ValueError when the arms are not over the same tasks, have no tasks,
    or a task has no trials in one of them.
    """
    if arm.keys() != baseline.keys():
        raise ValueError("the arms must be over the same tasks")
    if not arm:
        raise ValueError("the arms have no tasks")
    names = list(arm)
    for name in names:
        if not arm[name] or not baseline[name]:
            raise ValueError(f"task {name} has no trials in one of the arms")

    # Per task: resolved and trials in the arm, then in the baseline.
    counts = [
        (sum(arm[n]), len(arm[n]), sum(baseline[n]), len(baseline[n])) for n in names
    ]
    estimate = _difference(counts)

    rng = random.Random(SEED)
    resampled = [
        _difference(rng.choices(counts, k=len(counts))) for _ in range(RESAMPLES)
    ]
    # Cut points at every 2.5%: the first is the 2.5th percentile, the last the 97.5th.
    cuts = statistics.quantiles(resampled, n=40, method="inclusive")
    return Difference(estimate, cuts[0], cuts[-1])


def _difference(counts: Sequence[tuple[int, int, int, int]]) -> float:
    resolved, trials, base_resolved, base_trials = map(sum, zip(*counts))
    return resolved / trials - base_resolved / base_trials
