"""
Grade trials through ReplayVerifier, in the stand-in environment.

Each test lays out, on the host, the agent trial as Harbor leaves it, with the
trial's agent/model.patch, and the verifier's sandbox: its workspace, a git
repository, and the patch in its artifacts directory.
"""

import asyncio
import hashlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest
from harbor.models.task.task import Task
from harbor.models.trial.config import VerifierConfig
from harbor.models.trial.paths import TrialPaths
from harbor.models.verifier.result import VerifierResult
from harbor.verifier.factory import VerifierFactory
from harbor.verifier.verifier import Verifier

import swe_drill.replay
from swe_drill.replay import ReplayError, ReplayVerifier, is_test_file

TASK = "gleam-lang__gleam-5797"
# Git sees no NUL as text, and writes these bytes into the patch raw, where
# passing the patch through decoded text corrupts them.
HIGH_BYTES = bytes(range(0x80, 0x100)) + b"\n"
LINES = "".join(f"line {n}\n" for n in range(1, 21))


def go(coroutine):
    return asyncio.run(coroutine)


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout


@pytest.fixture
def workspace(tmp_path) -> Path:
    """The task's repository at its base, with one file of twenty numbered lines."""
    repo = tmp_path / "sandbox" / "gleam"
    repo.mkdir(parents=True)
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.com")
    (repo / "lines.txt").write_text(LINES)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "base")
    return repo


@pytest.fixture
def found(monkeypatch, workspace) -> list[tuple]:
    """Stand find_workspace in for the sandbox's starting directory, and record each call."""
    calls: list[tuple] = []

    async def find_workspace(*args, **kwargs):
        calls.append((args, kwargs))
        return str(workspace)

    monkeypatch.setattr(swe_drill.replay, "find_workspace", find_workspace)
    return calls


def patch_of(workspace: Path, change) -> bytes:
    """The patch Adapter would capture for `change`, leaving the workspace at its base."""
    change(workspace)
    git(workspace, "add", "-A")
    patch = subprocess.run(
        ["git", "diff", "--cached", "--binary", "HEAD"],
        cwd=workspace,
        check=True,
        capture_output=True,
    ).stdout
    git(workspace, "reset", "-q", "--hard", "HEAD")
    git(workspace, "clean", "-qfd")
    return patch


def edit_line(number: int, text: str):
    def change(repo: Path) -> None:
        lines = (repo / "lines.txt").read_text().splitlines(keepends=True)
        lines[number - 1] = f"{text}\n"
        (repo / "lines.txt").write_text("".join(lines))

    return change


def replayed(tmp_path: Path, environment, patch: bytes) -> dict:
    """Apply `patch` and put the test files back as ReplayVerifier does, then return its replay.json."""
    verifier = replay_verifier(tmp_path, environment)
    target = tmp_path / "sandbox" / "logs" / "artifacts" / "model.patch"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(patch)
    go(verifier._replay())
    return recorded(tmp_path)


def test_finds_the_workspace_with_the_environment_alone(
    tmp_path, local_environment, workspace, found
):
    replayed(tmp_path, local_environment, patch_of(workspace, edit_line(3, "ours")))

    assert found == [((local_environment,), {})]


def test_applies_bytes_that_are_not_utf8_exactly(tmp_path, local_environment, workspace, found):
    def add_high_bytes(repo: Path) -> None:
        (repo / "latin1.txt").write_bytes(HIGH_BYTES)

    patch = patch_of(workspace, add_high_bytes)

    record = replayed(tmp_path, local_environment, patch)

    assert record["apply_rc"] == 0
    assert (workspace / "latin1.txt").read_bytes() == HIGH_BYTES


def methods_run(record: dict) -> list[str]:
    return [line[2:] for line in record["apply_out"].splitlines() if line.startswith("$ ")]


def test_a_patch_that_applies_cleanly_runs_only_git_apply(
    tmp_path, local_environment, workspace, found
):
    patch = patch_of(workspace, edit_line(13, "ours"))

    record = replayed(tmp_path, local_environment, patch)

    assert methods_run(record) == ["git apply --verbose"]
    assert record["apply_rc"] == 0
    assert "Applied patch lines.txt cleanly" in record["apply_out"]


def test_a_patch_whose_context_moved_is_refused_not_merged(
    tmp_path, local_environment, workspace, found
):
    patch = patch_of(workspace, edit_line(12, "ours"))
    # A context line of the hunk differs from the patch's, though git could merge it.
    edit_line(10, "moved")(workspace)
    git(workspace, "commit", "-qam", "moved")

    record = replayed(tmp_path, local_environment, patch)

    assert methods_run(record) == ["git apply --verbose"]
    assert record["apply_rc"] != 0
    assert (workspace / "lines.txt").read_text().splitlines()[11] == "line 12"


def test_a_patch_nothing_applies_records_the_failure_and_returns(
    tmp_path, local_environment, workspace, found
):
    hand_written = b"--- a/absent.txt\n+++ b/absent.txt\n@@ -1,1 +1,1 @@\n-gone\n+ours\n"
    patch = hand_written

    record = replayed(tmp_path, local_environment, patch)

    assert methods_run(record) == ["git apply --verbose"]
    assert record["apply_rc"] != 0
    assert "absent.txt" in record["apply_out"]


def test_each_methods_output_follows_its_own_line_where_stderr_is_kept_apart(
    tmp_path, modal_environment, workspace, found
):
    hand_written = b"--- a/absent.txt\n+++ b/absent.txt\n@@ -1,1 +1,1 @@\n-gone\n+ours\n"
    patch = hand_written

    record = replayed(tmp_path, modal_environment, patch)

    assert record["apply_out"].startswith("$ git apply --verbose\n"), record["apply_out"]
    assert "error: absent.txt" in record["apply_out"], record["apply_out"]


@pytest.mark.parametrize(
    "path",
    [
        "tests/issues/GH7798.test.ts",
        "internal/spec/spec_test.go",
        "libs/agno/tests/unit/team/test_run.py",
        "core/src/test/java/io/kestra/core/utils/LogsTest.java",
        "hugegraph-test/src/main/java/org/apache/hugegraph/core/CountStrategyCoreTest.java",
        "src/XMLTests.java",
        "packages/core/__tests__/unsetAllMarks.spec.ts",
        "packages/api/src/agents/tools.spec.ts",
        "crates/extract/src/visitor/tests.rs",
        "crates/coral-spec/src/v4/openapi_tests.rs",
        "crates/ruff_linter/src/rules/ruff/snapshots/ruff_linter__rules__ruff__tests__ruf100_5.snap",
        "crates/oxc_semantic/tests/fixtures/oxc/ts/decorators/parameter-decorator-shadow.snap",
        "testdata/baselines/reference/compiler/isolatedDeclarations.types",
        "crates/ty_python_semantic/resources/mdtest/generics/legacy/variance.md",
        "test-parity/node-suite/node-core/namespace-absent-member-read.ts",
        "test262/harness/assert.js",
    ],
)
def test_a_path_with_a_test_word_or_a_js_spec_name_is_a_test_file(path):
    assert is_test_file(path)


@pytest.mark.parametrize(
    "path",
    [
        "internal/spec/spec.go",
        "crates/coral-spec/src/v4/surfaces/openapi/schemas.rs",
        "langchain4j-core/src/main/java/dev/langchain4j/agent/tool/ToolSpecifications.java",
        "crates/rust-analyzer/src/target_spec.rs",
        "packages/data-schemas/tsconfig.spec.json",
        "tasks/coverage/snapshots/semantic_typescript.snap",
        "server/src/main/java/com/arcadedb/server/http/handler/GetTimeSeriesLatestHandler.java",
        "feature/tpm/attestation.go",
        "net/speedtest/speedtest.go",
        "crates/ty_vendored/vendor/typeshed/stdlib/unittest/case.pyi",
        "src/main/java/TestedModels.java",
        "crates/ruff_db/src/testing.rs",
    ],
)
def test_a_path_with_no_test_word_and_no_js_spec_name_is_not_a_test_file(path):
    assert not is_test_file(path)


def commit(repo: Path, files: dict[str, bytes]) -> None:
    """Add `files` to the base, as the task's repository had them."""
    write(files)(repo)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "tests")


def write(files: dict[str, bytes | None]):
    """A change that writes each file, or deletes it where the content is None."""

    def change(repo: Path) -> None:
        for name, content in files.items():
            if content is None:
                (repo / name).unlink()
            else:
                (repo / name).parent.mkdir(parents=True, exist_ok=True)
                (repo / name).write_bytes(content)

    return change


def regrade(tmp_path, environment, workspace, change) -> dict:
    return replayed(tmp_path, environment, patch_of(workspace, change))


def test_a_test_file_the_patch_edited_is_graded_as_it_was_at_the_base(
    tmp_path, local_environment, workspace, found
):
    commit(workspace, {"tests/test_lines.py": b"assert snapshot_written\n"})
    change = write({"tests/test_lines.py": b"assert not snapshot_written\n", "lines.txt": b"fix\n"})

    record = regrade(tmp_path, local_environment, workspace, change)

    assert (workspace / "tests/test_lines.py").read_bytes() == b"assert snapshot_written\n"
    assert (workspace / "lines.txt").read_bytes() == b"fix\n"
    assert record["tests_reset"] == {"restored": ["tests/test_lines.py"], "removed": []}
    assert git(workspace, "status", "--porcelain") == " M lines.txt\n"


def test_a_test_file_the_patch_deleted_comes_back(tmp_path, local_environment, workspace, found):
    commit(workspace, {"src/test/java/LinesTest.java": b"class LinesTest {}\n"})

    record = regrade(
        tmp_path, local_environment, workspace, write({"src/test/java/LinesTest.java": None})
    )

    assert (workspace / "src/test/java/LinesTest.java").read_bytes() == b"class LinesTest {}\n"
    assert record["tests_reset"] == {"restored": ["src/test/java/LinesTest.java"], "removed": []}
    assert git(workspace, "status", "--porcelain") == ""


def test_a_test_file_the_patch_added_is_removed(tmp_path, local_environment, workspace, found):
    change = write({"pkg/lines_test.go": b"package lines\n", "lines.txt": b"fix\n"})

    record = regrade(tmp_path, local_environment, workspace, change)

    assert not (workspace / "pkg/lines_test.go").exists()
    assert record["tests_reset"] == {"restored": [], "removed": ["pkg/lines_test.go"]}
    assert git(workspace, "status", "--porcelain") == " M lines.txt\n"


def test_a_renamed_test_file_is_back_under_its_old_name_and_gone_from_its_new(
    tmp_path, local_environment, workspace, found
):
    commit(workspace, {"tests/test_old.py": b"assert True\n" * 20})

    def rename(repo: Path) -> None:
        (repo / "tests/test_old.py").rename(repo / "tests/test_new.py")

    record = regrade(tmp_path, local_environment, workspace, rename)

    assert record["tests_reset"] == {
        "restored": ["tests/test_old.py"],
        "removed": ["tests/test_new.py"],
    }
    assert git(workspace, "status", "--porcelain") == ""


@pytest.mark.parametrize("name", ["tests/conftest.py", "tests/odd name ü.py"])
def test_a_test_file_the_patch_added_and_ignored_is_removed(
    tmp_path, local_environment, workspace, found, name
):
    def add_ignored(repo: Path) -> None:
        write({name: b"import pytest\n", ".gitignore": f"/{name}\n".encode()})(repo)
        git(repo, "add", "-f", name)

    record = regrade(tmp_path, local_environment, workspace, add_ignored)

    assert not (workspace / name).exists()
    assert record["tests_reset"] == {"restored": [], "removed": [name]}


def test_an_ignored_test_file_the_image_had_stays_when_the_patch_drops_its_ignore_rule(
    tmp_path, local_environment, workspace, found
):
    commit(workspace, {".gitignore": b"/tests/generated_test.py\n"})
    patch = patch_of(workspace, write({".gitignore": b""}))
    write({"tests/generated_test.py": b"built by the image\n"})(workspace)

    record = replayed(tmp_path, local_environment, patch)

    assert (workspace / "tests/generated_test.py").read_bytes() == b"built by the image\n"
    assert record["tests_reset"] == {"restored": [], "removed": []}


def test_an_ignored_test_file_the_image_had_stays_when_the_patch_tries_to_add_it(
    tmp_path, local_environment, workspace, found
):
    commit(workspace, {".gitignore": b"/tests/generated_test.py\n"})

    def add_ignored(repo: Path) -> None:
        write({"tests/generated_test.py": b"the harness's\n"})(repo)
        git(repo, "add", "-f", "tests/generated_test.py")

    patch = patch_of(workspace, add_ignored)
    write({"tests/generated_test.py": b"built by the image\n"})(workspace)

    record = replayed(tmp_path, local_environment, patch)

    assert (workspace / "tests/generated_test.py").exists()
    assert record["tests_reset"] == {"restored": [], "removed": []}


@pytest.mark.parametrize("name", ["tests/fixtures/blob.bin", "tests/fixtures/odd ü.bin"])
def test_a_binary_test_file_the_patch_added_and_ignored_is_removed(
    tmp_path, local_environment, workspace, found, name
):
    def add_ignored(repo: Path) -> None:
        write({name: b"\0" + HIGH_BYTES, ".gitignore": f"/{name}\n".encode()})(repo)
        git(repo, "add", "-f", name)

    record = regrade(tmp_path, local_environment, workspace, add_ignored)

    assert not (workspace / name).exists()
    assert record["tests_reset"] == {"restored": [], "removed": [name]}


def test_a_test_file_the_patch_replaced_with_a_directory_comes_back_as_a_file(
    tmp_path, local_environment, workspace, found
):
    commit(workspace, {"tests/cases": b"case one\n"})

    def replace(repo: Path) -> None:
        (repo / "tests/cases").unlink()
        write({"tests/cases/one_test.py": b"assert True\n"})(repo)

    record = regrade(tmp_path, local_environment, workspace, replace)

    assert (workspace / "tests/cases").read_bytes() == b"case one\n"
    assert record["tests_reset"] == {
        "restored": ["tests/cases"],
        "removed": ["tests/cases/one_test.py"],
    }


def test_a_binary_test_file_is_restored_byte_for_byte(
    tmp_path, local_environment, workspace, found
):
    fixture = bytes(range(256)) * 4
    commit(workspace, {"tests/fixtures/image.bin": fixture})

    record = regrade(
        tmp_path, local_environment, workspace, write({"tests/fixtures/image.bin": fixture[::-1]})
    )

    assert (workspace / "tests/fixtures/image.bin").read_bytes() == fixture
    assert record["tests_reset"]["restored"] == ["tests/fixtures/image.bin"]


def test_a_test_file_whose_name_needs_quoting_is_restored_and_removed(
    tmp_path, local_environment, workspace, found
):
    edited, added = "tests/[ab]* ü.py", 'tests/new "x"\t.py'
    commit(workspace, {edited: b"base\n", "tests/a.py": b"untouched\n"})

    record = regrade(
        tmp_path, local_environment, workspace, write({edited: b"edited\n", added: b"added\n"})
    )

    assert (workspace / edited).read_bytes() == b"base\n"
    assert (workspace / "tests/a.py").read_bytes() == b"untouched\n"
    assert not (workspace / added).exists()
    assert record["tests_reset"] == {"restored": [edited], "removed": [added]}
    assert git(workspace, "status", "--porcelain") == ""


def test_a_test_file_the_image_had_changed_and_the_patch_did_not_touch_is_left_alone(
    tmp_path, local_environment, workspace, found
):
    commit(workspace, {"tests/test_lines.py": b"base\n", "tests/test_other.py": b"other\n"})
    patch = patch_of(workspace, write({"lines.txt": b"fix\n"}))
    # The image starts with these, before any patch.
    write({"tests/test_lines.py": b"image\n", "tests/test_image.py": b"image\n"})(workspace)

    record = replayed(tmp_path, local_environment, patch)

    assert (workspace / "tests/test_lines.py").read_bytes() == b"image\n"
    assert (workspace / "tests/test_image.py").read_bytes() == b"image\n"
    assert (workspace / "tests/test_other.py").read_bytes() == b"other\n"
    assert record["tests_reset"] == {"restored": [], "removed": []}


def test_a_test_file_the_image_had_changed_and_the_patch_edited_is_graded_as_the_image_had_it(
    tmp_path, local_environment, workspace, found
):
    commit(workspace, {"tests/test_lines.py": b"base\n", "tests/test_image.py": b"base\n"})
    image = {"tests/test_lines.py": b"image\n", "tests/test_image.py": None,
             "tests/test_new.py": b"image\n"}
    # The patch runs from the starting tree, so it is taken with the image's
    # changes committed, which are then left uncommitted as the image has them.
    write(image)(workspace)
    git(workspace, "add", "-A")
    git(workspace, "commit", "-q", "-m", "image")
    change = write({"tests/test_lines.py": b"harness\n", "tests/test_new.py": None,
                    "tests/test_image.py": b"harness\n"})
    patch = patch_of(workspace, change)
    git(workspace, "reset", "-q", "HEAD~1")

    record = replayed(tmp_path, local_environment, patch)

    assert (workspace / "tests/test_lines.py").read_bytes() == b"image\n"
    assert (workspace / "tests/test_new.py").read_bytes() == b"image\n"
    assert not (workspace / "tests/test_image.py").exists()
    assert record["tests_reset"] == {
        "restored": ["tests/test_lines.py", "tests/test_new.py"],
        "removed": ["tests/test_image.py"],
    }
    assert git(workspace, "status", "--porcelain") == (
        " D tests/test_image.py\n M tests/test_lines.py\n?? tests/test_new.py\n"
    )


def test_a_patch_that_touches_no_test_file_records_two_empty_lists(
    tmp_path, local_environment, workspace, found
):
    record = regrade(tmp_path, local_environment, workspace, edit_line(3, "ours"))

    assert list(record) == ["patch", "patch_bytes", "sha256", "apply_rc", "apply_out", "tests_reset"]
    assert record["apply_rc"] == 0
    assert record["tests_reset"] == {"restored": [], "removed": []}
    assert (workspace / "lines.txt").read_text().splitlines()[2] == "ours"


def test_a_reset_git_refuses_is_an_error_naming_what_git_printed(
    tmp_path, modal_environment, workspace, found, monkeypatch
):
    commit(workspace, {"tests/test_lines.py": b"base\n"})
    patch = patch_of(workspace, write({"tests/test_lines.py": b"edited\n"}))
    exec_ = modal_environment.exec

    async def lock_the_index_after_applying(command, **kwargs):
        result = await exec_(command, **kwargs)
        if "git apply --verbose" in command:
            (workspace / ".git" / "index.lock").touch()
        return result

    monkeypatch.setattr(modal_environment, "exec", lock_the_index_after_applying)

    with pytest.raises(ReplayError, match="index.lock"):
        replayed(tmp_path, modal_environment, patch)
    assert recorded(tmp_path) == {"patch": None}


# ReplayVerifier as Harbor runs it, with test.sh after the apply and the reset.
#
# The verifier's sandbox holds the workspace at its base and the agent trial's
# patch in its artifacts directory. The host holds the trial's directory, with
# the agent/model.patch Harbor downloaded from the agent's sandbox.

AGENT_TRIAL = "gleam-lang__gleam-57__XyZ1234"


@dataclass
class Graded:
    record: dict | None
    # The workspace's files as the task's test.sh found them, or None when it never ran.
    seen: dict[str, bytes | None] | None
    result: VerifierResult | None


def a_task(root: Path) -> Task:
    task = root / "task"
    (task / "environment").mkdir(parents=True)
    (task / "environment" / "Dockerfile").write_text("FROM scratch\n")
    (task / "tests").mkdir()
    (task / "tests" / "test.sh").write_text("#!/bin/bash\n")
    (task / "instruction.md").write_text("Fix it.\n")
    (task / "task.toml").write_text('[verifier]\nenvironment_mode = "separate"\n')
    return Task(task)


def replay_verifier(tmp_path: Path, environment) -> ReplayVerifier:
    """A ReplayVerifier for the agent's trial, built as Harbor builds a separate verifier."""
    paths = TrialPaths(tmp_path / "agent-job" / AGENT_TRIAL)
    paths.mkdir()
    return VerifierFactory.create_verifier_from_config(
        VerifierConfig(import_path="swe_drill.replay:ReplayVerifier"),
        task=a_task(tmp_path),
        trial_paths=paths,
        environment=environment,
        skip_tests_upload=True,
        artifacts=str(tmp_path / "sandbox" / "logs" / "artifacts"),
    )


def grade(
    tmp_path,
    environment,
    workspace,
    monkeypatch,
    artifact: bytes | None,
    agents: bytes | None = None,
    watched: tuple[str, ...] = ("lines.txt",),
) -> Graded:
    """
    Grade the trial whose artifact is `artifact`, and whose agent/model.patch on
    the host is `agents`, or the artifact where that is None.

    Harbor's own verifier is stood in for by one that reads `watched` as the
    task's test.sh would find them.
    """
    verifier = replay_verifier(tmp_path, environment)
    if artifact is not None:
        target = tmp_path / "sandbox" / "logs" / "artifacts" / "model.patch"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(artifact)
    agents = agents if agents is not None else artifact
    if agents is not None:
        verifier.trial_paths.agent_dir.mkdir(parents=True, exist_ok=True)
        (verifier.trial_paths.agent_dir / "model.patch").write_bytes(agents)
    seen: dict[str, bytes | None] | None = None

    async def run_test_sh(self) -> VerifierResult:
        nonlocal seen
        seen = {
            name: (workspace / name).read_bytes() if (workspace / name).exists() else None
            for name in watched
        }
        return VerifierResult(rewards={"reward": 1.0})

    monkeypatch.setattr(Verifier, "verify", run_test_sh)
    result = go(verifier.verify())
    return Graded(recorded(tmp_path), seen, result)


def recorded(tmp_path: Path) -> dict | None:
    """The trial's verifier/replay.json, or None when the verifier wrote none."""
    path = tmp_path / "agent-job" / AGENT_TRIAL / "verifier" / "replay.json"
    return json.loads(path.read_text()) if path.exists() else None


def test_the_verifier_applies_the_artifact_and_puts_test_files_back_before_test_sh(
    tmp_path, local_environment, workspace, found, monkeypatch
):
    commit(workspace, {"tests/test_lines.py": b"assert snapshot_written\n"})
    patch = patch_of(
        workspace,
        write({"tests/test_lines.py": b"assert not snapshot_written\n", "lines.txt": b"fix\n"}),
    )

    graded = grade(
        tmp_path, local_environment, workspace, monkeypatch, patch,
        watched=("lines.txt", "tests/test_lines.py"),
    )

    assert graded.seen == {
        "lines.txt": b"fix\n",
        "tests/test_lines.py": b"assert snapshot_written\n",
    }, "test.sh ran on a workspace without the patch, or with the patch's test files"
    assert graded.result.rewards == {"reward": 1.0}
    assert graded.record == {
        "patch": str(tmp_path / "sandbox" / "logs" / "artifacts" / "model.patch"),
        "patch_bytes": len(patch),
        "sha256": hashlib.sha256(patch).hexdigest(),
        "apply_rc": 0,
        "apply_out": graded.record["apply_out"],
        "tests_reset": {"restored": ["tests/test_lines.py"], "removed": []},
    }


def test_the_verifier_applies_bytes_that_are_not_utf8_exactly(
    tmp_path, local_environment, workspace, found, monkeypatch
):
    patch = patch_of(workspace, write({"high.bin": HIGH_BYTES}))

    graded = grade(
        tmp_path, local_environment, workspace, monkeypatch, patch, watched=("high.bin",)
    )

    assert graded.seen == {"high.bin": HIGH_BYTES}


def test_a_trial_that_left_no_artifact_records_null_and_runs_no_test(
    tmp_path, local_environment, workspace, found, monkeypatch
):
    with pytest.raises(ReplayError, match="no patch"):
        grade(tmp_path, local_environment, workspace, monkeypatch, None)

    assert recorded(tmp_path) == {"patch": None}


def test_an_artifact_other_than_the_agents_patch_records_null_and_runs_no_test(
    tmp_path, local_environment, workspace, found, monkeypatch
):
    patch = patch_of(workspace, edit_line(3, "ours"))

    with pytest.raises(ReplayError, match="differs"):
        grade(tmp_path, local_environment, workspace, monkeypatch, b"", agents=patch)

    assert recorded(tmp_path) == {"patch": None}
    assert git(workspace, "status", "--porcelain") == ""


def test_an_empty_artifact_records_null_and_grades_the_unchanged_workspace(
    tmp_path, local_environment, workspace, found, monkeypatch
):
    graded = grade(tmp_path, local_environment, workspace, monkeypatch, b"")

    assert graded.record == {"patch": None}
    assert graded.seen == {"lines.txt": LINES.encode()}


def test_a_patch_the_verifier_cannot_apply_is_recorded_and_still_graded(
    tmp_path, local_environment, workspace, found, monkeypatch
):
    patch = patch_of(workspace, edit_line(3, "ours"))
    write({"lines.txt": b"moved on\n"})(workspace)
    git(workspace, "commit", "-qam", "the base moved")

    graded = grade(tmp_path, local_environment, workspace, monkeypatch, patch)

    assert graded.record["apply_rc"] != 0
    assert graded.seen == {"lines.txt": b"moved on\n"}
