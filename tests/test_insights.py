import importlib.util
import json
from pathlib import Path

LANE = "pro-hard51"
ARM = "copilot-x"


def load_script():
    path = Path(__file__).resolve().parent.parent / "scripts" / "insights.py"
    spec = importlib.util.spec_from_file_location("insights", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def trial(mirror: Path, job: str, task: str, reward=None, tests=None, error=None, patch_bytes=100, seconds=60) -> None:
    """One trial as a mirror holds it; `tests` is SWE-bench Pro's parsed list, written only when given."""
    directory = mirror / "results" / LANE / ARM / job / f"{task}__abc"
    result = {"task_name": task, "verifier_result": {"rewards": {"reward": reward}} if reward is not None else None}
    if error:
        result["exception_info"] = {"exception_type": error}
    write(directory / "result.json", result)
    write(directory / "agent" / "suss.json", {"status": "ok", "run_seconds": seconds, "patch_bytes": patch_bytes, "usage": {}})
    write(directory / "verifier" / "replay.json", {"patch": "/logs/artifacts/model.patch", "apply_rc": 0})
    if tests is not None:
        write(directory / "verifier" / "output.json", {"tests": tests})


def insights_for(mirror: Path, jobs: list[str]) -> dict:
    script = load_script()
    arm = {"name": ARM, "runs": [{"job": job, "where": "Modal"} for job in jobs]}
    trials = script.valid_trials(mirror, LANE, arm)
    by_task: dict[str, list[bool]] = {}
    for t in trials:
        by_task.setdefault(t.task, []).append(t.resolved)
    return script.arm_insights(mirror, LANE, arm, {ARM: by_task})


def test_a_pro_failure_with_parsed_tests_failed_its_tests_and_one_without_has_no_test_result(tmp_path):
    trial(tmp_path, "j1", "ran", reward=0, tests=[{"name": "t", "status": "FAILED"}])
    trial(tmp_path, "j1", "silent", reward=0)
    assert insights_for(tmp_path, ["j1"])["failures"] == {"Tests failed": 1, "No test result": 1}


def test_an_invalid_trial_counts_nowhere(tmp_path):
    trial(tmp_path, "j1", "task", reward=1)
    trial(tmp_path, "j2", "task", error="RewardFileEmptyError")
    found = insights_for(tmp_path, ["j1", "j2"])
    assert found["tasks"] == {"always": 1, "sometimes": [], "never": []}, "the invalid run must not make the task flaky"
    assert found["failures"] == {}
    assert [run["trials"] for run in found["runs"]] == [1, 0]


def test_a_trial_with_no_recorded_time_is_left_out_of_the_time_ranking(tmp_path):
    trial(tmp_path, "j1", "timed", reward=1, seconds=600)
    trial(tmp_path, "j1", "untimed", reward=1, seconds=None)
    found = insights_for(tmp_path, ["j1"])["minutes"]
    assert found == {"median": 10.0, "top": [{"task": "timed", "value": 10.0}]}
