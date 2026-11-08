import re
from pathlib import Path

import pytest

from suss import config, lanes

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
senior_swe_bench = "../tasks/senior/senior-swe-bench-v2026.06"
environment = "docker"
k = 3
concurrency = 4
"""


def test_reads_the_top_level_keys_of_the_spec_example(spec_config: Path):
    settings = config.load(spec_config, "claude-code", "rebench")

    assert settings.swe_rebench == (spec_config.parent / "../tasks/swe-rebench-07-2026").resolve()
    assert settings.environment == "docker"
    assert settings.k == 1
    assert settings.concurrency == 16
    assert settings.arm.max_seconds == 2700


def test_reads_each_arm_of_the_spec_example(spec_config: Path):
    claude_code = config.load(spec_config, "claude-code", "rebench").arm
    peer_review = config.load(spec_config, "peer-review", "rebench").arm

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

    assert config.load(path, "a", "rebench").arm.max_seconds == 2700


# 0 is a cap the arm set, so it overrides the top's rather than falling through to it.
@pytest.mark.parametrize("seconds", [600, 0])
def test_an_arms_own_max_seconds_overrides_the_tops(tmp_path: Path, seconds: int):
    path = write_config(
        tmp_path,
        TOP + f'max_seconds = 2700\n[arms.a]\nharness = "h"\nmax_seconds = {seconds}\n',
    )

    assert config.load(path, "a", "rebench").arm.max_seconds == seconds


def test_an_arms_max_seconds_applies_where_the_top_sets_none(tmp_path: Path):
    path = write_config(tmp_path, TOP + '[arms.a]\nharness = "h"\nmax_seconds = 600\n')

    assert config.load(path, "a", "rebench").arm.max_seconds == 600


def test_a_max_seconds_set_nowhere_is_none(tmp_path: Path):
    path = write_config(tmp_path, TOP + '[arms.a]\nharness = "h"\n')

    assert config.load(path, "a", "rebench").arm.max_seconds is None


def test_a_lanes_max_seconds_overrides_the_tops_on_that_lane_alone(tmp_path: Path):
    path = write_config(
        tmp_path,
        TOP + 'max_seconds = 2700\n[lanes.senior]\nmax_seconds = 5400\n[arms.a]\nharness = "h"\n',
    )

    assert config.load(path, "a", "senior").arm.max_seconds == 5400
    assert config.load(path, "a", "rebench").arm.max_seconds == 2700


# Each value differs from the others, so the one loaded says which won.
@pytest.mark.parametrize("seconds", [1800, 0])
def test_an_arms_own_max_seconds_overrides_its_lanes(tmp_path: Path, seconds: int):
    path = write_config(
        tmp_path,
        TOP + "max_seconds = 2700\n[lanes.senior]\nmax_seconds = 5400\n"
        f'[arms.a]\nharness = "h"\nmax_seconds = {seconds}\n',
    )

    assert config.load(path, "a", "senior").arm.max_seconds == seconds


def test_a_lanes_max_seconds_applies_where_the_top_sets_none(tmp_path: Path):
    path = write_config(
        tmp_path, TOP + '[lanes.rebench]\nmax_seconds = 0\n[arms.a]\nharness = "h"\n'
    )

    assert config.load(path, "a", "rebench").arm.max_seconds == 0


def test_a_lane_suss_does_not_have_is_an_error_naming_it(tmp_path: Path):
    path = write_config(
        tmp_path,
        TOP + "[lanes.rebench]\nmax_seconds = 2400\n[lanes.senoir]\nmax_seconds = 5400\n"
        '[arms.a]\nharness = "h"\n',
    )

    with pytest.raises(config.ConfigError) as error:
        config.load(path, "a", "rebench")

    assert str(error.value) == (
        f"{path}: sets [lanes.senoir], but suss has no lane named senoir; its lanes are rebench, senior"
    )


def test_a_key_suss_does_not_read_on_a_lane_is_an_error_naming_it(tmp_path: Path):
    path = write_config(
        tmp_path, TOP + '[lanes.rebench]\nmax_cost_usd = 20\n[arms.a]\nharness = "h"\n'
    )

    with pytest.raises(config.ConfigError) as error:
        config.load(path, "a", "rebench")

    assert str(error.value) == (
        f"{path}: [lanes.rebench] sets max_cost_usd, but suss reads only max_seconds there"
    )


@pytest.mark.parametrize(
    "text", ["lanes = 3\n", "lanes = { rebench = 2400 }\n"], ids=["lanes", "lane"]
)
def test_lanes_that_are_not_tables_are_an_error(tmp_path: Path, text: str):
    path = write_config(tmp_path, TOP + text + '[arms.a]\nharness = "h"\n')

    with pytest.raises(config.ConfigError) as error:
        config.load(path, "a", "rebench")

    assert str(path) in str(error.value) and "table" in str(error.value)


def test_paths_resolve_from_the_directory_holding_suss_toml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    path = write_config(tmp_path / "project", TOP + '[arms.a]\nharness = "../self-review"\n')
    elsewhere = tmp_path / "elsewhere" / "deeper"
    elsewhere.mkdir(parents=True)
    monkeypatch.chdir(elsewhere)

    settings = config.load(path, "a", "rebench")

    assert settings.swe_rebench == (tmp_path / "tasks" / "swe-rebench-07-2026").resolve()
    assert settings.senior_swe_bench == (
        tmp_path / "tasks" / "senior" / "senior-swe-bench-v2026.06"
    ).resolve()
    assert settings.arm.harness == (tmp_path / "self-review").resolve()


def test_paths_resolve_from_suss_toml_given_by_a_relative_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    write_config(tmp_path / "project", TOP + '[arms.a]\nharness = "adapters/claude-code"\n')
    monkeypatch.chdir(tmp_path)

    settings = config.load(Path("project/suss.toml"), "a", "rebench")

    assert settings.swe_rebench == (tmp_path / "tasks" / "swe-rebench-07-2026").resolve()
    assert settings.arm.harness == (tmp_path / "project" / "adapters" / "claude-code").resolve()


def test_reads_the_env_file_beside_suss_toml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    path = write_config(tmp_path / "project", TOP + '[arms.a]\nharness = "h"\n')
    (tmp_path / "project" / ".env").write_text(
        "ANTHROPIC_API_KEY=sk-ant-123\n\nDEEPSEEK_API_KEY=ds-456\n"
    )
    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=from-the-cwd\n")
    monkeypatch.chdir(tmp_path)

    settings = config.load(path, "a", "rebench")

    assert settings.env == {"ANTHROPIC_API_KEY": "sk-ant-123", "DEEPSEEK_API_KEY": "ds-456"}


def test_an_env_value_keeps_everything_after_the_first_equals_sign(tmp_path: Path):
    path = write_config(tmp_path, TOP + '[arms.a]\nharness = "h"\n')
    (tmp_path / ".env").write_text("TOKEN=abc=def==\nEMPTY=\n")

    settings = config.load(path, "a", "rebench")

    assert settings.env == {"TOKEN": "abc=def==", "EMPTY": ""}


def test_a_missing_env_file_is_an_empty_mapping(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    path = write_config(tmp_path / "project", TOP + '[arms.a]\nharness = "h"\n')
    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=from-the-cwd\n")
    monkeypatch.chdir(tmp_path)

    settings = config.load(path, "a", "rebench")

    assert settings.env == {}


def test_an_empty_env_file_is_an_empty_mapping(tmp_path: Path):
    path = write_config(tmp_path, TOP + '[arms.a]\nharness = "h"\n')
    (tmp_path / ".env").write_text("")

    settings = config.load(path, "a", "rebench")

    assert settings.env == {}


def test_an_env_line_without_an_equals_sign_is_an_error(tmp_path: Path):
    path = write_config(tmp_path, TOP + '[arms.a]\nharness = "h"\n')
    (tmp_path / ".env").write_text("A=1\nANTHROPIC_API_KEY\n")

    with pytest.raises(config.ConfigError) as error:
        config.load(path, "a", "rebench")

    assert str(tmp_path / ".env") in str(error.value)
    assert "line 2" in str(error.value)


def test_an_arm_the_file_does_not_define_is_an_error_naming_both(spec_config: Path):
    with pytest.raises(config.ConfigError) as error:
        config.load(spec_config, "no-such-arm", "rebench")

    assert "no-such-arm" in str(error.value)
    assert str(spec_config) in str(error.value)


def test_a_missing_suss_toml_is_an_error_naming_it(tmp_path: Path):
    path = tmp_path / "suss.toml"

    with pytest.raises(config.ConfigError) as error:
        config.load(path, "a", "rebench")

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
        config.load(path, "a", "rebench")

    assert str(path) in str(error.value)


@pytest.mark.parametrize(
    "key, lane",
    [
        ("swe_rebench", "rebench"),
        ("senior_swe_bench", "senior"),
        ("environment", "rebench"),
        ("k", "senior"),
        ("concurrency", "rebench"),
    ],
)
def test_a_missing_top_level_key_is_an_error_naming_it_and_the_file(
    tmp_path: Path, key: str, lane: str
):
    lines = [line for line in TOP.splitlines() if not line.startswith(f"{key} =")]
    path = write_config(tmp_path, "\n".join(lines) + '\n[arms.a]\nharness = "h"\n')

    with pytest.raises(config.ConfigError) as error:
        config.load(path, "a", lane)

    assert str(error.value) == f"{path}: no {key!r}"


@pytest.mark.parametrize(
    "key, lane", [("swe_rebench", "senior"), ("senior_swe_bench", "rebench")]
)
def test_a_lane_needs_no_other_lanes_download(tmp_path: Path, key: str, lane: str):
    lines = [line for line in TOP.splitlines() if not line.startswith(f"{key} =")]
    path = write_config(tmp_path, "\n".join(lines) + '\n[arms.a]\nharness = "h"\n')

    settings = config.load(path, "a", lane)

    assert getattr(settings, key) is None
    assert settings.download(lane) == (tmp_path / TOP_DOWNLOADS[lane]).resolve()


TOP_DOWNLOADS = {
    "rebench": "../tasks/swe-rebench-07-2026",
    "senior": "../tasks/senior/senior-swe-bench-v2026.06",
}


def test_each_lane_reads_the_download_its_own_key_names(spec_config: Path):
    for lane in ("rebench", "senior"):
        settings = config.load(spec_config, "claude-code", lane)

        assert settings.download(lane) == (spec_config.parent / TOP_DOWNLOADS[lane]).resolve()


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
        config.load(path, "a", "rebench")

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
        config.load(path, "a", "rebench")


def test_an_arm_without_a_harness_is_an_error_naming_the_key_the_arm_and_the_file(
    tmp_path: Path,
):
    path = write_config(tmp_path, TOP + '[arms.lonely]\nbaseline = "claude-code"\n')

    with pytest.raises(config.ConfigError) as error:
        config.load(path, "lonely", "rebench")

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
        config.load(path, "a", "rebench")

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


# The verifier's environment

# Every Senior SWE-Bench task's [verifier.env], as the v2026.06 download writes it.
SENIOR_VERIFIER_ENV = {
    "ANTHROPIC_API_KEY": "${ANTHROPIC_API_KEY:-}",
    "OPENAI_API_KEY": "${OPENAI_API_KEY:-}",
    "PORTKEY_API_KEY": "${PORTKEY_API_KEY:-}",
    "SSB_OVERRIDE_ALL_JUDGE_MODEL": "${SSB_OVERRIDE_ALL_JUDGE_MODEL:-}",
    "SSB_OVERRIDE_CLASSIFIER_MODEL": "${SSB_OVERRIDE_CLASSIFIER_MODEL:-}",
    "SSB_OVERRIDE_VA_HARNESS": "${SSB_OVERRIDE_VA_HARNESS:-}",
    "SSB_OVERRIDE_VA_MODEL": "${SSB_OVERRIDE_VA_MODEL:-}",
    "FORCE_VA_HARNESS": "${FORCE_VA_HARNESS:-}",
    "FORCE_VA_MODEL": "${FORCE_VA_MODEL:-}",
    "REPO_NAME": "turborepo",
    "TIMEOUT_MULTIPLIER": "${TIMEOUT_MULTIPLIER:-1}",
}
SENIOR_VARIABLES = (
    "ANTHROPIC_API_KEY",
    "FORCE_VA_HARNESS",
    "FORCE_VA_MODEL",
    "OPENAI_API_KEY",
    "PORTKEY_API_KEY",
    "SSB_OVERRIDE_ALL_JUDGE_MODEL",
    "SSB_OVERRIDE_CLASSIFIER_MODEL",
    "SSB_OVERRIDE_VA_HARNESS",
    "SSB_OVERRIDE_VA_MODEL",
    "TIMEOUT_MULTIPLIER",
)


def with_env(tmp_path: Path, dotenv: str) -> config.Config:
    path = write_config(tmp_path, TOP + '[arms.a]\nharness = "h"\n')
    (tmp_path / ".env").write_text(dotenv)
    return config.load(path, "a", "rebench")


def test_the_variables_tasks_read_from_harbors_environment_are_those_their_templates_name():
    tasks = {
        Path("one"): {"verifier": {"env": SENIOR_VERIFIER_ENV}},
        Path("two"): {
            "environment": {"env": {"BUILD_TOKEN": "${BUILD_TOKEN}", "LANG": "C"}},
            # Harbor reads [solution.env] only for its oracle agent, which suss never runs.
            "solution": {"env": {"SOLUTION_ONLY": "${SOLUTION_ONLY}"}},
        },
    }

    assert config.host_variables(tasks) == tuple(sorted((*SENIOR_VARIABLES, "BUILD_TOKEN")))


def test_a_task_with_only_literal_env_values_reads_nothing_from_harbors_environment():
    tasks = {Path("one"): {"environment": {"env": {"LANG": "C"}}, "verifier": {"env": {"A": "b"}}}}

    assert config.host_variables(tasks) == ()


def test_the_grading_job_gets_each_variable_the_tasks_read_that_env_sets_and_no_empty_one(
    tmp_path: Path,
):
    settings = with_env(
        tmp_path,
        "ANTHROPIC_API_KEY=sk-ant-1\nSSB_OVERRIDE_ALL_JUDGE_MODEL=openai/gpt-5.5\n"
        "TIMEOUT_MULTIPLIER=\nDEEPSEEK_API_KEY=ds-1\n",
    )

    assert config.verifier_env(settings, SENIOR_VARIABLES) == {
        "ANTHROPIC_API_KEY": "sk-ant-1",
        "SSB_OVERRIDE_ALL_JUDGE_MODEL": "openai/gpt-5.5",
    }




# The senior lane's judge

JUDGE = '[lanes.senior.judge]\ncredit_budget = 2000\n'


def senior(tmp_path: Path, lanes_text: str, dotenv: str = "COPILOT_GITHUB_TOKEN=gh-1\n") -> config.Config:
    path = write_config(tmp_path, TOP + lanes_text + '[arms.a]\nharness = "h"\n')
    (tmp_path / ".env").write_text(dotenv)
    return config.load(path, "a", "senior")


def test_the_senior_lanes_judge_is_read_with_its_defaults_left_to_the_verifier(tmp_path: Path):
    settings = senior(tmp_path, JUDGE)

    assert settings.judge == config.Judge(credit_budget=2000)
    config.check_judge(settings, "senior")


def test_the_senior_lanes_judge_names_its_model_and_effort(tmp_path: Path):
    settings = senior(tmp_path, JUDGE + 'model = "gpt-6.1-sol"\neffort = ""\n')

    assert settings.judge == config.Judge(credit_budget=2000, model="gpt-6.1-sol", effort="")


def test_another_lanes_run_takes_no_judge(tmp_path: Path):
    path = write_config(tmp_path, TOP + JUDGE + '[arms.a]\nharness = "h"\n')

    settings = config.load(path, "a", "rebench")

    assert settings.judge is None
    config.check_judge(settings, "rebench")


def test_a_senior_run_with_no_judge_is_refused_naming_the_table(tmp_path: Path):
    settings = senior(tmp_path, "")

    with pytest.raises(config.ConfigError) as error:
        config.check_judge(settings, "senior")

    assert str(error.value) == (
        f"{settings.path}: no [lanes.senior.judge], which suss judges senior with; set its"
        " credit_budget, the AI credits every judge session of a job may spend together"
    )


@pytest.mark.parametrize("dotenv", ["", "COPILOT_GITHUB_TOKEN=\n"], ids=["unset", "empty"])
def test_a_senior_run_with_no_copilot_token_is_refused_naming_it(tmp_path: Path, dotenv: str):
    settings = senior(tmp_path, JUDGE, dotenv)

    with pytest.raises(config.ConfigError) as error:
        config.check_judge(settings, "senior")

    assert str(error.value) == (
        f"{tmp_path / '.env'} sets no COPILOT_GITHUB_TOKEN, which suss judges the trials of senior with"
    )


def test_a_senior_run_needs_no_key_for_seniors_own_judges(tmp_path: Path):
    settings = senior(tmp_path, JUDGE)

    assert not set(config.GRADING_KEYS) & set(settings.env)
    config.check_judge(settings, "senior")


@pytest.mark.parametrize(
    "text",
    [
        "[lanes.senior.judge]\nmodel = \"x\"\n",
        "[lanes.senior.judge]\ncredit_budget = 0\n",
        "[lanes.senior.judge]\ncredit_budget = \"lots\"\n",
        "[lanes.senior.judge]\ncredit_budget = true\n",
        "[lanes.senior.judge]\ncredit_budget = nan\n",
        "[lanes.senior.judge]\ncredit_budget = inf\n",
    ],
    ids=["missing", "zero", "string", "bool", "nan", "infinity"],
)
def test_a_judge_with_no_positive_credit_budget_is_an_error(tmp_path: Path, text: str):
    with pytest.raises(config.ConfigError, match=r"\[lanes.senior.judge\] must set credit_budget"):
        senior(tmp_path, text)


def test_a_judge_key_suss_does_not_read_is_an_error_naming_it(tmp_path: Path):
    with pytest.raises(config.ConfigError) as error:
        senior(tmp_path, JUDGE + "classifier = 1\n")

    assert str(error.value) == (
        f"{tmp_path / 'suss.toml'}: [lanes.senior.judge] sets classifier, but suss reads only"
        " credit_budget, model and effort there"
    )


def test_a_judge_model_that_is_not_a_string_is_an_error(tmp_path: Path):
    with pytest.raises(config.ConfigError, match="sets model to 5, which must be a string"):
        senior(tmp_path, JUDGE + "model = 5\n")


def test_a_judge_on_another_lane_is_an_error(tmp_path: Path):
    path = write_config(
        tmp_path, TOP + '[lanes.rebench.judge]\ncredit_budget = 1\n[arms.a]\nharness = "h"\n'
    )

    with pytest.raises(config.ConfigError) as error:
        config.load(path, "a", "rebench")

    assert str(error.value) == (
        f"{path}: [lanes.rebench] sets judge, but suss reads only max_seconds there"
    )
