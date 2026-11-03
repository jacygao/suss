import re
from pathlib import Path

import pytest

from suss import config

SPEC = Path(__file__).resolve().parent.parent / "docs" / "specs" / "suss-spec.md"


def spec_suss_toml() -> str:
    """The example suss.toml from the spec, so the code and the spec cannot drift apart."""
    blocks = re.findall(r"```toml\n(.*?)```", SPEC.read_text(), re.DOTALL)
    examples = [block for block in blocks if "[arms." in block]
    assert len(examples) == 1, f"expected one example suss.toml in {SPEC}, found {len(examples)}"
    return examples[0]


@pytest.fixture
def spec_config(tmp_path: Path) -> Path:
    path = tmp_path / "suss.toml"
    path.write_text(spec_suss_toml())
    return path


def write_config(directory: Path, text: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "suss.toml"
    path.write_text(text)
    return path


TOP = """\
swe_rebench = "../tasks/swe-rebench-07-2026"
environment = "docker"
k = 3
concurrency = 4
"""


def test_reads_the_top_level_keys_of_the_spec_example(spec_config: Path):
    settings = config.load(spec_config, "claude-code")

    assert settings.swe_rebench == (spec_config.parent / "../tasks/swe-rebench-07-2026").resolve()
    assert settings.environment == "docker"
    assert settings.k == 1
    assert settings.concurrency == 16
    assert settings.arm.max_seconds == 2700


def test_reads_each_arm_of_the_spec_example(spec_config: Path):
    claude_code = config.load(spec_config, "claude-code").arm
    peer_review = config.load(spec_config, "peer-review").arm

    assert claude_code.name == "claude-code"
    assert claude_code.baseline is None
    assert claude_code.params == {
        "version": "2.1.293",
        "model": "claude-opus-4-6",
        "effort": "high",
        "max_budget_usd": 20,
    }
    assert peer_review.name == "peer-review"
    assert peer_review.baseline == "claude-code"
    assert peer_review.params == {
        "model": "claude-opus-4-6",
        "effort": "high",
        "rounds": 3,
        "reviewer": "deepseek/deepseek-v3.2",
    }


def test_an_arm_that_sets_no_max_seconds_takes_the_tops(tmp_path: Path):
    path = write_config(tmp_path, TOP + 'max_seconds = 2700\n[arms.a]\nharness = "h"\n')

    assert config.load(path, "a").arm.max_seconds == 2700


# 0 is a cap the arm set, so it overrides the top's rather than falling through to it.
@pytest.mark.parametrize("seconds", [600, 0])
def test_an_arms_own_max_seconds_overrides_the_tops(tmp_path: Path, seconds: int):
    path = write_config(
        tmp_path,
        TOP + f'max_seconds = 2700\n[arms.a]\nharness = "h"\nmax_seconds = {seconds}\n',
    )

    assert config.load(path, "a").arm.max_seconds == seconds


def test_an_arms_max_seconds_applies_where_the_top_sets_none(tmp_path: Path):
    path = write_config(tmp_path, TOP + '[arms.a]\nharness = "h"\nmax_seconds = 600\n')

    assert config.load(path, "a").arm.max_seconds == 600


def test_a_max_seconds_set_nowhere_is_none(tmp_path: Path):
    path = write_config(tmp_path, TOP + '[arms.a]\nharness = "h"\n')

    assert config.load(path, "a").arm.max_seconds is None


def test_paths_resolve_from_the_directory_holding_suss_toml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    path = write_config(tmp_path / "project", TOP + '[arms.a]\nharness = "../self-review"\n')
    elsewhere = tmp_path / "elsewhere" / "deeper"
    elsewhere.mkdir(parents=True)
    monkeypatch.chdir(elsewhere)

    settings = config.load(path, "a")

    assert settings.swe_rebench == (tmp_path / "tasks" / "swe-rebench-07-2026").resolve()
    assert settings.arm.harness == (tmp_path / "self-review").resolve()


def test_paths_resolve_from_suss_toml_given_by_a_relative_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    write_config(tmp_path / "project", TOP + '[arms.a]\nharness = "adapters/claude-code"\n')
    monkeypatch.chdir(tmp_path)

    settings = config.load(Path("project/suss.toml"), "a")

    assert settings.swe_rebench == (tmp_path / "tasks" / "swe-rebench-07-2026").resolve()
    assert settings.arm.harness == (tmp_path / "project" / "adapters" / "claude-code").resolve()


def test_reads_the_env_file_beside_suss_toml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    path = write_config(tmp_path / "project", TOP + '[arms.a]\nharness = "h"\n')
    (tmp_path / "project" / ".env").write_text(
        "ANTHROPIC_API_KEY=sk-ant-123\n\nDEEPSEEK_API_KEY=ds-456\n"
    )
    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=from-the-cwd\n")
    monkeypatch.chdir(tmp_path)

    settings = config.load(path, "a")

    assert settings.env == {"ANTHROPIC_API_KEY": "sk-ant-123", "DEEPSEEK_API_KEY": "ds-456"}


def test_an_env_value_keeps_everything_after_the_first_equals_sign(tmp_path: Path):
    path = write_config(tmp_path, TOP + '[arms.a]\nharness = "h"\n')
    (tmp_path / ".env").write_text("TOKEN=abc=def==\nEMPTY=\n")

    settings = config.load(path, "a")

    assert settings.env == {"TOKEN": "abc=def==", "EMPTY": ""}


def test_a_missing_env_file_is_an_empty_mapping(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    path = write_config(tmp_path / "project", TOP + '[arms.a]\nharness = "h"\n')
    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=from-the-cwd\n")
    monkeypatch.chdir(tmp_path)

    settings = config.load(path, "a")

    assert settings.env == {}


def test_an_empty_env_file_is_an_empty_mapping(tmp_path: Path):
    path = write_config(tmp_path, TOP + '[arms.a]\nharness = "h"\n')
    (tmp_path / ".env").write_text("")

    settings = config.load(path, "a")

    assert settings.env == {}


def test_an_env_line_without_an_equals_sign_is_an_error(tmp_path: Path):
    path = write_config(tmp_path, TOP + '[arms.a]\nharness = "h"\n')
    (tmp_path / ".env").write_text("A=1\nANTHROPIC_API_KEY\n")

    with pytest.raises(config.ConfigError) as error:
        config.load(path, "a")

    assert str(tmp_path / ".env") in str(error.value)
    assert "line 2" in str(error.value)


def test_an_arm_the_file_does_not_define_is_an_error_naming_both(spec_config: Path):
    with pytest.raises(config.ConfigError) as error:
        config.load(spec_config, "no-such-arm")

    assert "no-such-arm" in str(error.value)
    assert str(spec_config) in str(error.value)


def test_a_missing_suss_toml_is_an_error_naming_it(tmp_path: Path):
    path = tmp_path / "suss.toml"

    with pytest.raises(config.ConfigError) as error:
        config.load(path, "a")

    assert str(path) in str(error.value)


@pytest.mark.parametrize(
    "content",
    [b"k = \n", b"k = 1\nk = 2\n", b'environment = "\xff"\n'],
    ids=["no-value", "duplicate-key", "not-utf-8"],
)
def test_a_suss_toml_that_is_not_toml_is_an_error_naming_it(tmp_path: Path, content: bytes):
    path = tmp_path / "suss.toml"
    path.write_bytes(content)

    with pytest.raises(config.ConfigError) as error:
        config.load(path, "a")

    assert str(path) in str(error.value)


@pytest.mark.parametrize(
    "key", ["swe_rebench", "environment", "k", "concurrency"]
)
def test_a_missing_top_level_key_is_an_error_naming_it_and_the_file(tmp_path: Path, key: str):
    lines = [line for line in TOP.splitlines() if not line.startswith(f"{key} =")]
    path = write_config(tmp_path, "\n".join(lines) + '\n[arms.a]\nharness = "h"\n')

    with pytest.raises(config.ConfigError) as error:
        config.load(path, "a")

    assert repr(key) in str(error.value)
    assert str(path) in str(error.value)


@pytest.mark.parametrize(
    "old, named",
    [
        ('swe_bench_pro = "../SWE-bench_Pro-os"\n', "swe_bench_pro"),
        ('dataset = "swe-bench-pro-v2"\n', "dataset"),
        ('swe_bench_pro = "../SWE-bench_Pro-os"\ndataset = "swe-bench-pro-v2"\n', "swe_bench_pro and dataset"),
    ],
    ids=["swe_bench_pro", "dataset", "both"],
)
def test_a_key_suss_no_longer_reads_is_an_error_naming_it(tmp_path: Path, old: str, named: str):
    path = write_config(tmp_path, old + TOP + '[arms.a]\nharness = "h"\n')

    with pytest.raises(config.ConfigError) as error:
        config.load(path, "a")

    assert str(error.value) == (
        f"{path}: sets {named}, which suss no longer reads; name the directory of "
        "downloaded SWE-rebench tasks with swe_rebench"
    )


def test_an_old_suss_toml_without_swe_rebench_is_refused_for_its_old_keys(tmp_path: Path):
    old = TOP.replace(
        'swe_rebench = "../tasks/swe-rebench-07-2026"\n',
        'swe_bench_pro = "../SWE-bench_Pro-os"\ndataset = "swe-bench-pro-v2"\n',
    )
    path = write_config(tmp_path, old + '[arms.a]\nharness = "h"\n')

    with pytest.raises(config.ConfigError, match="sets swe_bench_pro and dataset"):
        config.load(path, "a")


def test_an_arm_without_a_harness_is_an_error_naming_the_key_the_arm_and_the_file(
    tmp_path: Path,
):
    path = write_config(tmp_path, TOP + '[arms.lonely]\nbaseline = "claude-code"\n')

    with pytest.raises(config.ConfigError) as error:
        config.load(path, "lonely")

    assert "'harness'" in str(error.value)
    assert "lonely" in str(error.value)
    assert str(path) in str(error.value)


# Read and ignored, it would leave a run its author believes is capped running uncapped.
@pytest.mark.parametrize(
    "top, arm_table, where",
    [
        ("max_cost_usd = 20\n", "", "max_cost_usd is set at the top"),
        ("", "max_cost_usd = 5\n", "[arms.a] sets max_cost_usd"),
        ("max_cost_usd = 20\n", "max_cost_usd = 0\n", "max_cost_usd is set at the top"),
    ],
    ids=["top", "arm", "both"],
)
def test_max_cost_usd_is_refused_naming_the_param_that_replaces_it(
    tmp_path: Path, top: str, arm_table: str, where: str
):
    path = write_config(tmp_path, TOP + top + '[arms.a]\nharness = "h"\n' + arm_table)

    with pytest.raises(config.ConfigError) as error:
        config.load(path, "a")

    assert str(error.value) == (
        f"{path}: {where}, but suss does not read it; set the arm's spending limit in its "
        "params, under the name its adapter takes, such as max_budget_usd for "
        "adapters/claude-code"
    )


def test_baselines_reads_each_arms_baseline_and_none_where_it_names_none(spec_config: Path):
    assert config.baselines(spec_config, ["peer-review", "claude-code"]) == {
        "peer-review": "claude-code",
        "claude-code": None,
    }


def test_baselines_needs_none_of_what_a_run_needs(tmp_path: Path):
    # No top-level key, no harness, and a max_cost_usd that load refuses.
    path = write_config(
        tmp_path, 'max_cost_usd = 20\n[arms.a]\nbaseline = "b"\nmax_cost_usd = 5\n[arms.b]\n'
    )

    assert config.baselines(path, ["a", "b"]) == {"a": "b", "b": None}


def test_baselines_refuses_an_arm_the_file_does_not_define_naming_both(tmp_path: Path):
    path = write_config(tmp_path, '[arms.a]\nbaseline = "b"\n')

    with pytest.raises(config.ConfigError) as error:
        config.baselines(path, ["a", "b"])

    assert str(error.value) == f"arm b is not defined in {path.resolve()}"


def test_baselines_refuses_a_missing_file(tmp_path: Path):
    with pytest.raises(config.ConfigError, match="suss.toml: no such file"):
        config.baselines(tmp_path / "suss.toml", ["a"])


def test_baselines_refuses_a_file_that_is_not_toml(tmp_path: Path):
    path = write_config(tmp_path, "[arms.a\n")

    with pytest.raises(config.ConfigError, match="not valid TOML"):
        config.baselines(path, ["a"])
