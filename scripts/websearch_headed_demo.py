#!/usr/bin/env python3
"""
Headed visualization / profile seeding for the websearch Bing crawler.

Usage:
  python scripts/websearch_headed_demo.py "福州天气"
  python scripts/websearch_headed_demo.py --login-setup
  python scripts/websearch_headed_demo.py "福州天气" --screenshot --no-wait

Persistent profile (cookies / login) lives under:
  <workspace>/data/plugins/websearch/browser_profile
Override with WEBSEARCH_USER_DATA_DIR. Disable with WEBSEARCH_PERSIST_PROFILE=0.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
from datetime import datetime
from pathlib import Path

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
from websearch_api import resolve_browser_profile_dir  # noqa: E402


def _safe_filename(text: str) -> str:
    cleaned = re.sub(r"[^\w\u4e00-\u9fff\-]+", "_", text.strip(), flags=re.UNICODE)
    return (cleaned[:60] or "query").strip("_")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Headed Bing search demo / login seeding for websearch.",
    )
    parser.add_argument(
        "query",
        nargs="?",
        default=None,
        help="Search query (omit when using --login-setup)",
    )
    parser.add_argument(
        "--login-setup",
        action="store_true",
        help="Open persistent Chrome profile on Bing so you can log in; cookies are saved",
    )
    parser.add_argument("--max-results", type=int, default=5)
    parser.add_argument("--pause", type=float, default=None, metavar="SECONDS")
    parser.add_argument("--no-wait", action="store_true")
    parser.add_argument("--screenshot", action="store_true")
    return parser.parse_args()


async def _login_setup() -> int:
    profile = resolve_browser_profile_dir()
    print("=" * 72)
    print("websearch login setup (persistent profile)")
    print("=" * 72)
    print(f"Profile dir: {profile}")
    print()
    print("1) A Chrome window will open on cn.bing.com")
    print("2) Log into Microsoft / Bing if you want personalized results")
    print("3) Optionally search once manually so cookies settle")
    print("4) Press Enter here — profile is flushed on close")
    print("=" * 72)

    from websearch_api import _launch_persistent_context

    async with async_playwright() as p:
        context = await _launch_persistent_context(p, headless=False)
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto("https://cn.bing.com/", timeout=60000, wait_until="domcontentloaded")
            print()
            print("--- Log in in the browser window, then press Enter here ---")
            await asyncio.to_thread(input)
        finally:
            await context.close()
    print(f"Saved profile to: {profile}")
    return 0


async def _run_search(args: argparse.Namespace) -> int:
    region = detect_bing_region(args.query)
    search_url = region.build_search_url(args.query)
    profile = resolve_browser_profile_dir()

    print("=" * 72)
    print("websearch headed demo")
    print("=" * 72)
    print(f"Query:        {args.query}")
    print(f"Bing region:  {region.label} ({region.base_url})")
    print(f"SERP URL:     {search_url}")
    print(f"Profile dir:  {profile}")
    print("=" * 72)

    screenshot_path = None
    if args.screenshot:
        out_dir = _REPO_ROOT / "logs" / "websearch_headed"
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        screenshot_path = str(out_dir / f"{stamp}_{_safe_filename(args.query)}.png")

    wait_for_enter = not args.no_wait and args.pause is None
    pause_seconds = 0.0 if args.no_wait else (args.pause or 0.0)
    keep_open = wait_for_enter or pause_seconds > 0
    results: list = []

    from websearch_api import _launch_persistent_context

    async with async_playwright() as p:
        # Use the same on-disk profile as the service (cookies shared).
        persist = os.environ.get("WEBSEARCH_PERSIST_PROFILE", "1").strip().lower() not in {
            "0",
            "false",
            "no",
            "off",
        }
        if persist:
            context = await _launch_persistent_context(p, headless=False)
            browser = None
            shared = context
        else:
            chrome_path = os.environ.get("OPENSQUAD_CHROME_PATH", "").strip()
            launch_kwargs: dict = {"headless": False}
            if chrome_path:
                launch_kwargs["executable_path"] = chrome_path
            else:
                launch_kwargs["channel"] = "chrome"
            try:
                browser = await p.chromium.launch(**launch_kwargs)
            except Exception as exc:
                if "channel" in launch_kwargs:
                    print(f"Chrome channel unavailable ({exc}); falling back to Chromium")
                    launch_kwargs.pop("channel", None)
                    browser = await p.chromium.launch(**launch_kwargs)
                else:
                    raise
            context = None
            shared = None

        try:
            results = await search_with_bing_playwright(
                browser,
                args.query,
                max_results=args.max_results,
                screenshot_path=screenshot_path,
                pause_seconds=0,
                wait_for_enter=False,
                keep_open=keep_open,
                shared_context=shared,
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
            if context is not None:
                await context.close()
            if browser is not None:
                await browser.close()

    return 0 if results else 1


def main() -> None:
    if sys.platform == "win32":
        for stream in (sys.stdout, sys.stderr):
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8", errors="replace")
    args = _parse_args()
    if args.login_setup:
        raise SystemExit(asyncio.run(_login_setup()))
    if not args.query:
        print("error: query is required unless --login-setup", file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(asyncio.run(_run_search(args)))


if __name__ == "__main__":
    main()
