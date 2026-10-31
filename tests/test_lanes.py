import os
import re
from pathlib import Path

import pytest

from suss import config, lanes
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


TOP = """\
swe_bench_pro = "../SWE-bench_Pro-os"
dataset = "swe-bench-pro-v2"
environment = "docker"
k = 1
concurrency = 1
"""


def spec_timeout_error() -> str:
    """The error the spec shows for a max_seconds not under the timeout, unwrapped."""
    blocks = re.findall(r"```\n\s*(\$ suss run .*?)```", SPEC.read_text(), re.DOTALL)
    errors = [block for block in blocks if "agent timeout" in block]
    assert len(errors) == 1, f"expected one agent timeout error in {SPEC}, found {len(errors)}"
    printed = errors[0].split("\n", 1)[1]
    return " ".join(printed.split())


def arm(tmp_path: Path, toml: str, name: str = "peer-review") -> config.Arm:
    path = tmp_path / "suss.toml"
    path.write_text(toml)
    return config.load(path, name).arm


def timed_tasks(tmp_path: Path, *timeouts: object) -> dict[Path, dict]:
    """One task per timeout, named in order and read; None leaves timeout_sec out."""
    directories = []
    for number, timeout in enumerate(timeouts):
        directory = tmp_path / "tasks" / f"instance_owner__repo-{number:04d}"
        directory.mkdir(parents=True)
        agent = 'network_mode = "no-network"\n'
        if timeout is not None:
            agent += f"timeout_sec = {timeout}\n"
        (directory / "task.toml").write_text(f'version = "1.0"\n\n[agent]\n{agent}')
        directories.append(directory)
    return lanes.read_tasks(directories)


def test_max_seconds_equal_to_the_timeout_is_the_error_the_spec_shows(tmp_path: Path):
    peer_review = arm(tmp_path, TOP + '[arms.peer-review]\nharness = "h"\nmax_seconds = 3000\n')

    with pytest.raises(LaneError) as error:
        lanes.check_max_seconds(peer_review, "pro-go", timed_tasks(tmp_path, "3000.0", "3000.0"))

    assert f"error: {error.value}" == spec_timeout_error()


def test_max_seconds_one_under_the_timeout_passes(tmp_path: Path):
    peer_review = arm(tmp_path, TOP + '[arms.peer-review]\nharness = "h"\nmax_seconds = 2999\n')

    lanes.check_max_seconds(peer_review, "pro-go", timed_tasks(tmp_path, "3000.0", "3000.0"))


def test_max_seconds_is_checked_against_the_shortest_timeout_in_the_lane(tmp_path: Path):
    peer_review = arm(tmp_path, TOP + '[arms.peer-review]\nharness = "h"\nmax_seconds = 2000\n')
    lane = timed_tasks(tmp_path, "3000.0", "1800.0", "2400.0")

    with pytest.raises(LaneError) as error:
        lanes.check_max_seconds(peer_review, "pro-go", lane)

    assert "under the 1800-second agent timeout" in str(error.value)


def test_the_top_level_max_seconds_applies_to_an_arm_that_sets_none(tmp_path: Path):
    peer_review = arm(tmp_path, TOP + 'max_seconds = 3000\n[arms.peer-review]\nharness = "h"\n')

    with pytest.raises(LaneError) as error:
        lanes.check_max_seconds(peer_review, "pro-go", timed_tasks(tmp_path, "3000.0"))

    assert "sets max_seconds = 3000," in str(error.value)


def test_an_arm_max_seconds_is_checked_in_place_of_the_top_level_one(tmp_path: Path):
    toml = TOP + 'max_seconds = 2700\n[arms.peer-review]\nharness = "h"\nmax_seconds = 3000\n'
    peer_review = arm(tmp_path, toml)

    with pytest.raises(LaneError) as error:
        lanes.check_max_seconds(peer_review, "pro-go", timed_tasks(tmp_path, "3000.0"))

    assert "sets max_seconds = 3000," in str(error.value)


def test_an_arm_with_no_max_seconds_anywhere_passes(tmp_path: Path):
    peer_review = arm(tmp_path, TOP + '[arms.peer-review]\nharness = "h"\n')

    lanes.check_max_seconds(peer_review, "pro-go", timed_tasks(tmp_path, "3000.0"))


def test_whole_floats_print_as_integers_and_fractions_as_they_are(tmp_path: Path):
    peer_review = arm(tmp_path, TOP + '[arms.peer-review]\nharness = "h"\nmax_seconds = 3000.0\n')

    with pytest.raises(LaneError) as error:
        lanes.check_max_seconds(peer_review, "pro-go", timed_tasks(tmp_path, "2999.5"))

    assert "sets max_seconds = 3000, which must be under the 2999.5-second agent timeout" in str(
        error.value
    )


def test_a_task_with_no_timeout_is_an_error_naming_the_task(tmp_path: Path):
    peer_review = arm(tmp_path, TOP + '[arms.peer-review]\nharness = "h"\nmax_seconds = 10\n')
    lane = timed_tasks(tmp_path, "3000.0", None)

    with pytest.raises(LaneError) as error:
        lanes.check_max_seconds(peer_review, "pro-go", lane)

    assert list(lane)[1].name in str(error.value)
    assert "timeout_sec" in str(error.value)
