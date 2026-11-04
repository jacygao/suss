"""The settings one named arm runs with, read from suss.toml and the .env beside it."""

import tomllib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from harbor.utils.env import get_required_host_vars

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
    # rebench's download, as senior_swe_bench is senior's; each None where suss.toml names none.
    swe_rebench: Path | None
    environment: str
    k: int
    concurrency: int
    arm: Arm
    env: dict[str, str]
    senior_swe_bench: Path | None = None

    def download(self, lane: str) -> Path:
        """The download `lane` is built from, which `load` requires for its lane."""
        found = getattr(self, DOWNLOADS[lane])
        if found is None:
            raise ConfigError(f"{self.path}: no {DOWNLOADS[lane]!r}")
        return found


# The key that names each lane's download.
DOWNLOADS = {lanes.REBENCH: "swe_rebench", lanes.SENIOR: "senior_swe_bench"}
REQUIRED = ("environment", "k", "concurrency")
# Ignored, they would leave a run going on what its author believes they still set.
RETIRED = ("swe_bench_pro", "dataset")
REQUIRED_IN_ARM = ("harness",)
LANE_KEYS = ("max_seconds",)


def load(path: Path, arm: str, lane: str) -> Config:
    """
    Read the settings `arm` runs with on `lane` from the suss.toml at `path`.

    Paths in the file resolve from the directory holding it, whatever the
    current directory is. `max_seconds` is optional, and so is the download
    of each lane but `lane`. Raises ConfigError, naming the file, when it is
    missing or is not TOML, when it sets `swe_bench_pro` or `dataset`, when a
    required key is missing, when `k` is
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
    required = (DOWNLOADS[lane],) if lane in DOWNLOADS else ()
    for key in (*required, *REQUIRED):
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
        swe_rebench=_path(directory, data.get("swe_rebench")),
        senior_swe_bench=_path(directory, data.get("senior_swe_bench")),
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


def _path(directory: Path, value: str | None) -> Path | None:
    return None if value is None else (directory / value).resolve()


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


# The task.toml tables whose `${NAME}` values Harbor fills from its own environment
# in a job suss runs. It reads [solution.env] only for its oracle agent.
HOST_ENV_TABLES = ("environment", "verifier")


def host_variables(tasks: Mapping[Path, dict]) -> tuple[str, ...]:
    """
    The names the tasks' `[environment.env]` and `[verifier.env]` values read
    from Harbor's environment, as `${NAME}` or `${NAME:-default}`, sorted.

    `tasks` are as `lanes.read_tasks` gives them. A literal value reads nothing.
    """
    names = set()
    for toml in tasks.values():
        for table in HOST_ENV_TABLES:
            section = toml.get(table)
            env = section.get("env") if isinstance(section, dict) else None
            if isinstance(env, dict):
                values = {key: value for key, value in env.items() if isinstance(value, str)}
                names |= {name for name, _ in get_required_host_vars(values)}
    return tuple(sorted(names))


def verifier_env(settings: Config, variables: Iterable[str]) -> dict[str, str]:
    """
    The value .env gives each of `variables`, for the regrade's Harbor.

    A variable .env leaves empty is left out, so the task's own default applies,
    as it does for one .env does not set.
    """
    return {name: settings.env[name] for name in variables if settings.env.get(name)}


# Read by Senior SWE-Bench's verifier alone, which grades with LLM judges.
JUDGE_SETTING = "SSB_OVERRIDE_ALL_JUDGE_MODEL"
# Each model the verifier calls, by the settings that choose it, the first set
# one winning, and its default.
GRADING_MODELS = (
    ((JUDGE_SETTING,), "anthropic/claude-sonnet-4-6"),
    (("SSB_OVERRIDE_CLASSIFIER_MODEL",), "anthropic/claude-haiku-4-5-20251001"),
    (("SSB_OVERRIDE_VA_MODEL", "FORCE_VA_MODEL"), "anthropic/claude-sonnet-4-6"),
)
# The verifier sends every call through Portkey when this is set, whatever the model.
ROUTER_KEY = "PORTKEY_API_KEY"
PROVIDER_KEYS = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}


def check_grading_keys(settings: Config, lane: str) -> None:
    """
    Refuse a .env that lacks a key the senior lane's verifier needs to call
    its models. Every other lane passes.

    The verifier needs `ROUTER_KEY`, or else the key of the provider each of
    its models names, as anthropic/ or openai/. Without it the judges cannot run, and the
    verifier still writes a reward. An empty value counts as unset. Raises
    ConfigError naming the missing keys, or a model setting whose provider has
    no key in `PROVIDER_KEYS`.
    """
    if lane != lanes.SENIOR or settings.env.get(ROUTER_KEY):
        return
    dotenv = settings.path.parent / ".env"
    needed: dict[str, list[str]] = {}
    for names, default in GRADING_MODELS:
        setting = next((name for name in names if settings.env.get(name)), None)
        model = settings.env[setting] if setting else default
        key = PROVIDER_KEYS.get(model.partition("/")[0]) if "/" in model else None
        if key is None:
            raise ConfigError(
                f"{dotenv} sets {setting} to {model}, but the verifier of the tasks in {lane} "
                f"is given a key only for {' and '.join(f'{p}/' for p in PROVIDER_KEYS)} "
                f"models; name one of those, or set {ROUTER_KEY}"
            )
        if not settings.env.get(key) and model not in needed.setdefault(key, []):
            needed[key].append(model)
    if needed:
        models = [model for found in needed.values() for model in found]
        them = "it" if len(needed) == 1 else "them"
        raise ConfigError(
            f"{dotenv} sets no {' or '.join(sorted(needed))}, which the verifier of the "
            f"tasks in {lane} needs for {_and(models)}, or its judges cannot run; set {them}, "
            f"or set {ROUTER_KEY} to reach every model through Portkey"
        )


def _and(items: list[str]) -> str:
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} and {items[-1]}"


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
