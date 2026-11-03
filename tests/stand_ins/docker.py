"""
A stand-in for `docker info`, which records how it was run.

It appends its argv, working directory and environment to the JSON lines file
STAND_IN_LOG names. `info` prints STAND_IN_DOCKER_CAPACITY as the engine's CPUs
and memory. With STAND_IN_DOCKER_INFO_FAIL set, `info` prints that to stderr
and exits 1, as it does when the engine is not running. Any other command,
`pull` among them, exits 1 naming it.
"""

import json
import os
import sys

argv = sys.argv[1:]
with open(os.environ["STAND_IN_LOG"], "a") as log:
    log.write(json.dumps({"program": "docker", "argv": argv, "cwd": os.getcwd(), "env": dict(os.environ)}) + "\n")

if argv[0] == "info":
    if os.environ.get("STAND_IN_DOCKER_INFO_FAIL"):
        print(os.environ["STAND_IN_DOCKER_INFO_FAIL"], file=sys.stderr)
        sys.exit(1)
    print(os.environ["STAND_IN_DOCKER_CAPACITY"])
else:
    sys.exit(f"stand-in docker does not know {argv[0]}")
