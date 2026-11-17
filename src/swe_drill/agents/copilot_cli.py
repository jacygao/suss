import contextlib
import json
import re
import shlex
from typing import Any, ClassVar

from harbor.agents.installed.copilot_cli import CopilotCli as HarborCopilotCli
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

from swe_drill.agents import Adapter
from swe_drill.config import ConfigError

# How many times `fetch` tries a download, and how much longer it waits after each failed try.
FETCH_TRIES = 5
FETCH_PAUSE_SECONDS = 5

# The JSON lines of a session that `read_events` reads. The rest are deltas.
EVENTS = ("model.call_start", "model.call_final_result", "session.usage_checkpoint",
          "session.usage_record", "session.model_change")
EVENT_LINES = shlex.quote('"type": ?"(' + "|".join(re.escape(e) for e in EVENTS) + ')"')


def fetch() -> str:
    """
    A shell function, `fetch <file> <url>`, that downloads with curl and tries
    again on any failure, FETCH_TRIES times in all. curl's own --retry skips a
    dropped connection, which is how a download most often fails.
    """
    return (
        "fetch() { tries=1; until curl -fsSL --retry 5 -o \"$1\" \"$2\"; do code=$?;"
        f' [ "$tries" -lt {FETCH_TRIES} ] || return "$code"; rm -f "$1";'
        f' sleep "$((tries * {FETCH_PAUSE_SECONDS}))"; tries=$((tries + 1)); done; }}; '
    )


class CopilotCli(Adapter, HarborCopilotCli):
    """
    Harbor's Copilot CLI agent, installed from npm's registry, with the
    built-in MCP servers, the web tools, auto-update and client telemetry off,
    and only the arm's own token.

    npm's package for each platform holds the same binary as GitHub's release
    of the version, and needs no Node to run. GitHub's release downloads fail
    for hours at a time while npm's registry serves.

    Copilot CLI's built-in GitHub MCP server reaches GitHub through the model
    host the agent phase allows, so leaving it on lets a model read a task's
    real fix. The token is COPILOT_GITHUB_TOKEN from the .env beside
    swe-drill.toml, never GH_TOKEN or GITHUB_TOKEN, and it reaches only the agent
    phase's commands.
    """

    # Flags every Copilot CLI command this agent starts passes, its install's check of the version among them.
    GLOBAL_FLAGS: ClassVar[tuple[str, ...]] = ()
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
        # Copilot CLI still signs in with it, but the shells it starts never get it.
        "--secret-env-vars",
        TOKEN,
        "--excluded-tools",
        "web_fetch",
        "web_search",
    )

    def __init__(self, *, fusion_tier: Any = None, **kwargs: Any):
        """
        Raises ConfigError, before a sandbox starts, when .env gives no
        COPILOT_GITHUB_TOKEN, the model names a provider, or `fusion_tier` is
        not a tier of Fusion's, or is set for another model, which ignores it.
        Raises ValueError for a version that is not exact, as 1.0.107 is, since
        npm names each package file for its exact version, never a tag.

        Harbor runs a model that names a provider, as `anthropic/<model>`,
        against that provider's own API, with a key it takes from its own
        environment.
        """
        if fusion_tier is not None:
            kwargs["fusion_tier"] = fusion_tier
        super().__init__(**kwargs)
        if not re.fullmatch(r"\d+\.\d+\.\d+", self._version or ""):
            raise ValueError(f"Copilot CLI's version must be an exact version, as 1.0.107 is, not {self._version!r}")
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

    @classmethod
    def parse_options(cls, kwargs=None, env=None):
        """
        Harbor's parse, without `fusion_tier`. Harbor's agent would pass it as
        --fusion-tier, which Copilot CLI does not take, since it reads the tier
        only from its settings.
        """
        kept = {key: value for key, value in (kwargs or {}).items() if key != "fusion_tier"}
        return super().parse_options(kept, env)

    def build_cli_flags(self) -> str:
        return " ".join([super().build_cli_flags(), *self.GLOBAL_FLAGS, *self.FLAGS]).strip()

    def get_version_command(self) -> str:
        return f'export PATH="$HOME/.local/bin:$PATH"; {self.version_check()}'

    async def install(self, environment: BaseEnvironment) -> None:
        """
        Install Copilot CLI, and write the arm's `fusion_tier` to its
        settings, the one place Copilot CLI reads it from.
        """
        await self._install_copilot(environment)
        if self.fusion_tier is not None:
            settings = shlex.quote(json.dumps({"fusionTier": self.fusion_tier}))
            await self.exec_as_agent(
                environment, f"mkdir -p ~/.copilot && printf %s {settings} > ~/.copilot/settings.json"
            )

    async def _install_copilot(self, environment: BaseEnvironment) -> None:
        """Install Copilot CLI at the agent's version into $HOME/.local/bin. Raises when it cannot."""
        await self.ensure_system_dependencies(environment, ("curl", "bash", "git", "tar"))
        version = self._version
        await self.exec_as_agent(
            environment,
            command=(
                f"set -euo pipefail; {fetch()}"
                'case "$(uname -m)" in x86_64|amd64) arch=x64;; aarch64|arm64) arch=arm64;; '
                '*) echo "no Copilot CLI build for $(uname -m)"; exit 1;; esac; '
                "libc=linux; if ls /lib/ld-musl-* >/dev/null 2>&1; then libc=linuxmusl; fi; "
                'package="copilot-$libc-$arch"; '
                "dir=$(mktemp -d); trap 'rm -rf \"$dir\"' EXIT; "
                'fetch "$dir/copilot.tgz" '
                f'"https://registry.npmjs.org/@github/$package/-/$package-{version}.tgz"; '
                'tar -xzf "$dir/copilot.tgz" -C "$dir"; '
                'mkdir -p "$HOME/.local/bin"; install -m 755 "$dir/package/copilot" "$HOME/.local/bin/copilot"; '
                "echo 'export PATH=\"$HOME/.local/bin:$PATH\"' >> ~/.bashrc; "
                'export PATH="$HOME/.local/bin:$PATH"; '
                f"{self.version_check()}"
            ),
        )

    def version_check(self) -> str:
        """The command that prints the installed Copilot CLI's version, run where $HOME/.local/bin is on PATH."""
        return " ".join(["copilot", *self.GLOBAL_FLAGS, "--version"])

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
        `read_events` reads them. Harbor's agent parses none of them.
        """
        stream = shlex.quote(f"{self.sandbox_logs.rstrip('/')}/{self._TRAJECTORY_FILENAME}")
        result = await environment.exec(f"grep -E {EVENT_LINES} {stream} 2>/dev/null")
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


def read_events(text: str) -> dict[str, Any]:
    """
    What a session's JSON lines say it spent: the credits, and the models and
    efforts Copilot CLI reports it used.

    Credits are the largest `totalNanoAiu` of its usage checkpoints, over 1e9,
    or None where it reported none. The models are those of its model calls
    and usage records, and the efforts those its usage checkpoints and model
    changes name.
    """
    events = []
    for line in text.splitlines():
        with contextlib.suppress(ValueError):
            event = json.loads(line)
            if isinstance(event, dict):
                events.append(event)
    data = [(event.get("type"), event.get("data") or {}) for event in events]
    nano = [d.get("totalNanoAiu") for kind, d in data if kind == "session.usage_checkpoint"]
    nano = [n for n in nano if isinstance(n, (int, float))]
    models = {d.get("model") for kind, d in data if kind in ("model.call_start", "model.call_final_result")}
    # A session's stored events have no model calls, only its usage records.
    models |= {(d.get("usage") or {}).get("model") for kind, d in data if kind == "session.usage_record"}
    efforts = {
        state.get("reasoning_effort")
        for kind, d in data
        if kind == "session.usage_checkpoint"
        for entry in d.get("promptCacheBreakState") or []
        for state in (entry.get("models") or {}).values()
    }
    efforts |= {d.get("reasoningEffort") for kind, d in data if kind == "session.model_change"}
    return {
        "credits": round(max(nano) / 1e9, 4) if nano else None,
        "reported_models": sorted(m for m in models if m),
        "reported_efforts": sorted(e for e in efforts if e),
    }


def _usage_of(text: str) -> dict[str, Any]:
    events = read_events(text)
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
