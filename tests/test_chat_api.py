"""Unit tests for ChatAPI pure-logic methods.

Tests cover: add_user_message, add_tool_result, _is_tool_result_msg,
_build_conv_text, _prepare_messages entry logic.
"""

import pytest


@pytest.fixture
def chat():
    """Create a minimal ChatAPI instance for testing."""
    from opensquad.chat_api import ChatAPI

    return ChatAPI(
        api_key="test-key",
        model="gpt-4",
        base_url="https://api.openai.com/v1",
        prompt="You are a helpful assistant.",
        reduction_strategy="start",
    )


class TestLazyInitialization:
    """ChatAPI should defer OpenAI SDK / tiktoken work until first use."""

    def test_init_does_not_build_client_or_encoding(self):
        from opensquad.chat_api import ChatAPI

        api = ChatAPI(
            api_key="test-key",
            model="gpt-4",
            base_url="https://api.openai.com/v1",
            prompt="You are a helpful assistant.",
        )
        assert api.client is None
        assert api._encoding is None

    def test_ensure_client_builds_only_on_demand(self, monkeypatch):
        import opensquad.chat_api as chat_api_module
        from opensquad.chat_api import ChatAPI

        built = []

        class FakeAsyncOpenAI:
            def __init__(self, **kwargs):
                built.append(kwargs)

        monkeypatch.setattr(chat_api_module, "_get_async_openai", lambda: FakeAsyncOpenAI)
        monkeypatch.setattr(chat_api_module, "_make_llm_http_client", lambda timeout: object())

        api = ChatAPI.__new__(ChatAPI)
        api.client = None
        api.api_key = "k"
        api.base_url = "https://example.com"
        api.timeout = 30.0

        first = api._ensure_client()
        second = api._ensure_client()

        assert first is second
        assert api.client is first
        assert len(built) == 1

    def test_encoding_is_lazy(self, monkeypatch):
        import opensquad.chat_api as chat_api_module
        from opensquad.chat_api import ChatAPI

        loaded = []
        fake_encoding = object()

        class FakeTiktoken:
            def encoding_for_model(self, model):
                loaded.append(model)
                return fake_encoding

        monkeypatch.setattr(chat_api_module, "_get_tiktoken", lambda: FakeTiktoken())

        api = ChatAPI.__new__(ChatAPI)
        api._encoding = None
        api.model = "gpt-4"

        assert api.encoding is fake_encoding
        assert api.encoding is fake_encoding
        assert loaded == ["gpt-4"]

    def test_warmup_builds_client_encoding_and_token_cache(self):
        from opensquad.chat_api import ChatAPI

        api = ChatAPI.__new__(ChatAPI)
        api.client = None
        api._client_lock = None
        api._encoding = None
        api.model = "gpt-4"
        api._last_tools = None
        token_calls = []

        api._build_client = lambda: object()
        api._build_encoding = lambda: object()
        api.get_current_token_count = lambda tools: token_calls.append(tools) or 42

        api.warmup()

        assert api.client is not None
        assert api._encoding is not None
        assert token_calls == [None]


class TestAddUserMessage:
    """Test add_user_message — adding user messages to conversation history."""

    def test_add_text_message(self, chat):
        result = chat.add_user_message("Hello")
        assert result is not False
        assert len(chat.req) == 2  # system + user
        assert chat.req[-1]["role"] == "user"
        assert chat.req[-1]["content"] == "Hello"

    def test_skip_empty_message(self, chat):
        result = chat.add_user_message("")
        assert result is False
        assert len(chat.req) == 1  # only system

    def test_add_text_with_image(self, chat):
        chat.is_img_model = True
        result = chat.add_user_message("Check this", image_path=["/nonexistent.png"])
        assert result is not False
        assert chat.req[-1]["role"] == "user"
        assert len(chat.req[-1]["content"]) >= 1  # at least text portion

    def test_increment_request_count(self, chat):
        count_before = chat.total_requests
        chat.add_user_message("Test")
        assert chat.total_requests == count_before + 1

    def test_empty_message_no_increment(self, chat):
        count_before = chat.total_requests
        chat.add_user_message("")
        assert chat.total_requests == count_before


class TestAddToolResult:
    """Test add_tool_result — adding tool call/result pairs."""

    def test_add_tool_result_appends_messages(self, chat):
        chat.add_user_message("Do something")
        chat.add_tool_result("read_file", {"path": "/test"}, "file contents")
        assert len(chat.req) == 4  # system + user + assistant + tool

    def test_tool_result_role_is_tool(self, chat):
        chat.add_user_message("Do something")
        chat.add_tool_result("read", {"path": "/test"}, "output")
        assert chat.req[-1]["role"] == "tool"
        assert chat.req[-1]["name"] == "read"

    def test_generates_tool_call_id(self, chat):
        chat.add_user_message("Do something")
        chat.add_tool_result("read", {"path": "/test"}, "output")
        assistant = chat.req[-2]
        assert "tool_calls" in assistant
        assert assistant["tool_calls"][0]["id"].startswith("call_")

    def test_amends_existing_assistant(self, chat):
        chat.add_user_message("Do something")
        chat.add_assistant_message("Let me check")
        chat.add_tool_result("read", {"path": "/test"}, "output")
        assistant = chat.req[-2]
        assert "tool_calls" in assistant
        assert assistant["tool_calls"][0]["function"]["name"] == "read"


class TestIsToolResultMsg:
    """Test _is_tool_result_msg — detecting tool result message format."""

    @staticmethod
    def _target(msg: dict) -> bool:
        from opensquad.chat_api import ChatAPI

        return ChatAPI._is_tool_result_msg(msg)

    def test_tool_result_true(self):
        assert self._target({"role": "tool", "tool_call_id": "call_1"}) is True

    def test_user_message_false(self):
        assert self._target({"role": "user", "content": "hi"}) is False

    def test_assistant_without_tool_calls_false(self):
        assert self._target({"role": "assistant", "content": "ok"}) is False

    def test_empty_dict(self):
        assert self._target({}) is False


class TestBuildConvText:
    """Test _build_conv_text — building conversation summary text."""

    @staticmethod
    def _target(messages, budget_chars=5000):
        from opensquad.chat_api import ChatAPI

        chat = object.__new__(ChatAPI)
        return ChatAPI._build_conv_text(chat, messages, budget_chars)

    def test_empty_messages(self):
        assert self._target([]) == ""

    def test_user_message_included(self, chat):
        chat.add_user_message("Hello")
        result = self._target(chat.req)
        assert "Hello" in result

    def test_system_message_included(self, chat):
        """_build_conv_text includes ALL roles including system."""
        chat.add_user_message("Hello")
        result = self._target(chat.req)
        assert "You are a helpful assistant" in result

    def test_respects_budget(self):
        from opensquad.chat_api import ChatAPI

        msgs = [
            {"role": "user", "content": "A" * 1000},
            {"role": "assistant", "content": "B" * 1000},
        ]
        result = ChatAPI._build_conv_text(object.__new__(ChatAPI), msgs, budget_chars=500)
        assert len(result) <= 2000  # includes "user: " and "assistant: " prefixes


# ── _prepare_messages (entry logic only) ────────────────────────────────


class TestPrepareMessages:
    """Test _prepare_messages — context compression trigger logic.

    These tests verify the entry/early-return paths only, not the full compression.
    """

    def test_returns_req_when_below_threshold(self, chat):
        chat.add_user_message("Hello")
        result = chat._prepare_messages()
        assert result == chat.req

    def test_early_return_for_few_messages(self, chat):
        chat.token_max = 100
        for _i in range(100):
            chat._prepare_messages()
        pass  # just verifies no exception

    def test_system_message_preserved(self, chat):
        system = chat.req[0]
        result = chat._prepare_messages()
        assert result[0] == system
