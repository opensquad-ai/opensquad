"""Tests for websearch query parsing, optional scoring helpers, and SERP merge."""

import os
import sys

sys.path.insert(
    0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src", "plugins", "websearch", "service"))
)

from relevance import (
    apply_rerank_strategy,
    merge_serp_results,
    parse_query_terms,
    score_single_result,
    tokenize_query,
)


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


def test_weather_degraded_serp_detection():
    from web_crawler import (
        _filter_weather_intent_results,
        _query_looks_like_weather,
        _result_is_weather_relevant,
        _results_look_like_weather,
        _weather_rescue_query,
    )

    assert _query_looks_like_weather("福州 天气预报 今天")
    assert not _query_looks_like_weather("福州旅游攻略")
    assert not _results_look_like_weather(
        [
            {
                "title": "福州市_百度百科",
                "url": "https://baike.baidu.com/item/福州市/1",
                "summary": "行政区划与人口",
            },
            {
                "title": "福州10大好玩景点",
                "url": "https://www.thepaper.cn/newsDetail_forward_1",
                "summary": "三坊七巷旅游",
            },
        ]
    )
    assert _results_look_like_weather(
        [
            {
                "title": "福州天气预报",
                "url": "https://www.weather.com.cn/weather/101230101.shtml",
                "summary": "今日气温",
            }
        ]
    )
    rescue = _weather_rescue_query("福州今天天气")
    assert "site:weather.com.cn" in rescue
    # Bare weather.com.cn must still be rewritten to site:
    assert "site:weather.com.cn" in _weather_rescue_query("福州 天气 weather.com.cn")
    assert "site:weather.com" in _weather_rescue_query(
        "Fuzhou weather forecast"
    ) or "site:weather.com.cn" in _weather_rescue_query("Fuzhou weather forecast")
    assert "福州" in _weather_rescue_query("Fuzhou weather forecast")
    # Already site-scoped — leave unchanged.
    assert _weather_rescue_query("福州天气 site:weather.com.cn") == "福州天气 site:weather.com.cn"
    assert not _result_is_weather_relevant(
        {"title": "福州市人民政府", "url": "https://www.fuzhou.gov.cn/", "summary": "政务"}
    )
    assert _result_is_weather_relevant(
        {
            "title": "福州天气预报",
            "url": "https://www.weather.com.cn/weather/101230101.shtml",
            "summary": "今日气温",
        }
    )
    filtered = _filter_weather_intent_results(
        ["福州天气预报 今天"],
        [
            {"title": "福州市人民政府", "url": "https://www.fuzhou.gov.cn/", "summary": "政务"},
            {
                "title": "福州天气预报",
                "url": "https://www.weather.com.cn/weather/101230101.shtml",
                "summary": "今日气温",
            },
        ],
    )
    assert len(filtered) == 1
    assert "weather.com.cn" in filtered[0]["url"]


def test_apply_rerank_strategy_all_noise_returns_empty():
    """Do not fall back to junk tourism/gov pages when every score is below threshold."""
    results = [
        {"title": "福州市人民政府", "url": "https://www.fuzhou.gov.cn/"},
        {"title": "福州旅游攻略", "url": "https://zhuanlan.zhihu.com/p/1"},
    ]
    scores = [0.001, 0.002]
    out = apply_rerank_strategy(results, scores, noise_threshold=0.1)
    assert out == []


def test_apply_rerank_strategy_keeps_high_scores():
    results = [
        {"title": "福州天气预报", "url": "https://www.weather.com.cn/weather/101230101.shtml"},
        {"title": "福州市人民政府", "url": "https://www.fuzhou.gov.cn/"},
    ]
    scores = [0.99, 0.001]
    out = apply_rerank_strategy(results, scores, noise_threshold=0.1)
    assert len(out) == 1
    assert "weather.com.cn" in out[0]["url"]


def test_apply_rerank_strategy_weak_noise_shape_rejected():
    """Borderline (0.3-0.5) baike/gov/zhihu portal hits are dropped."""
    results = [
        {"title": "福州市_百度百科", "url": "https://baike.baidu.com/item/福州市/1"},
        {"title": "2026最新福州旅游攻略", "url": "https://zhuanlan.zhihu.com/p/2"},
    ]
    scores = [0.41, 0.39]
    out = apply_rerank_strategy(results, scores)  # default threshold 0.3, weak_floor 0.5
    assert out == []


def test_apply_rerank_strategy_weak_kept_for_real_site():
    """Borderline score on a non-noise host is kept."""
    results = [
        {"title": "2026年一季度福州市经济运行情况 GDP数据发布", "url": "https://www.kantianqi.com/gdp/1"},
    ]
    scores = [0.409]
    out = apply_rerank_strategy(results, scores)  # 0.409 < 0.5 but not a noise shape
    assert len(out) == 1


def test_apply_rerank_strategy_place_only_portal_rejected_for_year_query():
    """A gov/baike page matching only the place is dropped for a 'city 2026 GDP' query."""
    results = [
        {"title": "福州市人民政府", "url": "https://www.fuzhou.gov.cn/?page_index=1"},
        {"title": "2026年一季度福州市经济运行情况", "url": "https://www.kantianqi.com/gdp/fuzhou"},
    ]
    scores = [0.41, 0.62]
    out = apply_rerank_strategy(results, scores, query="福州市 2026 年 GDP")
    assert len(out) == 1
    assert "kantianqi.com" in out[0]["url"]


def test_apply_rerank_strategy_strong_always_kept():
    """Score >= weak_floor (0.5) survives regardless of host."""
    results = [
        {"title": "福州市人民政府 政务公开", "url": "https://www.fuzhou.gov.cn/"},
    ]
    scores = [0.998]
    out = apply_rerank_strategy(results, scores)
    assert len(out) == 1


def test_query_variants_splits_joiners():
    from websearch_api import _query_variants

    vs = _query_variants("python asyncio 并发 教程")
    assert vs[0] == "python asyncio 并发 教程"
    assert any("python asyncio" in v for v in vs)
    assert len(vs) <= 3


def test_query_variants_drops_tail_word():
    from websearch_api import _query_variants

    vs = _query_variants("RAG 检索增强生成 应用")
    assert any(v == "RAG 检索增强生成" for v in vs)
    assert len(vs) <= 3


def test_query_variants_zh_en():
    from websearch_api import _query_variants

    vs = _query_variants("2025年人工智能发展趋势 大模型")
    # Tail word "大模型" is dropped and an English variant is added.
    assert any("大模型" not in v and "人工智能" in v for v in vs)
    assert any("LLM" in v or "large language model" in v for v in vs)
    assert len(vs) <= 3


def test_query_variants_short_unchanged():
    from websearch_api import _query_variants

    assert _query_variants("GDP") == ["GDP"]
    assert _query_variants("天气") == ["天气"]
