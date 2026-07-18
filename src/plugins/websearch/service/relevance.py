"""Keyword–URL relevance scoring for web search results."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import quote, unquote, urlparse

_CJK_RUN_RE = re.compile(r"[\u4e00-\u9fff]+")
_EN_WORD_RE = re.compile(r"[a-z0-9]{2,}", re.I)
_PHRASE_SPLIT_RE = re.compile(r"[,;|、，；]+")
_TOKEN_SPLIT_RE = re.compile(r'[\s/\\"\'()\[\]{}:：。！？、\-–—]+')
_QUOTED_PHRASE_RE = re.compile(r'"([^"]+)"|\'([^\']+)\'')
_DATE_RE = re.compile(r"\b(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})\b")
_YEAR_RE = re.compile(r"\b(20\d{2})\b")

# Intent terms: if the query expresses an intent (weather, etc.) but a result
# only matches a place/entity name, cap its score so encyclopedias/tourism
# pages do not outrank on-topic hits.
_INTENT_TERMS = frozenset(
    {
        "天气",
        "气温",
        "温度",
        "降雨",
        "降水",
        "预报",
        "气象",
        "湿度",
        "风力",
        "weather",
        "forecast",
        "temperature",
        "rainfall",
        "precipitation",
        "humidity",
        "气温预报",
        "天气预报",
        "tianqi",
    }
)

# Latin / path aliases so empty-title SERP rows (common on Bing weather widgets)
# still match Chinese intent queries via URL signals like weather.com.cn / tianqi.com.
_INTENT_URL_ALIASES: dict[str, tuple[str, ...]] = {
    "天气": ("weather", "tianqi", "forecast"),
    "天气预报": ("weather", "tianqi", "forecast"),
    "预报": ("forecast", "weather", "tianqi"),
    "气温": ("temperature", "temp", "weather", "tianqi"),
    "温度": ("temperature", "temp", "weather"),
    "降雨": ("rain", "rainfall", "precipitation"),
    "降水": ("rain", "precipitation"),
    "气象": ("weather", "meteorolog"),
    "湿度": ("humidity",),
    "风力": ("wind",),
    "weather": ("weather",),
    "forecast": ("forecast",),
    "temperature": ("temperature", "temp"),
    "rainfall": ("rain", "rainfall"),
    "precipitation": ("precipitation", "rain"),
    "humidity": ("humidity",),
    "tianqi": ("tianqi", "weather"),
    "气温预报": ("weather", "tianqi", "forecast", "temperature"),
}

# Common place-name hints for URL paths (pinyin). Used only as a boost / keyword
# bridge when Bing returns weather links with empty title/summary text.
_PLACE_URL_HINTS: dict[str, tuple[str, ...]] = {
    "北京": ("beijing",),
    "上海": ("shanghai",),
    "广州": ("guangzhou",),
    "深圳": ("shenzhen",),
    "杭州": ("hangzhou",),
    "成都": ("chengdu",),
    "重庆": ("chongqing",),
    "武汉": ("wuhan",),
    "南京": ("nanjing",),
    "天津": ("tianjin",),
    "西安": ("xian", "xi'an"),
    "苏州": ("suzhou",),
    "青岛": ("qingdao",),
    "厦门": ("xiamen",),
    "福州": ("fuzhou",),
    "宁波": ("ningbo",),
    "无锡": ("wuxi",),
    "长沙": ("changsha",),
    "郑州": ("zhengzhou",),
    "大连": ("dalian",),
    "沈阳": ("shenyang",),
    "济南": ("jinan",),
    "哈尔滨": ("harbin", "haerbin"),
    "昆明": ("kunming",),
    "合肥": ("hefei",),
}

# Minimal stop words that add noise to matching but rarely appear in titles.
_STOP_WORDS = frozenset(
    {
        "the",
        "a",
        "an",
        "and",
        "or",
        "of",
        "in",
        "on",
        "at",
        "to",
        "for",
        "is",
        "are",
        "with",
        "by",
        "from",
        "how",
        "what",
        "why",
        "when",
        "where",
        "的",
        "了",
        "是",
        "在",
        "和",
        "与",
        "及",
        "等",
        "如何",
        "什么",
        "怎么",
        "哪些",
        "介绍",
        "概述",
        "关于",
    }
)


@dataclass
class QueryTerms:
    """Structured terms extracted from one search query string."""

    phrases: list[str] = field(default_factory=list)
    tokens: list[str] = field(default_factory=list)


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _add_unique(items: list[str], seen: set[str], value: str) -> None:
    value = _normalize_text(value)
    if len(value) < 2 or value in _STOP_WORDS or value in seen:
        return
    seen.add(value)
    items.append(value)


def _extract_date_entities(segment: str, tokens: list[str], seen: set[str]) -> str:
    """Keep YYYY-MM-DD as one token; strip date pieces so they don't dilute scoring."""

    def _repl(match: re.Match[str]) -> str:
        y, m, d = match.group(1), match.group(2).zfill(2), match.group(3).zfill(2)
        _add_unique(tokens, seen, f"{y}-{m}-{d}")
        return " "

    return _DATE_RE.sub(_repl, segment)


def _extract_tokens_from_segment(segment: str, tokens: list[str], seen: set[str]) -> None:
    segment = _extract_date_entities(segment, tokens, seen)

    for word in _EN_WORD_RE.findall(segment):
        # Bare years from dates are weak match signals for weather/news queries.
        if _YEAR_RE.fullmatch(word):
            continue
        _add_unique(tokens, seen, word)

    for run in _CJK_RUN_RE.findall(segment):
        if len(run) >= 2:
            _add_unique(tokens, seen, run)
        # Prefer meaningful 2-grams; skip overlapping bigrams that straddle
        # intent boundaries poorly (e.g. 州天 from 福州天气).
        if len(run) >= 4:
            for i in range(len(run) - 1):
                bigram = run[i : i + 2]
                if bigram in _INTENT_TERMS or any(
                    intent in run and bigram in intent for intent in _INTENT_TERMS if len(intent) >= 2
                ):
                    _add_unique(tokens, seen, bigram)
                elif i == 0 or i + 2 == len(run):
                    # Keep edge bigrams of long runs (city/topic anchors).
                    _add_unique(tokens, seen, bigram)
        elif len(run) == 3:
            _add_unique(tokens, seen, run[:2])
            _add_unique(tokens, seen, run[1:])

    for part in _TOKEN_SPLIT_RE.split(segment):
        part = part.strip()
        if len(part) >= 2 and not _CJK_RUN_RE.fullmatch(part) and not _DATE_RE.fullmatch(part):
            _add_unique(tokens, seen, part)


def _query_intent_terms(query: str) -> list[str]:
    normalized = _normalize_text(query)
    return [term for term in _INTENT_TERMS if term in normalized]


def _url_has_intent_alias(intent_terms: list[str], url_text: str) -> bool:
    blob = _normalize_text(url_text)
    if not blob or not intent_terms:
        return False
    for term in intent_terms:
        if term in blob:
            return True
        for alias in _INTENT_URL_ALIASES.get(term, ()):
            if alias and alias in blob:
                return True
    return False


def _result_has_intent(intent_terms: list[str], title: str, summary: str, url_text: str) -> bool:
    if not intent_terms:
        return True
    blob = _normalize_text(f"{title} {summary} {url_text}")
    if any(term in blob for term in intent_terms):
        return True
    return _url_has_intent_alias(intent_terms, url_text)


def _place_tokens_from_query(query: str, terms: QueryTerms | None = None) -> list[str]:
    """CJK query tokens that look like place/entity anchors (not intent words)."""
    terms = terms or parse_query_terms(query)
    places: list[str] = []
    seen: set[str] = set()
    for token in terms.tokens:
        if token in _INTENT_TERMS or token in seen:
            continue
        if _CJK_RUN_RE.fullmatch(token) and len(token) >= 2:
            seen.add(token)
            places.append(token)
    return places


def _url_has_place_signal(place_tokens: list[str], url_text: str) -> bool:
    blob = _normalize_text(url_text)
    if not blob or not place_tokens:
        return False
    for place in place_tokens:
        if place in blob:
            return True
        for hint in _PLACE_URL_HINTS.get(place, ()):
            if hint and hint in blob:
                return True
    return False


def _intent_keywords_from_url(intent_terms: list[str], url_text: str) -> list[str]:
    """Map URL latin aliases back to the Chinese/English intent terms from the query."""
    blob = _normalize_text(url_text)
    matched: list[str] = []
    seen: set[str] = set()
    for term in intent_terms:
        hit = term in blob or any(alias in blob for alias in _INTENT_URL_ALIASES.get(term, ()) if alias)
        if hit and term not in seen:
            seen.add(term)
            matched.append(term)
    return matched


def _place_keywords_from_url(place_tokens: list[str], url_text: str) -> list[str]:
    blob = _normalize_text(url_text)
    matched: list[str] = []
    seen: set[str] = set()
    for place in place_tokens:
        hit = place in blob or any(hint in blob for hint in _PLACE_URL_HINTS.get(place, ()) if hint)
        if hit and place not in seen:
            seen.add(place)
            matched.append(place)
    return matched


def _extract_space_phrases(segment: str, phrases: list[str], seen: set[str]) -> None:
    """Build contiguous English / mixed word phrases inside one segment."""
    words = [w for w in _TOKEN_SPLIT_RE.split(segment) if w.strip()]
    if len(words) < 2:
        return

    normalized_words = [_normalize_text(w) for w in words if _normalize_text(w)]
    if len(normalized_words) >= 2:
        _add_unique(phrases, seen, " ".join(normalized_words))

    for size in (3, 2):
        if len(normalized_words) < size:
            continue
        for i in range(len(normalized_words) - size + 1):
            _add_unique(phrases, seen, " ".join(normalized_words[i : i + size]))


def parse_query_terms(text: str) -> QueryTerms:
    """
    Parse a query into keyword phrases and fallback tokens.

    Supports:
    - Comma/semicolon separated keywords: ``深度学习, 神经网络, Transformer``
    - Space-separated multi-word phrases: ``Python asyncio tutorial``
    - Quoted phrases: ``"large language model" 最新进展``
    - Mixed Chinese / English queries
    """
    if not text:
        return QueryTerms()

    phrases: list[str] = []
    tokens: list[str] = []
    phrase_seen: set[str] = set()
    token_seen: set[str] = set()

    quoted_segments: list[str] = []
    remaining = text
    for match in _QUOTED_PHRASE_RE.finditer(text):
        quoted = match.group(1) or match.group(2) or ""
        if quoted.strip():
            quoted_segments.append(quoted.strip())
        remaining = remaining.replace(match.group(0), " ")

    segments = [s.strip() for s in _PHRASE_SPLIT_RE.split(remaining) if s.strip()]
    segments.extend(quoted_segments)

    if not segments:
        segments = [text.strip()]

    for segment in segments:
        # Pull dates out first so hyphenated YYYY-MM-DD does not explode into
        # noisy space phrases like "福州天气 2026 07 13".
        segment_for_phrases = _extract_date_entities(segment, tokens, token_seen)
        normalized_segment = _normalize_text(segment_for_phrases)
        if len(normalized_segment) >= 2:
            _add_unique(phrases, phrase_seen, normalized_segment)

        _extract_space_phrases(segment_for_phrases, phrases, phrase_seen)
        _extract_tokens_from_segment(segment, tokens, token_seen)

    # Tokens that duplicate whole phrases add little value for partial matching.
    phrase_set = set(phrases)
    tokens = [token for token in tokens if token not in phrase_set]

    return QueryTerms(phrases=phrases, tokens=tokens)


def tokenize_query(text: str) -> list[str]:
    """Backward-compatible flat token list."""
    terms = parse_query_terms(text)
    merged: list[str] = []
    seen: set[str] = set()
    for item in terms.phrases + terms.tokens:
        if item not in seen:
            seen.add(item)
            merged.append(item)
    return merged


def _field_hits(items: list[str], haystack: str) -> tuple[int, list[str]]:
    if not items or not haystack:
        return 0, []
    normalized = _normalize_text(haystack)
    matched: list[str] = []
    for item in items:
        if item in normalized:
            matched.append(item)
    return len(matched), matched


def score_single_result(
    query: str,
    title: str,
    summary: str,
    url: str,
    *,
    rank: int = 0,
) -> float:
    """Score how well a search result matches one query (0.0–1.0)."""
    terms = parse_query_terms(query)
    if not terms.phrases and not terms.tokens:
        return 0.0

    parsed = urlparse(url)
    url_text = unquote(f"{parsed.netloc}{parsed.path}".replace("-", " ").replace("_", " "))

    title_phrase_hits, title_phrase_matched = _field_hits(terms.phrases, title)
    summary_phrase_hits, summary_phrase_matched = _field_hits(terms.phrases, summary)
    url_phrase_hits, url_phrase_matched = _field_hits(terms.phrases, url_text)

    title_token_hits, title_token_matched = _field_hits(terms.tokens, title)
    summary_token_hits, summary_token_matched = _field_hits(terms.tokens, summary)
    url_token_hits, url_token_matched = _field_hits(terms.tokens, url_text)

    # Include URL hits so latin/path matches participate in coverage + early-exit.
    matched_phrases = set(title_phrase_matched + summary_phrase_matched + url_phrase_matched)
    matched_tokens = set(title_token_matched + summary_token_matched + url_token_matched)

    phrase_count = max(len(terms.phrases), 1)
    token_count = max(len(terms.tokens), 1)

    phrase_coverage = len(matched_phrases) / phrase_count
    token_coverage = len(matched_tokens) / token_count if terms.tokens else 0.0

    weighted_hits = (
        title_phrase_hits * 5.0
        + summary_phrase_hits * 3.0
        + url_phrase_hits * 1.5
        + title_token_hits * 2.0
        + summary_token_hits * 1.2
        + url_token_hits * 0.6
    )
    max_weight = phrase_count * 5.0 + token_count * 2.0
    density = weighted_hits / max_weight if max_weight else 0.0

    # Prefer results that cover most comma/space-separated keywords, not just one token.
    base = 0.50 * phrase_coverage + 0.25 * token_coverage + 0.25 * density

    if terms.phrases and len(matched_phrases) == len(terms.phrases):
        base += 0.12
    elif terms.phrases and len(matched_phrases) >= max(2, len(terms.phrases) // 2):
        base += 0.05

    intent_terms = _query_intent_terms(query)
    place_tokens = _place_tokens_from_query(query, terms)
    has_intent = _result_has_intent(intent_terms, title, summary, url_text)
    url_intent = _url_has_intent_alias(intent_terms, url_text) if intent_terms else False
    url_place = _url_has_place_signal(place_tokens, url_text)

    # Bing weather/rich SERP rows often have empty title/summary; URL still carries
    # weather/tianqi (+ optional city pinyin). Do not drop these as score 0.
    if not matched_phrases and not matched_tokens:
        if intent_terms and url_intent:
            base = 0.42
            if url_place:
                base += 0.14
            # Intent-in-URL is a strong signal (mirrors titled weather pages).
            base += 0.18
            rank_bonus = max(0.0, (10 - rank) * 0.012)
            return min(1.0, base + rank_bonus)
        return 0.0

    if intent_terms:
        if has_intent:
            # Title/URL intent hits are strong relevance signals.
            title_blob = _normalize_text(f"{title} {url_text}")
            if any(term in title_blob for term in intent_terms) or url_intent:
                base += 0.18
            else:
                base += 0.08
            if url_place:
                base += 0.06
        else:
            # City-only encyclopedia / tourism pages: keep weakly, never top.
            base = min(base, 0.12)

    # Preserve a small Bing-rank prior only among keyword-matched results.
    # Skip rank inflation for intent-missing pages so Baike can't ride Bing order.
    rank_bonus = 0.0 if (intent_terms and not has_intent) else max(0.0, (10 - rank) * 0.012)
    return min(1.0, base + rank_bonus)


def _collect_matched_keywords(query: str, title: str, summary: str, url: str) -> list[str]:
    terms = parse_query_terms(query)
    parsed = urlparse(url)
    url_text = unquote(f"{parsed.netloc}{parsed.path}".replace("-", " ").replace("_", " "))
    combined = " ".join([title, summary, url_text])

    matched: list[str] = []
    seen: set[str] = set()
    for item in terms.phrases + terms.tokens:
        if item in _normalize_text(combined) and item not in seen:
            seen.add(item)
            matched.append(item)

    # Bridge latin URL aliases back to query intent / place keywords so empty-title
    # weather rows still pass the matched_keywords filter in merge_and_rank_results.
    intent_terms = _query_intent_terms(query)
    for item in _intent_keywords_from_url(intent_terms, url_text):
        if item not in seen:
            seen.add(item)
            matched.append(item)
    for item in _place_keywords_from_url(_place_tokens_from_query(query, terms), url_text):
        if item not in seen:
            seen.add(item)
            matched.append(item)
    return matched


def merge_serp_results(
    queries: list[str],
    search_results_list: list[list[dict[str, str]]],
    *,
    ad_str_list: list[str] | None = None,
) -> list[dict[str, str]]:
    """
    Merge multi-query Bing SERP rows while preserving engine order.

    No relevance scoring / re-ranking: first-seen URL wins (Bing page order for
    each query, then query order). Drops Bing chrome links and simple ad markers.

    Returns: title, url, summary, snippet, matched_queries, match_count,
    result_type, and optional card_kind / bing_region.
    """
    ad_str_list = ad_str_list or []
    ordered: list[dict[str, str]] = []
    by_url: dict[str, dict] = {}

    for query, result_list in zip(queries, search_results_list, strict=False):
        for result in result_list:
            url = (result.get("url") or "").strip()
            if not url or (url.startswith("https://cn.bing.com/") and result.get("result_type") != "answer_card"):
                continue
            if url.startswith("https://cn.bing.com/") and result.get("result_type") == "answer_card":
                kind = result.get("card_kind") or "generic"
                url = f"bing-answer://{kind}/{quote(query.strip() or 'query')}"

            title = result.get("title") or ""
            summary = result.get("summary") or result.get("snippet") or ""
            if any(ad in summary for ad in ad_str_list):
                continue

            if url in by_url:
                entry = by_url[url]
                if query not in entry["matched_queries"]:
                    entry["matched_queries"].append(query)
                    entry["match_count"] = len(entry["matched_queries"])
                continue

            item: dict[str, str] = {
                "title": title,
                "url": url,
                "summary": summary,
                "snippet": summary,
                "matched_queries": [query],
                "match_count": 1,
                "result_type": result.get("result_type") or "organic",
            }
            if result.get("card_kind"):
                item["card_kind"] = result["card_kind"]
            if result.get("bing_region"):
                item["bing_region"] = result["bing_region"]
            by_url[url] = item
            ordered.append(item)

    return ordered


def merge_and_rank_results(
    queries: list[str],
    search_results_list: list[list[dict[str, str]]],
    *,
    min_score: float = 0.08,  # retained for call-site compat; ignored
    ad_str_list: list[str] | None = None,
) -> list[dict[str, str]]:
    """Backward-compatible alias: preserves Bing SERP order (no scoring)."""
    del min_score  # scoring removed
    return merge_serp_results(queries, search_results_list, ad_str_list=ad_str_list)
