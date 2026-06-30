"""测试解析器是否能正确处理 GLM-5 输出的错误 </arg_value> 标签"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from opensquad.parser import ResponseParser


def test_glm5_arg_value_bug():
    """测试 GLM-5 多输出 </arg_value> 标签的情况"""
    # GLM-5 实际输出的格式（带错误的 </arg_value>）
    text = """<tool_call>
  <func>websearch.search</func>
  <queries>["福州天气预报"]</queries>
  <max_results>5</max_results>
  <contains_chinese>true</contains_chinese>
</arg_value></tool_call>"""

    print("GLM-5 实际输出（带 </arg_value> 错误）:")
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

    # 验证是否正确解析（忽略 </arg_value>）
    expected_tool = "websearch.search"

    if tool_name != expected_tool:
        print(f"\n❌ 工具名错误: 期望 {expected_tool}, 得到 {tool_name}")
        return False

    if "queries" not in args:
        print("\n❌ 缺少 queries 参数")
        return False

    if "max_results" not in args:
        print("\n❌ 缺少 max_results 参数")
        return False

    print("\n✅ 测试通过！解析器正确忽略了 </arg_value> 标签")
    return True


def test_correct_format():
    """测试正确格式（对比）"""
    text = """<tool_call>
  <func>websearch.search</func>
  <queries>["福州天气预报"]</queries>
  <max_results>5</max_results>
  <contains_chinese>true</contains_chinese>
</tool_call>"""

    print("\n" + "=" * 60)
    print("正确格式（对比）:")
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

    print("\n✅ 正确格式解析成功")
    return True


if __name__ == "__main__":
    print("=" * 60)
    print("测试 GLM-5 的 </arg_value> 问题")
    print("=" * 60)
    print()

    test1 = test_glm5_arg_value_bug()
    test2 = test_correct_format()

    print("\n" + "=" * 60)
    if test1 and test2:
        print("✅ 所有测试通过")
    else:
        print("❌ 部分测试失败")
        sys.exit(1)
