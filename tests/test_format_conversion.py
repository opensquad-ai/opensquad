#!/usr/bin/env python3
"""
测试工具名称格式转换
验证 registry.call() 支持：
1. namespace.function (XML 模式)
2. namespace__function (Native FC 模式)
3. bare function name → 自动翻译为 namespace__function
"""

import asyncio

import pytest

from opensquad.registry import ToolRegistry

pytestmark = pytest.mark.asyncio


class SampleTools:
    @staticmethod
    def sample_function(arg1: str, arg2: int = 10):
        """测试函数"""
        return f"Success: arg1={arg1}, arg2={arg2}"

    @staticmethod
    def memory_write(topic: str, summary: str, entry_type: str = "knowledge"):
        """假 memory_write，用于裸名翻译测试"""
        return f"Wrote:{topic}:{summary}:{entry_type}"


class OtherTools:
    @staticmethod
    def memory_write(topic: str, summary: str):
        return f"Other:{topic}"


async def test_format_conversion():
    registry = ToolRegistry()
    registry.register(SampleTools, "test_tools", level="core")

    result1 = await registry.call("test_tools.sample_function", {"arg1": "hello", "arg2": 20})
    assert "Success" in result1, f"❌ XML 格式失败: {result1}"

    result2 = await registry.call("test_tools__sample_function", {"arg1": "world", "arg2": 30})
    assert "Success" in result2, f"❌ Native FC 格式失败: {result2}"

    result3 = await registry.call("invalid_format", {"arg1": "test"})
    assert "Invalid format" in result3, f"❌ 应该返回错误: {result3}"

    result4 = await registry.call("mcp__some_tool", {"arg1": "test"})
    assert "Invalid format" not in result4, f"❌ MCP 格式不应报告格式错误: {result4}"


async def test_bare_tool_name_resolves_uniquely():
    registry = ToolRegistry()
    registry.register(SampleTools, "test_tools", level="core")

    resolved = registry.resolve_bare_tool_name("sample_function")
    assert resolved == "test_tools.sample_function"

    result = await registry.call(
        "sample_function",
        {"arg1": "bare", "arg2": 1},
    )
    assert "Success: arg1=bare" in result


async def test_bare_memory_write_prefers_long_memory_namespace():
    registry = ToolRegistry()
    registry.register(SampleTools, "long_memory", level="core")
    registry.register(OtherTools, "self_learn", level="extended")

    resolved = registry.resolve_bare_tool_name("memory_write")
    assert resolved == "long_memory.memory_write"

    result = await registry.call(
        "memory_write",
        {
            "topic": "weather.com.cn 抓取方式选择",
            "summary": "有反爬时优先用 playwright",
            "entry_type": "experience",
        },
    )
    assert result.startswith("Wrote:")
    assert "experience" in result


async def test_bare_memory_write_prefers_memory_alias_namespace():
    registry = ToolRegistry()
    registry.register(SampleTools, "memory", level="core")

    resolved = registry.resolve_bare_tool_name("memory_write")
    assert resolved == "memory.memory_write"

    result = await registry.call("memory_write", {"topic": "t", "summary": "s"})
    assert result.startswith("Wrote:")


async def test_ambiguous_bare_name_without_priority_errors():
    registry = ToolRegistry()

    class PluginA:
        @staticmethod
        def shared_tool(x: str = ""):
            return "a"

    class PluginB:
        @staticmethod
        def shared_tool(x: str = ""):
            return "b"

    registry.register(PluginA, "plugin_a", level="extended")
    registry.register(PluginB, "plugin_b", level="extended")

    assert registry.resolve_bare_tool_name("shared_tool") is None
    result = await registry.call("shared_tool", {"x": "1"})
    assert "Ambiguous tool name" in result


if __name__ == "__main__":
    asyncio.run(test_format_conversion())
