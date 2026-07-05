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


def _contains_chinese(text: str) -> bool:
    """Check whether a string contains Chinese characters using a regex."""
    if not text:
        return False
    return bool(re.search(r"[\u4e00-\u9fff]", text))


async def search_with_bing_playwright(browser: Browser, query: str, max_results: int = 5):
    """
    Perform an asynchronous search on Bing using a shared browser instance.
    Includes error handling and debug snapshot functionality.

    :param browser: An already-launched Playwright browser instance.
    :param query: The search query.
    :param max_results: Maximum number of results.
    :return: A list of dicts containing title, URL, and summary.
    """
    print(f"--- Starting async search for: '{query}' ---")
    results_data = []
    context = None

    try:
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            locale="zh-CN",
        )
        page = await context.new_page()
        await stealth_async(page)
        # https://cn.bing.com/search?q=
        # --- Force Chinese market and Simplified Chinese via URL parameters ---
        await page.goto("https://cn.bing.com", timeout=60000)

        search_box_selector = "#sb_form_q"
        await page.wait_for_selector(search_box_selector, state="visible", timeout=10000)
        await page.fill(search_box_selector, query)
        await page.press(search_box_selector, "Enter")

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
                screenshot_path = os.path.join(debug_dir, f"error_screenshot_{safe_query}.png")
                html_path = os.path.join(debug_dir, f"error_page_{safe_query}.html")

                await page.screenshot(path=screenshot_path, full_page=True)
                with open(html_path, "w", encoding="utf-8") as f:
                    f.write(await page.content())

                print(f"--- 📸 Screenshot saved to: {screenshot_path} ---")
                print(f"--- 📄 HTML saved to: {html_path} ---")
                print(f"--- Error details: {e} ---")
                break  # Cannot continue, exit loop

            search_items = await page.query_selector_all("li.b_algo")

            if not search_items:
                print(f"--- No more results found on page {page_num} for '{query}'. ---")
                break

            for item in search_items:
                if len(results_data) >= max_results:
                    break

                title_element = await item.query_selector("h2 a")
                summary_element = await item.query_selector("div.b_caption p")

                if title_element:
                    title = await title_element.inner_text()
                    url = await title_element.get_attribute("href")
                    summary = await summary_element.inner_text() if summary_element else "No summary available."

                    # --- Key filtering step: only keep results whose title contains Chinese ---
                    # if url and not url.startswith("https://cn.bing.com/") and _contains_chinese(title):
                    results_data.append({"title": title, "url": url, "summary": summary})
                    # elif not _contains_chinese(title):
                    #     print(f"--- Filtering out non-Chinese result: {title} ---")

            if len(results_data) >= max_results:
                print(f"--- Reached max results ({max_results}) for '{query}'. Stopping. ---")
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
        if context:
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
