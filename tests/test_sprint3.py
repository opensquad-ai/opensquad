"""Native Function Calling 模式的基础行为测试。

本文件原先是以 ``print`` 堆出来的演示脚本：用例返回 ``list`` / ``str`` 而不是
``None``，pytest 只发一条 ``PytestReturnNotNoneWarning`` 就判定通过——没有任何
断言，等于永远绿。现已改写为带真实断言的测试；纯打印的 token 估算对比与
"真实场景准备"两段已删除（它们不验证任何行为）。
"""

from opensquad.registry import ToolRegistry
from opensquad.tool_call_strategy import ToolCallStrategySelector
from opensquad.tools import filesystem, system


def _registry() -> ToolRegistry:
    """注册 core 级工具集，作为策略选择与 schema 生成的真实输入。"""
    registry = ToolRegistry()
    registry.register(filesystem, "filesystem", level="core")
    registry.register(system, "system", level="core")
    return registry


def test_tools_schema_generation_produces_well_formed_openai_tools():
    tools = _registry().generate_openai_tools()

    assert tools, "未生成任何工具定义"

    names = [tool["function"]["name"] for tool in tools]
    assert len(names) == len(set(names)), f"工具名重复: {sorted(names)}"

    for tool in tools:
        assert tool["type"] == "function"
        func = tool["function"]
        assert isinstance(func["name"], str) and func["name"]
        assert isinstance(func["parameters"], dict)
        assert "properties" in func["parameters"], f"{func['name']} 缺少 parameters.properties"

    assert any(name.startswith("filesystem__") for name in names)
    assert any(name.startswith("system__") for name in names)


def test_tool_descriptions_render_as_non_empty_text():
    """XML 模式依赖这段纯文本描述，为空则模型看不到任何工具。"""
    descriptions = _registry().generate_tool_descriptions()

    assert isinstance(descriptions, str)
    assert descriptions.strip(), "XML 模式的工具描述为空"


def test_selector_picks_native_fc_for_capable_model():
    strategy = ToolCallStrategySelector().select(
        {"model": {"provider": "DeepSeek", "model_name": "deepseek-chat"}},
        _registry(),
    )

    assert strategy.get_strategy_name() == "Native-FC"


def test_selector_falls_back_to_xml_for_unknown_model():
    """能力未知时必须退回 XML，不能乐观地发 tools 参数。"""
    strategy = ToolCallStrategySelector().select(
        {"model": {"provider": "UnknownVendor", "model_name": "some-unknown-model-xyz"}},
        _registry(),
    )

    assert strategy.get_strategy_name() == "XML"


def test_explicit_tool_call_mode_overrides_capability_detection():
    """显式配置的 tool_call_mode 优先于模型能力探测。"""
    strategy = ToolCallStrategySelector().select(
        {
            "model": {
                "provider": "DeepSeek",
                "model_name": "deepseek-chat",
                "tool_call_mode": "xml",
            }
        },
        _registry(),
    )

    assert strategy.get_strategy_name() == "XML"
