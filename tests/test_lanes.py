import re
import shutil
import tomllib
from pathlib import Path

import pytest
from harbor.models.task.config import NetworkMode, TaskConfig

from suss import config, lanes
from suss.lanes import LaneError

SPEC = Path(__file__).resolve().parent.parent / "docs" / "specs" / "suss-spec.md"

def test_the_only_lane_is_the_specs():
    named = re.findall(r"Suss has one lane, `([^`]+)`", SPEC.read_text())

    assert named == list(lanes.LANES)


@pytest.mark.parametrize("lane", ["pro-go", "pro-hard51", "rebench-check", "Rebench"])
def test_a_lane_other_than_rebench_is_an_error_naming_the_only_lane(lane: str):
    with pytest.raises(LaneError) as error:
        lanes.check_lane(lane)

    assert str(error.value) == f"no lane named {lane}; the only lane is rebench"


def test_rebench_is_a_lane():
    lanes.check_lane("rebench")


TOP = """\
swe_rebench = "../tasks/swe-rebench-07-2026"
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
        lanes.check_max_seconds(peer_review, "rebench", timed_tasks(tmp_path, "3000.0", "3000.0"))

    assert f"error: {error.value}" == spec_timeout_error()


def test_max_seconds_one_under_the_timeout_passes(tmp_path: Path):
    peer_review = arm(tmp_path, TOP + '[arms.peer-review]\nharness = "h"\nmax_seconds = 2999\n')

    lanes.check_max_seconds(peer_review, "rebench", timed_tasks(tmp_path, "3000.0", "3000.0"))


def test_max_seconds_is_checked_against_the_shortest_timeout_in_the_lane(tmp_path: Path):
    peer_review = arm(tmp_path, TOP + '[arms.peer-review]\nharness = "h"\nmax_seconds = 2000\n')
    lane = timed_tasks(tmp_path, "3000.0", "1800.0", "2400.0")

    with pytest.raises(LaneError) as error:
        lanes.check_max_seconds(peer_review, "rebench", lane)

    assert "under the 1800-second agent timeout" in str(error.value)


def test_the_top_level_max_seconds_applies_to_an_arm_that_sets_none(tmp_path: Path):
    peer_review = arm(tmp_path, TOP + 'max_seconds = 3000\n[arms.peer-review]\nharness = "h"\n')

    with pytest.raises(LaneError) as error:
        lanes.check_max_seconds(peer_review, "rebench", timed_tasks(tmp_path, "3000.0"))

    assert "sets max_seconds = 3000," in str(error.value)


def test_an_arm_max_seconds_is_checked_in_place_of_the_top_level_one(tmp_path: Path):
    toml = TOP + 'max_seconds = 2700\n[arms.peer-review]\nharness = "h"\nmax_seconds = 3000\n'
    peer_review = arm(tmp_path, toml)

    with pytest.raises(LaneError) as error:
        lanes.check_max_seconds(peer_review, "rebench", timed_tasks(tmp_path, "3000.0"))

    assert "sets max_seconds = 3000," in str(error.value)


def test_an_arm_with_no_max_seconds_anywhere_passes(tmp_path: Path):
    peer_review = arm(tmp_path, TOP + '[arms.peer-review]\nharness = "h"\n')

    lanes.check_max_seconds(peer_review, "rebench", timed_tasks(tmp_path, "3000.0"))


def test_whole_floats_print_as_integers_and_fractions_as_they_are(tmp_path: Path):
    peer_review = arm(tmp_path, TOP + '[arms.peer-review]\nharness = "h"\nmax_seconds = 3000.0\n')

    with pytest.raises(LaneError) as error:
        lanes.check_max_seconds(peer_review, "rebench", timed_tasks(tmp_path, "2999.5"))

    assert "sets max_seconds = 3000, which must be under the 2999.5-second agent timeout" in str(
        error.value
    )


def test_a_task_with_no_timeout_is_an_error_naming_the_task(tmp_path: Path):
    peer_review = arm(tmp_path, TOP + '[arms.peer-review]\nharness = "h"\nmax_seconds = 10\n')
    lane = timed_tasks(tmp_path, "3000.0", None)

    with pytest.raises(LaneError) as error:
        lanes.check_max_seconds(peer_review, "rebench", lane)

    assert list(lane)[1].name in str(error.value)
    assert "timeout_sec" in str(error.value)


# A task.toml as SWE-rebench's July 2026 snapshot ships it, with a comment added
# so that a rewrite through a TOML library shows.
REBENCH_TOML = """\
schema_version = "1.3"
artifacts = []

[task]
name = "ibragim-badertdinov/{name}"
description = "SWE-rebench leaderboard — July 2026 task"
keywords = [ "swe-rebench",]
[[task.authors]]
name = "Ibragim Badertdinov"
email = "badertdinov.ibragim@gmail.com"

[verifier]
timeout_sec = 3000.0
collect = []

[verifier.env]
_JAVA_OPTIONS = "-Dfile.encoding=UTF-8"

# The agent's own limits.
[agent]
timeout_sec = 3000.0

[environment]
network_mode = "public"
build_timeout_sec = 1800.0
cpus = 1
memory_mb = 4096
mcp_servers = []

[environment.env]

[solution.env]
"""

NO_NETWORK = 'network_mode = "no-network"'


def make_rebench(root: Path, names: list[str], toml: str = REBENCH_TOML) -> Path:
    """A download holding `names`, made in the order given, each laid out as the snapshot's are."""
    for name in names:
        task = root / name
        (task / "environment").mkdir(parents=True)
        (task / "tests").mkdir()
        (task / "solution").mkdir()
        (task / "task.toml").write_text(toml.replace("{name}", name))
        (task / "instruction.md").write_text(f"Fix {name}.\n")
        (task / "environment" / "Dockerfile").write_text("FROM scratch\n")
        (task / "tests" / "config.json").write_bytes(b'{"name": "\xc3\xa9"}\r\n')
        (task / "tests" / "test.sh").write_text("#!/bin/bash\nexit 0\n")
        (task / "solution" / "solve.sh").write_text("#!/bin/bash\nexit 0\n")
    return root


def toml_of(task: Path) -> dict:
    with (task / "task.toml").open("rb") as file:
        return tomllib.load(file)


def files_of(task: Path) -> dict[str, bytes]:
    """Every file under `task` but its task.toml, by path relative to it."""
    return {
        str(path.relative_to(task)): path.read_bytes()
        for path in sorted(task.rglob("*"))
        if path.is_file() and path.name != "task.toml"
    }


def test_the_rebench_lane_holds_a_copy_of_each_task_and_nothing_else(tmp_path: Path):
    names = ["apache__pulsar-25793", "ArcadeData__arcadedb-4281", "HKUDS__nanobot-4048"]
    source = make_rebench(tmp_path / "swe-rebench-07-2026", names)
    (source / "README.md").write_text("# SWE-rebench-07-2026\n")
    (source / "notes").mkdir()
    (source / "notes" / "todo.md").write_text("not a task\n")

    built = lanes.build_rebench(source, tmp_path / "lanes")

    assert built == tmp_path / "lanes" / "rebench"
    assert sorted(entry.name for entry in built.iterdir()) == sorted(names)
    for name in names:
        assert not (built / name).is_symlink(), f"{name} is a link, not a copy"
        assert (built / name).is_dir()


def test_each_task_toml_parses_to_the_downloads_plus_the_agents_network_mode(tmp_path: Path):
    source = make_rebench(tmp_path / "source", ["owner__repo-1"])

    built = lanes.build_rebench(source, tmp_path / "lanes")

    expected = toml_of(source / "owner__repo-1")
    expected["agent"]["network_mode"] = "no-network"
    assert toml_of(built / "owner__repo-1") == expected


def test_the_task_toml_keeps_its_text_and_gains_one_line_in_its_agent_table(tmp_path: Path):
    source = make_rebench(tmp_path / "source", ["owner__repo-1"])

    built = lanes.build_rebench(source, tmp_path / "lanes")

    original = (source / "owner__repo-1" / "task.toml").read_text().splitlines()
    copied = (built / "owner__repo-1" / "task.toml").read_text().splitlines()
    assert copied.count("[agent]") == 1, "a second [agent] header makes the file invalid TOML"
    assert [line for line in copied if line != NO_NETWORK] == original
    assert copied[copied.index("[agent]") + 1] == NO_NETWORK


@pytest.mark.parametrize(
    ("original", "copied"),
    [
        (
            "[agent]\ntimeout_sec = 3000.0",
            '[agent]\nnetwork_mode = "no-network"\ntimeout_sec = 3000.0',
        ),
        ("x = 1\n[agent]", 'x = 1\n[agent]\nnetwork_mode = "no-network"\n'),
        ("x = 1", 'x = 1\n\n[agent]\nnetwork_mode = "no-network"\n'),
    ],
)
def test_a_task_toml_with_no_final_newline_changes_only_where_the_line_goes(
    tmp_path: Path, original: str, copied: str
):
    source = make_rebench(tmp_path / "source", ["owner__repo-1"], original)

    built = lanes.build_rebench(source, tmp_path / "lanes")

    assert (built / "owner__repo-1" / "task.toml").read_bytes() == copied.encode()


def test_a_task_with_no_agent_table_gains_one(tmp_path: Path):
    toml = REBENCH_TOML.replace("[agent]\ntimeout_sec = 3000.0\n", "")
    source = make_rebench(tmp_path / "source", ["owner__repo-1"], toml)

    built = lanes.build_rebench(source, tmp_path / "lanes")

    expected = toml_of(source / "owner__repo-1")
    expected["agent"] = {"network_mode": "no-network"}
    assert toml_of(built / "owner__repo-1") == expected


@pytest.mark.parametrize("header", ["[ agent ]", "[agent]  # the agent", "[agent]\r"])
def test_an_agent_header_spelled_otherwise_gains_the_key_inside_it(tmp_path: Path, header: str):
    toml = REBENCH_TOML.replace("[agent]\n", f"{header}\n")
    source = make_rebench(tmp_path / "source", ["owner__repo-1"], toml)

    built = lanes.build_rebench(source, tmp_path / "lanes")

    expected = toml_of(source / "owner__repo-1")
    expected["agent"]["network_mode"] = "no-network"
    assert toml_of(built / "owner__repo-1") == expected


@pytest.mark.parametrize(
    "agent",
    [
        # A table made by dotted keys cannot take an [agent] header after them.
        "agent.timeout_sec = 3000.0\n",
        "agent = { timeout_sec = 3000.0 }\n",
        "agent = 3000\n",
    ],
)
def test_a_task_toml_the_key_cannot_be_added_to_is_an_error_naming_it(tmp_path: Path, agent: str):
    toml = 'schema_version = "1.3"\n' + agent + "\n[environment]\ncpus = 1\n"
    source = make_rebench(tmp_path / "source", ["owner__repo-1"], toml)

    with pytest.raises(LaneError, match=re.escape(str(source / "owner__repo-1" / "task.toml"))):
        lanes.build_rebench(source, tmp_path / "lanes")


@pytest.mark.parametrize("mode", ["no-network", "public"])
def test_a_task_that_sets_the_agents_network_mode_is_an_error_naming_it(tmp_path: Path, mode: str):
    toml = REBENCH_TOML.replace("[agent]\n", f'[agent]\nnetwork_mode = "{mode}"\n')
    source = make_rebench(tmp_path / "source", ["owner__repo-1", "owner__repo-2"])
    (source / "owner__repo-2" / "task.toml").write_text(toml)

    with pytest.raises(LaneError, match=re.escape(str(source / "owner__repo-2" / "task.toml"))):
        lanes.build_rebench(source, tmp_path / "lanes")
    assert (source / "owner__repo-2" / "task.toml").read_text() == toml


def test_a_failed_build_leaves_the_earlier_lane_as_it_was(tmp_path: Path):
    source = make_rebench(tmp_path / "source", ["owner__repo-1"])
    built = lanes.build_rebench(source, tmp_path / "lanes")
    before = toml_of(built / "owner__repo-1")
    make_rebench(source, ["owner__repo-2"])
    toml = REBENCH_TOML.replace("[agent]\n", '[agent]\nnetwork_mode = "public"\n')
    (source / "owner__repo-2" / "task.toml").write_text(toml)

    with pytest.raises(LaneError):
        lanes.build_rebench(source, tmp_path / "lanes")

    assert sorted(entry.name for entry in built.iterdir()) == ["owner__repo-1"]
    assert toml_of(built / "owner__repo-1") == before


def test_every_other_file_is_copied_byte_for_byte(tmp_path: Path):
    source = make_rebench(tmp_path / "source", ["owner__repo-1"])

    built = lanes.build_rebench(source, tmp_path / "lanes")

    assert files_of(built / "owner__repo-1") == files_of(source / "owner__repo-1")


def test_file_modes_are_kept_so_executable_scripts_stay_executable(tmp_path: Path):
    source = make_rebench(tmp_path / "source", ["owner__repo-1"])
    task = source / "owner__repo-1"
    (task / "tests" / "test.sh").chmod(0o755)
    (task / "solution" / "solve.sh").chmod(0o750)
    (task / "instruction.md").chmod(0o600)
    # Read-only, so a build that copies it and then writes into the copy fails.
    (task / "task.toml").chmod(0o444)

    built = lanes.build_rebench(source, tmp_path / "lanes") / "owner__repo-1"

    for path in ("tests/test.sh", "solution/solve.sh", "instruction.md", "task.toml"):
        assert oct((built / path).stat().st_mode) == oct((task / path).stat().st_mode), path


def test_read_only_task_directories_are_copied_with_their_modes_and_rebuilt_over(
    tmp_path: Path,
):
    source = make_rebench(tmp_path / "source", ["owner__repo-1"])
    task = source / "owner__repo-1"
    (task / "tests").chmod(0o555)
    task.chmod(0o555)
    try:
        lanes.build_rebench(source, tmp_path / "lanes")
        built = lanes.build_rebench(source, tmp_path / "lanes") / "owner__repo-1"

        for path in ("", "tests"):
            assert oct((built / path).stat().st_mode) == oct((task / path).stat().st_mode), path
        assert toml_of(built)["agent"]["network_mode"] == "no-network"
    finally:
        # Left read-only, they would stop pytest removing tmp_path.
        for directory in tmp_path.rglob("*"):
            if directory.is_dir():
                directory.chmod(0o755)


def test_a_rebuild_leaves_only_the_tasks_of_the_latest_download(tmp_path: Path):
    source = make_rebench(tmp_path / "source", ["owner__repo-1", "owner__repo-2"])
    built = lanes.build_rebench(source, tmp_path / "lanes")
    (built / "owner__repo-1" / "left-by-a-trial.txt").write_text("stale\n")
    shutil.rmtree(source / "owner__repo-2")
    (source / "owner__repo-1" / "tests" / "config.json").write_text("{}\n")

    lanes.build_rebench(source, tmp_path / "lanes")

    assert [entry.name for entry in built.iterdir()] == ["owner__repo-1"]
    assert files_of(built / "owner__repo-1") == files_of(source / "owner__repo-1")


def test_the_rebench_check_lane_holds_only_the_first_task_by_name(tmp_path: Path):
    # By code point, ArcadeData comes first. Ignoring case, apache would, and
    # by number, owner__repo-9 would come before owner__repo-10.
    names = ["owner__repo-9", "owner__repo-10", "apache__pulsar-1", "ArcadeData__arcadedb-2"]
    source = make_rebench(tmp_path / "source", names)

    built = lanes.build_rebench(source, tmp_path / "lanes", check=True)

    assert built == tmp_path / "lanes" / "rebench-check"
    assert [entry.name for entry in built.iterdir()] == ["ArcadeData__arcadedb-2"]
    assert not (tmp_path / "lanes" / "rebench").exists()


def test_building_the_rebench_check_lane_leaves_the_full_lane_as_it_was(tmp_path: Path):
    names = ["owner__repo-1", "owner__repo-2"]
    source = make_rebench(tmp_path / "source", names)
    full = lanes.build_rebench(source, tmp_path / "lanes")

    lanes.build_rebench(source, tmp_path / "lanes", check=True)

    assert sorted(entry.name for entry in full.iterdir()) == names


def test_a_download_with_no_tasks_is_an_error_naming_it(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "README.md").write_text("# empty\n")

    with pytest.raises(LaneError, match=re.escape(str(source))):
        lanes.build_rebench(source, tmp_path / "lanes")


def test_a_download_that_is_not_a_directory_is_an_error_naming_it(tmp_path: Path):
    with pytest.raises(LaneError, match=re.escape(str(tmp_path / "missing"))):
        lanes.build_rebench(tmp_path / "missing", tmp_path / "lanes")


def test_a_rebench_lane_path_that_is_not_a_directory_is_an_error_and_left_alone(tmp_path: Path):
    source = make_rebench(tmp_path / "source", ["owner__repo-1"])
    (tmp_path / "lanes").mkdir()
    (tmp_path / "lanes" / "rebench").write_text("mine\n")

    with pytest.raises(LaneError, match=re.escape(str(tmp_path / "lanes" / "rebench"))):
        lanes.build_rebench(source, tmp_path / "lanes")
    assert (tmp_path / "lanes" / "rebench").read_text() == "mine\n"


def test_a_rebench_lane_path_that_is_a_link_is_an_error_and_its_target_left_alone(
    tmp_path: Path,
):
    source = make_rebench(tmp_path / "source", ["owner__repo-1"])
    target = tmp_path / "elsewhere"
    target.mkdir()
    (target / "mine.txt").write_text("mine\n")
    (tmp_path / "lanes").mkdir()
    (tmp_path / "lanes" / "rebench").symlink_to(target)

    with pytest.raises(LaneError, match=re.escape(str(tmp_path / "lanes" / "rebench"))):
        lanes.build_rebench(source, tmp_path / "lanes")
    assert [entry.name for entry in target.iterdir()] == ["mine.txt"]


def test_harbor_reads_the_copy_as_a_task_whose_agent_has_no_network(tmp_path: Path):
    source = make_rebench(tmp_path / "source", ["owner__repo-1"])

    built = lanes.build_rebench(source, tmp_path / "lanes")

    task = TaskConfig.model_validate_toml((built / "owner__repo-1" / "task.toml").read_text())
    assert task.agent.network_mode == NetworkMode.NO_NETWORK
    assert task.environment.network_mode == NetworkMode.PUBLIC


def test_the_lane_checks_are_made_on_the_rebench_lane(tmp_path: Path):
    toml = REBENCH_TOML.replace("cpus = 1", "cpus = 2").replace(
        "3000.0\n\n[environment]", "1800.0\n\n[environment]"
    )
    source = make_rebench(tmp_path / "source", ["owner__repo-1"])
    make_rebench(source, ["owner__repo-2"], toml)
    built = lanes.build_rebench(source, tmp_path / "lanes")
    read = lanes.read_tasks(sorted(built.iterdir()))
    peer_review = arm(tmp_path, TOP + '[arms.peer-review]\nharness = "h"\nmax_seconds = 1800\n')

    assert lanes.needs(read) == (2, 4096)
    with pytest.raises(LaneError, match="under the 1800-second agent timeout"):
        lanes.check_max_seconds(peer_review, "rebench", read)


@pytest.mark.parametrize("check", [False, True], ids=["full", "check"])
@pytest.mark.parametrize("blocker", ["a-file", "a-link", "lanes-a-file", "network-set"])
def test_check_buildable_refuses_what_a_build_would_and_changes_nothing(
    tmp_path: Path, blocker: str, check: bool
):
    source = make_rebench(tmp_path / "source", ["owner__repo-1", "owner__repo-2"])
    lane = tmp_path / "lanes" / ("rebench-check" if check else "rebench")
    if blocker == "lanes-a-file":
        (tmp_path / "lanes").write_text("mine\n")
        named = tmp_path / "lanes"
    elif blocker == "network-set":
        toml = source / "owner__repo-1" / "task.toml"
        toml.write_text(toml.read_text().replace("[agent]\n", '[agent]\nnetwork_mode = "public"\n'))
        named = toml
    else:
        lane.parent.mkdir()
        if blocker == "a-file":
            lane.write_text("mine\n")
        else:
            (tmp_path / "elsewhere").mkdir()
            lane.symlink_to(tmp_path / "elsewhere")
        named = lane
    before = sorted(str(path) for path in tmp_path.rglob("*"))

    with pytest.raises(LaneError, match=re.escape(str(named))):
        lanes.check_buildable(source, tmp_path / "lanes", check)
    with pytest.raises(LaneError, match=re.escape(str(named))):
        lanes.build_rebench(source, tmp_path / "lanes", check)
    assert sorted(str(path) for path in tmp_path.rglob("*")) == before


def test_check_buildable_passes_a_lane_a_build_would_replace(tmp_path: Path):
    source = make_rebench(tmp_path / "source", ["owner__repo-1"])
    lanes.build_rebench(source, tmp_path / "lanes")
    (tmp_path / "lanes" / "rebench" / "stray.txt").write_text("left by hand\n")

    lanes.check_buildable(source, tmp_path / "lanes")

    assert (tmp_path / "lanes" / "rebench" / "stray.txt").exists()
