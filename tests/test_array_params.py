# -*- coding: utf-8 -*-
"""
测试 XML 格式中数组参数的自动转换功能

测试场景：
1. 函数期望 List[str]，传入逗号分隔的字符串 -> 自动转换为 List
2. 函数期望 str，传入字符串 -> 保持原样
3. 函数期望 List[int]，传入字符串 -> 不转换（仅支持 List[str]）
4. 函数期望 List[str]，传入已经是 List -> 保持原样
"""

import pytest
from typing import List, Dict, Any
from opensquad.registry import ToolRegistry

# 使用 anyio 作为 async 后端
pytestmark = pytest.mark.anyio


# 测试用的工具函数（模拟 WebSearch）
def mock_search(queries: List[str], max_results: int = 10) -> Dict[str, Any]:
    """模拟搜索工具"""
    return {
        "status": "ok",
        "queries": queries,
        "query_count": len(queries),
        "max_results": max_results,
    }


def mock_echo(message: str) -> str:
    """模拟普通字符串参数工具"""
    return f"Echo: {message}"


def mock_add_numbers(numbers: List[int]) -> int:
    """模拟 List[int] 参数工具（不应自动转换）"""
    return sum(numbers)


@pytest.mark.anyio
async def test_list_str_auto_conversion():
    """测试 List[str] 参数自动转换"""
    import types
    mock_module = types.ModuleType("mock_tools")
    mock_module.mock_search = mock_search
    
    registry = ToolRegistry()
    registry.register(mock_module, "test", level="user")
    
    # 测试：传入逗号分隔的字符串
    result = await registry.call("test.mock_search", {
        "queries": "福州天气 今天,福州天气预报,福州气温",
        "max_results": 5
    })
    
    assert result["status"] == "ok"
    assert result["queries"] == ["福州天气 今天", "福州天气预报", "福州气温"]
    assert result["query_count"] == 3
    assert result["max_results"] == 5


@pytest.mark.anyio
async def test_list_str_with_spaces():
    """测试 List[str] 参数自动转换（包含空格）"""
    import types
    mock_module = types.ModuleType("mock_tools")
    mock_module.mock_search = mock_search
    
    registry = ToolRegistry()
    registry.register(mock_module, "test", level="user")
    
    # 测试：逗号前后有空格
    result = await registry.call("test.mock_search", {
        "queries": " 查询1 , 查询2 , 查询3 ",
        "max_results": 10
    })
    
    assert result["queries"] == ["查询1", "查询2", "查询3"]


@pytest.mark.anyio
async def test_list_str_already_list():
    """测试 List[str] 参数已经是 List（不转换）"""
    import types
    mock_module = types.ModuleType("mock_tools")
    mock_module.mock_search = mock_search
    
    registry = ToolRegistry()
    registry.register(mock_module, "test", level="user")
    
    # 测试：传入已经是 List 的参数
    result = await registry.call("test.mock_search", {
        "queries": ["查询A", "查询B"],
        "max_results": 10
    })
    
    assert result["queries"] == ["查询A", "查询B"]


@pytest.mark.anyio
async def test_string_param_no_conversion():
    """测试普通字符串参数不被转换"""
    import types
    mock_module = types.ModuleType("mock_tools")
    mock_module.mock_echo = mock_echo
    
    registry = ToolRegistry()
    registry.register(mock_module, "test", level="user")
    
    # 测试：字符串参数包含逗号，不应被转换
    result = await registry.call("test.mock_echo", {
        "message": "Hello, world, this is a test"
    })
    
    assert result == "Echo: Hello, world, this is a test"


@pytest.mark.anyio
async def test_list_int_no_auto_conversion():
    """测试 List[int] 参数不会自动转换（仅支持 List[str]）"""
    import types
    mock_module = types.ModuleType("mock_tools")
    mock_module.mock_add_numbers = mock_add_numbers
    
    registry = ToolRegistry()
    registry.register(mock_module, "test", level="user")
    
    # 测试：传入字符串，不应被转换为 List[int]
    result = await registry.call("test.mock_add_numbers", {
        "numbers": "1,2,3,4,5"
    })
    
    # 应该返回错误（因为字符串无法直接当作 List[int] 使用）
    assert isinstance(result, str) and "Error" in result


@pytest.mark.anyio
async def test_empty_list_str():
    """测试空字符串转换为空 List"""
    import types
    mock_module = types.ModuleType("mock_tools")
    mock_module.mock_search = mock_search
    
    registry = ToolRegistry()
    registry.register(mock_module, "test", level="user")
    
    # 测试：空字符串
    result = await registry.call("test.mock_search", {
        "queries": "",
        "max_results": 10
    })
    
    assert result["queries"] == []


@pytest.mark.anyio
async def test_list_str_single_element():
    """测试单个元素的 List"""
    import types
    mock_module = types.ModuleType("mock_tools")
    mock_module.mock_search = mock_search
    
    registry = ToolRegistry()
    registry.register(mock_module, "test", level="user")
    
    # 测试：单个元素（无逗号）
    result = await registry.call("test.mock_search", {
        "queries": "单个查询",
        "max_results": 10
    })
    
    assert result["queries"] == ["单个查询"]


@pytest.mark.anyio
async def test_list_str_with_semicolon():
    """测试使用分号分隔（内容包含逗号）"""
    import types
    mock_module = types.ModuleType("mock_tools")
    mock_module.mock_search = mock_search
    
    registry = ToolRegistry()
    registry.register(mock_module, "test", level="user")
    
    # 测试：使用分号分隔，内容包含逗号
    result = await registry.call("test.mock_search", {
        "queries": "2024年1月1日,北京,天气预报;深圳市南山区,科技园;上海天气",
        "max_results": 10
    })
    
    assert result["queries"] == ["2024年1月1日,北京,天气预报", "深圳市南山区,科技园", "上海天气"]
    assert result["query_count"] == 3


@pytest.mark.anyio
async def test_list_str_semicolon_priority():
    """测试分号优先级（有分号时忽略逗号）"""
    import types
    mock_module = types.ModuleType("mock_tools")
    mock_module.mock_search = mock_search
    
    registry = ToolRegistry()
    registry.register(mock_module, "test", level="user")
    
    # 测试：同时包含分号和逗号，分号优先
    result = await registry.call("test.mock_search", {
        "queries": "查询A,包含逗号;查询B",
        "max_results": 10
    })
    
    # 应该按分号分隔，而不是逗号
    assert result["queries"] == ["查询A,包含逗号", "查询B"]
    assert result["query_count"] == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
