"""
GoogleAPI: Google Gemini native interface, feature-aligned with ChatAPI / ClaudeAPI

Dependency: pip install google-generativeai
Supported models: gemini-1.5-flash, gemini-1.5-pro, gemini-2.0-flash, gemini-2.5-pro, etc.
"""

import asyncio
import base64
import json
import logging
import os

try:
    import tiktoken
except ImportError:
    tiktoken = None

try:
    from .events import bus
    from .input_hub import input_hub
    from .model_config import ModelConfig
    from .system_config import syscfg
    from .xml_parser import StreamingTagParser
except ImportError:
    StreamingTagParser = None
    bus = None
    input_hub = None
    syscfg = None
    ModelConfig = None

from .utils import CharPrinter

try:
    import google.generativeai as _genai_mod

    _GENAI_AVAILABLE = True
except ImportError:
    _genai_mod = None
    _GENAI_AVAILABLE = False

logger = logging.getLogger(__name__)


# - GoogleAPI -


class GoogleAPI:
    """
    GoogleAPI v1.0: Google Gemini API native interface
    - Interface fully aligned with ChatAPI / ClaudeAPI (same method names, same field names)
    - Supports streaming output, image input, context compression, history persistence, Token stats
    - Chain-of-thought models (gemini-2.0-flash-thinking-exp, etc.) automatically filter out 'thought' parts
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
        stream_parser=None,
        load_his: str | None = None,
        is_img_model: bool | None = None,
        is_audio_model: bool | None = None,
        is_video_model: bool | None = None,
        use_file_api: bool | None = None,
        file_api_size_threshold: int | None = None,
        is_image_output: bool | None = None,
        top_k: int | None = None,
    ):
        """
        P2-1: Accepts either a ModelConfig dataclass (preferred) or legacy kwargs.

        Args:
            config: ModelConfig instance containing all parameters.
            **kwargs: Legacy individual parameters (deprecated but kept for compat).
        """
        if config is None:
            config = ModelConfig(
                api_key=api_key or "",
                model=model or "",
                base_url=base_url or "",
                prompt=prompt or "",
                timeout=timeout if timeout is not None else 120.0,
                token_max=token_max if token_max is not None else 1_000_000,
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
                is_image_output=is_image_output if is_image_output is not None else False,
                top_k=top_k if top_k is not None else 0,
            )
        self.config = config

        self.api_key = config.api_key
        self.model = config.model
        self.base_url = config.base_url  # Reserved field (can be used for Vertex AI or other custom endpoints)
        self.timeout = config.timeout
        self.temperature = config.temperature
        self.token_max = config.token_max
        self.is_img_model = config.is_img_model
        self.is_audio_model = config.is_audio_model
        self.is_video_model = config.is_video_model
        self.use_file_api = config.use_file_api
        self.file_api_size_threshold = config.file_api_size_threshold
        self.is_image_output = config.is_image_output
        self.top_k = config.top_k
        self.output_media_dir: str = ""  # set externally by agents_boot

        self._prompt_template = config.prompt
        # req[0] is always the system message, consistent with ChatAPI / ClaudeAPI
        self.req: list[dict] = [{"role": "system", "content": config.prompt}]
        self.stream_parser = stream_parser
        self.printer = CharPrinter(max_width=80)
        self.load_his = config.load_his

        self.history_dir = None  # Lazy: resolved on first use
        self.history_file = None
        self._initialize_history(config.load_his)

        self.reduction_strategy = config.reduction_strategy
        self.reduction_batch_size = config.reduction_batch_size
        self._sid_provider = None
        self._user_id_provider = None  # Injected by Runner; returns the user_id for the current turn
        self._latest_summary = ""

        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_requests = 0
        self.total_cache_read_tokens = 0
        self.total_cache_creation_tokens = 0
        # Google API's prompt_token_count is cumulative (not incremental);
        # record the previous value and only accumulate the delta each time to avoid triangular inflation.
        self._last_prompt_token_count = 0
        # ── Per-message token cache (P3 perf optimization) ──
        self._msg_token_cache: dict[int, int] = {}
        self._msg_token_cache_max_size = 5000

        if tiktoken:
            try:
                self.encoding = tiktoken.get_encoding("cl100k_base")
            except Exception:
                self.encoding = None
        else:
            self.encoding = None

        # Initialize Google Generative AI
        if _GENAI_AVAILABLE:
            _genai_mod.configure(api_key=self.api_key)
            self._genai = _genai_mod
            logger.info(f"GoogleAPI Initialized. Model: {self.model}")
        else:
            self._genai = None
            logger.error("[GoogleAPI] google-generativeai is not installed. Run: pip install google-generativeai")

    async def reload_model(self, model_cfg: dict):
        """Hot-reload model parameters without losing conversation history.

        Declared ``async`` to match the ``ChatAPI.reload_model`` contract so
        the runner can ``await`` it uniformly across all providers.
        """
        old_model = self.model
        self.api_key = model_cfg.get("api_key", self.api_key)
        self.model = model_cfg.get("model_name", self.model)
        self.base_url = model_cfg.get("base_url", self.base_url)
        self.temperature = model_cfg.get("temperature", self.temperature)
        self.token_max = model_cfg.get("token_max", self.token_max)
        self.is_img_model = model_cfg.get("is_image", self.is_img_model)
        self.is_audio_model = model_cfg.get("is_audio_model", self.is_audio_model)
        self.is_video_model = model_cfg.get("is_video", self.is_video_model)
        self.use_file_api = model_cfg.get("use_file_api", self.use_file_api)
        self.file_api_size_threshold = model_cfg.get("file_api_size_threshold", self.file_api_size_threshold)
        self.is_image_output = model_cfg.get("is_image_output", self.is_image_output)
        self.top_k = model_cfg.get("top_k", self.top_k)
        # Re-configure Google GenAI with new API key
        if _GENAI_AVAILABLE:
            _genai_mod.configure(api_key=self.api_key)
            self._genai = _genai_mod
        logger.info(f"[GoogleAPI] Model hot-reloaded: {old_model} -> {self.model}")

    # -- Prompt management (aligned with ChatAPI / ClaudeAPI) --

    def update_system_prompt(self, new_prompt: str):
        if self.req and self.req[0]["role"] == "system":
            self.req[0]["content"] = new_prompt
        else:
            self.req.insert(0, {"role": "system", "content": new_prompt})

    def get_system_prompt(self) -> str:
        return self.req[0]["content"] if self.req else ""

    def get_template(self) -> str:
        return self._prompt_template

    def set_template(self, template: str):
        self._prompt_template = template

    # -- Event dispatch --

    def _emit_with_sid(self, etype, data):
        sid = self._sid_provider() if self._sid_provider else None
        if bus:
            if sid:
                bus.emit(etype, {"sid": sid, "data": data})
            else:
                bus.emit(etype, data)

    # -- Message management --

    def add_user_message(
        self,
        message: str,
        image_path: list[str] | None = None,
        image_b64_list: list[dict] | None = None,
        audio_path: list[str] | None = None,
        video_path: list[str] | None = None,
    ):
        # Skip empty text-only messages (tool result already in history via add_tool_result)
        has_multimodal = bool(image_path or image_b64_list or audio_path or video_path)
        if not message and not has_multimodal:
            logger.debug(
                f"[GoogleAPI] Skipping empty user message (tool result already in history), req_len={len(self.req)}"
            )
            return

        logger.debug(
            f"[GoogleAPI] add_user_message: text_len={len(message)}, has_multimodal={has_multimodal}, req_len_before={len(self.req)}"
        )
        content = []

        if image_path and self.is_img_model:
            for img in image_path:
                block = self._encode_image_block(img)
                if block:
                    content.append(block)

        if image_b64_list and self.is_img_model:
            for img_data in image_b64_list:
                mime = img_data.get("mimeType", "image/png")
                b64 = img_data.get("data", "")
                if b64:
                    content.append({"type": "image", "source": {"type": "base64", "media_type": mime, "data": b64}})

        if audio_path and self.is_audio_model:
            for audio in audio_path or []:
                if not audio:
                    continue
                file_size = os.path.getsize(audio) if os.path.exists(audio) else 0
                if file_size > 20 * 1024 * 1024:
                    logger.warning(f"[GoogleAPI] Audio > 20MB, skipping inline: {audio}")
                    content.append(
                        {"type": "text", "text": f"[Audio file too large to send inline: {os.path.basename(audio)}]"}
                    )
                    continue
                try:
                    with open(audio, "rb") as f:
                        data = base64.b64encode(f.read()).decode("utf-8")
                    mime = self._guess_audio_mime(audio)
                    content.append({"type": "audio", "source": {"type": "base64", "media_type": mime, "data": data}})
                except Exception as e:
                    logger.error(f"[GoogleAPI] Audio encode error ({audio}): {e}")

        if video_path and self.is_video_model:
            for video in video_path or []:
                if not video:
                    continue
                file_size = os.path.getsize(video) if os.path.exists(video) else 0
                if file_size > 20 * 1024 * 1024:
                    logger.warning(f"[GoogleAPI] Video > 20MB, skipping inline: {video}")
                    content.append(
                        {"type": "text", "text": f"[Video file too large to send inline: {os.path.basename(video)}]"}
                    )
                    continue
                try:
                    with open(video, "rb") as f:
                        data = base64.b64encode(f.read()).decode("utf-8")
                    mime = self._guess_video_mime(video)
                    content.append({"type": "video", "source": {"type": "base64", "media_type": mime, "data": data}})
                except Exception as e:
                    logger.error(f"[GoogleAPI] Video encode error ({video}): {e}")

        content.append({"type": "text", "text": message})
        final_content = content if len(content) > 1 else message
        self.req.append({"role": "user", "content": final_content})

    def add_tool_result(self, tool_name: str, tool_args: dict, result: str, tool_call_id: str = ""):
        """Inject tool execution result into message history in Gemini-compatible format.

        Gemini's API uses functionResponse in user messages:
        - assistant message with `tool_calls` array (for OpenAI-style tracking)
        - user message with content containing functionResponse (Gemini native format)

        Both are stored so that _to_gemini_parts() and _split_history_and_last() can
        convert appropriately when sending to the API.

        Args:
            tool_name: name of the tool that was called
            tool_args: dict of arguments passed to the tool
            result: the tool execution result (plain text)
            tool_call_id: unique call identifier (auto-generated if empty)
        """
        import uuid

        if not tool_call_id:
            tool_call_id = f"call_{uuid.uuid4().hex[:8]}"

        _mode = ""

        # If the last message is an assistant message (from streaming text),
        # we need to keep its text content and ADD tool_calls to it.
        if self.req and self.req[-1].get("role") == "assistant":
            last_msg = self.req[-1]
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
            if not last_msg.get("content"):
                last_msg["content"] = None
            _mode = "amended_existing_assistant"
        else:
            # No preceding assistant message — create one with just tool_calls
            self.req.append(
                {
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
            )
            _mode = "created_new_assistant"

        # Gemini functionResponse format: user message with functionResponse part
        # For internal tracking we store a marker; _to_gemini_parts will convert it
        self.req.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "functionResponse",
                        "function_name": tool_name,
                        "response": {"name": tool_name, "content": str(result) if result else "(empty result)"},
                    }
                ],
            }
        )
        self.save_history()

        logger.info(
            f"[GoogleAPI] add_tool_result: tool={tool_name}, "
            f"call_id={tool_call_id}, mode={_mode}, "
            f"result_len={len(str(result))}, total_req_messages={len(self.req)}"
        )

    def add_pipeline_events(self, events_text: str):
        """Append accumulated pipeline events as a tool-result notification.

        Mirrors ``ChatAPI.add_pipeline_events`` so the runner can call it
        uniformly across providers. External messages are injected as a
        ``system__event_pipeline`` tool result rather than a fresh user turn,
        preserving the never-stop architecture.
        """
        if not events_text or not events_text.strip():
            return
        self.add_tool_result(
            tool_name="system__event_pipeline",
            tool_args={},
            result=events_text,
        )
        logger.debug(
            f"[GoogleAPI] add_pipeline_events: events_len={len(events_text)}, total_req_messages={len(self.req)}"
        )

    def add_assistant_message(self, content: str):
        if content:
            self.req.append({"role": "assistant", "content": content})

    def pop_last_message(self) -> dict | None:
        """Remove and return the last message from history."""
        if len(self.req) > 1:  # Never pop the system prompt
            msg = self.req.pop()
            self.save_history()
            return msg
        return None

    def pop_last_assistant_message(self) -> bool:
        """Specifically find and remove the last assistant message if it's the last turn."""
        if self.req and self.req[-1]["role"] == "assistant":
            self.req.pop()
            self.save_history()
            logger.info("[GoogleAPI] Popped last assistant message to break loop")
            return True
        return False

    # -- Image encoding (storage format consistent with ClaudeAPI; conversion done in _to_gemini_parts) --

    def _encode_image_block(self, img_path: str) -> dict | None:
        try:
            with open(img_path, "rb") as f:
                data = base64.b64encode(f.read()).decode("utf-8")
            mime = self._guess_mime(img_path)
            return {"type": "image", "source": {"type": "base64", "media_type": mime, "data": data}}
        except Exception as e:
            logger.error(f"[GoogleAPI] Image encode error ({img_path}): {e}")
            return None

    def _guess_mime(self, path: str) -> str:
        ext = os.path.splitext(path)[1].lower()
        return {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".gif": "image/gif",
            ".webp": "image/webp",
        }.get(ext, "image/jpeg")

    def _guess_audio_mime(self, path: str) -> str:
        ext = os.path.splitext(path)[1].lower()
        return {
            ".mp3": "audio/mp3",
            ".wav": "audio/wav",
            ".ogg": "audio/ogg",
            ".flac": "audio/flac",
            ".m4a": "audio/mp4",
            ".aac": "audio/aac",
            ".opus": "audio/opus",
            ".webm": "audio/webm",
        }.get(ext, "audio/mp3")

    def _guess_video_mime(self, path: str) -> str:
        ext = os.path.splitext(path)[1].lower()
        return {
            ".mp4": "video/mp4",
            ".webm": "video/webm",
            ".mov": "video/quicktime",
            ".avi": "video/x-msvideo",
            ".mkv": "video/x-matroska",
            ".flv": "video/x-flv",
            ".wmv": "video/x-ms-wmv",
            ".3gp": "video/3gpp",
        }.get(ext, "video/mp4")

    # -- Token counting --

    def _count_tokens(self, messages: list[dict], tools: list[dict] | None = None) -> int:
        """Estimate token consumption (based on tiktoken).

        Mirrors ``ChatAPI._count_tokens``: counts message tokens plus, when
        ``tools`` is provided, the per-tool definition overhead
        (name/description/parameters). The runner passes ``_last_tools`` here so
        the token-stat breakdown stays consistent across providers; without this
        parameter the call ``_count_tokens(req, tools)`` raises a TypeError.
        """
        if not self.encoding:
            return len(str(messages)) // 3
        try:
            num_tokens = sum(self._count_message_tokens(m) for m in messages)
            if tools:
                for tool in tools:
                    fn = tool.get("function", {}) if isinstance(tool, dict) else getattr(tool, "function", {})
                    num_tokens += 6
                    if fn.get("name"):
                        num_tokens += len(self.encoding.encode(fn["name"]))
                    if fn.get("description"):
                        num_tokens += len(self.encoding.encode(fn["description"]))
                    if fn.get("parameters"):
                        num_tokens += len(self.encoding.encode(json.dumps(fn["parameters"], ensure_ascii=False)))
            return num_tokens
        except (TypeError, AttributeError):
            return len(str(messages)) // 3

    def _count_message_tokens(self, message: dict) -> int:
        """Count tokens for a single message with content-hash cache."""
        try:
            msg_key = hash(json.dumps(message, sort_keys=True, ensure_ascii=False))
            if msg_key in self._msg_token_cache:
                return self._msg_token_cache[msg_key]
        except (TypeError, ValueError):
            msg_key = None

        num_tokens = 4
        if message.get("role") == "tool":
            num_tokens += 3
        content = message.get("content", "")
        if isinstance(content, str):
            num_tokens += len(self.encoding.encode(content))
        elif isinstance(content, list):
            for item in content:
                if item.get("type") == "text":
                    num_tokens += len(self.encoding.encode(item["text"]))
                elif item.get("type") == "image":
                    num_tokens += 300
                elif item.get("type") == "audio":
                    num_tokens += 1000
                elif item.get("type") == "video":
                    num_tokens += 3000
                elif item.get("type") == "functionResponse":
                    resp = item.get("response", {})
                    resp_content = resp.get("content", {})
                    if isinstance(resp_content, str):
                        num_tokens += len(self.encoding.encode(resp_content))
                    else:
                        num_tokens += len(self.encoding.encode(json.dumps(resp_content, ensure_ascii=False)))
        if "tool_calls" in message:
            num_tokens += 6
            for tc in message["tool_calls"]:
                func = tc.get("function", {})
                num_tokens += len(self.encoding.encode(func.get("name", "")))
                num_tokens += len(self.encoding.encode(func.get("arguments", "")))

        if msg_key is not None:
            self._msg_token_cache[msg_key] = num_tokens
            if len(self._msg_token_cache) > self._msg_token_cache_max_size:
                keep = self._msg_token_cache_max_size * 2 // 3
                self._msg_token_cache = dict(list(self._msg_token_cache.items())[-keep:])
        return num_tokens

    # -- Context compression (aligned with ClaudeAPI) --

    def _prepare_messages(self) -> list[dict]:
        current_tokens = self._count_tokens(self.req)
        threshold = syscfg.ctx_trigger_threshold() if syscfg else 0.75
        if current_tokens <= self.token_max * threshold:
            return self.req

        logger.warning(
            f"[GoogleAPI] Context limit reached ({current_tokens}/{self.token_max}, threshold={threshold}). Compacting."
        )
        self._emit_with_sid("status", "Context limit reached, compacting...")
        self._emit_with_sid("info", f"Memory overload ({current_tokens} tokens), compressing context for Gemini...")

        system_msg = self.req[0]
        if len(self.req) < 10:
            return [system_msg] + self.req[-3:]

        # Find the first real user message (not a tool_result)
        first_user_idx = -1
        for i in range(1, len(self.req)):
            msg = self.req[i]
            if msg["role"] == "user":
                content = msg.get("content", "")
                is_tool_result = isinstance(content, list) and any(
                    isinstance(item, dict) and item.get("type") == "tool_result" for item in content
                )
                if not is_tool_result:
                    first_user_idx = i
                    break

        n_rounds = syscfg.ctx_keep_recent_rounds() if syscfg else 4
        recent_msgs = self._tail_msgs_for_rounds(n_rounds)
        recent_start_idx = len(self.req) - len(recent_msgs)
        start_scan = (first_user_idx + 1) if first_user_idx != -1 else 1
        end_scan = recent_start_idx

        # Cap how far back rounds-based retention can pull. In a long autonomous
        # tool-calling run the 2nd-to-last real user turn sits near the start of
        # the conversation, so _tail_msgs_for_rounds swallows nearly everything
        # and leaves the summarize range empty — compression becomes a no-op and
        # tokens keep climbing. Fall back to token-budget retention when the
        # recent section exceeds the hard cap.
        hard_cap_frac = syscfg.ctx_recent_hard_cap_frac() if syscfg else 0.30
        keep_frac = syscfg.ctx_keep_recent_fraction() if syscfg else 0.1
        msg_tokens = [(i, self._count_message_tokens(m)) for i, m in enumerate(self.req)]
        recent_tokens = sum(t for _, t in msg_tokens[recent_start_idx:])
        recent_hard_cap = int(current_tokens * hard_cap_frac)
        if recent_tokens > recent_hard_cap:
            keep_budget = int(current_tokens * keep_frac)
            new_start = len(self.req)
            acc = 0
            for idx, tok in reversed(msg_tokens):
                if acc + tok <= keep_budget:
                    acc += tok
                    new_start = idx
                else:
                    break
            logger.warning(
                f"[GoogleAPI] rounds-based recent section too large "
                f"({recent_tokens} > cap {recent_hard_cap}), falling back to "
                f"token-budget retention (recent_start {recent_start_idx} -> {new_start})"
            )
            recent_start_idx = new_start
            recent_msgs = self.req[recent_start_idx:]
            end_scan = recent_start_idx

        if start_scan >= end_scan:
            # Range still empty — force a token-budget-only split (dropping
            # round/anchor protection) so the summarizer actually runs. Never
            # return uncompressed context here; that pins tokens above the limit.
            logger.warning(
                f"[GoogleAPI] compression range empty (start={start_scan} end={end_scan}), "
                f"forcing token-budget-only retention"
            )
            keep_budget = int(current_tokens * keep_frac)
            recent_start_idx = len(self.req)
            acc = 0
            for idx, tok in reversed(msg_tokens):
                if acc + tok <= keep_budget:
                    acc += tok
                    recent_start_idx = idx
                else:
                    break
            min_scan = min((first_user_idx + 2) if first_user_idx != -1 else 2, len(self.req))
            if recent_start_idx < min_scan:
                recent_start_idx = min_scan
            recent_msgs = self.req[recent_start_idx:]
            end_scan = recent_start_idx
            start_scan = (first_user_idx + 1) if first_user_idx != -1 else 1
            logger.warning(
                f"[GoogleAPI] forced recent_start={recent_start_idx}, "
                f"summarize_range=[{start_scan}, {end_scan}) msgs={end_scan - start_scan}"
            )

        if start_scan >= end_scan:
            new_req = [system_msg]
            if first_user_idx != -1:
                new_req.append(self.req[first_user_idx])
            new_req.extend(recent_msgs)
            self.req = new_req
            return self.req

        dropped_count = end_scan - start_scan
        msgs_to_summarize = self.req[start_scan:end_scan]

        self._emit_with_sid("info", f"Calling LLM to intelligently summarize {dropped_count} historical messages...")
        summary_content = self._generate_summary(msgs_to_summarize)

        self._latest_summary = f"[Context Summary | Compressed {dropped_count} messages]\n{summary_content}"

        new_req = [system_msg]
        if first_user_idx != -1:
            new_req.append(self.req[first_user_idx])
        new_req.extend(recent_msgs)
        self.req = new_req
        logger.info(f"[GoogleAPI] Compacted context: {dropped_count} messages summarized.")

        new_token_count = self._count_tokens(self.req)
        self._emit_with_sid(
            "info",
            f"Context compression complete: {current_tokens} -> {new_token_count} tokens ({len(self.req)} messages)",
        )

        return self.req

    def _tail_msgs_for_rounds(self, n_rounds: int) -> list[dict]:
        """Return the tail message list covering the most recent n_rounds user turns."""
        msgs = self.req[1:]
        user_turn_count = 0
        for i in range(len(msgs) - 1, -1, -1):
            msg = msgs[i]
            role = msg.get("role", "")
            content = msg.get("content", "")
            is_tool_result = (
                role == "user"
                and isinstance(content, list)
                and any(isinstance(item, dict) and item.get("type") == "tool_result" for item in content)
            )
            if role == "user" and not is_tool_result:
                user_turn_count += 1
                if user_turn_count >= n_rounds:
                    return msgs[i:]
        return msgs

    def _build_conv_text(self, messages: list[dict], budget_chars: int) -> str:
        """Build conversation text with overall budget control to avoid per-message hard truncation."""
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

        total_chars = sum(len(c) for _, c in items)
        if total_chars <= budget_chars:
            return "\n".join(f"{role}: {content}" for role, content in items)

        n = len(items)
        base_alloc = max(200, budget_chars // n)
        parts = []
        for role, content in items:
            if len(content) <= base_alloc:
                parts.append(f"{role}: {content}")
            else:
                parts.append(f"{role}: {content[:base_alloc]}...[truncated]")
        return "\n".join(parts)

    def _generate_summary(self, messages: list[dict]) -> str:
        """Call Gemini itself to generate a state-snapshot-style summary."""
        if not self._genai:
            return "Summary unavailable (google-generativeai not installed)."

        budget = syscfg.ctx_conv_text_budget_chars() if syscfg else 12000
        max_tokens = syscfg.ctx_summary_max_tokens() if syscfg else 1500
        conv_text = self._build_conv_text(messages, budget)

        prompt = (
            "You are compressing conversation history for an AI Agent currently executing a task.\n"
            "The compressed result will replace this history; the Agent must be able to continue working seamlessly based on your summary.\n\n"
            "[Hard rules - the following content must be preserved verbatim; do NOT rewrite or omit]\n"
            "- All file paths and directory names\n"
            "- All IDs, ports, version numbers, config values\n"
            "- The original text of all error messages\n"
            "- Requirements, constraints, or preferences explicitly specified by the user\n\n"
            "[Output format - use lists; avoid lengthy prose; omit irrelevant details]\n\n"
            "## Current Task\n"
            "(User's original goal, one sentence)\n\n"
            "## Completed\n"
            "(Operations successfully executed and confirmed, with key output values)\n\n"
            "## Current State\n"
            "(What state the system/files/code is in right now -- this is the most important section)\n\n"
            "## Key Parameters\n"
            "(Exact values needed going forward: paths, configs, API addresses, etc.)\n\n"
            "## Unresolved Issues\n"
            "(Confirmed blockers or errors; omit this section if none)\n\n"
            "---\n"
            f"Conversation to compress:\n{conv_text}"
        )

        try:
            generation_config = self._genai.GenerationConfig(
                temperature=0.3,
                max_output_tokens=max_tokens,
            )
            gemini_model = self._genai.GenerativeModel(
                model_name=self.model,
                generation_config=generation_config,
            )
            response = gemini_model.generate_content(prompt)
            return response.text
        except Exception as e:
            logger.error(f"[GoogleAPI] Summary generation failed: {e}")
            return "Summary generation failed. Please rely on the First User Query."

    # -- Gemini format conversion --

    def _to_gemini_parts(self, content) -> list:
        """Convert internal OpenAI-compatible content format to Gemini parts list."""
        if content is None:
            return [{"text": ""}]
        if isinstance(content, str):
            return [{"text": content}]

        parts = []
        for item in content:
            if item.get("type") == "text":
                parts.append({"text": item["text"]})
            elif item.get("type") in ("image", "audio", "video"):
                source = item.get("source", {})
                if source.get("type") == "base64":
                    parts.append(
                        {
                            "inline_data": {
                                "mime_type": source.get("media_type", "application/octet-stream"),
                                "data": source.get("data", ""),
                            }
                        }
                    )
            elif item.get("type") == "functionResponse":
                # Tool result from add_tool_result — convert to Gemini functionResponse
                parts.append(
                    {
                        "functionResponse": item.get("response", {"name": "unknown", "content": {}}),
                    }
                )
        return parts

    def _split_history_and_last(self, messages: list[dict]):
        """
        Split the message list (excluding system) into:
          - history: Gemini-format history for start_chat() (all but the last message)
          - last_parts: Gemini parts for the last user message
        Gemini history requires alternating user/model roles; converted in order here.
        """
        history = []
        for msg in messages[:-1]:
            gemini_role = "model" if msg["role"] == "assistant" else "user"
            parts = self._to_gemini_parts(msg["content"])
            history.append({"role": gemini_role, "parts": parts})

        last_parts = self._to_gemini_parts(messages[-1]["content"])
        return history, last_parts

    # -- Core chat method --

    def _convert_tools_to_google_format(self, openai_tools: list[dict]):
        """
        Convert OpenAI Tools JSON Schema to Google Gemini format

        Args:
            openai_tools: List of OpenAI format tools

        Returns:
            List of genai.Tool objects (or empty list if genai not available)

        Transformation:
            - Remove "type": "function" wrapper
            - Use genai.FunctionDeclaration to wrap each function
            - Use genai.Tool to wrap all declarations
            - Keep "parameters" key unchanged (same as OpenAI)

        Example:
            Input:  [{"type": "function", "function": {"name": "foo", "parameters": {...}}}]
            Output: [genai.Tool(function_declarations=[...])]
        """
        if not self._genai:
            return []

        function_declarations = []
        for tool in openai_tools:
            if tool.get("type") == "function":
                func = tool["function"]
                function_declarations.append(
                    self._genai.protos.FunctionDeclaration(
                        name=func["name"],
                        description=func.get("description", ""),
                        parameters=self._jsonschema_to_google_schema(func.get("parameters", {})),
                    )
                )

        if not function_declarations:
            return []

        return [self._genai.protos.Tool(function_declarations=function_declarations)]

    _GOOGLE_TYPE_MAP = {
        "string": "STRING",
        "number": "NUMBER",
        "integer": "INTEGER",
        "boolean": "BOOLEAN",
        "array": "ARRAY",
        "object": "OBJECT",
    }

    def _jsonschema_to_google_schema(self, schema: dict):
        """
        Recursively convert an OpenAI JSON Schema dict to a Google genai Schema proto.
        OpenAI schema uses string "type" (e.g. "object");
        Google proto requires a Type enum value (e.g. Type.OBJECT).
        """
        if not schema:
            return self._genai.protos.Schema(type_=self._genai.protos.Type.OBJECT)

        type_str = schema.get("type", "object")
        type_name = self._GOOGLE_TYPE_MAP.get(type_str, "OBJECT")
        google_type = getattr(self._genai.protos.Type, type_name)

        kwargs: dict = {"type_": google_type}

        if "description" in schema:
            kwargs["description"] = schema["description"]

        if "properties" in schema:
            kwargs["properties"] = {k: self._jsonschema_to_google_schema(v) for k, v in schema["properties"].items()}

        if "required" in schema:
            kwargs["required"] = list(schema["required"])

        if "items" in schema:
            kwargs["items"] = self._jsonschema_to_google_schema(schema["items"])

        if "enum" in schema:
            kwargs["enum"] = [str(e) for e in schema["enum"]]

        return self._genai.protos.Schema(**kwargs)

    def _parse_google_function_call(self, parts) -> tuple[str, dict] | None:
        """
        Extract functionCall from Gemini response.candidates[0].content.parts

        Args:
            parts: Gemini response parts list (can contain text and functionCall)

        Returns:
            (function_name, function_args) or None

        Example:
            Input:  [{"text": "..."}, {"functionCall": {"name": "foo", "args": {...}}}]
            Output: ("foo", {...})
        """
        if not parts:
            return None

        for part in parts:
            # Check if part has function_call attribute
            if hasattr(part, "function_call") and part.function_call:
                func_call = part.function_call
                func_name = getattr(func_call, "name", None)
                func_args_obj = getattr(func_call, "args", None)

                # Convert args to dict
                func_args = {}
                if func_args_obj:
                    # args is a protobuf Struct, convert to dict
                    try:
                        func_args = dict(func_args_obj)
                    except (TypeError, ValueError, RuntimeError):
                        # Fallback: try to extract fields manually
                        func_args = {k: v for k, v in func_args_obj.items()} if hasattr(func_args_obj, "items") else {}

                if func_name:
                    return (func_name, func_args)

        return None

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
        """Async wrapper around the synchronous Google Gemini API call.

        The GenAI SDK call is synchronous and blocking. Running it via
        ``asyncio.to_thread`` keeps the event loop responsive while the runner
        uniformly ``await``s ``self.chat_api.chat(...)`` across all providers.
        """
        return await asyncio.to_thread(
            self._chat_sync,
            user_message,
            image_path,
            image_b64_list,
            audio_path,
            video_path,
            tools,
            tool_choice,
            tool_call_strategy,
            skip_add_user,
        )

    def _chat_sync(
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
        Call Google Gemini API with Native Function Calling support

        Args:
            user_message: User input text
            image_path: List of image file paths
            image_b64_list: List of base64-encoded images
            audio_path: List of audio file paths
            video_path: List of video file paths
            tools: OpenAI Tools JSON Schema (will be converted to Gemini format)
            tool_choice: "auto" | "required" | "none"
            tool_call_strategy: ToolCallStrategy instance (not used in Gemini, for compatibility)
            skip_add_user: If True, do NOT call add_user_message().

        Returns:
            dict: {"text": response_text, "tool_data": (tool_name, tool_args) or None}
        """

        if not self._genai:
            return {
                "text": "[Error] google-generativeai is not installed. Run: pip install google-generativeai",
                "tool_data": None,
            }

        if not skip_add_user:
            self.add_user_message(
                user_message, image_path, image_b64_list=image_b64_list, audio_path=audio_path, video_path=video_path
            )
        all_msgs = self._prepare_messages()

        # Separate system message from conversation messages
        system_content = ""
        messages = []
        for m in all_msgs:
            if m["role"] == "system":
                system_content = m["content"]
            else:
                messages.append(m)

        if not messages:
            return {"text": "[Error] No messages to send", "tool_data": None}

        # Convert tools to Gemini format
        google_tools = None
        tool_config = None
        if tools:
            google_tools = self._convert_tools_to_google_format(tools)
            logger.info(f"[GoogleAPI] Converted {len(tools)} OpenAI tools to Gemini format (Native FC enabled)")

            # Convert tool_choice to Gemini format
            mode_map = {
                "auto": self._genai.protos.FunctionCallingConfig.Mode.AUTO,
                "required": self._genai.protos.FunctionCallingConfig.Mode.ANY,
                "none": self._genai.protos.FunctionCallingConfig.Mode.NONE,
            }
            mode = mode_map.get(tool_choice, self._genai.protos.FunctionCallingConfig.Mode.AUTO)
            tool_config = self._genai.protos.ToolConfig(
                function_calling_config=self._genai.protos.FunctionCallingConfig(mode=mode)
            )

        def _is_timeout_error(exc: Exception) -> bool:
            cls = type(exc).__name__.lower()
            msg = str(exc).lower()
            return ("timeout" in cls) or ("timed out" in msg) or ("readtimeout" in cls)

        def _is_image_not_supported_error(exc: Exception) -> bool:
            """Detect errors indicating the model/provider does not support image input."""
            msg = str(exc).lower()
            return (
                ("no endpoints found that support image" in msg)
                or ("image input" in msg and "not support" in msg)
                or ("does not support image" in msg)
                or ("vision" in msg and "not support" in msg)
                or ("multimodal" in msg and "not support" in msg)
                or ("image" in msg and "not supported" in msg)
            )

        def _strip_images_from_google_messages(msgs: list) -> list:
            """Remove image content from Google/Gemini messages, keeping only text."""
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

        gen_config_kwargs = {"temperature": self.temperature}
        if self.top_k > 0:
            gen_config_kwargs["top_k"] = self.top_k
        generation_config = self._genai.GenerationConfig(**gen_config_kwargs)

        # Get session context for tracking
        sid = self._sid_provider() if self._sid_provider else None
        uid = self._user_id_provider() if self._user_id_provider else None

        # Inject session context into system instruction for tracking
        if sid or uid:
            session_context = "\n\n[Session Context]\n"
            if sid:
                session_context += f"- Session ID: {sid}\n"
            if uid:
                session_context += f"- User ID: {uid}\n"
            system_content = (system_content or "") + session_context
            logger.debug(f"[GoogleAPI] Added session context: session_id={sid}, user_id={uid}")

        # Build model parameters
        model_params = {
            "model_name": self.model,
            "system_instruction": system_content or None,
            "generation_config": generation_config,
        }

        # Add tools if provided
        if google_tools:
            model_params["tools"] = google_tools

        max_stream_retries = 2
        _images_stripped = False  # Track if images were stripped due to unsupported error

        for attempt in range(max_stream_retries + 1):
            got_any_chunk = False
            try:
                gemini_model = self._genai.GenerativeModel(**model_params)

                history, last_parts = self._split_history_and_last(messages)
                chat_session = gemini_model.start_chat(history=history)

                if self.stream_parser:
                    self.stream_parser.clean()

                full_text = ""
                image_output_items: list = []  # collect model image outputs

                # Build send_message parameters
                send_params = {"stream": True}
                if tool_config:
                    send_params["tool_config"] = tool_config

                response = chat_session.send_message(last_parts, **send_params)

                for chunk in response:
                    got_any_chunk = True
                    # Check interruption
                    if input_hub and input_hub.is_stop_requested():
                        logger.info("[GoogleAPI] Interrupted by user")
                        break

                    # Extract text: chain-of-thought models may have parts with thought=True; filter them
                    chunk_text = ""
                    try:
                        # Try extracting per-part and filtering chain-of-thought sections
                        for part in chunk.candidates[0].content.parts:
                            if not getattr(part, "thought", False):
                                chunk_text += getattr(part, "text", "") or ""
                            # Detect image output (inline_data)
                            inline = getattr(part, "inline_data", None)
                            if inline and self.is_image_output:
                                mime = getattr(inline, "mime_type", "")
                                if mime.startswith("image/"):
                                    image_output_items.append({"mime": mime, "data": getattr(inline, "data", "")})
                    except (IndexError, AttributeError, ValueError):
                        # fallback: use chunk.text directly
                        try:
                            chunk_text = chunk.text or ""
                        except (ValueError, AttributeError):
                            chunk_text = ""

                    if chunk_text:
                        full_text += chunk_text
                        self.printer.dynamic_single_callback(chunk_text)
                        if self.stream_parser:
                            self.stream_parser.feed(chunk_text)

                # Token stats
                try:
                    usage = response.usage_metadata
                    prompt_count = getattr(usage, "prompt_token_count", 0) or 0
                    # prompt_token_count is cumulative history (not incremental); compute the delta for this request
                    delta_input = max(0, prompt_count - self._last_prompt_token_count)
                    self._last_prompt_token_count = prompt_count
                    self.total_input_tokens += delta_input
                    self.total_output_tokens += getattr(usage, "candidates_token_count", 0) or 0
                except Exception:
                    self.total_input_tokens += self._count_tokens(all_msgs)
                    if self.encoding and full_text:
                        self.total_output_tokens += len(self.encoding.encode(full_text))

                # Parse functionCall from response
                tool_data = None
                try:
                    final_parts = response.candidates[0].content.parts
                    tool_data = self._parse_google_function_call(final_parts)
                    if tool_data:
                        logger.info(f"[GoogleAPI] Detected function call: {tool_data[0]}")
                except (IndexError, AttributeError) as e:
                    logger.debug(f"[GoogleAPI] No function call detected: {e}")

                self.total_requests += 1

                if self.stream_parser:
                    self.stream_parser.finish()

                self.add_assistant_message(full_text)
                self.save_history()

                # Return dict format with tool_data (Native FC now supported)
                output_media = []
                if image_output_items and self.output_media_dir:
                    import base64 as _b64
                    import uuid as _uuid

                    os.makedirs(self.output_media_dir, exist_ok=True)
                    for item in image_output_items:
                        try:
                            ext = item["mime"].split("/")[-1].replace("jpeg", "jpg")
                            fname = f"agent_img_{_uuid.uuid4().hex[:12]}.{ext}"
                            fpath = os.path.join(self.output_media_dir, fname)
                            with open(fpath, "wb") as f:
                                f.write(_b64.b64decode(item["data"]))
                            output_media.append({"type": "image", "url": f"/uploads/{fname}", "mime": item["mime"]})
                            logger.info(f"[GoogleAPI] Saved image output: {fname}")
                        except Exception as e:
                            logger.error(f"[GoogleAPI] Failed to save image output: {e}")

                return {
                    "text": full_text,
                    "tool_data": tool_data,
                    "output_media": output_media,
                    "finish_reason": "tool_calls" if tool_data else "stop",
                    "stream_error": False,
                    "timed_out": False,
                }

            except Exception as e:
                is_timeout = _is_timeout_error(e)
                is_image_error = _is_image_not_supported_error(e)
                can_retry_image = is_image_error and (not _images_stripped)

                if can_retry_image:
                    # Model/provider doesn't support image input -- strip images and retry
                    logger.warning(f"[GoogleAPI] Image not supported by model, stripping images and retrying: {e}")
                    # Rebuild history and last_parts without images
                    cleaned_messages = _strip_images_from_google_messages(messages)
                    history, last_parts = self._split_history_and_last(cleaned_messages)
                    chat_session = gemini_model.start_chat(history=history)
                    _images_stripped = True
                    self.is_img_model = False
                    continue

                can_retry = is_timeout and (attempt < max_stream_retries) and (not got_any_chunk)
                if can_retry:
                    wait_s = 0.8 * (attempt + 1)
                    logger.warning(
                        f"[GoogleAPI] Stream timeout before first chunk, retrying ({attempt + 1}/{max_stream_retries}) after {wait_s:.1f}s: {e}"
                    )
                    import time as _time

                    _time.sleep(wait_s)
                    continue
                logger.error(f"[GoogleAPI] API Error: {e}", exc_info=True)
                return {
                    "text": f"[Error] {e}",
                    "tool_data": None,
                    "output_media": output_media,
                    "finish_reason": None,
                    "stream_error": True,
                    "timed_out": is_timeout,
                }

    # -- History management --

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
                logger.error(f"[GoogleAPI] Failed to load history: {e}")

    def save_history(self):
        if self.history_file:
            try:
                with open(self.history_file, "w", encoding="utf-8") as f:
                    json.dump(self.req, f, ensure_ascii=False, indent=2)
            except Exception as e:
                logger.error(f"[GoogleAPI] Failed to save history: {e}")

    def delete_all_uploaded_files(self):
        """Aligns with the ChatAPI / ClaudeAPI interface (Google Files API not yet supported)."""
        pass

    # -- Statistics --

    def get_cumulative_stats(self) -> dict:
        """Return cumulative consumption stats (aligned with ChatAPI / ClaudeAPI)."""
        return {
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_tokens": self.total_input_tokens + self.total_output_tokens,
            "total_requests": self.total_requests,
            "total_cache_read_tokens": self.total_cache_read_tokens,
            "total_cache_creation_tokens": self.total_cache_creation_tokens,
        }

    def list_sessions(self) -> list[str]:
        """Return the list of historical sessions (aligned with ChatAPI)."""
        self._ensure_history_dir()
        if not os.path.exists(self.history_dir):
            return []
        return [f[:-5] for f in os.listdir(self.history_dir) if f.endswith(".json")]
