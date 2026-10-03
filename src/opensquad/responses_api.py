"""OpenAI Responses API transport (``api_protocol: openai_responses``).

The Responses API (``POST /v1/responses``) is OpenAI's newer wire format.  It
differs from Chat Completions in three ways that matter here:

* the request carries ``instructions`` + a list of ``input`` items, where a
  tool round-trip is a ``function_call`` item followed by a
  ``function_call_output`` item (not an ``assistant.tool_calls`` message plus a
  ``role: "tool"`` message);
* tools are flat (``{"type": "function", "name": ...}``) instead of nested
  under ``function``;
* the stream is typed events (``response.output_text.delta``,
  ``response.function_call_arguments.delta``, ``response.completed`` …) rather
  than ``ChatCompletionChunk`` objects.

Rather than fork the ~600-line turn engine, this class keeps ``ChatAPI`` in
charge of the whole turn (message assembly, context compression, retries,
stop handling, Native-FC accumulation, usage accounting, session persistence)
and swaps only the transport.  A tiny client bridge exposes a
``client.chat.completions.create(**params)`` method that:

1. converts the Chat-Completions-shaped request params into a Responses
   request, and
2. translates the Responses event stream back into Chat-Completions-shaped
   chunk objects.

Every other SDK resource (``files``, ``images``, ``responses``) is delegated to
the real ``AsyncOpenAI`` client, so file uploads and the image-generation path
keep working unchanged.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

from .chat_api import ChatAPI

logger = logging.getLogger(__name__)

__all__ = ["ResponsesAPI"]


# ─────────────────────────────────────────────────────────────────────────────
# Synthetic Chat-Completions-shaped chunks
#
# ChatAPI.chat() (and NativeToolCallStrategy) read `.choices[0].delta.content`,
# `.delta.reasoning_content`, `.delta.tool_calls[i].function.name/arguments` and
# `.choices[0].finish_reason`. `SimpleNamespace` gives exactly that duck type.
# ─────────────────────────────────────────────────────────────────────────────
def _function(name=None, arguments=None):
    return SimpleNamespace(name=name, arguments=arguments)


def _tool_call(*, index, id=None, type=None, name=None, arguments=None):
    return SimpleNamespace(index=index, id=id, type=type, function=_function(name, arguments))


def _chunk(*, content=None, reasoning=None, tool_call=None, usage=None, finish_reason=None):
    delta = SimpleNamespace(
        content=content,
        reasoning_content=reasoning,
        reasoning=None,
        tool_calls=([tool_call] if tool_call is not None else None),
        audio=None,
    )
    choice = SimpleNamespace(delta=delta, finish_reason=finish_reason, index=0)
    return SimpleNamespace(choices=[choice], usage=usage, finish_reason=finish_reason)


def _map_usage(usage):
    """Map a Responses ``usage`` object onto the Chat Completions shape.

    ``extract_cached_tokens`` (provider base) reads
    ``prompt_tokens_details.cached_tokens``; keeping the same nesting means the
    context panel's prompt-cache numbers keep working for Responses cards too.
    """
    if usage is None:
        return None
    inp = int(getattr(usage, "input_tokens", 0) or 0)
    out = int(getattr(usage, "output_tokens", 0) or 0)
    details = getattr(usage, "input_tokens_details", None)
    cached = int(getattr(details, "cached_tokens", 0) or 0) if details is not None else 0
    return SimpleNamespace(
        prompt_tokens=inp,
        completion_tokens=out,
        total_tokens=inp + out,
        prompt_tokens_details=SimpleNamespace(cached_tokens=cached),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Request translation: Chat Completions params -> Responses kwargs
# ─────────────────────────────────────────────────────────────────────────────
def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(p.get("text") or "") for p in content if isinstance(p, dict) and p.get("type") in ("text", "input_text")
        )
    return "" if content is None else str(content)


def _content_parts(content):
    """Translate Chat Completions content (str or parts list) into Responses input.

    Text and images are translated faithfully. Audio / files are passed through
    best-effort; anything unrecognised is dropped rather than sent as an
    invalid part (which would 400 the whole turn).
    """
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return "" if content is None else str(content)

    parts: list = []
    for item in content:
        if not isinstance(item, dict):
            continue
        ptype = item.get("type")
        if ptype in ("text", "input_text"):
            parts.append({"type": "input_text", "text": str(item.get("text") or "")})
        elif ptype == "image_url":
            url = (item.get("image_url") or {}).get("url") or ""
            if url:
                parts.append({"type": "input_image", "image_url": url})
        elif ptype == "input_audio":
            audio = item.get("input_audio") or {}
            if audio.get("data"):
                parts.append(
                    {
                        "type": "input_audio",
                        "input_audio": {"data": audio["data"], "format": audio.get("format") or "wav"},
                    }
                )
        elif ptype == "file":
            file_id = (item.get("file") or {}).get("file_id")
            if file_id:
                parts.append({"type": "input_file", "file_id": file_id})
    return parts or ""


def _tool_schema(tool):
    """Chat Completions tool def -> Responses flat tool def."""
    if not isinstance(tool, dict):
        return None
    fn = tool.get("function") if isinstance(tool.get("function"), dict) else tool
    name = fn.get("name")
    if not name:
        return None
    params = fn.get("parameters")
    if not isinstance(params, dict):
        params = {"type": "object", "properties": {}}
    return {
        "type": "function",
        "name": str(name),
        "description": str(fn.get("description") or ""),
        "parameters": params,
        "strict": False,
    }


def _input_from_messages(messages: list) -> tuple[str, list]:
    """Build ``(instructions, input_items)`` from a Chat-Completions message list."""
    instructions_parts: list[str] = []
    input_items: list = []

    for msg in messages or []:
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role") or "").lower()

        if role == "system":
            text = _text_of(msg.get("content"))
            if text:
                instructions_parts.append(text)
            continue

        if role == "tool":
            input_items.append(
                {
                    "type": "function_call_output",
                    "call_id": str(msg.get("tool_call_id") or ""),
                    "output": _text_of(msg.get("content")) or "(empty result)",
                }
            )
            continue

        if role == "assistant":
            text = _text_of(msg.get("content"))
            if text:
                input_items.append({"role": "assistant", "content": text})
            for tc in msg.get("tool_calls") or []:
                if not isinstance(tc, dict):
                    continue
                fn = tc.get("function") or {}
                input_items.append(
                    {
                        "type": "function_call",
                        "call_id": str(tc.get("id") or ""),
                        "name": str(fn.get("name") or ""),
                        "arguments": str(fn.get("arguments") or "{}"),
                    }
                )
            continue

        # user (and anything unexpected) -> a user message
        input_items.append({"role": "user", "content": _content_parts(msg.get("content"))})

    return "\n\n".join(p for p in instructions_parts if p), input_items


# ─────────────────────────────────────────────────────────────────────────────
# Client bridge: exposes the Chat Completions surface ChatAPI expects
# ─────────────────────────────────────────────────────────────────────────────
class _CompletionsResource:
    def __init__(self, owner: ResponsesAPI):
        self._owner = owner

    async def create(self, **params):
        # ChatAPI awaits create() then `async for chunk in stream`; returning the
        # async generator directly (not awaiting it) preserves that contract.
        return self._owner._responses_chunk_iter(params)


class _ChatResource:
    def __init__(self, owner: ResponsesAPI):
        self.completions = _CompletionsResource(owner)


class _ResponsesClientBridge:
    """Wraps AsyncOpenAI so ``client.chat.completions.create`` speaks Responses.

    All other attributes (``files``, ``images``, ``responses``, the underlying
    httpx client used by ``_is_client_closed``) are delegated to the real SDK
    client via ``__getattr__``.
    """

    def __init__(self, real, owner: ResponsesAPI):
        self._real = real
        self._owner = owner
        self.chat = _ChatResource(owner)

    def __getattr__(self, name):
        return getattr(self._real, name)

    async def close(self):
        return await self._real.close()


class ResponsesAPI(ChatAPI):
    """``ChatAPI`` whose streaming transport is the OpenAI Responses API."""

    def __init__(self, *args, **kwargs):
        # The real AsyncOpenAI instance behind the bridge; set on every (re)build.
        self._real_client = None
        super().__init__(*args, **kwargs)

    # ── transport ──────────────────────────────────────────────────────────
    def _build_client(self):
        real = super()._build_client()
        self._real_client = real
        return _ResponsesClientBridge(real, self)

    def _to_responses_params(self, params: dict) -> dict:
        instructions, input_items = _input_from_messages(params.get("messages") or [])

        kwargs: dict = {
            "model": params.get("model") or self.model,
            "input": input_items,
            "stream": True,
            # Do not retain request/response server-side by default.
            "store": False,
        }
        if instructions:
            kwargs["instructions"] = instructions

        # Reasoning models reject a non-default temperature; drop it when thinking.
        temperature = params.get("temperature")
        if temperature is not None and not getattr(self, "is_think", False):
            kwargs["temperature"] = temperature

        tools = params.get("tools")
        if tools:
            converted = [t for t in (_tool_schema(t) for t in tools) if t]
            if converted:
                kwargs["tools"] = converted
                kwargs["tool_choice"] = params.get("tool_choice") or "auto"

        if getattr(self, "is_think", False):
            from .reasoning_effort import normalize_effort

            kwargs["reasoning"] = {"effort": normalize_effort(getattr(self, "reasoning_effort", "high"))}

        return kwargs

    async def _responses_chunk_iter(self, params: dict):
        """Stream Responses events as Chat-Completions-shaped chunks."""
        kwargs = self._to_responses_params(params)
        client = self._real_client
        if client is None:
            # Defensive: a caller that built request_params without going through
            # _ensure_client (e.g. a test) still needs a client here.
            self._ensure_client()
            client = self._real_client

        try:
            stream = await client.responses.create(**kwargs)
        except Exception as exc:
            # Same resilience as ChatAPI's stream_options retry: a strict
            # Responses-compatible gateway may reject `store` outright. Drop it
            # and retry once rather than failing the whole turn.
            if "store" not in kwargs or "store" not in str(exc).lower():
                raise
            logger.warning("[ResponsesAPI] endpoint rejected `store`, retrying without it: %s", exc)
            kwargs.pop("store", None)
            stream = await client.responses.create(**kwargs)

        call_seq: dict[str, int] = {}
        next_seq = 0
        had_function_call = False
        incomplete = False

        try:
            async for event in stream:
                etype = getattr(event, "type", "") or ""

                if etype == "response.output_text.delta":
                    text = getattr(event, "delta", None)
                    if text:
                        yield _chunk(content=text)
                elif etype in ("response.reasoning_summary_text.delta", "response.reasoning_text.delta"):
                    reasoning = getattr(event, "delta", None)
                    if reasoning:
                        yield _chunk(reasoning=reasoning)
                elif etype == "response.output_item.added":
                    item = getattr(event, "item", None)
                    if item is not None and getattr(item, "type", "") == "function_call":
                        had_function_call = True
                        item_id = getattr(item, "id", None) or getattr(item, "call_id", None) or str(next_seq)
                        seq = next_seq
                        next_seq += 1
                        call_seq[item_id] = seq
                        yield _chunk(
                            tool_call=_tool_call(
                                index=seq,
                                id=getattr(item, "call_id", None) or getattr(item, "id", None),
                                type="function",
                                name=getattr(item, "name", None),
                            )
                        )
                elif etype == "response.function_call_arguments.delta":
                    item_id = getattr(event, "item_id", None)
                    seq = call_seq.get(item_id)
                    if seq is None:
                        seq = next_seq
                        next_seq += 1
                        call_seq[item_id] = seq
                    had_function_call = True
                    delta = getattr(event, "delta", None)
                    if delta:
                        yield _chunk(tool_call=_tool_call(index=seq, arguments=delta))
                elif etype in ("response.completed", "response.incomplete"):
                    response = getattr(event, "response", None)
                    if etype == "response.incomplete":
                        incomplete = True
                    usage = _map_usage(getattr(response, "usage", None)) if response is not None else None
                    if had_function_call:
                        finish = "tool_calls"
                    elif incomplete or (getattr(response, "status", None) == "incomplete"):
                        finish = "length"
                    else:
                        finish = "stop"
                    yield _chunk(usage=usage, finish_reason=finish)
                elif etype == "response.failed":
                    response = getattr(event, "response", None)
                    error = getattr(response, "error", None) if response is not None else None
                    raise RuntimeError(f"OpenAI Responses request failed: {error or 'unknown error'}")
                elif etype == "error":
                    detail = getattr(event, "message", None) or getattr(event, "code", None)
                    raise RuntimeError(f"OpenAI Responses error: {detail or 'unknown error'}")
                # Every other event type (created/in_progress/content_part…/output_item.done)
                # carries no content we need.
        finally:
            aclose = getattr(stream, "aclose", None) or getattr(stream, "close", None)
            if callable(aclose):
                try:
                    result = aclose()
                    if hasattr(result, "__await__"):
                        await result
                except Exception:
                    pass

    # ── summariser: reach it over Responses too ─────────────────────────────
    def _summarizer_request(self, system_prompt: str, user_prompt: str, max_tokens: int) -> str:
        from .chat_api import _get_openai

        summary_model = self._summarizer_model()
        client = _get_openai()(api_key=self.api_key, base_url=self.base_url, timeout=180)
        try:
            response = client.responses.create(
                model=summary_model,
                instructions=system_prompt,
                input=user_prompt,
                max_output_tokens=max_tokens,
                store=False,
            )
        except TypeError:
            # Older SDKs / compat gateways may not accept `store`.
            response = client.responses.create(
                model=summary_model,
                instructions=system_prompt,
                input=user_prompt,
                max_output_tokens=max_tokens,
            )
        text = getattr(response, "output_text", "") or ""
        if not text:
            # Fall back to walking the output items.
            for item in getattr(response, "output", None) or []:
                for part in getattr(item, "content", None) or []:
                    chunk = getattr(part, "text", None)
                    if chunk:
                        text += chunk
        logger.info("%s summariser via Responses: %d chars", self._provider_label(), len(text))
        return text.strip()
