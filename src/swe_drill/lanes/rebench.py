"""
What the rebench lane leaves out of its download, and changes in each task's
copy beyond what every lane does.

Each copy's tests/test.sh installs Python through apt before it parses the
test log. On an image with no python3, the task's own script runs its parser
with uv, which downloads a Python from GitHub on every grade and fails the
grade when GitHub does not answer.
"""

from pathlib import Path

from swe_drill.lanes.edits import TEST_SH, LaneError, read_test_sh

# Their builds need Maven or Gradle artifacts that no longer exist, so no fix passes their tests.
UNGRADEABLE = (
    "langchain4j__langchain4j-5315",
    "langchain4j__langchain4j-5367",
    "openrewrite__rewrite-7784",
)

# The line of every rebench test.sh that starts choosing what runs its parser.
PARSER = b"if command -v python3 >/dev/null 2>&1; then"
# What the lane's copy runs just before it. Where apt is missing or fails,
# the script falls back to uv as the task's own does.
INSTALL_PYTHON = (
    b"# Installed through apt, so that uv never downloads a Python from GitHub for the parser.",
    b"command -v python3 >/dev/null 2>&1 || { apt-get update -qq && DEBIAN_FRONTEND=noninteractive"
    b" apt-get install -y -qq --no-install-recommends python3; }",
)


def rewrite(task: Path, toml: bytes) -> dict[str, bytes]:
    """
    The files the task's copy writes, by path from the task: its tests/test.sh
    with Python installed before its parser runs, as `_with_python` gives it.
    Its task.toml is left as every lane makes it.

    Raises LaneError as `_with_python` does.
    """
    return {TEST_SH: _with_python(task)}


def _with_python(task: Path) -> bytes:
    """
    The task's test.sh with INSTALL_PYTHON inserted above its one line that is
    exactly PARSER, each inserted line ending as that line does, its bytes
    otherwise unchanged.

    Raises LaneError as `read_test_sh` does, and naming the file when not
    exactly one of its lines is PARSER.
    """
    lines = read_test_sh(task, "install Python in it")
    found = [number for number, line in enumerate(lines) if line.rstrip(b"\r\n") == PARSER]
    if len(found) != 1:
        raise LaneError(
            f"{task / TEST_SH}: has {len(found)} lines that are {PARSER.decode()!r}, so the lane "
            "cannot install Python before its parser runs; it takes a test.sh with exactly one"
        )
    line = lines[found[0]]
    newline = line[len(line.rstrip(b"\r\n")) :] or b"\n"
    lines[found[0] : found[0]] = [added + newline for added in INSTALL_PYTHON]
    return b"".join(lines)
