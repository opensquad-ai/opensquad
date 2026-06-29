# -*- coding: utf-8 -*-
"""
测试 ToolRegistry 的 docstring 参数描述提取功能
"""
import pytest
from opensquad.registry import ToolRegistry


class TestDocstringParsing:
    """测试 _parse_docstring_params() 方法"""
    
    def setup_method(self):
        self.registry = ToolRegistry()
    
    def test_google_style_docstring(self):
        """测试 Google Style docstring 解析"""
        def sample_func(path: str, start_line: int = 1, max_lines: int = 200):
            """
            读取文件内容。
            
            Args:
                path: 文件路径。
                start_line: 起始行号 (从1开始)，默认为1。
                max_lines: 最大读取行数，默认 200。
            """
            pass
        
        param_desc = self.registry._parse_docstring_params(sample_func)
        
        assert "path" in param_desc
        assert "start_line" in param_desc
        assert "max_lines" in param_desc
        
        assert param_desc["path"] == "文件路径。"
        assert "起始行号" in param_desc["start_line"]
        assert "最大读取行数" in param_desc["max_lines"]
    
    def test_google_style_multiline_description(self):
        """测试 Google Style 多行描述"""
        def sample_func(pattern: str, case_sensitive: bool = False):
            """
            搜索功能。
            
            Args:
                pattern: 正则表达式匹配模式 (Regex)。
                    支持完整的正则语法。
                case_sensitive: 是否区分大小写，
                    默认 False。
            """
            pass
        
        param_desc = self.registry._parse_docstring_params(sample_func)
        
        assert "pattern" in param_desc
        assert "case_sensitive" in param_desc
        
        # 多行描述应该被合并成单行
        assert "正则表达式" in param_desc["pattern"]
        assert "支持完整的正则语法" in param_desc["pattern"]
        assert "是否区分大小写" in param_desc["case_sensitive"]
    
    def test_numpy_style_docstring(self):
        """测试 NumPy Style docstring 解析"""
        def sample_func(x: int, y: int):
            """
            计算两数之和。
            
            Parameters
            ----------
            x : int
                第一个数字
            y : int
                第二个数字
            
            Returns
            -------
            int
                两数之和
            """
            pass
        
        param_desc = self.registry._parse_docstring_params(sample_func)
        
        assert "x" in param_desc
        assert "y" in param_desc
        
        assert "第一个数字" in param_desc["x"]
        assert "第二个数字" in param_desc["y"]
    
    def test_sphinx_style_docstring(self):
        """测试 Sphinx Style docstring 解析"""
        def sample_func(name: str, age: int):
            """
            用户信息。
            
            :param name: 用户姓名
            :param age: 用户年龄
            :type name: str
            :type age: int
            """
            pass
        
        param_desc = self.registry._parse_docstring_params(sample_func)
        
        assert "name" in param_desc
        assert "age" in param_desc
        
        assert param_desc["name"] == "用户姓名"
        assert param_desc["age"] == "用户年龄"
    
    def test_no_docstring(self):
        """测试没有 docstring 的函数"""
        def sample_func(x, y):
            pass
        
        param_desc = self.registry._parse_docstring_params(sample_func)
        
        assert param_desc == {}
    
    def test_empty_args_section(self):
        """测试没有 Args 部分的 docstring"""
        def sample_func(x: int):
            """
            简单函数描述。
            """
            pass
        
        param_desc = self.registry._parse_docstring_params(sample_func)
        
        assert param_desc == {}


class TestParametersSchemaWithDocstring:
    """测试 _extract_parameters_schema() 使用 docstring 描述"""
    
    def setup_method(self):
        self.registry = ToolRegistry()
    
    def test_schema_with_google_docstring(self):
        """测试 Schema 生成使用 Google Style 描述"""
        def read_file(path: str, start_line: int = 1, max_lines: int = 200):
            """
            读取文件。
            
            Args:
                path: 文件路径。
                start_line: 起始行号，默认为1。
                max_lines: 最大行数，默认200。
            """
            pass
        
        schema = self.registry._extract_parameters_schema(read_file)
        
        assert schema["type"] == "object"
        assert "path" in schema["properties"]
        assert "start_line" in schema["properties"]
        assert "max_lines" in schema["properties"]
        
        # 验证描述来自 docstring
        assert schema["properties"]["path"]["description"] == "文件路径。"
        assert "起始行号" in schema["properties"]["start_line"]["description"]
        assert "最大行数" in schema["properties"]["max_lines"]["description"]
        
        # 验证类型
        assert schema["properties"]["path"]["type"] == "string"
        assert schema["properties"]["start_line"]["type"] == "integer"
        assert schema["properties"]["max_lines"]["type"] == "integer"
        
        # 验证必需参数
        assert schema["required"] == ["path"]
    
    def test_schema_without_docstring_uses_default(self):
        """测试没有 docstring 时使用默认描述"""
        def simple_func(x: str, y: int):
            pass
        
        schema = self.registry._extract_parameters_schema(simple_func)
        
        # 应该使用默认描述
        assert schema["properties"]["x"]["description"] == "Parameter x"
        assert schema["properties"]["y"]["description"] == "Parameter y"
    
    def test_schema_with_partial_docstring(self):
        """测试部分参数有 docstring 描述"""
        def mixed_func(a: str, b: int, c: bool):
            """
            混合函数。
            
            Args:
                a: 参数A的描述。
                c: 参数C的描述。
            """
            pass
        
        schema = self.registry._extract_parameters_schema(mixed_func)
        
        # a 和 c 有描述，b 使用默认
        assert schema["properties"]["a"]["description"] == "参数A的描述。"
        assert schema["properties"]["b"]["description"] == "Parameter b"
        assert schema["properties"]["c"]["description"] == "参数C的描述。"


class TestOpenAIToolsGenerationWithDocstring:
    """测试 generate_openai_tools() 使用 docstring 描述"""
    
    def setup_method(self):
        self.registry = ToolRegistry()
        
        # 创建一个测试工具集
        class TestTools:
            def search_files(self, path: str = ".", pattern: str = "", 
                           max_results: int = 100):
                """
                全文搜索 (Grep)。
                
                Args:
                    path: 搜索的根目录，默认为 "."。
                    pattern: 正则表达式匹配模式 (Regex)。
                    max_results: 最大返回匹配项数，默认 100。
                """
                pass
            
            def read_file(self, filepath: str, start: int = 1):
                """
                读取文件内容。
                
                Args:
                    filepath: 要读取的文件路径。
                    start: 起始行号。
                """
                pass
        
        self.registry.register(TestTools(), "test_tools", level="core")
    
    def test_openai_tools_include_docstring_descriptions(self):
        """测试生成的 OpenAI Tools 包含 docstring 描述"""
        tools = self.registry.generate_openai_tools()
        
        # 查找 search_files 工具
        search_tool = None
        for tool in tools:
            if tool["function"]["name"] == "test_tools__search_files":
                search_tool = tool
                break
        
        assert search_tool is not None
        
        # 验证参数描述
        params = search_tool["function"]["parameters"]["properties"]
        assert "path" in params
        assert "pattern" in params
        assert "max_results" in params
        
        # 验证描述来自 docstring
        assert "搜索的根目录" in params["path"]["description"]
        assert "正则表达式" in params["pattern"]["description"]
        assert "最大返回匹配项数" in params["max_results"]["description"]
    
    def test_all_tools_have_meaningful_descriptions(self):
        """测试所有工具都有有意义的描述"""
        tools = self.registry.generate_openai_tools()
        
        for tool in tools:
            params = tool["function"]["parameters"]["properties"]
            for param_name, param_schema in params.items():
                description = param_schema.get("description", "")
                
                # 描述不应该是空的
                assert description != ""
                
                # 如果是默认描述格式，至少应该有参数名
                if description.startswith("Parameter "):
                    assert param_name in description


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
