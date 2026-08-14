#!/usr/bin/env python
"""Smoke-test the dashboard by driving it in a real browser.

There is no unit-test suite here - the data is verified by reconciliation
against EIA, and the dashboard was verified by looking at it. This script is
the repeatable half of "looking at it": it walks every control combination on
every tab and fails on anything a screenshot would not have shown you.

    pip install playwright        # chromium only, no other deps
    python scripts/check_dashboard.py

It starts its own server on docs/ (--url to point at a deployed site instead,
e.g. the live GitHub Pages URL after a merge).

Three classes of failure, each from something that actually shipped broken:

1. Console and page errors. A JS exception leaves a half-rendered page that
   looks fine in a screenshot of the tab that still works. The guide link
   inheriting the tab-button click handler threw on every navigation and was
   invisible until this ran.

2. Empty charts. A pivot that silently produces no points renders as an empty
   panel, which reads as "no data that range" rather than as a bug.

3. Horizontal overflow. Checked at every width from a small phone up, because
   a single flex row that cannot fit - the tab strip, the preset pills - sets
   a minimum width for the entire document, and every chart on the page then
   scrolls sideways with it. Nothing about the desktop view hints at it.
"""
from __future__ import annotations

import argparse
import contextlib
import functools
import http.server
import pathlib
import socketserver
import sys
import threading

DOCS = pathlib.Path(__file__).resolve().parent.parent / "docs"
WIDTHS = [320, 390, 460, 768, 1024, 1440]
CHART_TABS = {
    "national": "nationalChart",
    "fuel": "fuelChart",
    "byiso": "byIsoChart",
    "iso": "isoChart",
}


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    """A request log per year file per viewport buries the actual findings."""

    def log_message(self, *args):
        pass


@contextlib.contextmanager
def serve(directory: pathlib.Path):
    handler = functools.partial(QuietHandler, directory=str(directory))
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("127.0.0.1", 0), handler) as httpd:
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            yield f"http://127.0.0.1:{httpd.server_address[1]}"
        finally:
            httpd.shutdown()


def check(base_url: str) -> list[str]:
    from playwright.sync_api import sync_playwright

    problems: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path="/opt/pw-browsers/chromium")
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.on("console", lambda m: problems.append(f"console.error: {m.text}") if m.type == "error" else None)
        page.on("pageerror", lambda e: problems.append(f"pageerror: {e}"))

        page.goto(f"{base_url}/index.html", wait_until="networkidle")
        page.wait_for_timeout(2500)
        if "Failed to load" in (page.text_content("#subtitle") or ""):
            problems.append(f"dashboard did not load: {page.text_content('#subtitle')}")
            browser.close()
            return problems
        print(f"loaded: {page.text_content('#subtitle')}")

        combinations = 0

        def points(var: str) -> int:
            return page.evaluate(
                f"() => (typeof {var} !== 'undefined' && {var})"
                f" ? {var}.data.datasets.reduce((s,d)=>s+d.data.filter(v=>v!==null).length,0) : -1"
            )

        # Chart tabs: every preset against both chart types, plus % where offered.
        for tab, chart_var in CHART_TABS.items():
            page.click(f'.tab-btn[data-view="{tab}"]')
            for preset in ["1D", "1W", "1M", "1Y", "5Y", "Max"]:
                for chart_type in ["stacked", "lines"]:
                    page.click(f'#{tab}-presets .preset-btn:text-is("{preset}")')
                    page.select_option(f"#{tab}-type", chart_type)
                    page.wait_for_timeout(150)
                    combinations += 1
                    if points(chart_var) <= 0:
                        problems.append(f"{tab} {preset} {chart_type}: chart has no points")

        # Every fuel, on the tab where a missing bucket would show up.
        page.click('.tab-btn[data-view="fuel"]')
        page.select_option("#fuel-type", "lines")
        for fuel in page.eval_on_selector_all("#fuel-select option", "els => els.map(e => e.value)"):
            page.select_option("#fuel-select", fuel)
            page.wait_for_timeout(150)
            combinations += 1
            if points("fuelChart") <= 0:
                problems.append(f"fuel {fuel}: chart has no points")

        # Every ISO.
        page.click('.tab-btn[data-view="iso"]')
        for iso in page.eval_on_selector_all("#iso-select option", "els => els.map(e => e.value)"):
            page.select_option("#iso-select", iso)
            page.wait_for_timeout(150)
            combinations += 1
            if points("isoChart") <= 0:
                problems.append(f"iso {iso}: chart has no points")

        # Snapshot: both change units against every period. A change cell may
        # legitimately be blank (nothing to compare against), but a whole panel
        # of blanks means the comparison lookup broke.
        page.click('.tab-btn[data-view="snapshot"]')
        for period in ["1D", "1W", "1M", "1Y", "all"]:
            for mode in ["energy", "share"]:
                page.select_option("#snapshot-period", period)
                page.select_option("#snapshot-mode", mode)
                page.wait_for_timeout(200)
                combinations += 1
                filled = page.eval_on_selector_all(
                    "#snapshot-grid .value.delta", "els => els.filter(e => e.textContent.trim()).length"
                )
                total = page.eval_on_selector_all("#snapshot-grid .value.delta", "els => els.length")
                if total == 0 or filled < total * 0.5:
                    problems.append(f"snapshot {period}/{mode}: only {filled}/{total} change cells populated")

        # Responsive: the page itself must never scroll sideways.
        for width in WIDTHS:
            page.set_viewport_size({"width": width, "height": 900})
            for path in ["index.html", "guide.html"]:
                page.goto(f"{base_url}/{path}", wait_until="networkidle")
                page.wait_for_timeout(1500)
                views = list(CHART_TABS) + ["snapshot"] if path == "index.html" else [None]
                for view in views:
                    if view:
                        page.click(f'.tab-btn[data-view="{view}"]')
                        page.wait_for_timeout(250)
                    if view == "snapshot":
                        page.select_option("#snapshot-period", "all")
                        page.select_option("#snapshot-mode", "share")
                        page.wait_for_timeout(250)
                    combinations += 1
                    body, viewport = page.evaluate(
                        "() => [document.body.scrollWidth, document.documentElement.clientWidth]"
                    )
                    if body > viewport + 1:
                        problems.append(f"{path} {view or ''} at {width}px: page scrolls sideways ({body} > {viewport})")

        browser.close()
    print(f"exercised {combinations} combinations")
    return problems


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", help="Check a deployed site instead of serving docs/ locally")
    args = parser.parse_args()

    if args.url:
        problems = check(args.url.rstrip("/"))
    else:
        with serve(DOCS) as base_url:
            problems = check(base_url)

    if problems:
        print(f"\n{len(problems)} problem(s):")
        for p in problems:
            print(f"  - {p}")
        sys.exit(1)
    print("\nDashboard OK.")


if __name__ == "__main__":
    main()
