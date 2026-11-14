import asyncio
import stat
import subprocess
from pathlib import Path

import pytest

from swe_drill.workspace import (
    WorkspaceError,
    capture_patch,
    find_workspace,
    read_base,
    read_start_tree,
)

# Git sees a NUL as binary, and writes the file into the patch encoded.
BINARY = bytes(range(256)) * 4
# Git sees no NUL as text, and writes these bytes into the patch raw, where
# passing the patch through decoded text corrupts them.
HIGH_BYTES = bytes(range(0x80, 0x100)) + b"\n"


def run(coroutine):
    return asyncio.run(coroutine)


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout


def tree(root: Path) -> dict[str, tuple[bytes, bool]]:
    """Every file under root outside .git, with its bytes and whether it is executable."""
    files = {}
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if relative.parts[0] == ".git" or not path.is_file():
            continue
        executable = bool(path.stat().st_mode & stat.S_IXUSR)
        files[relative.as_posix()] = (path.read_bytes(), executable)
    return files


@pytest.fixture
def repo(tmp_path) -> Path:
    """A workspace at its base: a text file, a file to delete, a script to make executable, and an existing binary."""
    repo = tmp_path / "app"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.com")
    (repo / "keep.txt").write_text("one\n")
    (repo / "gone.txt").write_text("doomed\n")
    (repo / "script.sh").write_text("echo hi\n")
    (repo / "old.bin").write_bytes(BINARY)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "base")
    return repo


@pytest.fixture
def logs(tmp_path) -> Path:
    return tmp_path / "logs"


def change_everything(repo: Path) -> None:
    """Make every kind of change a patch must carry: edit, delete, add, binary, high bytes, mode."""
    (repo / "keep.txt").write_text("one\ntwo\n")
    (repo / "gone.txt").unlink(missing_ok=True)
    (repo / "new.txt").write_text("fresh\n")
    (repo / "latin1.txt").write_bytes(HIGH_BYTES)
    (repo / "dir").mkdir(exist_ok=True)
    (repo / "dir" / "new.bin").write_bytes(b"\x00\xff\x80" + BINARY)
    (repo / "old.bin").write_bytes(BINARY[::-1])
    (repo / "script.sh").chmod(0o755)


def leave_in_working_tree(repo: Path) -> None:
    change_everything(repo)


def stage(repo: Path) -> None:
    change_everything(repo)
    git(repo, "add", "-A")


def commit_once(repo: Path) -> None:
    change_everything(repo)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "all")


def commit_several(repo: Path) -> None:
    (repo / "keep.txt").write_text("one\ntwo\n")
    git(repo, "commit", "-q", "-am", "first")
    (repo / "gone.txt").unlink()
    (repo / "new.txt").write_text("fresh\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "second")
    change_everything(repo)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "third")


def apply_to_fresh_clone(repo: Path, base: str, patch: Path, into: Path) -> Path:
    subprocess.run(["git", "clone", "-q", str(repo), str(into)], check=True)
    git(into, "checkout", "-q", base)
    git(into, "apply", "--binary", str(patch))
    return into


@pytest.mark.parametrize(
    "harness", [leave_in_working_tree, stage, commit_once, commit_several]
)
def test_patch_rebuilds_the_harness_tree_from_the_base(
    local_environment, repo, logs, tmp_path, harness
):
    base = run(read_base(local_environment, str(repo)))
    start = run(read_start_tree(local_environment, str(repo)))
    harness(repo)
    size = run(capture_patch(local_environment, str(repo), start, str(logs)))

    patch = logs / "model.patch"
    assert size == patch.stat().st_size
    rebuilt = apply_to_fresh_clone(repo, base, patch, tmp_path / "rebuilt")
    assert tree(rebuilt) == tree(repo), (
        "applying model.patch at the base does not give the harness's tree"
    )


@pytest.mark.parametrize(
    "setting", ["diff.noprefix=true", "color.ui=always"]
)
def test_the_repository_config_cannot_change_the_patch(
    local_environment, repo, logs, tmp_path, setting
):
    key, value = setting.split("=")
    git(repo, "config", key, value)
    base = run(read_base(local_environment, str(repo)))
    change_everything(repo)
    run(capture_patch(local_environment, str(repo), base, str(logs)))

    rebuilt = apply_to_fresh_clone(repo, base, logs / "model.patch", tmp_path / "rebuilt")
    assert tree(rebuilt) == tree(repo), f"with {setting}, model.patch does not apply"


def test_the_base_is_head_before_the_harness_runs(local_environment, repo):
    head = git(repo, "rev-parse", "HEAD").strip()
    assert run(read_base(local_environment, str(repo))) == head


def test_no_change_gives_an_empty_patch(local_environment, repo, logs):
    base = run(read_base(local_environment, str(repo)))
    size = run(capture_patch(local_environment, str(repo), base, str(logs)))

    patch = logs / "model.patch"
    assert patch.exists()
    assert patch.read_bytes() == b""
    assert size == 0


def test_capture_raises_outside_a_repository(local_environment, tmp_path, logs):
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(WorkspaceError):
        run(capture_patch(local_environment, str(plain), "HEAD", str(logs)))


def test_capture_raises_for_a_start_that_is_not_a_tree(local_environment, repo, logs):
    change_everything(repo)
    with pytest.raises(WorkspaceError):
        run(capture_patch(local_environment, str(repo), "0" * 40, str(logs)))


def test_read_base_raises_outside_a_repository(local_environment, tmp_path):
    with pytest.raises(WorkspaceError):
        run(read_base(local_environment, str(tmp_path)))


def dirty_the_image(repo: Path) -> None:
    """
    Leave the changes a task's image can hold before the trial starts: an edit,
    a staged edit, a deletion and an untracked file. The harness edits keep.txt
    again.
    """
    (repo / "keep.txt").write_text("zero\none\n")
    (repo / "pom.xml").write_text("<version>1.18.38</version>\n")
    (repo / "script.sh").write_text("echo hello\n")
    git(repo, "add", "script.sh")
    (repo / "gone.txt").unlink()
    (repo / "uv.lock").write_text("pinned\n")


def edit_on_top(repo: Path) -> None:
    (repo / "keep.txt").write_text("zero\none\ntwo\n")
    (repo / "new.txt").write_text("fresh\n")


def stage_on_top(repo: Path) -> None:
    edit_on_top(repo)
    git(repo, "add", "-A")


def commit_on_top(repo: Path) -> None:
    edit_on_top(repo)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "harness")


def apply_to_the_image(repo: Path, base: str, patch: Path, into: Path) -> Path:
    """A fresh clone at the base, holding the image's changes, as a regrade starts; then the patch."""
    subprocess.run(["git", "clone", "-q", str(repo), str(into)], check=True)
    git(into, "checkout", "-q", base)
    dirty_the_image(into)
    git(into, "apply", "--binary", str(patch))
    return into


def files_named(patch: Path) -> set[str]:
    return {
        line.rsplit(" b/", 1)[-1]
        for line in patch.read_text().splitlines()
        if line.startswith("diff --git ")
    }


@pytest.mark.parametrize("harness", [edit_on_top, stage_on_top, commit_on_top])
def test_the_patch_leaves_out_what_the_image_already_changed(
    local_environment, repo, logs, tmp_path, harness
):
    dirty_the_image(repo)
    base = run(read_base(local_environment, str(repo)))
    start = run(read_start_tree(local_environment, str(repo)))
    harness(repo)
    run(capture_patch(local_environment, str(repo), start, str(logs)))

    patch = logs / "model.patch"
    assert files_named(patch) == {"keep.txt", "new.txt"}, (
        "model.patch holds more than the harness changed"
    )
    rebuilt = apply_to_the_image(repo, base, patch, tmp_path / "rebuilt")
    assert tree(rebuilt) == tree(repo), (
        "applying model.patch to the image's starting tree does not give the harness's tree"
    )


def test_reading_the_start_tree_changes_nothing_the_harness_sees(local_environment, repo):
    dirty_the_image(repo)
    index = repo / ".git" / "index"

    def seen() -> tuple:
        return (
            git(repo, "status", "--porcelain=v2", "--untracked-files=all"),
            git(repo, "ls-files", "--stage"),
            git(repo, "rev-parse", "HEAD"),
            git(repo, "for-each-ref"),
            git(repo, "stash", "list"),
            index.read_bytes(),
        )

    before = seen()
    run(read_start_tree(local_environment, str(repo)))

    assert seen() == before, "reading the start tree changed the workspace the harness sees"


@pytest.mark.parametrize(
    "harness", [leave_in_working_tree, stage, commit_once, commit_several]
)
def test_a_clean_image_gives_the_same_patch_as_a_diff_from_the_base_commit(
    local_environment, repo, logs, harness
):
    base = run(read_base(local_environment, str(repo)))
    start = run(read_start_tree(local_environment, str(repo)))
    harness(repo)
    run(capture_patch(local_environment, str(repo), start, str(logs)))
    from_start = (logs / "model.patch").read_bytes()
    run(capture_patch(local_environment, str(repo), base, str(logs)))

    assert from_start == (logs / "model.patch").read_bytes(), (
        "on a clean image, the patch differs from the one the base commit gives"
    )


def test_files_git_ignores_stay_out_of_the_start_tree_and_the_patch(
    local_environment, repo, logs
):
    (repo / ".gitignore").write_text("*.log\n")
    (repo / "tracked.log").write_text("tracked though ignored\n")
    git(repo, "add", "-f", ".gitignore", "tracked.log")
    git(repo, "commit", "-q", "-m", "ignore logs")
    (repo / "build.log").write_text("noise\n")

    start = run(read_start_tree(local_environment, str(repo)))
    (repo / "more.log").write_text("more noise\n")
    run(capture_patch(local_environment, str(repo), start, str(logs)))

    assert start == git(repo, "rev-parse", "HEAD^{tree}").strip(), (
        "the start tree differs from HEAD's, though the image added only ignored files"
    )
    assert (logs / "model.patch").read_bytes() == b""


@pytest.mark.parametrize("pattern", ["uv.lock", "*.lock", "cache/", "cache/*.txt"])
def test_a_file_the_image_left_untracked_and_the_harness_ignores_stays_in_the_tree(
    local_environment, repo, logs, tmp_path, pattern
):
    def untracked_by_the_image(into: Path) -> None:
        (into / "uv.lock").write_text("pinned\n")
        (into / "cache").mkdir()
        (into / "cache" / "data.txt").write_text("cached\n")

    untracked_by_the_image(repo)
    base = run(read_base(local_environment, str(repo)))
    start = run(read_start_tree(local_environment, str(repo)))
    (repo / ".gitignore").write_text(pattern + "\n")
    run(capture_patch(local_environment, str(repo), start, str(logs)))

    rebuilt = tmp_path / "rebuilt"
    subprocess.run(["git", "clone", "-q", str(repo), str(rebuilt)], check=True)
    git(rebuilt, "checkout", "-q", base)
    untracked_by_the_image(rebuilt)
    git(rebuilt, "apply", "--binary", str(logs / "model.patch"))
    assert tree(rebuilt) == tree(repo), (
        f"ignoring {pattern} made model.patch delete a file the harness left alone"
    )


def test_a_file_the_image_left_untracked_and_the_harness_ignores_and_deletes_is_deleted(
    local_environment, repo, logs
):
    (repo / "uv.lock").write_text("pinned\n")
    start = run(read_start_tree(local_environment, str(repo)))
    (repo / ".gitignore").write_text("uv.lock\n")
    (repo / "uv.lock").unlink()
    run(capture_patch(local_environment, str(repo), start, str(logs)))

    assert "deleted file mode 100644" in (logs / "model.patch").read_text()
    assert files_named(logs / "model.patch") == {".gitignore", "uv.lock"}


def test_an_ignored_starting_file_stays_when_the_harness_adds_a_copy_of_it(
    local_environment, repo, logs
):
    (repo / "uv.lock").write_text("pinned\n")
    start = run(read_start_tree(local_environment, str(repo)))
    (repo / ".gitignore").write_text("uv.lock\n")
    (repo / "saved.lock").write_text("pinned\n")
    run(capture_patch(local_environment, str(repo), start, str(logs)))

    assert files_named(logs / "model.patch") == {".gitignore", "saved.lock"}
    assert "a/uv.lock" not in (logs / "model.patch").read_text()


def test_a_starting_directory_the_harness_replaces_with_a_symlink_is_captured(
    local_environment, repo, logs
):
    (repo / "dir").mkdir()
    (repo / "dir" / "file.txt").write_text("from the image\n")
    (repo / "elsewhere").mkdir()
    (repo / "elsewhere" / "file.txt").write_text("from the image\n")
    start = run(read_start_tree(local_environment, str(repo)))
    (repo / "dir" / "file.txt").unlink()
    (repo / "dir").rmdir()
    (repo / "dir").symlink_to("elsewhere")
    run(capture_patch(local_environment, str(repo), start, str(logs)))

    patch = (logs / "model.patch").read_text()
    assert files_named(logs / "model.patch") == {"dir", "dir/file.txt"}
    assert "new file mode 120000" in patch


def test_an_image_with_no_index_and_no_harness_change_gives_an_empty_patch(
    local_environment, repo, logs
):
    (repo / ".gitignore").write_text("*.log\n")
    (repo / "tracked.log").write_text("tracked though ignored\n")
    git(repo, "add", "-f", ".gitignore", "tracked.log")
    git(repo, "commit", "-q", "-m", "ignore logs")
    (repo / ".git" / "index").unlink()

    start = run(read_start_tree(local_environment, str(repo)))
    run(capture_patch(local_environment, str(repo), start, str(logs)))

    assert (logs / "model.patch").read_bytes() == b""


def test_read_start_tree_raises_outside_a_repository(local_environment, tmp_path):
    with pytest.raises(WorkspaceError):
        run(read_start_tree(local_environment, str(tmp_path)))


def init(repo: Path) -> Path:
    repo.mkdir(parents=True)
    git(repo, "init", "-q")
    git(repo, "-c", "user.name=Test", "-c", "user.email=test@example.com",
        "commit", "-q", "--allow-empty", "-m", "base")
    return repo


def start_in(env, directory: Path) -> None:
    """Make the sandbox start in `directory`, as Harbor does for a task that sets a workdir."""
    env.task_env_config.workdir = str(directory)


@pytest.fixture(params=["local_environment", "modal_environment"])
def env(request):
    """Both stand-ins, since Docker gives None for empty output and Modal gives ""."""
    return request.getfixturevalue(request.param)


def test_the_workspace_is_the_directory_the_sandbox_starts_in(env, tmp_path):
    # A space in the name, so a path left unquoted breaks.
    repo = init(tmp_path / "the gleam")
    start_in(env, repo)
    assert run(find_workspace(env)) == str(repo)


def test_a_start_directory_reached_through_a_symlink_is_its_repository(env, tmp_path):
    repo = init(tmp_path / "gleam")
    link = tmp_path / "link"
    link.symlink_to(repo)
    start_in(env, link)
    assert Path(run(find_workspace(env))).resolve() == repo.resolve()


def test_a_start_directory_with_no_repository_fails_naming_it(env, tmp_path):
    plain = tmp_path / "plain dir"
    plain.mkdir()
    start_in(env, plain)
    with pytest.raises(WorkspaceError) as raised:
        run(find_workspace(env))
    assert str(plain) in str(raised.value), "the error does not name the directory"
    assert "not a git repository" in str(raised.value), "the error drops what git said"


def test_a_start_directory_inside_a_repository_fails_naming_it(env, tmp_path):
    """git apply in a subdirectory skips every path outside it, so a regrade there would drop changes."""
    repo = init(tmp_path / "gleam")
    inside = repo / "src"
    inside.mkdir()
    start_in(env, inside)
    with pytest.raises(WorkspaceError) as raised:
        run(find_workspace(env))
    assert str(inside) in str(raised.value), "the error does not name the directory"
