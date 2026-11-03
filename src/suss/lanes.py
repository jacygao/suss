"""
A lane's directory, which Harbor reads as `-p lanes/<lane>`, and the checks
made on a lane before any job.

A SWE-bench Pro lane holds links to its tasks in the checkout. The rebench lane
holds copies of the downloaded tasks, since each task.toml must change.
"""

import re
import shutil
import stat
import tomllib
from collections.abc import Iterable, Mapping
from pathlib import Path

from suss.config import Arm


class LaneError(Exception):
    """A lane cannot be built from the checkout given, or cannot run the arm given."""


MIB = 2**20

# Each lane's owners, or None for the lane listed in v2/hard51_ids.txt.
LANES: dict[str, tuple[str, ...] | None] = {
    "pro-go": ("flipt-io", "gravitational", "future-architect", "navidrome"),
    "pro-python": ("ansible", "internetarchive", "qutebrowser"),
    "pro-js": ("protonmail", "element-hq", "NodeBB", "tutao"),
    "pro-hard51": None,
}

REBENCH = "rebench"

NO_NETWORK = 'network_mode = "no-network"'

# The `[agent]` header as TOML allows it to be spelled, bare-keyed.
AGENT_HEADER = re.compile(r"[ \t]*\[[ \t]*agent[ \t]*\][ \t]*(#.*)?\r?\n?\Z")


def tasks(checkout: Path, lane: str) -> list[Path]:
    """
    The lane's task directories in the checkout, sorted by name.

    Raises LaneError for an unknown lane, a checkout with no v2/tasks/ or no
    v2/hard51_ids.txt, an id in that file with no task, or a lane with no
    tasks at all.
    """
    if lane not in LANES:
        raise LaneError(f"no lane named {lane}; the lanes are {', '.join(LANES)}")
    directory = Path(checkout).resolve() / "v2" / "tasks"
    if not directory.is_dir():
        raise LaneError(f"{directory}: no such directory")
    owners = LANES[lane]
    if owners is None:
        found = _listed(directory, directory.parent / "hard51_ids.txt")
    else:
        found = [
            task for task in directory.iterdir() if task.is_dir() and _owner(task.name) in owners
        ]
    if not found:
        raise LaneError(f"{directory} holds no tasks for lane {lane}")
    return sorted(found, key=lambda task: task.name)


def build(checkout: Path, lane: str, lanes: Path, check: bool = False) -> Path:
    """
    Make `lanes/<lane>/` hold exactly one link to each of the lane's tasks.

    With `check`, it makes `lanes/<lane>-check/` instead, holding only the
    first task by name. Each link is named for its task and points at it by
    absolute path, so it resolves wherever the lane is read from. Links left
    by an earlier build are removed. Raises LaneError as `tasks` does, and
    when the directory holds anything but links, which it leaves in place.
    """
    found = tasks(checkout, lane)
    if check:
        found = found[:1]
    check_buildable(lanes, lane, check)
    directory = _directory(lanes, lane, check)
    directory.mkdir(parents=True, exist_ok=True)
    for entry in directory.iterdir():
        entry.unlink()
    for task in found:
        (directory / task.name).symlink_to(task)
    return directory


def check_buildable(lanes: Path, lane: str, check: bool = False) -> None:
    """
    Refuse a lane whose directory `build` would refuse, changing nothing.

    Raises LaneError when the directory, or the lanes directory holding it, is
    something other than a directory, or when it holds anything but links.
    """
    directory = _directory(lanes, lane, check)
    for path in (Path(lanes), directory):
        # A dangling link does not exist, and still blocks the mkdir.
        if (path.exists() or path.is_symlink()) and not path.is_dir():
            raise LaneError(f"{path}: not a directory, so no lane can be built there")
    if not directory.is_dir():
        return
    for entry in directory.iterdir():
        if not entry.is_symlink():
            raise LaneError(f"{entry}: not a link, so not left by a lane build")


def _directory(lanes: Path, lane: str, check: bool) -> Path:
    return Path(lanes) / (f"{lane}-check" if check else lane)


def read_tasks(tasks: Iterable[Path]) -> dict[Path, dict]:
    """
    Each task directory's task.toml, read once for every check made on it.

    Raises LaneError naming a task.toml that is missing, cannot be read, or
    is not TOML.
    """
    return {task: _read_task(task / "task.toml") for task in tasks}


def check_max_seconds(arm: Arm, lane: str, tasks: Mapping[Path, dict]) -> None:
    """
    Refuse an arm whose max_seconds is not under the agent timeout of every task.

    Harbor stops a trial at its agent timeout, before suss can capture the
    patch, so max_seconds must be strictly under the shortest one in the lane.
    An arm with no max_seconds passes. `tasks` are the lane's, as `read_tasks`
    gives them. Raises LaneError naming a task.toml with no [agent] timeout_sec.
    """
    if arm.max_seconds is None:
        return
    shortest = min(_agent_timeout(task, toml) for task, toml in tasks.items())
    if arm.max_seconds >= shortest:
        raise LaneError(
            f"arm {arm.name} sets max_seconds = {_seconds(arm.max_seconds)}, which must be "
            f"under the {_seconds(shortest)}-second agent timeout of the tasks in {lane}"
        )


def images(tasks: Mapping[Path, dict]) -> list[str]:
    """
    The images the tasks name in `[environment] docker_image`, each once, in
    the order of the first task naming it.

    `tasks` are as `read_tasks` gives them. Raises LaneError naming a
    task.toml that names no image.
    """
    found = {}
    for task, toml in tasks.items():
        image = _field(toml, "environment", "docker_image")
        if not isinstance(image, str) or not image:
            raise LaneError(
                f"{task / 'task.toml'}: no [environment] docker_image, so suss cannot pull its image"
            )
        found[image] = None
    return list(found)


def needs(tasks: Mapping[Path, dict]) -> tuple[int, int]:
    """
    The most CPUs, and the most MiB of memory, that any one of the tasks names
    in `[environment] cpus` and `memory_mb`.

    `tasks` are as `read_tasks` gives them. Raises LaneError naming a
    task.toml where either is missing or is not a positive whole number.
    """
    cpus = max(_need(task, toml, "cpus") for task, toml in tasks.items())
    memory_mb = max(_need(task, toml, "memory_mb") for task, toml in tasks.items())
    return cpus, memory_mb


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


def _agent_timeout(task: Path, toml: dict) -> float:
    timeout = _field(toml, "agent", "timeout_sec")
    if not isinstance(timeout, int | float):
        raise LaneError(f"{task / 'task.toml'}: no [agent] timeout_sec")
    return timeout


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


def rebench_tasks(swe_rebench: Path) -> list[Path]:
    """
    The task directories of the download, sorted by name: each directory in it
    holding a task.toml.

    Raises LaneError when the download is not a directory or holds no task.
    """
    source = Path(swe_rebench).resolve()
    if not source.is_dir():
        raise LaneError(f"{source}: no such directory")
    found = [entry for entry in source.iterdir() if (entry / "task.toml").is_file()]
    if not found:
        raise LaneError(f"{source} holds no tasks: no directory in it has a task.toml")
    return sorted(found, key=lambda task: task.name)


def build_rebench(swe_rebench: Path, lanes: Path, check: bool = False) -> Path:
    """
    Make `lanes/rebench/` hold a copy of each task in the download, whose
    task.toml adds `[agent] network_mode = "no-network"`.

    With `check`, it makes `lanes/rebench-check/` instead, holding only the
    first task by name. Every other file is copied as it is, mode included.
    Whatever an earlier build left is removed. Raises LaneError as
    `rebench_tasks` does, naming a task.toml that already sets the agent's
    network mode or that the key cannot be added to, and when the lane's path
    is a link or anything else but a directory. Every task.toml is read before
    the lane is touched, so an error leaves an earlier build as it was.
    """
    found = rebench_tasks(swe_rebench)
    if check:
        found = found[:1]
    directory = _directory(lanes, REBENCH, check)
    for path in (Path(lanes), directory):
        # A dangling link does not exist, and still blocks the mkdir.
        if (path.exists() or path.is_symlink()) and not path.is_dir():
            raise LaneError(f"{path}: not a directory, so no lane can be built there")
    # A build empties the lane, and must not empty a directory the link names.
    if directory.is_symlink():
        raise LaneError(f"{directory}: a link, so the rebench lane is not built through it")
    tomls = {task: _without_network(task / "task.toml") for task in found}
    if directory.is_dir():
        shutil.rmtree(directory, onexc=_remove_from_read_only)
    directory.mkdir(parents=True)
    for task, toml in tomls.items():
        copy = directory / task.name
        # The task.toml is written fresh rather than over its copy, which may be read-only.
        shutil.copytree(
            task,
            copy,
            symlinks=True,
            ignore=lambda at, names: ["task.toml"] if at == str(task) else [],
        )
        mode = copy.stat().st_mode
        copy.chmod(mode | stat.S_IWUSR)
        (copy / "task.toml").write_bytes(toml)
        shutil.copymode(task / "task.toml", copy / "task.toml")
        copy.chmod(mode)
    return directory


def _remove_from_read_only(function, path: str, error: BaseException) -> None:
    """Retry a removal that a read-only directory, copied from the download, refused."""
    if not isinstance(error, PermissionError):
        raise error
    parent = Path(path).parent
    parent.chmod(parent.stat().st_mode | stat.S_IWUSR)
    function(path)


def _without_network(path: Path) -> bytes:
    """
    The task.toml at `path` with `network_mode = "no-network"` added to its
    `[agent]` table, its text otherwise unchanged.

    The line goes directly under the `[agent]` header, or under a new one at
    the end where there is none. Raises LaneError naming the file when it
    already sets the agent's network mode, or when the result does not parse
    to the original plus that one key.
    """
    original = _read_task(path)
    agent = original.get("agent", {})
    if isinstance(agent, dict) and "network_mode" in agent:
        raise LaneError(
            f"{path}: sets [agent] network_mode = {agent['network_mode']!r}, which the "
            "rebench lane must set to no-network itself"
        )
    lines = path.read_bytes().decode().splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    headers = [number for number, line in enumerate(lines) if AGENT_HEADER.match(line)]
    if len(headers) == 1:
        header = lines[headers[0]]
        # The added line ends as the header does, \r\n or \n.
        lines.insert(headers[0] + 1, NO_NETWORK + header[len(header.rstrip("\r\n")) :])
    else:
        lines.append(f"\n[agent]\n{NO_NETWORK}\n")
    text = "".join(lines)
    if not isinstance(agent, dict):
        agent = {}
    expected = {**original, "agent": {**agent, "network_mode": "no-network"}}
    try:
        added = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        added = None
    # A header inside a multi-line string, or a table made some other way,
    # puts the line where it changes something else, or breaks the file.
    if added != expected:
        raise LaneError(
            f"{path}: [agent] network_mode cannot be added to it without changing "
            "anything else, so it must be added by hand"
        )
    return text.encode()


def _seconds(value: float) -> str:
    # A TOML float such as 3000.0 prints as 3000, and a fraction as it is.
    if float(value).is_integer():
        return str(int(value))
    return str(value)


def _owner(name: str) -> str | None:
    # The owner is everything up to the first `__`; a prefix match would let
    # one owner take the tasks of another whose name starts with it.
    rest = name.removeprefix("instance_")
    if rest == name or "__" not in rest:
        return None
    return rest.split("__", 1)[0]


def _listed(directory: Path, ids: Path) -> list[Path]:
    """The tasks in `directory` named by the lines of `ids`, which may carry blank lines and surrounding space."""
    try:
        lines = ids.read_text().splitlines()
    except FileNotFoundError:
        raise LaneError(f"{ids}: no such file") from None
    found = []
    for line in lines:
        name = line.strip()
        if not name:
            continue
        task = directory / name
        if not task.is_dir():
            raise LaneError(f"{ids} lists {name}, which has no task in {directory}")
        found.append(task)
    return found
