import os
import re
from pathlib import Path

import pytest

from suss import lanes
from suss.lanes import LaneError

SPEC = Path(__file__).resolve().parent.parent / "docs" / "specs" / "suss-spec.md"

# One task for each owner in the spec's lanes, by name.
OWNER_TASKS = {
    "pro-go": [
        "instance_flipt-io__flipt-0a1b",
        "instance_gravitational__teleport-1c2d",
        "instance_future-architect__vuls-2e3f",
        "instance_navidrome__navidrome-3a4b",
    ],
    "pro-python": [
        "instance_ansible__ansible-4c5d",
        "instance_internetarchive__openlibrary-5e6f",
        "instance_qutebrowser__qutebrowser-6a7b",
    ],
    "pro-js": [
        "instance_protonmail__webclients-7c8d",
        "instance_element-hq__element-web-8e9f",
        "instance_NodeBB__NodeBB-9a0b",
        "instance_tutao__tutanota-0c1d",
    ],
}

# Tasks no owner lane may take. Each one passes a match that skips the `__`
# boundary or ignores case.
DECOYS = [
    "instance_navidrome-fork__navidrome-1111",
    "instance_ansiblex__ansible-2222",
    "instance_nodebb__NodeBB-3333",
    "instance_other__flipt-io-4444",
    "instance_other__ansible__x-5555",
]

ALL_TASKS = [name for names in OWNER_TASKS.values() for name in names] + DECOYS


def make_checkout(root: Path, names: list[str], hard51: str | None = None) -> Path:
    tasks = root / "v2" / "tasks"
    for name in names:
        (tasks / name).mkdir(parents=True)
        (tasks / name / "task.toml").write_text(f'# {name}\n')
    if hard51 is not None:
        (root / "v2" / "hard51_ids.txt").write_text(hard51)
    return root


def links(lane: Path) -> dict[str, str]:
    """Each entry in a lane directory, by name, with the target of its link."""
    return {entry.name: os.readlink(entry) for entry in lane.iterdir()}


def assert_resolves_to_tasks(lane: Path, checkout: Path):
    assert any(lane.iterdir()), f"{lane} is empty"
    for entry in lane.iterdir():
        assert entry.is_symlink(), f"{entry} is not a link"
        assert (entry / "task.toml").is_file(), f"{entry} does not resolve to a task"
        assert entry.resolve() == (checkout / "v2" / "tasks" / entry.name).resolve()


def test_the_lanes_and_their_owners_are_the_specs():
    rows = re.findall(r"^\| `(pro-[a-z0-9]+)` \| \d+ \| (.*) \|$", SPEC.read_text(), re.MULTILINE)
    spec = {
        lane: (None if "hard51_ids.txt" in owners else set(re.findall(r"`([^`]+)`", owners)))
        for lane, owners in rows
    }

    assert spec == {
        lane: (None if owners is None else set(owners)) for lane, owners in lanes.LANES.items()
    }


@pytest.mark.parametrize("lane", sorted(OWNER_TASKS))
def test_an_owner_lane_takes_exactly_the_tasks_of_its_owners(tmp_path: Path, lane: str):
    checkout = make_checkout(tmp_path / "checkout", ALL_TASKS)

    built = lanes.build(checkout, lane, tmp_path / "lanes")

    assert built == tmp_path / "lanes" / lane
    assert sorted(links(built)) == sorted(OWNER_TASKS[lane])


def test_hard51_takes_the_listed_tasks_ignoring_blank_lines_and_surrounding_space(
    tmp_path: Path,
):
    listed = ["instance_ansible__ansible-4c5d", "instance_NodeBB__NodeBB-9a0b"]
    checkout = make_checkout(
        tmp_path / "checkout",
        ALL_TASKS,
        hard51=f"{listed[0]}  \n\n  {listed[1]}\r\n\n",
    )

    built = lanes.build(checkout, "pro-hard51", tmp_path / "lanes")

    assert sorted(links(built)) == sorted(listed)


def test_a_hard51_id_with_no_task_is_an_error_naming_it(tmp_path: Path):
    checkout = make_checkout(
        tmp_path / "checkout",
        ALL_TASKS,
        hard51="instance_ansible__ansible-4c5d\ninstance_ansible__ansible-ffff\n",
    )

    with pytest.raises(LaneError, match="instance_ansible__ansible-ffff"):
        lanes.build(checkout, "pro-hard51", tmp_path / "lanes")


def test_a_checkout_without_hard51_ids_is_an_error_naming_the_file(tmp_path: Path):
    checkout = make_checkout(tmp_path / "checkout", ALL_TASKS)

    with pytest.raises(LaneError, match=re.escape(str(checkout / "v2" / "hard51_ids.txt"))):
        lanes.build(checkout, "pro-hard51", tmp_path / "lanes")


def test_every_link_resolves_to_its_task_from_any_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    checkout = make_checkout(tmp_path / "checkout", ALL_TASKS)
    run = tmp_path / "run"
    run.mkdir()
    # suss run passes paths relative to the directory it runs in.
    monkeypatch.chdir(run)
    lanes.build(Path("../checkout"), "pro-go", Path("lanes"))

    assert_resolves_to_tasks(Path("lanes/pro-go"), checkout)
    monkeypatch.chdir(tmp_path)
    assert_resolves_to_tasks(run / "lanes" / "pro-go", checkout)


def test_a_rebuild_leaves_only_the_links_of_the_latest_build(tmp_path: Path):
    first = make_checkout(tmp_path / "first", ["instance_flipt-io__flipt-aaaa", "instance_navidrome__navidrome-bbbb"])
    second = make_checkout(tmp_path / "second", ["instance_flipt-io__flipt-cccc"])
    lanes.build(first, "pro-go", tmp_path / "lanes")

    built = lanes.build(second, "pro-go", tmp_path / "lanes")

    assert sorted(links(built)) == ["instance_flipt-io__flipt-cccc"]
    assert_resolves_to_tasks(built, second)


def test_a_lane_directory_holding_anything_but_links_is_an_error_and_left_alone(
    tmp_path: Path,
):
    checkout = make_checkout(tmp_path / "checkout", ALL_TASKS)
    lane = tmp_path / "lanes" / "pro-go"
    lane.mkdir(parents=True)
    (lane / "notes.txt").write_text("mine\n")

    with pytest.raises(LaneError, match=re.escape(str(lane / "notes.txt"))):
        lanes.build(checkout, "pro-go", tmp_path / "lanes")
    assert (lane / "notes.txt").read_text() == "mine\n"


def test_the_check_lane_holds_only_the_first_task_by_name(tmp_path: Path):
    checkout = make_checkout(tmp_path / "checkout", ALL_TASKS)

    built = lanes.build(checkout, "pro-js", tmp_path / "lanes", check=True)

    assert built == tmp_path / "lanes" / "pro-js-check"
    # By code point, so the capitalised owner sorts first.
    assert list(links(built)) == ["instance_NodeBB__NodeBB-9a0b"]
    assert_resolves_to_tasks(built, checkout)


def test_building_the_check_lane_leaves_the_full_lane_as_it_was(tmp_path: Path):
    checkout = make_checkout(tmp_path / "checkout", ALL_TASKS)
    full = lanes.build(checkout, "pro-js", tmp_path / "lanes")
    before = links(full)

    lanes.build(checkout, "pro-js", tmp_path / "lanes", check=True)

    assert links(full) == before
    assert sorted(before) == sorted(OWNER_TASKS["pro-js"])


def test_an_unknown_lane_is_an_error_naming_it(tmp_path: Path):
    checkout = make_checkout(tmp_path / "checkout", ALL_TASKS)

    with pytest.raises(LaneError, match="pro-rust"):
        lanes.build(checkout, "pro-rust", tmp_path / "lanes")
    assert not (tmp_path / "lanes" / "pro-rust").exists()


def test_a_checkout_without_v2_tasks_is_an_error_naming_it(tmp_path: Path):
    checkout = tmp_path / "checkout"
    checkout.mkdir()

    with pytest.raises(LaneError, match=re.escape(str(checkout / "v2" / "tasks"))):
        lanes.build(checkout, "pro-go", tmp_path / "lanes")


def test_a_lane_with_no_tasks_in_the_checkout_is_an_error_naming_it(tmp_path: Path):
    checkout = make_checkout(tmp_path / "checkout", OWNER_TASKS["pro-python"])

    with pytest.raises(LaneError, match="pro-go"):
        lanes.build(checkout, "pro-go", tmp_path / "lanes", check=True)
