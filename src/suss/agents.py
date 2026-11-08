"""
The Harbor agents suss runs its arms on: Harbor's own agent classes, each
with SussMixin applied, which makes every trial leave a patch the verifier
grades.

Harbor constructs an agent on the host, then calls `setup` in the setup phase
and `run` in the agent phase, in the same sandbox. Everything read from the
host, suss.toml and its .env, is read in the constructor, so a bad config
fails before a sandbox starts.
"""

import asyncio
import inspect
import json
import re
import shlex
import tempfile
import time
from pathlib import Path
from typing import Any, ClassVar

from harbor.agents.base import BaseAgent
from harbor.agents.installed.base import NonZeroAgentExitCodeError
from harbor.agents.installed.claude_code import ClaudeCode
from harbor.agents.installed.copilot_cli import CopilotCli
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext
from harbor.utils.import_path import import_symbol

from suss import config
from suss import judge, lanes, sandbox
from suss.config import ConfigError
from suss.workspace import (
    ARTIFACTS_DIR,
    LOGS_DIR,
    PATCH_NAME,
    capture_patch,
    find_workspace,
    read_base,
    read_start_tree,
)

RECORD_NAME = "suss.json"
# The dataset each lane's tasks come from, which names its trials' grader.
DATASETS = {lanes.REBENCH: "swe-rebench-07-2026", lanes.SENIOR: "senior-swe-bench-v2026.06"}
# How long a cancelled run may take to capture the patch and write suss.json.
CLEANUP_SECONDS = 60
# The kwargs suss itself gives every agent, which an arm's own cannot take.
SUSS_KWARGS = ("arm", "lane", "suss_config")


class SussMixin:
    """
    Applied before a Harbor agent class, it records the starting tree before
    the agent runs and, however the run ends, stops every process the agent
    started and writes model.patch to the logs and artifacts directories, the
    same bytes.

    A run whose patch cannot be written raises the capture's error, which
    Harbor does not treat as the agent's, so the trial fails rather than
    being graded as the unchanged repository.

    A class takes the arm kwargs in KWARGS, and needs those in REQUIRED_KWARGS
    and, where REQUIRES_MODEL is set, a model. ALLOWED_HOSTS are the hosts its
    agent phase reaches.
    """

    ALLOWED_HOSTS: ClassVar[tuple[str, ...]] = ()
    KWARGS: ClassVar[tuple[str, ...]] = ()
    REQUIRED_KWARGS: ClassVar[tuple[str, ...]] = ()
    REQUIRES_MODEL: ClassVar[bool] = False

    def __init__(
        self,
        *,
        arm: Any,
        lane: Any,
        suss_config: Any,
        logs: str = LOGS_DIR,
        artifacts: str = ARTIFACTS_DIR,
        **kwargs: Any,
    ):
        """
        Read the arm's settings on `lane` from the suss.toml at `suss_config`,
        a host path relative to the directory `harbor run` started in.

        `logs` and `artifacts` are where the patch goes in the sandbox. Harbor
        never passes them. Raises TypeError when Harbor parsed `arm`, `lane` or
        `suss_config` as something other than a string, ConfigError when the
        settings cannot be read, and LaneError for a lane with no dataset in
        DATASETS, and as `grader` does.
        """
        for key, value in (("arm", arm), ("lane", lane), ("suss_config", suss_config)):
            if not isinstance(value, str):
                quoted = shlex.quote(f"{key}={json.dumps(json.dumps(value))}")
                raise TypeError(
                    f"--ak {key} must be a string, but Harbor parsed it as JSON into"
                    f" {value!r}, a {type(value).__name__}. Quote it as a JSON string:"
                    f" --ak {quoted}"
                )
        self.settings = config.load(Path(suss_config), arm, lane)
        if lane not in DATASETS:
            raise lanes.LaneError(
                f"suss cannot name the grader of a trial on {lane}, so it runs none there"
            )
        self.grader = grader(self.settings, lane)
        self.arm_kwargs = {key: kwargs[key] for key in self.KWARGS if key in kwargs}
        self.sandbox_logs = logs
        self.sandbox_artifacts = artifacts
        self._prepared: tuple[str, str, str] | None = None
        self._token: str | None = None
        self.installed_version: str | None = None
        super().__init__(**kwargs)

    @classmethod
    def parse_options(cls, kwargs=None, env=None):
        """
        Harbor's parse of the kwargs its agent's options name, with no fallback
        from the host's environment.

        Harbor refuses any other kwarg, in the job's preflight and in the
        constructor, so suss's own and the arm's own are left out. An option
        not given takes its default, since Harbor fills an absent one from
        Harbor's own environment.
        """
        fields = cls.options_model.model_fields if cls.options_model is not None else {}
        own = (*SUSS_KWARGS, *cls.KWARGS)
        kept = {key: value for key, value in (kwargs or {}).items() if key in fields or key not in own}
        for name, info in fields.items():
            if name not in kept and not info.is_required():
                kept[name] = info.get_default(call_default_factory=True)
        return super().parse_options(kept, env)

    def _env_sources(self):
        """Leave out Harbor's own environment, so no variable of the host's reaches the agent."""
        return (self._resolved_env_vars, self._extra_env)

    async def setup(self, environment: BaseEnvironment) -> None:
        """Record the workspace, the base and the starting tree, then let the agent install."""
        workspace = await find_workspace(environment)
        base = await read_base(environment, workspace)
        start_tree = await read_start_tree(environment, workspace)
        self._prepared = (workspace, base, start_tree)
        await super().setup(environment)
        self.installed_version = await self._installed(environment)

    async def _installed(self, environment: BaseEnvironment) -> str | None:
        """
        The version the installed agent reports, which may differ from the one
        asked for, or None where it reports none.
        """
        command = self.get_version_command()
        if not command:
            return None
        result = await environment.exec(command)
        if result.return_code != 0 or not result.stdout:
            return None
        return self.parse_version(result.stdout)

    async def _exec(self, environment, command, user=None, env=None, cwd=None, timeout_sec=None):
        """Mark every command run in the agent phase, so that a stop finds all it started."""
        if self._token is not None:
            env = {**(env or {}), sandbox.MARKER: self._token}
        return await super()._exec(
            environment, command, user=user, env=env, cwd=cwd, timeout_sec=timeout_sec
        )

    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        """
        Run the agent, stop everything it started, capture the patch, and
        write suss.json.

        The trial is `timed-out` when Harbor cancels the run at its agent
        timeout, `harness-failed` when the agent raises, and `ok` otherwise. The agent's exception, and Harbor's
        cancel, are raised once the record is written, the cancel within
        CLEANUP_SECONDS so Harbor still sees its timeout.
        """
        if self._prepared is None:
            raise RuntimeError(f"{type(self).__name__}.run was called before setup")
        self._token = sandbox.new_token()
        status = "ok"
        raised: BaseException | None = None
        started = time.monotonic()
        try:
            await self._before_agent(environment)
            await super().run(instruction, environment, context)
        except asyncio.CancelledError as cancelled:
            status, raised = "timed-out", cancelled
        except BaseException as error:
            status, raised = "harness-failed", error
        run_seconds = time.monotonic() - started
        token, self._token = self._token, None
        finishing = asyncio.ensure_future(self._finish(environment, token, status, run_seconds, raised))
        # A cancel that arrives during the stop or the capture waits for them,
        # for CLEANUP_SECONDS at most, since Harbor waits on a cancelled agent
        # with no limit. It is raised once they are done.
        late: asyncio.CancelledError | None = None
        deadline: float | None = None
        loop = asyncio.get_running_loop()
        while not finishing.done():
            if deadline is not None and loop.time() >= deadline:
                finishing.cancel()
                # Retrieved once the cancel settles it, so a failure it
                # raises meanwhile is never reported as unretrieved.
                finishing.add_done_callback(lambda task: task.cancelled() or task.exception())
                raise late
            try:
                if deadline is None:
                    await asyncio.shield(finishing)
                else:
                    await asyncio.wait({finishing}, timeout=deadline - loop.time())
            except asyncio.CancelledError as cancel:
                late = late or cancel
                deadline = deadline or loop.time() + CLEANUP_SECONDS
        if finishing.cancelled() or (finishing.exception() is None and late is not None):
            raise late or asyncio.CancelledError()
        finishing.result()

    async def _finish(
        self,
        environment: BaseEnvironment,
        token: str,
        status: str,
        run_seconds: float,
        raised: BaseException | None,
    ) -> None:
        """
        Stop everything the agent started, capture the patch and write
        suss.json, then raise what the run raised.
        """
        await sandbox.stop_marked(environment, token)
        try:
            # Harbor waits on a cancelled agent with no limit.
            cleanup = CLEANUP_SECONDS if isinstance(raised, asyncio.CancelledError) else None
            async with asyncio.timeout(cleanup):
                await self._record(environment, status, run_seconds)
        except BaseException as error:
            # Harbor grades a trial whose agent raised its own error, which an
            # empty or missing patch would then pass as the unchanged repository.
            if raised is not None and not isinstance(raised, asyncio.CancelledError):
                raise error from raised
            raise
        if raised is not None:
            raise raised

    async def _record(self, environment: BaseEnvironment, status: str, run_seconds: float) -> None:
        """Capture the patch, copy it to the artifacts directory, then write suss.json beside it."""
        workspace, base, start_tree = self._prepared
        patch_bytes = await capture_patch(environment, workspace, start_tree, self.sandbox_logs)
        logs = f"{self.sandbox_logs.rstrip('/')}/{PATCH_NAME}"
        # Harbor collects only the artifacts directory for a verifier in a
        # sandbox of its own, so the copy is the patch that verifier grades.
        await sandbox.run(
            environment,
            f"mkdir -p {shlex.quote(self.sandbox_artifacts)}"
            f" && cp {shlex.quote(logs)} {shlex.quote(self.sandbox_artifacts)}",
        )
        record = {
            "arm": self.settings.arm.name,
            "agent": {
                "class": self.settings.arm.agent,
                "name": self.name(),
                "version": self.installed_version,
            },
            "model": self.model_name,
            "kwargs": self.arm_kwargs,
            "grader": self.grader,
            "base": base,
            "status": status,
            "run_seconds": run_seconds,
            "patch_bytes": patch_bytes,
            "usage": await self._usage(environment),
        }
        await sandbox.upload_bytes(
            environment,
            (json.dumps(record, indent=2) + "\n").encode(),
            f"{self.sandbox_logs.rstrip('/')}/{RECORD_NAME}",
        )

    async def _before_agent(self, environment: BaseEnvironment) -> None:
        """Prepare the workspace for the agent. A failure here is the agent's, as its run's would be."""

    async def _usage(self, environment: BaseEnvironment) -> dict[str, Any] | None:
        """What the agent reports it spent, for suss.json. None for an agent that reports nothing suss reads."""
        return None


def grader(settings: config.Config, lane: str) -> dict[str, str]:
    """
    What grades a trial of `lane`, a lane in DATASETS: its dataset.

    On senior it adds the litellm requirement the lane's test.sh installs, and
    the models and validation harness the regrade's verifier will use. Those
    follow from the variables `suss run` gives the regrade's Harbor, which only
    the settings the lane's tasks read can reach. Raises LaneError when a
    task.toml in the download cannot be read.
    """
    found = {"dataset": DATASETS[lane]}
    if lane == lanes.SENIOR:
        download = settings.download(lane)
        reads = config.host_variables(lanes.read_tasks(lanes.lane_tasks(lane, download)))
        found["litellm"] = lanes.PINNED_LITELLM.decode()
        found |= config.grading_choices(config.verifier_env(settings, reads))
    return found


def agent_class(import_path: str, path: Path, arm: str) -> type[SussMixin]:
    """
    The class an arm's `agent` names, which must apply SussMixin.

    Raises ConfigError, naming the file and the arm, for one that cannot be
    imported, is not a class, or leaves no patch for the verifier to grade.
    """
    try:
        found = import_symbol(import_path)
    except ValueError as error:
        raise ConfigError(f"{path}: [arms.{arm}] agent = {import_path!r}: {error}") from None
    concrete = isinstance(found, type) and issubclass(found, BaseAgent) and not inspect.isabstract(found)
    if not concrete or not issubclass(found, SussMixin):
        raise ConfigError(
            f"{path}: [arms.{arm}] agent = {import_path!r} is not an agent built on "
            "suss.agents.SussMixin, and only such an agent leaves the patch its trial is "
            "graded by; use suss.agents:SussCopilotCli, suss.agents:SussCopilotCliHve or"
            " suss.agents:SussClaudeCode"
        )
    return found


def check_arm(cls: type[SussMixin], settings: config.Config) -> None:
    """
    Refuse an arm whose kwargs or model its agent class cannot take.

    Harbor refuses a kwarg its agent's options do not name only once `harbor
    run` has started, and an agent with no options takes any. Raises
    ConfigError naming the file, the arm, and what the class takes.
    """
    arm = settings.arm
    where = f"{settings.path}: [arms.{arm.name}]"
    for key in arm.kwargs:
        if key not in cls.KWARGS:
            takes = ", ".join(cls.KWARGS) or "none"
            raise ConfigError(
                f"{where} sets kwargs {key}, which {arm.agent} does not take; it takes {takes}"
            )
    for key in cls.REQUIRED_KWARGS:
        if key not in arm.kwargs:
            raise ConfigError(f"{where} sets no kwargs {key}, which {arm.agent} needs")
    if cls.REQUIRES_MODEL and not arm.model:
        raise ConfigError(f"{where} sets no model, which {arm.agent} needs")


class SussCopilotCli(SussMixin, CopilotCli):
    """
    Harbor's Copilot CLI agent, with the built-in MCP servers, the web tools,
    auto-update and client telemetry off, and only the arm's own token.

    Copilot CLI's built-in GitHub MCP server reaches GitHub through the model
    host the agent phase allows, so leaving it on lets a model read a task's
    real fix. The token is COPILOT_GITHUB_TOKEN from the .env beside
    suss.toml, never GH_TOKEN or GITHUB_TOKEN, and it reaches only the agent
    phase's commands.
    """

    ALLOWED_HOSTS = ("api.github.com", "api.enterprise.githubcopilot.com")
    KWARGS = ("version", "reasoning_effort")
    REQUIRED_KWARGS = ("version",)
    REQUIRES_MODEL = True
    TOKEN = "COPILOT_GITHUB_TOKEN"
    # Every token Copilot CLI or Harbor's agent would take in its place.
    TOKENS = ("COPILOT_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN")
    FLAGS = (
        "--disable-builtin-mcps",
        "--no-auto-update",
        "--disable-client-telemetry",
        "--no-ask-user",
        "--excluded-tools",
        "web_fetch",
        "web_search",
    )

    def __init__(self, **kwargs: Any):
        """
        Raises ConfigError, before a sandbox starts, when .env gives no
        COPILOT_GITHUB_TOKEN, or the model names a provider.

        Harbor runs a model that names a provider, as `anthropic/<model>`,
        against that provider's own API, with a key it takes from its own
        environment.
        """
        super().__init__(**kwargs)
        if "/" in (self.model_name or ""):
            raise ConfigError(
                f"{self.settings.path}: [arms.{self.settings.arm.name}] model ="
                f" {self.model_name!r} names a provider, and {type(self).__name__} runs"
                " only Copilot's own models, by the names Copilot CLI's --model takes"
            )
        self._copilot_token = self.settings.env.get(self.TOKEN, "")
        if not self._copilot_token:
            raise ConfigError(
                f"{self.settings.path.parent / '.env'} sets no {self.TOKEN}, which"
                f" {type(self).__name__} gives Copilot CLI"
            )

    def build_cli_flags(self) -> str:
        return " ".join([super().build_cli_flags(), *self.FLAGS]).strip()

    async def exec_as_agent(self, environment, command, env=None, cwd=None, timeout_sec=None):
        """Give the command no token but the arm's, and that one only in the agent phase."""
        env = {key: value for key, value in (env or {}).items() if key not in self.TOKENS}
        if self._token is not None:
            env[self.TOKEN] = self._copilot_token
        return await super().exec_as_agent(
            environment, command, env=env or None, cwd=cwd, timeout_sec=timeout_sec
        )

    async def _usage(self, environment: BaseEnvironment) -> dict[str, Any]:
        """
        The credits, models and efforts Copilot CLI's stream reports, as
        `judge.read_events` reads them. Harbor's agent parses none of them.
        """
        stream = shlex.quote(f"{self.sandbox_logs.rstrip('/')}/{self._TRAJECTORY_FILENAME}")
        result = await environment.exec(f"grep -E {judge.EVENT_LINES} {stream} 2>/dev/null")
        return _usage_of(result.stdout or "")

    def populate_context_post_run(self, context: AgentContext) -> None:
        """
        Harbor's trajectory and token counts, then the credits Copilot CLI's
        last usage checkpoint reports in `metadata`, and its token counts
        where Harbor's parse found none.
        """
        super().populate_context_post_run(context)
        stream = self.logs_dir / self._TRAJECTORY_FILENAME
        if not stream.exists():
            return
        text = stream.read_text(errors="replace")
        context.metadata = {**(context.metadata or {}), **_usage_of(text)}
        checkpoint = _checkpoint_tokens(text)
        for field, key in (
            ("n_input_tokens", "inputTokens"),
            ("n_cache_tokens", "cacheReadTokens"),
            ("n_output_tokens", "outputTokens"),
        ):
            value = checkpoint.get(key)
            if not getattr(context, field) and isinstance(value, int):
                setattr(context, field, value)


def _usage_of(text: str) -> dict[str, Any]:
    events = judge.read_events(text)
    return {
        "ai_credits": events["credits"],
        "reported_models": events["reported_models"],
        "reported_efforts": events["reported_efforts"],
    }


def _checkpoint_tokens(text: str) -> dict[str, int]:
    """
    The token counts of the stream's last `session.usage_checkpoint`, summed
    over its models, by the names its `usage` gives them, or {} where it has none.
    """
    found: dict[str, int] = {}
    for line in text.splitlines():
        if "session.usage_checkpoint" not in line:
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict) or event.get("type") != "session.usage_checkpoint":
            continue
        found = {}
        for entry in (event.get("data") or {}).get("providerModelMetrics") or []:
            usage = ((entry or {}).get("metrics") or {}).get("usage") or {}
            for key, value in usage.items():
                if isinstance(value, int):
                    found[key] = found.get(key, 0) + value
    return found


class SussClaudeCode(SussMixin, ClaudeCode):
    """
    Harbor's Claude Code agent, signed in with the owner's Claude subscription
    and nothing else.

    The token is CLAUDE_CODE_OAUTH_TOKEN from the .env beside suss.toml, and it
    reaches only the agent phase's commands. No API key, gateway or token from
    Harbor's environment reaches Claude Code, since Claude Code prefers an
    ANTHROPIC_API_KEY to the subscription and would bill the API.
    """

    ALLOWED_HOSTS = ("api.anthropic.com",)
    KWARGS = ("version", "reasoning_effort")
    REQUIRED_KWARGS = ("version",)
    REQUIRES_MODEL = True
    TOKEN = "CLAUDE_CODE_OAUTH_TOKEN"
    # Harbor's run reads these from its own environment, past _env_sources.
    HOST_FLAGS = ("CLAUDE_CODE_MAX_OUTPUT_TOKENS", "CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING")

    def __init__(self, **kwargs: Any):
        """
        Raises ConfigError, before a sandbox starts, when .env gives no
        CLAUDE_CODE_OAUTH_TOKEN, or the model names a provider, which a
        subscription does not serve.
        """
        super().__init__(**kwargs)
        if "/" in (self.model_name or ""):
            raise ConfigError(
                f"{self.settings.path}: [arms.{self.settings.arm.name}] model ="
                f" {self.model_name!r} names a provider, and {type(self).__name__} runs"
                " only Anthropic's models, by the names Claude Code's --model takes"
            )
        self._oauth_token = self.settings.env.get(self.TOKEN, "")
        if not self._oauth_token:
            raise ConfigError(
                f"{self.settings.path.parent / '.env'} sets no {self.TOKEN}, which"
                f" {type(self).__name__} gives Claude Code"
            )

    def _resolve_auth_env(self) -> dict[str, str]:
        """
        The subscription token alone, as CLAUDE_FORCE_OAUTH has Harbor give it.

        Harbor's own resolution reads the API key, token and gateway from its
        environment, which _env_sources does not cover.
        """
        return {self.TOKEN: self._oauth_token}

    async def exec_as_agent(self, environment, command, env=None, cwd=None, timeout_sec=None):
        env = {key: value for key, value in (env or {}).items() if key not in self.HOST_FLAGS}
        return await super().exec_as_agent(
            environment, command, env=env or None, cwd=cwd, timeout_sec=timeout_sec
        )


RPI_AGENT =".github/agents/hve-core/rpi-agent.agent.md"
# A branch or a tag names whatever it points at on the day of the run.
FULL_COMMIT = re.compile(r"[0-9a-f]{40}")
FULL_AUTO = re.compile(
    r'^  - label: "Full Auto"\n(?:(?!  - label: ).*\n)*?    prompt: "([^"\\\n]*)"$', re.MULTILINE
)


class SussCopilotCliHve(SussCopilotCli):
    """
    SussCopilotCli as hve-core's RPI Agent, in its full-auto mode, from the
    hve-core plugin at the arm's `hve_core_commit`.

    RPI keeps its state and documents in the workspace's .copilot-tracking/,
    which is made a link to the logs directory and ignored through
    .git/info/exclude, so they are kept as they are written, even when the run
    is stopped, and stay out of the patch.
    """

    KWARGS = (*SussCopilotCli.KWARGS, "hve_core_commit")
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
        Install Copilot CLI as Harbor's agent does, then hve-core at the
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
            raise RuntimeError("SussCopilotCliHve ran before install read its Full Auto prompt")
        return f"{self._full_auto}\n\n{super().render_instruction(instruction)}"

    def build_cli_flags(self) -> str:
        if self._plugin is None:
            raise RuntimeError("SussCopilotCliHve ran before install placed hve-core")
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

