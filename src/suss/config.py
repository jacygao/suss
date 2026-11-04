"""The settings one named arm runs with, read from suss.toml and the .env beside it."""

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from suss import lanes


class ConfigError(Exception):
    """suss.toml or its .env cannot give the settings asked for."""


@dataclass(frozen=True)
class Arm:
    name: str
    harness: Path
    baseline: str | None
    # Passed to the harness as it stands; suss reads none of it.
    params: dict[str, Any]
    # The arm's own, else its lane's, else the top's; None where none sets one.
    max_seconds: float | None


@dataclass(frozen=True)
class Config:
    path: Path
    swe_rebench: Path
    environment: str
    k: int
    concurrency: int
    arm: Arm
    env: dict[str, str]


REQUIRED = ("swe_rebench", "environment", "k", "concurrency")
# Ignored, they would leave a run going on what its author believes they still set.
RETIRED = ("swe_bench_pro", "dataset")
REQUIRED_IN_ARM = ("harness",)
LANE_KEYS = ("max_seconds",)


def load(path: Path, arm: str, lane: str) -> Config:
    """
    Read the settings `arm` runs with on `lane` from the suss.toml at `path`.

    Paths in the file resolve from the directory holding it, whatever the
    current directory is. `max_seconds` is optional. Raises ConfigError,
    naming the file, when it is missing or is not TOML, when it sets
    `swe_bench_pro` or `dataset`, when a required key is missing, when `k` is
    not a whole number of at least 1, when the file does not define `arm`, when
    the top or `arm` sets `max_cost_usd`, when a `[lanes.<lane>]` table names a
    lane suss does not have or sets a key other than `max_seconds`, or when a
    .env line is not NAME=VALUE.
    """
    path, data = _read(path)
    directory = path.parent
    retired = [key for key in RETIRED if key in data]
    if retired:
        raise ConfigError(
            f"{path}: sets {' and '.join(retired)}, which suss no longer reads; name the "
            "directory of downloaded SWE-rebench tasks with swe_rebench"
        )
    for key in REQUIRED:
        if key not in data:
            raise ConfigError(f"{path}: no {key!r}")
    # bool is an int in Python, and true is not a number of jobs.
    if not isinstance(data["k"], int) or isinstance(data["k"], bool) or data["k"] < 1:
        raise ConfigError(f"{path}: 'k' must be a whole number of at least 1, not {data['k']!r}")
    arms = data.get("arms", {})
    if arm not in arms:
        raise ConfigError(f"arm {arm} is not defined in {path}")
    table = arms[arm]
    for key in REQUIRED_IN_ARM:
        if key not in table:
            raise ConfigError(f"{path}: [arms.{arm}] has no {key!r}")
    # Ignored, it would leave a run its author believes is capped running uncapped.
    for where, keys in (
        ("max_cost_usd is set at the top", data),
        (f"[arms.{arm}] sets max_cost_usd", table),
    ):
        if "max_cost_usd" in keys:
            raise ConfigError(
                f"{path}: {where}, but suss does not read it; set the arm's spending limit in "
                "its params, under the name its adapter takes, such as max_budget_usd for "
                "adapters/claude-code"
            )
    lane_tables = _lanes(path, data)
    return Config(
        path=path,
        swe_rebench=(directory / data["swe_rebench"]).resolve(),
        environment=data["environment"],
        k=data["k"],
        concurrency=data["concurrency"],
        arm=Arm(
            name=arm,
            harness=(directory / table["harness"]).resolve(),
            baseline=table.get("baseline"),
            params=table.get("params", {}),
            max_seconds=_cap("max_seconds", table, lane_tables.get(lane, {}), data),
        ),
        env=_read_env(directory / ".env"),
    )


def baselines(path: Path, arms: list[str]) -> dict[str, str | None]:
    """
    Each of `arms`' baseline from the suss.toml at `path`, None for an arm that
    names none.

    Nothing else in the file is read, so a file `load` refuses for a run still
    gives them. Raises ConfigError, naming the file, when it is missing or is
    not TOML, or when it does not define one of the arms.
    """
    path, data = _read(path)
    defined = data.get("arms", {})
    found = {}
    for arm in arms:
        if arm not in defined:
            raise ConfigError(f"arm {arm} is not defined in {path}")
        found[arm] = defined[arm].get("baseline")
    return found


def _read(path: Path) -> tuple[Path, dict[str, Any]]:
    """The file's resolved path and its TOML, raising ConfigError when it has none."""
    path = Path(path).resolve()
    try:
        with path.open("rb") as file:
            return path, tomllib.load(file)
    except FileNotFoundError:
        raise ConfigError(f"{path}: no such file") from None
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as error:
        raise ConfigError(f"{path}: not valid TOML: {error}") from None


def _lanes(path: Path, data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """
    The file's `[lanes.<lane>]` tables, keyed by lane.

    Raises ConfigError, naming the file, for a lane suss does not have, a key
    other than `max_seconds` in a lane's table, or lanes that are not tables.
    """
    found = data.get("lanes", {})
    if not isinstance(found, dict):
        raise ConfigError(f"{path}: lanes must be a table of [lanes.<lane>] tables")
    for name, table in found.items():
        if name not in lanes.LANES:
            raise ConfigError(
                f"{path}: sets [lanes.{name}], but suss has no lane named {name}; "
                f"its lanes are {', '.join(lanes.LANES)}"
            )
        if not isinstance(table, dict):
            raise ConfigError(f"{path}: lanes.{name} must be a table, as [lanes.{name}]")
        for key in table:
            if key not in LANE_KEYS:
                raise ConfigError(
                    f"{path}: [lanes.{name}] sets {key}, but suss reads only "
                    f"{' and '.join(LANE_KEYS)} there"
                )
    return found


def _cap(key: str, *tables: dict[str, Any]) -> Any:
    """`key` from the first of `tables` that sets it, or None where none does."""
    for table in tables:
        # Membership, not truthiness, so that a 0 still overrides what comes after.
        if key in table:
            return table[key]
    return None


def _read_env(path: Path) -> dict[str, str]:
    """
    Read NAME=VALUE lines, skipping blank ones. A missing file is empty.

    The value is everything after the first `=`, taken as it stands: no
    quotes are removed and no `export` is recognised.
    """
    if not path.exists():
        return {}
    env = {}
    for number, line in enumerate(path.read_text().splitlines(), start=1):
        if not line.strip():
            continue
        name, separator, value = line.partition("=")
        if not separator:
            raise ConfigError(f"{path}, line {number}: expected NAME=VALUE")
        env[name] = value
    return env
