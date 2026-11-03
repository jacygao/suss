"""
Install Copilot CLI with the real `install.sh` in small public images shaped
like pro-go's task images, then start it.

Only a real image shows whether the build chosen runs on its C library, and
whether the image's own downloader fetches it. Both images are x64, as the task
images are, and the release is downloaded from GitHub.
"""

import json
import shutil
import socket
import subprocess
from pathlib import Path

import pytest

ADAPTER = Path(__file__).resolve().parent.parent / "adapters" / "copilot-cli"
VERSION = "1.0.105"


@pytest.fixture
def docker_with_github():
    if shutil.which("docker") is None or subprocess.run(
        ["docker", "info"], capture_output=True
    ).returncode != 0:
        pytest.skip("Docker is not running")
    try:
        socket.create_connection(("github.com", 443), timeout=5).close()
    except OSError as error:
        pytest.skip(f"no network to github.com, which the install downloads from: {error}")


def install_and_start(image: str, prepare: str, tmp_path: Path) -> subprocess.CompletedProcess:
    """
    Run install.sh in a fresh container of `image` after the shell command
    `prepare`, then `copilot --version`, then list the workspace, which must
    be empty.
    """
    (tmp_path / "params.json").write_text(json.dumps({"version": VERSION, "model": "x"}))
    script = (
        f"{prepare}\n"
        "mkdir /workspace && cd /workspace\n"
        "/adapter/install.sh --workspace /workspace --params /opt/suss/params.json || exit 1\n"
        '"$HOME/.local/bin/copilot" --version || exit 1\n'
        "echo \"workspace: [$(ls -A /workspace)]\"\n"
    )
    return subprocess.run(
        ["docker", "run", "--rm", "--platform", "linux/amd64",
         "-v", f"{ADAPTER}:/adapter:ro", "-v", f"{tmp_path / 'params.json'}:/opt/suss/params.json:ro",
         image, "sh", "-c", script],
        capture_output=True,
        text=True,
        timeout=900,
    )


# Alpine with busybox's `wget` and no `curl`, as the 7 pro-go images that
# failed with `curl: not found` are.
def test_copilot_installs_and_starts_on_musl_without_curl_111(docker_with_github, tmp_path):
    result = install_and_start(
        "alpine:3.20", "! command -v curl || { echo 'the image has curl' >&2; exit 1; }", tmp_path
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert VERSION in result.stdout, result.stdout
    assert "workspace: []" in result.stdout, "install left files in the workspace"


def test_copilot_installs_and_starts_on_glibc_with_curl_111(docker_with_github, tmp_path):
    result = install_and_start("buildpack-deps:bookworm-curl", "command -v curl > /dev/null", tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert VERSION in result.stdout, result.stdout
    assert "workspace: []" in result.stdout, "install left files in the workspace"
