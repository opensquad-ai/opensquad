"""测试 GLM-5 输出字面 \\n 的问题"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from opensquad.parser import ResponseParser


def test_glm5_literal_newline():
    """测试 GLM-5 输出字面 \\n 的情况"""
    # GLM-5 实际输出（带字面的 \n）
    text = """<tool_call>
  <func>websearch.search</func>
  <queries>["福州天气"]</queries>\\n<max_results>5</max_results>\\n<contains_chinese>True</contains_chinese>
</tool_call>"""

    print("=" * 60)
    print("GLM-5 实际输出（带字面 \\n）:")
    print(text)
    print()

    result = ResponseParser.parse_tool_call(text)

    if result is None:
        print("❌ 解析失败：返回 None")
        return False

    tool_name, args = result

    print("解析结果:")
    print(f"  工具名: {tool_name}")
    print("  参数:")
    for key, value in args.items():
        print(f"    {key}: {value!r} (类型: {type(value).__name__})")
    print()

    # 验证所有参数都被正确提取
    if "queries" not in args:
        print("❌ 缺少 queries 参数")
        return False

    if "max_results" not in args:
        print("❌ 缺少 max_results 参数")
        return False

    if "contains_chinese" not in args:
        print("❌ 缺少 contains_chinese 参数")
        return False

    # 验证参数类型
    if not isinstance(args["queries"], list):
        print(f"❌ queries 应该是 list，但得到 {type(args['queries'])}")
        return False

    if not isinstance(args["max_results"], int):
        print(f"❌ max_results 应该是 int，但得到 {type(args['max_results'])}")
        return False

    print("✅ 测试通过！所有参数都正确提取")
    return True


def test_glm5_multiple_escape_chars():
    """测试多种转义字符"""
    text = """<tool_call>
  <func>filesystem.write</func>
  <path>"/tmp/test.txt"</path>\\n<content>"Line 1\\nLine 2\\tTabbed\\rCarriage"</content>
</tool_call>"""

    print("=" * 60)
    print("测试多种转义字符:")
    print(text)
    print()

    result = ResponseParser.parse_tool_call(text)

    if result is None:
        print("❌ 解析失败：返回 None")
        return False

    tool_name, args = result

    print("解析结果:")
    print(f"  工具名: {tool_name}")
    print("  参数:")
    for key, value in args.items():
        print(f"    {key}: {value!r}")
    print()

    # 验证 content 中的转义字符被正确处理
    if "content" not in args:
        print("❌ 缺少 content 参数")
        return False

    content = args["content"]

    # 内容中的 \n \t \r 应该被转换为真正的换行、制表、回车符
    if "\\n" in content or "\\t" in content or "\\r" in content:
        print(f"❌ content 中仍有未转换的转义字符: {content!r}")
        return False

    print("✅ 测试通过！转义字符正确处理")
    return True


def test_normal_format_still_works():
    """确保正常格式不受影响"""
    text = """<tool_call>
  <func>websearch.search</func>
  <queries>["福州天气"]</queries>
  <max_results>5</max_results>
  <contains_chinese>True</contains_chinese>
</tool_call>"""

    print("=" * 60)
    print("测试正常格式（对比）:")
    print(text)
    print()

    result = ResponseParser.parse_tool_call(text)

    if result is None:
        print("❌ 解析失败：返回 None")
        return False

    tool_name, args = result

    print("解析结果:")
    print(f"  工具名: {tool_name}")
    print("  参数:")
    for key, value in args.items():
        print(f"    {key}: {value!r} (类型: {type(value).__name__})")
    print()

    if len(args) != 3:
        print(f"❌ 应该有 3 个参数，但得到 {len(args)} 个")
        return False

    print("✅ 正常格式仍然工作正常")
    return True


def test_combined_issues():
    """测试 GLM-5 的多个问题组合（\\n + </arg_value>）"""
    text = """<tool_call>
  <func>websearch.search</func>
  <queries>["福州天气预报"]</queries>\\n<max_results>5</max_results>\\n<contains_chinese>true</contains_chinese>
</arg_value></tool_call>"""

    print("=" * 60)
    print("测试组合问题（\\n + </arg_value>）:")
    print(text)
    print()

    result = ResponseParser.parse_tool_call(text)

    if result is None:
        print("❌ 解析失败：返回 None")
        return False

    tool_name, args = result

    print("解析结果:")
    print(f"  工具名: {tool_name}")
    print("  参数:")
    for key, value in args.items():
        print(f"    {key}: {value!r} (类型: {type(value).__name__})")
    print()

    if len(args) != 3:
        print(f"❌ 应该有 3 个参数，但得到 {len(args)} 个: {list(args.keys())}")
        return False

    print("✅ 组合问题正确处理！")
    return True


if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("测试 GLM-5 字面 \\n 问题的补丁")
    print("=" * 60)
    print()

    tests = [
        ("GLM-5 字面 \\n", test_glm5_literal_newline),
        ("多种转义字符", test_glm5_multiple_escape_chars),
        ("正常格式对比", test_normal_format_still_works),
        ("组合问题", test_combined_issues),
    ]

    passed = 0
    failed = 0

    for name, test_func in tests:
        try:
            if test_func():
                passed += 1
            else:
                failed += 1
                print(f"\n⚠️  {name} 失败")
        except Exception as e:
            print(f"\n❌ {name} 异常: {e}")
            import traceback

            traceback.print_exc()
            failed += 1

    print("\n" + "=" * 60)
    print(f"测试结果: {passed} 通过, {failed} 失败")
    print("=" * 60)

    if failed > 0:
        sys.exit(1)
    else:
        print("\n🎉 所有测试通过！GLM-5 补丁工作正常")
