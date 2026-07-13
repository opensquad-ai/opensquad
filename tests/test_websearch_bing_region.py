"""Tests for Bing region auto-selection."""

import os
import sys

sys.path.insert(
    0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src", "plugins", "websearch", "service"))
)

from bing_region import detect_bing_region


def test_detect_bing_region_chinese_query():
    region = detect_bing_region("福州天气预报")
    assert region.base_url == "https://cn.bing.com"
    assert region.locale == "zh-CN"
    assert region.label == "cn"
    assert region.market == "zh-CN"
    assert region.country_code == "CN"


def test_detect_bing_region_english_query():
    region = detect_bing_region("Python asyncio tutorial")
    assert region.base_url == "https://www.bing.com"
    assert region.locale == "en-US"
    assert region.label == "global"
    assert region.market == "en-US"


def test_detect_bing_region_mixed_query_prefers_chinese():
    region = detect_bing_region("人工智能 AI development trends 2025")
    assert region.base_url == "https://cn.bing.com"


def test_detect_bing_region_english_with_single_cjk_uses_global():
    region = detect_bing_region("OpenAI GPT-4 中文 benchmark")
    assert region.base_url == "https://www.bing.com"


def test_build_search_url_includes_market_params():
    region = detect_bing_region("福州天气 2026-07-13")
    url = region.build_search_url("福州天气 2026-07-13")
    assert url.startswith("https://cn.bing.com/search?q=")
    assert "mkt=zh-CN" in url
    assert "setlang=zh-hans" in url
    assert "cc=CN" in url
    assert "FORM=QBRE" in url
