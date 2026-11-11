"""The settings one named arm runs with, read from suss.toml and the .env beside it."""

import math
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
    # The import path of the Harbor agent class the arm runs on.
    agent: str
    # Harbor's -m; None where the arm sets none.
    model: str | None
    # Each is passed to the agent class as an --ak.
    kwargs: dict[str, Any]


@dataclass(frozen=True)
class Judge:
    """What JudgeVerifier judges a senior run with, from `[lanes.senior.judge]`."""

    credit_budget: float
    # None takes JudgeVerifier's own default.
    model: str | None = None
    effort: str | None = None


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
    # The lane's judge, which only senior has; None where suss.toml sets none.
    judge: Judge | None = None

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
REQUIRED_IN_ARM = ("agent",)
# Each would leave a run going on what its author believes it still sets.
RETIRED_IN_ARM = ("harness", "params")
# Set by suss on every agent, so an arm's own kwargs cannot take them.
RESERVED_KWARGS = ("arm", "lane", "suss_config")
JUDGE_KEYS = ("credit_budget", "model", "effort")


def load(path: Path, arm: str, lane: str) -> Config:
    """
    Read the settings `arm` runs with on `lane` from the suss.toml at `path`.

    Paths in the file resolve from the directory holding it, whatever the
    current directory is. The download of each lane but `lane` is optional.
    Raises ConfigError, naming the file, when it is missing or is not TOML,
    when it sets `swe_bench_pro` or `dataset`, when a required key is missing,
    when `k` is not a whole number of at least 1, when the file does not
    define `arm`, when the arm sets `harness` or `params`, has no `agent`, or
    has a `model` or `kwargs` it cannot pass to Harbor, when the top or `arm`
    sets `max_cost_usd`, when a `[lanes.<lane>]` table names a lane suss does
    not have or sets a key other than `judge` on senior, when
    `[lanes.senior.judge]` is not as `_judge` takes it, when any arm sets
    `baseline`, or when a .env line is not NAME=VALUE.
    """
    path, data = _read(path)
    _refuse_baseline(path, data)
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
    retired = [key for key in RETIRED_IN_ARM if key in table]
    if retired:
        raise ConfigError(
            f"{path}: [arms.{arm}] sets {' and '.join(retired)}, which suss no longer reads; "
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
            raise ConfigError(f"{path}: {where}, but suss does not read it")
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
            agent=table["agent"],
            model=table.get("model"),
            kwargs=table.get("kwargs", {}),
        ),
        env=_read_env(directory / ".env"),
        judge=_judge(path, judge) if (judge := lane_tables.get(lane, {}).get("judge")) is not None else None,
    )


def _check_agent(path: Path, arm: str, table: dict[str, Any]) -> None:
    """
    Raise ConfigError, naming the file and the arm, for an `agent` that is not
    an import path, a `model` that is not a string, or `kwargs` that are not a
    table of strings, numbers and booleans, or that take a name suss sets.
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
            raise ConfigError(f"{where}: kwargs sets {key}, which suss sets on every agent")
        if not isinstance(value, (str, int, float, bool)):
            raise ConfigError(
                f"{where}: kwargs {key} must be a string, a number or a boolean, which Harbor "
                f"takes as an --ak, not {value!r}"
            )


def _path(directory: Path, value: str | None) -> Path | None:
    return None if value is None else (directory / value).resolve()


def check_arms(path: Path) -> None:
    """
    Raise ConfigError, naming the suss.toml at `path`, when any arm sets
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
                f"{path}: [arms.{arm}] sets baseline, which suss no longer reads; "
                "suss leaderboard ranks every arm on its lane"
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

    Raises ConfigError, naming the file, for a lane suss does not have, a key
    other than `judge` on senior in a lane's table, a judge `_judge` refuses,
    or lanes that are not tables.
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
            if key != "judge" or name != lanes.SENIOR:
                raise ConfigError(
                    f"{path}: [lanes.{name}] sets {key}, but suss reads only [lanes.{lanes.SENIOR}.judge]"
                )
        if "judge" in table:
            _judge(path, table["judge"])
    return found


def _judge(path: Path, table: Any) -> Judge:
    """
    The judge `[lanes.senior.judge]` sets.

    Raises ConfigError, naming the file, when it is not a table, sets another
    key, has no positive `credit_budget`, or sets `model` or `effort` to
    something other than a string.
    """
    where = f"{path}: [lanes.{lanes.SENIOR}.judge]"
    if not isinstance(table, dict):
        raise ConfigError(f"{path}: lanes.{lanes.SENIOR}.judge must be a table, as [lanes.{lanes.SENIOR}.judge]")
    for key in table:
        if key not in JUDGE_KEYS:
            raise ConfigError(f"{where} sets {key}, but suss reads only {_and(list(JUDGE_KEYS))} there")
    budget = table.get("credit_budget")
    # TOML reads nan and inf as floats, which Harbor would not parse back as numbers.
    if isinstance(budget, bool) or not isinstance(budget, int | float) or not 0 < budget < math.inf:
        raise ConfigError(
            f"{where} must set credit_budget, the AI credits every judge session of a job may"
            f" spend together, to a positive finite number, not {budget!r}"
        )
    for key in ("model", "effort"):
        if key in table and not isinstance(table[key], str):
            raise ConfigError(f"{where} sets {key} to {table[key]!r}, which must be a string")
    return Judge(budget, table.get("model"), table.get("effort"))


# The task.toml tables whose `${NAME}` values Harbor fills from its own environment
# in a job suss runs. It reads [solution.env] only for its oracle agent.
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


# Read by Senior SWE-Bench's verifier alone, which grades with LLM judges.
JUDGE_SETTING = "SSB_OVERRIDE_ALL_JUDGE_MODEL"
# Each model the verifier calls, by its name in a senior trial's grader: the
# settings that choose it, the first set one winning, and its default.
GRADING_MODELS = {
    "judge_model": ((JUDGE_SETTING,), "anthropic/claude-sonnet-4-6"),
    "classifier_model": (("SSB_OVERRIDE_CLASSIFIER_MODEL",), "anthropic/claude-haiku-4-5-20251001"),
    "validation_model": (("SSB_OVERRIDE_VA_MODEL", "FORCE_VA_MODEL"), "anthropic/claude-sonnet-4-6"),
}
# The validation agent's harness, chosen as a model is. It calls no provider of its own.
VALIDATION_HARNESS = (("SSB_OVERRIDE_VA_HARNESS", "FORCE_VA_HARNESS"), "miniswe")


def _chosen(env: Mapping[str, str], names: tuple[str, ...], default: str) -> tuple[str | None, str]:
    """The first of `names` that `env` sets non-empty, and its value, else None and `default`."""
    setting = next((name for name in names if env.get(name)), None)
    return setting, env[setting] if setting else default


def grading_choices(env: Mapping[str, str]) -> dict[str, str]:
    """
    What the senior verifier, given `env`, grades with: each of GRADING_MODELS,
    and the validation agent's harness as `validation_harness`.

    `env` is what the agent job's Harbor holds, as `verifier_env` gives it.
    """
    choices = {**GRADING_MODELS, "validation_harness": VALIDATION_HARNESS}
    order = ("judge_model", "classifier_model", "validation_harness", "validation_model")
    return {name: _chosen(env, *choices[name])[1] for name in order}


# The keys senior's own judges and validation agent call their models with.
# Suss judges senior itself, on a Copilot subscription, so no job is given them.
GRADING_KEYS = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "PORTKEY_API_KEY")
TOKEN_NAME = "COPILOT_GITHUB_TOKEN"


def check_judge(settings: Config, lane: str) -> None:
    """
    Refuse a senior run with no judge: no `[lanes.senior.judge]` in suss.toml,
    or no COPILOT_GITHUB_TOKEN in .env. Every other lane passes.

    An empty token counts as unset. Raises ConfigError naming what is missing.
    """
    if lane != lanes.SENIOR:
        return
    if settings.judge is None:
        raise ConfigError(
            f"{settings.path}: no [lanes.{lane}.judge], which suss judges {lane} with; set its"
            " credit_budget, the AI credits every judge session of a job may spend together"
        )
    if not settings.env.get(TOKEN_NAME):
        raise ConfigError(
            f"{settings.path.parent / '.env'} sets no {TOKEN_NAME}, which suss judges the trials"
            f" of {lane} with"
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
