"""
端到端测试：模拟 Agent 使用新 <func> 格式调用工具
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from opensquad.parser import ResponseParser


def test_websearch_tool_call():
    """测试 WebSearch 工具调用（新格式）"""
    print("=" * 60)
    print("测试 1: WebSearch 工具调用（新 <func> 格式）")
    print("=" * 60)

    # 模拟 Agent 输出
    agent_output = """<tool_call>
  <func>WebSearch.search</func>
  <query>"福州明天的天气"</query>
  <max_results>5</max_results>
</tool_call>"""

    print(f"\n模拟 Agent 输出:\n{agent_output}\n")

    # 解析工具调用
    result = ResponseParser.parse_tool_call(agent_output)

    if result is None:
        print("❌ 解析失败：无法识别工具调用")
        return False

    tool_name, args = result

    print("解析结果:")
    print(f"  工具名: {tool_name}")
    print("  参数:")
    for key, value in args.items():
        print(f"    {key}: {value!r} (类型: {type(value).__name__})")

    # 验证结果
    assert tool_name == "WebSearch.search", f"工具名错误: {tool_name}"
    assert args["query"] == "福州明天的天气", f"query 参数错误: {args['query']}"
    assert args["max_results"] == 5, f"max_results 参数错误: {args['max_results']}"
    assert isinstance(args["max_results"], int), "max_results 应该是整数类型"

    print("\n✅ 测试通过！")
    return True


def test_im_send_tool_call():
    """测试 IM 发送工具调用（新格式，容错：忘记引号）"""
    print("\n" + "=" * 60)
    print("测试 2: IM 发送工具调用（容错：忘记引号）")
    print("=" * 60)

    # 模拟 Agent 输出（忘记给字符串加引号）
    agent_output = """<tool_call>
  <func>im.send</func>
  <to>user@ai.com</to>
  <content>您好！我是 AI 助手</content>
</tool_call>"""

    print(f"\n模拟 Agent 输出（忘记引号）:\n{agent_output}\n")

    # 解析工具调用
    result = ResponseParser.parse_tool_call(agent_output)

    if result is None:
        print("❌ 解析失败：无法识别工具调用")
        return False

    tool_name, args = result

    print("解析结果:")
    print(f"  工具名: {tool_name}")
    print("  参数:")
    for key, value in args.items():
        print(f"    {key}: {value!r}")

    # 验证结果：即使忘记引号，也应该被识别为字符串
    assert tool_name == "im.send", f"工具名错误: {tool_name}"
    assert args["to"] == "user@ai.com", f"to 参数错误: {args['to']}"
    assert args["content"] == "您好！我是 AI 助手", f"content 参数错误: {args['content']}"

    print("\n✅ 测试通过！（容错机制工作正常）")
    return True


def test_list_parameter():
    """测试带列表参数的工具调用"""
    print("\n" + "=" * 60)
    print("测试 3: 带列表参数的工具调用")
    print("=" * 60)

    # 模拟 Agent 输出
    agent_output = """<tool_call>
  <func>data.filter</func>
  <tags>["新闻", "科技", "AI"]</tags>
  <limit>10</limit>
</tool_call>"""

    print(f"\n模拟 Agent 输出:\n{agent_output}\n")

    # 解析工具调用
    result = ResponseParser.parse_tool_call(agent_output)

    if result is None:
        print("❌ 解析失败：无法识别工具调用")
        return False

    tool_name, args = result

    print("解析结果:")
    print(f"  工具名: {tool_name}")
    print("  参数:")
    for key, value in args.items():
        print(f"    {key}: {value!r} (类型: {type(value).__name__})")

    # 验证结果
    assert tool_name == "data.filter", f"工具名错误: {tool_name}"
    assert args["tags"] == ["新闻", "科技", "AI"], f"tags 参数错误: {args['tags']}"
    assert isinstance(args["tags"], list), "tags 应该是列表类型"
    assert args["limit"] == 10, f"limit 参数错误: {args['limit']}"

    print("\n✅ 测试通过！")
    return True


def test_missing_func_tag():
    """测试缺少 <func> 标签的情况"""
    print("\n" + "=" * 60)
    print("测试 4: 缺少 <func> 标签（应该返回 None）")
    print("=" * 60)

    # 模拟 Agent 输出（旧格式，缺少 <func>）
    agent_output = """<tool_call>
  <query>"福州天气"</query>
  <max_results>5</max_results>
</tool_call>"""

    print(f"\n模拟 Agent 输出（缺少 <func>）:\n{agent_output}\n")

    # 解析工具调用
    result = ResponseParser.parse_tool_call(agent_output)

    if result is None:
        print("✅ 正确处理：返回 None（缺少 <func> 标签）")
        return True
    else:
        print(f"❌ 错误：应该返回 None，但返回了 {result}")
        return False


def test_thought_with_title():
    """测试 <thought> 中提到 <title> 标签"""
    print("\n" + "=" * 60)
    print("测试 5: <thought> 中提到 <title> 标签（应作为普通文本）")
    print("=" * 60)

    from opensquad.xml_parser import StreamingTagParser

    thought_content = []
    title_content = []

    handlers = {
        "thought": lambda c: thought_content.append(c),
        "title": lambda c: title_content.append(c),
    }

    parser = StreamingTagParser(handlers=handlers)

    # 模拟 Agent 输出
    text = "<thought>我建议用户使用 <title> 标签来设置会话主题</thought>"

    print(f"\n模拟 Agent 输出:\n{text}\n")

    # 逐字符输入（模拟流式输出）
    for char in text:
        parser.feed(char)

    parser.finish()

    # 拼接内容
    full_thought = "".join(thought_content)

    print("解析结果:")
    print(f"  thought 内容: {full_thought!r}")
    print(f"  title 被触发次数: {len(title_content)}")

    # 验证：<title> 应该作为普通文本出现在 thought 中
    if "<title>" in full_thought and len(title_content) == 0:
        print("\n✅ 测试通过！嵌套标签保护机制工作正常")
        return True
    else:
        print("\n❌ 测试失败！")
        return False


if __name__ == "__main__":
    print("\n开始运行端到端测试（新 <func> 格式）...\n")

    tests = [
        test_websearch_tool_call,
        test_im_send_tool_call,
        test_list_parameter,
        test_missing_func_tag,
        test_thought_with_title,
    ]

    passed = 0
    failed = 0

    for test in tests:
        try:
            if test():
                passed += 1
            else:
                failed += 1
        except Exception as e:
            print(f"\n❌ {test.__name__} 异常: {e}")
            import traceback

            traceback.print_exc()
            failed += 1

    print("\n" + "=" * 60)
    print(f"测试结果: {passed} 通过, {failed} 失败")
    print("=" * 60)

    if failed > 0:
        sys.exit(1)
    else:
        print("\n🎉 所有端到端测试通过！")
