import re
from pathlib import Path

import pytest

from swe_drill import config

SPEC = Path(__file__).resolve().parent.parent / "docs" / "specs" / "swe-drill-spec.md"


def spec_swe_drill_toml() -> str:
    """The example swe-drill.toml from the spec, so the code and the spec cannot drift apart."""
    blocks = re.findall(r"```toml\n(.*?)```", SPEC.read_text(), re.DOTALL)
    examples = [block for block in blocks if "[arms." in block]
    assert len(examples) == 1, f"expected one example swe-drill.toml in {SPEC}, found {len(examples)}"
    return examples[0]


@pytest.fixture
def spec_config(tmp_path: Path) -> Path:
    path = tmp_path / "swe-drill.toml"
    path.write_text(spec_swe_drill_toml())
    return path


def write_config(directory: Path, text: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "swe-drill.toml"
    path.write_text(text)
    return path


TOP = """\
swe_rebench = "../tasks/swe-rebench-07-2026"
swe_atlas_rf = "../tasks/swe-atlas-rf"
environment = "docker"
k = 3
concurrency = 4
"""


def test_reads_the_top_level_keys_of_the_spec_example(spec_config: Path):
    settings = config.load(spec_config, "copilot-sonnet", "rebench")

    assert settings.swe_rebench == (spec_config.parent / "../tasks/swe-rebench-07-2026").resolve()
    assert settings.environment == "docker"
    assert settings.k == 1
    assert settings.concurrency == 16


def test_reads_each_arm_of_the_spec_example(spec_config: Path):
    sonnet = config.load(spec_config, "copilot-sonnet", "rebench").arm
    hve = config.load(spec_config, "hve-sol", "rebench").arm

    assert (sonnet.name, sonnet.agent, sonnet.model) == (
        "copilot-sonnet", "swe_drill.agents.copilot_cli:CopilotCli", "claude-sonnet-5.5",
    )
    assert sonnet.kwargs == {"version": "1.0.105", "reasoning_effort": "high"}
    assert (hve.agent, hve.model) == ("swe_drill.agents.hve:CopilotCliHve", "gpt-6.1-sol")
    assert hve.kwargs == {
        "version": "1.0.105",
        "reasoning_effort": "high",
        "hve_core_commit": "55352bb8e9f0622ff40f5c6afa80363adaf6b07f",
    }


def test_a_lane_swe_drill_does_not_have_is_an_error_naming_it(tmp_path: Path):
    path = write_config(
        tmp_path,
        TOP + '[lanes.senoir]\n[arms.a]\nagent = "m:A"\n',
    )

    with pytest.raises(config.ConfigError) as error:
        config.load(path, "a", "rebench")

    assert str(error.value) == (
        f"{path}: sets [lanes.senoir], but SWE-drill has no lane named senoir; its lanes are rebench, atlas-rf"
    )


def test_a_key_swe_drill_does_not_read_on_a_lane_is_an_error_naming_it(tmp_path: Path):
    path = write_config(
        tmp_path, TOP + '[lanes.rebench]\nmax_cost_usd = 20\n[arms.a]\nagent = "m:A"\n'
    )

    with pytest.raises(config.ConfigError) as error:
        config.load(path, "a", "rebench")

    assert str(error.value) == (
        f"{path}: [lanes.rebench] sets max_cost_usd, but SWE-drill reads only [lanes.atlas-rf.judge]"
    )


@pytest.mark.parametrize(
    "text", ["lanes = 3\n", "lanes = { rebench = 2400 }\n"], ids=["lanes", "lane"]
)
def test_lanes_that_are_not_tables_are_an_error(tmp_path: Path, text: str):
    path = write_config(tmp_path, TOP + text + '[arms.a]\nagent = "m:A"\n')

    with pytest.raises(config.ConfigError) as error:
        config.load(path, "a", "rebench")

    assert str(path) in str(error.value) and "table" in str(error.value)


def test_paths_resolve_from_the_directory_holding_swe_drill_toml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    path = write_config(tmp_path / "project", TOP + '[arms.a]\nagent = "m:A"\n')
    elsewhere = tmp_path / "elsewhere" / "deeper"
    elsewhere.mkdir(parents=True)
    monkeypatch.chdir(elsewhere)

    settings = config.load(path, "a", "rebench")

    assert settings.swe_rebench == (tmp_path / "tasks" / "swe-rebench-07-2026").resolve()
    assert settings.swe_atlas_rf == (tmp_path / "tasks" / "swe-atlas-rf").resolve()


def test_paths_resolve_from_swe_drill_toml_given_by_a_relative_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    write_config(tmp_path / "project", TOP + '[arms.a]\nagent = "m:A"\n')
    monkeypatch.chdir(tmp_path)

    settings = config.load(Path("project/swe-drill.toml"), "a", "rebench")

    assert settings.swe_rebench == (tmp_path / "tasks" / "swe-rebench-07-2026").resolve()


def test_reads_the_env_file_beside_swe_drill_toml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    path = write_config(tmp_path / "project", TOP + '[arms.a]\nagent = "m:A"\n')
    (tmp_path / "project" / ".env").write_text(
        "ANTHROPIC_API_KEY=sk-ant-123\n\nDEEPSEEK_API_KEY=ds-456\n"
    )
    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=from-the-cwd\n")
    monkeypatch.chdir(tmp_path)

    settings = config.load(path, "a", "rebench")

    assert settings.env == {"ANTHROPIC_API_KEY": "sk-ant-123", "DEEPSEEK_API_KEY": "ds-456"}


def test_an_env_value_keeps_everything_after_the_first_equals_sign(tmp_path: Path):
    path = write_config(tmp_path, TOP + '[arms.a]\nagent = "m:A"\n')
    (tmp_path / ".env").write_text("TOKEN=abc=def==\nEMPTY=\n")

    settings = config.load(path, "a", "rebench")

    assert settings.env == {"TOKEN": "abc=def==", "EMPTY": ""}


def test_a_missing_env_file_is_an_empty_mapping(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    path = write_config(tmp_path / "project", TOP + '[arms.a]\nagent = "m:A"\n')
    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=from-the-cwd\n")
    monkeypatch.chdir(tmp_path)

    settings = config.load(path, "a", "rebench")

    assert settings.env == {}


def test_an_empty_env_file_is_an_empty_mapping(tmp_path: Path):
    path = write_config(tmp_path, TOP + '[arms.a]\nagent = "m:A"\n')
    (tmp_path / ".env").write_text("")

    settings = config.load(path, "a", "rebench")

    assert settings.env == {}


def test_an_env_line_without_an_equals_sign_is_an_error(tmp_path: Path):
    path = write_config(tmp_path, TOP + '[arms.a]\nagent = "m:A"\n')
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


def test_a_missing_swe_drill_toml_is_an_error_naming_it(tmp_path: Path):
    path = tmp_path / "swe-drill.toml"

    with pytest.raises(config.ConfigError) as error:
        config.load(path, "a", "rebench")

    assert str(path) in str(error.value)


@pytest.mark.parametrize(
    "content",
    [b"k = \n", b"k = 1\nk = 2\n", b'environment = "\xff"\n'],
    ids=["no-value", "duplicate-key", "not-utf-8"],
)
def test_a_swe_drill_toml_that_is_not_toml_is_an_error_naming_it(tmp_path: Path, content: bytes):
    path = tmp_path / "swe-drill.toml"
    path.write_bytes(content)

    with pytest.raises(config.ConfigError) as error:
        config.load(path, "a", "rebench")

    assert str(path) in str(error.value)


@pytest.mark.parametrize(
    "key, lane",
    [
        ("swe_rebench", "rebench"),
        ("swe_atlas_rf", "atlas-rf"),
        ("environment", "rebench"),
        ("k", "atlas-rf"),
        ("concurrency", "rebench"),
    ],
)
def test_a_missing_top_level_key_is_an_error_naming_it_and_the_file(
    tmp_path: Path, key: str, lane: str
):
    lines = [line for line in TOP.splitlines() if not line.startswith(f"{key} =")]
    path = write_config(tmp_path, "\n".join(lines) + '\n[arms.a]\nagent = "m:A"\n')

    with pytest.raises(config.ConfigError) as error:
        config.load(path, "a", lane)

    assert str(error.value) == f"{path}: no {key!r}"


@pytest.mark.parametrize(
    "key, lane",
    [("swe_rebench", "atlas-rf"), ("swe_atlas_rf", "rebench")],
)
def test_a_lane_needs_no_other_lanes_download(tmp_path: Path, key: str, lane: str):
    lines = [line for line in TOP.splitlines() if not line.startswith(f"{key} =")]
    path = write_config(tmp_path, "\n".join(lines) + '\n[arms.a]\nagent = "m:A"\n')

    settings = config.load(path, "a", lane)

    assert getattr(settings, key) is None
    assert settings.download(lane) == (tmp_path / TOP_DOWNLOADS[lane]).resolve()


TOP_DOWNLOADS = {
    "rebench": "../tasks/swe-rebench-07-2026",
    "atlas-rf": "../tasks/swe-atlas-rf",
}


def test_each_lane_reads_the_download_its_own_key_names(spec_config: Path):
    for lane in ("rebench", "atlas-rf"):
        settings = config.load(spec_config, "copilot-sonnet", lane)

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
def test_a_key_swe_drill_no_longer_reads_is_an_error_naming_it(tmp_path: Path, old: str, named: str):
    path = write_config(tmp_path, old + TOP + '[arms.a]\nagent = "m:A"\n')

    with pytest.raises(config.ConfigError) as error:
        config.load(path, "a", "rebench")

    assert str(error.value) == (
        f"{path}: sets {named}, which SWE-drill no longer reads; name the directory of "
        "downloaded SWE-rebench tasks with swe_rebench"
    )


def test_an_old_swe_drill_toml_without_swe_rebench_is_refused_for_its_old_keys(tmp_path: Path):
    old = TOP.replace(
        'swe_rebench = "../tasks/swe-rebench-07-2026"\n',
        'swe_bench_pro = "../SWE-bench_Pro-os"\ndataset = "swe-bench-pro-v2"\n',
    )
    path = write_config(tmp_path, old + '[arms.a]\nagent = "m:A"\n')

    with pytest.raises(config.ConfigError, match="sets swe_bench_pro and dataset"):
        config.load(path, "a", "rebench")


def test_an_old_swe_drill_toml_that_names_seniors_download_is_refused_naming_it(tmp_path: Path):
    path = write_config(
        tmp_path,
        'senior_swe_bench = "../tasks/senior/senior-swe-bench-v2026.06"\n' + TOP + '[arms.a]\nagent = "m:A"\n',
    )

    with pytest.raises(config.ConfigError) as error:
        config.load(path, "a", "rebench")

    assert str(error.value) == (
        f"{path}: sets senior_swe_bench, which SWE-drill no longer reads, since it has no senior lane;"
        " remove it"
    )


def test_an_old_swe_drill_toml_that_sets_seniors_judge_is_refused_naming_the_lane(tmp_path: Path):
    path = write_config(tmp_path, TOP + '[lanes.senior.judge]\ncredit_budget = 2000\n[arms.a]\nagent = "m:A"\n')

    with pytest.raises(config.ConfigError) as error:
        config.load(path, "a", "rebench")

    assert str(error.value) == (
        f"{path}: sets [lanes.senior], but SWE-drill has no lane named senior; its lanes are rebench, atlas-rf"
    )


def test_an_arm_without_an_agent_is_an_error_naming_the_key_the_arm_and_the_file(
    tmp_path: Path,
):
    path = write_config(tmp_path, TOP + '[arms.lonely]\nmodel = "claude-sonnet-5.5"\n')

    with pytest.raises(config.ConfigError) as error:
        config.load(path, "lonely", "rebench")

    assert "'agent'" in str(error.value)
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
def test_max_cost_usd_is_refused(
    tmp_path: Path, top: str, arm_table: str, where: str
):
    path = write_config(tmp_path, TOP + top + '[arms.a]\nagent = "m:A"\n' + arm_table)

    with pytest.raises(config.ConfigError) as error:
        config.load(path, "a", "rebench")

    assert str(error.value) == f"{path}: {where}, but SWE-drill does not read it"


BASELINE = "\n[arms.b]\nagent = \"m:B\"\nbaseline = \"a\"\n"


@pytest.mark.parametrize("arm", ["a", "b"], ids=["another-arm", "the-arm-run"])
def test_an_arm_that_sets_baseline_is_refused_naming_it_and_the_key(tmp_path: Path, arm: str):
    path = write_config(tmp_path, TOP + '[arms.a]\nagent = "m:A"\n' + BASELINE)

    with pytest.raises(config.ConfigError) as error:
        config.load(path, arm, "rebench")

    assert str(error.value) == (
        f"{path}: [arms.b] sets baseline, which SWE-drill no longer reads; swe-drill leaderboard "
        "ranks every arm on its lane"
    )


def test_check_arms_refuses_an_arm_that_sets_baseline(tmp_path: Path):
    path = write_config(tmp_path, '[arms.a]\nagent = "m:A"\n' + BASELINE)

    with pytest.raises(config.ConfigError, match=r"\[arms.b\] sets baseline"):
        config.check_arms(path)


def test_check_arms_needs_none_of_what_a_run_needs(tmp_path: Path):
    # No top-level key, no agent, and a max_cost_usd that load refuses.
    path = write_config(tmp_path, 'max_cost_usd = 20\n[arms.a]\nmax_cost_usd = 5\n[arms.b]\n')

    config.check_arms(path)


def test_check_arms_refuses_a_missing_file(tmp_path: Path):
    with pytest.raises(config.ConfigError, match="swe-drill.toml: no such file"):
        config.check_arms(tmp_path / "swe-drill.toml")


def test_check_arms_refuses_a_file_that_is_not_toml(tmp_path: Path):
    path = write_config(tmp_path, "[arms.a\n")

    with pytest.raises(config.ConfigError, match="not valid TOML"):
        config.check_arms(path)


# The verifier's environment

# Every SWE Atlas task's [verifier.env], as the download writes it, with a literal value added.
ATLAS_VERIFIER_ENV = {
    "EVAL_API_KEY": "${OPENAI_API_KEY}",
    "EVAL_BASE_URL": "${OPENAI_API_BASE}",
    "EVAL_MODEL": "${EVAL_MODEL:-anthropic/claude-opus-4-5-20251101}",
    "REPO_NAME": "turborepo",
}
ATLAS_VARIABLES = ("EVAL_MODEL", "OPENAI_API_BASE", "OPENAI_API_KEY")


def with_env(tmp_path: Path, dotenv: str) -> config.Config:
    path = write_config(tmp_path, TOP + '[arms.a]\nagent = "m:A"\n')
    (tmp_path / ".env").write_text(dotenv)
    return config.load(path, "a", "rebench")


def test_the_variables_tasks_read_from_harbors_environment_are_those_their_templates_name():
    tasks = {
        Path("one"): {"verifier": {"env": ATLAS_VERIFIER_ENV}},
        Path("two"): {
            "environment": {"env": {"BUILD_TOKEN": "${BUILD_TOKEN}", "LANG": "C"}},
            # Harbor reads [solution.env] only for its oracle agent, which SWE-drill never runs.
            "solution": {"env": {"SOLUTION_ONLY": "${SOLUTION_ONLY}"}},
        },
    }

    assert config.host_variables(tasks) == tuple(sorted((*ATLAS_VARIABLES, "BUILD_TOKEN")))


def test_a_task_with_only_literal_env_values_reads_nothing_from_harbors_environment():
    tasks = {Path("one"): {"environment": {"env": {"LANG": "C"}}, "verifier": {"env": {"A": "b"}}}}

    assert config.host_variables(tasks) == ()


def test_the_grading_job_gets_each_variable_the_tasks_read_that_env_sets_and_no_empty_one(
    tmp_path: Path,
):
    settings = with_env(
        tmp_path,
        "OPENAI_API_KEY=sk-oa-1\nEVAL_MODEL=openai/gpt-5.5\n"
        "OPENAI_API_BASE=\nDEEPSEEK_API_KEY=ds-1\n",
    )

    assert config.verifier_env(settings, ATLAS_VARIABLES) == {
        "OPENAI_API_KEY": "sk-oa-1",
        "EVAL_MODEL": "openai/gpt-5.5",
    }




@pytest.mark.parametrize("retired", ["harness", "params"])
def test_an_arm_that_sets_harness_or_params_is_refused_naming_what_replaces_them(tmp_path, retired):
    path = write_config(tmp_path, TOP + f'[arms.a]\nagent = "m:A"\n{retired} = "x"\n')

    with pytest.raises(config.ConfigError) as error:
        config.load(path, "a", "rebench")

    assert str(error.value) == (
        f"{path}: [arms.a] sets {retired}, which SWE-drill no longer reads; name the arm's Harbor "
        "agent class with agent, its model with model, and its settings with kwargs"
    )


@pytest.mark.parametrize(
    "table, says",
    [
        ('agent = "no-colon"\n', "module:Class"),
        ('agent = 7\n', "module:Class"),
        ('agent = "m:A"\nmodel = 5\n', "model must be a string"),
        ('agent = "m:A"\nkwargs = 5\n', "kwargs must be a table"),
        ('agent = "m:A"\nkwargs = { limits = [1, 2] }\n', "kwargs limits must be"),
        ('agent = "m:A"\nkwargs = { arm = "b" }\n', "kwargs sets arm, which SWE-drill sets"),
        ('agent = "m:A"\nkwargs = { drill_config = "x" }\n', "kwargs sets drill_config"),
    ],
)
def test_an_arm_harbor_could_not_be_given_is_refused(tmp_path, table, says):
    path = write_config(tmp_path, TOP + "[arms.a]\n" + table)

    with pytest.raises(config.ConfigError, match=says):
        config.load(path, "a", "rebench")


# The atlas-rf lane's rubric judge

ATLAS_JUDGE = '[lanes.atlas-rf.judge]\nmodel = "gemini-4-argon"\n'


def atlas_rf(tmp_path: Path, lanes_text: str, dotenv: str = "COPILOT_GITHUB_TOKEN=gh-1\n") -> config.Config:
    path = write_config(tmp_path, TOP + lanes_text + '[arms.a]\nagent = "m:A"\n')
    (tmp_path / ".env").write_text(dotenv)
    return config.load(path, "a", "atlas-rf")


def test_the_atlas_rf_lanes_judge_names_the_model_its_rubric_judge_calls(tmp_path: Path):
    settings = atlas_rf(tmp_path, ATLAS_JUDGE)

    assert settings.eval_model == "gemini-4-argon"
    config.check_judge(settings, "atlas-rf")


def test_another_lanes_run_takes_no_rubric_judge_and_needs_no_token(tmp_path: Path):
    path = write_config(tmp_path, TOP + ATLAS_JUDGE + '[arms.a]\nagent = "m:A"\n')

    settings = config.load(path, "a", "rebench")

    assert settings.eval_model is None
    config.check_judge(settings, "rebench")


def test_a_judge_on_another_lane_is_an_error(tmp_path: Path):
    path = write_config(tmp_path, TOP + '[lanes.rebench.judge]\nmodel = "x"\n[arms.a]\nagent = "m:A"\n')

    with pytest.raises(config.ConfigError) as error:
        config.load(path, "a", "rebench")

    assert str(error.value) == (
        f"{path}: [lanes.rebench] sets judge, but SWE-drill reads only [lanes.atlas-rf.judge]"
    )


@pytest.mark.parametrize("lanes_text", ["", "[lanes.atlas-rf.judge]\n"], ids=["no-table", "no-model"])
def test_an_atlas_rf_run_with_no_judge_model_is_refused_naming_the_table(tmp_path: Path, lanes_text: str):
    settings = atlas_rf(tmp_path, lanes_text)

    with pytest.raises(config.ConfigError) as error:
        config.check_judge(settings, "atlas-rf")

    assert str(error.value) == (
        f"{settings.path}: no model in [lanes.atlas-rf.judge], which names the model atlas-rf's rubric"
        " judge calls through Copilot, such as gemini-4-argon"
    )


@pytest.mark.parametrize("dotenv", ["", "COPILOT_GITHUB_TOKEN=\n"], ids=["unset", "empty"])
def test_an_atlas_rf_run_with_no_copilot_token_is_refused_naming_it(tmp_path: Path, dotenv: str):
    settings = atlas_rf(tmp_path, ATLAS_JUDGE, dotenv)

    with pytest.raises(config.ConfigError) as error:
        config.check_judge(settings, "atlas-rf")

    assert str(error.value) == (
        f"{tmp_path / '.env'} sets no COPILOT_GITHUB_TOKEN, which SWE-drill judges the trials of atlas-rf with"
    )


@pytest.mark.parametrize(
    "text, says",
    [
        ("model = 5\n", "sets model to 5, which must be a non-empty string"),
        ('model = ""\n', "sets model to '', which must be a non-empty string"),
        ('model = "x"\neffort = "high"\n', "sets effort, but SWE-drill reads only model there"),
    ],
    ids=["number", "empty", "other-key"],
)
def test_an_atlas_rf_judge_swe_drill_cannot_read_is_an_error_naming_it(tmp_path: Path, text: str, says: str):
    with pytest.raises(config.ConfigError) as error:
        atlas_rf(tmp_path, "[lanes.atlas-rf.judge]\n" + text)

    assert str(error.value) == f"{tmp_path / 'swe-drill.toml'}: [lanes.atlas-rf.judge] {says}"


def test_an_atlas_rf_judge_is_refused_when_another_lane_runs(tmp_path: Path):
    path = write_config(tmp_path, TOP + '[lanes.atlas-rf.judge]\nmodel = 5\n[arms.a]\nagent = "m:A"\n')

    with pytest.raises(config.ConfigError, match="must be a non-empty string"):
        config.load(path, "a", "rebench")
