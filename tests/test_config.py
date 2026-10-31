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
swe_bench_pro = "../SWE-bench_Pro-os"
dataset = "swe-bench-pro-v2"
environment = "docker"
k = 3
concurrency = 4
"""


def test_reads_the_top_level_keys_of_the_spec_example(spec_config: Path):
    settings = config.load(spec_config, "claude-code")

    assert settings.dataset == "swe-bench-pro-v2"
    assert settings.environment == "modal"
    assert settings.k == 1
    assert settings.concurrency == 16
    assert settings.arm.max_cost_usd == 20
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
    }
    assert peer_review.name == "peer-review"
    assert peer_review.baseline == "claude-code"
    assert peer_review.params == {
        "model": "claude-opus-4-6",
        "effort": "high",
        "rounds": 3,
        "reviewer": "deepseek/deepseek-v3.2",
    }


def test_an_arm_that_sets_neither_cap_takes_both_from_the_top(tmp_path: Path):
    path = write_config(
        tmp_path,
        TOP + 'max_cost_usd = 20\nmax_seconds = 2700\n[arms.a]\nharness = "h"\n',
    )

    arm = config.load(path, "a").arm

    assert (arm.max_cost_usd, arm.max_seconds) == (20, 2700)


def test_an_arm_that_sets_both_caps_overrides_the_top(tmp_path: Path):
    path = write_config(
        tmp_path,
        TOP
        + "max_cost_usd = 20\nmax_seconds = 2700\n"
        + '[arms.a]\nharness = "h"\nmax_cost_usd = 5\nmax_seconds = 600\n',
    )

    arm = config.load(path, "a").arm

    assert (arm.max_cost_usd, arm.max_seconds) == (5, 600)


def test_an_arm_that_sets_only_max_cost_usd_keeps_the_top_max_seconds(tmp_path: Path):
    path = write_config(
        tmp_path,
        TOP + 'max_cost_usd = 20\nmax_seconds = 2700\n[arms.a]\nharness = "h"\nmax_cost_usd = 5\n',
    )

    arm = config.load(path, "a").arm

    assert (arm.max_cost_usd, arm.max_seconds) == (5, 2700)


def test_an_arm_that_sets_only_max_seconds_keeps_the_top_max_cost_usd(tmp_path: Path):
    path = write_config(
        tmp_path,
        TOP + 'max_cost_usd = 20\nmax_seconds = 2700\n[arms.a]\nharness = "h"\nmax_seconds = 600\n',
    )

    arm = config.load(path, "a").arm

    assert (arm.max_cost_usd, arm.max_seconds) == (20, 600)


def test_an_arm_cap_of_zero_still_overrides_the_top(tmp_path: Path):
    path = write_config(
        tmp_path,
        TOP
        + "max_cost_usd = 20\nmax_seconds = 2700\n"
        + '[arms.a]\nharness = "h"\nmax_cost_usd = 0\nmax_seconds = 0\n',
    )

    arm = config.load(path, "a").arm

    assert (arm.max_cost_usd, arm.max_seconds) == (0, 0)


def test_an_arm_cap_applies_where_the_top_sets_none(tmp_path: Path):
    path = write_config(
        tmp_path, TOP + '[arms.a]\nharness = "h"\nmax_cost_usd = 5\nmax_seconds = 600\n'
    )

    arm = config.load(path, "a").arm

    assert (arm.max_cost_usd, arm.max_seconds) == (5, 600)


def test_a_cap_set_nowhere_is_none(tmp_path: Path):
    path = write_config(tmp_path, TOP + '[arms.a]\nharness = "h"\n')

    arm = config.load(path, "a").arm

    assert (arm.max_cost_usd, arm.max_seconds) == (None, None)


def test_paths_resolve_from_the_directory_holding_suss_toml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    path = write_config(tmp_path / "project", TOP + '[arms.a]\nharness = "../self-review"\n')
    elsewhere = tmp_path / "elsewhere" / "deeper"
    elsewhere.mkdir(parents=True)
    monkeypatch.chdir(elsewhere)

    settings = config.load(path, "a")

    assert settings.swe_bench_pro == (tmp_path / "SWE-bench_Pro-os").resolve()
    assert settings.arm.harness == (tmp_path / "self-review").resolve()


def test_paths_resolve_from_suss_toml_given_by_a_relative_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    write_config(tmp_path / "project", TOP + '[arms.a]\nharness = "adapters/claude-code"\n')
    monkeypatch.chdir(tmp_path)

    settings = config.load(Path("project/suss.toml"), "a")

    assert settings.swe_bench_pro == (tmp_path / "SWE-bench_Pro-os").resolve()
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
