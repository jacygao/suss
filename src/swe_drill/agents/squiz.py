import asyncio
import json
import re
import shlex
from typing import Any

from harbor.agents.installed.base import NonZeroAgentExitCodeError, PackageSpec
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

from swe_drill import judge
from swe_drill.agents import RECORD_NAME
from swe_drill.agents.copilot_cli import CopilotCli
from swe_drill.copilot import fetch

NODE_VERSION = "24.15.0"
# The sha256 of nodejs.org's archive of NODE_VERSION for each architecture, as its SHASUMS256.txt gives it.
NODE_SHA256 = {
    "x64": "44836872d9aec49f1e6b52a9a922872db9a2b02d235a616a5681b6a85fec8d89",
    "arm64": "73afc234d558c24919875f51c2d1ea002a2ada4ea6f83601a383869fefa64eed",
}
EXACT_VERSION = re.compile(r"\d+\.\d+\.\d+")
# The first squiz that keeps a round's findings when its state lock is slow to take, as on a loaded machine.
# Its reviewer and fixer also sign in from `copilotTokenFile`, since Copilot CLI gives its hooks no token.
LEAST_VERSION = (0, 3, 3)
SETTINGS = {"venue": "local", "reviewer": "copilot", "fixer": "copilot"}
# The values squiz's `thinking` takes.
REVIEWER_EFFORTS = ("off", "minimal", "low", "medium", "high", "xhigh", "max")
# How often the wait for squiz's close looks for it.
POLL_SECONDS = 2
# The ids Copilot CLI names a session's directory by, which are then safe in a path.
SESSION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


class CopilotCliSquiz(CopilotCli):
    """
    CopilotCli with squiz, at the arm's `squiz_version`, reviewing and fixing
    the agent's change in its local venue, with Copilot CLI as reviewer and
    fixer.

    squiz reviews once `copilot -p` has exited, from a round host the agent's
    Stop hook leaves running, and its fixer edits the workspace after that.
    So the run waits for squiz to close or stop its episode before anything
    is stopped or the patch captured.

    squiz keeps its settings in the workspace's .squiz.json and its state in
    .squiz/, which .git/info/exclude keeps out of the patch. Its state is
    copied to the logs once the run ends.

    Copilot CLI gives its hooks no COPILOT_GITHUB_TOKEN, so squiz's reviewer
    and fixer sign in from a token file outside the workspace, which exists
    only while the agent runs.
    """

    KWARGS = (*CopilotCli.KWARGS, "squiz_version", "reviewer_model", "reviewer_effort")
    REQUIRED_KWARGS = ("version", "squiz_version")
    # squiz's hooks and its wake run only with Copilot CLI's experimental features on.
    GLOBAL_FLAGS = ("--experimental",)
    AFTER_AGENT_TIMED_OUT = "review-timed-out"
    # squiz tells a process running from one gone by `ps`, which a slim image lacks.
    SYSTEM_PACKAGES = {
        **CopilotCli.SYSTEM_PACKAGES,
        "ps": PackageSpec(
            commands=("ps",),
            packages={"apt-get": ("procps",), "dnf": ("procps-ng",), "yum": ("procps-ng",), "apk": ("procps",)},
        ),
    }

    def __init__(self, *, squiz_version: Any, reviewer_model: Any = None, reviewer_effort: Any = None, **kwargs: Any):
        """
        Raises ValueError for a `squiz_version` that is not exact, as 0.3.3 is,
        or is before 0.3.3, and for a `reviewer_model` or `reviewer_effort`
        squiz would refuse.

        The reviewer runs on Copilot's own default model and squiz's default
        effort where the arm names none.
        """
        if reviewer_model is not None and (not isinstance(reviewer_model, str) or not reviewer_model.strip()):
            raise ValueError(f"reviewer_model must be a Copilot CLI model name, not {reviewer_model!r}")
        if reviewer_effort is not None and reviewer_effort not in REVIEWER_EFFORTS:
            raise ValueError(f"reviewer_effort must be one of {', '.join(REVIEWER_EFFORTS)}, not {reviewer_effort!r}")
        if not isinstance(squiz_version, str) or not EXACT_VERSION.fullmatch(squiz_version):
            raise ValueError(f"squiz_version must be an exact version, as 0.3.3 is, not {squiz_version!r}")
        if tuple(int(part) for part in squiz_version.split(".")) < LEAST_VERSION:
            raise ValueError(
                f"squiz_version {squiz_version} is before 0.3.3, the first squiz that keeps"
                " a round's findings when its state lock is slow to take"
            )
        super().__init__(
            squiz_version=squiz_version, reviewer_model=reviewer_model, reviewer_effort=reviewer_effort, **kwargs
        )
        self.reviewer = {
            key: value for key, value in (("model", reviewer_model), ("thinking", reviewer_effort)) if value is not None
        }
        self.squiz_version = squiz_version
        self.squiz_installed: str | None = None
        self._plugin: str | None = None
        self._token_file: str | None = None

    async def install(self, environment: BaseEnvironment) -> None:
        """
        Install Copilot CLI as CopilotCli does, then Node NODE_VERSION and
        squiz at its version, each under $HOME/.local/share/.

        squiz's own `bin/squiz` runs whatever `node` is on PATH, and its hooks
        inherit the agent's PATH, where `node` is the task's. So it is
        replaced by one that runs this Node, and linked into $HOME/.local/bin,
        where the fixer runs `squiz reply` by name. Raises RuntimeError when
        the installed squiz reports another version.
        """
        await super().install(environment)
        await self.ensure_system_dependencies(environment, ("curl", "tar", "ps"))
        home = ((await self.exec_as_agent(environment, 'printf %s "$HOME"')).stdout or "").strip()
        node = f"{home}/.local/share/swe-drill/node-v{NODE_VERSION}"
        plugin = f"{home}/.local/share/squiz/{self.squiz_version}"
        checksums = " ".join(f"{arch}) want={digest};;" for arch, digest in NODE_SHA256.items())
        await self.exec_as_agent(
            environment,
            f"set -e; {fetch()}fail() {{ echo \"$1\"; exit 1; }}; "
            'case "$(uname -m)" in x86_64|amd64) arch=x64;; aarch64|arm64) arch=arm64;; '
            '*) fail "nodejs.org builds no Node for $(uname -m)";; esac; '
            "if ls /lib/ld-musl-* >/dev/null 2>&1; then fail 'nodejs.org builds no Node for musl'; fi; "
            f'case "$arch" in {checksums} *) fail "no checksum is pinned for Node on $arch";; esac; '
            "dir=$(mktemp -d); trap 'rm -rf \"$dir\"' EXIT; cd \"$dir\"; "
            "fetch node.tar.gz"
            f' "https://nodejs.org/dist/v{NODE_VERSION}/node-v{NODE_VERSION}-linux-$arch.tar.gz"; '
            "sum=$(sha256sum node.tar.gz | cut -d ' ' -f 1); "
            f'[ "$sum" = "$want" ] || fail "the archive of Node {NODE_VERSION} has checksum $sum, not $want"; '
            "mkdir unpacked; tar -xzf node.tar.gz -C unpacked; "
            f"mkdir -p {shlex.quote(node.rsplit('/', 1)[0])}; rm -rf {shlex.quote(node)}; "
            f'mv "unpacked/node-v{NODE_VERSION}-linux-$arch" {shlex.quote(node)}',
        )
        source = f"squiz-{self.squiz_version}"
        archive = f"https://codeload.github.com/jacygao/squiz/tar.gz/refs/tags/v{self.squiz_version}"
        shim = f'#!/bin/sh\nexec {shlex.quote(node + "/bin/node")} {shlex.quote(plugin + "/src/cli.ts")} "$@"\n'
        installed = await self.exec_as_agent(
            environment,
            f"set -e; {fetch()}dir=$(mktemp -d); trap 'rm -rf \"$dir\"' EXIT; cd \"$dir\"; "
            f"fetch squiz.tar.gz {shlex.quote(archive)}; "
            "mkdir unpacked; tar -xzf squiz.tar.gz -C unpacked; "
            f"{{ test -f unpacked/{source}/hooks/hooks.json && test -f unpacked/{source}/src/cli.ts; }}"
            f" || {{ echo 'the archive of squiz {self.squiz_version} holds no {source}/hooks/hooks.json"
            " and src/cli.ts'; exit 1; }; "
            f"mkdir -p {shlex.quote(plugin.rsplit('/', 1)[0])}; rm -rf {shlex.quote(plugin)}; "
            f"mv unpacked/{source} {shlex.quote(plugin)}; "
            f"printf %s {shlex.quote(shim)} > {shlex.quote(plugin + '/bin/squiz')}; "
            f"chmod 755 {shlex.quote(plugin + '/bin/squiz')}; "
            'mkdir -p "$HOME/.local/bin"; '
            f'ln -sfn {shlex.quote(plugin + "/bin/squiz")} "$HOME/.local/bin/squiz"; '
            '"$HOME/.local/bin/squiz" version',
        )
        # `squiz version` prints `squiz <version> <path>`, the version its plugin.json names.
        words = (installed.stdout or "").split()
        reported = words[1] if len(words) > 1 and words[0] == "squiz" else (installed.stdout or "").strip()
        if reported != self.squiz_version:
            raise RuntimeError(f"the archive of squiz {self.squiz_version} installed squiz {reported}")
        self.squiz_installed = reported
        self._plugin = plugin
        self._token_file = f"{home}/.local/share/swe-drill/copilot-token"

    def build_cli_flags(self) -> str:
        if self._plugin is None:
            raise RuntimeError("CopilotCliSquiz ran before install placed squiz")
        return f"{super().build_cli_flags()} --plugin-dir {shlex.quote(self._plugin)}"

    async def _before_agent(self, environment: BaseEnvironment) -> None:
        """
        Write the token file, as the agent's user and readable by it alone,
        then the workspace's .squiz.json, and keep it and .squiz/ out of git.

        Raises NonZeroAgentExitCodeError, so the trial is graded with no change,
        when the workspace already has either, which is the task's, or when its
        own ignore rules keep either in the patch.
        """
        if self._token_file is None:
            raise RuntimeError("CopilotCliSquiz ran before install placed squiz")
        token_file = shlex.quote(self._token_file)
        # The token reaches the file from the environment, so no command line holds it.
        written = await self.exec_as_agent(
            environment,
            f"umask 077 && mkdir -p \"$(dirname {token_file})\" && rm -f {token_file}"
            f' && printf %s "${self.TOKEN}" > {token_file}',
        )
        if written.return_code != 0:
            raise NonZeroAgentExitCodeError(f"cannot write the token file {self._token_file}")
        settings = shlex.quote(json.dumps({**SETTINGS, **self.reviewer, "copilotTokenFile": self._token_file}))
        result = await environment.exec(
            "fail() { echo \"$1\"; exit 1; }; "
            "for path in .squiz.json .squiz; do"
            ' if [ -e "$path" ] || [ -L "$path" ]; then'
            " fail \"the workspace already has $path, where squiz keeps its settings and state\"; fi; done; "
            "exclude=$(git rev-parse --git-path info/exclude)"
            " || fail 'git cannot find the workspace'\"'\"'s info/exclude'; "
            'mkdir -p "${exclude%/*}" || fail "cannot make ${exclude%/*}"; '
            # The newline first, in case the file does not end in one.
            "printf '\\n/.squiz.json\\n/.squiz/\\n' >> \"$exclude\" || fail \"cannot write $exclude\"; "
            # The repository's own .gitignore outranks info/exclude, and can unignore either.
            "{ git check-ignore -q .squiz.json && git check-ignore -q .squiz/; }"
            " || fail 'the workspace'\"'\"'s ignore rules keep .squiz.json or .squiz/, so squiz'\"'\"'s files"
            " would be in the patch'; "
            f"printf %s {settings} > .squiz.json || fail 'cannot write .squiz.json'",
            cwd=self._prepared[0],
        )
        if result.return_code != 0:
            raise NonZeroAgentExitCodeError((result.stdout or "").strip())

    async def _after_agent(self, environment: BaseEnvironment) -> str:
        """
        Wait for squiz's episode to close, `ok`, or stop short of its close,
        `review-stopped`. Only Harbor's agent timeout ends a wait for neither.

        Raises RuntimeError when the run left no base.json, which squiz's
        SessionStart hook writes, since then squiz never loaded and nothing
        will review the change.
        """
        local = self._local()
        loaded = await environment.exec(f"test -f {shlex.quote(local + '/base.json')}")
        if loaded.return_code != 0:
            raise RuntimeError(
                f"squiz never loaded: Copilot CLI exited and left no {local}/base.json,"
                " which squiz's SessionStart hook writes"
            )
        while True:
            found = await environment.exec(
                f"cd {shlex.quote(local)} && for name in closed.json stopped.json; do"
                ' if [ -f "$name" ]; then echo "$name"; break; fi; done'
            )
            marker = (found.stdout or "").strip()
            if marker == "closed.json":
                return "ok"
            if marker == "stopped.json":
                return "review-stopped"
            await asyncio.sleep(POLL_SECONDS)

    async def _record(self, environment: BaseEnvironment, status: str, run_seconds: float) -> None:
        """
        Remove the token file, copy squiz's state and its fixer's sessions to
        the logs, then record the trial as CopilotCli does.
        """
        if self._token_file is not None:
            await environment.exec(f"rm -f {shlex.quote(self._token_file)}")
        await self._keep_squiz(environment)
        await super()._record(environment, status, run_seconds)

    async def _keep_squiz(self, environment: BaseEnvironment) -> None:
        """
        Copy .squiz/local/ to the logs as squiz/, less the objects of the heads
        it built, and the fixer's sessions, which Copilot CLI keeps in the
        agent's own COPILOT_HOME, to squiz/fixer-sessions/.
        """
        kept = self._kept()
        await environment.exec(
            f"rm -rf {shlex.quote(kept)}; if [ -d {shlex.quote(self._local())} ]; then"
            f" cp -R {shlex.quote(self._local())} {shlex.quote(kept)} && rm -rf {shlex.quote(kept + '/objects')}; fi"
        )
        listed = await environment.exec(f"find {shlex.quote(kept)} -name state.json 2>/dev/null")
        sessions: list[str] = []
        for path in (listed.stdout or "").split():
            text = (await environment.exec(f"cat {shlex.quote(path)}")).stdout or ""
            try:
                state = json.loads(text)
            except ValueError:
                continue
            sessions += [found for found in fixer_sessions(state) if found not in sessions]
        for session in sessions:
            await environment.exec(
                f"mkdir -p {shlex.quote(kept + '/fixer-sessions')} && cp -R"
                f' "$HOME/.copilot/session-state/{session}" {shlex.quote(kept + "/fixer-sessions/" + session)}'
            )

    async def _usage(self, environment: BaseEnvironment) -> dict[str, Any]:
        """
        CopilotCli's usage, with `ai_credits` the whole run's: the agent's, and
        each reviewer's and fixer's session's, which `squiz` breaks out. It is
        None where any of them is unknown, so no partial sum reads as the whole.
        """
        usage = await super()._usage(environment)
        kept = shlex.quote(self._kept())
        reviewer = await _sessions_usage(
            environment, f"find {kept} -path '*/session/session-state/*/events.jsonl' 2>/dev/null"
        )
        fixer = await _sessions_usage(environment, f"find {kept}/fixer-sessions -name events.jsonl 2>/dev/null")
        parts = [usage["ai_credits"], reviewer["ai_credits"], fixer["ai_credits"]]
        usage["ai_credits"] = None if None in parts else round(sum(parts), 4)
        usage["squiz"] = {"reviewer": reviewer, "fixer": fixer}
        return usage

    async def _details(self, environment: BaseEnvironment) -> dict[str, Any]:
        """Which squiz ran, whether it loaded, and how its episode ended, as `outcome` and the marker's own fields."""
        kept = self._kept()
        markers = {}
        for name in ("base", "closed", "stopped"):
            result = await environment.exec(f"cat {shlex.quote(f'{kept}/{name}.json')} 2>/dev/null")
            try:
                markers[name] = json.loads(result.stdout or "") if result.return_code == 0 else None
            except ValueError:
                markers[name] = None
        outcome = "closed" if markers["closed"] is not None else "stopped" if markers["stopped"] is not None else None
        return {
            "squiz": {
                "version": self.squiz_installed,
                "copilot_experimental": "--experimental" in self.build_cli_flags().split(),
                "loaded": markers["base"] is not None,
                "outcome": outcome,
                "closed": markers["closed"],
                "stopped": markers["stopped"],
            }
        }

    def populate_context_post_run(self, context: AgentContext) -> None:
        """As CopilotCli does, with the whole run's credits suss.json records in place of the agent's alone."""
        super().populate_context_post_run(context)
        record = self.logs_dir / RECORD_NAME
        if not record.exists():
            return
        usage = json.loads(record.read_text()).get("usage") or {}
        context.metadata = {**(context.metadata or {}), "ai_credits": usage.get("ai_credits"), "squiz": usage.get("squiz")}

    def _local(self) -> str:
        return f"{self._prepared[0]}/.squiz/local"

    def _kept(self) -> str:
        return f"{self.sandbox_logs.rstrip('/')}/squiz"


def fixer_sessions(state: Any) -> list[str]:
    """The id of each fixer session a squiz state file records, as its rounds' `fixer.session.id`."""
    found: list[str] = []
    if isinstance(state, dict):
        session = (state.get("fixer") or {}).get("session") if isinstance(state.get("fixer"), dict) else None
        if isinstance(session, dict) and isinstance(session.get("id"), str) and SESSION_ID.fullmatch(session["id"]):
            found.append(session["id"])
        children = state.values()
    elif isinstance(state, list):
        children = state
    else:
        return found
    for child in children:
        found += [session for session in fixer_sessions(child) if session not in found]
    return found


async def _sessions_usage(environment: BaseEnvironment, listing: str) -> dict[str, Any]:
    """
    The credits and models of every Copilot CLI session whose events.jsonl
    `listing` prints. The credits are None where any session reported none,
    since its cost is unknown, not nothing.
    """
    paths = sorted(((await environment.exec(listing)).stdout or "").split())
    credits: float | None = 0.0
    models: set[str] = set()
    for path in paths:
        result = await environment.exec(f"grep -E {judge.EVENT_LINES} {shlex.quote(path)}")
        events = judge.read_events(result.stdout or "")
        credits = None if credits is None or events["credits"] is None else credits + events["credits"]
        models.update(events["reported_models"])
    return {
        "ai_credits": None if credits is None else round(credits, 4),
        "reported_models": sorted(models),
        "sessions": len(paths),
    }
