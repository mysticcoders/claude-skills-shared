"""Click CLI for the WCAG audit skill."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.parse import urlparse

import click

from .auditor import audit_urls
from .reporter import render_report, render_terminal_summary
from .server import StaticServer, discover_dist_pages


def _is_url(target: str) -> bool:
    parsed = urlparse(target)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _normalize_page(path: str) -> str:
    """Normalize a `--pages` value to a leading-slash path."""
    if path.startswith(("http://", "https://")):
        return path
    if not path.startswith("/"):
        return "/" + path
    return path


def _build_url_list(
    target: str,
    pages: tuple[str, ...],
    crawl: bool,
    server: StaticServer | None,
) -> list[str]:
    if server is not None:
        base = server.base_url
        if pages:
            return [base + _normalize_page(p) for p in pages]
        if crawl:
            return [base + p for p in discover_dist_pages(server.root)]
        return [base + "/"]

    if _is_url(target):
        if pages:
            origin = target.rstrip("/")
            urls: list[str] = []
            for p in pages:
                if _is_url(p):
                    urls.append(p)
                else:
                    urls.append(origin + _normalize_page(p))
            return urls
        return [target]

    raise click.ClickException(
        f"`{target}` is not a URL, directory, or HTML file. "
        "Pass an http(s) URL or a path to a built dist/ directory."
    )


def _viewports_for_flag(flag: str) -> list[str]:
    if flag == "both":
        return ["desktop", "mobile"]
    return [flag]


@click.command()
@click.argument("target")
@click.option(
    "--pages",
    "-p",
    multiple=True,
    help="Specific paths or URLs to audit. Repeatable. Defaults to the root only.",
)
@click.option(
    "--crawl",
    is_flag=True,
    help="When target is a dist/ directory, audit every .html file under it.",
)
@click.option(
    "--level",
    "-l",
    type=click.Choice(["A", "AA", "AAA"]),
    default="AA",
    show_default=True,
    help="Highest WCAG level to enforce.",
)
@click.option(
    "--viewport",
    type=click.Choice(["desktop", "mobile", "both"]),
    default="desktop",
    show_default=True,
    help="Viewport size to test. 'both' runs each page in desktop and mobile.",
)
@click.option(
    "--output",
    "-o",
    type=click.Path(dir_okay=False, writable=True, path_type=Path),
    default=Path("wcag-report.md"),
    show_default=True,
    help="Markdown output path.",
)
@click.option(
    "--json-output",
    type=click.Path(dir_okay=False, writable=True, path_type=Path),
    default=None,
    help="Optional path to write raw axe JSON results.",
)
@click.option(
    "--include-best-practice",
    is_flag=True,
    help="Include axe 'best-practice' rules in addition to WCAG.",
)
@click.option(
    "--timeout",
    type=int,
    default=30,
    show_default=True,
    help="Per-page navigation timeout in seconds.",
)
def main(
    target: str,
    pages: tuple[str, ...],
    crawl: bool,
    level: str,
    viewport: str,
    output: Path,
    json_output: Path | None,
    include_best_practice: bool,
    timeout: int,
) -> None:
    """Audit a URL or local dist/ directory against WCAG using axe-core.

    Examples:

        wcag-audit https://example.com

        wcag-audit https://example.com -p / -p /about -p /contact

        wcag-audit ./dist --crawl --viewport both -o report.md

        wcag-audit ./dist -p / -p /about --level AAA
    """
    target_path = Path(target)
    server: StaticServer | None = None

    if _is_url(target):
        pass
    elif target_path.is_dir():
        server = StaticServer(target_path)
        server.start()
    elif target_path.is_file() and target_path.suffix == ".html":
        # Treat a single HTML file as a one-page dist rooted at its parent.
        server = StaticServer(target_path.parent)
        server.start()
        if not pages:
            pages = ("/" + target_path.name,)
    else:
        raise click.ClickException(
            f"`{target}` is neither a URL nor an existing directory or HTML file."
        )

    try:
        urls = _build_url_list(target, pages, crawl, server)
        if not urls:
            raise click.ClickException("No URLs to audit. Use --pages or --crawl.")

        viewports = _viewports_for_flag(viewport)
        total_runs = len(urls) * len(viewports)
        click.echo(
            f"Auditing {len(urls)} page(s) × {len(viewports)} viewport(s) = "
            f"{total_runs} run(s) at WCAG {level}...",
            err=True,
        )

        def _progress(url: str, vp: str) -> None:
            click.echo(f"  - {url} [{vp}]", err=True)

        results = audit_urls(
            urls,
            level=level,
            viewports=viewports,
            include_best_practice=include_best_practice,
            timeout_seconds=timeout,
            on_progress=_progress,
        )

        report = render_report(
            results,
            target=target,
            level=level,
            include_best_practice=include_best_practice,
        )
        output.write_text(report)
        click.echo(f"\nReport written to {output}", err=True)

        if json_output:
            json_output.write_text(json.dumps(results, indent=2, default=str))
            click.echo(f"JSON results written to {json_output}", err=True)

        click.echo(render_terminal_summary(results, level=level), err=True)

        # Exit code reflects whether any violations were found, useful for CI.
        any_violations = any(p.get("violations") for p in results)
        sys.exit(1 if any_violations else 0)

    finally:
        if server is not None:
            server.stop()


if __name__ == "__main__":
    main()
