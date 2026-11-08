import dataclasses
import os
import re
import shlex
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import typer.main
from harbor.cli.main import app as harbor_app
from harbor.cli.utils import parse_kwargs

from suss import config
from suss.cli import harbor_commands, harbor_executable, stamp
from suss.config import Arm, Config, Judge
from suss.agents import SussCopilotCli

SPEC = Path(__file__).resolve().parent.parent / "docs" / "specs" / "suss-spec.md"
STAMP = "20261031T142500Z"
TASKS = Path("/work/tasks/swe-rebench-07-2026")


def make_config(
    root: Path, arm: str = "peer-review", environment: str = "docker", concurrency: int = 16
):
    return Config(
        path=root / "suss.toml",
        swe_rebench=TASKS,
        environment=environment,
        k=3,
        concurrency=concurrency,
        arm=Arm(
            name=arm,
            agent="suss.agents:SussCopilotCli",
            baseline=None,
            model=None,
            kwargs={},
        ),
        env={},
        judge=Judge(credit_budget=2000),
    )


def hosts(allowed_hosts: tuple[str, ...] = ("api.anthropic.com", "api.deepseek.com")):
    """The hosts an agent class would list in ALLOWED_HOSTS."""
    return allowed_hosts


def ak_values(argv: list[str]) -> dict[str, str]:
    """Each `--ak` token as it stands, keyed by the name before its `=`."""
    values = {}
    for flag, token in zip(argv, argv[1:]):
        if flag == "--ak":
            key, _, value = token.partition("=")
            values[key] = value
    return values


def harbor_parses(argv: list[str]) -> dict:
    """The `--ak` values as Harbor's own parser hands them to the agent."""
    return parse_kwargs([token for flag, token in zip(argv, argv[1:]) if flag == "--ak"])


def spec_commands(root: Path, env: Path, lane: str) -> list[list[str]]:
    """
    The `harbor run` commands the spec's `suss run` gives for `lane`, split into
    tokens, with `root` as the directory holding suss.toml, and `env` as the
    environment suss is installed in.
    """
    blocks = re.findall(r"```bash\n(.*?)```", SPEC.read_text(), re.DOTALL)
    found = [
        block for block in blocks
        if "-a suss.agents:" in block and f"--ak lane={lane}" in block
    ]
    assert len(found) == 1, f"expected one block with the {lane} agent job in {SPEC}, found {len(found)}"
    return [
        shlex.split(
            command.replace("<stamp>", STAMP)
            .replace("<dir>", shlex.quote(str(root)))
            .replace("<env>", shlex.quote(str(env)))
        )
        for command in found[0].replace("\\\n", " ").strip().split("\n\n")
    ]


def spec_config(tmp_path: Path, lane: str) -> Config:
    blocks = re.findall(r"```toml\n(.*?)```", SPEC.read_text(), re.DOTALL)
    examples = [block for block in blocks if "[arms." in block]
    assert len(examples) == 1, f"expected one example suss.toml in {SPEC}, found {len(examples)}"
    path = tmp_path / "suss" / "suss.toml"
    path.parent.mkdir()
    path.write_text(examples[0])
    return config.load(path, "copilot-gpt", lane)


@pytest.mark.parametrize(
    "lane, reads", [("rebench", ()), ("senior", ("ANTHROPIC_API_KEY",))]
)
def test_each_lanes_commands_match_the_spec_token_for_token(tmp_path, lane, reads):
    loaded = spec_config(tmp_path, lane)
    harbor = harbor_executable()
    expected = spec_commands(loaded.path.parent, harbor.parent.parent, lane)

    command = harbor_commands(loaded, SussCopilotCli.ALLOWED_HOSTS, lane, STAMP, 1, reads=reads)

    # suss runs each command with its own harbor in place of the first token.
    assert [[str(harbor), *command.argv[1:]]] == expected


def test_the_stamp_is_utc_in_basic_iso_form():
    assert stamp(datetime(2026, 10, 31, 14, 25, 0, tzinfo=timezone.utc)) == STAMP


def test_a_start_time_in_another_zone_is_converted_to_utc():
    sydney = timezone(timedelta(hours=11))

    assert stamp(datetime(2026, 11, 1, 1, 25, 0, tzinfo=sydney)) == STAMP


def test_a_start_time_with_no_zone_is_refused():
    with pytest.raises(ValueError, match="time zone"):
        stamp(datetime(2026, 10, 31, 14, 25, 0))


def test_the_job_is_named_from_the_stamp_and_the_trial_number(tmp_path):
    agent = harbor_commands(make_config(tmp_path), hosts(), "senior", STAMP, 3)

    assert agent.argv[agent.argv.index("--job-name") + 1] == f"{STAMP}-3"


def test_a_check_takes_the_check_lane_and_writes_under_checks(tmp_path):
    agent = harbor_commands(make_config(tmp_path), hosts(), "senior", STAMP, 1, check=True)

    assert agent.argv[agent.argv.index("-p") + 1] == "lanes/senior-check"
    assert agent.argv[agent.argv.index("--jobs-dir") + 1] == "checks/senior/peer-review"


def test_the_job_takes_the_environment_and_concurrency_from_the_config(tmp_path):
    agent = harbor_commands(
        make_config(tmp_path, environment="modal", concurrency=4), hosts(), "senior", STAMP, 1
    )

    assert agent.argv[:10] == [
        "harbor", "run", "-p", "lanes/senior", "-e", "modal", "-n", "4", "-k", "1",
    ]


def test_the_agent_job_allows_each_host_its_agent_class_lists_in_order_and_no_others(tmp_path):
    listed = ("b.example.com", "a.example.com", "10.0.0.0/8")

    agent = harbor_commands(make_config(tmp_path), hosts(listed), "rebench", STAMP, 1)

    allowed = [token for flag, token in zip(agent.argv, agent.argv[1:]) if flag == "--allow-agent-host"]
    assert allowed == list(listed)


def test_an_agent_with_no_allowed_hosts_gets_no_allow_agent_host(tmp_path):
    agent = harbor_commands(make_config(tmp_path), hosts(()), "senior", STAMP, 1)

    assert agent.argv == [
        "harbor", "run", "-p", "lanes/senior", "-e", "docker", "-n", "16", "-k", "1",
        "-a", "suss.agents:SussCopilotCli", "--ak", "arm=peer-review", "--ak", "suss_config=suss.toml",
        "--ak", "lane=senior",
        "--verifier", "suss.judge:JudgeVerifier", "--verifier-kwarg", "credit_budget=2000",
        "--jobs-dir", "results/senior/peer-review", "--job-name", f"{STAMP}-1",
    ]


def test_a_senior_agent_job_judges_and_grades_its_trials_in_suss_verifier_with_no_regrade(
    tmp_path,
):
    settings = with_dotenv(tmp_path, {"ANTHROPIC_API_KEY": "sk-ant-1", "COPILOT_GITHUB_TOKEN": "gh-1"})
    settings = dataclasses.replace(
        settings, judge=Judge(credit_budget=2000.5, model="gpt-6.1-sol", effort="")
    )

    agent = harbor_commands(
        settings, hosts(), "senior", STAMP, 2, ["--max-retries", "1"], reads=READS,
        verifier_reads=READS,
    )

    assert agent.argv == [
        "harbor", "run", "-y", "-p", "lanes/senior", "-e", "docker", "-n", "16", "-k", "1",
        "-a", "suss.agents:SussCopilotCli", "--ak", "arm=peer-review", "--ak", "suss_config=suss.toml",
        "--ak", "lane=senior",
        "--allow-agent-host", "api.anthropic.com", "--allow-agent-host", "api.deepseek.com",
        "--verifier", "suss.judge:JudgeVerifier",
        "--verifier-kwarg", "credit_budget=2000.5", "--verifier-kwarg", "model=gpt-6.1-sol",
        "--verifier-kwarg", "effort=",
        "--jobs-dir", "results/senior/peer-review", "--job-name", f"{STAMP}-2",
        "--max-retries", "1",
    ]
    assert parse_kwargs(
        [token for flag, token in zip(agent.argv, agent.argv[1:]) if flag == "--verifier-kwarg"]
    ) == {"credit_budget": 2000.5, "model": "gpt-6.1-sol", "effort": ""}
    assert not any("gh-1" in token for token in agent.argv), "the judge's token is an argument"


def test_a_senior_agent_job_gives_its_verifier_no_key_for_seniors_own_judges(tmp_path):
    settings = with_dotenv(
        tmp_path,
        {
            "ANTHROPIC_API_KEY": "sk-ant-1", "OPENAI_API_KEY": "sk-oa-1", "PORTKEY_API_KEY": "pk-1",
            "SSB_OVERRIDE_ALL_JUDGE_MODEL": "anthropic/x", "COPILOT_GITHUB_TOKEN": "gh-1",
        },
    )
    reads = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "PORTKEY_API_KEY", "SSB_OVERRIDE_ALL_JUDGE_MODEL")

    agent = harbor_commands(
        settings, hosts(), "senior", STAMP, 1, reads=reads, verifier_reads=reads
    )

    # They would run senior's judges and validation agent on API keys, at about $100 a run.
    assert agent.given == {"SSB_OVERRIDE_ALL_JUDGE_MODEL": "anthropic/x"}
    assert agent.withheld == (*reads, "COPILOT_GITHUB_TOKEN")


def test_a_rebench_agent_job_grades_its_trials_in_suss_verifier_with_no_regrade(tmp_path):
    agent = harbor_commands(make_config(tmp_path), hosts(()), "rebench", STAMP, 1)

    assert agent.argv == [
        "harbor", "run", "-p", "lanes/rebench", "-e", "docker", "-n", "16", "-k", "1",
        "-a", "suss.agents:SussCopilotCli", "--ak", "arm=peer-review", "--ak", "suss_config=suss.toml",
        "--ak", "lane=rebench", "--verifier", "suss.replay:ReplayVerifier",
        "--jobs-dir", "results/rebench/peer-review", "--job-name", f"{STAMP}-1",
    ]


GRADING_OPTIONS = ("--ve", "--verifier-env", "--ae", "--agent-env", "--ek", "--environment-kwarg")


def test_a_rebench_agent_job_takes_grading_keys_from_harbors_environment_never_its_arguments(
    tmp_path,
):
    settings = with_dotenv(tmp_path, {"GRADER_KEY": "sk-grade-secret", "SHARED": "sk-both"})

    agent = harbor_commands(
        settings,
        hosts(),
        "rebench",
        STAMP,
        1,
        reads=("GRADER_KEY", "SHARED"),
        verifier_reads=("GRADER_KEY",),
    )

    # Harbor fills [verifier.env] from its own environment, and only test.sh's command gets it.
    assert agent.given == {"GRADER_KEY": "sk-grade-secret"}
    assert agent.withheld == ("GRADER_KEY", "SHARED")
    assert not any(token.partition("=")[0] in GRADING_OPTIONS for token in agent.argv)
    assert not any("sk-" in token for token in agent.argv)


def test_arguments_after_the_separator_go_at_the_end_of_the_agent_job(tmp_path):
    extra = ["--max-retries", "2", "--ek", "foo=bar"]

    agent = harbor_commands(make_config(tmp_path), hosts(), "senior", STAMP, 1, extra)
    plain = harbor_commands(make_config(tmp_path), hosts(), "senior", STAMP, 1)

    assert agent.argv == plain.argv + extra


def test_a_refused_option_as_another_options_value_passes_through(tmp_path):
    extra = ["--extra-instruction", "-k", "--retry-include=--job-name"]

    agent = harbor_commands(make_config(tmp_path), hosts(), "rebench", STAMP, 1, extra)

    assert agent.argv[-3:] == extra


@pytest.mark.parametrize(
    "extra",
    [
        ["-p", "lanes/rebench"],
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
        ["-a", "suss.agents:SussCopilotCli"],
        ["--agent", "other"],
        ["-m", "replay"],
        ["--model=x"],
        ["--ak", "arm=peer-review"],
        ["--agent-kwarg", "x=1"],
        ["--allow-agent-host", "api.anthropic.com"],
        ["--allow-agent-host=example.com"],
        ["--jobs-dir", "results/rebench/peer-review"],
        ["--jobs-dir=elsewhere"],
        ["-o", "elsewhere"],
        ["-oelsewhere"],
        ["--job-name", f"{STAMP}-1"],
        ["--job-name=other"],
        ["--disable-verification"],
        ["--enable-verification"],
        ["--verifier", "other:Verifier"],
        ["--verifier-import-path=other:Verifier"],
        ["--verifier-kwarg", "artifacts=/tmp"],
        ["--max-retries", "2", "-k", "2"],
    ],
)
def test_an_option_suss_sets_is_refused_in_every_spelling_whatever_its_value(extra, tmp_path):
    # Harbor takes the last of a repeated option, so any of these would win over suss's own.
    with pytest.raises(ValueError, match="can't be passed after --"):
        harbor_commands(make_config(tmp_path), hosts(), "rebench", STAMP, 1, extra)


def harbor_run_options():
    run = typer.main.get_command(harbor_app).commands["run"]
    return run, run.params


def options_suss_sets(tmp_path) -> set[str]:
    run, _ = harbor_run_options()
    # -y only on a lane whose tasks read Harbor's environment.
    commands = [
        harbor_commands(make_config(tmp_path), hosts(), "rebench", STAMP, 1),
        harbor_commands(
            make_config(tmp_path), hosts(), "senior", STAMP, 1, reads=("ANTHROPIC_API_KEY",)
        ),
    ]
    emitted = set()
    for command in commands:
        _, _, found = run.make_parser(run.context_class(run)).parse_args(command.argv[2:])
        emitted |= {param.name for param in found}
    return emitted


def refused_options(tmp_path) -> set[str]:
    _, params = harbor_run_options()
    refused = set()
    for param in params:
        extra = [param.opts[0]] if param.is_flag else [param.opts[0], "1"]
        try:
            harbor_commands(make_config(tmp_path), hosts(), "rebench", STAMP, 1, extra)
        except ValueError as error:
            if str(error).startswith(param.opts[0]):
                refused.add(param.name)
    return refused


def test_every_option_suss_sets_on_the_job_is_refused(tmp_path):
    emitted = options_suss_sets(tmp_path)

    assert len(emitted) == 12
    assert emitted <= refused_options(tmp_path)


def test_the_options_refused_beyond_those_suss_sets_are_these(tmp_path):
    # Each selects other tasks, another agent, model, effort, endpoint or verifier, stops the
    # job grading its trials, widens the verifier's network, or keeps it from leaving a local
    # trial for its verifier to read.
    assert refused_options(tmp_path) - options_suss_sets(tmp_path) == {
        "model_names",
        "effort",
        "base_url",
        "api_key_name",
        "api_format",
        "skip_model_parsing",
        "allow_verifier_hosts",
        "diff_sources",
        "disable_verification",
        "config_path",
        "agent_import_path",
        "verifier_import_path",
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
        "print_config",
        "env_file",
        "agent_env",
    }


# The verifier's environment

READS = ("ANTHROPIC_API_KEY", "SSB_OVERRIDE_ALL_JUDGE_MODEL", "TIMEOUT_MULTIPLIER")


def with_dotenv(tmp_path: Path, env: dict[str, str]) -> Config:
    return dataclasses.replace(make_config(tmp_path), env=env)


def test_the_job_answers_harbors_question_when_the_tasks_read_its_environment(tmp_path):
    agent = harbor_commands(make_config(tmp_path), hosts(), "senior", STAMP, 1, reads=READS)

    # Without a terminal, Harbor reads no answer and aborts the job.
    assert agent.argv[:3] == ["harbor", "run", "-y"]


def test_the_job_answers_no_question_harbor_does_not_ask(tmp_path):
    agent = harbor_commands(make_config(tmp_path), hosts(), "senior", STAMP, 1)

    assert "-y" not in agent.argv


def test_the_agent_job_withholds_every_variable_the_tasks_read_and_is_given_none_they_read_elsewhere(
    tmp_path,
):
    settings = with_dotenv(tmp_path, {"SSB_OVERRIDE_ALL_JUDGE_MODEL": "anthropic/x"})

    agent = harbor_commands(settings, hosts(), "senior", STAMP, 1, reads=READS)

    assert agent.withheld == (*READS, "COPILOT_GITHUB_TOKEN")
    assert agent.given == {}


def test_the_agent_job_is_given_the_values_env_sets_for_what_its_verifier_alone_reads(tmp_path):
    settings = with_dotenv(
        tmp_path,
        {"SSB_OVERRIDE_ALL_JUDGE_MODEL": "anthropic/x", "TIMEOUT_MULTIPLIER": "", "DEEPSEEK_API_KEY": "ds-1"},
    )

    agent = harbor_commands(
        settings, hosts(), "senior", STAMP, 1, reads=READS, verifier_reads=READS
    )

    assert agent.withheld == (*READS, "COPILOT_GITHUB_TOKEN")
    assert agent.given == {"SSB_OVERRIDE_ALL_JUDGE_MODEL": "anthropic/x"}


def test_a_commands_values_from_env_never_show_in_its_repr(tmp_path):
    settings = with_dotenv(tmp_path, {"GRADER_KEY": "sk-grade-secret"})

    agent = harbor_commands(
        settings, hosts(), "rebench", STAMP, 1, reads=("GRADER_KEY",),
        verifier_reads=("GRADER_KEY",),
    )

    assert agent.given
    assert "sk-grade-secret" not in repr(agent)


def test_yes_after_the_separator_is_refused_as_an_option_suss_sets(tmp_path):
    with pytest.raises(ValueError) as refused:
        harbor_commands(make_config(tmp_path), hosts(), "rebench", STAMP, 1, ["--yes"])

    assert str(refused.value) == (
        "-y/--yes can't be passed after --: suss answers Harbor's question itself, on a lane "
        "whose tasks read Harbor's environment"
    )


def test_an_env_file_after_the_separator_is_refused(tmp_path):
    with pytest.raises(ValueError) as refused:
        harbor_commands(
            make_config(tmp_path), hosts(), "rebench", STAMP, 1, ["--env-file", ".env"]
        )

    assert str(refused.value) == (
        "--env-file can't be passed after --: it would give the agent job every variable its "
        "tasks read from Harbor's environment; suss gives it only those .env sets that reach its "
        "verifier alone"
    )


def test_sharing_with_an_organization_is_refused_where_suss_answers_harbors_question(tmp_path):
    extra = ["--upload", "--share", "acme"]

    with pytest.raises(ValueError) as refused:
        harbor_commands(make_config(tmp_path), hosts(), "senior", STAMP, 1, extra, reads=READS)

    assert str(refused.value) == (
        "--share can't be passed after -- on senior: suss passes -y there, which would "
        "share the job with an organization you are not a member of without asking"
    )


def test_sharing_with_an_organization_passes_through_where_harbor_asks_no_question(tmp_path):
    extra = ["--upload", "--share", "acme"]

    agent = harbor_commands(make_config(tmp_path), hosts(), "rebench", STAMP, 1, extra)

    assert agent.argv[-3:] == extra


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
            "-t/--task can't be passed after --: a run covers every task in its lanes",
        ),
        (
            ["-i", "instance_*"],
            "-i/--include-task-name can't be passed after --: a run covers every task in its"
            " lanes",
        ),
        (
            ["-x", "instance_*"],
            "-x/--exclude-task-name can't be passed after --: a run covers every task in its"
            " lanes",
        ),
        (
            ["-l", "5"],
            "-l/--n-tasks can't be passed after --: a run covers every task in its lanes",
        ),
        (
            ["-d", "swe-bench-pro@2"],
            "-d/--dataset can't be passed after --: a run covers every task in its lanes",
        ),
        (
            ["--agent-import-path", "x:Y"],
            "--agent-import-path can't be passed after --: set the arm's agent in suss.toml",
        ),
        (
            ["--environment-import-path", "x:Y"],
            "--environment-import-path can't be passed after --: set environment in suss.toml",
        ),
        (
            ["--user-agent", "x"],
            "--user-agent can't be passed after --: suss runs the arm's agent alone, with no"
            " simulated user",
        ),
        (
            ["--install-only"],
            "--install-only can't be passed after --: it skips the agent run, so the verifier"
            " would find no patch to grade",
        ),
        (
            ["--launch"],
            "--launch can't be passed after --: suss runs each agent job locally, where its"
            " verifier reads the trial on the host",
        ),
        (
            ["--print-config"],
            "--print-config can't be passed after --: it stops harbor before the job runs;"
            " DRY_RUN=1 prints suss's commands instead",
        ),
        (
            ["--base-url", "https://example.com"],
            "--base-url can't be passed after --: the arm's agent reaches its model through"
            " the harness's own service, as it ships",
        ),
        (
            ["--allow-verifier-host", "example.com"],
            "--allow-verifier-host can't be passed after --: suss grades each trial with the"
            " network its task gives the verifier, and no more",
        ),
    ],
)
def test_an_option_that_changes_what_the_agent_job_runs_says_why_it_is_refused(
    extra, message, tmp_path
):
    with pytest.raises(ValueError) as refused:
        harbor_commands(make_config(tmp_path), hosts(), "rebench", STAMP, 1, extra)

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
        harbor_commands(make_config(tmp_path), hosts(), "rebench", STAMP, 1, extra)

    assert str(refused.value) == f"{word} can't be passed after --: harbor run takes only options"


def test_an_options_value_is_not_taken_for_a_positional_argument(tmp_path):
    extra = ["--max-retries", "2", "--ek=foo=bar", "--retry-include", "stray"]

    agent = harbor_commands(make_config(tmp_path), hosts(), "rebench", STAMP, 1, extra)

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
        (["-a", "x"], "-a/--agent can't be passed after --: set the arm's agent in suss.toml"),
        (
            ["-m", "x"],
            "-m/--model can't be passed after --: set the arm's model in suss.toml",
        ),
        (
            ["--ak", "x=1"],
            "--ak/--agent-kwarg can't be passed after --: set the arm's kwargs in suss.toml",
        ),
        (
            ["--allow-agent-host", "x"],
            "--allow-agent-host can't be passed after --: suss allows the hosts the arm's agent"
            " class lists in ALLOWED_HOSTS",
        ),
        (
            ["-o", "x"],
            "-o/--jobs-dir can't be passed after --: suss writes each job under"
            " results/<lane>/<arm>/, or checks/ with --check",
        ),
        (
            ["--disable-verification"],
            "--disable-verification/--enable-verification can't be passed after --: suss grades"
            " each trial in the agent job's own verifier, in a fresh sandbox",
        ),
        (
            ["--enable-verification"],
            "--disable-verification/--enable-verification can't be passed after --: suss grades"
            " each trial in the agent job's own verifier, in a fresh sandbox",
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
        harbor_commands(make_config(tmp_path), hosts(), "rebench", STAMP, 1, extra)

    assert str(refused.value) == message


@pytest.mark.parametrize(
    "extra", [["--allow-agent-host", "example.com"], ["--allow-agent-host=example.com"]]
)
def test_allow_agent_host_is_refused_when_the_manifest_allows_no_hosts(extra, tmp_path):
    with pytest.raises(ValueError, match="^--allow-agent-host can't be passed after --"):
        harbor_commands(make_config(tmp_path), hosts(()), "rebench", STAMP, 1, extra)


def test_an_option_harbor_does_not_know_is_refused(tmp_path):
    with pytest.raises(ValueError, match="--no-such-option"):
        harbor_commands(
            make_config(tmp_path), hosts(), "rebench", STAMP, 1, ["--no-such-option"]
        )


# What Harbor does with --verifier, in the process `harbor` starts in.
LOAD_THE_VERIFIER = """
import sys
from harbor.utils.import_path import import_class
from harbor.verifier.base import BaseVerifier

found = import_class(sys.argv[1], base=BaseVerifier, label="verifier")
print(found.__module__, found.__name__)
"""


@pytest.mark.parametrize(
    "lane, verifier",
    [("rebench", "suss.replay ReplayVerifier"), ("senior", "suss.judge JudgeVerifier")],
)
def test_the_harbor_suss_runs_loads_each_lanes_verifier_with_no_pythonpath(tmp_path, lane, verifier):
    agent = harbor_commands(make_config(tmp_path), hosts(), lane, STAMP, 1)
    python = harbor_executable().read_text().splitlines()[0].removeprefix("#!")
    env = {name: value for name, value in os.environ.items() if name != "PYTHONPATH"}

    loaded = subprocess.run(
        [python, "-c", LOAD_THE_VERIFIER, agent.argv[agent.argv.index("--verifier") + 1]],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )

    assert loaded.returncode == 0, loaded.stderr
    assert loaded.stdout == f"{verifier}\n"


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
    agent = harbor_commands(make_config(tmp_path, arm=arm), hosts(), "senior", STAMP, 1)

    assert harbor_parses(agent.argv) == {"arm": arm, "suss_config": "suss.toml", "lane": "senior"}


def test_an_ordinary_ak_value_is_not_quoted(tmp_path):
    agent = harbor_commands(make_config(tmp_path), hosts(), "senior", STAMP, 1)

    assert ak_values(agent.argv) == {"arm": "peer-review", "suss_config": "suss.toml", "lane": "senior"}


def test_an_arm_harbor_would_read_as_a_number_is_quoted_as_a_json_string(tmp_path):
    agent = harbor_commands(make_config(tmp_path, arm="2026"), hosts(), "rebench", STAMP, 1)

    assert ak_values(agent.argv)["arm"] == '"2026"'


def test_the_arms_agent_model_and_kwargs_reach_harbor_each_of_its_own_type(tmp_path):
    settings = make_config(tmp_path)
    kwargs = {"version": "1.0.105", "reasoning_effort": "high", "rounds": 3, "strict": True,
              "ratio": 0.5, "label": "007", "spaced": " it "}
    arm = dataclasses.replace(settings.arm, model="gpt-6.1-sol", kwargs=kwargs)

    agent = harbor_commands(dataclasses.replace(settings, arm=arm), hosts(), "rebench", STAMP, 1)

    at = agent.argv.index("-a")
    assert agent.argv[at + 1 : at + 4] == ["suss.agents:SussCopilotCli", "-m", "gpt-6.1-sol"]
    parsed = harbor_parses(agent.argv)
    assert {key: parsed[key] for key in kwargs} == kwargs
    assert {key: type(parsed[key]) for key in kwargs} == {key: type(v) for key, v in kwargs.items()}


def test_an_agent_env_option_is_refused_since_it_would_reach_the_agents_commands(tmp_path):
    with pytest.raises(ValueError) as refused:
        harbor_commands(make_config(tmp_path), hosts(), "rebench", STAMP, 1, ["--ae", "GH_TOKEN=x"])

    assert str(refused.value) == (
        "--ae/--agent-env can't be passed after --: the arm's agent takes only the variables its"
        " class names, from .env, and an --ae value would reach every command it runs"
    )
