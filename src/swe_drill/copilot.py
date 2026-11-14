"""Copilot CLI as the judge and the Copilot CLI adapters install it."""

import re
from typing import Any

from harbor.agents.installed.copilot_cli import CopilotCli
from harbor.environments.base import BaseEnvironment


class NpmCopilotCli(CopilotCli):
    """
    Harbor's Copilot CLI agent, installing Copilot CLI from npm's registry
    instead of GitHub's releases.

    npm's package for each platform holds the same binary as GitHub's release
    of the version, and needs no Node to run. GitHub's release downloads fail
    for hours at a time while npm's registry serves.
    """

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
                "set -euo pipefail; "
                'case "$(uname -m)" in x86_64|amd64) arch=x64;; aarch64|arm64) arch=arm64;; '
                '*) echo "no Copilot CLI build for $(uname -m)"; exit 1;; esac; '
                "libc=linux; if ls /lib/ld-musl-* >/dev/null 2>&1; then libc=linuxmusl; fi; "
                'package="copilot-$libc-$arch"; '
                "dir=$(mktemp -d); trap 'rm -rf \"$dir\"' EXIT; "
                'curl -fsSL --retry 5 -o "$dir/copilot.tgz" '
                f'"https://registry.npmjs.org/@github/$package/-/$package-{version}.tgz"; '
                'tar -xzf "$dir/copilot.tgz" -C "$dir"; '
                'mkdir -p "$HOME/.local/bin"; install -m 755 "$dir/package/copilot" "$HOME/.local/bin/copilot"; '
                "echo 'export PATH=\"$HOME/.local/bin:$PATH\"' >> ~/.bashrc; "
                'export PATH="$HOME/.local/bin:$PATH"; '
                "copilot --version"
            ),
        )
