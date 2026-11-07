"""
Judge trials through JudgeAgent, in the stand-in environment, with a stand-in
`copilot` and a stand-in senior task.

The task, under lanes/senior/ in the test's directory, has senior's file
layout and the names in run_judge.py the judge calls. The stand-in `copilot`
answers each session from a scenario the test writes, and records every
call. The source job, the regrade trial and the workspace are laid out as in
test_replay.py.
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
from harbor.agents.factory import AgentFactory
from harbor.cli.utils import parse_kwargs
from harbor.models.agent.context import AgentContext
from harbor.models.trial.config import AgentConfig

import suss.judge
import suss.replay
from suss.judge import CreditLedger, JudgeAgent, JudgePaths, extract_json
from suss.replay import ReplayAgent
from test_replay import edit_line, git, go, patch_of, trial

HERE = Path(__file__).resolve().parent
FAKE_TASK = HERE / "fake_senior_task"
FAKE_ADAPTER = HERE / "fake_adapter"
TASK = "fake-senior-task"
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
    monkeypatch.setattr(suss.judge, "find_workspace", find_workspace)


@pytest.fixture
def box(tmp_path, monkeypatch, found) -> Path:
    """
    The test's directory, which Harbor would start in: the task, the .env,
    a copy of the fake adapter, and the stand-in copilot with its scenario.
    """
    monkeypatch.chdir(tmp_path)
    shutil.copytree(FAKE_TASK, tmp_path / "lanes" / "senior" / TASK)
    (tmp_path / ".env").write_text(f"COPILOT_GITHUB_TOKEN={TOKEN}\nOTHER_KEY=not-for-copilot\n")
    shutil.copytree(FAKE_ADAPTER, tmp_path / "adapter")
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
    answers validly, at one credit.
    """
    defaults = {
        "classify": {"answer": json.dumps(CLASSIFY_ANSWER), "nano": 1_000_000_000},
        "rubric": {"answer": json.dumps(RUBRIC_ANSWER), "nano": 8_000_000_000},
        "taste": {"answer": json.dumps(TASTE_ANSWER), "nano": 20_000_000_000},
    }
    for kind, changes in kinds.items():
        defaults[kind] = {**defaults[kind], **changes}
    (box / "copilot" / "scenario.json").write_text(json.dumps(defaults))


def calls(box: Path) -> list[dict]:
    path = box / "copilot" / "calls.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def source_job(box: Path, workspace: Path, change=whole_fix) -> Path:
    job = box / "agent-job"
    trial(job, "fake-senior-task__AbCdEfG", TASK, patch_of(workspace, change) if change else None)
    # trial() names the task under lanes/rebench/, and the judge reads it from lanes/senior/.
    config = job / "fake-senior-task__AbCdEfG" / "config.json"
    config.write_text(json.dumps({"task": {"path": f"lanes/senior/{TASK}", "source": "senior"}}))
    return job


def regrade_dir(box: Path, name: str = "fake-senior-task__XyZ1234", job: str = "judge-job") -> Path:
    regrade = box / job / name
    regrade.mkdir(parents=True)
    config = {"task": {"path": f"lanes/senior/{TASK}", "source": "senior"}}
    (regrade / "config.json").write_text(json.dumps(config))
    return regrade


def judge_agent(box: Path, job: Path, regrade: Path | None = None, **kwargs) -> JudgeAgent:
    """A JudgeAgent for the regrade trial of the task, constructed as Harbor constructs it."""
    regrade = regrade or regrade_dir(box)
    sandbox = box / "sandbox"
    kwargs.setdefault("credit_budget", 1000)
    return JudgeAgent(
        source_job=str(job),
        adapter=str(box / "adapter"),
        paths=JudgePaths(
            root=str(sandbox / "opt" / regrade.name),
            logs=str(sandbox / "logs" / regrade.name / "judge"),
            copilot=str(box / "copilot" / "copilot"),
        ),
        patch_target=str(sandbox / "tmp" / f"{regrade.name}.patch"),
        logs_dir=regrade / "agent",
        **kwargs,
    )


def judge(agent: JudgeAgent, environment) -> dict:
    go(agent.setup(environment))
    go(agent.run("an instruction", environment, AgentContext()))
    return json.loads((agent.logs_dir / "judge.json").read_text())


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


def regraded_input(box: Path, workspace: Path, job: Path, environment) -> dict:
    """What a verifier would read after ReplayAgent regraded the same trial, with the workspace reset after."""
    regrade = regrade_dir(box, "fake-senior-task__RePlay1", job="replay-job")
    agent = ReplayAgent(
        source_job=str(job),
        patch_target=str(box / "sandbox" / "tmp" / "replay.patch"),
        logs_dir=regrade / "agent",
    )
    go(agent.setup(environment))
    go(agent.run("an instruction", environment, AgentContext()))
    seen = verifier_input(workspace)
    git(workspace, "reset", "-q", "--hard", "HEAD")
    git(workspace, "clean", "-qfdx")
    return seen


# Loading


def test_harbor_loads_it_with_gpt_6_1_sol_at_high_effort_by_default(tmp_path):
    job = tmp_path / "agent-job"
    job.mkdir()
    config = AgentConfig(
        name="suss.judge:JudgeAgent",
        model_name="judge",
        kwargs=parse_kwargs([f"source_job={job}", "credit_budget=80"]),
    )

    agent = AgentFactory.create_agent_from_config(config, logs_dir=tmp_path / "agent")

    assert isinstance(agent, JudgeAgent)
    assert (agent.model, agent.effort, agent.classifier_model) == (
        "gpt-6.1-sol",
        "high",
        "claude-haiku-4.5",
    )
    assert agent.credit_budget == 80


def test_a_credit_budget_that_is_not_a_number_is_an_error(tmp_path):
    job = tmp_path / "agent-job"
    job.mkdir()

    with pytest.raises(TypeError, match="credit_budget"):
        JudgeAgent(source_job=str(job), credit_budget="lots", logs_dir=tmp_path / "agent")


# What the judge sees


def test_judges_the_whole_patch_with_its_test_files_then_puts_them_back(
    box, workspace, local_environment
):
    job = source_job(box, workspace)

    agent = judge_agent(box, job)

    record = judge(agent, local_environment)

    judged = [call for call in calls(box) if call["kind"] in ("rubric", "taste")]
    assert [call["kind"] for call in judged] == ["rubric", "taste"]
    rubric = judged[0]
    assert f"?? {TEST_FILE}" in rubric["status"].splitlines(), (
        "the rubric judge ran on a workspace without the trial's test file"
    )
    assert f"+++ b/{TEST_FILE}" in rubric["prompt"], "the rubric prompt lacks the trial's test file"
    assert TEST_FILE in record["judged_files"]
    assert not (workspace / TEST_FILE).exists(), "the test file was not put back for the verifier"
    replayed = json.loads((agent.logs_dir / "replay.json").read_text())
    assert replayed["tests_reset"] == {"restored": [], "removed": [TEST_FILE]}


def test_builds_each_prompt_with_the_tasks_own_code_and_says_how_to_answer(
    box, workspace, local_environment
):
    job = source_job(box, workspace)

    judge(judge_agent(box, job), local_environment)

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


def test_each_session_reads_and_cannot_write(box, workspace, local_environment):
    job = source_job(box, workspace)

    judge(judge_agent(box, job), local_environment)

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


def test_records_the_scores_in_the_shape_of_seniors_reward_json(box, workspace, local_environment):
    job = source_job(box, workspace)

    record = judge(judge_agent(box, job), local_environment)

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
    box, workspace, local_environment
):
    # Installed is not what was asked for, as an auto-update would leave it.
    (box / "copilot" / "version").write_text("1.0.106")
    job = source_job(box, workspace)

    record = judge(judge_agent(box, job, copilot_version="1.0.105"), local_environment)

    assert record["judge"] == {
        "client": "copilot-cli",
        "version": "1.0.106",
        "requested_version": "1.0.105",
        "model": "gpt-6.1-sol",
        "effort": "high",
        "classifier_model": "claude-haiku-4.5",
    }
    rubric = next(session for session in record["sessions"] if session["name"] == "rubric")
    assert rubric["reported_models"] == ["gpt-6.1-sol"]
    assert rubric["reported_efforts"] == ["high"]
    classify = next(session for session in record["sessions"] if session["name"] == "classify-agent")
    assert (classify["model"], classify["effort"]) == ("claude-haiku-4.5", None)
    assert "--reasoning-effort" not in next(c for c in calls(box) if c["kind"] == "classify")["argv"]


@pytest.mark.parametrize("effort", ["high", ""], ids=["with-effort", "without-effort"])
def test_judges_with_a_model_of_another_family_as_with_the_default(
    box, workspace, local_environment, effort
):
    job = source_job(box, workspace)

    record = judge(judge_agent(box, job, model="gemini-4-argon", effort=effort), local_environment)

    assert (record["rubric_status"], record["taste_status"]) == ("ok", "ok")
    assert (record["judge"]["model"], record["judge"]["effort"]) == ("gemini-4-argon", effort or None)
    judged = [call for call in calls(box) if call["kind"] in ("rubric", "taste")]
    for call in judged:
        assert call["flags"]["--model"] == ["gemini-4-argon"]
        assert call["flags"].get("--reasoning-effort") == ([effort] if effort else None)


def test_records_each_sessions_credits_and_their_total(box, workspace, local_environment):
    write_scenario(box, classify={"nano": 1_250_000_000})
    job = source_job(box, workspace)

    record = judge(judge_agent(box, job), local_environment)

    credits = {session["name"]: session["credits"] for session in record["sessions"]}
    assert credits == {"classify-agent": 1.25, "classify-oracle": 1.25, "rubric": 8.0, "taste": 20.0}
    assert record["credits"] == 30.5


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


def test_reads_an_answer_in_a_code_fence_or_with_prose_around_it(box, workspace, local_environment):
    write_scenario(
        box,
        rubric={"answer": f"My scores:\n\n{json.dumps(RUBRIC_ANSWER)}\n"},
        taste={"answer": f"```json\n{json.dumps(TASTE_ANSWER)}\n```"},
    )
    job = source_job(box, workspace)

    record = judge(judge_agent(box, job), local_environment)

    assert (record["rubric_status"], record["taste_status"]) == ("ok", "ok")
    assert {s["name"]: s["json"] for s in record["sessions"]}["rubric"] == "embedded"
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
def test_an_answer_that_is_missing_or_invalid_fails_and_records_no_score(
    box, workspace, local_environment, kind, answer, status
):
    write_scenario(box, **{kind: {"answer": answer}})
    job = source_job(box, workspace)

    record = judge(judge_agent(box, job), local_environment)

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
    box, workspace, local_environment, scenario, status
):
    write_scenario(box, rubric=scenario)
    job = source_job(box, workspace)

    record = judge(judge_agent(box, job), local_environment)

    assert record["rubric_status"] == status
    assert "rubric_score" not in record["scores"]
    assert record["taste_status"] == "ok"


def test_a_task_with_no_rubric_criteria_skips_the_rubric_judge(box, workspace, local_environment):
    rubric = box / "lanes" / "senior" / TASK / "tests" / "judge" / "rubric.json"
    rubric.write_text(json.dumps({"files": [], "fail_to_pass": [], "pass_to_pass": []}))
    job = source_job(box, workspace)

    record = judge(judge_agent(box, job), local_environment)

    assert record["rubric_status"] == "skipped:no_criteria"
    assert "rubric" not in [call["kind"] for call in calls(box)]
    assert record["taste_status"] == "ok"
    assert record["scores"]["rubric_judge_ok"] == 0


@pytest.mark.parametrize("patch", ["empty", "missing"])
def test_an_empty_or_missing_patch_skips_both_judges(box, workspace, local_environment, patch):
    job = source_job(box, workspace, change=(lambda repo: None) if patch == "empty" else None)

    record = judge(judge_agent(box, job), local_environment)

    assert (record["rubric_status"], record["taste_status"]) == ("skipped:no_patch", "skipped:no_patch")
    assert calls(box) == []


def test_an_install_that_fails_fails_both_judges_and_the_trial_is_still_regraded(
    box, workspace, local_environment
):
    (box / "adapter" / "install.then").write_text("exit 3\n")
    job = source_job(box, workspace)

    record = judge(judge_agent(box, job), local_environment)

    assert (record["rubric_status"], record["taste_status"]) == ("failed:install", "failed:install")
    assert "3" in record["rubric_error"]
    assert (workspace / "lines.txt").read_text().splitlines()[2] == "the fix"


def test_a_dotenv_with_no_token_skips_both_judges(box, workspace, local_environment):
    (box / ".env").write_text("OTHER_KEY=x\n")
    job = source_job(box, workspace)

    record = judge(judge_agent(box, job), local_environment)

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
def test_the_verifier_sees_what_it_would_after_a_regrade_whatever_the_judge_does(
    box, workspace, local_environment, scenario, settings
):
    job = source_job(box, workspace)
    expected = regraded_input(box, workspace, job, local_environment)
    write_scenario(box, **scenario)

    record = judge(judge_agent(box, job, **settings), local_environment)

    assert verifier_input(workspace) == expected
    if scenario:
        assert "failed:" in record["rubric_status"] + record["taste_status"]


def test_a_session_timing_out_fails_its_judge_alone(box, workspace, local_environment):
    write_scenario(box, taste={"sleep": 30})
    job = source_job(box, workspace)

    record = judge(judge_agent(box, job, session_seconds=1), local_environment)

    assert (record["rubric_status"], record["taste_status"]) == ("ok", "failed:timeout")


def test_the_judge_timing_out_fails_every_judge_it_had_not_finished(box, workspace, local_environment):
    write_scenario(box, taste={"sleep": 60})
    job = source_job(box, workspace)

    record = judge(judge_agent(box, job, judge_seconds=10), local_environment)

    assert (record["rubric_status"], record["taste_status"]) == ("ok", "failed:timeout")


@pytest.mark.parametrize("path", ["lines.txt", "notes.txt"], ids=["edits-a-file", "adds-a-file"])
def test_a_session_that_changes_the_repository_fails_and_the_change_is_undone(
    box, workspace, local_environment, path
):
    job = source_job(box, workspace)
    expected = regraded_input(box, workspace, job, local_environment)
    write_scenario(box, rubric={"write": path})

    record = judge(judge_agent(box, job), local_environment)

    assert record["rubric_status"] == "failed:repo_changed"
    rubric = next(session for session in record["sessions"] if session["name"] == "rubric")
    assert rubric["repo_unchanged"] is False
    assert rubric["restored"] is True
    assert "rubric_score" not in record["scores"]
    assert verifier_input(workspace) == expected


# The token


def test_the_token_reaches_copilot_alone_and_no_record_or_log(box, workspace, local_environment):
    job = source_job(box, workspace)
    agent = judge_agent(box, job)

    record = judge(agent, local_environment)

    sha = hashlib.sha256(TOKEN.encode()).hexdigest()
    for call in calls(box):
        assert call["token_sha256"] == sha
        assert not call["gh_token"]
        assert TOKEN not in " ".join(call["argv"])
        assert call["argv"][call["argv"].index("--secret-env-vars") + 1] == "COPILOT_GITHUB_TOKEN"
    assert record["rubric_status"] == "ok"
    written = [path for path in (box / "sandbox").rglob("*") if path.is_file()]
    written += [path for path in agent.logs_dir.rglob("*") if path.is_file()]
    assert [path for path in written if TOKEN.encode() in path.read_bytes()] == []
    # A later command, as the verifier's, starts with no token.
    later = go(local_environment.exec("printenv COPILOT_GITHUB_TOKEN"))
    assert later.return_code != 0


def test_a_log_holding_the_token_is_removed(box, workspace, local_environment):
    write_scenario(box, rubric={"leak": True})
    job = source_job(box, workspace)
    agent = judge_agent(box, job)

    record = judge(agent, local_environment)

    rubric = next(session for session in record["sessions"] if session["name"] == "rubric")
    assert len(rubric["removed_for_token"]) == 1
    assert not Path(rubric["removed_for_token"][0]).exists()
    written = [path for path in (box / "sandbox").rglob("*") if path.is_file()]
    assert [path for path in written if TOKEN.encode() in path.read_bytes()] == []


# The credit budget


def test_a_job_starts_no_session_its_budget_cannot_hold(box, workspace, local_environment):
    job = source_job(box, workspace)
    # The classifiers take 2 and the rubric judge 8, so the taste judge's
    # reservation of 60 would pass a budget of 40.
    agent = judge_agent(box, job, credit_budget=40)

    record = judge(agent, local_environment)

    assert record["rubric_status"] == "ok"
    assert record["taste_status"] == "skipped:budget"
    assert "taste" not in [call["kind"] for call in calls(box)]
    assert CreditLedger(agent.logs_dir.parent.parent / "judge-credits.jsonl", 40).spent() == 10.0


def test_every_trial_of_a_job_charges_one_ledger(box, workspace, local_environment):
    job = source_job(box, workspace)
    first = judge_agent(box, job, regrade=regrade_dir(box, "fake-senior-task__One1111"))
    judge(first, local_environment)
    git(workspace, "reset", "-q", "--hard", "HEAD")
    git(workspace, "clean", "-qfdx")
    second = judge_agent(box, job, regrade=regrade_dir(box, "fake-senior-task__Two2222"), credit_budget=40)

    record = judge(second, local_environment)

    # The first trial spent 30, so the second has 10 left: the classifiers fit, and neither judge does.
    assert [s["status"] for s in record["sessions"]] == ["ok", "ok", "skipped:budget", "skipped:budget"]
    assert (record["rubric_status"], record["taste_status"]) == ("skipped:budget", "skipped:budget")


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


def test_a_token_in_a_reply_or_an_error_never_reaches_the_record(box, workspace, local_environment):
    leaky = json.loads(json.dumps(TASTE_ANSWER))
    leaky["practice_alignment"]["style_consistency"]["rationale"] = f"saw {TOKEN}"
    write_scenario(box, taste={"answer": json.dumps(leaky)}, rubric={"answer": f"no scores, {TOKEN}"})
    job = source_job(box, workspace)
    agent = judge_agent(box, job)

    record = judge(agent, local_environment)

    assert (record["rubric_status"], record["taste_status"]) == ("failed:no_json", "ok")
    assert TOKEN not in (agent.logs_dir / "judge.json").read_text()
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
    box, workspace, local_environment, command
):
    job = source_job(box, workspace)
    expected = regraded_input(box, workspace, job, local_environment)
    write_scenario(box, rubric={"run": command})

    record = judge(judge_agent(box, job), local_environment)

    assert record["rubric_status"] == "failed:repo_changed"
    assert next(s for s in record["sessions"] if s["name"] == "rubric")["restored"] is True
    assert verifier_input(workspace) == expected


def test_a_workspace_that_cannot_be_put_back_fails_the_trial(
    box, workspace, local_environment, monkeypatch
):
    job = source_job(box, workspace)
    write_scenario(box, rubric={"write": "notes.txt"})
    agent = judge_agent(box, job)

    async def leaves_it(*args, **kwargs):
        pass

    monkeypatch.setattr(agent, "_restore", leaves_it)
    go(agent.setup(local_environment))

    with pytest.raises(suss.judge.JudgeError, match="put back"):
        go(agent.run("an instruction", local_environment, AgentContext()))
    record = json.loads((agent.logs_dir / "judge.json").read_text())
    assert record["rubric_status"] == "failed:repo_changed"


def test_a_restore_that_raises_fails_the_trial(box, workspace, local_environment, monkeypatch):
    job = source_job(box, workspace)
    write_scenario(box, rubric={"write": "notes.txt"})
    agent = judge_agent(box, job)

    async def stuck(*args, **kwargs):
        raise RuntimeError("the reset stopped midway")

    monkeypatch.setattr(agent, "_restore", stuck)
    go(agent.setup(local_environment))

    with pytest.raises(suss.judge.JudgeError, match="put back"):
        go(agent.run("an instruction", local_environment, AgentContext()))


def test_a_cancel_during_cleanup_still_puts_the_test_files_back_and_fails_the_trial(
    box, workspace, local_environment
):
    job = source_job(box, workspace)
    expected = regraded_input(box, workspace, job, local_environment)
    agent = judge_agent(box, job)
    real_cleanup = agent._cleanup
    entered = asyncio.Event()

    async def slow_cleanup(*args):
        entered.set()
        await asyncio.sleep(30)
        await real_cleanup(*args)

    agent._cleanup = slow_cleanup
    go(agent.setup(local_environment))

    async def cancel_during_cleanup():
        run = asyncio.create_task(agent.run("an instruction", local_environment, AgentContext()))
        await entered.wait()
        run.cancel()
        with pytest.raises(suss.judge.JudgeError, match="put back"):
            await run

    go(cancel_during_cleanup())

    assert verifier_input(workspace) == expected
    assert (agent.logs_dir / "replay.json").exists()


def test_a_cancelled_run_still_puts_the_test_files_back_and_stays_cancelled(
    box, workspace, local_environment
):
    job = source_job(box, workspace)
    expected = regraded_input(box, workspace, job, local_environment)
    write_scenario(box, taste={"sleep": 60})
    agent = judge_agent(box, job)
    go(agent.setup(local_environment))

    async def cancel_during_taste():
        run = asyncio.create_task(agent.run("an instruction", local_environment, AgentContext()))
        while "taste" not in [call["kind"] for call in calls(box)]:
            await asyncio.sleep(0.1)
        run.cancel()
        with pytest.raises(asyncio.CancelledError):
            await run

    go(cancel_during_taste())

    assert verifier_input(workspace) == expected
    assert (agent.logs_dir / "replay.json").exists()
    record = json.loads((agent.logs_dir / "judge.json").read_text())
    assert (record["rubric_status"], record["taste_status"]) == ("ok", "failed:cancelled")


def test_a_session_the_judge_stops_keeps_the_credits_it_reported(box, workspace, local_environment):
    write_scenario(box, taste={"sleep": 60})
    job = source_job(box, workspace)
    agent = judge_agent(box, job, judge_seconds=10)

    record = judge(agent, local_environment)

    taste = next(s for s in record["sessions"] if s["name"] == "taste")
    assert taste["credits"] == 20.0
    assert record["credits"] == 30.0
    assert CreditLedger(agent.logs_dir.parent.parent / "judge-credits.jsonl", 1000).spent() == 30.0


def test_a_dotenv_with_no_token_leaves_the_judges_files_alone(box, workspace, local_environment):
    (box / ".env").write_text("OTHER_KEY=x\n")
    job = source_job(box, workspace)
    agent = judge_agent(box, job)

    judge(agent, local_environment)

    assert (Path(agent.paths.logs) / "install.log").exists()
    assert (Path(agent.paths.adapter) / "install.sh").exists()
