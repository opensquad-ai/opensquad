"""Bing region selection based on query language."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import quote_plus

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_LATIN_RE = re.compile(r"[A-Za-z]")


@dataclass(frozen=True)
class BingRegion:
    base_url: str
    locale: str
    accept_language: str
    market: str
    setlang: str
    country_code: str

    @property
    def label(self) -> str:
        return "cn" if "cn.bing.com" in self.base_url else "global"

    def build_search_url(self, query: str) -> str:
        """
        Build a direct Bing SERP URL aligned with browser market params.

        Using /search?q=...&mkt=... avoids homepage form navigation and makes
        market / language / country explicit (previously dead fields).
        """
        q = quote_plus((query or "").strip())
        return f"{self.base_url}/search?q={q}&mkt={self.market}&setlang={self.setlang}&cc={self.country_code}&FORM=QBRE"

    def build_news_search_url(self, query: str) -> str:
        """Bing News vertical — better for 新闻/资讯 queries than degraded web SERPs."""
        q = quote_plus((query or "").strip())
        return (
            f"{self.base_url}/news/search?q={q}"
            f"&mkt={self.market}&setlang={self.setlang}&cc={self.country_code}&FORM=NWRFSH"
        )


def detect_bing_region(query: str) -> BingRegion:
    """
    Pick Bing endpoint/locale from query language.

    Rules:
    - Pure English / no CJK -> www.bing.com + en-US
    - Pure Chinese -> cn.bing.com + zh-CN
    - Mixed queries use cn when Chinese characters are a meaningful share of the query
    """
    text = (query or "").strip()
    if not text:
        return _global_region()

    cjk_count = len(_CJK_RE.findall(text))
    if cjk_count == 0:
        return _global_region()

    latin_count = len(_LATIN_RE.findall(text))
    if latin_count == 0:
        return _cn_region()

    cjk_ratio = cjk_count / (cjk_count + latin_count)
    if cjk_count >= 3 or cjk_ratio >= 0.15:
        return _cn_region()

    return _global_region()


def _cn_region() -> BingRegion:
    return BingRegion(
        base_url="https://cn.bing.com",
        locale="zh-CN",
        accept_language="zh-CN,zh;q=0.9,en;q=0.8",
        market="zh-CN",
        setlang="zh-hans",
        country_code="CN",
    )


def _global_region() -> BingRegion:
    return BingRegion(
        base_url="https://www.bing.com",
        locale="en-US",
        accept_language="en-US,en;q=0.9",
        market="en-US",
        setlang="en",
        country_code="US",
    )
