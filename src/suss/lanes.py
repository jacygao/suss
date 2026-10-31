"""
A lane's directory of links to its task directories in the SWE-bench Pro
checkout, which Harbor reads as `-p lanes/<lane>`, and the checks made on a
lane before any job.
"""

import tomllib
from collections.abc import Iterable
from pathlib import Path

from suss.config import Arm


class LaneError(Exception):
    """A lane cannot be built from the checkout given, or cannot run the arm given."""


# Each lane's owners, or None for the lane listed in v2/hard51_ids.txt.
LANES: dict[str, tuple[str, ...] | None] = {
    "pro-go": ("flipt-io", "gravitational", "future-architect", "navidrome"),
    "pro-python": ("ansible", "internetarchive", "qutebrowser"),
    "pro-js": ("protonmail", "element-hq", "NodeBB", "tutao"),
    "pro-hard51": None,
}


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
    directory = Path(lanes) / (f"{lane}-check" if check else lane)
    directory.mkdir(parents=True, exist_ok=True)
    entries = list(directory.iterdir())
    for entry in entries:
        if not entry.is_symlink():
            raise LaneError(f"{entry}: not a link, so not left by a lane build")
    for entry in entries:
        entry.unlink()
    for task in found:
        (directory / task.name).symlink_to(task)
    return directory


def check_max_seconds(arm: Arm, lane: str, tasks: Iterable[Path]) -> None:
    """
    Refuse an arm whose max_seconds is not under the agent timeout of every task.

    Harbor stops a trial at its agent timeout, before suss can capture the
    patch, so max_seconds must be strictly under the shortest one in the lane.
    An arm with no max_seconds passes. `tasks` are the lane's task directories.
    Raises LaneError naming the task.toml of a task with no [agent] timeout_sec.
    """
    if arm.max_seconds is None:
        return
    shortest = min(_agent_timeout(task) for task in tasks)
    if arm.max_seconds >= shortest:
        raise LaneError(
            f"arm {arm.name} sets max_seconds = {_seconds(arm.max_seconds)}, which must be "
            f"under the {_seconds(shortest)}-second agent timeout of the tasks in {lane}"
        )


def images(tasks: Iterable[Path]) -> list[str]:
    """
    The images the tasks name in `[environment] docker_image`, each once, in
    the order of the first task naming it.

    Raises LaneError naming the task.toml of a task that names none.
    """
    found = {}
    for task in tasks:
        path = task / "task.toml"
        with path.open("rb") as file:
            image = tomllib.load(file).get("environment", {}).get("docker_image")
        if not isinstance(image, str) or not image:
            raise LaneError(f"{path}: no [environment] docker_image, so suss cannot pull its image")
        found[image] = None
    return list(found)


def _agent_timeout(task: Path) -> float:
    path = task / "task.toml"
    with path.open("rb") as file:
        data = tomllib.load(file)
    timeout = data.get("agent", {}).get("timeout_sec")
    if not isinstance(timeout, int | float):
        raise LaneError(f"{path}: no [agent] timeout_sec")
    return timeout


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
