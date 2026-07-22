import asyncio
import os
import time

from playwright.async_api import Browser, BrowserContext, async_playwright

try:
    from .fetch_content import fetch_page_content_async
    from .relevance import merge_serp_results
    from .wash_content import wash_content
    from .web_crawler import _CHROME_UA, _parse_geolocation, search_with_bing_playwright
except ImportError:
    from fetch_content import fetch_page_content_async
    from relevance import merge_serp_results
    from wash_content import wash_content
    from web_crawler import _CHROME_UA, _parse_geolocation, search_with_bing_playwright


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
_persistent_context: BrowserContext | None = None
_browser_lock = asyncio.Lock()


def _persist_profile_enabled() -> bool:
    """WEBSEARCH_PERSIST_PROFILE=0 disables on-disk cookie/profile persistence."""
    return os.environ.get("WEBSEARCH_PERSIST_PROFILE", "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def resolve_browser_profile_dir() -> str:
    """
    Dedicated Chromium/Chrome user-data dir for websearch.

    Persists cookies, localStorage, and profile state across service restarts.
    Do NOT point this at your daily Chrome Default profile while Chrome is open
    (profile lock). Use a dedicated path; seed login via headed --login-setup.
    """
    override = os.environ.get("WEBSEARCH_USER_DATA_DIR", "").strip()
    if override:
        path = os.path.abspath(override)
    else:
        try:
            from plugins._service_runtime import workspace_data_dir
        except ImportError:
            try:
                from _service_runtime import workspace_data_dir
            except ImportError:
                workspace_data_dir = None  # type: ignore[assignment]
        if workspace_data_dir is not None:
            path = workspace_data_dir("plugins", "websearch", "browser_profile")
        else:
            path = os.path.join(os.path.expanduser("~"), ".opensquad", "websearch_browser_profile")
    os.makedirs(path, exist_ok=True)
    return path


def _launch_kwargs(headless: bool) -> dict:
    chrome_path = os.environ.get("OPENSQUAD_CHROME_PATH", "").strip()
    launch_kwargs: dict = {"headless": headless}
    if chrome_path:
        launch_kwargs["executable_path"] = chrome_path
    else:
        launch_kwargs["channel"] = "chrome"
    return launch_kwargs


async def _launch_chromium(playwright, launch_kwargs: dict):
    try:
        return await playwright.chromium.launch(**launch_kwargs)
    except Exception as chrome_exc:
        if "channel" in launch_kwargs:
            print(
                f"[WebSearch] Chrome channel unavailable ({chrome_exc}); "
                "falling back to bundled Chromium"
            )
            launch_kwargs = dict(launch_kwargs)
            launch_kwargs.pop("channel", None)
            return await playwright.chromium.launch(**launch_kwargs)
        raise


async def _launch_persistent_context(playwright, headless: bool) -> BrowserContext:
    """Launch Chrome/Chromium with an on-disk profile (cookies survive restarts)."""
    profile_dir = resolve_browser_profile_dir()
    launch_kwargs = _launch_kwargs(headless)
    geo = _parse_geolocation()
    context_kwargs: dict = {
        **launch_kwargs,
        "user_agent": _CHROME_UA,
        "locale": "zh-CN",
        "timezone_id": "Asia/Shanghai",
        "extra_http_headers": {"Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"},
        "ignore_https_errors": True,
        "viewport": {"width": 1280, "height": 900},
    }
    if geo:
        context_kwargs["geolocation"] = geo
        context_kwargs["permissions"] = ["geolocation"]

    try:
        ctx = await playwright.chromium.launch_persistent_context(profile_dir, **context_kwargs)
    except Exception as chrome_exc:
        if "channel" in context_kwargs:
            print(
                f"[WebSearch] Persistent Chrome channel unavailable ({chrome_exc}); "
                "falling back to bundled Chromium profile"
            )
            context_kwargs.pop("channel", None)
            ctx = await playwright.chromium.launch_persistent_context(profile_dir, **context_kwargs)
        else:
            raise
    print(f"[WebSearch] Persistent browser profile: {profile_dir}")
    return ctx


async def _get_browser(headless: bool = True):
    """Get or create the shared browser instance (lazy init, thread-safe)."""
    global _playwright, _browser
    async with _browser_lock:
        if _browser is not None and _browser.is_connected():
            return _browser
        if _playwright is None:
            _playwright = await async_playwright().start()
        try:
            _browser = await _launch_chromium(_playwright, _launch_kwargs(headless))
        except Exception as e:
            if "Executable doesn't exist" in str(e):
                print(
                    "[WebSearch] Chromium binary not found. "
                    "Fix: run `python -m playwright install chromium` to download it."
                )
                raise
            _browser = await _playwright.chromium.launch(headless=headless)
        print("[WebSearch] Browser launched (singleton)")
        return _browser


async def _get_persistent_context(headless: bool = True) -> BrowserContext:
    """Shared persistent context — cookies/localStorage written under browser_profile."""
    global _playwright, _persistent_context
    async with _browser_lock:
        if _persistent_context is not None:
            try:
                # Touch pages list to detect closed context.
                _ = _persistent_context.pages
                return _persistent_context
            except Exception:
                _persistent_context = None
        if _playwright is None:
            _playwright = await async_playwright().start()
        _persistent_context = await _launch_persistent_context(_playwright, headless)
        return _persistent_context


async def _get_search_handle(headless: bool = True) -> tuple[Browser | None, BrowserContext | None]:
    """
    Return (browser, shared_context) for search.

    Prefer persistent context when enabled so Bing cookies survive restarts.
    """
    if _persist_profile_enabled():
        ctx = await _get_persistent_context(headless)
        return None, ctx
    browser = await _get_browser(headless)
    return browser, None


async def _get_context(headless: bool = True):
    """Get or create the shared browser context (fetch path)."""
    global _context
    if _persist_profile_enabled():
        return await _get_persistent_context(headless)
    await _get_browser(headless)
    if _context is None:
        _context = await _browser.new_context(ignore_https_errors=True)
    return _context


async def shutdown_browser():
    """Cleanup: call on plugin unload."""
    global _playwright, _browser, _context, _persistent_context
    if _persistent_context:
        try:
            await _persistent_context.close()
        except Exception:
            pass
        _persistent_context = None
    if _context:
        try:
            await _context.close()
        except Exception:
            pass
        _context = None
    if _browser:
        try:
            await _browser.close()
        except Exception:
            pass
        _browser = None
    if _playwright:
        try:
            await _playwright.stop()
        except Exception:
            pass
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

    browser, shared_context = await _get_search_handle(headless)
    search_tasks = [
        search_with_bing_playwright(
            browser,
            query,
            max_results=max_results_per_query,
            shared_context=shared_context,
        )
        for query in queries
    ]
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
