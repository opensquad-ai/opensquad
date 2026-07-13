"""Tests for websearch relevance scoring."""

import os
import sys

sys.path.insert(
    0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src", "plugins", "websearch", "service"))
)

from relevance import merge_and_rank_results, parse_query_terms, score_single_result, tokenize_query


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


def test_score_single_result_prefers_multi_keyword_coverage():
    query = "量子计算, 硬件架构, 2025 发展趋势"
    strong = score_single_result(
        query,
        title="2025量子计算硬件架构发展趋势报告",
        summary="覆盖量子计算硬件与架构演进",
        url="https://research.example.com/quantum-hardware-2025",
        rank=0,
    )
    weak = score_single_result(
        query,
        title="2025年科技新闻汇总",
        summary="仅提到量子一词",
        url="https://news.example.com/tech-digest",
        rank=0,
    )
    assert strong > weak


def test_merge_and_rank_results_tracks_query_provenance():
    results = merge_and_rank_results(
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

    assert len(results) >= 1
    top = results[0]
    assert top["url"] == "https://docs.python.org/asyncio"
    assert top["match_count"] == 2
    assert "Python asyncio" in top["matched_queries"]
    assert top["relevance_score"] >= 0.2
    assert top["snippet"] == top["summary"]
    assert len(top["matched_keywords"]) >= 1


def test_merge_and_rank_results_comma_keywords_rank_stronger_page():
    results = merge_and_rank_results(
        ["福州天气, 福州气温, 福州降雨预报"],
        [
            [
                {
                    "title": "福州本地生活资讯",
                    "url": "https://life.example.com/fuzhou",
                    "summary": "福州美食与旅游推荐",
                },
                {
                    "title": "福州天气预报_气温_降雨",
                    "url": "https://weather.example.com/fuzhou",
                    "summary": "提供福州天气、气温和降雨预报",
                },
            ]
        ],
    )

    assert results[0]["url"] == "https://weather.example.com/fuzhou"
    assert any("天气" in kw or "气温" in kw or "降雨" in kw for kw in results[0]["matched_keywords"])


def test_merge_and_rank_results_filters_low_relevance():
    results = merge_and_rank_results(
        ["quantum computing hardware 2025"],
        [
            [
                {
                    "title": "Buy shoes online",
                    "url": "https://shop.example.com/shoes",
                    "summary": "选购最新款运动鞋",
                },
                {
                    "title": "Quantum computing hardware advances in 2025",
                    "url": "https://research.example.com/quantum",
                    "summary": "Overview of quantum hardware progress",
                },
            ]
        ],
    )

    urls = [item["url"] for item in results]
    assert "https://shop.example.com/shoes" not in urls
    assert "https://research.example.com/quantum" in urls


def test_merge_and_rank_results_keeps_minimum_when_all_low():
    results = merge_and_rank_results(
        ["xyzabc123"],
        [
            [
                {
                    "title": "Completely unrelated page",
                    "url": "https://example.com/a",
                    "summary": "Nothing relevant",
                }
            ]
        ],
        min_score=0.99,
    )
    assert len(results) >= 1


def test_parse_query_terms_keeps_date_as_single_token():
    terms = parse_query_terms("福州天气 2026-07-13")
    assert "2026-07-13" in terms.tokens
    assert "2026" not in terms.tokens
    assert "天气" in terms.tokens or any("天气" in p for p in terms.phrases)


def test_score_weather_intent_prefers_weather_page_over_baike():
    query = "福州天气 2026-07-13"
    weather = score_single_result(
        query,
        title="7月13日福州天气_福州2026年7月13日天气预报_天气后报",
        summary="查询福州2026年7月13日的历史天气与预报",
        url="https://www.tianqihoubao.com/weather/fuzhou/20260713.htm",
        rank=1,
    )
    baike = score_single_result(
        query,
        title="福州市_百度百科",
        summary="福州市地貌属典型的河口盆地，属亚热带季风气候，国家历史文化名城",
        url="https://baike.baidu.com/item/福州市/366603",
        rank=0,
    )
    tourism = score_single_result(
        query,
        title="福州10大好玩景点，第一次到福州游玩一定不要错过",
        summary="福州好玩的景点有：三坊七巷，平潭岛，福州国家森林公园",
        url="https://www.thepaper.cn/newsDetail_forward_26004317",
        rank=0,
    )
    assert weather > baike
    assert weather > tourism
    assert baike <= 0.15
    assert tourism <= 0.15


def test_merge_and_rank_weather_query_demotes_off_intent_pages():
    results = merge_and_rank_results(
        ["福州天气 2026-07-13"],
        [
            [
                {
                    "title": "福州市_百度百科",
                    "url": "https://baike.baidu.com/item/福州市/366603",
                    "summary": "福州市地貌属典型的河口盆地，属亚热带季风气候",
                },
                {
                    "title": "福州10大好玩景点",
                    "url": "https://www.thepaper.cn/newsDetail_forward_26004317",
                    "summary": "福州好玩的景点有：三坊七巷，平潭岛",
                },
                {
                    "title": "7月13日福州天气_天气预报",
                    "url": "https://www.tianqihoubao.com/weather/fuzhou/20260713.htm",
                    "summary": "福州2026年7月13日天气预报与气温",
                },
            ]
        ],
    )
    assert results[0]["url"] == "https://www.tianqihoubao.com/weather/fuzhou/20260713.htm"
    assert results[0]["relevance_score"] > results[-1]["relevance_score"]
