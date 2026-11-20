"""
The atlas-rf rubric judge, run on the host once the verifier's sandbox has run
the tests.

The judge calls Copilot's API with the user's Copilot token, and the tests run
code the agent wrote, so the token never enters a sandbox: the verifier reads
it from .env, as the agents do, and gives it to the judge's own process alone.
"""

import asyncio
import json
import math
import os
import sys
from pathlib import Path
from typing import Any

from harbor.models.verifier.result import VerifierResult

from swe_drill import config
from swe_drill.replay import ReplayVerifier
from swe_drill.workspace import PATCH_NAME

BASE_URL = "https://api.enterprise.githubcopilot.com"
EVALUATE = "evaluate_rubrics.py"
RESULTS_NAME = "rubrics_results.json"
LOG_NAME = "judge.log"
TESTS_REWARD_NAME = "tests_reward.txt"
# The patch test.sh leaves in the verifier directory, for an agent that leaves no model.patch.
SANDBOX_PATCH_NAME = "agent.patch"

# Runs the copy's evaluate_rubrics.py with its paths in the container made the host's.
RUNNER = """
import importlib.util, sys
tests, patch, results = sys.argv[1:4]
spec = importlib.util.spec_from_file_location("evaluate_rubrics", tests + "/evaluate_rubrics.py")
judge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(judge)
judge.TESTS_DIR = tests
judge.PATCH_PATH = judge.PATCH_FALLBACK = patch
judge.RESULTS_PATH = results
sys.exit(judge.main())
"""


class JudgedVerifier(ReplayVerifier):
    """
    ReplayVerifier, then the task's rubric judge on the host, then the reward
    test.sh would have given, from the tests' result and the judge's.
    """

    def __init__(self, *, drill_config: Any, arm: Any, lane: Any, **kwargs: Any):
        """
        Read the judge's model and the Copilot token from the swe-drill.toml at
        `drill_config`, and the .env beside it, as `arm` runs `lane` with them.

        Raises ConfigError as `config.load` and `config.check_judge` do.
        """
        super().__init__(**kwargs)
        settings = config.load(Path(str(drill_config)), str(arm), str(lane))
        config.check_judge(settings, str(lane))
        self.model = settings.eval_model
        self._token = settings.env[config.TOKEN_NAME]

    async def verify(self) -> VerifierResult:
        """
        Grade as ReplayVerifier does, judge the patch, and return the combined
        reward, which verifier/reward.json and reward.txt then hold.

        Raises what ReplayVerifier raises, and then runs no judge.
        """
        await super().verify()
        await self._judge()
        return VerifierResult(rewards=self._combine())

    async def _judge(self) -> None:
        """
        Run the copy's evaluate_rubrics.py on the agent's patch, writing
        verifier/rubrics_results.json, and what it prints to verifier/judge.log.

        Its process alone gets the token. Whatever the sandbox left at
        rubrics_results.json is removed first, since the tests could have
        written one.
        """
        verifier_dir = self.trial_paths.verifier_dir
        results = verifier_dir / RESULTS_NAME
        results.unlink(missing_ok=True)
        model_patch = self.trial_paths.agent_dir / PATCH_NAME
        patch = model_patch if model_patch.is_file() else verifier_dir / SANDBOX_PATCH_NAME
        env = {
            "PATH": os.environ.get("PATH", ""),
            "EVAL_API_KEY": self._token,
            "EVAL_BASE_URL": BASE_URL,
            "EVAL_MODEL": str(self.model),
        }
        with (verifier_dir / LOG_NAME).open("wb") as log:
            process = await asyncio.create_subprocess_exec(
                sys.executable, "-I", "-c", RUNNER,
                str(self.task.paths.tests_dir), str(patch), str(results),
                env=env, stdout=log, stderr=asyncio.subprocess.STDOUT,
            )
            try:
                await process.wait()
            finally:
                # Harbor cancels verify() at the verifier's timeout.
                if process.returncode is None:
                    process.kill()
                    await process.wait()

    def _combine(self) -> dict[str, Any]:
        """
        The reward test.sh gives from tests_reward.txt and
        rubrics_results.json, written to reward.json and reward.txt: 1 when
        the tests passed and the judge passed every must-have item.

        A file that is missing or unreadable, or a value that is not a finite
        number, counts as a failure.
        """
        verifier_dir = self.trial_paths.verifier_dir
        try:
            tests_reward = float((verifier_dir / TESTS_REWARD_NAME).read_text().strip() or "0")
        except (OSError, ValueError):
            tests_reward = 0.0
        if not math.isfinite(tests_reward):
            tests_reward = 0.0
        try:
            rubrics = json.loads((verifier_dir / RESULTS_NAME).read_text())
        except (OSError, ValueError):
            rubrics = {}
        if not isinstance(rubrics, dict):
            rubrics = {}
        must_have_pass = bool(rubrics.get("must_have_pass", False))
        overall_pass = tests_reward >= 1.0 and must_have_pass
        reward = {
            "reward": 1.0 if overall_pass else 0.0,
            "tests_reward": tests_reward,
            "must_have_pass": must_have_pass,
            "rubrics_agg_score": _score(rubrics.get("agg_score")),
            "overall_pass": overall_pass,
        }
        self.trial_paths.reward_json_path.write_text(json.dumps(reward, indent=2))
        self.trial_paths.reward_text_path.write_text(str(reward["reward"]))
        return reward


def _score(value: Any) -> float:
    """`value` as a finite number, or 0 for anything else, which Harbor would refuse as a reward."""
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        return 0.0
    return float(value)
