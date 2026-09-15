"""GLM-5 在 XML 参数分隔位置输出**字面** ``\\n`` 时的解析行为。

GLM-5 偶尔会把换行写成两个字符 ``\\`` + ``n``，而不是真正的换行符。这会让
``<queries>`` 与 ``<max_results>`` 落进同一"行"，解析器必须仍能把它们拆成
独立参数；参数**值内部**的字面 ``\\n`` / ``\\t`` / ``\\r`` 则必须还原成真正的
控制字符。

（本文件原先是用 ``print`` + ``return bool`` 写的脚本，pytest 忽略返回值导致
4 个用例恒过；已改写为带真实断言的测试。）
"""

from opensquad.parser import ResponseParser

LITERAL_NEWLINE_BETWEEN_ARGS = """<tool_call>
  <func>websearch.search</func>
  <queries>["福州天气"]</queries>\\n<max_results>5</max_results>\\n<contains_chinese>True</contains_chinese>
</tool_call>"""

LITERAL_ESCAPES_INSIDE_VALUE = """<tool_call>
  <func>filesystem.write</func>
  <path>"/tmp/test.txt"</path>\\n<content>"Line 1\\nLine 2\\tTabbed\\rCarriage"</content>
</tool_call>"""

WELL_FORMED = """<tool_call>
  <func>websearch.search</func>
  <queries>["福州天气"]</queries>
  <max_results>5</max_results>
  <contains_chinese>True</contains_chinese>
</tool_call>"""

COMBINED = """<tool_call>
  <func>websearch.search</func>
  <queries>["福州天气预报"]</queries>\\n<max_results>5</max_results>\\n<contains_chinese>true</contains_chinese>
</arg_value></tool_call>"""


def test_literal_newline_between_args_still_splits_parameters():
    result = ResponseParser.parse_tool_call(LITERAL_NEWLINE_BETWEEN_ARGS)

    assert result is not None, "字面 \\n 导致解析失败"
    tool_name, args = result
    assert tool_name == "websearch.search"
    assert len(args) == 3, f"参数未正确拆分: {list(args)}"
    assert args["queries"] == ["福州天气"]
    assert args["max_results"] == 5
    assert args["contains_chinese"] is True


def test_literal_escapes_inside_value_are_restored():
    result = ResponseParser.parse_tool_call(LITERAL_ESCAPES_INSIDE_VALUE)

    assert result is not None
    tool_name, args = result
    assert tool_name == "filesystem.write"
    assert args["path"] == "/tmp/test.txt"
    assert args["content"] == "Line 1\nLine 2\tTabbed\rCarriage"


def test_well_formed_input_is_unaffected():
    result = ResponseParser.parse_tool_call(WELL_FORMED)

    assert result is not None
    tool_name, args = result
    assert tool_name == "websearch.search"
    assert len(args) == 3, f"正常格式参数数量异常: {list(args)}"
    assert args["queries"] == ["福州天气"]
    assert args["max_results"] == 5


def test_combined_literal_newline_and_stray_arg_value_tag():
    """字面 ``\\n`` 与杂散 ``</arg_value>`` 同时出现。"""
    result = ResponseParser.parse_tool_call(COMBINED)

    assert result is not None
    tool_name, args = result
    assert tool_name == "websearch.search"
    assert len(args) == 3, f"参数未正确拆分: {list(args)}"
    assert args["queries"] == ["福州天气预报"]
    assert args["max_results"] == 5
