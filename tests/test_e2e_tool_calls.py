"""端到端解析：Agent 使用 ``<func>`` XML 格式调用工具。

（本文件原先是脚本式测试：用例末尾 ``return True`` / 失败分支 ``return False``，
pytest 忽略返回值，因此失败分支完全不生效——只有 assert 那几行是真的。
现已去掉 print 噪音与返回值，并把原来的 ``return False`` 分支改成断言。）
"""

from opensquad.parser import ResponseParser
from opensquad.xml_parser import StreamingTagParser


def test_websearch_tool_call():
    """``<func>`` 格式解析，且工具名统一小写归一化。"""
    agent_output = """<tool_call>
  <func>WebSearch.search</func>
  <query>"福州明天的天气"</query>
  <max_results>5</max_results>
</tool_call>"""

    result = ResponseParser.parse_tool_call(agent_output)

    assert result is not None, "无法识别工具调用"
    tool_name, args = result
    # 解析器把供应商原样写出的 "WebSearch.search" 归一化为小写。
    assert tool_name == "websearch.search", f"工具名错误: {tool_name}"
    assert args["query"] == "福州明天的天气", f"query 参数错误: {args['query']}"
    assert args["max_results"] == 5, f"max_results 参数错误: {args['max_results']}"
    assert isinstance(args["max_results"], int), "max_results 应该是整数类型"


def test_im_send_tool_call():
    """容错：字符串参数忘记加引号时仍应识别为字符串。"""
    agent_output = """<tool_call>
  <func>im.send</func>
  <to>user@ai.com</to>
  <content>您好！我是 AI 助手</content>
</tool_call>"""

    result = ResponseParser.parse_tool_call(agent_output)

    assert result is not None, "无法识别工具调用"
    tool_name, args = result
    assert tool_name == "im.send", f"工具名错误: {tool_name}"
    assert args["to"] == "user@ai.com", f"to 参数错误: {args['to']}"
    assert args["content"] == "您好！我是 AI 助手", f"content 参数错误: {args['content']}"


def test_list_parameter():
    """列表与整数参数的类型还原。"""
    agent_output = """<tool_call>
  <func>data.filter</func>
  <tags>["新闻", "科技", "AI"]</tags>
  <limit>10</limit>
</tool_call>"""

    result = ResponseParser.parse_tool_call(agent_output)

    assert result is not None, "无法识别工具调用"
    tool_name, args = result
    assert tool_name == "data.filter", f"工具名错误: {tool_name}"
    assert args["tags"] == ["新闻", "科技", "AI"], f"tags 参数错误: {args['tags']}"
    assert isinstance(args["tags"], list), "tags 应该是列表类型"
    assert args["limit"] == 10, f"limit 参数错误: {args['limit']}"


def test_missing_func_tag():
    """缺少 ``<func>`` 标签时必须拒绝解析，而不是猜一个工具名。"""
    agent_output = """<tool_call>
  <query>"福州天气"</query>
  <max_results>5</max_results>
</tool_call>"""

    result = ResponseParser.parse_tool_call(agent_output)

    assert result is None, f"缺少 <func> 时应返回 None，实际返回 {result}"


def test_thought_with_title():
    """``<thought>`` 正文里提到的 ``<title>`` 必须当作普通文本，不能触发 handler。"""
    thought_content: list[str] = []
    title_content: list[str] = []

    handlers = {
        "thought": lambda c: thought_content.append(c),
        "title": lambda c: title_content.append(c),
    }

    parser = StreamingTagParser(handlers=handlers)

    text = "<thought>我建议用户使用 <title> 标签来设置会话主题</thought>"

    for char in text:
        parser.feed(char)
    parser.finish()

    full_thought = "".join(thought_content)

    assert "<title>" in full_thought, f"内层标签未作为文本保留: {full_thought!r}"
    assert title_content == [], f"内层 <title> 不应触发 handler，实际收到: {title_content!r}"
