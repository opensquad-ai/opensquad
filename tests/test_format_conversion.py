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


async def test_namespace_only_name_defaults_to_search():
    registry = ToolRegistry()

    class WebSearchTools:
        @staticmethod
        def search(query: str = ""):
            return f"hit:{query}"

        @staticmethod
        def fetch(url: str = ""):
            return f"page:{url}"

    registry.register(WebSearchTools, "websearch", level="core")
    assert registry.resolve_namespace_default_call("websearch") == "websearch.search"
    result = await registry.call("websearch", {"query": "福州天气"})
    assert result == "hit:福州天气"


async def test_shell_alias_dispatches_to_run_session_job():
    registry = ToolRegistry()

    class SystemTools:
        @staticmethod
        def run_session_job(command: str = "", timeout: float = 120.0):
            return f"ran:{command}"

    registry.register(SystemTools, "system", level="core")
    assert registry.resolve_tool_alias("shell") == "system.run_session_job"
    assert registry.resolve_tool_alias("system.shell") == "system.run_session_job"
    assert registry.resolve_tool_alias("system__shell") == "system.run_session_job"
    assert registry.resolve_tool_alias("system shell") == "system.run_session_job"
    assert registry.resolve_tool_alias("执行命令") == "system.run_session_job"

    result = await registry.call("shell", {"command": "dir /b"})
    assert result == "ran:dir /b"
    result2 = await registry.call("system.shell", {"command": "echo hi"})
    assert result2 == "ran:echo hi"
    result3 = await registry.call("cmd", {"command": "cd /d c:\\tmp && dir /b"})
    assert result3.startswith("ran:")


async def test_filesystem_and_search_aliases():
    registry = ToolRegistry()

    class FileTools:
        @staticmethod
        def read_file(path: str, start_line: int = 1):
            return f"read:{path}:{start_line}"

        @staticmethod
        def search_files(path: str = ".", pattern: str = "", include: str = "*"):
            return f"grep:{path}:{pattern}:{include}"

        @staticmethod
        def find_files(path: str = ".", pattern: str = "**/*"):
            return f"glob:{path}:{pattern}"

        @staticmethod
        def list_directory(path: str = "."):
            return f"ls:{path}"

        @staticmethod
        def replace_in_file(path: str, old_str: str, new_str: str, replace_all: bool = False):
            return f"edit:{path}:{old_str}->{new_str}:{replace_all}"

        @staticmethod
        def write_file(path: str, content: str):
            return f"write:{path}:{content}"

    class WebSearchTools:
        @staticmethod
        def search(query: str = "", queries=None):
            return f"web:{query or queries}"

        @staticmethod
        def fetch(urls=None):
            return f"fetch:{urls}"

    registry.register(FileTools, "filesystem", level="core")
    registry.register(WebSearchTools, "websearch", level="core")

    assert "read:" in await registry.call("read", {"file_path": "a.py"})
    assert "grep:" in await registry.call("grep", {"pattern": "TODO", "path": "src", "glob": "*.py"})
    assert "glob:" in await registry.call("glob", {"glob_pattern": "**/*.ts"})
    assert "ls:" in await registry.call("ls", {"path": "."})
    assert "edit:" in await registry.call(
        "str_replace",
        {"path": "a.py", "old_string": "foo", "new_string": "bar"},
    )
    assert "write:" in await registry.call("create_file", {"path": "a.py", "contents": "hi"})
    assert "web:" in await registry.call("search", {"query": "福州天气"})
    assert "grep:" in await registry.call("search", {"pattern": "TODO"})
    assert "fetch:" in await registry.call("webfetch", {"url": "https://example.com"})
    listed = await registry.call("list_tools", {})
    assert "filesystem" in listed and "websearch" in listed


async def test_unknown_kwargs_are_dropped():
    registry = ToolRegistry()
    registry.register(SampleTools, "test_tools", level="core")
    result = await registry.call(
        "test_tools.sample_function",
        {"arg1": "hello", "job_name": "x", "stat": True},
    )
    assert "Success: arg1=hello" in result
    assert "Error:" not in result


if __name__ == "__main__":
    asyncio.run(test_format_conversion())
