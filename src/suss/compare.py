"""
suss compare: reading an arm's trials under `results/`, each with its regrade's
result, the difference between two arms' resolved shares, with an interval, and
the tables the command prints.

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

from suss import config

REGRADE = "-regrade"

# The keys of parsed_counts in a rebench verifier's report.
TEST_OUTCOMES = ("PASSED", "FAILED", "SKIPPED", "ERROR")


@dataclass(frozen=True)
class Trial:
    """One trial of an agent job, with what its regrade made of it."""

    # The agent job's name, `<stamp>-<i>`.
    job: str
    task: str
    status: str
    # None when the trial has no suss.json.
    grader: dict | None
    patch_bytes: int
    resolved: bool
    # False when the regrade's replay.json has `"patch": null`, or when it has
    # no replay.json and raised. None when nothing says either way, as when
    # there is no regrade trial.
    regrade_applied: bool | None
    # True when the regrade had a patch and its replay.json does not show git
    # applied it.
    regrade_refused: bool
    # The type of the exception the regrade raised, when it wrote no replay.json.
    regrade_raised: str | None
    # Whether the regrade's verifier/report.json counts a test as passed,
    # failed, skipped or errored. When it counts none, a 0 cannot tell a wrong
    # patch from grading that broke.
    regrade_tested: bool
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
    replay_path = regrade / "agent" / "replay.json" if regrade else None
    replay = _json(replay_path) if replay_path else None
    # ReplayAgent writes replay.json last, so a regrade that raised before
    # writing it counts as applying nothing.
    raised = _exception(regrade) if replay_path and not replay_path.exists() else None
    if isinstance(replay, dict) and "patch" in replay:
        applied = replay["patch"] is not None
    else:
        applied = False if raised is not None else None
    return Trial(
        job=job,
        task=task,
        status=record.get("status", "harness-failed"),
        grader=record.get("grader"),
        patch_bytes=patch_bytes if isinstance(patch_bytes, int) else 0,
        resolved=regrade is not None and _reward(regrade) == 1,
        regrade_applied=applied,
        regrade_refused=applied is True and not _applied_whole(replay),
        regrade_raised=raised,
        regrade_tested=regrade is not None and _tested(regrade),
        path=trial,
    )


def _applied_whole(replay: dict) -> bool:
    """
    Whether the replay.json of a regrade that applied a patch shows that all of
    it applied, which `git apply` does exactly when it exits 0.
    """
    code = replay.get("apply_rc")
    return type(code) is int and code == 0


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
    The trial's task, as the last part of `task_name` in its result.json.

    A trial and its regrade's trial run the same task, so both read the same
    name here, and this is what pairs them. A trial Harbor stopped before writing result.json still names its task in
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


class CompareError(Exception):
    """The arms' results cannot be compared, for the reason the message gives."""


@dataclass(frozen=True)
class Shares:
    """An arm's trials, and the share of them resolved, failed and timed out."""

    trials: int
    resolved: float
    failed: float
    timed_out: float


def report(directory: Path, lane: str, arms: Sequence[str]) -> str:
    """
    What `suss compare` prints for the arms on the lane, run in `directory`,
    the directory holding suss.toml.

    Both tables count only the tasks every arm has trials for. The second has
    a row for each arm whose baseline is among `arms`, in the order given, and
    is left out when no arm has one. After them come the trials of those tasks
    whose regrade recorded no test result, by arm in the order given, then by
    job and task.

    Raises ConfigError when suss.toml does not define every arm, and
    CompareError when an arm has no trials, a trial's regrade applied none of
    its patch or could not apply it, two trials carry different graders, or the arms
    share no task.
    """
    baselines = config.baselines(Path(directory) / "suss.toml", list(arms))
    trials = _read(directory, lane, arms)
    _one_grader(lane, trials)
    common = set.intersection(*({trial.task for trial in found} for found in trials.values()))
    if not common:
        raise CompareError(
            f"arms {', '.join(arms[:-1])} and {arms[-1]} have no task in common "
            f"under results/{lane}/"
        )
    kept = {arm: [trial for trial in found if trial.task in common] for arm, found in trials.items()}
    shares = {arm: _shares(kept[arm]) for arm in arms}
    outcomes = {arm: _outcomes(kept[arm]) for arm in arms}
    differences = [
        (arm, baselines[arm], resolved_difference(outcomes[arm], outcomes[baselines[arm]]))
        for arm in arms
        if baselines[arm] in arms
    ]
    untested = [
        (arm, trial.job, trial.task) for arm in arms for trial in kept[arm] if not trial.regrade_tested
    ]
    return render(shares, differences, untested)


def _read(directory: Path, lane: str, arms: Sequence[str]) -> dict[str, list[Trial]]:
    """
    Every trial of each arm, raising CompareError for an arm with none, and
    for the first trial with a patch whose regrade applied none of it or
    could not apply it, so that nothing is compared that cannot be.
    """
    trials = {}
    for arm in arms:
        trials[arm] = read_trials(directory, lane, arm)
        if not trials[arm]:
            raise CompareError(f"no results for arm {arm} under results/{lane}/")
        for trial in trials[arm]:
            # That regrade did not grade the patch, so its 0 is not the trial's result.
            if trial.patch_bytes > 0 and trial.regrade_applied is False:
                raised = f"raised {trial.regrade_raised} and " if trial.regrade_raised else ""
                raise CompareError(
                    f"the trial of {trial.task} in results/{lane}/{arm}/{trial.job} made a "
                    f"{trial.patch_bytes}-byte patch, but its regrade in "
                    f"{trial.job}{REGRADE} {raised}applied none"
                )
            # Its result is the grade of a change the harness did not make.
            if trial.regrade_refused:
                raise CompareError(
                    f"the trial of {trial.task} in results/{lane}/{arm}/{trial.job} made a "
                    f"patch, but its regrade in {trial.job}{REGRADE} could not apply it"
                )
    return trials


def _one_grader(lane: str, trials: Mapping[str, Sequence[Trial]]) -> None:
    """
    Raise CompareError naming the first two graders that differ.

    Every trial read counts, including one on a task only some arms have. A
    trial with no suss.json has no grader, and takes no part.
    """
    first = None
    for arm, found in trials.items():
        for trial in found:
            if trial.grader is None:
                continue
            if first is None:
                first = (arm, trial)
            elif trial.grader != first[1].grader:
                named = _graders(first[1].grader, trial.grader)
                raise CompareError(
                    f"trials carry different graders: {named[0]} in "
                    f"results/{lane}/{first[0]}/{first[1].job}, "
                    f"{named[1]} in results/{lane}/{arm}/{trial.job}"
                )


def _graders(one, other) -> tuple[str, str]:
    """
    The two graders as a person reads them, `<dataset> (<first 8 of digest>)`.

    Graders that would read the same that way, or that are not a dataset and a
    digest, print in full.
    """
    def short(grader) -> str | None:
        if isinstance(grader, dict) and grader.keys() == {"dataset", "digest"}:
            if all(isinstance(value, str) for value in grader.values()):
                return f"{grader['dataset']} ({grader['digest'][:8]})"
        return None

    named = short(one), short(other)
    if None in named or named[0] == named[1]:
        return json.dumps(one, sort_keys=True), json.dumps(other, sort_keys=True)
    return named


def _outcomes(trials: Sequence[Trial]) -> dict[str, list[bool]]:
    """Whether each trial resolved, by task, as resolved_difference takes them."""
    by_task: dict[str, list[bool]] = {}
    for trial in trials:
        by_task.setdefault(trial.task, []).append(trial.resolved)
    return by_task


def _shares(trials: Sequence[Trial]) -> Shares:
    count = len(trials)
    return Shares(
        trials=count,
        resolved=sum(trial.resolved for trial in trials) / count,
        failed=sum(trial.status == "harness-failed" for trial in trials) / count,
        timed_out=sum(trial.status == "timed-out" for trial in trials) / count,
    )


def render(
    shares: Mapping[str, Shares],
    differences: Sequence[tuple[str, str, Difference]],
    untested: Sequence[tuple[str, str, str]] = (),
) -> str:
    """
    The first table, of each arm's shares, and the second, of each
    (arm, baseline, difference), unless there are none. Then each
    (arm, job, task) in `untested`, unless there are none.
    """
    header = ["arm", "trials", "resolved", "failed", "timed out"]
    rows = [
        [arm, str(s.trials), _percent(s.resolved), _percent(s.failed), _percent(s.timed_out)]
        for arm, s in shares.items()
    ]
    widths = [max(len(row[i]) for row in [header, *rows]) for i in range(len(header))]
    lines = [
        "  ".join(
            [row[0].ljust(widths[0])] + [cell.rjust(width) for cell, width in zip(row[1:], widths[1:])]
        )
        for row in [header, *rows]
    ]
    if differences:
        labels = [f"{arm} - {baseline}" for arm, baseline, _ in differences]
        estimates = [_signed(difference.estimate) for _, _, difference in differences]
        label_width = max(len(label) for label in ["vs. baseline", *labels])
        estimate_width = max(len(estimate) for estimate in estimates)
        lines += ["", f"{'vs. baseline'.ljust(label_width)}   resolved"]
        for label, estimate, (_, _, difference) in zip(labels, estimates, differences):
            lines.append(
                f"{label.ljust(label_width)}   {estimate.rjust(estimate_width)} "
                f"[{_signed(difference.low)}, {_signed(difference.high)}]"
            )
    if untested:
        arm_width = max(len(arm) for arm, _, _ in untested)
        job_width = max(len(job) for _, job, _ in untested)
        lines += ["", "These trials' regrades recorded no test result, so their grading may have broken:"]
        for arm, job, task in untested:
            lines.append(f"  {arm.ljust(arm_width)}  {job.ljust(job_width)}  {task}")
    return "\n".join(lines) + "\n"


def _whole(share: float) -> int:
    """The share as a whole percent, a half rounded away from zero."""
    percent = int(abs(share) * 100 + 0.5)
    return -percent if share < 0 else percent


def _percent(share: float) -> str:
    return f"{_whole(share)}%"


def _signed(share: float) -> str:
    # A share that rounds to 0 from below prints +0%, not -0%.
    return f"{_whole(share):+d}%"
