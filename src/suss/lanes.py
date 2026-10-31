"""A lane's directory of links to its task directories in the SWE-bench Pro checkout, which Harbor reads as `-p lanes/<lane>`."""

from pathlib import Path


class LaneError(Exception):
    """A lane cannot be built from the checkout given."""


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
