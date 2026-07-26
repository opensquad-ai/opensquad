"""
Bocha (博查) search tools for OpenSquad agents.

Docs: https://bocha-ai.feishu.cn/wiki/RXEOw02rFiwzGSkd9mUcqoeAnNK
Endpoint: POST {base}/v1/web-search  |  POST {base}/v1/ai-search
Auth: Authorization: Bearer <BOCHA_API_KEY>
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import requests

logger = logging.getLogger("plugins.bocha_search")

_DEFAULT_BASE = "https://api.bocha.cn"
_DEFAULT_COUNT = 10
_DEFAULT_TIMEOUT = 30
_FRESHNESS_OK = frozenset({"noLimit", "oneDay", "oneWeek", "oneMonth", "oneYear"})


def _load_plugin_config() -> dict[str, Any]:
    """Load data/plugins/bocha_search/config.json when present."""
    try:
        from opensquad.system_config import syscfg

        path = syscfg.workspace_data_dir("plugins", "bocha_search", "config.json")
    except Exception:
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "data",
            "plugins",
            "bocha_search",
            "config.json",
        )
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        logger.warning("Failed to read bocha_search config: %s", exc)
        return {}


def _resolve_settings() -> dict[str, Any]:
    cfg = _load_plugin_config()
    api_key = os.environ.get("BOCHA_API_KEY", "").strip() or str(cfg.get("api_key") or "").strip()
    base_url = (
        os.environ.get("BOCHA_BASE_URL", "").strip() or str(cfg.get("base_url") or _DEFAULT_BASE).strip()
    ).rstrip("/")
    try:
        default_count = int(cfg.get("default_count", _DEFAULT_COUNT))
    except (TypeError, ValueError):
        default_count = _DEFAULT_COUNT
    try:
        timeout_sec = int(cfg.get("timeout_sec", _DEFAULT_TIMEOUT))
    except (TypeError, ValueError):
        timeout_sec = _DEFAULT_TIMEOUT
    return {
        "api_key": api_key,
        "base_url": base_url or _DEFAULT_BASE,
        "default_count": max(1, min(50, default_count)),
        "timeout_sec": max(5, timeout_sec),
    }


def _clamp_count(count: int | None, default: int) -> int:
    try:
        n = int(count) if count is not None else default
    except (TypeError, ValueError):
        n = default
    return max(1, min(50, n))


def _normalize_freshness(freshness: str | None) -> str:
    raw = (freshness or "noLimit").strip()
    if raw in _FRESHNESS_OK:
        return raw
    # Allow date / range forms documented by Bocha (YYYY-MM-DD or A..B).
    if ".." in raw or (len(raw) >= 8 and raw[0:4].isdigit()):
        return raw
    logger.warning("Unknown freshness %r; using noLimit", freshness)
    return "noLimit"


def _post_json(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    settings = _resolve_settings()
    api_key = settings["api_key"]
    if not api_key:
        return {
            "error": (
                "Bocha API key missing. Set BOCHA_API_KEY or write api_key to data/plugins/bocha_search/config.json"
            )
        }
    url = f"{settings['base_url']}{path}"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    logger.info("Bocha POST %s query=%r", path, payload.get("query"))
    try:
        resp = requests.post(
            url,
            headers=headers,
            json=payload,
            timeout=settings["timeout_sec"],
        )
    except requests.exceptions.RequestException as exc:
        logger.error("Bocha request failed: %s", exc)
        return {"error": f"Bocha request failed: {exc}"}

    try:
        body = resp.json()
    except Exception:
        return {"error": f"Bocha returned non-JSON (HTTP {resp.status_code}): {resp.text[:500]}"}

    if resp.status_code >= 400:
        msg = body.get("msg") or body.get("message") or resp.text[:500]
        return {"error": f"Bocha HTTP {resp.status_code}: {msg}"}

    if isinstance(body, dict) and body.get("code") not in (None, 200, "200"):
        return {"error": f"Bocha API error: {body.get('msg') or body}"}

    return body if isinstance(body, dict) else {"error": "Unexpected Bocha response"}


def _map_web_page(item: dict[str, Any]) -> dict[str, Any]:
    summary = (item.get("summary") or item.get("snippet") or "").strip()
    return {
        "title": (item.get("name") or "").strip(),
        "url": (item.get("url") or "").strip(),
        "summary": summary or "No summary available.",
        "snippet": (item.get("snippet") or "").strip(),
        "site_name": (item.get("siteName") or "").strip(),
        "site_icon": (item.get("siteIcon") or "").strip(),
        "date_published": (item.get("datePublished") or "").strip(),
        "result_type": "organic",
        "provider": "bocha",
    }


def search(
    query: str | None = None,
    freshness: str = "noLimit",
    count: int = 10,
    summary: bool = True,
    *,
    queries: list[str] | str | None = None,
) -> list[dict[str, Any]] | dict[str, Any]:
    """
    Search the web via Bocha Web Search API (AI-oriented results with summaries).

    Prefer this over browser-scraped Bing when you need stable Chinese web results
    (news, weather, facts) without automation/CAPTCHA issues.

    Args:
        query: Search keywords (same phrasing a human would type).
        freshness: Time filter — ``noLimit`` (default, recommended), ``oneDay``,
            ``oneWeek``, ``oneMonth``, ``oneYear``, or ``YYYY-MM-DD`` /
            ``YYYY-MM-DD..YYYY-MM-DD``. Prefer ``noLimit`` and put time words in
            ``query`` so Bocha can rewrite the range.
        count: Number of results (1–50, default 10).
        summary: If True, request long webpage summaries (recommended).
        queries: Optional alias / multi-query list (merged, de-duplicated by URL).

    Returns:
        List of dicts with title, url, summary, site_name, date_published, …
        or ``{"error": "..."}`` on failure.
    """
    q_list: list[str] = []
    if queries is not None:
        if isinstance(queries, str):
            q_list.append(queries.strip())
        else:
            q_list.extend(str(q).strip() for q in queries if str(q).strip())
    if query and str(query).strip():
        q_list.insert(0, str(query).strip())
    # de-dupe preserving order
    seen_q: set[str] = set()
    uniq: list[str] = []
    for q in q_list:
        if q not in seen_q:
            seen_q.add(q)
            uniq.append(q)
    if not uniq:
        return {"error": "Missing required parameter 'query'."}

    settings = _resolve_settings()
    n = _clamp_count(count, settings["default_count"])
    fresh = _normalize_freshness(freshness)

    merged: list[dict[str, Any]] = []
    seen_url: set[str] = set()
    errors: list[str] = []

    for q in uniq:
        body = _post_json(
            "/v1/web-search",
            {
                "query": q,
                "freshness": fresh,
                "summary": bool(summary),
                "count": n,
            },
        )
        if "error" in body:
            errors.append(f"{q}: {body['error']}")
            continue
        data = body.get("data") or {}
        pages = ((data.get("webPages") or {}).get("value")) or []
        for item in pages:
            if not isinstance(item, dict):
                continue
            row = _map_web_page(item)
            row["matched_queries"] = [q]
            url = row.get("url") or ""
            if url and url in seen_url:
                for existing in merged:
                    if existing.get("url") == url:
                        mq = existing.setdefault("matched_queries", [])
                        if q not in mq:
                            mq.append(q)
                        break
                continue
            if url:
                seen_url.add(url)
            merged.append(row)

    if not merged and errors:
        return {"error": "; ".join(errors)}
    if errors:
        logger.warning("Partial Bocha errors: %s", errors)
    return merged[: max(n, n * len(uniq))]


def ai_search(
    query: str,
    freshness: str = "noLimit",
    count: int = 10,
) -> list[dict[str, Any]] | dict[str, Any]:
    """
    Bocha AI Search — web hits plus vertical modality cards (weather, stock, wiki, …).

    Use when the question likely needs structured cards (weather, calendar, FX, …)
    in addition to normal web pages.

    Args:
        query: Search keywords.
        freshness: Same as ``search`` (default ``noLimit``).
        count: Number of web results (1–50).

    Returns:
        Mixed list of ``result_type=organic`` pages and ``result_type=modality_card``
        items, or ``{"error": "..."}``.
    """
    q = (query or "").strip()
    if not q:
        return {"error": "Missing required parameter 'query'."}

    settings = _resolve_settings()
    n = _clamp_count(count, settings["default_count"])
    body = _post_json(
        "/v1/ai-search",
        {
            "query": q,
            "freshness": _normalize_freshness(freshness),
            "count": n,
            "answer": False,
            "stream": False,
        },
    )
    if "error" in body:
        return body

    results: list[dict[str, Any]] = []
    messages = body.get("messages") or body.get("data", {}).get("messages") or []
    if not messages and isinstance(body.get("data"), dict):
        # Some gateways nest like web-search
        pages = ((body["data"].get("webPages") or {}).get("value")) or []
        for item in pages:
            if isinstance(item, dict):
                results.append(_map_web_page(item))
        return results or {"error": "No results found."}

    for message in messages:
        if not isinstance(message, dict):
            continue
        content_raw = message.get("content") or "{}"
        content: Any
        try:
            content = json.loads(content_raw) if isinstance(content_raw, str) else content_raw
        except Exception:
            content = {}
        ctype = message.get("content_type") or ""
        if ctype == "webpage":
            values = (content or {}).get("value") if isinstance(content, dict) else None
            if isinstance(values, list):
                for item in values:
                    if isinstance(item, dict):
                        results.append(_map_web_page(item))
        elif ctype == "image":
            continue
        elif content_raw and content_raw != "{}":
            results.append(
                {
                    "title": f"Bocha card ({ctype or 'modality'})",
                    "url": "",
                    "summary": content_raw if isinstance(content_raw, str) else json.dumps(content, ensure_ascii=False),
                    "result_type": "modality_card",
                    "card_kind": ctype or "generic",
                    "provider": "bocha",
                    "matched_queries": [q],
                }
            )

    if not results:
        return {"error": "No results found."}
    return results
