"""Map axe-core impact levels to P0-P3 severity buckets."""

from __future__ import annotations

IMPACT_TO_SEVERITY: dict[str, str] = {
    "critical": "P0",
    "serious": "P1",
    "moderate": "P2",
    "minor": "P3",
}

SEVERITY_ORDER: list[str] = ["P0", "P1", "P2", "P3"]

SEVERITY_LABEL: dict[str, str] = {
    "P0": "Critical — blocks core functionality; fix immediately",
    "P1": "Serious — significant barrier; fix urgently",
    "P2": "Moderate — frustrating but workable; fix this cycle",
    "P3": "Minor — small annoyance; fix when convenient",
}


def severity_for_impact(impact: str | None) -> str:
    """Map an axe impact label to a P0-P3 severity. Unknown impact → P3."""
    if impact is None:
        return "P3"
    return IMPACT_TO_SEVERITY.get(impact, "P3")
