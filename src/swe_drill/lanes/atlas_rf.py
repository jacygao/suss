"""
What the atlas-rf lane changes in each task's copy, beyond what every lane does.

Each copy's rubric judge calls Copilot's API with the Copilot token from .env,
on the model `swe-drill run` gives its verifier as EVAL_MODEL, so grading needs
no OpenAI or Anthropic key.
"""

import tomllib
from pathlib import Path

from swe_drill.lanes.edits import LaneError, read_task_file

# Every SWE Atlas Refactoring task can be graded.
UNGRADEABLE: tuple[str, ...] = ()

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


def rewrite(task: Path, toml: bytes) -> dict[str, bytes]:
    """
    The files the task's copy writes, by path from the task: its task.toml
    `toml` with the judge pointed at Copilot, as `_judge_on_copilot` gives it,
    and its tests/evaluate_rubrics.py sending Copilot's header, as
    `_with_header` gives it.

    Raises LaneError as each of them does.
    """
    return {"task.toml": _judge_on_copilot(task, toml), EVALUATE: _with_header(task)}


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
