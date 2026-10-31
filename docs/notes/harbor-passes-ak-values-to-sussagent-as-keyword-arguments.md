---
settles: "§ 3 — how Harbor loads suss.agent:SussAgent and what its constructor receives; § 6 — the -a and --ak spelling of the agent job's command"
issue: [2]
recorded: 2026-10-31
versions: { harbor: 0.22.0, python: 3.12.16, docker-desktop: 4.73.0 }
recheck-when: Harbor upgrades past 0.22.0
---

# Harbor passes --ak values to SussAgent as keyword arguments

## Intent

- Whether `harbor run -a suss.agent:SussAgent --ak arm=<arm> --ak config=suss.toml`
  loads suss as a custom agent.
- Whether the constructor receives `arm` and `config`, and in what form.

## Decisions

- Keep the § 6 agent command as written. Harbor 0.22.0 takes the import path
  through `-a` and each value through `--ak`. `--agent-import-path` still parses
  but is a hidden, deprecated alias.
- `SussAgent.__init__` takes `arm` and `config` as keyword arguments and passes
  everything else on to `BaseAgent.__init__` through `**kwargs`. Harbor calls
  it with keywords only and no positional arguments:

  ```
  arm='peer-review' (str), config='suss.toml' (str), logs_dir, model_name=None,
  logger, extra_env={}
  ```

- `suss run` runs the `harbor` installed in suss's own environment, which
  `pyproject.toml` already gets by depending on `harbor==0.22.0`. Harbor imports
  the agent in its own process, on the host, so suss must be importable there.
  Where it is not, the job exits 1 with
  `ValueError: Failed to import module 'suss.agent': No module named 'suss'`.
- `SussAgent` reads `config` relative to the directory `harbor run` started in.
  The constructor runs on the host, so `suss.toml` is a host path, not one in
  the sandbox.

## Needs your input

- Harbor parses each `--ak` value as JSON before it falls back to a string, so
  `--ak arm=2026` arrives as the int `2026`, and `arm=true` as `True`. An arm
  named like a JSON number or literal is a legal `suss.toml` key and would reach
  `SussAgent` with the wrong type. I recommend `SussAgent` exit with an error
  when `arm` or `config` is not a `str`, rather than quoting every value in the
  command.

## Reference

- `AgentFactory.create_agent_from_config` treats an `-a` value containing `:`
  as an import path. The job's `config.json` records it as the agent's `name`,
  `"suss.agent:SussAgent"`, with `kwargs` `{"arm": "peer-review", "config": "suss.toml"}`.
- A failed import still writes a job directory and a trial directory under
  `--jobs-dir`. A trial directory is no evidence that the agent loaded; the
  `agent_info.name` in the trial's `result.json` is what `name()` returned.
- `parse_kwargs` strips whitespace around the key and the value, and splits on
  the first `=` only.
- The V2 images are `linux/amd64` only. On an arm64 Docker host, Harbor's pull
  fails with `no matching manifest for linux/arm64/v8` unless
  `DOCKER_DEFAULT_PLATFORM=linux/amd64` is set.

## Limits

- The stub never ran on an unmodified V2 task end to end. Its constructor ran
  on `instance_flipt-io__flipt-02e21636c58e86c51119b63e0fb5ca7b813b07b1` and
  logged both values, but Docker Desktop's kernel lacks `CONFIG_NFT_FIB_INET`,
  so Harbor refused the task's `no-network` agent phase before a container
  started. `setup()` and `run()` ran only on a copy of that task with its
  agent `network_mode` set to `public`. No Modal account was available.
- Only string values were passed through a real run. The typing of other
  values was read from `parse_kwargs` and checked by calling it directly.
