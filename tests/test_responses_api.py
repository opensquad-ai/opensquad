"""Tests for the OpenAI Responses transport (``api_protocol: openai_responses``).

Covers:
* the request translation (messages -> instructions + input items, flat tools);
* the event translation (typed Responses events -> Chat-Completions chunks);
* the protocol wiring (config schema, provider resolution, factory selection);
* an end-to-end ``chat()`` turn reusing ChatAPI's loop over the Responses stream.
"""

from types import SimpleNamespace

import pytest


# ─────────────────────────────────────────────────────────────────────────────
# Pure translation helpers
# ─────────────────────────────────────────────────────────────────────────────
def test_input_from_messages_translates_tool_roundtrip():
    from opensquad.responses_api import _input_from_messages

    messages = [
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "weather in Paris?"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "get_weather", "arguments": '{"city":"Paris"}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "name": "get_weather", "content": "18C"},
    ]

    instructions, items = _input_from_messages(messages)

    assert instructions == "You are helpful."
    assert items[0] == {"role": "user", "content": "weather in Paris?"}
    assert items[1] == {
        "type": "function_call",
        "call_id": "call_1",
        "name": "get_weather",
        "arguments": '{"city":"Paris"}',
    }
    assert items[2] == {"type": "function_call_output", "call_id": "call_1", "output": "18C"}


def test_content_parts_translate_text_and_image():
    from opensquad.responses_api import _content_parts

    parts = _content_parts(
        [
            {"type": "text", "text": "look"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
        ]
    )
    assert parts == [
        {"type": "input_text", "text": "look"},
        {"type": "input_image", "image_url": "data:image/png;base64,AAAA"},
    ]


def test_tool_schema_is_flattened():
    from opensquad.responses_api import _tool_schema

    chat_tool = {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
        },
    }
    assert _tool_schema(chat_tool) == {
        "type": "function",
        "name": "read_file",
        "description": "Read a file",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
        "strict": False,
    }


def test_to_responses_params_basic():
    from opensquad.responses_api import ResponsesAPI

    api = ResponsesAPI(api_key="k", model="gpt-5", base_url="https://api.openai.com/v1", prompt="sys")
    params = {
        "model": "gpt-5",
        "messages": [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi"},
        ],
        "temperature": 0.3,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    out = api._to_responses_params(params)
    assert out["model"] == "gpt-5"
    assert out["instructions"] == "sys"
    assert out["input"] == [{"role": "user", "content": "hi"}]
    assert out["stream"] is True
    assert out["store"] is False
    assert out["temperature"] == 0.3
    assert "stream_options" not in out  # Chat-Completions-only field is dropped


def test_to_responses_params_drops_temperature_when_thinking():
    from opensquad.responses_api import ResponsesAPI

    api = ResponsesAPI(
        api_key="k",
        model="o3",
        base_url="https://api.openai.com/v1",
        prompt="sys",
        is_think=True,
    )
    out = api._to_responses_params({"model": "o3", "messages": [{"role": "user", "content": "hi"}], "temperature": 0})
    assert "temperature" not in out
    assert out["reasoning"] == {"effort": "high"}


# ─────────────────────────────────────────────────────────────────────────────
# Event stream translation
# ─────────────────────────────────────────────────────────────────────────────
def _event(**kwargs):
    return SimpleNamespace(**kwargs)


async def _collect(api, params):
    chunks = []
    async for chunk in api._responses_chunk_iter(params):
        chunks.append(chunk)
    return chunks


async def test_chunk_iter_translates_text_and_usage():
    from opensquad.responses_api import ResponsesAPI

    api = ResponsesAPI(api_key="k", model="gpt-5", base_url="https://api.openai.com/v1", prompt="sys")

    usage = SimpleNamespace(input_tokens=10, output_tokens=4, input_tokens_details=SimpleNamespace(cached_tokens=3))
    events = [
        _event(type="response.created"),
        _event(type="response.output_text.delta", delta="Hel"),
        _event(type="response.output_text.delta", delta="lo"),
        _event(type="response.completed", response=SimpleNamespace(status="completed", usage=usage, output=[])),
    ]

    class _Responses:
        async def create(self, **kwargs):
            self.kwargs = kwargs

            async def _gen():
                for e in events:
                    yield e

            return _gen()

    class _Real:
        def __init__(self):
            self.responses = _Responses()

    api._real_client = _Real()

    chunks = await _collect(api, {"model": "gpt-5", "messages": [{"role": "user", "content": "hi"}]})

    texts = [c.choices[0].delta.content for c in chunks if c.choices[0].delta.content]
    assert texts == ["Hel", "lo"]
    final = chunks[-1]
    assert final.choices[0].finish_reason == "stop"
    assert final.usage.prompt_tokens == 10
    assert final.usage.completion_tokens == 4
    assert final.usage.prompt_tokens_details.cached_tokens == 3


async def test_chunk_iter_translates_function_call():
    from opensquad.responses_api import ResponsesAPI

    api = ResponsesAPI(api_key="k", model="gpt-5", base_url="https://api.openai.com/v1", prompt="sys")

    events = [
        _event(
            type="response.output_item.added",
            item=SimpleNamespace(type="function_call", id="fc_1", call_id="call_1", name="get_weather"),
        ),
        _event(type="response.function_call_arguments.delta", item_id="fc_1", delta='{"city":'),
        _event(type="response.function_call_arguments.delta", item_id="fc_1", delta='"Paris"}'),
        _event(type="response.completed", response=SimpleNamespace(status="completed", usage=None, output=[])),
    ]

    class _Responses:
        async def create(self, **kwargs):
            async def _gen():
                for e in events:
                    yield e

            return _gen()

    class _Real:
        def __init__(self):
            self.responses = _Responses()

    api._real_client = _Real()

    chunks = await _collect(api, {"model": "gpt-5", "messages": [{"role": "user", "content": "hi"}]})

    tool_chunks = [c for c in chunks if c.choices[0].delta.tool_calls]
    assert tool_chunks[0].choices[0].delta.tool_calls[0].function.name == "get_weather"
    args = "".join(tc.choices[0].delta.tool_calls[0].function.arguments or "" for tc in tool_chunks)
    assert args == '{"city":"Paris"}'
    assert chunks[-1].choices[0].finish_reason == "tool_calls"


async def test_chunk_iter_retries_without_store_when_rejected():
    from opensquad.responses_api import ResponsesAPI

    api = ResponsesAPI(api_key="k", model="gpt-5", base_url="https://example.com/v1", prompt="sys")
    calls = []

    class _Responses:
        async def create(self, **kwargs):
            calls.append(kwargs)
            if "store" in kwargs:
                raise TypeError("Unsupported parameter: 'store' is not supported with this model.")

            async def _gen():
                yield _event(type="response.output_text.delta", delta="ok")
                yield _event(
                    type="response.completed", response=SimpleNamespace(status="completed", usage=None, output=[])
                )

            return _gen()

    class _Real:
        def __init__(self):
            self.responses = _Responses()

    api._real_client = _Real()

    chunks = await _collect(api, {"model": "gpt-5", "messages": [{"role": "user", "content": "hi"}]})

    assert [c.choices[0].delta.content for c in chunks if c.choices[0].delta.content] == ["ok"]
    assert "store" in calls[0]
    assert "store" not in calls[1]


async def test_chunk_iter_raises_on_failed_response():
    from opensquad.responses_api import ResponsesAPI

    api = ResponsesAPI(api_key="k", model="gpt-5", base_url="https://api.openai.com/v1", prompt="sys")
    events = [
        _event(type="response.failed", response=SimpleNamespace(error=SimpleNamespace(message="boom"))),
    ]

    class _Responses:
        async def create(self, **kwargs):
            async def _gen():
                for e in events:
                    yield e

            return _gen()

    class _Real:
        def __init__(self):
            self.responses = _Responses()

    api._real_client = _Real()

    with pytest.raises(RuntimeError, match="boom"):
        await _collect(api, {"model": "gpt-5", "messages": [{"role": "user", "content": "hi"}]})


# ─────────────────────────────────────────────────────────────────────────────
# Protocol wiring
# ─────────────────────────────────────────────────────────────────────────────
def test_config_schema_accepts_responses_protocols():
    from opensquad.config_schema import validate_agent_config

    for proto in ("openai_responses", "responses"):
        result = validate_agent_config(
            {
                "agent_id": "a",
                "agent_name": "A",
                "model": {"api_protocol": proto, "model_name": "gpt-5", "base_url": "https://api.openai.com/v1"},
            }
        )
        assert result["model"]["api_protocol"] == proto


def test_resolve_provider_passthrough():
    from opensquad.agents_boot import resolve_provider

    assert resolve_provider({"api_protocol": "openai_responses", "model_name": "gpt-5"}) == "openai_responses"


def test_factory_selects_responses_api():
    from opensquad.agents_boot import create_chat_api_from_config
    from opensquad.responses_api import ResponsesAPI
    from opensquad.xml_parser import StreamingTagParser

    api = create_chat_api_from_config(
        {
            "api_protocol": "openai_responses",
            "api_key": "k",
            "base_url": "https://api.openai.com/v1",
            "model_name": "gpt-5",
        },
        "sys",
        StreamingTagParser({}),
    )
    assert isinstance(api, ResponsesAPI)


def test_native_fc_is_implemented_for_responses():
    from opensquad.tool_call_strategy import ToolCallStrategySelector

    assert ToolCallStrategySelector._is_native_fc_implemented("openai_responses") is True


# ─────────────────────────────────────────────────────────────────────────────
# End-to-end turn: ChatAPI's loop driven over the Responses transport
# ─────────────────────────────────────────────────────────────────────────────
class _FakeHttpx:
    def __init__(self):
        self.is_closed = False


class _FakeEncoding:
    def encode(self, text):
        return list(range(max(1, len(str(text).split()))))


def _install_fakes(monkeypatch, events):
    import opensquad.chat_api as chat_api_module
    from opensquad.responses_api import ResponsesAPI

    class _Responses:
        async def create(self, **kwargs):
            self.kwargs = kwargs

            async def _gen():
                for e in events:
                    yield e

            return _gen()

    class _FakeAsyncOpenAI:
        def __init__(self, **kwargs):
            self.responses = _Responses()
            self.client = _FakeHttpx()

        async def close(self):
            pass

    class _FakeTiktoken:
        def encoding_for_model(self, model):
            return _FakeEncoding()

        def get_encoding(self, name):
            return _FakeEncoding()

    class _FakeSessionManager:
        def add_message(self, *a, **k):
            pass

        def sync_tool_call_message(self, *a, **k):
            pass

        def add_event(self, *a, **k):
            pass

    monkeypatch.setattr(chat_api_module, "_get_async_openai", lambda: _FakeAsyncOpenAI)
    monkeypatch.setattr(chat_api_module, "_make_llm_http_client", lambda timeout: object())
    monkeypatch.setattr(chat_api_module, "_get_tiktoken", lambda: _FakeTiktoken())
    monkeypatch.setattr(chat_api_module._session_module, "get_session_manager", lambda: _FakeSessionManager())

    return ResponsesAPI(api_key="k", model="gpt-5", base_url="https://api.openai.com/v1", prompt="sys")


async def test_chat_end_to_end_text(monkeypatch):
    usage = SimpleNamespace(input_tokens=12, output_tokens=3, input_tokens_details=SimpleNamespace(cached_tokens=5))
    events = [
        _event(type="response.output_text.delta", delta="Hello there"),
        _event(type="response.completed", response=SimpleNamespace(status="completed", usage=usage, output=[])),
    ]
    api = _install_fakes(monkeypatch, events)

    result = await api.chat("hi")

    assert result["text"] == "Hello there"
    assert result["tool_data"] is None
    assert result["finish_reason"] == "stop"
    assert result["stream_error"] is False
    # Counters picked up the mapped usage, including the cached-token split.
    assert api.total_input_tokens == 12
    assert api.total_output_tokens == 3
    assert api.total_cache_read_tokens == 5


async def test_chat_end_to_end_native_tool_call(monkeypatch):
    from opensquad.tool_call_strategy import NativeToolCallStrategy

    events = [
        _event(
            type="response.output_item.added",
            item=SimpleNamespace(type="function_call", id="fc_1", call_id="call_1", name="get_weather"),
        ),
        _event(type="response.function_call_arguments.delta", item_id="fc_1", delta='{"city":"Paris"}'),
        _event(type="response.completed", response=SimpleNamespace(status="completed", usage=None, output=[])),
    ]
    api = _install_fakes(monkeypatch, events)
    strategy = NativeToolCallStrategy(tool_registry=object())

    result = await api.chat(
        "weather?",
        tools=[{"type": "function", "function": {"name": "get_weather", "parameters": {"type": "object"}}}],
        tool_call_strategy=strategy,
    )

    assert result["tool_data"] == [("get_weather", {"city": "Paris"})]
    assert result["finish_reason"] == "tool_calls"
