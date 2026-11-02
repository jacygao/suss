import re
import shlex
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import typer.main
from harbor.cli.main import app as harbor_app
from harbor.cli.utils import parse_kwargs

from suss import config
from suss.cli import harbor_commands, harbor_executable, stamp
from suss.config import Arm, Config
from suss.harness import Manifest

SPEC = Path(__file__).resolve().parent.parent / "docs" / "specs" / "suss-spec.md"
STAMP = "20261031T142500Z"
CHECKOUT = Path("/work/SWE-bench_Pro-os")


def make_config(
    root: Path, arm: str = "peer-review", environment: str = "docker", concurrency: int = 16
):
    return Config(
        path=root / "suss.toml",
        swe_bench_pro=CHECKOUT,
        dataset="swe-bench-pro-v2",
        environment=environment,
        k=3,
        concurrency=concurrency,
        arm=Arm(
            name=arm,
            harness=Path("/work/peer-review"),
            baseline=None,
            params={},
            max_cost_usd=None,
            sets_max_cost_usd=False,
            max_seconds=None,
        ),
        env={},
    )


def make_manifest(allowed_hosts: tuple[str, ...] = ("api.anthropic.com", "api.deepseek.com")):
    return Manifest(
        name="peer-review",
        version="0.1.0",
        install=None,
        run="run.sh",
        allowed_hosts=allowed_hosts,
        env=(),
        max_cost_usd=True,
    )


def ak_values(argv: list[str]) -> dict[str, str]:
    """Each `--ak` token as it stands, keyed by the name before its `=`."""
    values = {}
    for flag, token in zip(argv, argv[1:]):
        if flag == "--ak":
            key, _, value = token.partition("=")
            values[key] = value
    return values


def harbor_parses(argv: list[str]) -> dict:
    """The `--ak` values as Harbor 0.22.0's own parser hands them to the agent."""
    return parse_kwargs([token for flag, token in zip(argv, argv[1:]) if flag == "--ak"])


def spec_commands(root: Path, checkout: Path, env: Path) -> tuple[list[str], list[str]]:
    """
    The two `harbor run` commands from the spec's `suss run`, split into tokens,
    with `root` as the directory holding suss.toml, `checkout` as the SWE-bench
    Pro checkout, and `env` as the environment suss is installed in.
    """
    blocks = re.findall(r"```bash\n(.*?)```", SPEC.read_text(), re.DOTALL)
    found = [block for block in blocks if "-a suss.agent:SussAgent" in block]
    assert len(found) == 1, f"expected one block with the agent job in {SPEC}, found {len(found)}"
    agent, regrade = found[0].replace("\\\n", " ").strip().split("\n\n")
    return tuple(
        shlex.split(
            command.replace("<stamp>", STAMP)
            .replace("<dir>", shlex.quote(str(root)))
            .replace("<checkout>", shlex.quote(str(checkout)))
            .replace("<env>", shlex.quote(str(env)))
        )
        for command in (agent, regrade)
    )


def spec_config(tmp_path: Path) -> Config:
    blocks = re.findall(r"```toml\n(.*?)```", SPEC.read_text(), re.DOTALL)
    examples = [block for block in blocks if "[arms." in block]
    assert len(examples) == 1, f"expected one example suss.toml in {SPEC}, found {len(examples)}"
    path = tmp_path / "suss" / "suss.toml"
    path.parent.mkdir()
    path.write_text(examples[0])
    return config.load(path, "peer-review")


def test_both_commands_match_the_spec_token_for_token(tmp_path):
    loaded = spec_config(tmp_path)
    harbor = harbor_executable()
    expected_agent, expected_regrade = spec_commands(
        loaded.path.parent, loaded.swe_bench_pro, harbor.parent.parent
    )
    pythonpath = expected_regrade.pop(0)

    agent, regrade = harbor_commands(loaded, make_manifest(), "pro-go", STAMP, 1)

    # suss runs each command with its own harbor in place of the first token.
    assert [str(harbor), *agent.argv[1:]] == expected_agent
    assert [str(harbor), *regrade.argv[1:]] == expected_regrade
    name, _, value = pythonpath.partition("=")
    assert regrade.env == {name: value}


def test_the_stamp_is_utc_in_basic_iso_form():
    assert stamp(datetime(2026, 10, 31, 14, 25, 0, tzinfo=timezone.utc)) == STAMP


def test_a_start_time_in_another_zone_is_converted_to_utc():
    sydney = timezone(timedelta(hours=11))

    assert stamp(datetime(2026, 11, 1, 1, 25, 0, tzinfo=sydney)) == STAMP


def test_a_start_time_with_no_zone_is_refused():
    with pytest.raises(ValueError, match="time zone"):
        stamp(datetime(2026, 10, 31, 14, 25, 0))


def test_the_jobs_are_named_from_the_stamp_and_the_trial_number(tmp_path):
    agent, regrade = harbor_commands(make_config(tmp_path), make_manifest(), "pro-go", STAMP, 3)

    assert agent.argv[agent.argv.index("--job-name") + 1] == f"{STAMP}-3"
    assert regrade.argv[regrade.argv.index("--job-name") + 1] == f"{STAMP}-3-regrade"


def test_the_regrade_replays_the_agent_job_it_follows(tmp_path):
    agent, regrade = harbor_commands(make_config(tmp_path), make_manifest(), "pro-go", STAMP, 2)

    jobs_dir = agent.argv[agent.argv.index("--jobs-dir") + 1]
    job_name = agent.argv[agent.argv.index("--job-name") + 1]
    # Harbor runs in the directory holding suss.toml, so --jobs-dir resolves from there.
    assert Path(harbor_parses(regrade.argv)["source_job"]) == tmp_path / jobs_dir / job_name
    assert regrade.argv[regrade.argv.index("--jobs-dir") + 1] == jobs_dir


def test_the_regrade_names_its_source_job_by_an_absolute_path_from_the_suss_toml_directory(
    tmp_path,
):
    _, regrade = harbor_commands(make_config(tmp_path), make_manifest(), "pro-go", STAMP, 1)

    source_job = harbor_parses(regrade.argv)["source_job"]
    assert source_job == f"{tmp_path}/results/pro-go/peer-review/{STAMP}-1"
    assert Path(source_job).is_absolute()


def test_a_check_takes_the_check_lane_and_writes_under_checks(tmp_path):
    agent, regrade = harbor_commands(
        make_config(tmp_path), make_manifest(), "pro-go", STAMP, 1, check=True
    )

    for command in (agent, regrade):
        assert command.argv[command.argv.index("-p") + 1] == "lanes/pro-go-check"
        assert command.argv[command.argv.index("--jobs-dir") + 1] == "checks/pro-go/peer-review"
    assert harbor_parses(regrade.argv)["source_job"] == f"{tmp_path}/checks/pro-go/peer-review/{STAMP}-1"


def test_both_jobs_take_the_environment_and_concurrency_from_the_config(tmp_path):
    agent, regrade = harbor_commands(
        make_config(tmp_path, environment="modal", concurrency=4), make_manifest(), "pro-go", STAMP, 1
    )

    for command in (agent, regrade):
        assert command.argv[:10] == [
            "harbor", "run", "-p", "lanes/pro-go", "-e", "modal", "-n", "4", "-k", "1",
        ]


def test_the_agent_job_allows_each_manifest_host_in_order_and_no_others(tmp_path):
    hosts = ("b.example.com", "a.example.com", "10.0.0.0/8")

    agent, _ = harbor_commands(make_config(tmp_path), make_manifest(hosts), "pro-go", STAMP, 1)

    allowed = [token for flag, token in zip(agent.argv, agent.argv[1:]) if flag == "--allow-agent-host"]
    assert allowed == list(hosts)


def test_a_harness_with_no_allowed_hosts_gets_no_allow_agent_host(tmp_path):
    agent, _ = harbor_commands(make_config(tmp_path), make_manifest(()), "pro-go", STAMP, 1)

    assert agent.argv == [
        "harbor", "run", "-p", "lanes/pro-go", "-e", "docker", "-n", "16", "-k", "1",
        "-a", "suss.agent:SussAgent", "--ak", "arm=peer-review", "--ak", "config=suss.toml",
        "--disable-verification",
        "--jobs-dir", "results/pro-go/peer-review", "--job-name", f"{STAMP}-1",
    ]


def test_the_regrade_runs_the_verifier(tmp_path):
    _, regrade = harbor_commands(
        make_config(tmp_path), make_manifest(), "pro-go", STAMP, 1, ["--max-retries", "2"]
    )

    # Its verifier's reward is the trial's result, and with verification disabled it records none.
    assert "--disable-verification" not in regrade.argv
    assert "--enable-verification" not in regrade.argv


def test_the_regrade_allows_no_agent_host(tmp_path):
    _, regrade = harbor_commands(make_config(tmp_path), make_manifest(), "pro-go", STAMP, 1)

    assert regrade.argv[0] == "harbor"
    assert "--allow-agent-host" not in regrade.argv


def test_arguments_after_the_separator_go_to_the_agent_job_only(tmp_path):
    extra = ["--max-retries", "2", "--ae", "FOO=bar"]

    agent, regrade = harbor_commands(make_config(tmp_path), make_manifest(), "pro-go", STAMP, 1, extra)
    plain_agent, plain_regrade = harbor_commands(make_config(tmp_path), make_manifest(), "pro-go", STAMP, 1)

    assert agent.argv == plain_agent.argv + extra
    assert regrade == plain_regrade


def test_a_refused_option_as_another_options_value_passes_through(tmp_path):
    extra = ["--extra-instruction", "-k", "--retry-include=--job-name"]

    agent, _ = harbor_commands(make_config(tmp_path), make_manifest(), "pro-go", STAMP, 1, extra)

    assert agent.argv[-3:] == extra


@pytest.mark.parametrize(
    "extra",
    [
        ["-p", "lanes/pro-go"],
        ["--path", "elsewhere"],
        ["--path=elsewhere"],
        ["-pelsewhere"],
        ["-e", "docker"],
        ["--env=modal"],
        ["-n", "16"],
        ["--n-concurrent", "4"],
        ["-k", "1"],
        ["-k2"],
        ["--n-attempts=2"],
        ["-a", "suss.agent:SussAgent"],
        ["--agent", "other"],
        ["-m", "replay"],
        ["--model=x"],
        ["--ak", "arm=peer-review"],
        ["--agent-kwarg", "x=1"],
        ["--allow-agent-host", "api.anthropic.com"],
        ["--allow-agent-host=example.com"],
        ["--jobs-dir", "results/pro-go/peer-review"],
        ["--jobs-dir=elsewhere"],
        ["-o", "elsewhere"],
        ["-oelsewhere"],
        ["--job-name", f"{STAMP}-1"],
        ["--job-name=other"],
        ["--disable-verification"],
        ["--enable-verification"],
        ["--max-retries", "2", "-k", "2"],
    ],
)
def test_an_option_suss_sets_is_refused_in_every_spelling_whatever_its_value(extra, tmp_path):
    # Harbor takes the last of a repeated option, so any of these would win over suss's own.
    with pytest.raises(ValueError, match="can't be passed after --"):
        harbor_commands(make_config(tmp_path), make_manifest(), "pro-go", STAMP, 1, extra)


def harbor_run_options():
    run = typer.main.get_command(harbor_app).commands["run"]
    return run, run.params


def options_suss_sets(tmp_path) -> set[str]:
    run, _ = harbor_run_options()
    agent, regrade = harbor_commands(make_config(tmp_path), make_manifest(), "pro-go", STAMP, 1)
    emitted = set()
    for command in (agent, regrade):
        _, _, found = run.make_parser(run.context_class(run)).parse_args(command.argv[2:])
        emitted |= {param.name for param in found}
    return emitted


def refused_options(tmp_path) -> set[str]:
    _, params = harbor_run_options()
    refused = set()
    for param in params:
        extra = [param.opts[0]] if param.is_flag else [param.opts[0], "1"]
        try:
            harbor_commands(make_config(tmp_path), make_manifest(), "pro-go", STAMP, 1, extra)
        except ValueError as error:
            if str(error).startswith(param.opts[0]):
                refused.add(param.name)
    return refused


def test_every_option_suss_sets_on_either_job_is_refused(tmp_path):
    emitted = options_suss_sets(tmp_path)

    assert len(emitted) == 11
    assert emitted <= refused_options(tmp_path)


def test_the_options_refused_beyond_those_suss_sets_are_these(tmp_path):
    # Each selects other tasks or another agent, or keeps the agent job from leaving a
    # local trial, so its regrade would find no patch and score 0.
    assert refused_options(tmp_path) - options_suss_sets(tmp_path) == {
        "config_path",
        "agent_import_path",
        "environment_import_path",
        "dataset_name_version",
        "registry_url",
        "registry_path",
        "repo",
        "task_git_url",
        "task_git_commit_id",
        "task_ref",
        "dataset_task_names",
        "dataset_exclude_task_names",
        "n_tasks",
        "user_agent_name",
        "user_model",
        "user_agent_kwargs",
        "user_persona_path",
        "user_prompt_template_path",
        "bridge_kind",
        "bridge_prompt_path",
        "bridge_kwargs",
        "install_only",
        "launch",
        "dry_run",
        "init",
        "print_config",
    }


@pytest.mark.parametrize(
    "extra, message",
    [
        (
            ["-c", "job.yaml"],
            "-c/--config can't be passed after --: a job config file can change anything"
            " suss sets on the job",
        ),
        (
            ["-t", "org/task"],
            "-t/--task can't be passed after --: a run covers every task in its lanes,"
            " and the regrade grades them all",
        ),
        (
            ["-i", "instance_*"],
            "-i/--include-task-name can't be passed after --: a run covers every task in its"
            " lanes, and the regrade grades them all",
        ),
        (
            ["-x", "instance_*"],
            "-x/--exclude-task-name can't be passed after --: a run covers every task in its"
            " lanes, and the regrade grades them all",
        ),
        (
            ["-l", "5"],
            "-l/--n-tasks can't be passed after --: a run covers every task in its lanes,"
            " and the regrade grades them all",
        ),
        (
            ["-d", "swe-bench-pro@2"],
            "-d/--dataset can't be passed after --: a run covers every task in its lanes,"
            " and the regrade grades them all",
        ),
        (
            ["--agent-import-path", "x:Y"],
            "--agent-import-path can't be passed after --: suss runs every agent job with"
            " SussAgent",
        ),
        (
            ["--environment-import-path", "x:Y"],
            "--environment-import-path can't be passed after --: set environment in suss.toml",
        ),
        (
            ["--user-agent", "x"],
            "--user-agent can't be passed after --: suss runs SussAgent alone, with no"
            " simulated user",
        ),
        (
            ["--install-only"],
            "--install-only can't be passed after --: it skips the agent run, so the regrade"
            " would find no patch to grade",
        ),
        (
            ["--launch"],
            "--launch can't be passed after --: suss runs each agent job locally, where its"
            " regrade can read it",
        ),
        (
            ["--init"],
            "--init can't be passed after --: it stops harbor before the job runs;"
            " DRY_RUN=1 prints suss's commands instead",
        ),
    ],
)
def test_an_option_that_changes_what_the_agent_job_runs_says_why_it_is_refused(
    extra, message, tmp_path
):
    with pytest.raises(ValueError) as refused:
        harbor_commands(make_config(tmp_path), make_manifest(), "pro-go", STAMP, 1, extra)

    assert str(refused.value) == message


@pytest.mark.parametrize(
    "extra, word",
    [
        (["stray"], "stray"),
        (["--max-retries", "2", "stray"], "stray"),
        (["--", "x"], "x"),
        (["--", "-k", "2"], "-k"),
    ],
)
def test_a_positional_argument_is_refused_by_name(extra, word, tmp_path):
    with pytest.raises(ValueError) as refused:
        harbor_commands(make_config(tmp_path), make_manifest(), "pro-go", STAMP, 1, extra)

    assert str(refused.value) == f"{word} can't be passed after --: harbor run takes only options"


def test_an_options_value_is_not_taken_for_a_positional_argument(tmp_path):
    extra = ["--max-retries", "2", "--ae=FOO=bar", "--retry-include", "stray"]

    agent, _ = harbor_commands(make_config(tmp_path), make_manifest(), "pro-go", STAMP, 1, extra)

    assert agent.argv[-5:] == extra


@pytest.mark.parametrize(
    "extra, message",
    [
        (["-p", "x"], "-p/--path can't be passed after --: suss gives each job its lane's directory"),
        (["-e", "x"], "-e/--env can't be passed after --: set environment in suss.toml"),
        (["-n", "1"], "-n/--n-concurrent can't be passed after --: set concurrency in suss.toml"),
        (
            ["-k", "2"],
            "-k/--n-attempts can't be passed after --: suss runs one trial per agent job,"
            " and k in suss.toml sets how many jobs",
        ),
        (["-a", "x"], "-a/--agent can't be passed after --: suss runs every agent job with SussAgent"),
        (
            ["-m", "x"],
            "-m/--model can't be passed after --: set the arm's model in its params in suss.toml",
        ),
        (
            ["--ak", "x=1"],
            "--ak/--agent-kwarg can't be passed after --: set the arm's params in suss.toml",
        ),
        (
            ["--allow-agent-host", "x"],
            "--allow-agent-host can't be passed after --: list the hosts in allowed_hosts"
            " in the harness's manifest",
        ),
        (
            ["-o", "x"],
            "-o/--jobs-dir can't be passed after --: suss writes each job under"
            " results/<lane>/<arm>/, or checks/ with --check",
        ),
        (
            ["--disable-verification"],
            "--disable-verification/--enable-verification can't be passed after --: suss grades"
            " each trial by its regrade alone, so the agent job never runs the verifier",
        ),
        (
            ["--enable-verification"],
            "--disable-verification/--enable-verification can't be passed after --: suss grades"
            " each trial by its regrade alone, so the agent job never runs the verifier",
        ),
        (
            ["--job-name", "x"],
            "--job-name can't be passed after --: suss names each job from the run's stamp"
            " and its trial number",
        ),
    ],
)
def test_a_refused_option_says_where_its_setting_comes_from(extra, message, tmp_path):
    with pytest.raises(ValueError) as refused:
        harbor_commands(make_config(tmp_path), make_manifest(), "pro-go", STAMP, 1, extra)

    assert str(refused.value) == message


@pytest.mark.parametrize(
    "extra", [["--allow-agent-host", "example.com"], ["--allow-agent-host=example.com"]]
)
def test_allow_agent_host_is_refused_when_the_manifest_allows_no_hosts(extra, tmp_path):
    with pytest.raises(ValueError, match="^--allow-agent-host can't be passed after --"):
        harbor_commands(make_config(tmp_path), make_manifest(()), "pro-go", STAMP, 1, extra)


def test_an_option_harbor_does_not_know_is_refused(tmp_path):
    with pytest.raises(ValueError, match="--no-such-option"):
        harbor_commands(
            make_config(tmp_path), make_manifest(), "pro-go", STAMP, 1, ["--no-such-option"]
        )


def test_only_the_regrade_gets_pythonpath_and_it_is_absolute(tmp_path):
    agent, regrade = harbor_commands(make_config(tmp_path), make_manifest(), "pro-go", STAMP, 1)

    assert agent.env == {}
    assert regrade.env == {"PYTHONPATH": str(CHECKOUT / "v2" / "tooling")}
    assert Path(regrade.env["PYTHONPATH"]).is_absolute()
    assert regrade.argv[0] == "harbor"


@pytest.mark.parametrize(
    "arm",
    [
        "peer-review",
        "claude code",
        "123",
        "-1",
        "1.5",
        "1e5",
        "true",
        "false",
        "null",
        "True",
        "False",
        "None",
        "NaN",
        "Infinity",
        "-Infinity",
        "[1]",
        "{}",
        '"quoted"',
        " padded ",
        "a=b",
    ],
)
def test_every_ak_value_reaches_the_agent_as_the_string_it_was(arm, tmp_path):
    agent, regrade = harbor_commands(make_config(tmp_path, arm=arm), make_manifest(), "pro-go", STAMP, 1)

    assert harbor_parses(agent.argv) == {"arm": arm, "config": "suss.toml"}
    assert harbor_parses(regrade.argv) == {"source_job": f"{tmp_path}/results/pro-go/{arm}/{STAMP}-1"}


def test_an_ordinary_ak_value_is_not_quoted(tmp_path):
    agent, regrade = harbor_commands(make_config(tmp_path), make_manifest(), "pro-go", STAMP, 1)

    assert ak_values(agent.argv) == {"arm": "peer-review", "config": "suss.toml"}
    assert ak_values(regrade.argv) == {"source_job": f"{tmp_path}/results/pro-go/peer-review/{STAMP}-1"}


def test_an_arm_harbor_would_read_as_a_number_is_quoted_as_a_json_string(tmp_path):
    agent, _ = harbor_commands(make_config(tmp_path, arm="2026"), make_manifest(), "pro-go", STAMP, 1)

    assert ak_values(agent.argv)["arm"] == '"2026"'
