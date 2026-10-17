# Working in this repository

The skills named here come from the `skills` plugin of the `jacygao/skills`
marketplace, which `.claude/settings.json` enables. They hold the method; this
file holds what is specific to suss, and they read it from here.

## Specs

**Load the `skills:writing-specs` skill before writing or editing anything under
`docs/specs/`.** Every time, without being asked.

`docs/specs/suss-spec.md` is the specification. Its § 8, The project, fixes the
language and the `src/` layout.

## Checks

```bash
uv run pytest
```
