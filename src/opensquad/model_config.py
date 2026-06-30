"""ModelConfig — Unified dataclass for LLM client parameters (P2-1).

Consolidates the 20+ scattered parameters from ChatAPI, ClaudeAPI, and GoogleAPI
into a single typed dataclass. This eliminates repetitive parameter passing in
agents_boot.py and provides a foundation for future parameter validation.

Usage:
    from opensquad.model_config import ModelConfig

    cfg = ModelConfig.from_dict(config.get("model", {}), system_prompt="...")
    chat_api = ChatAPI(config=cfg)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ModelConfig:
    """Configuration for LLM API clients (OpenAI-compatible, Claude, Google Gemini)."""

    # ── Required ──
    api_key: str = ""
    model: str = ""
    base_url: str = ""
    prompt: str = ""  # System prompt / template

    # ── Common optional ──
    timeout: float = 120.0
    token_max: int = 100_000
    temperature: float = 0.3
    reduction_strategy: str = "start"
    reduction_batch_size: int = 2
    load_his: str | None = None

    # ── Media / multimodal ──
    is_img_model: bool = False
    is_audio_model: bool = False
    is_video_model: bool = False
    use_file_api: bool = False
    file_api_size_threshold: int = 4 * 1024 * 1024  # 4 MB

    # ── OpenAI-specific ──
    is_audio_output: bool = False
    audio_output_voice: str = "alloy"
    frequency_penalty: float = 0.0
    presence_penalty: float = 0.0
    enable_repetition_check: bool = False

    # ── Claude-specific ──
    max_video_frames: int = 8
    is_think: bool = False
    thinking_budget_tokens: int = 10_000

    # ── Google-specific ──
    is_image_output: bool = False

    # ── Shared ──
    top_k: int = 0

    # ── Provider hint (set by caller, not part of config.json) ──
    provider: str = "openai"

    @classmethod
    def from_dict(
        cls,
        model_cfg: dict[str, Any],
        prompt: str = "",
        provider: str = "openai",
    ) -> ModelConfig:
        """Build ModelConfig from config.json's ``model`` section.

        Args:
            model_cfg: Raw dict from ``config.json`` under the ``model`` key.
            prompt: The rendered system prompt string.
            provider: Detected provider (``openai``, ``claude``, ``google``).

        Returns:
            Populated ``ModelConfig`` instance with sensible defaults.
        """

        # Helper: safely get with default
        def _get(key: str, default: Any) -> Any:
            return model_cfg.get(key, default)

        return cls(
            api_key=_get("api_key", ""),
            model=_get("model_name", ""),
            base_url=_get("base_url", ""),
            prompt=prompt,
            timeout=float(_get("timeout", 120.0)),
            token_max=int(_get("token_max", 100_000)),
            temperature=float(_get("temperature", 0.3)),
            reduction_strategy=_get("reduction_strategy", "start"),
            reduction_batch_size=int(_get("reduction_batch_size", 2)),
            load_his=_get("load_his", None),
            is_img_model=_get("is_image", False),
            is_audio_model=_get("is_audio_model", False),
            is_video_model=_get("is_video", False),
            use_file_api=_get("use_file_api", False),
            file_api_size_threshold=int(_get("file_api_size_threshold", 4 * 1024 * 1024)),
            # OpenAI-specific
            is_audio_output=_get("is_audio_output", False),
            audio_output_voice=_get("audio_output_voice", "alloy"),
            frequency_penalty=float(_get("frequency_penalty", 0.0)),
            presence_penalty=float(_get("presence_penalty", 0.0)),
            enable_repetition_check=_get("enable_repetition_check", False),
            # Claude-specific
            max_video_frames=min(int(_get("max_video_frames", 8)), 20),
            is_think=_get("is_think", False),
            thinking_budget_tokens=int(_get("thinking_budget_tokens", 10_000)),
            # Google-specific
            is_image_output=_get("is_image_output", False),
            # Shared
            top_k=int(_get("top_k", 0)),
            provider=provider,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize to plain dict (useful for passing to delegate tools)."""
        return {
            "provider": self.provider,
            "api_key": self.api_key,
            "base_url": self.base_url,
            "model": self.model,
            "token_max": self.token_max,
            "temperature": self.temperature,
            "timeout": self.timeout,
            "is_img_model": self.is_img_model,
            "is_audio_model": self.is_audio_model,
            "is_video_model": self.is_video_model,
            "use_file_api": self.use_file_api,
            "file_api_size_threshold": self.file_api_size_threshold,
            "tool_call_mode": "auto",  # default for delegate
            "tool_filter": "all",  # default for delegate
        }
