# Suss Specification: Measuring Whether a Tool Improves a Coding Agent's Code

**Version:** 0.1 (draft)

## 1. Purpose

**Suss measures whether a tool makes a coding agent's work better.** It runs the
same benchmark tasks under several arms. Each arm is one coding agent and model,
with or without a tool applied to it. Suss reports how each arm scored and
whether the differences between them are larger than the noise.

A tool under measurement is called a treatment. Squiz, which reviews the agent's
change with a second agent, is one treatment. A plugin that changes how the
agent works is another. Suss knows nothing about any treatment beyond the
contract in § 4: each one supplies a small adapter, in its own repository, and
suss runs it.

A run with three arms looks like this:

| Arm | Coding agent | Treatment |
|---|---|---|
| `baseline` | Claude Code, Opus | none |
| `self-review` | Claude Code, Opus | the same model reviews its own change, three rounds at most |
| `squiz` | Claude Code, Opus | squiz with `pi` on another model, three rounds at most |

## 2. Dependencies

| Dependency | Version | Needed for |
|---|---|---|
| Harbor | 0.22.0 | Runs each task in a sandbox, runs the agent, and grades the result with the task's own verifier. |
| Modal | the account Harbor's `modal` environment uses | The sandboxes. |
| SWE Atlas | a checkout of `scaleapi/swe-atlas` | The tasks (§ 5). |
| Python | 3.12 | Suss itself, which Harbor loads as a custom agent. |
| `uv` | any | Installs suss and Harbor. |

Each arm needs API keys for its coding agent and for every model its treatment
calls. The SWE Atlas verifier needs a key for its judge model. Suss reads all of
them from a `.env` file at the root of the run directory, as SWE Atlas's own
scripts do.

## 3. A trial

### Terminology

| Term | Meaning |
|---|---|
| **Task** | One benchmark task: a repository in a sandbox image, an instruction, and a verifier. |
| **Arm** | A coding agent, a model, a reasoning effort, and at most one treatment. |
| **Treatment** | The tool under measurement, reached through its adapter (§ 4). |
| **Trial** | One attempt at one task by one arm. Harbor runs K trials of each task for each arm. |
| **Turn** | One run of the coding agent inside a trial. The first turn is given the task's instruction. Each later turn is given a review's findings. |
| **Round** | One review of the change, followed by one turn that acts on its findings. |
| **Base** | The commit the workspace's `HEAD` names when the trial starts. |

### What a trial does

Suss is a Harbor custom agent, `suss.agent:SussAgent`. Harbor starts it once per
trial, inside the sandbox, and grades the workspace after it returns.

1. **Find the workspace and record the base.** Suss finds the repository root as
   the SWE Atlas verifier does: `/workspace` if it is a git repository, and
   otherwise the first of the verifier's own list of candidate paths that holds a
   `.git` directory. It records `git rev-parse HEAD` as the base.
2. **Install the treatment**, if the arm has one (§ 4, Installing).
3. **Run the first turn.** The coding agent is given the task's instruction,
   unchanged.
4. **Run rounds**, if the treatment is a reviewer (§ 4, Reviewing). Each round
   takes a snapshot commit (§ 3, The snapshot commit), runs the review, and stops
   the loop if it returned no findings. Otherwise it runs a turn that hands the
   findings to the coding agent in the same session. The loop also stops after the
   arm's round cap.
5. **Check the workspace** (§ 3, The workspace check).
6. **Write the trial record** (§ 6) and return.

Harbor then runs the task's verifier on the workspace as it stands.

A turn after the first continues the coding agent's session. For Claude Code,
that is Harbor's own Claude Code invocation with `--continue`. The prompt for
such a turn is:

```
A reviewer read your change and reported the findings below. Fix the ones that
apply. For any you leave, say why. The task's instructions still hold.

1. high — pkg/writers/buffer/buffer.go:88
   Put returns a grown buffer to the pool instead of a fresh one
   <the finding's body>

2. medium — pkg/gitparse/gitparse.go:412
   ...
```

### The snapshot commit

A reviewer reads the change as `git diff <base>...<head>`. The coding agent never
commits, so suss makes a commit for each round from the workspace as it stands,
without moving `HEAD`, the branch, or the index:

```bash
export GIT_INDEX_FILE="$(mktemp)"
git add -A
tree="$(git write-tree)"
head="$(git commit-tree "$tree" -p "$base" -m "suss round $n")"
git update-ref "refs/suss/round-$n" "$head"
unset GIT_INDEX_FILE
```

- The commit's only parent is the base, so every round's diff is the whole change
  so far.
- `git add -A` with a fresh index picks up the same files the SWE Atlas verifier
  grades, which also runs `git add -A`, and leaves out what `.gitignore` excludes.
- The ref keeps the commit from garbage collection. It is not a file in the tree,
  so it is not part of the graded patch.

### The workspace check

After the last turn, suss checks that:

- `HEAD` still names the base;
- `refs/suss/` holds only the refs suss made.

A failed check marks the trial `invalid` in its record (§ 6). Suss does not
change the workspace to repair it, because the verifier grades the workspace as
the agent left it.

## 4. The treatment contract

A treatment is a directory, which its own repository ships. Suss uploads it into
the sandbox at `/opt/suss/treatment/` before the trial starts. It holds a
manifest, `suss-treatment.toml`:

```toml
[treatment]
name = "squiz"
version = "0.1.0"

# Run once, before the first turn. Optional.
install = "install.sh"

# Run once a round. Present for a reviewer, absent otherwise.
review = "review.sh"

# Hostnames the treatment calls during the trial, added to the sandbox's allowlist.
allowed_hosts = ["api.deepseek.com"]

# Environment variables the treatment reads, passed through from the run's .env.
env = ["DEEPSEEK_API_KEY"]
```

A treatment is one of two kinds, or both:

- **An installer** changes how the coding agent works, through its `install`
  command, and then takes no part in the trial.
- **A reviewer** reads the change after each turn, through its `review` command,
  and reports findings for the next turn.

### Installing

Suss runs `install` once, as the agent's user, with the workspace as its current
directory and `SUSS_WORKSPACE` naming it. A non-zero exit ends the trial and
marks it `treatment-failed`.

An installer may write the coding agent's own configuration, such as a Claude
Code plugin or settings under the agent's home directory. It writes nothing
inside the workspace.

### Reviewing

Suss runs `review` once a round, as the agent's user, with:

```
review --workspace <path> --base <sha> --head <sha> --round <n> \
       --history <file> --out <file>
```

- `--head` is the round's snapshot commit.
- `--history` is a JSON file holding every earlier round: its findings, and the
  coding agent's last message from the turn that acted on them. It is `[]` in the
  first round.
- `--out` is where the review writes its findings.

The findings file is:

```json
{
  "findings": [
    {
      "severity": "high",
      "path": "pkg/writers/buffer/buffer.go",
      "line": 88,
      "title": "Put returns a grown buffer to the pool instead of a fresh one",
      "body": "..."
    }
  ],
  "usage": { "input_tokens": 182340, "output_tokens": 9120, "cost_usd": 0.41 }
}
```

- `severity` is `high`, `medium` or `low`. Suss lists findings in that order in
  the turn's prompt and does not otherwise act on it.
- `path` and `line` may be `null` for a finding about the change as a whole.
- `usage` is optional. Where it is present, suss adds it to the trial's cost.

An empty `findings` list ends the loop. A non-zero exit, or an `--out` file that
does not parse, ends the loop and marks the trial `treatment-failed`. The
verifier still grades the workspace as the last turn left it.

A reviewer reads the workspace and the git objects, and writes nothing inside the
workspace. Its scratch files go under `$TMPDIR`.

### Self-review

Self-review is a reviewer that suss ships, in `treatments/self-review/`. It runs
the arm's own coding agent and model in a fresh session, in the snapshot commit's
tree, and asks it to review `git diff <base>...<head>` and write the findings
file. It is the control for any reviewer measured against it.

## 5. Datasets

Suss runs SWE Atlas's Refactoring lane (`data/rf`, 70 tasks) and Test Writing
lane (`data/tw`, 90 tasks). Its Codebase QnA lane produces an answer rather than a
change, so a reviewer has nothing to read there.

The SWE Atlas verifier grades each trial. A Refactoring trial resolves when the
change breaks none of the task's relevant tests and passes every `must have`
rubric. A Test Writing trial resolves when its tests pass the rubrics, the
manifest check and the mutation check. Each trial also gets a rubric score, the
share of rubric items it passed, which suss reports beside the resolve rate.

Every arm runs with the network restrictions SWE Atlas sets for the lane. Suss
adds the coding agent's API host and the treatment's `allowed_hosts` to the
allowlist, and nothing else.

## 6. Results

Harbor writes each trial's directory as usual. Suss adds `suss.json` to the
trial's agent logs, at `/logs/agent/suss.json` in the sandbox:

```json
{
  "arm": "squiz",
  "treatment": { "name": "squiz", "version": "0.1.0" },
  "base": "5568b2e0a686cc4d838bddc095b831379143e61b",
  "status": "ok",
  "rounds": [
    { "round": 1, "head": "…", "findings": 3, "review_seconds": 214, "turn_seconds": 388 },
    { "round": 2, "head": "…", "findings": 0, "review_seconds": 160 }
  ],
  "usage": {
    "agent": { "input_tokens": 2104000, "output_tokens": 61000, "cost_usd": 6.90 },
    "treatment": { "input_tokens": 342000, "output_tokens": 15800, "cost_usd": 0.77 }
  }
}
```

`status` is `ok`, `treatment-failed` or `invalid`.

`suss compare` reads the results of two or more arms over the same tasks and
prints, for each arm:

- the resolve rate, and the mean rubric score;
- the mean agent and treatment cost a trial;
- the share of trials that are not `ok`.

For each pair of arms it prints the difference in resolve rate, with a 95%
interval from a bootstrap that resamples tasks, so that each task's K trials stay
together.

## 7. Commands

### `suss run`

```bash
suss run <arm> <lane>... [-- <harbor args>]
suss run squiz rf tw -- -i task-69391d8d1ce51c407be1e531
```

It reads the arm from `suss.toml` in the current directory and runs one
`harbor run` for each lane, with suss as the agent. Anything after `--` goes to
every `harbor run`. `DRY_RUN=1` prints the commands instead of running them.

`suss.toml` names the SWE Atlas checkout and the arms:

```toml
swe_atlas = "../swe-atlas"
k = 3
concurrency = 16

[arms.baseline]
agent = "claude-code"
model = "anthropic/claude-opus-4-6"
effort = "high"

[arms.self-review]
agent = "claude-code"
model = "anthropic/claude-opus-4-6"
effort = "high"
treatment = "self-review"
rounds = 3

[arms.squiz]
agent = "claude-code"
model = "anthropic/claude-opus-4-6"
effort = "high"
treatment = "../squiz/eval/suss"
rounds = 3
```

`treatment` is a path to a treatment directory, or the name of one suss ships.

### `suss compare`

```bash
suss compare <lane> <arm> <arm>...
```

It prints the table in § 6, Results.

## 8. The project

### Language

Python 3.12, managed with `uv`. Harbor is a dependency, pinned to 0.22.0.
`uv run pytest` runs the tests.

### Structure

```
src/suss/
  agent.py        SussAgent, the Harbor custom agent
  snapshot.py     the snapshot commit and the workspace check
  treatment.py    reading a manifest, installing, running a review
  config.py       suss.toml
  compare.py      suss compare
  cli.py          the suss command
treatments/
  self-review/    the self-review treatment
tests/
docs/specs/
```

## 9. Limits

- **Claude Code is the only coding agent.** A later turn continues the agent's
  session, and only Claude Code's continuation is specified (§ 3).
- **A treatment's writes to the workspace are not detected.** The contract
  forbids them (§ 4), but the workspace check cannot tell them from the coding
  agent's, so a treatment that writes there changes the graded patch unseen.
- **The rounds of a reviewer are not matched for compute.** An arm with a
  treatment spends more than the baseline. `suss compare` reports the cost of
  each arm, and leaves the reader to weigh it.
- **The reviewer's model is not separated from the reviewer.** A squiz arm on
  another model differs from self-review in both the tool and the model. Telling
  the two apart needs a second squiz arm on the coding agent's own model, which
  `suss.toml` can hold.
