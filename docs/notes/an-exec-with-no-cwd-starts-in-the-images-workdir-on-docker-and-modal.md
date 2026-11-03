---
settles: "§ 3 — where suss finds the workspace, on both backends, and why it must be the repository's top level"
issue: [119]
recorded: 2026-11-03
versions: { harbor: 0.22.0, modal: 1.6.3, docker: 29.9.2, host: "macOS arm64 client" }
recheck-when: Harbor upgrades past 0.22.0, Modal's client upgrades past 1.6.3, or a task's task.toml sets [environment] workdir
---

# An exec with no cwd starts in the image's WORKDIR on Docker and on Modal

## Intent

- What directory a command starts in when suss calls Harbor's `exec` with no
  `cwd`, under `docker` and under `modal`.
- Whether the workspace may be a directory inside a repository, or must be
  its top level.

## Decisions

- suss reads the workspace with `pwd` in the sandbox, with no `cwd`. Both
  backends start that command in the image's `WORKDIR`, so it gives the same
  directory on each.
- The workspace must be the repository's top level, and a directory inside
  one fails the trial. Run from a subdirectory, `git apply` skips every path
  outside it and still exits 0, so the regrade would silently drop part of
  the patch.

## Needs your input

Nothing.

## Reference

- Docker: Harbor's `exec` passes `cwd or task_env_config.workdir` to
  `docker compose exec`, and adds `-w` only when that is set. With neither,
  the container's `WORKDIR` applies, inherited from a base image or not.
- Modal: Harbor's `exec` passes `cwd or task_env_config.workdir or` the last
  `WORKDIR` of the task's own `Dockerfile`, parsed on the host, as the
  sandbox exec's `workdir`. With none of them, Modal starts the command in
  the image's `WORKDIR`: `/go` for `golang:1.23-alpine`, which inherits it,
  and `/gleam` for a Dockerfile that sets it.
- Modal runs a command for a `user` as `su <user> -s /bin/bash -c`, with no
  `-`, which keeps the directory.
- A task whose `task.toml` sets `[environment] workdir` starts there on both
  backends, in place of the image's `WORKDIR`.
- No SWE-rebench task sets `workdir`, and no task's `Dockerfile` sets a
  `WORKDIR`. Each inherits it from its base image,
  `docker.io/swerebenchv2/<task>:v0.1.0`, so on Modal Harbor passes no
  `workdir` and Modal's own default applies. All 111 base images set it to
  a directory directly under `/`, such as `/arcadedb`, `/pulsar` and
  `/pyscn`, read from each image's config without pulling it.
- In `swerebenchv2/ludo-technologies__pyscn-500:v0.1.0`, `pwd` gives
  `/pyscn` and `git rev-parse --show-prefix` gives an empty line: the
  `WORKDIR` is the repository's top level.

## Limits

- Harbor's Modal parser resolves a relative `WORKDIR` against `/`, not
  against a `WORKDIR` the base image set. It does not matter while no task's
  `Dockerfile` sets one.
- The Modal result comes from Modal's SDK directly, not through Harbor's
  `ModalEnvironment`. The Docker result comes from `docker exec` with no `-w`,
  not through Harbor's compose wrapper, which the code shows builds the same
  command.
- Only one task image was run, so that the `WORKDIR` is the repository's top
  level is shown for one task. For the other 110 it is likely, from the
  directory's name, but not shown. A task where it is not fails its trial
  with an error naming the directory.
