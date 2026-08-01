from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import threading
import time
import urllib.request

from playwright.async_api import Browser, BrowserContext, async_playwright

try:
    from .bing_http import export_cookies_from_storage_state, fetch_serp_http
    from .fetch_content import fetch_page_content_async
    from .relevance import apply_rerank_strategy, merge_serp_results, tokenize_query
    from .wash_content import wash_content
    from .web_crawler import (
        _CHROME_UA,
        _filter_weather_intent_results,
        _parse_geolocation,
        _query_looks_like_weather,
        _weather_rescue_query,
        search_with_bing_playwright,
    )
except ImportError:
    from bing_http import export_cookies_from_storage_state, fetch_serp_http
    from fetch_content import fetch_page_content_async
    from relevance import apply_rerank_strategy, merge_serp_results, tokenize_query
    from wash_content import wash_content
    from web_crawler import (
        _CHROME_UA,
        _filter_weather_intent_results,
        _parse_geolocation,
        _query_looks_like_weather,
        _weather_rescue_query,
        search_with_bing_playwright,
    )


# ── Model-based reranker (Qwen3-Reranker-0.6B) ───────────────────────
# Optional second-stage relevance rerank over merged Bing SERP results.
# The reranker runs as a separate HTTP service (default :8111) using the
# official Qwen3 yes/no scoring method. websearch only calls it here; if the
# service is down, results keep Bing's native order (graceful fallback).
_RERANKER_URL = os.environ.get("WEBSEARCH_RERANKER_URL", "http://127.0.0.1:8111").rstrip("/")
_RERANKER_ENABLED = os.environ.get("WEBSEARCH_RERANKER_ENABLED", "1").strip().lower() not in {"0", "false", "no", "off"}
# Rerank cap: score at most this many results per search.
# - GTX 1060 (GPU): 20 rows ~4.5s, 10 rows ~1.7s.
# - Agent Python (CPU, torch+cpu): 20 rows ~14.3s (right at the 15s timeout),
#   10 rows ~7s — the cap is 10 so CPU deployments keep a comfortable margin
#   while still ranking the meaningful top of the SERP.
_RERANK_MAX_ROWS = 10
# Skip reranker calls for this long after a failure (avoid per-request timeouts).
_reranker_down_until = 0.0

# ── Reranker stability: LRU cache + concurrency mutex ──────────────────
# GTX 1060 scores ~150ms/row; identical (query, document) pairs recur across
# calls (same query, same SERP), so caching skips redundant GPU work. The
# mutex serializes in-flight rerank calls so a slow batch never stacks.
_RERANK_CACHE_MAX = 512
_rerank_cache: dict[str, tuple[float, list[float]]] = {}
_rerank_cache_lock = threading.Lock()
_rerank_call_lock = threading.Lock()


def _rerank_cache_key(queries: list[str], documents: list[str]) -> str:
    import hashlib

    h = hashlib.sha1()
    for q, d in zip(queries, documents, strict=True):
        h.update(q.encode("utf-8", "replace"))
        h.update(b"\x00")
        h.update(d.encode("utf-8", "replace"))
        h.update(b"\x01")
    return h.hexdigest()


def _rerank_cache_get(key: str) -> list[float] | None:
    with _rerank_cache_lock:
        hit = _rerank_cache.get(key)
        if hit is None:
            return None
        ts, scores = hit
        # 10 min TTL; results drift as SERPs change but not within a session.
        if time.time() - ts > 600:
            _rerank_cache.pop(key, None)
            return None
        return scores


def _rerank_cache_set(key: str, scores: list[float]) -> None:
    with _rerank_cache_lock:
        if len(_rerank_cache) >= _RERANK_CACHE_MAX:
            # Drop oldest 20%.
            for old_key in list(_rerank_cache.keys())[: _RERANK_CACHE_MAX // 5]:
                _rerank_cache.pop(old_key, None)
        _rerank_cache[key] = (time.time(), scores)


def _rerank_scores_sync(queries: list[str], documents: list[str], timeout: float = 15.0) -> list[float] | None:
    """POST {queries, documents} to the reranker service; return aligned scores.

    Returns None on any failure (service down, error, mismatched length) so the
    caller falls back to Bing order. ``queries`` is one query per document so
    each result is scored against the query that actually found it.
    """
    global _reranker_down_until
    if not _RERANKER_ENABLED or not documents:
        return None
    if time.time() < _reranker_down_until:
        return None

    cache_key = _rerank_cache_key(queries, documents)
    cached = _rerank_cache_get(cache_key)
    if cached is not None:
        return cached

    payload = json.dumps({"queries": queries, "documents": documents}).encode("utf-8")
    req = urllib.request.Request(
        f"{_RERANKER_URL}/rerank",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        # Serialize in-flight rerank calls: slow GPU batches must not stack.
        with _rerank_call_lock:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        scores = list(data.get("scores", []))
        if len(scores) != len(documents):
            return None
        _rerank_cache_set(cache_key, scores)
        return scores
    except Exception as e:
        # Connection refused (WinError 10061) usually means the reranker is
        # still loading — retry soon instead of tripping the long circuit
        # breaker, which would make every search skip rerank for 30s.
        e_text = str(e)
        if "10061" in e_text or "Connection refused" in e_text or "拒绝" in e_text:
            print(f"[WebSearch] reranker not ready yet ({e_text}); will retry shortly")
            _reranker_down_until = time.time() + 3.0
        else:
            print(f"[WebSearch] reranker unavailable ({e_text}); keeping Bing order")
            _reranker_down_until = time.time() + 30.0
        return None


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
# Shared persistent profile cannot safely parallelize Bing scrapes.
_bing_search_lock = asyncio.Lock()


def _env_flag(name: str, default: str = "0") -> bool:
    return os.environ.get(name, default).strip().lower() not in {"0", "false", "no", "off", ""}


def resolve_headless() -> bool:
    """WEBSEARCH_HEADLESS=0 → headed Chrome (closer to manual). Default: headless."""
    return _env_flag("WEBSEARCH_HEADLESS", "1")


def resolve_serial_search() -> bool:
    """WEBSEARCH_SERIAL=0 allows parallel Bing scrapes (unsafe with persistent profile)."""
    return _env_flag("WEBSEARCH_SERIAL", "1")


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
            print(f"[WebSearch] Chrome channel unavailable ({chrome_exc}); falling back to bundled Chromium")
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
        # Export Bing cookies once so the http-direct path (bing_http) can skip
        # Chrome entirely for subsequent searches (1-2s instead of 5-10s).
        try:
            state = await _persistent_context.storage_state()
            n = export_cookies_from_storage_state(state)
            if not n:
                print("[WebSearch] no Bing cookies exported; http-direct search disabled")
        except Exception as e:
            print(f"[WebSearch] cookie export failed (non-fatal): {e}")
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


async def run_login_setup() -> None:
    """
    Headed Chrome with the persistent websearch profile for manual Bing login.

    Run:  python service/main.py --login-setup
    After signing into Bing/Microsoft in the window, press Enter here so cookies
    are flushed to disk for subsequent headless/headed service searches.
    """
    try:
        from playwright_stealth import stealth_async as _stealth
    except ImportError:

        async def _stealth(page):  # type: ignore[misc]
            return None

    await shutdown_browser()
    os.environ["WEBSEARCH_HEADLESS"] = "0"
    os.environ.setdefault("WEBSEARCH_PERSIST_PROFILE", "1")
    profile = resolve_browser_profile_dir()
    print("[WebSearch] Login setup")
    print(f"[WebSearch] Profile dir: {profile}")
    print("[WebSearch] Opening headed Chrome → https://cn.bing.com/ …")
    print("[WebSearch] Log into Bing / Microsoft Account in that window if needed.")
    print("[WebSearch] Then return here and press Enter to save cookies and exit.")

    ctx = await _get_persistent_context(headless=False)
    page = ctx.pages[0] if ctx.pages else await ctx.new_page()
    try:
        await _stealth(page)
    except Exception:
        pass
    try:
        await page.goto("https://cn.bing.com/", wait_until="domcontentloaded", timeout=60000)
    except Exception as exc:
        print(f"[WebSearch] goto cn.bing.com failed ({exc}); leaving blank tab open")
    await asyncio.to_thread(input, "\n>>> Press Enter after login to save profile and exit… ")
    # Touch storage so Chromium flushes cookies.
    try:
        await page.goto("https://cn.bing.com/", wait_until="domcontentloaded", timeout=30000)
    except Exception:
        pass
    await shutdown_browser()
    print("[WebSearch] Login setup done. Restart the websearch service to use the profile.")
    try:
        # Prefer sibling package path when launched as service/main.py
        _plugins = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        if _plugins not in sys.path:
            sys.path.insert(0, _plugins)
        from websearch.setup_status import mark_login_done, write_plugin_status

        mark_login_done(source="login_setup")
        write_plugin_status()
    except Exception as exc:
        print(f"[WebSearch] Could not write login marker / status.json: {exc}")


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


def _rerank_payload(
    results: list[dict],
    *,
    status: str,
    applied: bool,
    filtered: bool = False,
    before_count: int | None = None,
) -> dict:
    """Build the search payload agents see (results + rerank transparency)."""
    after = len(results)
    before = after if before_count is None else before_count
    if status == "applied":
        note = (
            "Relevance rerank applied (Qwen3-Reranker). Results are ordered by "
            "relevance; low-relevance SERP noise may have been filtered out."
            if filtered
            else "Relevance rerank applied (Qwen3-Reranker). Results are ordered "
            "by relevance; no items fell below the noise threshold."
        )
    elif status == "applied_all_noise":
        note = (
            "Relevance rerank applied, but ALL Bing hits scored as off-topic "
            "(retrieval miss). Results are empty — do not cite the prior SERP; "
            "retry with a more specific query (e.g. add 中国天气网 / weather.com.cn) "
            "or fetch a known weather URL such as https://wttr.in/<city>?format=j1&lang=zh."
        )
    elif status == "disabled":
        note = (
            "Relevance rerank disabled (WEBSEARCH_RERANKER_ENABLED=0). "
            "Results follow Bing SERP order and were not relevance-filtered."
        )
    elif status == "skipped_empty":
        note = "No search hits; relevance rerank was not run."
    elif status == "skipped_weather":
        note = (
            "Weather-intent query; results were hard-filtered by the weather "
            "allow-list (site: retrieval + host rules). Model rerank skipped "
            "since every surviving row is weather-relevant."
        )
    else:
        # unavailable / unknown
        note = (
            "Relevance rerank NOT applied (reranker unavailable or timed out). "
            "Results follow Bing SERP order and were not relevance-filtered."
        )
    return {
        "results": results,
        "relevance_rerank_applied": applied,
        "relevance_rerank_status": status,
        "filtered_by_relevance": filtered,
        "result_count_before_filter": before,
        "result_count": after,
        "relevance_rerank_note": note,
    }


def _dynamic_rerank_window(
    results: list[dict],
    queries: list[str],
    *,
    cap: int = _RERANK_MAX_ROWS,
) -> tuple[list[dict], list[dict]]:
    """Pick which rows the model scores (window) vs which keep Bing order (tail).

    When more rows than ``cap`` survive, we still want every row that carries
    the query's own keywords to be model-scored (keyword hits are the strongest
    cheap relevance signal). Rows below the cap are kept, then keyword-free rows
    are pushed to the tail so the GPU only scores likely-relevant candidates.
    """
    if len(results) <= cap:
        return results, []
    tokens: set[str] = set()
    for q in queries:
        tokens.update(t.lower() for t in tokenize_query(q))
    if not tokens:
        return results[:cap], results[cap:]
    scored: list[dict] = []
    tail: list[dict] = []
    for r in results:
        blob = f"{r.get('title', '')} {r.get('snippet', '') or r.get('summary', '')} {r.get('url', '')}".lower()
        hit = any(tok in blob for tok in tokens)
        (scored if hit else tail).append(r)
    if len(scored) > cap:
        scored = scored[:cap]
    return scored, tail


def _detect_region_for_query(query: str) -> dict[str, str]:
    """Resolve Bing endpoint params for a query (mirrors web_crawler's region)."""
    try:
        from .bing_region import detect_bing_region
    except ImportError:
        from bing_region import detect_bing_region  # type: ignore[no-redef]
    region = detect_bing_region(query)
    return {
        "base_url": region.base_url,
        "market": region.market,
        "setlang": region.setlang,
        "country_code": region.country_code,
    }


# ── Query decomposition (borrowed from proxyless-llm-websearch, rule-based) ──
# proxyless uses an LLM to split a question into sub-queries. We emulate the
# key benefit locally with cheap rules: for multi-topic / bilingual queries,
# fetch a few focused variants and merge by URL, so the reranker sees a richer
# candidate set. Expansion is bounded and only enabled when it is likely to help.
_QUERY_JOINERS = re.compile(r"\s+(?:and|与|和|及|跟|以及|还有|、|,|，)\s+", re.I)
# Trailing generic words whose removal yields a more focused query.
_QUERY_TAIL_WORDS = (
    "教程",
    "应用",
    "最新",
    "大全",
    "排名",
    "报告",
    "是什么",
    "怎么",
    "如何",
    "介绍",
    "推荐",
    "大模型",
    "llm",
    "用法",
)
# Chinese technical terms → focused English variant (Bing global indexes them
# differently, often catching the exact topic when zh SERP drifts).
_ZH_EN_VARIANTS = {
    "人工智能": "AI artificial intelligence",
    "大模型": "LLM large language model",
    "检索增强生成": "RAG retrieval augmented generation",
    "机器学习": "machine learning",
    "深度学习": "deep learning",
    "神经网络": "neural network",
    "异步": "asyncio async",
    "并发": "concurrency parallel",
    "搜索引擎": "search engine",
    "websearch": "web search",
}


def _query_variants(query: str) -> list[str]:
    """Return [query] plus focused variants, deduped, max 3 total."""
    q = (query or "").strip()
    if not q or len(q) < 6:
        return [q]
    # Weather queries already get a dedicated site: rescue rewrite
    # (_weather_rescue_query in web_crawler) and skip the model rerank
    # (skipped_weather). Decomposition is harmful here: e.g. stripping CJK
    # from "北京天气 中国天气网 weather.com.cn 101010100" yields
    # "weather.com.cn 101010100", which Bing misreads as a random city code.
    if _query_looks_like_weather(q):
        return [q]

    variants: list[str] = [q]
    seen = {q}

    # 1) Split on topic joiners: "python asyncio 并发 教程" -> "python asyncio".
    parts = [p.strip() for p in _QUERY_JOINERS.split(q) if p.strip()]
    if len(parts) >= 2 and all(len(p) >= 3 for p in parts):
        for part in parts:
            if part not in seen and len(part) >= 4:
                seen.add(part)
                variants.append(part)

    # 2) Drop a trailing generic word ("教程/应用/最新") to sharpen the core topic.
    low = q.lower()
    for tail in _QUERY_TAIL_WORDS:
        if low.endswith(tail) and len(q) - len(tail) >= 6:
            core = q[: -len(tail)].strip()
            if core not in seen:
                seen.add(core)
                variants.append(core)
            break

    # 3) Bilingual pair: pure Latin/English form for mixed CJK+Latin queries.
    has_cjk = bool(re.search(r"[\u4e00-\u9fff]", q))
    has_latin = bool(re.search(r"[a-zA-Z]{3,}", q))
    if has_cjk and has_latin:
        latin_only = re.sub(r"[\u4e00-\u9fff]+", " ", q).strip()
        latin_only = re.sub(r"\s+", " ", latin_only)
        # Drop fragments that are mostly a URL/code (e.g. "weather.com.cn
        # 101010100"), which mislead Bing into city-code guesses. Only keep a
        # pure-Latin variant if it still reads like a real phrase.
        looks_like_url = bool(re.search(r"\.(com|cn|net|org|io)\b|www\.", latin_only, re.I))
        has_real_word = bool(re.search(r"[a-zA-Z]{5,}", latin_only)) and not looks_like_url
        if has_real_word and len(latin_only) >= 4 and latin_only not in seen:
            seen.add(latin_only)
            variants.append(latin_only)

    # 4) Chinese technical term → English variant.
    if has_cjk:
        zh_tokens = re.findall(r"[\u4e00-\u9fff]{2,8}", q)
        for token in zh_tokens:
            en = _ZH_EN_VARIANTS.get(token)
            if en and en not in seen and len(variants) < 3:
                # Keep the English as a standalone focused variant.
                seen.add(en)
                variants.append(en)

    # Keep at most 3 variants to bound scraping cost.
    return variants[:3]


# ── API 1: Search ─────────────────────────────────────────────────────
async def search_links_async(queries: list[str], max_results_per_query: int = 30, headless: bool | None = None) -> dict:
    """Search Bing and optionally rerank. Returns a dict for the agent tool.

    Keys: ``results``, ``relevance_rerank_applied``, ``relevance_rerank_status``,
    ``filtered_by_relevance``, ``result_count_before_filter``, ``result_count``,
    ``relevance_rerank_note``.

    ``headless`` defaults to ``resolve_headless()`` (``WEBSEARCH_HEADLESS``, default on).
    """
    if headless is None:
        headless = resolve_headless()
    cache_key = str(sorted(queries)) + f":{max_results_per_query}"
    cached = _cache_get(_search_cache, cache_key)
    if cached is not None:
        print(f"[WebSearch] Search cache hit: {queries}")
        # Older cache entries were bare lists; wrap for agent-facing shape.
        if isinstance(cached, list):
            return _rerank_payload(cached, status="unavailable", applied=False)
        return cached

    print(f"--- API 1: Starting Link Search for {len(queries)} queries ---")
    print(f"[WebSearch] headless={headless} serial={resolve_serial_search()}")
    start_time = time.time()
    ad_str_list = ["选购"]

    # ── Fast path: http-direct Bing (reuses exported persistent cookies) ──
    # Try to serve the whole request with httpx first; only fall back to
    # Playwright for queries where the http path failed. This skips Chrome
    # launch/render entirely on the hot path (~1-2s vs 5-10s).
    #
    # Query decomposition (borrowed from proxyless): each input query expands
    # into up to 3 focused variants (split on joiners / bilingual), fetched
    # concurrently over httpx, then merged by URL so the reranker sees a richer
    # candidate set. The original query always comes first.
    variant_map: dict[str, list[str]] = {q: _query_variants(q) for q in queries}
    http_rows: dict[str, list] = {}
    http_ok = 0
    total_variants = sum(len(vs) for vs in variant_map.values())
    variant_index: list[tuple[str, str]] = []  # (original_query, variant)
    for q, vs in variant_map.items():
        variant_index.extend((q, v) for v in vs)

    if total_variants > 0:

        async def _fetch_variant(q: str, v: str):
            # Weather queries need the site: rescue rewrite even on the http
            # path (the Playwright path does this inside search_with_bing_playwright;
            # http-direct must mirror it or raw weather SERPs degrade to
            # baike/gov/travel → all filtered as noise).
            active = _weather_rescue_query(v) if _query_looks_like_weather(v) else v
            region = _detect_region_for_query(active)
            try:
                rows = await asyncio.to_thread(
                    fetch_serp_http,
                    active,
                    base_url=region["base_url"],
                    market=region["market"],
                    setlang=region["setlang"],
                    country_code=region["country_code"],
                    max_results=max_results_per_query,
                )
            except Exception as e:
                print(f"[WebSearch] http-direct error for {v!r}: {e}")
                return q, v, None
            return q, v, rows

        fetched = await asyncio.gather(*[_fetch_variant(q, v) for q, v in variant_index])
        for q, v, rows in fetched:
            if rows:
                http_rows[v] = rows
                http_ok += 1
        print(f"[WebSearch] http-direct fetched {http_ok}/{total_variants} variants")

        # Merge variants per original query: original query's rows first, then
        # variant rows (dedup by url). The result is a per-original-query list.
        def _merge_variants(q: str) -> list:
            merged: list[dict] = []
            seen: set[str] = set()
            for v in variant_map[q]:
                for r in http_rows.get(v, []):
                    u = r.get("url")
                    if u and u not in seen:
                        seen.add(u)
                        merged.append(r)
            return merged

        # Only treat as full-fast-path if every original query got >=1 row.
        search_results_list = [_merge_variants(q) for q in queries]
        if all(bool(rows) for rows in search_results_list):
            print("[WebSearch] http-direct search: all queries served (fast path)")
            shared_context = None
        else:
            # Some originals empty — fall through to Playwright for those.
            fallback_queries = [q for q, rows in zip(queries, search_results_list, strict=True) if not rows]
            partial_rows: dict[str, list] = {
                q: rows for q, rows in zip(queries, search_results_list, strict=True) if rows
            }
            print(
                f"[WebSearch] http-direct partial ({len(partial_rows)}/{len(queries)}); Playwright for {len(fallback_queries)}"
            )

            browser, shared_context = await _get_search_handle(headless)
            serial = resolve_serial_search() or shared_context is not None

            if len(fallback_queries) > 1 and browser is not None and not serial:

                async def _run_parallel(query: str):
                    ctx = await browser.new_context(
                        ignore_https_errors=True,
                        user_agent=_CHROME_UA,
                        locale="zh-CN",
                    )
                    try:
                        return await search_with_bing_playwright(
                            None,
                            query,
                            max_results=max_results_per_query,
                            shared_context=ctx,
                        )
                    finally:
                        try:
                            await ctx.close()
                        except Exception:
                            pass

                fallback_rows = await asyncio.gather(*[_run_parallel(q) for q in fallback_queries])
            else:

                async def _run_one(query: str):
                    if serial:
                        async with _bing_search_lock:
                            return await search_with_bing_playwright(
                                browser,
                                query,
                                max_results=max_results_per_query,
                                shared_context=shared_context,
                            )
                    return await search_with_bing_playwright(
                        browser,
                        query,
                        max_results=max_results_per_query,
                        shared_context=shared_context,
                    )

                fallback_rows = await asyncio.gather(*[_run_one(q) for q in fallback_queries])

            # Merge partial http rows + Playwright fallback rows.
            fb_iter = iter(fallback_rows)
            final_list: list[list] = []
            for q in queries:
                if q in partial_rows:
                    final_list.append(partial_rows[q])
                else:
                    final_list.append(next(fb_iter))
            search_results_list = final_list

    # Weather miss on polluted persistent profile → one ephemeral Chromium pass.
    if (
        shared_context is not None
        and any(_query_looks_like_weather(q) for q in queries)
        and not any(bool(rows) for rows in search_results_list)
    ):
        print("--- weather SERP empty on persistent profile; retrying ephemeral browser ---")
        eph_browser = await _get_browser(headless)
        eph_rows: list = []
        for q in queries:
            async with _bing_search_lock:
                eph_rows.append(
                    await search_with_bing_playwright(
                        eph_browser,
                        q,
                        max_results=max_results_per_query,
                        shared_context=None,
                    )
                )
        search_results_list = eph_rows

    results = merge_serp_results(
        queries,
        search_results_list,
        ad_str_list=ad_str_list,
    )
    serp_count = len(results)
    # Weather intent: drop gov/baike/tourism even if Bing ranked them high.
    # Reranker alone false-positives (~0.2–0.6) on city-name portal pages.
    results = _filter_weather_intent_results(queries, results)

    # Optional Qwen3-Reranker pass: score each result against the query that
    # found it, then reorder / drop clear noise. Expose status so agents know
    # whether the list was relevance-filtered.
    if not results:
        if serp_count > 0:
            payload = _rerank_payload(
                results,
                status="applied_all_noise",
                applied=True,
                filtered=True,
                before_count=serp_count,
            )
        else:
            payload = _rerank_payload(results, status="skipped_empty", applied=False)
    # Weather queries are already hard-filtered by _filter_weather_intent_results
    # (allow-listed hosts + intent regex) — the model rerank adds ~1.5-2s with no
    # measurable gain since every surviving row is weather-relevant.
    elif all(_query_looks_like_weather(q) for q in queries):
        payload = _rerank_payload(
            results,
            status="skipped_weather",
            applied=False,
            filtered=False,
            before_count=serp_count,
        )
    elif not _RERANKER_ENABLED:
        payload = _rerank_payload(
            results,
            status="disabled",
            applied=False,
            filtered=len(results) < serp_count,
            before_count=serp_count,
        )
    else:
        # Dynamic model window: keyword-carrying rows get scored; keyword-free
        # rows beyond the cap keep Bing order. Keeps the GPU load bounded on
        # slow hardware (GTX 1060: 30-row scoring ~4.5s can blow the timeout).
        window, tail = _dynamic_rerank_window(results, queries)
        docs = [f"{r.get('title', '')}. {r.get('snippet', '') or r.get('summary', '')}" for r in window]
        rqs = [(r.get("matched_queries") or queries[:1] or [""])[0] for r in window]
        scores = await asyncio.to_thread(_rerank_scores_sync, rqs, docs)
        if scores is None:
            payload = _rerank_payload(
                results,
                status="unavailable",
                applied=False,
                filtered=len(results) < serp_count,
                before_count=serp_count,
            )
        else:
            before = serp_count
            # Pass the query that found these rows so weak-relevance review can
            # drop place-only portals (e.g. gov homepage for a "city GDP" query).
            ranked = apply_rerank_strategy(window, scores, query=queries[0] if queries else "")
            # Append unranked tail rows in Bing order (dedupe by url).
            seen = {r.get("url") for r in ranked}
            ranked = ranked + [r for r in tail if r.get("url") not in seen]
            results = ranked
            # Second weather hard-filter after model ranking (false-positive belt).
            results = _filter_weather_intent_results(queries, results)
            if not results and before > 0:
                payload = _rerank_payload(
                    results,
                    status="applied_all_noise",
                    applied=True,
                    filtered=True,
                    before_count=before,
                )
            else:
                payload = _rerank_payload(
                    results,
                    status="applied",
                    applied=True,
                    filtered=len(results) < before,
                    before_count=before,
                )

    elapsed = time.time() - start_time
    print(
        f"--- API 1: Finished. Found {payload['result_count']} links "
        f"(rerank={payload['relevance_rerank_status']}) in {elapsed:.2f}s ---"
    )
    # Do not cache retrieval misses — weather SERPs flip between junk and good.
    if payload.get("relevance_rerank_status") != "applied_all_noise":
        _cache_set(_search_cache, cache_key, payload)
    return payload


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
async def fetch_and_wash_urls_async(url_infos: list[str], headless: bool | None = None) -> dict[str, str]:
    if headless is None:
        headless = resolve_headless()
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
async def fetch_html_content_async(url: str | None = None, headless: bool | None = None) -> str:
    if headless is None:
        headless = resolve_headless()
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
    search_payload = await search_links_async(queries=my_queries, max_results_per_query=3)
    link_results = search_payload.get("results") or []

    print("\n\n==================== API 1: Link Search Results ====================")
    print(
        f"Rerank: applied={search_payload.get('relevance_rerank_applied')} "
        f"status={search_payload.get('relevance_rerank_status')} "
        f"filtered={search_payload.get('filtered_by_relevance')}"
    )
    print(f"Note: {search_payload.get('relevance_rerank_note')}")
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
