"""GLM-5 输出的杂散 ``</arg_value>`` 标签不应影响工具调用解析。

GLM-5 偶尔会在 ``</tool_call>`` 之前多吐一个 ``</arg_value>``。解析器必须忽略
它，并产出与正常格式**完全一致**的结果。

（本文件原先是用 ``print`` + ``return bool`` 写的脚本，pytest 会忽略返回值导致
用例恒过；已改写为带真实断言的测试。）
"""

from opensquad.parser import ResponseParser

WITH_STRAY_TAG = """<tool_call>
  <func>websearch.search</func>
  <queries>["福州天气预报"]</queries>
  <max_results>5</max_results>
  <contains_chinese>true</contains_chinese>
</arg_value></tool_call>"""

WELL_FORMED = """<tool_call>
  <func>websearch.search</func>
  <queries>["福州天气预报"]</queries>
  <max_results>5</max_results>
  <contains_chinese>true</contains_chinese>
</tool_call>"""


def test_glm5_stray_arg_value_tag_is_ignored():
    result = ResponseParser.parse_tool_call(WITH_STRAY_TAG)

    assert result is not None, "杂散 </arg_value> 导致解析失败"
    tool_name, args = result
    assert tool_name == "websearch.search"
    assert args["queries"] == ["福州天气预报"]
    assert args["max_results"] == 5


def test_well_formed_tool_call_parses():
    result = ResponseParser.parse_tool_call(WELL_FORMED)

    assert result is not None, "正常格式解析失败"
    tool_name, args = result
    assert tool_name == "websearch.search"
    assert args["queries"] == ["福州天气预报"]
    assert args["max_results"] == 5


def test_stray_tag_does_not_change_parsed_args():
    """两种输入的解析结果必须逐字段一致。"""
    stray = ResponseParser.parse_tool_call(WITH_STRAY_TAG)
    clean = ResponseParser.parse_tool_call(WELL_FORMED)

    assert stray is not None and clean is not None
    assert stray[0] == clean[0]
    assert stray[1] == clean[1]
