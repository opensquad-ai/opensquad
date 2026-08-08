import asyncio
import base64
import json
import logging
import re
import threading
import uuid
from collections import OrderedDict

from .system_config import syscfg
from .xml_parser import StreamingTagParser

try:
    from tool import logger
except ImportError:
    from .tool import logger
import contextlib
import os

from . import session_manager as _session_module
from .events import bus
from .input_hub import input_hub
from .model_config import ModelConfig
from .utils import CharPrinter

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


__all__ = ["ChatAPI"]


class ChatAPI:
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
        self._enable_prompt_cache = bool(getattr(config, "prompt_cache", False))
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
        self._sid_provider = None  # Injected by Runner; returns the session_id for the current turn
        self._user_id_provider = None  # Injected by Runner; returns the user_id for the current turn
        self._latest_summary = ""  # Context compression summary (for {{CONTEXT_SUMMARY}} injection)
        self._auto_compressed = False  # Flag: did auto-compression run during the last chat() call?
        self._auto_compress_stats = {}  # Stats from last auto-compression (tokens_before, tokens_after, etc.)
        self._last_tools = None  # Cached tools from last chat() call, used for accurate token counting
        self._prev_reasoning_content = (
            ""  # CRITICAL: Must be passed back to DeepSeek V4 in next turn when tools are involved
        )

        # ── Incremental token counter (P0 perf optimization) ──
        # Avoids re-encoding all messages on every _prepare_messages() call.
        # Incremented in add_user_message/add_tool_result/add_assistant_message,
        # invalidated on compression/pop/hot-reload.
        self._cached_token_count: int | None = None  # None = needs full recount
        self._cached_tools_token_count: int = 0  # tokens from _last_tools

        # ── Per-message token cache (P1 perf optimization) ──
        # Avoids re-encoding the same message content across repeated
        # _prepare_messages() calls. Keyed by content hash.
        self._msg_token_cache: OrderedDict[int, int] = OrderedDict()
        self._msg_token_cache_max_size = 5000

        # Cumulative token consumption statistics
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_requests = 0
        self.total_cache_read_tokens = 0

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

    def _emit_with_sid(self, etype, data):
        """Send an event with session_id (obtained via sid_provider injected by Runner)"""
        sid = self._sid_provider() if self._sid_provider else None
        if sid:
            bus.emit(etype, {"sid": sid, "data": data})
        else:
            bus.emit(etype, data)

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
            # If it has content text, keep it and add tool_calls
            last_msg["tool_calls"] = [
                {
                    "id": tool_call_id,
                    "type": "function",
                    "function": {
                        "name": tool_name,
                        "arguments": json.dumps(tool_args, ensure_ascii=False) if tool_args else "{}",
                    },
                }
            ]
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

    def _prepare_messages(self) -> list[dict]:
        """
        Prepare the message list to send to the API, applying smart context compression.

        Strategy:
        - Trigger: when total_tokens > token_max * trigger_threshold (default 0.75).
        - Retention: newest ~10% of tokens are kept verbatim (by token count, not rounds).
        - Summarize: everything between first_user_msg and the recent retained portion.

        Both regular messages and tool_result messages are treated identically —
        tool_result with large payloads will be summarized, not preserved.
        """
        import time as _time

        _t0 = _time.monotonic()

        # Reset auto-compression flag for this call
        self._auto_compressed = False
        self._auto_compress_stats = {}

        # 1. Count current tokens (uses incremental cache when available)
        current_tokens = self.get_current_token_count(self._last_tools)
        threshold = syscfg.ctx_trigger_threshold()
        # Use the model's declared context window. A 1M-token model must not
        # be compacted at 128k just because that is the common card default.
        threshold_tokens = int(self.token_max * threshold)

        # PERF-3 (400-token hard guard): the local tiktoken/cl100k estimate
        # systematically undercounts (~2.6-3x) versus the provider's real
        # accounting (DeepSeek uses ~1.35 chars/token; relay wrappers inflate
        # further).  If the *scaled* estimate would exceed a high watermark
        # (85% of max), force compression so the request never hits a 400.
        # This is a safety net on top of the normal threshold-based path.
        _scaled_estimate = current_tokens * 3
        _hard_watermark = int(self.token_max * 0.85)
        if _scaled_estimate > _hard_watermark and current_tokens <= threshold_tokens:
            logger.warning(
                "[CompressTrace] HARD GUARD triggered: local estimate %d tokens, scaled x3 = %d > 85%% of max %d. "
                "Forcing compression to avoid 400.",
                current_tokens,
                _scaled_estimate,
                self.token_max,
            )
            self._emit_with_sid("status", "Context near limit, compacting (hard guard)...")

        if current_tokens <= threshold_tokens and _scaled_estimate <= _hard_watermark:
            logger.info("[CompressTrace] below threshold, no compression needed")
            return self.req

        logger.warning(
            "[CompressTrace] context compression TRIGGERED (%.1f%% of max)",
            current_tokens / self.token_max * 100,
        )
        self._emit_with_sid("status", "Context limit reached, compacting...")

        if len(self.req) < 5:
            # Too few messages to compress meaningfully — keep all
            logger.info("[CompressTrace] too few messages (%d), skipping compression", len(self.req))
            return self.req

        # 3. Compression strategy
        system_msg = self.req[0]

        # Find the first user message (original intent)
        first_user_msg = None
        first_user_idx = 0
        for i in range(1, len(self.req)):
            if self.req[i]["role"] == "user":
                first_user_msg = self.req[i]
                first_user_idx = i
                break

        logger.info(
            "[CompressTrace] scan: total_msgs=%d, first_user_idx=%d",
            len(self.req),
            first_user_idx,
        )

        # Compute per-message token counts
        msg_tokens = []  # list of (index, token_count)
        for i, msg in enumerate(self.req):
            t = self._count_message_tokens(msg)
            msg_tokens.append((i, t))

        # Determine retention boundary: newest messages whose cumulative tokens
        # are <= keep_recent_fraction (default 0.10 = 10%) of total.
        keep_frac = syscfg.ctx_keep_recent_fraction()
        keep_token_budget = int(current_tokens * keep_frac)

        recent_start = len(self.req)  # default: no recent portion
        recent_token_sum = 0
        for idx, tok in reversed(msg_tokens):
            if recent_token_sum + tok <= keep_token_budget:
                recent_token_sum += tok
                recent_start = idx
            else:
                break

        # recent_start must be at least after first_user_msg
        if first_user_msg is not None:
            recent_start = max(recent_start, first_user_idx + 1)

        # Ensure the recent section still covers recent user turns so the agent
        # doesn't lose sight of the current task — BUT cap how far back we pull.
        # In a long autonomous tool-calling run the 2nd-to-last user message can
        # sit near the very start of the conversation (e.g. idx=2 with 370+
        # messages of tool I/O after it). "Protecting" it by pulling recent_start
        # all the way back swallows the entire context and leaves the summarize
        # range empty, so compression becomes a no-op and tokens keep climbing.
        # We refuse to extend if doing so would exceed a hard cap on the recent
        # section; the user message then just gets summarized like everything
        # else.
        user_indices = [i for i in range(len(self.req)) if self.req[i].get("role") == "user"]
        recent_hard_cap = int(current_tokens * syscfg.ctx_recent_hard_cap_frac())
        for anchor in (
            user_indices[-2] if len(user_indices) >= 2 else None,
            user_indices[-1] if user_indices else None,
        ):
            if anchor is None or anchor >= recent_start:
                continue
            candidate_tokens = sum(t for _, t in msg_tokens[anchor:])
            if candidate_tokens <= recent_hard_cap:
                logger.warning(
                    "[CompressTrace] extending recent_start to include user at "
                    "idx=%d (was recent_start=%d, candidate_tokens=%d <= cap=%d)",
                    anchor,
                    recent_start,
                    candidate_tokens,
                    recent_hard_cap,
                )
                recent_start = min(recent_start, anchor)
            else:
                logger.warning(
                    "[CompressTrace] NOT extending to user at idx=%d: would add "
                    "%d tokens, exceeding recent hard cap %d (will be summarized)",
                    anchor,
                    candidate_tokens,
                    recent_hard_cap,
                )

        # CRITICAL: Ensure recent_start doesn't split a tool_call/tool_result pair.
        # If the first message in recent_msgs has role="tool", we must include its
        # preceding assistant message with tool_calls, otherwise DeepSeek rejects
        # the request with "Messages with role 'tool' must be a response to a
        # preceding message with 'tool_calls'".
        # FIX: recent_start may equal len(self.req) when the newest message is too
        # large to fit the retention budget (kept at its initial value). Guard the
        # index to avoid IndexError that kills the whole tool flow mid-turn.
        while recent_start > 0 and recent_start < len(self.req) and self.req[recent_start].get("role") == "tool":
            recent_start -= 1
            logger.warning(
                "[CompressTrace] tool message at recent_start, extending to include "
                "preceding assistant (new recent_start=%d, role=%s)",
                recent_start,
                self.req[recent_start].get("role"),
            )
        # Also scan the first few messages in recent block for orphan tool messages
        for offset in range(min(3, len(self.req) - recent_start)):
            idx = recent_start + offset
            if self.req[idx].get("role") == "tool":
                needed = idx - 1
                while needed >= 0 and self.req[needed].get("role") != "assistant":
                    needed -= 1
                if needed >= 0 and self.req[needed].get("tool_calls"):
                    recent_start = min(recent_start, needed)
                    logger.warning(
                        "[CompressTrace] orphan tool at idx=%d, extending recent_start "
                        "to %d (assistant with tool_calls)",
                        idx,
                        recent_start,
                    )
                    break

        recent_msgs = self.req[recent_start:]
        end_scan = recent_start

        # Compression range: from after first_user_msg up to recent_start
        start_scan = first_user_idx + 1 if first_user_msg else 1

        logger.info(
            "[CompressTrace] retention: keep_frac=%.2f, keep_budget=%d tokens, "
            "recent_start=%d, recent_msgs=%d, recent_tokens=%d, "
            "summarize_range=[%d, %d) msgs=%d",
            keep_frac,
            keep_token_budget,
            recent_start,
            len(recent_msgs),
            recent_token_sum,
            start_scan,
            end_scan,
            end_scan - start_scan,
        )

        if start_scan >= end_scan:
            # Anchor pullback (or a degenerate conversation) swallowed the
            # entire compression range. NEVER return uncompressed context here
            # — that defeats the whole point of triggering compression and
            # leaves tokens pinned above the limit. Force a token-budget-only
            # split that drops user anchors and guarantees a non-empty summarize
            # range so the summarizer actually runs.
            logger.warning(
                "[CompressTrace] compression range empty (start=%d end=%d), "
                "forcing token-budget-only retention (dropping user anchors)",
                start_scan,
                end_scan,
            )
            recent_start = len(self.req)
            acc = 0
            for idx, tok in reversed(msg_tokens):
                if acc + tok <= keep_token_budget:
                    acc += tok
                    recent_start = idx
                else:
                    break
            # Leave at least one message for the summarizer.
            min_scan = min((first_user_idx + 2) if first_user_msg else 2, len(self.req))
            if recent_start < min_scan:
                recent_start = min_scan
            # Re-run tool-pair integrity fix on the forced boundary.
            while recent_start > 0 and recent_start < len(self.req) and self.req[recent_start].get("role") == "tool":
                recent_start -= 1
            recent_msgs = self.req[recent_start:]
            end_scan = recent_start
            start_scan = (first_user_idx + 1) if first_user_msg else 1
            logger.warning(
                "[CompressTrace] forced recent_start=%d, summarize_range=[%d, %d) msgs=%d",
                recent_start,
                start_scan,
                end_scan,
                end_scan - start_scan,
            )
            if start_scan >= end_scan:
                # Degenerate tiny conversation — nothing to summarize, keep all.
                partial = [system_msg]
                if first_user_msg:
                    partial.append(first_user_msg)
                partial.extend(recent_msgs)
                logger.info(
                    "[CompressTrace] skip summary: msgs=%d, tokens_before=%d, tokens_after=%d",
                    len(partial),
                    current_tokens,
                    self._count_tokens(partial, self._last_tools),
                )
                return partial

        # 4. Generate summary
        dropped_count = end_scan - start_scan
        msgs_to_summarize = self.req[start_scan:end_scan]
        summarize_tokens = sum(t for _, t in msg_tokens[start_scan:end_scan])

        _t1 = _time.monotonic()
        logger.info(
            "[CompressTrace] calling summarizer: %d messages, %d tokens, build_wait=%.2fs",
            dropped_count,
            summarize_tokens,
            _t1 - _t0,
        )

        summary_content = self._generate_summary(msgs_to_summarize)

        _t2 = _time.monotonic()
        logger.info(
            "[CompressTrace] summarizer returned: summary_len=%d chars, elapsed=%.2fs",
            len(summary_content),
            _t2 - _t1,
        )

        # Capture prior summary BEFORE overwriting — runner needs it for
        # compress_current_session(previous_summary=...).
        previous_summary_snapshot = (getattr(self, "_latest_summary", "") or "").strip()

        self._latest_summary = f"[Context summary | Compressed {dropped_count} messages]\n{summary_content}"

        compacted_req = [system_msg]
        if first_user_msg:
            compacted_req.append(first_user_msg)
        compacted_req.extend(recent_msgs)

        new_token_count = self._count_tokens(compacted_req, self._last_tools)

        logger.info(
            "[CompressTrace] compression result: msgs: %d -> %d, tokens: %d -> %d, saved=%d (%.1f%%)",
            len(self.req),
            len(compacted_req),
            current_tokens,
            new_token_count,
            current_tokens - new_token_count,
            (current_tokens - new_token_count) / max(current_tokens, 1) * 100,
        )

        # CRITICAL FIX: Preserve reasoning_content from the ORIGINAL pre-compression
        # self.req before it gets overwritten by compacted_req.
        # The holder message (with reasoning_content) may be outside recent_msgs and get dropped;
        # we MUST keep a copy in self._prev_reasoning_content so the inject logic at lines
        # 949-990 can re-attach it to every assistant message in the compacted context.
        _last_reasoning = None
        for m in reversed(self.req):  # scan original (not yet overwritten)
            if m.get("role") == "assistant" and m.get("reasoning_content"):
                _last_reasoning = m.get("reasoning_content")
                break

        self.req = compacted_req
        self.invalidate_token_cache()  # Compression changed message list

        if _last_reasoning:
            self._prev_reasoning_content = _last_reasoning
            logger.info(
                f"[CompressTrace] Preserved _prev_reasoning_content after auto-compression, len={len(_last_reasoning)}"
            )

        # Fingerprint of the first kept recent message so runner can align the
        # disk archive cut with this recent_start boundary.
        first_kept_role = ""
        first_kept_content = ""
        for m in recent_msgs:
            role = m.get("role") or ""
            if role not in ("user", "assistant"):
                continue
            content = m.get("content", "")
            if isinstance(content, list):
                parts = []
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "text":
                        parts.append(str(item.get("text") or ""))
                content = "\n".join(parts)
            content = str(content or "").strip()
            if not content:
                continue
            first_kept_role = role
            first_kept_content = content[:240]
            break

        # Signal auto-compression to runner (which will emit summary_stream + history_sync)
        self._auto_compressed = True
        self._auto_compress_stats = {
            "tokens_before": current_tokens,
            "tokens_after": new_token_count,
            "messages_before": len(self.req) + dropped_count,
            "messages_after": len(self.req),
            "dropped_count": dropped_count,
            "summarize_range": [start_scan, end_scan],
            "recent_start": recent_start,
            "recent_tokens": recent_token_sum,
            "keep_frac": keep_frac,
            "previous_summary": previous_summary_snapshot,
            "first_kept_role": first_kept_role,
            "first_kept_content": first_kept_content,
        }
        logger.info(
            "[CompressTrace] auto-compression COMPLETE (total_elapsed=%.2fs): %d -> %d tokens, %d messages retained",
            _time.monotonic() - _t0,
            current_tokens,
            new_token_count,
            len(self.req),
        )

        return self.req

    def _tail_msgs_for_rounds(self, n_rounds: int) -> list[dict]:
        """Return the tail messages covering the most recent n_rounds user turns.

        IMPORTANT: tool_result messages are NOT preserved in the recent section.
        They are included in the compression range so their large payloads get summarized.
        This prevents tool_result from dominating the context even in recent rounds.
        """
        msgs = self.req[1:]  # Skip system msg
        user_turn_count = 0
        for i in range(len(msgs) - 1, -1, -1):
            msg = msgs[i]
            role = msg.get("role", "")
            content = msg.get("content", "")
            # Check if this is a tool_result (role=user but content is tool result)
            is_tool_result = (
                role == "user"
                and isinstance(content, list)
                and any(
                    isinstance(item, dict) and item.get("type") in ("tool_result", "functionResponse")
                    for item in content
                )
            )
            # Also exclude role:"tool" messages — they are not user turns
            is_tool_role = role == "tool"
            # Only count actual user messages (not tool_result, not tool role)
            if role == "user" and not is_tool_result and not is_tool_role:
                user_turn_count += 1
                if user_turn_count >= n_rounds:
                    # Return recent messages but EXCLUDE tool_result from preservation.
                    # tool_result will fall in the compression range and be summarized.
                    tail = msgs[i:]
                    return [m for m in tail if not self._is_tool_result_msg(m)]
        return msgs  # fallback: return all non-system messages

    @staticmethod
    def _is_tool_result_msg(msg: dict) -> bool:
        """Check if a message is a tool_result (large payload that should be compressed)."""
        role = msg.get("role", "")
        content = msg.get("content", "")
        if role == "tool":
            return True
        if role == "user" and isinstance(content, list):
            return any(
                isinstance(item, dict) and item.get("type") in ("tool_result", "functionResponse") for item in content
            )
        return False

    def _build_conv_text(self, messages: list[dict], budget_chars: int) -> str:
        """
        Convert a message list to text using an overall budget rather than per-message truncation.
        Tool call results are prioritized; remaining space is allocated proportionally per message.
        """
        # First pass: extract full text of each message
        items = []
        for m in messages:
            role = m.get("role", "unknown")
            content = m.get("content", "")
            if isinstance(content, list):
                text_parts = []
                for item in content:
                    if not isinstance(item, dict):
                        continue
                    t = item.get("type", "")
                    if t == "text":
                        text_parts.append(item["text"])
                    elif t == "tool_use":
                        inp = json.dumps(item.get("input", {}), ensure_ascii=False)[:300]
                        text_parts.append(f"[tool_use: {item.get('name', '?')}({inp})]")
                    elif t == "tool_result":
                        for c in item.get("content") or []:
                            if isinstance(c, dict) and c.get("type") == "text":
                                text_parts.append(c["text"])
                content = "\n".join(text_parts)
            else:
                content = str(content)
            items.append((role, content))

        # Second pass: compute total length, truncate proportionally
        total_chars = sum(len(c) for _, c in items)
        if total_chars <= budget_chars:
            return "\n".join(f"{role}: {content}" for role, content in items)

        n = len(items)
        min_per_msg = 200
        base_alloc = max(min_per_msg, budget_chars // n)

        parts = []
        for role, content in items:
            if len(content) <= base_alloc:
                parts.append(f"{role}: {content}")
            else:
                parts.append(f"{role}: {content[:base_alloc]}...[truncated]")
        return "\n".join(parts)

    def _generate_summary(self, messages: list[dict]) -> str:
        """Use LLM to generate a state-snapshot style summary of the message list.

        Uses streaming to match manual compression behavior and improve compatibility
        with various API providers that may handle streaming differently than non-streaming.
        """
        budget = syscfg.ctx_conv_text_budget_chars()
        max_tokens = syscfg.ctx_summary_max_tokens()
        conv_text = self._build_conv_text(messages, budget)

        # Use dedicated summarizer model if configured (same logic as manual compression in runner.py)
        summary_model = syscfg.get("summarizer", "model") or self.model

        system_prompt = (
            "You are a summarizer agent. Return ONLY the summary in the specified template. "
            "Do not add commentary or extra sections."
        )

        user_prompt = (
            "You are compressing conversation history for an AI Agent that is currently executing a task.\n"
            "The compression result will replace this history; the Agent must be able to seamlessly continue working based on your summary.\n\n"
            "[Hard rules - the following must be preserved verbatim, never rewritten or omitted]\n"
            "- All file paths and directory names\n"
            "- All IDs, ports, version numbers, and configuration values\n"
            "- The original text of all error messages\n"
            "- Requirements, constraints, or preferences explicitly specified by the user\n"
            "- The most recent user request (what the agent is currently working on)\n\n"
            "[Output format - be specific, include exact values, avoid vague summaries]\n\n"
            "## Current Task\n"
            "(What the agent is working on RIGHT NOW — the most recent user request in detail)\n\n"
            "## Original Goal\n"
            "(The very first user request in this session, in one sentence)\n\n"
            "## Completed\n"
            "(Operations successfully executed and confirmed, with key output values and file paths)\n\n"
            "## Current State\n"
            "(What state the system/files/code is in right now — this is the most important section. "
            "Include open files, current working directory, last tool executed, etc.)\n\n"
            "## Key Parameters\n"
            "(Exact values that will definitely be needed going forward: paths, configs, API addresses, port numbers, etc.)\n\n"
            "## Unresolved Issues\n"
            "(Explicitly existing blockers, errors, or incomplete steps; omit this section if none)\n\n"
            "---\n"
            f"Conversation history to compress:\n{conv_text}"
        )

        # Pre-check: estimate prompt token count
        try:
            full_prompt_text = system_prompt + "\n" + user_prompt
            estimated_prompt_tokens = len(_get_tiktoken().encode(full_prompt_text))
            logger.info(
                "[CompressTrace] summary prompt: model=%s, chars=%d, estimated_tokens=%d, max_tokens=%d",
                summary_model,
                len(full_prompt_text),
                estimated_prompt_tokens,
                max_tokens,
            )
        except (TypeError, AttributeError):
            pass  # Tokenizer may fail, continue anyway

        try:
            # Use a dedicated client for summarization to avoid interfering with main agent client
            summary_client = _get_openai()(
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=180,  # Generous timeout for large context compression via streaming
            )

            # Use streaming to match manual compression behavior and improve API compatibility
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

            # Collect streaming chunks
            parts: list[str] = []
            prompt_tokens = 0
            completion_tokens = 0
            finish_reason = None

            for chunk in stream:
                if not chunk.choices:
                    # Extract usage from chunk with empty choices (some APIs do this)
                    chunk_usage = getattr(chunk, "usage", None)
                    if chunk_usage:
                        prompt_tokens = getattr(chunk_usage, "prompt_tokens", 0) or 0
                        completion_tokens = getattr(chunk_usage, "completion_tokens", 0) or 0
                    continue

                delta = chunk.choices[0].delta
                if delta and delta.content:
                    parts.append(delta.content)

                # Capture finish_reason and usage from the last chunk
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
                return "Summary generation returned empty response. Please rely on the First User Query."
            return content
        except Exception as e:
            logger.error(f"Summary generation failed: {e}")
            return "Summary generation failed. Please rely on the First User Query."

    def _count_tokens(self, messages: list[dict], tools: list[dict] | None = None) -> int:
        num_tokens = 0
        try:
            for message in messages:
                num_tokens += 4
                role = message.get("role", "")
                if role:
                    num_tokens += len(self.encoding.encode(role))
                if message.get("name"):
                    num_tokens += len(self.encoding.encode(message["name"])) + 1
                if message.get("tool_call_id"):
                    num_tokens += len(self.encoding.encode(message["tool_call_id"])) + 1
                if role == "tool":
                    num_tokens += 2
                for key, value in message.items():
                    if key == "content":
                        if isinstance(value, str):
                            num_tokens += len(self.encoding.encode(value))
                        elif value is None:
                            num_tokens += 1
                        elif isinstance(value, list):
                            from opensquad.token_breakdown import count_multimodal_content_tokens

                            for item in value:
                                if not isinstance(item, dict):
                                    continue
                                if item.get("type") == "text":
                                    num_tokens += len(self.encoding.encode(item["text"]))
                                elif item.get("type") == "image_url":
                                    detail = item.get("image_url", {}).get("detail", "auto")
                                    num_tokens += 1105 if detail == "high" else 85
                                elif item.get("type") in ("audio_url", "video_url"):
                                    num_tokens += 120
                            # Claude tool_result / Gemini functionResponse / tool_use
                            num_tokens += count_multimodal_content_tokens(value, self.encoding)
                    elif key == "reasoning_content" and isinstance(value, str):
                        # Thinking text uploads with the message (DeepSeek V4
                        # requires it on follow-up turns) and counts toward the
                        # provider's input tokens; previously skipped (~2.5%
                        # undercount on the 151735_a7s7 session).
                        num_tokens += len(self.encoding.encode(value))
                    elif key == "tool_calls" and isinstance(value, list):
                        from opensquad.token_breakdown import tool_fn_text

                        for tc in value:
                            num_tokens += 8
                            tc_id = tc.get("id", "") if isinstance(tc, dict) else ""
                            if tc_id:
                                num_tokens += len(self.encoding.encode(tc_id))
                            fn = tc.get("function", {}) if isinstance(tc, dict) else {}
                            text = tool_fn_text(fn if fn else tc)
                            if text:
                                num_tokens += len(self.encoding.encode(text))
            if tools:
                for tool in tools:
                    num_tokens += 6
                    fn = tool.get("function", {})
                    if fn.get("name"):
                        num_tokens += len(self.encoding.encode(fn["name"]))
                    if fn.get("description"):
                        num_tokens += len(self.encoding.encode(fn["description"]))
                    if fn.get("parameters"):
                        num_tokens += len(self.encoding.encode(json.dumps(fn["parameters"], ensure_ascii=False)))
        except Exception as e:
            logger.warning(f"Token count error: {e}")
            return len(str(messages)) // 4

        num_tokens += 3
        return num_tokens

    def _count_message_tokens(self, message: dict) -> int:
        """Count tokens for a single message. Used by incremental counter."""
        # Fast path: content-based cache (messages are immutable once added)
        try:
            msg_key = hash(json.dumps(message, sort_keys=True, ensure_ascii=False))
            if msg_key in self._msg_token_cache:
                # True LRU: move to end (most recently used)
                self._msg_token_cache.move_to_end(msg_key)
                return self._msg_token_cache[msg_key]
        except (TypeError, ValueError):
            msg_key = None

        num_tokens = 4
        role = message.get("role", "")
        if role:
            num_tokens += len(self.encoding.encode(role))
        if message.get("name"):
            num_tokens += len(self.encoding.encode(message["name"])) + 1
        if message.get("tool_call_id"):
            num_tokens += len(self.encoding.encode(message["tool_call_id"])) + 1
        if role == "tool":
            num_tokens += 2
        for key, value in message.items():
            if key == "content":
                if isinstance(value, str):
                    num_tokens += len(self.encoding.encode(value))
                elif value is None:
                    num_tokens += 1
                elif isinstance(value, list):
                    from opensquad.token_breakdown import count_multimodal_content_tokens

                    for item in value:
                        if not isinstance(item, dict):
                            continue
                        if item.get("type") == "text":
                            num_tokens += len(self.encoding.encode(item["text"]))
                        elif item.get("type") == "image_url":
                            detail = item.get("image_url", {}).get("detail", "auto")
                            num_tokens += 1105 if detail == "high" else 85
                        elif item.get("type") in ("audio_url", "video_url"):
                            num_tokens += 120
                    num_tokens += count_multimodal_content_tokens(value, self.encoding)
            elif key == "reasoning_content" and isinstance(value, str):
                # Thinking text uploads with the message (DeepSeek V4 requires
                # it on follow-up turns) and counts toward the provider's input
                # tokens; previously skipped (~2.5% undercount).
                num_tokens += len(self.encoding.encode(value))
            elif key == "tool_calls" and isinstance(value, list):
                from opensquad.token_breakdown import tool_fn_text

                for tc in value:
                    num_tokens += 8
                    tc_id = tc.get("id", "") if isinstance(tc, dict) else ""
                    if tc_id:
                        num_tokens += len(self.encoding.encode(tc_id))
                    fn = tc.get("function", {}) if isinstance(tc, dict) else {}
                    text = tool_fn_text(fn if fn else tc)
                    if text:
                        num_tokens += len(self.encoding.encode(text))
        if msg_key is not None:
            self._msg_token_cache[msg_key] = num_tokens
            # True LRU eviction: pop oldest (first) items when over capacity
            while len(self._msg_token_cache) > self._msg_token_cache_max_size:
                self._msg_token_cache.popitem(last=False)
        return num_tokens

    def _count_tools_tokens(self, tools: list[dict] | None) -> int:
        """Count tokens for tool definitions."""
        if not tools:
            return 0
        num_tokens = 0
        for tool in tools:
            num_tokens += 6
            fn = tool.get("function", {})
            if fn.get("name"):
                num_tokens += len(self.encoding.encode(fn["name"]))
            if fn.get("description"):
                num_tokens += len(self.encoding.encode(fn["description"]))
            if fn.get("parameters"):
                num_tokens += len(self.encoding.encode(json.dumps(fn["parameters"], ensure_ascii=False)))
        return num_tokens

    def get_current_token_count(self, tools: list[dict] | None = None) -> int:
        """Get the current token count, using incremental cache when possible.

        This is the preferred API for token counting. It maintains an
        incremental counter that is updated when messages are added,
        and only does a full recount when the cache is invalidated.
        """
        # If tools changed, recalculate tools tokens
        if tools is not None and tools is not self._last_tools:
            self._cached_tools_token_count = self._count_tools_tokens(tools)
            self._last_tools = tools

        # If cache is valid, use it
        if self._cached_token_count is not None:
            return self._cached_token_count + self._cached_tools_token_count + 3

        # Cache miss: full recount
        total = self._count_tokens(self.req, tools)
        self._cached_token_count = total - self._cached_tools_token_count - 3
        return total

    def invalidate_token_cache(self):
        """Invalidate the incremental token count cache.

        Call this after compression, pop_last_message, or any operation
        that modifies self.req without going through add_* methods.
        """
        self._cached_token_count = None

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
            with open(fpath, "wb") as f:
                f.write(raw)
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
                # Image editing path (StepFun / OpenAI images.edits)
                with open(edit_paths[0], "rb") as img_f:
                    result = await client.images.edit(
                        model=self.model,
                        image=img_f,
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
                                with open(img, "rb") as f:
                                    import base64 as _b64

                                    encoded = _b64.b64encode(f.read()).decode("utf-8")
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
            injected = False
            for m in reversed(messages):
                if m.get("role") != "assistant":
                    continue
                # With tool involvement, the following assistant turn must carry
                # reasoning; without it, any assistant message qualifies. Either
                # way only the MOST RECENT one needs it.
                needs = (m.get("tool_calls") or m.get("content")) if not has_tool_involvement else m.get("tool_calls")
                if not needs:
                    continue
                if "reasoning_content" not in m:
                    m["reasoning_content"] = self._prev_reasoning_content
                    injected = True
                    logger.debug(
                        f"[ChatAPI] Injected _prev_reasoning_content into latest assistant message, len={len(self._prev_reasoning_content)}"
                    )
                break
            if not injected:
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
        if self._enable_prompt_cache and (self.base_url or "").lower().find("deepseek") != -1:
            extra_body = dict(request_params.get("extra_body") or {})
            extra_body["chat_template_kwargs"] = {"cache": {"use": True}}
            request_params["extra_body"] = extra_body

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

        max_stream_retries = 6  # Increased for rate limit handling
        stream_ok = False
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
        for attempt in range(max_stream_retries + 1):
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

                    # 2. Handle regular content
                    content = delta.content
                    if content:
                        full_response.append(content)
                        self.printer.dynamic_single_callback(content)
                        if self.stream_parser:
                            # Core anti-collision logic: if native thought already occurred this turn
                            # and the current chunk contains thought tags, filter them out
                            if self._turn_has_native_thought:
                                # Fully remove <thought>...</thought> and its content to avoid interference
                                # In streaming, simply suppress these tag characters
                                content = content.replace("<thought>", "").replace("</thought>", "")
                                content = content.replace("<think>", "").replace("</think>", "")

                            self.stream_parser.feed(content)

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

                stream_ok = True
                break
            except Exception as e:
                is_timeout = _is_timeout_error(e)
                is_rate_limit = _is_rate_limit_error(e)
                is_image_error = _is_image_not_supported_error(e)
                is_auth_error = _is_auth_error(e)
                can_retry_timeout = is_timeout and (attempt < max_stream_retries) and (not got_any_chunk)
                can_retry_rate_limit = is_rate_limit and (attempt < max_stream_retries)
                can_retry_image = is_image_error and (not _images_stripped)

                if is_auth_error:
                    stream_error = True
                    logger.warning("[ChatAPI] API auth error (not retrying): %s", e)
                    full_response.append(f"\n[Error: {type(e).__name__} - {e}]")
                    break

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
                    wait_s = 0.8 * (attempt + 1)
                    logger.warning(
                        f"[ChatAPI] Stream timeout before first chunk, retrying ({attempt + 1}/{max_stream_retries}) after {wait_s:.1f}s: {e}"
                    )
                    await asyncio.sleep(wait_s)
                    continue

                if can_retry_rate_limit:
                    # Exponential backoff: 5s, 10s, 20s, 40s, 80s... capped at 600s (10 min)
                    wait_s = min(5 * (2**attempt), 600)
                    logger.warning(
                        f"[ChatAPI] Rate limit / quota exceeded, retrying ({attempt + 1}/{max_stream_retries}) after {wait_s:.0f}s: {e}"
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

        if not stream_ok and not stream_error:
            stream_error = True
            full_response.append("\n[Error: Stream interrupted - unknown streaming failure]")

        res_text = "".join(full_response)
        if tool_call_strategy and hasattr(tool_call_strategy, "set_delta_callback"):
            tool_call_strategy.set_delta_callback(None)

        if self.stream_parser:
            self.stream_parser.finish()

        # -- Double-think detection + fix --
        # If native reasoning_content was received this turn and the model's body also outputs <thought>...</thought>,
        # the model did not follow the instruction "don't output thought tags if native thinking is present".
        # Strip <thought>...</thought> (including content) with regex to avoid double-rendering in UI.
        _THOUGHT_RE = re.compile(r"<thought>.*?</thought>", re.DOTALL | re.IGNORECASE)
        _THINK_INLINE_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
        if collected_reasoning:
            if _THOUGHT_RE.search(res_text):
                logger.warning(
                    "[ChatAPI] Double-think detected: model output <thought>...</thought> "
                    "despite having native reasoning_content. Stripping prompted thought block."
                )
                res_text = _THOUGHT_RE.sub("", res_text).strip()
            # Also handle <think>...</think> (used by some models)
            if _THINK_INLINE_RE.search(res_text):
                logger.warning(
                    "[ChatAPI] Double-think detected: model output <think>...</think> "
                    "in content despite having native reasoning_content. Stripping."
                )
                res_text = _THINK_INLINE_RE.sub("", res_text).strip()

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
            # OpenAI cached tokens: usage.prompt_tokens_details.cached_tokens
            details = getattr(stream_usage, "prompt_tokens_details", None)
            if details:
                self.total_cache_read_tokens += getattr(details, "cached_tokens", 0) or 0
        else:
            # Fallback: estimate based on tiktoken
            self.total_input_tokens += self._count_tokens(messages, self._last_tools)
            self.total_output_tokens += len(self.encoding.encode(res_text)) if res_text else 0

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
                with open(fpath, "wb") as f:
                    f.write(raw_bytes)
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

    def _ensure_history_dir(self):
        """Lazy resolve history_dir from workspace (workspace may not be set at __init__ time)."""
        if self.history_dir is None:
            self.history_dir = syscfg.workspace_data_dir("ai_his_talk")
            os.makedirs(self.history_dir, exist_ok=True)

    def _initialize_history(self, topic: str | None):
        if not topic:
            return
        self._ensure_history_dir()
        self.history_file = os.path.join(self.history_dir, f"{topic}.json")
        if os.path.exists(self.history_file):
            try:
                with open(self.history_file, encoding="utf-8") as f:
                    self.req = json.load(f)
            except Exception as e:
                logger.error(f"Failed to load history: {e}")

    def save_history(self):
        if self.history_file:
            try:
                with open(self.history_file, "w", encoding="utf-8") as f:
                    json.dump(self.req, f, ensure_ascii=False, indent=2)
            except Exception as e:
                logger.error(f"Failed to save history: {e}")

    def get_cumulative_stats(self) -> dict:
        """Return cumulative token consumption statistics"""
        return {
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_tokens": self.total_input_tokens + self.total_output_tokens,
            "total_requests": self.total_requests,
            "cache_read_tokens": self.total_cache_read_tokens,
            "cache_creation_tokens": 0,  # OpenAI does not distinguish creation; all merged into cache_read
        }

    def list_sessions(self) -> list[str]:
        """List all historical session names"""
        self._ensure_history_dir()
        if not os.path.exists(self.history_dir):
            return []
        files = os.listdir(self.history_dir)
        return [f.replace(".json", "") for f in files if f.endswith(".json")]
