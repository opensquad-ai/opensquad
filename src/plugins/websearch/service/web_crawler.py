import asyncio
import os
import re

from playwright.async_api import Browser, async_playwright
from playwright.async_api import Error as PlaywrightError

try:
    from playwright_stealth import stealth_async
except ImportError:
    try:
        from playwright_stealth import stealth_sync

        async def stealth_async(page):
            await asyncio.to_thread(stealth_sync, page)
    except ImportError:

        async def stealth_async(page):
            pass  # stealth not available, skip


try:
    from .bing_answer_cards import extract_answer_cards_from_page
    from .bing_region import detect_bing_region
except ImportError:
    from bing_answer_cards import extract_answer_cards_from_page
    from bing_region import detect_bing_region

# Keep UA close to a current desktop Chrome so Bing serves a normal SERP,
# not a degraded / bot-shaped layout.
_CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# When Bing treats an automated session as a bot, Chinese weather queries may
# collapse to generic city pages (百科 / 旅游 / 政府站) with zero weather hits.
_WEATHER_QUERY_RE = re.compile(r"(天气|气温|温度|预报|气象|降雨|降水|weather|forecast|tianqi)", re.I)
_WEATHER_RESULT_RE = re.compile(
    r"(天气|气温|温度|预报|气象|降雨|降水|weather|forecast|tianqi|nmc\.cn|weather\.com)",
    re.I,
)


def _contains_chinese(text: str) -> bool:
    """Check whether a string contains Chinese characters using a regex."""
    if not text:
        return False
    return bool(re.search(r"[\u4e00-\u9fff]", text))


def _query_looks_like_weather(query: str) -> bool:
    return bool(_WEATHER_QUERY_RE.search(query or ""))


def _results_look_like_weather(results: list[dict]) -> bool:
    if not results:
        return False
    for item in results[:8]:
        blob = f"{item.get('title', '')} {item.get('summary', '')} {item.get('url', '')}"
        if item.get("result_type") == "answer_card" and item.get("card_kind") == "weather":
            return True
        if _WEATHER_RESULT_RE.search(blob):
            return True
    return False


def _parse_geolocation() -> dict | None:
    """Optional WEBSEARCH_GEO=lat,lon (e.g. 26.08,119.30 for Fuzhou)."""
    raw = os.environ.get("WEBSEARCH_GEO", "").strip()
    if not raw:
        return None
    try:
        lat_s, lon_s = raw.split(",", 1)
        return {"latitude": float(lat_s.strip()), "longitude": float(lon_s.strip())}
    except Exception:
        print(f"[WebSearch] Ignoring invalid WEBSEARCH_GEO={raw!r} (want lat,lon)")
        return None


async def _new_search_context(browser: Browser, region) -> object:
    """Create a SERP context closer to a normal CN desktop browser."""
    geo = _parse_geolocation()
    kwargs: dict = {
        "user_agent": _CHROME_UA,
        "locale": region.locale,
        "extra_http_headers": {"Accept-Language": region.accept_language},
        "timezone_id": "Asia/Shanghai" if region.label == "cn" else "America/New_York",
    }
    if geo:
        kwargs["geolocation"] = geo
        kwargs["permissions"] = ["geolocation"]
    return await browser.new_context(**kwargs)


async def _wait_for_organic_text(page, timeout_ms: int = 8000) -> None:
    """
    Bing often paints li.b_algo shells before hydrating title/snippet text.

    Scraping immediately after #b_results is visible yields empty title/summary
    even though a normal browser later shows both. Wait until at least one
    organic result has real title or caption text.
    """
    try:
        await page.wait_for_function(
            """() => {
                const items = document.querySelectorAll('li.b_algo');
                for (const item of items) {
                    const title = (item.querySelector('h2 a')?.textContent || '').trim();
                    const caption = (
                        item.querySelector('div.b_caption p')?.textContent
                        || item.querySelector('div.b_caption')?.textContent
                        || ''
                    ).trim();
                    if (title.length >= 2 || caption.length >= 8) {
                        return true;
                    }
                }
                return false;
            }""",
            timeout=timeout_ms,
        )
    except PlaywrightError:
        # Best-effort: continue with whatever DOM is present.
        print("--- Organic title/snippet text not ready in time; scraping current DOM ---")


async def _extract_organics_from_page(page, region_label: str, limit: int) -> list[dict[str, str]]:
    """
    Extract organic Bing rows in one JS pass.

    Using page.evaluate avoids stale ElementHandle errors when Bing re-renders
    mid-scrape, and reads textContent so we still get snippets if layout text
    briefly reports empty via inner_text.
    """
    if limit <= 0:
        return []
    raw = await page.evaluate(
        """(limit) => {
            const rows = [];
            const items = document.querySelectorAll('li.b_algo');
            for (const item of items) {
                if (rows.length >= limit) break;
                const h2a = item.querySelector('h2 a');
                const tilk = item.querySelector('a.tilk');
                const titleEl = h2a || tilk;
                if (!titleEl) continue;
                const url = (h2a && h2a.href) || (tilk && tilk.href) || '';
                let title = (h2a && (h2a.textContent || '').trim()) || '';
                if (!title && tilk) {
                    title = (tilk.getAttribute('aria-label') || tilk.textContent || '').trim();
                }
                const captionSelectors = [
                    'div.b_caption p.b_lineclamp2',
                    'div.b_caption p.b_lineclamp3',
                    'div.b_caption p.b_lineclamp4',
                    'div.b_caption p',
                    'div.b_caption .b_algoSlug',
                    'p.b_lineclamp2',
                    'p.b_algoSlug',
                    'div.b_caption',
                ];
                let summary = '';
                for (const sel of captionSelectors) {
                    const el = item.querySelector(sel);
                    const text = (el && el.textContent || '').trim().replace(/\\s+/g, ' ');
                    if (text) { summary = text; break; }
                }
                if (!url) continue;
                if (!title && !summary) continue;
                rows.push({ title: title || '', url, summary: summary || '' });
            }
            return rows;
        }""",
        limit,
    )
    results: list[dict[str, str]] = []
    for row in raw or []:
        summary = (row.get("summary") or "").strip() or "No summary available."
        results.append(
            {
                "title": (row.get("title") or "").strip(),
                "url": (row.get("url") or "").strip(),
                "summary": summary,
                "bing_region": region_label,
                "result_type": "organic",
            }
        )
    return results


async def search_with_bing_playwright(
    browser: Browser | None,
    query: str,
    max_results: int = 5,
    screenshot_path: str | None = None,
    pause_seconds: float = 0,
    wait_for_enter: bool = False,
    keep_open: bool = False,
    shared_context=None,
):
    """
    Perform an asynchronous search on Bing using a shared browser instance.
    Includes error handling and debug snapshot functionality.

    :param browser: Playwright browser (ephemeral mode). Ignored when shared_context is set.
    :param query: The search query.
    :param max_results: Maximum number of results.
    :param screenshot_path: Optional path to save a SERP screenshot after #b_results is visible.
    :param pause_seconds: Keep the page open this many seconds before closing (headed demos).
    :param wait_for_enter: If True, wait for Enter in the terminal before closing the page.
    :param keep_open: If True, leave the page/context open for the caller to close later.
    :param shared_context: Persistent BrowserContext (cookies/profile on disk). Prefer this
        for production so Bing login/cookies survive service restarts.
    :return: A list of dicts containing title, URL, and summary.
    """
    print(f"--- Starting async search for: '{query}' ---")
    results_data: list[dict] = []
    context = None
    owned_context = False
    page = None
    region = detect_bing_region(query)
    search_url = region.build_search_url(query)
    print(f"--- Bing region: {region.label} ({region.base_url}, locale={region.locale}, mkt={region.market}) ---")
    print(f"--- Bing SERP URL: {search_url} ---")

    async def _open_page():
        nonlocal context, owned_context, page
        if page is not None:
            try:
                await page.close()
            except Exception:
                pass
            page = None
        if shared_context is not None:
            context = shared_context
            owned_context = False
            page = await context.new_page()
        else:
            if context and owned_context:
                try:
                    await context.close()
                except Exception:
                    pass
            if browser is None:
                raise RuntimeError("search_with_bing_playwright requires browser or shared_context")
            context = await _new_search_context(browser, region)
            owned_context = True
            page = await context.new_page()
        await stealth_async(page)
        await page.goto(search_url, timeout=60000, wait_until="domcontentloaded")
        return page

    async def _scrape_once(*, shot_path: str | None) -> list[dict]:
        active = await _open_page()
        collected: list[dict] = []
        page_num = 1
        while len(collected) < max_results:
            print(f"--- Scraping page {page_num} for '{query}' ---")

            await active.wait_for_selector("#b_content", state="attached", timeout=10000)
            await active.evaluate(
                "document.getElementById('b_content') && "
                "(document.getElementById('b_content').style.visibility = 'visible')"
            )

            try:
                await active.wait_for_selector("#b_results", state="visible", timeout=10000)
            except PlaywrightError as e:
                print(
                    f"--- [FAIL] Critical Error on page {page_num} for '{query}': "
                    f"Could not find visible results. ---"
                )
                print("--- Saving debug info to help diagnose... ---")
                try:
                    from plugins._service_runtime import workspace_logs_dir

                    debug_dir = workspace_logs_dir("websearch_debug")
                except Exception:
                    import tempfile

                    debug_dir = os.path.join(tempfile.gettempdir(), "opensquad_websearch_debug")
                os.makedirs(debug_dir, exist_ok=True)
                safe_query = "".join(c for c in query if c.isalnum() or c in (" ", "_")).rstrip()
                error_screenshot_path = os.path.join(debug_dir, f"error_screenshot_{safe_query}.png")
                html_path = os.path.join(debug_dir, f"error_page_{safe_query}.html")
                await active.screenshot(path=error_screenshot_path, full_page=True)
                with open(html_path, "w", encoding="utf-8") as f:
                    f.write(await active.content())
                print(f"--- Screenshot saved to: {error_screenshot_path} ---")
                print(f"--- HTML saved to: {html_path} ---")
                print(f"--- Error details: {e} ---")
                break

            await _wait_for_organic_text(active)

            if shot_path and page_num == 1:
                os.makedirs(os.path.dirname(os.path.abspath(shot_path)) or ".", exist_ok=True)
                await active.screenshot(path=shot_path, full_page=True)
                print(f"--- SERP screenshot saved to: {shot_path} ---")

            remain = max_results - len(collected)
            page_organics = await _extract_organics_from_page(active, region.label, remain)

            if page_num == 1 and len(collected) < max_results:
                try:
                    answer_cards = await extract_answer_cards_from_page(
                        active,
                        query,
                        region_label=region.label,
                        base_url=region.base_url,
                        max_cards=min(5, max_results),
                    )
                except Exception as card_exc:  # noqa: BLE001
                    print(f"--- Answer card extraction skipped: {card_exc} ---")
                    answer_cards = []
                if answer_cards:
                    print(f"--- Captured {len(answer_cards)} answer card(s) for '{query}' ---")
                    collected.extend(answer_cards)

            if not page_organics and not collected:
                print(f"--- No more results found on page {page_num} for '{query}'. ---")
                break

            for organic in page_organics:
                if len(collected) >= max_results:
                    break
                if any(r.get("url") == organic["url"] for r in collected):
                    continue
                collected.append(organic)

            if len(collected) >= max_results:
                print(f"--- Reached max results ({max_results}) for '{query}'. Stopping. ---")
                cards = [r for r in collected if r.get("result_type") == "answer_card"]
                organics = [r for r in collected if r.get("result_type") != "answer_card"]
                remain_n = max(0, max_results - len(cards))
                collected = (cards[:max_results] + organics[:remain_n])[:max_results]
                break

            next_button = await active.query_selector("a.sb_pagN")
            if next_button:
                print(f"--- Clicking 'Next' page for '{query}'. ---")
                await next_button.click()
                page_num += 1
            else:
                print(f"--- No 'Next' page button found for '{query}'. ---")
                break

        return collected

    try:
        results_data = await _scrape_once(shot_path=screenshot_path)
        if _query_looks_like_weather(query) and not _results_look_like_weather(results_data):
            print(
                "--- Weather query got non-weather SERP (likely bot/degraded). "
                "Retrying once with a fresh page ---"
            )
            results_data = await _scrape_once(shot_path=None)
    except PlaywrightError as e:
        print(f"A top-level error occurred during async search for '{query}': {e}")

    finally:
        if keep_open or wait_for_enter or (pause_seconds and pause_seconds > 0):
            if wait_for_enter:
                print("--- Browser stays open. Press Enter in this terminal to close it ---")
                await asyncio.to_thread(input)
            elif pause_seconds and pause_seconds > 0:
                print(f"--- Pausing {pause_seconds:.1f}s so you can inspect the headed browser ---")
                await asyncio.sleep(pause_seconds)

        if page is not None and not keep_open:
            try:
                await page.close()
            except Exception:
                pass
            page = None

        if owned_context and context and not keep_open:
            try:
                await context.close()
            except Exception:
                pass
            context = None

    print(f"--- Finished async search for '{query}', found {len(results_data)} results. ---")
    return results_data


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        query = "How do you view the development trends of artificial intelligence in 2025"
        await search_with_bing_playwright(browser, query, max_results=5)
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
