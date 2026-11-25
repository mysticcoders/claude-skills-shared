"""Render axe-core results as a markdown audit report."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import date
from typing import Iterable

from .severity import SEVERITY_LABEL, SEVERITY_ORDER, severity_for_impact

_WCAG_SC_PATTERN = re.compile(r"^wcag(\d)(\d)(\d+)$")
_WCAG_LEVEL_PATTERN = re.compile(r"^wcag(2|21|22)(a|aa|aaa)$")
_MAX_ELEMENTS_PER_FINDING = 5
_HTML_SNIPPET_MAX = 500


def _wcag_sc_from_tags(tags: list[str]) -> list[str]:
    """Extract WCAG success criteria like '1.4.3' from axe rule tags."""
    scs: list[str] = []
    for tag in tags or []:
        m = _WCAG_SC_PATTERN.match(tag)
        if m:
            scs.append(f"{m.group(1)}.{m.group(2)}.{m.group(3)}")
    # Deduplicate, preserve order
    seen: set[str] = set()
    return [sc for sc in scs if not (sc in seen or seen.add(sc))]


def _wcag_level_from_tags(tags: list[str]) -> str | None:
    """Extract the most stringent WCAG level (A, AA, AAA) from axe tags."""
    levels: list[str] = []
    for tag in tags or []:
        m = _WCAG_LEVEL_PATTERN.match(tag)
        if m:
            levels.append(m.group(2).upper())
    if not levels:
        return None
    rank = {"A": 1, "AA": 2, "AAA": 3}
    return max(levels, key=lambda level: rank.get(level, 0))


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _format_target(target: list) -> str:
    """Stringify an axe `target` (list of CSS selector segments)."""
    if not target:
        return "<unknown>"
    if isinstance(target[0], list):
        # Iframe / shadow DOM nested selector
        return " >>> ".join(", ".join(t) if isinstance(t, list) else str(t) for t in target)
    return ", ".join(str(t) for t in target)


def render_report(
    page_results: list[dict],
    *,
    target: str,
    level: str = "AA",
    include_best_practice: bool = False,
) -> str:
    """Render markdown for a list of axe page results."""

    # Flatten violations across pages, keep page URL + viewport association.
    flat_violations: list[tuple[str, str, dict]] = []
    flat_incomplete: list[tuple[str, str, dict]] = []
    for page in page_results:
        url = page.get("_url", "?")
        viewport = page.get("_viewport", "desktop")
        for v in page.get("violations", []):
            flat_violations.append((url, viewport, v))
        for v in page.get("incomplete", []):
            flat_incomplete.append((url, viewport, v))

    by_severity: dict[str, list[tuple[str, str, dict]]] = defaultdict(list)
    for url, viewport, v in flat_violations:
        sev = severity_for_impact(v.get("impact"))
        by_severity[sev].append((url, viewport, v))

    counts = Counter({sev: len(items) for sev, items in by_severity.items()})

    rule_freq = Counter()
    for _, _, v in flat_violations:
        rule_freq[v.get("id", "?")] += len(v.get("nodes", []))

    # Build markdown.
    lines: list[str] = []
    lines.append(f"# WCAG {level} Accessibility Audit")
    lines.append("")
    lines.append(f"**Target**: {target}")
    lines.append(f"**Date**: {date.today().isoformat()}")
    lines.append(f"**Pages tested**: {len({p.get('_url') for p in page_results})}")

    viewports_used = sorted({p.get("_viewport", "desktop") for p in page_results})
    if viewports_used != ["desktop"]:
        lines.append(f"**Viewports**: {', '.join(viewports_used)}")

    total_violations = sum(counts.values())
    lines.append(f"**Total violations**: {total_violations}")

    if total_violations > 0:
        sev_summary = ", ".join(
            f"{sev}: {counts.get(sev, 0)}" for sev in SEVERITY_ORDER if counts.get(sev, 0) > 0
        )
        lines.append(f"**By severity**: {sev_summary}")

    if flat_incomplete:
        lines.append(f"**Needs manual review**: {len(flat_incomplete)} (incomplete checks)")

    if include_best_practice:
        lines.append("**Includes**: axe best-practice rules in addition to WCAG.")

    test_engine = next(
        (p.get("testEngine") for p in page_results if p.get("testEngine")), None
    )
    if test_engine:
        lines.append(f"**Engine**: axe-core {test_engine.get('version', '?')}")

    lines.append("")

    # Summary block.
    if total_violations == 0:
        lines.append("## Result")
        lines.append("")
        lines.append(f"No WCAG {level} violations detected by axe-core.")
        lines.append("")
        lines.append(
            "Note: axe automatically covers ~57% of WCAG success criteria. "
            "Criteria like 1.3.3 (Sensory Characteristics), 2.5.1 (Pointer Gestures), "
            "and 3.3.4 (Error Prevention) still require manual review."
        )
        if flat_incomplete:
            lines.append("")
            _append_incomplete(lines, flat_incomplete)
        return "\n".join(lines) + "\n"

    lines.append("## Summary")
    lines.append("")
    lines.append("| Severity | Count | Impact |")
    lines.append("| --- | --- | --- |")
    for sev in SEVERITY_ORDER:
        count = counts.get(sev, 0)
        if count == 0:
            continue
        lines.append(f"| **{sev}** | {count} | {SEVERITY_LABEL[sev]} |")
    lines.append("")

    if rule_freq:
        top = rule_freq.most_common(5)
        lines.append("### Top recurring rules")
        lines.append("")
        for rule, count in top:
            lines.append(f"- `{rule}` — {count} affected element(s)")
        lines.append("")

    # Findings grouped by severity.
    for sev in SEVERITY_ORDER:
        items = by_severity.get(sev, [])
        if not items:
            continue
        lines.append(f"## {sev} — {SEVERITY_LABEL[sev]}")
        lines.append("")

        # Group items by rule id so multiple pages with the same rule fold together.
        grouped: dict[str, list[tuple[str, str, dict]]] = defaultdict(list)
        for url, viewport, v in items:
            grouped[v.get("id", "?")].append((url, viewport, v))

        for rule_id, entries in grouped.items():
            sample = entries[0][2]
            scs = _wcag_sc_from_tags(sample.get("tags", []))
            sc_str = ", ".join(scs) if scs else "—"
            wcag_level = _wcag_level_from_tags(sample.get("tags", []))
            level_suffix = f" (Level {wcag_level})" if wcag_level else ""

            lines.append(f"### `{rule_id}` — {sample.get('help', '?')}")
            lines.append("")
            lines.append(f"- **WCAG SC**: {sc_str}{level_suffix}")
            lines.append(f"- **Impact**: {sample.get('impact', '?')}")
            lines.append(f"- **Rule docs**: {sample.get('helpUrl', '?')}")
            lines.append(f"- **Description**: {sample.get('description', '').strip()}")

            total_nodes = sum(len(e[2].get("nodes", [])) for e in entries)
            page_count = len({e[0] for e in entries})
            lines.append(
                f"- **Affected**: {total_nodes} element(s) across {page_count} page(s)"
            )
            lines.append("")

            # Show elements grouped by page (first few per page).
            for url, viewport, v in entries:
                nodes = v.get("nodes", [])
                if not nodes:
                    continue
                vp_suffix = f" ({viewport})" if viewport != "desktop" else ""
                lines.append(f"**Page: `{url}`{vp_suffix}** — {len(nodes)} element(s)")
                lines.append("")
                for i, node in enumerate(nodes[:_MAX_ELEMENTS_PER_FINDING], 1):
                    selector = _format_target(node.get("target", []))
                    html = _truncate(node.get("html", "").strip(), _HTML_SNIPPET_MAX)
                    fail = (node.get("failureSummary") or "").strip()

                    lines.append(f"- Element {i}: `{selector}`")
                    lines.append("  ```html")
                    for ln in html.splitlines() or [""]:
                        lines.append(f"  {ln}")
                    lines.append("  ```")
                    if fail:
                        for fl in fail.splitlines():
                            lines.append(f"  > {fl}")
                    lines.append("")
                if len(nodes) > _MAX_ELEMENTS_PER_FINDING:
                    lines.append(
                        f"  _...and {len(nodes) - _MAX_ELEMENTS_PER_FINDING} more on this page._"
                    )
                    lines.append("")
        lines.append("")

    if flat_incomplete:
        _append_incomplete(lines, flat_incomplete)

    lines.append("---")
    lines.append("")
    lines.append(
        "_axe-core covers approximately 57% of WCAG success criteria automatically. "
        "Manual review is still required for criteria such as 1.3.3 Sensory Characteristics, "
        "2.4.6 Headings and Labels (meaningfulness), 2.5.1 Pointer Gestures, "
        "3.1.5 Reading Level, 3.3.4 Error Prevention, and most cognitive accessibility "
        "criteria. This report is a strong baseline, not a complete audit._"
    )
    lines.append("")
    return "\n".join(lines)


def _append_incomplete(
    lines: list[str], flat_incomplete: list[tuple[str, str, dict]]
) -> None:
    lines.append("## Incomplete checks — needs manual review")
    lines.append("")
    lines.append(
        "axe couldn't fully evaluate these rules (e.g., text over a background image, "
        "computed contrast through opacity stacks). Verify them by hand."
    )
    lines.append("")
    by_rule: dict[str, list[tuple[str, str, dict]]] = defaultdict(list)
    for url, viewport, v in flat_incomplete:
        by_rule[v.get("id", "?")].append((url, viewport, v))

    for rule_id, entries in by_rule.items():
        sample = entries[0][2]
        scs = _wcag_sc_from_tags(sample.get("tags", []))
        sc_str = ", ".join(scs) if scs else "—"
        nodes_total = sum(len(e[2].get("nodes", [])) for e in entries)
        page_urls = sorted({e[0] for e in entries})
        lines.append(f"### `{rule_id}` — {sample.get('help', '?')}")
        lines.append(f"- **WCAG SC**: {sc_str}")
        lines.append(f"- **Affected**: {nodes_total} element(s) across {len(page_urls)} page(s)")
        lines.append(f"- **Pages**: {', '.join(page_urls)}")
        lines.append(f"- **Docs**: {sample.get('helpUrl', '?')}")
        lines.append("")


def render_terminal_summary(
    page_results: list[dict],
    *,
    level: str,
) -> str:
    """Short ANSI-free summary suitable for stderr / terminal output."""
    counts: Counter[str] = Counter()
    incomplete_count = 0
    for page in page_results:
        for v in page.get("violations", []):
            counts[severity_for_impact(v.get("impact"))] += 1
        incomplete_count += len(page.get("incomplete", []))

    total = sum(counts.values())
    pages = len({p.get("_url") for p in page_results})
    lines = [
        "",
        f"WCAG {level} audit complete.",
        f"  Pages tested:   {pages}",
        f"  Violations:     {total}",
    ]
    for sev in SEVERITY_ORDER:
        if counts.get(sev):
            lines.append(f"    {sev}: {counts[sev]}")
    if incomplete_count:
        lines.append(f"  Needs review:   {incomplete_count} incomplete check(s)")
    return "\n".join(lines)
