# -*- coding: utf-8 -*-
"""Time parser - key assertion tests"""
import sys
import os
import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from memory.storage import parse_time_expression

NOW = datetime.datetime(2026, 2, 11, 14, 30, 0)  # Wednesday
TODAY = NOW.date()
passed = 0
failed = 0

def assert_test(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  PASS: {name}")
    else:
        failed += 1
        print(f"  FAIL: {name}  {detail}")

# 1. Last week -> 2026-02-02 (Mon) ~ 2026-02-08 (Sun)
r = parse_time_expression("\u4e0a\u5468\u6211\u5bb6\u7684\u5c0f\u732b\u5403\u4e86\u4e24\u6839\u706b\u817f", now=NOW)
assert_test("\u4e0a\u5468-time_range_start",
    datetime.datetime.fromtimestamp(r["time_range"][0]).strftime("%Y-%m-%d") == "2026-02-02",
    f'got {datetime.datetime.fromtimestamp(r["time_range"][0]).strftime("%Y-%m-%d")}')
assert_test("\u4e0a\u5468-time_range_end",
    datetime.datetime.fromtimestamp(r["time_range"][1]).strftime("%Y-%m-%d") == "2026-02-08")
assert_test("\u4e0a\u5468-time_expr", r["time_expr"] == "\u4e0a\u5468")
assert_test("\u4e0a\u5468-keywords_contain_cat", "\u5c0f\u732b" in r["keywords"])
assert_test("\u4e0a\u5468-keywords_contain_ham", "\u706b\u817f" in r["keywords"])

# 2. Last year -> all of 2025
r = parse_time_expression("\u53bb\u5e74\u6c7d\u8f66\u7ef4\u4fee\u7684\u4fdd\u517b\u8d39\u662f300\u7f8e\u5143", now=NOW)
assert_test("\u53bb\u5e74-time_expr", r["time_expr"] == "\u53bb\u5e74")
assert_test("\u53bb\u5e74-keywords_contain_repair", "\u7ef4\u4fee" in r["keywords"] or "\u6c7d\u8f66\u7ef4\u4fee" in r["keywords"])
assert_test("\u53bb\u5e74-300_not_time", "300" not in (r["time_expr"] or ""))

# 3. Previous two months -> 2025-12-11 ~ 2026-02-11
r = parse_time_expression("\u524d\u4e24\u4e2a\u6708\u4f60\u624d\u521a\u4f5c\u5b8c\u7684\u624b\u672f", now=NOW)
assert_test("\u524d\u4e24\u4e2a\u6708-time_expr", r["time_expr"] == "\u524d\u4e24\u4e2a\u6708")
assert_test("\u524d\u4e24\u4e2a\u6708-start_date",
    datetime.datetime.fromtimestamp(r["time_range"][0]).strftime("%Y-%m-%d") == "2025-12-11")
assert_test("\u524d\u4e24\u4e2a\u6708-keywords_contain_surgery", "\u624b\u672f" in r["keywords"])

# 4. This year May to August -> 2026-05-01 ~ 2026-08-31
r = parse_time_expression("\u4eca\u5e745\u6708\u4efd\u52308\u6708\u4efd\u7684\u5c71\u59c6\u5723\u8bde\u8d60\u793c\u6d3b\u52a8", now=NOW)
assert_test("\u6708\u8303\u56f4-time_expr", r["time_expr"] == "\u4eca\u5e745\u6708\u4efd\u52308\u6708\u4efd")
assert_test("\u6708\u8303\u56f4-start_may",
    datetime.datetime.fromtimestamp(r["time_range"][0]).strftime("%Y-%m") == "2026-05")
assert_test("\u6708\u8303\u56f4-end_aug",
    datetime.datetime.fromtimestamp(r["time_range"][1]).strftime("%Y-%m") == "2026-08")
assert_test("\u6708\u8303\u56f4-keywords_contain_sams", "\u5c71\u59c6" in r["keywords"])

# 5. Today -> 2026-02-11
r = parse_time_expression("\u4eca\u5929\u53d1\u751f\u4e86\u4ec0\u4e48\u4e8b", now=NOW)
assert_test("\u4eca\u5929-time_expr", r["time_expr"] == "\u4eca\u5929")
assert_test("\u4eca\u5929-date_correct",
    datetime.datetime.fromtimestamp(r["time_range"][0]).strftime("%Y-%m-%d") == "2026-02-11")

# 6. Day before yesterday -> 2026-02-09 (should not be mismatched by before_n)
r = parse_time_expression("\u524d\u5929\u7684\u4f1a\u8bae\u8bb0\u5f55", now=NOW)
assert_test("\u524d\u5929-time_expr", r["time_expr"] == "\u524d\u5929")
assert_test("\u524d\u5929-date_correct",
    datetime.datetime.fromtimestamp(r["time_range"][0]).strftime("%Y-%m-%d") == "2026-02-09")

# 7. Year before last -> 2024 (should not be mismatched by before_n)
r = parse_time_expression("\u524d\u5e74\u7684\u4e8b\u60c5", now=NOW)
assert_test("\u524d\u5e74-time_expr", r["time_expr"] == "\u524d\u5e74")
assert_test("\u524d\u5e74-year_correct",
    datetime.datetime.fromtimestamp(r["time_range"][0]).strftime("%Y") == "2024")

# 8. $300 -> no time match
r = parse_time_expression("300\u7f8e\u5143\u7684\u6c7d\u8f66\u4fdd\u517b", now=NOW)
assert_test("300usd-no_time_range", r["time_range"] is None)
assert_test("300usd-no_time_expr", r["time_expr"] is None)

# 9. Recent three days -> 2026-02-08 ~ 2026-02-11
r = parse_time_expression("\u6700\u8fd1\u4e09\u5929\u7684\u65b0\u95fb", now=NOW)
assert_test("\u6700\u8fd1\u4e09\u5929-time_expr", r["time_expr"] == "\u6700\u8fd1\u4e09\u5929")
assert_test("\u6700\u8fd1\u4e09\u5929-start_date",
    datetime.datetime.fromtimestamp(r["time_range"][0]).strftime("%Y-%m-%d") == "2026-02-08")

# 10. Two months ago -> same as "previous two months"
r = parse_time_expression("\u4e24\u4e2a\u6708\u524d\u4e70\u7684\u8f66", now=NOW)
assert_test("N\u6708\u524d-time_expr", r["time_expr"] == "\u4e24\u4e2a\u6708\u524d")
assert_test("N\u6708\u524d-start_dec",
    datetime.datetime.fromtimestamp(r["time_range"][0]).strftime("%Y-%m-%d") == "2025-12-11")

# 11. Year 2024 -> full year
r = parse_time_expression("2024\u5e74\u7684\u65c5\u884c\u8ba1\u5212", now=NOW)
assert_test("2024\u5e74-time_expr", r["time_expr"] == "2024\u5e74")
assert_test("2024\u5e74-year_correct",
    datetime.datetime.fromtimestamp(r["time_range"][0]).strftime("%Y") == "2024")

# 12. Empty input
r = parse_time_expression("", now=NOW)
assert_test("empty_input-no_time", r["time_range"] is None)

r = parse_time_expression(None, now=NOW)
assert_test("None_input-no_time", r["time_range"] is None)

# 13. This year May 3rd -> specific date
r = parse_time_expression("\u4eca\u5e745\u67083\u65e5\u7684\u7ea6\u4f1a", now=NOW)
assert_test("\u5e74\u6708\u65e5-time_expr", r["time_expr"] == "\u4eca\u5e745\u67083\u65e5")
assert_test("\u5e74\u6708\u65e5-date_correct",
    datetime.datetime.fromtimestamp(r["time_range"][0]).strftime("%Y-%m-%d") == "2026-05-03")
assert_test("\u5e74\u6708\u65e5-keywords_contain_date", "\u7ea6\u4f1a" in r["keywords"])

# 14. Last month -> January 2026
r = parse_time_expression("\u4e0a\u4e2a\u6708\u7684\u8d26\u5355", now=NOW)
assert_test("\u4e0a\u4e2a\u6708-time_expr", r["time_expr"] == "\u4e0a\u4e2a\u6708")
assert_test("\u4e0a\u4e2a\u6708-month_correct",
    datetime.datetime.fromtimestamp(r["time_range"][0]).strftime("%Y-%m") == "2026-01")

# 15. Two weeks ago
r = parse_time_expression("\u4e0a\u4e0a\u5468\u7684\u4f1a\u8bae", now=NOW)
assert_test("\u4e0a\u4e0a\u5468-time_expr", r["time_expr"] == "\u4e0a\u4e0a\u5468")
assert_test("\u4e0a\u4e0a\u5468-start_date",
    datetime.datetime.fromtimestamp(r["time_range"][0]).strftime("%Y-%m-%d") == "2026-01-26")

# 16. This week -> 2026-02-09 (Mon) ~ 2026-02-15 (Sun)
r = parse_time_expression("\u672c\u5468\u7684\u5de5\u4f5c\u5b89\u6392", now=NOW)
assert_test("\u672c\u5468-time_expr", r["time_expr"] == "\u672c\u5468")
assert_test("\u672c\u5468-start_monday",
    datetime.datetime.fromtimestamp(r["time_range"][0]).strftime("%Y-%m-%d") == "2026-02-09")

# 17. This month -> February 2026
r = parse_time_expression("\u672c\u6708\u7684\u5f00\u652f\u660e\u7ec6", now=NOW)
assert_test("\u672c\u6708-time_expr", r["time_expr"] == "\u672c\u6708")
assert_test("\u672c\u6708-month_correct",
    datetime.datetime.fromtimestamp(r["time_range"][0]).strftime("%Y-%m") == "2026-02")

# 18. This year May -> single month
r = parse_time_expression("\u4eca\u5e745\u6708\u7684\u8003\u8bd5", now=NOW)
assert_test("\u4eca\u5e745\u6708-time_expr", r["time_expr"] == "\u4eca\u5e745\u6708")
assert_test("\u4eca\u5e745\u6708-month_correct",
    datetime.datetime.fromtimestamp(r["time_range"][0]).strftime("%Y-%m") == "2026-05")

# 19. Last year December
r = parse_time_expression("\u53bb\u5e7412\u6708\u4efd\u53d1\u751f\u7684\u4e8b", now=NOW)
assert_test("\u53bb\u5e7412\u6708-time_expr", r["time_expr"] == "\u53bb\u5e7412\u6708\u4efd")
assert_test("\u53bb\u5e7412\u6708-month_correct",
    datetime.datetime.fromtimestamp(r["time_range"][0]).strftime("%Y-%m") == "2025-12")

# ============================================================
# Confidence system tests -- anti-false-positive protection
# ============================================================

print(f"\n{'='*60}")
print("  Confidence System Tests -- Anti-False-Positive")
print(f"{'='*60}")

# -- Negative scenarios: time word recognized but confidence too low, time_range=None --

# C1. Descriptive verb "mentioned" -> should not parse as constraint
r = parse_time_expression("\u90a3\u7bc7\u6587\u7ae0\u91cc\u63d0\u5230\u4e0a\u5468\u7684\u5e02\u573a\u6ce2\u52a8", now=NOW)
assert_test("C1-desc_verb-time_expr_recognized", r["time_expr"] == "\u4e0a\u5468")
assert_test("C1-desc_verb-time_range_is_None", r["time_range"] is None)
assert_test("C1-desc_verb-confidence<0.45", r["confidence"] is not None and r["confidence"] < 0.45)
assert_test("C1-desc_verb-original_text_preserved", r["cleaned_text"] == "\u90a3\u7bc7\u6587\u7ae0\u91cc\u63d0\u5230\u4e0a\u5468\u7684\u5e02\u573a\u6ce2\u52a8")

# C2. Descriptive verb "analyzed" + "in" -> should not parse
r = parse_time_expression("\u62a5\u544a\u4e2d\u5206\u6790\u4e862024\u5e74\u7684GDP\u6570\u636e", now=NOW)
assert_test("C2-analysis_report-time_expr_recognized", r["time_expr"] == "2024\u5e74")
assert_test("C2-analysis_report-time_range_is_None", r["time_range"] is None)
assert_test("C2-analysis_report-confidence<0.45", r["confidence"] is not None and r["confidence"] < 0.45)

# C3. Inside book title brackets -> should not parse
r = parse_time_expression("\u30102025\u5e74\u7ecf\u6d4e\u5c55\u671b\u3011\u8fd9\u672c\u4e66\u5f88\u4e0d\u9519", now=NOW)
assert_test("C3-book_title-time_expr_recognized", r["time_expr"] == "2025\u5e74")
assert_test("C3-book_title-time_range_is_None", r["time_range"] is None)
assert_test("C3-book_title-quote_signal_present", "-\u5f15\u53f7\u5185" in r["confidence_signals"])

# C4. Descriptive verb "said" -> should not parse
r = parse_time_expression("\u4ed6\u8bf4\u4e86\u53bb\u5e74\u53d1\u751f\u7684\u4e00\u4ef6\u4e8b\u60c5", now=NOW)
assert_test("C4-said_last_year-time_expr_recognized", r["time_expr"] == "\u53bb\u5e74")
assert_test("C4-said_last_year-time_range_is_None", r["time_range"] is None)

# C5. "in article" + "discuss" -> should not parse
r = parse_time_expression("\u6587\u7ae0\u91cc\u8ba8\u8bba\u4eca\u5e745\u6708\u7684\u6570\u636e\u53d8\u5316\u8d8b\u52bf", now=NOW)
assert_test("C5-article_discuss-time_expr_recognized", r["time_expr"] == "\u4eca\u5e745\u6708")
assert_test("C5-article_discuss-time_range_is_None", r["time_range"] is None)

# -- Positive scenarios: query verb or sentence-initial -> should parse normally --

# C6. Query verb "help me find" -> should parse
r = parse_time_expression("\u5e2e\u6211\u627e\u4e0a\u5468\u7684\u4f1a\u8bae\u8bb0\u5f55", now=NOW)
assert_test("C6-help_find-time_range_has_value", r["time_range"] is not None)
assert_test("C6-help_find-confidence>=0.45", r["confidence"] >= 0.45)
assert_test("C6-help_find-query_verb_signal_present", "+\u67e5\u8be2\u52a8\u8bcd" in r["confidence_signals"])

# C7. Query verb "search" -> should parse
r = parse_time_expression("\u641c\u72672024\u5e74\u7684\u62a5\u544a", now=NOW)
assert_test("C7-search-time_range_has_value", r["time_range"] is not None)
assert_test("C7-search-confidence>=0.45", r["confidence"] >= 0.45)

# C8. Query verb "view" -> should parse
r = parse_time_expression("\u67e5\u770b\u6700\u8fd1\u4e09\u5929\u7684\u65e5\u5fd7", now=NOW)
assert_test("C8-view-time_range_has_value", r["time_range"] is not None)
assert_test("C8-view-confidence>=0.45", r["confidence"] >= 0.45)

# C9. Sentence-initial time word (typical query) -> should parse
r = parse_time_expression("\u6628\u5929\u4e0b\u5348\u7684\u8fdb\u5c55\u5982\u4f55", now=NOW)
assert_test("C9-sentence_initial_yesterday-time_range_has_value", r["time_range"] is not None)
assert_test("C9-sentence_initial_yesterday-confidence>=0.45", r["confidence"] >= 0.45)
assert_test("C9-sentence_initial_yesterday-sentence_initial_signal", "+\u53e5\u9996" in r["confidence_signals"])

# C10. confidence_threshold=0.0 backward compatibility (disable filtering)
r = parse_time_expression("\u90a3\u7bc7\u6587\u7ae0\u91cc\u63d0\u5230\u4e0a\u5468\u7684\u5e02\u573a\u6ce2\u52a8", now=NOW,
                          confidence_threshold=0.0)
assert_test("C10-threshold_0-time_range_has_value", r["time_range"] is not None,
            "confidence_threshold=0.0 should disable filtering")

# C11. Chinese double quotes -> should not parse
r = parse_time_expression('\u4ed6\u5f15\u7528\u4e86\u201c\u53bb\u5e74\u7684\u6570\u636e\u201d\u6765\u8bf4\u660e\u95ee\u9898', now=NOW)
assert_test("C11-chinese_quotes-time_expr_recognized", r["time_expr"] == "\u53bb\u5e74")
assert_test("C11-chinese_quotes-time_range_is_None", r["time_range"] is None)

# C12. No match -> confidence should be None
r = parse_time_expression("\u8fd9\u662f\u4e00\u6bb5\u6ca1\u6709\u65f6\u95f4\u8bcd\u7684\u6587\u672c", now=NOW)
assert_test("C12-no_match-confidence_is_None", r["confidence"] is None)
assert_test("C12-no_match-signals_is_None", r["confidence_signals"] is None)

print(f"\n{'='*60}")
print(f"  Result: {passed} passed, {failed} failed, {passed+failed} total")
print(f"{'='*60}")

if failed > 0:
    exit(1)
else:
    print("  ALL TESTS PASSED!")
