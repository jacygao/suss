"""
Run senior's own run_validate.py through `swe_drill.validate`, with a
stand-in `copilot`, on a one-story validation spec over a small repository.

The repository's `lines.value()` returns "fixed" once the trial's change is
in, and "broken" at its base. The story expects "fixed". The stand-in plays
the validation agent, which writes the story's script, and the review judge,
which answers with the `submit_review` JSON. run_validate.py, its drivers and
its judge's parser are senior's, copied unchanged from a design task.
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from conftest import CREDENTIALS
from swe_drill import validate

HERE = Path(__file__).resolve().parent
SENIOR_TESTS = HERE / "senior_tasks" / "prefect-feat-dbt-per-node-hardening" / "tests"
LAUNCHER = HERE / "run_senior_validation.py"
TOKEN = "github_pat_stand_in_validation_token"
STORY = "value_is_fixed"
SPEC = f"""\
instructions = "Check what lines.value() returns."

[settings]
va_max_turns = 10
va_timeout_sec = {{va_timeout}}
script_timeout_sec = 60

[[story]]
id = "{STORY}"
driver = "pytest"
description = "value() returns the fixed value."
procedure = "Call lines.value() and compare it with the expected value."

[[story.test_case]]
inputs = {{{{}}}}
expected = {{{{ value = "fixed" }}}}
"""
# Checks the story honestly, and keeps the environment the story ran with.
SCRIPT = """\
import json
import os

import lines


def test_value(inputs, expected):
    with open(os.path.join(os.path.dirname(__file__), "..", "story-env.json"), "w") as f:
        json.dump(dict(os.environ), f)
    assert lines.value() == expected["value"]
"""
RUBRIC = tomllib.loads((SENIOR_TESTS / "ssb_lib" / "validation_judge" / "validation_judge_rubric.toml").read_text())
CRITERIA = [criterion["id"] for criterion in RUBRIC["criterion"]]


def review(failure_class: str = "pass", score: int = 3) -> str:
    """The review judge's submit_review answer, scoring every criterion `score`."""
    return json.dumps({
        "severity": {
            "per_criterion": {name: score for name in CRITERIA},
            "rationales": {name: "fine" for name in CRITERIA},
            "per_story": {
                STORY: {"implemented": True, "faithful_score": 3, "failure_class": failure_class, "notes": "ok"}
            },
            "notes": "",
        },
        "actionable": [],
    })


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def box(tmp_path) -> Path:
    """
    The sandbox's stand-ins under `sandbox/`, the stand-in copilot under
    `copilot/`, and the settings and token JudgeVerifier leaves under
    `sandbox/opt/validation/`.
    """
    root = tmp_path / "sandbox"
    repo = root / "repo"
    repo.mkdir(parents=True)
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.com")
    (repo / ".gitignore").write_text("__pycache__/\n")
    (repo / "lines.py").write_text("def value():\n    return 'broken'\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "base")
    (root / "base_ref").write_text(git(repo, "rev-parse", "HEAD"))
    (root / "validate").mkdir()
    write_spec(tmp_path)
    (root / "logs").mkdir()

    scenario = tmp_path / "copilot"
    scenario.mkdir()
    (scenario / "version").write_text("1.0.105")
    wrapper = scenario / "copilot"
    wrapper.write_text(f'#!/bin/sh\nexec {sys.executable} {HERE / "fake_copilot.py"} {scenario} "$@"\n')
    wrapper.chmod(0o755)
    write_scenario(tmp_path)

    settings = root / "opt" / "validation"
    settings.mkdir(parents=True)
    write_settings(tmp_path)
    (settings / "token").write_text(TOKEN)
    return tmp_path


# A second story, whose script the stand-in agent never writes.
UNWRITTEN = """
[[story]]
id = "value_is_a_string"
driver = "pytest"
description = "value() returns a string."
procedure = "Call lines.value() and check its type."

[[story.test_case]]
inputs = {}
expected = { type = "str" }
"""


def write_spec(box: Path, va_timeout: int = 60, unwritten: bool = False) -> None:
    text = SPEC.format(va_timeout=va_timeout) + (UNWRITTEN if unwritten else "")
    (box / "sandbox" / "validate" / "validation_spec.toml").write_text(text)


def write_settings(box: Path, **changes) -> None:
    settings = {
        "copilot": str(box / "copilot" / "copilot"),
        "version": "1.0.105",
        "model": "gemini-4-argon",
        "effort": "high",
        "credits": 200,
        **changes,
    }
    (box / "sandbox" / "opt" / "validation" / "settings.json").write_text(json.dumps(settings))


def fix(box: Path) -> None:
    """Commit the trial's change, as test.sh commits the agent's changes before validation."""
    repo = box / "sandbox" / "repo"
    (repo / "lines.py").write_text("def value():\n    return 'fixed'\n")
    git(repo, "commit", "-qam", "agent changes")


def write_scenario(box: Path, agent: dict | None = None, review_answer: str | None = None, **kinds) -> None:
    """
    What the stand-in does as the validation agent, which by default writes
    the story's script for 20 credits, and as the review judge, which by
    default passes the story for 7.
    """
    script = box / "sandbox" / "logs" / "validation_scripts" / f"{STORY}.py"
    scenarios = {
        "agent": {"files": {str(script): SCRIPT}, "answer": "Wrote the script.", "nano": 20_000_000_000},
        "review": {"answer": review_answer or review(), "nano": 7_000_000_000},
    }
    scenarios["agent"].update(agent or {})
    for kind, changes in kinds.items():
        scenarios[kind].update(changes)
    (box / "copilot" / "scenario.json").write_text(json.dumps(scenarios))


def validate_in(box: Path, env: dict | None = None) -> subprocess.CompletedProcess:
    """Run the validation as test.sh would, with the settings JudgeVerifier names, and no key."""
    environment = {name: value for name, value in os.environ.items() if name not in CREDENTIALS}
    environment.update(
        SWE_DRILL_VALIDATION=str(box / "sandbox" / "opt" / "validation"),
        REPO_NAME="repo",
        PYTHONDONTWRITEBYTECODE="1",
        **(env or {}),
    )
    return subprocess.run(
        [sys.executable, str(LAUNCHER), str(SENIOR_TESTS), str(box / "sandbox")],
        env=environment, capture_output=True, text=True, timeout=300,
    )


def calls(box: Path) -> list[dict]:
    path = box / "copilot" / "calls.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def logs(box: Path) -> Path:
    return box / "sandbox" / "logs"


def results(box: Path) -> dict:
    return json.loads((logs(box) / "validation_results.json").read_text())


def record(box: Path) -> dict:
    return json.loads((logs(box) / "validation.json").read_text())


def explain(run: subprocess.CompletedProcess) -> str:
    return f"exit {run.returncode}:\n{run.stderr[-4000:]}"


# The reward follows the stories


@pytest.mark.parametrize("fixed, score", [(True, 1.0), (False, 0.0)], ids=["fixed", "unchanged"])
def test_the_validation_score_follows_the_story_the_agent_wrote_both_ways(box, fixed, score):
    if fixed:
        fix(box)
    else:
        write_scenario(box, review_answer=review("behavioral_fail"))

    run = validate_in(box)

    assert run.returncode == 0, explain(run)
    # run_validate.py forgives a failure it detects when the stories gave a verdict.
    assert "infrastructure failure" not in run.stderr, explain(run)
    found = results(box)
    assert found["validation_score"] == score
    assert found["stories"][STORY]["pass"] is fixed
    assert found["stories"][STORY]["execution"] == "yes"
    assert "validation_agent_infrastructure_failure" not in found
    assert json.loads((logs(box) / "reward.json").read_text())["validation_score"] == score
    assert [call["kind"] for call in calls(box)] == ["agent", "review", "review"]


def test_runs_as_seniors_claude_code_harness_with_the_model_asked_for_and_no_key(box):
    fix(box)

    run = validate_in(box)

    assert run.returncode == 0, explain(run)
    found = results(box)
    assert (found["validation_agent_harness"], found["validation_agent_model"]) == ("claude_code", "gemini-4-argon")
    assert (logs(box) / "cc_result_subtype.txt").read_text() == "success"
    assert (logs(box) / "cc_output.txt").read_text() == "Wrote the script."


# The review judge


def test_the_review_judges_verdict_is_read_by_seniors_parser_and_can_discard(box):
    fix(box)
    write_scenario(box, review_answer=review(score=0))

    run = validate_in(box)

    assert run.returncode == 3, explain(run)
    assert results(box)["judge_discard"].startswith("pre-retry: ")
    [judged] = [call for call in calls(box) if call["kind"] == "review"]
    assert "submit_review" in judged["instructions"]
    assert '"per_criterion"' in judged["instructions"], "the review prompt gives no answer schema"


def test_a_review_answer_that_is_not_json_is_retried_in_the_same_session(box):
    fix(box)
    write_scenario(box, review={"answer": "I think it is fine.", "retry": {"answer": review(), "nano": 9_000_000_000}})

    run = validate_in(box)

    assert run.returncode == 0, explain(run)
    reviews = [call for call in calls(box) if call["kind"] == "review"]
    assert [call["resumed"] for call in reviews] == [False, True, False, True]
    assert reviews[1]["flags"]["--resume"] == reviews[0]["flags"]["--session-id"]
    sessions = [s for s in record(box)["sessions"] if s["kind"] == "review"]
    assert [s["status"] for s in sessions] == ["failed:no_json", "ok", "failed:no_json", "ok"]
    # A resumed session reports what it spent counted on from its first answer's.
    assert [s["credits"] for s in sessions] == [7.0, 9.0, 7.0, 9.0]
    assert results(box)["validation_score"] == 1.0


def test_a_review_judge_that_never_answers_validly_makes_the_trial_invalid(box):
    fix(box)
    write_scenario(box, review={"answer": "{}", "retry": {"answer": "{}"}})

    run = validate_in(box)

    assert run.returncode == 2, explain(run)
    sessions = [s for s in record(box)["sessions"] if s["kind"] == "review"]
    assert [s["status"] for s in sessions] == ["failed:invalid_answer"] * 2


# The agent's own failures


def test_an_agent_that_fails_is_an_infrastructure_failure_not_a_zero(box):
    fix(box)
    # A story with no script gives no verdict, so run_validate.py cannot forgive the failure.
    write_spec(box, unwritten=True)
    write_scenario(box, agent={"exit": 1})

    run = validate_in(box)

    assert run.returncode == 2, explain(run)
    assert "failed:exit_1" in results(box)["validation_agent_infrastructure_failure"]
    assert [s["status"] for s in record(box)["sessions"] if s["kind"] == "agent"] == ["failed:exit_1"]


def test_an_agent_that_runs_out_of_time_is_recorded_so(box):
    fix(box)
    write_spec(box, va_timeout=1)
    write_scenario(box, agent={"files": {}, "sleep": 5})

    run = validate_in(box)

    assert run.returncode == 2, explain(run)
    first = record(box)["sessions"][0]
    assert (first["kind"], first["status"]) == ("agent", "failed:timeout")
    assert "1 seconds" in first["error"]
    assert "validation_agent_infrastructure_failure" in results(box)


# The settings JudgeVerifier leaves


@pytest.mark.parametrize("missing", ["token", "settings", "copilot"])
def test_a_validation_that_cannot_reach_copilot_fails_before_any_session(box, missing):
    fix(box)
    settings = box / "sandbox" / "opt" / "validation"
    if missing == "copilot":
        (box / "copilot" / "copilot").chmod(0o644)
    else:
        (settings / ("token" if missing == "token" else "settings.json")).unlink()

    run = validate_in(box)

    assert run.returncode == 2, explain(run)
    found = record(box)
    assert found["status"] == "failed:setup"
    assert {"token": "COPILOT_GITHUB_TOKEN", "settings": "settings.json", "copilot": "copilot"}[missing] in found["error"]
    assert calls(box) == []


def test_a_validation_judgeverifier_could_not_set_up_fails_with_its_reason(box):
    fix(box)
    write_settings(box, copilot=None, error="Copilot CLI could not be installed: exit 3")
    (box / "sandbox" / "opt" / "validation" / "token").unlink()

    run = validate_in(box)

    assert run.returncode == 2, explain(run)
    assert record(box)["error"] == "Copilot CLI could not be installed: exit 3"


def test_records_the_agent_its_sessions_and_the_credits_they_spent(box):
    fix(box)

    run = validate_in(box)

    assert run.returncode == 0, explain(run)
    found = record(box)
    assert found["validation_agent"] == {
        "client": "copilot-cli", "version": "1.0.105", "model": "gemini-4-argon", "effort": "high"
    }
    assert [(s["kind"], s["status"], s["credits"]) for s in found["sessions"]] == [
        ("agent", "ok", 20.0), ("review", "ok", 7.0), ("review", "ok", 7.0)
    ]
    assert found["credits"] == 34.0
    assert found["exit"] == 0


def test_each_session_runs_on_the_model_and_effort_asked_for_capped_and_without_the_web(box):
    fix(box)
    write_settings(box, model="gpt-6.1-sol", effort="medium", credits=150)

    validate_in(box)

    agent, *reviews = calls(box)
    for call in (agent, *reviews):
        assert call["flags"]["--model"] == ["gpt-6.1-sol"]
        assert call["flags"]["--reasoning-effort"] == ["medium"]
        assert call["flags"]["--max-ai-credits"] == ["150"]
    assert agent["cwd"] == str(box / "sandbox" / "repo")
    assert agent["flags"]["--deny-tool"] == ["web_fetch", "web_search"]
    for call in reviews:
        assert call["flags"]["--available-tools"] == ["view"]
        assert call["flags"]["--deny-tool"] == ["shell", "write"]
        assert call["cwd"] != str(box / "sandbox" / "repo")


def test_an_empty_effort_passes_none(box):
    fix(box)
    write_settings(box, effort="")

    validate_in(box)

    assert all("--reasoning-effort" not in call["flags"] for call in calls(box))


def test_a_prompt_too_long_for_one_argument_is_read_from_a_file(box):
    spec = box / "sandbox" / "validate" / "validation_spec.toml"
    spec.write_text(spec.read_text().replace("Check what", "Check " + "very " * 40_000 + "carefully what"))
    fix(box)

    run = validate_in(box)

    assert run.returncode == 0, explain(run)
    for call in calls(box):
        assert len(call["prompt"]) < validate.ARG_LIMIT
    assert any(len(call["instructions"]) > validate.ARG_LIMIT for call in calls(box))


# The token


def test_the_token_reaches_copilot_alone_and_no_story_record_or_log(box):
    fix(box)

    run = validate_in(box)

    assert run.returncode == 0, explain(run)
    sha = hashlib.sha256(TOKEN.encode()).hexdigest()
    for call in calls(box):
        assert call["token_sha256"] == sha
        assert not call["gh_token"]
        assert TOKEN not in " ".join(call["argv"])
        assert call["flags"]["--secret-env-vars"] == ["COPILOT_GITHUB_TOKEN"]
    story_env = json.loads((logs(box) / "story-env.json").read_text())
    assert "COPILOT_GITHUB_TOKEN" not in story_env
    assert TOKEN not in json.dumps(story_env)
    assert not (box / "sandbox" / "opt" / "validation" / "token").exists()
    written = [path for path in (box / "sandbox").rglob("*") if path.is_file()]
    assert [path for path in written if TOKEN.encode() in path.read_bytes()] == []


def test_a_log_holding_the_token_is_removed(box):
    fix(box)
    write_scenario(box, agent={"leak": True})

    run = validate_in(box)

    assert run.returncode == 0, explain(run)
    [removed] = record(box)["sessions"][0]["removed_for_token"]
    assert not Path(removed).exists()
    written = [path for path in (box / "sandbox").rglob("*") if path.is_file()]
    assert [path for path in written if TOKEN.encode() in path.read_bytes()] == []


def test_a_token_in_a_reply_never_reaches_a_record(box):
    fix(box)
    write_scenario(box, agent={"answer": f"The token is {TOKEN}."})

    run = validate_in(box)

    assert run.returncode == 0, explain(run)
    written = [path for path in (box / "sandbox").rglob("*") if path.is_file()]
    assert [path for path in written if TOKEN.encode() in path.read_bytes()] == []
    assert "[redacted]" in (logs(box) / "cc_output.txt").read_text()
