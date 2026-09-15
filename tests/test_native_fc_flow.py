"""Native Function Calling 完整流程：
1. 策略是否正确在 system prompt 中声明 Native FC；
2. tools 定义是否从工具签名正确生成；
3. 流式响应中的 tool_calls 是否被正确拼装。

（本文件原先是脚本式测试：用例末尾 ``return True`` / 失败分支 ``return False``，
pytest 忽略返回值，因此失败分支失效；``main()`` 里那套统计也从未被 CI 执行。
现已改为纯断言，并删除 print 噪音。）
"""

from types import SimpleNamespace

from opensquad.registry import ToolRegistry
from opensquad.tool_call_strategy import NativeToolCallStrategy


def _delta(tool_calls):
    """构造一个只携带 tool_calls 的流式 chunk。"""
    return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(tool_calls=tool_calls), finish_reason=None)])


def test_prompt_cleanup():
    """系统提示被注入 Native FC 说明，且不破坏其余段落。"""
    registry = ToolRegistry()
    strategy = NativeToolCallStrategy(registry)

    test_prompt = """你是一个助手

## 2. Tool Call Format

### 2.1 Tool Call Format
Use XML tags like this:
<function_calls>
<invoke>
...
</invoke>
</function_calls>

## 3. Tools & Skills

Available tools:
- tool1
- tool2

## 4. Other Instructions
Keep doing great work!
"""

    result = strategy.prepare_llm_call(system_prompt=test_prompt)
    cleaned_prompt = result["system_prompt"]

    assert "Native Function Calling" in cleaned_prompt, "Native FC 说明未添加"
    assert "DO NOT" in cleaned_prompt and "XML format" in cleaned_prompt, "禁止使用 XML 的警告未添加"
    assert "## 4. Other Instructions" in cleaned_prompt, "其他指令被错误移除"


def test_tool_definitions():
    """工具定义从签名 + docstring 生成，含参数名与描述。"""
    registry = ToolRegistry()

    class TestTools:
        @staticmethod
        def test_search(query: str, limit: int = 10):
            """搜索网络内容

            Args:
                query: 搜索关键词
                limit: 返回结果数量，默认 10

            Returns:
                搜索结果列表
            """
            return f"Found {limit} results for: {query}"

    registry.register(TestTools, "test_tools", level="core")
    strategy = NativeToolCallStrategy(registry)

    result = strategy.prepare_llm_call(system_prompt="You are a helpful assistant")
    tools = result.get("tools", [])

    test_tool = next((t for t in tools if t["function"]["name"] == "test_tools__test_search"), None)

    assert test_tool is not None, f"未找到 test_tools__test_search，实际生成: {[t['function']['name'] for t in tools]}"

    properties = test_tool["function"]["parameters"]["properties"]
    assert "query" in properties, "缺少 query 参数"
    assert "limit" in properties, "缺少 limit 参数"
    assert "搜索关键词" in properties["query"]["description"], "参数描述未提取"


def test_streaming_parse():
    """分片下发的 tool_calls 应拼装成完整的一次工具调用。"""
    registry = ToolRegistry()

    class TestTools:
        @staticmethod
        def test_tool(arg1: str):
            """测试工具"""
            return f"Result: {arg1}"

    registry.register(TestTools, "test_tools", level="core")
    strategy = NativeToolCallStrategy(registry)

    chunks = [
        SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(role="assistant"), finish_reason=None)]),
        SimpleNamespace(
            choices=[
                SimpleNamespace(
                    delta=SimpleNamespace(
                        tool_calls=[
                            SimpleNamespace(
                                index=0,
                                id="call_123",
                                type="function",
                                function=SimpleNamespace(name="test_tools__test_tool", arguments=""),
                            )
                        ]
                    ),
                    finish_reason=None,
                )
            ]
        ),
        SimpleNamespace(
            choices=[
                SimpleNamespace(
                    delta=SimpleNamespace(
                        tool_calls=[SimpleNamespace(index=0, function=SimpleNamespace(arguments='{"arg1":'))]
                    ),
                    finish_reason=None,
                )
            ]
        ),
        SimpleNamespace(
            choices=[
                SimpleNamespace(
                    delta=SimpleNamespace(
                        tool_calls=[SimpleNamespace(index=0, function=SimpleNamespace(arguments=' "test_value"}'))]
                    ),
                    finish_reason=None,
                )
            ]
        ),
        SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(), finish_reason="tool_calls")]),
    ]

    parsed_data = None
    for chunk in chunks:
        result = strategy.parse_response(chunk)
        if result:
            parsed_data = result

    assert parsed_data is not None, "未解析到工具调用"

    tool_name, args_dict = parsed_data[0]
    assert tool_name == "test_tools__test_tool", f"工具名称错误: {tool_name}"
    assert args_dict == {"arg1": "test_value"}, f"参数错误: {args_dict}"


def test_parallel_tool_calls_out_of_order():
    """并行工具调用乱序下发时不应越界崩溃。

    回归用例：provider 先推 index=1 再推 index=0，旧实现只 append 一个槽位，
    随后 buffer[index] 抛 IndexError，被上层 except 吞掉后整条流中断。
    """
    registry = ToolRegistry()

    class TestTools:
        @staticmethod
        def alpha(x: str):
            """工具 A"""
            return x

        @staticmethod
        def beta(y: str):
            """工具 B"""
            return y

    registry.register(TestTools, "test_tools", level="core")
    strategy = NativeToolCallStrategy(registry)

    # 关键：index=1 先于 index=0 到达
    chunks = [
        _delta(
            [
                SimpleNamespace(
                    index=1,
                    id="call_b",
                    type="function",
                    function=SimpleNamespace(name="test_tools__beta", arguments=""),
                )
            ]
        ),
        _delta(
            [
                SimpleNamespace(
                    index=0,
                    id="call_a",
                    type="function",
                    function=SimpleNamespace(name="test_tools__alpha", arguments=""),
                )
            ]
        ),
        _delta([SimpleNamespace(index=1, function=SimpleNamespace(arguments='{"y": "two"}'))]),
        _delta([SimpleNamespace(index=0, function=SimpleNamespace(arguments='{"x": "one"}'))]),
        SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(), finish_reason="tool_calls")]),
    ]

    parsed = None
    for chunk in chunks:
        result = strategy.parse_response(chunk)
        if result:
            parsed = result

    assert parsed is not None, "乱序并行工具调用未被解析"
    assert parsed == [
        ("test_tools__alpha", {"x": "one"}),
        ("test_tools__beta", {"y": "two"}),
    ], f"乱序并行工具调用解析结果错误: {parsed}"


def test_missing_tool_call_index_accumulates():
    """代理省略 index 时，增量分片应累积到同一槽位而不是各自新建。"""
    registry = ToolRegistry()

    class TestTools:
        @staticmethod
        def gamma(z: str):
            """工具 C"""
            return z

    registry.register(TestTools, "test_tools", level="core")
    strategy = NativeToolCallStrategy(registry)

    # 注意：这些 delta 完全没有 index 字段
    chunks = [
        _delta(
            [
                SimpleNamespace(
                    id="call_c",
                    type="function",
                    function=SimpleNamespace(name="test_tools__gamma", arguments=""),
                )
            ]
        ),
        _delta([SimpleNamespace(function=SimpleNamespace(arguments='{"z":'))]),
        _delta([SimpleNamespace(function=SimpleNamespace(arguments=' "three"}'))]),
        SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(), finish_reason="tool_calls")]),
    ]

    parsed = None
    for chunk in chunks:
        result = strategy.parse_response(chunk)
        if result:
            parsed = result

    assert parsed == [("test_tools__gamma", {"z": "three"})], f"无 index 流解析错误: {parsed}"
