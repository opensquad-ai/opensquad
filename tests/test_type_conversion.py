"""
测试 XML 格式中基础类型的自动转换功能

测试场景：
1. int 类型：字符串 "10" -> 整数 10
2. float 类型：字符串 "3.14" -> 浮点数 3.14
3. bool 类型：字符串 "true" -> 布尔值 True
4. 混合类型：同时包含多种类型的参数
5. 错误处理：无效的类型转换
"""

from typing import Any

import pytest

from opensquad.registry import ToolRegistry

# 使用 anyio 作为 async 后端
pytestmark = pytest.mark.anyio


# 测试用的工具函数
def tool_with_int(count: int, name: str) -> dict[str, Any]:
    """模拟带整数参数的工具"""
    return {
        "count": count,
        "count_type": type(count).__name__,
        "name": name,
    }


def tool_with_float(price: float, currency: str = "USD") -> dict[str, Any]:
    """模拟带浮点数参数的工具"""
    return {
        "price": price,
        "price_type": type(price).__name__,
        "currency": currency,
    }


def tool_with_bool(enabled: bool, verbose: bool = False) -> dict[str, Any]:
    """模拟带布尔参数的工具"""
    return {
        "enabled": enabled,
        "enabled_type": type(enabled).__name__,
        "verbose": verbose,
        "verbose_type": type(verbose).__name__,
    }


def tool_with_mixed(
    queries: list[str],
    max_results: int,
    min_score: float,
    strict: bool,
) -> dict[str, Any]:
    """模拟混合类型参数的工具"""
    return {
        "queries": queries,
        "max_results": max_results,
        "min_score": min_score,
        "strict": strict,
        "types": {
            "queries": type(queries).__name__,
            "max_results": type(max_results).__name__,
            "min_score": type(min_score).__name__,
            "strict": type(strict).__name__,
        },
    }


@pytest.mark.anyio
async def test_int_conversion():
    """测试字符串自动转换为整数"""
    import types

    mock_module = types.ModuleType("mock_tools")
    mock_module.tool_with_int = tool_with_int

    registry = ToolRegistry()
    registry.register(mock_module, "test", level="user")

    # 测试：传入字符串 "10"
    result = await registry.call("test.tool_with_int", {"count": "10", "name": "测试"})

    assert result["count"] == 10
    assert result["count_type"] == "int"
    assert result["name"] == "测试"


@pytest.mark.anyio
async def test_float_conversion():
    """测试字符串自动转换为浮点数"""
    import types

    mock_module = types.ModuleType("mock_tools")
    mock_module.tool_with_float = tool_with_float

    registry = ToolRegistry()
    registry.register(mock_module, "test", level="user")

    # 测试：传入字符串 "3.14"
    result = await registry.call("test.tool_with_float", {"price": "3.14", "currency": "CNY"})

    assert abs(result["price"] - 3.14) < 0.0001
    assert result["price_type"] == "float"
    assert result["currency"] == "CNY"


@pytest.mark.anyio
async def test_bool_conversion_true():
    """测试字符串自动转换为布尔值（True 的各种表示）"""
    import types

    mock_module = types.ModuleType("mock_tools")
    mock_module.tool_with_bool = tool_with_bool

    registry = ToolRegistry()
    registry.register(mock_module, "test", level="user")

    # 测试 True 的各种表示方式
    for true_value in ["true", "True", "TRUE", "1", "yes", "Yes", "on", "ON"]:
        result = await registry.call("test.tool_with_bool", {"enabled": true_value, "verbose": "false"})

        assert result["enabled"] is True, f"Failed for value: {true_value}"
        assert result["enabled_type"] == "bool"
        assert result["verbose"] is False


@pytest.mark.anyio
async def test_bool_conversion_false():
    """测试字符串自动转换为布尔值（False 的各种表示）"""
    import types

    mock_module = types.ModuleType("mock_tools")
    mock_module.tool_with_bool = tool_with_bool

    registry = ToolRegistry()
    registry.register(mock_module, "test", level="user")

    # 测试 False 的各种表示方式
    for false_value in ["false", "False", "FALSE", "0", "no", "No", "off", "OFF", ""]:
        result = await registry.call("test.tool_with_bool", {"enabled": false_value, "verbose": "true"})

        assert result["enabled"] is False, f"Failed for value: {false_value}"
        assert result["enabled_type"] == "bool"
        assert result["verbose"] is True


@pytest.mark.anyio
async def test_mixed_types():
    """测试混合类型参数（List[str] + int + float + bool）"""
    import types

    mock_module = types.ModuleType("mock_tools")
    mock_module.tool_with_mixed = tool_with_mixed

    registry = ToolRegistry()
    registry.register(mock_module, "test", level="user")

    # 测试：所有参数都是字符串，系统自动转换
    result = await registry.call(
        "test.tool_with_mixed",
        {"queries": "查询1,查询2,查询3", "max_results": "100", "min_score": "0.85", "strict": "true"},
    )

    assert result["queries"] == ["查询1", "查询2", "查询3"]
    assert result["max_results"] == 100
    assert abs(result["min_score"] - 0.85) < 0.0001
    assert result["strict"] is True

    # 验证类型
    assert result["types"]["queries"] == "list"
    assert result["types"]["max_results"] == "int"
    assert result["types"]["min_score"] == "float"
    assert result["types"]["strict"] == "bool"


@pytest.mark.anyio
async def test_invalid_int_conversion():
    """测试无效的整数转换（保持原值）"""
    import types

    mock_module = types.ModuleType("mock_tools")
    mock_module.tool_with_int = tool_with_int

    registry = ToolRegistry()
    registry.register(mock_module, "test", level="user")

    # 测试：传入无效的整数字符串
    result = await registry.call("test.tool_with_int", {"count": "not_a_number", "name": "测试"})

    # 转换失败，保持原字符串值
    assert result["count"] == "not_a_number"
    assert result["count_type"] == "str"


@pytest.mark.anyio
async def test_invalid_float_conversion():
    """测试无效的浮点数转换（保持原值）"""
    import types

    mock_module = types.ModuleType("mock_tools")
    mock_module.tool_with_float = tool_with_float

    registry = ToolRegistry()
    registry.register(mock_module, "test", level="user")

    # 测试：传入无效的浮点数字符串
    result = await registry.call("test.tool_with_float", {"price": "invalid", "currency": "USD"})

    # 转换失败，保持原字符串值
    assert result["price"] == "invalid"
    assert result["price_type"] == "str"


@pytest.mark.anyio
async def test_already_correct_type():
    """测试已经是正确类型的参数（不转换）"""
    import types

    mock_module = types.ModuleType("mock_tools")
    mock_module.tool_with_int = tool_with_int

    registry = ToolRegistry()
    registry.register(mock_module, "test", level="user")

    # 测试：传入已经是整数的参数
    result = await registry.call(
        "test.tool_with_int",
        {
            "count": 42,  # 已经是 int
            "name": "测试",
        },
    )

    assert result["count"] == 42
    assert result["count_type"] == "int"


@pytest.mark.anyio
async def test_negative_numbers():
    """测试负数转换"""
    import types

    mock_module = types.ModuleType("mock_tools")
    mock_module.tool_with_int = tool_with_int
    mock_module.tool_with_float = tool_with_float

    registry = ToolRegistry()
    registry.register(mock_module, "test", level="user")

    # 测试负整数
    result = await registry.call("test.tool_with_int", {"count": "-10", "name": "负数"})
    assert result["count"] == -10

    # 测试负浮点数
    result = await registry.call("test.tool_with_float", {"price": "-3.14", "currency": "USD"})
    assert abs(result["price"] - (-3.14)) < 0.0001


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
