"""
A stand-in for `docker info` and `docker pull`, which records how it was run.

It appends its argv, working directory and environment to the JSON lines file
STAND_IN_LOG names. `info` prints STAND_IN_DOCKER_ARCH as the engine's
architecture. `pull` exits 1 for the image STAND_IN_DOCKER_FAIL names, and 0
for any other.
"""

import json
import os
import sys

argv = sys.argv[1:]
with open(os.environ["STAND_IN_LOG"], "a") as log:
    log.write(json.dumps({"program": "docker", "argv": argv, "cwd": os.getcwd(), "env": dict(os.environ)}) + "\n")

if argv[0] == "info":
    print(os.environ["STAND_IN_DOCKER_ARCH"])
elif argv[0] == "pull":
    if argv[-1] == os.environ.get("STAND_IN_DOCKER_FAIL"):
        print(f"stand-in docker cannot pull {argv[-1]}", file=sys.stderr)
        sys.exit(1)
    print(f"stand-in docker pulled {argv[-1]}")
else:
    sys.exit(f"stand-in docker does not know {argv[0]}")
