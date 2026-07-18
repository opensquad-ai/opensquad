"""Tests for websearch query parsing, optional scoring helpers, and SERP merge."""

import os
import sys

sys.path.insert(
    0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src", "plugins", "websearch", "service"))
)

from relevance import merge_serp_results, parse_query_terms, score_single_result, tokenize_query


def test_tokenize_query_mixed_language():
    tokens = tokenize_query("2025人工智能发展趋势 AI trends")
    assert "trends" in tokens
    assert any("人工" in t or "智能" in t or "人工智能" in t for t in tokens)


def test_parse_query_terms_comma_separated():
    terms = parse_query_terms("深度学习, 神经网络, Transformer 架构")
    assert "深度学习" in terms.phrases
    assert "神经网络" in terms.phrases
    assert "transformer 架构" in terms.phrases or "transformer" in terms.tokens


def test_parse_query_terms_space_separated_phrase():
    terms = parse_query_terms("Python asyncio tutorial")
    assert "python asyncio tutorial" in terms.phrases
    assert "python" in terms.tokens or "asyncio" in terms.tokens


def test_score_single_result_prefers_title_match():
    high = score_single_result(
        "Python asyncio tutorial",
        title="Complete Python asyncio tutorial for beginners",
        summary="Learn other programming topics",
        url="https://example.com/blog/other",
        rank=0,
    )
    low = score_single_result(
        "Python asyncio tutorial",
        title="Unrelated cooking recipes",
        summary="Nothing about programming here",
        url="https://example.com/food",
        rank=0,
    )
    assert high > low


def test_parse_query_terms_keeps_date_as_single_token():
    terms = parse_query_terms("福州天气 2026-07-13")
    assert "2026-07-13" in terms.tokens
    assert "2026" not in terms.tokens
    assert "天气" in terms.tokens or any("天气" in p for p in terms.phrases)


def test_merge_serp_preserves_bing_order():
    results = merge_serp_results(
        ['"关中王来了" 梗'],
        [
            [
                {
                    "title": "关中地区_百度百科",
                    "url": "https://baike.baidu.com/item/关中地区/1",
                    "summary": "地理介绍",
                },
                {
                    "title": "关中王来了是什么梗",
                    "url": "https://meme.example.com/guanzhongwang",
                    "summary": "网络梗出处与用法",
                },
                {
                    "title": "关中平原_百度百科",
                    "url": "https://baike.baidu.com/item/关中平原/2",
                    "summary": "渭河平原",
                },
            ]
        ],
    )
    assert [r["url"] for r in results] == [
        "https://baike.baidu.com/item/关中地区/1",
        "https://meme.example.com/guanzhongwang",
        "https://baike.baidu.com/item/关中平原/2",
    ]
    assert "relevance_score" not in results[0]
    assert "matched_keywords" not in results[0]
    assert results[0]["snippet"] == results[0]["summary"]


def test_merge_serp_dedupes_keeping_first_and_tracks_queries():
    results = merge_serp_results(
        ["Python asyncio", "async programming Python"],
        [
            [
                {
                    "title": "Python asyncio guide",
                    "url": "https://docs.python.org/asyncio",
                    "summary": "Official asyncio documentation",
                },
                {
                    "title": "Random news",
                    "url": "https://news.example.com/weather",
                    "summary": "Today's weather forecast",
                },
            ],
            [
                {
                    "title": "Async programming in Python",
                    "url": "https://docs.python.org/asyncio",
                    "summary": "Covers asyncio event loops",
                }
            ],
        ],
    )
    assert [r["url"] for r in results] == [
        "https://docs.python.org/asyncio",
        "https://news.example.com/weather",
    ]
    assert results[0]["match_count"] == 2
    assert "Python asyncio" in results[0]["matched_queries"]
    assert "async programming Python" in results[0]["matched_queries"]


def test_merge_serp_filters_ad_marker():
    results = merge_serp_results(
        ["sneakers"],
        [
            [
                {
                    "title": "Buy shoes online",
                    "url": "https://shop.example.com/shoes",
                    "summary": "选购最新款运动鞋",
                },
                {
                    "title": "Running shoe review",
                    "url": "https://reviews.example.com/shoes",
                    "summary": "Independent review of trail shoes",
                },
            ]
        ],
        ad_str_list=["选购"],
    )
    assert [r["url"] for r in results] == ["https://reviews.example.com/shoes"]
