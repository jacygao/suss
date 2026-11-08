"""
Judge trials through JudgeVerifier, in the stand-in environment, with a
stand-in `copilot` and a stand-in senior task.

The task, under lanes/senior/ in the test's directory, has senior's file
layout and the names in run_judge.py the judge calls. The stand-in `copilot`
answers each session from a scenario the test writes, and records every
call. The agent trial and the verifier's sandbox are laid out as in
test_replay.py: the patch in the sandbox's artifacts directory and in the
trial's agent/ on the host. Harbor's own verifier, which runs test.sh, is
stood in for by one that records what test.sh would find.
"""

import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from harbor.cli.utils import parse_kwargs
from harbor.models.task.task import Task
from harbor.models.trial.config import VerifierConfig
from harbor.models.trial.paths import TrialPaths
from harbor.models.verifier.result import VerifierResult
from harbor.utils.env import resolve_env_vars
from harbor.verifier.factory import VerifierFactory
from harbor.verifier.verifier import Verifier

import suss.judge
import suss.replay
from suss.judge import CreditLedger, JudgePaths, JudgeVerifier, extract_json
from suss.replay import ReplayError, ReplayVerifier
from test_replay import edit_line, git, go, patch_of

HERE = Path(__file__).resolve().parent
FAKE_TASK = HERE / "fake_senior_task"
TASK = "fake-senior-task"
TRIAL = "fake-senior-task__AbCdEfG"
TOKEN = "github_pat_stand_in_judge_token"
TEST_FILE = "tests/test_lines.py"

RUBRIC_ANSWER = {
    "criteria": [
        {"name": "fixes_line_three", "score": 1, "reason": "Line three says the fix."},
        {"name": "keeps_other_lines", "score": 0, "reason": "Line five changed too."},
    ]
}
PRACTICE = ("style_consistency", "pattern_adherence", "library_usage", "abstraction_level", "documentation_fit")
RELATIVE = ("minimality", "approach_quality", "hygiene", "fluency", "craftsmanship")
TASTE_ANSWER = {
    "practice_alignment": {
        name: {"score": score, "rationale": "fits"} for name, score in zip(PRACTICE, (5, 4, 4, 3, 5))
    },
    "relative_taste": {
        name: {"score": score, "rationale": "fine"} for name, score in zip(RELATIVE, (2, 3, 3, 4, 2))
    },
}
CLASSIFY_ANSWER = {
    "files": [
        {"path": "lines.txt", "category": "behavioral", "rationale": "source"},
        {"path": TEST_FILE, "category": "test", "rationale": "a test"},
    ]
}


def whole_fix(repo: Path) -> None:
    """The trial's change: the fix on line three, a stray edit on line five, and a new test."""
    edit_line(3, "the fix")(repo)
    edit_line(5, "stray")(repo)
    (repo / "tests").mkdir()
    (repo / TEST_FILE).write_text("def test_line_three():\n    assert True\n")


@pytest.fixture
def workspace(tmp_path) -> Path:
    """The task's repository at its base, with one file of twenty numbered lines."""
    repo = tmp_path / "sandbox" / "gleam"
    repo.mkdir(parents=True)
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.com")
    (repo / "lines.txt").write_text("".join(f"line {n}\n" for n in range(1, 21)))
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "base")
    return repo


@pytest.fixture
def found(monkeypatch, workspace):
    """Stand find_workspace in for the sandbox's starting directory."""

    async def find_workspace(*args, **kwargs):
        return str(workspace)

    monkeypatch.setattr(suss.replay, "find_workspace", find_workspace)


@pytest.fixture
def box(tmp_path, monkeypatch, found) -> Path:
    """
    The test's directory, which Harbor would start in: the task, the .env,
    the stand-in copilot with its scenario, and a stand-in for Harbor's install
    of Copilot CLI, which records each call and raises where install-fails holds
    a message.
    """
    monkeypatch.chdir(tmp_path)
    shutil.copytree(FAKE_TASK, tmp_path / "lanes" / "senior" / TASK)
    (tmp_path / ".env").write_text(f"COPILOT_GITHUB_TOKEN={TOKEN}\nOTHER_KEY=not-for-copilot\n")

    async def install_copilot(environment, copilot_version, logs_dir):
        with open(tmp_path / "installs.jsonl", "a") as f:
            f.write(json.dumps({"version": copilot_version, "logs_dir": str(logs_dir)}) + "\n")
        failure = tmp_path / "install-fails"
        if failure.exists():
            raise RuntimeError(failure.read_text())

    monkeypatch.setattr(suss.judge, "install_copilot", install_copilot)
    scenario = tmp_path / "copilot"
    scenario.mkdir()
    (scenario / "version").write_text("1.0.105")
    shutil.copy(HERE / "fake_copilot.py", scenario / "fake_copilot.py")
    wrapper = scenario / "copilot"
    wrapper.write_text(
        f'#!/bin/sh\nexec {sys.executable} {scenario / "fake_copilot.py"} {scenario} "$@"\n'
    )
    wrapper.chmod(0o755)
    write_scenario(tmp_path)
    return tmp_path


def write_scenario(box: Path, **kinds) -> None:
    """
    What the stand-in copilot does in each kind of session. A kind left out
    answers validly, and its `retry`, the scenario of a resumed call, answers
    validly for 2 credits.
    """
    defaults = {
        "classify": {"answer": json.dumps(CLASSIFY_ANSWER), "nano": 1_000_000_000},
        "rubric": {"answer": json.dumps(RUBRIC_ANSWER), "nano": 8_000_000_000},
        "taste": {"answer": json.dumps(TASTE_ANSWER), "nano": 20_000_000_000},
    }
    for kind, scenario in defaults.items():
        scenario["retry"] = {"answer": scenario["answer"], "nano": 2_000_000_000}
    for kind, changes in kinds.items():
        retry = {**defaults[kind]["retry"], **changes.get("retry", {})}
        defaults[kind] = {**defaults[kind], **changes, "retry": retry}
    (box / "copilot" / "scenario.json").write_text(json.dumps(defaults))


def calls(box: Path) -> list[dict]:
    path = box / "copilot" / "calls.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def a_patch(workspace: Path, change=whole_fix) -> bytes | None:
    """The patch the agent trial left, or None for a trial that left none."""
    return patch_of(workspace, change) if change else None


def judge_verifier(
    box: Path, environment, patch: bytes | None, trial: str = TRIAL, job: str = "agent-job",
    **kwargs,
) -> JudgeVerifier:
    """
    A JudgeVerifier for the agent trial `trial` of `job`, built as Harbor
    builds a separate verifier, with `patch` in the sandbox's artifacts and
    the trial's agent/ where it is not None.
    """
    sandbox = box / "sandbox"
    paths = TrialPaths(box / job / trial)
    paths.mkdir()
    artifacts = sandbox / "logs" / trial / "artifacts"
    if patch is not None:
        artifacts.mkdir(parents=True, exist_ok=True)
        (artifacts / "model.patch").write_bytes(patch)
        (paths.agent_dir / "model.patch").write_bytes(patch)
    kwargs.setdefault("credit_budget", 1000)
    return VerifierFactory.create_verifier_from_config(
        VerifierConfig(import_path="suss.judge:JudgeVerifier"),
        task=Task(box / "lanes" / "senior" / TASK),
        trial_paths=paths,
        environment=environment,
        skip_tests_upload=True,
        artifacts=str(artifacts),
        paths=JudgePaths(
            root=str(sandbox / "opt" / trial),
            logs=str(sandbox / "logs" / trial / "verifier" / "judge"),
            copilot=str(box / "copilot" / "copilot"),
        ),
        **kwargs,
    )


@pytest.fixture
def tested(monkeypatch, workspace) -> list[dict]:
    """
    Stand in for Harbor's own verifier, which runs test.sh: each run records
    what test.sh would read of the workspace, and the environment Harbor would
    give it, then rewards 1.
    """
    runs: list[dict] = []

    async def run_test_sh(self) -> VerifierResult:
        merged = {**self.task.config.verifier.env, **(self.verifier_env or {}), **self.override_env}
        result = await self.environment.exec("env", env=resolve_env_vars(merged) or None)
        runs.append({"workspace": verifier_input(workspace), "env": result.stdout or ""})
        return VerifierResult(rewards={"reward": 1.0})

    monkeypatch.setattr(Verifier, "verify", run_test_sh)
    return runs


def verify(verifier) -> VerifierResult:
    return go(verifier.verify())


def judge(verifier: JudgeVerifier) -> dict:
    verify(verifier)
    return recorded(verifier)


def recorded(verifier) -> dict:
    return json.loads((verifier.trial_paths.verifier_dir / "judge.json").read_text())


def ledger(verifier) -> Path:
    return verifier.trial_paths.trial_dir.parent / "judge-credits.jsonl"


def verifier_input(workspace: Path) -> dict:
    """Everything of the workspace a verifier could read: its files, index, HEAD and status."""
    files = {
        str(path.relative_to(workspace)): path.read_bytes()
        for path in sorted(workspace.rglob("*"))
        if path.is_file() and ".git" not in path.relative_to(workspace).parts
    }
    return {
        "files": files,
        "index": git(workspace, "ls-files", "-s"),
        "head": git(workspace, "rev-parse", "HEAD"),
        "status": git(workspace, "status", "--porcelain=v1", "-uall", "--ignored"),
    }


def replayed_input(box: Path, workspace: Path, patch: bytes, environment, tested) -> dict:
    """What test.sh would read after ReplayVerifier graded the same patch, with the workspace reset after."""
    paths = TrialPaths(box / "replay-job" / TRIAL)
    paths.mkdir()
    artifacts = box / "sandbox" / "logs" / "replay" / "artifacts"
    artifacts.mkdir(parents=True)
    (artifacts / "model.patch").write_bytes(patch)
    verifier = VerifierFactory.create_verifier_from_config(
        VerifierConfig(import_path="suss.replay:ReplayVerifier"),
        task=Task(box / "lanes" / "senior" / TASK),
        trial_paths=paths,
        environment=environment,
        skip_tests_upload=True,
        artifacts=str(artifacts),
    )
    verify(verifier)
    seen = tested.pop()["workspace"]
    git(workspace, "reset", "-q", "--hard", "HEAD")
    git(workspace, "clean", "-qfdx")
    return seen


# Loading


def test_harbor_loads_it_with_gemini_4_argon_at_high_effort_by_default(tmp_path, local_environment):
    paths = TrialPaths(tmp_path / "agent-job" / TRIAL)
    paths.mkdir()
    config = VerifierConfig(
        import_path="suss.judge:JudgeVerifier", kwargs=parse_kwargs(["credit_budget=80"])
    )

    verifier = VerifierFactory.create_verifier_from_config(
        config, task=Task(FAKE_TASK), trial_paths=paths, environment=local_environment,
        skip_tests_upload=True,
    )

    assert isinstance(verifier, JudgeVerifier)
    assert (verifier.model, verifier.effort, verifier.classifier_model) == (
        "gemini-4-argon",
        "high",
        "claude-haiku-4.5",
    )
    assert verifier.credit_budget == 80
    assert verifier.judge_seconds == 1800


def test_a_credit_budget_that_is_not_a_number_is_an_error(box, local_environment):
    with pytest.raises(TypeError, match="credit_budget"):
        judge_verifier(box, local_environment, None, credit_budget="lots")


# What the judge sees


def test_judges_the_whole_patch_with_its_test_files_then_puts_them_back_for_test_sh(
    box, workspace, local_environment, tested
):
    verifier = judge_verifier(box, local_environment, a_patch(workspace))

    record = judge(verifier)

    judged = [call for call in calls(box) if call["kind"] in ("rubric", "taste")]
    assert [call["kind"] for call in judged] == ["rubric", "taste"]
    rubric = judged[0]
    assert f"?? {TEST_FILE}" in rubric["status"].splitlines(), (
        "the rubric judge ran on a workspace without the trial's test file"
    )
    assert f"+++ b/{TEST_FILE}" in rubric["prompt"], "the rubric prompt lacks the trial's test file"
    assert TEST_FILE in record["judged_files"], "the judge was given the patch with its test files reset"
    assert TEST_FILE not in tested[0]["workspace"]["files"], "test.sh ran with the trial's test file"
    replayed = json.loads((verifier.trial_paths.verifier_dir / "replay.json").read_text())
    assert replayed["tests_reset"] == {"restored": [], "removed": [TEST_FILE]}


def test_builds_each_prompt_with_the_tasks_own_code_and_says_how_to_answer(
    box, workspace, local_environment, tested
):
    judge(judge_verifier(box, local_environment, a_patch(workspace)))

    by_kind = {call["kind"]: call["prompt"] for call in calls(box)}
    assert by_kind["rubric"].startswith("You are the stand-in rubric judge. Call submit_scores.")
    assert "Use exactly these criterion names: ['fixes_line_three', 'keeps_other_lines']" in by_kind["rubric"]
    assert by_kind["taste"].startswith("You are the stand-in taste judge.")
    # The classifier dropped the test file, so the taste judge sees the source alone.
    assert TEST_FILE not in by_kind["taste"]
    assert "+the fix" in by_kind["taste"]
    for prompt in by_kind.values():
        assert "only the JSON object you would have passed" in prompt
        assert '"$defs"' in prompt or '"properties"' in prompt


def test_each_session_reads_and_cannot_write(box, workspace, local_environment, tested):
    judge(judge_verifier(box, local_environment, a_patch(workspace)))

    tools = {call["kind"]: call["flags"]["--available-tools"] for call in calls(box)}
    assert tools == {"classify": ["view"], "rubric": ["view"], "taste": ["view", "glob", "grep", "rg"]}
    for call in calls(box):
        assert call["flags"]["--deny-tool"] == ["shell", "write"]
        for flag in ("--no-custom-instructions", "--no-ask-user", "--no-auto-update",
                     "--disable-builtin-mcps", "--disable-client-telemetry"):
            assert flag in call["argv"], flag
        assert call["cwd"] == str(workspace)
        assert call["auto_update"] == "false"


# What the trial records


def test_records_the_scores_in_the_shape_of_seniors_reward_json(box, workspace, local_environment, tested):
    record = judge(judge_verifier(box, local_environment, a_patch(workspace)))

    assert (record["rubric_status"], record["taste_status"]) == ("ok", "ok")
    assert record["scores"] == {
        "rubric_score": 0.5,
        "rubric_f2p_score": 1.0,
        "rubric_p2p_score": 0.0,
        # The stand-in counts added lines: 2 in the filtered patch, 1 in the oracle's.
        "taste_patch_bloat": 2.0,
        "taste_practice_alignment": 4.0,
        "taste_relative_taste": 3.0,
        "rubric_judge_ok": 1,
        "taste_judge_ok": 1,
    }
    assert record["rubric"] == {"fixes_line_three": 1.0, "keeps_other_lines": 0.0}
    assert record["taste"]["pa_abstraction_level"] == 3
    assert record["taste"]["rt_craftsmanship"] == 2


def test_records_the_client_the_model_and_effort_asked_for_and_reported_and_the_installed_version(
    box, workspace, local_environment, tested
):
    # Installed is not what was asked for, as an auto-update would leave it.
    (box / "copilot" / "version").write_text("1.0.106")

    record = judge(judge_verifier(box, local_environment, a_patch(workspace), copilot_version="1.0.105"))

    assert record["judge"] == {
        "client": "copilot-cli",
        "version": "1.0.106",
        "requested_version": "1.0.105",
        "model": "gemini-4-argon",
        "effort": "high",
        "classifier_model": "claude-haiku-4.5",
    }
    rubric = session_named(record, "rubric")
    assert rubric["reported_models"] == ["gemini-4-argon"]
    assert rubric["reported_efforts"] == ["high"]
    classify = session_named(record, "classify-agent")
    assert (classify["model"], classify["effort"]) == ("claude-haiku-4.5", None)
    assert "--reasoning-effort" not in next(c for c in calls(box) if c["kind"] == "classify")["argv"]


@pytest.mark.parametrize("effort", ["high", ""], ids=["with-effort", "without-effort"])
def test_judges_with_a_model_of_another_family_as_with_the_default(
    box, workspace, local_environment, tested, effort
):
    record = judge(
        judge_verifier(box, local_environment, a_patch(workspace), model="gpt-6.1-sol", effort=effort)
    )

    assert (record["rubric_status"], record["taste_status"]) == ("ok", "ok")
    assert (record["judge"]["model"], record["judge"]["effort"]) == ("gpt-6.1-sol", effort or None)
    judged = [call for call in calls(box) if call["kind"] in ("rubric", "taste")]
    for call in judged:
        assert call["flags"]["--model"] == ["gpt-6.1-sol"]
        assert call["flags"].get("--reasoning-effort") == ([effort] if effort else None)


def test_records_each_sessions_credits_and_their_total(box, workspace, local_environment, tested):
    write_scenario(box, classify={"nano": 1_250_000_000})

    record = judge(judge_verifier(box, local_environment, a_patch(workspace)))

    credits = {session["name"]: session["credits"] for session in record["sessions"]}
    assert credits == {"classify-agent": 1.25, "classify-oracle": 1.25, "rubric": 8.0, "taste": 20.0}
    assert record["credits"] == 30.5


def test_keeps_the_judges_logs_in_the_verifiers_logs(box, workspace, local_environment, tested):
    verifier = judge_verifier(box, local_environment, a_patch(workspace))

    judge(verifier)

    logs = Path(verifier.paths.logs)
    assert logs.parts[-2:] == ("verifier", "judge")
    assert (logs / "rubric" / "out.jsonl").is_file()
    assert JudgePaths().logs == "/logs/verifier/judge"


# Answers


@pytest.mark.parametrize(
    "text, how",
    [
        ('{"a": 1}', "clean"),
        ('```json\n{"a": 1}\n```', "fenced"),
        ('```\n{"a": 1}\n```', "fenced"),
        ('Here are my scores:\n\n{"a": 1}\n\nThat is all.', "embedded"),
        ('I weighed {style} first. {"a": 1}', "embedded"),
    ],
)
def test_reads_the_json_object_from_a_reply(text, how):
    assert extract_json(text) == ({"a": 1}, how)


@pytest.mark.parametrize("text", ["", "I cannot score this.", "[1, 2]", '{"a": 1'])
def test_a_reply_with_no_json_object_gives_none(text):
    assert extract_json(text) == (None, "none")


def test_reads_an_answer_in_a_code_fence_or_with_prose_around_it(box, workspace, local_environment, tested):
    write_scenario(
        box,
        rubric={"answer": f"My scores:\n\n{json.dumps(RUBRIC_ANSWER)}\n"},
        taste={"answer": f"```json\n{json.dumps(TASTE_ANSWER)}\n```"},
    )

    record = judge(judge_verifier(box, local_environment, a_patch(workspace)))

    assert (record["rubric_status"], record["taste_status"]) == ("ok", "ok")
    assert session_named(record, "rubric")["json"] == "embedded"
    assert record["scores"]["rubric_score"] == 0.5


def without(answer: dict, *path: str) -> dict:
    copy = json.loads(json.dumps(answer))
    target = copy
    for key in path[:-1]:
        target = target[key]
    del target[path[-1]]
    return copy


@pytest.mark.parametrize(
    "kind, answer, status",
    [
        ("taste", "I would score this a 4 overall.", "failed:no_json"),
        ("taste", json.dumps(without(TASTE_ANSWER, "relative_taste")), "failed:invalid_answer"),
        ("taste", json.dumps(without(TASTE_ANSWER, "practice_alignment", "library_usage")), "failed:invalid_answer"),
        ("rubric", "", "failed:no_json"),
        ("rubric", json.dumps({"criteria": RUBRIC_ANSWER["criteria"][:1]}), "failed:invalid_answer"),
        ("rubric", json.dumps({"criteria": [*RUBRIC_ANSWER["criteria"], {"name": "made_up", "score": 1, "reason": "x"}]}), "failed:invalid_answer"),
        ("rubric", json.dumps({"criteria": [{"name": "fixes_line_three", "score": 3, "reason": "x"}]}), "failed:invalid_answer"),
    ],
    ids=["taste-prose", "taste-missing-relative-taste", "taste-missing-a-dimension", "rubric-empty",
         "rubric-missing-a-criterion", "rubric-unknown-criterion", "rubric-out-of-range"],
)
def test_an_answer_that_is_missing_or_invalid_twice_fails_and_records_no_score(
    box, workspace, local_environment, tested, kind, answer, status
):
    write_scenario(box, **{kind: {"answer": answer, "retry": {"answer": answer}}})

    record = judge(judge_verifier(box, local_environment, a_patch(workspace)))

    assert [call["resumed"] for call in calls(box) if call["kind"] == kind] == [False, True]
    assert record[f"{kind}_status"] == status
    assert record[f"{kind}_error"]
    assert record["scores"][f"{kind}_judge_ok"] == 0
    scored = {"rubric": ("rubric_score", "rubric_f2p_score", "rubric_p2p_score"),
              "taste": ("taste_practice_alignment", "taste_relative_taste")}[kind]
    assert not set(scored) & set(record["scores"]), "a failed judge recorded a score"
    assert kind not in record or not record[kind]


@pytest.mark.parametrize(
    "scenario, status",
    [
        ({"model": "gpt-5-mini"}, "failed:wrong_model"),
        ({"effort": "low"}, "failed:wrong_effort"),
        ({"exit": 1}, "failed:exit_1"),
    ],
)
def test_a_session_on_the_wrong_model_or_effort_or_that_fails_records_no_score(
    box, workspace, local_environment, tested, scenario, status
):
    write_scenario(box, rubric=scenario)

    record = judge(judge_verifier(box, local_environment, a_patch(workspace)))

    assert record["rubric_status"] == status
    assert "rubric_score" not in record["scores"]
    assert record["taste_status"] == "ok"


def test_a_task_with_no_rubric_criteria_skips_the_rubric_judge(box, workspace, local_environment, tested):
    rubric = box / "lanes" / "senior" / TASK / "tests" / "judge" / "rubric.json"
    rubric.write_text(json.dumps({"files": [], "fail_to_pass": [], "pass_to_pass": []}))

    record = judge(judge_verifier(box, local_environment, a_patch(workspace)))

    assert record["rubric_status"] == "skipped:no_criteria"
    assert "rubric" not in [call["kind"] for call in calls(box)]
    assert record["taste_status"] == "ok"
    assert record["scores"]["rubric_judge_ok"] == 0


def test_an_empty_patch_skips_both_judges_and_is_still_graded(box, workspace, local_environment, tested):
    record = judge(judge_verifier(box, local_environment, a_patch(workspace, lambda repo: None)))

    assert (record["rubric_status"], record["taste_status"]) == ("skipped:no_patch", "skipped:no_patch")
    assert calls(box) == []
    assert len(tested) == 1


def test_a_trial_that_left_no_patch_skips_both_judges_and_runs_no_test(
    box, workspace, local_environment, tested
):
    verifier = judge_verifier(box, local_environment, None)

    with pytest.raises(ReplayError, match="no patch"):
        verify(verifier)

    record = recorded(verifier)
    assert (record["rubric_status"], record["taste_status"]) == ("skipped:no_patch", "skipped:no_patch")
    assert tested == []


def test_an_install_that_fails_fails_both_judges_and_the_trial_is_still_graded(
    box, workspace, local_environment, tested
):
    (box / "install-fails").write_text("Command failed (exit 3): curl -fsSL https://gh.io/copilot-install")

    record = judge(judge_verifier(box, local_environment, a_patch(workspace)))

    assert (record["rubric_status"], record["taste_status"]) == ("failed:install", "failed:install")
    assert "3" in record["rubric_error"]
    assert tested[0]["workspace"]["files"]["lines.txt"].splitlines()[2] == b"the fix"


def test_a_dotenv_with_no_token_skips_both_judges(box, workspace, local_environment, tested):
    (box / ".env").write_text("OTHER_KEY=x\n")

    record = judge(judge_verifier(box, local_environment, a_patch(workspace)))

    assert (record["rubric_status"], record["taste_status"]) == ("skipped:no_token", "skipped:no_token")
    assert calls(box) == []


# The test reward


@pytest.mark.parametrize(
    "scenario, settings",
    [
        ({}, {}),
        ({"taste": {"exit": 2}, "rubric": {"answer": "no"}}, {}),
        ({"taste": {"sleep": 30}}, {"session_seconds": 1}),
        ({"rubric": {"sleep": 30}}, {"judge_seconds": 2}),
    ],
    ids=["judged", "failing", "session-timing-out", "judge-timing-out"],
)
def test_test_sh_sees_what_it_would_after_replayverifier_and_gives_its_reward_whatever_the_judge_does(
    box, workspace, local_environment, tested, scenario, settings
):
    patch = a_patch(workspace)
    expected = replayed_input(box, workspace, patch, local_environment, tested)
    write_scenario(box, **scenario)

    result = verify(judge_verifier(box, local_environment, patch, **settings))

    assert tested[-1]["workspace"] == expected, "the judge changed what test.sh grades"
    assert result.rewards == {"reward": 1.0}, "the judge changed the test reward"
    record = json.loads((box / "agent-job" / TRIAL / "verifier" / "judge.json").read_text())
    if scenario:
        assert "failed:" in record["rubric_status"] + record["taste_status"]


def test_a_judge_that_raises_fails_both_judges_and_test_sh_still_gives_the_reward(
    box, workspace, local_environment, tested
):
    patch = a_patch(workspace)
    expected = replayed_input(box, workspace, patch, local_environment, tested)
    run_judge = box / "lanes" / "senior" / TASK / "tests" / "run_judge.py"
    run_judge.write_text("raise SystemExit(3)\n" + run_judge.read_text())

    verifier = judge_verifier(box, local_environment, patch)

    result = verify(verifier)

    assert result.rewards == {"reward": 1.0}, "a judge that raised changed the test reward"
    assert tested[-1]["workspace"] == expected
    record = recorded(verifier)
    assert (record["rubric_status"], record["taste_status"]) == ("failed:error", "failed:error")


def test_a_session_timing_out_fails_its_judge_alone(box, workspace, local_environment, tested):
    write_scenario(box, taste={"sleep": 30})

    record = judge(judge_verifier(box, local_environment, a_patch(workspace), session_seconds=1))

    assert (record["rubric_status"], record["taste_status"]) == ("ok", "failed:timeout")


def test_the_judge_timing_out_fails_every_judge_it_had_not_finished(box, workspace, local_environment, tested):
    write_scenario(box, taste={"sleep": 60})

    record = judge(judge_verifier(box, local_environment, a_patch(workspace), judge_seconds=10))

    assert (record["rubric_status"], record["taste_status"]) == ("ok", "failed:timeout")
    assert record["scores"]["taste_judge_ok"] == 0


def test_a_task_whose_verifier_timeout_cannot_hold_the_judge_is_refused_before_anything_runs(
    box, workspace, local_environment, tested
):
    toml = box / "lanes" / "senior" / TASK / "task.toml"
    toml.write_text(toml.read_text().replace("timeout_sec = 2700.0", "timeout_sec = 600.0"))
    verifier = judge_verifier(box, local_environment, a_patch(workspace))

    with pytest.raises(suss.judge.JudgeError, match="600 seconds"):
        verify(verifier)

    assert calls(box) == [], "a judge session ran that Harbor's verifier timeout would cut off"
    assert tested == []
    assert not (verifier.trial_paths.verifier_dir / "judge.json").exists()


def test_a_judge_harbors_verifier_timeout_cancels_is_recorded_unjudged_and_runs_no_test(
    box, workspace, local_environment, tested
):
    patch = a_patch(workspace)
    expected = replayed_input(box, workspace, patch, local_environment, tested)
    write_scenario(box, taste={"sleep": 60})
    verifier = judge_verifier(box, local_environment, patch)

    async def cancel_during_taste():
        # As Harbor's asyncio.wait_for cancels verify() at the verifier timeout.
        run = asyncio.create_task(verifier.verify())
        while "taste" not in [call["kind"] for call in calls(box)]:
            await asyncio.sleep(0.1)
        run.cancel()
        with pytest.raises(asyncio.CancelledError):
            await run

    go(cancel_during_taste())

    assert verifier_input(workspace) == expected
    assert tested == []
    record = recorded(verifier)
    assert (record["rubric_status"], record["taste_status"]) == ("ok", "failed:cancelled")
    assert record["scores"]["taste_judge_ok"] == 0, "a cancelled taste judge reads as judged"
    assert session_named(record, "taste")["status"] == "failed:cancelled"


@pytest.mark.parametrize("path", ["lines.txt", "notes.txt"], ids=["edits-a-file", "adds-a-file"])
def test_a_session_that_changes_the_repository_fails_and_the_change_is_undone_before_test_sh(
    box, workspace, local_environment, tested, path
):
    patch = a_patch(workspace)
    expected = replayed_input(box, workspace, patch, local_environment, tested)
    write_scenario(box, rubric={"write": path})

    record = judge(judge_verifier(box, local_environment, patch))

    assert record["rubric_status"] == "failed:repo_changed"
    rubric = session_named(record, "rubric")
    assert rubric["repo_unchanged"] is False
    assert rubric["restored"] is True
    assert "rubric_score" not in record["scores"]
    assert tested[-1]["workspace"] == expected


# The token


def test_the_token_reaches_copilot_alone_and_no_record_log_or_test_sh(
    box, workspace, local_environment, tested
):
    verifier = judge_verifier(box, local_environment, a_patch(workspace))

    record = judge(verifier)

    sha = hashlib.sha256(TOKEN.encode()).hexdigest()
    for call in calls(box):
        assert call["token_sha256"] == sha
        assert not call["gh_token"]
        assert TOKEN not in " ".join(call["argv"])
        assert call["argv"][call["argv"].index("--secret-env-vars") + 1] == "COPILOT_GITHUB_TOKEN"
    assert record["rubric_status"] == "ok"
    assert TOKEN not in tested[0]["env"], "test.sh's environment holds the judge's token"
    written = [path for path in (box / "sandbox").rglob("*") if path.is_file()]
    written += [path for path in verifier.trial_paths.trial_dir.rglob("*") if path.is_file()]
    assert [path for path in written if TOKEN.encode() in path.read_bytes()] == []
    # A later command, as test.sh's, starts with no token.
    later = go(local_environment.exec("printenv COPILOT_GITHUB_TOKEN"))
    assert later.return_code != 0


def test_a_log_holding_the_token_is_removed(box, workspace, local_environment, tested):
    write_scenario(box, rubric={"leak": True})
    verifier = judge_verifier(box, local_environment, a_patch(workspace))

    record = judge(verifier)

    rubric = session_named(record, "rubric")
    assert len(rubric["removed_for_token"]) == 1
    assert not Path(rubric["removed_for_token"][0]).exists()
    written = [path for path in (box / "sandbox").rglob("*") if path.is_file()]
    assert [path for path in written if TOKEN.encode() in path.read_bytes()] == []


# The credit budget


def test_a_job_starts_no_session_its_budget_cannot_hold(box, workspace, local_environment, tested):
    # The classifiers take 2 and the rubric judge 8, so the taste judge's
    # reservation of 60 would pass a budget of 40.
    verifier = judge_verifier(box, local_environment, a_patch(workspace), credit_budget=40)

    record = judge(verifier)

    assert record["rubric_status"] == "ok"
    assert record["taste_status"] == "skipped:budget"
    assert "taste" not in [call["kind"] for call in calls(box)]
    assert CreditLedger(ledger(verifier), 40).spent() == 10.0


def test_every_trial_of_a_job_charges_one_ledger_on_the_host_and_another_jobs_charges_its_own(
    box, workspace, local_environment, tested
):
    patch = a_patch(workspace)
    first = judge_verifier(box, local_environment, patch, trial="fake-senior-task__One1111")
    judge(first)
    git(workspace, "reset", "-q", "--hard", "HEAD")
    git(workspace, "clean", "-qfdx")
    second = judge_verifier(
        box, local_environment, patch, trial="fake-senior-task__Two2222", credit_budget=40
    )

    record = judge(second)

    assert ledger(first) == ledger(second) == box / "agent-job" / "judge-credits.jsonl"
    # The first trial spent 30, so the second has 10 left: the classifiers fit, and neither judge does.
    assert [s["status"] for s in record["sessions"]] == ["ok", "ok", "skipped:budget", "skipped:budget"]
    assert (record["rubric_status"], record["taste_status"]) == ("skipped:budget", "skipped:budget")
    labels = {json.loads(line).get("label", "").split("/")[0] for line in ledger(first).read_text().splitlines()}
    assert labels >= {"fake-senior-task__One1111", "fake-senior-task__Two2222"}
    git(workspace, "reset", "-q", "--hard", "HEAD")
    git(workspace, "clean", "-qfdx")
    other = judge_verifier(box, local_environment, patch, job="other-job", credit_budget=40)
    assert judge(other)["rubric_status"] == "ok", "another job's trials drew on this job's budget"


def test_concurrent_sessions_never_take_a_ledger_past_its_budget(tmp_path, monkeypatch):
    monkeypatch.setattr(CreditLedger, "POLL_SECONDS", 0.01)
    ledger = CreditLedger(tmp_path / "judge-credits.jsonl", 100)
    running = []
    peak = []

    async def session(n: int) -> bool:
        held = await ledger.reserve(f"trial-{n}", 30)
        if held is None:
            return False
        running.append(n)
        peak.append(len(running))
        await asyncio.sleep(0.05)
        running.remove(n)
        ledger.settle(held, 20)
        return True

    async def main():
        return await asyncio.gather(*(session(n) for n in range(8)))

    started = asyncio.run(main())

    # 3 reservations of 30 fit in 100 at once. Each session spends 20, so 4 run in all.
    assert max(peak) == 3
    assert sum(started) == 4
    assert ledger.spent() == 80


def test_a_reservation_whose_process_died_counts_as_spent_and_is_not_waited_for(tmp_path, monkeypatch):
    # Long enough that a reservation which waited would fail the test's timeout.
    monkeypatch.setattr(CreditLedger, "POLL_SECONDS", 60)
    path = tmp_path / "judge-credits.jsonl"
    dead = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"], capture_output=True, text=True)
    path.write_text(json.dumps({"reserve": "old", "label": "x", "credits": 90, "pid": int(dead.stdout)}) + "\n")
    ledger = CreditLedger(path, 100)

    async def both():
        small = await asyncio.wait_for(ledger.reserve("small", 5), 5)
        ledger.settle(small, 1)
        return small, await asyncio.wait_for(ledger.reserve("large", 50), 5)

    small, large = asyncio.run(both())

    assert small is not None
    assert large is None
    assert ledger.spent() == 91
    entries = [json.loads(line) for line in path.read_text().splitlines()]
    assert [e["pid"] for e in entries if e.get("reserve") == small] == [os.getpid()]


# Found in review of #198


def test_a_token_in_a_reply_or_an_error_never_reaches_the_record(box, workspace, local_environment, tested):
    leaky = json.loads(json.dumps(TASTE_ANSWER))
    leaky["practice_alignment"]["style_consistency"]["rationale"] = f"saw {TOKEN}"
    write_scenario(
        box,
        taste={"answer": json.dumps(leaky)},
        rubric={"answer": f"no scores, {TOKEN}", "retry": {"answer": f"still none, {TOKEN}"}},
    )
    verifier = judge_verifier(box, local_environment, a_patch(workspace))

    record = judge(verifier)

    assert (record["rubric_status"], record["taste_status"]) == ("failed:no_json", "ok")
    assert TOKEN not in (verifier.trial_paths.verifier_dir / "judge.json").read_text()
    assert "[redacted]" in record["rubric_error"]


@pytest.mark.parametrize(
    "command",
    [
        "echo 'x = 1' > new.py && git add new.py",
        "git add -A && git -c user.name=J -c user.email=j@example.com commit -qm judged",
        "git rm -q --cached lines.txt",
    ],
    ids=["stages-a-new-file", "commits-the-patch", "untracks-a-file"],
)
def test_a_session_that_stages_or_commits_is_put_back_whole(
    box, workspace, local_environment, tested, command
):
    patch = a_patch(workspace)
    expected = replayed_input(box, workspace, patch, local_environment, tested)
    write_scenario(box, rubric={"run": command})

    record = judge(judge_verifier(box, local_environment, patch))

    assert record["rubric_status"] == "failed:repo_changed"
    assert session_named(record, "rubric")["restored"] is True
    assert tested[-1]["workspace"] == expected


def test_a_workspace_that_cannot_be_put_back_fails_the_trial_and_runs_no_test(
    box, workspace, local_environment, tested, monkeypatch
):
    write_scenario(box, rubric={"write": "notes.txt"})
    verifier = judge_verifier(box, local_environment, a_patch(workspace))

    async def leaves_it(*args, **kwargs):
        pass

    monkeypatch.setattr(verifier, "_restore", leaves_it)

    with pytest.raises(suss.judge.JudgeError, match="put back"):
        verify(verifier)
    assert recorded(verifier)["rubric_status"] == "failed:repo_changed"
    assert tested == []


def test_a_restore_that_raises_fails_the_trial(box, workspace, local_environment, tested, monkeypatch):
    write_scenario(box, rubric={"write": "notes.txt"})
    verifier = judge_verifier(box, local_environment, a_patch(workspace))

    async def stuck(*args, **kwargs):
        raise RuntimeError("the reset stopped midway")

    monkeypatch.setattr(verifier, "_restore", stuck)

    with pytest.raises(suss.judge.JudgeError, match="put back"):
        verify(verifier)


def test_a_cancel_during_cleanup_still_puts_the_test_files_back_and_fails_the_trial(
    box, workspace, local_environment, tested
):
    patch = a_patch(workspace)
    expected = replayed_input(box, workspace, patch, local_environment, tested)
    verifier = judge_verifier(box, local_environment, patch)
    real_cleanup = verifier._cleanup
    entered = asyncio.Event()

    async def slow_cleanup(*args):
        entered.set()
        await asyncio.sleep(30)
        await real_cleanup(*args)

    verifier._cleanup = slow_cleanup

    async def cancel_during_cleanup():
        run = asyncio.create_task(verifier.verify())
        await entered.wait()
        run.cancel()
        with pytest.raises(suss.judge.JudgeError, match="put back"):
            await run

    go(cancel_during_cleanup())

    assert verifier_input(workspace) == expected
    assert (verifier.trial_paths.verifier_dir / "replay.json").exists()
    assert tested == []


def test_a_session_the_judge_stops_keeps_the_credits_it_reported(box, workspace, local_environment, tested):
    write_scenario(box, taste={"sleep": 60})
    verifier = judge_verifier(box, local_environment, a_patch(workspace), judge_seconds=10)

    record = judge(verifier)

    taste = session_named(record, "taste")
    assert taste["credits"] == 20.0
    assert record["credits"] == 30.0
    assert CreditLedger(ledger(verifier), 1000).spent() == 30.0


def test_a_dotenv_with_no_token_leaves_the_judges_files_alone(box, workspace, local_environment, tested):
    (box / ".env").write_text("OTHER_KEY=x\n")
    verifier = judge_verifier(box, local_environment, a_patch(workspace))

    judge(verifier)

    assert [json.loads(line)["version"] for line in (box / "installs.jsonl").read_text().splitlines()] == [
        "1.0.105"
    ]
    assert Path(verifier.paths.session).exists()


# Retrying an answer that fails the schema

# Argon's one answer that was not JSON quoted a word in a rationale without escaping it.
UNESCAPED_TASTE = json.dumps(TASTE_ANSWER).replace('"fits"', '"reads "events" as the queue"', 1)
ARGON_CHECKPOINT = {"checkpoint": {"vendor": "openai", "api_endpoint": "/chat/completions", "transport": "http"}}


def session_named(record: dict, name: str) -> dict:
    return next(session for session in record["sessions"] if session["name"] == name)


def test_an_answer_that_is_not_json_is_retried_by_resuming_the_same_session(
    box, workspace, local_environment, tested
):
    write_scenario(box, taste={"answer": UNESCAPED_TASTE})

    record = judge(judge_verifier(box, local_environment, a_patch(workspace)))

    first = next(call for call in calls(box) if call["kind"] == "taste")
    retry = next(call for call in calls(box) if call["prompt"].startswith("Your final reply cannot be scored"))
    assert retry["flags"].get("--resume") == first["flags"]["--session-id"], "the retry started a new session"
    assert "Expecting ',' delimiter" in retry["prompt"], "the retry does not say where the JSON broke"
    assert retry["copilot_home"] == first["copilot_home"]
    for flag in ("--model", "--reasoning-effort", "--available-tools", "--deny-tool"):
        assert retry["flags"][flag] == first["flags"][flag], flag
    assert record["taste_status"] == "ok"
    assert record["scores"]["taste_practice_alignment"] == 4.0


def test_the_retry_names_what_was_wrong_and_both_answers_are_recorded(
    box, workspace, local_environment, tested
):
    partial = json.dumps({"criteria": RUBRIC_ANSWER["criteria"][:1]})
    write_scenario(box, rubric={"answer": partial})

    record = judge(judge_verifier(box, local_environment, a_patch(workspace)))

    retried = [call for call in calls(box) if call["kind"] == "rubric"][1]
    assert "keeps_other_lines" in retried["prompt"], "the retry does not say which criterion was missing"
    first, retry = session_named(record, "rubric"), session_named(record, "rubric-retry")
    assert first["status"] == "failed:invalid_answer"
    assert "keeps_other_lines" in first["error"]
    assert first["reply"] == partial
    assert (retry["status"], retry["reply"]) == ("ok", json.dumps(RUBRIC_ANSWER))
    assert record["rubric_status"] == "ok"
    assert record["scores"]["rubric_score"] == 0.5


def test_the_retry_charges_the_jobs_ledger_what_it_spent_after_reserving(
    box, workspace, local_environment, tested
):
    write_scenario(box, taste={"answer": "no scores", "retry": {"nano": 3_500_000_000}})
    verifier = judge_verifier(box, local_environment, a_patch(workspace))

    record = judge(verifier)

    # Copilot CLI counts on across a resume: the retry reports 23.5, of which the first call spent 20.
    assert (session_named(record, "taste")["credits"], session_named(record, "taste-retry")["credits"]) == (20.0, 3.5)
    assert record["credits"] == 33.5
    entries = [json.loads(line) for line in ledger(verifier).read_text().splitlines()]
    reserved = next(i for i, e in enumerate(entries) if e.get("label", "").endswith("/taste-retry"))
    settled = next(i for i, e in enumerate(entries) if e.get("settle") == entries[reserved]["reserve"])
    assert reserved < settled
    assert entries[settled]["credits"] == 3.5
    assert CreditLedger(ledger(verifier), 1000).spent() == 33.5


def test_a_retry_the_budget_cannot_hold_is_skipped_and_the_first_failure_stands(
    box, workspace, local_environment, tested
):
    # The classifiers and the rubric judge spend 10 and the taste judge 55,
    # which leaves 5 of 70: less than a retry holds.
    write_scenario(box, taste={"answer": "no scores", "nano": 55_000_000_000})

    record = judge(judge_verifier(box, local_environment, a_patch(workspace), credit_budget=70))

    assert [call["resumed"] for call in calls(box) if call["kind"] == "taste"] == [False]
    assert session_named(record, "taste-retry")["status"] == "skipped:budget"
    assert record["taste_status"] == "failed:no_json"


@pytest.mark.parametrize(
    "scenario, settings",
    [
        ({}, {}),
        ({"model": "gpt-5-mini"}, {}),
        ({"effort": "low"}, {}),
        ({"exit": 1}, {}),
        ({"write": "notes.txt"}, {}),
        ({"sleep": 30}, {"session_seconds": 1}),
    ],
    ids=["valid", "wrong-model", "wrong-effort", "exits-1", "changes-the-repository", "times-out"],
)
def test_only_an_answer_that_fails_the_schema_is_retried(
    box, workspace, local_environment, tested, scenario, settings
):
    write_scenario(box, rubric=scenario)

    record = judge(judge_verifier(box, local_environment, a_patch(workspace), **settings))

    assert [call["resumed"] for call in calls(box) if call["kind"] == "rubric"] == [False]
    assert "rubric-retry" not in [session["name"] for session in record["sessions"]]


def test_a_retry_that_changes_the_repository_fails_and_the_change_is_undone(
    box, workspace, local_environment, tested
):
    patch = a_patch(workspace)
    expected = replayed_input(box, workspace, patch, local_environment, tested)
    write_scenario(box, rubric={"answer": "no scores", "retry": {"write": "notes.txt"}})

    record = judge(judge_verifier(box, local_environment, patch))

    retry = session_named(record, "rubric-retry")
    assert (retry["repo_unchanged"], retry["restored"]) == (False, True)
    assert record["rubric_status"] == "failed:repo_changed"
    assert "rubric_score" not in record["scores"]
    assert tested[-1]["workspace"] == expected


def test_reads_back_argons_effort_from_a_checkpoint_that_names_its_vendor_openai(
    box, workspace, local_environment, tested
):
    write_scenario(box, rubric=ARGON_CHECKPOINT, taste=ARGON_CHECKPOINT)

    record = judge(
        judge_verifier(box, local_environment, a_patch(workspace), model="gemini-4-argon", effort="high")
    )

    assert (record["rubric_status"], record["taste_status"]) == ("ok", "ok")
    for name in ("rubric", "taste"):
        session = session_named(record, name)
        assert (session["reported_models"], session["reported_efforts"]) == (["gemini-4-argon"], ["high"])
