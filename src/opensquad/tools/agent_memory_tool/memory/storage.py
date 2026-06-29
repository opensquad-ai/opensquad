# -*- coding: utf-8 -*-
from __future__ import annotations
"""
Memory entry storage engine (SQLite version)
- All fields are optional (topic/keywords/summary/body); at least one must be non-empty
- SQLite real-time persistence + keyword_index inverted index table
- Exact + fuzzy search
- Time-range filtering
- Optional automatic keyword extraction (jieba)
- Multi-agent sharing (source identifier)
- Added fields: entry_type, category, date_str, access_count, last_accessed,
                importance, supersedes
"""

import json
import time
import re
import os
import sqlite3
import datetime
import functools
import calendar
from collections import defaultdict

# ========================
# jieba lazy loading (only used when auto_extract_keywords=True)
# ========================
_jieba = None

def _ensure_jieba():
    global _jieba
    if _jieba is None:
        import jieba
        _jieba = jieba
    return _jieba


# Stopword set (kept in sync with main_v2.py)
# Chinese stopwords stored as unicode escapes to keep source file ASCII-clean.
STOPWORDS = {
    "\u8868\u793a", "\u8fdb\u884c", "\u6ca1\u6709", "\u53ef\u4ee5", "\u5df2\u7ecf", "\u5176\u4e2d", "\u4e0d\u662f",
    "\u5c31\u662f", "\u8fd9\u4e2a", "\u90a3\u4e2a", "\u4ec0\u4e48", "\u4ed6\u4eec", "\u6211\u4eec", "\u81ea\u5df1",
    "\u5e94\u8be5", "\u76ee\u524d", "\u5982\u679c", "\u901a\u8fc7", "\u4e4b\u540e", "\u4ee5\u53ca", "\u4ee5\u6765",
    "\u56e0\u4e3a", "\u6240\u4ee5", "\u4f46\u662f", "\u800c\u4e14", "\u6216\u8005", "\u5bf9\u4e8e",
    "\u5173\u4e8e", "\u6839\u636e", "\u6309\u7167", "\u7531\u4e8e", "\u867d\u7136", "\u4e0d\u8fc7", "\u7136\u800c",
    "\u8fd8\u662f", "\u4ecd\u7136", "\u53ea\u662f", "\u4e5f\u662f", "\u5e76\u4e14", "\u540c\u65f6", "\u8fd9\u6837",
    "\u90a3\u6837", "\u5982\u4f55", "\u600e\u4e48", "\u4e3a\u4ec0\u4e48", "\u600e\u6837", "\u54ea\u4e9b", "\u90a3\u4e9b",
    "\u8fd9\u4e9b", "\u4e00\u4e9b", "\u5f88\u591a", "\u975e\u5e38", "\u6bd4\u8f83", "\u76f8\u5173", "\u5176\u4ed6",
    "\u9700\u8981", "\u6210\u4e3a", "\u8ba4\u4e3a", "\u5305\u62ec", "\u6765\u770b", "\u770b\u6765", "\u8fd9\u662f",
    "\u8bb0\u8005", "\u62a5\u9053", "\u636e\u6089", "\u4e86\u89e3", "\u4ecb\u7ecd", "\u65b9\u9762", "\u60c5\u51b5",
    "\u95ee\u9898", "\u5de5\u4f5c", "\u53d1\u5c55", "\u5efa\u8bbe", "\u6d3b\u52a8", "\u5730\u533a", "\u56fd\u5bb6",
    "\u4e0a\u5348", "\u4e0b\u5348", "\u6628\u5929", "\u4eca\u5929", "\u660e\u5929", "\u53bb\u5e74", "\u4eca\u5e74",
    "\u660e\u5e74", "\u4e0a\u534a\u5e74", "\u4e0b\u534a\u5e74",
}

RE_NOISE = re.compile(
    r'^(\d+\.?\d*%?|'
    r'\d{4}\u5e74?\d{0,2}\u6708?\d{0,2}\u65e5?|'
    r'[a-zA-Z]|'
    r'[\u3000\xa0\s]+)$'
)


@functools.lru_cache(maxsize=256)
def extract_keywords_jieba(text, min_len=2):
    """
    Auto-extract keywords from text (jieba cut_for_search + stopword filtering).
    Used as fallback extraction when auto_extract_keywords=True.
    """
    if not text or not isinstance(text, str):
        return []

    jieba = _ensure_jieba()
    words = jieba.cut_for_search(text)

    clean = set()
    for w in words:
        if len(w) < min_len:
            continue
        if w in STOPWORDS:
            continue
        if RE_NOISE.match(w):
            continue
        clean.add(w)

    # Substring deduplication: if a short word is a substring of a longer word, keep only the longer
    sorted_words = sorted(clean, key=len, reverse=True)
    result = []
    for word in sorted_words:
        if not any(word in kept and word != kept for kept in result):
            result.append(word)

    return result


# POS whitelist for nominal words (used as allowPOS param in jieba.analyse.extract_tags)
# n=common noun, ns=place name, nt=org name, nz=other proper noun, nrt=transliterated person name
# nr=person name, eng=English word (e.g. Transformer), vn=verbal noun (e.g. "computation")
# l=common phrase (e.g. "natural language"), i=idiom/set phrase, j=abbreviation
_NOUN_POS_ALLOW = ('n', 'ns', 'nt', 'nz', 'nr', 'nrt',
                   'eng', 'vn', 'l', 'i', 'j')


def extract_nouns_jieba(text, top_k=20, min_len=2):
    """
    Extract nominal keywords from text by TF-IDF weight (high-quality concept words).

    Differences from extract_keywords_jieba:
    - Uses jieba.analyse.extract_tags TF-IDF ranking + allowPOS POS filtering
    - Keeps only nominal words (n/ns/nt/nz/nr/eng/vn/l/i/j); filters verbs/adverbs/prepositions
    - Returns a list of (word, weight) tuples sorted by TF-IDF weight descending
    - Suitable for injecting high-quality concept words into the co-occurrence matrix in auto mode

    Args:
        text: str - input text
        top_k: int - max number of keywords to extract (default 20)
        min_len: int - minimum word length (filter single characters)

    Returns:
        list[tuple(str, float)] - [(word, weight), ...] sorted by TF-IDF weight descending
    """
    if not text or not isinstance(text, str):
        return []

    analyse = _ensure_jieba_analyse()

    # TF-IDF extraction + POS whitelist filtering (extract more, then filter stopwords and noise)
    raw_tags = analyse.extract_tags(
        text, topK=top_k * 2, withWeight=True, allowPOS=_NOUN_POS_ALLOW)

    # Secondary filtering: stopwords + noise regex + min word length
    filtered = []
    for word, weight in raw_tags:
        if len(word) < min_len:
            continue
        if word in STOPWORDS:
            continue
        if RE_NOISE.match(word):
            continue
        filtered.append((word, weight))

    # Substring deduplication: if a short word is a substring of a longer word, keep longer (higher weight)
    sorted_by_len = sorted(filtered, key=lambda x: len(x[0]), reverse=True)
    result = []
    for word, weight in sorted_by_len:
        if not any(word in kept_w and word != kept_w for kept_w, _ in result):
            result.append((word, weight))

    # Sort by weight descending, take top_k
    result.sort(key=lambda x: x[1], reverse=True)
    return result[:top_k]


# ========================
# jieba TF-IDF lazy loading
# ========================
_jieba_analyse = None


def _ensure_jieba_analyse():
    global _jieba_analyse
    if _jieba_analyse is None:
        import jieba.analyse
        _jieba_analyse = jieba.analyse
    return _jieba_analyse


def extract_keywords_weighted(text, top_k=25, long_threshold=80, min_len=2):
    """
    Smart keyword extraction (TF-IDF with weights for long text; uniform weight for short text).

    Short text (<= long_threshold chars): falls back to extract_keywords_jieba() with uniform weight 1.0
    Long text (> long_threshold chars): uses jieba.analyse.extract_tags() TF-IDF ranking

    Args:
        text: str - input text
        top_k: int - max number of keywords to extract (default 25)
        long_threshold: int - long-text threshold (chars), default 80
        min_len: int - minimum word length

    Returns:
        list[tuple(str, float)] -- [(word, weight), ...] sorted by weight descending
        Short text: weight is uniformly 1.0
    """
    if not text or not isinstance(text, str):
        return []

    # Short text: fall back to extract_keywords_jieba with uniform weight 1.0
    if len(text) <= long_threshold:
        words = extract_keywords_jieba(text, min_len=min_len)
        return [(w, 1.0) for w in words]

    # Long text: use jieba TF-IDF to extract weighted keywords
    analyse = _ensure_jieba_analyse()
    raw_tags = analyse.extract_tags(text, topK=top_k * 2, withWeight=True)

    # Filter: stopwords + noise + min word length
    filtered = []
    for word, weight in raw_tags:
        if len(word) < min_len:
            continue
        if word in STOPWORDS:
            continue
        if RE_NOISE.match(word):
            continue
        filtered.append((word, weight))

    # Substring deduplication: if a short word is a substring of a longer word, keep longer (higher weight)
    sorted_by_len = sorted(filtered, key=lambda x: len(x[0]), reverse=True)
    result = []
    for word, weight in sorted_by_len:
        if not any(word in kept_w and word != kept_w for kept_w, _ in result):
            result.append((word, weight))

    # Sort by weight descending, take top_k
    result.sort(key=lambda x: x[1], reverse=True)
    return result[:top_k]


# ========================
# Time expression auto-parser
# ========================

def _parse_cn_num(s):
    """
    Convert Chinese/Arabic numerals to int (supports 0~99).

    Supported formats:
        Arabic: "3", "12", "300" (only small numbers used for time units)
        Chinese: "three", "twelve", "twenty-three", "two" (single/compound characters)
    Returns None if parsing fails.
    """
    if not s:
        return None

    # Arabic digits: convert directly
    if s.isdigit():
        return int(s)

    cn_map = {
        "\u96f6": 0, "\u4e00": 1, "\u4e8c": 2, "\u4e24": 2, "\u4e09": 3, "\u56db": 4,
        "\u4e94": 5, "\u516d": 6, "\u4e03": 7, "\u516b": 8, "\u4e5d": 9, "\u5341": 10,
        "\u3007": 0, "\u58f9": 1, "\u8d30": 2, "\u53c1": 3, "\u8086": 4,
        "\u4f0d": 5, "\u9646": 6, "\u67d2": 7, "\u634c": 8, "\u7396": 9, "\u62fe": 10,
    }

    # Single character: look up table
    if len(s) == 1 and s in cn_map:
        return cn_map[s]

    # "\u5341X" -> 10+X  (e.g. \u5341\u4e8c -> 12)
    if s.startswith("\u5341") or s.startswith("\u62fe"):
        if len(s) == 1:
            return 10
        rest = s[1:]
        if rest in cn_map:
            return 10 + cn_map[rest]
        return None

    # "X\u5341" -> X*10  (e.g. \u4e8c\u5341 -> 20)
    # "X\u5341Y" -> X*10+Y  (e.g. \u4e8c\u5341\u4e09 -> 23)
    for i, ch in enumerate(s):
        if ch in ("\u5341", "\u62fe") and i > 0:
            tens_ch = s[:i]
            units_part = s[i + 1:]
            if tens_ch in cn_map:
                tens = cn_map[tens_ch] * 10
                if not units_part:
                    return tens
                if units_part in cn_map:
                    return tens + cn_map[units_part]
            return None

    return None


def _resolve_year_prefix(prefix, now_dt):
    """
    Parse year prefix -> year int.

    Supports: this year / last year / the year before last / 3 years ago / 2024 / 2024
    Args:
        prefix: str - "\u4eca" (this) / "\u53bb" (last) / "\u524d" (before) / "\u5927\u524d" (3 ago) / "2024" / "2024" / ""
        now_dt: datetime.date - current date
    Returns: int - year; returns now_dt.year if parsing fails
    """
    if not prefix:
        return now_dt.year

    prefix = prefix.rstrip("\u5e74")

    if prefix == "\u4eca":
        return now_dt.year
    if prefix == "\u53bb":
        return now_dt.year - 1
    if prefix == "\u524d":
        return now_dt.year - 2
    if prefix == "\u5927\u524d":
        return now_dt.year - 3

    # 4-digit Arabic year: "2024"
    if prefix.isdigit() and len(prefix) == 4:
        return int(prefix)

    return now_dt.year


def _month_ts_range(year, month):
    """
    Return timestamp range for a given year/month: (first day 00:00:00 ts, last day 23:59:59 ts).
    """
    first_day = datetime.datetime(year, month, 1, 0, 0, 0)
    _, last_day_num = calendar.monthrange(year, month)
    last_day = datetime.datetime(year, month, last_day_num, 23, 59, 59)
    return first_day.timestamp(), last_day.timestamp()


def _day_ts_range(dt):
    """
    Return timestamp range for a given day: (00:00:00 ts, 23:59:59 ts).
    Args: dt: datetime.date or datetime.datetime
    """
    if isinstance(dt, datetime.date) and not isinstance(dt, datetime.datetime):
        dt = datetime.datetime(dt.year, dt.month, dt.day)
    start = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    end = dt.replace(hour=23, minute=59, second=59, microsecond=0)
    return start.timestamp(), end.timestamp()


def _calc_relative_range(today, n, unit):
    """
    Calculate time range for "last N days/weeks/months/years".

    Returns: (start_ts, end_ts) -- from N units ago to today 23:59:59

    Args:
        today: datetime.date - current date
        n: int - count
        unit: str - day/week/month/year (Chinese characters)
    """
    today_dt = datetime.datetime(today.year, today.month, today.day)
    end_ts = today_dt.replace(hour=23, minute=59, second=59).timestamp()

    if unit in ("\u5929", "\u65e5"):
        start_dt = today_dt - datetime.timedelta(days=n)
    elif unit in ("\u5468", "\u661f\u671f"):
        start_dt = today_dt - datetime.timedelta(weeks=n)
    elif unit == "\u6708":
        year = today.year
        month = today.month - n
        while month <= 0:
            month += 12
            year -= 1
        # Handle date overflow (e.g. March 31 -> one month back -> Feb 28)
        _, max_day = calendar.monthrange(year, month)
        day = min(today.day, max_day)
        start_dt = datetime.datetime(year, month, day)
    elif unit == "\u5e74":
        year = today.year - n
        month = today.month
        day = today.day
        # Handle leap year boundary (Feb 29 rolled back to non-leap year)
        try:
            start_dt = datetime.datetime(year, month, day)
        except ValueError:
            start_dt = datetime.datetime(year, month, 28)
    else:
        return None

    start_ts = start_dt.replace(hour=0, minute=0, second=0).timestamp()
    return start_ts, end_ts


def _calc_simple_time(today, expr, now_dt=None):
    """
    Parse simple time expressions.

    Supports: today / yesterday / day before yesterday / 3 days ago /
              last week / the week before last / this week /
              last month / 2 months ago / this month /
              this year / last year / 2 years ago

    Args:
        today: datetime.date - current date
        expr: str - time expression
        now_dt: datetime.datetime | None - current time
    Returns:
        (start_ts, end_ts) | None
    """
    today_dt = datetime.datetime(today.year, today.month, today.day)

    if expr == "\u4eca\u5929":
        return _day_ts_range(today_dt)

    if expr == "\u6628\u5929":
        return _day_ts_range(today_dt - datetime.timedelta(days=1))

    if expr == "\u524d\u5929":
        return _day_ts_range(today_dt - datetime.timedelta(days=2))

    if expr == "\u5927\u524d\u5929":
        return _day_ts_range(today_dt - datetime.timedelta(days=3))

    if expr == "\u4e0a\u5468":
        # Last Monday to last Sunday
        # weekday(): 0=Monday, 6=Sunday
        days_since_monday = today.weekday()
        this_monday = today_dt - datetime.timedelta(days=days_since_monday)
        last_monday = this_monday - datetime.timedelta(weeks=1)
        last_sunday = last_monday + datetime.timedelta(days=6)
        return (last_monday.replace(hour=0, minute=0, second=0).timestamp(),
                last_sunday.replace(hour=23, minute=59, second=59).timestamp())

    if expr == "\u4e0a\u4e0a\u5468":
        days_since_monday = today.weekday()
        this_monday = today_dt - datetime.timedelta(days=days_since_monday)
        target_monday = this_monday - datetime.timedelta(weeks=2)
        target_sunday = target_monday + datetime.timedelta(days=6)
        return (target_monday.replace(hour=0, minute=0, second=0).timestamp(),
                target_sunday.replace(hour=23, minute=59, second=59).timestamp())

    if expr == "\u672c\u5468":
        days_since_monday = today.weekday()
        this_monday = today_dt - datetime.timedelta(days=days_since_monday)
        this_sunday = this_monday + datetime.timedelta(days=6)
        return (this_monday.replace(hour=0, minute=0, second=0).timestamp(),
                this_sunday.replace(hour=23, minute=59, second=59).timestamp())

    if expr in ("\u4e0a\u4e2a\u6708", "\u4e0a\u6708"):
        year = today.year
        month = today.month - 1
        if month <= 0:
            month += 12
            year -= 1
        return _month_ts_range(year, month)

    if expr == "\u4e0a\u4e0a\u4e2a\u6708":
        year = today.year
        month = today.month - 2
        while month <= 0:
            month += 12
            year -= 1
        return _month_ts_range(year, month)

    if expr == "\u672c\u6708":
        return _month_ts_range(today.year, today.month)

    if expr == "\u4eca\u5e74":
        start = datetime.datetime(today.year, 1, 1, 0, 0, 0)
        end = datetime.datetime(today.year, 12, 31, 23, 59, 59)
        return start.timestamp(), end.timestamp()

    if expr == "\u53bb\u5e74":
        y = today.year - 1
        start = datetime.datetime(y, 1, 1, 0, 0, 0)
        end = datetime.datetime(y, 12, 31, 23, 59, 59)
        return start.timestamp(), end.timestamp()

    if expr == "\u524d\u5e74":
        y = today.year - 2
        start = datetime.datetime(y, 1, 1, 0, 0, 0)
        end = datetime.datetime(y, 12, 31, 23, 59, 59)
        return start.timestamp(), end.timestamp()

    return None


# ---- Time expression confidence evaluation ----
# Used to distinguish "search constraint" from "time word in content description"

# Relative time expressions -- naturally lean toward query intent
_RELATIVE_TIME_EXPRS = {
    "\u4eca\u5929", "\u6628\u5929", "\u524d\u5929", "\u5927\u524d\u5929",
    "\u4e0a\u5468", "\u4e0a\u4e0a\u5468", "\u672c\u5468",
    "\u4e0a\u4e2a\u6708", "\u4e0a\u4e0a\u4e2a\u6708", "\u4e0a\u6708", "\u672c\u6708",
    "\u4eca\u5e74", "\u53bb\u5e74", "\u524d\u5e74",
    "\u6700\u8fd1",
}

# Query-intent verbs
_RE_QUERY_VERB = re.compile(
    r'(\u627e|\u641c|\u67e5|\u770b\u770b|\u5e2e\u6211|\u56de\u5fc6|\u60f3\u60f3|\u56de\u987e|\u641c\u7d22|\u67e5\u627e|\u67e5\u770b|\u67e5\u8be2|\u68c0\u7d22)\s*$'
)

# Descriptive verbs (these before a time word -> likely content description)
_RE_DESC_VERB = re.compile(
    r'(\u63d0\u5230|\u8bf4\u4e86|\u5199\u4e86|\u8bb0\u5f55\u4e86|\u63cf\u8ff0|\u8bb2\u8ff0|\u8ba8\u8bba|\u8c08\u5230|\u4ecb\u7ecd|\u5206\u6790\u4e86|\u5f15\u7528\u4e86|\u5217\u4e3e\u4e86|\u7edf\u8ba1\u4e86|\u9884\u6d4b)\s*$'
)

# Container pattern (nearby "inside/in/within" -> likely quoting a document; time word is content)
_RE_CONTAINER_NEARBY = re.compile(r'[\u91cc\u4e2d\u5185]')


def _is_in_quotes(text, start, end):
    """
    Check whether text[start:end] is enclosed in quotation or book-title marks.

    Supports:
        Chinese quotes:  \u201c\u201d \u2018\u2019 \u300a\u300b \u3008\u3009 \u300c\u300d
        English quotes:  "" ''
    """
    quote_pairs = [
        ('\u201c', '\u201d'),   # Chinese double quotes
        ('\u2018', '\u2019'),   # Chinese single quotes
        ('\u300a', '\u300b'),   # book title marks
        ('\u3008', '\u3009'),   # angle brackets
        ('\u300c', '\u300d'),   # corner brackets
        ('"', '"'),             # English double quotes
        ("'", "'"),             # English single quotes
    ]
    for open_q, close_q in quote_pairs:
        open_pos = text.rfind(open_q, 0, start)
        if open_pos == -1:
            continue
        close_pos = text.find(close_q, end)
        if close_pos != -1:
            return True
    return False


def _compute_time_confidence(text, match, time_expr):
    """
    Compute the confidence that a time expression is a "search constraint"
    (as opposed to a "content description").

    Args:
        text: str       -- full user input text
        match: re.Match -- regex match object
        time_expr: str  -- matched time expression text

    Returns:
        (score: float, signals: list[str])
        score range [0.0, 1.0]; higher means more likely a search constraint
    """
    score = 0.5  # base score
    signals = []

    pos = match.start()
    before = text[:pos]          # text before the time word
    after = text[match.end():]   # text after the time word

    before_stripped = before.rstrip()

    # ============ Positive signals ============

    # 1. At sentence start (nothing before, or only punctuation/whitespace)
    if len(before_stripped) == 0 or re.match(r'^[\uff0c\u3002\uff01\uff1f\u3001\uff1b\uff1a\s]*$', before_stripped):
        score += 0.3
        signals.append("+sentence_start")

    # 2. Query verb before the time word
    if _RE_QUERY_VERB.search(before_stripped):
        score += 0.3
        signals.append("+query_verb")

    # 3. Followed by possessive particle
    if after.startswith("\u7684"):
        score += 0.1
        signals.append("+de_suffix")

    # 4. Short text (<40 chars, more like a query command)
    if len(text) < 40:
        score += 0.15
        signals.append("+short_text")

    # 5. Relative time expression (today/yesterday/last week etc. naturally lean toward query)
    is_relative = (time_expr in _RELATIVE_TIME_EXPRS or
                   time_expr.startswith("\u6700\u8fd1"))
    if is_relative:
        score += 0.1
        signals.append("+relative_time")

    # ============ Negative signals ============

    # 6. Inside quotes/book-title marks
    if _is_in_quotes(text, match.start(), match.end()):
        score -= 0.5
        signals.append("-in_quotes")

    # 7. Descriptive verb before the time word
    if _RE_DESC_VERB.search(before_stripped):
        score -= 0.35
        signals.append("-desc_verb")

    # 8. Container word (inside/in/within) nearby within 10 chars before
    #    These suggest the preceding text is quoting a document/report
    near_before = before_stripped[-10:] if len(before_stripped) > 10 else before_stripped
    if _RE_CONTAINER_NEARBY.search(near_before):
        score -= 0.25
        signals.append("-container_word")

    # 9. Long text (>120 chars, more like content description than search command)
    if len(text) > 120:
        score -= 0.1
        signals.append("-long_text")

    return max(0.0, min(1.0, score)), signals


# ---- Regex patterns (ordered from highest to lowest priority) ----

# Month range: "this year May to August" / "2024 March~June"
_RE_MONTH_RANGE = re.compile(
    r'(\u4eca|\u53bb|\u524d|\u5927\u524d|\d{4})\u5e74?'
    r'(\d{1,2}|[\u4e00\u4e8c\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d\u5341]+)\u6708\u4efd?'
    r'[\u5230\u81f3~\-]'
    r'(\d{1,2}|[\u4e00\u4e8c\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d\u5341]+)\u6708\u4efd?'
)

# Year + month: "this year May" / "last year December" / "2024 March"
_RE_YEAR_MONTH = re.compile(
    r'(\u4eca|\u53bb|\u524d|\u5927\u524d|\d{4})\u5e74'
    r'(\d{1,2}|[\u4e00\u4e8c\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d\u5341]+)\u6708\u4efd?'
)

# Year + month + day: "this year May 3rd" / "2024 March 15th"
_RE_YEAR_MONTH_DAY = re.compile(
    r'(\u4eca|\u53bb|\u524d|\u5927\u524d|\d{4})\u5e74'
    r'(\d{1,2}|[\u4e00\u4e8c\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d\u5341]+)\u6708'
    r'(\d{1,2}|[\u4e00\u4e8c\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d\u5341]+)[\u65e5\u53f7]'
)

# "previous N days/weeks/months/years": "previous two months" / "previous 3 days"
_RE_BEFORE_N = re.compile(
    r'\u524d(\d+|[\u4e00\u4e8c\u4e24\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d\u5341\u767e]+)\u4e2a?(\u5929|\u65e5|\u5468|\u661f\u671f|\u6708|\u5e74)'
)

# "N days/weeks/months/years ago": "two months ago" / "3 days ago"
_RE_N_AGO = re.compile(
    r'(\d+|[\u4e00\u4e8c\u4e24\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d\u5341\u767e]+)\u4e2a?(\u5929|\u65e5|\u5468|\u661f\u671f|\u6708|\u5e74)\u524d'
)

# "recent N days/weeks/months/years": "recent three days" / "recent 2 months"
_RE_RECENT_N = re.compile(
    r'\u6700\u8fd1(\d+|[\u4e00\u4e8c\u4e24\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d\u5341\u767e]+)\u4e2a?(\u5929|\u65e5|\u5468|\u661f\u671f|\u6708|\u5e74)'
)

# Simple time: today/yesterday/day-before-yesterday/3-days-ago/last-week/...
_RE_SIMPLE = re.compile(
    r'(\u5927\u524d\u5929|\u524d\u5929|\u6628\u5929|\u4eca\u5929|\u4e0a\u4e0a\u5468|\u4e0a\u5468|\u672c\u5468|\u4e0a\u4e0a\u4e2a\u6708|\u4e0a\u4e2a\u6708|\u4e0a\u6708|\u672c\u6708|\u4eca\u5e74|\u53bb\u5e74|\u524d\u5e74)'
)

# 4-digit year: "2024" (standalone, represents the whole year)
_RE_SPEC_YEAR = re.compile(
    r'(\d{4})\u5e74'
)


def parse_time_expression(text, now=None, confidence_threshold=0.45):
    """
    Auto-extract time expressions from Chinese natural language, splitting into
    time constraint + content keywords.

    Built-in confidence evaluation: uses heuristic rules based on time word position,
    surrounding verbs, quote enclosure, etc. to judge whether the time word is a
    "search constraint" or a "content description". When confidence is below the
    threshold, time_range returns None and the original text is not modified.

    Tries patterns in priority order (most specific first):
        1. year_month_day: "this year May 3rd" / "2024 March 15th"
        2. month_range: "this year May to August" / "2024 March~June"
        3. year_month: "this year May" / "last year December"
        4. before_n: "previous two months" / "previous 3 days"
        5. n_ago: "two months ago" / "3 days ago"
        6. recent_n: "recent three days" / "recent 2 months"
        7. simple: "today" / "yesterday" / "last week" / "last year" etc.
        8. spec_year: "2024"

    Args:
        text: str - raw user input text
        now: datetime.datetime | None - current time, defaults to datetime.datetime.now()
        confidence_threshold: float - confidence threshold; below this value the time word
            is not treated as a search constraint. Default 0.45. Set to 0.0 to disable
            confidence filtering (backward compatible behavior).

    Returns:
        dict - {
            "time_range": (start_ts, end_ts) | None,  # parsed time range (None if low confidence)
            "keywords": list[str],      # content keywords after stripping the time expression
            "time_expr": str | None,    # matched time expression text (unaffected by confidence)
            "cleaned_text": str,        # text with time expression removed (original if low confidence)
            "confidence": float | None, # confidence that time word is a search constraint (None if no match)
            "confidence_signals": list[str] | None,  # signals for confidence calculation (debug)
        }
    """
    if not text or not isinstance(text, str):
        return {
            "time_range": None,
            "keywords": extract_keywords_jieba(text) if text else [],
            "time_expr": None,
            "cleaned_text": text or "",
            "confidence": None,
            "confidence_signals": None,
        }

    if now is None:
        now = datetime.datetime.now()
    today = now.date()

    time_range = None
    time_expr = None
    matched_span = None  # (start, end) in text
    matched_obj = None   # re.Match object, for confidence calculation

    # ---- Try patterns in priority order ----

    # 1. year_month_day
    if time_range is None:
        m = _RE_YEAR_MONTH_DAY.search(text)
        if m:
            year = _resolve_year_prefix(m.group(1), today)
            month = _parse_cn_num(m.group(2))
            day = _parse_cn_num(m.group(3))
            if month and 1 <= month <= 12 and day and 1 <= day <= 31:
                try:
                    target_dt = datetime.datetime(year, month, day)
                    time_range = _day_ts_range(target_dt)
                    time_expr = m.group(0)
                    matched_span = m.span()
                    matched_obj = m
                except ValueError:
                    pass

    # 2. month_range
    if time_range is None:
        m = _RE_MONTH_RANGE.search(text)
        if m:
            year = _resolve_year_prefix(m.group(1), today)
            month_start = _parse_cn_num(m.group(2))
            month_end = _parse_cn_num(m.group(3))
            if (month_start and month_end and
                    1 <= month_start <= 12 and 1 <= month_end <= 12):
                start_ts, _ = _month_ts_range(year, month_start)
                _, end_ts = _month_ts_range(year, month_end)
                time_range = (start_ts, end_ts)
                time_expr = m.group(0)
                matched_span = m.span()
                matched_obj = m

    # 3. year_month
    if time_range is None:
        m = _RE_YEAR_MONTH.search(text)
        if m:
            year = _resolve_year_prefix(m.group(1), today)
            month = _parse_cn_num(m.group(2))
            if month and 1 <= month <= 12:
                time_range = _month_ts_range(year, month)
                time_expr = m.group(0)
                matched_span = m.span()
                matched_obj = m

    # 4. before_n
    if time_range is None:
        m = _RE_BEFORE_N.search(text)
        if m:
            n = _parse_cn_num(m.group(1))
            unit = m.group(2)
            if n and n > 0:
                result = _calc_relative_range(today, n, unit)
                if result:
                    time_range = result
                    time_expr = m.group(0)
                    matched_span = m.span()
                    matched_obj = m

    # 5. n_ago
    if time_range is None:
        m = _RE_N_AGO.search(text)
        if m:
            n = _parse_cn_num(m.group(1))
            unit = m.group(2)
            if n and n > 0:
                result = _calc_relative_range(today, n, unit)
                if result:
                    time_range = result
                    time_expr = m.group(0)
                    matched_span = m.span()
                    matched_obj = m

    # 6. recent_n
    if time_range is None:
        m = _RE_RECENT_N.search(text)
        if m:
            n = _parse_cn_num(m.group(1))
            unit = m.group(2)
            if n and n > 0:
                result = _calc_relative_range(today, n, unit)
                if result:
                    time_range = result
                    time_expr = m.group(0)
                    matched_span = m.span()
                    matched_obj = m

    # 7. simple
    if time_range is None:
        m = _RE_SIMPLE.search(text)
        if m:
            result = _calc_simple_time(today, m.group(1), now)
            if result:
                time_range = result
                time_expr = m.group(0)
                matched_span = m.span()
                matched_obj = m

    # 8. spec_year
    if time_range is None:
        m = _RE_SPEC_YEAR.search(text)
        if m:
            year = int(m.group(1))
            if 1900 <= year <= 2100:
                start = datetime.datetime(year, 1, 1, 0, 0, 0)
                end = datetime.datetime(year, 12, 31, 23, 59, 59)
                time_range = (start.timestamp(), end.timestamp())
                time_expr = m.group(0)
                matched_span = m.span()
                matched_obj = m

    # ---- Confidence evaluation ----
    confidence = None
    confidence_signals = None

    if matched_obj is not None and time_range is not None:
        confidence, confidence_signals = _compute_time_confidence(
            text, matched_obj, time_expr
        )
        # Below threshold -> do not use as search constraint
        if confidence < confidence_threshold:
            time_range = None
            matched_span = None   # do not strip from cleaned_text

    # ---- Build cleaned_text (remove matched time expression) ----
    if matched_span:
        cleaned = text[:matched_span[0]] + text[matched_span[1]:]
        # Clean up any residual connectives/punctuation
        cleaned = re.sub(r'^[\u7684\uff0c,\s]+', '', cleaned)
        cleaned = re.sub(r'[\u7684\uff0c,\s]+$', '', cleaned)
        # Clean up extra internal whitespace
        cleaned = re.sub(r'\s{2,}', ' ', cleaned)
        cleaned = cleaned.strip()
    else:
        cleaned = text.strip()

    # ---- Extract keywords (from cleaned_text) ----
    keywords = extract_keywords_jieba(cleaned) if cleaned else []

    return {
        "time_range": time_range,
        "keywords": keywords,
        "time_expr": time_expr,
        "cleaned_text": cleaned,
        "confidence": confidence,
        "confidence_signals": confidence_signals,
    }


# ========================
# SQLite table creation SQL
# ========================

_CREATE_ENTRIES_TABLE = """
CREATE TABLE IF NOT EXISTS entries (
    id            TEXT PRIMARY KEY,
    entry_type    TEXT NOT NULL DEFAULT 'knowledge',
    topic         TEXT,
    summary       TEXT,
    body          TEXT,
    source        TEXT,
    category      TEXT,
    timestamp     REAL NOT NULL,
    date_str      TEXT,
    keywords_json TEXT,
    access_count  INTEGER DEFAULT 0,
    last_accessed REAL,
    importance    INTEGER DEFAULT 3,
    supersedes    TEXT
)
"""

_CREATE_KEYWORD_INDEX_TABLE = """
CREATE TABLE IF NOT EXISTS keyword_index (
    keyword   TEXT NOT NULL,
    entry_id  TEXT NOT NULL,
    PRIMARY KEY (keyword, entry_id)
)
"""

_CREATE_INDICES = [
    "CREATE INDEX IF NOT EXISTS idx_type       ON entries(entry_type)",
    "CREATE INDEX IF NOT EXISTS idx_date       ON entries(date_str)",
    "CREATE INDEX IF NOT EXISTS idx_category   ON entries(entry_type, category)",
    "CREATE INDEX IF NOT EXISTS idx_source     ON entries(source)",
    "CREATE INDEX IF NOT EXISTS idx_timestamp  ON entries(timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_importance ON entries(importance)",
    "CREATE INDEX IF NOT EXISTS idx_keyword    ON keyword_index(keyword)",
]

_CREATE_META_TABLE = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
)
"""


class MemoryStore:
    """
    Multi-agent shared memory storage engine (SQLite version).

    Supports two modes:
    1. SQLite persistence mode: MemoryStore(db_path="./data/memory.db")
       - All writes are persisted to SQLite in real time
       - Automatically loaded on startup

    2. Pure in-memory mode (backward compatible): MemoryStore()
       - Uses :memory: SQLite (in-memory database)
       - Can still use save()/load() for JSON backup/restore

    Write:
        store.add(topic="US-China trade", keywords=["tariff","export"], summary="...", body="...")
        store.add(summary="a note", auto_extract_keywords=True)
        store.add(keywords=["concept A","concept B"])

    Search:
        store.search_exact(["tariff"])              -> {entry_id: hit_count}
        store.search_fuzzy(["trade"])               -> {entry_id: match_score}
        store.filter_by_time(ids, time_recent=24)   -> entries from the last 24 hours
    """

    def __init__(self, db_path=None):
        """
        Initialize the storage engine.

        Args:
            db_path: str | None
                - None: use in-memory SQLite (backward compatible, behaves like old dict version)
                - path string: use file SQLite with real-time persistence
        """
        if db_path is None:
            self._db_path = ":memory:"
        else:
            self._db_path = db_path
            db_dir = os.path.dirname(db_path)
            if db_dir:
                os.makedirs(db_dir, exist_ok=True)

        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        # Enable WAL mode for better concurrent performance (file mode only)
        if self._db_path != ":memory:":
            self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")

        self._init_tables()
        self._next_id = self._load_next_id()

    def _init_tables(self):
        """Create tables and indexes."""
        cur = self._conn.cursor()
        cur.execute(_CREATE_ENTRIES_TABLE)
        cur.execute(_CREATE_KEYWORD_INDEX_TABLE)
        cur.execute(_CREATE_META_TABLE)
        for idx_sql in _CREATE_INDICES:
            cur.execute(idx_sql)
        self._conn.commit()

    def _load_next_id(self):
        """Recover next_id from meta table or existing data."""
        cur = self._conn.cursor()
        # Check meta table first
        row = cur.execute("SELECT value FROM meta WHERE key='next_id'").fetchone()
        if row:
            return int(row[0])
        # Infer from existing entries
        row = cur.execute(
            "SELECT id FROM entries ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        if row:
            last_id = row[0]
            try:
                num = int(last_id.split("_")[1])
                return num
            except (IndexError, ValueError):
                pass
        return 0

    def _save_next_id(self):
        """Save next_id to meta table."""
        self._conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('next_id', ?)",
            (str(self._next_id),)
        )

    def _generate_id(self):
        self._next_id += 1
        return f"mem_{self._next_id:06d}"

    # ========================
    # Write
    # ========================

    def add(self, topic=None, keywords=None, summary=None, body=None,
            source=None, auto_extract_keywords=False, timestamp=None,
            entry_type="knowledge", category=None, date_str=None,
            importance=3, supersedes=None):
        """
        Write a memory entry.

        Args:
            topic:    str | None   -- topic
            keywords: list[str] | None -- keyword list (provided directly by AI)
            summary:  str | None   -- summary description
            body:     str | None   -- body (key points + details)
            source:   str | None   -- source identifier (which agent wrote it)
            auto_extract_keywords: bool -- auto-extract keywords via jieba if none given
            timestamp: float | None -- write timestamp, defaults to time.time()
            entry_type: str -- entry type: 'knowledge'/'experience'/'log'
            category: str | None -- category (free text)
            date_str: str | None -- date string (e.g. '2025-01-15')
            importance: int -- importance level 1~5, default 3
            supersedes: str | None -- ID of the old entry being superseded

        Returns: str -- entry_id
        """
        # Validate: at least one content field must be non-empty
        has_content = any([topic, keywords, summary, body])
        if not has_content:
            raise ValueError("At least one non-empty field is required (topic/keywords/summary/body)")

        # Validate importance range
        importance = max(1, min(5, int(importance)))

        entry_id = self._generate_id()
        ts = timestamp if timestamp is not None else time.time()

        # Handle keywords
        final_keywords = list(keywords) if keywords else []

        # Auto-extract keywords (fallback)
        if auto_extract_keywords and not final_keywords:
            text_parts = []
            if topic:
                text_parts.append(topic)
            if summary:
                text_parts.append(summary)
            if body:
                text_parts.append(body[:500])
            if text_parts:
                final_keywords = extract_keywords_jieba(" ".join(text_parts))

        keywords_json = json.dumps(final_keywords, ensure_ascii=False) if final_keywords else None

        # Handle supersedes: lower the importance of the superseded entry
        if supersedes:
            self._handle_supersedes(supersedes)

        # Insert into entries table
        self._conn.execute("""
            INSERT INTO entries (id, entry_type, topic, summary, body, source,
                                 category, timestamp, date_str, keywords_json,
                                 access_count, last_accessed, importance, supersedes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, NULL, ?, ?)
        """, (entry_id, entry_type, topic, summary, body, source,
              category, ts, date_str, keywords_json,
              importance, supersedes))

        # Build inverted index
        self._index_entry_sql(entry_id, final_keywords, topic)

        # Save next_id
        self._save_next_id()
        self._conn.commit()

        return entry_id

    def _handle_supersedes(self, old_entry_id):
        """Handle memory reconsolidation: decrease importance of superseded entry by 1."""
        row = self._conn.execute(
            "SELECT importance FROM entries WHERE id=?", (old_entry_id,)
        ).fetchone()
        if row:
            old_importance = max(1, row[0] - 1)
            self._conn.execute(
                "UPDATE entries SET importance=? WHERE id=?",
                (old_importance, old_entry_id)
            )

    def _index_entry_sql(self, entry_id, keywords, topic):
        """Build keyword_index inverted index for a memory entry."""
        pairs = []
        for kw in (keywords or []):
            pairs.append((kw, entry_id))
        if topic:
            pairs.append((topic, entry_id))
        if pairs:
            self._conn.executemany(
                "INSERT OR IGNORE INTO keyword_index (keyword, entry_id) VALUES (?, ?)",
                pairs
            )

    # ========================
    # Read / Delete
    # ========================

    def get(self, entry_id):
        """Get a memory entry by ID; returns None if not found. Returns dict compatible with old version."""
        row = self._conn.execute(
            "SELECT * FROM entries WHERE id=?", (entry_id,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_dict(row)

    def remove(self, entry_id):
        """Delete a memory entry; returns whether it succeeded."""
        row = self._conn.execute(
            "SELECT id FROM entries WHERE id=?", (entry_id,)
        ).fetchone()
        if row is None:
            return False

        # Delete inverted index
        self._conn.execute(
            "DELETE FROM keyword_index WHERE entry_id=?", (entry_id,)
        )
        # Delete entry
        self._conn.execute("DELETE FROM entries WHERE id=?", (entry_id,))
        self._conn.commit()
        return True

    # ========================
    # Search
    # ========================

    def search_exact(self, keywords):
        """
        Exact matching: find entries containing the specified keywords via keyword_index table.

        Args:  keywords: list[str]
        Returns:  dict[str, int] -- {entry_id: number of hit keywords}, sorted by hit count descending
        """
        if not keywords:
            return {}

        hits = defaultdict(int)
        for kw in keywords:
            rows = self._conn.execute(
                "SELECT entry_id FROM keyword_index WHERE keyword=?", (kw,)
            ).fetchall()
            for row in rows:
                hits[row[0]] += 1

        return dict(sorted(hits.items(), key=lambda x: x[1], reverse=True))

    def search_fuzzy(self, keywords):
        """
        Fuzzy matching: prefix + substring containment.

        "trade" -> hits "trade war", "international trade", "trade agreement"
        "Trum" -> hits "Trump"

        Args:  keywords: list[str]
        Returns:  dict[str, float] -- {entry_id: fuzzy match score}, sorted by score descending
        """
        if not keywords:
            return {}

        # Get all indexed keywords (for fuzzy matching)
        all_index_keys = [row[0] for row in
                          self._conn.execute(
                              "SELECT DISTINCT keyword FROM keyword_index"
                          ).fetchall()]

        hits = defaultdict(float)

        for query_kw in keywords:
            for idx_kw in all_index_keys:
                if query_kw == idx_kw:
                    continue    # exact match handled by search_exact

                score = 0.0

                if query_kw in idx_kw:
                    # Query word is substring of index word: "trade" in "trade war"
                    score = len(query_kw) / len(idx_kw)
                elif idx_kw in query_kw:
                    # Index word is substring of query word
                    score = len(idx_kw) / len(query_kw) * 0.8

                if score > 0:
                    rows = self._conn.execute(
                        "SELECT entry_id FROM keyword_index WHERE keyword=?",
                        (idx_kw,)
                    ).fetchall()
                    for row in rows:
                        hits[row[0]] = max(hits[row[0]], score)

        return dict(sorted(hits.items(), key=lambda x: x[1], reverse=True))

    def filter_by_time(self, entry_ids, time_range=None, time_recent=None):
        """
        Hard time-range filter (entries outside range are excluded).

        Args:
            entry_ids:   iterable of entry_ids
            time_range:  tuple(start_ts, end_ts) -- exact time range
            time_recent: float -- last N hours (mutually exclusive with time_range)
        Returns:
            list[str] -- filtered entry_id list
        """
        if time_range is None and time_recent is None:
            return list(entry_ids)

        now = time.time()
        if time_range:
            start_ts, end_ts = time_range
        else:
            start_ts = now - time_recent * 3600
            end_ts = now

        entry_id_list = list(entry_ids)
        if not entry_id_list:
            return []

        # Batch query (SQLite variable count limit)
        result = []
        batch_size = 500
        for i in range(0, len(entry_id_list), batch_size):
            batch = entry_id_list[i:i + batch_size]
            placeholders = ",".join(["?"] * len(batch))
            rows = self._conn.execute(
                f"SELECT id FROM entries WHERE id IN ({placeholders}) "
                f"AND timestamp >= ? AND timestamp <= ?",
                batch + [start_ts, end_ts]
            ).fetchall()
            result.extend(row[0] for row in rows)

        return result

    def search_by_source(self, source):
        """Filter by source agent; returns list of entry_ids."""
        rows = self._conn.execute(
            "SELECT id FROM entries WHERE source=?", (source,)
        ).fetchall()
        return [row[0] for row in rows]

    # ========================
    # Time weight (inverse function, importance-aware)
    # ========================

    @staticmethod
    def compute_time_weight(entry_timestamp, decay_lambda=0.1, now=None,
                            importance=3):
        """
        Inverse-function time decay weight (importance-aware).

        Formula:  weight = 1 / (1 + adjusted_lambda * age_days)
                  adjusted_lambda = decay_lambda * 3.0 / importance

        importance=3: equivalent to old formula 1/(1+lambda*age_days) (fully backward compatible)
        importance=5: slower decay (important things remembered longer)
        importance=1: faster decay (unimportant things forgotten sooner)

        lambda=0.1, importance=3:
          0d: 1.00    1d: 0.91    7d: 0.59
          30d: 0.25   100d: 0.09  365d: 0.027

        lambda=0.1, importance=5:
          0d: 1.00    1d: 0.94    7d: 0.70
          30d: 0.36   100d: 0.14  365d: 0.044

        Args:
            entry_timestamp: float -- entry write timestamp
            decay_lambda: float -- decay coefficient; larger means faster decay
            now: float | None -- current timestamp, defaults to time.time()
            importance: int -- importance level 1~5, default 3
        Returns:
            float -- time weight between 0 and 1
        """
        if now is None:
            now = time.time()
        age_days = max(0, (now - entry_timestamp) / 86400.0)
        # importance=3 -> adjusted_lambda = decay_lambda (identical to old formula)
        # importance=5 -> adjusted_lambda = decay_lambda * 0.6 (slower decay)
        # importance=1 -> adjusted_lambda = decay_lambda * 3.0 (faster decay)
        adjusted_lambda = decay_lambda * 3.0 / max(1, importance)
        return 1.0 / (1.0 + adjusted_lambda * age_days)

    # ========================
    # Retrieval reinforcement: access_count / last_accessed
    # ========================

    def increment_access(self, entry_ids):
        """
        Batch-increment access_count and update last_accessed.
        Called by the upper layer after query() returns results.

        Args: entry_ids: list[str] -- IDs of entries hit by the query
        """
        if not entry_ids:
            return
        now = time.time()
        for eid in entry_ids:
            self._conn.execute(
                "UPDATE entries SET access_count = access_count + 1, "
                "last_accessed = ? WHERE id = ?",
                (now, eid)
            )
        self._conn.commit()

    # ========================
    # Importance management
    # ========================

    def set_importance(self, entry_id, level):
        """
        Set the importance level of an entry.

        Args:
            entry_id: str -- entry ID
            level: int -- importance 1~5
        Returns:
            bool -- whether it succeeded (whether the entry exists)
        """
        level = max(1, min(5, int(level)))
        result = self._conn.execute(
            "UPDATE entries SET importance=? WHERE id=?",
            (level, entry_id)
        )
        self._conn.commit()
        return result.rowcount > 0

    # ========================
    # List / Stats / Utilities
    # ========================

    def list_entries(self, source_filter=None, entry_type=None):
        """
        List all entries (optionally filtered by source or entry_type).

        Returns: list[dict]
        """
        conditions = []
        params = []
        if source_filter:
            conditions.append("source = ?")
            params.append(source_filter)
        if entry_type:
            conditions.append("entry_type = ?")
            params.append(entry_type)

        sql = "SELECT * FROM entries"
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        sql += " ORDER BY timestamp DESC"

        rows = self._conn.execute(sql, params).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_all_keywords(self):
        """Return all keywords in the inverted index."""
        rows = self._conn.execute(
            "SELECT DISTINCT keyword FROM keyword_index"
        ).fetchall()
        return [row[0] for row in rows]

    def get_stats(self):
        """Return storage statistics."""
        total = self._conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
        total_keys = self._conn.execute(
            "SELECT COUNT(DISTINCT keyword) FROM keyword_index"
        ).fetchone()[0]
        sources_rows = self._conn.execute(
            "SELECT DISTINCT source FROM entries WHERE source IS NOT NULL"
        ).fetchall()
        sources = sorted(row[0] for row in sources_rows)

        return {
            "total_entries": total,
            "total_index_keys": total_keys,
            "sources": sources,
        }

    def get_entries_since(self, since_timestamp):
        """
        Get all entries after the specified timestamp.
        Used for consolidate(rebuild_from_recent=N).

        Args: since_timestamp: float -- starting timestamp
        Returns: list[dict]
        """
        rows = self._conn.execute(
            "SELECT * FROM entries WHERE timestamp >= ? ORDER BY timestamp",
            (since_timestamp,)
        ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def __len__(self):
        return self._conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0]

    # ========================
    # Persistence (backward compatible interface + native SQLite)
    # ========================

    def save(self, path):
        """
        Save to JSON file (backward compatible).
        Note: in SQLite mode, data is already persisted in real time; this method is
        mainly for exporting a backup.
        """
        entries = {}
        rows = self._conn.execute("SELECT * FROM entries").fetchall()
        for row in rows:
            d = self._row_to_dict(row)
            entries[d["id"]] = d

        data = {
            "entries": entries,
            "next_id": self._next_id,
        }
        dir_name = os.path.dirname(path)
        if dir_name:
            os.makedirs(dir_name, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def load(self, path):
        """
        Load from JSON file (backward compatible).
        Imports entries from JSON into the current SQLite database.
        Returns: bool -- whether it succeeded
        """
        if not os.path.exists(path):
            return False

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        entries = data.get("entries", {})
        next_id = data.get("next_id", 0)

        # Clear existing data
        self._conn.execute("DELETE FROM keyword_index")
        self._conn.execute("DELETE FROM entries")

        # Import
        for entry_id, entry in entries.items():
            keywords = entry.get("keywords") or []
            keywords_json = json.dumps(keywords, ensure_ascii=False) if keywords else None

            self._conn.execute("""
                INSERT OR REPLACE INTO entries
                (id, entry_type, topic, summary, body, source, category,
                 timestamp, date_str, keywords_json,
                 access_count, last_accessed, importance, supersedes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                entry_id,
                entry.get("entry_type", "knowledge"),
                entry.get("topic"),
                entry.get("summary"),
                entry.get("body"),
                entry.get("source"),
                entry.get("category"),
                entry.get("timestamp", time.time()),
                entry.get("date_str"),
                keywords_json,
                entry.get("access_count", 0),
                entry.get("last_accessed"),
                entry.get("importance", 3),
                entry.get("supersedes"),
            ))

            self._index_entry_sql(entry_id, keywords, entry.get("topic"))

        self._next_id = next_id
        self._save_next_id()
        self._conn.commit()

        return True

    # ========================
    # Internal utilities
    # ========================

    def _row_to_dict(self, row):
        """Convert sqlite3.Row to a dict format compatible with the old version."""
        keywords_json = row["keywords_json"]
        keywords = json.loads(keywords_json) if keywords_json else None

        d = {
            "id": row["id"],
            "topic": row["topic"],
            "keywords": keywords,
            "summary": row["summary"],
            "body": row["body"],
            "source": row["source"],
            "timestamp": row["timestamp"],
            # New fields
            "entry_type": row["entry_type"],
            "category": row["category"],
            "date_str": row["date_str"],
            "access_count": row["access_count"],
            "last_accessed": row["last_accessed"],
            "importance": row["importance"],
            "supersedes": row["supersedes"],
        }
        return d

    @property
    def entries(self):
        """
        Backward-compatible property: returns a dict view of all entries.
        Warning: loads all entries into memory; use only for legacy code compatibility.
        For large datasets, use list_entries() or direct SQL queries instead.
        """
        rows = self._conn.execute("SELECT * FROM entries").fetchall()
        return {row["id"]: self._row_to_dict(row) for row in rows}

    @property
    def inverted_index(self):
        """
        Backward-compatible property: returns a dict view of the inverted index.
        Warning: use only for legacy code compatibility.
        """
        rows = self._conn.execute(
            "SELECT keyword, entry_id FROM keyword_index"
        ).fetchall()
        idx = defaultdict(set)
        for row in rows:
            idx[row[0]].add(row[1])
        return dict(idx)

    def close(self):
        """Close the database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None

    def __del__(self):
        """Auto-close connection on destruction."""
        try:
            self.close()
        except Exception:
            pass
