"""
Run `swe_drill.validate` on senior's own run_validate.py, as a design task's
test.sh runs it, with the container paths run_validate.py fixes moved under
one directory of this machine.

    run_senior_validation.py <senior's tests/> <root>

`<root>` holds `repo/`, `validate/validation_spec.toml`, `logs/` and
`base_ref`, which stand for /repo/<REPO_NAME>, /tests/validate,
/logs/verifier and /var/lib/devcontainer_base_ref. Senior's drivers write
each story's parameters read-only, and write them again for each run of the
story, which root may and a user may not, so each is removed before it is
written again. Nothing else of senior's code changes. It exits as
run_validate.py would.
"""

import dataclasses
import sys
from pathlib import Path

from swe_drill import validate

tests, root = Path(sys.argv[1]), Path(sys.argv[2])
rv = validate.load(tests)
rv.REPO_DIR = root / "repo"
rv.TESTS_DIR = tests
rv.VALIDATE_DIR = root / "validate"
rv.VALIDATION_SPEC = rv.VALIDATE_DIR / "validation_spec.toml"
rv.LOGS_DIR = root / "logs"
rv.SCRIPTS_DIR = rv.LOGS_DIR / "validation_scripts"
rv.AGENT_PATCH = rv.LOGS_DIR / "agent.patch"
base_ref = (root / "base_ref").read_text().strip()
read_spec = rv.ValidationConfig.from_spec.__func__
rv.ValidationConfig.from_spec = classmethod(
    lambda cls, spec: dataclasses.replace(read_spec(cls, spec), base_ref=base_ref)
)
Driver = sys.modules["ssb_lib.drivers.base"].Driver
write_params = Driver.write_params_file


def rewrite_params(self, test_cases, logs_dir, story_id):
    (Path(logs_dir) / "validation_params" / f"{story_id}.json").unlink(missing_ok=True)
    return write_params(self, test_cases, logs_dir, story_id)


Driver.write_params_file = rewrite_params
sys.exit(validate.run(rv))
