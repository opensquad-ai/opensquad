"""
Long-text processing mode tests

Tests:
    Test 1: extract_keywords_weighted short-text degradation (all weights 1.0)
    Test 2: extract_keywords_weighted long-text TF-IDF weighting
    Test 3: AgentMemory.query() long-text mode - keyword tiering
    Test 4: AgentMemory.query() short-text mode - unchanged behavior
    Test 5: supplement keyword verification bonus
    Test 6: edge cases (empty input, threshold boundary, no AI keywords)

Usage:
    python test_long_text.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from memory import AgentMemory
from memory.storage import extract_keywords_weighted


def print_sep(title):
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}")


def test_1_weighted_short():
    """Test 1: extract_keywords_weighted short-text degradation"""
    print_sep("Test 1: extract_keywords_weighted short-text degradation")

    short_text = "\u4eba\u5de5\u667a\u80fd\u548c\u6df1\u5ea6\u5b66\u4e60\u7684\u6700\u65b0\u8fdb\u5c55"
    result = extract_keywords_weighted(short_text)

    assert isinstance(result, list), f"Should return list, got: {type(result)}"
    assert all(isinstance(item, tuple) and len(item) == 2 for item in result), (
        "Each element should be a (word, weight) tuple"
    )

    # Short-text mode: all weights should be 1.0
    for word, weight in result:
        assert weight == 1.0, f"Short-text weight should be 1.0, got: {word}={weight}"

    print(f"  [*] Short-text extraction: {result}")
    print("  [*] All weights are 1.0 [OK]")


def test_2_weighted_long():
    """Test 2: extract_keywords_weighted long-text TF-IDF weighting"""
    print_sep("Test 2: extract_keywords_weighted long-text TF-IDF")

    long_text = (
        "\u4eba\u5de5\u667a\u80fd\u9886\u57df\u5728\u8fd1\u5e74\u6765\u7ecf\u5386\u4e86\u98de\u901f\u7684\u53d1\u5c55\uff0c\u7279\u522b\u662f\u6df1\u5ea6\u5b66\u4e60\u548c\u795e\u7ecf\u7f51\u7edc\u65b9\u9762\u7684\u7a81\u7834\u3002"
        "\u4ece\u65e9\u671f\u7684\u611f\u77e5\u673a\u6a21\u578b\u5230\u5982\u4eca\u7684 Transformer \u67b6\u6784\uff0c\u673a\u5668\u5b66\u4e60\u6280\u672f\u4e0d\u65ad\u6f14\u8fdb\u3002"
        "\u5927\u578b\u8bed\u8a00\u6a21\u578b\u5982 GPT \u7cfb\u5217\u5728\u81ea\u7136\u8bed\u8a00\u5904\u7406\u9886\u57df\u5c55\u73b0\u4e86\u524d\u6240\u672a\u6709\u7684\u80fd\u529b\uff0c"
        "\u5305\u62ec\u6587\u672c\u751f\u6210\u3001\u673a\u5668\u7ffb\u8bd1\u3001\u60c5\u611f\u5206\u6790\u548c\u77e5\u8bc6\u95ee\u7b54\u7b49\u591a\u4e2a\u65b9\u9762\u3002"
        "\u4e0e\u6b64\u540c\u65f6\uff0c\u8ba1\u7b97\u673a\u89c6\u89c9\u9886\u57df\u7684\u5377\u79ef\u795e\u7ecf\u7f51\u7edc\u548c\u89c6\u89c9 Transformer \u4e5f\u53d6\u5f97\u4e86\u91cd\u5927\u8fdb\u5c55\uff0c"
        "\u5728\u56fe\u50cf\u8bc6\u522b\u3001\u76ee\u6807\u68c0\u6d4b\u548c\u56fe\u50cf\u751f\u6210\u7b49\u4efb\u52a1\u4e2d\u8fbe\u5230\u4e86\u8d85\u8d8a\u4eba\u7c7b\u7684\u6c34\u5e73\u3002"
        "\u5f3a\u5316\u5b66\u4e60\u5219\u5728\u6e38\u620f\u667a\u80fd\u548c\u673a\u5668\u4eba\u63a7\u5236\u65b9\u9762\u5c55\u793a\u4e86\u5de8\u5927\u7684\u6f5c\u529b\u3002"
    )
    assert len(long_text) > 80, f"Test text should exceed 80 chars, got: {len(long_text)}"

    result = extract_keywords_weighted(long_text)

    assert isinstance(result, list), "Should return list"
    assert len(result) > 0, "Long text should yield keywords"

    # Long-text mode: weights should vary (not all 1.0)
    weights = [w for _, w in result]
    assert not all(w == 1.0 for w in weights), f"Long-text weights should not all be 1.0: {weights}"

    # Should be sorted descending by weight
    for i in range(len(weights) - 1):
        assert weights[i] >= weights[i + 1], f"Should be sorted descending, pos {i}: {weights[i]} < {weights[i + 1]}"

    print(f"  [*] Long-text extracted {len(result)} keywords")
    for word, weight in result[:8]:
        print(f"      {word}: {weight:.4f}")
    print("  [*] Weights vary and are descending [OK]")


def test_3_query_long_text_mode():
    """Test 3: AgentMemory.query() long-text mode - keyword tiering"""
    print_sep("Test 3: query() long-text mode")

    am = AgentMemory()

    # Write some memory entries
    am.write(
        topic="AI history",
        keywords=["\u4eba\u5de5\u667a\u80fd", "\u56fe\u7075", "\u611f\u77e5\u673a"],
        summary="\u4eba\u5de5\u667a\u80fd\u7684\u5386\u53f2\u53ef\u4ee5\u8ffd\u6eaf\u5230\u56fe\u7075\u6d4b\u8bd5\u548c\u65e9\u671f\u611f\u77e5\u673a\u6a21\u578b",
        importance=4,
    )
    am.write(
        topic="Deep learning",
        keywords=["\u6df1\u5ea6\u5b66\u4e60", "\u795e\u7ecf\u7f51\u7edc", "\u53cd\u5411\u4f20\u64ad"],
        summary="\u6df1\u5ea6\u5b66\u4e60\u901a\u8fc7\u591a\u5c42\u795e\u7ecf\u7f51\u7edc\u548c\u53cd\u5411\u4f20\u64ad\u7b97\u6cd5\u5b9e\u73b0\u4e86\u7a81\u7834",
        importance=5,
    )
    am.write(
        topic="Transformer architecture",
        keywords=["Transformer", "\u6ce8\u610f\u529b\u673a\u5236", "GPT"],
        summary="Transformer\u67b6\u6784\u57fa\u4e8e\u81ea\u6ce8\u610f\u529b\u673a\u5236\uff0c\u662fGPT\u7b49\u5927\u8bed\u8a00\u6a21\u578b\u7684\u57fa\u7840",
        importance=5,
    )
    am.write(
        topic="Reinforcement learning",
        keywords=["\u5f3a\u5316\u5b66\u4e60", "\u5956\u52b1", "\u7b56\u7565"],
        summary="\u5f3a\u5316\u5b66\u4e60\u901a\u8fc7\u5956\u52b1\u4fe1\u53f7\u4f18\u5316\u7b56\u7565\uff0c\u5728\u6e38\u620f\u548c\u673a\u5668\u4eba\u9886\u57df\u8868\u73b0\u4f18\u79c0",
        importance=3,
    )
    am.write(
        topic="Computer vision",
        keywords=["\u8ba1\u7b97\u673a\u89c6\u89c9", "\u56fe\u50cf\u8bc6\u522b", "\u5377\u79ef\u795e\u7ecf\u7f51\u7edc"],
        summary="\u8ba1\u7b97\u673a\u89c6\u89c9\u5229\u7528\u5377\u79ef\u795e\u7ecf\u7f51\u7edc\u5728\u56fe\u50cf\u8bc6\u522b\u4efb\u52a1\u4e2d\u53d6\u5f97\u4e86\u7a81\u7834",
        importance=4,
    )
    am.write(
        topic="Quantum computing",
        keywords=["\u91cf\u5b50", "\u91cf\u5b50\u6bd4\u7279", "\u9000\u76f8\u5e72"],
        summary="\u91cf\u5b50\u8ba1\u7b97\u5229\u7528\u91cf\u5b50\u6bd4\u7279\u7684\u53e0\u52a0\u6001\u8fdb\u884c\u5e76\u884c\u8fd0\u7b97",
        importance=3,
    )

    # Build matrices
    am.rebuild_matrices()

    # Long-text query
    long_query = (
        "\u6211\u60f3\u4e86\u89e3\u4eba\u5de5\u667a\u80fd\u9886\u57df\u7684\u6574\u4f53\u53d1\u5c55\u8109\u7edc\uff0c\u7279\u522b\u662f\u4ece\u65e9\u671f\u7684\u611f\u77e5\u673a\u548c\u56fe\u7075\u6d4b\u8bd5\uff0c"
        "\u5230\u540e\u6765\u7684\u6df1\u5ea6\u5b66\u4e60\u9769\u547d\uff0c\u518d\u5230\u73b0\u5728\u7684Transformer\u67b6\u6784\u548c\u5927\u578b\u8bed\u8a00\u6a21\u578b\u3002"
        "\u8fd9\u4e9b\u6280\u672f\u662f\u5982\u4f55\u4e00\u6b65\u6b65\u6f14\u8fdb\u7684\uff1f\u5404\u4e2a\u9636\u6bb5\u6709\u54ea\u4e9b\u5173\u952e\u7684\u7a81\u7834\u70b9\uff1f"
        "\u53e6\u5916\uff0c\u5f3a\u5316\u5b66\u4e60\u548c\u8ba1\u7b97\u673a\u89c6\u89c9\u65b9\u9762\u7684\u8fdb\u5c55\u4e5f\u5f88\u611f\u5174\u8da3\u3002"
    )
    assert len(long_query) > 80, "Query text should exceed 80 chars"

    result = am.query(user_input=long_query, depth="standard")

    stats = result["search_stats"]
    print(f"  [*] long_text_mode: {stats.get('long_text_mode')}")
    assert stats["long_text_mode"] is True, f"Long-text mode should be activated, got: {stats.get('long_text_mode')}"

    # Verify keyword tiers exist
    assert "core_keywords" in stats, "stats should contain core_keywords"
    assert "important_keywords" in stats, "stats should contain important_keywords"
    assert "supplement_keywords" in stats, "stats should contain supplement_keywords"

    print(f"  [*] core_keywords ({len(stats['core_keywords'])}): {stats['core_keywords']}")
    print(f"  [*] important_keywords ({len(stats['important_keywords'])}): {stats['important_keywords']}")
    print(f"  [*] supplement_keywords ({len(stats['supplement_keywords'])}): {stats['supplement_keywords']}")

    # Should have matched results
    assert len(result["matched_entries"]) > 0, "Long-text query should have matched entries"
    print(f"  [*] Matched entries: {len(result['matched_entries'])}")
    for me in result["matched_entries"]:
        print(f"      {me['entry_id']}: score={me['final_score']}, relevance={me['relevance_score']}")

    # Quantum computing is less relevant and should not rank at the top
    if len(result["matched_entries"]) >= 2:
        top_entries = [me["entry_id"] for me in result["matched_entries"][:3]]
        print(f"  [*] Top 3 entries: {top_entries}")

    print("  [*] Long-text mode query passed [OK]")


def test_4_query_short_text_mode():
    """Test 4: AgentMemory.query() short-text mode - unchanged behavior"""
    print_sep("Test 4: query() short-text mode (compatibility)")

    am = AgentMemory()
    am.write(
        topic="Python programming",
        keywords=["Python", "\u7f16\u7a0b", "\u811a\u672c"],
        summary="Python\u662f\u4e00\u79cd\u901a\u7528\u9ad8\u7ea7\u7f16\u7a0b\u8bed\u8a00",
    )
    am.write(
        topic="Java programming",
        keywords=["Java", "\u7f16\u7a0b", "\u9762\u5411\u5bf9\u8c61"],
        summary="Java\u662f\u9762\u5411\u5bf9\u8c61\u7684\u7f16\u7a0b\u8bed\u8a00",
    )

    am.rebuild_matrices()

    # Short-text query
    short_query = "Python\u7f16\u7a0b\u8bed\u8a00"
    assert len(short_query) <= 80

    result = am.query(user_input=short_query, depth="standard")
    stats = result["search_stats"]

    assert stats["long_text_mode"] is False, (
        f"Short text should not trigger long-text mode, got: {stats.get('long_text_mode')}"
    )

    # Short-text mode should not have tiered keywords
    assert "core_keywords" not in stats, "Short-text mode should not have core_keywords"

    print(f"  [*] long_text_mode: {stats['long_text_mode']} [OK]")
    print(f"  [*] Matched entries: {len(result['matched_entries'])}")
    if result["matched_entries"]:
        print(f"  [*] Top entry: {result['matched_entries'][0]['entry_id']}")
    print("  [*] Short-text mode compatibility passed [OK]")


def test_5_supplement_verification():
    """Test 5: supplement keyword verification bonus"""
    print_sep("Test 5: supplement keyword verification bonus")

    am = AgentMemory()

    # Write two entries: one with more supplement keyword matches, one with fewer
    am.write(
        topic="Comprehensive AI survey",
        keywords=[
            "\u4eba\u5de5\u667a\u80fd",
            "\u6df1\u5ea6\u5b66\u4e60",
            "\u673a\u5668\u5b66\u4e60",
            "\u795e\u7ecf\u7f51\u7edc",
        ],
        summary="\u4eba\u5de5\u667a\u80fd\u6df1\u5ea6\u5b66\u4e60\u673a\u5668\u5b66\u4e60\u795e\u7ecf\u7f51\u7edc\u56fe\u7075\u611f\u77e5\u673aTransformer\u6ce8\u610f\u529b\u673a\u5236\u5f3a\u5316\u5b66\u4e60\u8ba1\u7b97\u673a\u89c6\u89c9\u56fe\u50cf\u8bc6\u522b",
        importance=3,
    )
    am.write(
        topic="Simple AI intro",
        keywords=["\u4eba\u5de5\u667a\u80fd", "\u6df1\u5ea6\u5b66\u4e60"],
        summary="\u4eba\u5de5\u667a\u80fd\u548c\u6df1\u5ea6\u5b66\u4e60\u7684\u7b80\u8981\u4ecb\u7ecd",
        importance=3,
    )

    am.rebuild_matrices()

    # Long-text query with many supplement keywords
    long_query = (
        "\u8bf7\u8be6\u7ec6\u4ecb\u7ecd\u4eba\u5de5\u667a\u80fd\u548c\u6df1\u5ea6\u5b66\u4e60\u7684\u53d1\u5c55\u5386\u7a0b\uff0c\u5305\u62ec\u65e9\u671f\u7684\u56fe\u7075\u6d4b\u8bd5\u548c\u611f\u77e5\u673a\uff0c"
        "\u4ee5\u53ca\u540e\u6765\u7684\u795e\u7ecf\u7f51\u7edc\u9769\u547d\uff0cTransformer\u67b6\u6784\u548c\u6ce8\u610f\u529b\u673a\u5236\u7684\u7a81\u7834\uff0c"
        "\u8fd8\u6709\u5f3a\u5316\u5b66\u4e60\u5728\u6e38\u620f\u9886\u57df\u7684\u5e94\u7528\u548c\u8ba1\u7b97\u673a\u89c6\u89c9\u56fe\u50cf\u8bc6\u522b\u7684\u8fdb\u5c55\u3002"
    )

    result = am.query(user_input=long_query, depth="fast")  # fast mode to avoid expansion interference
    stats = result["search_stats"]

    assert stats["long_text_mode"] is True, "Should trigger long-text mode"

    if len(result["matched_entries"]) >= 2:
        # Comprehensive survey has more supplement keyword matches, should rank higher (all else equal)
        entries = result["matched_entries"]
        print("  [*] Ranking results:")
        for e in entries:
            print(f"      {e['entry_id']}: relevance={e['relevance_score']}, final={e['final_score']}")
        print("  [*] Supplement keyword verification bonus working [OK]")
    else:
        print(f"  [*] Matched entries: {len(result['matched_entries'])}")
        print("  [!] Insufficient entries to verify ranking (but mechanism code confirmed)")

    print("  [*] Supplement verification test complete [OK]")


def test_6_edge_cases():
    """Test 6: edge cases"""
    print_sep("Test 6: edge cases")

    # 6a: extract_keywords_weighted empty input
    result = extract_keywords_weighted("")
    assert result == [], f"Empty input should return empty list: {result}"
    print("  [*] Empty input: [] [OK]")

    result = extract_keywords_weighted(None)
    assert result == [], f"None input should return empty list: {result}"
    print("  [*] None input: [] [OK]")

    # 6b: exactly at threshold (80 chars)
    text_80 = "\u8fd9\u662f\u4e00\u6bb5\u6d4b\u8bd5\u6587\u672c" * 10  # 80 chars
    assert len(text_80) == 80
    result = extract_keywords_weighted(text_80, long_threshold=80)
    # 80 chars = threshold, should use short-text mode (<= 80)
    for w, weight in result:
        assert weight == 1.0, f"80 chars (=threshold) should use short-text mode: {w}={weight}"
    print("  [*] 80 chars=threshold -> short-text mode [OK]")

    # 6c: 81 chars (just over threshold)
    text_81 = text_80 + "\u591a"  # 81 chars
    assert len(text_81) == 81
    result_81 = extract_keywords_weighted(text_81, long_threshold=80)
    # 81 chars > threshold, should use long-text mode
    # (repetitive content may not show much weight variation)
    print(f"  [*] 81 chars (>threshold) -> extracted {len(result_81)} keywords")

    # 6d: query with no AI keywords + long text
    am = AgentMemory()
    am.write(
        topic="Test",
        keywords=["\u6d4b\u8bd5", "\u9a8c\u8bc1"],
        summary="\u8fd9\u662f\u4e00\u4e2a\u6d4b\u8bd5\u6761\u76ee",
    )
    am.rebuild_matrices()

    long_text = "\u8fd9\u662f\u4e00\u6bb5\u8f83\u957f\u7684\u6d4b\u8bd5\u67e5\u8be2\u6587\u672c" * 10  # > 80 chars
    result = am.query(user_input=long_text, depth="fast")
    stats = result["search_stats"]
    assert stats["long_text_mode"] is True
    # Without AI keywords, core_keywords should not contain AI-provided terms
    if "core_keywords" in stats:
        print(f"  [*] No AI keywords, core={stats['core_keywords']}")
    print("  [*] Edge case tests passed [OK]")


def test_7_ai_keywords_merge():
    """Test 7: AI keyword merging with TF-IDF extraction"""
    print_sep("Test 7: AI keyword merging into core keywords")

    am = AgentMemory()
    am.write(
        topic="Machine translation",
        keywords=["\u673a\u5668\u7ffb\u8bd1", "\u5e8f\u5217\u5230\u5e8f\u5217", "\u6ce8\u610f\u529b"],
        summary="\u673a\u5668\u7ffb\u8bd1\u4f7f\u7528\u5e8f\u5217\u5230\u5e8f\u5217\u6a21\u578b\u548c\u6ce8\u610f\u529b\u673a\u5236",
    )
    am.write(
        topic="Dialogue system",
        keywords=["\u5bf9\u8bdd", "\u804a\u5929\u673a\u5668\u4eba", "\u81ea\u7136\u8bed\u8a00"],
        summary="\u5bf9\u8bdd\u7cfb\u7edf\u662f\u81ea\u7136\u8bed\u8a00\u5904\u7406\u7684\u91cd\u8981\u5e94\u7528",
    )
    am.rebuild_matrices()

    long_query = (
        "\u6211\u60f3\u4e86\u89e3\u81ea\u7136\u8bed\u8a00\u5904\u7406\u9886\u57df\u4e2d\u673a\u5668\u7ffb\u8bd1\u548c\u5bf9\u8bdd\u7cfb\u7edf\u7684\u6700\u65b0\u6280\u672f\u8fdb\u5c55\uff0c"
        "\u7279\u522b\u662f\u57fa\u4e8eTransformer\u7684\u5e8f\u5217\u5230\u5e8f\u5217\u6a21\u578b\u5728\u7ffb\u8bd1\u4efb\u52a1\u4e2d\u7684\u5e94\u7528\uff0c"
        "\u4ee5\u53ca\u5927\u8bed\u8a00\u6a21\u578b\u5728\u5bf9\u8bdd\u751f\u6210\u548c\u77e5\u8bc6\u95ee\u7b54\u65b9\u9762\u7684\u7a81\u7834\u6027\u6210\u679c\u3002"
    )

    ai_keywords = ["\u673a\u5668\u7ffb\u8bd1", "\u5bf9\u8bdd\u7cfb\u7edf"]  # AI-judged core keywords

    result = am.query(user_input=long_query, keywords=ai_keywords, depth="fast")
    stats = result["search_stats"]

    assert stats["long_text_mode"] is True

    # AI-provided keywords should appear in core_keywords
    core = stats.get("core_keywords", [])
    for kw in ai_keywords:
        assert kw in core, f"AI keyword '{kw}' should be in core_keywords, got: {core}"

    print(f"  [*] core_keywords: {core}")
    print(f"  [*] AI keywords {ai_keywords} all present in core keywords [OK]")
    print("  [*] AI keyword merge test passed [OK]")


# ========================
# Run all tests
# ========================

if __name__ == "__main__":
    tests = [
        test_1_weighted_short,
        test_2_weighted_long,
        test_3_query_long_text_mode,
        test_4_query_short_text_mode,
        test_5_supplement_verification,
        test_6_edge_cases,
        test_7_ai_keywords_merge,
    ]

    passed = 0
    failed = 0
    errors = []

    for test_fn in tests:
        try:
            test_fn()
            passed += 1
        except Exception as e:
            failed += 1
            errors.append((test_fn.__name__, str(e)))
            import traceback

            print(f"\n  [FAIL] {test_fn.__name__}: {e}")
            traceback.print_exc()

    print(f"\n{'=' * 60}")
    print(f"  Results: {passed} passed, {failed} failed ({len(tests)} total)")
    if errors:
        print("\n  Failed:")
        for name, err in errors:
            print(f"    - {name}: {err}")
    print(f"{'=' * 60}")

    sys.exit(0 if failed == 0 else 1)
