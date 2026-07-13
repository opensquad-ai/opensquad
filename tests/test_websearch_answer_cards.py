"""Tests for Bing answer-card extraction and ranking."""

import os
import sys

sys.path.insert(
    0,
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src", "plugins", "websearch", "service")),
)
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "fixtures")))

from bing_answer_cards import parse_answer_cards_from_html
from bing_weather_serp import FIXTURE_RELATED_ONLY_HTML, FIXTURE_WEATHER_SERP_HTML
from relevance import merge_and_rank_results


def test_parse_weather_answer_card_from_fixture():
    cards = parse_answer_cards_from_html(
        FIXTURE_WEATHER_SERP_HTML,
        "福州天气 2026-07-13",
        region_label="cn",
        base_url="https://cn.bing.com",
    )
    assert cards
    top = cards[0]
    assert top["result_type"] == "answer_card"
    assert top["card_kind"] == "weather"
    assert "34" in top["summary"] or "晴" in top["summary"] or "天气" in top["summary"]
    assert top["url"]
    assert "msn.com" in top["url"] or top["url"].startswith("bing-answer://")


def test_parse_skips_related_searches_noise():
    cards = parse_answer_cards_from_html(
        FIXTURE_RELATED_ONLY_HTML,
        "福州",
        region_label="cn",
    )
    assert cards == []


def test_merge_ranks_answer_card_above_baike():
    query = "福州天气 2026-07-13"
    cards = parse_answer_cards_from_html(
        FIXTURE_WEATHER_SERP_HTML,
        query,
        region_label="cn",
    )
    organics = [
        {
            "title": "福州市_百度百科",
            "url": "https://baike.baidu.com/item/福州市/366603",
            "summary": "福州市地貌属典型的河口盆地，属亚热带季风气候",
            "result_type": "organic",
        },
        {
            "title": "福州10大好玩景点",
            "url": "https://www.thepaper.cn/newsDetail_forward_26004317",
            "summary": "福州好玩的景点有：三坊七巷，平潭岛",
            "result_type": "organic",
        },
    ]
    results = merge_and_rank_results([query], [cards + organics])
    assert results
    assert results[0]["result_type"] == "answer_card"
    assert results[0]["card_kind"] == "weather"
    assert "34" in results[0]["summary"] or "晴" in results[0]["summary"] or "天气" in results[0]["summary"]
