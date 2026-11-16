---
settles: "§ 4 — that CopilotCliSquiz on Copilot CLI 1.0.107 and squiz 0.3.2 reviews and fixes a rebench trial, and where its reviewer and fixer get the token; § 5 — that its run needs no host beyond CopilotCli's; § 6 — what squiz-gpt-6.1-sol's leaderboard row measures"
issue: [110]
recorded: 2026-11-16
versions: { copilot-cli: 1.0.107, squiz: 0.3.2, node: 24.15.0, harbor: 0.25.0, environment: "Docker on Azure Standard_E20as_v5, one VM per region", lane: "rebench, 108 tasks, k = 3", model: "gpt-6.1-sol, effort high", reviewer: "claude-sonnet-5.5, effort high" }
recheck-when: an arm pins another squiz or Copilot CLI version, squiz changes how its reviewer signs in or which hosts it reaches, or the egress allowlist changes
---

# squiz reviews Copilot CLI's rebench trials on Copilot CLI's own two hosts, and adds 45% to the cost for a gain within noise

## Intent

- Does `CopilotCliSquiz` get squiz to review and fix the agent's change inside the sandbox, with no login but the arm's token?
- Which hosts does the run need beyond plain Copilot CLI's?
- What does squiz change on rebench, in resolved rate and in cost?

## Decisions

- **Give the reviewer and fixer the token through `copilotTokenFile`, and
  require squiz 0.3.2 or later.** Copilot CLI starts its hooks without
  `COPILOT_GITHUB_TOKEN`, so on squiz 0.3.1 the reviewer said `Error: No
  authentication information found.` and the round stopped with nothing
  reviewed. From a file readable by the agent's user alone, every round
  signed in. No token string reached a log or a patch.
- **Keep `CopilotCli`'s `ALLOWED_HOSTS` for the squiz adapter.**
  `api.github.com` and `api.enterprise.githubcopilot.com` were enough for the
  agent, the reviewer and the fixer. `exp.enterprise.githubcopilot.com`, which
  squiz's reviewer reaches outside the sandbox, was never needed.
- **Read `squiz-gpt-6.1-sol` as no better than plain Copilot CLI on this
  lane, at 45% more cost.** It resolved 64% ±8 against
  `copilot-gpt-6.1-sol`'s 62% ±9 on the same model, and cost $63.82 a run
  against $44.06. The coding agent took $43.56 of that, the reviewer $15.64
  and the fixer $4.62. Its median trial took 7.6–8.1 min against 5.5–5.9.
- **Count the run's whole cost from `usage.ai_credits` alone.** It already
  holds the agent's, reviewer's and fixer's credits. `usage.squiz.reviewer`
  and `usage.squiz.fixer` only break it down, and adding them counts them
  twice.

## Needs your input

- **Whether to run `squiz-gpt-6.1-sol` again once squiz fixes its state-file
  lock (jacygao/squiz#899, planned for 0.4.0).** Seven trials lost their
  review to it. I recommend waiting for the fix and then running the arm
  again only if a squiz version is to be compared, since seven of 324
  trials cannot move the rate past its margin.

## Reference

### The run

```toml
[arms."squiz-gpt-6.1-sol"]
agent = "swe_drill.agents.squiz:CopilotCliSquiz"
model = "gpt-6.1-sol"
kwargs = { version = "1.0.107", reasoning_effort = "high", squiz_version = "0.3.2", reviewer_model = "claude-sonnet-5.5", reviewer_effort = "high" }
```

`scripts/azure-run.sh <region> squiz-gpt-6.1-sol rebench` in
`australiaeast`, `newzealandnorth` and `koreacentral`, started three minutes
apart: jobs `20261115T114014Z-1`, `20261115T114339Z-1` and
`20261115T114642Z-1`. No trial needed running again.

### What the 324 trials recorded

| `squiz.outcome` | Trials | What it means |
|---|---:|---|
| `closed` | 314 | squiz closed its episode, and the patch was saved after it. One closed with a thread still open. |
| `stopped` | 7 | The trial is `review-stopped`. Each `stopped.json` reason begins `nothing the reviewer found was posted: <workspace>/.squiz/local/state.json could not be written`. |
| none | 3 | Two `timed-out` before squiz started, and one `review-timed-out`. |

- **Rounds:** 239 trials had one reviewer session, 76 two and 7 three. Two
  had none.
- **The fixer** ran on 78 trials, the ones whose review left a thread open.
- **`usage.squiz.reviewer.reported_models`** is `["claude-sonnet-5.5"]` on
  every trial that reviewed.
- **No test result:** `apache__pulsar-25953` in all three runs and
  `apache__hugegraph-3037` in the first and third. They count as unresolved,
  as the other arms' were before regrading.

### The first check

The 0.3.1 check, job `checks/rebench/squiz-gpt-6.1-sol/20261115T071635Z-1`
on `ArcadeData__arcadedb-4281`, recorded `"copilot_experimental": true` and
`"loaded": true`, and stopped round 1 on the sign-in error. The 0.3.2 check,
`20261115T094039Z-1` on the same task, closed after one round with no
findings: 9.20 AI credits for the reviewer, 45.93 in all, 465 s, reward 1.

## Limits

- One model and one reviewer. Whether squiz helps a weaker coding agent, or
  with another reviewer, was not tested.
- The rate counts the seven stopped trials' patches as graded, unreviewed.
- Hosts were checked by the egress sidecar's allowlist holding on Docker, not
  by a capture of every connection.
- The credits are Copilot CLI's own reports. GitHub's bill was not checked
  against them.
