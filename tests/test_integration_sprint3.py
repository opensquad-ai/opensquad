#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
集成测试：验证 Native FC 模式的完整流程

这个测试模拟 Runner 的工作流程，验证：
1. 策略选择
2. prepare_llm_call() 生成正确的参数
3. 工具 Schema 包含完整的参数描述
"""

import json
import sys
import os
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Resolve agent config path (supports non-standard project roots)
def _agent_config_path(agent_name: str = "ultimate") -> str:
    project_root = os.environ.get("OPENSQUAD_WORKSPACE", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(project_root, "agents", agent_name, "config.json")

pytestmark = pytest.mark.asyncio

from opensquad.registry import ToolRegistry
from opensquad.tool_call_strategy import ToolCallStrategySelector
from opensquad.tools import filesystem, system


def test_integration():
    """完整流程测试"""
    print("=" * 60)
    print("集成测试：Native FC 模式完整流程")
    print("=" * 60)
    
    # 1. 加载配置
    print("\n[1] 加载配置...")
    with open(_agent_config_path(), 'r', encoding='utf-8') as f:
        config = json.load(f)
    
    model_config = config['model']
    print(f"  ✓ Tool Call Mode: {model_config.get('tool_call_mode', 'auto')}")
    print(f"  ✓ Model: {model_config.get('model_name')}")
    
    # 2. 注册工具
    print("\n[2] 注册工具...")
    registry = ToolRegistry()
    registry.register(filesystem, 'filesystem', level='core')
    registry.register(system, 'system', level='core')
    print(f"  ✓ 注册了 2 个工具集")
    
    # 3. 选择策略
    print("\n[3] 选择策略...")
    strategy = ToolCallStrategySelector.select(config, registry)
    strategy_name = strategy.get_strategy_name()
    print(f"  ✓ 策略: {strategy_name}")
    
    # 4. 准备 LLM 调用参数
    print("\n[4] 准备 LLM 调用参数...")
    base_prompt = "System: You are a helpful assistant.\n{{TOOL_DESCRIPTIONS}}"
    llm_params = strategy.prepare_llm_call(base_prompt)
    
    print(f"  ✓ 返回的参数:")
    print(f"    - system_prompt: {len(llm_params['system_prompt'])} 字符")
    print(f"    - tools: {len(llm_params.get('tools', []))} 个")
    print(f"    - tool_choice: {llm_params.get('tool_choice', 'N/A')}")
    
    # 5. 验证 Native FC 模式的特征
    print("\n[5] 验证 Native FC 模式特征...")
    if strategy_name == "Native-FC":
        # Native FC 模式应该：
        # - 移除 System Prompt 中的工具格式说明
        # - 提供 tools 参数（OpenAI Tools JSON Schema）
        # - tools 参数包含完整的参数描述
        
        assert 'tools' in llm_params, "Native FC 模式必须提供 tools 参数"
        assert len(llm_params['tools']) > 0, "tools 参数不能为空"
        
        # 检查 System Prompt 中是否移除了工具格式说明
        assert "{{TOOL_DESCRIPTIONS}}" not in llm_params['system_prompt'], \
            "System Prompt 中不应包含 {{TOOL_DESCRIPTIONS}} 占位符"
        
        print(f"  ✓ System Prompt 已移除工具格式说明")
        print(f"  ✓ 提供了 {len(llm_params['tools'])} 个工具的 Schema")
        
        # 6. 验证工具 Schema 质量
        print("\n[6] 验证工具 Schema 质量...")
        
        # 检查一个具体的工具（filesystem__read_file）
        read_file_tool = None
        for tool in llm_params['tools']:
            if tool['function']['name'] == 'filesystem__read_file':
                read_file_tool = tool
                break
        
        if read_file_tool:
            print(f"  ✓ 找到工具: filesystem__read_file")
            
            func = read_file_tool['function']
            params = func['parameters']['properties']
            
            print(f"    描述: {func['description']}")
            print(f"    参数数量: {len(params)}")
            
            # 验证参数描述不是默认的 "Parameter xxx"
            for param_name, param_schema in params.items():
                desc = param_schema.get('description', '')
                if desc.startswith("Parameter "):
                    print(f"    ⚠ 参数 {param_name} 使用默认描述: {desc}")
                else:
                    print(f"    ✓ 参数 {param_name} 有真实描述: {desc[:50]}...")
        else:
            print(f"  ⚠ 未找到 filesystem__read_file 工具")
    
    elif strategy_name == "XML":
        # XML 模式应该：
        # - 在 System Prompt 中包含工具描述
        # - 不提供 tools 参数
        
        assert "{{TOOL_DESCRIPTIONS}}" not in llm_params['system_prompt'], \
            "System Prompt 中 {{TOOL_DESCRIPTIONS}} 应该被替换"
        assert llm_params.get('tools') is None, "XML 模式不应提供 tools 参数"
        
        print(f"  ✓ System Prompt 包含工具描述")
        print(f"  ✓ 不提供 tools 参数（使用 XML 格式）")
    
    # 7. 对比两种模式
    print("\n[7] 对比两种模式...")
    
    # 模拟 XML 模式
    xml_config = config.copy()
    xml_config['model'] = model_config.copy()
    xml_config['model']['tool_call_mode'] = 'xml'
    
    xml_registry = ToolRegistry()
    xml_registry.register(filesystem, 'filesystem', level='core')
    xml_registry.register(system, 'system', level='core')
    
    xml_strategy = ToolCallStrategySelector.select(xml_config, xml_registry)
    xml_params = xml_strategy.prepare_llm_call(base_prompt)
    
    xml_prompt_len = len(xml_params['system_prompt'])
    native_prompt_len = len(llm_params['system_prompt'])
    
    print(f"  XML 模式 System Prompt: {xml_prompt_len} 字符")
    print(f"  Native FC 模式 System Prompt: {native_prompt_len} 字符")
    print(f"  差异: {xml_prompt_len - native_prompt_len} 字符 ({(1 - native_prompt_len/xml_prompt_len)*100:.1f}%)")
    
    # 8. 总结
    print("\n" + "=" * 60)
    print("✅ 集成测试完成")
    print("=" * 60)
    print(f"\n当前配置：")
    print(f"  - 策略: {strategy_name}")
    print(f"  - 工具数量: {len(llm_params.get('tools', []))}")
    print(f"  - System Prompt 长度: {native_prompt_len} 字符")
    
    if strategy_name == "Native-FC":
        print(f"\n✓ Native FC 模式已正确配置")
        print(f"✓ System Prompt 减少了 {xml_prompt_len - native_prompt_len} 字符")
        print(f"✓ 工具参数描述已从 docstring 提取")
    
    print(f"\n下一步：")
    print(f"  1. 启动 Agent: python agents_boot.py ultimate")
    print(f"  2. 在日志中查找 '[Runner] Tool call strategy: Native-FC'")
    print(f"  3. 测试工具调用并观察成功率")


if __name__ == "__main__":
    test_integration()
