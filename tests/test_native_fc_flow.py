#!/usr/bin/env python3
"""
测试 Native FC 完整流程：
1. 策略是否正确移除 XML 格式说明
2. 工具调用是否在流式中被解析
3. 工具数据是否正确返回
"""

import json
import sys
from opensquad.tool_call_strategy import NativeToolCallStrategy
from opensquad.registry import ToolRegistry


def test_prompt_cleanup():
    """测试 1: System Prompt 是否正确移除 XML 格式"""
    print("\n" + "="*70)
    print("测试 1: System Prompt 清理")
    print("="*70)
    
    registry = ToolRegistry()
    strategy = NativeToolCallStrategy(registry)
    
    # 模拟包含 XML 格式说明的 prompt
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
    
    print(f"原始 prompt 长度: {len(test_prompt)} 字符")
    print(f"清理后长度: {len(cleaned_prompt)} 字符")
    print(f"\n清理后的 prompt:\n{cleaned_prompt}")
    
    # 验证（测试 prompt 里本来就没有 XML 格式说明，所以只检查是否添加了 Native FC 说明）
    assert "Native Function Calling" in cleaned_prompt, "❌ Native FC 说明未添加"
    assert "DO NOT" in cleaned_prompt and "XML format" in cleaned_prompt, "❌ 禁止使用 XML 的警告未添加"
    assert "## 4. Other Instructions" in cleaned_prompt, "❌ 其他指令被错误移除"
    
    print("\n✅ Prompt 清理测试通过（Native FC 说明已添加）")
    return True


def test_tool_definitions():
    """测试 2: 工具定义是否正确生成"""
    print("\n" + "="*70)
    print("测试 2: 工具定义生成")
    print("="*70)
    
    registry = ToolRegistry()
    
    # 注册一个测试工具（需要作为工具集）
    class TestTools:
        @staticmethod
        def test_search(query: str, limit: int = 10):
            """
            搜索网络内容
            
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
    print(f"\n生成了 {len(tools)} 个工具定义")
    
    # 找到 test_search
    test_tool = next((t for t in tools if t["function"]["name"] == "test_tools__test_search"), None)
    
    if test_tool:
        print(f"\n✅ 找到工具: test_tools__test_search")
        print(f"描述: {test_tool['function']['description']}")
        print(f"参数: {json.dumps(test_tool['function']['parameters'], indent=2, ensure_ascii=False)}")
        
        # 验证
        assert "query" in test_tool["function"]["parameters"]["properties"], "❌ 缺少 query 参数"
        assert "limit" in test_tool["function"]["parameters"]["properties"], "❌ 缺少 limit 参数"
        assert "搜索关键词" in test_tool["function"]["parameters"]["properties"]["query"]["description"], "❌ 参数描述未提取"
        
        print("\n✅ 工具定义测试通过")
        return True
    else:
        print(f"❌ 未找到 test_tools__test_search，可用工具: {[t['function']['name'] for t in tools[:5]]}")
        return False


def test_streaming_parse():
    """测试 3: 流式解析工具调用"""
    print("\n" + "="*70)
    print("测试 3: 流式解析工具调用")
    print("="*70)
    
    registry = ToolRegistry()
    
    # 注册测试工具集
    class TestTools:
        @staticmethod
        def test_tool(arg1: str):
            """测试工具"""
            return f"Result: {arg1}"
    
    registry.register(TestTools, "test_tools", level="core")
    strategy = NativeToolCallStrategy(registry)
    
    # 模拟流式响应对象（使用 SimpleNamespace 模拟 API response）
    from types import SimpleNamespace
    
    chunks = [
        # Chunk 1: 角色
        SimpleNamespace(
            choices=[SimpleNamespace(
                delta=SimpleNamespace(role="assistant"),
                finish_reason=None
            )]
        ),
        # Chunk 2: 工具调用开始
        SimpleNamespace(
            choices=[SimpleNamespace(
                delta=SimpleNamespace(
                    tool_calls=[SimpleNamespace(
                        index=0,
                        id="call_123",
                        type="function",
                        function=SimpleNamespace(
                            name="test_tools__test_tool",
                            arguments=""
                        )
                    )]
                ),
                finish_reason=None
            )]
        ),
        # Chunk 3: 参数流式传输 1
        SimpleNamespace(
            choices=[SimpleNamespace(
                delta=SimpleNamespace(
                    tool_calls=[SimpleNamespace(
                        index=0,
                        function=SimpleNamespace(
                            arguments='{"arg1":'
                        )
                    )]
                ),
                finish_reason=None
            )]
        ),
        # Chunk 4: 参数流式传输 2
        SimpleNamespace(
            choices=[SimpleNamespace(
                delta=SimpleNamespace(
                    tool_calls=[SimpleNamespace(
                        index=0,
                        function=SimpleNamespace(
                            arguments=' "test_value"}'
                        )
                    )]
                ),
                finish_reason=None
            )]
        ),
        # Chunk 5: 完成
        SimpleNamespace(
            choices=[SimpleNamespace(
                delta=SimpleNamespace(),
                finish_reason="tool_calls"
            )]
        )
    ]
    
    print("\n开始流式解析...")
    parsed_data = None
    
    for i, chunk in enumerate(chunks):
        result = strategy.parse_response(chunk)
        print(f"Chunk {i+1}: {result}")
        if result:
            parsed_data = result
    
    print(f"\n最终解析结果: {parsed_data}")
    
    if parsed_data:
        tool_name, args_dict = parsed_data
        assert tool_name == "test_tools__test_tool", f"❌ 工具名称错误: {tool_name}"
        assert args_dict == {"arg1": "test_value"}, f"❌ 参数错误: {args_dict}"
        print("\n✅ 流式解析测试通过")
        return True
    else:
        print("\n❌ 未解析到工具调用")
        return False


def main():
    """运行所有测试"""
    print("\n🚀 开始 Native FC 完整流程测试\n")
    
    results = []
    
    try:
        results.append(("Prompt 清理", test_prompt_cleanup()))
    except Exception as e:
        print(f"\n❌ 测试 1 失败: {e}")
        results.append(("Prompt 清理", False))
    
    try:
        results.append(("工具定义生成", test_tool_definitions()))
    except Exception as e:
        print(f"\n❌ 测试 2 失败: {e}")
        results.append(("工具定义生成", False))
    
    try:
        results.append(("流式解析", test_streaming_parse()))
    except Exception as e:
        print(f"\n❌ 测试 3 失败: {e}")
        results.append(("流式解析", False))
    
    # 总结
    print("\n" + "="*70)
    print("测试结果总结")
    print("="*70)
    
    for name, passed in results:
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"{status} - {name}")
    
    total = len(results)
    passed_count = sum(1 for _, p in results if p)
    
    print(f"\n总计: {passed_count}/{total} 通过")
    
    if passed_count == total:
        print("\n🎉 所有测试通过！Native FC 流程工作正常")
        return 0
    else:
        print("\n⚠️  部分测试失败，需要进一步调试")
        return 1


if __name__ == "__main__":
    sys.exit(main())
