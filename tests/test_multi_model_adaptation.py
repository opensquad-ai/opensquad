# -*- coding: utf-8 -*-
"""
Multi-Model Adaptation Tests
测试不同模型的 Native FC 支持检测
"""

import pytest
from opensquad.model_capabilities import (
    ModelCapabilityRegistry,
    supports_function_calling,
    get_model_capability
)


class TestModelCapabilityRegistry:
    """测试模型能力注册表"""
    
    def test_openai_models_support_fc(self):
        """测试 OpenAI 模型支持 Function Calling"""
        assert supports_function_calling("gpt-4", "openai") is True
        assert supports_function_calling("gpt-4-turbo", "openai") is True
        assert supports_function_calling("gpt-4o", "openai") is True
        assert supports_function_calling("gpt-3.5-turbo", "openai") is True
    
    def test_glm_models_support_fc(self):
        """测试 GLM 模型支持 Function Calling"""
        assert supports_function_calling("glm-4", "openai_compat") is True
        assert supports_function_calling("glm-5", "openai_compat") is True
        assert supports_function_calling("glm-4v", "openai_compat") is True
    
    def test_deepseek_models_support_fc(self):
        """测试 DeepSeek 模型支持 Function Calling"""
        assert supports_function_calling("deepseek-chat", "openai_compat") is True
        assert supports_function_calling("deepseek-coder", "openai_compat") is True
        assert supports_function_calling("deepseek-v2", "openai_compat") is True
        assert supports_function_calling("deepseek-v3", "openai_compat") is True
    
    def test_claude_models_support_fc(self):
        """测试 Claude 模型支持 Function Calling"""
        assert supports_function_calling("claude-2", "claude") is True
        assert supports_function_calling("claude-3-sonnet", "claude") is True
        assert supports_function_calling("claude-3-opus", "claude") is True
        assert supports_function_calling("claude-3-haiku", "claude") is True
        assert supports_function_calling("claude-3.5-sonnet", "claude") is True
    
    def test_gemini_models_support_fc(self):
        """测试 Gemini 模型支持 Function Calling"""
        assert supports_function_calling("gemini-pro", "google") is True
        assert supports_function_calling("gemini-1.5-pro", "google") is True
        assert supports_function_calling("gemini-1.5-flash", "google") is True
    
    def test_qwen_models_support_fc(self):
        """测试通义千问模型支持 Function Calling"""
        assert supports_function_calling("qwen-plus", "openai_compat") is True
        assert supports_function_calling("qwen-max", "openai_compat") is True
        assert supports_function_calling("qwen-turbo", "openai_compat") is True
        assert supports_function_calling("qwen-vl-plus", "openai_compat") is True
    
    def test_moonshot_models_no_fc(self):
        """测试 Moonshot 模型不支持 Function Calling"""
        assert supports_function_calling("moonshot-v1-8k", "openai_compat") is False
        assert supports_function_calling("moonshot-v1-32k", "openai_compat") is False
        assert supports_function_calling("moonshot-v1-128k", "openai_compat") is False
    
    def test_baichuan_models_no_fc(self):
        """测试百川模型不支持 Function Calling"""
        assert supports_function_calling("baichuan2-turbo", "openai_compat") is False
    
    def test_minimax_models_no_fc(self):
        """测试 MiniMax 模型不支持 Function Calling"""
        assert supports_function_calling("abab5.5-chat", "openai_compat") is False
    
    def test_fuzzy_matching(self):
        """测试模糊匹配（处理版本号变体）"""
        # GPT-4 变体
        assert supports_function_calling("gpt-4-0125-preview", "openai") is True
        assert supports_function_calling("gpt-4-1106-preview", "openai") is True
        assert supports_function_calling("gpt-4-vision-preview", "openai") is True
        
        # GLM 变体
        assert supports_function_calling("glm-4-plus", "openai_compat") is True
        assert supports_function_calling("glm-5-turbo", "openai_compat") is True
        
        # Claude 变体
        assert supports_function_calling("claude-3-sonnet-20240229", "claude") is True
        assert supports_function_calling("claude-3.5-sonnet-20241022", "claude") is True
    
    def test_unknown_model_default(self):
        """测试未知模型的默认行为"""
        # OpenAI-compatible 未知模型，默认不支持
        assert supports_function_calling("unknown-model-v1", "openai") is False
        
        # Claude 未知模型，假设支持（保守策略）
        assert supports_function_calling("claude-4-unknown", "claude") is True
        
        # Google 未知模型，假设支持（保守策略）
        assert supports_function_calling("gemini-2.0-unknown", "google") is True
    
    def test_get_model_capability(self):
        """测试获取模型能力配置"""
        # GPT-4
        cap = get_model_capability("gpt-4", "openai")
        assert cap.supports_function_calling is True
        assert cap.supports_streaming is True
        assert cap.supports_images is True
        assert cap.function_calling_format == "openai"
        assert cap.max_context_tokens == 128000
        
        # GLM-5
        cap = get_model_capability("glm-5", "openai_compat")
        assert cap.supports_function_calling is True
        assert cap.max_tokens == 8192
        assert cap.notes == "智谱 GLM-5，Native FC 错误率 ~5%"
        
        # Claude 3.5 Sonnet
        cap = get_model_capability("claude-3.5-sonnet", "claude")
        assert cap.supports_function_calling is True
        assert cap.function_calling_format == "claude"
        assert cap.max_context_tokens == 200000
        
        # Gemini 1.5 Pro
        cap = get_model_capability("gemini-1.5-pro", "google")
        assert cap.supports_function_calling is True
        assert cap.supports_video is True
        assert cap.function_calling_format == "google"
        assert cap.max_context_tokens == 1000000
        
        # Moonshot（不支持 FC）
        cap = get_model_capability("moonshot-v1-128k", "openai_compat")
        assert cap.supports_function_calling is False
        assert cap.max_context_tokens == 128000
    
    def test_get_supported_models(self):
        """测试获取支持 FC 的模型列表"""
        # 所有支持 FC 的模型
        all_models = ModelCapabilityRegistry.get_supported_models()
        assert len(all_models) > 0
        assert "gpt-4" in all_models
        assert "glm-5" in all_models
        assert "claude-3.5-sonnet" in all_models
        assert "gemini-1.5-pro" in all_models
        assert "moonshot-v1-128k" not in all_models
        
        # 仅 OpenAI 格式
        openai_models = ModelCapabilityRegistry.get_supported_models("openai")
        assert "gpt-4" in openai_models
        assert "glm-5" in openai_models
        assert "deepseek-chat" in openai_models
        assert "claude-3.5-sonnet" not in openai_models
        
        # 仅 Claude 格式
        claude_models = ModelCapabilityRegistry.get_supported_models("claude")
        assert "claude-3.5-sonnet" in claude_models
        assert "claude-3-opus" in claude_models
        assert "gpt-4" not in claude_models
        
        # 仅 Google 格式
        google_models = ModelCapabilityRegistry.get_supported_models("google")
        assert "gemini-1.5-pro" in google_models
        assert "gemini-pro" in google_models
        assert "gpt-4" not in google_models
    
    def test_list_all_capabilities(self):
        """测试列出所有模型能力"""
        all_caps = ModelCapabilityRegistry.list_all_capabilities()
        assert isinstance(all_caps, dict)
        assert len(all_caps) > 20  # 至少有 20+ 个模型
        
        # 验证数据结构
        assert "gpt-4" in all_caps
        gpt4_cap = all_caps["gpt-4"]
        assert "supports_function_calling" in gpt4_cap
        assert "supports_streaming" in gpt4_cap
        assert "max_tokens" in gpt4_cap
        assert "function_calling_format" in gpt4_cap
        assert gpt4_cap["supports_function_calling"] is True
        assert gpt4_cap["function_calling_format"] == "openai"


class TestToolCallStrategySelector:
    """测试工具调用策略选择器（集成测试）"""
    
    def test_strategy_selector_uses_model_registry(self):
        """测试策略选择器使用模型能力注册表"""
        from opensquad.tool_call_strategy import ToolCallStrategySelector
        
        # 测试 GPT-4（支持）
        result = ToolCallStrategySelector._supports_function_calling("openai", "gpt-4")
        assert result is True
        
        # 测试 GLM-5（支持）
        result = ToolCallStrategySelector._supports_function_calling("openai_compat", "glm-5")
        assert result is True
        
        # 测试 Claude 3.5（支持）
        result = ToolCallStrategySelector._supports_function_calling("claude", "claude-3.5-sonnet")
        assert result is True
        
        # 测试 Gemini（支持）
        result = ToolCallStrategySelector._supports_function_calling("google", "gemini-1.5-pro")
        assert result is True
        
        # 测试 Moonshot（不支持）
        result = ToolCallStrategySelector._supports_function_calling("openai_compat", "moonshot-v1-128k")
        assert result is False
    
    def test_provider_name_normalization(self):
        """测试 provider 名称标准化"""
        from opensquad.tool_call_strategy import ToolCallStrategySelector
        
        # openai_compat → openai
        result = ToolCallStrategySelector._supports_function_calling("openai_compat", "gpt-4")
        assert result is True
        
        # anthropic → claude
        result = ToolCallStrategySelector._supports_function_calling("anthropic", "claude-3-sonnet")
        assert result is True
        
        # gemini → google
        result = ToolCallStrategySelector._supports_function_calling("gemini", "gemini-pro")
        assert result is True


class TestModelCapabilityEdgeCases:
    """测试边缘情况和特殊场景"""
    
    def test_case_insensitivity(self):
        """测试大小写不敏感"""
        assert supports_function_calling("GPT-4", "openai") is True
        assert supports_function_calling("Gpt-4", "openai") is True
        assert supports_function_calling("GLM-5", "openai_compat") is True
        assert supports_function_calling("CLAUDE-3.5-SONNET", "claude") is True
    
    def test_model_with_extra_suffixes(self):
        """测试带额外后缀的模型名称"""
        # GPT-4 with timestamps
        assert supports_function_calling("gpt-4-0613", "openai") is True
        assert supports_function_calling("gpt-4-32k-0613", "openai") is True
        
        # GLM with variants
        assert supports_function_calling("glm-4-air", "openai_compat") is True
        assert supports_function_calling("glm-4-airx", "openai_compat") is True
        
        # DeepSeek variants
        assert supports_function_calling("deepseek-chat-v2", "openai_compat") is True
        assert supports_function_calling("deepseek-coder-33b", "openai_compat") is True
    
    def test_multimodal_capabilities(self):
        """测试多模态能力标记"""
        # GPT-4o 支持图片和音频
        cap = get_model_capability("gpt-4o", "openai")
        assert cap.supports_images is True
        assert cap.supports_audio is True
        assert cap.supports_video is False
        
        # GLM-4V 支持图片和视频
        cap = get_model_capability("glm-4v", "openai_compat")
        assert cap.supports_images is True
        assert cap.supports_video is True
        
        # Gemini 1.5 Pro 支持图片和视频
        cap = get_model_capability("gemini-1.5-pro", "google")
        assert cap.supports_images is True
        assert cap.supports_video is True
        
        # Claude 3.5 仅支持图片
        cap = get_model_capability("claude-3.5-sonnet", "claude")
        assert cap.supports_images is True
        assert cap.supports_audio is False
        assert cap.supports_video is False
    
    def test_token_limits(self):
        """测试 token 限制配置"""
        # GPT-4: 128K context
        cap = get_model_capability("gpt-4", "openai")
        assert cap.max_context_tokens == 128000
        assert cap.max_tokens == 4096
        
        # GLM-5: 128K context, 8K output
        cap = get_model_capability("glm-5", "openai_compat")
        assert cap.max_context_tokens == 128000
        assert cap.max_tokens == 8192
        
        # Claude 3.5: 200K context
        cap = get_model_capability("claude-3.5-sonnet", "claude")
        assert cap.max_context_tokens == 200000
        
        # Gemini 1.5 Pro: 1M context!
        cap = get_model_capability("gemini-1.5-pro", "google")
        assert cap.max_context_tokens == 1000000


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
