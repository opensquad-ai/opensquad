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


def _contains_chinese(text: str) -> bool:
    """Check whether a string contains Chinese characters using a regex."""
    if not text:
        return False
    return bool(re.search(r"[\u4e00-\u9fff]", text))


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
    browser: Browser,
    query: str,
    max_results: int = 5,
    screenshot_path: str | None = None,
    pause_seconds: float = 0,
    wait_for_enter: bool = False,
    keep_open: bool = False,
):
    """
    Perform an asynchronous search on Bing using a shared browser instance.
    Includes error handling and debug snapshot functionality.

    :param browser: An already-launched Playwright browser instance.
    :param query: The search query.
    :param max_results: Maximum number of results.
    :param screenshot_path: Optional path to save a SERP screenshot after #b_results is visible.
    :param pause_seconds: Keep the page open this many seconds before closing (headed demos).
    :param wait_for_enter: If True, wait for Enter in the terminal before closing the page.
    :param keep_open: If True, leave the page/context open for the caller to close later.
    :return: A list of dicts containing title, URL, and summary.
    """
    print(f"--- Starting async search for: '{query}' ---")
    results_data = []
    context = None
    region = detect_bing_region(query)
    search_url = region.build_search_url(query)
    print(f"--- Bing region: {region.label} ({region.base_url}, locale={region.locale}, mkt={region.market}) ---")
    print(f"--- Bing SERP URL: {search_url} ---")

    try:
        context = await browser.new_context(
            user_agent=_CHROME_UA,
            locale=region.locale,
            extra_http_headers={"Accept-Language": region.accept_language},
        )
        page = await context.new_page()
        await stealth_async(page)
        # Direct SERP navigation (mkt/setlang/cc) — closer to a real browser search.
        await page.goto(search_url, timeout=60000, wait_until="domcontentloaded")

        page_num = 1
        while len(results_data) < max_results:
            print(f"--- Scraping page {page_num} for '{query}' ---")

            # --- Anti-anti-crawling: force hidden content visible ---
            # 1. First wait for the core content container to be loaded in the DOM (even if hidden)
            await page.wait_for_selector("#b_content", state="attached", timeout=10000)
            # 2. Then force it visible via JavaScript
            await page.evaluate("document.getElementById('b_content').style.visibility = 'visible';")

            try:
                await page.wait_for_selector("#b_results", state="visible", timeout=10000)
            except PlaywrightError as e:
                print(
                    f"--- [FAIL] Critical Error on page {page_num} for '{query}': Could not find visible results. ---"
                )
                print("--- Saving debug info to help diagnose... ---")

                # Write debug artifacts to the writable workspace logs dir, not a
                # relative "debug_output" (which would resolve to the read-only
                # _internal/ in frozen mode and raise PermissionError).
                try:
                    from plugins._service_runtime import workspace_logs_dir

                    debug_dir = workspace_logs_dir("websearch_debug")
                except Exception:
                    import tempfile

                    debug_dir = os.path.join(tempfile.gettempdir(), "opensquad_websearch_debug")
                os.makedirs(debug_dir, exist_ok=True)

                # Sanitize query string to make it a valid filename
                safe_query = "".join(c for c in query if c.isalnum() or c in (" ", "_")).rstrip()
                error_screenshot_path = os.path.join(debug_dir, f"error_screenshot_{safe_query}.png")
                html_path = os.path.join(debug_dir, f"error_page_{safe_query}.html")

                await page.screenshot(path=error_screenshot_path, full_page=True)
                with open(html_path, "w", encoding="utf-8") as f:
                    f.write(await page.content())

                print(f"--- Screenshot saved to: {error_screenshot_path} ---")
                print(f"--- HTML saved to: {html_path} ---")
                print(f"--- Error details: {e} ---")
                break  # Cannot continue, exit loop

            # Wait until titles/snippets hydrate — otherwise we capture empty strings
            # while a normal browser still shows captions under each blue link.
            await _wait_for_organic_text(page)

            # Optional SERP screenshot for headed demos / manual comparison.
            if screenshot_path and page_num == 1:
                os.makedirs(os.path.dirname(os.path.abspath(screenshot_path)) or ".", exist_ok=True)
                await page.screenshot(path=screenshot_path, full_page=True)
                print(f"--- SERP screenshot saved to: {screenshot_path} ---")

            # Extract organic rows first while the SERP DOM is stable. Answer-card
            # parsing calls page.content() and can race with Bing late navigation.
            remain = max_results - len(results_data)
            page_organics = await _extract_organics_from_page(page, region.label, remain)

            # Answer cards / rich widgets appear on page 1 only; never fail the search.
            if page_num == 1 and len(results_data) < max_results:
                try:
                    answer_cards = await extract_answer_cards_from_page(
                        page,
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
                    results_data.extend(answer_cards)

            if not page_organics and not results_data:
                print(f"--- No more results found on page {page_num} for '{query}'. ---")
                break

            for organic in page_organics:
                if len(results_data) >= max_results:
                    break
                # Dedupe by URL against answer cards already collected.
                if any(r.get("url") == organic["url"] for r in results_data):
                    continue
                results_data.append(organic)

            if len(results_data) >= max_results:
                print(f"--- Reached max results ({max_results}) for '{query}'. Stopping. ---")
                # Keep answer cards first; trim organic overflow while preserving card priority.
                cards = [r for r in results_data if r.get("result_type") == "answer_card"]
                organics = [r for r in results_data if r.get("result_type") != "answer_card"]
                remain = max(0, max_results - len(cards))
                results_data = cards[:max_results] + organics[:remain]
                results_data = results_data[:max_results]
                break

            next_button = await page.query_selector("a.sb_pagN")
            if next_button:
                print(f"--- Clicking 'Next' page for '{query}'. ---")
                await next_button.click()
                page_num += 1
            else:
                print(f"--- No 'Next' page button found for '{query}'. ---")
                break

    except PlaywrightError as e:
        print(f"A top-level error occurred during async search for '{query}': {e}")

    finally:
        if context and not keep_open:
            if wait_for_enter:
                print("--- Browser stays open. Press Enter in this terminal to close it ---")
                await asyncio.to_thread(input)
            elif pause_seconds and pause_seconds > 0:
                print(f"--- Pausing {pause_seconds:.1f}s so you can inspect the headed browser ---")
                await asyncio.sleep(pause_seconds)
            await context.close()

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
