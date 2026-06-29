"""
集成测试：验证完整的 XML 工具调用流程
测试从解析 -> 注册 -> 执行的完整链路
"""
import sys
import os
import asyncio
import pytest
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

pytestmark = pytest.mark.asyncio

from opensquad.parser import ResponseParser
from opensquad.registry import ToolRegistry


async def test_full_xml_workflow():
    """测试完整的 XML 工具调用工作流"""
    
    # 1. 模拟 LLM 输出（新 XML 格式）
    llm_output = """
    <tool_call name="system.echo">
    <message>Hello World</message>
    </tool_call>
    """
    
    # 2. 解析工具调用
    name, args = ResponseParser.parse_tool_call(llm_output)
    print(f"✅ 解析成功:")
    print(f"   工具名: {name}")
    print(f"   参数类型: {type(args)}")
    print(f"   参数内容: {args}")
    assert name == "system.echo"
    assert isinstance(args, dict)
    assert args == {"message": "Hello World"}
    
    # 3. 创建工具注册表
    registry = ToolRegistry()
    
    # 4. 注册一个简单的测试工具（创建一个工具对象）
    class SystemTools:
        def echo(self, message: str) -> str:
            """回显消息"""
            return f"Echo: {message}"
    
    registry.register(SystemTools(), "system")
    
    # 5. 调用工具（传入 dict）
    result = await registry.call("system.echo", args)
    print(f"✅ 工具执行成功:")
    print(f"   返回值: {result}")
    assert result == "Echo: Hello World"
    
    print("\n" + "="*50)
    print("✅ 完整工作流测试通过！")
    print("="*50)


async def test_legacy_json_workflow():
    """测试向后兼容：旧 JSON 格式仍然能正常工作"""
    
    # 1. 模拟 LLM 输出（旧 JSON 格式）
    llm_output = """
    <tool_call name="system.echo">
    <arguments>{"message": "Legacy Format"}</arguments>
    </tool_call>
    """
    
    # 2. 解析工具调用
    name, args = ResponseParser.parse_tool_call(llm_output)
    print(f"✅ 兼容模式解析成功:")
    print(f"   工具名: {name}")
    print(f"   参数类型: {type(args)}")
    print(f"   参数内容: {args}")
    assert name == "system.echo"
    assert isinstance(args, dict)
    assert args == {"message": "Legacy Format"}
    
    # 3. 创建工具注册表并调用
    registry = ToolRegistry()
    class SystemTools:
        def echo(self, message: str) -> str:
            return f"Echo: {message}"
    registry.register(SystemTools(), "system")
    
    result = await registry.call("system.echo", args)
    print(f"✅ 工具执行成功:")
    print(f"   返回值: {result}")
    assert result == "Echo: Legacy Format"
    
    print("\n" + "="*50)
    print("✅ 向后兼容测试通过！")
    print("="*50)


def test_cdata_workflow():
    """测试 CDATA 格式的 XML 参数"""
    
    # 1. 模拟 LLM 输出（包含特殊字符的 CDATA）
    llm_output = """
    <tool_call name="filesystem.write">
    <path>/tmp/test.xml</path>
    <content><![CDATA[<html><body>Hello & "World"</body></html>]]></content>
    </tool_call>
    """
    
    # 2. 解析工具调用
    name, args = ResponseParser.parse_tool_call(llm_output)
    print(f"✅ CDATA 解析成功:")
    print(f"   工具名: {name}")
    print(f"   参数: {args}")
    assert name == "filesystem.write"
    assert args["path"] == "/tmp/test.xml"
    assert args["content"] == '<html><body>Hello & "World"</body></html>'
    
    print("\n" + "="*50)
    print("✅ CDATA 工作流测试通过！")
    print("="*50)


def test_multiline_workflow():
    """测试多行文本参数"""
    
    llm_output = """
    <tool_call name="filesystem.write">
    <path>/tmp/test.py</path>
    <content>def hello():
    print("Hello")
    print("World")</content>
    </tool_call>
    """
    
    name, args = ResponseParser.parse_tool_call(llm_output)
    print(f"✅ 多行文本解析成功:")
    print(f"   工具名: {name}")
    print(f"   参数: {args}")
    assert name == "filesystem.write"
    assert "def hello():" in args["content"]
    assert args["content"].count("\n") >= 2
    
    print("\n" + "="*50)
    print("✅ 多行文本工作流测试通过！")
    print("="*50)


if __name__ == "__main__":
    print("开始集成测试...\n")
    
    async def run_tests():
        try:
            await test_full_xml_workflow()
            print("\n")
            await test_legacy_json_workflow()
            print("\n")
            test_cdata_workflow()
            print("\n")
            test_multiline_workflow()
            
            print("\n" + "🎉"*25)
            print("所有集成测试通过！XML 工具调用系统工作正常！")
            print("🎉"*25)
            
        except AssertionError as e:
            print(f"\n❌ 测试失败: {e}")
            import traceback
            traceback.print_exc()
            sys.exit(1)
        except Exception as e:
            print(f"\n❌ 意外错误: {e}")
            import traceback
            traceback.print_exc()
            sys.exit(1)
    
    asyncio.run(run_tests())
