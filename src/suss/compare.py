"""
suss compare: reading an arm's trials under `results/`, each with its regrade's
result, the difference between two arms' resolved shares, with an interval, and
the tables the command prints.

Reading never raises on what a trial left or failed to leave. A trial with no
readable record is `harness-failed`. One whose regrade has no readable reward
is unresolved, unless its verifier judged it invalid.
"""

import json
import random
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
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
    # True when the regrade's verifier judged the trial invalid by writing an
    # empty reward, which Harbor records as RewardFileEmptyError. A timeout or
    # a crash also leaves no reward, and is not this.
    invalid: bool
    # The agent trial's directory.
    path: Path
    # The regrade's `verifier_result.rewards`, or None when it has none.
    rewards: Mapping | None = None
    # `rubric_status` in the regrade's verifier/judge_output.json, or None.
    rubric_status: str | None = None


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
        invalid=regrade is not None and _exception(regrade) == "RewardFileEmptyError",
        path=trial,
        rewards=_rewards(regrade) if regrade else None,
        rubric_status=_rubric_status(regrade) if regrade else None,
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
    rewards = _rewards(trial)
    return _number(rewards.get("reward")) if rewards is not None else None


def _rewards(trial: Path) -> dict | None:
    """`verifier_result.rewards` in the trial's result.json, or None."""
    result = _json(trial / "result.json")
    verifier = result.get("verifier_result") if isinstance(result, dict) else None
    rewards = verifier.get("rewards") if isinstance(verifier, dict) else None
    return rewards if isinstance(rewards, dict) else None


def _number(value) -> float | int | None:
    return value if isinstance(value, int | float) and not isinstance(value, bool) else None


def _rubric_status(trial: Path) -> str | None:
    """`rubric_status` in the trial's verifier/judge_output.json, or None."""
    output = _json(trial / "verifier" / "judge_output.json")
    status = output.get("rubric_status") if isinstance(output, dict) else None
    return status if isinstance(status, str) else None


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


# The one lane whose regrades carry the judges' scores.
SENIOR = "senior"

# The rubric judge's status on a task with no rubric criteria, which no arm can be scored on.
NO_CRITERIA = "skipped:no_criteria"


@dataclass(frozen=True)
class Mean:
    """A mean over the trials that have the score, and how many those are."""

    # None when no trial has the score.
    value: Decimal | float | None
    trials: int


@dataclass(frozen=True)
class Means:
    """An arm's mean score from each of a senior regrade's judges."""

    rubric: Mean
    practice: Mean
    relative: Mean
    bloat: Mean


# Each mean's field, the score's key in a regrade's rewards, and the judge's
# flag there. The patch bloat is under the taste judge's flag because, when that
# judge fails, the bloat is measured without the classifier it relies on.
SCORES = (
    ("rubric", "rubric_score", "rubric_judge_ok"),
    ("practice", "taste_practice_alignment", "taste_judge_ok"),
    ("relative", "taste_relative_taste", "taste_judge_ok"),
    ("bloat", "taste_patch_bloat", "taste_judge_ok"),
)


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

    Both tables leave out every invalid trial, and count only the tasks every
    arm has a valid trial of. The second has a row for each arm whose baseline
    is among `arms`, in the order given, and is left out when no arm has one.
    After them come the counted trials whose regrade recorded no test result,
    then the invalid trials of tasks every arm has a trial of, valid or not.
    Each list goes by arm in the order given, then by job and task.
    On the senior lane, a third table gives each arm's mean judges' scores over
    the counted trials, and a last list names the counted trials whose judges
    failed or were skipped, in the same order.

    Raises ConfigError when suss.toml does not define every arm, and
    CompareError when an arm has no trials or only invalid ones, a trial's
    regrade applied none of its patch or could not apply it, two trials carry
    different graders, or the arms share no task.
    """
    baselines = config.baselines(Path(directory) / "suss.toml", list(arms))
    trials = _read(directory, lane, arms)
    _one_grader(lane, trials)
    valid = {arm: [trial for trial in found if not trial.invalid] for arm, found in trials.items()}
    common = set.intersection(*({trial.task for trial in found} for found in valid.values()))
    if not common:
        raise CompareError(
            f"arms {', '.join(arms[:-1])} and {arms[-1]} have no task in common "
            f"under results/{lane}/"
        )
    kept = {arm: [trial for trial in found if trial.task in common] for arm, found in valid.items()}
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
    shared = set.intersection(*({trial.task for trial in found} for found in trials.values()))
    invalid = [
        (arm, trial.job, trial.task)
        for arm in arms
        for trial in trials[arm]
        if trial.invalid and trial.task in shared
    ]
    if lane != SENIOR:
        return render(shares, differences, untested, invalid)
    means = {arm: _means(kept[arm]) for arm in arms}
    unjudged = [
        (arm, trial.job, trial.task, judges)
        for arm in arms
        for trial in kept[arm]
        if (judges := _unjudged(trial))
    ]
    return render(shares, differences, untested, invalid, means, unjudged)


def _means(trials: Sequence[Trial]) -> Means:
    """
    Each score's mean over the trials whose regrade has it under a flag of 1.

    A trial whose judge failed, was skipped, or never ran has no score, and
    takes no part in that mean.
    """
    means = {}
    for field, key, flag in SCORES:
        scores = [
            score
            for trial in trials
            if trial.rewards is not None
            and _number(trial.rewards.get(flag)) == 1
            and (score := _number(trial.rewards.get(key))) is not None
        ]
        # In decimal, so that a mean half way between two places is not a float just under it.
        total = sum(Decimal(str(score)) for score in scores)
        means[field] = Mean(total / len(scores) if scores else None, len(scores))
    return Means(**means)


def _unjudged(trial: Trial) -> list[str]:
    """
    The judges of the trial's regrade that failed or were skipped: `rubric`,
    `taste`, both or neither.

    A regrade with no rewards graded nothing, so none of its judges is named.
    """
    if trial.rewards is None:
        return []
    judges = []
    if _number(trial.rewards.get("rubric_judge_ok")) != 1 and trial.rubric_status != NO_CRITERIA:
        judges.append("rubric")
    if _number(trial.rewards.get("taste_judge_ok")) != 1:
        judges.append("taste")
    return judges


def _read(directory: Path, lane: str, arms: Sequence[str]) -> dict[str, list[Trial]]:
    """
    Every trial of each arm, raising CompareError for an arm with none or with
    only invalid ones, and for the first trial with a patch whose regrade applied none of it or
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
        # Such an arm has no share to print.
        if all(trial.invalid for trial in trials[arm]):
            raise CompareError(
                f"every trial of arm {arm} under results/{lane}/ is invalid: "
                "its regrade wrote an empty reward"
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
    invalid: Sequence[tuple[str, str, str]] = (),
    means: Mapping[str, Means] | None = None,
    unjudged: Sequence[tuple[str, str, str, Sequence[str]]] = (),
) -> str:
    """
    The first table, of each arm's shares, and the second, of each
    (arm, baseline, difference), unless there are none. Then the table of
    `means`, unless it is None. Then each (arm, job, task) in `untested`, then
    in `invalid`, and each (arm, job, task, judges) in `unjudged`, each under
    its own heading, unless there are none.
    """
    header = ["arm", "trials", "resolved", "failed", "timed out"]
    rows = [
        [arm, str(s.trials), _percent(s.resolved), _percent(s.failed), _percent(s.timed_out)]
        for arm, s in shares.items()
    ]
    lines = _table(header, rows)
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
    if means is not None:
        lines += ["", *render_means(means)]
    lines += _named(
        "These trials' regrades recorded no test result, so their grading may have broken:", untested
    )
    lines += _named("These trials' regrades judged them invalid, so neither table counts them:", invalid)
    task_width = max((len(task) for _, _, task, _ in unjudged), default=0)
    lines += _named(
        "These trials' judges failed or were skipped, so the means leave out their scores:",
        [(arm, job, f"{task.ljust(task_width)}  {', '.join(judges)}") for arm, job, task, judges in unjudged],
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


def render_means(means: Mapping[str, Means]) -> list[str]:
    """The lines of the table of each arm's mean judges' scores."""
    header = ["mean (trials)", "rubric", "practice alignment", "relative taste", "patch bloat"]
    rows = [
        [arm] + [_mean(getattr(m, field)) for field, _, _ in SCORES] for arm, m in means.items()
    ]
    return _table(header, rows)


def _table(header: Sequence[str], rows: Sequence[Sequence[str]]) -> list[str]:
    """The rows under the header, the first column to the left and the rest to the right."""
    widths = [max(len(row[i]) for row in [header, *rows]) for i in range(len(header))]
    return [
        "  ".join(
            [row[0].ljust(widths[0])] + [cell.rjust(width) for cell, width in zip(row[1:], widths[1:])]
        )
        for row in [header, *rows]
    ]


def _mean(mean: Mean) -> str:
    """The mean to two places, a half rounded away from zero, then its trials."""
    if mean.value is None:
        return f"- ({mean.trials})"
    places = Decimal(str(mean.value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return f"{places} ({mean.trials})"


def _whole(share: float) -> int:
    """The share as a whole percent, a half rounded away from zero."""
    percent = int(abs(share) * 100 + 0.5)
    return -percent if share < 0 else percent


def _percent(share: float) -> str:
    return f"{_whole(share)}%"


def _signed(share: float) -> str:
    # A share that rounds to 0 from below prints +0%, not -0%.
    return f"{_whole(share):+d}%"
