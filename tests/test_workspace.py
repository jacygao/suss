import asyncio
import os
import stat
import subprocess
from pathlib import Path

import pytest

from suss.workspace import WorkspaceError, capture_patch, find_workspace, read_base

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
    harness(repo)
    size = run(capture_patch(local_environment, str(repo), base, str(logs)))

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


def test_capture_raises_for_a_base_that_is_not_a_commit(local_environment, repo, logs):
    change_everything(repo)
    with pytest.raises(WorkspaceError):
        run(capture_patch(local_environment, str(repo), "0" * 40, str(logs)))


def test_read_base_raises_outside_a_repository(local_environment, tmp_path):
    with pytest.raises(WorkspaceError):
        run(read_base(local_environment, str(tmp_path)))


@pytest.mark.parametrize(
    "present, expected",
    [(["app"], "app"), (["testbed"], "testbed"), (["app", "testbed"], "app")],
)
def test_finds_app_or_else_testbed(local_environment, tmp_path, present, expected):
    for name in present:
        (tmp_path / name).mkdir()
    candidates = (str(tmp_path / "app"), str(tmp_path / "testbed"))
    assert run(find_workspace(local_environment, candidates)) == str(tmp_path / expected)


def test_no_workspace_raises(local_environment, tmp_path):
    candidates = (str(tmp_path / "app"), str(tmp_path / "testbed"))
    with pytest.raises(WorkspaceError):
        run(find_workspace(local_environment, candidates))


def test_the_workspace_is_found_in_the_sandbox_not_on_this_machine(
    local_environment, tmp_path, monkeypatch
):
    """
    This machine is made to look as if it has /app, and the sandbox, stood in
    for by an exec that maps the default paths into tmp_path, has only /testbed.
    """
    on_this_machine = {"/app", "/testbed"}
    for name in ("exists", "is_dir"):
        real = getattr(Path, name)
        monkeypatch.setattr(
            Path, name, lambda self, real=real: str(self) in on_this_machine or real(self)
        )
    for name in ("exists", "isdir"):
        real = getattr(os.path, name)
        monkeypatch.setattr(
            os.path, name, lambda path, real=real: str(path) in on_this_machine or real(path)
        )
    (tmp_path / "testbed").mkdir()
    real_exec = local_environment.exec

    async def sandbox_exec(command, *args, **kwargs):
        for path in ("/app", "/testbed"):
            command = command.replace(path, str(tmp_path) + path)
        return await real_exec(command, *args, **kwargs)

    local_environment.exec = sandbox_exec
    assert run(find_workspace(local_environment)) == "/testbed"
