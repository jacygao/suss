"""
What the senior lane changes in each task's copy, beyond what every lane does.

Each copy's verifier has room for suss's judge, and its tests/test.sh pins
litellm below the first release that fails every judge call.
"""

import tomllib
from pathlib import Path

from suss.lanes.edits import TEST_SH, LaneError, add_key, read_test_sh, replace_key

# Every senior task can be graded.
UNGRADEABLE: tuple[str, ...] = ()

# How long suss's judge may take on a senior trial, its install included: about
# three times the longest the judge has taken on one.
JUDGE_SECONDS = 1800
# How long putting the workspace back may take once the judge has stopped.
JUDGE_CLEANUP_SECONDS = 300
# What a senior task's verifier timeout gains, so the judge never shortens the
# time its tests had.
JUDGE_MARGIN_SECONDS = JUDGE_SECONDS + JUDGE_CLEANUP_SECONDS
# Harbor's verifier timeout for a task.toml that sets none.
VERIFIER_SECONDS = 600.0

# The requirement every senior task's test.sh installs, and what the lane's copy
# installs in its place: every litellm from 1.106.0 fails each judge's call.
LITELLM = b"litellm>=1.0,<2.0"
PINNED_LITELLM = b"litellm>=1.0,<1.106"


def rewrite(task: Path, toml: bytes) -> dict[str, bytes]:
    """
    The files the task's copy writes, by path from the task: its task.toml
    `toml` with room for the judge, as `_judge_room` gives it, and its
    tests/test.sh with litellm pinned, as `_pinned` gives it.

    Raises LaneError as each of them does.
    """
    return {
        "task.toml": _judge_room(task / "task.toml", toml),
        TEST_SH: _pinned(task),
    }


def _judge_room(path: Path, text: bytes) -> bytes:
    """
    The task.toml `text`, read from `path`, with its `[verifier] timeout_sec`,
    or Harbor's default where it sets none, raised by JUDGE_MARGIN_SECONDS.

    Raises LaneError naming the file when the timeout is not a positive
    number, and as `add_key` and `replace_key` do.
    """
    original = tomllib.loads(text.decode())
    verifier = original.get("verifier", {})
    if not isinstance(verifier, dict):
        verifier = {}
    timeout = verifier.get("timeout_sec", VERIFIER_SECONDS)
    if isinstance(timeout, bool) or not isinstance(timeout, int | float) or timeout <= 0:
        raise LaneError(
            f"{path}: [verifier] timeout_sec is {timeout!r}, not a positive number of seconds,"
            " so the lane cannot make room in it for the judge"
        )
    raised = float(timeout) + JUDGE_MARGIN_SECONDS
    if "timeout_sec" in verifier:
        return replace_key(path, text, original, "verifier", "timeout_sec", raised)
    return add_key(path, text, original, "verifier", "timeout_sec", raised)


def _pinned(task: Path) -> bytes:
    """
    The task's test.sh with its one line that is exactly LITELLM made
    PINNED_LITELLM, its bytes otherwise unchanged.

    Raises LaneError as `read_test_sh` does, and naming the file when not
    exactly one of its lines is LITELLM.
    """
    lines = read_test_sh(task, "pin litellm in it")
    found = [number for number, line in enumerate(lines) if line.rstrip(b"\r\n") == LITELLM]
    if len(found) != 1:
        raise LaneError(
            f"{task / TEST_SH}: has {len(found)} lines that are {LITELLM.decode()}, so the lane "
            f"cannot pin litellm to {PINNED_LITELLM.decode()}; it takes a test.sh with exactly one"
        )
    line = lines[found[0]]
    lines[found[0]] = PINNED_LITELLM + line[len(LITELLM) :]
    return b"".join(lines)
