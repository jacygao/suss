"""The settings one named arm runs with, read from suss.toml and the .env beside it."""

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ConfigError(Exception):
    """suss.toml or its .env cannot give the settings asked for."""


@dataclass(frozen=True)
class Arm:
    name: str
    harness: Path
    baseline: str | None
    # Passed to the harness as it stands; suss reads none of it.
    params: dict[str, Any]
    # None where neither the arm nor the top of the file sets one.
    max_cost_usd: float | None
    max_seconds: float | None


@dataclass(frozen=True)
class Config:
    path: Path
    swe_bench_pro: Path
    dataset: str
    environment: str
    k: int
    concurrency: int
    arm: Arm
    env: dict[str, str]


def load(path: Path, arm: str) -> Config:
    """
    Read the settings `arm` runs with from the suss.toml at `path`.

    Paths in the file resolve from the directory holding it, whatever the
    current directory is. Raises ConfigError for an arm the file does not
    define, or a .env line that is not NAME=VALUE.
    """
    path = Path(path).resolve()
    directory = path.parent
    with path.open("rb") as file:
        data = tomllib.load(file)
    arms = data.get("arms", {})
    if arm not in arms:
        raise ConfigError(f"arm {arm} is not defined in {path}")
    table = arms[arm]
    return Config(
        path=path,
        swe_bench_pro=(directory / data["swe_bench_pro"]).resolve(),
        dataset=data["dataset"],
        environment=data["environment"],
        k=data["k"],
        concurrency=data["concurrency"],
        arm=Arm(
            name=arm,
            harness=(directory / table["harness"]).resolve(),
            baseline=table.get("baseline"),
            params=table.get("params", {}),
            max_cost_usd=_cap("max_cost_usd", table, data),
            max_seconds=_cap("max_seconds", table, data),
        ),
        env=_read_env(directory / ".env"),
    )


def _cap(key: str, arm: dict[str, Any], top: dict[str, Any]) -> Any:
    # Membership, not truthiness, so that an arm's 0 still overrides the top.
    if key in arm:
        return arm[key]
    return top.get(key)


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
