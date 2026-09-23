import asyncio
import base64
import json
import logging
import threading
import uuid
from collections import OrderedDict

from .xml_parser import StreamingTagParser, StreamingThoughtBlockDropper, strip_prompted_thought_blocks

try:
    from tool import logger
except ImportError:
    from .tool import logger
import contextlib
import os

from . import session_manager as _session_module
from ._provider_base import ProviderAPIBase, extract_cached_tokens
from .input_hub import input_hub
from .model_config import ModelConfig
from .utils import CharPrinter, blocking_io

_openai_client = None
_async_openai_client = None  # NEW
_tiktoken_mod = None


def _get_openai():
    """Lazy import of OpenAI SDK (avoids ~1s import penalty at startup)."""
    global _openai_client
    if _openai_client is None:
        from openai import OpenAI as _O

        _openai_client = _O
    return _openai_client


def _get_async_openai():
    """Lazy import of AsyncOpenAI SDK."""
    global _async_openai_client
    if _async_openai_client is None:
        from openai import AsyncOpenAI as _AO

        _async_openai_client = _AO
    return _async_openai_client


# PERF-11: shared client for downloading generated-image URLs (reused across
# calls instead of opening a fresh connection per image).
_image_download_client = None


def _make_llm_http_client(timeout: float):
    """Build an httpx.AsyncClient that ignores system proxy env vars.

    LLM endpoints are reached over HTTPS, so the OpenAI SDK's underlying
    httpx client reads HTTPS_PROXY. On dev hosts with a local proxy
    (e.g. 127.0.0.1:17897) that is offline, httpx routes the request there
    and raises APIConnectionError. trust_env=False forces a direct
    connection, mirroring the Vite reverse-proxy client in main.py.

    Phased timeouts: connect fails fast (~10s) instead of blocking on the
    full read budget; read stays at the model-level timeout so long prefills
    are not cut short.
    """
    import httpx as _httpx

    return _httpx.AsyncClient(
        trust_env=False,
        timeout=_httpx.Timeout(connect=10.0, read=timeout, write=30.0, pool=10.0),
    )


def _get_tiktoken():
    """Lazy import of tiktoken."""
    global _tiktoken_mod
    if _tiktoken_mod is None:
        import tiktoken as _T

        _tiktoken_mod = _T
    return _tiktoken_mod


def wants_deepseek_prompt_cache(prompt_cache: bool, base_url: str = "", model: str = "") -> bool:
    """True when DeepSeek-style ``chat_template_kwargs.cache.use`` should be sent.

    Official DeepSeek hosts put ``deepseek`` in the URL. Volcengine Ark and
    other OpenAI-compat gateways often use ``ark.cn-beijing.volces.com`` with a
    ``deepseek-v4-*`` model name — match either.
    """
    if not prompt_cache:
        return False
    return "deepseek" in f"{base_url} {model}".lower()


def apply_deepseek_prompt_cache(
    request_params: dict,
    *,
    prompt_cache: bool,
    base_url: str = "",
    model: str = "",
) -> dict:
    """Mutate *request_params* in place; return the same dict."""
    if not wants_deepseek_prompt_cache(prompt_cache, base_url, model):
        return request_params
    extra_body = dict(request_params.get("extra_body") or {})
    extra_body["chat_template_kwargs"] = {"cache": {"use": True}}
    request_params["extra_body"] = extra_body
    return request_params


__all__ = ["ChatAPI", "apply_deepseek_prompt_cache", "wants_deepseek_prompt_cache"]


class ChatAPI(ProviderAPIBase):
    """
    ChatAPI v2.1: Clean OpenAI-compatible interface with streaming tag push support.
    Added provider-level file uploads (Files API) for large files / video / audio direct upload.
    """

    def __init__(
        self,
        config: ModelConfig | None = None,
        # ── Backward-compat kwargs (deprecated, use config=...) ──
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        prompt: str | None = None,
        timeout: float | None = None,
        token_max: int | None = None,
        temperature: float | None = None,
        reduction_strategy: str | None = None,
        reduction_batch_size: int | None = None,
        stream_parser: StreamingTagParser | None = None,
        load_his: str | None = None,
        is_img_model: bool | None = None,
        is_audio_model: bool | None = None,
        is_video_model: bool | None = None,
        use_file_api: bool | None = None,
        file_api_size_threshold: int | None = None,
        is_audio_output: bool | None = None,
        audio_output_voice: str | None = None,
        frequency_penalty: float | None = None,
        presence_penalty: float | None = None,
        enable_repetition_check: bool | None = None,
        is_think: bool | None = None,
        reasoning_effort: str | None = None,
        is_image_output: bool | None = None,
        image_size: str | None = None,
        image_steps: int | None = None,
        image_cfg_scale: float | None = None,
    ):
        """
        P2-1: Accepts either a ModelConfig dataclass (preferred) or legacy kwargs.

        Args:
            config: ModelConfig instance containing all parameters.
            **kwargs: Legacy individual parameters (deprecated but kept for compat).
        """
        # Build config from legacy kwargs if not provided
        if config is None:
            config = ModelConfig(
                api_key=api_key or "",
                model=model or "",
                base_url=base_url or "",
                prompt=prompt or "",
                timeout=timeout if timeout is not None else 120.0,
                token_max=token_max if token_max is not None else 100000,
                temperature=temperature if temperature is not None else 0.3,
                reduction_strategy=reduction_strategy or "start",
                reduction_batch_size=reduction_batch_size if reduction_batch_size is not None else 2,
                load_his=load_his,
                is_img_model=is_img_model if is_img_model is not None else False,
                is_audio_model=is_audio_model if is_audio_model is not None else False,
                is_video_model=is_video_model if is_video_model is not None else False,
                use_file_api=use_file_api if use_file_api is not None else False,
                file_api_size_threshold=file_api_size_threshold
                if file_api_size_threshold is not None
                else 4 * 1024 * 1024,
                is_audio_output=is_audio_output if is_audio_output is not None else False,
                audio_output_voice=audio_output_voice or "alloy",
                frequency_penalty=frequency_penalty if frequency_penalty is not None else 0.0,
                presence_penalty=presence_penalty if presence_penalty is not None else 0.0,
                enable_repetition_check=enable_repetition_check if enable_repetition_check is not None else False,
                is_think=is_think if is_think is not None else False,
                reasoning_effort=reasoning_effort or "high",
                is_image_output=is_image_output if is_image_output is not None else False,
            )
        self.config = config

        # Unpack for convenience (preserves existing attribute access patterns)
        self.api_key = config.api_key
        self.base_url = config.base_url
        self.model = config.model
        self.timeout = config.timeout
        self.temperature = config.temperature
        self.is_img_model = config.is_img_model
        self.is_audio_model = config.is_audio_model
        self.is_video_model = config.is_video_model
        self.use_file_api = config.use_file_api
        self.file_api_size_threshold = config.file_api_size_threshold
        # Explicit prompt-caching opt-in (OpenAI-compat providers only).
        self._enable_prompt_cache = bool(getattr(config, "prompt_cache", True))
        # file_id cache: path -> file_id, avoids re-uploading the same file within a session
        self._file_id_cache: OrderedDict[str, str] = OrderedDict()  # path -> file_id LRU cache (max 1000)
        self.is_audio_output = config.is_audio_output
        self.audio_output_voice = config.audio_output_voice
        self.frequency_penalty = config.frequency_penalty
        self.presence_penalty = config.presence_penalty
        self.enable_repetition_check = config.enable_repetition_check
        self.is_think = config.is_think
        self.is_image_output = config.is_image_output
        # OpenAI-compatible Images API knobs (StepFun / DALL·E style)
        self.image_size = image_size or getattr(config, "image_size", None) or "1024x1024"
        self.image_steps = image_steps if image_steps is not None else int(getattr(config, "image_steps", 8) or 8)
        self.image_cfg_scale = (
            image_cfg_scale if image_cfg_scale is not None else float(getattr(config, "image_cfg_scale", 1.0) or 1.0)
        )
        from opensquad.reasoning_effort import normalize_effort

        self.reasoning_effort = normalize_effort(config.reasoning_effort)
        self.output_media_dir: str = ""  # Set externally (agents_boot)
        self._prompt_template = config.prompt  # Raw placeholder template (does not change with per-turn replacements)
        self.prompt_message = {"role": "system", "content": config.prompt}
        self.req = [self.prompt_message]
        self.printer = CharPrinter(max_width=80)
        self.stream_parser = stream_parser
        self.load_his = config.load_his

        self.history_dir = None  # Lazy: resolved on first use
        self.history_file = None
        self._initialize_history(config.load_his)

        # Defer OpenAI SDK, httpx SSL setup and tiktoken loading to first use.
        # Building the client here costs several seconds per agent boot.
        self.client = None
        self._client_lock = threading.Lock()
        self._encoding = None
        self.token_max = config.token_max
        self.reduction_strategy = config.reduction_strategy
        self.reduction_batch_size = config.reduction_batch_size
        # ── Shared provider state ──
        # Counters, incremental token counter, per-message token LRU cache and
        # the compression flags all live in ProviderAPIBase so the three
        # providers cannot drift apart again.
        self._init_provider_base()

        # ── Safety cap for message history (P2 defense) ──
        self._MAX_HISTORY_MESSAGES = 5000  # Prevent unbounded memory growth

        logger.info(f"ChatAPI Initialized. Model: {self.model}")

    def _build_client(self):
        return _get_async_openai()(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=self.timeout,
            http_client=_make_llm_http_client(self.timeout),
            # SDK default is 2 retries, which stacks on top of the 10s connect
            # timeout and turns a dead endpoint into ~65s of blind waiting.
            # Retries=0 keeps worst-case feedback ~20s (phase-1 C-6 target
            # <30s); transient failures are covered by the runner/turn-level
            # retry logic instead.
            max_retries=0,
        )

    def _is_client_closed(self) -> bool:
        """True when the lazily-built client can no longer send requests.

        The AsyncOpenAI client wraps an ``httpx.AsyncClient`` that owns the
        connection pool. ``reload_model`` / ``update_model`` close the *old*
        pool asynchronously (``_close_old``), and session clones share the
        same client via ``_clone_chat_api``. If that shared pool is closed
        while ``self.client`` still references it, the next LLM streaming call
        fails with ``APIConnectionError: Cannot send a request, as the client
        has been closed`` — which is what silently aborts a turn right after a
        tool call. Detect the closed underlying httpx client so ``_ensure_client``
        can rebuild it.
        """
        if self.client is None:
            return False
        try:
            inner = getattr(self.client, "client", None)
            if inner is not None and getattr(inner, "is_closed", False):
                return True
        except Exception:
            return False
        return False

    def _ensure_client(self):
        if self.client is None or self._is_client_closed():
            lock = getattr(self, "_client_lock", None)
            if lock is None:
                self.client = self._build_client()
            else:
                with lock:
                    if self.client is None or self._is_client_closed():
                        self.client = self._build_client()
        return self.client

    @property
    def encoding(self):
        if getattr(self, "_encoding", None) is None:
            self._encoding = self._build_encoding()
        return self._encoding

    @encoding.setter
    def encoding(self, value):
        self._encoding = value

    def _build_encoding(self):
        try:
            return _get_tiktoken().encoding_for_model(self.model)
        except KeyError:
            return _get_tiktoken().get_encoding("cl100k_base")

    def warmup(self) -> None:
        """Pre-build client, tokenizer and token caches without a network call."""
        self._ensure_client()
        _ = self.encoding
        try:
            self.get_current_token_count(self._last_tools)
        except Exception as exc:
            logger.debug("[ChatAPI] warmup token count skipped: %s", exc)

    def _trim_history_if_needed(self):
        """Safety cap: prevent unbounded memory growth in extreme long-running sessions.

        Removes whole conversation turns from the head (keeping the system
        prompt) so that an assistant(tool_calls)+tool(result) pair is never
        split, which would leave an orphan tool_call_id and cause API 400s.
        """
        if len(self.req) <= self._MAX_HISTORY_MESSAGES:
            return
        excess = len(self.req) - self._MAX_HISTORY_MESSAGES
        # Index 0 is the system prompt; start trimming from index 1.
        cut = 1
        removed = 0
        while removed < excess and cut < len(self.req):
            msg = self.req[cut]
            removed += 1
            # If this is an assistant with tool_calls, also drop the trailing
            # tool-result messages that belong to it, so no orphan remains.
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                while cut + 1 < len(self.req) and self.req[cut + 1].get("role") == "tool":
                    removed += 1
                    cut += 1
            cut += 1
        if removed > 0:
            del self.req[1:cut]
            logger.warning(
                "[ChatAPI] History safety-trimmed by %d messages (cap=%d)",
                removed,
                self._MAX_HISTORY_MESSAGES,
            )
            self.invalidate_token_cache()

    async def reload_model(self, model_cfg: dict):
        """Hot-reload model parameters without losing conversation history.

        Called by Runner when config.json model section changes (e.g. model card
        switched via the management UI).  Updates credentials, recreates the
        OpenAI client, and resets provider-specific caches.  Conversation
        history (self.req) is intentionally preserved.
        """
        old_model = self.model
        self.api_key = model_cfg.get("api_key", self.api_key)
        self.base_url = model_cfg.get("base_url", self.base_url)
        self.model = model_cfg.get("model_name", self.model)
        self.temperature = model_cfg.get("temperature", self.temperature)
        self.token_max = model_cfg.get("token_max", self.token_max)
        self.is_img_model = model_cfg.get("is_image", self.is_img_model)
        self.is_audio_model = model_cfg.get("is_audio_model", self.is_audio_model)
        self.is_video_model = model_cfg.get("is_video", self.is_video_model)
        self.use_file_api = model_cfg.get("use_file_api", self.use_file_api)
        self.file_api_size_threshold = model_cfg.get("file_api_size_threshold", self.file_api_size_threshold)
        self.is_audio_output = model_cfg.get("is_audio_output", self.is_audio_output)
        self.audio_output_voice = model_cfg.get("audio_output_voice", self.audio_output_voice)
        self.is_image_output = model_cfg.get("is_image_output", self.is_image_output)
        self.image_size = model_cfg.get("image_size", self.image_size)
        self.image_steps = int(model_cfg.get("image_steps", self.image_steps))
        self.image_cfg_scale = float(model_cfg.get("image_cfg_scale", self.image_cfg_scale))
        self.frequency_penalty = model_cfg.get("frequency_penalty", self.frequency_penalty)
        self.presence_penalty = model_cfg.get("presence_penalty", self.presence_penalty)
        self.is_think = model_cfg.get("is_think", getattr(self, "is_think", False))
        from opensquad.reasoning_effort import normalize_effort

        if "reasoning_effort" in model_cfg:
            self.reasoning_effort = normalize_effort(model_cfg.get("reasoning_effort"))

        # Keep the backing config dict in sync. Parallel sessions clone via
        # ``base.config`` — if we only mutate instance fields, clones (and
        # post-restart boots) keep calling the old provider (e.g. OpenCode).
        try:
            merged = dict(self.config or {}) if isinstance(getattr(self, "config", None), dict) else {}
            merged.update(model_cfg)
            self.config = merged
        except Exception:
            self.config = dict(model_cfg)
        self.model_config = dict(model_cfg)

        # Close old client connection pool without blocking the switch.
        # await close() can stall for seconds on half-open sockets; the UI then
        # sits on "Switching…" even though credentials are already updated.
        old_client = self.client
        self.client = self._build_client()

        async def _close_old() -> None:
            with contextlib.suppress(Exception):
                await old_client.close()

        with contextlib.suppress(Exception):
            asyncio.get_running_loop().create_task(_close_old())

        # File IDs are provider/account specific -- clear cache
        self._file_id_cache.clear()
        # Reset the lazily loaded encoding so the next token count rebuilds it.
        if old_model != self.model or getattr(self, "_encoding", None) is None:
            self._encoding = None
        logger.info(f"[ChatAPI] Model hot-reloaded: {old_model} -> {self.model}")

    def update_system_prompt(self, new_prompt: str):
        if self.req and self.req[0]["role"] == "system":
            self.req[0]["content"] = new_prompt
        else:
            self.req.insert(0, {"role": "system", "content": new_prompt})
        self.prompt_message["content"] = new_prompt

    def get_system_prompt(self) -> str:
        return self.prompt_message["content"]

    def get_template(self) -> str:
        """Return the raw placeholder template (each turn _setup_prompt starts replacing from here)"""
        return self._prompt_template

    def set_template(self, template: str):
        """Update the raw template (only used at boot phase, e.g. injecting EXPERT_ROLE_CARD)"""
        self._prompt_template = template

    # -- Provider-level Files API --

    def _upload_file_openai(self, path: str, purpose: str = "user_data") -> str | None:
        """
        Upload a local file to the OpenAI Files API and return the file_id.
        Files are cached (same path within a session is not re-uploaded).

        purpose options:
          "user_data"   -- General media/documents (Chat Completions file reference)
          "assistants"  -- Assistants API only
          "vision"      -- Images (used by some providers)
        """
        if path in self._file_id_cache:
            return self._file_id_cache[path]
        try:
            client = self._ensure_client()
            with open(path, "rb") as f:
                response = client.files.create(file=f, purpose=purpose)
            file_id = response.id
            self._file_id_cache[path] = file_id
            # LRU eviction: discard oldest entry when over limit
            if len(self._file_id_cache) > 1000:
                self._file_id_cache.popitem(last=False)
            logger.info(f"[ChatAPI] Uploaded to Files API: {path} -> {file_id}")
            return file_id
        except Exception as e:
            logger.error(f"[ChatAPI] Files API upload failed ({path}): {e}")
            return None

    def _delete_file_openai(self, file_id: str):
        """Delete an uploaded file from the Files API (can be called for cleanup after session ends)"""
        try:
            self._ensure_client().files.delete(file_id)
            # Clear cache
            self._file_id_cache = OrderedDict((k, v) for k, v in self._file_id_cache.items() if v != file_id)
            logger.info(f"[ChatAPI] Deleted from Files API: {file_id}")
        except Exception as e:
            logger.warning(f"[ChatAPI] Files API delete failed ({file_id}): {e}")

    def delete_all_uploaded_files(self):
        """Clean up all Files API files uploaded in this session"""
        for _path, fid in list(self._file_id_cache.items()):
            self._delete_file_openai(fid)
        self._file_id_cache.clear()

    # -- MIME helpers --

    def _guess_audio_mime(self, path: str) -> str:
        ext = os.path.splitext(path)[1].lower()
        if ext in [".wav"]:
            return "audio/wav"
        if ext in [".webm"]:
            return "audio/webm"
        if ext in [".m4a"]:
            return "audio/mp4"
        if ext in [".ogg", ".oga"]:
            return "audio/ogg"
        return "audio/mpeg"

    def _guess_video_mime(self, path: str) -> str:
        ext = os.path.splitext(path)[1].lower()
        if ext in [".webm"]:
            return "video/webm"
        if ext in [".mov"]:
            return "video/quicktime"
        if ext in [".mkv"]:
            return "video/x-matroska"
        return "video/mp4"

    # -- Summariser transport (prompt assembly lives in ProviderAPIBase) --

    def _summarizer_request(self, system_prompt: str, user_prompt: str, max_tokens: int) -> str:
        """Reach the summariser through an OpenAI-compatible streaming client.

        Uses a dedicated client so a long compression request cannot disturb
        the main agent client, and streams to match the manual-compression
        path (better compatibility across relay wrappers).
        """
        summary_model = self._summarizer_model()
        summary_client = _get_openai()(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=180,  # Generous timeout for large context compression
        )
        logger.info("[CompressTrace] Starting streaming summary generation...")
        stream = summary_client.chat.completions.create(
            model=summary_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=max_tokens,
            temperature=0.2,
            stream=True,
        )

        parts: list[str] = []
        prompt_tokens = 0
        completion_tokens = 0
        finish_reason = None

        for chunk in stream:
            if not chunk.choices:
                chunk_usage = getattr(chunk, "usage", None)
                if chunk_usage:
                    prompt_tokens = getattr(chunk_usage, "prompt_tokens", 0) or 0
                    completion_tokens = getattr(chunk_usage, "completion_tokens", 0) or 0
                continue

            delta = chunk.choices[0].delta
            if delta and delta.content:
                parts.append(delta.content)

            chunk_finish = getattr(chunk.choices[0], "finish_reason", None)
            if chunk_finish:
                finish_reason = chunk_finish
            chunk_usage = getattr(chunk, "usage", None)
            if chunk_usage:
                prompt_tokens = getattr(chunk_usage, "prompt_tokens", 0) or 0
                completion_tokens = getattr(chunk_usage, "completion_tokens", 0) or 0

        content = "".join(parts).strip()
        logger.info(
            "[CompressTrace] streaming summary complete: content_len=%d, prompt_tokens=%d, "
            "completion_tokens=%d, finish_reason=%s",
            len(content),
            prompt_tokens,
            completion_tokens,
            finish_reason,
        )
        if not content:
            logger.warning(
                "Summary generation returned empty content, model=%s, prompt_len=%d, "
                "prompt_tokens=%d, completion_tokens=%d, finish_reason=%s, chunks_collected=%d",
                summary_model,
                len(user_prompt),
                prompt_tokens,
                completion_tokens,
                finish_reason,
                len(parts),
            )
            # The compressed middle is gone for good, so point the model at what
            # it *does* still have — the pinned opening request this used to
            # reference is no longer carried in the request (see
            # _provider_base._prepare_messages step 5).
            return (
                "Summary generation returned an empty response. The older conversation is no longer "
                "in context — continue from the retained recent messages and the current request, and "
                "ask the user if a detail you need is missing."
            )
        return content

    def add_user_message(
        self,
        message: str,
        image_path: list[str] | None = None,
        image_b64_list: list[dict] | None = None,
        audio_path: list[str] | None = None,
        video_path: list[str] | None = None,
    ):
        """
        Add a user message to the conversation history.

        If message is empty AND no multimodal content is provided, skip adding
        the message entirely. This supports tool-result-only inner loop iterations
        where the tool result was already injected via add_tool_result().
        """
        # Skip empty text-only messages (tool result already in history via add_tool_result)
        has_multimodal = bool(image_path or image_b64_list or audio_path or video_path)
        if not message and not has_multimodal:
            logger.debug(
                f"[ChatAPI] Skipping empty user message (tool result already in history via add_tool_result), "
                f"req_len={len(self.req)}"
            )
            return False  # Indicate no message was added

        # Count a "conversation turn" only when a real user message arrives.
        # Internal LLM tool-call iterations (role=assistant/tool) should NOT
        # increment the request counter — from the user's perspective, the entire
        # tool chain triggered by one user message counts as ONE conversation turn.
        if message:
            logger.debug(
                f"[ChatAPI] [QUOTA] add_user_message: message_len={len(message)}, "
                f"total_requests_before={self.total_requests}, req_last_role={self.req[-1]['role'] if self.req else 'EMPTY'}"
            )
            self.total_requests += 1

        logger.debug(
            f"[ChatAPI] add_user_message: text_len={len(message)}, has_multimodal={has_multimodal}, req_len_before={len(self.req)}"
        )

        content = [{"type": "text", "text": message}]
        if image_path and self.is_img_model:
            for img in image_path:
                try:
                    with open(img, "rb") as f:
                        encoded = base64.b64encode(f.read()).decode("utf-8")
                        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{encoded}"}})
                except Exception as e:
                    logger.error(f"Failed to encode image {img}: {e}")

        if image_b64_list and self.is_img_model:
            for img_data in image_b64_list:
                mime = img_data.get("mimeType", "image/png")
                b64 = img_data.get("data", "")
                if b64:
                    content.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}})

        if audio_path and self.is_audio_model:
            for audio in audio_path:
                if not audio:
                    continue
                file_size = os.path.getsize(audio) if os.path.exists(audio) else 0

                # -- Branch 1: Files API upload (large file + use_file_api=True) --
                if self.use_file_api and file_size > self.file_api_size_threshold:
                    file_id = self._upload_file_openai(audio, purpose="user_data")
                    if file_id:
                        # OpenAI Chat Completions supports input_audio + file_id (newer API)
                        content.append({"type": "input_audio", "input_audio": {"file_id": file_id}})
                        continue
                    # Upload failed -> fallback to base64 inline

                # -- Branch 2: base64 inline (standard GPT-4o Audio format) --
                if file_size > 25 * 1024 * 1024:
                    logger.warning(f"[ChatAPI] Audio > 25MB, skipping inline: {audio}")
                    continue
                try:
                    with open(audio, "rb") as f:
                        encoded = base64.b64encode(f.read()).decode("utf-8")
                    # Extract format (OpenAI input_audio supports: wav/mp3/webm/ogg/flac/opus)
                    ext = os.path.splitext(audio)[1].lower().lstrip(".")
                    fmt = ext if ext in ("wav", "mp3", "webm", "ogg", "flac", "opus", "m4a") else "mp3"
                    content.append({"type": "input_audio", "input_audio": {"data": encoded, "format": fmt}})
                except Exception as e:
                    logger.error(f"[ChatAPI] Failed to encode audio {audio}: {e}")

        if video_path and self.is_video_model:
            for video in video_path:
                if not video:
                    continue
                file_size = os.path.getsize(video) if os.path.exists(video) else 0

                # -- Branch 1: Files API upload (recommended, supports large videos) --
                if self.use_file_api:
                    file_id = self._upload_file_openai(video, purpose="user_data")
                    if file_id:
                        # Use OpenAI file content block (Responses API / compatible extension)
                        content.append({"type": "file", "file": {"file_id": file_id}})
                        continue
                    # Upload failed -> fallback to base64 inline

                # -- Branch 2: base64 inline (fallback for small videos) --
                if file_size > 12 * 1024 * 1024:
                    logger.warning(f"[ChatAPI] Video > 12MB and Files API disabled/failed, skipping: {video}")
                    continue
                try:
                    with open(video, "rb") as f:
                        encoded = base64.b64encode(f.read()).decode("utf-8")
                        mime = self._guess_video_mime(video)
                        content.append({"type": "video_url", "video_url": {"url": f"data:{mime};base64,{encoded}"}})
                except Exception as e:
                    logger.error(f"[ChatAPI] Failed to encode video {video}: {e}")

        # If no images, store as plain string to save space
        final_content = content if len(content) > 1 else message
        msg = {"role": "user", "content": final_content}
        self.req.append(msg)
        # Incremental token count update
        if self._cached_token_count is not None:
            try:
                self._cached_token_count += self._count_message_tokens(msg)
            except (TypeError, AttributeError):
                self._cached_token_count = None
        self._trim_history_if_needed()

    def add_assistant_message(self, content: str, reasoning_content: str | None = None, *, force_record: bool = False):
        """Add assistant message and sync reasoning_content to session for persistence.

        ``force_record=True`` records the message even when both content and
        reasoning are empty — required for native-FC turns that carry only
        ``tool_calls`` (the tool-call anchor must exist for the API to accept
        the following ``role=tool`` continuation).
        """
        msg = {"role": "assistant", "content": content}
        if reasoning_content:
            msg["reasoning_content"] = reasoning_content
        if content or reasoning_content or force_record:
            self.req.append(msg)
            # Incremental token count update
            if self._cached_token_count is not None:
                try:
                    self._cached_token_count += self._count_message_tokens(msg)
                except (TypeError, AttributeError):
                    self._cached_token_count = None
            # P0-4: Always sync to session for refresh survival, even for non-thinking models.
            # Previously this was gated on reasoning_content only, meaning assistant messages
            # without reasoning (e.g. plain text, or empty-content tool_calls responses) were
            # never written to the session file and would vanish on page refresh.
            save_kwargs = {"msg_type": "api_sync"}
            if reasoning_content:
                save_kwargs["reasoning_content"] = reasoning_content
            _session_module.get_session_manager().add_message(
                "assistant",
                content,
                **save_kwargs,
            )
        self._trim_history_if_needed()

    def add_tool_result(self, tool_name: str, tool_args: dict, result: str, tool_call_id: str = ""):
        """Add assistant message with tool_calls + tool result message in OpenAI standard format.

        This ensures multi-step tool calling works correctly: the LLM sees both
        the original tool_call (with id/name/arguments) and the tool execution result.

        If the last message in self.req is an assistant message (from streaming text),
        it will be replaced with a proper tool_calls structure.

        Args:
            tool_name: name of the tool that was called
            tool_args: dict of arguments passed to the tool
            result: the tool execution result (plain text)
            tool_call_id: unique call identifier (auto-generated if empty)
        """
        import uuid

        if not tool_call_id:
            tool_call_id = f"call_{uuid.uuid4().hex[:8]}"

        # Track what we're doing for logging
        _mode = ""

        # If the last message is an assistant message (from streaming),
        # we need to keep its text content and ADD tool_calls to it.
        # Some models output both text AND tool_calls in the same response.
        if self.req and self.req[-1].get("role") == "assistant":
            last_msg = self.req[-1]
            new_call = {
                "id": tool_call_id,
                "type": "function",
                "function": {
                    "name": tool_name,
                    "arguments": json.dumps(tool_args, ensure_ascii=False) if tool_args else "{}",
                },
            }
            existing = last_msg.get("tool_calls")
            if isinstance(existing, list):
                # Parallel tool calls: MERGE instead of overwriting. The LLM can
                # emit several tool_calls in one turn; each tool result arrives
                # via its own add_tool_result. Overwriting here dropped sibling
                # calls from the assistant message while their tool responses
                # stayed in history — those responses became orphaned and the
                # next request 400'd ("tool_call_ids did not have response
                # messages"). De-dup by id in case a call is reported twice.
                ids = {c.get("id") for c in existing}
                if tool_call_id not in ids:
                    last_msg["tool_calls"] = [*existing, new_call]
            else:
                last_msg["tool_calls"] = [new_call]
            # If content was empty (pure tool_call response), set to null for OpenAI compatibility
            if not last_msg.get("content"):
                last_msg["content"] = None
            _mode = "amended_existing_assistant"
        else:
            # No preceding assistant message — create one with just tool_calls
            # CRITICAL FIX for DeepSeek V4: if the assistant BEFORE this gap had
            # reasoning_content, copy it here so DeepSeek V4 doesn't 400 on next turn.
            prev_reasoning = ""
            if self.req:
                for m in reversed(self.req):
                    if m.get("role") == "assistant":
                        prev_reasoning = m.get("reasoning_content", "")
                        break
            new_assistant = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": tool_call_id,
                        "type": "function",
                        "function": {
                            "name": tool_name,
                            "arguments": json.dumps(tool_args, ensure_ascii=False) if tool_args else "{}",
                        },
                    }
                ],
            }
            if prev_reasoning:
                new_assistant["reasoning_content"] = prev_reasoning
            self.req.append(new_assistant)
            _mode = "created_new_assistant"

        # Tool result message
        tool_msg = {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "name": tool_name,
            "content": str(result) if result else "(empty result)",
        }
        self.req.append(tool_msg)
        self.save_history()
        # Incremental token count: invalidate because we amended existing messages
        # (tool_calls added to assistant msg changes its token count)
        self.invalidate_token_cache()

        # ── Tool-loop fix: persist tool_calls + tool message to the session file ──
        # Previously this lived only in the in-memory req; any _load_history()
        # (turn bind / session switch) rebuilt req from the session file, which
        # had no tool messages → the LLM "forgot" every tool result and re-issued
        # identical calls forever (observed: 172 identical rounds / 42 min).
        try:
            from opensquad.session_manager import get_session_manager

            _sm = get_session_manager()
            _sid = ""
            if self._sid_provider is not None:
                try:
                    _sid = self._sid_provider() or ""
                except Exception:
                    _sid = ""
            _tool_calls_for_session = None
            if self.req:
                for _m in reversed(self.req):
                    if _m.get("role") == "assistant" and _m.get("tool_calls"):
                        _tool_calls_for_session = _m.get("tool_calls")
                        break
            if _tool_calls_for_session:
                _sm.sync_tool_call_message(
                    _tool_calls_for_session,
                    content=(last_msg.get("content") if _mode == "amended_existing_assistant" else None),
                    reasoning_content=(
                        last_msg.get("reasoning_content") if _mode == "amended_existing_assistant" else None
                    ),
                    sid=_sid or None,
                )
            _sm.add_message(
                "tool",
                str(result) if result else "(empty result)",
                msg_type="tool_result",
                tool_call_id=tool_call_id,
                name=tool_name,
                sid=_sid or None,
            )
        except Exception as _sm_e:
            logger.debug(f"[ChatAPI] session persistence of tool result skipped: {_sm_e}")

        logger.info(
            f"[ChatAPI] add_tool_result: tool={tool_name}, "
            f"call_id={tool_call_id}, mode={_mode}, "
            f"result_len={len(str(result))}, total_req_messages={len(self.req)}"
        )
        self._trim_history_if_needed()

    def add_pipeline_events(self, events_text: str):
        """Append accumulated pipeline events as a tool-role message.

        This is the key change for the 'never stop' architecture:
        External messages (web user, group chat, DM, timer, task_watch) flow
        through role=tool instead of role=user, so they're treated as event
        notifications rather than new conversation turns.

        Args:
            events_text: Formatted event pipeline text (from event_pipeline.drain_formatted_sync())
        """
        if not events_text or not events_text.strip():
            return

        # Use a SINGLE call_id for both the assistant's tool_calls and the tool message,
        # so DeepSeek/OpenAI API always sees matching IDs.
        _call_id = f"pipeline_events_{uuid.uuid4().hex[:8]}"

        # If the last message is already an assistant with tool_calls, APPEND to it.
        # Otherwise, create a synthetic assistant message.
        if self.req and self.req[-1].get("role") == "assistant" and self.req[-1].get("tool_calls"):
            self.req[-1]["tool_calls"] = [
                *list(self.req[-1]["tool_calls"]),
                {"id": _call_id, "type": "function", "function": {"name": "system__event_pipeline", "arguments": "{}"}},
            ]
            logger.info(f"[ChatAPI] Appended pipeline_events tool_call to existing assistant, call_id={_call_id}")
        else:
            logger.info(
                f"[ChatAPI] Injecting synthetic assistant message with tool_call for pipeline events, call_id={_call_id}"
            )
            synth_reasoning = ""
            for _lookback in reversed(self.req):
                if _lookback.get("role") == "assistant" and _lookback.get("reasoning_content"):
                    synth_reasoning = _lookback["reasoning_content"]
                    break
            synth_msg = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": _call_id,
                        "type": "function",
                        "function": {
                            "name": "system__event_pipeline",
                            "arguments": "{}",
                        },
                    }
                ],
            }
            if synth_reasoning:
                synth_msg["reasoning_content"] = synth_reasoning
                logger.info(
                    f"[ChatAPI] Carried reasoning_content ({len(synth_reasoning)} chars) into synthetic assistant for pipeline events"
                )
            self.req.append(synth_msg)

        self.req.append(
            {
                "role": "tool",
                "tool_call_id": _call_id,
                "name": "system__event_pipeline",
                "content": events_text,
            }
        )
        self.save_history()
        # Invalidate the token cache: the injected tool message changes the
        # total token count, so the next _prepare_messages threshold check
        # must recompute (otherwise compression may trigger too late).
        self.invalidate_token_cache()
        logger.debug(
            f"[ChatAPI] add_pipeline_events: events_len={len(events_text)}, "
            f"call_id={_call_id}, total_req_messages={len(self.req)}"
        )
        self._trim_history_if_needed()

    def pop_last_message(self) -> dict | None:
        """Remove and return the last message from history."""
        if len(self.req) > 1:  # Never pop the system prompt
            msg = self.req.pop()
            self.save_history()
            self.invalidate_token_cache()
            return msg
        return None

    def pop_last_assistant_message(self) -> bool:
        """Specifically find and remove the last assistant message if it's the last turn."""
        if self.req and self.req[-1]["role"] == "assistant":
            self.req.pop()
            self.save_history()
            self.invalidate_token_cache()
            logger.info("[ChatAPI] Popped last assistant message to break loop")
            return True
        return False

    def _force_text_only_modalities(self) -> bool:
        """Models like stepaudio-2.5-chat accept audio input but only return text."""
        name = (self.model or "").lower()
        return "stepaudio-2.5-chat" in name or (name.endswith("-chat") and "stepaudio" in name)

    def _truncate_image_prompt(self, prompt: str, max_chars: int = 512) -> str:
        text = (prompt or "").strip()
        if len(text) <= max_chars:
            return text
        return text[:max_chars]

    async def _save_generated_image_bytes(self, raw: bytes, mime: str = "image/png") -> dict | None:
        """Persist generated image bytes into output_media_dir and return output_media item."""
        if not raw or not self.output_media_dir:
            logger.warning("[ChatAPI] Cannot save image output: empty bytes or output_media_dir unset")
            return None
        try:
            os.makedirs(self.output_media_dir, exist_ok=True)
            ext = mime.split("/")[-1].replace("jpeg", "jpg") if mime else "png"
            if ext not in ("png", "jpg", "jpeg", "webp", "gif"):
                ext = "png"
            fname = f"agent_img_{uuid.uuid4().hex[:12]}.{ext}"
            fpath = os.path.join(self.output_media_dir, fname)
            # Generated media: write it off the event loop.
            await blocking_io.write_bytes(fpath, raw)
            item = {"type": "image", "url": f"/uploads/{fname}", "mime": mime or "image/png"}
            logger.info(f"[ChatAPI] Saved image output: {fname}")
            return item
        except Exception as e:
            logger.error(f"[ChatAPI] Failed to save image output: {e}")
            return None

    async def _collect_images_api_result(self, result) -> list[dict]:
        """Convert OpenAI Images API result into output_media list."""
        output_media: list[dict] = []
        data = getattr(result, "data", None) or []
        for item in data:
            b64 = getattr(item, "b64_json", None)
            if b64:
                try:
                    raw = base64.b64decode(b64)
                except Exception as e:
                    logger.error(f"[ChatAPI] Invalid b64_json from images API: {e}")
                    continue
                saved = await self._save_generated_image_bytes(raw, "image/png")
                if saved:
                    output_media.append(saved)
                continue
            url = getattr(item, "url", None)
            if url:
                # Remote URL — download when possible so UI can serve via /uploads/
                try:
                    import httpx

                    # PERF-11: reuse a module-level client instead of opening a
                    # fresh connection per image download.
                    global _image_download_client
                    if _image_download_client is None or _image_download_client.is_closed:
                        _image_download_client = httpx.AsyncClient(timeout=60.0)
                    resp = await _image_download_client.get(url)
                    resp.raise_for_status()
                    ctype = resp.headers.get("content-type", "image/png").split(";")[0].strip()
                    saved = await self._save_generated_image_bytes(resp.content, ctype or "image/png")
                    if saved:
                        output_media.append(saved)
                        continue
                except Exception as e:
                    logger.warning(f"[ChatAPI] Failed to download image url, falling back to remote url: {e}")
                output_media.append({"type": "image", "url": url, "mime": "image/png"})
        return output_media

    async def _chat_image_generation(
        self,
        user_message: str,
        image_path: list[str] | None = None,
    ) -> dict:
        """Generate an image via OpenAI-compatible /v1/images/generations (or /edits).

        Used when ``is_image_output`` is True on openai / openai_compat models
        such as StepFun ``step-image-edit-2``.
        """
        prompt = self._truncate_image_prompt(user_message)
        if not prompt:
            text = "<to_user>请描述你想生成的图片内容。</to_user>"
            self.add_assistant_message(text)
            self.save_history()
            if self.stream_parser:
                self.stream_parser.clean()
                self.stream_parser.feed(text)
                self.stream_parser.finish()
            return {
                "text": text,
                "tool_data": None,
                "output_media": [],
                "finish_reason": "stop",
                "stream_error": False,
                "timed_out": False,
            }

        logger.info(
            "[ChatAPI] Image generation via Images API: model=%s prompt_len=%d has_input_image=%s",
            self.model,
            len(prompt),
            bool(image_path),
        )
        self.printer.dynamic_single_callback("正在生成图片…\n")

        output_media: list[dict] = []
        err_msg = ""
        try:
            extra_body = {
                "cfg_scale": float(self.image_cfg_scale),
                "steps": int(self.image_steps),
            }
            client = self._ensure_client()
            edit_paths = [p for p in (image_path or []) if p and os.path.isfile(p)]
            if edit_paths and self.is_img_model:
                # Image editing path (StepFun / OpenAI images.edits).
                # Read the source image off the event loop and hand the SDK a
                # (filename, bytes) pair: reading it inline stalls every WS push
                # while the multipart body is assembled.
                edit_bytes = await blocking_io.read_bytes(edit_paths[0])
                result = await client.images.edit(
                    model=self.model,
                    image=(os.path.basename(edit_paths[0]), edit_bytes),
                    prompt=prompt,
                    response_format="b64_json",
                    extra_body=extra_body,
                )
            else:
                result = await client.images.generate(
                    model=self.model,
                    prompt=prompt,
                    size=self.image_size or "1024x1024",
                    response_format="b64_json",
                    n=1,
                    extra_body=extra_body,
                )
            output_media = await self._collect_images_api_result(result)
            self.total_requests += 1
        except Exception as e:
            err_msg = str(e)
            logger.error(f"[ChatAPI] Image generation failed: {e}")

        if output_media:
            text = "<to_user>已根据你的描述生成图片。</to_user>"
        else:
            detail = err_msg or "未返回图片数据"
            text = f"<to_user>图片生成失败：{detail}</to_user>"

        self.add_assistant_message(text)
        self.save_history()
        if self.stream_parser:
            self.stream_parser.clean()
            self.stream_parser.feed(text)
            self.stream_parser.finish()
        self.printer.dynamic_single_callback("已生成图片\n" if output_media else f"生成失败：{err_msg}\n")

        return {
            "text": text,
            "tool_data": None,
            "output_media": output_media,
            "finish_reason": "stop",
            "stream_error": False,
            "timed_out": False,
        }

    async def chat(
        self,
        user_message: str,
        image_path: list[str] | None = None,
        image_b64_list: list[dict] | None = None,
        audio_path: list[str] | None = None,
        video_path: list[str] | None = None,
        tools: list[dict] | None = None,
        tool_choice: str = "auto",
        tool_call_strategy=None,
        skip_add_user: bool = False,
    ):
        """
        Call LLM API

        Args:
            user_message: User input text
            image_path: List of image file paths
            image_b64_list: List of base64-encoded images
            audio_path: List of audio file paths
            video_path: List of video file paths
            tools: OpenAI Tools JSON Schema (for Native Function Calling)
            tool_choice: "auto" | "required" | "none"
            tool_call_strategy: ToolCallStrategy instance for parsing tool calls
            skip_add_user: If True, do NOT call add_user_message(). Use this when
                external events are already injected via add_pipeline_events(role=tool)
                to avoid duplicating the message as both role=user and role=tool.

        Returns:
            dict: {"text": response_text, "tool_data": (tool_name, tool_args) or None}
        """
        # Inject multimodal content (images) when skip_add_user=True, but only if model supports images
        if skip_add_user and image_path and self.is_img_model:
            if self.req:
                for m in reversed(self.req):
                    if m.get("role") == "user":
                        img_content = []
                        for img in image_path:
                            try:
                                import base64 as _b64

                                # User-sized image: read it off the event loop.
                                data = await blocking_io.read_bytes(img)
                                encoded = _b64.b64encode(data).decode("utf-8")
                                img_content.append(
                                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{encoded}"}}
                                )
                            except Exception as e:
                                logger.error(f"[ChatAPI] Failed to encode image {img}: {e}")
                        if img_content:
                            if isinstance(m.get("content"), list):
                                m["content"].extend(img_content)
                            else:
                                old_text = m.get("content", "")
                                m["content"] = [{"type": "text", "text": old_text}, *img_content]
                            logger.info(f"[ChatAPI] Injected {len(img_content)} image(s) into existing user message")
                        break
        elif not skip_add_user:
            self.add_user_message(
                user_message, image_path, image_b64_list=image_b64_list, audio_path=audio_path, video_path=video_path
            )

        # OpenAI-compatible text-to-image / image-edit models (e.g. StepFun step-image-edit-2)
        if self.is_image_output:
            return await self._chat_image_generation(user_message, image_path=image_path)

        self._last_tools = tools
        messages = self._prepare_messages()

        # CRITICAL FIX: 孤儿 tool_calls 修复 —— 内存 req 继续对话（并行会话保持
        # req 跨 turn 存活，_has_mem_history=true 时不再 _load_history）时，上一轮
        # 工具循环若在 add_tool_result 之前中断（Stop/异常），req 里会残留
        # assistant(tool_calls) 而无对应 role=tool 响应，重发即触发 400：
        #   "An assistant message with 'tool_calls' must be followed by tool messages
        #    responding to each 'tool_call_id'"
        # session_manager._repair_orphan_tool_calls 负责补齐占位 tool 响应
        # （返回新列表，不改 self.req 与落盘数据）。
        messages = _session_module.SessionManager._repair_orphan_tool_calls(messages)

        # DeepSeek V4 thinking mode: reasoning_content must be passed back on
        # the turn FOLLOWING a tool-call turn — i.e. the most recent assistant
        # message, not every historical assistant message. The previous
        # "inject into ALL" logic copied the latest reasoning into every
        # tool_calls message (121 on session 151735_a7s7), multiplying the
        # request ~2.75x (362K -> 998K tokens) and pushing real requests past
        # the 1M context limit (400).
        has_tool_involvement = any(m.get("tool_calls") or m.get("role") == "tool" for m in messages)

        # Debug logging
        prev_reasoning_len = len(self._prev_reasoning_content) if self._prev_reasoning_content else 0
        logger.info(
            f"[ChatAPI] reasoning_content check: prev_len={prev_reasoning_len}, has_tool_involvement={has_tool_involvement}, msg_count={len(messages)}"
        )

        if self._prev_reasoning_content and messages:
            injected = 0
            for m in messages:
                if m.get("role") != "assistant":
                    continue
                # With tool involvement, the following assistant turn must carry
                # reasoning; without it, any assistant message qualifies. We
                # patch EVERY assistant message that is missing reasoning_content
                # (not just the most recent one): Console Go's thinking mode
                # requires reasoning_content to be passed back on every
                # historical tool-call assistant message. Pure tool-loop turns
                # (request ends with a tool message) return NO reasoning from the
                # upstream, so those assistant messages land in history without
                # the field — leaving even one of them unpatched triggers a 400
                # ("The reasoning_content in the thinking mode must be passed
                # back to the API"). Only MISSING ones are patched (typically a
                # handful), so this stays far below the token blow-up that the
                # old "inject into ALL 121 messages" logic caused.
                needs = (m.get("tool_calls") or m.get("content")) if not has_tool_involvement else m.get("tool_calls")
                if not needs:
                    continue
                if "reasoning_content" not in m:
                    m["reasoning_content"] = self._prev_reasoning_content
                    injected += 1
            if injected:
                logger.debug(
                    f"[ChatAPI] Injected _prev_reasoning_content into {injected} assistant message(s) missing reasoning, len={len(self._prev_reasoning_content)}"
                )
            else:
                # P1-6: failing to inject is EXPECTED when the conversation has
                # no assistant message yet (e.g. a fresh session whose first
                # message is user-only) — that is not an error worth a WARNING
                # on every turn. Only surface it when assistant messages exist
                # but none matched, which would indicate a real regression.
                has_assistant = any(m.get("role") == "assistant" for m in messages)
                if has_assistant:
                    logger.warning(
                        "[ChatAPI] FAILED to inject reasoning_content: no assistant message with tool_calls found"
                    )
                else:
                    logger.debug("[ChatAPI] reasoning_content not injected (no assistant message in conversation yet)")

        # DEBUG: log message sequence before API call
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(f"[ChatAPI] DEBUG messages sent to API ({len(messages)}):")
            for i, m in enumerate(messages):
                role = m.get("role", "?")
                content = m.get("content", "")
                content_preview = (content[:200] + "...") if content and len(content) > 200 else (content or "(None)")
                has_tool_calls = "tool_calls" in m
                has_reasoning = "reasoning_content" in m
                logger.debug(
                    f"[ChatAPI]   [{i}] role={role}, content_len={len(content) if content else 0}, tool_calls={has_tool_calls}, reasoning_content={has_reasoning}, content_preview={content_preview}"
                )

        # Build API request parameters
        request_params = {"model": self.model, "messages": messages, "stream": True, "temperature": self.temperature}

        # Streaming usage: without this OpenAI-compatible endpoints send NO
        # `usage` object at all (verified against Ark: 66 chunks, usage=None),
        # so the counters below silently degrade to a local tokenizer estimate
        # and `total_cache_read_tokens` can never leave 0.  The field is
        # standard on every OpenAI-compatible backend; endpoints that reject it
        # are handled by dropping the parameter and retrying (see the
        # `_stream_options_rejected` branch in the retry loop).
        if not self._stream_options_rejected:
            request_params["stream_options"] = {"include_usage": True}

        from opensquad.reasoning_effort import apply_openai_compat_thinking_params

        apply_openai_compat_thinking_params(
            request_params,
            is_think=bool(getattr(self, "is_think", False)),
            effort=getattr(self, "reasoning_effort", "high"),
            model=self.model or "",
            base_url=self.base_url or "",
        )

        extra_headers = {}
        sid = self._sid_provider() if self._sid_provider else None
        uid = self._user_id_provider() if self._user_id_provider else None
        if sid:
            extra_headers["X-Session-Id"] = sid
        if uid:
            extra_headers["X-User-Id"] = uid
        if extra_headers:
            request_params["extra_headers"] = extra_headers
        if self.frequency_penalty != 0.0:
            request_params["frequency_penalty"] = self.frequency_penalty
        if self.presence_penalty != 0.0:
            request_params["presence_penalty"] = self.presence_penalty

        # Prompt caching (OpenAI-compat): DeepSeek-compatible endpoints opt in
        # via chat_template_kwargs.cache.use; OpenAI/other providers cache the
        # stable system prefix automatically, so nothing is injected there.
        apply_deepseek_prompt_cache(
            request_params,
            prompt_cache=self._enable_prompt_cache,
            base_url=self.base_url or "",
            model=self.model or "",
        )

        # Add tools parameter if provided (for Native Function Calling)
        if tools:
            request_params["tools"] = tools
            request_params["tool_choice"] = tool_choice
            logger.debug(f"[ChatAPI] Using Native Function Calling with {len(tools)} tools")

        # Audio output modality
        if self.is_audio_output and not self._force_text_only_modalities():
            request_params["modalities"] = ["text", "audio"]
            request_params["audio"] = {"voice": self.audio_output_voice, "format": "wav"}
            logger.debug(f"[ChatAPI] Audio output enabled, voice={self.audio_output_voice}")
        elif self._force_text_only_modalities():
            request_params["modalities"] = ["text"]
            logger.debug("[ChatAPI] Forcing modalities=['text'] for text-only audio chat model")

        full_response = []
        collected_reasoning = []
        audio_output_chunks: list = []  # Collect model audio output base64 chunks
        tool_calls_detected = False  # Flag for Native FC tool call detection
        stream_usage = None  # Capture usage info from stream
        finish_reason = None  # Track the finish reason
        parsed_tool_data = None  # Store tool data parsed during streaming
        stream_error = False  # Track stream interruption
        if self.stream_parser:
            self.stream_parser.clean()
            # Reset parser ignore settings at the start of each turn
            self.stream_parser._buffered_tags.add("thought")  # Ensure thought can be buffered or streamed as needed
            # Use a temporary attribute to mark whether native thought has occurred this turn
            self._turn_has_native_thought = False
            self._thought_dropper = StreamingThoughtBlockDropper()

        def _is_timeout_error(exc: Exception) -> bool:
            cls = type(exc).__name__.lower()
            msg = str(exc).lower()
            return ("timeout" in cls) or ("timed out" in msg) or ("readtimeout" in cls)

        def _is_rate_limit_error(exc: Exception) -> bool:
            """Detect 429 Rate Limit / Quota Exceeded errors."""
            msg = str(exc).lower()
            return (
                ("429" in msg)
                or ("rate_limit" in msg)
                or ("rate limit" in msg)
                or ("insufficient_quota" in msg)
                or ("quota" in msg)
                or ("coding_plan_cluster_rate_limited" in msg)
                or ("high demand" in msg)
            )

        def _is_connection_error(exc: Exception) -> bool:
            """Detect network/connection errors (APIConnectionError, ConnectError, etc.)."""
            cls = type(exc).__name__.lower()
            msg = str(exc).lower()
            return (
                ("apiconnectionerror" in cls)
                or ("connectionerror" in cls)
                or ("connecterror" in cls)
                or ("connection error" in msg)
                or ("connection reset" in msg)
                or ("connection refused" in msg)
                or ("connection closed" in msg)
                or ("failed to establish" in msg)
                or ("network is unreachable" in msg)
                or ("temporarily unavailable" in msg)
                or ("econnaborted" in msg)
                or ("econnreset" in msg)
                or ("econnrefused" in msg)
            )

        def _is_stream_options_unsupported_error(exc: Exception) -> bool:
            """Detect an endpoint rejecting ``stream_options`` (unknown parameter).

            Strict OpenAI-compatible proxies answer 400 with the parameter name
            in the message; some only say "extra fields not permitted".  Either
            way the request is otherwise valid, so the caller retries without it
            rather than failing the turn.
            """
            msg = str(exc).lower()
            if "stream_options" not in msg and "include_usage" not in msg:
                return False
            return any(
                token in msg for token in ("unknown", "unsupported", "not support", "unrecognized", "invalid", "400")
            )

        def _is_image_not_supported_error(exc: Exception) -> bool:
            """Detect errors indicating the model/provider does not support image input."""
            msg = str(exc).lower()
            return (
                ("no endpoints found that support image" in msg)
                or ("image input" in msg and "not support" in msg)
                or ("does not support image" in msg)
                or ("vision" in msg and "not support" in msg)
                or ("multimodal" in msg and "not support" in msg)
                or ("image_url" in msg and "not support" in msg)
            )

        def _is_auth_error(exc: Exception) -> bool:
            """Detect authentication/authorization errors (401, 403) that should not be retried."""
            cls = type(exc).__name__.lower()
            msg = str(exc).lower()
            return (
                ("authentication" in cls)
                or ("authenticationerror" in cls)
                or ("permissiondenied" in cls)
                or ("permission" in cls)
                or ("401" in msg)
                or ("403" in msg)
                or ("invalid api key" in msg)
                or ("invalid_api_key" in msg)
                or ("api key" in msg and "invalid" in msg)
                or ("unauthorized" in msg)
                or ("access denied" in msg)
            )

        def _strip_images_from_messages(msgs: list) -> list:
            """Remove image_url content from messages, keeping only text."""
            cleaned = []
            for m in msgs:
                m_copy = dict(m)
                content = m_copy.get("content")
                if isinstance(content, list):
                    text_parts = [p for p in content if isinstance(p, dict) and p.get("type") == "text"]
                    if text_parts:
                        m_copy["content"] = text_parts
                cleaned.append(m_copy)
            return cleaned

        max_stream_retries = 6  # Per-type budget for stream timeout / rate limit handling
        max_connection_retries = 10  # Network errors: up to 10 retries, exponential backoff capped at 60s
        timeout_retries = 0
        conn_retries = 0
        rate_retries = 0
        # Last exception that triggered a retry. If the retry loop is exhausted
        # right after a retry (continue on the final iteration), the loop ends
        # without break and stream_error stays False -- surface this exception
        # instead of the misleading "unknown streaming failure".
        last_retry_exc: Exception | None = None
        stream_ok = False
        stream_stopped = False
        _images_stripped = False  # Track if images were stripped due to unsupported error

        # Live tool-arg streaming for Agent Web (write/edit file code blocks).
        # Only the live WS feed consumes these frames — the Gateway reader
        # filters tool_call_delta from every history read (the final tool_call
        # carries full args), so persisting them here is pure write
        # amplification and is intentionally skipped.
        if tool_call_strategy and hasattr(tool_call_strategy, "set_delta_callback"):

            def _on_tool_call_delta(payload):
                self._emit_with_sid("tool_call_delta", payload)

            tool_call_strategy.set_delta_callback(_on_tool_call_delta)

        client = self._ensure_client()
        # Loop bound covers the worst case where every per-type budget is used
        # (timeout 6 + rate limit 6 + connection 10) plus one final error pass.
        for attempt in range(max_stream_retries * 2 + max_connection_retries + 1):
            got_any_chunk = False
            try:
                stream = await client.chat.completions.create(**request_params)

                async for chunk in stream:
                    got_any_chunk = True
                    # Check for stop request; break streaming early if requested
                    # (agent-wide or this session — parallel panes use session stop).
                    _stop_sid = self._sid_provider() if self._sid_provider else None
                    if input_hub.is_stop_requested() or (
                        _stop_sid and input_hub.is_session_stop_requested(str(_stop_sid))
                    ):
                        logger.info("[ChatAPI] Stop requested during streaming, breaking")
                        stream_stopped = True
                        aclose = getattr(stream, "aclose", None) or getattr(stream, "close", None)
                        if callable(aclose):
                            try:
                                res = aclose()
                                if hasattr(res, "__await__"):
                                    await res
                            except Exception:
                                logger.debug("[ChatAPI] stream aclose on stop skipped", exc_info=True)
                        break

                    if not chunk.choices:
                        # Some proxy APIs return chunks with empty choices list
                        # (e.g., GitHub Copilot proxy). These chunks may still carry
                        # important metadata like usage info or finish_reason at the
                        # chunk level. Extract what we can before skipping.
                        logger.debug("[ChatAPI] Received chunk with empty choices, checking for metadata")
                        # Check for usage info at chunk level
                        chunk_usage = getattr(chunk, "usage", None)
                        if chunk_usage:
                            stream_usage = chunk_usage

                        # CRITICAL: Even with empty choices, we MUST feed the chunk to
                        # the tool_call_strategy so it can check for finish_reason and
                        # return any buffered tool calls.
                        if tool_call_strategy:
                            chunk_tool_data = tool_call_strategy.parse_response(chunk)
                            if chunk_tool_data:
                                parsed_tool_data = chunk_tool_data
                                logger.info(
                                    f"[ChatAPI] Tool data parsed from empty-choices chunk: {chunk_tool_data[0]}"
                                )

                        # Also try to extract finish_reason from choices[0] even when
                        # the choices list is empty -- some proxies return choices=[]
                        # but still have a finish_reason in the first (empty) choice
                        if hasattr(chunk, "choices") and chunk.choices is not None:
                            try:
                                chunk_finish_reason = (
                                    getattr(chunk.choices[0], "finish_reason", None) if len(chunk.choices) > 0 else None
                                )
                                if chunk_finish_reason:
                                    finish_reason = chunk_finish_reason
                                    logger.debug(
                                        f"[ChatAPI] Captured finish_reason from empty-choices chunk: {finish_reason}"
                                    )
                            except (IndexError, AttributeError):
                                pass
                        # Also check chunk-level finish_reason
                        chunk_finish_reason = getattr(chunk, "finish_reason", None)
                        if chunk_finish_reason:
                            finish_reason = chunk_finish_reason
                            logger.debug(f"[ChatAPI] Captured finish_reason from chunk level: {finish_reason}")
                        continue

                    delta = chunk.choices[0].delta

                    # Capture usage info (some APIs return it in the last chunk's usage field)
                    chunk_usage = getattr(chunk, "usage", None)
                    if chunk_usage:
                        stream_usage = chunk_usage

                    # Capture finish_reason (used for tool call detection)
                    chunk_finish_reason = getattr(chunk.choices[0], "finish_reason", None)
                    if chunk_finish_reason:
                        finish_reason = chunk_finish_reason
                        logger.debug(f"[ChatAPI] Captured finish_reason: {finish_reason}")

                    # Feed chunk to strategy for tool call parsing (Native FC mode)
                    # Strategy accumulates tool_calls and returns result when finish_reason is received
                    if tool_call_strategy:
                        chunk_tool_data = tool_call_strategy.parse_response(chunk)
                        if chunk_tool_data:
                            parsed_tool_data = chunk_tool_data
                            logger.info(f"[ChatAPI] Tool data parsed in stream: {chunk_tool_data[0]}")

                    # Detect Native Function Calling tool_calls (streaming mode)
                    if hasattr(delta, "tool_calls") and delta.tool_calls:
                        if not tool_calls_detected:
                            tool_calls_detected = True
                            # Send progress hint to user
                            logger.info("[ChatAPI] Native FC tool call detected in stream")

                    # 1. Handle native reasoning process (OpenAI o1 / DeepSeek R1 style)
                    reasoning = getattr(delta, "reasoning_content", None) or getattr(delta, "reasoning", None)
                    if reasoning:
                        self._turn_has_native_thought = True
                        collected_reasoning.append(reasoning)
                        self._emit_with_sid("thought", reasoning)
                        if self.stream_parser:
                            # Mentions of <thought> in the body must stay literal.
                            self.stream_parser.set_passthrough_tags(("thought", "think"))

                    # 2. Handle regular content
                    content = delta.content
                    if content:
                        full_response.append(content)
                        self.printer.dynamic_single_callback(content)
                        if self.stream_parser:
                            feed = content
                            if self._turn_has_native_thought:
                                dropper = getattr(self, "_thought_dropper", None)
                                if dropper is None:
                                    dropper = StreamingThoughtBlockDropper()
                                    self._thought_dropper = dropper
                                feed = dropper.feed(content)
                            if feed:
                                self.stream_parser.feed(feed)

                    # 3. Handle audio output delta (OpenAI audio modality)
                    audio_delta = getattr(delta, "audio", None)
                    if audio_delta:
                        chunk_data = getattr(audio_delta, "data", None)
                        if chunk_data:
                            audio_output_chunks.append(chunk_data)
                        transcript = getattr(audio_delta, "transcript", None)
                        if transcript:
                            full_response.append(transcript)
                            self.printer.dynamic_single_callback(transcript)
                            if self.stream_parser:
                                self.stream_parser.feed(transcript)

                if stream_stopped:
                    break
                stream_ok = True
                break
            except Exception as e:
                is_timeout = _is_timeout_error(e)
                is_rate_limit = _is_rate_limit_error(e)
                is_connection = _is_connection_error(e)
                is_image_error = _is_image_not_supported_error(e)
                is_stream_options_error = _is_stream_options_unsupported_error(e)
                is_auth_error = _is_auth_error(e)
                can_retry_timeout = is_timeout and (timeout_retries < max_stream_retries) and (not got_any_chunk)
                can_retry_rate_limit = is_rate_limit and (rate_retries < max_stream_retries)
                can_retry_connection = is_connection and (conn_retries < max_connection_retries) and (not got_any_chunk)
                can_retry_image = is_image_error and (not _images_stripped)

                if is_auth_error:
                    stream_error = True
                    logger.warning("[ChatAPI] API auth error (not retrying): %s", e)
                    full_response.append(f"\n[Error: {type(e).__name__} - {e}]")
                    break

                if is_stream_options_error and "stream_options" in request_params:
                    # Endpoint predates stream_options. Drop it and retry: the
                    # turn still works, it just loses provider usage (and with
                    # it the cache hit rate) — the panel reports that as
                    # "unavailable" rather than a fake 0%.
                    logger.warning(
                        "[ChatAPI] Endpoint rejected stream_options.include_usage, "
                        "retrying without it — provider usage stats will be unavailable: %s",
                        e,
                    )
                    request_params.pop("stream_options", None)
                    self._stream_options_rejected = True
                    continue

                if can_retry_image:
                    # Model/provider doesn't support image input -- strip images and retry
                    logger.warning(f"[ChatAPI] Image not supported by model, stripping images and retrying: {e}")
                    request_params["messages"] = _strip_images_from_messages(request_params["messages"])
                    # Append a text notice so the model still knows images arrived
                    try:
                        msgs = request_params["messages"]
                        for m in reversed(msgs):
                            if m.get("role") != "user":
                                continue
                            notice = (
                                "[System notice] The user also sent image(s), but this model/provider "
                                "rejected image input. Acknowledge that images were received and "
                                "answer the user's text; do not claim you can see the images."
                            )
                            content = m.get("content")
                            if isinstance(content, list):
                                m["content"] = list(content) + [{"type": "text", "text": notice}]
                            elif isinstance(content, str):
                                m["content"] = (content or "") + "\n\n" + notice
                            else:
                                m["content"] = notice
                            break
                    except Exception as _ne:
                        logger.debug(f"[ChatAPI] Failed to inject image-strip notice: {_ne}")
                    _images_stripped = True
                    # Also mark is_img_model=False so subsequent turns skip images
                    self.is_img_model = False
                    continue

                if can_retry_timeout:
                    timeout_retries += 1
                    wait_s = 0.8 * timeout_retries
                    last_retry_exc = e
                    logger.warning(
                        f"[ChatAPI] Stream timeout before first chunk, retrying ({timeout_retries}/{max_stream_retries}) after {wait_s:.1f}s: {e}"
                    )
                    await asyncio.sleep(wait_s)
                    continue

                if can_retry_rate_limit:
                    # Exponential backoff: 5s, 10s, 20s, 40s, 80s... capped at 600s (10 min)
                    rate_retries += 1
                    wait_s = min(5 * (2 ** (rate_retries - 1)), 600)
                    last_retry_exc = e
                    logger.warning(
                        f"[ChatAPI] Rate limit / quota exceeded, retrying ({rate_retries}/{max_stream_retries}) after {wait_s:.0f}s: {e}"
                    )
                    await asyncio.sleep(wait_s)
                    continue

                if can_retry_connection:
                    # Network / connection error: exponential backoff 2s, 4s, 8s, 16s, 32s
                    # then capped at 60s; give up after max_connection_retries (10).
                    conn_retries += 1
                    wait_s = min(2**conn_retries, 60)
                    last_retry_exc = e
                    logger.warning(
                        f"[ChatAPI] Connection error, retrying ({conn_retries}/{max_connection_retries}) after {wait_s:.0f}s: {e}"
                    )
                    await asyncio.sleep(wait_s)
                    continue

                stream_error = True
                # Print the full exception chain so the real httpx-level cause
                # (ConnectError / ReadError / RemoteProtocolError / proxy, etc.)
                # is visible instead of just the SDK's generic "Connection error."
                _cause = e.__cause__ or e.__context__
                logger.error(
                    f"[ChatAPI] Stream error: {type(e).__name__}: {e}"
                    f" | underlying: {type(_cause).__name__ if _cause else 'None'}: {_cause}"
                )
                full_response.append(f"\n[Error: {type(e).__name__} - {e}]")
                break

        if not stream_ok and not stream_error and not stream_stopped:
            stream_error = True
            if last_retry_exc is not None:
                # The retry loop was exhausted right after a retry -- report the
                # real error instead of "unknown streaming failure".
                full_response.append(f"\n[Error: {type(last_retry_exc).__name__} - {last_retry_exc}]")
            else:
                full_response.append("\n[Error: Stream interrupted - unknown streaming failure]")

        res_text = "".join(full_response)
        if tool_call_strategy and hasattr(tool_call_strategy, "set_delta_callback"):
            tool_call_strategy.set_delta_callback(None)

        if self.stream_parser:
            if getattr(self, "_turn_has_native_thought", False):
                dropper = getattr(self, "_thought_dropper", None)
                if dropper is not None:
                    tail = dropper.flush()
                    if tail:
                        self.stream_parser.feed(tail)
            self.stream_parser.finish()

        # Native reasoning_content plus XML <thought> dumps in the body.
        # Drop real line-start blocks (including inner text); leave mentions
        # and fenced samples intact so the live bubble is not hole-punched.
        if collected_reasoning:
            stripped = strip_prompted_thought_blocks(res_text)
            if stripped != res_text:
                logger.warning(
                    "[ChatAPI] Double-think detected: model output a prompted "
                    "<thought>/<think> block despite having native reasoning_content. Stripping."
                )
                res_text = stripped.strip()

        # If native reasoning content was collected, we need to:
        # 1. Save the clean content (without reasoning) for the API message's "content" field
        # 2. Pass reasoning separately as "reasoning_content" field (required by DeepSeek thinking mode)
        # 3. Keep res_text with <think> wrapper for backward compatibility (XML parsing in runner)
        api_content = res_text
        api_reasoning = None
        if collected_reasoning:
            api_reasoning = "".join(collected_reasoning)
            res_text = f"<think>{api_reasoning}</think>\n{res_text}"

        # Accumulate token consumption statistics
        # NOTE: total_requests is now counted in add_user_message() to match user's perspective:
        # the entire tool chain triggered by one user message counts as ONE conversation turn.
        if stream_usage:
            # API returned real usage data
            self.total_input_tokens += getattr(stream_usage, "prompt_tokens", 0) or 0
            self.total_output_tokens += getattr(stream_usage, "completion_tokens", 0) or 0
            # Cache-hit prompt tokens.  OpenAI/Ark use
            # `usage.prompt_tokens_details.cached_tokens`, DeepSeek puts
            # `prompt_cache_hit_tokens` at the top level, Gemini's compat layer
            # may return either — one extractor covers all of them.
            self.total_cache_read_tokens += extract_cached_tokens(stream_usage)
            self.usage_reported_turns += 1
            # `messages` is exactly what was sent, so this is a real measurement
            # of the provider/local token ratio — the compression trigger uses it
            # instead of trusting the local estimate blindly.
            self.record_token_calibration(getattr(stream_usage, "prompt_tokens", 0) or 0, messages, self._last_tools)
        else:
            # Fallback: estimate based on tiktoken. Cache read is unknowable
            # here, so the turn is recorded as estimated and the context panel
            # refuses to print a hit rate for it.
            self.total_input_tokens += self._count_tokens(messages, self._last_tools)
            self.total_output_tokens += len(self.encoding.encode(res_text)) if res_text else 0
            self.usage_estimated_turns += 1

        from opensquad.turn_trace import has_unclosed_tool_call

        persist_assistant = True
        if stream_stopped and has_unclosed_tool_call(api_content or ""):
            persist_assistant = False
            logger.info("[ChatAPI] Stop: not persisting unclosed <tool_call> as assistant")
        if persist_assistant:
            self.add_assistant_message(
                api_content,
                reasoning_content=api_reasoning,
                # Bugfix: a native-FC turn may return ONLY tool_calls with empty
                # content. The assistant message MUST still be recorded (req +
                # session) so the tool-result continuation has a valid anchor and
                # the UI workflow does not show a dangling tool step after refresh.
                force_record=bool(parsed_tool_data) or finish_reason == "tool_calls",
            )

        # CRITICAL FIX: Remove the premature tool_calls injection into self.req.
        # The fix previously added tool_calls to self.req BEFORE the runner called
        # add_tool_result(), which caused orphaned tool_call_ids in self.req when
        # add_tool_result() overwrites the tool_calls array with a new single-element
        # list (see line 451: last_msg["tool_calls"] = [{...}]). For parallel tools,
        # this caused only the last tool_call to be preserved while orphaned IDs
        # remained, triggering DeepSeek 400 errors:
        #   "An assistant message with 'tool_calls' must be followed by tool messages"
        #
        # add_tool_result() already handles this correctly: it checks if
        # self.req[-1].role == "assistant" and either amends the existing message
        # or creates a new one with reasoning_content preserved (lines 448-488).
        # The premature injection here was redundant and harmful.

        # CRITICAL: Save reasoning_content for next turn (DeepSeek V4 requires it to be passed back when tools are involved)
        if api_reasoning:
            self._prev_reasoning_content = api_reasoning
            logger.info(f"[ChatAPI] Saved _prev_reasoning_content for next turn, len={len(api_reasoning)}")
        self.save_history()

        # Extract tool call data using strategy (if provided)
        tool_data = None
        if tool_call_strategy:
            # Prioritize tool_data parsed during streaming
            if parsed_tool_data:
                tool_data = parsed_tool_data
                logger.info(f"[ChatAPI] Using tool_data from stream: {tool_data[0]}")
            elif finish_reason:
                # Fallback: try to parse from buffer (in case finish_reason came without tool_calls)
                final_response = type(
                    "Response",
                    (),
                    {
                        "choices": [
                            type("Choice", (), {"finish_reason": finish_reason, "delta": type("Delta", (), {})()})()
                        ]
                    },
                )()
                tool_data = tool_call_strategy.parse_response(final_response)
                if tool_data:
                    logger.info(f"[ChatAPI] Extracted tool_data from final parse: {tool_data[0]}")
                else:
                    logger.debug(f"[ChatAPI] No tool_data in buffer (finish_reason={finish_reason})")
            else:
                logger.warning("[ChatAPI] finish_reason is None, cannot extract tool_data")
        else:
            logger.debug("[ChatAPI] No strategy provided, tool_data will be None")

        # CRITICAL FIX: Before processing tool_data, ensure reasoning_content is preserved in assistant message
        # When the stream had BOTH reasoning_content AND tool_calls, the reasoning was stored in collected_reasoning
        # but the tool_call block (at finish_reason='tool_calls') may have cleared the content before we could capture it.
        # We need to ensure the final assistant message includes reasoning_content.
        if collected_reasoning and finish_reason == "tool_calls":
            # There's reasoning but we haven't added the assistant message yet for tool_calls case
            # The reasoning will be in collected_reasoning, content in full_response
            logger.info(
                f"[ChatAPI] reasoning_content preserved for tool_calls turn, reasoning_len={len(''.join(collected_reasoning))}"
            )

        # Return both text and tool call data
        output_media = []
        if audio_output_chunks and self.output_media_dir:
            try:
                import uuid as _uuid

                os.makedirs(self.output_media_dir, exist_ok=True)
                fname = f"agent_audio_{_uuid.uuid4().hex[:12]}.wav"
                fpath = os.path.join(self.output_media_dir, fname)
                raw_bytes = b"".join(base64.b64decode(c) for c in audio_output_chunks)
                # Generated media: write it off the event loop.
                await blocking_io.write_bytes(fpath, raw_bytes)
                output_media.append({"type": "audio", "url": f"/uploads/{fname}", "mime": "audio/wav"})
                logger.info(f"[ChatAPI] Saved audio output: {fname}")
            except Exception as e:
                logger.error(f"[ChatAPI] Failed to save audio output: {e}")

        result = {
            "text": res_text,
            "tool_data": tool_data,
            "output_media": output_media,
            "finish_reason": finish_reason,
            "stream_error": stream_error,
            "timed_out": bool(stream_error and not finish_reason and "timed out" in res_text.lower()),
        }
        logger.info(f"[ChatAPI] Returning dict with tool_data={'present' if tool_data else 'None'}")
        return result
