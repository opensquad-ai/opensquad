#!/usr/bin/env python3
"""
测试工具名称格式转换
验证 registry.call() 支持两种格式：
1. namespace.function (XML 模式)
2. namespace__function (Native FC 模式)
"""

import asyncio
import pytest
from opensquad.registry import ToolRegistry

pytestmark = pytest.mark.asyncio


class TestTools:
    @staticmethod
    def test_function(arg1: str, arg2: int = 10):
        """测试函数"""
        return f"Success: arg1={arg1}, arg2={arg2}"


async def test_format_conversion():
    print("\n=== 测试工具名称格式转换 ===\n")
    
    # 创建 registry 并注册测试工具
    registry = ToolRegistry()
    registry.register(TestTools, "test_tools", level="core")
    
    # 测试 1: XML 格式（点分隔）
    print("测试 1: XML 格式 (test_tools.test_function)")
    result1 = await registry.call("test_tools.test_function", {"arg1": "hello", "arg2": 20})
    print(f"结果: {result1}")
    assert "Success" in result1, f"❌ XML 格式失败: {result1}"
    print("✅ XML 格式测试通过\n")
    
    # 测试 2: Native FC 格式（双下划线）
    print("测试 2: Native FC 格式 (test_tools__test_function)")
    result2 = await registry.call("test_tools__test_function", {"arg1": "world", "arg2": 30})
    print(f"结果: {result2}")
    assert "Success" in result2, f"❌ Native FC 格式失败: {result2}"
    print("✅ Native FC 格式测试通过\n")
    
    # 测试 3: 无效格式（没有分隔符）
    print("测试 3: 无效格式 (invalid_format)")
    result3 = await registry.call("invalid_format", {"arg1": "test"})
    print(f"结果: {result3}")
    assert "Invalid format" in result3, f"❌ 应该返回错误: {result3}"
    print("✅ 无效格式正确报错\n")
    
    # 测试 4: MCP 格式（特殊处理，应该不受影响）
    print("测试 4: MCP 格式 (mcp__some_tool)")
    result4 = await registry.call("mcp__some_tool", {"arg1": "test"})
    print(f"结果: {result4}")
    # MCP adapter 不存在时应该返回错误（但不是格式错误）
    assert "Invalid format" not in result4, f"❌ MCP 格式不应报告格式错误: {result4}"
    print("✅ MCP 格式不受影响\n")
    
    print("🎉 所有格式转换测试通过！")


if __name__ == "__main__":
    asyncio.run(test_format_conversion())
