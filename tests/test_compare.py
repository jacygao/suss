import asyncio
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

from suss import agent, cli, compare, config, replay
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
    write_agent_trial(arm / "20261101T005320Z-1", FLIPT, "kY8wt23", status="timed-out", patch_bytes=6490)
    write_regrade_trial(arm / "20261101T005320Z-1-regrade", FLIPT, "NfDX2zK")

    [trial] = read_trials(tmp_path, LANE, ARM)

    assert trial.job == "20261101T005320Z-1"
    assert trial.task == FLIPT
    assert trial.status == "timed-out"
    assert trial.grader == GRADER
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
    (task / "environment").mkdir()
    (task / "instruction.md").write_text("Fix it.\n")
    (task / "task.toml").write_text('[verifier]\nenvironment_mode = "separate"\n')
    verifier = VerifierFactory.create_verifier_from_config(
        VerifierConfig(import_path="suss.replay:ReplayVerifier"),
        task=Task(task), trial_paths=TrialPaths(trial), environment=local_environment,
        skip_tests_upload=True, artifacts=str(artifacts),
    )
    try:
        asyncio.run(verifier._replay())
    except replay.ReplayError as error:
        record_exception(trial, error)

    [read] = read_trials(tmp_path, LANE, ARM)

    assert read.verifier_applied is patch


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
def test_a_verifier_with_no_reward_is_unresolved(tmp_path, arm, verifier_result):
    write_agent_trial(arm / "S-1", FLIPT, "kY8wt23")
    write_agent_trial(arm / "S-1", NAVIDROME, "Qp3rT9a")
    write_regrade_trial(arm / "S-1-regrade", FLIPT, "NfDX2zK", verifier_result)
    write_regrade_trial(arm / "S-1-regrade", NAVIDROME, "Lm8vB2c", RESOLVED)

    trials = by_job(read_trials(tmp_path, LANE, ARM))

    assert trials["S-1", FLIPT].resolved is False
    assert trials["S-1", NAVIDROME].resolved is True


def test_a_verifier_with_no_verifier_result_key_is_unresolved(tmp_path, arm):
    write_agent_trial(arm / "S-1", FLIPT, "kY8wt23")
    regrade = write_regrade_trial(arm / "S-1-regrade", FLIPT, "NfDX2zK")
    write_json(regrade / "result.json", {"task_name": f"swebench-pro/{FLIPT}"})

    [trial] = read_trials(tmp_path, LANE, ARM)

    assert (trial.task, trial.resolved) == (FLIPT, False)


def test_a_trial_whose_verifier_never_ran_is_unresolved(tmp_path, arm):
    write_agent_trial(arm / "S-1", FLIPT, "kY8wt23")
    write_agent_trial(arm / "S-1", NAVIDROME, "Qp3rT9a")
    write_regrade_trial(arm / "S-1-regrade", NAVIDROME, "Lm8vB2c")
    # A run stopped before the second job's verifier began.
    write_agent_trial(arm / "S-2", FLIPT, "Zx4cV6b")

    trials = by_job(read_trials(tmp_path, LANE, ARM))

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

    [trial] = read_trials(tmp_path, LANE, ARM)

    assert (trial.resolved, trial.verifier_applied) == (False, None)


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


def test_a_verifier_that_applied_no_patch_says_so(tmp_path, arm):
    write_agent_trial(arm / "S-1", FLIPT, "kY8wt23", patch_bytes=6490)
    write_agent_trial(arm / "S-1", NAVIDROME, "Qp3rT9a")
    write_regrade_trial(arm / "S-1-regrade", FLIPT, "NfDX2zK", UNRESOLVED, applied=False)
    write_regrade_trial(arm / "S-1-regrade", NAVIDROME, "Lm8vB2c")

    trials = by_job(read_trials(tmp_path, LANE, ARM))

    assert trials["S-1", FLIPT].verifier_applied is False
    assert trials["S-1", FLIPT].patch_bytes == 6490
    assert trials["S-1", NAVIDROME].verifier_applied is True


def test_a_verifier_that_raised_before_writing_replay_json_applied_no_patch(tmp_path, arm):
    write_agent_trial(arm / "S-1", FLIPT, "kY8wt23")
    verifier_raised(write_regrade_trial(arm / "S-1-regrade", FLIPT, "NfDX2zK"))

    [trial] = read_trials(tmp_path, LANE, ARM)

    assert (trial.resolved, trial.verifier_applied) == (False, False)


def test_a_verifier_that_timed_out_after_replay_json_applied_its_patch(tmp_path, arm):
    write_agent_trial(arm / "S-1", FLIPT, "kY8wt23")
    regrade = write_regrade_trial(arm / "S-1-regrade", FLIPT, "NfDX2zK")
    record_exception(regrade, VerifierTimeoutError("Verifier execution timed out after 3000.0 seconds"))

    [trial] = read_trials(tmp_path, LANE, ARM)

    assert (trial.resolved, trial.verifier_applied) == (False, True)


def regrade_wrote_an_empty_reward(trial: Path) -> None:
    """
    Make the regrade's trial one whose verifier judged its trial invalid, as a
    Senior SWE-Bench verifier leaves it when its tests crashed: an empty
    reward.txt, a verifier_results.json counting no test, no report.json, and
    Harbor's RewardFileEmptyError in result.json.
    """
    (trial / "verifier").mkdir(exist_ok=True)
    (trial / "verifier" / "reward.txt").write_text("")
    write_json(trial / "verifier" / "verifier_results.json", RUNNER_FAILED)
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

    trials = by_job(read_trials(tmp_path, LANE, ARM))

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

    [trial] = read_trials(tmp_path, LANE, ARM)

    assert trial.verifier_tested is tested


def test_a_trial_with_no_verifier_records_no_test_result(tmp_path, arm):
    write_agent_trial(arm / "S-1", FLIPT, "kY8wt23")

    [trial] = read_trials(tmp_path, LANE, ARM)

    assert trial.verifier_tested is False


def test_a_report_that_is_not_json_records_no_test_result(tmp_path, arm):
    write_agent_trial(arm / "S-1", FLIPT, "kY8wt23")
    regrade = write_regrade_trial(arm / "S-1-regrade", FLIPT, "NfDX2zK", UNRESOLVED)
    (regrade / "verifier" / "report.json").write_text('{"parsed_counts": {"PASSED": 14')

    [trial] = read_trials(tmp_path, LANE, ARM)

    assert trial.verifier_tested is False


def verifier_results(passed: int, total: int, **given) -> dict:
    """The verifier/verifier_results.json a senior verifier writes, its tests left out."""
    return {"passed": passed, "total": total, "all_pass": passed == total, "tests": {}, **given}


SENIOR_TESTED = verifier_results(passed=3, total=4)
# What a senior verifier writes when its runner crashed or ran no test. It also
# writes an empty reward, so the trial is invalid.
RUNNER_FAILED = verifier_results(
    passed=0, total=0, all_pass=False, runner_errors={"python": "pytest exited 4"}
)
# What a senior verifier writes for a task with no tests, which its validation agent alone grades.
NO_TESTS = verifier_results(passed=0, total=0)


@pytest.mark.parametrize(
    "results, tested",
    [
        (SENIOR_TESTED, True),
        (verifier_results(passed=0, total=1), True),
        (NO_TESTS, True),
        (RUNNER_FAILED, False),
        (dict(RUNNER_FAILED, runner_errors=None), False),
        (dict(NO_TESTS, runner_errors={"verify": "no verify/ directory found"}), False),
        (None, False),
        ({key: value for key, value in SENIOR_TESTED.items() if key != "total"}, False),
        (dict(SENIOR_TESTED, total="4"), False),
        (dict(SENIOR_TESTED, total=True), False),
        (dict(SENIOR_TESTED, total=-1), False),
        (dict(NO_TESTS, all_pass=1), False),
        ([SENIOR_TESTED], False),
    ],
    ids=[
        "tests-ran",
        "one-failed",
        "no-tests-to-run",
        "runner-failed",
        "zero-not-passing",
        "zero-with-runner-errors",
        "no-file",
        "no-total",
        "total-a-string",
        "total-a-bool",
        "total-below-zero",
        "all-pass-not-a-bool",
        "not-an-object",
    ],
)
def test_a_senior_verifier_records_a_test_result_from_verifier_results_json(
    tmp_path, results, tested
):
    arm = tmp_path / "results" / "senior" / ARM
    write_agent_trial(arm / "S-1", FLIPT, "kY8wt23")
    # A report.json counting tests, which a senior regrade's test result must not come from.
    regrade = write_regrade_trial(arm / "S-1-regrade", FLIPT, "NfDX2zK", UNRESOLVED)
    if results is not None:
        write_json(regrade / "verifier" / "verifier_results.json", results)

    [trial] = read_trials(tmp_path, "senior", ARM)

    assert trial.verifier_tested is tested


def test_a_senior_verifier_whose_verifier_results_json_is_not_json_records_no_test_result(tmp_path):
    arm = tmp_path / "results" / "senior" / ARM
    write_agent_trial(arm / "S-1", FLIPT, "kY8wt23")
    regrade = write_regrade_trial(arm / "S-1-regrade", FLIPT, "NfDX2zK", UNRESOLVED)
    (regrade / "verifier" / "verifier_results.json").write_text('{"passed": 3, "total": 4')

    [trial] = read_trials(tmp_path, "senior", ARM)

    assert trial.verifier_tested is False


@pytest.mark.parametrize("report, tested", [(None, False), (TESTED, True)], ids=["no-report", "report"])
def test_a_rebench_verifier_takes_no_test_result_from_verifier_results_json(
    tmp_path, arm, report, tested
):
    write_agent_trial(arm / "S-1", FLIPT, "kY8wt23")
    regrade = write_regrade_trial(arm / "S-1-regrade", FLIPT, "NfDX2zK", UNRESOLVED, report=report)
    write_json(regrade / "verifier" / "verifier_results.json", NO_TESTS)

    [trial] = read_trials(tmp_path, LANE, ARM)

    assert trial.verifier_tested is tested


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
                job.with_name(f"{job.name}{REGRADE}"),
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
    elif how == "only-under-checks":
        write_agent_trial(checks / "S-1", "t1", "agent1")
        write_regrade_trial(checks / f"S-1{REGRADE}", "t1", "regrd1")


@pytest.mark.parametrize("how", ["missing", "empty", "only-under-checks"])
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
    Make write_arm's i-th trial of the task one whose verifier found no
    patch, as ReplayVerifier leaves it: replay.json naming none, and reward 0.
    """
    regrade = regrade_of(root, arm, task, i)
    result = json.loads((regrade / "result.json").read_text())
    write_json(regrade / "result.json", dict(result, verifier_result=UNRESOLVED))
    write_json(regrade / "verifier" / "replay.json", {"patch": None})
    return regrade / "verifier" / "replay.json"


@pytest.mark.parametrize("task", ["t2", "t5"], ids=["shared", "alphas-own"])
def test_a_verifier_that_applied_none_of_a_trials_patch_is_an_error_naming_both(
    results, capsys, task
):
    # Every trial read counts, including one of a task the other arms lack.
    regrade_applies_nothing(results, "alpha", task, 1)

    code, out, err = run_compare(results, capsys, LANE, "base", "alpha")

    assert code != 0
    assert (out, err) == (
        "",
        f"error: the trial of {task} in results/{LANE}/alpha/S-1 made a 4182-byte patch, "
        "but its verifier in S-1 applied none\n",
    )


def test_a_trial_with_no_record_whose_verifier_applied_nothing_is_compared(results, capsys):
    # alpha's first trial of t1 has no suss.json, so no patch_bytes.
    regrade_applies_nothing(results, "alpha", "t1", 1)

    code, out, err = run_compare(results, capsys, LANE, "alpha", "base")

    assert (code, err) == (0, "")
    assert "alpha      12       33%     17%        17%\n" in out


def test_an_empty_patch_whose_verifier_applied_nothing_counts_as_unresolved(results, capsys):
    trial = trial_directory(results / "results" / LANE / "beta" / "S-1", "t1", "agent1")
    write_json(trial / "agent" / "suss.json", {"grader": GRADER, "status": "ok", "patch_bytes": 0})
    regrade_applies_nothing(results, "beta", "t1", 1)

    code, out, err = run_compare(results, capsys, LANE, "beta", "base")

    # beta's resolved trial of t1 is now unresolved: 7 of 13 resolve, not 8.
    assert (code, err) == (0, "")
    assert "beta      13       54%      0%         0%\n" in out


@pytest.mark.parametrize("how", ["no-replay-json", "no-patch-in-it", "no-verifier"])
def test_a_patch_whose_verifier_says_nothing_of_it_counts_as_unresolved(results, capsys, how):
    replay = regrade_applies_nothing(results, "beta", "t1", 1)
    if how == "no-replay-json":
        replay.unlink()
    elif how == "no-patch-in-it":
        write_json(replay, {"apply_rc": 0})
    else:
        ungrade(replay.parent.parent)

    code, out, err = run_compare(results, capsys, LANE, "beta", "base")

    assert (code, err) == (0, "")
    assert "beta      13       54%      0%         0%\n" in out


def regrade_of(root: Path, arm: str, task: str, i: int) -> Path:
    """write_arm's i-th trial of the task, which its own verifier graded."""
    return grading_trial(root / "results" / LANE / arm / f"S-{i}{REGRADE}", task, f"regrd{i}")


@pytest.mark.parametrize("task", ["t2", "t5"], ids=["shared", "alphas-own"])
def test_a_verifier_that_raised_before_applying_a_trials_patch_is_an_error_naming_both(
    results, capsys, task
):
    verifier_raised(regrade_of(results, "alpha", task, 1))

    code, out, err = run_compare(results, capsys, LANE, "base", "alpha")

    assert code != 0
    assert (out, err) == (
        "",
        f"error: the trial of {task} in results/{LANE}/alpha/S-1 made a 4182-byte patch, "
        "but its verifier in S-1 raised ReplayError and applied none\n",
    )


@pytest.mark.parametrize(
    "arm, task",
    [("alpha", "t2"), ("alpha", "t5"), ("alpha", "t1"), ("base", "t3")],
    ids=["shared", "alphas-own", "no-record", "baseline"],
)
def test_a_verifier_that_applied_part_of_a_trials_patch_is_an_error_naming_both(
    results, capsys, arm, task
):
    # alpha's first trial of t1 has no suss.json, and its patch still counts.
    applied_part(regrade_of(results, arm, task, 1), SKIPPED_A_HUNK)

    code, out, err = run_compare(results, capsys, LANE, "base", "alpha")

    assert code != 0
    assert (out, err) == (
        "",
        f"error: the trial of {task} in results/{LANE}/{arm}/S-1 made a patch, "
        "but its verifier in S-1 could not apply it\n",
    )


def test_the_first_verifier_that_applied_part_of_a_patch_is_the_one_named(results, capsys):
    applied_part(regrade_of(results, "alpha", "t3", 2), SKIPPED_A_HUNK)
    applied_part(regrade_of(results, "alpha", "t4", 1), SKIPPED_A_HUNK)
    applied_part(regrade_of(results, "base", "t4", 3), SKIPPED_A_HUNK)

    code, out, err = run_compare(results, capsys, LANE, "alpha", "base")

    # By arm in the order given, then by job and task.
    assert code != 0
    assert (out, err) == (
        "",
        f"error: the trial of t4 in results/{LANE}/alpha/S-1 made a patch, "
        "but its verifier in S-1 could not apply it\n",
    )


def test_a_verifier_that_timed_out_counts_as_unresolved(results, capsys):
    regrade = regrade_of(results, "beta", "t1", 1)
    record_exception(regrade, VerifierTimeoutError("Verifier execution timed out after 3000.0 seconds"))

    code, out, err = run_compare(results, capsys, LANE, "beta", "base")

    assert (code, err) == (0, "")
    assert "beta      13       54%      0%         0%\n" in out


def test_an_empty_patch_whose_verifier_raised_counts_as_unresolved(results, capsys):
    trial = trial_directory(results / "results" / LANE / "beta" / "S-1", "t1", "agent1")
    write_json(trial / "agent" / "suss.json", {"grader": GRADER, "status": "ok", "patch_bytes": 0})
    verifier_raised(regrade_of(results, "beta", "t1", 1))

    code, out, err = run_compare(results, capsys, LANE, "beta", "base")

    assert (code, err) == (0, "")
    assert "beta      13       54%      0%         0%\n" in out


def test_a_trial_with_no_record_whose_verifier_raised_is_compared(results, capsys):
    # alpha's first trial of t1 has no suss.json, so no patch_bytes.
    verifier_raised(regrade_of(results, "alpha", "t1", 1))

    code, out, err = run_compare(results, capsys, LANE, "alpha", "base")

    assert (code, err) == (0, "")
    assert "alpha      12       33%     17%        17%\n" in out


def add_trial(root: Path, arm: str, job: str, task: str, grader) -> None:
    """One more trial of the arm, in an agent job of its own, resolved by its regrade."""
    jobs = root / "results" / LANE / arm
    write_agent_trial(jobs / job, task, "agentX", grader=grader)
    write_regrade_trial(jobs / f"{job}{REGRADE}", task, "regrdX")


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


NAMED = "These trials' verifiers recorded no test result, so their grading may have broken:\n"


def report_records_no_test_result(root: Path, arm: str, task: str, i: int) -> None:
    """Give write_arm's i-th regrade of the task the report of a verifier whose grading broke."""
    write_json(regrade_of(root, arm, task, i) / "verifier" / "report.json", NO_TEST_RESULT)


def test_compare_names_each_trial_whose_verifier_recorded_no_test_result(results, capsys):
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


def test_a_trial_whose_verifier_never_ran_is_named(results, capsys):
    ungrade(regrade_of(results, "beta", "t3", 2))

    code, out, err = run_compare(results, capsys, LANE, "beta", "base")

    assert (code, err) == (0, "")
    assert out.endswith("\n" + NAMED + "  beta  S-2  t3\n")


def test_a_trial_on_a_task_not_every_arm_has_is_not_named(results, capsys):
    # t5 is alpha's alone, so it counts in neither table.
    report_records_no_test_result(results, "alpha", "t5", 1)

    code, out, err = run_compare(results, capsys, LANE, "alpha", "base")

    assert (code, err) == (0, "")
    assert NAMED not in out


INVALID = "These trials' verifiers judged them invalid, so no table counts them:\n"


def without(root: Path, elsewhere: Path, trials: list[tuple[str, str, int]]) -> Path:
    """
    A copy of the results in `elsewhere`, with write_arm's i-th trial of each
    (arm, task) gone.
    """
    copy = elsewhere / "copy"
    shutil.copytree(root, copy)
    for arm, task, i in trials:
        shutil.rmtree(trial_directory(copy / "results" / LANE / arm / f"S-{i}", task, f"agent{i}"))
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
        "its verifier wrote an empty reward\n",
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


SENIOR_GRADER = {
    "dataset": "senior-swe-bench-v2026.06",
    "digest": "0e0174e2" + "0" * 56,
    "litellm": "litellm>=1.0,<1.106",
    "judge_model": "anthropic/claude-sonnet-4-6",
    "classifier_model": "anthropic/claude-haiku-4-5-20251001",
    "validation_harness": "miniswe",
    "validation_model": "anthropic/claude-sonnet-4-6",
}


def write_senior_arm(
    root: Path, arm: str, tasks: dict[str, list[tuple[dict, dict | None]]], grader=SENIOR_GRADER
) -> None:
    """
    The arm's senior results: each task's trials, the i-th in agent job `S-<i>`.
    Each is its regrade's rewards and judge_output.json, where None writes no
    judge_output.json.
    """
    for task, trials in tasks.items():
        for i, (rewards, judge_output) in enumerate(trials, start=1):
            job = root / "results" / SENIOR / arm / f"S-{i}"
            write_agent_trial(job, task, f"agent{i}", grader=grader)
            regrade = write_regrade_trial(
                job.with_name(f"{job.name}{REGRADE}"),
                task,
                f"regrd{i}",
                {"rewards": rewards},
                report=None,
            )
            write_json(regrade / "verifier" / "verifier_results.json", SENIOR_TESTED)
            if judge_output is not None:
                write_json(regrade / "verifier" / "judge_output.json", judge_output)


def regrade_of_senior(root: Path, arm: str, task: str, i: int) -> Path:
    """write_senior_arm's i-th trial of the task, which its own verifier graded."""
    return grading_trial(root / "results" / SENIOR / arm / f"S-{i}{REGRADE}", task, f"regrd{i}")


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


def test_a_verifier_with_a_reward_and_no_judges_flags_is_named(senior, capsys):
    # As when the verifier wrote reward.txt, and its aggregate step no reward.json.
    regrade = regrade_of_senior(senior, "beta", "s1", 1)
    result = json.loads((regrade / "result.json").read_text())
    write_json(regrade / "result.json", result | {"verifier_result": {"rewards": {"reward": 1.0}}})

    code, out, err = run_compare(senior, capsys, SENIOR, "base", "beta")

    assert (code, err) == (0, "")
    assert "beta           0.50 (1)            2.50 (2)        2.50 (2)     2.00 (2)\n" in out
    assert out.endswith("  base  S-1  s2  rubric, taste\n  beta  S-1  s1  rubric, taste\n")


def test_a_verifier_with_no_reward_has_no_scores_and_is_not_named_for_its_judges(senior, capsys):
    record_exception(regrade_of_senior(senior, "beta", "s1", 1), RuntimeError("Reward file is empty"))

    code, out, err = run_compare(senior, capsys, SENIOR, "base", "beta")

    assert (code, err) == (0, "")
    assert "beta           0.50 (1)            2.50 (2)        2.50 (2)     2.00 (2)\n" in out
    assert out.endswith(JUDGES_NAMED + "  base  S-1  s2  rubric, taste\n")


def test_another_lane_prints_no_means_whatever_its_rewards_hold(senior, capsys):
    (senior / "results" / SENIOR).rename(senior / "results" / LANE)
    for results in (senior / "results" / LANE).glob("*/*/*/verifier"):
        write_json(results / "report.json", TESTED)

    code, out, err = run_compare(senior, capsys, LANE, "base", "beta")

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


def test_a_senior_trial_is_named_from_what_its_verifier_results_json_counts(senior, capsys):
    base_s2 = regrade_of_senior(senior, "base", "s2", 2) / "verifier" / "verifier_results.json"
    base_s2.unlink()
    write_json(regrade_of_senior(senior, "beta", "s3", 1) / "verifier" / "verifier_results.json", NO_TESTS)
    regrade_wrote_an_empty_reward(regrade_of_senior(senior, "base", "s1", 2))

    code, out, err = run_compare(senior, capsys, SENIOR, "base", "beta")

    assert (code, err) == (0, "")
    assert out.endswith(
        "\n" + NAMED + "  base  S-2  s2\n"
        "\n" + INVALID + "  base  S-2  s1\n"
        "\n" + JUDGES_NAMED + "  base  S-1  s2  rubric, taste\n"
    ), (
        "a task with no tests to run records a test result, and an invalid trial "
        "is named only as invalid"
    )


@pytest.mark.parametrize("field", list(SENIOR_GRADER))
def test_senior_arms_whose_graders_differ_in_any_field_are_an_error_naming_it(
    tmp_path, capsys, field
):
    (tmp_path / "suss.toml").write_text(SENIOR_TOML)
    other = {**SENIOR_GRADER, field: SENIOR_GRADER[field] + "-other"}
    write_senior_arm(tmp_path, "base", {"s1": [(JUDGED, OK)]})
    write_senior_arm(tmp_path, "beta", {"s1": [(JUDGED, OK)]}, grader=other)

    code, out, err = run_compare(tmp_path, capsys, SENIOR, "base", "beta")

    assert code != 0
    assert (out, err) == (
        "",
        f"error: trials carry different graders: {field} {json.dumps(SENIOR_GRADER[field])} "
        f"in results/{SENIOR}/base/S-1, {field} {json.dumps(other[field])} "
        f"in results/{SENIOR}/beta/S-1\n",
    )


def test_senior_graders_that_differ_in_two_fields_name_both(tmp_path, capsys):
    (tmp_path / "suss.toml").write_text(SENIOR_TOML)
    other = {**SENIOR_GRADER, "judge_model": "openai/gpt-5.5", "validation_harness": "claude_code"}
    write_senior_arm(tmp_path, "base", {"s1": [(JUDGED, OK)]})
    write_senior_arm(tmp_path, "beta", {"s1": [(JUDGED, OK)]}, grader=other)

    code, out, err = run_compare(tmp_path, capsys, SENIOR, "base", "beta")

    assert code != 0
    assert (out, err) == (
        "",
        "error: trials carry different graders: "
        'judge_model "anthropic/claude-sonnet-4-6" and validation_harness "miniswe" '
        f"in results/{SENIOR}/base/S-1, "
        'judge_model "openai/gpt-5.5" and validation_harness "claude_code" '
        f"in results/{SENIOR}/beta/S-1\n",
    )


# A suss.json SussAgent wrote before senior trials had a grader, over a download
# holding one task, owner__repo-1, whose task.toml is OLD_TASK_TOML.
OLD_REBENCH_RECORD = Path(__file__).resolve().parent / "old_records" / "rebench-suss.json"
OLD_TASK_TOML = '[environment]\nnetwork_mode = "public"\n'


def test_a_rebench_trial_recorded_before_senior_graders_compares_with_one_recorded_now(
    tmp_path, capsys
):
    download = tmp_path / "tasks" / "swe-rebench-07-2026"
    (download / "owner__repo-1").mkdir(parents=True)
    (download / "owner__repo-1" / "task.toml").write_text(OLD_TASK_TOML)
    (tmp_path / "suss.toml").write_text(
        'swe_rebench = "tasks/swe-rebench-07-2026"\nenvironment = "docker"\nk = 1\n'
        'concurrency = 1\n\n[arms.base]\nharness = "."\n\n'
        '[arms.beta]\nharness = "."\nbaseline = "base"\n'
    )
    settings = config.load(tmp_path / "suss.toml", "beta", "rebench")
    jobs = tmp_path / "results" / "rebench"
    old = write_agent_trial(jobs / "base" / "S-1", "t1", "agent1")
    shutil.copy(OLD_REBENCH_RECORD, old / "agent" / "suss.json")
    write_agent_trial(jobs / "beta" / "S-1", "t1", "agent1", grader=agent.grader(settings, "rebench"))
    for arm in ("base", "beta"):
        write_regrade_trial(jobs / arm / f"S-1{REGRADE}", "t1", "regrd1")

    code, out, err = run_compare(tmp_path, capsys, "rebench", "base", "beta")

    assert (code, err) == (0, ""), "a rebench trial recorded before senior graders no longer compares"


ARGON = "gemini-4-argon"
SOL = "gpt-6.1-sol"


def judge_json(
    rubric=None,
    practice=None,
    relative=None,
    bloat=None,
    *,
    model=ARGON,
    rubric_status="ok",
    taste_status="ok",
    sessions=None,
) -> dict:
    """
    The verifier/judge.json JudgeVerifier writes, trimmed to what compare reads.

    A judge that did not finish ok has no scores, as JudgeVerifier writes them,
    except the patch bloat, which it measures without the taste judge.
    """
    scores = {}
    if rubric_status == "ok":
        scores |= {"rubric_score": rubric, "rubric_f2p_score": 1.0, "rubric_p2p_score": 1.0}
    if bloat is not None:
        scores["taste_patch_bloat"] = bloat
    if taste_status == "ok":
        scores |= {"taste_practice_alignment": practice, "taste_relative_taste": relative}
    for kind, status in (("rubric", rubric_status), ("taste", taste_status)):
        scores[f"{kind}_judge_ok"] = 1 if status == "ok" else 0
    return {
        "judge": {
            "client": "copilot-cli",
            "version": "1.0.105",
            "requested_version": "1.0.105",
            "model": model,
            "effort": "high",
            "classifier_model": "claude-haiku-4.5",
        },
        "sessions": sessions
        or [
            {"name": "classify-agent", "model": "claude-haiku-4.5", "status": "ok"},
            {"name": "classify-oracle", "model": "claude-haiku-4.5", "status": "ok"},
            {"name": "rubric", "model": model, "effort": "high", "status": rubric_status},
            {"name": "taste", "model": model, "effort": "high", "status": taste_status},
        ],
        "rubric_status": rubric_status,
        "taste_status": taste_status,
        "scores": scores,
        "credits": 25.1933,
    }


def write_judged_arm(root: Path, arm: str, tasks: dict[str, list[dict]], lane: str = SENIOR) -> None:
    """
    The arm's results with JudgeAgent as each regrade's agent: the i-th trial of
    each task in agent job `S-<i>`, with the judge.json given. On senior, the
    regrade's rewards are those of a verifier with no key, whose judges failed.
    """
    for task, records in tasks.items():
        for i, record in enumerate(records, start=1):
            job = root / "results" / lane / arm / f"S-{i}"
            write_agent_trial(job, task, f"agent{i}", grader=SENIOR_GRADER if lane == SENIOR else GRADER)
            regrade = write_regrade_trial(
                job.with_name(f"{job.name}{REGRADE}"),
                task,
                f"regrd{i}",
                {"rewards": JUDGES_FAILED if lane == SENIOR else {"reward": 1.0}},
                report=None if lane == SENIOR else TESTED,
            )
            if lane == SENIOR:
                write_json(regrade / "verifier" / "verifier_results.json", SENIOR_TESTED)
            write_json(regrade / "verifier" / "judge.json", record)


BASE_JUDGED = {"s1": [judge_json(1.0, 4.0, 3.0, 1.0)], "s2": [judge_json(0.5, 2.0, 2.0, 2.0)]}
BETA_JUDGED = {"s1": [judge_json(0.75, 5.0, 4.0, 1.5)], "s2": [judge_json(0.25, 3.0, 3.0, 0.5)]}


def write_copilot_judged(root: Path, base=BASE_JUDGED, beta=BETA_JUDGED) -> Path:
    """Two senior arms over s1 and s2, every regrade judged by Copilot CLI with Argon unless given."""
    (root / "suss.toml").write_text(SENIOR_TOML)
    write_judged_arm(root, "base", base)
    write_judged_arm(root, "beta", beta)
    return root


MEANS_HEADER = "mean (trials)    rubric  practice alignment  relative taste  patch bloat  judge\n"
ARGON_JUDGE = "copilot-cli gemini-4-argon high"
BASE_MEANS = "base           0.75 (2)            3.00 (2)        2.50 (2)     1.50 (2)  " + ARGON_JUDGE + "\n"
BETA_MEANS = "beta           0.50 (2)            4.00 (2)        3.50 (2)     1.00 (2)  " + ARGON_JUDGE + "\n"


def test_compare_gives_each_arms_mean_scores_from_judge_json_and_names_its_judge(tmp_path, capsys):
    code, out, err = run_compare(write_copilot_judged(tmp_path), capsys, SENIOR, "base", "beta")

    # The verifier's rewards have both judges failed, so reading them would
    # give no mean and name every trial.
    assert (code, err) == (0, "")
    assert out.endswith("beta - base    +0% [+0%, +0%]\n\n" + MEANS_HEADER + BASE_MEANS + BETA_MEANS)


def test_a_trial_whose_copilot_judges_failed_counts_in_no_mean_and_is_named(tmp_path, capsys):
    failed = judge_json(bloat=9.0, rubric_status="failed:timeout", taste_status="failed:exit_1")
    root = write_copilot_judged(tmp_path, base=BASE_JUDGED | {"s1": [*BASE_JUDGED["s1"], failed]})

    code, out, err = run_compare(root, capsys, SENIOR, "base", "beta")

    assert (code, err) == (0, "")
    assert BASE_MEANS in out, "a failed judge's missing scores, or its bloat, moved base's means"
    assert out.endswith(JUDGES_NAMED + "  base  S-2  s1  rubric, taste\n")


def test_a_judge_that_answered_on_its_retry_is_judged(tmp_path, capsys):
    # As judge-argon-1 recorded it: the first rubric session gave no JSON, and its retry did.
    retried = judge_json(
        1.0,
        4.0,
        3.0,
        1.0,
        sessions=[
            {"name": "classify-agent", "status": "ok"},
            {"name": "classify-oracle", "status": "ok"},
            {"name": "rubric", "status": "failed:no_json", "error": "the reply holds no JSON object"},
            {"name": "rubric-retry", "status": "ok"},
            {"name": "taste", "status": "ok"},
        ],
    )
    root = write_copilot_judged(tmp_path, base=BASE_JUDGED | {"s1": [retried]})

    code, out, err = run_compare(root, capsys, SENIOR, "base", "beta")

    assert (code, err) == (0, "")
    assert BASE_MEANS in out
    assert JUDGES_NAMED not in out, "a judge that answered on its retry was named as failed"


def test_an_arm_judged_by_two_judges_has_no_mean_and_says_which(tmp_path, capsys):
    sol = judge_json(0.0, 1.0, 1.0, 1.0, model=SOL)
    root = write_copilot_judged(tmp_path, beta=BETA_JUDGED | {"s1": [*BETA_JUDGED["s1"], sol]})

    code, out, err = run_compare(root, capsys, SENIOR, "base", "beta")

    assert (code, err) == (0, "")
    assert out.endswith(
        MEANS_HEADER + BASE_MEANS
        + "beta                  -                   -               -            -  more than one\n"
        "\n"
        "beta was judged by copilot-cli gemini-4-argon high on 2 trials and "
        "copilot-cli gpt-6.1-sol high on 1 trial, so it has no mean.\n"
    )


def test_arms_judged_by_different_judges_get_means_and_no_comparison(senior, capsys):
    shutil.rmtree(senior / "results" / SENIOR / "beta")
    write_judged_arm(
        senior, "beta", {"s1": [judge_json(1.0, 4.0, 4.0, 1.0)], "s2": [judge_json(0.5, 2.0, 2.0, 3.0)]}
    )

    code, out, err = run_compare(senior, capsys, SENIOR, "base", "beta")

    # base's means are over the verifier's rewards for s1 and s2 alone, as beta has no s3.
    assert (code, err) == (0, "")
    assert out.endswith(
        MEANS_HEADER
        + "base           0.58 (3)            3.00 (3)        2.67 (3)     1.16 (3)  "
        "senior verifier anthropic/claude-sonnet-4-6\n"
        "beta           0.75 (2)            3.00 (2)        3.00 (2)     2.00 (2)  " + ARGON_JUDGE + "\n"
        "\n"
        "The means do not compare across arms: base was judged by senior verifier "
        "anthropic/claude-sonnet-4-6, beta by copilot-cli gemini-4-argon high.\n"
        "\n" + JUDGES_NAMED + "  base  S-1  s2  rubric, taste\n"
    )


def test_judge_json_wins_over_the_verifiers_judges(senior, capsys):
    # Every regrade of the senior fixture has the verifier's judges' scores.
    for task in ("s1", "s2", "s3"):
        for i in (1, 2):
            write_json(
                regrade_of_senior(senior, "base", task, i) / "verifier" / "judge.json",
                judge_json(0.0, 1.0, 1.0, 5.0),
            )

    code, out, err = run_compare(senior, capsys, SENIOR, "base", "beta")

    assert (code, err) == (0, "")
    assert "base           0.00 (6)            1.00 (6)        1.00 (6)     5.00 (6)  " + ARGON_JUDGE + "\n" in out, (
        "base's means are not judge.json's, which wins over the verifier's"
    )


def test_a_trial_off_senior_judged_for_taste_alone_has_taste_means_and_no_rubric(tmp_path, capsys):
    (tmp_path / "suss.toml").write_text(SENIOR_TOML)
    for arm, practice in (("base", 4.0), ("beta", 2.0)):
        write_judged_arm(
            tmp_path,
            arm,
            {"t1": [judge_json(None, practice, 3.0, 1.0, rubric_status="skipped:no_criteria")]},
            lane=LANE,
        )

    code, out, err = run_compare(tmp_path, capsys, LANE, "base", "beta")

    assert (code, err) == (0, "")
    assert out.endswith(
        "mean (trials)  rubric  practice alignment  relative taste  patch bloat  judge\n"
        "base            - (0)            4.00 (1)        3.00 (1)     1.00 (1)  " + ARGON_JUDGE + "\n"
        "beta            - (0)            2.00 (1)        3.00 (1)     1.00 (1)  " + ARGON_JUDGE + "\n"
    )


# rebench, whose agent job grades each trial in its own separate verifier


def write_graded_trial(
    job: Path,
    task: str,
    suffix: str,
    verifier_result=RESOLVED,
    *,
    replay_record: dict | None = None,
    report=TESTED,
) -> Path:
    """
    A rebench agent trial as Harbor leaves it once ReplayVerifier has graded it:
    the reward in its result.json, and the verifier's replay.json and report.json.
    """
    trial = write_agent_trial(job, task, suffix)
    result = json.loads((trial / "result.json").read_text())
    write_json(trial / "result.json", dict(result, verifier_result=verifier_result))
    if replay_record is None:
        replay_record = {
            "patch": "/logs/artifacts/model.patch",
            "patch_bytes": 4182,
            "apply_rc": 0,
            "apply_out": "$ git apply --verbose\nApplied patch cleanly.\n",
            "tests_reset": {"restored": [], "removed": []},
        }
    write_json(trial / "verifier" / "replay.json", replay_record)
    if report is not None:
        write_json(trial / "verifier" / "report.json", report)
    return trial


def rebench_arm(root: Path, arm: str = ARM) -> Path:
    return root / "results" / "rebench" / arm


def test_a_rebench_trial_is_graded_by_its_own_verifier(tmp_path):
    write_graded_trial(rebench_arm(tmp_path) / "S-1", FLIPT, "kY8wt23")
    write_graded_trial(rebench_arm(tmp_path) / "S-1", NAVIDROME, "Qp3rT9a", UNRESOLVED, report=None)

    trials = {trial.task: trial for trial in read_trials(tmp_path, "rebench", ARM)}

    assert (trials[FLIPT].resolved, trials[FLIPT].verifier_applied) == (True, True)
    assert trials[FLIPT].verifier_tested is True
    assert (trials[NAVIDROME].resolved, trials[NAVIDROME].verifier_tested) == (False, False)


def rebench_results(root: Path) -> Path:
    """Two arms on rebench, each with one trial of FLIPT, both resolved."""
    (root / "suss.toml").write_text(SUSS_TOML)
    for arm in ("base", "beta"):
        write_graded_trial(rebench_arm(root, arm) / "S-1", FLIPT, f"agent{arm}")
    return root


def test_a_rebench_verifier_that_applied_none_of_a_trials_patch_is_an_error_naming_it(
    tmp_path, capsys
):
    trial = trial_directory(rebench_arm(rebench_results(tmp_path), "beta") / "S-1", FLIPT, "agentbeta")
    write_json(trial / "verifier" / "replay.json", {"patch": None})
    record_exception(trial, replay.ReplayError("the trial left no patch"))

    code, out, err = run_compare(tmp_path, capsys, "rebench", "beta", "base")

    assert (code, out) == (1, "")
    assert err == (
        f"error: the trial of {FLIPT} in results/rebench/beta/S-1 made a 4182-byte patch, "
        "but its verifier in S-1 applied none\n"
    )


def test_a_rebench_verifier_that_could_not_apply_a_trials_patch_is_an_error_naming_it(
    tmp_path, capsys
):
    trial = trial_directory(rebench_arm(rebench_results(tmp_path), "beta") / "S-1", FLIPT, "agentbeta")
    record = json.loads((trial / "verifier" / "replay.json").read_text())
    write_json(trial / "verifier" / "replay.json", dict(record, apply_rc=1))

    code, out, err = run_compare(tmp_path, capsys, "rebench", "beta", "base")

    assert (code, out) == (1, "")
    assert err == (
        f"error: the trial of {FLIPT} in results/rebench/beta/S-1 made a patch, "
        "but its verifier in S-1 could not apply it\n"
    )


def test_a_senior_trial_is_read_from_its_own_verifier_for_its_test_result_and_judge_json(tmp_path):
    arm = tmp_path / "results" / "senior" / ARM
    trial = write_agent_trial(arm / "S-1", FLIPT, "kY8wt23")
    write_regrade_trial(arm / f"S-1{REGRADE}", FLIPT, "NfDX2zK", RESOLVED, report=None)
    write_json(trial / "verifier" / "verifier_results.json", SENIOR_TESTED)
    write_json(trial / "verifier" / "judge.json", judge_json(0.5, 4.0, 3.0, 1.25))
    # Where a JudgeAgent regrade wrote it, in the trial and in a regrade job of its own.
    write_json(trial / "agent" / "judge.json", judge_json(1.0, 1.0, 1.0, 9.0))
    old = trial_directory(write_job(arm / "S-0-regrade"), FLIPT, "OldOld1")
    write_json(old / "agent" / "judge.json", judge_json(1.0, 1.0, 1.0, 9.0))

    trials = by_job(read_trials(tmp_path, "senior", ARM))

    read = trials["S-1", FLIPT]
    assert (read.resolved, read.verifier_tested, read.verifier_applied) == (True, True, True)
    assert read.judge == "copilot-cli gemini-4-argon high"
    scored = ("rubric_score", "taste_practice_alignment", "taste_patch_bloat")
    assert {key: read.rewards.get(key) for key in scored} == {
        "rubric_score": 0.5,
        "taste_practice_alignment": 4.0,
        "taste_patch_bloat": 1.25,
    }, "the scores were not read from the agent trial's verifier/judge.json"
