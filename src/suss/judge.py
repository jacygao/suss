"""
JudgeAgent, the Harbor custom agent that scores a trial's patch with Senior
SWE-Bench's rubric and taste judges, run through Copilot CLI in the task's
container.

It regrades as ReplayAgent does, and judges in between: after the whole patch
is applied, test files included, and before those test files are put back for
the verifier. A judge that fails, times out or changes the repository leaves
the workspace as a regrade leaves it, so the test reward is a regrade's.

The prompts, the rubric and the answer schemas are the task's own, built by
the task's tests/run_judge.py in a Python process of their own on the host,
since each task carries its own copy of senior's modules under one name.
Harbor constructs the agent on the host, where the agent job and the tasks are.
"""

import asyncio
import contextlib
import fcntl
import hashlib
import importlib
import importlib.util
import json
import os
import re
import shlex
import sys
import time
import tomllib
import uuid
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path
from typing import Any

from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

from suss import harness
from suss.agent import _as_root, _upload, _upload_bytes
from suss.config import _read_env
from suss.harness import SandboxPaths
from suss.replay import ReplayAgent
from suss.workspace import find_workspace, read_start_tree

RECORD_NAME = "judge.json"
LEDGER_NAME = "judge-credits.jsonl"
ADAPTER = Path(__file__).resolve().parents[2] / "adapters" / "copilot-cli"
CLIENT = "copilot-cli"
MODEL = "gpt-6.1-sol"
EFFORT = "high"
CLASSIFIER_MODEL = "claude-haiku-4.5"
COPILOT_VERSION = "1.0.105"
TOKEN_NAME = "COPILOT_GITHUB_TOKEN"
JUDGES = ("rubric", "taste")
# The tools each kind of session is offered, all of them read-only.
TOOLS = {"classify": ("view",), "rubric": ("view",), "taste": ("view", "glob", "grep", "rg")}
# Credits a session holds while it runs: about twice what each took on the
# largest patch the spike judged.
RESERVE = {"classify": 5.0, "rubric": 30.0, "taste": 60.0}
# Each running session's JSON lines that the record reads. The rest are deltas.
EVENTS = ("model.call_start", "model.call_final_result", "assistant.message",
          "session.usage_checkpoint", "tool.execution_start", "result")
EVENT_LINES = shlex.quote('"type": ?"(' + "|".join(re.escape(e) for e in EVENTS) + ')"')
SENIOR_SECONDS = 300
# How long putting the workspace back may take once the judge has stopped.
CLEANUP_SECONDS = 300
SESSION_SCRIPT = "session.sh"

# How each prompt ends, in place of the forced tool call senior's judges make.
ANSWER = """

---

# How to answer in this session

Your tools in this session are not the ones named above. {tools}

You cannot call `{emit}`. Where the instructions above say to call it, end
instead with a final reply that is only the JSON object you would have passed
to it: no other text, and no code fence. It must validate against this JSON
schema:

{schema}
"""
TOOLS_TEXT = {
    "rubric": (
        "Use `view` to read a file or list a directory: it takes the place of"
        " read_file and list_directory. Do not change any file."
    ),
    "taste": (
        "Use `view` to read a file or list a directory, and `glob`, `grep` or `rg`"
        " to search: they take the place of read_file, list_directory and"
        " search_code. Do not change any file."
    ),
    "classify": "You need no tools for this. Do not change any file.",
}

# Run as `session.sh <workspace> <prompt> <out> <home> <log dir> <copilot>
# <model> <effort, or empty> <tool>...`, with the token in the environment.
SESSION = """#!/bin/sh
# One judge session: Copilot CLI reads the workspace, changes nothing, and
# ends in a reply. Its JSON lines go to the out file, and its stderr here.
[ $# -ge 9 ] || { echo "session.sh: expected at least 9 arguments" >&2; exit 2; }
workspace=$1 prompt=$2 out=$3 home=$4 logs=$5 copilot=$6 model=$7 effort=$8
shift 8
[ -n "$COPILOT_GITHUB_TOKEN" ] || { echo "session.sh: COPILOT_GITHUB_TOKEN is empty" >&2; exit 2; }
mkdir -p "$home/copilot" "$home/gh" "$logs" || exit 2
# Copilot CLI falls back to these tokens, to a login in its config home and to
# gh's. Empty homes leave it the judge's token alone.
unset GH_TOKEN GITHUB_TOKEN COPILOT_MODEL
export COPILOT_HOME="$home/copilot" GH_CONFIG_DIR="$home/gh" COPILOT_AUTO_UPDATE=false
[ -n "$HOME" ] && PATH="$HOME/.local/bin:$PATH"
text=$(cat "$prompt") || exit 2
cd "$workspace" || exit 2
set -- -p "$text" --model "$model" --available-tools "$@"
# Copilot CLI refuses --reasoning-effort for a model that takes none.
[ -n "$effort" ] && set -- "$@" --reasoning-effort "$effort"
# --available-tools decides what the model is offered; --deny-tool refuses a call.
exec "$copilot" "$@" --output-format json --allow-all-tools \\
    --deny-tool shell --deny-tool write \\
    --no-ask-user --no-custom-instructions --no-auto-update \\
    --disable-client-telemetry --disable-builtin-mcps \\
    --secret-env-vars COPILOT_GITHUB_TOKEN \\
    --log-level debug --log-dir "$logs" > "$out"
"""


class JudgeError(Exception):
    """The judge cannot run a step that is not a session's, such as senior's prompt code."""


@dataclass(frozen=True)
class JudgePaths:
    """Where the judge keeps things in the sandbox, and the `copilot` it runs."""

    root: str = "/opt/suss-judge"
    logs: str = "/logs/agent/judge"
    # Found on PATH, where the session puts install.sh's $HOME/.local/bin first.
    copilot: str = "copilot"

    @property
    def adapter(self) -> str:
        return f"{self.root}/adapter"

    @property
    def params(self) -> str:
        return f"{self.root}/params.json"

    @property
    def session(self) -> str:
        return f"{self.root}/{SESSION_SCRIPT}"

    @property
    def mark(self) -> str:
        return f"{self.root}/mark"

    @property
    def index(self) -> str:
        return f"{self.root}/index"


class CreditLedger:
    """
    The AI credits a job's judge sessions have spent and hold, in one file that
    every trial of the job shares, so trials running at once draw on one budget.

    A session holds a reservation while it runs. It starts only when what was
    spent, what running sessions hold and its own reservation fit the budget
    together. One that does not fit waits while other sessions hold credits,
    since they may spend less than they hold, and is refused once none do.
    Every read and write holds the file's lock, so processes agree.
    """

    POLL_SECONDS = 5.0

    def __init__(self, path: Path, budget: float):
        self.path = path
        self.budget = budget

    async def reserve(self, label: str, credits: float) -> str | None:
        """Hold `credits` for a session, and return the reservation, or None when it never fits."""
        while True:
            with self._locked() as file:
                spent, held = self._state(file)
                if spent + held + credits <= self.budget:
                    reservation = uuid.uuid4().hex
                    entry = {"reserve": reservation, "label": label, "credits": credits, "pid": os.getpid()}
                    file.write(json.dumps(entry) + "\n")
                    return reservation
                if held == 0:
                    return None
            await asyncio.sleep(self.POLL_SECONDS)

    def settle(self, reservation: str, credits: float) -> None:
        """Replace a reservation with what its session spent."""
        with self._locked() as file:
            file.write(json.dumps({"settle": reservation, "credits": credits}) + "\n")

    def spent(self) -> float:
        with self._locked() as file:
            return self._state(file)[0]

    @contextlib.contextmanager
    def _locked(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Appending, so every write lands at the end wherever a read left off.
        with open(self.path, "a+") as file:
            fcntl.flock(file, fcntl.LOCK_EX)
            file.seek(0)
            yield file

    @staticmethod
    def _state(file) -> tuple[float, float]:
        """
        What has been spent, and what running sessions hold.

        The reservation of a process that has died counts as spent, since its
        session may have spent it, and no one waits on it.
        """
        open_reservations: dict[str, dict] = {}
        spent = 0.0
        for line in file.read().splitlines():
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if "reserve" in entry:
                open_reservations[entry["reserve"]] = entry
            elif "settle" in entry:
                open_reservations.pop(entry["settle"], None)
                spent += entry["credits"]
        held = 0.0
        for entry in open_reservations.values():
            if _alive(entry["pid"]):
                held += entry["credits"]
            else:
                spent += entry["credits"]
        return spent, held


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    return True


def extract_json(text: str) -> tuple[Any, str]:
    """
    The JSON object a reply holds, and where it was: "clean" when the reply is
    only the object, "fenced" in a code fence, "embedded" in prose, or
    "none", with None, when it holds no object.
    """
    stripped = text.strip()
    with contextlib.suppress(ValueError):
        data = json.loads(stripped)
        return (data, "clean") if isinstance(data, dict) else (None, "none")
    for body in re.findall(r"```[A-Za-z]*\n(.*?)```", stripped, re.DOTALL):
        with contextlib.suppress(ValueError):
            data = json.loads(body)
            if isinstance(data, dict):
                return data, "fenced"
    decoder = json.JSONDecoder()
    # The first brace that opens a whole object, which is the outermost.
    for start in (match.start() for match in re.finditer(r"\{", stripped)):
        with contextlib.suppress(ValueError):
            data, _ = decoder.raw_decode(stripped, start)
            if isinstance(data, dict):
                return data, "embedded"
    return None, "none"


def read_events(text: str) -> dict[str, Any]:
    """
    What a session's JSON lines say it did: the credits, the models and
    efforts Copilot CLI reports it used, the calls, and the final reply.

    Credits are the largest `totalNanoAiu` of its usage checkpoints, over 1e9,
    or None where it reported none. The models are those of its model calls,
    and the efforts those its usage checkpoints name.
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
    efforts = {
        state.get("reasoning_effort")
        for kind, d in data
        if kind == "session.usage_checkpoint"
        for entry in d.get("promptCacheBreakState") or []
        for state in (entry.get("models") or {}).values()
    }
    messages = [d.get("content") or "" for kind, d in data if kind == "assistant.message"]
    tools: dict[str, int] = {}
    for kind, d in data:
        if kind == "tool.execution_start":
            tools[d.get("toolName")] = tools.get(d.get("toolName"), 0) + 1
    return {
        "credits": round(max(nano) / 1e9, 4) if nano else None,
        "reported_models": sorted(m for m in models if m),
        "reported_efforts": sorted(e for e in efforts if e),
        "model_calls": sum(1 for kind, _ in data if kind == "model.call_start"),
        "tool_calls": tools,
        "final": messages[-1] if messages else "",
    }


class JudgeAgent(ReplayAgent):
    def __init__(
        self,
        *,
        source_job: Any,
        credit_budget: Any,
        model: Any = MODEL,
        effort: Any = EFFORT,
        classifier_model: Any = CLASSIFIER_MODEL,
        copilot_version: Any = COPILOT_VERSION,
        dotenv: Any = ".env",
        adapter: Any = str(ADAPTER),
        session_seconds: Any = 1200,
        judge_seconds: Any = 3600,
        paths: JudgePaths = JudgePaths(),
        **kwargs: Any,
    ):
        """
        Judge each trial of the agent job at `source_job`, spending at most
        `credit_budget` AI credits over the whole job, as ReplayAgent regrades it.

        `effort` is empty for a model that takes none. `dotenv` holds
        COPILOT_GITHUB_TOKEN, and `adapter` is the copilot-cli adapter, whose
        install.sh installs `copilot_version`. Host paths are relative to the
        directory `harbor run` started in. A session is stopped after
        `session_seconds`, and the judge after `judge_seconds`, which must
        stay under the task's agent timeout. `paths` is where the sandbox keeps
        things; Harbor never passes it.

        Raises TypeError for a setting Harbor parsed into the wrong type,
        ConfigError when the .env cannot be read, and what ReplayAgent raises.
        """
        for key, value in (("credit_budget", credit_budget), ("session_seconds", session_seconds),
                           ("judge_seconds", judge_seconds)):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TypeError(
                    f"--ak {key} must be a number, but Harbor parsed it into {value!r},"
                    f" a {type(value).__name__}"
                )
        for key, value in (("model", model), ("effort", effort), ("classifier_model", classifier_model),
                           ("copilot_version", copilot_version), ("dotenv", dotenv), ("adapter", adapter)):
            if not isinstance(value, str):
                raise TypeError(
                    f"--ak {key} must be a string, but Harbor parsed it as JSON into {value!r},"
                    f" a {type(value).__name__}. Quote it as a JSON string."
                )
        super().__init__(source_job=source_job, **kwargs)
        self.credit_budget = credit_budget
        self.model = model
        self.effort = effort or None
        self.classifier_model = classifier_model
        self.copilot_version = copilot_version
        self.adapter = Path(adapter).absolute()
        self.session_seconds = session_seconds
        self.judge_seconds = judge_seconds
        self.paths = paths
        self._token = _read_env(Path(dotenv).absolute()).get(TOKEN_NAME, "")
        self._install = harness.load_manifest(self.adapter)
        self._session_manifest = harness.Manifest(
            name="suss-judge", version=version("suss"), install=None, run=SESSION_SCRIPT,
            allowed_hosts=(), env=(TOKEN_NAME,),
        )
        # The job's directory, which holds every trial of it.
        self._ledger = CreditLedger(self.logs_dir.parent.parent / LEDGER_NAME, credit_budget)
        self._installed: str | None = None
        self._setup_error: str | None = None
        self._record: dict[str, Any] = {}
        self._baseline: dict[str, Any] | None = None

    @staticmethod
    def name() -> str:
        return "suss-judge"

    async def setup(self, environment: BaseEnvironment) -> None:
        """
        Install Copilot CLI, and read the version it reports.

        Never raises an Exception: a failure is kept, so the trial is still
        regraded, and both judges record `failed:install`.
        """
        paths = self.paths
        try:
            workspace = await find_workspace(environment)
            await _upload(environment, self.adapter, paths.adapter)
            await _upload_bytes(environment, json.dumps({"version": self.copilot_version}).encode(), paths.params)
            await _upload_bytes(environment, SESSION.encode(), paths.session)
            await _as_root(environment, f"chmod 755 {shlex.quote(paths.session)}")
            code = await harness.install(
                environment, self._install, workspace, {},
                SandboxPaths(adapter=paths.adapter, params=paths.params, logs=paths.logs),
            )
            if code not in (0, None):
                self._setup_error = f"install.sh exited {code}; its output is in {paths.logs}/install.log"
                return
            result = await environment.exec(
                f'PATH="$HOME/.local/bin:$PATH" {shlex.quote(paths.copilot)} --no-auto-update --version 2>&1'
            )
            output = (result.stdout or "").strip()
            found = re.search(r"\d+\.\d+\.\d+", output)
            if result.return_code != 0 or found is None:
                self._setup_error = f"`copilot --version` exited {result.return_code}: {output[:500]}"
                return
            self._installed = found.group()
        except Exception as error:
            self._setup_error = f"{type(error).__name__}: {error}"

    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        """
        Regrade as ReplayAgent does, judging the patch once it is applied, and
        write judge.json beside replay.json.

        Raises what ReplayAgent raises. Nothing the judge does raises.
        """
        self._record = {
            "judge": {
                "client": CLIENT,
                "version": self._installed,
                "requested_version": self.copilot_version,
                "model": self.model,
                "effort": self.effort,
                "classifier_model": self.classifier_model,
            },
            "sessions": [],
        }
        await super().run(instruction, environment, context)
        self._unfinished("skipped:no_patch")
        self._write()

    async def _after_apply(
        self,
        environment: BaseEnvironment,
        workspace: str,
        patch: Path,
        applied: dict[str, Any],
    ) -> None:
        """Judge the applied patch, then put back anything a session changed."""
        self._record["patch"] = str(patch)
        try:
            async with asyncio.timeout(self.judge_seconds):
                await self._judge(environment, workspace, patch, applied)
        except TimeoutError:
            self._unfinished("failed:timeout", f"the judge passed its {self.judge_seconds} seconds")
            for session in self._record["sessions"]:
                session.setdefault("status", "failed:timeout")
        except Exception as error:
            self._unfinished("failed:error", f"{type(error).__name__}: {error}")
        finally:
            try:
                async with asyncio.timeout(CLEANUP_SECONDS):
                    await self._cleanup(environment, workspace)
            except Exception as error:
                self._record["cleanup_error"] = f"{type(error).__name__}: {error}"
            self._write()

    async def _judge(
        self, environment: BaseEnvironment, workspace: str, patch: Path, applied: dict[str, Any]
    ) -> None:
        record = self._record
        if applied["apply_rc"] != 0:
            self._unfinished("failed:patch_not_applied", applied["apply_out"][-500:])
            return
        if self._setup_error is not None:
            self._unfinished("failed:install", self._setup_error)
            return
        if not self._token:
            self._unfinished("skipped:no_token", f"the .env sets no {TOKEN_NAME}")
            return
        task = self._task_dir()
        repo_name = _repo_name(task)
        if repo_name is None:
            self._unfinished("skipped:no_repo_name", f"{task / 'task.toml'} sets no [verifier.env] REPO_NAME")
            return
        senior = _Senior(task / "tests", repo_name)
        patches = {"agent_patch": patch.read_bytes().decode(errors="replace"), "oracle_patch": _oracle(task)}
        prepared = await senior.call("prepare", **patches)
        record["judged_files"] = prepared["changed_files"]
        self._baseline = await self._fingerprint(environment, workspace)
        await _run(
            environment,
            f"cd {shlex.quote(workspace)} && index=$(git rev-parse --git-path index)"
            f' && {{ [ ! -f "$index" ] || cp "$index" {shlex.quote(self.paths.index)}; }}',
        )

        classified: dict[str, Any] = {}
        classifiers: dict[str, dict] = {}
        for key in ("agent", "oracle"):
            classified[key] = None
            prompt = prepared["classifier"][key]
            if prompt is None:
                continue
            session, final = await self._session(
                environment, workspace, f"classify-{key}", "classify", prompt, self.classifier_model, None
            )
            classifiers[key] = session
            if session["status"] == "ok":
                classified[key], session["json"] = extract_json(final)
                if classified[key] is None:
                    session["status"] = "failed:no_json"
        taste = await senior.call("taste", **patches, classified=classified)
        for key, error in taste["classified"].items():
            if error is not None:
                classifiers[key]["status"] = "failed:invalid_answer"
                classifiers[key]["error"] = error
        record["patch_bloat"] = taste["patch_bloat"]
        record["patch_classifications"] = taste["classifications"]

        rubric = prepared["rubric"]
        if rubric["status"] != "ok":
            record["rubric_status"] = rubric["status"]
        else:
            await self._score(environment, workspace, senior, "rubric", rubric["prompt"], rubric)
        if taste["status"] != "ok":
            record["taste_status"] = taste["status"]
        else:
            await self._score(environment, workspace, senior, "taste", taste["prompt"], {})

    async def _score(
        self, environment: BaseEnvironment, workspace: str, senior: "_Senior", kind: str,
        prompt: str, rubric: dict[str, Any],
    ) -> None:
        """Run one judge's session, and record its scores once senior's schema accepts its answer."""
        record = self._record
        session, final = await self._session(environment, workspace, kind, kind, prompt, self.model, self.effort)
        if session["status"] != "ok":
            record[f"{kind}_status"] = session["status"]
            record[f"{kind}_error"] = session.get("error", "")
            return
        answer, session["json"] = extract_json(final)
        if answer is None:
            record[f"{kind}_status"] = "failed:no_json"
            record[f"{kind}_error"] = f"the reply holds no JSON object: {final[:500]!r}"
            return
        scored = await senior.call(
            "score", judge=kind, answer=answer,
            **{key: rubric.get(key, []) for key in ("criteria", "fail_to_pass", "pass_to_pass")},
        )
        if not scored["valid"]:
            record[f"{kind}_status"] = "failed:invalid_answer"
            record[f"{kind}_error"] = scored["error"]
            return
        record[f"{kind}_status"] = "ok"
        if kind == "rubric":
            record["rubric"] = scored["criteria"]
            record["rubric_reasons"] = scored["reasons"]
            record["rubric_scores"] = {key: scored[key] for key in ("rubric_score", "fail_to_pass_score", "pass_to_pass_score")}
        else:
            record["taste"] = scored["taste"]
            record["taste_rationales"] = scored["rationales"]

    async def _session(
        self, environment: BaseEnvironment, workspace: str, name: str, kind: str,
        prompt: str, model: str, effort: str | None,
    ) -> tuple[dict[str, Any], str]:
        """
        Run one Copilot CLI session in the workspace, and return its record and
        final reply.

        It starts only once the job's ledger holds its reservation. Afterwards
        the workspace is checked against what the patch left, and anything the
        session changed is put back, which fails the session.
        """
        session: dict[str, Any] = {"name": name, "model": model, "effort": effort}
        self._record["sessions"].append(session)
        held = await self._ledger.reserve(f"{self.logs_dir.parent.name}/{name}", RESERVE[kind])
        if held is None:
            session["status"] = "skipped:budget"
            session["error"] = f"the job's credit budget of {self.credit_budget} cannot hold this session"
            return session, ""
        paths = self.paths
        logs = f"{paths.logs}/{name}"
        prompt_file = f"{paths.root}/prompts/{name}.md"
        out = f"{logs}/out.jsonl"
        home = f"{paths.root}/homes/{name}"
        session["prompt_sha256"] = hashlib.sha256(prompt.encode()).hexdigest()
        spent = RESERVE[kind]
        try:
            await _upload_bytes(environment, prompt.encode(), prompt_file)
            await _run(environment, f"mkdir -p {shlex.quote(logs)} && touch {shlex.quote(paths.mark)}")
            started = time.monotonic()
            code = await harness._start(
                environment, self._session_manifest, SESSION_SCRIPT,
                [workspace, prompt_file, out, home, f"{logs}/logs", paths.copilot, model, effort or "",
                 *TOOLS[kind]],
                workspace, {TOKEN_NAME: self._token},
                SandboxPaths(adapter=paths.root, logs=logs), "session.log", self.session_seconds,
            )
            session["seconds"] = round(time.monotonic() - started, 1)
            session["exit_code"] = code
            events = read_events(await _output(environment, f"grep -E {EVENT_LINES} {shlex.quote(out)}"))
            final = events.pop("final")
            session.update(events)
            if events["credits"] is not None:
                spent = events["credits"]
        finally:
            self._ledger.settle(held, spent)
        await _run(environment, f"rm -rf {shlex.quote(home)}")
        session["removed_for_token"] = await self._remove_token(environment)
        session["repo_unchanged"], session["restored"] = await self._guard(environment, workspace)

        if not session["repo_unchanged"]:
            session["status"] = "failed:repo_changed"
            session["error"] = "the session changed the workspace, which was put back"
        elif code is None:
            session["status"] = "failed:timeout"
            session["error"] = f"the session passed its {self.session_seconds} seconds"
        elif code != 0:
            session["status"] = f"failed:exit_{code}"
            session["error"] = f"Copilot CLI exited {code}; its stderr is in {logs}/session.log"
        elif session["reported_models"] != [model]:
            session["status"] = "failed:wrong_model"
            session["error"] = f"asked for {model}, and Copilot CLI reports {session['reported_models']}"
        elif effort is not None and session["reported_efforts"] != [effort]:
            session["status"] = "failed:wrong_effort"
            session["error"] = f"asked for effort {effort}, and Copilot CLI reports {session['reported_efforts']}"
        else:
            session["status"] = "ok"
        return session, final

    async def _fingerprint(self, environment: BaseEnvironment, workspace: str, since: str | None = None) -> dict:
        """
        What the workspace holds: its tree, HEAD, its status with and without
        ignored files, and each path written since the file `since` was.
        """
        tree = await read_start_tree(environment, workspace)
        newer = f" && find . -path ./.git -prune -o -newer {shlex.quote(since)} -print | head -n 20" if since else ""
        output = await _run(
            environment,
            f"cd {shlex.quote(workspace)} && git rev-parse HEAD"
            " && git status --porcelain=v1 -uall -z | git hash-object --stdin"
            " && git status --porcelain=v1 --ignored -z | git hash-object --stdin" + newer,
        )
        lines = output.splitlines()
        return {"tree": tree, "head": lines[0], "status": lines[1], "ignored": lines[2],
                "newer": [line for line in lines[3:] if line]}

    async def _guard(self, environment: BaseEnvironment, workspace: str) -> tuple[bool, bool | None]:
        """
        Whether the workspace is as the patch left it, and when it is not,
        whether putting it back succeeded.

        It is put back to the tree the patch left, with the index as it was.
        An ignored file a session changed cannot be put back.
        """
        baseline = self._baseline
        if baseline is None:
            return True, None
        keys = ("tree", "head", "status", "ignored")
        now = await self._fingerprint(environment, workspace, since=self.paths.mark)
        if all(now[key] == baseline[key] for key in keys) and not now["newer"]:
            return True, None
        index = shlex.quote(self.paths.index)
        await _run(
            environment,
            f"cd {shlex.quote(workspace)} && git clean -fdq"
            f" && git checkout -q {shlex.quote(baseline['tree'])} -- ."
            f' && index=$(git rev-parse --git-path index)'
            f' && if [ -f {index} ]; then cp {index} "$index"; else rm -f "$index"; fi',
        )
        after = await self._fingerprint(environment, workspace)
        return False, all(after[key] == baseline[key] for key in keys)

    async def _remove_token(self, environment: BaseEnvironment) -> list[str]:
        """Delete every file under the judge's directories that holds the token, and return their paths."""
        dirs = " ".join(shlex.quote(path) for path in (self.paths.logs, self.paths.root))
        output = await _output(
            environment,
            f'grep -rlF -e "${TOKEN_NAME}" -- {dirs} 2>/dev/null'
            ' | while IFS= read -r f; do rm -f -- "$f" && printf "%s\\n" "$f"; done',
            {TOKEN_NAME: self._token},
        )
        return [line for line in output.splitlines() if line]

    async def _cleanup(self, environment: BaseEnvironment, workspace: str) -> None:
        """Once the judge stops, however it stops: no session's home or token is left, and the workspace is put back."""
        await _run(environment, f"rm -rf {shlex.quote(self.paths.root + '/homes')}")
        removed = await self._remove_token(environment)
        if removed:
            self._record.setdefault("removed_for_token", []).extend(removed)
        unchanged, restored = await self._guard(environment, workspace)
        if not unchanged:
            self._record["restored_after_judge"] = restored

    def _unfinished(self, status: str, error: str = "") -> None:
        """Give each judge that has no status yet this one."""
        for kind in JUDGES:
            if self._record.get(f"{kind}_status") is None:
                self._record[f"{kind}_status"] = status
                if error:
                    self._record[f"{kind}_error"] = error

    def _write(self) -> None:
        """Write judge.json, with the scores flattened as senior's reward.json has them."""
        record = self._record
        scores: dict[str, float | int] = {}
        rubric = record.get("rubric_scores") or {}
        for key, flat in (("rubric_score", "rubric_score"), ("fail_to_pass_score", "rubric_f2p_score"),
                          ("pass_to_pass_score", "rubric_p2p_score")):
            if rubric.get(key) is not None:
                scores[flat] = round(float(rubric[key]), 4)
        bloat = record.get("patch_bloat") or {}
        if bloat.get("bloat_ratio") is not None:
            scores["taste_patch_bloat"] = round(float(bloat["bloat_ratio"]), 4)
        taste = record.get("taste") or {}
        for key, flat in (("practice_alignment_score", "taste_practice_alignment"),
                          ("relative_taste_score", "taste_relative_taste")):
            if taste.get(key) is not None:
                scores[flat] = round(float(taste[key]), 2)
        for kind in JUDGES:
            if record.get(f"{kind}_status"):
                scores[f"{kind}_judge_ok"] = 1 if record[f"{kind}_status"] == "ok" else 0
        record["scores"] = scores
        record["credits"] = round(sum(s.get("credits") or 0 for s in record["sessions"]), 4)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        (self.logs_dir / RECORD_NAME).write_text(json.dumps(record, indent=2) + "\n")

    def _task_dir(self) -> Path:
        """The task's directory on the host, as the trial's config.json names it."""
        config = json.loads((self.logs_dir.parent / "config.json").read_text())
        return Path(config["task"]["path"]).absolute()


def _repo_name(task: Path) -> str | None:
    """The REPO_NAME senior's judges read, from the task's [verifier.env]."""
    with open(task / "task.toml", "rb") as file:
        name = tomllib.load(file).get("verifier", {}).get("env", {}).get("REPO_NAME")
    return name if isinstance(name, str) and name else None


def _oracle(task: Path) -> str:
    """The reference patch, where senior's taste judge looks for it, or empty."""
    for path in (task / "tests" / "judge" / "oracle.patch", task / "solution" / "oracle.patch"):
        if path.is_file():
            return path.read_text(errors="replace")
    return ""


async def _run(environment: BaseEnvironment, command: str) -> str:
    """Run a command, merging its stderr, and return its output. Raises JudgeError when it fails."""
    result = await environment.exec(f"{{ {command}; }} 2>&1")
    if result.return_code != 0:
        raise JudgeError(f"`{command}` exited {result.return_code}: {(result.stdout or '').strip()[:1000]}")
    return result.stdout or ""


async def _output(environment: BaseEnvironment, command: str, env: dict[str, str] | None = None) -> str:
    """A command's stdout, whatever it exits with."""
    result = await environment.exec(command, env=env)
    return result.stdout or ""


class _Senior:
    """Senior's own prompt and schema code from one task's tests/, run in a Python process of its own."""

    def __init__(self, tests: Path, repo_name: str):
        self.tests = tests
        self.repo_name = repo_name

    async def call(self, op: str, **request: Any) -> dict:
        """
        Run one step of `_senior_main` on the task's code, and return its answer.

        Raises JudgeError when the process fails, says nothing, or passes
        SENIOR_SECONDS.
        """
        payload = json.dumps({"op": op, "tests": str(self.tests), **request}).encode()
        # REPO_NAME is read when run_judge.py is imported.
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-I", "-m", "suss.judge",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, env={**os.environ, "REPO_NAME": self.repo_name},
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(payload), SENIOR_SECONDS)
        except TimeoutError:
            raise JudgeError(f"senior's code took more than {SENIOR_SECONDS} seconds to {op}") from None
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
        if process.returncode != 0 or not stdout.strip():
            raise JudgeError(
                f"senior's code in {self.tests} cannot {op}: exit {process.returncode},"
                f" {stderr.decode(errors='replace').strip()[-1500:]}"
            )
        return json.loads(stdout)


def _load_senior(tests: Path) -> Any:
    """Import the task's run_judge.py, with the ssb_lib beside it."""
    sys.path.insert(0, str(tests))
    spec = importlib.util.spec_from_file_location("senior_run_judge", tests / "run_judge.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@contextlib.contextmanager
def _captured(owner: Any, name: str):
    """
    Replace the function `owner.name` with one that keeps its keyword
    arguments and returns None, as a model call that gave no answer.
    """
    seen: dict[str, Any] = {}
    real = getattr(owner, name)

    def keep(**kwargs: Any) -> None:
        seen.update(kwargs)

    setattr(owner, name, keep)
    try:
        yield seen
    finally:
        setattr(owner, name, real)


def _prompt(seen: dict[str, Any], user: str, kind: str, emit: str) -> str:
    """One prompt for `copilot -p`, which takes no system prompt: senior's system prompt, its user prompt, and how to answer."""
    schema = json.dumps(seen["emit_schema" if "emit_schema" in seen else "schema"].model_json_schema(), indent=2)
    answer = ANSWER.format(tools=TOOLS_TEXT[kind], emit=emit, schema=schema)
    return f"{seen['system']}\n\n---\n\n{user}{answer}"


def _classifier_prompt(patch: str) -> tuple[str, str] | None:
    """The classifier's prompt for `patch` and its user message, or None where senior would not call it."""
    llm_utils = importlib.import_module("ssb_lib.llm_utils")
    classify = importlib.import_module("ssb_lib.patch_classify").classify_patch
    if not patch.strip():
        return None
    with _captured(llm_utils, "complete_structured") as seen:
        classify(patch, model="unused")
    if not seen:
        return None
    user = seen["messages"][0]["content"]
    return _prompt(seen, user, "classify", seen["tool_name"]), user


def _prepare(rj: Any, request: dict) -> dict:
    """The rubric judge's prompt, as rubric_main builds it, and the classifier's for each patch."""
    tests = Path(request["tests"])
    text = request["agent_patch"].strip()
    changed = rj._extract_changed_files(text)
    rubric_path = tests / "judge" / "rubric.json"
    rubric: dict[str, Any] = {"status": "skipped:no_rubric_file"}
    if rubric_path.exists():
        files, fail_to_pass, pass_to_pass = rj.parse_rubric(rubric_path)
        names = list({**fail_to_pass, **pass_to_pass})
        rubric = {"status": "skipped:no_criteria"}
        if names:
            extra = [path for path in files if path not in set(changed)]
            block = rj._build_criteria_block(fail_to_pass, pass_to_pass)
            with _captured(rj.llm_tools, "run_explore_and_emit") as seen:
                rj._rubric_explore_and_score(
                    "unused", block, names, text, changed, extra, os.environ["REPO_NAME"]
                )
            rubric = {
                "status": "ok",
                "prompt": _prompt(seen, seen["user"], "rubric", seen["emit_tool_name"]),
                "criteria": names,
                "fail_to_pass": list(fail_to_pass),
                "pass_to_pass": list(pass_to_pass),
            }
    classifier = {}
    for key in ("agent", "oracle"):
        found = _classifier_prompt(request[f"{key}_patch"])
        classifier[key] = found[0] if found else None
    return {"changed_files": changed, "rubric": rubric, "classifier": classifier}


def _taste(rj: Any, request: dict) -> dict:
    """
    Filter both patches with the classifier's answers, as taste_main does, and
    build the taste judge's prompt and the bloat figures from them.

    An answer that is missing or fails senior's schema counts as no answer,
    which senior's classifier treats as every file behavioural.
    """
    agent, oracle = request["agent_patch"], request["oracle_patch"]
    found: dict[str, Any] = {"patch_bloat": None, "classifications": None, "classified": {}}
    if not (agent.strip() or oracle.strip()):
        return {**found, "status": "skipped:no_patches"}
    schema = importlib.import_module("ssb_lib.patch_classify")._PatchClassifications
    llm_utils = importlib.import_module("ssb_lib.llm_utils")
    answers = []
    for key, patch in (("agent", agent), ("oracle", oracle)):
        data = request["classified"].get(key)
        parsed = None
        if data is not None:
            try:
                parsed = schema.model_validate(data)
                found["classified"][key] = None
            except Exception as error:
                found["classified"][key] = str(error)[:1000]
        prompt = _classifier_prompt(patch)
        if prompt is not None:
            answers.append((prompt[1], parsed))

    def answer(**kwargs: Any) -> Any:
        # By the prompt, since senior skips the call for a patch with no files.
        user = kwargs["messages"][0]["content"]
        for i, (asked, parsed) in enumerate(answers):
            if asked == user:
                return answers.pop(i)[1]
        return None

    real = llm_utils.complete_structured
    llm_utils.complete_structured = answer
    try:
        prepared = rj._prepare_filtered_patches(agent, oracle, classifier_model="unused")
    finally:
        llm_utils.complete_structured = real
    found["patch_bloat"] = rj.assess_patch_bloat(
        agent, oracle,
        agent_filtered=prepared["agent_filtered"], oracle_filtered=prepared["oracle_filtered"],
        agent_dropped=prepared["agent_dropped"], oracle_dropped=prepared["oracle_dropped"],
    )
    found["classifications"] = {
        key: [c.model_dump(mode="json") for c in prepared[f"{key}_classifications"]]
        for key in ("agent", "oracle")
    }
    if not (prepared["agent_filtered"].strip() and prepared["oracle_filtered"].strip()):
        return {**found, "status": "skipped:empty_patches"}
    with _captured(rj.llm_tools, "run_explore_and_emit") as seen:
        rj.assess_taste_with_llm(prepared["agent_filtered"], prepared["oracle_filtered"], model="unused")
    return {**found, "status": "ok", "prompt": _prompt(seen, seen["user"], "taste", seen["emit_tool_name"])}


def _score(rj: Any, request: dict) -> dict:
    """
    Validate a judge's answer with senior's schema, and score it as senior does.

    A rubric answer must score each criterion once, and no other.
    """
    answer = request["answer"]
    if request["judge"] == "taste":
        try:
            flat = rj._flatten_taste_scores(rj.TasteScores.model_validate(answer))
        except Exception as error:
            return {"valid": False, "error": str(error)[:1000]}
        return {
            "valid": True,
            "taste": {k: v for k, v in flat.items() if not k.endswith("_rationale")},
            "rationales": {k: v for k, v in flat.items() if k.endswith("_rationale")},
        }
    try:
        result = rj.RubricResult.model_validate(answer)
    except Exception as error:
        return {"valid": False, "error": str(error)[:1000]}
    names = [c.name for c in result.criteria]
    if sorted(names) != sorted(request["criteria"]):
        return {"valid": False, "error": f"the answer scores {names}, and the rubric's criteria are {request['criteria']}"}
    scores = {c.name: c.score for c in result.criteria}

    def mean(names: list[str]) -> float | None:
        return round(sum(scores[n] for n in names) / len(names), 4) if names else None

    return {
        "valid": True,
        "criteria": scores,
        "reasons": {c.name: c.reason for c in result.criteria},
        "rubric_score": mean(request["criteria"]),
        "fail_to_pass_score": mean(request["fail_to_pass"]),
        "pass_to_pass_score": mean(request["pass_to_pass"]),
    }


def _senior_main() -> None:
    """Answer one request from `_Senior.call`, read from stdin, on stdout."""
    request = json.load(sys.stdin)
    steps = {"prepare": _prepare, "taste": _taste, "score": _score}
    # Senior's code prints its progress, which must not reach the answer.
    with contextlib.redirect_stdout(sys.stderr):
        answer = steps[request["op"]](_load_senior(Path(request["tests"])), request)
    json.dump(answer, sys.stdout)


if __name__ == "__main__":
    _senior_main()
