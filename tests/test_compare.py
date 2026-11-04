import asyncio
import json
import random
import shutil
import subprocess
import time
from pathlib import Path

import pytest
from harbor.models.agent.context import AgentContext
from harbor.models.trial.result import ExceptionInfo
from harbor.trial.errors import VerifierTimeoutError
from harbor.verifier.verifier import RewardFileEmptyError

from suss import cli, compare, replay
from suss.compare import Difference, Trial, read_trials, resolved_difference

LANE = "pro-go"
ARM = "claude-code"
DIGEST = "9c2f04b1" + "0" * 56
GRADER = {"dataset": "swe-rebench-07-2026", "digest": DIGEST}
FLIPT = "instance_flipt-io__flipt-02e21636c58e86c51119b63e0fb5ca7b813b07b1"
# Harbor truncates a trial directory's name, so this task's trials begin as
# FLIPT's do.
FLIPT_TOO = "instance_flipt-io__flipt-02e21639a0b1c2d3e4f5a6b7c8d9e0f1a2b3c4d5"
NAVIDROME = "instance_navidrome__navidrome-0130c6dc13438b48cf0fdfab08a89e357b5517c9"
# Harbor's own reward when a regrade resolves its task.
RESOLVED = {"rewards": {"reward": 1.0}}
UNRESOLVED = {"rewards": {"reward": 0.0}}


def verifier_report(passed: int, failed: int, fail_to_pass: str = "PASSED") -> dict:
    """The verifier/report.json a rebench verifier writes, here over pytest."""
    return {
        "log_parser": "parse_log_pytest",
        "parsed_counts": {"PASSED": passed, "FAILED": failed, "SKIPPED": 0, "ERROR": 0},
        "fail_to_pass": {"tests/test_threads.py::test_print_is_builtin": fail_to_pass},
        "pass_to_pass": {"tests/test_threads.py::test_thread_print": "PASSED"},
        "fail_to_pass_ok": fail_to_pass == "PASSED",
        "pass_to_pass_ok": True,
        "resolved": fail_to_pass == "PASSED" and failed == 0,
    }


TESTED = verifier_report(passed=144, failed=4)
# What a verifier writes when grading broke before any test ran, as when a
# package it installs has no wheel for its Python.
NO_TEST_RESULT = verifier_report(passed=0, failed=0, fail_to_pass="NOT_FOUND")


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
    job: Path,
    task: str,
    suffix: str,
    verifier_result=RESOLVED,
    *,
    applied: bool = True,
    report=TESTED,
) -> Path:
    """
    The regrade's trial of the task, with the replay.json ReplayAgent writes
    when it applied the trial's patch, or when it found none, and the
    verifier's report. A report of None leaves the verifier no report.json.
    """
    trial = trial_directory(write_job(job), task, suffix)
    write_json(trial / "config.json", {"task": {"path": f"lanes/{LANE}/{task}"}})
    write_json(
        trial / "result.json",
        {
            "task_name": f"swebench-pro/{task}",
            "trial_name": trial.name,
            "agent_info": {"name": "suss-replay"},
            "verifier_result": verifier_result,
        },
    )
    replay = {"task": task, "patch": None}
    if applied:
        source = job.parent / job.name.removesuffix(compare.REGRADE) / task / "agent"
        replay = {
            "task": task,
            "patch": str(source / "model.patch"),
            "apply_rc": 0,
            "apply_out": "$ git apply --verbose\nApplied patch cleanly.\n",
        }
    write_json(trial / "agent" / "replay.json", replay)
    if report is not None:
        write_json(trial / "verifier" / "report.json", report)
    return trial


def record_exception(trial: Path, error: Exception) -> None:
    """
    Give the regrade's result.json the exception, as Harbor's Trial.run records
    one: `exception_info` set and no reward.
    """
    try:
        raise error
    except Exception as raised:
        info = ExceptionInfo.from_exception(raised).model_dump(mode="json")
    result = json.loads((trial / "result.json").read_text())
    write_json(trial / "result.json", dict(result, verifier_result=None, exception_info=info))


def regrade_raised(trial: Path) -> None:
    """
    Make the regrade's trial one whose ReplayAgent raised before writing
    replay.json, as a real `harbor run` left it: no `agent/` at all, and the
    exception in result.json, and no verifier run.
    """
    shutil.rmtree(trial / "agent")
    shutil.rmtree(trial / "verifier", ignore_errors=True)
    record_exception(trial, replay.ReplayError("source_job must hold one trial of the task"))


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


@pytest.mark.parametrize("patch", [True, False], ids=["found", "missing"])
def test_reads_whether_the_regrade_applied_the_patch_from_what_replay_agent_wrote(
    tmp_path, arm, local_environment, monkeypatch, patch
):
    trial = write_agent_trial(arm / "S-1", FLIPT, "kY8wt23")
    if not patch:
        (trial / "agent" / "model.patch").unlink()
    regrade = write_regrade_trial(arm / "S-1-regrade", FLIPT, "NfDX2zK", UNRESOLVED)
    (regrade / "agent" / "replay.json").unlink()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    # A repository, as every workspace is, since the regrade asks git what the patch changed.
    subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)

    async def find_workspace(environment):
        return str(workspace)

    monkeypatch.setattr(replay, "find_workspace", find_workspace)
    agent = replay.ReplayAgent(
        source_job=str(arm / "S-1"),
        patch_target=str(tmp_path / "replay.patch"),
        logs_dir=regrade / "agent",
    )
    asyncio.run(agent.run("an instruction", local_environment, AgentContext()))

    [read] = read_trials(tmp_path, LANE, ARM)

    assert read.regrade_applied is patch


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
    write_regrade_trial(arm / "S-1-regrade", FLIPT, "NfDX2zK", UNRESOLVED, applied=False)

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
    write_regrade_trial(arm / "S-1-regrade", FLIPT, "NfDX2zK", UNRESOLVED, applied=False)
    write_regrade_trial(arm / "S-1-regrade", NAVIDROME, "Lm8vB2c")

    trials = by_job(read_trials(tmp_path, LANE, ARM))

    assert trials["S-1", FLIPT].regrade_applied is False
    assert trials["S-1", FLIPT].patch_bytes == 6490
    assert trials["S-1", NAVIDROME].regrade_applied is True


def test_a_regrade_that_raised_before_writing_replay_json_applied_no_patch(tmp_path, arm):
    write_agent_trial(arm / "S-1", FLIPT, "kY8wt23")
    regrade_raised(write_regrade_trial(arm / "S-1-regrade", FLIPT, "NfDX2zK"))

    [trial] = read_trials(tmp_path, LANE, ARM)

    assert (trial.resolved, trial.regrade_applied) == (False, False)


def test_a_regrade_whose_verifier_timed_out_after_replay_json_applied_its_patch(tmp_path, arm):
    write_agent_trial(arm / "S-1", FLIPT, "kY8wt23")
    regrade = write_regrade_trial(arm / "S-1-regrade", FLIPT, "NfDX2zK")
    record_exception(regrade, VerifierTimeoutError("Verifier execution timed out after 3000.0 seconds"))

    [trial] = read_trials(tmp_path, LANE, ARM)

    assert (trial.resolved, trial.regrade_applied) == (False, True)


def regrade_wrote_an_empty_reward(trial: Path) -> None:
    """
    Make the regrade's trial one whose verifier judged its trial invalid, as a
    Senior SWE-Bench verifier leaves it: an empty reward.txt, no report.json,
    and Harbor's RewardFileEmptyError in result.json.
    """
    (trial / "verifier").mkdir(exist_ok=True)
    (trial / "verifier" / "reward.txt").write_text("")
    (trial / "verifier" / "report.json").unlink(missing_ok=True)
    record_exception(trial, RewardFileEmptyError(f"Reward file is empty at {trial}/verifier/reward.txt"))


@pytest.mark.parametrize(
    "error, invalid",
    [
        (None, False),
        (RewardFileEmptyError("Reward file is empty at verifier/reward.txt"), True),
        (VerifierTimeoutError("Verifier execution timed out after 3000.0 seconds"), False),
        (RuntimeError("Docker container exited with code 137"), False),
    ],
    ids=["graded", "empty-reward", "verifier-timed-out", "verifier-crashed"],
)
def test_only_a_regrade_that_wrote_an_empty_reward_makes_its_trial_invalid(
    tmp_path, arm, error, invalid
):
    write_agent_trial(arm / "S-1", FLIPT, "kY8wt23")
    regrade = write_regrade_trial(arm / "S-1-regrade", FLIPT, "NfDX2zK", UNRESOLVED)
    if error is not None:
        record_exception(regrade, error)

    [trial] = read_trials(tmp_path, LANE, ARM)

    # Each leaves verifier_result null, so only the exception's type tells them apart.
    assert (trial.invalid, trial.resolved) == (invalid, False)


# What applying a patch printed in a real regrade, whose patch skipped a hunk
# that git apply had refused, and exited 1.
SKIPPED_A_HUNK = {
    "apply_rc": 1,
    "apply_out": (
        "$ git apply --verbose\n"
        "Checking patch package.json...\n"
        "error: while searching for:\n"
        "  },\n"
        '  "engines": {\n'
        '    "node": ">=18.0.0"\n'
        "  }\n"
        "}\n"
        "\n"
        "error: patch failed: package.json:136\n"
        "error: package.json: patch does not apply\n"
        "Checking patch src/__tests__/useForm/valuesWithShouldUnregister.test.tsx...\n"
        "Checking patch src/logic/createFormControl.ts...\n"
        "$ git apply --3way\n"
        "error: package.json: does not match index\n"
        "Falling back to direct application...\n"
        "error: src/logic/createFormControl.ts: does not match index\n"
        "$ patch --fuzz=3 -p1 -i\n"
        "patching file package.json\n"
        "Reversed (or previously applied) patch detected!  Assume -R? [n] \n"
        "Apply anyway? [n] \n"
        "Skipping patch.\n"
        "1 out of 1 hunk ignored -- saving rejects to file package.json.rej\n"
        "patching file src/__tests__/useForm/valuesWithShouldUnregister.test.tsx\n"
        "patching file src/logic/createFormControl.ts\n"
    ),
}
def applied_part(trial: Path, applying: dict) -> None:
    """Make the regrade trial's replay.json record its patch applied as `applying` says."""
    replay = trial / "agent" / "replay.json"
    task = json.loads(replay.read_text())["task"]
    write_json(replay, {"task": task, "patch": "model.patch", **applying})


@pytest.mark.parametrize(
    "applying",
    [SKIPPED_A_HUNK, {}, {"apply_rc": None, "apply_out": ""}],
    ids=["skipped-a-hunk", "no-exit-code", "null-exit-code"],
)
def test_a_regrade_that_does_not_show_it_applied_all_of_its_patch_applied_part(
    tmp_path, arm, applying
):
    write_agent_trial(arm / "S-1", FLIPT, "kY8wt23")
    write_agent_trial(arm / "S-1", NAVIDROME, "Qp3rT9a")
    write_agent_trial(arm / "S-1", FLIPT_TOO, "Zz9yX8w")
    applied_part(write_regrade_trial(arm / "S-1-regrade", FLIPT, "NfDX2zK"), applying)
    write_regrade_trial(arm / "S-1-regrade", NAVIDROME, "Lm8vB2c")
    write_regrade_trial(arm / "S-1-regrade", FLIPT_TOO, "Hh7gG6f", UNRESOLVED, applied=False)

    trials = by_job(read_trials(tmp_path, LANE, ARM))

    assert trials["S-1", FLIPT].regrade_refused is True
    # A clean apply, and a regrade that applied no patch, applied no part.
    assert trials["S-1", NAVIDROME].regrade_refused is False
    assert trials["S-1", FLIPT_TOO].regrade_refused is False


def counts(**given) -> dict:
    """NO_TEST_RESULT, with the given counts in its parsed_counts."""
    return dict(NO_TEST_RESULT, parsed_counts=dict(NO_TEST_RESULT["parsed_counts"], **given))


@pytest.mark.parametrize(
    "report, tested",
    [
        (TESTED, True),
        (counts(PASSED=1), True),
        (counts(FAILED=1), True),
        (counts(SKIPPED=1), True),
        (counts(ERROR=1), True),
        (NO_TEST_RESULT, False),
        (
            dict(NO_TEST_RESULT, fail_to_pass={"tests/test_threads.py::test_print_is_builtin": "PASSED"}),
            False,
        ),
        (None, False),
        ({key: value for key, value in TESTED.items() if key != "parsed_counts"}, False),
        (dict(TESTED, parsed_counts=None), False),
        (dict(TESTED, parsed_counts=[144, 4, 0, 0]), False),
        (counts(PASSED="144"), False),
        (counts(PASSED=True), False),
        (counts(PASSED=-1), False),
        (counts(OTHER=3), False),
        ([TESTED], False),
    ],
    ids=[
        "tests-ran",
        "one-passed",
        "one-failed",
        "one-skipped",
        "one-errored",
        "all-zero",
        "all-zero-but-fail-to-pass-passed",
        "no-report",
        "no-parsed-counts",
        "null-parsed-counts",
        "parsed-counts-a-list",
        "a-count-a-string",
        "a-count-a-bool",
        "a-count-below-zero",
        "only-another-key",
        "report-not-an-object",
    ],
)
def test_a_regrade_records_a_test_result_only_when_parsed_counts_counts_a_test(
    tmp_path, arm, report, tested
):
    write_agent_trial(arm / "S-1", FLIPT, "kY8wt23")
    write_regrade_trial(arm / "S-1-regrade", FLIPT, "NfDX2zK", UNRESOLVED, report=report)

    [trial] = read_trials(tmp_path, LANE, ARM)

    assert trial.regrade_tested is tested


def test_a_trial_with_no_regrade_trial_records_no_test_result(tmp_path, arm):
    write_agent_trial(arm / "S-1", FLIPT, "kY8wt23")

    [trial] = read_trials(tmp_path, LANE, ARM)

    assert trial.regrade_tested is False


def test_a_report_that_is_not_json_records_no_test_result(tmp_path, arm):
    write_agent_trial(arm / "S-1", FLIPT, "kY8wt23")
    regrade = write_regrade_trial(arm / "S-1-regrade", FLIPT, "NfDX2zK", UNRESOLVED)
    (regrade / "verifier" / "report.json").write_text('{"parsed_counts": {"PASSED": 14')

    [trial] = read_trials(tmp_path, LANE, ARM)

    assert trial.regrade_tested is False


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
    ReplayAgent leaves it: replay.json naming none, and reward 0.
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


def regrade_of(root: Path, arm: str, task: str, i: int) -> Path:
    """write_arm's i-th regrade trial of the task."""
    return trial_directory(root / "results" / LANE / arm / f"S-{i}{compare.REGRADE}", task, f"regrd{i}")


@pytest.mark.parametrize("task", ["t2", "t5"], ids=["shared", "alphas-own"])
def test_a_regrade_that_raised_before_applying_a_trials_patch_is_an_error_naming_both(
    results, capsys, task
):
    regrade_raised(regrade_of(results, "alpha", task, 1))

    code, out, err = run_compare(results, capsys, LANE, "base", "alpha")

    assert code != 0
    assert (out, err) == (
        "",
        f"error: the trial of {task} in results/{LANE}/alpha/S-1 made a 4182-byte patch, "
        "but its regrade in S-1-regrade raised ReplayError and applied none\n",
    )


@pytest.mark.parametrize(
    "arm, task",
    [("alpha", "t2"), ("alpha", "t5"), ("alpha", "t1"), ("base", "t3")],
    ids=["shared", "alphas-own", "no-record", "baseline"],
)
def test_a_regrade_that_applied_part_of_a_trials_patch_is_an_error_naming_both(
    results, capsys, arm, task
):
    # alpha's first trial of t1 has no suss.json, and its patch still counts.
    applied_part(regrade_of(results, arm, task, 1), SKIPPED_A_HUNK)

    code, out, err = run_compare(results, capsys, LANE, "base", "alpha")

    assert code != 0
    assert (out, err) == (
        "",
        f"error: the trial of {task} in results/{LANE}/{arm}/S-1 made a patch, "
        "but its regrade in S-1-regrade could not apply it\n",
    )


def test_the_first_regrade_that_applied_part_of_a_patch_is_the_one_named(results, capsys):
    applied_part(regrade_of(results, "alpha", "t3", 2), SKIPPED_A_HUNK)
    applied_part(regrade_of(results, "alpha", "t4", 1), SKIPPED_A_HUNK)
    applied_part(regrade_of(results, "base", "t4", 3), SKIPPED_A_HUNK)

    code, out, err = run_compare(results, capsys, LANE, "alpha", "base")

    # By arm in the order given, then by job and task.
    assert code != 0
    assert (out, err) == (
        "",
        f"error: the trial of t4 in results/{LANE}/alpha/S-1 made a patch, "
        "but its regrade in S-1-regrade could not apply it\n",
    )


def test_a_regrade_whose_verifier_timed_out_counts_as_unresolved(results, capsys):
    regrade = regrade_of(results, "beta", "t1", 1)
    record_exception(regrade, VerifierTimeoutError("Verifier execution timed out after 3000.0 seconds"))

    code, out, err = run_compare(results, capsys, LANE, "beta", "base")

    assert (code, err) == (0, "")
    assert "beta      13       54%      0%         0%\n" in out


def test_an_empty_patch_whose_regrade_raised_counts_as_unresolved(results, capsys):
    trial = trial_directory(results / "results" / LANE / "beta" / "S-1", "t1", "agent1")
    write_json(trial / "agent" / "suss.json", {"grader": GRADER, "status": "ok", "patch_bytes": 0})
    regrade_raised(regrade_of(results, "beta", "t1", 1))

    code, out, err = run_compare(results, capsys, LANE, "beta", "base")

    assert (code, err) == (0, "")
    assert "beta      13       54%      0%         0%\n" in out


def test_a_trial_with_no_record_whose_regrade_raised_is_compared(results, capsys):
    # alpha's first trial of t1 has no suss.json, so no patch_bytes.
    regrade_raised(regrade_of(results, "alpha", "t1", 1))

    code, out, err = run_compare(results, capsys, LANE, "alpha", "base")

    assert (code, err) == (0, "")
    assert "alpha      12       33%     17%        17%\n" in out


def add_trial(root: Path, arm: str, job: str, task: str, grader) -> None:
    """One more trial of the arm, in an agent job of its own, resolved by its regrade."""
    jobs = root / "results" / LANE / arm
    write_agent_trial(jobs / job, task, "agentX", grader=grader)
    write_regrade_trial(jobs / f"{job}{compare.REGRADE}", task, "regrdX")


# GRADER, as the error names it.
SHORT = "swe-rebench-07-2026 (9c2f04b1)"


def graders_differ(one: str, one_in: str, other: str, other_in: str) -> str:
    """The error for two graders, each with the arm and job of the trial carrying it."""
    return (
        f"error: trials carry different graders: {one} in results/{LANE}/{one_in}, "
        f"{other} in results/{LANE}/{other_in}\n"
    )


@pytest.mark.parametrize(
    "other, named",
    [
        ({**GRADER, "dataset": "swe-rebench-08-2026"}, "swe-rebench-08-2026 (9c2f04b1)"),
        ({**GRADER, "digest": "4e7a1c90" + "f" * 56}, "swe-rebench-07-2026 (4e7a1c90)"),
    ],
    ids=["dataset", "digest"],
)
def test_arms_whose_graders_differ_are_an_error_naming_both(results, capsys, other, named):
    add_trial(results, "beta", "S-9", "t1", other)

    code, out, err = run_compare(results, capsys, LANE, "base", "beta")

    assert code != 0
    assert (out, err) == (
        "",
        graders_differ(SHORT, "base/S-1", named, "beta/S-9"),
    )


def test_graders_that_differ_within_one_arm_are_an_error(results, capsys):
    add_trial(results, "base", "S-9", "t1", {**GRADER, "digest": "4e7a1c90" + "f" * 56})

    code, out, err = run_compare(results, capsys, LANE, "base", "beta")

    assert code != 0
    assert (out, err) == (
        "",
        graders_differ(SHORT, "base/S-1", "swe-rebench-07-2026 (4e7a1c90)", "base/S-9"),
    )


def test_a_grader_that_differs_on_a_task_not_every_arm_has_is_still_an_error(results, capsys):
    # t5 is alpha's alone, so it counts in neither table, and is still read.
    add_trial(results, "alpha", "S-9", "t5", {**GRADER, "dataset": "swe-rebench-08-2026"})

    code, out, err = run_compare(results, capsys, LANE, "base", "alpha")

    assert code != 0
    assert (out, err) == (
        "",
        graders_differ(SHORT, "base/S-1", "swe-rebench-08-2026 (9c2f04b1)", "alpha/S-9"),
    )


def test_graders_whose_digests_share_a_short_form_print_in_full(results, capsys):
    other = "9c2f04b1" + "f" * 56
    add_trial(results, "beta", "S-9", "t1", {**GRADER, "digest": other})

    code, out, err = run_compare(results, capsys, LANE, "base", "beta")

    assert code != 0
    assert (out, err) == (
        "",
        graders_differ(
            f'{{"dataset": "swe-rebench-07-2026", "digest": "{DIGEST}"}}',
            "base/S-1",
            f'{{"dataset": "swe-rebench-07-2026", "digest": "{other}"}}',
            "beta/S-9",
        ),
    )


def test_a_grader_of_another_shape_prints_in_full(results, capsys):
    old = {"dataset": "swe-bench-pro-v2", "commit": "66f92766"}
    add_trial(results, "beta", "S-9", "t1", old)

    code, out, err = run_compare(results, capsys, LANE, "base", "beta")

    assert code != 0
    assert (out, err) == (
        "",
        graders_differ(
            f'{{"dataset": "swe-rebench-07-2026", "digest": "{DIGEST}"}}',
            "base/S-1",
            '{"commit": "66f92766", "dataset": "swe-bench-pro-v2"}',
            "beta/S-9",
        ),
    )


def test_a_trial_with_no_suss_json_takes_no_part_in_the_grader_check(tmp_path, capsys):
    (tmp_path / "suss.toml").write_text(SUSS_TOML)
    write_arm(tmp_path, "base", {"t1": [True, (UNRECORDED, False)]})
    write_arm(tmp_path, "beta", {"t1": [(UNRECORDED, False), True]})

    code, out, err = run_compare(tmp_path, capsys, LANE, "base", "beta")

    assert (code, err) == (0, "")
    assert out.splitlines()[:3] == [
        "arm   trials  resolved  failed  timed out",
        "base       2       50%     50%         0%",
        "beta       2       50%     50%         0%",
    ]


def test_graders_whose_keys_come_in_another_order_are_the_same_grader(results, capsys):
    add_trial(results, "beta", "S-9", "t1", {"digest": GRADER["digest"], "dataset": GRADER["dataset"]})

    code, out, err = run_compare(results, capsys, LANE, "base", "beta")

    assert (code, err) == (0, "")
    assert out.startswith("arm ")


NAMED = "These trials' regrades recorded no test result, so their grading may have broken:\n"


def report_records_no_test_result(root: Path, arm: str, task: str, i: int) -> None:
    """Give write_arm's i-th regrade of the task the report of a verifier whose grading broke."""
    write_json(regrade_of(root, arm, task, i) / "verifier" / "report.json", NO_TEST_RESULT)


def test_compare_names_each_trial_whose_regrade_recorded_no_test_result(results, capsys):
    _, before, _ = run_compare(results, capsys, LANE, "base", "gamma", "beta", "alpha")
    # beta's trial resolved and base's did not; base's regrade has no report at all.
    report_records_no_test_result(results, "beta", "t2", 3)
    (regrade_of(results, "base", "t4", 2) / "verifier" / "report.json").unlink()
    report_records_no_test_result(results, "alpha", "t1", 2)

    code, out, err = run_compare(results, capsys, LANE, "base", "gamma", "beta", "alpha")

    assert (code, err) == (0, "")
    assert out == before + "\n" + NAMED + (
        "  base   S-2  t4\n"
        "  beta   S-3  t2\n"
        "  alpha  S-2  t1\n"
    ), "a trial with no test result must count in both tables as its regrade graded it"


def test_a_trial_with_no_regrade_trial_is_named(results, capsys):
    shutil.rmtree(regrade_of(results, "beta", "t3", 2))

    code, out, err = run_compare(results, capsys, LANE, "beta", "base")

    assert (code, err) == (0, "")
    assert out.endswith("\n" + NAMED + "  beta  S-2  t3\n")


def test_a_trial_on_a_task_not_every_arm_has_is_not_named(results, capsys):
    # t5 is alpha's alone, so it counts in neither table.
    report_records_no_test_result(results, "alpha", "t5", 1)

    code, out, err = run_compare(results, capsys, LANE, "alpha", "base")

    assert (code, err) == (0, "")
    assert NAMED not in out


INVALID = "These trials' regrades judged them invalid, so neither table counts them:\n"


def without(root: Path, elsewhere: Path, trials: list[tuple[str, str, int]]) -> Path:
    """
    A copy of the results in `elsewhere`, with write_arm's i-th trial of each
    (arm, task) and its regrade gone.
    """
    copy = elsewhere / "copy"
    shutil.copytree(root, copy)
    for arm, task, i in trials:
        shutil.rmtree(trial_directory(copy / "results" / LANE / arm / f"S-{i}", task, f"agent{i}"))
        shutil.rmtree(regrade_of(copy, arm, task, i))
    return copy


def test_an_invalid_trial_counts_in_neither_table_and_is_named(results, tmp_path_factory, capsys):
    report_records_no_test_result(results, "base", "t4", 2)
    # beta's first trial of t2 resolved, so leaving it out moves every number of beta's.
    gone = without(results, tmp_path_factory.mktemp("gone"), [("beta", "t2", 1)])
    _, expected, _ = run_compare(gone, capsys, LANE, "base", "beta")
    regrade_wrote_an_empty_reward(regrade_of(results, "beta", "t2", 1))

    code, out, err = run_compare(results, capsys, LANE, "base", "beta")

    assert (code, err) == (0, "")
    assert "beta      12       58%" in expected
    assert out == expected + "\n" + INVALID + "  beta  S-1  t2\n", (
        "an invalid trial must leave every number in both tables as if it were not there, "
        "and be named once, as invalid and not as recording no test result"
    )


def test_a_task_whose_every_trial_in_one_arm_is_invalid_leaves_the_comparison(
    results, tmp_path_factory, capsys
):
    gone = without(
        results,
        tmp_path_factory.mktemp("gone"),
        [(arm, "t1", i) for arm in ("base", "beta") for i in (1, 2)],
    )
    _, expected, _ = run_compare(gone, capsys, LANE, "base", "beta")
    for i in (1, 2):
        regrade_wrote_an_empty_reward(regrade_of(results, "beta", "t1", i))

    code, out, err = run_compare(results, capsys, LANE, "base", "beta")

    assert (code, err) == (0, "")
    assert "base      11" in expected
    assert out == expected + "\n" + INVALID + "  beta  S-1  t1\n  beta  S-2  t1\n", (
        "base's valid trials of t1 must count nowhere, since beta has none to compare them with"
    )


def test_an_arm_whose_every_trial_is_invalid_is_an_error(results, capsys):
    for task, k in {"t1": 2, "t2": 4, "t3": 2, "t4": 4, "t6": 1}.items():
        for i in range(1, k + 1):
            regrade_wrote_an_empty_reward(regrade_of(results, "beta", task, i))

    code, out, err = run_compare(results, capsys, LANE, "base", "beta")

    assert code != 0
    assert (out, err) == (
        "",
        f"error: every trial of arm beta under results/{LANE}/ is invalid: "
        "its regrade wrote an empty reward\n",
    )


def test_an_invalid_trial_on_a_task_not_every_arm_has_is_not_named(results, capsys):
    # t5 is alpha's alone, so it counts in neither table.
    regrade_wrote_an_empty_reward(regrade_of(results, "alpha", "t5", 1))

    code, out, err = run_compare(results, capsys, LANE, "alpha", "base")

    assert (code, err) == (0, "")
    assert INVALID not in out


SENIOR = "senior"

# verifier_result.rewards of a senior regrade whose judges both ran, as a real
# regrade of paperless-ngx-perf-workflow-queries wrote it.
JUDGED = {
    "reward": 1.0,
    "correctness": 1.0,
    "verifier_score": 1.0,
    "rubric_score": 1.0,
    "rubric_f2p_score": 1.0,
    "rubric_p2p_score": 1.0,
    "taste_patch_bloat": 1.984,
    "taste_practice_alignment": 4.0,
    "taste_relative_taste": 3.0,
    "rubric_judge_ok": 1,
    "taste_judge_ok": 1,
}
# As a real regrade wrote it when both judges failed: the patch bloat is still
# there, measured without the classifier the taste judge needs.
JUDGES_FAILED = {
    "reward": 1.0,
    "correctness": 1.0,
    "verifier_score": 1.0,
    "taste_patch_bloat": 1.984,
    "rubric_judge_ok": 0,
    "taste_judge_ok": 0,
}
# A task with no rubric criteria: the rubric judge records skipped:no_criteria,
# so its flag is 0 and it has no score.
NO_CRITERIA = {key: value for key, value in JUDGED.items() if not key.startswith("rubric_")} | {
    "rubric_judge_ok": 0,
}
OK = {"rubric_status": "ok", "taste_status": "ok"}
STATUS_NO_CRITERIA = {"rubric_status": "skipped:no_criteria", "taste_status": "ok"}

SENIOR_TOML = """\
[arms.base]

[arms.beta]
baseline = "base"
"""


def judged(rubric=None, practice=None, relative=None, bloat=None, base=JUDGED) -> dict:
    """The rewards of a judged regrade, with the scores given in place of JUDGED's."""
    given = {
        "rubric_score": rubric,
        "taste_practice_alignment": practice,
        "taste_relative_taste": relative,
        "taste_patch_bloat": bloat,
    }
    return base | {key: value for key, value in given.items() if value is not None}


def write_senior_arm(root: Path, arm: str, tasks: dict[str, list[tuple[dict, dict | None]]]) -> None:
    """
    The arm's senior results: each task's trials, the i-th in agent job `S-<i>`.
    Each is its regrade's rewards and judge_output.json, where None writes no
    judge_output.json.
    """
    for task, trials in tasks.items():
        for i, (rewards, judge_output) in enumerate(trials, start=1):
            job = root / "results" / SENIOR / arm / f"S-{i}"
            write_agent_trial(job, task, f"agent{i}")
            # With a rebench report, so no trial here is named for recording no test result.
            regrade = write_regrade_trial(
                job.with_name(f"{job.name}{compare.REGRADE}"), task, f"regrd{i}", {"rewards": rewards}
            )
            if judge_output is not None:
                write_json(regrade / "verifier" / "judge_output.json", judge_output)


def regrade_of_senior(root: Path, arm: str, task: str, i: int) -> Path:
    """write_senior_arm's i-th regrade trial of the task."""
    return trial_directory(root / "results" / SENIOR / arm / f"S-{i}{compare.REGRADE}", task, f"regrd{i}")


@pytest.fixture
def senior(tmp_path) -> Path:
    """
    Two arms on senior over s1 to s3, where s3 has no rubric criteria, and an
    s9 only beta has, whose low scores would drag beta's means down.

    base's first trial of s2 is the failed regrade, with stale scores added
    under its 0 flags.
    """
    (tmp_path / "suss.toml").write_text(SENIOR_TOML)
    failed = {"rubric_status": "failed:api_error", "taste_status": "failed:api_error"}
    stale = JUDGES_FAILED | {"rubric_score": 1.0, "taste_practice_alignment": 5.0, "taste_relative_taste": 5.0}
    no_criteria = (judged(None, 5.0, 4.0, 2.0, base=NO_CRITERIA), STATUS_NO_CRITERIA)
    write_senior_arm(
        tmp_path,
        "base",
        {
            "s1": [(judged(1.0, 4.0, 3.0, 1.984), OK), (judged(0.5, 2.0, 3.0, 1.0), OK)],
            "s2": [(stale, failed), (judged(0.25, 3.0, 2.0, 0.5), OK)],
            "s3": [no_criteria, no_criteria],
        },
    )
    write_senior_arm(
        tmp_path,
        "beta",
        {
            "s1": [(judged(1.0, 4.0, 4.0, 1.0), OK)],
            "s2": [(judged(0.5, 2.0, 2.0, 3.0), OK)],
            "s3": [(judged(None, 3.0, 3.0, 1.0, base=NO_CRITERIA), STATUS_NO_CRITERIA)],
            "s9": [(judged(0.0, 1.0, 1.0, 9.0), OK)],
        },
    )
    return tmp_path


JUDGES_NAMED = "These trials' judges failed or were skipped, so the means leave out their scores:\n"


def test_compare_gives_each_arms_mean_judges_scores_on_senior(senior, capsys):
    code, out, err = run_compare(senior, capsys, SENIOR, "base", "beta")

    # base's rubric mean is over s1's two trials and s2's second: s2's first
    # trial's judges failed, and s3 has no criteria. Counting either as 0 gives
    # 0.29, and counting the stale scores gives 0.69, with a bloat of 1.58.
    # beta's s9 counts nowhere.
    assert (code, err) == (0, "")
    assert out == (
        "arm   trials  resolved  failed  timed out\n"
        "base       6      100%      0%         0%\n"
        "beta       3      100%      0%         0%\n"
        "\n"
        "vs. baseline   resolved\n"
        "beta - base    +0% [+0%, +0%]\n"
        "\n"
        "mean (trials)    rubric  practice alignment  relative taste  patch bloat\n"
        "base           0.58 (3)            3.80 (5)        3.20 (5)     1.50 (5)\n"
        "beta           0.75 (2)            3.00 (3)        3.00 (3)     1.67 (3)\n"
        "\n" + JUDGES_NAMED + "  base  S-1  s2  rubric, taste\n"
    )


def test_a_mean_with_no_trial_to_count_prints_a_dash():
    means = {
        "a": compare.Means(
            rubric=compare.Mean(None, 0),
            practice=compare.Mean(3.125, 8),
            relative=compare.Mean(2.0, 8),
            bloat=compare.Mean(1.0049, 7),
        )
    }

    assert compare.render_means(means) == [
        "mean (trials)  rubric  practice alignment  relative taste  patch bloat",
        "a               - (0)            3.13 (8)        2.00 (8)     1.00 (7)",
    ]


def set_rewards(regrade: Path, rewards: dict) -> None:
    result = json.loads((regrade / "result.json").read_text())
    write_json(regrade / "result.json", result | {"verifier_result": {"rewards": rewards}})


def test_a_mean_half_way_between_two_places_rounds_up_however_floats_add_it(senior, capsys):
    # 0.7 + 0.11 over 2 is 0.40499999999999997 in floats.
    set_rewards(regrade_of_senior(senior, "beta", "s1", 1), judged(bloat=0.7))
    set_rewards(regrade_of_senior(senior, "beta", "s2", 1), judged(bloat=0.11))
    set_rewards(regrade_of_senior(senior, "beta", "s3", 1), JUDGES_FAILED)

    code, out, err = run_compare(senior, capsys, SENIOR, "base", "beta")

    assert (code, err) == (0, "")
    assert "beta           1.00 (2)            4.00 (2)        3.00 (2)     0.41 (2)\n" in out


def test_a_judge_with_no_status_to_say_it_had_no_criteria_is_named(senior, capsys):
    # With no judge_output.json, a rubric flag of 0 cannot be told from a failure.
    (regrade_of_senior(senior, "beta", "s3", 1) / "verifier" / "judge_output.json").unlink()

    code, out, err = run_compare(senior, capsys, SENIOR, "base", "beta")

    assert (code, err) == (0, "")
    assert out.endswith(JUDGES_NAMED + "  base  S-1  s2  rubric, taste\n  beta  S-1  s3  rubric\n")


def test_a_regrade_with_a_reward_and_no_judges_flags_is_named(senior, capsys):
    # As when the verifier wrote reward.txt, and its aggregate step no reward.json.
    regrade = regrade_of_senior(senior, "beta", "s1", 1)
    result = json.loads((regrade / "result.json").read_text())
    write_json(regrade / "result.json", result | {"verifier_result": {"rewards": {"reward": 1.0}}})

    code, out, err = run_compare(senior, capsys, SENIOR, "base", "beta")

    assert (code, err) == (0, "")
    assert "beta           0.50 (1)            2.50 (2)        2.50 (2)     2.00 (2)\n" in out
    assert out.endswith("  base  S-1  s2  rubric, taste\n  beta  S-1  s1  rubric, taste\n")


def test_a_regrade_with_no_reward_has_no_scores_and_is_not_named_for_its_judges(senior, capsys):
    record_exception(regrade_of_senior(senior, "beta", "s1", 1), RuntimeError("Reward file is empty"))

    code, out, err = run_compare(senior, capsys, SENIOR, "base", "beta")

    assert (code, err) == (0, "")
    assert "beta           0.50 (1)            2.50 (2)        2.50 (2)     2.00 (2)\n" in out
    assert out.endswith(JUDGES_NAMED + "  base  S-1  s2  rubric, taste\n")


def test_another_lane_prints_no_means_whatever_its_rewards_hold(senior, capsys):
    (senior / "results" / SENIOR).rename(senior / "results" / "rebench")

    code, out, err = run_compare(senior, capsys, "rebench", "base", "beta")

    assert (code, err) == (0, "")
    assert out.endswith("beta - base    +0% [+0%, +0%]\n")


def test_a_task_whose_every_trial_in_one_arm_is_invalid_leaves_the_means(senior, capsys):
    regrade_wrote_an_empty_reward(regrade_of_senior(senior, "beta", "s1", 1))

    code, out, err = run_compare(senior, capsys, SENIOR, "base", "beta")

    # base's two judged trials of s1 count nowhere, since beta has none to compare them with.
    assert (code, err) == (0, "")
    assert out.endswith(
        "mean (trials)    rubric  practice alignment  relative taste  patch bloat\n"
        "base           0.25 (1)            4.33 (3)        3.33 (3)     1.50 (3)\n"
        "beta           0.50 (1)            2.50 (2)        2.50 (2)     2.00 (2)\n"
        "\n" + INVALID + "  beta  S-1  s1\n"
        "\n" + JUDGES_NAMED + "  base  S-1  s2  rubric, taste\n"
    )
