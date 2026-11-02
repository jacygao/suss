import re
from pathlib import Path

import pytest

from suss.harness import Manifest, ManifestError, load_manifest

REPO = Path(__file__).resolve().parent.parent
SPEC = REPO / "docs" / "specs" / "suss-spec.md"


def spec_example_manifest() -> str:
    """The `suss-harness.toml` the spec shows, so the spec and the loader cannot drift apart."""
    contract = SPEC.read_text().split("## 4. The harness contract", 1)[1]
    match = re.search(r"```toml\n(.*?)```", contract, re.DOTALL)
    assert match, f"no toml example under The harness contract in {SPEC}"
    return match.group(1)


@pytest.fixture
def adapter(tmp_path):
    """Write a manifest into an adapter directory, and return the directory."""

    def write(text: str) -> Path:
        (tmp_path / "suss-harness.toml").write_text(text)
        return tmp_path

    return write


def test_reads_the_spec_example(adapter):
    manifest = load_manifest(adapter(spec_example_manifest()))

    assert manifest == Manifest(
        name="peer-review",
        version="0.1.0",
        install="install.sh",
        run="run.sh",
        allowed_hosts=("api.anthropic.com", "api.deepseek.com"),
        env=("ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY"),
        max_cost_usd=True,
    )


REQUIRED = 'name = "h"\nversion = "1"\nrun = "run.sh"\n'


def test_an_absent_install_is_none(adapter):
    manifest = load_manifest(adapter("[harness]\n" + REQUIRED))

    assert manifest.install is None


def test_an_empty_install_is_kept_apart_from_an_absent_one(adapter):
    manifest = load_manifest(adapter("[harness]\n" + REQUIRED + 'install = ""\n'))

    assert manifest.install == ""


def test_absent_allowed_hosts_and_env_are_empty(adapter):
    manifest = load_manifest(adapter("[harness]\n" + REQUIRED))

    assert manifest.allowed_hosts == ()
    assert manifest.env == ()


def test_an_absent_max_cost_usd_is_true(adapter):
    manifest = load_manifest(adapter("[harness]\n" + REQUIRED))

    assert manifest.max_cost_usd is True


def test_max_cost_usd_false_is_read(adapter):
    manifest = load_manifest(adapter("[harness]\n" + REQUIRED + "max_cost_usd = false\n"))

    assert manifest.max_cost_usd is False


def test_the_claude_code_adapter_takes_a_budget_in_dollars():
    manifest = load_manifest(REPO / "adapters" / "claude-code")

    assert manifest.max_cost_usd is True


@pytest.mark.parametrize("field", ["name", "version", "run"])
def test_a_missing_required_field_is_named_with_the_path(adapter, field):
    lines = [line for line in REQUIRED.splitlines() if not line.startswith(field + " ")]
    directory = adapter("[harness]\n" + "\n".join(lines) + "\n")

    with pytest.raises(ManifestError) as error:
        load_manifest(directory)

    assert f"'{field}'" in str(error.value)
    assert str(directory / "suss-harness.toml") in str(error.value)


def test_an_unknown_field_is_named_with_the_path(adapter):
    # A misspelt `install` would otherwise skip installing without a word.
    directory = adapter("[harness]\n" + REQUIRED + 'instal = "install.sh"\n')

    with pytest.raises(ManifestError) as error:
        load_manifest(directory)

    assert "'instal'" in str(error.value)
    assert str(directory / "suss-harness.toml") in str(error.value)


def test_a_missing_harness_table_names_the_path(adapter):
    directory = adapter("[tool]\n" + REQUIRED)

    with pytest.raises(ManifestError, match=r"\[harness\]") as error:
        load_manifest(directory)

    assert str(directory / "suss-harness.toml") in str(error.value)


def test_a_missing_manifest_names_the_path(tmp_path):
    with pytest.raises(ManifestError) as error:
        load_manifest(tmp_path)

    assert str(tmp_path / "suss-harness.toml") in str(error.value)


def test_a_manifest_that_is_not_toml_names_the_path(adapter):
    directory = adapter("[harness\n")

    with pytest.raises(ManifestError) as error:
        load_manifest(directory)

    assert str(directory / "suss-harness.toml") in str(error.value)


def test_a_manifest_that_is_not_utf8_names_the_path(tmp_path):
    (tmp_path / "suss-harness.toml").write_text("[harness]\n" + REQUIRED, encoding="utf-16")

    with pytest.raises(ManifestError) as error:
        load_manifest(tmp_path)

    assert str(tmp_path / "suss-harness.toml") in str(error.value)


@pytest.mark.parametrize(
    "field, value",
    [
        ("name", "1"),
        ("install", "true"),
        # A string would otherwise become a tuple of its characters.
        ("allowed_hosts", '"api.anthropic.com"'),
        ("env", '["A", 1]'),
    ],
)
def test_a_field_of_the_wrong_type_is_named_with_the_path(adapter, field, value):
    fields = {"name": '"h"', "version": '"1"', "run": '"run.sh"', field: value}
    directory = adapter("[harness]\n" + "".join(f"{k} = {v}\n" for k, v in fields.items()))

    with pytest.raises(ManifestError) as error:
        load_manifest(directory)

    assert f"'{field}'" in str(error.value)
    assert str(directory / "suss-harness.toml") in str(error.value)


# Python reads each of these as true or false, and none of them says which the harness means.
@pytest.mark.parametrize("value", ["0", "1", '"false"'])
def test_a_max_cost_usd_that_is_not_a_boolean_is_named_with_the_path(adapter, value):
    directory = adapter("[harness]\n" + REQUIRED + f"max_cost_usd = {value}\n")

    with pytest.raises(ManifestError) as error:
        load_manifest(directory)

    assert str(error.value) == (
        f"{directory / 'suss-harness.toml'}: 'max_cost_usd' must be true or false"
    )
