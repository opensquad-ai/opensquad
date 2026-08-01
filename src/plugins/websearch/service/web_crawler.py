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

# When Bing treats an automated cold /search?q= jump as a bot, SERPs may collapse
# to lower-relevance generic pages (e.g. for Chinese weather: 百科 / 旅游 / 政府站).
_WEATHER_QUERY_RE = re.compile(r"(天气|气温|温度|预报|气象|降雨|降水|weather|forecast|tianqi)", re.I)
_WEATHER_RESULT_RE = re.compile(
    r"(天气|气温|温度|预报|气象|降雨|降水|weather|forecast|tianqi|nmc\.cn|weather\.com)",
    re.I,
)
# Hosts that actually publish forecasts (hard allow for weather intent).
_WEATHER_HOST_RE = re.compile(
    r"(?:^|[./])(?:"
    r"weather\.com\.cn|forecast\.weather\.com\.cn|weather\.com|"
    r"tianqi\.com|nmc\.cn|wttr\.in|accuweather\.com|"
    r"msn\.(?:com|cn)/[^?\s]*weather"
    r")",
    re.I,
)
_NON_WEATHER_HOST_RE = re.compile(
    r"(?:^|[./])(?:"
    r"gov\.cn|edu\.cn|baike\.baidu\.com|zhidao\.baidu\.com|"
    r"zhihu\.com|thepaper\.cn|ctrip\.com|163\.com|fuzhou\.gov\.cn"
    r")",
    re.I,
)
_NEWS_QUERY_RE = re.compile(
    r"(新闻|资讯|头条|快讯|要闻|消息|驱动事件|市场动态|rolling|headline|\bnews\b)",
    re.I,
)
# Broad/fact queries where Bing routinely returns portals instead of the actual
# fact page (statistics, GDP, price, ranking, comparison…). We append a focused
# qualifier so the top hits match the concrete intent.
_BROAD_QUERY_RE = re.compile(
    r"(GDP|gdp|统计|统计数据|统计局|报告|白皮书|蓝皮书|数据|排名|排行|榜单|对比|比较|"
    r"价格|报价|多少钱|上市|财报|年报|季报|市场规模|增长率|占比|预测|展望|最新消息|进展)",
    re.I,
)
# Tokens that are too generic to anchor a site: rewrite (keep them, just add qualifiers).
_BROAD_WEAK_QUALIFIERS = (
    "官方数据",
    "官方发布",
    "统计公报",
    "报告全文",
    "最新数据",
    "具体数据",
    "权威来源",
)
# Low "约 N 个结果" vs normal tens-of-thousands is a strong bot-degraded signal.
_RESULT_COUNT_RE = re.compile(r"([\d,，\.\s]+)\s*个结果")
_DEGRADED_RESULT_COUNT = 2000


def _contains_chinese(text: str) -> bool:
    """Check whether a string contains Chinese characters using a regex."""
    if not text:
        return False
    return bool(re.search(r"[\u4e00-\u9fff]", text))


def _weather_city_token(query: str) -> str:
    """Best-effort city token for weather retrieval rewrites."""
    q = (query or "").strip()
    m = re.search(
        r"([\u4e00-\u9fff]{2,8}?)(?:今天|今日|实时|现在|明天|一周|七日|未来|预报)?(?:的)?天气",
        q,
    )
    if m:
        return m.group(1)
    m = re.search(r"([\u4e00-\u9fff]{2,8}?)天气预报", q)
    if m:
        return m.group(1)
    m = re.search(r"\b([A-Za-z][A-Za-z.-]{2,24})\s+(?:weather|forecast|temperature)\b", q, re.I)
    if m:
        return m.group(1)
    if "福州" in q:
        return "福州"
    if re.search(r"\bfuzhou\b", q, re.I):
        return "Fuzhou"
    cjk = re.findall(r"[\u4e00-\u9fff]{2,8}", q)
    if cjk:
        return cjk[0]
    latin = re.findall(r"[A-Za-z]{3,24}", q)
    return latin[0] if latin else q[:12] or "China"


# English city → Chinese for cn.bing site: retrieval (global Bing is weak for weather).
_WEATHER_CITY_EN_ZH = {
    "fuzhou": "福州",
    "beijing": "北京",
    "shanghai": "上海",
    "guangzhou": "广州",
    "shenzhen": "深圳",
    "hangzhou": "杭州",
    "nanjing": "南京",
    "chengdu": "成都",
    "wuhan": "武汉",
    "xiamen": "厦门",
}


def _weather_city_zh(city: str, query: str) -> str | None:
    if _contains_chinese(city):
        return city
    if _contains_chinese(query):
        cjk = re.findall(r"[\u4e00-\u9fff]{2,8}", query)
        if cjk:
            return cjk[0]
    key = re.sub(r"[^a-z]", "", (city or "").lower())
    return _WEATHER_CITY_EN_ZH.get(key)


def _weather_rescue_query(query: str) -> str:
    """Rewrite weather queries to site-scoped retrieval.

    Plain weather queries on Bing (esp. persistent/bot profiles) often rank
    gov/tourism/baike with zero forecast organics. Bare ``weather.com.cn`` in the
    query is NOT enough — Bing still returns portals. Always prefer ``site:``.
    English queries for Chinese cities are rewritten to zh + cn.bing hosts —
    www.bing.com site: bundles frequently return empty under automation.
    """
    q = (query or "").strip()
    if not q:
        return q
    low = q.lower()
    # Already properly site-scoped — leave unchanged.
    if any(
        s in low
        for s in (
            "site:weather.com.cn",
            "site:tianqi.com",
            "site:nmc.cn",
            "site:weather.com",
            "site:msn.com/weather",
            "site:wttr.in",
        )
    ):
        return q
    city = _weather_city_token(q)
    zh = _weather_city_zh(city, q)
    if zh or _contains_chinese(q):
        label = zh or city
        return f"{label} 天气 (site:weather.com.cn OR site:tianqi.com OR site:nmc.cn OR site:msn.com/weather)"
    return (
        f"{city} weather (site:weather.com OR site:weather.com.cn OR site:nmc.cn "
        f"OR site:msn.com/weather OR site:accuweather.com)"
    )


def _query_looks_like_weather(query: str) -> bool:
    return bool(_WEATHER_QUERY_RE.search(query or ""))


def _query_looks_like_news(query: str) -> bool:
    return bool(_NEWS_QUERY_RE.search(query or ""))


def _query_looks_broad(query: str) -> bool:
    """Fact/stat/price queries where Bing SERPs degrade to portals."""
    return bool(_BROAD_QUERY_RE.search(query or ""))


# Site-scoped rewrites for high-value fact queries (mirror the weather site:
# strategy). The first matching intent wins; each steers Bing to the kind of
# domain that actually publishes that data.
_BROAD_SITE_REWRITES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(GDP|gdp|国内生产总值)"), " (site:stats.gov.cn OR site:gov.cn OR site:fuzhou.gov.cn)"),
    (re.compile(r"(统计公报|统计年鉴|统计局|统计数据)"), " (site:stats.gov.cn OR site:gov.cn)"),
    (re.compile(r"(价格|报价|多少钱|房价)"), " (site:baidu.com OR site:anjuke.com OR site:zhongguancun.com.cn)"),
    (re.compile(r"(股票|股价|行情|上证|A股)"), " (site:finance.sina.com.cn OR site:eastmoney.com)"),
)


def _expand_broad_query(query: str) -> str:
    """Rewrite broad fact queries (GDP/statistics/price/ranking…) to focus retrieval.

    Plain "福州市 2026 年 GDP" on Bing returns gov homepage / baike / tourism.
    Appending a concrete qualifier steers the SERP toward the actual data page,
    without over-constraining when the query already looks specific.
    """
    q = (query or "").strip()
    if not q or not _query_looks_broad(q):
        return q
    low = q.lower()
    # Already site-scoped or quoted — leave untouched.
    if "site:" in low or '"' in q:
        return q
    # Never append to very short queries ("GDP", "统计" alone) — no anchor.
    if len(q) < 6:
        return q
    # If a qualifier is already present, don't add another.
    if any(qual in q for qual in _BROAD_WEAK_QUALIFIERS):
        return q
    for pattern, suffix in _BROAD_SITE_REWRITES:
        if pattern.search(q):
            return f"{q}{suffix}"
    return f"{q} {_BROAD_WEAK_QUALIFIERS[0]}"


def _results_look_broad_degraded(results: list[dict]) -> bool:
    """Broad-fact SERP that returned only portal pages (no data page).

    Used as a rescue trigger: when a GDP/stat/price query yields mostly
    baike/gov-home/tourism hits, re-run with the site: rewrite. Mirrors
    _results_look_like_weather but for broad fact queries.
    """
    if not results:
        return True
    portal_hits = 0
    for r in results[:8]:
        url = (r.get("url") or "").lower()
        title = (r.get("title") or "").lower()
        if (
            "baike.baidu.com" in url
            or "zhihu.com/question" in url
            or "travel" in url
            or "trip" in url
            or "攻略" in title
            or "景点" in title
        ):
            portal_hits += 1
    return portal_hits >= max(3, len(results[:8]) // 2)


def _result_is_weather_relevant(item: dict) -> bool:
    """Hard allow-list for weather intent (reranker alone false-positives on gov/baike)."""
    if not item:
        return False
    if item.get("result_type") == "answer_card" and item.get("card_kind") == "weather":
        return True
    url = (item.get("url") or "").strip()
    title = (item.get("title") or "").strip()
    summary = (item.get("summary") or item.get("snippet") or "").strip()
    if _WEATHER_HOST_RE.search(url):
        return True
    if _NON_WEATHER_HOST_RE.search(url):
        return False
    blob = f"{title} {summary} {url}"
    return bool(_WEATHER_RESULT_RE.search(blob))


def _results_look_like_weather(results: list[dict]) -> bool:
    if not results:
        return False
    return any(_result_is_weather_relevant(item) for item in results[:8])


def _filter_weather_intent_results(queries: list[str], results: list[dict]) -> list[dict]:
    """Drop non-weather hits when every query is weather-intent."""
    if not results or not queries:
        return results
    if not all(_query_looks_like_weather(q) for q in queries):
        return results
    return [r for r in results if _result_is_weather_relevant(r)]


def _host_key(url: str) -> str:
    try:
        from urllib.parse import urlparse

        host = (urlparse(url or "").netloc or "").lower()
        if host.startswith("www."):
            host = host[4:]
        return host
    except Exception:
        return ""


def _results_look_degraded(results: list[dict], result_count: int | None) -> bool:
    """
    Bot-shaped SERPs often show a tiny result count and/or dump many hits from one
    low-value domain (e.g. all 52pojie.cn for an A-share news query).
    """
    if result_count is not None and 0 < result_count < _DEGRADED_RESULT_COUNT:
        return True
    hosts = [_host_key(r.get("url", "")) for r in (results or [])[:6]]
    hosts = [h for h in hosts if h]
    return bool(len(hosts) >= 4 and len(set(hosts)) == 1)


async def _read_serp_result_count(page) -> int | None:
    try:
        text = await page.evaluate(
            """() => {
                const el =
                    document.querySelector('.sb_count')
                    || document.querySelector('#b_tween .sb_count')
                    || document.querySelector('#b_tween');
                return el ? (el.textContent || '') : '';
            }"""
        )
    except Exception:
        return None
    m = _RESULT_COUNT_RE.search(text or "")
    if not m:
        return None
    digits = re.sub(r"[^\d]", "", m.group(1))
    if not digits:
        return None
    try:
        return int(digits)
    except ValueError:
        return None


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


# Bing homepage search box selectors (CN / global layouts vary).
_BING_SEARCH_INPUT_SELECTORS = (
    "#sb_form_q",
    "textarea[name='q']",
    "input[name='q']",
    "#sb_form textarea",
    "#sb_form input[type='search']",
)

# Prefer clicking Bing's search button (same path as many manual users) before Enter.
_BING_SEARCH_SUBMIT_SELECTORS = (
    "#search_icon",
    "label[for='sb_form_go']",
    "#sb_form_go",
    "#sb_form button[type='submit']",
    "button#search_icon",
    ".b_searchboxSubmit",
)


def _prefer_search_box_nav() -> bool:
    """
    Default: direct /search?q=... jump (fast; ~5x fewer round-trips than
    homepage→type→submit). Manual homepage typing is still available via
    WEBSEARCH_NAV=search_box for profiles where direct jumps get bot-shaped SERPs.
    """
    mode = os.environ.get("WEBSEARCH_NAV", "direct").strip().lower()
    return mode not in {"direct", "url", "goto", "0", "false", "off", "no"}


def _prefer_form_for_query(query: str) -> bool:
    """Weather keeps homepage typing by default; WEBSEARCH_WEATHER_NAV can override.

    Non-weather queries default to direct URL jumps (fast). Weather queries stay
    on homepage typing + site: rewrite, which is more reliable for forecast SERPs.
    """
    if _query_looks_like_weather(query):
        wnav = os.environ.get("WEBSEARCH_WEATHER_NAV", "").strip().lower()
        if wnav in {"direct", "url", "goto"}:
            return False
        if wnav in {"search_box", "form", "home", "manual"}:
            return True
        return True
    return _prefer_search_box_nav()


def _env_ms(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return max(0, int(raw))
    except ValueError:
        print(f"[WebSearch] Ignoring invalid {name}={raw!r}")
        return default


async def _locate_first_visible(page, selectors: tuple[str, ...], timeout_ms: int = 5000):
    """Return (locator, None) on success, or (None, last_error) if none visible."""
    last_err: Exception | None = None
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            await loc.wait_for(state="visible", timeout=timeout_ms)
            return loc, None
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            continue
    return None, last_err


async def _navigate_via_search_box(
    page,
    region,
    query: str,
    *,
    start_url: str | None = None,
) -> str:
    """
    Open Bing homepage (or News home), fill query once, submit.

    Tunables:
      WEBSEARCH_HOME_SETTLE_MS  pause after homepage is interactive (default 500)
      WEBSEARCH_SUGGEST_WAIT_MS pause after fill before submit (default 200)
      WEBSEARCH_TYPE_DELAY_MS   per-key delay for human-like typing (default 20; 0=fill)
    """
    home = (start_url or (region.base_url.rstrip("/") + "/")).rstrip("/") + "/"
    home_settle = _env_ms("WEBSEARCH_HOME_SETTLE_MS", 500)
    suggest_wait = _env_ms("WEBSEARCH_SUGGEST_WAIT_MS", 200)
    type_delay = _env_ms("WEBSEARCH_TYPE_DELAY_MS", 20)
    print(
        f"--- Navigating via Bing search box ({home}); "
        f"settle={home_settle}ms suggest_wait={suggest_wait}ms "
        f"type_delay={type_delay}ms ---"
    )
    await page.goto(home, timeout=60000, wait_until="domcontentloaded")
    try:
        await page.wait_for_load_state("load", timeout=15000)
    except PlaywrightError:
        pass

    search_input, last_err = await _locate_first_visible(page, _BING_SEARCH_INPUT_SELECTORS, timeout_ms=8000)
    if search_input is None:
        raise PlaywrightError(f"Bing search box not found on {home} (last error: {last_err})")

    if home_settle:
        await asyncio.sleep(home_settle / 1000.0)

    await search_input.click()
    if type_delay > 0:
        await search_input.fill("")
        await search_input.type(query, delay=type_delay)
    else:
        await search_input.fill(query)

    if suggest_wait:
        await asyncio.sleep(suggest_wait / 1000.0)

    submitted = False
    for sel in _BING_SEARCH_SUBMIT_SELECTORS:
        try:
            btn = page.locator(sel).first
            if await btn.count() == 0:
                continue
            if not await btn.is_visible():
                continue
            await btn.click(timeout=3000)
            submitted = True
            print(f"--- Submitted via search button ({sel}) ---")
            break
        except Exception:
            continue
    if not submitted:
        await search_input.press("Enter")
        print("--- Submitted via Enter ---")

    try:
        await page.wait_for_url(re.compile(r".*/(?:news/)?search\?.*"), timeout=20000)
    except PlaywrightError:
        pass
    try:
        await page.wait_for_selector(
            "#b_results, #b_content, .news-card, .card-with-cluster",
            state="attached",
            timeout=20000,
        )
    except PlaywrightError:
        # Still return current URL for logging; scraper will surface the failure.
        pass

    final_url = page.url
    print(f"--- After form submit, landed on: {final_url} ---")
    return final_url


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
                const items = document.querySelectorAll('li.b_algo, .news-card, .card-with-cluster');
                for (const item of items) {
                    const title = (
                        item.querySelector('h2 a, a.title, h2, h3')?.textContent || ''
                    ).trim();
                    const caption = (
                        item.querySelector('div.b_caption p')?.textContent
                        || item.querySelector('div.b_caption')?.textContent
                        || item.querySelector('.snippet, p')?.textContent
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
            const seen = new Set();
            const push = (title, url, summary) => {
                if (!url || seen.has(url)) return;
                if (!title && !summary) return;
                seen.add(url);
                rows.push({ title: title || '', url, summary: summary || '' });
            };
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
                push(title, url, summary);
            }
            // Bing News vertical cards (when li.b_algo is sparse / different layout).
            if (rows.length < limit) {
                const newsNodes = document.querySelectorAll(
                    '.news-card, .card-with-cluster, .newsitem, div[class*="news-card"]'
                );
                for (const item of newsNodes) {
                    if (rows.length >= limit) break;
                    const a = item.querySelector('a[href*="http"]') || item.querySelector('a[href]');
                    if (!a) continue;
                    const url = a.href || '';
                    const title = (
                        (item.querySelector('a.title, h2, h3, .title') || a).textContent || ''
                    ).trim();
                    const summary = (
                        item.querySelector('.snippet, .b_caption, p')?.textContent || ''
                    ).trim().replace(/\\s+/g, ' ');
                    push(title, url, summary);
                }
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
    # Weather intent: search with site: from the first hop. Plain queries on the
    # persistent Bing profile routinely return only gov/tourism/baike.
    active_query = query
    if _query_looks_like_weather(query):
        rewritten = _weather_rescue_query(query)
        if rewritten != query:
            print(f"--- weather retrieval rewrite: {query!r} -> {rewritten!r} ---")
            active_query = rewritten
    region = detect_bing_region(active_query)
    # Experiment: new tab → https://cn.bing.com/ → one-shot fill → submit.
    # News vertical only as degraded fallback (not first hop).
    # Weather keeps site: rewrite + homepage typing (manual-like). Override with
    # WEBSEARCH_WEATHER_NAV=direct if the persistent profile breaks on form submit.
    prefer_form = _prefer_form_for_query(query)
    prefer_news = False
    last_serp_count: int | None = None
    bing_home = region.base_url.rstrip("/") + "/"
    print(f"--- Bing region: {region.label} ({region.base_url}, locale={region.locale}, mkt={region.market}) ---")
    print(f"--- Bing home (new-tab target): {bing_home} ---")
    print(f"--- Bing SERP URL (direct fallback): {region.build_search_url(active_query)} ---")
    print(f"--- Navigate mode: new_tab → {bing_home} → fill ---")

    async def _open_page(*, via_form: bool, use_news: bool, q: str):
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

        # Explicit new tab — same idea as manually opening a tab, then visiting Bing.
        page = await context.new_page()
        print(f"--- Opened new browser tab (total tabs≈{len(context.pages)}) ---")
        await stealth_async(page)

        target_direct = region.build_news_search_url(q) if use_news else region.build_search_url(q)
        start_home = region.base_url.rstrip("/") + ("/news/" if use_news else "/")
        if via_form:
            try:
                print(f"--- New tab → goto {start_home} → fill query → submit ---")
                await _navigate_via_search_box(page, region, q, start_url=start_home)
            except Exception as form_exc:  # noqa: BLE001
                print(f"--- Search-box navigation failed ({form_exc}); falling back to direct URL ---")
                await page.goto(target_direct, timeout=60000, wait_until="domcontentloaded")
        else:
            await page.goto(target_direct, timeout=60000, wait_until="domcontentloaded")
        print(f"--- Active page URL: {page.url} ---")
        return page

    async def _scrape_once(
        *,
        shot_path: str | None,
        via_form: bool,
        use_news: bool,
        q: str | None = None,
    ) -> list[dict]:
        nonlocal last_serp_count
        active_q = (q or active_query or query).strip() or query
        active = await _open_page(via_form=via_form, use_news=use_news, q=active_q)
        collected: list[dict] = []
        page_num = 1
        while len(collected) < max_results:
            print(f"--- Scraping page {page_num} for '{active_q}' ---")

            try:
                await active.wait_for_selector(
                    "#b_content, #b_results, .news-card",
                    state="attached",
                    timeout=10000,
                )
            except PlaywrightError:
                pass
            await active.evaluate(
                "document.getElementById('b_content') && "
                "(document.getElementById('b_content').style.visibility = 'visible')"
            )

            try:
                await active.wait_for_selector(
                    "#b_results, li.b_algo, .news-card",
                    state="visible",
                    timeout=10000,
                )
            except PlaywrightError as e:
                print(
                    f"--- [FAIL] Critical Error on page {page_num} for '{active_q}': Could not find visible results. ---"
                )
                print("--- Saving debug info to help diagnose... ---")
                try:
                    from plugins._service_runtime import workspace_logs_dir

                    debug_dir = workspace_logs_dir("websearch_debug")
                except Exception:
                    import tempfile

                    debug_dir = os.path.join(tempfile.gettempdir(), "opensquad_websearch_debug")
                os.makedirs(debug_dir, exist_ok=True)
                safe_query = "".join(c for c in active_q if c.isalnum() or c in (" ", "_")).rstrip()
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
            if page_num == 1:
                last_serp_count = await _read_serp_result_count(active)
                print(f"--- SERP result count: {last_serp_count} ---")

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
                        active_q,
                        region_label=region.label,
                        base_url=region.base_url,
                        max_cards=min(5, max_results),
                    )
                except Exception as card_exc:  # noqa: BLE001
                    print(f"--- Answer card extraction skipped: {card_exc} ---")
                    answer_cards = []
                if answer_cards:
                    print(f"--- Captured {len(answer_cards)} answer card(s) for '{active_q}' ---")
                    collected.extend(answer_cards)

            # Hard stop: empty organics on this page (even if answer cards already
            # filled ``collected``). Previously ``not page_organics and not collected``
            # kept flipping Next forever after capturing weather/knowledge cards.
            if not page_organics:
                if not collected:
                    print(f"--- No more results found on page {page_num} for '{active_q}'. ---")
                else:
                    print(
                        f"--- No organic links on page {page_num} for '{active_q}' "
                        f"(have {len(collected)} so far). Stopping pagination. ---"
                    )
                break

            added = 0
            for organic in page_organics:
                if len(collected) >= max_results:
                    break
                if any(r.get("url") == organic["url"] for r in collected):
                    continue
                collected.append(organic)
                added += 1

            if len(collected) >= max_results:
                print(f"--- Reached max results ({max_results}) for '{active_q}'. Stopping. ---")
                cards = [r for r in collected if r.get("result_type") == "answer_card"]
                organics = [r for r in collected if r.get("result_type") != "answer_card"]
                remain_n = max(0, max_results - len(cards))
                collected = (cards[:max_results] + organics[:remain_n])[:max_results]
                break

            # Duplicate-only page (Bing loop / thin SERP) — do not keep clicking Next.
            if added == 0:
                print(f"--- Page {page_num} added 0 new URLs for '{active_q}'. Stopping pagination. ---")
                break

            # Cap pages: ~10 organics/page on Bing; never walk dozens of pages.
            max_pages = max(1, min(8, (max_results + 9) // 10 + 1))
            next_button = await active.query_selector("a.sb_pagN")
            if next_button and page_num < max_pages:
                try:
                    print(f"--- Clicking 'Next' page for '{active_q}'. ---")
                    # Use a locator re-query so a Bing re-render that detaches the
                    # stale ElementHandle cannot crash the whole search.
                    await active.locator("a.sb_pagN").first.click(timeout=5000)
                    page_num += 1
                except Exception as nav_exc:  # noqa: BLE001 — keep collected results
                    print(
                        f"--- 'Next' click failed for '{active_q}' ({nav_exc}); keeping {len(collected)} results. ---"
                    )
                    break
            else:
                if page_num >= max_pages:
                    print(
                        f"--- Hit max pages ({max_pages}) for '{active_q}' with {len(collected)} results. Stopping. ---"
                    )
                else:
                    print(f"--- No 'Next' page button found for '{active_q}'. ---")
                break

        return collected

    try:
        results_data = await _scrape_once(shot_path=screenshot_path, via_form=prefer_form, use_news=prefer_news)
        degraded = _results_look_degraded(results_data, last_serp_count)
        weather_bad = _query_looks_like_weather(query) and not _results_look_like_weather(results_data)
        need_retry = (not results_data) or degraded or weather_bad
        if need_retry:
            # Prefer News vertical on retry for news/degraded SERPs; else flip form↔direct.
            retry_news = prefer_news or degraded or _query_looks_like_news(query)
            if prefer_news and degraded:
                # Already tried news and still bad — flip to web + opposite form style.
                retry_news = False
            retry_via_form = not prefer_form if not degraded else prefer_form
            reason = (
                "empty SERP"
                if not results_data
                else (f"degraded SERP (count={last_serp_count})" if degraded else "weather query got non-weather SERP")
            )
            print(
                f"--- {reason}. Retrying once via "
                f"{'news+' if retry_news else 'web+'}"
                f"{'search_box' if retry_via_form else 'direct_url'} ---"
            )
            results_data = await _scrape_once(shot_path=None, via_form=retry_via_form, use_news=retry_news)
            weather_bad = _query_looks_like_weather(query) and not _results_look_like_weather(results_data)

        # Weather SERPs sometimes collapse to tourism/gov (esp. bot-shaped profiles).
        # Force another site:-scoped pass even when rewrite already matched active_query
        # (retry path above may have flipped nav without changing q).
        if weather_bad:
            rescue_q = _weather_rescue_query(query)
            print(f"--- weather SERP still non-weather; rescue query {rescue_q!r} ---")
            results_data = await _scrape_once(
                shot_path=None,
                via_form=False,
                use_news=False,
                q=rescue_q,
            )

        # Broad-query fallback: raw SERP full of portal pages (baike/gov/travel)
        # → re-run with the site: rewrite to pull actual data pages.
        if _query_looks_broad(query) and _results_look_broad_degraded(results_data):
            rescue_q = _expand_broad_query(query)
            if rescue_q != query:
                print(f"--- broad-query SERP degraded ({len(results_data)} portal-y); rescue query {rescue_q!r} ---")
                rescued = await _scrape_once(
                    shot_path=None,
                    via_form=False,
                    use_news=False,
                    q=rescue_q,
                )
                if rescued and not _results_look_broad_degraded(rescued):
                    results_data = rescued
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
