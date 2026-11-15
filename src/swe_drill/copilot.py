"""Copilot CLI as the judge and the Copilot CLI adapters install it."""

import re
from typing import Any, ClassVar

from harbor.agents.installed.copilot_cli import CopilotCli
from harbor.environments.base import BaseEnvironment

# How many times `fetch` tries a download, and how much longer it waits after each failed try.
FETCH_TRIES = 5
FETCH_PAUSE_SECONDS = 5


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


class NpmCopilotCli(CopilotCli):
    """
    Harbor's Copilot CLI agent, installing Copilot CLI from npm's registry
    instead of GitHub's releases.

    npm's package for each platform holds the same binary as GitHub's release
    of the version, and needs no Node to run. GitHub's release downloads fail
    for hours at a time while npm's registry serves.
    """

    # Flags every Copilot CLI command this agent starts passes, its install's check of the version among them.
    GLOBAL_FLAGS: ClassVar[tuple[str, ...]] = ()

    def __init__(self, **kwargs: Any):
        """
        Raises ValueError for a version that is not exact, as 1.0.107 is.
        npm names each package file for its exact version, never a tag.
        """
        super().__init__(**kwargs)
        if not re.fullmatch(r"\d+\.\d+\.\d+", self._version or ""):
            raise ValueError(f"Copilot CLI's version must be an exact version, as 1.0.107 is, not {self._version!r}")

    async def install(self, environment: BaseEnvironment) -> None:
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
