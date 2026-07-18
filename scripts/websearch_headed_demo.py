#!/usr/bin/env python3
"""
Headed visualization for the websearch Bing crawler (standalone, no launcher).

Runs the same Playwright Bing SERP path as production, but with a visible
Chromium window so you can compare against a manual browser search.

Known differences vs everyday browsing (read before comparing):
  - Engine is Bing only (cn.bing.com / www.bing.com), not Google/Baidu
  - No login / personalization; fixed Chrome UA + playwright-stealth
  - Navigates directly to SERP URL with mkt/setlang/cc (not homepage form)
  - Forces #b_content visible; extracts answer cards + li.b_algo organics
  - This CLI prints crawler order (raw page scrape), not merge_and_rank_results
    (ranking merge only runs on the FastAPI /search service path)

Usage:
  python scripts/websearch_headed_demo.py "福州天气"
  python scripts/websearch_headed_demo.py "AI 2025 trends" --max-results 8
  python scripts/websearch_headed_demo.py "福州天气" --screenshot
  python scripts/websearch_headed_demo.py "福州天气" --pause 10   # auto-close after 10s
  python scripts/websearch_headed_demo.py "福州天气" --no-wait    # close immediately
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
from datetime import datetime
from pathlib import Path

# scripts/ -> repo root -> src/plugins/websearch/service
_REPO_ROOT = Path(__file__).resolve().parents[1]
_SERVICE_DIR = _REPO_ROOT / "src" / "plugins" / "websearch" / "service"
_PLUGINS_DIR = _REPO_ROOT / "src" / "plugins"
for _p in (_SERVICE_DIR, _PLUGINS_DIR):
    _s = str(_p)
    if _s not in sys.path:
        sys.path.insert(0, _s)

from bing_region import detect_bing_region  # noqa: E402
from playwright.async_api import async_playwright  # noqa: E402
from web_crawler import search_with_bing_playwright  # noqa: E402


def _safe_filename(text: str) -> str:
    cleaned = re.sub(r"[^\w\u4e00-\u9fff\-]+", "_", text.strip(), flags=re.UNICODE)
    return (cleaned[:60] or "query").strip("_")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Headed Bing search demo for the websearch plugin crawler.",
    )
    parser.add_argument("query", help="Search query (same string agents would pass)")
    parser.add_argument(
        "--max-results",
        type=int,
        default=5,
        help="Max results to scrape (default: 5)",
    )
    parser.add_argument(
        "--pause",
        type=float,
        default=None,
        metavar="SECONDS",
        help="Auto-close after N seconds instead of waiting for Enter",
    )
    parser.add_argument(
        "--no-wait",
        action="store_true",
        help="Close the browser immediately after scrape (no pause)",
    )
    parser.add_argument(
        "--screenshot",
        action="store_true",
        help="Save a full-page SERP screenshot under logs/websearch_headed/",
    )
    return parser.parse_args()


async def _run(args: argparse.Namespace) -> int:
    region = detect_bing_region(args.query)
    search_url = region.build_search_url(args.query)

    print("=" * 72)
    print("websearch headed demo")
    print("=" * 72)
    print(f"Query:        {args.query}")
    print(f"Bing region:  {region.label} ({region.base_url})")
    print(f"SERP URL:     {search_url}")
    print()
    print("Compare tip: open the same SERP URL in your daily browser and")
    print("compare ranking, snippets, and answer cards side-by-side.")
    print("=" * 72)

    screenshot_path = None
    if args.screenshot:
        out_dir = _REPO_ROOT / "logs" / "websearch_headed"
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        screenshot_path = str(out_dir / f"{stamp}_{_safe_filename(args.query)}.png")

    chrome_path = os.environ.get("OPENSQUAD_CHROME_PATH", "").strip()
    launch_kwargs: dict = {"headless": False}
    if chrome_path:
        launch_kwargs["executable_path"] = chrome_path

    wait_for_enter = not args.no_wait and args.pause is None
    pause_seconds = 0.0 if args.no_wait else (args.pause or 0.0)
    # Keep SERP page open so results can be printed while you still inspect the window.
    keep_open = wait_for_enter or pause_seconds > 0
    results: list = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(**launch_kwargs)
        try:
            results = await search_with_bing_playwright(
                browser,
                args.query,
                max_results=args.max_results,
                screenshot_path=screenshot_path,
                pause_seconds=0,
                wait_for_enter=False,
                keep_open=keep_open,
            )

            print()
            print("=" * 72)
            print(f"Parsed results ({len(results)}):")
            print("=" * 72)
            if not results:
                print("(no results)")
            else:
                for i, item in enumerate(results, 1):
                    print(f"\n[{i}] type={item.get('result_type', '?')} region={item.get('bing_region', '?')}")
                    print(f"    title:   {item.get('title', '')}")
                    print(f"    url:     {item.get('url', '')}")
                    summary = (item.get("summary") or "").replace("\n", " ").strip()
                    if len(summary) > 240:
                        summary = summary[:237] + "..."
                    print(f"    summary: {summary}")

            print()
            print(f"SERP URL (for manual browser): {search_url}")
            if screenshot_path:
                print(f"Screenshot: {screenshot_path}")

            if wait_for_enter:
                print()
                print("--- Browser stays open. Press Enter in this terminal to close it ---")
                await asyncio.to_thread(input)
            elif pause_seconds > 0:
                print()
                print(f"--- Pausing {pause_seconds:.1f}s so you can inspect the headed browser ---")
                await asyncio.sleep(pause_seconds)
        finally:
            await browser.close()

    return 0 if results else 1


def main() -> None:
    if sys.platform == "win32":
        for stream in (sys.stdout, sys.stderr):
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8", errors="replace")
    args = _parse_args()
    raise SystemExit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
