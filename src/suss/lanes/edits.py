"""
Changing a task's files for its copy in a lane, and nothing else in them.

Every edit here either changes exactly what it names or raises LaneError
naming the file, so a lane never holds a copy changed in a way nobody chose.
"""

import re
import tomllib
from pathlib import Path


class LaneError(Exception):
    """A lane cannot be built from the tasks given, or cannot run the arm given."""


TEST_SH = "tests/test.sh"


def read_test_sh(task: Path, doing: str) -> list[bytes]:
    """
    The lines of the task's tests/test.sh, each with its line ending.

    `doing` says what the lane would do to it, such as "pin litellm in it",
    for the errors. Raises LaneError naming the file when it is missing or
    cannot be read, and naming tests/ when it is a link, since the copy keeps
    the link and a file written into it would be written through it.
    """
    path = task / TEST_SH
    if path.parent.is_symlink():
        raise LaneError(f"{path.parent}: a link, so the lane cannot write into a copy of it")
    try:
        return path.read_bytes().splitlines(keepends=True)
    except FileNotFoundError:
        raise LaneError(f"{path}: no such file, so the lane cannot {doing}") from None
    except OSError as error:
        raise LaneError(f"{path}: cannot be read: {error.strerror}") from None


def _header(table: str) -> re.Pattern:
    """The table's header as TOML allows it to be spelled, bare-keyed."""
    return re.compile(rf"[ \t]*\[[ \t]*{table}[ \t]*\][ \t]*(#.*)?\r?\n?\Z")


def replace_key(
    path: Path, text: bytes, original: dict, table: str, key: str, value: float
) -> bytes:
    """
    The task.toml `text`, read from `path` as `original`, with the line that
    sets `key` in its `[table]` made `key = value`, its text otherwise
    unchanged.

    Raises LaneError naming the file when no one line sets it, or when the
    result does not parse to the original with that one value changed.
    """
    lines = text.decode().splitlines(keepends=True)
    headers = [number for number, found in enumerate(lines) if _header(table).match(found)]
    setting = re.compile(rf"[ \t]*{key}[ \t]*=")
    found = []
    if len(headers) == 1:
        for number in range(headers[0] + 1, len(lines)):
            if lines[number].lstrip().startswith("["):
                break
            if setting.match(lines[number]):
                found.append(number)
    expected = {**original, table: {**original[table], key: value}}
    if len(found) == 1:
        line = lines[found[0]]
        lines[found[0]] = f"{key} = {value!r}" + line[len(line.rstrip("\r\n")) :]
        changed = "".join(lines)
        try:
            if tomllib.loads(changed) == expected:
                return changed.encode()
        except tomllib.TOMLDecodeError:
            pass
    raise LaneError(
        f"{path}: [{table}] {key} cannot be changed without changing anything else,"
        " so it must be changed by hand"
    )


def add_key(
    path: Path, text: bytes, original: dict, table: str, key: str, value: str | float
) -> bytes:
    """
    The task.toml `text`, read from `path` as `original`, with `key = value`
    added to its `[table]`, its text otherwise unchanged. A string value is
    quoted.

    The line goes directly under the table's header, or under a new one at
    the end where there is none. Raises LaneError naming the file when the
    result does not parse to the original plus that one key.
    """
    line = f'{key} = "{value}"' if isinstance(value, str) else f"{key} = {value!r}"
    lines = text.decode().splitlines(keepends=True)
    headers = [number for number, found in enumerate(lines) if _header(table).match(found)]
    if len(headers) == 1:
        header = lines[headers[0]]
        # The added line ends as the header does, \r\n or \n, and a header
        # ending the file without one gains one.
        newline = header[len(header.rstrip("\r\n")) :]
        if not newline:
            lines[headers[0]] += "\n"
            newline = "\n"
        lines.insert(headers[0] + 1, line + newline)
    else:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        lines.append(f"\n[{table}]\n{line}\n")
    added_text = "".join(lines)
    existing = original.get(table, {})
    if not isinstance(existing, dict):
        existing = {}
    expected = {**original, table: {**existing, key: value}}
    try:
        added = tomllib.loads(added_text)
    except tomllib.TOMLDecodeError:
        added = None
    # A header inside a multi-line string, or a table made some other way,
    # puts the line where it changes something else, or breaks the file.
    if added != expected:
        raise LaneError(
            f"{path}: [{table}] {key} cannot be added to it without changing "
            "anything else, so it must be added by hand"
        )
    return added_text.encode()
