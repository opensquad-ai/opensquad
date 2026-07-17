"""Unit tests for ChatAPI OpenAI-compatible image generation path."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.fixture
def image_chat(tmp_path):
    from opensquad.chat_api import ChatAPI

    chat = ChatAPI(
        api_key="test-key",
        model="step-image-edit-2",
        base_url="https://api.stepfun.com/v1",
        prompt="image bot",
        is_image_output=True,
        is_img_model=True,
        image_size="1024x1024",
        image_steps=8,
        image_cfg_scale=1.0,
    )
    chat.output_media_dir = str(tmp_path)
    return chat


@pytest.mark.asyncio
async def test_image_output_calls_images_generate(image_chat, tmp_path):
    # 1x1 PNG
    tiny_png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00"
        b"\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    import base64

    b64 = base64.b64encode(tiny_png).decode("ascii")
    fake_result = SimpleNamespace(data=[SimpleNamespace(b64_json=b64, url=None)])

    image_chat.client = MagicMock()
    image_chat.client.images = MagicMock()
    image_chat.client.images.generate = AsyncMock(return_value=fake_result)

    result = await image_chat.chat("a red apple")

    image_chat.client.images.generate.assert_awaited_once()
    kwargs = image_chat.client.images.generate.await_args.kwargs
    assert kwargs["model"] == "step-image-edit-2"
    assert kwargs["prompt"] == "a red apple"
    assert kwargs["response_format"] == "b64_json"
    assert result["finish_reason"] == "stop"
    assert result["tool_data"] is None
    assert len(result["output_media"]) == 1
    assert result["output_media"][0]["type"] == "image"
    assert result["output_media"][0]["url"].startswith("/uploads/")
    assert "已根据你的描述生成图片" in result["text"]


@pytest.mark.asyncio
async def test_image_output_empty_prompt(image_chat):
    image_chat.client = MagicMock()
    image_chat.client.images = MagicMock()
    image_chat.client.images.generate = AsyncMock()

    result = await image_chat.chat("   ")
    image_chat.client.images.generate.assert_not_awaited()
    assert result["output_media"] == []
    assert "请描述" in result["text"]
