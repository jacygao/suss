import re
import shutil
import tomllib
from pathlib import Path

import pytest
from harbor.models.task.config import NetworkMode, TaskConfig, VerifierEnvironmentMode
from harbor.models.task.verifier_mode import resolve_task_verifier_mode

from suss import lanes
from suss.lanes import LaneError

SPEC = Path(__file__).resolve().parent.parent / "docs" / "specs" / "suss-spec.md"

def test_the_lanes_are_the_specs():
    section = SPEC.read_text().split("\n## 5. Lanes\n", 1)[1].split("\n## ", 1)[0]
    named = re.findall(r"^### `([^`]+)`$", section, re.MULTILINE)

    assert named == list(lanes.LANES)


@pytest.mark.parametrize("lane", ["pro-go", "pro-hard51", "rebench-check", "Rebench", "Senior"])
def test_a_lane_suss_does_not_have_is_an_error_naming_its_lanes(lane: str):
    with pytest.raises(LaneError) as error:
        lanes.check_lane(lane)

    assert str(error.value) == f"suss has no lane named {lane}; its lanes are rebench, senior"


@pytest.mark.parametrize("lane", ["rebench", "senior"])
def test_rebench_and_senior_are_lanes(lane: str):
    lanes.check_lane(lane)


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


def copied_files(task: Path) -> dict[str, bytes]:
    """The files a rebench copy of `task` holds: the task's, and its Dockerfile in tests/ too."""
    return {**files_of(task), "tests/Dockerfile": (task / "environment" / "Dockerfile").read_bytes()}


SEPARATE = 'environment_mode = "separate"'
# What a rebench task.toml with no [verifier] table gains at its end.
SEPARATE_TABLE = f"\n[verifier]\n{SEPARATE}\n"


def test_the_rebench_lane_holds_a_copy_of_each_task_and_nothing_else(tmp_path: Path):
    names = ["apache__pulsar-25793", "ArcadeData__arcadedb-4281", "HKUDS__nanobot-4048"]
    source = make_rebench(tmp_path / "swe-rebench-07-2026", names)
    (source / "README.md").write_text("# SWE-rebench-07-2026\n")
    (source / "notes").mkdir()
    (source / "notes" / "todo.md").write_text("not a task\n")

    built = lanes.build(lanes.REBENCH, source, tmp_path / "lanes")

    assert built == tmp_path / "lanes" / "rebench"
    assert sorted(entry.name for entry in built.iterdir()) == sorted(names)
    for name in names:
        assert not (built / name).is_symlink(), f"{name} is a link, not a copy"
        assert (built / name).is_dir()


def test_each_task_toml_parses_to_the_downloads_plus_the_agents_network_and_verifiers_mode(
    tmp_path: Path,
):
    source = make_rebench(tmp_path / "source", ["owner__repo-1"])

    built = lanes.build(lanes.REBENCH, source, tmp_path / "lanes")

    expected = toml_of(source / "owner__repo-1")
    expected["agent"]["network_mode"] = "no-network"
    expected["verifier"]["environment_mode"] = "separate"
    assert toml_of(built / "owner__repo-1") == expected


def test_the_task_toml_keeps_its_text_and_gains_one_line_in_its_agent_table(tmp_path: Path):
    source = make_rebench(tmp_path / "source", ["owner__repo-1"])

    built = lanes.build(lanes.REBENCH, source, tmp_path / "lanes")

    original = (source / "owner__repo-1" / "task.toml").read_text().splitlines()
    copied = (built / "owner__repo-1" / "task.toml").read_text().splitlines()
    assert copied.count("[agent]") == 1, "a second [agent] header makes the file invalid TOML"
    assert [line for line in copied if line not in (NO_NETWORK, SEPARATE)] == original
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

    built = lanes.build(lanes.REBENCH, source, tmp_path / "lanes")

    # The verifier's table comes after, from the text the agent's line went into.
    with_verifier = copied + ("" if copied.endswith("\n") else "\n") + SEPARATE_TABLE
    assert (built / "owner__repo-1" / "task.toml").read_bytes() == with_verifier.encode()


def test_a_task_with_no_agent_table_gains_one(tmp_path: Path):
    toml = REBENCH_TOML.replace("[agent]\ntimeout_sec = 3000.0\n", "")
    source = make_rebench(tmp_path / "source", ["owner__repo-1"], toml)

    built = lanes.build(lanes.REBENCH, source, tmp_path / "lanes")

    expected = toml_of(source / "owner__repo-1")
    expected["agent"] = {"network_mode": "no-network"}
    expected["verifier"]["environment_mode"] = "separate"
    assert toml_of(built / "owner__repo-1") == expected


@pytest.mark.parametrize("header", ["[ agent ]", "[agent]  # the agent", "[agent]\r"])
def test_an_agent_header_spelled_otherwise_gains_the_key_inside_it(tmp_path: Path, header: str):
    toml = REBENCH_TOML.replace("[agent]\n", f"{header}\n")
    source = make_rebench(tmp_path / "source", ["owner__repo-1"], toml)

    built = lanes.build(lanes.REBENCH, source, tmp_path / "lanes")

    expected = toml_of(source / "owner__repo-1")
    expected["agent"]["network_mode"] = "no-network"
    expected["verifier"]["environment_mode"] = "separate"
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
        lanes.build(lanes.REBENCH, source, tmp_path / "lanes")


def test_a_task_that_opens_the_agents_network_is_an_error_naming_it(tmp_path: Path):
    toml = REBENCH_TOML.replace("[agent]\n", '[agent]\nnetwork_mode = "public"\n')
    source = make_rebench(tmp_path / "source", ["owner__repo-1", "owner__repo-2"])
    (source / "owner__repo-2" / "task.toml").write_text(toml)

    with pytest.raises(LaneError, match=re.escape(str(source / "owner__repo-2" / "task.toml"))):
        lanes.build(lanes.REBENCH, source, tmp_path / "lanes")
    assert (source / "owner__repo-2" / "task.toml").read_text() == toml


# An [agent] table as Senior SWE-Bench's v2026.06 tasks write it, with fewer hosts.
ALLOWLIST_AGENT = """\
[agent]
timeout_sec = 3000.0
network_mode = "allowlist"
allowed_hosts = [
    "api.portkey.ai",
    "pypi.org",
    "*.pythonhosted.org",
    "clickhouse.com",
    "*.clickhouse.com",
]
"""


@pytest.mark.parametrize(
    "agent",
    [ALLOWLIST_AGENT, '[agent]\ntimeout_sec = 3000.0\nnetwork_mode = "no-network"\n'],
    ids=["allowlist", "no-network"],
)
def test_a_task_whose_agent_already_has_a_closed_network_keeps_its_agent_table(
    tmp_path: Path, agent: str
):
    toml = REBENCH_TOML.replace("[agent]\ntimeout_sec = 3000.0\n", agent)
    source = make_rebench(tmp_path / "source", ["owner__repo-1"], toml)

    built = lanes.build(lanes.REBENCH, source, tmp_path / "lanes")

    original = (source / "owner__repo-1" / "task.toml").read_text().splitlines()
    copied = (built / "owner__repo-1" / "task.toml").read_text().splitlines()
    assert [line for line in copied if line != SEPARATE] == original


def test_a_task_with_agent_hosts_and_no_allowlist_is_an_error_naming_it(tmp_path: Path):
    # Harbor refuses allowed_hosts without network_mode = "allowlist" when it loads the task.
    toml = REBENCH_TOML.replace("[agent]\n", '[agent]\nallowed_hosts = ["pypi.org"]\n')
    source = make_rebench(tmp_path / "source", ["owner__repo-1"], toml)

    with pytest.raises(LaneError, match=re.escape(str(source / "owner__repo-1" / "task.toml"))):
        lanes.build(lanes.REBENCH, source, tmp_path / "lanes")


def test_a_failed_build_leaves_the_earlier_lane_as_it_was(tmp_path: Path):
    source = make_rebench(tmp_path / "source", ["owner__repo-1"])
    built = lanes.build(lanes.REBENCH, source, tmp_path / "lanes")
    before = toml_of(built / "owner__repo-1")
    make_rebench(source, ["owner__repo-2"])
    toml = REBENCH_TOML.replace("[agent]\n", '[agent]\nnetwork_mode = "public"\n')
    (source / "owner__repo-2" / "task.toml").write_text(toml)

    with pytest.raises(LaneError):
        lanes.build(lanes.REBENCH, source, tmp_path / "lanes")

    assert sorted(entry.name for entry in built.iterdir()) == ["owner__repo-1"]
    assert toml_of(built / "owner__repo-1") == before


def test_every_other_file_is_copied_byte_for_byte(tmp_path: Path):
    source = make_rebench(tmp_path / "source", ["owner__repo-1"])

    built = lanes.build(lanes.REBENCH, source, tmp_path / "lanes")

    assert files_of(built / "owner__repo-1") == copied_files(source / "owner__repo-1")


def test_file_modes_are_kept_so_executable_scripts_stay_executable(tmp_path: Path):
    source = make_rebench(tmp_path / "source", ["owner__repo-1"])
    task = source / "owner__repo-1"
    (task / "tests" / "test.sh").chmod(0o755)
    (task / "solution" / "solve.sh").chmod(0o750)
    (task / "instruction.md").chmod(0o600)
    # Read-only, so a build that copies it and then writes into the copy fails.
    (task / "task.toml").chmod(0o444)

    built = lanes.build(lanes.REBENCH, source, tmp_path / "lanes") / "owner__repo-1"

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
        lanes.build(lanes.REBENCH, source, tmp_path / "lanes")
        built = lanes.build(lanes.REBENCH, source, tmp_path / "lanes") / "owner__repo-1"

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
    built = lanes.build(lanes.REBENCH, source, tmp_path / "lanes")
    (built / "owner__repo-1" / "left-by-a-trial.txt").write_text("stale\n")
    shutil.rmtree(source / "owner__repo-2")
    (source / "owner__repo-1" / "tests" / "config.json").write_text("{}\n")

    lanes.build(lanes.REBENCH, source, tmp_path / "lanes")

    assert [entry.name for entry in built.iterdir()] == ["owner__repo-1"]
    assert files_of(built / "owner__repo-1") == copied_files(source / "owner__repo-1")


def test_the_rebench_check_lane_holds_only_the_first_task_by_name(tmp_path: Path):
    # By code point, ArcadeData comes first. Ignoring case, apache would, and
    # by number, owner__repo-9 would come before owner__repo-10.
    names = ["owner__repo-9", "owner__repo-10", "apache__pulsar-1", "ArcadeData__arcadedb-2"]
    source = make_rebench(tmp_path / "source", names)

    built = lanes.build(lanes.REBENCH, source, tmp_path / "lanes", check=True)

    assert built == tmp_path / "lanes" / "rebench-check"
    assert [entry.name for entry in built.iterdir()] == ["ArcadeData__arcadedb-2"]
    assert not (tmp_path / "lanes" / "rebench").exists()


def test_building_the_rebench_check_lane_leaves_the_full_lane_as_it_was(tmp_path: Path):
    names = ["owner__repo-1", "owner__repo-2"]
    source = make_rebench(tmp_path / "source", names)
    full = lanes.build(lanes.REBENCH, source, tmp_path / "lanes")

    lanes.build(lanes.REBENCH, source, tmp_path / "lanes", check=True)

    assert sorted(entry.name for entry in full.iterdir()) == names


# The tasks no fix can pass, as the snapshot names them.
UNGRADEABLE = ["langchain4j__langchain4j-5315", "langchain4j__langchain4j-5367", "openrewrite__rewrite-7784"]


def test_the_tasks_the_lane_leaves_out_are_the_specs():
    sentences = re.findall(r"It leaves out ([^.]*?), whose", SPEC.read_text().replace("\n", " "))

    assert len(sentences) == 1, f"expected one sentence naming the tasks left out in {SPEC}"
    assert sorted(re.findall(r"`([^`]+)`", sentences[0])) == sorted(lanes.UNGRADEABLE)


def test_the_rebench_lane_leaves_out_the_tasks_no_fix_can_pass(tmp_path: Path):
    source = make_rebench(tmp_path / "source", ["owner__repo-1", *UNGRADEABLE, "zzz__repo-2"])

    built = lanes.build(lanes.REBENCH, source, tmp_path / "lanes")

    assert sorted(entry.name for entry in built.iterdir()) == ["owner__repo-1", "zzz__repo-2"]


def test_the_check_lane_takes_the_first_task_the_lane_does_not_leave_out(tmp_path: Path):
    # Every name left out sorts before owner__repo-1.
    source = make_rebench(tmp_path / "source", ["owner__repo-1", *UNGRADEABLE])

    built = lanes.build(lanes.REBENCH, source, tmp_path / "lanes", check=True)

    assert [entry.name for entry in built.iterdir()] == ["owner__repo-1"]


def test_a_download_lacking_some_tasks_the_lane_leaves_out_builds_and_names_none(
    tmp_path: Path, capfd
):
    source = make_rebench(tmp_path / "source", ["owner__repo-1", UNGRADEABLE[2]])

    lanes.check_buildable(lanes.REBENCH, source, tmp_path / "lanes")
    built = lanes.build(lanes.REBENCH, source, tmp_path / "lanes")

    assert [entry.name for entry in built.iterdir()] == ["owner__repo-1"]
    assert capfd.readouterr() == ("", "")


@pytest.mark.parametrize("check", [False, True], ids=["full", "check"])
def test_a_rebuild_removes_tasks_left_out_that_an_earlier_lane_holds(tmp_path: Path, check: bool):
    source = make_rebench(tmp_path / "source", [*UNGRADEABLE, "owner__repo-1"])
    lane = tmp_path / "lanes" / ("rebench-check" if check else "rebench")
    # A lane built before any task was left out, holding copies of them all.
    shutil.copytree(source, lane)

    lanes.build(lanes.REBENCH, source, tmp_path / "lanes", check)

    assert [entry.name for entry in lane.iterdir()] == ["owner__repo-1"]


def test_a_download_holding_only_tasks_the_lane_leaves_out_is_an_error_naming_it(tmp_path: Path):
    source = make_rebench(tmp_path / "source", UNGRADEABLE)

    with pytest.raises(LaneError, match=re.escape(f"{source}: every task in it is one")):
        lanes.check_buildable(lanes.REBENCH, source, tmp_path / "lanes")
    assert not (tmp_path / "lanes").exists()


def test_a_download_with_no_tasks_is_an_error_naming_it(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "README.md").write_text("# empty\n")

    with pytest.raises(LaneError, match=re.escape(str(source))):
        lanes.build(lanes.REBENCH, source, tmp_path / "lanes")


def test_a_download_that_is_not_a_directory_is_an_error_naming_it(tmp_path: Path):
    with pytest.raises(LaneError, match=re.escape(str(tmp_path / "missing"))):
        lanes.build(lanes.REBENCH, tmp_path / "missing", tmp_path / "lanes")


def test_a_rebench_lane_path_that_is_not_a_directory_is_an_error_and_left_alone(tmp_path: Path):
    source = make_rebench(tmp_path / "source", ["owner__repo-1"])
    (tmp_path / "lanes").mkdir()
    (tmp_path / "lanes" / "rebench").write_text("mine\n")

    with pytest.raises(LaneError, match=re.escape(str(tmp_path / "lanes" / "rebench"))):
        lanes.build(lanes.REBENCH, source, tmp_path / "lanes")
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
        lanes.build(lanes.REBENCH, source, tmp_path / "lanes")
    assert [entry.name for entry in target.iterdir()] == ["mine.txt"]


def test_harbor_reads_the_copy_as_a_task_whose_agent_has_no_network(tmp_path: Path):
    source = make_rebench(tmp_path / "source", ["owner__repo-1"])

    built = lanes.build(lanes.REBENCH, source, tmp_path / "lanes")

    task = TaskConfig.model_validate_toml((built / "owner__repo-1" / "task.toml").read_text())
    assert task.agent.network_mode == NetworkMode.NO_NETWORK
    assert task.environment.network_mode == NetworkMode.PUBLIC


def test_the_lane_checks_are_made_on_the_rebench_lane(tmp_path: Path):
    toml = REBENCH_TOML.replace("cpus = 1", "cpus = 2")
    source = make_rebench(tmp_path / "source", ["owner__repo-1"])
    make_rebench(source, ["owner__repo-2"], toml)
    built = lanes.build(lanes.REBENCH, source, tmp_path / "lanes")
    read = lanes.read_tasks(sorted(built.iterdir()))

    assert lanes.needs(read) == (2, 4096)


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
        lanes.check_buildable(lanes.REBENCH, source, tmp_path / "lanes", check)
    with pytest.raises(LaneError, match=re.escape(str(named))):
        lanes.build(lanes.REBENCH, source, tmp_path / "lanes", check)
    assert sorted(str(path) for path in tmp_path.rglob("*")) == before


def test_check_buildable_passes_a_lane_a_build_would_replace(tmp_path: Path):
    source = make_rebench(tmp_path / "source", ["owner__repo-1"])
    lanes.build(lanes.REBENCH, source, tmp_path / "lanes")
    (tmp_path / "lanes" / "rebench" / "stray.txt").write_text("left by hand\n")

    lanes.check_buildable(lanes.REBENCH, source, tmp_path / "lanes")

    assert (tmp_path / "lanes" / "rebench" / "stray.txt").exists()


# The senior lane

# Three tasks of Senior SWE-Bench v2026.06, each with tests/test.sh as the
# download holds it, its task.toml with stand-ins for the narrative that
# describes the fix, and every other file a stand-in:
# - paperless-ngx-perf-workflow-queries, an investigate task, graded by tests;
# - harbor-add-agent-file-retention, a design task with tests, whose agent
#   timeout is 14400 seconds;
# - prefect-feat-dbt-per-node-hardening, a design task with no tests.
SENIOR_TASKS = Path(__file__).resolve().parent / "senior_tasks"
INVESTIGATE = "paperless-ngx-perf-workflow-queries"
DESIGN = "harbor-add-agent-file-retention"
NO_TESTS = "prefect-feat-dbt-per-node-hardening"
LITELLM = b"litellm>=1.0,<2.0"
PINNED = b"litellm>=1.0,<1.106"


def make_senior(root: Path) -> Path:
    """A download holding the three tasks, beside a file and a directory that are not tasks."""
    shutil.copytree(SENIOR_TASKS, root)
    (root / "README.md").write_text("# senior-swe-bench-v2026.06\n")
    (root / "notes").mkdir()
    (root / "notes" / "todo.md").write_text("not a task\n")
    return root


def every_file(task: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(task)): path.read_bytes()
        for path in sorted(task.rglob("*"))
        if path.is_file()
    }


def test_the_senior_fixtures_have_the_shapes_they_stand_for():
    shared = (SENIOR_TASKS / INVESTIGATE / "tests" / "test.sh").read_bytes()
    assert shared.splitlines().count(LITELLM) == 1
    for name in (DESIGN, NO_TESTS):
        assert (SENIOR_TASKS / name / "tests" / "test.sh").read_bytes() == shared, name
    assert (SENIOR_TASKS / INVESTIGATE / "tests" / "verify").is_dir()
    assert not (SENIOR_TASKS / INVESTIGATE / "tests" / "validate").exists()
    assert (SENIOR_TASKS / DESIGN / "tests" / "validate").is_dir()
    assert (SENIOR_TASKS / DESIGN / "tests" / "verify").is_dir()
    assert (SENIOR_TASKS / NO_TESTS / "tests" / "validate").is_dir()
    assert not (SENIOR_TASKS / NO_TESTS / "tests" / "verify").exists()


def test_the_senior_lane_holds_a_copy_of_each_task_and_nothing_else(tmp_path: Path):
    source = make_senior(tmp_path / "senior-swe-bench-v2026.06")

    built = lanes.build(lanes.SENIOR, source, tmp_path / "lanes")

    assert built == tmp_path / "lanes" / "senior"
    assert sorted(entry.name for entry in built.iterdir()) == sorted([INVESTIGATE, DESIGN, NO_TESTS])
    assert not any((built / name).is_symlink() for name in (INVESTIGATE, DESIGN, NO_TESTS))


@pytest.mark.parametrize("name", [INVESTIGATE, DESIGN, NO_TESTS])
def test_each_senior_copy_differs_from_its_task_in_its_verifier_and_the_litellm_line_alone(
    tmp_path: Path, name: str
):
    source = make_senior(tmp_path / "source")

    built = lanes.build(lanes.SENIOR, source, tmp_path / "lanes") / name

    original, copied = every_file(source / name), every_file(built)
    assert copied.keys() == original.keys() | {"tests/Dockerfile"}
    assert copied["tests/Dockerfile"] == original["environment/Dockerfile"]
    changed = [path for path in original if copied[path] != original[path]]
    assert changed == ["task.toml", "tests/test.sh"]
    before = original["tests/test.sh"].splitlines(keepends=True)
    after = copied["tests/test.sh"].splitlines(keepends=True)
    assert len(after) == len(before)
    changed = [(old, new) for old, new in zip(before, after) if old != new]
    assert changed == [(LITELLM + b"\n", PINNED + b"\n")]


def test_a_senior_copy_keeps_its_modes_and_is_rebuilt_over_a_read_only_tests_directory(
    tmp_path: Path,
):
    source = make_senior(tmp_path / "source")
    tests = source / INVESTIGATE / "tests"
    (tests / "test.sh").chmod(0o555)
    tests.chmod(0o555)
    try:
        lanes.build(lanes.SENIOR, source, tmp_path / "lanes")
        built = lanes.build(lanes.SENIOR, source, tmp_path / "lanes") / INVESTIGATE

        for path in ("tests", "tests/test.sh", "solution/solve.sh"):
            assert oct((built / path).stat().st_mode) == oct(
                (source / INVESTIGATE / path).stat().st_mode
            ), path
        assert PINNED in (built / "tests" / "test.sh").read_bytes().splitlines()
    finally:
        # Left read-only, they would stop pytest removing tmp_path.
        for directory in tmp_path.rglob("*"):
            if directory.is_dir():
                directory.chmod(0o755)


def test_a_line_that_names_litellm_otherwise_is_left_as_it_is(tmp_path: Path):
    source = make_senior(tmp_path / "source")
    test_sh = source / INVESTIGATE / "tests" / "test.sh"
    extra = [b"# litellm>=1.0,<2.0 once broke the judges\n", b"  litellm>=1.0,<2.0\n"]
    test_sh.write_bytes(b"".join(extra) + test_sh.read_bytes())

    built = lanes.build(lanes.SENIOR, source, tmp_path / "lanes") / INVESTIGATE

    copied = (built / "tests" / "test.sh").read_bytes().splitlines(keepends=True)
    assert copied[:2] == extra
    assert copied.count(PINNED + b"\n") == 1


def test_a_test_sh_with_crlf_lines_keeps_them(tmp_path: Path):
    source = make_senior(tmp_path / "source")
    test_sh = source / INVESTIGATE / "tests" / "test.sh"
    test_sh.write_bytes(test_sh.read_bytes().replace(b"\n", b"\r\n"))

    built = lanes.build(lanes.SENIOR, source, tmp_path / "lanes") / INVESTIGATE

    assert (built / "tests" / "test.sh").read_bytes() == test_sh.read_bytes().replace(
        LITELLM + b"\r\n", PINNED + b"\r\n"
    )


@pytest.mark.parametrize(
    "rewrite",
    [
        lambda text: text.replace(LITELLM + b"\n", b""),
        lambda text: text.replace(LITELLM, PINNED),
        lambda text: text.replace(LITELLM, LITELLM + b" "),
        lambda text: text.replace(LITELLM, b"litellm>=1.0,<3.0"),
        lambda text: text.replace(LITELLM + b"\n", (LITELLM + b"\n") * 2),
        None,
    ],
    ids=["no-line", "already-pinned", "trailing-space", "other-range", "twice", "no-test-sh"],
)
def test_a_test_sh_without_the_one_litellm_line_is_refused_naming_it_and_changes_nothing(
    tmp_path: Path, rewrite
):
    source = make_senior(tmp_path / "source")
    earlier = lanes.build(lanes.SENIOR, source, tmp_path / "lanes")
    test_sh = source / DESIGN / "tests" / "test.sh"
    if rewrite is None:
        test_sh.unlink()
    else:
        test_sh.write_bytes(rewrite(test_sh.read_bytes()))
    before = {str(path): path.read_bytes() for path in earlier.rglob("*") if path.is_file()}

    with pytest.raises(LaneError) as error:
        lanes.check_buildable(lanes.SENIOR, source, tmp_path / "lanes")
    with pytest.raises(LaneError):
        lanes.build(lanes.SENIOR, source, tmp_path / "lanes")

    assert str(error.value).startswith(f"{test_sh}: ")
    assert "pin litellm" in str(error.value)
    assert {str(path): path.read_bytes() for path in earlier.rglob("*") if path.is_file()} == before


@pytest.mark.parametrize("absolute", [True, False], ids=["absolute", "relative"])
def test_a_tests_directory_that_is_a_link_is_refused_and_the_download_left_alone(
    tmp_path: Path, absolute: bool
):
    source = make_senior(tmp_path / "source")
    earlier = lanes.build(lanes.SENIOR, source, tmp_path / "lanes")
    tests = source / DESIGN / "tests"
    shutil.move(tests, source / DESIGN / "real-tests")
    tests.symlink_to(source / DESIGN / "real-tests" if absolute else "real-tests")
    downloaded = every_file(source)
    built = every_file(earlier)

    with pytest.raises(LaneError) as error:
        lanes.build(lanes.SENIOR, source, tmp_path / "lanes")

    assert str(error.value).startswith(f"{tests}: ")
    assert every_file(source) == downloaded
    assert every_file(earlier) == built


def test_the_senior_check_lane_holds_only_the_first_task_by_name(tmp_path: Path):
    source = make_senior(tmp_path / "source")

    built = lanes.build(lanes.SENIOR, source, tmp_path / "lanes", check=True)

    assert built == tmp_path / "lanes" / "senior-check"
    assert [entry.name for entry in built.iterdir()] == [DESIGN]
    assert PINNED in (built / DESIGN / "tests" / "test.sh").read_bytes().splitlines()
    assert not (tmp_path / "lanes" / "senior").exists()


def test_the_senior_lane_takes_every_task_of_its_download(tmp_path: Path):
    source = make_senior(tmp_path / "source")
    # Named as a task the rebench lane leaves out, which the senior lane must not.
    shutil.copytree(source / INVESTIGATE, source / UNGRADEABLE[0])

    assert [task.name for task in lanes.lane_tasks(lanes.SENIOR, source)] == sorted(
        [INVESTIGATE, DESIGN, NO_TESTS, UNGRADEABLE[0]]
    )


def test_a_rebench_build_leaves_its_test_sh_unpinned(tmp_path: Path):
    source = make_rebench(tmp_path / "source", ["owner__repo-1"])
    (source / "owner__repo-1" / "tests" / "test.sh").write_bytes(LITELLM + b"\n")

    built = lanes.build(lanes.REBENCH, source, tmp_path / "lanes")

    assert (built / "owner__repo-1" / "tests" / "test.sh").read_bytes() == LITELLM + b"\n"


def test_harbor_grades_a_rebench_copy_in_a_separate_verifier_and_the_download_in_shared_mode(
    tmp_path: Path,
):
    source = make_rebench(tmp_path / "source", ["owner__repo-1"])

    built = lanes.build(lanes.REBENCH, source, tmp_path / "lanes")

    copied = TaskConfig.model_validate_toml((built / "owner__repo-1" / "task.toml").read_text())
    downloaded = TaskConfig.model_validate_toml((source / "owner__repo-1" / "task.toml").read_text())
    assert resolve_task_verifier_mode(copied) == VerifierEnvironmentMode.SEPARATE
    # Harbor's oracle leaves no patch for a separate verifier, so a check of it takes the download.
    assert resolve_task_verifier_mode(downloaded) == VerifierEnvironmentMode.SHARED


def test_a_rebench_task_toml_gains_one_line_in_its_verifier_table(tmp_path: Path):
    source = make_rebench(tmp_path / "source", ["owner__repo-1"])

    built = lanes.build(lanes.REBENCH, source, tmp_path / "lanes")

    original = (source / "owner__repo-1" / "task.toml").read_text().splitlines()
    copied = (built / "owner__repo-1" / "task.toml").read_text().splitlines()
    assert [line for line in copied if line not in (NO_NETWORK, SEPARATE)] == original
    assert copied[copied.index("[verifier]") + 1] == SEPARATE


def test_a_rebench_task_with_no_verifier_table_gains_one(tmp_path: Path):
    toml = REBENCH_TOML.replace("[verifier]\ntimeout_sec = 3000.0\ncollect = []\n", "")
    source = make_rebench(tmp_path / "source", ["owner__repo-1"], toml)

    built = lanes.build(lanes.REBENCH, source, tmp_path / "lanes")

    expected = toml_of(source / "owner__repo-1")
    expected["agent"]["network_mode"] = "no-network"
    expected["verifier"] = {**expected["verifier"], "environment_mode": "separate"}
    assert toml_of(built / "owner__repo-1") == expected


def test_a_rebench_task_already_graded_in_a_separate_verifier_is_left_as_it_is(tmp_path: Path):
    toml = REBENCH_TOML.replace("[verifier]\n", f"[verifier]\n{SEPARATE}\n")
    source = make_rebench(tmp_path / "source", ["owner__repo-1"], toml)

    built = lanes.build(lanes.REBENCH, source, tmp_path / "lanes")

    copied = (built / "owner__repo-1" / "task.toml").read_text().splitlines()
    assert copied.count(SEPARATE) == 1


@pytest.mark.parametrize(
    "verifier",
    ['environment_mode = "shared"', "environment = { cpus = 2 }"],
    ids=["shared", "its-own-environment"],
)
def test_a_rebench_task_whose_verifier_sets_its_own_sandbox_is_refused_naming_it(
    tmp_path: Path, verifier: str
):
    toml = REBENCH_TOML.replace("[verifier]\n", f"[verifier]\n{verifier}\n")
    source = make_rebench(tmp_path / "source", ["owner__repo-1"], toml)

    with pytest.raises(LaneError) as error:
        lanes.build(lanes.REBENCH, source, tmp_path / "lanes")

    assert str(error.value).startswith(f"{source / 'owner__repo-1' / 'task.toml'}: ")


def test_a_rebench_copy_builds_its_verifier_from_the_tasks_own_dockerfile_and_files(
    tmp_path: Path,
):
    source = make_rebench(tmp_path / "source", ["owner__repo-1"])
    environment = source / "owner__repo-1" / "environment"
    (environment / "Dockerfile").write_text("FROM scratch\nCOPY fix.patch /fix.patch\n")
    (environment / "fix.patch").write_bytes(b"\xff not text\n")
    (environment / "fix.patch").chmod(0o640)
    (environment / "conf").mkdir()
    (environment / "conf" / "a.ini").write_text("a = 1\n")

    built = lanes.build(lanes.REBENCH, source, tmp_path / "lanes") / "owner__repo-1"

    assert files_of(built) == {
        **files_of(source / "owner__repo-1"),
        "tests/Dockerfile": b"FROM scratch\nCOPY fix.patch /fix.patch\n",
        "tests/fix.patch": b"\xff not text\n",
        "tests/conf/a.ini": b"a = 1\n",
    }
    assert oct((built / "tests" / "fix.patch").stat().st_mode) == oct(
        (environment / "fix.patch").stat().st_mode
    )


@pytest.mark.parametrize("name", ["Dockerfile", "config.json"])
def test_a_rebench_tests_file_the_verifiers_build_would_replace_is_refused_naming_it(
    tmp_path: Path, name: str
):
    source = make_rebench(tmp_path / "source", ["owner__repo-1"])
    task = source / "owner__repo-1"
    if name == "Dockerfile":
        (task / "tests" / "Dockerfile").write_text("FROM other\n")
    else:
        (task / "environment" / "config.json").write_text("{}\n")

    with pytest.raises(LaneError) as error:
        lanes.build(lanes.REBENCH, source, tmp_path / "lanes")

    assert str(error.value).startswith(f"{task / 'tests' / name}: ")


def test_a_link_inside_rebench_tests_is_refused_and_what_it_names_left_alone(tmp_path: Path):
    source = make_rebench(tmp_path / "source", ["owner__repo-1"])
    task = source / "owner__repo-1"
    (task / "environment" / "conf").mkdir()
    (task / "environment" / "conf" / "a.ini").write_text("a = 1\n")
    outside = tmp_path / "outside"
    outside.mkdir()
    (task / "tests" / "conf").symlink_to(outside)

    with pytest.raises(LaneError) as error:
        lanes.build(lanes.REBENCH, source, tmp_path / "lanes")

    assert str(error.value).startswith(f"{task / 'tests' / 'conf'}: ")
    assert list(outside.iterdir()) == []


def test_a_nested_verifier_file_is_written_into_a_read_only_tests_directory(tmp_path: Path):
    source = make_rebench(tmp_path / "source", ["owner__repo-1"])
    task = source / "owner__repo-1"
    (task / "environment" / "conf").mkdir()
    (task / "environment" / "conf" / "a.ini").write_text("a = 1\n")
    (task / "tests").chmod(0o555)
    try:
        lanes.build(lanes.REBENCH, source, tmp_path / "lanes")
        built = lanes.build(lanes.REBENCH, source, tmp_path / "lanes") / "owner__repo-1"

        assert (built / "tests" / "conf" / "a.ini").read_text() == "a = 1\n"
        assert oct((built / "tests").stat().st_mode) == oct((task / "tests").stat().st_mode)
    finally:
        # Left read-only, they would stop pytest removing tmp_path.
        for directory in tmp_path.rglob("*"):
            if directory.is_dir() and not directory.is_symlink():
                directory.chmod(0o755)


def test_a_rebench_tests_directory_that_is_a_link_is_refused_and_the_download_left_alone(
    tmp_path: Path,
):
    source = make_rebench(tmp_path / "source", ["owner__repo-1"])
    tests = source / "owner__repo-1" / "tests"
    shutil.move(tests, source / "owner__repo-1" / "real-tests")
    tests.symlink_to("real-tests")
    downloaded = files_of(source)

    with pytest.raises(LaneError) as error:
        lanes.build(lanes.REBENCH, source, tmp_path / "lanes")

    assert str(error.value).startswith(f"{tests}: ")
    assert files_of(source) == downloaded


def test_harbor_reads_a_senior_copy_with_its_own_agent_allowlist_and_a_separate_verifier(
    tmp_path: Path,
):
    source = make_senior(tmp_path / "source")

    built = lanes.build(lanes.SENIOR, source, tmp_path / "lanes")

    for name in (INVESTIGATE, DESIGN, NO_TESTS):
        task = TaskConfig.model_validate_toml((built / name / "task.toml").read_text())
        assert task.agent.network_mode == NetworkMode.ALLOWLIST
        assert "pypi.org" in task.agent.allowed_hosts
        assert resolve_task_verifier_mode(task) == VerifierEnvironmentMode.SEPARATE
        assert task.verifier.network_mode == NetworkMode.PUBLIC


def test_the_judges_margin_is_its_limit_and_its_cleanup():
    assert lanes.JUDGE_MARGIN_SECONDS == lanes.JUDGE_SECONDS + lanes.JUDGE_CLEANUP_SECONDS == 2100


@pytest.mark.parametrize(
    "name, timeout", [(INVESTIGATE, 600.0), (DESIGN, 1800.0), (NO_TESTS, 1200.0)]
)
def test_each_senior_verifier_timeout_gains_the_judges_margin_and_nothing_else_changes(
    tmp_path: Path, name: str, timeout: float
):
    source = make_senior(tmp_path / "source")

    built = lanes.build(lanes.SENIOR, source, tmp_path / "lanes") / name

    original = toml_of(source / name)
    assert original["verifier"]["timeout_sec"] == timeout
    expected = {
        **original,
        "verifier": {**original["verifier"], "environment_mode": "separate", "timeout_sec": timeout + 2100},
    }
    assert toml_of(built) == expected, "the verifier cannot hold both the judge and the task's tests"
    before = (source / name / "task.toml").read_text().splitlines()
    after = (built / "task.toml").read_text().splitlines()
    assert [line for line in after if line not in before] == [SEPARATE, f"timeout_sec = {timeout + 2100}"]


def test_the_shortest_senior_verifier_timeout_leaves_its_tests_their_own_time(tmp_path: Path):
    source = make_senior(tmp_path / "source")
    toml = source / INVESTIGATE / "task.toml"
    toml.write_text(toml.read_text().replace("timeout_sec = 600.0", "timeout_sec = 300"))

    built = lanes.build(lanes.SENIOR, source, tmp_path / "lanes") / INVESTIGATE

    task = TaskConfig.model_validate_toml((built / "task.toml").read_text())
    assert task.verifier.timeout_sec == 300 + lanes.JUDGE_SECONDS + lanes.JUDGE_CLEANUP_SECONDS


def test_a_senior_verifier_with_no_timeout_gains_the_margin_on_harbors_default(tmp_path: Path):
    source = make_senior(tmp_path / "source")
    toml = source / INVESTIGATE / "task.toml"
    toml.write_text(toml.read_text().replace("timeout_sec = 600.0\n", "", 1))
    assert "timeout_sec" not in toml_of(source / INVESTIGATE)["verifier"]

    built = lanes.build(lanes.SENIOR, source, tmp_path / "lanes") / INVESTIGATE

    assert toml_of(built)["verifier"]["timeout_sec"] == 600.0 + 2100


@pytest.mark.parametrize("value", ['"600"', "true", "0"])
def test_a_senior_verifier_timeout_that_is_not_a_positive_number_is_refused_naming_it(
    tmp_path: Path, value: str
):
    source = make_senior(tmp_path / "source")
    toml = source / INVESTIGATE / "task.toml"
    toml.write_text(toml.read_text().replace("timeout_sec = 600.0", f"timeout_sec = {value}"))

    with pytest.raises(LaneError) as error:
        lanes.check_buildable(lanes.SENIOR, source, tmp_path / "lanes")

    assert str(error.value).startswith(f"{toml}: [verifier] timeout_sec is ")


def test_a_senior_copy_builds_its_verifier_from_the_tasks_own_dockerfile_and_files(tmp_path: Path):
    source = make_senior(tmp_path / "source")
    environment = source / DESIGN / "environment"
    # As teleport-add-traits-matching-logic's Dockerfile copies a patch from environment/.
    (environment / "proto_plumbing.patch").write_text("diff --git a/x b/x\n")

    built = lanes.build(lanes.SENIOR, source, tmp_path / "lanes") / DESIGN

    assert (built / "tests" / "proto_plumbing.patch").read_text() == "diff --git a/x b/x\n"
    assert (built / "tests" / "Dockerfile").read_bytes() == (environment / "Dockerfile").read_bytes()


def test_the_lane_checks_read_a_senior_tasks_memory_as_harbor_does(tmp_path: Path):
    source = make_senior(tmp_path / "source")
    read = lanes.read_tasks(lanes.lane_tasks(lanes.SENIOR, source))

    # 4 CPUs and 8G in the no-test task, 2 and 4G in the design task, 2 and 8G in the other.
    assert lanes.needs(read) == (4, 8192)
    assert lanes.needs({task: read[task] for task in read if task.name == DESIGN}) == (2, 4096)


@pytest.mark.parametrize("memory", ['"8 gigabytes"', "8"])
def test_a_memory_harbor_cannot_read_is_an_error_naming_the_task(tmp_path: Path, memory: str):
    source = make_senior(tmp_path / "source")
    toml = source / DESIGN / "task.toml"
    toml.write_text(toml.read_text().replace('memory = "4G"', f"memory = {memory}"))

    with pytest.raises(LaneError) as error:
        lanes.needs(lanes.read_tasks([source / DESIGN]))

    assert str(error.value).startswith(f"{toml}: [environment] memory is ")
