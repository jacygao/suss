"""
A lane's directory, which Harbor reads as `-p lanes/<lane>`, and the checks
made on a lane before any job.

Each lane holds copies of the downloaded tasks, since a task.toml that leaves
the agent's network open must close it, a rebench task must grade in a
separate verifier, and a senior task's test.sh must pin litellm.
"""

import re
import shutil
import stat
import tomllib
from collections.abc import Iterable, Mapping
from pathlib import Path

from harbor.models.task.config import EnvironmentConfig


class LaneError(Exception):
    """A lane cannot be built from the tasks given, or cannot run the arm given."""


MIB = 2**20

REBENCH = "rebench"
SENIOR = "senior"

LANES = (REBENCH, SENIOR)

# The lanes whose trials Harbor grades in a sandbox of their own, in the agent job.
SEPARATE_VERIFIER = (REBENCH, SENIOR)

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


def check_lane(lane: str) -> None:
    """Raise LaneError, naming the lanes, for a lane suss does not have."""
    if lane not in LANES:
        raise LaneError(f"suss has no lane named {lane}; its lanes are {', '.join(LANES)}")


def _directory(lanes: Path, lane: str, check: bool) -> Path:
    return Path(lanes) / (f"{lane}-check" if check else lane)


def read_tasks(tasks: Iterable[Path]) -> dict[Path, dict]:
    """
    Each task directory's task.toml, read once for every check made on it.

    Raises LaneError naming a task.toml that is missing, cannot be read, or
    is not TOML.
    """
    return {task: _read_task(task / "task.toml") for task in tasks}


def needs(tasks: Mapping[Path, dict]) -> tuple[int, int]:
    """
    The most CPUs, and the most MiB of memory, that any one of the tasks names
    in `[environment] cpus` and `memory_mb`.

    `tasks` are as `read_tasks` gives them. Raises LaneError naming a
    task.toml where either is missing or is not a positive whole number.
    """
    cpus = max(_need(task, toml, "cpus") for task, toml in tasks.items())
    memory_mb = max(_memory_mb(task, toml) for task, toml in tasks.items())
    return cpus, memory_mb


def _memory_mb(task: Path, toml: dict) -> int:
    """The task's `memory_mb`, else its `memory`, such as "8G", in MiB as Harbor reads it."""
    memory = _field(toml, "environment", "memory")
    if memory is None or _field(toml, "environment", "memory_mb") is not None:
        return _need(task, toml, "memory_mb")
    # Harbor ignores a memory that is not a string, and refuses one it cannot parse.
    found = 0
    if isinstance(memory, str):
        try:
            found = EnvironmentConfig._parse_size_to_mb(memory)
        except ValueError:
            pass
    if found < 1:
        raise LaneError(
            f"{task / 'task.toml'}: [environment] memory is {memory!r}, not a size Harbor "
            'reads, such as "8G"'
        )
    return found


def check_capacity(
    trials: str,
    trial_needs: tuple[int, int],
    concurrency: int,
    docker: tuple[int, int],
) -> None:
    """
    Refuse a lane whose `concurrency` trials at once need more than Docker has.

    `trial_needs` is the most CPUs and MiB any one trial takes, as `needs`
    gives it for the tasks alone, and `trials` names those trials in the
    error. Each trial is counted at the largest need, since any trial may be
    running the largest task. `docker` is the engine's CPUs and its memory in
    bytes. The error gives the largest concurrency that fits, or says that a
    single trial does not.
    """
    cpus, memory_mb = trial_needs
    docker_cpus, docker_bytes = docker
    fits = min(docker_cpus // cpus, docker_bytes // (memory_mb * MIB))
    if concurrency <= fits:
        return
    # Rounded down, so that it never claims more memory than Docker has.
    has = f"Docker has {_cpus(docker_cpus)} and {docker_bytes // MIB} MiB"
    if fits == 0:
        raise LaneError(
            f"one trial of {trials} needs {_cpus(cpus)} and {memory_mb} MiB, but {has}; "
            "give Docker more"
        )
    raise LaneError(
        f"concurrency = {concurrency} needs {_cpus(concurrency * cpus)} and "
        f"{concurrency * memory_mb} MiB for {trials}, but {has}; "
        f"set concurrency to {fits} or less, or give Docker more"
    )


def _cpus(count: int) -> str:
    return f"{count} CPU" if count == 1 else f"{count} CPUs"


def _need(task: Path, toml: dict, key: str) -> int:
    value = _field(toml, "environment", key)
    if value is None:
        raise LaneError(
            f"{task / 'task.toml'}: no [environment] {key}, so suss cannot tell how many "
            "of its trials fit in Docker at once"
        )
    # A bool is an int to Python, and true would count as 1.
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise LaneError(
            f"{task / 'task.toml'}: [environment] {key} is {value!r}, not a positive whole number"
        )
    return value


def _field(toml: dict, table: str, key: str) -> object:
    """The task.toml's `key` in `[table]`, or None where either is missing or `table` is not a table."""
    found = toml.get(table)
    return found.get(key) if isinstance(found, dict) else None


def _read_task(path: Path) -> dict:
    """Read a task.toml, raising LaneError naming it when it cannot be read or is not TOML."""
    try:
        with path.open("rb") as file:
            return tomllib.load(file)
    except FileNotFoundError:
        raise LaneError(f"{path}: no such file") from None
    except OSError as error:
        raise LaneError(f"{path}: cannot be read: {error.strerror}") from None
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as error:
        raise LaneError(f"{path}: not valid TOML: {error}") from None


def download_tasks(download: Path) -> list[Path]:
    """
    The task directories of the download, sorted by name: each directory in it
    holding a task.toml.

    Raises LaneError when the download is not a directory or holds no task.
    """
    source = Path(download).resolve()
    if not source.is_dir():
        raise LaneError(f"{source}: no such directory")
    found = [entry for entry in source.iterdir() if (entry / "task.toml").is_file()]
    if not found:
        raise LaneError(f"{source} holds no tasks: no directory in it has a task.toml")
    return sorted(found, key=lambda task: task.name)


# Their builds need Maven or Gradle artifacts that no longer exist, so no fix passes their tests.
UNGRADEABLE = (
    "langchain4j__langchain4j-5315",
    "langchain4j__langchain4j-5367",
    "openrewrite__rewrite-7784",
)


def lane_tasks(lane: str, download: Path) -> list[Path]:
    """
    The tasks of the download that `lane` takes, sorted by name: on rebench,
    each but those in UNGRADEABLE, and on senior, every one. A download
    lacking any of UNGRADEABLE is not an error.

    Raises LaneError as `download_tasks` does, and when the download holds no
    other task.
    """
    found = download_tasks(download)
    if lane == REBENCH:
        found = [task for task in found if task.name not in UNGRADEABLE]
    if not found:
        source = Path(download).resolve()
        raise LaneError(
            f"{source}: every task in it is one the {lane} lane leaves out, so the lane "
            "would be empty"
        )
    return found


def check_buildable(lane: str, download: Path, lanes: Path, check: bool = False) -> None:
    """Refuse a lane that `build` would refuse, changing nothing."""
    _plan_build(lane, download, lanes, check)


def build(lane: str, download: Path, lanes: Path, check: bool = False) -> Path:
    """
    Make `lanes/<lane>/` hold a copy of each task `lane_tasks` gives, whose
    task.toml adds `[agent] network_mode = "no-network"` unless its agent's
    network is already closed. On a lane in SEPARATE_VERIFIER, the task.toml
    also sets `[verifier] environment_mode = "separate"`, as `_separate` gives
    it. On senior, each
    copy's verifier timeout gains JUDGE_MARGIN_SECONDS, as `_judge_room` gives
    it, and its tests/test.sh pins litellm, as `_pinned` gives it.

    With `check`, it makes `lanes/<lane>-check/` instead, holding only the
    first task by name. Every other file is copied as it is, mode included.
    Whatever an earlier build left is removed. Raises LaneError as
    `lane_tasks` does, naming a task.toml whose agent network `_without_network`
    refuses or that a key cannot be added to, naming what `_separate`,
    `_judge_room` and `_pinned` refuse, naming a senior tests/ that is a link,
    and when the lane's path is a link or anything else but a
    directory. Every file it rewrites is read before the lane is touched, so
    an error leaves an earlier build as it was.
    """
    directory, rewritten = _plan_build(lane, download, lanes, check)
    if directory.is_dir():
        shutil.rmtree(directory, onexc=_remove_from_read_only)
    directory.mkdir(parents=True)
    for task, files in rewritten.items():
        copy = directory / task.name
        skipped = {str(task / name) for name in files}
        # Each rewritten file is written fresh rather than over its copy, which may be read-only.
        shutil.copytree(
            task,
            copy,
            symlinks=True,
            ignore=lambda at, names: [n for n in names if str(Path(at) / n) in skipped],
        )
        for name, (content, source) in files.items():
            _write(copy, name, content, source)
    return directory


def _write(copy: Path, name: str, content: bytes, source: Path) -> None:
    """
    Write `content` at `name` in the task's copy, with the mode of `source`.

    A directory copied read-only from the download is made writable for the
    write alone, and keeps its own mode.
    """
    directory = (copy / name).parent
    mode = directory.stat().st_mode
    directory.chmod(mode | stat.S_IWUSR)
    try:
        (copy / name).write_bytes(content)
        shutil.copymode(source, copy / name)
    finally:
        directory.chmod(mode)


def _plan_build(
    lane: str, download: Path, lanes: Path, check: bool
) -> tuple[Path, dict[Path, dict[str, tuple[bytes, Path]]]]:
    """
    The lane's directory, and each task it takes with the files its copy
    writes, by path from the task, each with its content and the file whose
    mode it takes. It reads everything and changes nothing.

    Raises LaneError as `build` does.
    """
    found = lane_tasks(lane, download)
    if check:
        found = found[:1]
    directory = _directory(lanes, lane, check)
    for path in (Path(lanes), directory):
        # A dangling link does not exist, and still blocks the mkdir.
        if (path.exists() or path.is_symlink()) and not path.is_dir():
            raise LaneError(f"{path}: not a directory, so no lane can be built there")
    # A build empties the lane, and must not empty a directory the link names.
    if directory.is_symlink():
        raise LaneError(f"{directory}: a link, so the {lane} lane is not built through it")
    rewritten = {}
    for task in found:
        toml = _without_network(task / "task.toml")
        if lane in SEPARATE_VERIFIER:
            toml = _separate(task / "task.toml", toml)
        if lane == SENIOR:
            toml = _judge_room(task / "task.toml", toml)
        rewritten[task] = {"task.toml": (toml, task / "task.toml")}
        if lane == SENIOR:
            # The copy keeps the link, so a file written into tests/ would be written through it.
            tests = (task / TEST_SH).parent
            if tests.is_symlink():
                raise LaneError(f"{tests}: a link, so the lane cannot write into a copy of it")
            rewritten[task][TEST_SH] = (_pinned(task / TEST_SH), task / TEST_SH)
    return directory, rewritten


TEST_SH = "tests/test.sh"
# The requirement every senior task's test.sh installs, and what the lane's copy
# installs in its place: every litellm from 1.106.0 fails each judge's call.
LITELLM = b"litellm>=1.0,<2.0"
PINNED_LITELLM = b"litellm>=1.0,<1.106"


def _pinned(path: Path) -> bytes:
    """
    The test.sh at `path` with its one line that is exactly LITELLM made
    PINNED_LITELLM, its bytes otherwise unchanged.

    Raises LaneError naming the file when it cannot be read, or when not
    exactly one of its lines is LITELLM.
    """
    try:
        lines = path.read_bytes().splitlines(keepends=True)
    except FileNotFoundError:
        raise LaneError(f"{path}: no such file, so the lane cannot pin litellm in it") from None
    except OSError as error:
        raise LaneError(f"{path}: cannot be read: {error.strerror}") from None
    found = [number for number, line in enumerate(lines) if line.rstrip(b"\r\n") == LITELLM]
    if len(found) != 1:
        raise LaneError(
            f"{path}: has {len(found)} lines that are {LITELLM.decode()}, so the lane cannot "
            f"pin litellm to {PINNED_LITELLM.decode()}; it takes a test.sh with exactly one"
        )
    line = lines[found[0]]
    lines[found[0]] = PINNED_LITELLM + line[len(LITELLM) :]
    return b"".join(lines)


def _remove_from_read_only(function, path: str, error: BaseException) -> None:
    """Retry a removal that a read-only directory, copied from the download, refused."""
    if not isinstance(error, PermissionError):
        raise error
    parent = Path(path).parent
    parent.chmod(parent.stat().st_mode | stat.S_IWUSR)
    function(path)


# The agent network modes under which Harbor opens, in each trial, only the
# task's own allowed_hosts and the job's --allow-agent-host hosts.
CLOSED = ("no-network", "allowlist")


def _without_network(path: Path) -> bytes:
    """
    The task.toml at `path` with `network_mode = "no-network"` added to its
    `[agent]` table, its text otherwise unchanged. One whose `[agent]` already
    sets a mode in CLOSED is returned as it is.

    The line goes directly under the `[agent]` header, or under a new one at
    the end where there is none. Raises LaneError naming the file when it
    sets any other agent network mode, sets `[agent] allowed_hosts` with no
    mode, or when the result does not parse to the original plus that one
    key.
    """
    original = _read_task(path)
    agent = original.get("agent", {})
    if isinstance(agent, dict) and agent.get("network_mode") in CLOSED:
        return path.read_bytes()
    if isinstance(agent, dict) and "network_mode" in agent:
        raise LaneError(
            f"{path}: sets [agent] network_mode = {agent['network_mode']!r}, under which "
            "Harbor opens every host to the agent; the lane takes only no-network or allowlist"
        )
    if isinstance(agent, dict) and "allowed_hosts" in agent:
        raise LaneError(
            f"{path}: sets [agent] allowed_hosts with no network_mode, which Harbor "
            'refuses; it takes them only with network_mode = "allowlist"'
        )
    return _add_key(path, path.read_bytes(), original, "agent", "network_mode", "no-network")


def _separate(path: Path, text: bytes) -> bytes:
    """
    The task.toml `text`, read from `path`, with `environment_mode =
    "separate"` added to its `[verifier]` table, so that Harbor grades each
    trial in a sandbox of its own. One that already sets it is returned as
    it is.

    Raises LaneError naming the file when its verifier sets another mode, or
    an environment of its own, which ReplayVerifier does not grade in, and as
    `_add_key` does.
    """
    original = tomllib.loads(text.decode())
    verifier = original.get("verifier", {})
    if not isinstance(verifier, dict):
        verifier = {}
    if "environment" in verifier:
        raise LaneError(
            f"{path}: sets [verifier] environment, but the lane grades its trials in a"
            " sandbox built from the task's own environment"
        )
    mode = verifier.get("environment_mode")
    if mode == "separate":
        return text
    if mode is not None:
        raise LaneError(
            f"{path}: sets [verifier] environment_mode = {mode!r}, but the lane grades its"
            ' trials in a separate verifier; it takes only "separate"'
        )
    return _add_key(path, text, original, "verifier", "environment_mode", "separate")


def _judge_room(path: Path, text: bytes) -> bytes:
    """
    The task.toml `text`, read from `path`, with its `[verifier] timeout_sec`,
    or Harbor's default where it sets none, raised by JUDGE_MARGIN_SECONDS.

    Raises LaneError naming the file when the timeout is not a positive
    number, and as `_add_key` and `_replace_key` do.
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
        return _replace_key(path, text, original, "verifier", "timeout_sec", raised)
    return _add_key(path, text, original, "verifier", "timeout_sec", raised)


def _header(table: str) -> re.Pattern:
    """The table's header as TOML allows it to be spelled, bare-keyed."""
    return re.compile(rf"[ \t]*\[[ \t]*{table}[ \t]*\][ \t]*(#.*)?\r?\n?\Z")


def _replace_key(
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


def _add_key(
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
