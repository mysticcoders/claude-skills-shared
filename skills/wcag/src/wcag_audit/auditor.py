"""Run axe-core against pages using Playwright."""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path
from typing import Iterable

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import async_playwright

AXE_JS_PATH = Path(__file__).parent / "axe.min.js"

VIEWPORTS: dict[str, dict[str, int]] = {
    "desktop": {"width": 1280, "height": 800},
    "mobile": {"width": 390, "height": 844},
}

LEVEL_TAGS: dict[str, list[str]] = {
    "A": ["wcag2a", "wcag21a"],
    "AA": ["wcag2a", "wcag21a", "wcag2aa", "wcag21aa"],
    "AAA": ["wcag2a", "wcag21a", "wcag2aa", "wcag21aa", "wcag2aaa", "wcag21aaa"],
}


class BrowserMissingError(RuntimeError):
    """Raised when Playwright's bundled Chromium isn't installed."""


def _ensure_axe_present() -> None:
    if not AXE_JS_PATH.exists():
        raise FileNotFoundError(
            f"axe.min.js not found at {AXE_JS_PATH}. Run "
            "`uv run python -m wcag_audit.install_axe` or re-install the skill."
        )


async def _run_axe_on_page(
    url: str,
    *,
    level: str,
    viewport: str,
    include_best_practice: bool,
    timeout_ms: int,
) -> dict:
    """Open one page, inject axe, return axe.run() results plus metadata."""
    _ensure_axe_present()
    axe_js = AXE_JS_PATH.read_text()

    tags = list(LEVEL_TAGS.get(level, LEVEL_TAGS["AA"]))
    if include_best_practice:
        tags.append("best-practice")

    config = {
        "runOnly": {"type": "tag", "values": tags},
        "resultTypes": ["violations", "incomplete"],
    }

    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch()
        except PlaywrightError as e:
            msg = str(e)
            if "Executable doesn't exist" in msg or "playwright install" in msg:
                raise BrowserMissingError(msg) from e
            raise

        context = await browser.new_context(viewport=VIEWPORTS[viewport])
        page = await context.new_page()

        try:
            await page.goto(url, wait_until="networkidle", timeout=timeout_ms)
        except PlaywrightError:
            # Some pages never reach network idle (long-poll, websockets); fall back
            # to domcontentloaded so we still audit a rendered DOM.
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)

        await page.add_script_tag(content=axe_js)

        results = await page.evaluate(
            """async (config) => {
                const r = await window.axe.run(document, config);
                return {
                    violations: r.violations,
                    incomplete: r.incomplete,
                    testEngine: r.testEngine,
                    testRunner: r.testRunner,
                    testEnvironment: r.testEnvironment,
                    url: r.url,
                    timestamp: r.timestamp
                };
            }""",
            config,
        )

        await browser.close()

    results["_url"] = url
    results["_viewport"] = viewport
    return results


def install_playwright_chromium() -> None:
    """Best-effort run of `playwright install chromium`."""
    print("Installing Playwright Chromium (one-time setup)...", file=sys.stderr)
    subprocess.run(
        [sys.executable, "-m", "playwright", "install", "chromium"],
        check=True,
    )


async def _audit(
    urls: Iterable[str],
    *,
    level: str,
    viewports: list[str],
    include_best_practice: bool,
    timeout_ms: int,
    on_progress=None,
) -> list[dict]:
    results: list[dict] = []
    for url in urls:
        for vp in viewports:
            if on_progress:
                on_progress(url, vp)
            page_result = await _run_axe_on_page(
                url,
                level=level,
                viewport=vp,
                include_best_practice=include_best_practice,
                timeout_ms=timeout_ms,
            )
            results.append(page_result)
    return results


def audit_urls(
    urls: list[str],
    *,
    level: str = "AA",
    viewports: list[str] | None = None,
    include_best_practice: bool = False,
    timeout_seconds: int = 30,
    on_progress=None,
) -> list[dict]:
    """Audit a list of URLs across viewports. Auto-installs Chromium if missing."""
    vps = viewports or ["desktop"]
    timeout_ms = timeout_seconds * 1000

    try:
        return asyncio.run(
            _audit(
                urls,
                level=level,
                viewports=vps,
                include_best_practice=include_best_practice,
                timeout_ms=timeout_ms,
                on_progress=on_progress,
            )
        )
    except BrowserMissingError:
        install_playwright_chromium()
        return asyncio.run(
            _audit(
                urls,
                level=level,
                viewports=vps,
                include_best_practice=include_best_practice,
                timeout_ms=timeout_ms,
                on_progress=on_progress,
            )
        )
