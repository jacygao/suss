"""
Find the workspace in a real container, whose image's WORKDIR is neither /app
nor a directory of this machine.

Only `docker exec` with no `-w` shows where the image starts a command, which
is where Harbor's Docker environment runs one for a task that sets no workdir.
"""

import asyncio
import subprocess

import pytest

from conftest import require_docker
from swe_drill.workspace import WorkspaceError, find_workspace

# node:24 has git and bash. /app and /testbed exist too, so looking for either
# is caught.
REPOSITORY = """\
FROM node:24
RUN mkdir -p /gleam/src /app /testbed && cd /gleam \\
 && git init -q && git -c user.name=t -c user.email=t@t commit -q --allow-empty -m base
WORKDIR /gleam
"""
INSIDE_IT = "FROM swe-drill-test/workdir:gleam\nWORKDIR /gleam/src\n"


def build(tag: str, dockerfile: str) -> str:
    built = subprocess.run(
        ["docker", "build", "-q", "-t", tag, "-"],
        input=dockerfile,
        capture_output=True,
        text=True,
    )
    assert built.returncode == 0, f"cannot build {tag}: {built.stderr}"
    return tag


@pytest.fixture(scope="module")
def images() -> dict[str, str]:
    require_docker()
    return {
        "gleam": build("swe-drill-test/workdir:gleam", REPOSITORY),
        "gleam/src": build("swe-drill-test/workdir:gleam-src", INSIDE_IT),
    }


@pytest.fixture
def container_image(request, images) -> str:
    return images[request.param]


@pytest.mark.parametrize("container_image", ["gleam"], indirect=True)
def test_the_workspace_is_the_images_workdir(container_environment):
    assert asyncio.run(find_workspace(container_environment)) == "/gleam"


@pytest.mark.parametrize("container_image", ["gleam/src"], indirect=True)
def test_a_workdir_inside_the_repository_fails_naming_it(container_environment):
    with pytest.raises(WorkspaceError) as raised:
        asyncio.run(find_workspace(container_environment))
    assert "/gleam/src" in str(raised.value), "the error does not name the directory"
