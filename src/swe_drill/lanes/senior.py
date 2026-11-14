"""
What the senior lane changes in each task's copy, beyond what every lane does.

Each copy's verifier has room for SWE-drill's judge and, on a design task, for
senior's validation agent. Its tests/test.sh pins litellm below the first
release that fails every judge call, and runs the validation agent through
Copilot CLI with the swe_drill.validate beside it.
"""

import tomllib
from pathlib import Path

from swe_drill import validate
from swe_drill.lanes.edits import TEST_SH, LaneError, add_key, read_test_sh, replace_key

# Every senior task can be graded.
UNGRADEABLE: tuple[str, ...] = ()

# How long SWE-drill's judge may take on a senior trial, its install included: about
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

VALIDATE = "tests/swe_drill_validate.py"
VALIDATION_SPEC = "tests/validate/validation_spec.toml"
# The lines of senior's test.sh that choose, install and run the validation
# agent, and what the copy runs in their place: the claude_code harness, whose
# outcome files swe_drill.validate writes, no Claude Code, and swe_drill.validate.
VALIDATION_LINES = (
    (
        b'    _va_harness="${SSB_OVERRIDE_VA_HARNESS:-${FORCE_VA_HARNESS:-miniswe}}"',
        b"    _va_harness=claude_code",
    ),
    (
        b"            bash /tests/ssb_lib/scripts/install-claude-code.sh || { : > /logs/verifier/reward.txt;"
        b" rm -f /logs/verifier/reward.json; exit 0; }",
        b'            echo "SWE-drill runs the validation agent through the Copilot CLI its judge installed"',
    ),
    (
        b'        "$_SYS_PYTHON" /tests/run_validate.py || _validation_exit=$?',
        b'        "$_SYS_PYTHON" /tests/swe_drill_validate.py || _validation_exit=$?',
    ),
)
# run_validate.py's defaults: the agent's first run, and the least its retry takes.
VA_SECONDS = 900
RETRY_SECONDS = 600
REVIEW_MARGIN_SECONDS = validate.REVIEWS * validate.REVIEW_SESSIONS * validate.REVIEW_SESSION_SECONDS


def rewrite(task: Path, toml: bytes) -> dict[str, bytes]:
    """
    The files the task's copy writes, by path from the task: its task.toml
    `toml` with room for the judge and the validation agent, as `_room`
    gives it, its tests/test.sh as `_test_sh` gives it, and VALIDATE.

    Raises LaneError as each of them does.
    """
    return {
        "task.toml": _room(task, toml),
        TEST_SH: _test_sh(task),
        VALIDATE: Path(validate.__file__).read_bytes(),
    }


def _room(task: Path, text: bytes) -> bytes:
    """
    The task.toml `text` with its `[verifier] timeout_sec`, or Harbor's
    default where it sets none, raised by JUDGE_MARGIN_SECONDS and, on a
    design task, by what `validation_seconds` gives.

    Raises LaneError naming the file when the timeout is not a positive
    number, as `validation_seconds` does, and as `add_key` and `replace_key` do.
    """
    path = task / "task.toml"
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
    raised = float(timeout) + JUDGE_MARGIN_SECONDS + validation_seconds(task)
    if "timeout_sec" in verifier:
        return replace_key(path, text, original, "verifier", "timeout_sec", raised)
    return add_key(path, text, original, "verifier", "timeout_sec", raised)


def validation_seconds(task: Path) -> int:
    """
    How long the task's validation agent and its review judge may take
    together, or 0 for a task with no validation spec.

    The agent's first run may take `va_timeout_sec`, and its retry
    `retry_timeout_sec`, or the first run's, but never less than
    RETRY_SECONDS, as run_validate.py reads them. Raises LaneError naming the
    spec when it is not TOML or either is not a positive number.
    """
    path = task / VALIDATION_SPEC
    if not path.is_file():
        return 0
    try:
        settings = tomllib.loads(path.read_text()).get("settings", {})
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise LaneError(f"{path}: cannot be read as TOML: {error}") from None
    if not isinstance(settings, dict):
        settings = {}
    found = {}
    for key, default in (("va_timeout_sec", VA_SECONDS), ("retry_timeout_sec", None)):
        value = settings.get(key, default)
        if value is not None and (isinstance(value, bool) or not isinstance(value, int | float) or value <= 0):
            raise LaneError(
                f"{path}: [settings] {key} is {value!r}, not a positive number of seconds, so the lane"
                " cannot make room for the validation agent"
            )
        found[key] = value
    first = found["va_timeout_sec"]
    retry = max(found["retry_timeout_sec"] or first, RETRY_SECONDS)
    return int(first + retry + REVIEW_MARGIN_SECONDS)


def _test_sh(task: Path) -> bytes:
    """
    The task's test.sh with its one line that is exactly LITELLM made
    PINNED_LITELLM, and each of VALIDATION_LINES made its replacement, its
    bytes otherwise unchanged.

    Raises LaneError as `read_test_sh` does, and naming the file when not
    exactly one of its lines is any one of them.
    """
    lines = read_test_sh(task, "pin litellm in it")
    _replace(task, lines, LITELLM, PINNED_LITELLM, f"pin litellm to {PINNED_LITELLM.decode()}")
    for old, new in VALIDATION_LINES:
        _replace(task, lines, old, new, "run the validation agent through Copilot CLI")
    return b"".join(lines)


def _replace(task: Path, lines: list[bytes], old: bytes, new: bytes, doing: str) -> None:
    """Make the one line of `lines` that is `old`, its ending aside, `new`, or raise LaneError naming the file."""
    found = [number for number, line in enumerate(lines) if line.rstrip(b"\r\n") == old]
    if len(found) != 1:
        raise LaneError(
            f"{task / TEST_SH}: has {len(found)} lines that are {old.decode().strip()}, so the lane "
            f"cannot {doing}; it takes a test.sh with exactly one"
        )
    line = lines[found[0]]
    lines[found[0]] = new + line[len(old) :]
