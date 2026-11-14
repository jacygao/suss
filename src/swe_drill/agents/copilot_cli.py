import json
import shlex
from typing import Any

from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

from swe_drill import judge
from swe_drill.agents import Adapter
from swe_drill.config import ConfigError
from swe_drill.copilot import NpmCopilotCli


class CopilotCli(Adapter, NpmCopilotCli):
    """
    Harbor's Copilot CLI agent, with the built-in MCP servers, the web tools,
    auto-update and client telemetry off, and only the arm's own token.

    Copilot CLI's built-in GitHub MCP server reaches GitHub through the model
    host the agent phase allows, so leaving it on lets a model read a task's
    real fix. The token is COPILOT_GITHUB_TOKEN from the .env beside
    swe-drill.toml, never GH_TOKEN or GITHUB_TOKEN, and it reaches only the agent
    phase's commands.
    """

    ALLOWED_HOSTS = ("api.github.com", "api.enterprise.githubcopilot.com")
    KWARGS = ("version", "reasoning_effort", "fusion_tier")
    REQUIRED_KWARGS = ("version",)
    REQUIRES_MODEL = True
    FUSION = "fusion"
    FUSION_TIERS = ("standard", "high", "max")
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

    def __init__(self, *, fusion_tier: Any = None, **kwargs: Any):
        """
        Raises ConfigError, before a sandbox starts, when .env gives no
        COPILOT_GITHUB_TOKEN, the model names a provider, or `fusion_tier` is
        not a tier of Fusion's, or is set for another model, which ignores it.

        Harbor runs a model that names a provider, as `anthropic/<model>`,
        against that provider's own API, with a key it takes from its own
        environment.
        """
        if fusion_tier is not None:
            kwargs["fusion_tier"] = fusion_tier
        super().__init__(**kwargs)
        if "/" in (self.model_name or ""):
            raise ConfigError(
                f"{self.settings.path}: [arms.{self.settings.arm.name}] model ="
                f" {self.model_name!r} names a provider, and {type(self).__name__} runs"
                " only Copilot's own models, by the names Copilot CLI's --model takes"
            )
        if fusion_tier is not None and (self.model_name != self.FUSION or fusion_tier not in self.FUSION_TIERS):
            raise ConfigError(
                f"{self.settings.path}: [arms.{self.settings.arm.name}] kwargs fusion_tier ="
                f" {fusion_tier!r} is not one of {', '.join(self.FUSION_TIERS)} with model"
                f" {self.FUSION!r}, and Copilot CLI uses it nowhere else"
            )
        self.fusion_tier = fusion_tier
        self._copilot_token = self.settings.env.get(self.TOKEN, "")
        if not self._copilot_token:
            raise ConfigError(
                f"{self.settings.path.parent / '.env'} sets no {self.TOKEN}, which"
                f" {type(self).__name__} gives Copilot CLI"
            )

    def build_cli_flags(self) -> str:
        return " ".join([super().build_cli_flags(), *self.FLAGS]).strip()

    async def install(self, environment: BaseEnvironment) -> None:
        """
        Install Copilot CLI, and write the arm's `fusion_tier` to its
        settings, the one place Copilot CLI reads it from.
        """
        await super().install(environment)
        if self.fusion_tier is not None:
            settings = shlex.quote(json.dumps({"fusionTier": self.fusion_tier}))
            await self.exec_as_agent(
                environment, f"mkdir -p ~/.copilot && printf %s {settings} > ~/.copilot/settings.json"
            )

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
