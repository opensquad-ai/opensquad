"""
Test Dual Mode Compatibility: XML and Native Function Calling

This test verifies that both modes work correctly and can coexist.
"""

import json
from unittest.mock import Mock
from opensquad.tool_call_strategy import (
    XMLToolCallStrategy,
    NativeToolCallStrategy,
    ToolCallStrategySelector
)


def test_xml_mode_still_works():
    """验证 XML 模式仍然正常工作"""
    config = {
        "model": {
            "provider": "openai_compat",
            "model_name": "glm-5",
            "tool_call_mode": "xml"
        }
    }
    
    mock_registry = Mock()
    mock_registry.generate_tool_descriptions.return_value = "Tools: foo, bar"
    
    strategy = ToolCallStrategySelector.select(config, mock_registry)
    
    assert isinstance(strategy, XMLToolCallStrategy)
    assert strategy.get_strategy_name() == "XML"
    print("✅ XML 模式正常工作")


def test_native_mode_works():
    """验证 Native FC 模式正常工作"""
    config = {
        "model": {
            "provider": "openai_compat",
            "model_name": "glm-5",
            "tool_call_mode": "native"
        }
    }
    
    mock_registry = Mock()
    mock_registry.generate_openai_tools.return_value = [
        {
            "type": "function",
            "function": {
                "name": "test.tool",
                "description": "Test tool",
                "parameters": {"type": "object", "properties": {}}
            }
        }
    ]
    
    strategy = ToolCallStrategySelector.select(config, mock_registry)
    
    assert isinstance(strategy, NativeToolCallStrategy)
    assert strategy.get_strategy_name() == "Native-FC"
    print("✅ Native FC 模式正常工作")


def test_auto_mode_selects_native_for_glm5():
    """验证 auto 模式为 GLM-5 选择 Native FC"""
    config = {
        "model": {
            "provider": "openai_compat",
            "model_name": "glm-5",
            "tool_call_mode": "auto"
        }
    }
    
    mock_registry = Mock()
    mock_registry.generate_openai_tools.return_value = []
    
    strategy = ToolCallStrategySelector.select(config, mock_registry)
    
    assert isinstance(strategy, NativeToolCallStrategy)
    print("✅ auto 模式为 GLM-5 自动选择 Native FC")


def test_auto_mode_selects_xml_for_unknown():
    """验证 auto 模式为未知模型选择 XML"""
    config = {
        "model": {
            "provider": "unknown",
            "model_name": "unknown-model",
            "tool_call_mode": "auto"
        }
    }
    
    mock_registry = Mock()
    
    strategy = ToolCallStrategySelector.select(config, mock_registry)
    
    assert isinstance(strategy, XMLToolCallStrategy)
    print("✅ auto 模式为未知模型自动降级到 XML")


def test_default_mode_is_auto():
    """验证未指定 tool_call_mode 时默认为 auto"""
    config = {
        "model": {
            "provider": "openai_compat",
            "model_name": "gpt-4"
            # 没有 tool_call_mode 字段
        }
    }
    
    mock_registry = Mock()
    mock_registry.generate_openai_tools.return_value = []
    
    strategy = ToolCallStrategySelector.select(config, mock_registry)
    
    # GPT-4 支持 FC，应该选择 Native
    assert isinstance(strategy, NativeToolCallStrategy)
    print("✅ 默认模式为 auto，GPT-4 自动选择 Native FC")


def test_both_strategies_produce_valid_output():
    """验证两种策略都能产生有效输出"""
    mock_registry = Mock()
    mock_registry.generate_tool_descriptions.return_value = "Tool descriptions"
    mock_registry.generate_openai_tools.return_value = [{"type": "function"}]
    
    # XML 策略
    xml_strategy = XMLToolCallStrategy(mock_registry)
    xml_result = xml_strategy.prepare_llm_call("System: {{TOOL_DESCRIPTIONS}}")
    assert "Tool descriptions" in xml_result["system_prompt"]
    assert xml_result["tools"] is None
    print("✅ XML 策略输出有效")
    
    # Native FC 策略
    native_strategy = NativeToolCallStrategy(mock_registry)
    native_result = native_strategy.prepare_llm_call("System: {{TOOL_DESCRIPTIONS}}")
    assert native_result["tools"] is not None
    assert len(native_result["tools"]) == 1
    print("✅ Native FC 策略输出有效")


if __name__ == "__main__":
    print("\n" + "="*60)
    print("  双模式兼容性测试")
    print("="*60 + "\n")
    
    test_xml_mode_still_works()
    test_native_mode_works()
    test_auto_mode_selects_native_for_glm5()
    test_auto_mode_selects_xml_for_unknown()
    test_default_mode_is_auto()
    test_both_strategies_produce_valid_output()
    
    print("\n" + "="*60)
    print("  ✅ 所有兼容性测试通过！")
    print("  ✅ XML 和 Native FC 模式可以完美共存！")
    print("="*60 + "\n")
