#!/usr/bin/env python
"""
Sprint 3 测试脚本：验证 Native Function Calling 模式

测试内容：
1. 配置加载
2. 策略选择
3. 工具 Schema 生成
4. Token 使用量对比
"""

import json
import os
import sys

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from opensquad.registry import ToolRegistry
from opensquad.tool_call_strategy import ToolCallStrategySelector
from opensquad.tools import filesystem, system


def load_agent_config(agent_name: str):
    """加载 Agent 配置"""
    import pytest

    project_root = os.environ.get("OPENSQUAD_WORKSPACE", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    config_path = os.path.join(project_root, "agents", agent_name, "config.json")
    if not os.path.isfile(config_path):
        pytest.skip(f"agent config not in this checkout: {config_path}")
    with open(config_path, encoding="utf-8") as f:
        return json.load(f)


def test_config_loading():
    """测试 1: 配置加载"""
    print("=" * 60)
    print("测试 1: 配置加载")
    print("=" * 60)

    config = load_agent_config("ultimate")
    model_config = config.get("model", {})

    print("✓ 配置文件加载成功")
    print(f"  - Provider: {model_config.get('provider')}")
    print(f"  - Model: {model_config.get('model_name')}")
    print(f"  - Tool Call Mode: {model_config.get('tool_call_mode', 'auto')}")

    return model_config


def test_strategy_selection():
    """测试 2: 策略选择"""
    print("\n" + "=" * 60)
    print("测试 2: 策略选择")
    print("=" * 60)

    model_config = load_agent_config("ultimate").get("model", {})
    config = {"model": model_config}

    # 创建真实的 registry
    registry = ToolRegistry()
    registry.register(filesystem, "filesystem", level="core")

    selector = ToolCallStrategySelector()
    strategy = selector.select(config, registry)

    print("✓ 策略选择完成")
    print(f"  - 策略名称: {strategy.get_strategy_name()}")
    print(f"  - 配置的模式: {model_config.get('tool_call_mode', 'auto')}")
    print(
        f"  - 模型支持 Function Calling: {selector._supports_function_calling(model_config.get('provider'), model_config.get('model_name'))}"
    )

    return strategy


def test_tools_schema_generation():
    """测试 3: 工具 Schema 生成"""
    print("\n" + "=" * 60)
    print("测试 3: 工具 Schema 生成")
    print("=" * 60)

    registry = ToolRegistry()
    registry.register(filesystem, "filesystem", level="core")
    registry.register(system, "system", level="core")

    tools = registry.generate_openai_tools()

    print(f"✓ 生成了 {len(tools)} 个工具定义")

    # 显示前 3 个工具的摘要
    print("\n前 3 个工具示例:")
    for i, tool in enumerate(tools[:3]):
        func = tool["function"]
        param_count = len(func["parameters"]["properties"])
        required_count = len(func["parameters"].get("required", []))
        print(f"  {i + 1}. {func['name']}")
        print(f"     描述: {func['description'][:50]}...")
        print(f"     参数: {param_count} 个 ({required_count} 个必需)")

    return tools


def test_token_comparison():
    """测试 4: Token 使用量对比"""
    print("\n" + "=" * 60)
    print("测试 4: Token 使用量对比（估算）")
    print("=" * 60)

    registry = ToolRegistry()
    registry.register(filesystem, "filesystem", level="core")
    registry.register(system, "system", level="core")
    tools = registry.generate_openai_tools()

    # XML 模式：生成文本描述
    xml_descriptions = registry.generate_tool_descriptions()
    xml_token_estimate = len(xml_descriptions) // 4  # 粗略估算：4 字符 ≈ 1 token

    # Native FC 模式：生成 JSON Schema
    import json

    native_schema_text = json.dumps(tools, ensure_ascii=False)
    native_token_estimate = len(native_schema_text) // 4

    print("✓ Token 使用量估算（仅工具描述部分）：")
    print(f"  - XML 模式（文本描述）: ~{xml_token_estimate} tokens")
    print(f"    文本长度: {len(xml_descriptions)} 字符")
    print(f"  - Native FC 模式（JSON Schema）: ~{native_token_estimate} tokens")
    print(f"    文本长度: {len(native_schema_text)} 字符")
    print(
        f"  - 节省: ~{xml_token_estimate - native_token_estimate} tokens ({(1 - native_token_estimate / xml_token_estimate) * 100:.1f}%)"
    )

    print("\n注意：")
    print("  - XML 模式：工具描述在 System Prompt 中，每次请求都发送")
    print("  - Native FC 模式：工具 Schema 通过 'tools' 参数发送，不占用 System Prompt")
    print("  - 实际节省取决于 System Prompt 的其他内容长度")


def test_real_scenario_comparison():
    """测试 5: 真实场景对比（准备测试数据）"""
    print("\n" + "=" * 60)
    print("测试 5: 真实场景准备")
    print("=" * 60)

    print("✓ 准备测试查询：")
    test_queries = [
        "读取文件 opensquad/registry.py 的前 50 行",
        "在当前目录搜索包含 'tool_call_strategy' 的 Python 文件",
        "列出当前目录的所有文件",
    ]

    for i, query in enumerate(test_queries, 1):
        print(f"  {i}. {query}")

    print("\n说明：")
    print("  这些查询将用于对比 XML 模式和 Native FC 模式的工具调用效果")
    print("  需要实际运行 Agent 来完成测试")

    return test_queries


def main():
    """主测试流程"""
    print("\n" + "=" * 60)
    print("Sprint 3 测试：Native Function Calling 模式验证")
    print("=" * 60)

    try:
        # 测试 1: 配置加载
        model_config = test_config_loading()

        # 测试 2: 策略选择
        test_strategy_selection(model_config)

        # 测试 3: 工具 Schema 生成
        tools = test_tools_schema_generation()

        # 测试 4: Token 使用量对比
        test_token_comparison(tools)

        # 测试 5: 真实场景准备
        test_real_scenario_comparison()

        print("\n" + "=" * 60)
        print("✅ 所有测试完成")
        print("=" * 60)
        print("\n下一步：")
        print("  1. 启动 ultimate agent: python agents_boot.py ultimate")
        print("  2. 查看日志确认使用 Native-FC 策略")
        print("  3. 测试上述查询并观察工具调用成功率")

    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
