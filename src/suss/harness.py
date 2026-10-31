"""Read a harness's manifest from its adapter directory."""

import tomllib
from dataclasses import dataclass
from pathlib import Path

MANIFEST_NAME = "suss-harness.toml"


class ManifestError(Exception):
    """A manifest suss cannot read. The message names the manifest's path."""


@dataclass(frozen=True)
class Manifest:
    name: str
    version: str
    # None when the manifest has no `install`, which is not the same as "".
    install: str | None
    run: str
    allowed_hosts: tuple[str, ...]
    env: tuple[str, ...]


REQUIRED = ("name", "version", "run")
LISTS = ("allowed_hosts", "env")
FIELDS = (*REQUIRED, "install", *LISTS)


def load_manifest(adapter_dir: Path) -> Manifest:
    """
    Read `suss-harness.toml` from the adapter directory.

    Raises ManifestError when the file is missing or is not TOML, when it has
    no `[harness]` table, or when a field is missing, unknown or of the wrong
    type. An absent `allowed_hosts` or `env` is empty.
    """
    path = adapter_dir / MANIFEST_NAME
    try:
        with open(path, "rb") as f:
            document = tomllib.load(f)
    except FileNotFoundError:
        raise ManifestError(f"{path}: no such file") from None
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as error:
        raise ManifestError(f"{path}: not valid TOML: {error}") from None

    table = document.get("harness")
    if not isinstance(table, dict):
        raise ManifestError(f"{path}: no [harness] table")

    unknown = sorted(set(table) - set(FIELDS))
    if unknown:
        raise ManifestError(f"{path}: unknown field {unknown[0]!r} in [harness]")
    for field in REQUIRED:
        if field not in table:
            raise ManifestError(f"{path}: [harness] has no {field!r}")

    for field in (*REQUIRED, "install"):
        if field in table and not isinstance(table[field], str):
            raise ManifestError(f"{path}: {field!r} must be a string")
    for field in LISTS:
        value = table.get(field, [])
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise ManifestError(f"{path}: {field!r} must be a list of strings")

    return Manifest(
        name=table["name"],
        version=table["version"],
        install=table.get("install"),
        run=table["run"],
        allowed_hosts=tuple(table.get("allowed_hosts", [])),
        env=tuple(table.get("env", [])),
    )
