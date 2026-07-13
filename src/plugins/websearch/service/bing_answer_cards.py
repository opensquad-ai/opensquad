"""Extract Bing SERP answer cards / rich results (weather, entity, AI answers)."""

from __future__ import annotations

import copy
import re
from typing import Any
from urllib.parse import quote, urljoin

from bs4 import BeautifulSoup, Tag

_SUMMARY_MAX = 700
_MIN_SUMMARY_LEN = 20

_WEATHER_HINT_RE = re.compile(
    r"(天气|气温|温度|预报|℃|°[CF]|晴|雨|雪|阴|多云|humidity|forecast|weather|msn\.com/weather)",
    re.I,
)
_RELATED_NOISE_RE = re.compile(r"(相关搜索|其他人还搜索了|related searches|people also search)", re.I)
_WHITESPACE_RE = re.compile(r"\s+")

# Prefer external / content links over Bing chrome.
_SKIP_HREF_PREFIXES = (
    "javascript:",
    "#",
    "data:",
)


def _normalize_space(text: str) -> str:
    return _WHITESPACE_RE.sub(" ", (text or "").strip())


def _truncate(text: str, limit: int = _SUMMARY_MAX) -> str:
    text = _normalize_space(text)
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _synthetic_url(kind: str, query: str) -> str:
    return f"bing-answer://{kind}/{quote((query or 'query').strip() or 'query')}"


def _is_skippable_href(href: str | None) -> bool:
    if not href:
        return True
    low = href.strip().lower()
    return any(low.startswith(p) for p in _SKIP_HREF_PREFIXES)


def _pick_url(node: Tag, base_url: str, *, kind: str, query: str) -> str:
    """Pick the best outbound URL from a card; fall back to synthetic scheme."""
    candidates: list[str] = []
    for a in node.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        if _is_skippable_href(href):
            continue
        absolute = urljoin(base_url, href)
        low = absolute.lower()
        # Prefer non-Bing destinations when available.
        if "bing.com/" in low and "/search?" in low:
            continue
        candidates.append(absolute)

    for url in candidates:
        low = url.lower()
        if "msn.com" in low or "weather" in low or "tianqi" in low:
            return url
    for url in candidates:
        if "bing.com" not in url.lower():
            return url
    if candidates:
        return candidates[0]
    return _synthetic_url(kind, query)


def _card_title(node: Tag, *, kind: str, query: str) -> str:
    for sel in ("h2", "h3", ".b_entityTitle", ".gs_heroTextHeader", "[role='heading']"):
        el = node.select_one(sel)
        if el:
            text = _normalize_space(el.get_text(" ", strip=True))
            if text and len(text) >= 2:
                return text[:160]

    labels = {
        "weather": f"{query}（Bing 天气卡）",
        "entity": f"{query}（Bing 知识卡）",
        "ai_answer": f"{query}（Bing 即时回答）",
        "generic": f"{query}（Bing 答案卡）",
    }
    return labels.get(kind, labels["generic"])


def _card_summary(node: Tag) -> str:
    # Drop script/style noise before reading text.
    for bad in node.find_all(["script", "style", "noscript", "svg"]):
        bad.decompose()
    return _truncate(node.get_text(" ", strip=True))


def _classify_card(node: Tag, summary: str) -> str | None:
    """Return card_kind or None if this block should be skipped."""
    classes = " ".join(node.get("class") or []).lower()
    node_id = (node.get("id") or "").lower()
    blob = f"{classes} {node_id} {summary[:200]}"

    if _RELATED_NOISE_RE.search(summary[:120]) and len(summary) < 180:
        return None
    if "b_rs" in classes or "relatedsearches" in classes.replace(" ", ""):
        return None

    if (
        "wtr" in classes
        or "wtr" in node_id
        or node.select_one('[class*="wtr"], [id*="wtr"], [class*="weather"]')
        or _WEATHER_HINT_RE.search(blob)
    ):
        return "weather"

    if (
        node.select_one(".b_genserp_container, #copans_container, .developer_answercard_wrapper")
        or "genserp" in classes
        or "copans" in node_id
    ):
        return "ai_answer"

    if (
        node.select_one("[class*='l_ecrd'], .b_entityTitle, .lite-entcard-blk")
        or "l_ecrd" in classes
        or "entity" in classes
    ):
        return "entity"

    if "b_ans" in classes or node.name == "li":
        if len(summary) < _MIN_SUMMARY_LEN:
            return None
        return "generic"

    if len(summary) >= _MIN_SUMMARY_LEN:
        return "generic"
    return None


def _make_result(
    *,
    title: str,
    url: str,
    summary: str,
    kind: str,
    region_label: str,
) -> dict[str, Any]:
    return {
        "title": title,
        "url": url,
        "summary": summary,
        "bing_region": region_label,
        "result_type": "answer_card",
        "card_kind": kind,
    }


def _iter_candidate_nodes(soup: BeautifulSoup) -> list[Tag]:
    nodes: list[Tag] = []
    seen: set[int] = set()

    def _add(node: Tag | None) -> None:
        if node is None or not isinstance(node, Tag):
            return
        key = id(node)
        if key in seen:
            return
        seen.add(key)
        nodes.append(node)

    # High priority: answer blocks in main results.
    for node in soup.select("#b_results li.b_ans, #b_results .b_ans"):
        _add(node)

    # Weather modules (may sit outside classic b_ans).
    for node in soup.select(
        '#b_results [class*="wtr"], #b_results [id*="wtr"], '
        '#b_content [class*="wtr"], #b_content [id*="wtr"], '
        '[class*="wtr_module"], [id*="wtr_module"]'
    ):
        # Prefer a reasonably sized container.
        container = node
        for _ in range(4):
            parent = container.parent if isinstance(container.parent, Tag) else None
            if parent is None:
                break
            parent_classes = " ".join(parent.get("class") or [])
            if parent.name == "li" or "b_ans" in parent_classes or "wtr" in parent_classes.lower():
                container = parent
            else:
                break
        _add(container)

    # AI / generative answers.
    for node in soup.select(
        "#b_results .b_genserp_container, #b_results #copans_container, "
        "#b_results .developer_answercard_wrapper, .b_genserp_container, #copans_container"
    ):
        _add(node)

    # Entity / knowledge cards in results or sidebar.
    for node in soup.select(
        "#b_results [class*='l_ecrd'], #b_context [class*='l_ecrd'], "
        "#b_context .b_entityTitle, #b_results .lite-entcard-blk"
    ):
        container = node
        for _ in range(5):
            parent = container.parent if isinstance(container.parent, Tag) else None
            if parent is None:
                break
            parent_classes = " ".join(parent.get("class") or [])
            if parent.name in {"li", "section", "aside"} or "b_ans" in parent_classes or "l_ecrd" in parent_classes:
                container = parent
            else:
                break
        _add(container)

    return nodes


def parse_answer_cards_from_html(
    html: str,
    query: str,
    *,
    region_label: str = "cn",
    base_url: str = "https://cn.bing.com",
    max_cards: int = 5,
) -> list[dict[str, Any]]:
    """
    Parse Bing SERP HTML for answer cards / rich results.

    Returns normalized dicts compatible with organic results, plus
    ``result_type='answer_card'`` and ``card_kind``.
    """
    if not html or not html.strip():
        return []

    soup = BeautifulSoup(html, "lxml")
    results: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    seen_summaries: set[str] = set()

    for node in _iter_candidate_nodes(soup):
        if len(results) >= max_cards:
            break

        # Work on a copy so decompose in _card_summary does not break later siblings.
        working = copy.copy(node)
        summary = _card_summary(working)
        kind = _classify_card(working, summary)
        if not kind:
            continue
        if len(summary) < _MIN_SUMMARY_LEN:
            continue

        summary_key = summary[:120].lower()
        if summary_key in seen_summaries:
            continue

        title = _card_title(working, kind=kind, query=query)
        url = _pick_url(working, base_url, kind=kind, query=query)
        if url in seen_urls:
            # Same destination from nested containers — skip duplicate.
            continue

        seen_urls.add(url)
        seen_summaries.add(summary_key)
        results.append(
            _make_result(
                title=title,
                url=url,
                summary=summary,
                kind=kind,
                region_label=region_label,
            )
        )

    # Prefer weather / AI / entity ordering for the caller.
    kind_order = {"weather": 0, "ai_answer": 1, "entity": 2, "generic": 3}
    results.sort(key=lambda item: kind_order.get(item.get("card_kind", "generic"), 9))
    return results[:max_cards]


async def extract_answer_cards_from_page(
    page: Any,
    query: str,
    *,
    region_label: str = "cn",
    base_url: str = "https://cn.bing.com",
    max_cards: int = 5,
) -> list[dict[str, Any]]:
    """
    Extract answer cards from a live Playwright page.

    Best-effort: any failure returns an empty list so organic scraping can continue.
    """
    try:
        # Give JS-injected widgets a short window after results are visible.
        try:
            await page.wait_for_timeout(800)
        except Exception:
            pass
        html = await page.content()
        return parse_answer_cards_from_html(
            html,
            query,
            region_label=region_label,
            base_url=base_url,
            max_cards=max_cards,
        )
    except Exception as exc:  # noqa: BLE001 — never fail the whole search
        print(f"--- Answer card extraction failed (non-fatal): {exc} ---")
        return []
