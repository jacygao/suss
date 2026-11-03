import json
import random
import shutil
import time
from pathlib import Path

import pytest

from suss import cli, compare
from suss.compare import Difference, Trial, read_trials, resolved_difference

LANE = "pro-go"
ARM = "claude-code"
GRADER = {"dataset": "swe-bench-pro-v2", "commit": "66f92766"}
FLIPT = "instance_flipt-io__flipt-02e21636c58e86c51119b63e0fb5ca7b813b07b1"
# Harbor truncates a trial directory's name, so this task's trials begin as
# FLIPT's do.
FLIPT_TOO = "instance_flipt-io__flipt-02e21639a0b1c2d3e4f5a6b7c8d9e0f1a2b3c4d5"
NAVIDROME = "instance_navidrome__navidrome-0130c6dc13438b48cf0fdfab08a89e357b5517c9"
# Harbor's own reward when a regrade resolves its task.
RESOLVED = {"rewards": {"reward": 1.0}}
UNRESOLVED = {"rewards": {"reward": 0.0}}


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def write_job(job: Path) -> Path:
    """A job directory with the four files Harbor writes beside its trials."""
    job.mkdir(parents=True, exist_ok=True)
    write_json(job / "config.json", {"job_name": job.name, "jobs_dir": str(job.parent)})
    write_json(job / "result.json", {"id": "job", "n_total_trials": 1, "stats": {}})
    write_json(job / "lock.json", {})
    (job / "job.log").write_text("")
    return job


def trial_directory(job: Path, task: str, suffix: str) -> Path:
    """The trial's directory, named as Harbor names it: the task cut short, and a random suffix."""
    return job / f"{task[:32]}__{suffix}"


def write_agent_trial(
    job: Path,
    task: str,
    suffix: str,
    *,
    status: str = "ok",
    patch_bytes: int = 4182,
    grader=GRADER,
    record: bool = True,
) -> Path:
    trial = trial_directory(write_job(job), task, suffix)
    write_json(trial / "config.json", {"task": {"path": f"lanes/{LANE}/{task}"}})
    write_json(
        trial / "result.json",
        {"task_name": f"swebench-pro/{task}", "trial_name": trial.name, "verifier_result": None},
    )
    (trial / "agent").mkdir()
    (trial / "agent" / "model.patch").write_text("x" * patch_bytes)
    if record:
        write_json(
            trial / "agent" / "suss.json",
            {
                "arm": ARM,
                "grader": grader,
                "status": status,
                "exit_codes": {"install": 0, "run": 0},
                "run_seconds": 1290.4,
                "patch_bytes": patch_bytes,
            },
        )
    return trial


def write_regrade_trial(
    job: Path, task: str, suffix: str, verifier_result=RESOLVED, *, patch="model.patch"
) -> Path:
    """
    The regrade's trial of the task. `patch` is what `replay.json` names, None
    when the regrade applied nothing.
    """
    trial = trial_directory(write_job(job), task, suffix)
    write_json(trial / "config.json", {"task": {"path": f"lanes/{LANE}/{task}"}})
    write_json(
        trial / "result.json",
        {
            "task_name": f"swebench-pro/{task}",
            "trial_name": trial.name,
            "agent_info": {"name": "patch-replay-agent"},
            "verifier_result": verifier_result,
        },
    )
    replay = {"task": task, "patch": patch}
    if patch is not None:
        replay["apply_rc"] = 0
    write_json(trial / "agent" / "replay.json", replay)
    return trial


@pytest.fixture
def arm(tmp_path) -> Path:
    return tmp_path / "results" / LANE / ARM


def by_job(trials: list[Trial]) -> dict[tuple[str, str], Trial]:
    return {(trial.job, trial.task): trial for trial in trials}


def test_a_trial_carries_its_record_and_its_regrades_result(tmp_path, arm):
    write_agent_trial(arm / "20261101T005320Z-1", FLIPT, "kY8wt23", status="timed-out", patch_bytes=6490)
    write_regrade_trial(arm / "20261101T005320Z-1-regrade", FLIPT, "NfDX2zK")

    [trial] = read_trials(tmp_path, LANE, ARM)

    assert trial.job == "20261101T005320Z-1"
    assert trial.task == FLIPT
    assert trial.status == "timed-out"
    assert trial.grader == GRADER
    assert trial.patch_bytes == 6490
    assert trial.resolved is True
    assert trial.regrade_applied is True


def test_trials_pair_with_their_regrade_by_task_name_and_not_by_directory(tmp_path, arm):
    # Both tasks' directories share a prefix, and the regrade's suffixes sort
    # the other way round from the agent job's.
    write_agent_trial(arm / "S-1", FLIPT, "aaaaaaa")
    write_agent_trial(arm / "S-1", FLIPT_TOO, "bbbbbbb")
    write_regrade_trial(arm / "S-1-regrade", FLIPT, "zzzzzzz", RESOLVED)
    write_regrade_trial(arm / "S-1-regrade", FLIPT_TOO, "yyyyyyy", UNRESOLVED)

    trials = by_job(read_trials(tmp_path, LANE, ARM))

    assert {task: trial.resolved for (_, task), trial in trials.items()} == {
        FLIPT: True,
        FLIPT_TOO: False,
    }


def test_a_trial_pairs_only_with_its_own_jobs_regrade(tmp_path, arm):
    # The same task in three jobs from two runs, each regraded differently.
    rewards = {"A-1": RESOLVED, "A-2": UNRESOLVED, "B-1": RESOLVED, "B-2": UNRESOLVED}
    for job, reward in rewards.items():
        write_agent_trial(arm / job, FLIPT, f"agent{job}")
        write_regrade_trial(arm / f"{job}-regrade", FLIPT, f"regrd{job}", reward)

    trials = read_trials(tmp_path, LANE, ARM)

    assert {trial.job: trial.resolved for trial in trials} == {
        "A-1": True,
        "A-2": False,
        "B-1": True,
        "B-2": False,
    }


def test_a_regrade_job_is_not_read_as_an_agent_job(tmp_path, arm):
    write_agent_trial(arm / "S-1", FLIPT, "kY8wt23")
    write_agent_trial(arm / "S-1", NAVIDROME, "Qp3rT9a")
    write_regrade_trial(arm / "S-1-regrade", FLIPT, "NfDX2zK")
    write_regrade_trial(arm / "S-1-regrade", NAVIDROME, "Lm8vB2c")

    trials = read_trials(tmp_path, LANE, ARM)

    assert sorted((trial.job, trial.task) for trial in trials) == [
        ("S-1", FLIPT),
        ("S-1", NAVIDROME),
    ]


def test_a_job_directorys_own_files_are_not_trials(tmp_path, arm):
    write_agent_trial(arm / "S-1", FLIPT, "kY8wt23")
    write_regrade_trial(arm / "S-1-regrade", FLIPT, "NfDX2zK")
    # A job's result.json, given a task_name, is still not a trial.
    write_json(arm / "S-1" / "result.json", {"task_name": f"swebench-pro/{NAVIDROME}"})

    trials = read_trials(tmp_path, LANE, ARM)

    assert [trial.task for trial in trials] == [FLIPT]


def test_a_trial_with_no_record_is_harness_failed_with_no_grader(tmp_path, arm):
    write_agent_trial(arm / "S-1", FLIPT, "kY8wt23", record=False)
    write_regrade_trial(arm / "S-1-regrade", FLIPT, "NfDX2zK", UNRESOLVED, patch=None)

    [trial] = read_trials(tmp_path, LANE, ARM)

    assert (trial.task, trial.status, trial.grader, trial.patch_bytes) == (
        FLIPT,
        "harness-failed",
        None,
        0,
    )


@pytest.mark.parametrize(
    "verifier_result",
    [None, {}, {"rewards": None}, {"rewards": {}}, {"rewards": {"other": 1.0}}],
    ids=["null", "empty", "null-rewards", "no-reward", "other-reward"],
)
def test_a_regrade_with_no_reward_is_unresolved(tmp_path, arm, verifier_result):
    write_agent_trial(arm / "S-1", FLIPT, "kY8wt23")
    write_agent_trial(arm / "S-1", NAVIDROME, "Qp3rT9a")
    write_regrade_trial(arm / "S-1-regrade", FLIPT, "NfDX2zK", verifier_result)
    write_regrade_trial(arm / "S-1-regrade", NAVIDROME, "Lm8vB2c", RESOLVED)

    trials = by_job(read_trials(tmp_path, LANE, ARM))

    assert trials["S-1", FLIPT].resolved is False
    assert trials["S-1", NAVIDROME].resolved is True


def test_a_regrade_trial_with_no_verifier_result_key_is_unresolved(tmp_path, arm):
    write_agent_trial(arm / "S-1", FLIPT, "kY8wt23")
    regrade = write_regrade_trial(arm / "S-1-regrade", FLIPT, "NfDX2zK")
    write_json(regrade / "result.json", {"task_name": f"swebench-pro/{FLIPT}"})

    [trial] = read_trials(tmp_path, LANE, ARM)

    assert (trial.task, trial.resolved) == (FLIPT, False)


def test_a_trial_with_no_regrade_trial_is_unresolved(tmp_path, arm):
    write_agent_trial(arm / "S-1", FLIPT, "kY8wt23")
    write_agent_trial(arm / "S-1", NAVIDROME, "Qp3rT9a")
    write_regrade_trial(arm / "S-1-regrade", NAVIDROME, "Lm8vB2c")
    # A run stopped before the second job's regrade began.
    write_agent_trial(arm / "S-2", FLIPT, "Zx4cV6b")

    trials = by_job(read_trials(tmp_path, LANE, ARM))

    assert {key: trial.resolved for key, trial in trials.items()} == {
        ("S-1", FLIPT): False,
        ("S-1", NAVIDROME): True,
        ("S-2", FLIPT): False,
    }
    assert trials["S-1", FLIPT].regrade_applied is None


def test_a_regrade_trial_with_no_replay_json_says_nothing_of_its_patch(tmp_path, arm):
    write_agent_trial(arm / "S-1", FLIPT, "kY8wt23")
    regrade = write_regrade_trial(arm / "S-1-regrade", FLIPT, "NfDX2zK", UNRESOLVED)
    (regrade / "agent" / "replay.json").unlink()

    [trial] = read_trials(tmp_path, LANE, ARM)

    assert (trial.resolved, trial.regrade_applied) == (False, None)


@pytest.mark.parametrize("reward", [1, 1.0])
def test_a_reward_of_one_resolves_whether_int_or_float(tmp_path, arm, reward):
    write_agent_trial(arm / "S-1", FLIPT, "kY8wt23")
    write_regrade_trial(arm / "S-1-regrade", FLIPT, "NfDX2zK", {"rewards": {"reward": reward}})

    [trial] = read_trials(tmp_path, LANE, ARM)

    assert trial.resolved is True


def test_a_reward_short_of_one_is_unresolved(tmp_path, arm):
    write_agent_trial(arm / "S-1", FLIPT, "kY8wt23")
    write_regrade_trial(arm / "S-1-regrade", FLIPT, "NfDX2zK", {"rewards": {"reward": 0.75}})

    [trial] = read_trials(tmp_path, LANE, ARM)

    assert (trial.task, trial.resolved) == (FLIPT, False)


def test_a_regrade_that_applied_no_patch_says_so(tmp_path, arm):
    write_agent_trial(arm / "S-1", FLIPT, "kY8wt23", patch_bytes=6490)
    write_agent_trial(arm / "S-1", NAVIDROME, "Qp3rT9a")
    write_regrade_trial(arm / "S-1-regrade", FLIPT, "NfDX2zK", UNRESOLVED, patch=None)
    write_regrade_trial(arm / "S-1-regrade", NAVIDROME, "Lm8vB2c")

    trials = by_job(read_trials(tmp_path, LANE, ARM))

    assert trials["S-1", FLIPT].regrade_applied is False
    assert trials["S-1", FLIPT].patch_bytes == 6490
    assert trials["S-1", NAVIDROME].regrade_applied is True


def test_nothing_under_checks_is_read(tmp_path, arm):
    checks = tmp_path / "checks" / LANE / ARM
    write_agent_trial(checks / "S-1", NAVIDROME, "Qp3rT9a")
    write_regrade_trial(checks / "S-1-regrade", NAVIDROME, "Lm8vB2c")
    write_agent_trial(arm / "S-1", FLIPT, "kY8wt23")
    write_regrade_trial(arm / "S-1-regrade", FLIPT, "NfDX2zK")

    trials = read_trials(tmp_path, LANE, ARM)

    assert [(trial.job, trial.task) for trial in trials] == [("S-1", FLIPT)]


def test_an_arm_with_no_results_has_no_trials(tmp_path):
    write_agent_trial(tmp_path / "checks" / LANE / ARM / "S-1", FLIPT, "kY8wt23")

    assert read_trials(tmp_path, LANE, ARM) == []


def outcomes(resolved: int, unresolved: int, k: int = 1) -> list[list[bool]]:
    return [[True] * k] * resolved + [[False] * k] * unresolved


def tasks(per_task: list[list[bool]]) -> dict[str, list[bool]]:
    return {f"task-{i}": trials for i, trials in enumerate(per_task)}


def test_the_estimate_is_the_difference_in_resolved_shares():
    arm = tasks(outcomes(30, 10))
    baseline = tasks(outcomes(20, 20))
    assert resolved_difference(arm, baseline).estimate == pytest.approx(0.25)


def test_a_task_counts_with_the_trials_it_has():
    # Task a has 3 trials and b has 1: the share over trials is 3/4, a mean over tasks 1/2.
    arm = {"a": [True, True, True], "b": [False]}
    baseline = {"a": [False, False, False], "b": [False]}
    assert resolved_difference(arm, baseline).estimate == pytest.approx(0.75)


def test_a_known_difference_lands_inside_the_interval():
    draw = random.Random(7)
    arm = {f"task-{i}": [draw.random() < 0.6 for _ in range(3)] for i in range(400)}
    baseline = {f"task-{i}": [draw.random() < 0.4 for _ in range(3)] for i in range(400)}
    result = resolved_difference(arm, baseline)
    assert result.low < 0.2 < result.high
    assert result.low <= result.estimate <= result.high


def test_identical_arms_differ_by_exactly_zero():
    arm = tasks(outcomes(12, 8, k=3))
    assert resolved_difference(arm, dict(arm)) == Difference(0.0, 0.0, 0.0)


def test_one_draw_of_tasks_is_shared_by_both_arms():
    # Paired, the difference is only how often the one task that differs is drawn.
    baseline = tasks(outcomes(50, 50))
    arm = dict(baseline, **{"task-99": [True]})
    result = resolved_difference(arm, baseline)
    assert result.estimate == pytest.approx(0.01)
    assert 0.0 <= result.low and result.high <= 0.05, (
        f"[{result.low}, {result.high}] is too wide for arms that differ on one "
        "task in 100: are the arms drawn apart?"
    )


def test_a_drawn_task_brings_all_its_trials():
    # Trials agree within each task, so the interval is as wide as 20 draws allow, not 100.
    arm = tasks(outcomes(10, 10, k=5))
    baseline = tasks(outcomes(0, 20, k=5))
    result = resolved_difference(arm, baseline)
    assert result.high - result.low > 0.35, (
        f"[{result.low}, {result.high}] is as narrow as a draw of single trials gives"
    )


def test_the_ends_are_the_2_5th_and_97_5th_percentiles():
    # Arms differ on 2 tasks of 40, so a resampled difference is X/40 with
    # X ~ Binomial(40, 0.05): its 2.5th percentile is 0, its 97.5th 5 and its 95th 4.
    baseline = tasks(outcomes(0, 40))
    arm = dict(baseline, **{"task-0": [True], "task-1": [True]})
    result = resolved_difference(arm, baseline)
    assert (result.low, result.high) == pytest.approx((0.0, 5 / 40))


def test_the_same_input_gives_the_same_interval():
    # Trial counts vary by task, so a different stream of draws moves the ends.
    draw = random.Random(11)
    arm, baseline = {}, {}
    for i in range(60):
        k = draw.randint(1, 5)
        arm[f"task-{i}"] = [draw.random() < 0.5 for _ in range(k)]
        baseline[f"task-{i}"] = [draw.random() < 0.4 for _ in range(draw.randint(1, 5))]
    random.seed(1)
    state = random.getstate()
    first = resolved_difference(arm, baseline)
    assert random.getstate() == state, "it drew from the module-level random state"
    random.seed(2)
    assert resolved_difference(arm, baseline) == first


def test_it_draws_ten_thousand_resamples(monkeypatch):
    draws = []

    class Counting(random.Random):
        def choices(self, population, *args, k=1, **kwargs):
            draws.append(k)
            return super().choices(population, *args, k=k, **kwargs)

    monkeypatch.setattr(compare.random, "Random", Counting)
    resolved_difference(tasks(outcomes(3, 4)), tasks(outcomes(5, 2)))
    assert draws == [7] * 10_000, "expected 10,000 resamples, each drawing 7 tasks"


def test_ten_thousand_resamples_over_256_tasks_take_under_a_second():
    draw = random.Random(3)
    arm = {f"task-{i}": [draw.random() < 0.5 for _ in range(3)] for i in range(256)}
    baseline = {f"task-{i}": [draw.random() < 0.5 for _ in range(3)] for i in range(256)}
    start = time.perf_counter()
    resolved_difference(arm, baseline)
    assert time.perf_counter() - start < 1.0


def test_arms_over_different_tasks_are_refused():
    with pytest.raises(ValueError, match="same tasks"):
        resolved_difference({"a": [True]}, {"b": [True]})


def test_a_task_with_no_trials_is_refused():
    with pytest.raises(ValueError, match="no trials"):
        resolved_difference({"a": [True], "b": []}, {"a": [True], "b": [False]})


def test_arms_with_no_tasks_are_refused():
    with pytest.raises(ValueError, match="no tasks"):
        resolved_difference({}, {})


# Defined out of the order the tests give them in, and with none of what only a
# run needs, including a max_cost_usd that a run refuses.
SUSS_TOML = """\
max_cost_usd = 20

[arms.alpha]
baseline = "omega"

[arms.base]

[arms.beta]
baseline = "base"

[arms.gamma]
baseline = "beta"

[arms.omega]
"""

UNRECORDED = "unrecorded"


def write_arm(root: Path, arm: str, tasks: dict[str, list]) -> None:
    """
    The arm's results: each task's trials, the i-th in agent job `S-<i>` and
    graded in `S-<i>-regrade`.

    A trial is whether its regrade resolved it, or a (status, resolved) pair.
    The status UNRECORDED leaves the trial no suss.json.
    """
    for task, trials in tasks.items():
        for i, trial in enumerate(trials, start=1):
            status, resolved = trial if isinstance(trial, tuple) else ("ok", trial)
            job = root / "results" / LANE / arm / f"S-{i}"
            write_agent_trial(job, task, f"agent{i}", status=status, record=status != UNRECORDED)
            write_regrade_trial(
                job.with_name(f"{job.name}{compare.REGRADE}"),
                task,
                f"regrd{i}",
                RESOLVED if resolved else UNRESOLVED,
            )


def run_compare(root: Path, capsys, *argv: str) -> tuple[int, str, str]:
    code = cli.main(["compare", *argv], directory=root)
    out, err = capsys.readouterr()
    return code, out, err


@pytest.fixture
def results(tmp_path) -> Path:
    """
    Four arms over tasks t1 to t4, with 2, 4, 2 and 4 trials, and two tasks
    that not every arm has.

    beta resolves half of each task's trials more than base does, so every
    resample of tasks gives beta - base exactly +50%. gamma is base again. alpha
    has t5, which no other arm has, and lacks t6, which the others resolve.
    """
    (tmp_path / "suss.toml").write_text(SUSS_TOML)
    F, T = False, True
    base = {
        "t1": [F, F],
        "t2": [T, F, F, F],
        "t3": [F, F],
        "t4": [F, F, F, F],
        "t6": [T],
    }
    write_arm(tmp_path, "base", base)
    write_arm(tmp_path, "gamma", base)
    write_arm(
        tmp_path,
        "beta",
        {
            "t1": [T, F],
            "t2": [T, T, T, F],
            "t3": [T, F],
            "t4": [T, T, F, F],
            "t6": [T],
        },
    )
    write_arm(
        tmp_path,
        "alpha",
        {
            "t1": [(UNRECORDED, F), ("timed-out", T)],
            "t2": [F, T, ("timed-out", T), F],
            "t3": [F, ("harness-failed", T)],
            "t4": [F, F, F, F],
            "t5": [("timed-out", T)],
        },
    )
    return tmp_path


def test_compare_prints_both_tables_over_the_tasks_every_arm_has(results, capsys):
    code, out, err = run_compare(results, capsys, LANE, "base", "gamma", "beta", "alpha")

    # alpha's own t5 and the others' t6 count nowhere. Shares are over trials:
    # base resolves 1 of 12 trials, where a share over tasks would be 1 of 4.
    # alpha's baseline, omega, is not given, so alpha has no row.
    assert (code, err) == (0, "")
    assert out == (
        "arm    trials  resolved  failed  timed out\n"
        "base       12        8%      0%         0%\n"
        "gamma      12        8%      0%         0%\n"
        "beta       12       58%      0%         0%\n"
        "alpha      12       33%     17%        17%\n"
        "\n"
        "vs. baseline   resolved\n"
        "gamma - beta   -50% [-50%, -50%]\n"
        "beta - base    +50% [+50%, +50%]\n"
    )


def test_shares_and_differences_print_as_whole_percents_and_every_difference_is_signed():
    shares = {"a": compare.Shares(trials=8, resolved=0.125, failed=0.0049, timed_out=0.375)}
    differences = [
        ("b", "a", Difference(0.03, -0.02, 0.08)),
        # Rounds to zero from below, and prints as +0%.
        ("c", "a", Difference(-0.004, -0.0251, 0.11)),
    ]

    assert compare.render(shares, differences) == (
        "arm  trials  resolved  failed  timed out\n"
        "a         8       13%      0%        38%\n"
        "\n"
        "vs. baseline   resolved\n"
        "b - a          +3% [-2%, +8%]\n"
        "c - a          +0% [-3%, +11%]\n"
    )


def test_only_the_first_table_prints_when_no_arms_baseline_is_given(results, capsys):
    code, out, err = run_compare(results, capsys, LANE, "alpha", "base")

    assert (code, err) == (0, "")
    assert out == (
        "arm    trials  resolved  failed  timed out\n"
        "alpha      12       33%     17%        17%\n"
        "base       12        8%      0%         0%\n"
    )


def leave_no_trials(root: Path, how: str) -> None:
    arm = root / "results" / LANE / "omega"
    checks = root / "checks" / LANE / "omega"
    if how == "empty":
        arm.mkdir(parents=True)
    elif how == "regrade-only":
        write_regrade_trial(arm / f"S-1{compare.REGRADE}", "t1", "regrd1")
    elif how == "only-under-checks":
        write_agent_trial(checks / "S-1", "t1", "agent1")
        write_regrade_trial(checks / f"S-1{compare.REGRADE}", "t1", "regrd1")


@pytest.mark.parametrize("how", ["missing", "empty", "regrade-only", "only-under-checks"])
def test_an_arm_with_no_trials_is_an_error_and_prints_nothing_else(results, capsys, how):
    leave_no_trials(results, how)

    code, out, err = run_compare(results, capsys, LANE, "base", "omega")

    assert code != 0
    assert (out, err) == ("", f"error: no results for arm omega under results/{LANE}/\n")


def test_an_arm_not_in_suss_toml_is_an_error_naming_it_and_the_file(results, capsys):
    code, out, err = run_compare(results, capsys, LANE, "base", "zeta")

    assert code != 0
    assert (out, err) == ("", f"error: arm zeta is not defined in {results / 'suss.toml'}\n")


def test_no_suss_toml_is_an_error(results, capsys):
    (results / "suss.toml").unlink()

    code, out, err = run_compare(results, capsys, LANE, "base", "beta")

    assert code != 0
    assert out == ""
    assert err.startswith("error: ") and "suss.toml: no such file" in err


def test_arms_with_no_task_in_common_are_an_error(results, capsys):
    write_arm(results, "omega", {"t7": [True]})

    code, out, err = run_compare(results, capsys, LANE, "base", "omega")

    assert code != 0
    assert (out, err) == (
        "",
        f"error: arms base and omega have no task in common under results/{LANE}/\n",
    )


def test_an_arm_given_twice_is_an_error(results, capsys):
    code, out, err = run_compare(results, capsys, LANE, "base", "beta", "base")

    assert code != 0
    assert (out, err) == ("", "error: base is named twice: each arm is compared once\n")


def test_compare_takes_at_least_two_arms(results, capsys):
    code, out, err = run_compare(results, capsys, LANE, "base")

    assert code != 0
    assert (out, err) == ("", "error: suss compare takes a lane and at least two arms\n")


def test_arguments_after_the_separator_are_refused(results, capsys):
    code, out, err = run_compare(results, capsys, LANE, "base", "beta", "--", "-k", "2")

    assert code != 0
    assert (out, err) == ("", "error: suss compare takes nothing after --\n")


def regrade_applies_nothing(root: Path, arm: str, task: str, i: int) -> Path:
    """
    Make write_arm's i-th regrade of the task one that found no patch, as
    PatchReplayAgent leaves it: replay.json naming none, and reward 0.
    """
    job = root / "results" / LANE / arm / f"S-{i}{compare.REGRADE}"
    regrade = trial_directory(job, task, f"regrd{i}")
    result = json.loads((regrade / "result.json").read_text())
    write_json(regrade / "result.json", dict(result, verifier_result=UNRESOLVED))
    write_json(regrade / "agent" / "replay.json", {"task": task, "patch": None})
    return regrade / "agent" / "replay.json"


@pytest.mark.parametrize("task", ["t2", "t5"], ids=["shared", "alphas-own"])
def test_a_regrade_that_applied_none_of_a_trials_patch_is_an_error_naming_both(
    results, capsys, task
):
    # Every trial read counts, including one of a task the other arms lack.
    regrade_applies_nothing(results, "alpha", task, 1)

    code, out, err = run_compare(results, capsys, LANE, "base", "alpha")

    assert code != 0
    assert (out, err) == (
        "",
        f"error: the trial of {task} in results/{LANE}/alpha/S-1 made a 4182-byte patch, "
        "but its regrade in S-1-regrade applied none\n",
    )


def test_a_trial_with_no_record_whose_regrade_applied_nothing_is_compared(results, capsys):
    # alpha's first trial of t1 has no suss.json, so no patch_bytes.
    regrade_applies_nothing(results, "alpha", "t1", 1)

    code, out, err = run_compare(results, capsys, LANE, "alpha", "base")

    assert (code, err) == (0, "")
    assert "alpha      12       33%     17%        17%\n" in out


def test_an_empty_patch_whose_regrade_applied_nothing_counts_as_unresolved(results, capsys):
    trial = trial_directory(results / "results" / LANE / "beta" / "S-1", "t1", "agent1")
    write_json(trial / "agent" / "suss.json", {"grader": GRADER, "status": "ok", "patch_bytes": 0})
    regrade_applies_nothing(results, "beta", "t1", 1)

    code, out, err = run_compare(results, capsys, LANE, "beta", "base")

    # beta's resolved trial of t1 is now unresolved: 7 of 13 resolve, not 8.
    assert (code, err) == (0, "")
    assert "beta      13       54%      0%         0%\n" in out


@pytest.mark.parametrize("how", ["no-replay-json", "no-patch-in-it", "no-regrade-trial"])
def test_a_patch_whose_regrade_says_nothing_of_it_counts_as_unresolved(results, capsys, how):
    replay = regrade_applies_nothing(results, "beta", "t1", 1)
    if how == "no-replay-json":
        replay.unlink()
    elif how == "no-patch-in-it":
        write_json(replay, {"task": "t1"})
    else:
        shutil.rmtree(replay.parent.parent)

    code, out, err = run_compare(results, capsys, LANE, "beta", "base")

    assert (code, err) == (0, "")
    assert "beta      13       54%      0%         0%\n" in out
