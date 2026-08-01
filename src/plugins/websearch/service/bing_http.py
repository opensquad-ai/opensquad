"""HTTP-direct Bing SERP fetch (no Playwright/Chrome in the hot path).

Uses the persistent-profile cookies (exported once via Playwright storage_state)
so Bing still treats the client as a logged-in real user, but each search is a
plain httpx GET + BeautifulSoup parse — 1-2s instead of 5-10s for Chrome launch +
render wait.

Fallback: if this module cannot fetch (cookies missing / blocked / parse empty),
the caller keeps the existing Playwright path.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time

import httpx

try:
    from .bing_answer_cards import parse_answer_cards_from_html
    from .web_crawler import _CHROME_UA
except ImportError:  # pragma: no cover - dev/standalone runs
    from bing_answer_cards import parse_answer_cards_from_html
    from web_crawler import _CHROME_UA

_UA = os.environ.get("WEBSEARCH_HTTP_UA", _CHROME_UA)
_HTTP_TIMEOUT = 15.0

# Cached Bing cookies (exported from the persistent Playwright profile).
_cookies: list[dict[str, str]] = []
_cookie_loaded_at = 0.0
_cookie_ttl = float(os.environ.get("WEBSEARCH_HTTP_COOKIE_TTL_S", "600") or "600")
# Reentrant lock: _has_fresh_cookies() holds it while _load_cookies_from_disk()
# also acquires it internally.
_cookie_lock = threading.RLock()

# Circuit breaker: after a hard failure (blocked / empty), stop trying httpx
# for a while so we don't hammer Bing with a broken cookie.
_blocked_until = 0.0


def _cookie_file_path() -> str:
    base = os.environ.get("WEBSEARCH_COOKIE_FILE", "").strip()
    if base:
        return base
    try:
        from plugins._service_runtime import workspace_data_dir
    except ImportError:
        try:
            from _service_runtime import workspace_data_dir
        except ImportError:
            workspace_data_dir = None  # type: ignore[assignment]
    if workspace_data_dir is not None:
        return workspace_data_dir("plugins", "websearch", "bing_cookies.json")
    return os.path.join(os.path.expanduser("~"), ".opensquad", "websearch", "bing_cookies.json")


def export_cookies_from_storage_state(storage_state: dict) -> int:
    """Store cookies from a Playwright storage_state dict for httpx reuse.

    ``storage_state`` is what ``await context.storage_state()`` returns:
    ``{"cookies": [...], "origins": [...]}``. Only cookies that matter to Bing
    are kept.
    """
    global _cookies, _cookie_loaded_at
    raw = (storage_state or {}).get("cookies") or []
    keep = []
    for c in raw:
        name = c.get("name")
        value = c.get("value")
        if not name or value is None:
            continue
        domain = c.get("domain") or ""
        if not (domain.endswith("bing.com") or domain.endswith(".bing.com") or domain == "bing.com"):
            continue
        keep.append(
            {
                "name": name,
                "value": value,
                "domain": domain,
                "path": c.get("path") or "/",
            }
        )
    if keep:
        with _cookie_lock:
            _cookies = keep
            _cookie_loaded_at = time.time()
        try:
            path = _cookie_file_path()
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(keep, f, ensure_ascii=False)
        except Exception as e:
            print(f"[WebSearch] cookie export to disk failed (non-fatal): {e}")
        print(f"[WebSearch] exported {len(keep)} Bing cookies for http-direct search")
    return len(keep)


def _load_cookies_from_disk() -> bool:
    global _cookies, _cookie_loaded_at
    path = _cookie_file_path()
    if not os.path.isfile(path):
        return False
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list) and data:
            with _cookie_lock:
                _cookies = data
                _cookie_loaded_at = time.time()
            return True
    except Exception:
        pass
    return False


def _has_fresh_cookies() -> bool:
    with _cookie_lock:
        if not _cookies:
            return _load_cookies_from_disk()
        if (time.time() - _cookie_loaded_at) < _cookie_ttl:
            return True
        # TTL expired — reload from disk (the exported file is fresher than
        # the in-memory snapshot after long-running sessions).
        return _load_cookies_from_disk()


def _cookie_jar() -> httpx.Cookies:
    with _cookie_lock:
        items = list(_cookies)
    jar = httpx.Cookies()
    for c in items:
        try:
            jar.set(
                c.get("name"),
                c.get("value"),
                domain=c.get("domain"),
                path=c.get("path") or "/",
            )
        except Exception:
            continue
    return jar


# ── Query impurity cleaning ────────────────────────────────────────────
# Bing's relevance collapses when a query carries URL/code noise: "北京天气
# 中国天气网 weather.com.cn 101010100" returns baike/gov/travel instead of
# forecasts, and the same happens for ANY topic (stats URLs, city codes, …).
# The site: rescue rewrite only fixes weather; the real fix is to scrub these
# tokens before the HTTP request so Bing searches clean keywords.
_URL_TOKEN_RE = re.compile(
    r"\b(?:www\.)?[a-z0-9-]+\.(?:com|cn|net|org|io|gov|edu|co)(?:\.[a-z]{2})?(?:/[^\s]*)?\b", re.I
)
# Long digit runs are almost always entity/city codes (weather id 101010100),
# not meaningful numbers. Keep short ones (years like 2025).
_LONG_DIGIT_RE = re.compile(r"(?<![a-z0-9])\d{6,}(?![a-z0-9])")
# Site-name hints users append to anchor results ("中国天气网"); after the URL
# is gone these just confuse Bing.
_SITE_HINT_RE = re.compile(r"(中国天气网|天气网|天气预报网|天气在线|天气预报网站|官方网站|官网|\.com|\.cn|\.net)", re.I)


def _clean_query(query: str) -> str:
    """Strip URL/code/site-hint noise from a query for Bing. Returns the query
    unchanged if cleaning produces nothing meaningful.

    ``site:`` constraints (used by the weather rescue rewrite) are preserved —
    their domains are intentional filters, not noise.
    """
    q = (query or "").strip()
    if not q:
        return q
    # Protect site: clauses so their domains survive cleaning.
    site_parts: list[str] = []

    def _hold(m: re.Match[str]) -> str:
        site_parts.append(m.group(0))
        return f" __SITE{len(site_parts) - 1}__ "

    s = re.sub(r"site:[a-z0-9./:\-_]+", _hold, q, flags=re.I)
    s = _URL_TOKEN_RE.sub(" ", s)
    s = _LONG_DIGIT_RE.sub(" ", s)
    s = _SITE_HINT_RE.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip()
    for i, part in enumerate(site_parts):
        s = s.replace(f"__SITE{i}__", part)
    # Never let cleaning empty the query (e.g. pure-URL query).
    return s if len(s) >= 4 else q


# ── HTML parsing (mirrors web_crawler._extract_organics_from_page in Python) ──
from bs4 import BeautifulSoup  # noqa: E402


def _parse_organics_html(html: str, region_label: str, limit: int) -> list[dict[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    results: list[dict[str, str]] = []
    seen: set[str] = set()

    for item in soup.select("li.b_algo"):
        if len(results) >= limit:
            break
        a = item.select_one("h2 a") or item.select_one("a.tilk")
        if not a:
            continue
        url = (a.get("href") or "").strip()
        if not url or url in seen:
            continue
        title = (a.get_text(" ", strip=True) or "").strip()
        if not title:
            aria = a.get("aria-label") or ""
            title = aria.strip() or ""
        if not title and not item.get_text(" ", strip=True):
            continue
        summary = ""
        for sel in (
            "div.b_caption p.b_lineclamp2",
            "div.b_caption p.b_lineclamp3",
            "div.b_caption p.b_lineclamp4",
            "div.b_caption p",
            "p.b_lineclamp2",
            "div.b_caption",
        ):
            el = item.select_one(sel)
            if el:
                summary = (el.get_text(" ", strip=True) or "").replace("\u00a0", " ").strip()
                if summary:
                    break
        seen.add(url)
        results.append(
            {
                "title": title,
                "url": url,
                "summary": summary or "No summary available.",
                "bing_region": region_label,
                "result_type": "organic",
            }
        )
    return results


# ── Main entry ─────────────────────────────────────────────────────────
def _build_search_url(base_url: str, query: str, *, market: str, setlang: str, country_code: str) -> str:
    from urllib.parse import quote_plus

    q = quote_plus((query or "").strip())
    return f"{base_url}/search?q={q}&mkt={market}&setlang={setlang}&cc={country_code}&FORM=QBRE"


def fetch_serp_http(
    query: str,
    *,
    base_url: str = "https://cn.bing.com",
    market: str = "zh-CN",
    setlang: str = "zh-hans",
    country_code: str = "CN",
    max_results: int = 20,
) -> list[dict[str, str]] | None:
    """Fetch + parse one Bing SERP over HTTP. Returns None if unusable."""
    global _blocked_until
    if not _has_fresh_cookies():
        return None
    if time.time() < _blocked_until:
        return None

    # Scrub URL/code/site-hint noise so Bing searches clean keywords (raw
    # queries with e.g. "weather.com.cn 101010100" collapse to baike/gov/travel).
    cleaned = _clean_query(query)
    if cleaned != query:
        print(f"[WebSearch] query cleaned: {query!r} -> {cleaned!r}")

    url = _build_search_url(base_url, cleaned, market=market, setlang=setlang, country_code=country_code)
    headers = {
        "User-Agent": _UA,
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    try:
        resp = httpx.get(url, headers=headers, cookies=_cookie_jar(), timeout=_HTTP_TIMEOUT, follow_redirects=True)
    except Exception as e:
        print(f"[WebSearch] http-direct search failed ({e}); falling back")
        return None
    if resp.status_code != 200:
        print(f"[WebSearch] http-direct search status {resp.status_code}; falling back")
        return None
    html = resp.text
    if not html or "b_algo" not in html:
        # Probably a bot/consent page — don't hammer.
        print("[WebSearch] http-direct SERP empty/blocked; cooling down + falling back")
        _blocked_until = time.time() + 300.0
        return None

    region_label = "cn" if "cn.bing.com" in base_url else "global"
    organics = _parse_organics_html(html, region_label, max_results)
    if not organics:
        return None

    try:
        cards = parse_answer_cards_from_html(
            html,
            query,
            region_label=region_label,
            base_url=base_url,
            max_cards=min(3, max_results),
        )
    except Exception as e:
        print(f"[WebSearch] answer-card parse skipped (non-fatal): {e}")
        cards = []

    rows: list[dict[str, str]] = cards + organics
    seen: set[str] = set()
    deduped: list[dict[str, str]] = []
    for r in rows:
        u = r.get("url")
        if u and u not in seen:
            seen.add(u)
            deduped.append(r)
    return deduped[:max_results]
