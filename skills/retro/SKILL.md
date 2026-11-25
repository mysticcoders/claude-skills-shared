---
name: retro
description: |
  Run a retrospective on a repository's recent agent work: read the Claude Code and Codex session transcripts and the repo's PRs (reviews, CI failures, fixes after opening), find where agents struggled or were corrected, trace each recurring problem to a root cause, and propose fixes to the environment (sensors, ratchets, lint rules, AGENTS.md, glossary, ADRs, docs, skills, harness settings), ranked. Files GitHub issues only for the findings the owner picks. Never edits code.

  MANDATORY TRIGGERS: "/retro", "run a retro", "retrospective on the agent sessions", "what did the agents struggle with"

  Works on any git repository. Default window is since the last retro, else 7 days.
allowed-tools: Bash(~/.claude/skills/retro/retro-extract.py*), Bash(gh issue list*), Bash(gh issue view*), Bash(gh issue create*), Bash(gh repo view*), Bash(git rev-parse*), Read, Glob, Grep, Agent, AskUserQuestion, Write
---

# /retro

Works in Claude Code and Codex; both read the same transcripts. Pocock's loop: after agents have worked unattended, spend tokens reading what they did, and turn every recurring problem into a change to the environment they work in, so the next run doesn't hit it. A review comment or correction that only fixed one PR is wasted. Prefer deterministic fixes (a sensor, ratchet or lint rule) over prose (an AGENTS.md line).

Arguments: `/retro [repo path] [--since YYYY-MM-DD] [--until YYYY-MM-DD] [--prs]`. With no path, use the current git repository.

## Step 1: Extract digests (script, no model)

```bash
~/.claude/skills/retro/retro-extract.py <repo> [--since YYYY-MM-DD] [--until YYYY-MM-DD]
```

It prints the output folder (`~/.cache/retro/<repo>/<timestamp>/`) and an index table, and writes:

- `sessions/*.md`: one digest per session (subagents folded in), with owner messages (those marked C look like corrections; it's a heuristic, so judge them), interrupts, permission denials, hook feedback, tool errors, commands that failed repeatedly, check fail-to-pass cycles, files edited five or more times, PRs linked and PRs the agent commented on.
- `github.md`: PRs updated in the window, with commits after opening, failed CI runs, reviews, line comments and conversation comments. Agents post as the owner's account; use each session's "PRs it commented on" to tell an agent's status report from the owner's review.
- `rules.md`: the repo's rule sources (AGENTS.md, glossary, ADRs, lint config, ratchets, sensors) and any ratchet raises.

Tell the owner the window, the session count and the PR count. If the index shows the retention WARNING, say that older transcripts are gone and suggest raising `cleanupPeriodDays` in `~/.claude/settings.json` (default 30 days) or running the retro more often. If there are no sessions, stop.

## Step 2: Analyze

Read `rules.md`, then the rule sources it lists that matter (AGENTS.md at least), so you can tell what is already covered.

- **Total digest size under about 150 KB:** read every digest and `github.md` yourself.
- **Larger:** split the session digests into batches of roughly equal size, at most six. If your harness can run subagents (the Agent tool in Claude Code, subagents in Codex), launch one per batch plus one for `github.md`, all at once so they run in parallel; otherwise read the batches one after another and keep only the findings between batches. Give each agent the repo path, the rule-source paths, its digest paths, and the finding format below. Tell them the digests are private and that evidence should cite the digest file and timestamp, not long quotes.

A finding is a recurring problem with evidence, not a one-off. Look for:

- **Owner corrections and interrupts.** What did the agent do that the owner had to stop or redirect? Was the right behavior written down anywhere?
- **Repeated failures.** The same command or check failing several times, edits that failed to apply, fail-to-pass cycles: what would have caught it sooner or prevented it?
- **Permission denials and blocked actions.** Especially ones the repo's own rules authorize, which point to a harness or settings mismatch.
- **Hook and sensor feedback.** A sensor that fires often is working; one the agent argues with may be wrong.
- **Churn.** A file edited many times usually means unclear requirements, a missing test seam or a missing fixture.
- **Review and CI pushback** in `github.md`: owner review comments, CI failures before green, commits after a PR opened, PRs left open with failing CI.
- **Rediscovery.** The agent researching something a doc, glossary entry or ADR should have told it, or arguing a decision an ADR already settled.
- **Docs that teach the wrong thing.** A rejected pattern that the docs or existing code demonstrate.

Finding format (one JSON object per finding):

```json
{"title": "short, specific", "evidence": ["digest-file @ MM-DD HH:MM: what happened", "github.md #NNN: ..."],
 "count": 3, "cost": "high|medium|low", "root_cause": "one sentence",
 "destination": "sensor|ratchet|lint|agents-md|glossary|adr|doc|skill|harness|code|none",
 "fix": "the concrete change", "covered_by": "existing rule that should have prevented it, or null"}
```

`destination` decides who fixes it:

| destination | when | the fix |
| --- | --- | --- |
| sensor / ratchet / lint | a mechanical check could have caught it | name the check and where it runs (agent-loop sensor, CI, ratchet baseline) |
| agents-md | a rule is missing, unclear, stale or contradicted | the exact wording to add or change |
| glossary / adr | naming confusion, or a decision re-argued | the term and definition, or the decision to record |
| doc | a doc or runbook was missing or taught the wrong thing | which doc, what to say |
| skill | a skill's steps led the agent wrong | the skill and the change; global skills live in `~/.claude/skills/` |
| harness | permissions, hooks, settings or tooling got in the way | the setting and value |
| code | the code itself makes the mistake easy (a missing seam, helper or type) | the refactor |

If `covered_by` is set, the finding is about why the existing rule didn't work (not seen, ambiguous, not enforced), and the fix should usually make it deterministic.

## Step 3: Rank

Merge findings that share a root cause. Drop anything that happened once with low cost. Rank by count × cost, with deterministic fixes ahead of prose at equal weight. Check open issues (`gh issue list --state open --limit 200`) and mark findings that already have one.

Show the owner a ranked table: rank, title, count, cost, destination, fix, plus an existing issue number where there is one. Keep evidence short; offer to show the full evidence for any row. Then ask which to file: AskUserQuestion with multiSelect (best eight) in Claude Code, or a numbered list the owner answers in other harnesses.

## Step 4: File

For each chosen finding, draft one issue: the problem, why it recurs (root cause), the proposed fix and destination, and how we'll know it worked. **Public repositories get no transcript content:** no quotes from owner messages or agent output, no local paths under `/Users`, no personal data. Describe evidence in your own words with dates and PR numbers. Check visibility with `gh repo view --json visibility`. Show the drafts and file only after the owner confirms. Then file an umbrella issue titled `Retro <date>: <N> findings` that links them and records the window.

Findings with destination `skill` for a global skill (`~/.claude/skills/...`) or `harness` (Claude Code settings) don't belong in the repo: list them in the final summary with the exact change, for the owner to approve.

Never edit code, rules or skills during a retro.

## Step 5: Record

Write the ranked findings (all of them, including the ones not filed) to `<output folder>/report.md`, then run:

```bash
~/.claude/skills/retro/retro-extract.py <repo> --mark-done
```

so the next retro starts from now. Finish with: the issues filed (links), the out-of-repo changes proposed, and the path to `report.md`.
