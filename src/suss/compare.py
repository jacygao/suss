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
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from suss import config, judge, lanes

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
    # Whether the regrade's verifier recorded a test result. When it records
    # none, a 0 cannot tell a wrong patch from grading that broke.
    regrade_tested: bool
    # True when the regrade's verifier judged the trial invalid by writing an
    # empty reward, which Harbor records as RewardFileEmptyError. A timeout or
    # a crash also leaves no reward, and is not this.
    invalid: bool
    # The agent trial's directory.
    path: Path
    # The judges' scores and flags, in the shape of the verifier's rewards:
    # the regrade's agent/judge.json when it has one, and on senior its
    # `verifier_result.rewards` otherwise. None when it has neither.
    rewards: Mapping | None = None
    # The rubric judge's status, from the same place as `rewards`, or None.
    rubric_status: str | None = None
    # Who gave `rewards` when judge.json did. None when senior's verifier did,
    # whose judge model only the grader records.
    judge: str | None = None
    # What graded the trial, and in which job, as an error names it.
    grading: str = ""


def read_trials(directory: Path, lane: str, arm: str) -> list[Trial]:
    """
    Every trial in the arm's agent jobs under `results/<lane>/<arm>/`, in the
    directory holding suss.toml, sorted by job and task.

    An arm with no such directory has no trials.
    """
    arm_directory = Path(directory) / "results" / lane / arm
    if not arm_directory.is_dir():
        return []
    separate = lane in lanes.SEPARATE_VERIFIER
    trials = []
    for job in sorted(_directories(arm_directory)):
        if job.name.endswith(REGRADE):
            continue
        regrades = {} if separate else _by_task(arm_directory / f"{job.name}{REGRADE}")
        for trial in _directories(job):
            task = _task(trial)
            # A trial that names no task cannot be paired or compared.
            if task is None:
                continue
            grading = trial if separate else regrades.get(task)
            trials.append(_trial(lane, job.name, task, trial, grading))
    return sorted(trials, key=lambda trial: (trial.job, trial.task))


def _trial(lane: str, job: str, task: str, trial: Path, regrade: Path | None) -> Trial:
    """
    The trial, graded by `regrade`: the regrade's trial of its task, or on a
    lane in `lanes.SEPARATE_VERIFIER` the trial itself.
    """
    separate = lane in lanes.SEPARATE_VERIFIER
    record = _json(trial / "agent" / "suss.json")
    if not isinstance(record, dict):
        record = {"status": "harness-failed"}
    patch_bytes = record.get("patch_bytes")
    replayed_by = "verifier" if separate else "agent"
    replay_path = regrade / replayed_by / "replay.json" if regrade else None
    replay = _json(replay_path) if replay_path else None
    # ReplayAgent writes replay.json last, so a regrade that raised before
    # writing it counts as applying nothing.
    raised = _exception(regrade) if replay_path and not replay_path.exists() else None
    if isinstance(replay, dict) and "patch" in replay:
        applied = replay["patch"] is not None
    else:
        applied = False if raised is not None else None
    judged = _json(regrade / "agent" / judge.RECORD_NAME) if regrade else None
    if isinstance(judged, dict):
        rewards, rubric_status, judged_by = _judge_scores(judged)
    elif regrade and lane == lanes.SENIOR:
        rewards, rubric_status, judged_by = _rewards(regrade), _rubric_status(regrade), None
    else:
        rewards, rubric_status, judged_by = None, None, None
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
        regrade_tested=regrade is not None
        and (_senior_tested(regrade) if lane == lanes.SENIOR else _tested(regrade)),
        invalid=regrade is not None and _exception(regrade) == "RewardFileEmptyError",
        path=trial,
        rewards=rewards,
        rubric_status=rubric_status,
        judge=judged_by,
        grading=f"verifier in {job}" if separate else f"regrade in {job}{REGRADE}",
    )


def _judge_scores(record: dict) -> tuple[dict, str | None, str]:
    """
    The scores in a judge.json with each judge's flag 1 exactly when its
    status is `ok`, then the rubric judge's status, then who gave the scores.

    The status is the judge's, not its first session's, so a judge that
    answered on its retry is judged.
    """
    scores = record.get("scores")
    flags = {f"{kind}_judge_ok": int(record.get(f"{kind}_status") == "ok") for kind in judge.JUDGES}
    status = record.get("rubric_status")
    named = record.get("judge")
    parts = [named.get(key) for key in ("client", "model", "effort")] if isinstance(named, dict) else []
    judged_by = " ".join(part for part in parts if isinstance(part, str) and part)
    return (
        (scores if isinstance(scores, dict) else {}) | flags,
        status if isinstance(status, str) else None,
        judged_by or "a judge.json that names no judge",
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


def _senior_tested(trial: Path) -> bool:
    """
    Whether the trial's verifier/verifier_results.json counts a test in
    `total`, or says the task has no test to run.

    A senior verifier writes a `total` of 0 with `all_pass` true only for a task
    with no tests, which its validation agent alone grades. When its tests
    crashed or ran none, it writes `all_pass` false and `runner_errors`.
    """
    results = _json(trial / "verifier" / "verifier_results.json")
    if not isinstance(results, dict):
        return False
    total = results.get("total")
    if type(total) is not int:
        return False
    if total > 0:
        return True
    return total == 0 and results.get("all_pass") is True and "runner_errors" not in results


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
    """An arm's mean score from each of a regrade's judges."""

    rubric: Mean
    practice: Mean
    relative: Mean
    bloat: Mean
    # Each judge that gave the arm's trials a score, and how many trials it
    # scored. With more than one, every mean above is None.
    judges: Mapping[str, int] = field(default_factory=dict)


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

    Every table leaves out every invalid trial, and counts only the tasks every
    arm has a valid trial of. The second has a row for each arm whose baseline
    is among `arms`, in the order given, and is left out when no arm has one.
    After them come the counted trials whose regrade recorded no test result,
    then the invalid trials of tasks every arm has a trial of, valid or not.
    Each list goes by arm in the order given, then by job and task.
    On the senior lane, and on any lane where a regrade wrote judge.json, a
    third table gives each arm's mean judges' scores over the counted trials,
    and a last list names the counted trials whose judges failed or were
    skipped, in the same order. The table names the judges when judge.json
    gave any score, since senior's verifier is otherwise the only one.

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
    # Elsewhere than senior, only judge.json gives scores.
    by_judge = any(trial.judge is not None for found in kept.values() for trial in found)
    if lane != lanes.SENIOR and not by_judge:
        return render(shares, differences, untested, invalid)
    verifier = _verifier_judge(trials)
    means = {arm: _means(kept[arm], verifier) for arm in arms}
    unjudged = [
        (arm, trial.job, trial.task, judges)
        for arm in arms
        for trial in kept[arm]
        if (judges := _unjudged(trial))
    ]
    return render(shares, differences, untested, invalid, means, unjudged, by_judge)


def _verifier_judge(trials: Mapping[str, Sequence[Trial]]) -> str:
    """
    Senior's verifier as a judge, named by the judge model in the grader.

    Every grader is the same one by now, so a trial with none takes the others'.
    """
    graders = (trial.grader for found in trials.values() for trial in found if trial.grader is not None)
    grader = next(graders, None)
    model = grader.get("judge_model") if isinstance(grader, dict) else None
    if isinstance(model, str) and model:
        return f"senior verifier {model}"
    return "senior verifier, whose judge model no grader records"


def _means(trials: Sequence[Trial], verifier: str) -> Means:
    """
    Each score's mean over the trials whose regrade has it under a flag of 1,
    when one judge scored them all. `verifier` names senior's verifier as a judge.

    A trial whose judge failed, was skipped, or never ran has no score, and
    takes no part in that mean, nor in which judges scored the arm.
    """
    judges = Counter(
        trial.judge or verifier
        for trial in trials
        if trial.rewards is not None
        and any(_number(trial.rewards.get(flag)) == 1 for _, _, flag in SCORES)
    )
    means = {}
    for name, key, flag in SCORES:
        scores = [
            score
            for trial in trials
            if len(judges) == 1
            and trial.rewards is not None
            and _number(trial.rewards.get(flag)) == 1
            and (score := _number(trial.rewards.get(key))) is not None
        ]
        # In decimal, so that a mean half way between two places is not a float just under it.
        total = sum(Decimal(str(score)) for score in scores)
        means[name] = Mean(total / len(scores) if scores else None, len(scores))
    return Means(**means, judges=dict(judges))


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
                    f"{trial.patch_bytes}-byte patch, but its {trial.grading} {raised}applied none"
                )
            # Its result is the grade of a change the harness did not make.
            if trial.regrade_refused:
                raise CompareError(
                    f"the trial of {trial.task} in results/{lane}/{arm}/{trial.job} made a "
                    f"patch, but its {trial.grading} could not apply it"
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

    Graders with the same fields, where those are more than a dataset and a
    digest, print only the fields they differ in, as `<field> <JSON value>`.
    Any others print in full when the short form would read the same or does
    not fit them.
    """
    def short(grader) -> str | None:
        if isinstance(grader, dict) and grader.keys() == {"dataset", "digest"}:
            if all(isinstance(value, str) for value in grader.values()):
                return f"{grader['dataset']} ({grader['digest'][:8]})"
        return None

    named = short(one), short(other)
    if None not in named and named[0] != named[1]:
        return named
    if (
        isinstance(one, dict)
        and isinstance(other, dict)
        and one.keys() == other.keys() != {"dataset", "digest"}
    ):
        fields = [field for field in one if one[field] != other[field]]
        first, second = (
            " and ".join(f"{field} {json.dumps(grader[field])}" for field in fields)
            for grader in (one, other)
        )
        return first, second
    return json.dumps(one, sort_keys=True), json.dumps(other, sort_keys=True)


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
    name_judges: bool = False,
) -> str:
    """
    The first table, of each arm's shares, and the second, of each
    (arm, baseline, difference), unless there are none. Then the table of
    `means`, unless it is None, as render_means gives it. Then each
    (arm, job, task) in `untested`, then in `invalid`, and each
    (arm, job, task, judges) in `unjudged`, each under its own heading, unless
    there are none.
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
        lines += ["", *render_means(means, name_judges)]
    lines += _named(
        "These trials' regrades recorded no test result, so their grading may have broken:", untested
    )
    lines += _named("These trials' regrades judged them invalid, so no table counts them:", invalid)
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


def render_means(means: Mapping[str, Means], name_judges: bool = False) -> list[str]:
    """
    The lines of the table of each arm's mean judges' scores, then a line for
    each arm that more than one judge scored, then one saying the arms' means
    do not compare when the other arms' judges differ.

    The table names each arm's judge in a last column when `name_judges` is
    true or some arm has more than one.
    """
    header = ["mean (trials)", "rubric", "practice alignment", "relative taste", "patch bloat"]
    rows = [
        [arm] + [_mean(getattr(m, name)) if len(m.judges) < 2 else "-" for name, _, _ in SCORES]
        for arm, m in means.items()
    ]
    mixed = {arm: m.judges for arm, m in means.items() if len(m.judges) > 1}
    single = {arm: next(iter(m.judges)) for arm, m in means.items() if len(m.judges) == 1}
    lines = _table(header, rows)
    if name_judges or mixed:
        cells = ["more than one" if arm in mixed else single.get(arm, "-") for arm in means]
        lines = [f"{line}  {cell}" for line, cell in zip(lines, ["judge", *cells])]
    notes = []
    for arm, judges in mixed.items():
        counted = [f"{name} on {_trials(count)}" for name, count in judges.items()]
        notes.append(f"{arm} was judged by {_joined(counted)}, so it has no mean.")
    if len(set(single.values())) > 1:
        judged_by = [f"{arm} by {name}" for arm, name in single.items()]
        judged_by[0] = judged_by[0].replace(" by ", " was judged by ", 1)
        notes.append(f"The means do not compare across arms: {', '.join(judged_by)}.")
    return lines + (["", *notes] if notes else [])


def _trials(count: int) -> str:
    return f"{count} trial" if count == 1 else f"{count} trials"


def _joined(items: Sequence[str]) -> str:
    """The items as a list in a sentence: `a`, `a and b`, `a, b and c`."""
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} and {items[-1]}"


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
