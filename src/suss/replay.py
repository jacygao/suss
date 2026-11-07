"""
Replaying a trial's model.patch in a fresh sandbox, so that the task's verifier
grades the change and nothing else the harness did to its sandbox.

Every test file the patch changed is then put back as it was in the starting
tree, so the change is graded against the tests and not against the harness's
edits to them. ReplayVerifier does this in Harbor's separate verifier, inside
the agent job. ReplayAgent does it as the agent of a second job, the regrade.
Harbor constructs both on the host, where the agent job is.
"""

import hashlib
import json
import re
import shlex
import tempfile
from collections.abc import Awaitable, Callable
from importlib.metadata import version
from pathlib import Path
from typing import Any

from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext
from harbor.models.verifier.result import VerifierResult
from harbor.verifier.verifier import Verifier

from suss.workspace import ARTIFACTS_DIR, PATCH_NAME, find_workspace, read_start_tree

RECORD_NAME = "replay.json"
APPLY_METHOD = "git apply --verbose"
APPLY_SECONDS = 300
TEST_WORDS = frozenset({"test", "tests", "testdata", "mdtest"})
JS_SPEC = re.compile(r"\.spec\.(?:js|jsx|ts|tsx|mjs|cjs|mts|cts)$")
GIT_ESCAPES = {
    "a": 0x07, "b": 0x08, "t": 0x09, "n": 0x0A, "v": 0x0B, "f": 0x0C, "r": 0x0D,
    '"': 0x22, "\\": 0x5C,
}


class ReplayError(Exception):
    """
    A regrade that cannot find the one trial of its task in the source job, a
    verifier with no patch it can trust, or a workspace git cannot read or put
    a test file back in.
    """


class ReplayVerifier(Verifier):
    """
    Harbor's verifier for a trial graded in a sandbox of its own, which applies
    the patch the agent's sandbox left in its artifacts directory, then runs
    the task's own test.sh, unchanged.

    The verifier's image is the task's own, so Harbor uploads the task's tests
    to it as it does to a verifier in the agent's sandbox.
    """

    def __init__(self, *, artifacts: str = ARTIFACTS_DIR, **kwargs: Any):
        """`artifacts` is where the patch is in the verifier's sandbox. Harbor never passes it."""
        super().__init__(**kwargs)
        self.artifacts = artifacts

    async def verify(self) -> VerifierResult:
        """
        Apply the patch, put back every test file it changed, write
        verifier/replay.json, and grade as Harbor's own verifier does.

        An empty patch applies nothing and records `"patch": null`, and the
        unchanged workspace is graded, since the agent changed nothing. A patch
        git refuses is recorded and graded as ReplayAgent grades it. Raises
        ReplayError, with `"patch": null` recorded and no test run, when the
        sandbox holds no patch, or one that differs from the agent's
        model.patch on the host: grading the workspace then would score a
        change the agent may not have made.
        """
        target = f"{self.artifacts.rstrip('/')}/{PATCH_NAME}"
        record: dict[str, Any] = {"patch": None}
        try:
            patch = await self._read(target)
            if patch:
                replayed = await replay(self.environment, patch, target)
                record = {
                    "patch": target,
                    "patch_bytes": len(patch),
                    "sha256": hashlib.sha256(patch).hexdigest(),
                    **replayed,
                }
        finally:
            self.trial_paths.verifier_dir.mkdir(parents=True, exist_ok=True)
            (self.trial_paths.verifier_dir / RECORD_NAME).write_text(
                json.dumps(record, indent=2) + "\n"
            )
        return await super().verify()

    async def _read(self, target: str) -> bytes:
        """
        The patch at `target` in the sandbox, raising ReplayError when there is
        none or it differs from the agent's model.patch on the host.

        A trial whose agent left no model.patch on the host, as Harbor's own
        agents leave none, is graded by the sandbox's alone.
        """
        found = await self.environment.exec(f"test -f {shlex.quote(target)}")
        if found.return_code != 0:
            raise ReplayError(
                f"the trial left no patch at {target}, so its change cannot be graded"
            )
        with tempfile.TemporaryDirectory() as directory:
            local = Path(directory) / PATCH_NAME
            await self.environment.download_file(target, local)
            patch = local.read_bytes()
        agents = self.trial_paths.agent_dir / PATCH_NAME
        if agents.is_file() and agents.read_bytes() != patch:
            raise ReplayError(
                f"the patch at {target}, {len(patch)} bytes, differs from the agent's {agents},"
                f" {agents.stat().st_size} bytes, so its change cannot be graded"
            )
        return patch


class ReplayAgent(BaseAgent):
    def __init__(
        self,
        *,
        source_job: Any,
        patch_target: str = "/tmp/suss-replay.patch",
        **kwargs: Any,
    ):
        """
        Regrade from the agent job at `source_job`, a host path relative to the
        directory `harbor run` started in.

        `patch_target` is where the patch goes in the sandbox. Harbor never
        passes it. Raises TypeError when Harbor parsed `source_job` as something
        other than a string, and ReplayError when it is not a directory.
        """
        if not isinstance(source_job, str):
            raise TypeError(
                "--ak source_job must be a string, but Harbor parsed it as JSON into"
                f" {source_job!r}, a {type(source_job).__name__}"
            )
        super().__init__(**kwargs)
        self.source_job = Path(source_job).absolute()
        if not self.source_job.is_dir():
            raise ReplayError(f"source_job {self.source_job} is not a directory")
        self.patch_target = patch_target

    @staticmethod
    def name() -> str:
        return "suss-replay"

    def version(self) -> str | None:
        return version("suss")

    async def setup(self, environment: BaseEnvironment) -> None:
        pass

    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        """
        Apply the source trial's patch in the workspace, put every test file
        it changed back as it was in the starting tree, and write replay.json.

        A patch that fails to apply is recorded, not raised, so the verifier
        still grades the workspace. Raises ReplayError when the source job holds
        no trial of this task, or more than one, and when git cannot compare
        the workspace with its starting tree or put a test file back. Raises
        WorkspaceError when git cannot build the starting tree.
        """
        # Harbor writes the trial's config.json before it starts the agent.
        task = _task(self.logs_dir.parent / "config.json")
        patch = self._source_trial(task) / "agent" / PATCH_NAME
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        record: dict[str, Any] = {"task": task, "patch": None}
        if patch.is_file() and patch.stat().st_size > 0:
            await environment.upload_file(patch, self.patch_target)

            async def after_apply(workspace: str, applied: dict[str, Any]) -> None:
                await self._after_apply(environment, workspace, patch, applied)

            replayed = await replay(
                environment, patch.read_bytes(), self.patch_target, after_apply
            )
            record = {"task": task, "patch": str(patch), **replayed}
        (self.logs_dir / RECORD_NAME).write_text(json.dumps(record, indent=2) + "\n")

    async def _after_apply(
        self,
        environment: BaseEnvironment,
        workspace: str,
        patch: Path,
        applied: dict[str, Any],
    ) -> None:
        """
        Act on the workspace while it holds the whole patch, test files
        included, before they are put back. A regrade does nothing here.
        """

    def _source_trial(self, task: str) -> Path:
        """The one trial directory of the source job that ran `task`."""
        trials = sorted(
            config.parent
            for config in self.source_job.glob("*/config.json")
            if _task(config) == task
        )
        if len(trials) != 1:
            found = ", ".join(str(trial) for trial in trials) or f"no trial of {task}"
            raise ReplayError(
                f"source_job {self.source_job} must hold one trial of {task}, and holds {found}"
            )
        return trials[0]


async def replay(
    environment: BaseEnvironment,
    patch: bytes,
    target: str,
    after_apply: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None,
) -> dict[str, Any]:
    """
    Apply `patch`, which is at `target` in the sandbox, in the workspace, then
    put back every test file it changed as it was in the starting tree.

    Return git's exit code and what it printed, as `apply_rc` and `apply_out`,
    and the test files put back, as `tests_reset`. A patch git refuses is
    recorded, not raised, so the verifier still grades the workspace.
    `after_apply` is given the workspace while it holds the whole patch, test
    files included. Raises ReplayError when git cannot compare the workspace
    with its starting tree or put a test file back, and WorkspaceError when it
    cannot build the starting tree.
    """
    workspace = await find_workspace(environment)
    added = [path for path in _created(patch) if is_test_file(path)]
    start = await read_start_tree(environment, workspace)
    # An ignored file the image holds is in no tree, but is not the patch's
    # either, so it stays.
    held = await _present(environment, workspace, added)
    applied = await _apply(environment, target, workspace)
    if after_apply is not None:
        await after_apply(workspace, applied)
    added = [path for path in added if path not in held]
    tests_reset = await _reset_tests(environment, workspace, start, added)
    return {**applied, "tests_reset": tests_reset}


async def _apply(environment: BaseEnvironment, target: str, workspace: str) -> dict[str, Any]:
    """
    Apply the patch at `target`, and return git's exit code and everything it
    printed.

    Git applies the whole patch or none of it. The patch runs from the starting
    tree this sandbox holds, so a patch suss captured applies whole. The output
    follows a line naming the command, stderr too. The patch is read from a
    file, since exec passes text, which corrupts bytes that are not valid UTF-8.
    """
    quoted = shlex.quote(target)
    command = f"{{ echo {shlex.quote('$ ' + APPLY_METHOD)}; {APPLY_METHOD} {quoted} 2>&1; }}"
    result = await environment.exec(command, cwd=workspace, timeout_sec=APPLY_SECONDS)
    return {
        "apply_rc": result.return_code,
        "apply_out": (result.stdout or "") + (result.stderr or ""),
    }


def is_test_file(path: str) -> bool:
    """
    Whether `path`, relative to the workspace, names a test file.

    A path's words are its runs of letters, each split before a capital that
    starts a lowercase word, so `XMLTests` is `XML` and `Tests`. Their case is
    ignored.
    """
    words = {word.lower() for word in re.findall(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+", path)}
    return bool(words & TEST_WORDS) or bool(JS_SPEC.search(path))


async def _changed_from_start(
    environment: BaseEnvironment, workspace: str, start: str
) -> dict[str, str]:
    """
    Every path in which the workspace's tree differs from the starting tree,
    mapped to its status letter: M, T, D or A.

    Ignored files are left out, as they are from the patch. Raises
    WorkspaceError when git cannot build the tree, and ReplayError when it
    cannot compare the two.
    """
    now = await read_start_tree(environment, workspace)
    # Git's stderr goes to a file, since Docker would mix a warning into the
    # paths. Every path comes back ASCII, quoted where it needs to be.
    command = (
        "err=$(mktemp) || exit 1; git -c core.quotePath=true diff-tree -r --no-renames"
        f' --name-status {shlex.quote(start)} {shlex.quote(now)} 2>"$err"; rc=$?;'
        ' [ $rc -eq 0 ] || cat "$err"; rm -f "$err"; exit $rc'
    )
    result = await environment.exec(command, cwd=workspace, timeout_sec=APPLY_SECONDS)
    output = (result.stdout or "") + (result.stderr or "")
    if result.return_code != 0:
        raise ReplayError(f"git cannot list what changed in {workspace}: {output.strip()}")
    # Each line is a status letter, a tab and the path.
    return {
        _unquote(line.split("\t", 1)[1]): line[0]
        for line in output.splitlines()
        if "\t" in line
    }


async def _reset_tests(
    environment: BaseEnvironment, workspace: str, start: str, added: list[str]
) -> dict[str, list[str]]:
    """
    Restore from the starting tree each test file the patch changed or
    deleted, remove each one it added, and return the paths of both.

    `added` is each test file the patch's headers create that the workspace
    lacked before the patch. Each path reset keeps HEAD's entry in the index,
    as it had before the patch. Raises ReplayError naming git's output when
    git cannot compare the trees or put a file back.
    """
    changed = await _changed_from_start(environment, workspace, start)
    restored = sorted(
        path for path, status in changed.items() if status != "A" and is_test_file(path)
    )
    # From the headers and not from git, which lists no ignored file: a patch
    # can add a test file and a rule ignoring it, or stop ignoring one the
    # image holds.
    removed = sorted(set(await _present(environment, workspace, added, outside=start)))
    # Literal, so a test file named with `*` or `[` names only itself.
    git = "git --literal-pathspecs"
    steps = []
    # Removed first, since a file restored where the patch made a directory
    # takes the directory's place, and the paths inside it with it.
    if removed:
        paths = " ".join(shlex.quote(path) for path in removed)
        steps.append(f"rm -f -- {paths} && {git} reset -q -- {paths}")
    if restored:
        paths = " ".join(shlex.quote(path) for path in restored)
        steps.append(
            f"{git} checkout {shlex.quote(start)} -- {paths} && {git} reset -q -- {paths}"
        )
    if steps:
        # Into stdout, because Modal keeps stderr apart and git explains a failure there.
        result = await environment.exec(
            f"{{ {' && '.join(steps)}; }} 2>&1", cwd=workspace, timeout_sec=APPLY_SECONDS
        )
        if result.return_code != 0:
            raise ReplayError(
                "git cannot put the patch's test files back as they were in the starting"
                f" tree in {workspace}: {(result.stdout or '').strip()}"
            )
    return {"restored": restored, "removed": removed}


async def _present(
    environment: BaseEnvironment, workspace: str, paths: list[str], *, outside: str = ""
) -> list[str]:
    """
    Those of `paths` that are in the workspace, and not in the tree `outside`
    when one is given.

    Raises ReplayError when the shell cannot check them.
    """
    if not paths:
        return []
    in_tree = f' && ! git cat-file -e {shlex.quote(outside)}:"$p" 2>/dev/null' if outside else ""
    # Each path that qualifies comes back as its index, since a name can hold a newline.
    command = (
        f"i=0; for p in {' '.join(shlex.quote(path) for path in paths)}; do"
        f' if {{ [ -e "$p" ] || [ -L "$p" ]; }}{in_tree}; then echo "$i"; fi; i=$((i+1)); done'
    )
    result = await environment.exec(command, cwd=workspace, timeout_sec=APPLY_SECONDS)
    output = (result.stdout or "") + (result.stderr or "")
    if result.return_code != 0:
        raise ReplayError(f"cannot check the patch's new files in {workspace}: {output.strip()}")
    return [paths[int(line)] for line in output.split() if line.isdigit()]


def _created(patch: bytes) -> list[str]:
    """
    Every path the patch's headers create, by a new file, a rename or a copy,
    relative to the workspace.
    """
    created = []
    lines = patch.decode(errors="surrogateescape").splitlines()
    new_file: str | None = None
    header = ""
    for i, line in enumerate(lines):
        if line.startswith("diff --git "):
            new_file = None
            header = line[len("diff --git ") :]
        elif line.startswith("new file mode"):
            # A binary file has no +++ line, so its path comes from the diff line.
            new_file = _git_header_path(header)
            if new_file is not None:
                created.append(new_file)
        elif line.startswith(("rename to ", "copy to ")):
            created.append(_unquote(line.split(" to ", 1)[1]))
        elif line == "--- /dev/null" and i + 1 < len(lines) and lines[i + 1].startswith("+++ "):
            target = lines[i + 1][4:]
            target = _unquote(target) if target.startswith('"') else target.split("\t")[0]
            # The first component goes, as `patch -p1` and `git apply` drop it.
            path = target.split("/", 1)[1] if "/" in target else target
            if path != new_file:
                created.append(path)
    return created


def _git_header_path(header: str) -> str | None:
    """
    The b/ path of a `diff --git` line for a new file, whose two paths are the
    same apart from their prefixes.
    """
    if header.startswith('"'):
        end = 1
        while end < len(header) and header[end] != '"':
            end += 2 if header[end] == "\\" else 1
        b = header[end + 2 :]
        b = _unquote(b) if b.startswith('"') else b
    else:
        b = header[(len(header) - 1) // 2 + 1 :]
    return b[2:] if b.startswith("b/") else None


def _unquote(path: str) -> str:
    """
    The path git printed, without the quotes and escapes it adds to a path
    holding a quote, a backslash, a control character or a byte above 0x7f.
    """
    if not path.startswith('"'):
        return path
    raw = bytearray()
    body = path[1:-1]
    i = 0
    while i < len(body):
        if body[i] != "\\":
            raw += body[i].encode()
            i += 1
        elif body[i + 1] in GIT_ESCAPES:
            raw.append(GIT_ESCAPES[body[i + 1]])
            i += 2
        else:
            raw.append(int(body[i + 1 : i + 4], 8))
            i += 4
    return raw.decode(errors="surrogateescape")


def _task(config: Path) -> str:
    """
    The task a trial ran: the name of the task directory its config.json names.

    Raises ReplayError when the file names none.
    """
    try:
        path = json.loads(config.read_text())["task"]["path"]
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise ReplayError(f"cannot read the task from {config}: {error!r}") from error
    if not isinstance(path, str):
        raise ReplayError(f"cannot read the task from {config}: task.path is {path!r}")
    return Path(path).name

