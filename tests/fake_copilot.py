"""
A stand-in for Copilot CLI's `copilot -p`, which answers each judge session
from a scenario a test writes, and records how it was started.

It is copied into each test's sandbox with the scenario directory as its
first line's argument, so concurrent tests never share one. The session it
plays is told from the prompt, by the tool the prompt says to call, or for a
resumed session from what its first call left in COPILOT_HOME. A resumed call
plays the kind's `retry` scenario, and reports usage counted on from the
first call's, as Copilot CLI does.
"""

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

SCENARIO_DIR = Path(sys.argv.pop(1))
KINDS = (("classify_files", "classify"), ("submit_taste_scores", "taste"), ("submit_scores", "rubric"))


def options(argv: list[str]) -> dict[str, list[str]]:
    """Each flag with the values after it, up to the next flag."""
    found: dict[str, list[str]] = {}
    flag = None
    for arg in argv:
        # A prompt can start with a dash, so whatever follows -p is its value.
        if arg.startswith("-") and flag != "-p":
            flag = arg
            found.setdefault(flag, [])
        elif flag is not None:
            found[flag].append(arg)
            if flag == "-p":
                flag = None
    return found


def main() -> int:
    argv = sys.argv[1:]
    if argv == ["--no-auto-update", "--version"]:
        print(f"GitHub Copilot CLI {(SCENARIO_DIR / 'version').read_text().strip()}.")
        print("Run 'copilot update' to check for updates.")
        return 0
    flags = options(argv)
    prompt = flags["-p"][0]
    scenarios = json.loads((SCENARIO_DIR / "scenario.json").read_text())
    states = Path(os.environ["COPILOT_HOME"]) / "session-state"
    resumed = (flags.get("--resume") or [None])[0]
    spent_before = 0
    # A call it cannot place is recorded, then fails, so a test sees how it was started.
    unplaced = None
    if resumed is not None:
        state_file = states / f"{resumed}.json"
        if state_file.exists():
            state = json.loads(state_file.read_text())
            kind, spent_before = state["kind"], state["nano"]
        else:
            kind, unplaced = None, f"No session {resumed} in {states}"
        scenario = scenarios.get(kind, {}).get("retry", {})
    else:
        kind = next((kind for marker, kind in KINDS if marker in prompt), None)
        unplaced = None if kind else "The prompt names no tool the stand-in knows"
        scenario = scenarios.get(kind, {})
    token = os.environ.get("COPILOT_GITHUB_TOKEN", "")
    status = subprocess.run(
        ["git", "status", "--porcelain=v1", "-uall"], capture_output=True, text=True
    ).stdout
    record = {
        "kind": kind,
        "resumed": resumed is not None,
        "argv": argv,
        "flags": flags,
        "cwd": os.getcwd(),
        "token_sha256": hashlib.sha256(token.encode()).hexdigest() if token else None,
        "gh_token": "GH_TOKEN" in os.environ or "GITHUB_TOKEN" in os.environ,
        "copilot_home": os.environ.get("COPILOT_HOME"),
        "auto_update": os.environ.get("COPILOT_AUTO_UPDATE"),
        "status": status,
        "prompt": prompt,
    }
    with open(SCENARIO_DIR / "calls.jsonl", "a") as calls:
        calls.write(json.dumps(record) + "\n")
    if unplaced:
        print(unplaced, file=sys.stderr)
        return 1

    if scenario.get("leak"):
        log_dir = Path(flags["--log-dir"][0])
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / "process.log").write_text(f"token={token}\n")
    if scenario.get("write"):
        Path(scenario["write"]).write_text("the judge wrote this\n")
    if scenario.get("run"):
        subprocess.run(scenario["run"], shell=True, check=True)

    model = scenario.get("model", flags["--model"][0])
    effort = scenario.get("effort", (flags.get("--reasoning-effort") or [None])[0])
    nano = spent_before + scenario.get("nano", 1_000_000_000)
    session_id = resumed or (flags.get("--session-id") or [None])[0]
    if session_id is not None:
        states.mkdir(parents=True, exist_ok=True)
        (states / f"{session_id}.json").write_text(json.dumps({"kind": kind, "nano": nano}))
    # What Copilot CLI reports beside the effort, which for gemini-4-argon names
    # the vendor openai, though its catalog says Google.
    reported = {"model": model, "reasoning_effort": effort, **scenario.get("checkpoint", {})}
    # Usage comes first, so a session stopped while it sleeps has reported some.
    started = [
        {"type": "model.call_start", "data": {"turnId": "0", "model": model}},
        {
            "type": "session.usage_checkpoint",
            "data": {
                "totalNanoAiu": nano,
                "promptCacheBreakState": [{"conversation": "main", "models": {model: reported}}],
            },
        },
    ]
    for line in started:
        print(json.dumps(line), flush=True)
    time.sleep(scenario.get("sleep", 0))
    finished = [
        {"type": "model.call_final_result", "data": {"model": model, "result": "success"}},
        {"type": "assistant.message", "data": {"content": scenario.get("answer", ""), "model": model}},
        {"type": "result", "exitCode": scenario.get("exit", 0)},
    ]
    for line in finished:
        print(json.dumps(line))
    return scenario.get("exit", 0)


if __name__ == "__main__":
    sys.exit(main())
