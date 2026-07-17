"""Unit tests for StepFun audio helpers and tool conversion."""

from opensquad.audio import http_base_url, ws_realtime_url
from opensquad.audio.realtime_bridge import openai_tools_to_stepfun


def test_openai_tools_to_stepfun_limit():
    tools = [
        {
            "type": "function",
            "function": {
                "name": f"filesystem.read_{i}",
                "description": "read",
                "parameters": {"type": "object", "properties": {}},
            },
        }
        for i in range(40)
    ]
    out = openai_tools_to_stepfun(tools, limit=32)
    assert len(out) == 32
    assert out[0]["type"] == "function"
    assert out[0]["function"]["name"] == "filesystem.read_0"


def test_ws_realtime_url():
    card = {"base_url": "https://api.stepfun.com/v1", "model_name": "stepaudio-2.5-realtime"}
    assert ws_realtime_url(card) == "wss://api.stepfun.com/v1/realtime?model=stepaudio-2.5-realtime"
    assert http_base_url(card) == "https://api.stepfun.com/v1"


def test_force_text_only_modalities():
    from opensquad.chat_api import ChatAPI

    chat = ChatAPI(api_key="x", model="stepaudio-2.5-chat", base_url="https://api.stepfun.com/v1", prompt="hi")
    assert chat._force_text_only_modalities() is True
    chat.model = "gpt-4o"
    assert chat._force_text_only_modalities() is False
