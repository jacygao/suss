"""
What the atlas-rf lane changes in each task's copy, beyond what every lane does.

Each copy's rubric judge calls Copilot's API with the Copilot token from .env,
on the model `swe-drill run` gives its verifier as EVAL_MODEL, so grading needs
no OpenAI or Anthropic key. Its test.sh gives that token to the judge alone,
and to no test, since the tests run code the agent wrote.
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

# The variable `swe-drill run` gives the verifier the judge's model in.
EVAL_MODEL = "EVAL_MODEL"
# The lines of every SWE Atlas task's [verifier.env], and the copy's in their
# place. The model has no default, so a job not given it stops before any
# trial, rather than calling a model Copilot does not serve.
VERIFIER_ENV_LINES = (
    (b'EVAL_API_KEY = "${OPENAI_API_KEY}"', b'EVAL_API_KEY = "${COPILOT_GITHUB_TOKEN}"'),
    (
        b'EVAL_BASE_URL = "${OPENAI_API_BASE}"',
        b'EVAL_BASE_URL = "https://api.enterprise.githubcopilot.com"',
    ),
    (
        b'EVAL_MODEL = "${EVAL_MODEL:-anthropic/claude-opus-4-5-20251101}"',
        b'EVAL_MODEL = "${EVAL_MODEL}"',
    ),
)


# The line of every SWE Atlas test.sh that its first command follows, and the
# line the copy adds after it, so that no command the tests start inherits the key.
SET_LINE = b"set -uo pipefail"
HIDE_KEY = b"_judge_key=${EVAL_API_KEY-}; unset EVAL_API_KEY"
# The line that runs the judge, and the copy's in its place, which gives the key back to it alone.
JUDGE_LINE = b'python3 /tests/evaluate_rubrics.py || echo "evaluate_rubrics.py exited non-zero"'
KEYED_JUDGE_LINE = b'EVAL_API_KEY="$_judge_key" ' + JUDGE_LINE


def rewrite(task: Path, toml: bytes) -> dict[str, bytes]:
    """
    The files the task's copy writes, by path from the task: its task.toml
    `toml` with the judge pointed at Copilot, as `_judge_on_copilot` gives it,
    its tests/evaluate_rubrics.py sending Copilot's header, as `_with_header`
    gives it, and its tests/test.sh giving the key to the judge alone, as
    `_key_to_judge_alone` gives it. A task in BASH_ENTRYPOINT also gets
    NO_ENTRYPOINT as its environment/docker-compose.yaml, as
    `_without_entrypoint` gives it.

    Raises LaneError as each of them does.
    """
    files = {
        "task.toml": _judge_on_copilot(task, toml),
        EVALUATE: _with_header(task),
        TEST_SH: _key_to_judge_alone(task),
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


def _key_to_judge_alone(task: Path) -> bytes:
    """
    The task's test.sh with HIDE_KEY inserted after its one line that is
    SET_LINE, and its one line that is JUDGE_LINE made KEYED_JUDGE_LINE, each
    line ending as the line it follows or replaces, its bytes otherwise
    unchanged.

    Raises LaneError as `read_test_sh` does, and naming the file when not
    exactly one of its lines is either.
    """
    lines = read_test_sh(task, "keep the judge's key from the tests")
    found = {}
    for anchor in (SET_LINE, JUDGE_LINE):
        at = [number for number, line in enumerate(lines) if line.rstrip(b"\r\n") == anchor]
        if len(at) != 1:
            raise LaneError(
                f"{task / TEST_SH}: has {len(at)} lines that are {anchor.decode()}, so the lane cannot"
                " keep the judge's key from the tests; it takes a test.sh with exactly one"
            )
        found[anchor] = at[0]
    judge = lines[found[JUDGE_LINE]]
    lines[found[JUDGE_LINE]] = KEYED_JUDGE_LINE + judge[len(JUDGE_LINE) :]
    line = lines[found[SET_LINE]]
    lines.insert(found[SET_LINE] + 1, HIDE_KEY + (line[len(SET_LINE) :] or b"\n"))
    return b"".join(lines)


def _judge_on_copilot(task: Path, text: bytes) -> bytes:
    """
    The task.toml `text` with each line of VERIFIER_ENV_LINES made its
    replacement, its bytes otherwise unchanged.

    Raises LaneError naming the file when not exactly one of its lines is any
    one of them, or when the result does not parse to the original with those
    three values of `[verifier.env]` changed.
    """
    path = task / "task.toml"
    lines = text.splitlines(keepends=True)
    for old, new in VERIFIER_ENV_LINES:
        found = [number for number, line in enumerate(lines) if line.rstrip(b"\r\n") == old]
        if len(found) != 1:
            raise LaneError(
                f"{path}: has {len(found)} lines that are {old.decode()}, so the lane cannot point"
                " its rubric judge at Copilot; it takes a task.toml with exactly one"
            )
        line = lines[found[0]]
        lines[found[0]] = new + line[len(old) :]
    changed = b"".join(lines)
    expected = tomllib.loads(text.decode())
    verifier = expected.get("verifier")
    env = verifier.get("env") if isinstance(verifier, dict) else None
    if isinstance(env, dict):
        for _, new in VERIFIER_ENV_LINES:
            env.update(tomllib.loads(new.decode()))
        try:
            if tomllib.loads(changed.decode()) == expected:
                return changed
        except tomllib.TOMLDecodeError:
            pass
    raise LaneError(
        f"{path}: its rubric judge's settings cannot be changed without changing anything else,"
        " so they must be changed by hand"
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
