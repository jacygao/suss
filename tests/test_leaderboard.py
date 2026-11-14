import asyncio
import hashlib
import json
import random
import shutil
import subprocess
import time
from pathlib import Path

import pytest
from harbor.models.task.task import Task
from harbor.models.trial.config import VerifierConfig
from harbor.models.trial.paths import TrialPaths
from harbor.models.trial.result import ExceptionInfo
from harbor.trial.errors import VerifierTimeoutError
from harbor.verifier.factory import VerifierFactory
from harbor.verifier.verifier import RewardFileEmptyError

from swe_drill import cli, leaderboard, replay
from swe_drill.leaderboard import Trial, margin, read_trials

LANE = "rebench"
ARM = "claude-code"
GRADER = {"dataset": "swe-rebench-07-2026"}
FLIPT = "instance_flipt-io__flipt-02e21636c58e86c51119b63e0fb5ca7b813b07b1"
# Harbor truncates a trial directory's name, so this task's trials begin as
# FLIPT's do.
FLIPT_TOO = "instance_flipt-io__flipt-02e21639a0b1c2d3e4f5a6b7c8d9e0f1a2b3c4d5"
NAVIDROME = "instance_navidrome__navidrome-0130c6dc13438b48cf0fdfab08a89e357b5517c9"
# How these tests name the grading of agent job `S-1`: `S-1-regrade`, which
# grading_trial maps to the agent trial itself, where its verifier writes.
REGRADE = "-regrade"
# Harbor's own reward when a verifier resolves its task.
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
        {
            "task_name": f"swebench-pro/{task}",
            "trial_name": trial.name,
            "task_checksum": hashlib.sha256(task.encode()).hexdigest(),
            "verifier_result": None,
        },
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


def grading_trial(job: Path, task: str, suffix: str) -> Path:
    """
    The agent trial of `task` that `job`, an agent job's name with REGRADE
    after it, grades: the trial itself, since its verifier writes there. One
    with no such trial yet is made, named for `suffix`.
    """
    agent_job = job.with_name(job.name.removesuffix(REGRADE))
    for trial in sorted(agent_job.glob("*__*")) if agent_job.is_dir() else []:
        named = json.loads((trial / "result.json").read_text()).get("task_name", "")
        if named.rsplit("/", 1)[-1] == task:
            return trial
    trial = trial_directory(write_job(agent_job), task, suffix)
    write_json(trial / "config.json", {"task": {"path": f"lanes/{LANE}/{task}"}})
    write_json(trial / "result.json", {"task_name": f"swebench-pro/{task}", "trial_name": trial.name})
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
    What the verifier of the task's trial in the agent job `job` names wrote:
    its reward, the replay.json ReplayVerifier writes when it applied the
    trial's patch, or when it found none, and the verifier's report. A report
    of None leaves the verifier no report.json.
    """
    trial = grading_trial(job, task, suffix)
    result = json.loads((trial / "result.json").read_text())
    write_json(trial / "result.json", result | {"verifier_result": verifier_result})
    replay = {"patch": None}
    if applied:
        replay = {
            "patch": "/logs/artifacts/model.patch",
            "patch_bytes": 4182,
            "sha256": "0" * 64,
            "apply_rc": 0,
            "apply_out": "$ git apply --verbose\nApplied patch cleanly.\n",
        }
    write_json(trial / "verifier" / "replay.json", replay)
    if report is not None:
        write_json(trial / "verifier" / "report.json", report)
    return trial


def ungrade(trial: Path) -> None:
    """Make the trial one whose verifier never ran: no verifier/ and no reward."""
    shutil.rmtree(trial / "verifier", ignore_errors=True)
    result = json.loads((trial / "result.json").read_text())
    write_json(trial / "result.json", result | {"verifier_result": None})


def record_exception(trial: Path, error: Exception) -> None:
    """
    Give the trial's result.json the exception, as Harbor's Trial.run records
    one: `exception_info` set and no reward.
    """
    try:
        raise error
    except Exception as raised:
        info = ExceptionInfo.from_exception(raised).model_dump(mode="json")
    result = json.loads((trial / "result.json").read_text())
    write_json(trial / "result.json", dict(result, verifier_result=None, exception_info=info))


def verifier_raised(trial: Path) -> None:
    """
    Make the trial one whose verifier raised before writing replay.json: no
    verifier/ at all, and the exception in result.json.
    """
    shutil.rmtree(trial / "verifier", ignore_errors=True)
    record_exception(trial, replay.ReplayError("the trial left no patch at /logs/artifacts/model.patch"))


@pytest.fixture
def arm(tmp_path) -> Path:
    return tmp_path / "results" / LANE / ARM


def by_job(trials: list[Trial]) -> dict[tuple[str, str], Trial]:
    return {(trial.job, trial.task): trial for trial in trials}


def test_a_trial_carries_its_record_and_its_verifiers_result(tmp_path, arm):
    write_agent_trial(arm / "20261101T005320Z-1", FLIPT, "kY8wt23", patch_bytes=6490)
    write_regrade_trial(arm / "20261101T005320Z-1-regrade", FLIPT, "NfDX2zK")

    [trial] = read_trials(tmp_path, ARM)

    assert trial.job == "20261101T005320Z-1"
    assert trial.task == FLIPT
    assert trial.patch_bytes == 6490
    assert trial.resolved is True
    assert trial.verifier_applied is True


@pytest.mark.parametrize("patch", [True, False], ids=["found", "missing"])
def test_reads_whether_the_verifier_applied_the_patch_from_what_replay_verifier_wrote(
    tmp_path, arm, local_environment, monkeypatch, patch
):
    trial = write_agent_trial(arm / "S-1", FLIPT, "kY8wt23")
    (trial / "agent" / "model.patch").unlink()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    # A repository, as every workspace is, since the verifier asks git what the patch changed.
    for command in (["init", "-q"], ["config", "user.email", "t@example.com"], ["config", "user.name", "T"]):
        subprocess.run(["git", *command], cwd=workspace, check=True)
    (workspace / "a.txt").write_text("one\n")
    subprocess.run(["git", "add", "-A"], cwd=workspace, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=workspace, check=True)
    artifacts = tmp_path / "artifacts"
    if patch:
        artifacts.mkdir()
        (artifacts / "model.patch").write_text(
            "diff --git a/a.txt b/a.txt\n--- a/a.txt\n+++ b/a.txt\n@@ -1 +1 @@\n-one\n+two\n"
        )

    async def find_workspace(environment):
        return str(workspace)

    monkeypatch.setattr(replay, "find_workspace", find_workspace)
    task = tmp_path / "task"
    (task / "tests").mkdir(parents=True)
    # Harbor refuses a task with no test.sh, though the replay runs none.
    (task / "tests" / "test.sh").write_text("#!/bin/bash\n")
    (task / "environment").mkdir()
    (task / "instruction.md").write_text("Fix it.\n")
    (task / "task.toml").write_text('[verifier]\nenvironment_mode = "separate"\n')
    verifier = VerifierFactory.create_verifier_from_config(
        VerifierConfig(import_path="swe_drill.replay:ReplayVerifier"),
        task=Task(task), trial_paths=TrialPaths(trial), environment=local_environment,
        skip_tests_upload=True, artifacts=str(artifacts),
    )
    try:
        asyncio.run(verifier._replay())
    except replay.ReplayError as error:
        record_exception(trial, error)

    [read] = read_trials(tmp_path, ARM)

    assert read.verifier_applied is patch


def test_a_job_directorys_own_files_are_not_trials(tmp_path, arm):
    write_agent_trial(arm / "S-1", FLIPT, "kY8wt23")
    write_regrade_trial(arm / "S-1-regrade", FLIPT, "NfDX2zK")
    # A job's result.json, given a task_name, is still not a trial.
    write_json(arm / "S-1" / "result.json", {"task_name": f"swebench-pro/{NAVIDROME}"})

    trials = read_trials(tmp_path, ARM)

    assert [trial.task for trial in trials] == [FLIPT]


def test_a_trial_with_no_record_has_no_patch(tmp_path, arm):
    write_agent_trial(arm / "S-1", FLIPT, "kY8wt23", record=False)
    write_regrade_trial(arm / "S-1-regrade", FLIPT, "NfDX2zK", UNRESOLVED, applied=False)

    [trial] = read_trials(tmp_path, ARM)

    assert (trial.task, trial.patch_bytes) == (FLIPT, 0)


@pytest.mark.parametrize(
    "verifier_result",
    [None, {}, {"rewards": None}, {"rewards": {}}, {"rewards": {"other": 1.0}}],
    ids=["null", "empty", "null-rewards", "no-reward", "other-reward"],
)
def test_a_verifier_with_no_reward_is_unresolved(tmp_path, arm, verifier_result):
    write_agent_trial(arm / "S-1", FLIPT, "kY8wt23")
    write_agent_trial(arm / "S-1", NAVIDROME, "Qp3rT9a")
    write_regrade_trial(arm / "S-1-regrade", FLIPT, "NfDX2zK", verifier_result)
    write_regrade_trial(arm / "S-1-regrade", NAVIDROME, "Lm8vB2c", RESOLVED)

    trials = by_job(read_trials(tmp_path, ARM))

    assert trials["S-1", FLIPT].resolved is False
    assert trials["S-1", NAVIDROME].resolved is True


def test_a_verifier_with_no_verifier_result_key_is_unresolved(tmp_path, arm):
    write_agent_trial(arm / "S-1", FLIPT, "kY8wt23")
    regrade = write_regrade_trial(arm / "S-1-regrade", FLIPT, "NfDX2zK")
    write_json(regrade / "result.json", {"task_name": f"swebench-pro/{FLIPT}"})

    [trial] = read_trials(tmp_path, ARM)

    assert (trial.task, trial.resolved) == (FLIPT, False)


def test_a_trial_whose_verifier_never_ran_is_unresolved(tmp_path, arm):
    write_agent_trial(arm / "S-1", FLIPT, "kY8wt23")
    write_agent_trial(arm / "S-1", NAVIDROME, "Qp3rT9a")
    write_regrade_trial(arm / "S-1-regrade", NAVIDROME, "Lm8vB2c")
    # A run stopped before the second job's verifier began.
    write_agent_trial(arm / "S-2", FLIPT, "Zx4cV6b")

    trials = by_job(read_trials(tmp_path, ARM))

    assert {key: trial.resolved for key, trial in trials.items()} == {
        ("S-1", FLIPT): False,
        ("S-1", NAVIDROME): True,
        ("S-2", FLIPT): False,
    }
    assert trials["S-1", FLIPT].verifier_applied is None


def test_a_verifier_with_no_replay_json_says_nothing_of_its_patch(tmp_path, arm):
    write_agent_trial(arm / "S-1", FLIPT, "kY8wt23")
    regrade = write_regrade_trial(arm / "S-1-regrade", FLIPT, "NfDX2zK", UNRESOLVED)
    (regrade / "verifier" / "replay.json").unlink()

    [trial] = read_trials(tmp_path, ARM)

    assert (trial.resolved, trial.verifier_applied) == (False, None)


@pytest.mark.parametrize("reward", [1, 1.0])
def test_a_reward_of_one_resolves_whether_int_or_float(tmp_path, arm, reward):
    write_agent_trial(arm / "S-1", FLIPT, "kY8wt23")
    write_regrade_trial(arm / "S-1-regrade", FLIPT, "NfDX2zK", {"rewards": {"reward": reward}})

    [trial] = read_trials(tmp_path, ARM)

    assert trial.resolved is True


def test_a_reward_short_of_one_is_unresolved(tmp_path, arm):
    write_agent_trial(arm / "S-1", FLIPT, "kY8wt23")
    write_regrade_trial(arm / "S-1-regrade", FLIPT, "NfDX2zK", {"rewards": {"reward": 0.75}})

    [trial] = read_trials(tmp_path, ARM)

    assert (trial.task, trial.resolved) == (FLIPT, False)


def test_a_verifier_that_applied_no_patch_says_so(tmp_path, arm):
    write_agent_trial(arm / "S-1", FLIPT, "kY8wt23", patch_bytes=6490)
    write_agent_trial(arm / "S-1", NAVIDROME, "Qp3rT9a")
    write_regrade_trial(arm / "S-1-regrade", FLIPT, "NfDX2zK", UNRESOLVED, applied=False)
    write_regrade_trial(arm / "S-1-regrade", NAVIDROME, "Lm8vB2c")

    trials = by_job(read_trials(tmp_path, ARM))

    assert trials["S-1", FLIPT].verifier_applied is False
    assert trials["S-1", FLIPT].patch_bytes == 6490
    assert trials["S-1", NAVIDROME].verifier_applied is True


def test_a_verifier_that_raised_before_writing_replay_json_applied_no_patch(tmp_path, arm):
    write_agent_trial(arm / "S-1", FLIPT, "kY8wt23")
    verifier_raised(write_regrade_trial(arm / "S-1-regrade", FLIPT, "NfDX2zK"))

    [trial] = read_trials(tmp_path, ARM)

    assert (trial.resolved, trial.verifier_applied) == (False, False)


def test_a_verifier_that_timed_out_after_replay_json_applied_its_patch(tmp_path, arm):
    write_agent_trial(arm / "S-1", FLIPT, "kY8wt23")
    regrade = write_regrade_trial(arm / "S-1-regrade", FLIPT, "NfDX2zK")
    record_exception(regrade, VerifierTimeoutError("Verifier execution timed out after 3000.0 seconds"))

    [trial] = read_trials(tmp_path, ARM)

    assert (trial.resolved, trial.verifier_applied) == (False, True)


def regrade_wrote_an_empty_reward(trial: Path) -> None:
    """
    Make the regrade's trial one whose verifier judged its trial invalid: an
    empty reward.txt, no report.json, and Harbor's RewardFileEmptyError in
    result.json.
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
def test_only_a_verifier_that_wrote_an_empty_reward_makes_its_trial_invalid(
    tmp_path, arm, error, invalid
):
    write_agent_trial(arm / "S-1", FLIPT, "kY8wt23")
    regrade = write_regrade_trial(arm / "S-1-regrade", FLIPT, "NfDX2zK", UNRESOLVED)
    if error is not None:
        record_exception(regrade, error)

    [trial] = read_trials(tmp_path, ARM)

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
    """Make the trial's replay.json record its patch applied as `applying` says."""
    write_json(trial / "verifier" / "replay.json", {"patch": "model.patch", **applying})


@pytest.mark.parametrize(
    "applying",
    [SKIPPED_A_HUNK, {}, {"apply_rc": None, "apply_out": ""}],
    ids=["skipped-a-hunk", "no-exit-code", "null-exit-code"],
)
def test_a_verifier_that_does_not_show_it_applied_all_of_its_patch_applied_part(
    tmp_path, arm, applying
):
    write_agent_trial(arm / "S-1", FLIPT, "kY8wt23")
    write_agent_trial(arm / "S-1", NAVIDROME, "Qp3rT9a")
    write_agent_trial(arm / "S-1", FLIPT_TOO, "Zz9yX8w")
    applied_part(write_regrade_trial(arm / "S-1-regrade", FLIPT, "NfDX2zK"), applying)
    write_regrade_trial(arm / "S-1-regrade", NAVIDROME, "Lm8vB2c")
    write_regrade_trial(arm / "S-1-regrade", FLIPT_TOO, "Hh7gG6f", UNRESOLVED, applied=False)

    trials = by_job(read_trials(tmp_path, ARM))

    assert trials["S-1", FLIPT].verifier_refused is True
    # A clean apply, and a regrade that applied no patch, applied no part.
    assert trials["S-1", NAVIDROME].verifier_refused is False
    assert trials["S-1", FLIPT_TOO].verifier_refused is False


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
def test_a_verifier_records_a_test_result_only_when_parsed_counts_counts_a_test(
    tmp_path, arm, report, tested
):
    write_agent_trial(arm / "S-1", FLIPT, "kY8wt23")
    write_regrade_trial(arm / "S-1-regrade", FLIPT, "NfDX2zK", UNRESOLVED, report=report)

    [trial] = read_trials(tmp_path, ARM)

    assert trial.verifier_tested is tested


def test_a_trial_with_no_verifier_records_no_test_result(tmp_path, arm):
    write_agent_trial(arm / "S-1", FLIPT, "kY8wt23")

    [trial] = read_trials(tmp_path, ARM)

    assert trial.verifier_tested is False


def test_a_report_that_is_not_json_records_no_test_result(tmp_path, arm):
    write_agent_trial(arm / "S-1", FLIPT, "kY8wt23")
    regrade = write_regrade_trial(arm / "S-1-regrade", FLIPT, "NfDX2zK", UNRESOLVED)
    (regrade / "verifier" / "report.json").write_text('{"parsed_counts": {"PASSED": 14')

    [trial] = read_trials(tmp_path, ARM)

    assert trial.verifier_tested is False


def test_nothing_under_checks_is_read(tmp_path, arm):
    checks = tmp_path / "checks" / LANE / ARM
    write_agent_trial(checks / "S-1", NAVIDROME, "Qp3rT9a")
    write_regrade_trial(checks / "S-1-regrade", NAVIDROME, "Lm8vB2c")
    write_agent_trial(arm / "S-1", FLIPT, "kY8wt23")
    write_regrade_trial(arm / "S-1-regrade", FLIPT, "NfDX2zK")

    trials = read_trials(tmp_path, ARM)

    assert [(trial.job, trial.task) for trial in trials] == [("S-1", FLIPT)]


def test_an_arm_with_no_results_has_no_trials(tmp_path):
    write_agent_trial(tmp_path / "checks" / LANE / ARM / "S-1", FLIPT, "kY8wt23")

    assert read_trials(tmp_path, ARM) == []


def outcomes(resolved: int, unresolved: int, k: int = 1) -> list[list[bool]]:
    return [[True] * k] * resolved + [[False] * k] * unresolved


def tasks(per_task: list[list[bool]]) -> dict[str, list[bool]]:
    return {f"task-{i}": trials for i, trials in enumerate(per_task)}


def mixed_tasks(seed: int, count: int = 60) -> dict[str, list[bool]]:
    """One run's tasks, each resolved at random, so that how they are resampled moves the margin."""
    draw = random.Random(seed)
    return {f"task-{i}": [draw.random() < 0.5] for i in range(count)}


def test_runs_that_repeat_one_run_have_that_runs_margin():
    one = mixed_tasks(5)
    three = {task: trials * 3 for task, trials in one.items()}

    assert margin(three) == margin(one), (
        "three identical runs must print the one run's margin: resampling single trials "
        "narrows it by about the square root of 3"
    )


def test_the_margin_is_half_the_width_of_the_95_percent_interval():
    # 2 of 40 tasks resolve, so a resampled rate is X/40 with X ~ Binomial(40, 0.05):
    # its 2.5th percentile is 0 and its 97.5th 5, so the margin is 2.5/40.
    assert margin(tasks(outcomes(2, 38))) == pytest.approx(2.5 / 40)


def test_the_same_trials_give_the_same_margin():
    arm = mixed_tasks(11)
    random.seed(1)
    state = random.getstate()
    first = margin(arm)
    assert random.getstate() == state, "it drew from the module-level random state"
    random.seed(2)
    assert margin(arm) == first


def test_it_draws_ten_thousand_resamples(monkeypatch):
    draws = []

    class Counting(random.Random):
        def choices(self, population, *args, k=1, **kwargs):
            draws.append(k)
            return super().choices(population, *args, k=k, **kwargs)

    monkeypatch.setattr(leaderboard.random, "Random", Counting)
    margin(tasks(outcomes(3, 4)))
    assert draws == [7] * 10_000, "expected 10,000 resamples, each drawing 7 tasks"


def test_ten_thousand_resamples_over_256_tasks_take_under_a_second():
    draw = random.Random(3)
    arm = {f"task-{i}": [draw.random() < 0.5 for _ in range(3)] for i in range(256)}
    start = time.perf_counter()
    margin(arm)
    assert time.perf_counter() - start < 1.0


SWE_DRILL_TOML = """\
environment = "docker"

[arms.alpha]
agent = "swe_drill.agents.copilot_cli:CopilotCli"
"""

UNRECORDED = "unrecorded"
F, T = False, True


def write_arm(root: Path, arm: str, tasks: dict[str, list]) -> None:
    """
    The arm's results: each task's trials, the i-th in agent job `S-<i>`, each
    resolved or not as given.

    A trial given as UNRECORDED is unresolved and has no suss.json.
    """
    for task, trials in tasks.items():
        for i, resolved in enumerate(trials, start=1):
            job = root / "results" / LANE / arm / f"S-{i}"
            write_agent_trial(job, task, f"agent{i}", record=resolved != UNRECORDED)
            write_regrade_trial(
                job.with_name(f"{job.name}{REGRADE}"),
                task,
                f"regrd{i}",
                RESOLVED if resolved is True else UNRESOLVED,
            )


def run_leaderboard(root: Path, capsys, *argv: str) -> tuple[int, str, str]:
    code = cli.main(["leaderboard", "--lane", LANE, *argv], directory=root)
    out, err = capsys.readouterr()
    return code, out, err


def regrade_of(root: Path, arm: str, task: str, i: int) -> Path:
    """write_arm's i-th trial of the task, which its own verifier graded."""
    return grading_trial(root / "results" / LANE / arm / f"S-{i}{REGRADE}", task, f"regrd{i}")


@pytest.fixture
def results(tmp_path) -> Path:
    """Three arms: alpha with two runs, which resolve different tasks, and beta and gamma with one."""
    (tmp_path / "swe-drill.toml").write_text(SWE_DRILL_TOML)
    write_arm(tmp_path, "alpha", {"t1": [T, F], "t2": [F, T], "t3": [F, F]})
    write_arm(tmp_path, "beta", {"t1": [T], "t2": [T], "t3": [F]})
    write_arm(tmp_path, "gamma", {"t1": [UNRECORDED], "t2": [F], "t3": [T]})
    return tmp_path


def test_the_leaderboard_ranks_every_arm_under_results(results, capsys):
    code, out, err = run_leaderboard(results, capsys)

    assert (code, err) == (0, "")
    assert out == (
        "arm    runs  resolved rate  pass@k\n"
        "beta      1        67% ±50  67% @1\n"
        "alpha     2        33% ±25  67% @2\n"
        "gamma     1        33% ±50  33% @1\n"
    )


def row(out: str, arm: str) -> list[str]:
    """The arm's row of the table, split into its cells, its name left out."""
    table = out.split("\n\n")[0].splitlines()
    [found] = [line.split() for line in table if line.split()[:1] == [arm]]
    return found[1:]


def test_pass_at_k_counts_a_task_that_any_run_resolved(tmp_path, capsys):
    (tmp_path / "swe-drill.toml").write_text(SWE_DRILL_TOML)
    # Run 1 resolves only A, run 2 only B, and neither resolves C.
    write_arm(tmp_path, "alpha", {"A": [T, F], "B": [F, T], "C": [F, F]})

    code, out, err = run_leaderboard(tmp_path, capsys)

    assert (code, err) == (0, "")
    assert row(out, "alpha") == ["2", "33%", "±25", "67%", "@2"]


def test_the_rate_is_over_trials_not_tasks(tmp_path, capsys):
    (tmp_path / "swe-drill.toml").write_text(SWE_DRILL_TOML)
    # t1 has 3 runs and t2 one: the rate over trials is 3/4, a mean over tasks 1/2.
    write_arm(tmp_path, "alpha", {"t1": [T, T, T], "t2": [F]})

    code, out, err = run_leaderboard(tmp_path, capsys)

    assert (code, err) == (0, "")
    assert row(out, "alpha")[:2] == ["3", "75%"]


def test_arms_with_the_same_rate_go_by_name(tmp_path, capsys):
    (tmp_path / "swe-drill.toml").write_text(SWE_DRILL_TOML)
    write_arm(tmp_path, "zeta", {"t1": [T], "t2": [F]})
    write_arm(tmp_path, "eta", {"t1": [F], "t2": [T]})
    write_arm(tmp_path, "omega", {"t1": [T], "t2": [T]})
    write_arm(tmp_path, "delta", {"t1": [F], "t2": [F]})

    code, out, err = run_leaderboard(tmp_path, capsys)

    assert (code, err) == (0, "")
    assert [line.split()[0] for line in out.splitlines()[1:]] == ["omega", "eta", "zeta", "delta"]


def without(root: Path, elsewhere: Path, trials: list[tuple[str, str, int]]) -> Path:
    """A copy of the results in `elsewhere`, with write_arm's i-th trial of each (arm, task) gone."""
    copy = elsewhere / "copy"
    shutil.copytree(root, copy)
    for arm, task, i in trials:
        shutil.rmtree(trial_directory(copy / "results" / LANE / arm / f"S-{i}", task, f"agent{i}"))
    return copy


INVALID = "These trials' verifiers judged them invalid, so the leaderboard counts none of them:\n"


def test_an_invalid_trial_counts_nowhere_and_is_named(tmp_path, tmp_path_factory, capsys):
    (tmp_path / "swe-drill.toml").write_text(SWE_DRILL_TOML)
    # t4's one trial is in run 1, so with it invalid alpha has no trial of t4 to count.
    write_arm(tmp_path, "alpha", {"t1": [T, F], "t2": [F, T], "t3": [F, F], "t4": [F]})
    gone = without(tmp_path, tmp_path_factory.mktemp("gone"), [("alpha", "t3", 2), ("alpha", "t4", 1)])
    _, expected, _ = run_leaderboard(gone, capsys)
    regrade_wrote_an_empty_reward(regrade_of(tmp_path, "alpha", "t3", 2))
    regrade_wrote_an_empty_reward(regrade_of(tmp_path, "alpha", "t4", 1))

    code, out, err = run_leaderboard(tmp_path, capsys)

    assert (code, err) == (0, "")
    assert row(out, "alpha") == ["2", "40%", "±25", "67%", "@2"]
    assert out == expected + "\n" + INVALID + "  alpha  S-1  t4\n  alpha  S-2  t3\n", (
        "an invalid trial must leave the rate and pass@k as if it were not there, "
        "and be named once, as invalid and not as recording no test result"
    )


def test_an_arm_whose_every_trial_is_invalid_is_an_error(results, capsys):
    for task in ("t1", "t2", "t3"):
        regrade_wrote_an_empty_reward(regrade_of(results, "beta", task, 1))

    code, out, err = run_leaderboard(results, capsys)

    assert code != 0
    assert (out, err) == (
        "",
        f"error: every trial of arm beta under results/{LANE}/ is invalid: "
        "its verifier wrote an empty reward\n",
    )


NAMED = "These trials' verifiers recorded no test result, so their grading may have broken:\n"


def report_records_no_test_result(root: Path, arm: str, task: str, i: int) -> None:
    """Give write_arm's i-th regrade of the task the report of a verifier whose grading broke."""
    write_json(regrade_of(root, arm, task, i) / "verifier" / "report.json", NO_TEST_RESULT)


def test_the_leaderboard_names_untested_then_invalid_trials_in_the_tables_order(results, capsys):
    report_records_no_test_result(results, "gamma", "t3", 1)
    report_records_no_test_result(results, "alpha", "t2", 2)
    # A regrade with no report at all.
    (regrade_of(results, "alpha", "t1", 1) / "verifier" / "report.json").unlink()
    regrade_wrote_an_empty_reward(regrade_of(results, "gamma", "t1", 1))
    regrade_wrote_an_empty_reward(regrade_of(results, "alpha", "t3", 1))

    code, out, err = run_leaderboard(results, capsys)

    assert (code, err) == (0, "")
    assert [line.split()[0] for line in out.splitlines()[1:4]] == ["beta", "gamma", "alpha"]
    assert out.endswith(
        "\n" + NAMED + "  gamma  S-1  t3\n  alpha  S-1  t1\n  alpha  S-2  t2\n"
        "\n" + INVALID + "  gamma  S-1  t1\n  alpha  S-1  t3\n"
    )


def test_a_trial_whose_verifier_never_ran_is_named(results, capsys):
    ungrade(regrade_of(results, "beta", "t3", 1))

    code, out, err = run_leaderboard(results, capsys)

    assert (code, err) == (0, "")
    assert out.endswith("\n" + NAMED + "  beta  S-1  t3\n")


def test_nothing_under_checks_counts(results, capsys):
    _, before, _ = run_leaderboard(results, capsys)
    for arm in ("alpha", "delta"):
        checks = results / "checks" / LANE / arm
        write_agent_trial(checks / "S-9", "t1", "agent9")
        write_regrade_trial(checks / f"S-9{REGRADE}", "t1", "regrd9")

    code, out, err = run_leaderboard(results, capsys)

    assert (code, err, out) == (0, "", before)


def test_an_arm_whose_name_starts_with_a_dot_is_ranked(tmp_path, capsys):
    (tmp_path / "swe-drill.toml").write_text(SWE_DRILL_TOML)
    write_arm(tmp_path, ".experiment", {"t1": [T], "t2": [F]})

    code, out, err = run_leaderboard(tmp_path, capsys)

    assert (code, err) == (0, "")
    assert row(out, ".experiment")[:2] == ["1", "50%"]


def test_no_arm_under_results_is_an_error(tmp_path, capsys):
    (tmp_path / "swe-drill.toml").write_text(SWE_DRILL_TOML)
    write_arm(tmp_path, "alpha", {"t1": [T]})
    shutil.move(tmp_path / "results", tmp_path / "checks")

    code, out, err = run_leaderboard(tmp_path, capsys)

    assert code != 0
    assert (out, err) == ("", f"error: no results under results/{LANE}/\n")


def test_an_arm_with_no_trials_is_an_error_and_prints_nothing_else(results, capsys):
    (results / "results" / LANE / "omega" / "S-1").mkdir(parents=True)

    code, out, err = run_leaderboard(results, capsys)

    assert code != 0
    assert (out, err) == ("", f"error: no results for arm omega under results/{LANE}/\n")


def test_no_swe_drill_toml_is_an_error(results, capsys):
    (results / "swe-drill.toml").unlink()

    code, out, err = run_leaderboard(results, capsys)

    assert code != 0
    assert out == ""
    assert err.startswith("error: ") and "swe-drill.toml: no such file" in err


def test_an_arm_that_sets_baseline_is_an_error_naming_it_and_the_key(results, capsys):
    (results / "swe-drill.toml").write_text(SWE_DRILL_TOML + '\n[arms.beta]\nbaseline = "alpha"\n')

    code, out, err = run_leaderboard(results, capsys)

    assert code != 0
    assert out == ""
    assert err == (
        f"error: {results / 'swe-drill.toml'}: [arms.beta] sets baseline, which SWE-drill no longer "
        "reads; swe-drill leaderboard ranks every arm on its lane\n"
    )


@pytest.mark.parametrize("lane", ["senior", "pro-go"])
def test_a_lane_other_than_rebench_is_an_error(results, capsys, lane):
    code = cli.main(["leaderboard", "--lane", lane], directory=results)

    out, err = capsys.readouterr()
    assert code != 0
    assert (out, err) == ("", f"error: swe-drill leaderboard covers rebench only, not {lane}\n")


def test_the_lane_is_required(results, capsys):
    code = cli.main(["leaderboard"], directory=results)

    out, err = capsys.readouterr()
    assert code != 0
    assert (out, err) == ("", "error: the following arguments are required: --lane\n")


def test_arguments_after_the_separator_are_refused(results, capsys):
    code, out, err = run_leaderboard(results, capsys, "--", "-k", "2")

    assert code != 0
    assert (out, err) == ("", "error: swe-drill leaderboard takes nothing after --\n")


def regrade_applies_nothing(root: Path, arm: str, task: str, i: int) -> Path:
    """
    Make write_arm's i-th trial of the task one whose verifier found no
    patch, as ReplayVerifier leaves it: replay.json naming none, and reward 0.
    """
    regrade = regrade_of(root, arm, task, i)
    result = json.loads((regrade / "result.json").read_text())
    write_json(regrade / "result.json", dict(result, verifier_result=UNRESOLVED))
    write_json(regrade / "verifier" / "replay.json", {"patch": None})
    return regrade / "verifier" / "replay.json"


def test_a_verifier_that_applied_none_of_a_trials_patch_is_an_error_naming_it(results, capsys):
    regrade_applies_nothing(results, "beta", "t2", 1)

    code, out, err = run_leaderboard(results, capsys)

    assert code != 0
    assert (out, err) == (
        "",
        f"error: the trial of t2 in results/{LANE}/beta/S-1 made a 4182-byte patch, "
        "but its verifier in S-1 applied none\n",
    )


def test_a_trial_with_no_record_whose_verifier_applied_nothing_counts(results, capsys):
    _, before, _ = run_leaderboard(results, capsys)
    # gamma's trial of t1 has no suss.json, so no patch_bytes.
    regrade_applies_nothing(results, "gamma", "t1", 1)

    code, out, err = run_leaderboard(results, capsys)

    assert (code, err, out) == (0, "", before)


@pytest.mark.parametrize("how", ["applied-nothing", "raised", "timed-out"])
def test_an_empty_patch_whose_verifier_graded_nothing_counts_as_unresolved(results, capsys, how):
    trial = trial_directory(results / "results" / LANE / "beta" / "S-1", "t1", "agent1")
    write_json(trial / "agent" / "suss.json", {"grader": GRADER, "status": "ok", "patch_bytes": 0})
    if how == "applied-nothing":
        regrade_applies_nothing(results, "beta", "t1", 1)
    elif how == "raised":
        verifier_raised(regrade_of(results, "beta", "t1", 1))
    else:
        record_exception(
            regrade_of(results, "beta", "t1", 1),
            VerifierTimeoutError("Verifier execution timed out after 3000.0 seconds"),
        )

    code, out, err = run_leaderboard(results, capsys)

    # beta's resolved trial of t1 is now unresolved: 1 of 3 resolve, not 2.
    assert (code, err) == (0, "")
    assert row(out, "beta")[:2] == ["1", "33%"]


def test_a_verifier_that_raised_before_applying_a_trials_patch_is_an_error_naming_it(
    results, capsys
):
    verifier_raised(regrade_of(results, "alpha", "t2", 1))

    code, out, err = run_leaderboard(results, capsys)

    assert code != 0
    assert (out, err) == (
        "",
        f"error: the trial of t2 in results/{LANE}/alpha/S-1 made a 4182-byte patch, "
        "but its verifier in S-1 raised ReplayError and applied none\n",
    )


def test_a_verifier_that_applied_part_of_a_trials_patch_is_an_error_naming_it(results, capsys):
    applied_part(regrade_of(results, "alpha", "t3", 2), SKIPPED_A_HUNK)

    code, out, err = run_leaderboard(results, capsys)

    assert code != 0
    assert (out, err) == (
        "",
        f"error: the trial of t3 in results/{LANE}/alpha/S-2 made a patch, "
        "but its verifier in S-2 could not apply it\n",
    )
