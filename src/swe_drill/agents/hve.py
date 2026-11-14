import re
import shlex
import tempfile
from pathlib import Path
from typing import Any

from harbor.agents.installed.base import NonZeroAgentExitCodeError
from harbor.environments.base import BaseEnvironment

from swe_drill.agents.copilot_cli import CopilotCli


RPI_AGENT = ".github/agents/hve-core/rpi-agent.agent.md"
# A branch or a tag names whatever it points at on the day of the run.
FULL_COMMIT = re.compile(r"[0-9a-f]{40}")
FULL_AUTO = re.compile(
    r'^  - label: "Full Auto"\n(?:(?!  - label: ).*\n)*?    prompt: "([^"\\\n]*)"$', re.MULTILINE
)


class CopilotCliHve(CopilotCli):
    """
    CopilotCli as hve-core's RPI Agent, in its full-auto mode, from the
    hve-core plugin at the arm's `hve_core_commit`.

    RPI keeps its state and documents in the workspace's .copilot-tracking/,
    which is made a link to the logs directory and ignored through
    .git/info/exclude, so they are kept as they are written, even when the run
    is stopped, and stay out of the patch.
    """

    KWARGS = (*CopilotCli.KWARGS, "hve_core_commit")
    REQUIRED_KWARGS = ("version", "hve_core_commit")

    def __init__(self, *, hve_core_commit: Any, **kwargs: Any):
        """Raises ValueError for a `hve_core_commit` that is not a full commit in lowercase hex."""
        if not isinstance(hve_core_commit, str) or not FULL_COMMIT.fullmatch(hve_core_commit):
            raise ValueError(
                f"hve_core_commit must be a full commit, 40 lowercase hex digits, not {hve_core_commit!r}"
            )
        super().__init__(hve_core_commit=hve_core_commit, **kwargs)
        self.hve_core_commit = hve_core_commit
        self._plugin: str | None = None
        self._full_auto: str | None = None

    async def install(self, environment: BaseEnvironment) -> None:
        """
        Install Copilot CLI as CopilotCli does, then hve-core at the
        commit, as a plugin directory under $HOME/.local/share/hve-core/.

        GitHub's archive of a commit holds one directory, named for the
        repository and the commit, so a directory of any other name is not
        that commit's. Raises RuntimeError when the archive holds no
        plugin.json, or its RPI Agent has no "Full Auto" handoff prompt.
        """
        await super().install(environment)
        await self.ensure_system_dependencies(environment, ("curl", "tar"))
        commit = self.hve_core_commit
        home = (await self.exec_as_agent(environment, 'printf %s "$HOME"')).stdout or ""
        plugin = f"{home.strip()}/.local/share/hve-core/{commit}"
        source = f"hve-core-{commit}"
        archive = f"https://codeload.github.com/microsoft/hve-core/tar.gz/{commit}"
        await self.exec_as_agent(
            environment,
            "set -e; dir=$(mktemp -d); trap 'rm -rf \"$dir\"' EXIT; cd \"$dir\"; "
            f"curl -fsSL -o hve-core.tar.gz {shlex.quote(archive)}; "
            "mkdir unpacked; tar -xzf hve-core.tar.gz -C unpacked; "
            f"test -f unpacked/{source}/plugin.json"
            f" || {{ echo 'the archive of hve-core {commit} holds no {source}/plugin.json'; exit 1; }}; "
            f"mkdir -p {shlex.quote(str(Path(plugin).parent))}; "
            f"rm -rf {shlex.quote(plugin)}; mv unpacked/{source} {shlex.quote(plugin)}",
        )
        with tempfile.TemporaryDirectory() as directory:
            local = Path(directory) / "rpi-agent.agent.md"
            await environment.download_file(f"{plugin}/{RPI_AGENT}", local)
            self._full_auto = full_auto_prompt(local.read_text())
        self._plugin = plugin

    def render_instruction(self, instruction: str) -> str:
        """
        The Full Auto prompt, a blank line, then the instruction.

        The RPI Agent reads it as leave to decide ordinary questions itself,
        where it would otherwise ask one, which ends a headless run.
        """
        if self._full_auto is None:
            raise RuntimeError("CopilotCliHve ran before install read its Full Auto prompt")
        return f"{self._full_auto}\n\n{super().render_instruction(instruction)}"

    def build_cli_flags(self) -> str:
        if self._plugin is None:
            raise RuntimeError("CopilotCliHve ran before install placed hve-core")
        return (
            f"{super().build_cli_flags()} --plugin-dir {shlex.quote(self._plugin)}"
            " --agent hve-core:rpi-agent"
        )

    async def _before_agent(self, environment: BaseEnvironment) -> None:
        """
        Make the workspace's .copilot-tracking a link to the logs, ignored by git.

        Raises NonZeroAgentExitCodeError, so the trial is graded with no change,
        when the workspace already has one, which is the task's and the link
        would delete, or when its own ignore rules keep the link in the patch.
        """
        workspace = self._prepared[0]
        tracking = shlex.quote(f"{self.sandbox_logs.rstrip('/')}/copilot-tracking")
        result = await environment.exec(
            "fail() { echo \"$1\"; exit 1; }; "
            "if [ -e .copilot-tracking ] || [ -L .copilot-tracking ]; then"
            " fail 'the workspace already has .copilot-tracking, where RPI keeps its state'; fi; "
            "exclude=$(git rev-parse --git-path info/exclude)"
            " || fail 'git cannot find the workspace'\"'\"'s info/exclude'; "
            f'mkdir -p "${{exclude%/*}}" {tracking} || fail "cannot make ${{exclude%/*}} and {tracking}"; '
            # The newline first, in case the file does not end in one.
            "printf '\\n/.copilot-tracking\\n' >> \"$exclude\" || fail \"cannot write $exclude\"; "
            # The repository's own .gitignore outranks info/exclude, and can unignore it.
            "git check-ignore -q .copilot-tracking || fail 'the workspace'\"'\"'s ignore rules keep"
            " .copilot-tracking, so RPI'\"'\"'s files would be in the patch'; "
            f"ln -s {tracking} .copilot-tracking || fail 'cannot link .copilot-tracking to {tracking}'",
            cwd=workspace,
        )
        if result.return_code != 0:
            raise NonZeroAgentExitCodeError((result.stdout or "").strip())


def full_auto_prompt(text: str) -> str:
    """
    The prompt of the RPI Agent's "Full Auto" handoff in an agent file's text.

    Raises RuntimeError when it has none, or more than one. A prompt holding a
    quote or a backslash is not read.
    """
    found = FULL_AUTO.findall(text)
    if len(found) != 1:
        raise RuntimeError(
            f"{RPI_AGENT} has {'no' if not found else 'more than one'} \"Full Auto\" handoff with a prompt"
        )
    return found[0]
