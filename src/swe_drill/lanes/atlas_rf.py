"""
What the atlas-rf lane changes in each task's copy, beyond what every lane does.

Each copy's test.sh runs the tests and not the rubric judge, and its
[verifier.env] gives the judge no key, model or address. The judge runs on the
host instead, in JudgedVerifier, since the tests run code the agent wrote and
the judge's key is the user's Copilot token. Each copy's evaluate_rubrics.py
sends the header Copilot's API needs.
"""

import tomllib
from pathlib import Path

from swe_drill.lanes.edits import TEST_SH, LaneError, read_task_file, read_test_sh

# Every SWE Atlas Refactoring task can be graded.
UNGRADEABLE: tuple[str, ...] = ()

# The tasks whose image's entrypoint is /bin/bash. Docker runs Harbor's
# keepalive command as arguments to it, so bash reads `sh` as a script and the
# container exits 126 before the agent starts.
BASH_ENTRYPOINT = (
    "task-69b7c2a04b6f8ff9ed98812c",
    "task-69b7c2a04b6f8ff9ed98812d",
    "task-69b7c2a04b6f8ff9ed98812e",
    "task-69b7c2a04b6f8ff9ed98812f",
    "task-69b7c2a04b6f8ff9ed988131",
    "task-69b7c2a04b6f8ff9ed988134",
    "task-69b7c2a04b6f8ff9ed988137",
    "task-69b7c2a04b6f8ff9ed98813a",
    "task-69b7c2a04b6f8ff9ed98813b",
    "task-69b7c2a04b6f8ff9ed98813d",
    "task-69d196f015a150488265afc0",
    "task-69d196f015a150488265afc1",
    "task-69d196f015a150488265afc2",
    "task-69d196f015a150488265afc3",
    "task-69d196f015a150488265afc4",
)
COMPOSE = "environment/docker-compose.yaml"
# Harbor merges a task's compose file over its own, so the container runs Harbor's command alone.
NO_ENTRYPOINT = b"services:\n  main:\n    entrypoint: []\n"

EVALUATE = "tests/evaluate_rubrics.py"
# The judge's one client in evaluate_rubrics.py, and the copy's in its place.
# Copilot's API refuses a call without the header with a 400.
OPENAI_CALL = b"OpenAI(api_key=api_key, base_url=base_url)"
COPILOT_CALL = (
    b'OpenAI(api_key=api_key, base_url=base_url, default_headers={"Copilot-Integration-Id":'
    b' "copilot-developer-cli"})'
)

# The lines of every SWE Atlas task's [verifier.env], which the copy leaves
# out, so nothing of Harbor's own environment reaches the verifier's sandbox.
JUDGE_ENV_LINES = (
    b'EVAL_API_KEY = "${OPENAI_API_KEY}"',
    b'EVAL_BASE_URL = "${OPENAI_API_BASE}"',
    b'EVAL_MODEL = "${EVAL_MODEL:-anthropic/claude-opus-4-5-20251101}"',
)

# The line of every SWE Atlas test.sh that runs the judge, and the copy's in its place.
JUDGE_LINE = b'python3 /tests/evaluate_rubrics.py || echo "evaluate_rubrics.py exited non-zero"'
NO_JUDGE_LINE = b'echo "The rubric judge runs on the host, after this script"'


def rewrite(task: Path, toml: bytes) -> dict[str, bytes]:
    """
    The files the task's copy writes, by path from the task: its task.toml
    `toml` with no judge settings, as `_without_judge_env` gives it, its
    tests/evaluate_rubrics.py sending Copilot's header, as `_with_header`
    gives it, and its tests/test.sh running no judge, as `_without_judge`
    gives it. A task in BASH_ENTRYPOINT also gets
    NO_ENTRYPOINT as its environment/docker-compose.yaml, as
    `_without_entrypoint` gives it.

    Raises LaneError as each of them does.
    """
    files = {
        "task.toml": _without_judge_env(task, toml),
        EVALUATE: _with_header(task),
        TEST_SH: _without_judge(task),
    }
    if task.name in BASH_ENTRYPOINT:
        files[COMPOSE] = _without_entrypoint(task)
    return files


def _without_entrypoint(task: Path) -> bytes:
    """
    NO_ENTRYPOINT, for the task's environment/docker-compose.yaml.

    Raises LaneError naming the task's environment directory when it is
    missing or a link, and naming its docker-compose.yaml when it has one,
    which the lane would replace.
    """
    path = task / COMPOSE
    if path.parent.is_symlink():
        raise LaneError(f"{path.parent}: a link, so the lane cannot write into a copy of it")
    if not path.parent.is_dir():
        raise LaneError(
            f"{path.parent}: no such directory, so the lane cannot clear its image's entrypoint"
        )
    if path.exists() or path.is_symlink():
        raise LaneError(
            f"{path}: the lane would replace it to clear the image's entrypoint, so it must be"
            " changed by hand"
        )
    return NO_ENTRYPOINT


def _without_judge(task: Path) -> bytes:
    """
    The task's test.sh with its one line that is JUDGE_LINE made NO_JUDGE_LINE,
    ending as that line did, its bytes otherwise unchanged.

    Raises LaneError as `read_test_sh` does, and naming the file when not
    exactly one of its lines is JUDGE_LINE.
    """
    lines = read_test_sh(task, "run its rubric judge on the host")
    at = [number for number, line in enumerate(lines) if line.rstrip(b"\r\n") == JUDGE_LINE]
    if len(at) != 1:
        raise LaneError(
            f"{task / TEST_SH}: has {len(at)} lines that are {JUDGE_LINE.decode()}, so the lane cannot"
            " run its rubric judge on the host; it takes a test.sh with exactly one"
        )
    line = lines[at[0]]
    lines[at[0]] = NO_JUDGE_LINE + line[len(JUDGE_LINE) :]
    return b"".join(lines)


def _without_judge_env(task: Path, text: bytes) -> bytes:
    """
    The task.toml `text` without its lines that are JUDGE_ENV_LINES, its bytes
    otherwise unchanged.

    Raises LaneError naming the file when not exactly one of its lines is any
    one of them, or when the result does not parse to the original without
    those three keys of `[verifier.env]`.
    """
    path = task / "task.toml"
    lines = text.splitlines(keepends=True)
    for old in JUDGE_ENV_LINES:
        found = [number for number, line in enumerate(lines) if line.rstrip(b"\r\n") == old]
        if len(found) != 1:
            raise LaneError(
                f"{path}: has {len(found)} lines that are {old.decode()}, so the lane cannot run"
                " its rubric judge on the host; it takes a task.toml with exactly one"
            )
        del lines[found[0]]
    changed = b"".join(lines)
    expected = tomllib.loads(text.decode())
    verifier = expected.get("verifier")
    env = verifier.get("env") if isinstance(verifier, dict) else None
    if isinstance(env, dict):
        for old in JUDGE_ENV_LINES:
            for key in tomllib.loads(old.decode()):
                env.pop(key, None)
        try:
            if tomllib.loads(changed.decode()) == expected:
                return changed
        except tomllib.TOMLDecodeError:
            pass
    raise LaneError(
        f"{path}: its rubric judge's settings cannot be removed without changing anything else,"
        " so they must be removed by hand"
    )


def _with_header(task: Path) -> bytes:
    """
    The task's evaluate_rubrics.py with its one OPENAI_CALL made COPILOT_CALL,
    its bytes otherwise unchanged.

    Raises LaneError as `read_task_file` does, and naming the file when it
    holds OPENAI_CALL other than once.
    """
    text = read_task_file(task, EVALUATE, "send Copilot's integration header from it")
    calls = text.count(OPENAI_CALL)
    if calls != 1:
        raise LaneError(
            f"{task / EVALUATE}: calls {OPENAI_CALL.decode()} {calls} times, so the lane cannot send"
            " Copilot's integration header from it; it takes a file that calls it once"
        )
    return text.replace(OPENAI_CALL, COPILOT_CALL)
