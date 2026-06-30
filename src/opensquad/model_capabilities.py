"""
Model Capabilities Registry
Manages capability profiles for different AI models, including Native Function Calling support.
"""

import functools
import re
from dataclasses import dataclass


@dataclass
class ModelCapability:
    """Model capability configuration."""

    supports_function_calling: bool = False
    supports_streaming: bool = True
    supports_images: bool = False
    supports_audio: bool = False
    supports_video: bool = False
    supports_system_prompt: bool = True
    max_tokens: int = 4096
    max_context_tokens: int = 128000
    function_calling_format: str = "openai"  # "openai" | "claude" | "google"
    notes: str = ""


class ModelCapabilityRegistry:
    """
    Model capability registry.

    Returns the capability configuration for a model given its name and provider.
    """

    # Model capability database
    CAPABILITIES: dict[str, ModelCapability] = {
        # OpenAI Models
        "gpt-4": ModelCapability(
            supports_function_calling=True,
            supports_images=True,
            max_tokens=4096,
            max_context_tokens=128000,
            function_calling_format="openai",
            notes="OpenAI GPT-4 series, stable Function Calling support",
        ),
        "gpt-4-turbo": ModelCapability(
            supports_function_calling=True,
            supports_images=True,
            max_tokens=4096,
            max_context_tokens=128000,
            function_calling_format="openai",
            notes="GPT-4 Turbo, faster response speed",
        ),
        "gpt-4o": ModelCapability(
            supports_function_calling=True,
            supports_images=True,
            supports_audio=True,
            max_tokens=16384,
            max_context_tokens=128000,
            function_calling_format="openai",
            notes="GPT-4o, enhanced multimodal capabilities",
        ),
        "gpt-3.5-turbo": ModelCapability(
            supports_function_calling=True,
            max_tokens=4096,
            max_context_tokens=16385,
            function_calling_format="openai",
            notes="GPT-3.5 Turbo, economical and practical",
        ),
        # GLM (Zhipu AI) Models
        "glm-4": ModelCapability(
            supports_function_calling=True,
            supports_images=True,
            max_tokens=4096,
            max_context_tokens=128000,
            function_calling_format="openai",
            notes="Zhipu GLM-4, supports Function Calling",
        ),
        "glm-5": ModelCapability(
            supports_function_calling=True,
            supports_images=True,
            max_tokens=8192,
            max_context_tokens=128000,
            function_calling_format="openai",
            notes="Zhipu GLM-5, Native FC error rate ~5%",
        ),
        "glm-4v": ModelCapability(
            supports_function_calling=True,
            supports_images=True,
            supports_video=True,
            max_tokens=4096,
            max_context_tokens=128000,
            function_calling_format="openai",
            notes="GLM-4V, multimodal visual understanding",
        ),
        # DeepSeek Models
        "deepseek-chat": ModelCapability(
            supports_function_calling=True,
            max_tokens=4096,
            max_context_tokens=64000,
            function_calling_format="openai",
            notes="DeepSeek Chat, great value",
        ),
        "deepseek-coder": ModelCapability(
            supports_function_calling=True,
            max_tokens=4096,
            max_context_tokens=64000,
            function_calling_format="openai",
            notes="DeepSeek Coder, code-specialized",
        ),
        "deepseek-v2": ModelCapability(
            supports_function_calling=True,
            max_tokens=8192,
            max_context_tokens=128000,
            function_calling_format="openai",
            notes="DeepSeek V2, improved performance",
        ),
        "deepseek-v3": ModelCapability(
            supports_function_calling=True,
            max_tokens=8192,
            max_context_tokens=128000,
            function_calling_format="openai",
            notes="DeepSeek V3, latest version",
        ),
        # Anthropic Claude Models
        "claude-2": ModelCapability(
            supports_function_calling=True,
            supports_images=False,
            max_tokens=4096,
            max_context_tokens=100000,
            function_calling_format="claude",
            notes="Claude 2, long context support",
        ),
        "claude-3-sonnet": ModelCapability(
            supports_function_calling=True,
            supports_images=True,
            max_tokens=4096,
            max_context_tokens=200000,
            function_calling_format="claude",
            notes="Claude 3 Sonnet, balanced performance",
        ),
        "claude-3-opus": ModelCapability(
            supports_function_calling=True,
            supports_images=True,
            max_tokens=4096,
            max_context_tokens=200000,
            function_calling_format="claude",
            notes="Claude 3 Opus, highest performance",
        ),
        "claude-3-haiku": ModelCapability(
            supports_function_calling=True,
            supports_images=True,
            max_tokens=4096,
            max_context_tokens=200000,
            function_calling_format="claude",
            notes="Claude 3 Haiku, fast response",
        ),
        "claude-3.5-sonnet": ModelCapability(
            supports_function_calling=True,
            supports_images=True,
            max_tokens=8192,
            max_context_tokens=200000,
            function_calling_format="claude",
            notes="Claude 3.5 Sonnet, upgraded performance",
        ),
        # Google Gemini Models
        "gemini-pro": ModelCapability(
            supports_function_calling=True,
            max_tokens=8192,
            max_context_tokens=32768,
            function_calling_format="google",
            notes="Gemini Pro, Google standard model",
        ),
        "gemini-1.5-pro": ModelCapability(
            supports_function_calling=True,
            supports_images=True,
            supports_video=True,
            max_tokens=8192,
            max_context_tokens=1000000,
            function_calling_format="google",
            notes="Gemini 1.5 Pro, million-token context",
        ),
        "gemini-1.5-flash": ModelCapability(
            supports_function_calling=True,
            supports_images=True,
            supports_video=True,
            max_tokens=8192,
            max_context_tokens=1000000,
            function_calling_format="google",
            notes="Gemini 1.5 Flash, fast inference",
        ),
        # Qwen (Tongyi Qianwen) Models
        "qwen-plus": ModelCapability(
            supports_function_calling=True,
            max_tokens=6144,
            max_context_tokens=32768,
            function_calling_format="openai",
            notes="Qwen Plus",
        ),
        "qwen-max": ModelCapability(
            supports_function_calling=True,
            max_tokens=6144,
            max_context_tokens=32768,
            function_calling_format="openai",
            notes="Qwen Max, strongest version",
        ),
        "qwen-turbo": ModelCapability(
            supports_function_calling=True,
            max_tokens=6144,
            max_context_tokens=32768,
            function_calling_format="openai",
            notes="Qwen Turbo, fast inference",
        ),
        "qwen-vl-plus": ModelCapability(
            supports_function_calling=True,
            supports_images=True,
            max_tokens=6144,
            max_context_tokens=32768,
            function_calling_format="openai",
            notes="Qwen VL Plus, visual understanding",
        ),
        # Moonshot Models
        "moonshot-v1-8k": ModelCapability(
            supports_function_calling=False,
            max_tokens=4096,
            max_context_tokens=8000,
            function_calling_format="openai",
            notes="Moonshot 8K context",
        ),
        "moonshot-v1-32k": ModelCapability(
            supports_function_calling=False,
            max_tokens=4096,
            max_context_tokens=32000,
            function_calling_format="openai",
            notes="Moonshot 32K context",
        ),
        "moonshot-v1-128k": ModelCapability(
            supports_function_calling=False,
            max_tokens=4096,
            max_context_tokens=128000,
            function_calling_format="openai",
            notes="Moonshot 128K context",
        ),
        # Baichuan Models
        "baichuan2-turbo": ModelCapability(
            supports_function_calling=False,
            max_tokens=4096,
            max_context_tokens=32768,
            function_calling_format="openai",
            notes="Baichuan 2 Turbo",
        ),
        # MiniMax Models
        "abab5.5-chat": ModelCapability(
            supports_function_calling=False,
            max_tokens=4096,
            max_context_tokens=16384,
            function_calling_format="openai",
            notes="MiniMax abab5.5",
        ),
        # Kimi (Moonshot) Models
        "kimi-k2.5": ModelCapability(
            supports_function_calling=True,
            supports_images=True,
            max_tokens=8192,
            max_context_tokens=262144,
            function_calling_format="openai",
            notes="Kimi K2.5, supports Native Function Calling via OpenAI-compatible API",
        ),
        "kimi-k2": ModelCapability(
            supports_function_calling=True,
            supports_images=True,
            max_tokens=8192,
            max_context_tokens=262144,
            function_calling_format="openai",
            notes="Kimi K2, supports Native Function Calling via OpenAI-compatible API",
        ),
    }

    @classmethod
    @functools.lru_cache(maxsize=128)
    def get_capability(cls, model_name: str, provider: str = "openai") -> ModelCapability:
        """
        Get the capability configuration for a model.

        Args:
            model_name: Model name (e.g. "gpt-4", "glm-5")
            provider: API provider (e.g. "openai", "claude", "google")

        Returns:
            ModelCapability object

        Example:
            >>> cap = ModelCapabilityRegistry.get_capability("gpt-4")
            >>> cap.supports_function_calling
            True
        """
        model_name_lower = model_name.lower()

        # Exact match
        if model_name_lower in cls.CAPABILITIES:
            return cls.CAPABILITIES[model_name_lower]

        # Fuzzy match (handles version suffix variants)
        for key, capability in cls.CAPABILITIES.items():
            if cls._fuzzy_match(model_name_lower, key):
                return capability

        # Unknown model, return a conservative default configuration
        return cls._get_default_capability(provider)

    @classmethod
    def _fuzzy_match(cls, model_name: str, key: str) -> bool:
        """Fuzzy-match a model name against a registry key."""
        # Match after stripping version suffixes
        # e.g. "gpt-4-0125-preview" matches "gpt-4"
        patterns = [
            r"^gpt-4",
            r"^gpt-3\.5",
            r"^glm-[45]",
            r"^claude-[23]",
            r"^claude-3\.5",
            r"^gemini",
            r"^deepseek",
            r"^qwen",
            r"^moonshot",
            r"^kimi",
        ]

        return any(re.match(pattern, model_name) and re.match(pattern, key) for pattern in patterns)

    @classmethod
    def _get_default_capability(cls, provider: str) -> ModelCapability:
        """Return the default capability configuration for a provider."""
        if provider in ["claude", "anthropic"]:
            return ModelCapability(
                supports_function_calling=True,
                supports_streaming=True,
                function_calling_format="claude",
                notes="Unknown Claude model, assuming Function Calling support",
            )
        elif provider in ["google", "gemini"]:
            return ModelCapability(
                supports_function_calling=True,
                supports_streaming=True,
                function_calling_format="google",
                notes="Unknown Gemini model, assuming Function Calling support",
            )
        else:
            # OpenAI-compatible APIs default to no Function Calling support
            return ModelCapability(
                supports_function_calling=False,
                supports_streaming=True,
                function_calling_format="openai",
                notes="Unknown model, assuming no Function Calling support (use XML fallback)",
            )

    @classmethod
    def supports_function_calling(cls, model_name: str, provider: str = "openai") -> bool:
        """
        Check whether a model supports Function Calling.

        Args:
            model_name: Model name
            provider: API provider

        Returns:
            True if Function Calling is supported

        Example:
            >>> ModelCapabilityRegistry.supports_function_calling("gpt-4")
            True
            >>> ModelCapabilityRegistry.supports_function_calling("moonshot-v1-8k")
            False
        """
        capability = cls.get_capability(model_name, provider)
        return capability.supports_function_calling

    @classmethod
    def get_supported_models(cls, provider: str | None = None) -> list[str]:
        """
        Get the list of models that support Function Calling.

        Args:
            provider: Optional; filter by provider.

        Returns:
            List of model names

        Example:
            >>> ModelCapabilityRegistry.get_supported_models("openai")
            ['gpt-4', 'gpt-4-turbo', 'gpt-3.5-turbo', ...]
        """
        models = []
        for model_name, capability in cls.CAPABILITIES.items():
            if capability.supports_function_calling:
                if provider is None or capability.function_calling_format == provider:
                    models.append(model_name)
        return sorted(models)

    @classmethod
    def list_all_capabilities(cls) -> dict[str, dict]:
        """
        List all model capabilities (for debugging and documentation generation).

        Returns:
            Dictionary of model capabilities
        """
        result = {}
        for model_name, capability in cls.CAPABILITIES.items():
            result[model_name] = {
                "supports_function_calling": capability.supports_function_calling,
                "supports_streaming": capability.supports_streaming,
                "supports_images": capability.supports_images,
                "supports_audio": capability.supports_audio,
                "supports_video": capability.supports_video,
                "max_tokens": capability.max_tokens,
                "max_context_tokens": capability.max_context_tokens,
                "function_calling_format": capability.function_calling_format,
                "notes": capability.notes,
            }
        return result


# Convenience functions
def supports_function_calling(model_name: str, provider: str = "openai") -> bool:
    """Shorthand: check whether a model supports Function Calling."""
    return ModelCapabilityRegistry.supports_function_calling(model_name, provider)


import functools


@functools.lru_cache(maxsize=128)
def get_model_capability(model_name: str, provider: str = "openai") -> ModelCapability:
    """Shorthand: get the capability configuration for a model.

    Results are cached via lru_cache since the registry is immutable at runtime.
    """
    return ModelCapabilityRegistry.get_capability(model_name, provider)
