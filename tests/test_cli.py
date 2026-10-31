import re
import shlex
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from harbor.cli.utils import parse_kwargs

from suss import config
from suss.cli import harbor_commands, stamp
from suss.config import Arm, Config
from suss.harness import Manifest

SPEC = Path(__file__).resolve().parent.parent / "docs" / "specs" / "suss-spec.md"
STAMP = "20261031T142500Z"
CHECKOUT = Path("/work/SWE-bench_Pro-os")


def make_config(arm: str = "peer-review", environment: str = "docker", concurrency: int = 16):
    return Config(
        path=Path("/work/suss/suss.toml"),
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


def spec_commands() -> tuple[list[str], list[str]]:
    """The two `harbor run` commands from the spec's `suss run`, split into tokens."""
    blocks = re.findall(r"```bash\n(.*?)```", SPEC.read_text(), re.DOTALL)
    found = [block for block in blocks if "-a suss.agent:SussAgent" in block]
    assert len(found) == 1, f"expected one block with the agent job in {SPEC}, found {len(found)}"
    agent, regrade = found[0].replace("\\\n", " ").strip().split("\n\n")
    return (
        shlex.split(agent.replace("<stamp>", STAMP)),
        shlex.split(regrade.replace("<stamp>", STAMP)),
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
    expected_agent, expected_regrade = spec_commands()
    pythonpath = expected_regrade.pop(0)

    agent, regrade = harbor_commands(loaded, make_manifest(), "pro-go", STAMP, 1)

    assert agent.argv == expected_agent
    assert regrade.argv == expected_regrade
    # The spec writes the checkout's path as suss.toml does, relative to suss.toml.
    name, _, value = pythonpath.partition("=")
    assert regrade.env == {name: str((loaded.path.parent / value).resolve())}


def test_the_stamp_is_utc_in_basic_iso_form():
    assert stamp(datetime(2026, 10, 31, 14, 25, 0, tzinfo=timezone.utc)) == STAMP


def test_a_start_time_in_another_zone_is_converted_to_utc():
    sydney = timezone(timedelta(hours=11))

    assert stamp(datetime(2026, 11, 1, 1, 25, 0, tzinfo=sydney)) == STAMP


def test_a_start_time_with_no_zone_is_refused():
    with pytest.raises(ValueError, match="time zone"):
        stamp(datetime(2026, 10, 31, 14, 25, 0))


def test_the_jobs_are_named_from_the_stamp_and_the_trial_number():
    agent, regrade = harbor_commands(make_config(), make_manifest(), "pro-go", STAMP, 3)

    assert agent.argv[agent.argv.index("--job-name") + 1] == f"{STAMP}-3"
    assert regrade.argv[regrade.argv.index("--job-name") + 1] == f"{STAMP}-3-regrade"


def test_the_regrade_replays_the_agent_job_it_follows():
    agent, regrade = harbor_commands(make_config(), make_manifest(), "pro-go", STAMP, 2)

    jobs_dir = agent.argv[agent.argv.index("--jobs-dir") + 1]
    job_name = agent.argv[agent.argv.index("--job-name") + 1]
    assert harbor_parses(regrade.argv)["source_job"] == f"{jobs_dir}/{job_name}"
    assert regrade.argv[regrade.argv.index("--jobs-dir") + 1] == jobs_dir


def test_a_check_takes_the_check_lane_and_writes_under_checks():
    agent, regrade = harbor_commands(
        make_config(), make_manifest(), "pro-go", STAMP, 1, check=True
    )

    for command in (agent, regrade):
        assert command.argv[command.argv.index("-p") + 1] == "lanes/pro-go-check"
        assert command.argv[command.argv.index("--jobs-dir") + 1] == "checks/pro-go/peer-review"
    assert harbor_parses(regrade.argv)["source_job"] == f"checks/pro-go/peer-review/{STAMP}-1"


def test_both_jobs_take_the_environment_and_concurrency_from_the_config():
    agent, regrade = harbor_commands(
        make_config(environment="modal", concurrency=4), make_manifest(), "pro-go", STAMP, 1
    )

    for command in (agent, regrade):
        assert command.argv[:10] == [
            "harbor", "run", "-p", "lanes/pro-go", "-e", "modal", "-n", "4", "-k", "1",
        ]


def test_the_agent_job_allows_each_manifest_host_in_order_and_no_others():
    hosts = ("b.example.com", "a.example.com", "10.0.0.0/8")

    agent, _ = harbor_commands(make_config(), make_manifest(hosts), "pro-go", STAMP, 1)

    allowed = [token for flag, token in zip(agent.argv, agent.argv[1:]) if flag == "--allow-agent-host"]
    assert allowed == list(hosts)


def test_a_harness_with_no_allowed_hosts_gets_no_allow_agent_host():
    agent, _ = harbor_commands(make_config(), make_manifest(()), "pro-go", STAMP, 1)

    assert agent.argv == [
        "harbor", "run", "-p", "lanes/pro-go", "-e", "docker", "-n", "16", "-k", "1",
        "-a", "suss.agent:SussAgent", "--ak", "arm=peer-review", "--ak", "config=suss.toml",
        "--jobs-dir", "results/pro-go/peer-review", "--job-name", f"{STAMP}-1",
    ]


def test_the_regrade_allows_no_agent_host():
    _, regrade = harbor_commands(make_config(), make_manifest(), "pro-go", STAMP, 1)

    assert regrade.argv[0] == "harbor"
    assert "--allow-agent-host" not in regrade.argv


def test_arguments_after_the_separator_go_to_the_agent_job_only():
    # `-m replay` is the regrade's own flag, so it must not be mistaken for one.
    extra = ["--max-retries", "2", "-m", "replay"]

    agent, regrade = harbor_commands(make_config(), make_manifest(), "pro-go", STAMP, 1, extra)
    plain_agent, plain_regrade = harbor_commands(make_config(), make_manifest(), "pro-go", STAMP, 1)

    assert agent.argv == plain_agent.argv + extra
    assert regrade == plain_regrade


@pytest.mark.parametrize(
    "extra",
    [
        ["--job-name", "other"],
        ["--job-name=other"],
        ["--jobs-dir", "elsewhere"],
        ["--jobs-dir=elsewhere"],
        ["-o", "elsewhere"],
        ["-oelsewhere"],
    ],
)
def test_arguments_that_would_move_the_agent_job_away_from_its_regrade_are_refused(extra):
    # Harbor takes the last of a repeated option, so these would win over suss's own.
    with pytest.raises(ValueError, match="regrade"):
        harbor_commands(make_config(), make_manifest(), "pro-go", STAMP, 1, extra)


def test_only_the_regrade_gets_pythonpath_and_it_is_absolute():
    agent, regrade = harbor_commands(make_config(), make_manifest(), "pro-go", STAMP, 1)

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
def test_every_ak_value_reaches_the_agent_as_the_string_it_was(arm):
    agent, regrade = harbor_commands(make_config(arm=arm), make_manifest(), "pro-go", STAMP, 1)

    assert harbor_parses(agent.argv) == {"arm": arm, "config": "suss.toml"}
    assert harbor_parses(regrade.argv) == {"source_job": f"results/pro-go/{arm}/{STAMP}-1"}


def test_an_ordinary_ak_value_is_not_quoted():
    agent, regrade = harbor_commands(make_config(), make_manifest(), "pro-go", STAMP, 1)

    assert ak_values(agent.argv) == {"arm": "peer-review", "config": "suss.toml"}
    assert ak_values(regrade.argv) == {"source_job": f"results/pro-go/peer-review/{STAMP}-1"}


def test_an_arm_harbor_would_read_as_a_number_is_quoted_as_a_json_string():
    agent, _ = harbor_commands(make_config(arm="2026"), make_manifest(), "pro-go", STAMP, 1)

    assert ak_values(agent.argv)["arm"] == '"2026"'
