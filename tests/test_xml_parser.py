"""
XML 参数解析器单元测试

测试 ResponseParser.parse_xml_arguments() 和 parse_tool_call() 函数
验证新的 <func> 格式和 ast.literal_eval() 参数解析
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from opensquad.parser import ResponseParser


def test_parse_xml_arguments_simple():
    """测试简单参数解析（字符串自动兜底）"""
    xml = '<to>"test@ai"</to><content>"hello"</content>'
    result = ResponseParser.parse_xml_arguments(xml)
    assert result == {"to": "test@ai", "content": "hello"}, (
        f"Expected {{'to': 'test@ai', 'content': 'hello'}}, got {result}"
    )
    print("✓ test_parse_xml_arguments_simple passed")


def test_parse_xml_arguments_multiline():
    """测试多行文本"""
    xml = "<content>line1\nline2\nline3</content>"
    result = ResponseParser.parse_xml_arguments(xml)
    # 没有引号时自动识别为字符串（保持原样）
    assert result == {"content": "line1\nline2\nline3"}, f"Expected multiline content, got {result}"
    print("✓ test_parse_xml_arguments_multiline passed")


def test_parse_xml_arguments_cdata():
    """测试 CDATA 处理"""
    xml = "<content><![CDATA[<html><body>test</body></html>]]></content>"
    result = ResponseParser.parse_xml_arguments(xml)
    assert result == {"content": "<html><body>test</body></html>"}, f"Expected CDATA content, got {result}"
    print("✓ test_parse_xml_arguments_cdata passed")


def test_parse_xml_arguments_empty():
    """测试空参数"""
    assert ResponseParser.parse_xml_arguments("") == {}
    assert ResponseParser.parse_xml_arguments("   ") == {}
    print("✓ test_parse_xml_arguments_empty passed")


def test_parse_xml_arguments_whitespace():
    """测试参数值前后空格处理"""
    xml = '<to>  "test@ai"  </to><content>  "hello world"  </content>'
    result = ResponseParser.parse_xml_arguments(xml)
    assert result == {"to": "test@ai", "content": "hello world"}, f"Expected trimmed values, got {result}"
    print("✓ test_parse_xml_arguments_whitespace passed")


def test_parse_param_value_string_with_quotes():
    """测试字符串参数（带引号）"""
    assert ResponseParser.parse_param_value('"hello"') == "hello"
    assert ResponseParser.parse_param_value('"福州天气"') == "福州天气"
    print("✓ test_parse_param_value_string_with_quotes passed")


def test_parse_param_value_string_without_quotes():
    """测试字符串参数（忘记引号，自动兜底）"""
    assert ResponseParser.parse_param_value("hello") == "hello"
    assert ResponseParser.parse_param_value("福州天气") == "福州天气"
    print("✓ test_parse_param_value_string_without_quotes passed")


def test_parse_param_value_numbers():
    """测试数字参数"""
    assert ResponseParser.parse_param_value("10") == 10
    assert ResponseParser.parse_param_value("3.14") == 3.14
    assert ResponseParser.parse_param_value("-5") == -5
    print("✓ test_parse_param_value_numbers passed")


def test_parse_param_value_booleans():
    """测试布尔值参数"""
    assert ResponseParser.parse_param_value("True") is True
    assert ResponseParser.parse_param_value("False") is False
    print("✓ test_parse_param_value_booleans passed")


def test_parse_param_value_lists():
    """测试列表参数"""
    assert ResponseParser.parse_param_value("[1, 2, 3]") == [1, 2, 3]
    assert ResponseParser.parse_param_value('["a", "b", "c"]') == ["a", "b", "c"]
    assert ResponseParser.parse_param_value('["新闻", "科技"]') == ["新闻", "科技"]
    print("✓ test_parse_param_value_lists passed")


def test_parse_tool_call_new_format():
    """测试新 <func> 格式解析"""
    text = """<tool_call>
  <func>im.send</func>
  <to>"user@ai"</to>
  <content>"hello"</content>
</tool_call>"""
    result = ResponseParser.parse_tool_call(text)
    assert result is not None, "Expected non-None result"
    name, args = result
    assert name == "im.send", f"Expected 'im.send', got {name}"
    assert args == {"to": "user@ai", "content": "hello"}, f"Expected dict args, got {args}"
    print("✓ test_parse_tool_call_new_format passed")


def test_parse_tool_call_new_format_no_params():
    """测试无参数 <func> 格式"""
    text = """<tool_call>
  <func>system.get_time</func>
</tool_call>"""
    result = ResponseParser.parse_tool_call(text)
    assert result is not None, "Expected non-None result"
    name, args = result
    assert name == "system.get_time", f"Expected 'system.get_time', got {name}"
    assert args == {}, f"Expected empty dict, got {args}"
    print("✓ test_parse_tool_call_new_format_no_params passed")


def test_parse_tool_call_with_numbers():
    """测试带数字参数的工具调用"""
    text = """<tool_call>
  <func>websearch.search</func>
  <query>"福州天气"</query>
  <max_results>10</max_results>
</tool_call>"""
    result = ResponseParser.parse_tool_call(text)
    assert result is not None, "Expected non-None result"
    name, args = result
    assert name == "websearch.search", f"Expected 'websearch.search', got {name}"
    assert args["query"] == "福州天气", f"Expected string query, got {args.get('query')}"
    assert args["max_results"] == 10, f"Expected int 10, got {args.get('max_results')}"
    print("✓ test_parse_tool_call_with_numbers passed")


def test_parse_tool_call_with_list():
    """测试带列表参数的工具调用"""
    text = """<tool_call>
  <func>websearch.search</func>
  <query>"福州天气"</query>
  <filters>["news", "blog"]</filters>
</tool_call>"""
    result = ResponseParser.parse_tool_call(text)
    assert result is not None, "Expected non-None result"
    name, args = result
    assert name == "websearch.search", f"Expected 'websearch.search', got {name}"
    assert args["filters"] == ["news", "blog"], f"Expected list, got {args.get('filters')}"
    print("✓ test_parse_tool_call_with_list passed")


def test_parse_tool_call_forgot_quotes():
    """测试忘记引号的情况（容错）"""
    text = """<tool_call>
  <func>im.send</func>
  <to>user@ai</to>
  <content>hello world</content>
</tool_call>"""
    result = ResponseParser.parse_tool_call(text)
    assert result is not None, "Expected non-None result"
    name, args = result
    assert name == "im.send", f"Expected 'im.send', got {name}"
    # 忘记引号时，系统会自动识别为字符串
    assert args["to"] == "user@ai", f"Expected 'user@ai', got {args.get('to')}"
    assert args["content"] == "hello world", f"Expected 'hello world', got {args.get('content')}"
    print("✓ test_parse_tool_call_forgot_quotes passed")


def test_parse_tool_call_with_newlines():
    """测试带换行符的工具调用"""
    text = """<tool_call>
  <func>filesystem.write</func>
  <path>"/tmp/test.txt"</path>
  <content>"Line 1
Line 2
Line 3"</content>
</tool_call>"""
    result = ResponseParser.parse_tool_call(text)
    assert result is not None, "Expected non-None result"
    name, args = result
    assert name == "filesystem.write", f"Expected 'filesystem.write', got {name}"
    assert args["path"] == "/tmp/test.txt", f"Expected path, got {args.get('path')}"
    assert "Line 1\nLine 2\nLine 3" in args["content"], f"Expected multiline content, got {args.get('content')}"
    print("✓ test_parse_tool_call_with_newlines passed")


def test_parse_tool_call_not_found():
    """测试找不到 tool_call 标签"""
    text = "This is a normal message without tool call"
    result = ResponseParser.parse_tool_call(text)
    assert result is None, f"Expected None, got {result}"
    print("✓ test_parse_tool_call_not_found passed")


def test_parse_tool_call_missing_func():
    """测试缺少 <func> 标签"""
    text = """<tool_call>
  <to>"user@ai"</to>
  <content>"hello"</content>
</tool_call>"""
    result = ResponseParser.parse_tool_call(text)
    # 缺少 <func> 应该返回 None
    assert result is None, f"Expected None when <func> is missing, got {result}"
    print("✓ test_parse_tool_call_missing_func passed")


def test_parse_xml_arguments_chinese():
    """测试中文参数"""
    xml = '<to>"张三@ai"</to><content>"你好世界"</content>'
    result = ResponseParser.parse_xml_arguments(xml)
    assert result == {"to": "张三@ai", "content": "你好世界"}, f"Expected Chinese content, got {result}"
    print("✓ test_parse_xml_arguments_chinese passed")


def test_streaming_parser_nested_tags():
    """测试流式解析器的嵌套标签保护机制"""
    from opensquad.xml_parser import StreamingTagParser

    # 测试场景：在 <thought> 内提到 <tool_call> 作为文本说明
    thought_content = []
    tool_call_content = []

    def thought_handler(content):
        # 流式标签会逐字符调用 handler
        thought_content.append(content)

    def tool_call_handler(content):
        tool_call_content.append(content)

    handlers = {
        "thought": thought_handler,
        "tool_call": tool_call_handler,
    }

    parser = StreamingTagParser(handlers=handlers)

    # 模拟流式输入
    text = "<thought>我打算用 <tool_call> 标签来调用工具</thought>"

    # 逐字符输入
    for char in text:
        parser.feed(char)

    parser.finish()

    # 拼接所有 thought 内容
    full_thought = "".join(thought_content)

    # 验证结果：<thought> 的内容应该包含 "<tool_call>" 作为普通文本
    assert len(thought_content) > 0, "Expected thought content, got empty"
    assert "<tool_call>" in full_thought, f"Expected '<tool_call>' in thought content, got: {full_thought}"

    # 验证没有触发 tool_call 处理器
    assert len(tool_call_content) == 0, (
        f"Expected 0 tool_calls (should not trigger inside thought), got {len(tool_call_content)}"
    )

    print("✓ test_streaming_parser_nested_tags passed")


def test_streaming_parser_nested_title_in_thought():
    """测试在 thought 中提到 title 标签"""
    from opensquad.xml_parser import StreamingTagParser

    thought_content = []
    title_content = []

    handlers = {
        "thought": lambda c: thought_content.append(c),
        "title": lambda c: title_content.append(c),
    }

    parser = StreamingTagParser(handlers=handlers)

    text = "<thought>用户可以用 <title> 标签来设置会话主题</thought>"

    for char in text:
        parser.feed(char)

    parser.finish()

    # 拼接所有 thought 内容
    full_thought = "".join(thought_content)

    # 验证 thought 包含完整内容
    assert len(thought_content) > 0, "Expected thought content, got empty"
    assert "<title>" in full_thought, f"Expected '<title>' in thought, got: {full_thought}"

    # 验证 title 处理器没有被触发
    assert len(title_content) == 0, f"Expected 0 titles, got {len(title_content)}"

    print("✓ test_streaming_parser_nested_title_in_thought passed")


def test_streaming_parser_actual_nested_tool_call():
    """测试实际的嵌套工具调用（在 thought 后面跟真正的 tool_call）"""
    from opensquad.xml_parser import StreamingTagParser

    thought_content = []
    tool_call_content = []

    handlers = {
        "thought": lambda c: thought_content.append(c),
        "tool_call": lambda c: tool_call_content.append(c),
    }

    parser = StreamingTagParser(handlers=handlers)

    # 先提到 tool_call，然后真的调用
    text = '<thought>我打算用 <tool_call> 来搜索</thought><tool_call><func>websearch.search</func><query>"test"</query></tool_call>'

    for char in text:
        parser.feed(char)

    parser.finish()

    # 拼接内容
    full_thought = "".join(thought_content)
    full_tool_call = "".join(tool_call_content)

    # 验证 thought 包含 <tool_call> 作为文本
    assert len(thought_content) > 0, "Expected thought content, got empty"
    assert "<tool_call>" in full_thought, f"Expected '<tool_call>' text in thought, got: {full_thought}"

    # 验证真正的 tool_call 被正确解析（tool_call 是提交型标签，应该一次性返回完整内容）
    assert len(tool_call_content) > 0, "Expected tool_call content, got empty"
    assert "<func>websearch.search</func>" in full_tool_call, f"Expected real tool_call content, got: {full_tool_call}"

    print("✓ test_streaming_parser_actual_nested_tool_call passed")


if __name__ == "__main__":
    print("开始运行 XML 解析器单元测试（新 <func> 格式）...\n")

    tests = [
        test_parse_xml_arguments_simple,
        test_parse_xml_arguments_multiline,
        test_parse_xml_arguments_cdata,
        test_parse_xml_arguments_empty,
        test_parse_xml_arguments_whitespace,
        test_parse_param_value_string_with_quotes,
        test_parse_param_value_string_without_quotes,
        test_parse_param_value_numbers,
        test_parse_param_value_booleans,
        test_parse_param_value_lists,
        test_parse_tool_call_new_format,
        test_parse_tool_call_new_format_no_params,
        test_parse_tool_call_with_numbers,
        test_parse_tool_call_with_list,
        test_parse_tool_call_forgot_quotes,
        test_parse_tool_call_with_newlines,
        test_parse_tool_call_not_found,
        test_parse_tool_call_missing_func,
        test_parse_xml_arguments_chinese,
        test_streaming_parser_nested_tags,
        test_streaming_parser_nested_title_in_thought,
        test_streaming_parser_actual_nested_tool_call,
    ]

    passed = 0
    failed = 0

    for test in tests:
        try:
            test()
            passed += 1
        except AssertionError as e:
            print(f"✗ {test.__name__} FAILED: {e}")
            failed += 1
        except Exception as e:
            print(f"✗ {test.__name__} ERROR: {e}")
            failed += 1

    print(f"\n{'=' * 60}")
    print(f"测试结果: {passed} 通过, {failed} 失败")
    print(f"{'=' * 60}")

    if failed > 0:
        sys.exit(1)
    else:
        print("\n所有测试通过！✓")
