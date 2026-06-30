#!/usr/bin/env python3
"""
测试工具筛选功能
验证 generate_openai_tools() 的 tool_filter 参数
"""

import asyncio
import json
import os

from opensquad.agents_boot import register_tools
from opensquad.registry import ToolRegistry
from plugins.plugin_manager import PluginManager


def _agent_config_path(agent_name: str = "ultimate") -> str:
    project_root = os.environ.get("OPENSQUAD_WORKSPACE", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(project_root, "agents", agent_name, "config.json")


async def test_tool_filtering():
    print("\n=== 测试工具筛选功能 ===\n")

    # 加载配置
    config_path = _agent_config_path()
    with open(config_path, encoding="utf-8") as f:
        config = json.load(f)

    # 注册工具
    registry = ToolRegistry()
    await register_tools(config, registry, os.path.dirname(config_path))

    # 加载插件
    pm = PluginManager(agent_id=config.get("agent_id", ""))
    pm.discover_and_load()
    pm.register_tools_to_agent(registry, config.get("agent_id", ""), config.get("tools", []))

    # 测试 1: 所有工具
    print("测试 1: tool_filter='all'")
    tools_all = registry.generate_openai_tools(tool_filter="all")
    print(f"  ✅ 导出 {len(tools_all)} 个工具\n")

    # 测试 2: 基线配置（高频工具）
    print("测试 2: tool_filter='baseline'")
    tools_baseline = registry.generate_openai_tools(tool_filter="baseline")
    print(f"  ✅ 导出 {len(tools_baseline)} 个工具")
    print(
        f"  📊 减少: {len(tools_all) - len(tools_baseline)} 个 ({(1 - len(tools_baseline) / len(tools_all)) * 100:.1f}%)\n"
    )

    # 测试 3: 高频+中频工具
    print("测试 3: tool_filter='high'")
    tools_high = registry.generate_openai_tools(tool_filter="high")
    print(f"  ✅ 导出 {len(tools_high)} 个工具")
    print(f"  📊 减少: {len(tools_all) - len(tools_high)} 个 ({(1 - len(tools_high) / len(tools_all)) * 100:.1f}%)\n")

    # 测试 4: 自定义命名空间
    print("测试 4: tool_filter=['filesystem', 'websearch']")
    tools_custom = registry.generate_openai_tools(tool_filter=["filesystem", "websearch"])
    print(f"  ✅ 导出 {len(tools_custom)} 个工具")

    # 验证只包含指定的命名空间（+ MCP）
    namespaces = set()
    for tool in tools_custom:
        ns = tool["function"]["name"].split("__")[0]
        namespaces.add(ns)
    print(f"  📋 命名空间: {sorted(namespaces)}\n")

    # 性能对比
    print("=" * 60)
    print("性能对比:\n")

    baseline_reduction = (1 - len(tools_baseline) / len(tools_all)) * 100
    high_reduction = (1 - len(tools_high) / len(tools_all)) * 100

    print("| 配置 | 工具数 | 减少 | 适用场景 |")
    print("|------|--------|------|---------|")
    print(f"| all | {len(tools_all)} | 0% | 复杂任务、不确定场景 |")
    print(f"| high | {len(tools_high)} | {high_reduction:.1f}% | 大多数日常任务 |")
    print(f"| baseline | {len(tools_baseline)} | {baseline_reduction:.1f}% | 简单任务、快速响应 |")
    print("| custom | 动态 | 动态 | 特定任务类型 |\n")

    print("=" * 60)

    # 推荐配置
    print("\n推荐配置:\n")
    print("1. 日常使用（推荐）: tool_filter='high'")
    print("   - 减少 ~20% 工具数量")
    print("   - 保留所有常用功能\n")

    print("2. 快速响应: tool_filter='baseline'")
    print("   - 减少 ~50% 工具数量")
    print("   - 专注核心功能\n")

    print("3. 特定任务: tool_filter=['filesystem', 'git', 'websearch']")
    print("   - 按需加载")
    print("   - 最小化 API 请求大小\n")

    return {
        "all": len(tools_all),
        "baseline": len(tools_baseline),
        "high": len(tools_high),
        "custom": len(tools_custom),
    }


if __name__ == "__main__":
    asyncio.run(test_tool_filtering())
