"""
A stand-in for `harbor run`, which records how it was run and leaves a job
directory as Harbor does.

It appends its argv, working directory and environment to the JSON lines file
STAND_IN_LOG names. STAND_IN_HARBOR_PLAN may name a JSON file mapping a job
name to what that job does, with these keys, each optional:

- `exit`: the exit code, 0 by default.
- `job_dir`: whether it makes the job directory, true by default.
- `trial`: whether it makes a trial directory in it, true by default.
- `sources`: whether it makes the `.sources` directory Harbor keeps for a
  job that regrades trials from its hub, false by default.
"""

import json
import os
import sys
from pathlib import Path


def value(argv: list[str], option: str) -> str:
    return argv[argv.index(option) + 1]


argv = sys.argv[1:]
with open(os.environ["STAND_IN_LOG"], "a") as log:
    log.write(json.dumps({"program": "harbor", "argv": argv, "cwd": os.getcwd(), "env": dict(os.environ)}) + "\n")

job_name = value(argv, "--job-name")
plan = {}
plan_file = Path(os.environ.get("STAND_IN_HARBOR_PLAN", "/nonexistent"))
if plan_file.exists():
    plan = json.loads(plan_file.read_text()).get(job_name, {})

print(f"stand-in harbor ran {job_name}", flush=True)
print(f"stand-in harbor complained about {job_name}", file=sys.stderr, flush=True)

job_dir = Path(value(argv, "--jobs-dir")) / job_name
if plan.get("job_dir", True):
    job_dir.mkdir(parents=True)
    (job_dir / "config.json").write_text("{}")
    (job_dir / "job.log").write_text("")
    if plan.get("sources", False):
        (job_dir / ".sources").mkdir()
    if plan.get("trial", True):
        trial = job_dir / "instance_flipt-io__flipt-02e2163__AivQxJ7"
        trial.mkdir()
        (trial / "result.json").write_text("{}")
sys.exit(plan.get("exit", 0))
