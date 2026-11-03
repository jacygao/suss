import json
from pathlib import Path

import pytest

from suss.compare import Trial, read_trials

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
    assert trials["S-1", FLIPT].regrade_applied is False


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
