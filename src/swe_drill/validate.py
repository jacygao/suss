"""
Senior SWE-Bench's run_validate.py, with both places it calls a model run
through Copilot CLI: the validation agent, which writes and repairs the story
scripts, and the review judge, whose verdict decides a retry or a discard.

Each senior task's copy holds this as tests/swe_drill_validate.py, and its
test.sh runs it where the task's runs run_validate.py, which stays as the task
has it. It runs in the verifier's sandbox under senior's own Python, so it
imports only the standard library and the task's own modules.

JudgeVerifier leaves the settings and the Copilot token in the directory that
SWE_DRILL_VALIDATION names. The token's file is deleted as it is read, and the
token goes into each Copilot CLI command's environment alone, never this
process's, so no story script and no command the agent runs is given it.
Every file the sessions leave that holds it is deleted.
"""

from __future__ import annotations

import importlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import traceback
import uuid
from pathlib import Path

SETTINGS_ENV = "SWE_DRILL_VALIDATION"
SETTINGS_NAME = "settings.json"
TOKEN_FILE = "token"
TOKEN_NAME = "COPILOT_GITHUB_TOKEN"
RECORD_NAME = "validation.json"
CLIENT = "copilot-cli"
# run_validate.py reads the outcome of a claude_code run from cc_output.txt and
# cc_result_subtype.txt, which the agent's session writes here.
HARNESS = "claude_code"
# Linux refuses a single argument over 128 KiB.
ARG_LIMIT = 120_000
# How long one review-judge session may take, and how many sessions one review
# may take: its first answer, and a retry of one that cannot be read.
REVIEW_SESSION_SECONDS = 300
REVIEW_SESSIONS = 2
# run_validate.py calls the review judge twice: before the retry, and after the stories run.
REVIEWS = 2

POINTER = "Your instructions are in {path}. Read all of it with the view tool, then follow it exactly."
COMMON = (
    "--output-format", "json", "--no-ask-user", "--no-custom-instructions", "--no-auto-update",
    "--disable-client-telemetry", "--disable-builtin-mcps", "--secret-env-vars", TOKEN_NAME,
    "--log-level", "debug",
)
AGENT_TOOLS = (
    "--allow-all-tools", "--allow-all-paths", "--deny-tool", "web_fetch", "--deny-tool", "web_search",
)
REVIEW_TOOLS = ("--available-tools", "view", "--allow-all-tools", "--deny-tool", "shell", "--deny-tool", "write")

AGENT_FOOTER = """

---

# How to work in this session

You are running non-interactively, and nobody will answer a question. Use
your shell tool to run commands and your file tools to read and write files.
Write each script into {scripts_dir} and nowhere else. When every script is
written and reviewed, end with a one-paragraph summary of what you wrote.
"""
REVIEW_FOOTER = """

---

# How to answer in this session

You cannot call `submit_review`, and you need no tools: everything you need
is above. Do not change any file. Where the instructions say to call
`submit_review`, end instead with a final reply that is only the JSON object
you would have passed to it: no other text, and no code fence. It must
validate against this JSON schema:

{schema}
"""
RETRY = """Your final reply cannot be used: {why}

Reply again with only the JSON object the instructions ask for, valid against
the JSON schema they give: no other text, and no code fence. Inside a string,
escape each double quote as \\" and each backslash as \\\\.
"""


class SetupError(Exception):
    """The validation cannot reach Copilot CLI with what JudgeVerifier left."""


def load(tests: Path):
    """Import the task's run_validate.py, with the ssb_lib beside it, as test.sh would run it."""
    sys.path.insert(0, str(tests))
    return importlib.import_module("run_validate")


def run(rv) -> int:
    """
    Run `rv`, the task's run_validate module, with its model calls made
    through Copilot CLI, and return the exit code run_validate.py would.

    A setup that cannot reach Copilot CLI exits 2 before any session, as
    run_validate.py exits for a validation agent that cannot run, which makes
    the trial invalid. validation.json in its logs directory records every
    session as it ends, and the exit code once run_validate.py stops.
    """
    record_path = Path(rv.LOGS_DIR) / RECORD_NAME
    validation = None
    code = 2
    try:
        validation = Validation.from_environment(rv, record_path)
        validation.install()
        rv.main()
        code = 0
    except SetupError as error:
        record = {"status": "failed:setup", "error": str(error), "sessions": [], "credits": 0, "exit": 2}
        record_path.parent.mkdir(parents=True, exist_ok=True)
        record_path.write_text(json.dumps(record, indent=2) + "\n")
        print(f"  Validation through Copilot CLI cannot start: {error}", file=sys.stderr)
        return 2
    except SystemExit as stopped:
        code = stopped.code if isinstance(stopped.code, int) else (0 if stopped.code is None else 1)
    except Exception:
        # As run_validate.py's own entry point: a crash is an invalid trial, never a pass.
        traceback.print_exc(file=sys.stderr)
        code = 2
    finally:
        if validation is not None:
            validation.finish(code)
    return code


class Validation:
    """The Copilot CLI sessions of one validation, and the record of them."""

    def __init__(self, rv, directory: Path, settings: dict, token: str, record_path: Path):
        self.rv = rv
        self.directory = directory
        self.settings = settings
        self.token = token
        self.record_path = record_path
        self.logs = Path(rv.LOGS_DIR) / "copilot-validation"
        self.record = {
            "validation_agent": {
                "client": CLIENT,
                "version": settings.get("version"),
                "model": settings["model"],
                "effort": settings.get("effort") or None,
            },
            "status": "ok",
            "sessions": [],
            "credits": 0,
        }

    @classmethod
    def from_environment(cls, rv, record_path: Path) -> Validation:
        """
        Read the settings and the token from the directory SWE_DRILL_VALIDATION
        names, deleting the token's file before anything else is done.

        Raises SetupError naming what is missing: the variable, the settings,
        the token, or an executable `copilot` at the path they give, or with
        the reason the settings give for having none.
        """
        named = os.environ.get(SETTINGS_ENV)
        if not named:
            raise SetupError(f"{SETTINGS_ENV} is not set, so there is no Copilot CLI to run")
        directory = Path(named)
        token_path = directory / TOKEN_FILE
        try:
            token = token_path.read_text().strip()
        except OSError:
            token = ""
        finally:
            token_path.unlink(missing_ok=True)
        try:
            settings = json.loads((directory / SETTINGS_NAME).read_text())
        except (OSError, ValueError) as error:
            raise SetupError(f"{directory / SETTINGS_NAME} cannot be read: {error}") from None
        if settings.get("error"):
            raise SetupError(settings["error"])
        if not token:
            raise SetupError(f"{token_path} gave no {TOKEN_NAME}")
        copilot = settings.get("copilot") or ""
        if not (os.path.isfile(copilot) and os.access(copilot, os.X_OK)):
            raise SetupError(f"no executable copilot at {copilot!r} for this user, {os.getuid()}")
        return cls(rv, directory, settings, token, record_path)

    def install(self) -> None:
        """Make run_validate's validation agent, review judge and credential check those of this validation."""
        rv = self.rv
        # Read by run_validate.py's config: the harness whose outcome files the
        # agent's sessions write, and the model its records name.
        os.environ["SSB_OVERRIDE_VA_HARNESS"] = HARNESS
        os.environ["SSB_OVERRIDE_VA_MODEL"] = self.settings["model"]
        rv.llm_utils.have_credentials = lambda: True
        rv.invoke_validation_agent = self.agent
        importlib.import_module("ssb_lib.validation_judge.judge")
        # The package re-exports the function under the module's own name, so
        # the module is found by its full name, and both are patched.
        sys.modules["ssb_lib.validation_judge"].judge = self.review
        sys.modules["ssb_lib.validation_judge.judge"].judge = self.review
        self._write()

    def agent(self, prompt: str, config, max_turns: int, timeout: int) -> None:
        """
        One validation-agent session in the repository, standing in for
        run_validate.invoke_validation_agent.

        It writes cc_output.txt and cc_result_subtype.txt, from which
        run_validate.py tells an agent that ran from one that failed.
        """
        rv = self.rv
        scripts = Path(rv.SCRIPTS_DIR)
        scripts.mkdir(parents=True, exist_ok=True)
        number = sum(1 for s in self.record["sessions"] if s["kind"] == "agent") + 1
        session, final = self._session(
            f"agent-{number}", "agent", prompt + AGENT_FOOTER.format(scripts_dir=scripts),
            cwd=Path(rv.REPO_DIR), timeout=timeout, tools=AGENT_TOOLS,
        )
        logs = Path(rv.LOGS_DIR)
        (logs / "cc_output.txt").write_text(self._redacted(final)[:50000] or "(no final message)")
        (logs / "cc_result_subtype.txt").write_text("success" if session["status"] == "ok" else session["status"])

    def review(self, inputs, *, rubric=None, model=None, thinking_budget=0):
        """
        One review, standing in for ssb_lib.validation_judge.judge: its prompt,
        and the answer read with the judge's own parse_response and
        should_discard.

        An answer that cannot be read is retried once in the same session.
        Raises RuntimeError when no answer can be, as senior's judge does.
        """
        vj = sys.modules["ssb_lib.validation_judge.judge"]
        rubric = rubric or vj._load_default_rubric()
        text, notes = vj.render_judge_prompt(inputs, rubric)
        schema = vj._tool_schema(rubric)["function"]["parameters"]
        prompt = text + REVIEW_FOOTER.format(schema=json.dumps(schema, indent=1))
        number = sum(1 for s in self.record["sessions"] if s["kind"] == "review" and "retries" not in s) + 1
        cwd = self.directory / "review"
        cwd.mkdir(parents=True, exist_ok=True)
        started = time.monotonic()
        resumes = None
        why = ""
        for attempt in range(REVIEW_SESSIONS):
            name = f"review-{number}" if attempt == 0 else f"review-{number}-retry"
            session, final = self._session(
                name, "review", prompt if attempt == 0 else RETRY.format(why=why),
                cwd=cwd, timeout=REVIEW_SESSION_SECONDS, tools=REVIEW_TOOLS, resumes=resumes,
            )
            resumes = session
            if session["status"] != "ok":
                break
            answer = _json_object(final)
            if answer is None:
                why = "it holds no JSON object"
                self._fail(session, "failed:no_json", f"the reply holds no JSON object: {final[:500]!r}")
                continue
            try:
                severity, actionable = vj.parse_response(answer)
            except Exception as error:
                why = f"it fails the schema: {str(error)[:800]}"
                self._fail(session, "failed:invalid_answer", str(error)[:1000])
                continue
            discard, reasons = vj.should_discard(severity, rubric)
            severity = vj.SeverityFindings(
                per_criterion=severity.per_criterion, rationales=severity.rationales,
                per_story=severity.per_story, overall=severity.overall,
                discard_recommended=discard, discard_reasons=reasons,
            )
            self._remove_home(session)
            return vj.JudgeResult(
                severity=severity, actionable=actionable, raw=answer,
                elapsed_sec=time.monotonic() - started, truncation_notes=notes,
            )
        raise RuntimeError(
            f"the review judge gave no answer submit_review would take, in {name}: {resumes.get('error', '')[:300]}"
        )

    def _session(
        self, name: str, kind: str, prompt: str, *, cwd: Path, timeout: float,
        tools: tuple[str, ...], resumes: dict | None = None,
    ) -> tuple[dict, str]:
        """Run one Copilot CLI session, or resume `resumes`, and record it. Returns its record and final reply."""
        session_id = resumes["session_id"] if resumes else str(uuid.uuid4())
        session: dict = {"name": name, "kind": kind, "session_id": session_id}
        if resumes:
            session["retries"] = resumes["name"]
        self.record["sessions"].append(session)
        logs = self.logs / name
        logs.mkdir(parents=True, exist_ok=True)
        if len(prompt.encode()) > ARG_LIMIT:
            (logs / "prompt.md").write_text(prompt)
            prompt = POINTER.format(path=logs / "prompt.md")
        settings = self.settings
        start = "--resume" if resumes else "--session-id"
        argv = [settings["copilot"], "-p", prompt, start, session_id, "--model", settings["model"]]
        if settings.get("effort"):
            argv += ["--reasoning-effort", settings["effort"]]
        argv += [*COMMON, "--log-dir", str(logs / "logs"), "--max-ai-credits", str(settings["credits"]), *tools]
        home = self._home(session)
        env = {key: value for key, value in os.environ.items() if key not in ("GH_TOKEN", "GITHUB_TOKEN", "COPILOT_MODEL")}
        # Empty homes leave Copilot CLI no login but the token.
        env.update(
            COPILOT_HOME=str(home / "copilot"), GH_CONFIG_DIR=str(home / "gh"),
            COPILOT_AUTO_UPDATE="false", **{TOKEN_NAME: self.token},
        )
        spent_before = (resumes.get("session_credits") or resumes.get("credits") or 0) if resumes else 0
        started = time.monotonic()
        code, out = _run(argv, cwd, env, timeout)
        (logs / "out.jsonl").write_text(self._redacted(out))
        events = read_events(out)
        final = events.pop("final")
        session.update(events, exit_code=code, seconds=round(time.monotonic() - started, 1))
        # Copilot CLI counts a resumed session's usage on from where it stopped.
        if resumes and session["credits"] is not None:
            session["session_credits"] = session["credits"]
            session["credits"] = round(max(session["credits"] - spent_before, 0), 4)
        if code is None:
            self._fail(session, "failed:timeout", f"the session passed its {timeout:g} seconds")
        elif code != 0:
            self._fail(session, f"failed:exit_{code}", f"Copilot CLI exited {code}; its logs are in {logs}")
        elif not session["model_calls"]:
            self._fail(session, "failed:no_model_call", "Copilot CLI called no model")
        else:
            session["status"] = "ok"
        if kind == "agent" or session["status"] != "ok":
            self._remove_home(session)
        session["removed_for_token"] = self._remove_token(logs)
        self._write()
        return session, final

    def _fail(self, session: dict, status: str, error: str) -> None:
        session["status"], session["error"] = status, error
        self._write()

    def _home(self, session: dict) -> Path:
        return self.directory / "homes" / session["session_id"]

    def _remove_home(self, session: dict) -> None:
        shutil.rmtree(self._home(session), ignore_errors=True)

    def _remove_token(self, directory: Path) -> list[str]:
        """Delete every file under `directory` that holds the token, and return their paths."""
        removed = []
        for path in sorted(directory.rglob("*")):
            try:
                if path.is_file() and not path.is_symlink() and self.token.encode() in path.read_bytes():
                    path.unlink()
                    removed.append(str(path))
            except OSError:
                continue
        return removed

    def _redacted(self, text: str) -> str:
        return text.replace(self.token, "[redacted]")

    def _write(self) -> None:
        record = self.record
        record["credits"] = round(sum(s.get("credits") or 0 for s in record["sessions"]), 4)
        self.record_path.parent.mkdir(parents=True, exist_ok=True)
        self.record_path.write_text(self._redacted(json.dumps(record, indent=2)) + "\n")

    def finish(self, code: int) -> None:
        """Record the exit code, and leave no Copilot home behind."""
        shutil.rmtree(self.directory / "homes", ignore_errors=True)
        self.record["exit"] = code
        self._write()


def _run(argv: list[str], cwd: Path, env: dict, timeout: float) -> tuple[int | None, str]:
    """
    Run a command in a session of its own, and return its exit code and
    stdout. At `timeout` the whole session is killed, with whatever the agent
    started, and the code is None.
    """
    process = subprocess.Popen(
        argv, cwd=str(cwd), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        out, err = process.communicate(timeout=timeout)
        code = process.returncode
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        out, err = process.communicate()
        code = None
    if err:
        sys.stderr.write(err.decode(errors="replace").replace(env[TOKEN_NAME], "[redacted]")[-2000:])
    return code, out.decode(errors="replace")


def read_events(text: str) -> dict:
    """
    What a session's JSON lines say it did: the credits, the models and
    efforts it reports, the calls, and the final reply.

    Credits are the largest `totalNanoAiu` of its usage checkpoints, over 1e9,
    or None where it reported none.
    """
    events = []
    for line in text.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
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
    tools: dict = {}
    for kind, d in data:
        if kind == "tool.execution_start":
            tools[d.get("toolName")] = tools.get(d.get("toolName"), 0) + 1
    messages = [d.get("content") or "" for kind, d in data if kind == "assistant.message"]
    return {
        "credits": round(max(nano) / 1e9, 4) if nano else None,
        "reported_models": sorted(m for m in models if m),
        "reported_efforts": sorted(e for e in efforts if e),
        "model_calls": sum(1 for kind, _ in data if kind == "model.call_start"),
        "tool_calls": tools,
        "final": messages[-1] if messages else "",
    }


def _json_object(text: str) -> dict | None:
    """The JSON object a reply is, or holds in a code fence or among prose, or None."""
    stripped = text.strip()
    fenced = re.fullmatch(r"```[A-Za-z]*\s*(.*?)\s*```", stripped, re.DOTALL)
    if fenced:
        stripped = fenced.group(1)
    candidates = [stripped]
    start, end = stripped.find("{"), stripped.rfind("}")
    if 0 <= start < end:
        candidates.append(stripped[start:end + 1])
    for candidate in candidates:
        try:
            found = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(found, dict):
            return found
    return None


def main() -> int:
    return run(load(Path(__file__).resolve().parent))


if __name__ == "__main__":
    sys.exit(main())
