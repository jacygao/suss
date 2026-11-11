"""
suss leaderboard: reading every arm's trials under `results/rebench/`, each
graded by its own verifier, and the table the command prints.

Reading never raises on what a trial left or failed to leave. One whose
verifier left no readable reward is unresolved, unless the verifier judged it
invalid.
"""

import json
import random
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from suss import config, lanes

LANE = lanes.REBENCH

# The keys of parsed_counts in a rebench verifier's report.
TEST_OUTCOMES = ("PASSED", "FAILED", "SKIPPED", "ERROR")


@dataclass(frozen=True)
class Trial:
    """One trial of an agent job, with what its verifier made of it."""

    # The agent job's name, `<stamp>-<i>`.
    job: str
    task: str
    patch_bytes: int
    resolved: bool
    # False when the verifier's replay.json has `"patch": null`, or when it has
    # no replay.json and raised. None when nothing says either way.
    verifier_applied: bool | None
    # True when the verifier had a patch and its replay.json does not show git
    # applied it.
    verifier_refused: bool
    # The type of the exception the verifier raised, when it wrote no replay.json.
    verifier_raised: str | None
    # Whether the verifier recorded a test result. When it records none, a 0
    # cannot tell a wrong patch from grading that broke.
    verifier_tested: bool
    # True when the verifier judged the trial invalid by writing an
    # empty reward, which Harbor records as RewardFileEmptyError. A timeout or
    # a crash also leaves no reward, and is not this.
    invalid: bool
    # The agent trial's directory.
    path: Path


def read_trials(directory: Path, arm: str) -> list[Trial]:
    """
    Every trial in the arm's agent jobs under `results/rebench/<arm>/`, in
    `directory`, sorted by job and task.

    An arm with no such directory has no trials.
    """
    arm_directory = Path(directory) / "results" / LANE / arm
    if not arm_directory.is_dir():
        return []
    trials = []
    for job in sorted(_directories(arm_directory)):
        for trial in _directories(job):
            task = _task(trial)
            # A trial that names no task cannot be counted against one.
            if task is None:
                continue
            trials.append(_trial(job.name, task, trial))
    return sorted(trials, key=lambda trial: (trial.job, trial.task))


def _trial(job: str, task: str, trial: Path) -> Trial:
    """The trial, as its own verifier graded it."""
    record = _json(trial / "agent" / "suss.json")
    patch_bytes = record.get("patch_bytes") if isinstance(record, dict) else None
    replay_path = trial / "verifier" / "replay.json"
    replay = _json(replay_path)
    # The verifier writes replay.json before any test, so one that raised
    # before writing it counts as applying nothing.
    raised = _exception(trial) if not replay_path.exists() else None
    if isinstance(replay, dict) and "patch" in replay:
        applied = replay["patch"] is not None
    else:
        applied = False if raised is not None else None
    return Trial(
        job=job,
        task=task,
        patch_bytes=patch_bytes if isinstance(patch_bytes, int) else 0,
        resolved=_reward(trial) == 1,
        verifier_applied=applied,
        verifier_refused=applied is True and not _applied_whole(replay),
        verifier_raised=raised,
        verifier_tested=_tested(trial),
        invalid=_exception(trial) == "RewardFileEmptyError",
        path=trial,
    )


def _applied_whole(replay: dict) -> bool:
    """
    Whether the replay.json of a verifier that applied a patch shows that all of
    it applied, which `git apply` does exactly when it exits 0.
    """
    code = replay.get("apply_rc")
    return type(code) is int and code == 0


def _directories(parent: Path) -> list[Path]:
    """
    The directories in parent, leaving out a job's own files.

    Harbor names each trial's directory for its task, and starts the name of
    any directory of its own with a `.`.
    """
    return [entry for entry in parent.iterdir() if entry.is_dir() and not entry.name.startswith(".")]


def _task(trial: Path) -> str | None:
    """
    The trial's task, as the last part of `task_name` in its result.json.

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


def _tested(trial: Path) -> bool:
    """
    Whether `parsed_counts` in the trial's verifier/report.json counts a test.

    A report that is missing, or whose parsed_counts is not counts, counts none.
    """
    report = _json(trial / "verifier" / "report.json")
    found = report.get("parsed_counts") if isinstance(report, dict) else None
    if not isinstance(found, dict):
        return False
    return any(
        isinstance(count, int) and not isinstance(count, bool) and count > 0
        for outcome, count in found.items()
        if outcome in TEST_OUTCOMES
    )


def _exception(trial: Path) -> str | None:
    """
    The type of the exception Harbor recorded as `exception_info` in the
    trial's result.json, `an exception` when it names none, or None.
    """
    result = _json(trial / "result.json")
    info = result.get("exception_info") if isinstance(result, dict) else None
    if info is None:
        return None
    kind = info.get("exception_type") if isinstance(info, dict) else None
    return kind if isinstance(kind, str) and kind else "an exception"


def _json(path: Path):
    """The file's JSON, or None when it is missing or not JSON."""
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


RESAMPLES = 10_000

# Fixed, so the same results always print the same margin.
SEED = 0


def margin(outcomes: Mapping[str, Sequence[bool]]) -> float:
    """
    Half the width of a 95% interval on the resolved rate, resolved trials
    over trials, of `outcomes`, which map each task to its trials' outcomes.

    The interval comes from a bootstrap: each resample draws tasks with
    replacement and takes every trial of each task drawn, so a task's runs
    stay together.
    """
    # Per task: resolved, then trials.
    counts = [(sum(trials), len(trials)) for trials in outcomes.values()]
    rng = random.Random(SEED)
    resampled = [_rate(rng.choices(counts, k=len(counts))) for _ in range(RESAMPLES)]
    # Cut points at every 2.5%: the first is the 2.5th percentile, the last the 97.5th.
    cuts = statistics.quantiles(resampled, n=40, method="inclusive")
    return (cuts[-1] - cuts[0]) / 2


def _rate(counts: Sequence[tuple[int, int]]) -> float:
    resolved, trials = map(sum, zip(*counts))
    return resolved / trials


class LeaderboardError(Exception):
    """The results cannot be ranked, for the reason the message gives."""


@dataclass(frozen=True)
class Standing:
    """An arm's row of the leaderboard."""

    arm: str
    runs: int
    rate: float
    margin: float
    # The share of the arm's tasks that some run resolved.
    pass_at_k: float


def report(directory: Path) -> str:
    """
    What `suss leaderboard --lane rebench` prints, run in `directory`, the
    directory holding suss.toml.

    Each arm is a directory under `results/rebench/`. An invalid trial counts
    nowhere, so a task whose every trial in an arm is invalid is not one of
    that arm's tasks. The table goes by resolved rate, best first, then by arm
    name. After it come the valid trials whose verifier recorded no test
    result, then the invalid trials, each list by arm in the table's order,
    then by job and task.

    Raises ConfigError when suss.toml is missing or an arm in it sets
    `baseline`, and LeaderboardError when no arm has results, an arm has no
    trials or only invalid ones, or a trial's verifier applied none of its
    patch or could not apply it.
    """
    config.check_arms(Path(directory) / "suss.toml")
    trials = _read(directory)
    standings = sorted(
        (_standing(arm, found) for arm, found in trials.items()),
        key=lambda standing: (-standing.rate, standing.arm),
    )
    order = [standing.arm for standing in standings]
    untested = [
        (arm, trial.job, trial.task)
        for arm in order
        for trial in trials[arm]
        if not trial.invalid and not trial.verifier_tested
    ]
    invalid = [(arm, trial.job, trial.task) for arm in order for trial in trials[arm] if trial.invalid]
    return render(standings, untested, invalid)


def _standing(arm: str, trials: Sequence[Trial]) -> Standing:
    by_task: dict[str, list[bool]] = {}
    for trial in trials:
        if not trial.invalid:
            by_task.setdefault(trial.task, []).append(trial.resolved)
    return Standing(
        arm=arm,
        runs=len({trial.job for trial in trials}),
        rate=_rate([(sum(found), len(found)) for found in by_task.values()]),
        margin=margin(by_task),
        pass_at_k=sum(any(found) for found in by_task.values()) / len(by_task),
    )


def _read(directory: Path) -> dict[str, list[Trial]]:
    """
    Every trial of each arm under `results/rebench/`, by arm, raising
    LeaderboardError when there is no arm, for an arm with no trials or with
    only invalid ones, and for the first trial with a patch whose verifier
    applied none of it or could not apply it, so that nothing is ranked that
    cannot be.
    """
    lane_directory = Path(directory) / "results" / LANE
    arms = sorted(arm.name for arm in _directories(lane_directory)) if lane_directory.is_dir() else []
    if not arms:
        raise LeaderboardError(f"no results under results/{LANE}/")
    trials = {}
    for arm in arms:
        trials[arm] = read_trials(directory, arm)
        if not trials[arm]:
            raise LeaderboardError(f"no results for arm {arm} under results/{LANE}/")
        for trial in trials[arm]:
            grading = f"verifier in {trial.job}"
            # That verifier did not grade the patch, so its 0 is not the trial's result.
            if trial.patch_bytes > 0 and trial.verifier_applied is False:
                raised = f"raised {trial.verifier_raised} and " if trial.verifier_raised else ""
                raise LeaderboardError(
                    f"the trial of {trial.task} in results/{LANE}/{arm}/{trial.job} made a "
                    f"{trial.patch_bytes}-byte patch, but its {grading} {raised}applied none"
                )
            # Its result is the grade of a change the harness did not make.
            if trial.verifier_refused:
                raise LeaderboardError(
                    f"the trial of {trial.task} in results/{LANE}/{arm}/{trial.job} made a "
                    f"patch, but its {grading} could not apply it"
                )
        # Such an arm has no rate to print.
        if all(trial.invalid for trial in trials[arm]):
            raise LeaderboardError(
                f"every trial of arm {arm} under results/{LANE}/ is invalid: "
                "its verifier wrote an empty reward"
            )
    return trials


def render(
    standings: Sequence[Standing],
    untested: Sequence[tuple[str, str, str]] = (),
    invalid: Sequence[tuple[str, str, str]] = (),
) -> str:
    """
    The table of `standings`, in the order given, then each (arm, job, task)
    in `untested`, then in `invalid`, each under its own heading, unless there
    are none.
    """
    header = ["arm", "runs", "resolved rate", "pass@k"]
    rows = [
        [
            s.arm,
            str(s.runs),
            f"{_whole(s.rate)}% ±{_whole(s.margin)}",
            f"{_whole(s.pass_at_k)}% @{s.runs}",
        ]
        for s in standings
    ]
    lines = _table(header, rows)
    lines += _named(
        "These trials' verifiers recorded no test result, so their grading may have broken:", untested
    )
    lines += _named(
        "These trials' verifiers judged them invalid, so the leaderboard counts none of them:", invalid
    )
    return "\n".join(lines) + "\n"


def _named(heading: str, trials: Sequence[tuple[str, str, str]]) -> list[str]:
    """The lines naming each (arm, job, task) under the heading, or none when there are none."""
    if not trials:
        return []
    arm_width = max(len(arm) for arm, _, _ in trials)
    job_width = max(len(job) for _, job, _ in trials)
    return ["", heading] + [
        f"  {arm.ljust(arm_width)}  {job.ljust(job_width)}  {task}" for arm, job, task in trials
    ]


def _table(header: Sequence[str], rows: Sequence[Sequence[str]]) -> list[str]:
    """The rows under the header, the first column to the left and the rest to the right."""
    widths = [max(len(row[i]) for row in [header, *rows]) for i in range(len(header))]
    return [
        "  ".join(
            [row[0].ljust(widths[0])] + [cell.rjust(width) for cell, width in zip(row[1:], widths[1:])]
        )
        for row in [header, *rows]
    ]


def _whole(share: float) -> int:
    """The share as a whole percent, a half rounded up."""
    return int(share * 100 + 0.5)
