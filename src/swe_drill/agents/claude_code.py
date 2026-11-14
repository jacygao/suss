from typing import Any

from harbor.agents.installed.claude_code import ClaudeCode as HarborClaudeCode

from swe_drill.agents import Adapter
from swe_drill.config import ConfigError


class ClaudeCode(Adapter, HarborClaudeCode):
    """
    Harbor's Claude Code agent, signed in with the owner's Claude subscription
    and nothing else.

    The token is CLAUDE_CODE_OAUTH_TOKEN from the .env beside swe-drill.toml, and it
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
