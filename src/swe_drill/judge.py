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
import shutil
import stat
import sys
import tempfile
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
        trial_dir = self.trial_paths.verifier_dir.parent
        private = Path(tempfile.mkdtemp(prefix=".judge-", dir=trial_dir))
        try:
            await self._judge(private)
            reward = self._combine(private)
            self._publish(private)
        finally:
            shutil.rmtree(private, ignore_errors=True)
        return VerifierResult(rewards=reward)

    async def _judge(self, private: Path) -> None:
        """
        Run the copy's evaluate_rubrics.py on the agent's patch, writing
        rubrics_results.json, and what it prints to judge.log, in `private`.

        Its process alone gets the token. It reads the patch from a copy in
        `private`, and nothing it reads or writes is in a directory a sandbox
        can write to.
        """
        patch = _read_plain(self.trial_paths.agent_dir / PATCH_NAME)
        if patch is None:
            patch = _read_plain(self.trial_paths.verifier_dir / SANDBOX_PATCH_NAME) or b""
        (private / PATCH_NAME).write_bytes(patch)
        env = {
            "PATH": os.environ.get("PATH", ""),
            "EVAL_API_KEY": self._token,
            "EVAL_BASE_URL": BASE_URL,
            "EVAL_MODEL": str(self.model),
        }
        with (private / LOG_NAME).open("wb") as log:
            process = await asyncio.create_subprocess_exec(
                sys.executable, "-I", "-c", RUNNER,
                str(self.task.paths.tests_dir), str(private / PATCH_NAME), str(private / RESULTS_NAME),
                env=env, stdout=log, stderr=asyncio.subprocess.STDOUT,
            )
            try:
                await process.wait()
            finally:
                # Harbor cancels verify() at the verifier's timeout.
                if process.returncode is None:
                    process.kill()
                    await process.wait()

    def _combine(self, private: Path) -> dict[str, Any]:
        """
        The reward test.sh gives from the sandbox's tests_reward.txt and the
        judge's rubrics_results.json in `private`, written to reward.json and
        reward.txt there: 1 when the tests passed and the judge passed every
        must-have item.

        A file that is missing, unreadable or a link, or a value that is not a
        finite number, counts as a failure.
        """
        try:
            text = (_read_plain(self.trial_paths.verifier_dir / TESTS_REWARD_NAME) or b"").decode()
            tests_reward = float(text.strip() or "0")
        except (UnicodeDecodeError, ValueError):
            tests_reward = 0.0
        if not math.isfinite(tests_reward):
            tests_reward = 0.0
        try:
            rubrics = json.loads((private / RESULTS_NAME).read_text())
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
        (private / self.trial_paths.reward_json_path.name).write_text(json.dumps(reward, indent=2))
        (private / self.trial_paths.reward_text_path.name).write_text(str(reward["reward"]))
        return reward

    def _publish(self, private: Path) -> None:
        """
        Move the judge's files from `private` into the verifier directory,
        and remove what a sandbox left at the name of one the judge did not
        write.

        A rename replaces whatever a sandbox left at each name, a link
        included, and never writes through it. A name it cannot replace, as
        when a sandbox left a directory there, is left as it is.
        """
        names = (RESULTS_NAME, LOG_NAME, self.trial_paths.reward_json_path.name,
                 self.trial_paths.reward_text_path.name)
        for name in names:
            target = self.trial_paths.verifier_dir / name
            try:
                if (private / name).exists():
                    os.replace(private / name, target)
                else:
                    target.unlink(missing_ok=True)
            except OSError:
                pass


def _read_plain(path: Path) -> bytes | None:
    """
    The bytes of the regular file at `path`, or None when there is none, or it
    is a link, which a sandbox could point at any file on the host.
    """
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return None
    with os.fdopen(descriptor, "rb") as file:
        if not stat.S_ISREG(os.fstat(file.fileno()).st_mode):
            return None
        return file.read()


def _score(value: Any) -> float:
    """`value` as a finite number, or 0 for anything else, which Harbor would refuse as a reward."""
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        return 0.0
    return float(value)
