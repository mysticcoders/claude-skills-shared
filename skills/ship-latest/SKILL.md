---
name: ship-latest
description: "Ship the current working changes end-to-end: branch, commit, push, open a PR, watch CI to green, then squash-merge to the default branch and sync local. Trigger: /ship-latest"
trigger: /ship-latest
---

# /ship-latest

Take whatever is currently in the working tree (or already committed on a feature
branch) and ship it: feature branch → commit → push → PR → watch checks → merge to
the default branch → sync local. Do NOT merge while any check is failing or pending.

## Usage

```
/ship-latest                     # ship the current changes with an inferred commit/PR title
/ship-latest <title>             # use <title> as the commit subject + PR title
/ship-latest --closes 123        # add "Closes #123" to the PR body
/ship-latest --no-merge          # stop after checks pass; leave the merge to the user
/ship-latest --draft             # open the PR as a draft (implies --no-merge)
```

## Procedure

Run these steps in order. Narrate briefly; stop and ask only when a step is
genuinely ambiguous or a check fails.

### 1. Preflight
- `git status --short` and `git branch --show-current`. If the tree is clean AND the
  branch already has commits ahead of the default branch, you're shipping an existing
  branch — skip to step 4 (push).
- Identify the default branch (usually `main`; confirm with `git remote show origin`
  or the repo's known default).
- If there are no changes to ship (clean tree, no ahead commits), say so and stop.

### 2. Branch
- If currently on the default branch, create a feature branch first:
  `git checkout -b <type>/<kebab-summary>` (e.g. `feat/mobile-bottom-sheet`). Infer the
  type (`feat`/`fix`/`chore`/`test`/`refactor`) and summary from the diff.
- If already on a feature branch, keep it.

### 3. Stage
- Stage the files that belong to THIS change. Prefer explicit paths over `git add -A`.
- EXCLUDE unrelated pre-existing untracked files (scratch docs, other people's WIP,
  `*-RFP-*.md`, screenshots, `test-results/`, `.playwright-mcp/`). When in doubt about
  an untracked file, ask before including it.
- Re-run `git status --short` to confirm the staged set looks right.

### 4. Verify (fast, before pushing)
- Run the affected packages' typecheck and lint (e.g. `pnpm --filter <pkg> typecheck`,
  `pnpm exec eslint <changed files>`). Fix anything you introduced.
- If the change touches UI/behavior with e2e coverage, run the relevant spec locally
  when practical. Note anything you couldn't run.

### 5. Commit
- One focused commit (or amend if iterating on an unpushed commit). Subject in the
  imperative, Conventional-Commits style: `feat(scope): summary`.
- Body: bullet the what/why.
- Honor the user's global rules (CLAUDE.md), which override the harness's attribution
  defaults: for example US English, no `Co-Authored-By` line, and no claude.ai session
  URLs or `Claude-Session:` trailers.
- Write the message to a temp file and `git commit -F` to preserve formatting.

### 6. Push (handle pre-push hooks)
- `git push -u origin <branch>`.
- A pre-push hook may fail (lint, image-pattern checks, etc.). Read the message, fix
  the root cause, amend or add a commit, and push again. Do not `--no-verify` around a
  real failure unless the user says so.
- If a `gh` GraphQL call later fails with a stale-auth 401, run `gh auth status`
  to find the invalid account, log it out with `gh auth logout -u <account>`, and retry.

### 7. Open the PR
- `gh pr create --base <default> --head <branch> --title "<subject>" --body-file <tmp>`.
- PR body: summarize highlights + verification; add `Closes #N` when an issue is known
  (ask if unsure and one is implied); end with the harness's
  "Generated with Claude Code" line.
- Capture the PR number/URL.

### 8. Watch checks
- `gh pr checks <n> --watch --interval 20` (run in the background so you're notified).
- When it finishes, re-read the authoritative status with `gh pr checks <n>`.
- If any check FAILS: pull the failing logs (`gh run view <run-id> --log-failed`,
  filter out server/browser noise), fix the cause, push, and re-watch. Common case:
  the change intentionally alters behavior an existing test asserted — update the test
  to the new expected behavior, don't weaken the assertion.
- Never proceed to merge while red or pending.

### 9. Merge + sync
- Unless `--no-merge`/`--draft`: `gh pr merge <n> --squash --delete-branch`.
- Verify merged: `gh pr view <n> --json state,mergeCommit`.
- Sync local: `git checkout <default> && git pull --ff-only`.
- Report the merge commit, the check results, and a one-line summary of what shipped.

## Notes
- Keep the loop honest: if a step is skipped or a check couldn't run, say so plainly.
- One PR per logical change. If the working tree mixes unrelated changes, flag it and
  ask how to split rather than bundling.
