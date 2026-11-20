"""
JudgedVerifier's judge and reward, with a stand-in evaluate_rubrics.py that
records what it was given, so no test calls Copilot.
"""

import asyncio
import json
import re
from pathlib import Path
from types import SimpleNamespace

from harbor.models.trial.paths import TrialPaths

from swe_drill.judge import JudgedVerifier
from swe_drill.replay import ReplayVerifier

# Records what the judge's process saw, and passes every must-have item.
STAND_IN = """
import json, os
TESTS_DIR = "/tests"
PATCH_PATH = PATCH_FALLBACK = "/logs/verifier/agent.patch"
RESULTS_PATH = "/logs/verifier/rubrics_results.json"
def main():
    seen = {
        "env": sorted(os.environ),
        "key": os.environ.get("EVAL_API_KEY"),
        "model": os.environ.get("EVAL_MODEL"),
        "patch": open(PATCH_PATH).read(),
        "prompt": open(os.path.join(TESTS_DIR, "prompt.txt")).read(),
    }
    with open(RESULTS_PATH, "w") as f:
        json.dump({"must_have_pass": True, "agg_score": 0.75, "seen": seen}, f)
    return 0
"""


def verifier(tmp_path: Path, judge: str = STAND_IN) -> JudgedVerifier:
    """A JudgedVerifier for a trial under `tmp_path`, built without reading a swe-drill.toml."""
    tests = tmp_path / "task" / "tests"
    tests.mkdir(parents=True)
    (tests / "evaluate_rubrics.py").write_text(judge)
    (tests / "prompt.txt").write_text("Refactor the buffer pool.")
    paths = TrialPaths(tmp_path / "trial")
    paths.mkdir()
    made = object.__new__(JudgedVerifier)
    made.task = SimpleNamespace(paths=SimpleNamespace(tests_dir=tests))
    made.trial_paths = paths
    made.model = "gemini-4-argon"
    made._token = "gh-1"
    return made


def judge_and_combine(made: JudgedVerifier) -> dict:
    asyncio.run(made._judge())
    return made._combine()


def test_the_judge_runs_on_the_host_with_the_token_and_model_and_the_agents_own_patch(tmp_path):
    made = verifier(tmp_path)
    (made.trial_paths.agent_dir / "model.patch").write_text("diff --git a/x b/x\n")
    (made.trial_paths.verifier_dir / "agent.patch").write_text("what the sandbox left\n")
    (made.trial_paths.verifier_dir / "tests_reward.txt").write_text("1.0")

    reward = judge_and_combine(made)

    seen = json.loads((made.trial_paths.verifier_dir / "rubrics_results.json").read_text())["seen"]
    assert seen["key"] == "gh-1"
    assert seen["model"] == "gemini-4-argon"
    assert seen["patch"] == "diff --git a/x b/x\n"
    assert seen["prompt"] == "Refactor the buffer pool."
    given = {"EVAL_API_KEY", "EVAL_BASE_URL", "EVAL_MODEL", "PATH"}
    # Python sets its locale variables as it starts, on macOS.
    extra = [name for name in seen["env"] if name not in given and not name.startswith(("LC_", "__CF"))]
    assert given <= set(seen["env"]) and extra == [], f"the judge got more than it needs: {extra}"
    assert reward == {
        "reward": 1.0, "tests_reward": 1.0, "must_have_pass": True,
        "rubrics_agg_score": 0.75, "overall_pass": True,
    }
    assert json.loads(made.trial_paths.reward_json_path.read_text()) == reward
    assert made.trial_paths.reward_text_path.read_text() == "1.0"


def test_a_trial_with_no_model_patch_is_judged_on_the_patch_test_sh_left(tmp_path):
    made = verifier(tmp_path)
    (made.trial_paths.verifier_dir / "agent.patch").write_text("from test.sh\n")

    judge_and_combine(made)

    seen = json.loads((made.trial_paths.verifier_dir / "rubrics_results.json").read_text())["seen"]
    assert seen["patch"] == "from test.sh\n"


def test_a_verdict_the_sandbox_left_is_never_counted(tmp_path):
    made = verifier(tmp_path, judge="import sys\ndef main():\n    return 1\n")
    (made.trial_paths.agent_dir / "model.patch").write_text("diff\n")
    (made.trial_paths.verifier_dir / "tests_reward.txt").write_text("1.0")
    # The tests run code the agent wrote, which could write a passing verdict.
    (made.trial_paths.verifier_dir / "rubrics_results.json").write_text('{"must_have_pass": true}')

    reward = judge_and_combine(made)

    assert reward["must_have_pass"] is False
    assert reward["reward"] == 0.0


def test_failed_tests_fail_the_trial_whatever_the_judge_says(tmp_path):
    made = verifier(tmp_path)
    (made.trial_paths.agent_dir / "model.patch").write_text("diff\n")
    (made.trial_paths.verifier_dir / "tests_reward.txt").write_text("0.0")

    reward = judge_and_combine(made)

    assert (reward["reward"], reward["must_have_pass"]) == (0.0, True)


def test_a_tests_reward_or_score_that_is_not_a_finite_number_counts_as_zero(tmp_path):
    made = verifier(tmp_path, judge=STAND_IN.replace('"agg_score": 0.75', '"agg_score": "high"'))
    (made.trial_paths.agent_dir / "model.patch").write_text("diff\n")
    (made.trial_paths.verifier_dir / "tests_reward.txt").write_text("nan")

    reward = judge_and_combine(made)

    assert (reward["reward"], reward["tests_reward"], reward["rubrics_agg_score"]) == (0.0, 0.0, 0.0)


SPEC = Path(__file__).resolve().parent.parent / "docs" / "specs" / "swe-drill-spec.md"


def test_the_verifier_reads_the_judges_model_from_swe_drill_toml_and_its_key_from_env(tmp_path, monkeypatch):
    blocks = re.findall(r"```toml\n(.*?)```", SPEC.read_text(), re.DOTALL)
    (tmp_path / "swe-drill.toml").write_text(next(block for block in blocks if "[arms." in block))
    (tmp_path / ".env").write_text("COPILOT_GITHUB_TOKEN=gh-from-dotenv\n")
    monkeypatch.setenv("COPILOT_GITHUB_TOKEN", "exported-in-the-shell")
    # Harbor's own arguments are not what this test is about.
    monkeypatch.setattr(ReplayVerifier, "__init__", lambda self, **kwargs: None)

    made = JudgedVerifier(drill_config=str(tmp_path / "swe-drill.toml"), arm="copilot-gpt", lane="atlas-rf")

    assert (made.model, made._token) == ("gemini-4-argon", "gh-from-dotenv")
