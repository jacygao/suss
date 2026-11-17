"""The settings one named arm runs with, read from swe-drill.toml and the .env beside it."""

import tomllib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from harbor.utils.env import get_required_host_vars

from swe_drill import lanes


class ConfigError(Exception):
    """swe-drill.toml or its .env cannot give the settings asked for."""


@dataclass(frozen=True)
class Arm:
    name: str
    # The import path of the Harbor agent class the arm runs on.
    agent: str
    # Harbor's -m; None where the arm sets none.
    model: str | None
    # Each is passed to the agent class as an --ak.
    kwargs: dict[str, Any]


@dataclass(frozen=True)
class Config:
    path: Path
    # rebench's download, as swe_atlas_rf is atlas-rf's; each None where swe-drill.toml names none.
    swe_rebench: Path | None
    environment: str
    k: int
    concurrency: int
    arm: Arm
    env: dict[str, str]
    swe_atlas_rf: Path | None = None
    # The model atlas-rf's rubric judge calls, on an atlas-rf run; None where swe-drill.toml sets none.
    eval_model: str | None = None

    def download(self, lane: str) -> Path:
        """The download `lane` is built from, which `load` requires for its lane."""
        found = getattr(self, DOWNLOADS[lane])
        if found is None:
            raise ConfigError(f"{self.path}: no {DOWNLOADS[lane]!r}")
        return found


# The key that names each lane's download.
DOWNLOADS = {
    lanes.REBENCH: "swe_rebench",
    lanes.ATLAS_RF: "swe_atlas_rf",
}
REQUIRED = ("environment", "k", "concurrency")
# Ignored, they would leave a run going on what its author believes they still set.
RETIRED = ("swe_bench_pro", "dataset")
REQUIRED_IN_ARM = ("agent",)
# Each would leave a run going on what its author believes it still sets.
RETIRED_IN_ARM = ("harness", "params")
# Set by SWE-drill on every agent, so an arm's own kwargs cannot take them.
RESERVED_KWARGS = ("arm", "lane", "drill_config")


def load(path: Path, arm: str, lane: str) -> Config:
    """
    Read the settings `arm` runs with on `lane` from the swe-drill.toml at `path`.

    Paths in the file resolve from the directory holding it, whatever the
    current directory is. The download of each lane but `lane` is optional.
    Raises ConfigError, naming the file, when it is missing or is not TOML,
    when it sets `swe_bench_pro`, `dataset` or `senior_swe_bench`, when a
    required key is missing, when `k` is not a whole number of at least 1,
    when the file does not define `arm`, when the arm sets `harness` or
    `params`, has no `agent`, or has a `model` or `kwargs` it cannot pass to
    Harbor, when the top or `arm` sets `max_cost_usd`, when a `[lanes.<lane>]`
    table names a lane SWE-drill does not have or sets a key other than
    `judge` on atlas-rf, when
    `[lanes.atlas-rf.judge]` is not as `_eval_model` takes it, when any arm sets
    `baseline`, or when a .env line is not NAME=VALUE.
    """
    path, data = _read(path)
    _refuse_baseline(path, data)
    directory = path.parent
    retired = [key for key in RETIRED if key in data]
    if retired:
        raise ConfigError(
            f"{path}: sets {' and '.join(retired)}, which SWE-drill no longer reads; name the "
            "directory of downloaded SWE-rebench tasks with swe_rebench"
        )
    # Ignored, it would leave its author believing SWE-drill still runs the senior lane.
    if "senior_swe_bench" in data:
        raise ConfigError(
            f"{path}: sets senior_swe_bench, which SWE-drill no longer reads, since it has no senior lane;"
            " remove it"
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
    retired = [key for key in RETIRED_IN_ARM if key in table]
    if retired:
        raise ConfigError(
            f"{path}: [arms.{arm}] sets {' and '.join(retired)}, which SWE-drill no longer reads; "
            "name the arm's Harbor agent class with agent, its model with model, and its "
            "settings with kwargs"
        )
    for key in REQUIRED_IN_ARM:
        if key not in table:
            raise ConfigError(f"{path}: [arms.{arm}] has no {key!r}")
    _check_agent(path, arm, table)
    # Ignored, it would leave a run its author believes is capped running uncapped.
    for where, keys in (
        ("max_cost_usd is set at the top", data),
        (f"[arms.{arm}] sets max_cost_usd", table),
    ):
        if "max_cost_usd" in keys:
            raise ConfigError(f"{path}: {where}, but SWE-drill does not read it")
    lane_tables = _lanes(path, data)
    judge = lane_tables.get(lane, {}).get("judge")
    return Config(
        path=path,
        swe_rebench=_path(directory, data.get("swe_rebench")),
        swe_atlas_rf=_path(directory, data.get("swe_atlas_rf")),
        environment=data["environment"],
        k=data["k"],
        concurrency=data["concurrency"],
        arm=Arm(
            name=arm,
            agent=table["agent"],
            model=table.get("model"),
            kwargs=table.get("kwargs", {}),
        ),
        env=_read_env(directory / ".env"),
        eval_model=_eval_model(path, judge) if lane == lanes.ATLAS_RF and judge is not None else None,
    )


def _check_agent(path: Path, arm: str, table: dict[str, Any]) -> None:
    """
    Raise ConfigError, naming the file and the arm, for an `agent` that is not
    an import path, a `model` that is not a string, or `kwargs` that are not a
    table of strings, numbers and booleans, or that take a name SWE-drill sets.
    """
    where = f"{path}: [arms.{arm}]"
    agent = table["agent"]
    if not isinstance(agent, str) or ":" not in agent:
        raise ConfigError(
            f"{where}: agent must name a Harbor agent class as module:Class, not {agent!r}"
        )
    if "model" in table and not isinstance(table["model"], str):
        raise ConfigError(f"{where}: model must be a string, not {table['model']!r}")
    kwargs = table.get("kwargs", {})
    if not isinstance(kwargs, dict):
        raise ConfigError(f"{where}: kwargs must be a table, as [arms.{arm}.kwargs]")
    for key, value in kwargs.items():
        if key in RESERVED_KWARGS:
            raise ConfigError(f"{where}: kwargs sets {key}, which SWE-drill sets on every agent")
        if not isinstance(value, (str, int, float, bool)):
            raise ConfigError(
                f"{where}: kwargs {key} must be a string, a number or a boolean, which Harbor "
                f"takes as an --ak, not {value!r}"
            )


def _path(directory: Path, value: str | None) -> Path | None:
    return None if value is None else (directory / value).resolve()


def check_arms(path: Path) -> None:
    """
    Raise ConfigError, naming the swe-drill.toml at `path`, when any arm sets
    `baseline`, or when the file is missing or is not TOML.

    Nothing else in the file is read, so a file `load` refuses for a run still
    passes.
    """
    _refuse_baseline(*_read(path))


def _refuse_baseline(path: Path, data: dict[str, Any]) -> None:
    """Raise ConfigError naming the first arm that sets `baseline`."""
    arms = data.get("arms", {})
    for arm, table in arms.items() if isinstance(arms, dict) else ():
        # Ignored, it would leave its author believing the arm is still measured against one.
        if isinstance(table, dict) and "baseline" in table:
            raise ConfigError(
                f"{path}: [arms.{arm}] sets baseline, which SWE-drill no longer reads; "
                "swe-drill leaderboard ranks every arm on its lane"
            )


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

    Raises ConfigError, naming the file, for a lane SWE-drill does not have, a key
    other than `judge` on atlas-rf, a judge `_eval_model` refuses, or lanes
    that are not tables.
    """
    found = data.get("lanes", {})
    if not isinstance(found, dict):
        raise ConfigError(f"{path}: lanes must be a table of [lanes.<lane>] tables")
    for name, table in found.items():
        if name not in lanes.LANES:
            raise ConfigError(
                f"{path}: sets [lanes.{name}], but SWE-drill has no lane named {name}; "
                f"its lanes are {', '.join(lanes.LANES)}"
            )
        if not isinstance(table, dict):
            raise ConfigError(f"{path}: lanes.{name} must be a table, as [lanes.{name}]")
        for key in table:
            if key != "judge" or name != lanes.ATLAS_RF:
                raise ConfigError(
                    f"{path}: [lanes.{name}] sets {key}, but SWE-drill reads only [lanes.{lanes.ATLAS_RF}.judge]"
                )
        if "judge" in table:
            _eval_model(path, table["judge"])
    return found


def _eval_model(path: Path, table: Any) -> str | None:
    """
    The model `[lanes.atlas-rf.judge]` names, or None where it names none.

    Raises ConfigError, naming the file, when it is not a table, sets another
    key, or sets a model that is not a non-empty string.
    """
    where = f"{path}: [lanes.{lanes.ATLAS_RF}.judge]"
    if not isinstance(table, dict):
        raise ConfigError(f"{path}: lanes.{lanes.ATLAS_RF}.judge must be a table, as [lanes.{lanes.ATLAS_RF}.judge]")
    for key in table:
        if key != "model":
            raise ConfigError(f"{where} sets {key}, but SWE-drill reads only model there")
    model = table.get("model")
    if model is not None and (not isinstance(model, str) or not model):
        raise ConfigError(f"{where} sets model to {model!r}, which must be a non-empty string")
    return model


# The task.toml tables whose `${NAME}` values Harbor fills from its own environment
# in a job SWE-drill runs. It reads [solution.env] only for its oracle agent.
HOST_ENV_TABLES = ("environment", "verifier")


def host_variables(
    tasks: Mapping[Path, dict], tables: Iterable[str] = HOST_ENV_TABLES
) -> tuple[str, ...]:
    """
    The names the tasks' `[<table>.env]` values read from Harbor's environment,
    as `${NAME}` or `${NAME:-default}`, sorted, for each of `tables`.

    `tasks` are as `lanes.read_tasks` gives them. A literal value reads nothing.
    """
    names = set()
    for toml in tasks.values():
        for table in tables:
            section = toml.get(table)
            env = section.get("env") if isinstance(section, dict) else None
            if isinstance(env, dict):
                values = {key: value for key, value in env.items() if isinstance(value, str)}
                names |= {name for name, _ in get_required_host_vars(values)}
    return tuple(sorted(names))


def verifier_env(settings: Config, variables: Iterable[str]) -> dict[str, str]:
    """
    The value .env gives each of `variables`, for the agent job's Harbor.

    A variable .env leaves empty is left out, so the task's own default applies,
    as it does for one .env does not set.
    """
    return {name: settings.env[name] for name in variables if settings.env.get(name)}


TOKEN_NAME = "COPILOT_GITHUB_TOKEN"


def check_judge(settings: Config, lane: str) -> None:
    """
    Refuse an atlas-rf run with no model in `[lanes.atlas-rf.judge]`, or no
    COPILOT_GITHUB_TOKEN in .env. Every other lane passes.

    An empty token counts as unset. Raises ConfigError naming what is missing.
    """
    if lane != lanes.ATLAS_RF:
        return
    if settings.eval_model is None:
        raise ConfigError(
            f"{settings.path}: no model in [lanes.{lane}.judge], which names the model {lane}'s rubric"
            " judge calls through Copilot, such as gemini-4-argon"
        )
    if not settings.env.get(TOKEN_NAME):
        raise ConfigError(
            f"{settings.path.parent / '.env'} sets no {TOKEN_NAME}, which SWE-drill judges the trials"
            f" of {lane} with"
        )


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
