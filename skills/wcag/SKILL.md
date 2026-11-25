---
name: wcag
description: |
  Run WCAG 2.1 accessibility audits against a live URL or a local `dist/` directory using axe-core via Playwright. Produces a markdown report mapping findings to WCAG success criteria with P0–P3 severity. Use this skill when users want to:
  - Audit accessibility of a deployed site or built static output
  - Check WCAG / ADA compliance and get a remediation plan
  - Get a pre-launch a11y review on a branch's `dist/` output

  MANDATORY TRIGGERS: "/wcag", "wcag audit", "wcag check", "accessibility audit", "a11y audit", "ADA compliance check", "WCAG compliance"
allowed-tools: Bash(~/.claude/skills/wcag/wcag-audit *), Bash(uv *), Bash(test *), Bash(ls *), Read, Write, AskUserQuestion
---

# WCAG Skill

Run a WCAG 2.1 audit against any URL or local `dist/` directory using axe-core (the same engine that backs Lighthouse, Deque's enterprise tools, and most professional audits), driven by Playwright Chromium. Outputs a markdown report.

## Usage

```
/wcag <target> [options]
```

`<target>` is one of:
- A URL: `https://example.com` or `https://example.com/about`
- A local directory: `./dist` or `/path/to/dist`
- A single HTML file: `./dist/index.html`

### Examples

```
/wcag https://example.com
/wcag https://example.com --pages / /about /contact --output audit.md
/wcag ./dist --crawl
/wcag ./dist --pages / /about --viewport both
/wcag ./dist --level AAA
```

## CLI invocation

The skill is implemented as a uv-managed Python CLI at `~/.claude/skills/wcag/`. Invoke through the wrapper:

```bash
~/.claude/skills/wcag/wcag-audit <target> [options]
```

The wrapper handles first-run setup (uv sync + Playwright Chromium install).

### Options

| Flag | Default | Description |
|---|---|---|
| `--pages, -p <path>` | (root only) | Repeatable. Specific paths/URLs to audit. For dist/, paths like `/about`. For URLs, paths appended to the origin. |
| `--crawl` | off | When target is a dist/ directory, audit every `*.html` file found. |
| `--level, -l {A,AA,AAA}` | `AA` | Highest WCAG level to enforce. |
| `--viewport {desktop,mobile,both}` | `desktop` | Viewport size(s). `both` runs each page twice. |
| `--output, -o <path>` | `wcag-report.md` | Markdown output path. |
| `--json-output <path>` | (none) | Optional path for raw JSON results from axe. |
| `--include-best-practice` | off | Include axe "best-practice" rules in addition to WCAG. |
| `--timeout <seconds>` | `30` | Per-page navigation timeout. |

## Workflow

When invoked:

1. **Parse the target** — URL, directory, or HTML file. If a directory or file, the skill spins up a local HTTP server (random port on 127.0.0.1) so Playwright can navigate via `http://` (file:// causes a number of cross-origin and asset-resolution headaches with modern bundlers).
2. **Build the URL list** — root path by default. `--pages` adds more. `--crawl` (dist/ only) discovers every `*.html` under the directory.
3. **Run Playwright + axe-core** on each page. Axe is configured to only run the rules tagged for the requested WCAG level.
4. **Aggregate violations**, map axe impacts to P0–P3:
   - `critical` → **P0** (blocks core functionality)
   - `serious`  → **P1** (significant barrier)
   - `moderate` → **P2** (frustrating but workable)
   - `minor`    → **P3** (small annoyance)
5. **Render markdown report** — counts by severity, then findings grouped P0 → P3 with WCAG SC reference, rule docs link, affected elements (HTML snippet + failure summary).
6. **Print summary** to the terminal and write the full report to `--output`.

## When the user asks to "fix" the findings

After producing the report, offer to walk through it and apply fixes. The report includes selectors and HTML snippets sufficient to locate each violation in source. Use the standard edit-review-rebuild loop and re-run `/wcag` to verify.

## Output report structure

```markdown
# WCAG AA Accessibility Audit

**Target**: ...
**Date**: ...
**Pages tested**: N
**Total violations**: N (P0: a, P1: b, P2: c, P3: d)

## Summary
[breakdown by severity, top recurring rules]

## P0 — Critical
### `rule-id` — Rule description
- **WCAG SC**: 1.4.3 (Level AA)
- **Impact**: critical
- **Page**: /about
- **Rule docs**: https://dequeuniversity.com/rules/axe/...
- **Affected elements**: 3

  **Element 1**: `selector`
  ```html
  <div>...</div>
  ```
  *Failure summary from axe*

[P1, P2, P3 follow same shape]

## Incomplete checks
[rules axe couldn't fully evaluate — needs manual review]
```

## First-run setup

On first invocation, the wrapper script:
1. Runs `uv sync` in the skill directory (installs Python deps).
2. Runs `playwright install chromium` if Chromium isn't already present.

Both steps are idempotent; subsequent runs are fast.

## Prerequisites

- `uv` (already required on this machine; see `~/.claude/CLAUDE.md`).
- Network access for first-run Chromium download (~150 MB).

## Notes

- Axe-core covers ~57% of WCAG SCs automatically — visible-on-page rules like contrast, labels, ARIA, landmarks, focus order, parsing. The remaining ~43% (e.g., 1.3.3 sensory characteristics, 2.5.1 pointer gestures, 3.3.4 error prevention) still need a human pass. The report notes this.
- `incomplete` results in axe are rules that *couldn't* be fully verified (e.g., color contrast on text over a background image) — they're surfaced in a separate section, not counted as violations.
- For SPAs that need interaction (open a modal, navigate via JS) before the relevant content renders, you'll currently need to audit those URLs as standalone routes if possible. Multi-step interactions are out of scope for v1.
- The skill respects the user's site as-is — it does not log in, click cookie banners, or take screenshots. If a cookie banner blocks content, audit the post-consent state via direct URL or by adding a query param the site recognizes.
