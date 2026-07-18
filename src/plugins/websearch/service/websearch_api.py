import asyncio
import os
import time

from playwright.async_api import Browser, BrowserContext, async_playwright

try:
    from .fetch_content import fetch_page_content_async
    from .relevance import merge_serp_results
    from .wash_content import wash_content
    from .web_crawler import search_with_bing_playwright
except ImportError:
    from fetch_content import fetch_page_content_async
    from relevance import merge_serp_results
    from wash_content import wash_content
    from web_crawler import search_with_bing_playwright


# ── Env var sanitization ──────────────────────────────────────────────
# PLAYWRIGHT_BROWSERS_PATH is often set manually (e.g. to D:\ms-playwright)
# and a trailing space is a common mistake that makes Playwright look for
# "D:\ms-playwright \chromium-XXXX" (note the space) which never exists.
# Strip surrounding whitespace so the path is always clean.
_pwb_path = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")
if _pwb_path != _pwb_path.strip():
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = _pwb_path.strip()
    print(
        f"[WebSearch] Corrected PLAYWRIGHT_BROWSERS_PATH (stripped whitespace) -> {os.environ['PLAYWRIGHT_BROWSERS_PATH']!r}"
    )


# ── Global browser singleton ──────────────────────────────────────────
_playwright = None
_browser: Browser | None = None
_context: BrowserContext | None = None
_browser_lock = asyncio.Lock()


async def _get_browser(headless: bool = True):
    """Get or create the shared browser instance (lazy init, thread-safe)."""
    global _playwright, _browser
    async with _browser_lock:
        if _browser is not None and _browser.is_connected():
            return _browser
        # Launch new browser
        if _playwright is None:
            _playwright = await async_playwright().start()
        try:
            # Use a system Chrome only when explicitly requested via env var
            # (e.g. OPENSQUAD_CHROME_PATH="C:\Program Files\Google\Chrome\
            # Application\chrome.exe"). Otherwise let Playwright use its
            # bundled Chromium so the path is cross-platform and does not
            # assume a Windows install location.
            chrome_path = os.environ.get("OPENSQUAD_CHROME_PATH", "").strip()
            launch_kwargs = {"headless": headless}
            if chrome_path:
                launch_kwargs["executable_path"] = chrome_path
            _browser = await _playwright.chromium.launch(**launch_kwargs)
        except Exception as e:
            # If the bundled Chromium is missing, surface a clear, actionable
            # error instead of a bare "Executable doesn't exist at ..." so the
            # user knows to run `python -m playwright install chromium`.
            # Do NOT retry launch() — if the binary is missing, the retry will
            # also fail and the uncaught exception would crash the endpoint.
            if "Executable doesn't exist" in str(e):
                print(
                    "[WebSearch] Chromium binary not found. "
                    "Fix: run `python -m playwright install chromium` to download it."
                )
                raise
            # For other errors (e.g. custom chrome_path failed), try without
            # executable_path as fallback.
            _browser = await _playwright.chromium.launch(headless=headless)
        print("[WebSearch] Browser launched (singleton)")
        return _browser


async def _get_context(headless: bool = True):
    """Get or create the shared browser context."""
    global _context
    await _get_browser(headless)
    if _context is None:
        _context = await _browser.new_context(ignore_https_errors=True)
    return _context


async def shutdown_browser():
    """Cleanup: call on plugin unload."""
    global _playwright, _browser, _context
    if _context:
        await _context.close()
        _context = None
    if _browser:
        await _browser.close()
        _browser = None
    if _playwright:
        await _playwright.stop()
        _playwright = None
    print("[WebSearch] Browser singleton shut down")


# ── LRU Cache ─────────────────────────────────────────────────────────
_CACHE_MAX = 200
_search_cache: dict[str, tuple] = {}  # key -> (timestamp, results)
_fetch_cache: dict[str, tuple] = {}
_CACHE_TTL = 300  # 5 minutes


def _cache_get(cache: dict, key: str):
    if key in cache:
        ts, val = cache[key]
        if time.time() - ts < _CACHE_TTL:
            return val
        del cache[key]
    return None


def _cache_set(cache: dict, key: str, val):
    if len(cache) > _CACHE_MAX:
        # Remove oldest 20% entries
        for old_key in list(cache.keys())[:40]:
            cache.pop(old_key, None)
    cache[key] = (time.time(), val)


# ── API 1: Search ─────────────────────────────────────────────────────
async def search_links_async(
    queries: list[str], max_results_per_query: int = 3, headless: bool = True
) -> list[dict[str, str]]:
    cache_key = str(sorted(queries)) + f":{max_results_per_query}"
    cached = _cache_get(_search_cache, cache_key)
    if cached is not None:
        print(f"[WebSearch] Search cache hit: {queries}")
        return cached

    print(f"--- API 1: Starting Link Search for {len(queries)} queries ---")
    start_time = time.time()
    ad_str_list = ["选购"]

    browser = await _get_browser(headless)
    search_tasks = [search_with_bing_playwright(browser, query, max_results=max_results_per_query) for query in queries]
    search_results_list = await asyncio.gather(*search_tasks)

    results = merge_serp_results(
        queries,
        search_results_list,
        ad_str_list=ad_str_list,
    )
    elapsed = time.time() - start_time
    print(f"--- API 1: Finished. Found {len(results)} unique links in {elapsed:.2f}s ---")
    _cache_set(_search_cache, cache_key, results)
    return results


_BLOCK_MARKERS = (
    "Forbid_code",
    "Forbid_code:",
    "您当前请求存在异常，暂时限制本次访问",  # noqa: RUF001
    "访问频率过高",
    "当前访问行为存在异常",
)


def _is_blocked_page(text: str | None) -> bool:
    if not text:
        return False
    sample = text[:4000]
    return any(marker in sample for marker in _BLOCK_MARKERS)


def _blocked_page_message(url: str) -> str:
    return (
        f"[blocked] Site rejected automated fetch for {url} "
        f"(e.g. Forbid_code / WAF). Do not retry fetch/fetch_html on this URL. "
        f"Use websearch.search snippets or answer_card summaries, or try another source."
    )


# ── API 2: Fetch + wash ───────────────────────────────────────────────
async def fetch_and_wash_urls_async(url_infos: list[str], headless: bool = True) -> dict[str, str]:
    if not url_infos:
        return {}

    # Check cache for each URL
    uncached_urls = []
    result = {}
    for url in url_infos:
        cached = _cache_get(_fetch_cache, url)
        if cached is not None:
            result[url] = cached
        else:
            uncached_urls.append(url)

    if not uncached_urls:
        print(f"[WebSearch] Fetch cache hit for all {len(url_infos)} URLs")
        return result

    print(f"\n--- API 2: Starting Content Fetch & Wash for {len(uncached_urls)} URLs (cached {len(result)} urls) ---")
    start_time = time.time()

    ctx = await _get_context(headless)
    sem = asyncio.Semaphore(3)  # max 3 concurrent

    async def _process_single(url):
        async with sem:
            content = await fetch_page_content_async(ctx, url)
            if not content:
                return
            if _is_blocked_page(content):
                msg = _blocked_page_message(url)
                result[url] = msg
                _cache_set(_fetch_cache, url, msg)
                print(f"--- [BLOCKED] {url} ---")
                return
            loop = asyncio.get_running_loop()
            washed = await loop.run_in_executor(None, wash_content, content, url)
            if washed and not _is_blocked_page(washed):
                result[url] = washed
                _cache_set(_fetch_cache, url, washed)
            elif washed and _is_blocked_page(washed):
                msg = _blocked_page_message(url)
                result[url] = msg
                _cache_set(_fetch_cache, url, msg)

    await asyncio.gather(*[_process_single(url) for url in uncached_urls])

    elapsed = time.time() - start_time
    print(f"--- API 2: Finished. Processed {len(result)} URLs in {elapsed:.2f}s ---")
    return result


# ── API 3: Fetch raw HTML ────────────────────────────────────────
async def fetch_html_content_async(url: str | None = None, headless: bool = True) -> str:
    if not url:
        return ""
    cached = _cache_get(_fetch_cache, url)
    if cached is not None:
        return cached

    print(f"--- API 3: Fetching HTML for {url} ---")
    start_time = time.time()
    ctx = await _get_context(headless)
    content = await fetch_page_content_async(ctx, url)
    if content and _is_blocked_page(content):
        content = _blocked_page_message(url)
    if content:
        _cache_set(_fetch_cache, url, content)
    elapsed = time.time() - start_time
    print(f"--- API 3: Finished in {elapsed:.2f}s ---")
    return content or ""


# --- Demo: how to use the new API in two steps ---
async def main():
    # --- Step 1: Use API 1 (Scout) to retrieve link metadata ---
    my_queries = [
        "2025 artificial intelligence development trends",
        "large language models in healthcare",
    ]
    link_results = await search_links_async(queries=my_queries, max_results_per_query=3)

    print("\n\n==================== API 1: Link Search Results ====================")
    if not link_results:
        print("API 1 did not return any links.")
        return

    for i, link in enumerate(link_results):
        print(f"  Result {i + 1}:")
        print(f"    Title: {link['title']}")
        print(f"    URL: {link['url']}")
        print(f"    Summary: {link.get('summary', 'N/A')}")
    print("====================================================================")

    # --- Step 2: User can decide which content to fetch deeply based on the results above ---
    # In this demo, we select all non-zhihu and non-PDF links for deep fetching
    print("\n--- User Filtering: Selecting URLs to process further... ---")
    urls_to_process = [
        link["url"]
        for link in link_results
        if "zhihu.com" not in link["url"] and not link["url"].lower().endswith(".pdf")
    ]

    if not urls_to_process:
        print("--- No URLs selected for deep content fetching. Exiting. ---")
        return

    # --- Step 3: Use API 2 (Strike team) to fetch and clean the selected URLs ---
    content_dictionary = await fetch_and_wash_urls_async(urls_to_process)

    print("\n\n==================== API 2: Washed Content Results ====================")
    if content_dictionary:
        for title, content in content_dictionary.items():
            print(f"\n--- Title: {title} ---")
            print(f"Content Preview: {content[:200].strip()}...")
    else:
        print("API 2 did not return any content.")
    print("======================================================================")


if __name__ == "__main__":
    asyncio.run(main())
