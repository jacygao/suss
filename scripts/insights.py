"""
Compute each leaderboard arm's insights from a results mirror, for the site.

    uv run python scripts/insights.py <mirror>

Reads the jobs `site/leaderboard.json` lists for each arm, in a mirror that
`scripts/results.sh down` made, and writes `site/insights.json`. Only the jobs
the leaderboard ranks are read, so a run it leaves out never shapes an insight.
"""

import json
import re
import statistics
import sys
from pathlib import Path

from swe_drill import leaderboard

SITE = Path(__file__).resolve().parent.parent / "site"
TOP = 3


def short(task: str) -> str:
    """A task's name as a reader scans it: SWE-bench Pro's commit hashes cut to seven characters."""
    name = task.removeprefix("instance_")
    return re.sub(r"-([0-9a-f]{12,})(-v[0-9a-f]+|-vnan)?$", lambda m: "-" + m.group(1)[:7], name)


def load(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def trial_facts(trial: leaderboard.Trial) -> dict:
    """What the agent's record and Harbor's result say about one trial, beyond whether it resolved."""
    record = load(trial.path / "agent" / "suss.json")
    result = load(trial.path / "result.json")
    agent = result.get("agent_result") or {}
    usage = record.get("usage") or {}
    tokens = None
    if agent.get("n_input_tokens") is not None:
        tokens = agent["n_input_tokens"] + (agent.get("n_output_tokens") or 0)
    raised = (result.get("exception_info") or {}).get("exception_type")
    return {
        # Harbor raises its timeout where the agent's record could not say timed-out.
        "timedOut": record.get("status") == "timed-out" or raised == "AgentTimeoutError",
        # A trial with no recorded time is unmeasured, never zero minutes.
        "minutes": record["run_seconds"] / 60 if isinstance(record.get("run_seconds"), int | float) else None,
        "tokens": tokens,
        "credits": usage.get("ai_credits"),
        "models": tuple(sorted(usage.get("reported_models") or [])),
    }


def why(trial: leaderboard.Trial, facts: dict) -> str:
    """The first reason an unresolved trial failed, in the order a reader would check them."""
    if facts["timedOut"]:
        return "Timed out"
    if trial.patch_bytes == 0:
        return "No change made"
    if trial.verifier_applied is False or trial.verifier_refused:
        return "Patch did not apply"
    # SWE-bench Pro's parser writes its tests to output.json, which the leaderboard reads only for rebench.
    pro_tests = load(trial.path / "verifier" / "output.json").get("tests")
    if not trial.verifier_tested and not pro_tests:
        return "No test result"
    return "Tests failed"


def valid_trials(mirror: Path, lane: str, arm: dict) -> list[leaderboard.Trial]:
    """
    The arm's trials in the jobs the leaderboard lists. An invalid trial counts
    nowhere, as if it had not run, as the leaderboard counts it.
    """
    jobs = {run["job"] for run in arm["runs"]}
    return [t for t in leaderboard.read_trials(mirror, arm["name"], lane) if t.job in jobs and not t.invalid]


def arm_insights(mirror: Path, lane: str, arm: dict, everyone: dict[str, dict[str, list[bool]]]) -> dict:
    jobs = [run["job"] for run in arm["runs"]]
    trials = valid_trials(mirror, lane, arm)
    facts = {(t.job, t.task): trial_facts(t) for t in trials}

    runs = []
    for job in jobs:
        mine = [t for t in trials if t.job == job]
        credits = [facts[(t.job, t.task)]["credits"] or 0 for t in mine]
        runs.append({
            "job": job,
            "resolved": sum(t.resolved for t in mine),
            "trials": len(mine),
            "timedOut": sum(facts[(t.job, t.task)]["timedOut"] for t in mine),
            "credits": round(sum(credits)),
        })

    by_task = everyone[arm["name"]]
    always = sorted(t for t, r in by_task.items() if all(r))
    sometimes = sorted(t for t, r in by_task.items() if any(r) and not all(r))
    never = sorted(t for t, r in by_task.items() if not any(r))
    others = {name: tasks for name, tasks in everyone.items() if name != arm["name"]}
    only = sorted(t for t in by_task if any(by_task[t]) and not any(any(o.get(t, [])) for o in others.values()))
    # A task every other arm resolved at least once, which this arm never did.
    missed = sorted(t for t in never if others and all(any(o.get(t, [])) for o in others.values()))

    failures: dict[str, int] = {}
    for t in trials:
        if not t.resolved:
            reason = why(t, facts[(t.job, t.task)])
            failures[reason] = failures.get(reason, 0) + 1

    def heaviest(key: str) -> list[dict]:
        """The tasks whose heaviest trial took the most, each task once."""
        most: dict[str, float] = {}
        for (job, task), f in facts.items():
            if f[key] is not None:
                most[task] = max(most.get(task, f[key]), f[key])
        ranked = sorted(most.items(), key=lambda kv: -kv[1])[:TOP]
        return [{"task": short(task), "value": value} for task, value in ranked]

    tokens = [f["tokens"] for f in facts.values() if f["tokens"] is not None]
    minutes = [f["minutes"] for f in facts.values() if f["minutes"] is not None]
    models: dict[str, int] = {}
    for f in facts.values():
        if f["models"]:
            label = " + ".join(f["models"])
            models[label] = models.get(label, 0) + 1

    def names(tasks: list[str]) -> list[str]:
        return [short(t) for t in tasks]

    return {
        "runs": runs,
        "tasks": {"always": len(always), "sometimes": names(sometimes), "never": names(never)},
        "only": names(only),
        "missed": names(missed),
        "failures": dict(sorted(failures.items(), key=lambda kv: -kv[1])),
        "tokens": {"median": round(statistics.median(tokens)) if tokens else None, "top": heaviest("tokens")},
        "minutes": {"median": round(statistics.median(minutes), 1) if minutes else None, "top": [
            {"task": h["task"], "value": round(h["value"], 1)} for h in heaviest("minutes")]},
        # Only a real mix says something: an arm on one model, with a stray call to another, is named in the table.
        "models": dict(sorted(models.items(), key=lambda kv: -kv[1]))
        if models and max(models.values()) < 0.9 * sum(models.values()) else None,
    }


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("usage: uv run python scripts/insights.py <mirror>")
    mirror = Path(sys.argv[1])
    board = json.loads((SITE / "leaderboard.json").read_text())
    out = {}
    for lane, entry in board.items():
        if not isinstance(entry, dict) or "arms" not in entry:
            continue
        everyone = {}
        for arm in entry["arms"]:
            jobs = {run["job"] for run in arm["runs"]}
            missing = [job for job in jobs if not (mirror / "results" / lane / arm["name"] / job).is_dir()]
            if missing:
                sys.exit(f"{mirror}: {lane}/{arm['name']} lacks {', '.join(sorted(missing))}, which the leaderboard lists; mirror it first")
            trials = valid_trials(mirror, lane, arm)
            by_task: dict[str, list[bool]] = {}
            for t in trials:
                by_task.setdefault(t.task, []).append(t.resolved)
            everyone[arm["name"]] = by_task
        out[lane] = {arm["name"]: arm_insights(mirror, lane, arm, everyone) for arm in entry["arms"]}
    (SITE / "insights.json").write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n")
    print(f"wrote {SITE / 'insights.json'}")


if __name__ == "__main__":
    main()
