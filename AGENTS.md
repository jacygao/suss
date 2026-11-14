# Working in this repository

The skills named here come from the `skills` plugin of the `jacygao/skills`
marketplace, which `.claude/settings.json` enables. They hold the method; this
file holds what is specific to suss, and they read it from here.

## Specs

**Load the `skills:writing-specs` skill before writing or editing anything under
`docs/specs/`.** Every time, without being asked.

`docs/specs/suss-spec.md` is the specification. Its § 7, The project, fixes the
language and the `src/` layout.

## Checks

```bash
uv run pytest
```

## Arms

Name an arm `<agent>-<model>`: the harness's short name, then the model's id
as it carries its version, such as `copilot-gpt-6.1-sol` for Copilot CLI on
`gpt-6.1-sol`. A setting that makes another arm of the same pair goes last:
`copilot-fusion-high`. A name with a dot is quoted in `suss.toml`:

```toml
[arms."copilot-gpt-6.1-sol"]
```

An arm named before this keeps its name until it next runs.
