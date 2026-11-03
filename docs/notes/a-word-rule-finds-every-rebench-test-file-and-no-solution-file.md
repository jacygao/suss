---
settles: "§ 3 — which paths the regrade grades as the starting tree held them, and what replay.json records of them; § 5 — that the rule holds for every language in rebench"
issue: [143]
recorded: 2026-11-04
versions: { harbor: 0.22.0, modal: 1.6.3, swe-rebench: "swe-rebench-07-2026 (108 tasks used)" }
recheck-when: the rebench download changes, a lane is added, or ReplayAgent's test-file rule changes
---

# A word rule finds every rebench test file and no solution file

## Intent

- Which rule for test files covers every path in the 108 reference test patches?
- Does that rule class any path of the 108 reference solution patches as a test file?
- Which trials of the first `rebench` baseline does it touch?
- Regraded with the rule, what does the baseline's `mikro-orm__mikro-orm-7799` trial score?

## Decisions

- **Class a path as a test file when one of its words is `test`, `tests`,
  `testdata` or `mdtest`, or its name ends in `.spec.` and a JS or TS
  extension.** It classes all 228 paths of the reference test patches as test
  files, and none of the 238 paths of the reference solution patches.
- **Split words at case changes and leave a word whole otherwise.** Matching
  `test` anywhere in the path would also cover all 228, but would class source
  files such as `GetTimeSeriesLatestHandler.java`, `net/speedtest/speedtest.go`
  and `feature/tpm/attestation.go` as tests, and restore a harness's fix to
  them.
- **Leave out `testing`, `spec` as a directory, and `.snap` as an extension.**
  None of the 228 needs them. `testing` would class source such as lisette's
  `crates/stdlib/typedefs/testing/`, a `spec` directory would class
  `internal/spec/spec.go`, and `.snap` would class oxc's solution file
  `tasks/coverage/snapshots/semantic_typescript.snap`.
- **The rule changes 94 of the 108 baseline trials.** In 22 of them the
  harness edited an existing test file that the verifier does not restore, and
  17 of those 22 scored 1.
- **The rule fixes `mikro-orm-7799`.** Regraded from its unmodified trial on
  Modal, it scored 0, and `replay.json` listed both migrator tests as restored.

## Needs your input

- **Whether to regrade the first baseline again.** 17 trials scored 1 with an
  edit to a test file the verifier did not restore, and only `mikro-orm-7799`
  is known to have passed because of one. I recommend regrading the whole
  baseline job with this rule before comparing any arm with it.

## Reference

### What the rule covers

| Set | Paths | Classed as test files |
|---|---|---|
| Reference test patches, from `tests/test.sh` | 228 | 228 |
| Reference solution patches, from `solution/solve.sh` | 238 | 0 |
| Files of the 108 repositories at their base commits | 418,168 | 225,478 |

The repository trees came from the GitHub trees API at each task's
`base_commit`. Both `microsoft__typescript-go` trees came back truncated, at
49,481 and 49,502 files.

What a single word or the name adds, over the trees:

| Only by | Files | Where |
|---|---|---|
| `testdata` | 89,621 | Go `testdata/` directories, 88,021 of them in typescript-go |
| `mdtest` | 1,056 | ruff: ty's `resources/mdtest/*.md` tests, and the `mdtest` and `ruff_mdtest` crates that run them |
| `.spec.` name | 536 | LibreChat, tiptap, base-ui and others, beside the source they test |

`ruff-25567` grades `mdtest::generics/legacy/variance.md` and
`mdtest::generics/pep695/variance.md`, which neither of its patches touches,
and its baseline harness edited both. `LibreChat-13166`'s harness edited
`packages/api/src/agents/tools.spec.ts`, whose path holds no `test`.

### The baseline trials whose existing test edits the verifier kept

Reward is the baseline regrade's, before this rule.

| Trial | Reward | Test files edited |
|---|---|---|
| `Gentleman-Programming__gentle-ai-595` | 1 | `internal/tui/model_test.go` |
| `agno-agi__agno-8148` | 0 | `libs/agno/tests/unit/team/test_run_requirement_fixes.py` |
| `astral-sh__ruff-25414` | 1 | `ERA001.py` fixture and its `.snap` |
| `astral-sh__ruff-25567` | 1 | both `variance.md` mdtests |
| `better-auth__better-auth-9756` | 1 | `packages/drizzle-adapter/src/drizzle-adapter.test.ts` |
| `cloudflare__vinext-1371` | 1 | `tests/link.test.ts` |
| `cloudflare__vinext-1574` | 1 | `tests/pages-page-response.test.ts` |
| `danny-avila__LibreChat-13166` | 1 | `packages/api/src/agents/tools.spec.ts` |
| `fallow-rs__fallow-762` | 1 | `crates/extract/src/tests/sfc.rs` |
| `fallow-rs__fallow-821` | 1 | `crates/core/src/plugins/registry/tests.rs` |
| `floci-io__floci-1235` | 0 | `S3ServiceTest.java` |
| `marimo-team__marimo-9754` | 0 | `tests/_ai/tools/tools/test_dependency_graph.py` |
| `microsoft__apm-1553` | 0 | `tests/unit/install/test_sources_phase3.py` |
| `mikro-orm__mikro-orm-7799` | 1 | `Migrator.postgres.test.ts`, `Migrator.sqlite.test.ts` |
| `mvanhorn__cli-printing-press-1920` | 1 | `internal/generator/generator_test.go` |
| `oxc-project__oxc-22623` | 1 | `crates/oxc_linter/src/rules/eslint/no_unused_vars/tests/oxc.rs` |
| `promptfoo__promptfoo-9313` | 1 | `test/server/eval.test.ts` |
| `rest-sh__restish-337` | 1 | `internal/content/registry_test.go` |
| `rust-lang__rust-analyzer-22397` | 1 | `crates/ide-completion/src/tests/special.rs` |
| `tursodatabase__turso-7117` | 1 | `testing/sqltests/tests/alter_table.sqltest` |
| `ubugeeei-prod__vize-769` | 0 | `crates/vize_atelier_sfc/src/compile/tests.rs` |
| `withcoral__coral-1143` | 1 | `crates/coral-spec/src/v4/projection_tests.rs` |

The other 72 trials the rule touches edited only test files the verifier
restores anyway, or added test files, which the regrade now removes.

### The mikro-orm regrade

Run from `~/Documents/Dev/suss-runs/checks/grade-tests-at-base-20261104/`,
over a copy of the lane task and of the trial
`mikro-orm__mikro-orm-7799__itUUpJB`, byte for byte. The regrade then restored
test files from `HEAD`, not from the starting tree. The two differ only where
the image changed a test file, and the patch's hunks in both test files applied
cleanly to the image:

```bash
uv run --extra modal --project <worktree> harbor run -p lane -e modal -n 1 -k 1 \
  -a suss.replay:ReplayAgent -m replay --ak "source_job=$PWD/src-job" \
  --jobs-dir jobs --job-name regrade
```

It took 44 seconds and scored 0.0. `git apply --verbose` applied all three
files, and `replay.json` held:

```json
"apply_rc": 0,
"tests_reset": {
  "restored": [
    "tests/features/migrations/Migrator.postgres.test.ts",
    "tests/features/migrations/Migrator.sqlite.test.ts"
  ],
  "removed": []
}
```

`verifier/test-stdout.txt` failed
`Migrator (sqlite) > migration up and down both write the snapshot`, the test
the harness had rewritten.

### Traps

- `harbor run -e modal` needs the `modal` extra, so `uv run` without
  `--extra modal` stops with `MissingExtraError`.
- With `git apply --3way`, a test file the patch adds is staged as well as
  written. Removing it from the working tree alone would leave it in the index.

## Limits

- A test inside a source file, such as a Rust `mod tests` or a Go example in a
  non-test file, is not a test file by this rule, so its edits are graded.
- The reference patches were not regraded with the rule. None of them touches
  a test file by the rule, so their regrade does exactly what it did before.
- Only `mikro-orm-7799` was regraded with the rule. Which of the other 16
  passing trials in the table above would now score 0 is unknown.
- The rule classes the `mdtest` and `ruff_mdtest` crates' sources as test
  files. A harness that fixed a bug in them would have that fix undone.
- The mikro-orm regrade was not rerun with restoring from the starting tree.
  Whether the image changes either migrator test outside the patch's hunks was
  not checked; if it does not, the result is the same.
- Only Modal was run. The regrade's git commands were tested on this machine's
  git, not in a Docker task image.
