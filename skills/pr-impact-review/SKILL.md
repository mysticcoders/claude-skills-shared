---
name: pr-impact-review
description: |
  Review a pull request by structural blast radius rather than uniformly: classify it Level 1 (operational / auto-mergeable), Level 2 (structural / needs human architectural review), or Level 3 (strategic / needs review plus recorded rationale), then review only what that level warrants.

  Use when asked to review a PR or diff, decide whether a change can auto-merge, or assess whether a change is riskier than it looks. Also emits data for tracking architectural health over time.

  MANDATORY TRIGGERS: "/pr-impact-review", "review this PR", "can this auto-merge", "what level is this PR"

  Operates on a git repo with a diff against a base ref. Refuses to classify from a PR description alone.
allowed-tools: Bash(~/.claude/skills/pr-impact-review/pr-signals.sh*), Bash(git *), Bash(gh *), Bash(rg *), Bash(jq *), Read, Grep
---

# /pr-impact-review

**Automation validates technical correctness. Your job is architectural direction.**

Derived from the tiered-review model in Manuel Tuero's
[Guidelines for structuring AI-optimized software projects](https://blog.blackboxvision.com/structuring-ai-optimized-projects/)
(2026-03-31), with three
additions that close holes in the published model: classification is computed rather
than declared, Level 1 carries a coverage condition, and split-PR gaming is checked.

## The three rules

1. **Compute, never trust.** If a human or PR template declares a level, treat it as a
   hypothesis to verify. Record disagreement; do not defer to it.
2. **Ambiguity escalates.** A false Level 1 lands an unreviewed structural change with
   unbounded consequences. A false Level 2 costs one person ten minutes. Never resolve
   uncertainty downward.
3. **Green is not correct.** A passing pipeline means the tests that exist passed. A
   change to untested code passes trivially.

## Step 0 — Refuse

Stop and return Level 3 immediately if the diff touches `.pr-impact.toml`, `.github/`,
or release machinery. **A change to the rules cannot be graded by the rules.**

Refuse entirely if you cannot obtain the diff. Never classify from a description.

## Step 1 — Gather facts

    ~/.claude/skills/pr-impact-review/pr-signals.sh [BASE_REF]   # default origin/main

Returns JSON: `files_changed`, `packages_touched`, `deps_changed`, `schema_changed`,
`exports_added/removed`, `boundary_files`, `strategic_files`, `rules_or_ci_touched`,
`fan_in`.

The script computes facts and deliberately does **not** classify — classification
requires escalating on ambiguity, which is judgment.

Read its `notes` field. `fan_in` is best-effort textual matching: **a zero is weak
evidence, a non-zero is strong evidence.** Verify a zero before relying on it.

## Step 2 — Classify

Assign the **highest** level any signal triggers. Never average.

**Level 3 — strategic**
- `strategic_files` or `rules_or_ci_touched` non-empty
- a new architectural pattern: a second way to do something the codebase already does
  one way (new transport, state manager, auth model, build tool, test framework)
- a previously recorded decision reversed

**Level 2 — structural**
- `exports_added`/`exports_removed` non-zero, or `deps_changed`, `schema_changed`,
  `boundary_files` non-empty
- `packages_count > 1`
- any touched package has `fan_in > 0`
- any public symbol or endpoint removed or renamed

**Level 1 — operational** — only if *all* hold:
- none of the above
- confined to one package with no external importers
- **changed lines are covered by executing tests**, at or above
  `min_changed_line_coverage`. Verify from coverage output, not from a test file
  existing. If coverage is unavailable, that is inconclusive → Level 2.

Inconclusive signal ⇒ treat as present.

## Step 3 — Evidence before verdict

Never emit a bare level. Emit:

    level: 2
    triggered_by:
      - signal: exports_changed
        evidence: "packages/bearing-api/src/routes.ts:+export function resolveCity()"
      - signal: fan_in
        evidence: "packages/flight-status imported by relay, bearing"
    not_triggered: [deps_changed, schema_changed]
    changed_line_coverage: 0.41
    declared_level: 1
    disagreement: true

A reviewer must be able to check your reasoning without re-reading the diff.

## Step 4 — Review to the level, and no further

The tiering is worthless if every level still gets every kind of attention.

**Level 1 — do not review the code.** Confirm the gate only: pipeline green, coverage
met, signals genuinely absent. Approve. If you want to comment on style, stop — that
belongs in lint.

**Level 2 — do not re-check correctness.** The pipeline did that. Ask only:
- Does this contradict a decision recorded in `docs/`, ADRs, or `CLAUDE.md`? Cite it.
- Does it introduce a *second* way to do something that already has one?
- Which boundary moved, and does the PR say it meant to move it?
- If wrong, what is the blast radius — and is it reversible?
- Is the new contract wider than it needs to be? Narrow contracts age better.

**Level 3 — refuse to approve.** Produce: the decision being made, the alternatives not
taken, and what would have to be true for this to be wrong. Route to a human. *A Level 3
that merges without a recorded rationale is the failure this process exists to prevent.*

## Step 5 — Anti-gaming

- **Recompute on every push.** A PR that opened at Level 1 and grew is graded on its
  current diff.
- **Split detection.** Check for sibling branches or recently-merged PRs by the same
  author that, combined with this one, would trigger Level 2:

      gh pr list --author @me --state all --limit 20 --json number,title,headRefName,mergedAt

  Salami-slicing a structural change into Level 1 slices is the obvious exploit, and it
  usually is not malicious — small PRs are normally good practice. Flag; do not
  auto-merge.
- **Track declared-vs-computed disagreement.** A repeated gap for one author or path is
  a signal about the process, not the person.

## Step 6 — Emit for the health metric

Append one line to `.pr-impact/log.jsonl`:

    {"pr":412,"level":2,"packages":3,"signals":["exports_changed"],"coverage":0.41,"ts":"..."}

**The distribution is the point.** Majority Level 1 means modular and low-coupling.
A rising Level 2 share means coupling is increasing.

⚠ **Confounded by work mix.** A quarter of deliberate platform work shows mostly Level 2
in a perfectly healthy architecture. The metric is only meaningful when what you are
building is roughly constant — say so when reporting it.

## Retrospective mode

To assess an existing repo without changing process, classify the last N merged PRs and
report only the distribution:

    gh pr list --state merged --limit 50 --json number,headRefName

For each, run the signals script against its merge base. This is the cheapest way to get
evidence about an architecture, and it needs no adoption commitment.

## Known limits — state these when reporting

- `fan_in` is textual and monorepo-shaped; weak outside pnpm/turbo layouts.
- **Minified or generated bundles inflate `exports_*` massively** (a single `main.js`
  can add hundreds). If the count looks absurd, check whether a build artifact is
  committed — that is itself worth flagging, but it is not a contract change.
- Coverage gating requires the repo to actually produce per-line coverage.
- The signal set encodes *one* opinion about what "structural" means. `.pr-impact.toml`
  is where a repo disagrees — and that file is Level 3 for a reason.
- The underlying model is **asserted, not measured**. Its author sells the methodology.
  Treat it as a reasonable default, not a validated one.
