import asyncio
import base64
import json
import logging
import os
import tempfile
import time

from .events import bus
from .input_hub import input_hub
from .model_config import ModelConfig
from .system_config import syscfg
from .utils import CharPrinter
from .xml_parser import StreamingTagParser

logger = logging.getLogger(__name__)

# ── Lazy imports ─────────────────────────────────────────────────
_tiktoken_mod = None


def _get_tiktoken():
    """Lazy import of tiktoken."""
    global _tiktoken_mod
    if _tiktoken_mod is None:
        import tiktoken as _T

        _tiktoken_mod = _T
    return _tiktoken_mod


class _AnthropicClient:
    """Fallback stub — real AnthropicClient loaded lazily via _get_anthropic()."""

    def __init__(self, **kwargs):
        pass


def _get_anthropic():
    """Lazy import of Anthropic SDK (avoids ~2.3s import penalty at startup)."""
    try:
        from anthropic import Anthropic

        return Anthropic
    except ImportError:
        return _AnthropicClient


class ClaudeAPI:
    """
    ClaudeAPI v3.1: Enhanced Anthropic interface, feature-aligned with ChatAPI.
    Supports: context compression, token statistics, history persistence, stream interruption, event dispatch.
    New: provider-level Files API upload (images/documents referenced by file_id), video frame extraction for image delivery.
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
        max_video_frames: int | None = None,
        top_k: int | None = None,
        is_think: bool | None = None,
        thinking_budget_tokens: int | None = None,
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
                timeout=timeout if timeout is not None else 180.0,
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
                max_video_frames=max_video_frames if max_video_frames is not None else 8,
                top_k=top_k if top_k is not None else 0,
                is_think=is_think if is_think is not None else False,
                thinking_budget_tokens=thinking_budget_tokens if thinking_budget_tokens is not None else 10000,
            )
        self.config = config

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
        # Claude supports at most 20 images per request; video frame extraction is bounded by this limit
        self.max_video_frames = max(1, min(config.max_video_frames, 20))
        self.top_k = config.top_k
        self.is_think = config.is_think
        self.thinking_budget_tokens = config.thinking_budget_tokens
        self._prev_reasoning_content = ""  # Must be passed back when tools are involved
        # file_id cache: path -> file_id, avoids re-uploading the same file within a session
        self._file_id_cache: dict[str, str] = {}

        self._prompt_template = config.prompt
        # Align with ChatAPI: req[0] is the system message
        self.req = [{"role": "system", "content": config.prompt}]
        self.stream_parser = stream_parser
        self.printer = CharPrinter(max_width=80)
        self.load_his = config.load_his

        self.history_dir = None  # Lazy: resolved on first use
        self.history_file = None
        self._initialize_history(config.load_his)

        self.token_max = config.token_max
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
        # ── Per-message token cache (P3 perf optimization) ──
        self._msg_token_cache: dict[int, int] = {}
        self._msg_token_cache_max_size = 5000

        try:
            self.encoding = _get_tiktoken().get_encoding("cl100k_base")
        except Exception:
            self.encoding = None

        client_params = {"api_key": self.api_key, "timeout": self.timeout}
        if self.base_url:
            client_params["base_url"] = self.base_url
        self.client = _get_anthropic()(**client_params)
        logger.info(f"ClaudeAPI Initialized. Model: {self.model}")

    async def reload_model(self, model_cfg: dict):
        """Hot-reload model parameters without losing conversation history.

        Declared ``async`` so it matches the ``ChatAPI.reload_model`` contract
        and can be ``await``-ed by the runner (which calls all providers
        uniformly via ``await self.chat_api.reload_model(...)``).
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
        self.max_video_frames = max(1, min(model_cfg.get("max_video_frames", self.max_video_frames), 20))
        self.top_k = model_cfg.get("top_k", self.top_k)
        self.is_think = model_cfg.get("is_think", self.is_think)
        self.thinking_budget_tokens = model_cfg.get("thinking_budget_tokens", self.thinking_budget_tokens)
        # Recreate Anthropic client
        client_params = {"api_key": self.api_key, "timeout": self.timeout}
        if self.base_url:
            client_params["base_url"] = self.base_url
        self.client = _get_anthropic()(**client_params)
        self._file_id_cache.clear()
        logger.info(f"[ClaudeAPI] Model hot-reloaded: {old_model} -> {self.model}")

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

    def _emit_with_sid(self, etype, data):
        sid = self._sid_provider() if self._sid_provider else None
        if sid:
            bus.emit(etype, {"sid": sid, "data": data})
        else:
            bus.emit(etype, data)

    # -- Provider-level Files API (Anthropic beta) --

    def _upload_file_claude(self, path: str, mime_type: str | None = None) -> str | None:
        """
        Upload a local file to the Anthropic Files API (beta) and return the file_id.
        The file is cached so the same path is not re-uploaded within the same session.
        """
        if path in self._file_id_cache:
            return self._file_id_cache[path]
        if mime_type is None:
            mime_type = self._guess_mime(path)
        try:
            filename = os.path.basename(path)
            with open(path, "rb") as f:
                response = self.client.beta.files.upload(file=(filename, f, mime_type))
            file_id = response.id
            self._file_id_cache[path] = file_id
            logger.info(f"[ClaudeAPI] Uploaded to Files API: {path} -> {file_id}")
            return file_id
        except Exception as e:
            logger.error(f"[ClaudeAPI] Files API upload failed ({path}): {e}")
            return None

    def _delete_file_claude(self, file_id: str):
        """Delete a file previously uploaded to the Anthropic Files API."""
        try:
            self.client.beta.files.delete(file_id)
            self._file_id_cache = {k: v for k, v in self._file_id_cache.items() if v != file_id}
            logger.info(f"[ClaudeAPI] Deleted from Files API: {file_id}")
        except Exception as e:
            logger.warning(f"[ClaudeAPI] Files API delete failed ({file_id}): {e}")

    def delete_all_uploaded_files(self):
        """Clean up all Files API files uploaded during this session."""
        for _path, fid in list(self._file_id_cache.items()):
            self._delete_file_claude(fid)
        self._file_id_cache.clear()

    # -- MIME / media helpers --

    def _guess_mime(self, path: str) -> str:
        """Infer MIME type from file extension."""
        ext = os.path.splitext(path)[1].lower()
        mapping = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".gif": "image/gif",
            ".webp": "image/webp",
            ".pdf": "application/pdf",
            ".txt": "text/plain",
            ".md": "text/plain",
            ".csv": "text/csv",
            ".html": "text/html",
            ".htm": "text/html",
            ".mp4": "video/mp4",
            ".mov": "video/quicktime",
            ".webm": "video/webm",
            ".mkv": "video/x-matroska",
            ".avi": "video/x-msvideo",
        }
        return mapping.get(ext, "application/octet-stream")

    def _is_image_path(self, path: str) -> bool:
        return os.path.splitext(path)[1].lower() in (".jpg", ".jpeg", ".png", ".gif", ".webp")

    def _is_document_path(self, path: str) -> bool:
        return os.path.splitext(path)[1].lower() in (".pdf", ".txt", ".md", ".csv", ".html", ".htm")

    def _extract_video_frames(self, video_path: str) -> list[str]:
        """
        Use OpenCV to evenly extract frames from a video and return a list of temporary JPEG file paths.
        The number of frames is controlled by self.max_video_frames (max 20, Claude's per-request image limit).
        Returns an empty list if cv2 is unavailable.
        """
        try:
            import cv2
        except ImportError:
            logger.warning("[ClaudeAPI] cv2 not available, cannot extract video frames")
            return []
        frame_paths = []
        try:
            cap = cv2.VideoCapture(video_path)
            total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            if total <= 0:
                cap.release()
                return []
            n = self.max_video_frames
            indices = [int(i * total / n) for i in range(n)]
            tmp_dir = tempfile.mkdtemp(prefix="claude_frames_")
            for idx in indices:
                cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
                ret, frame = cap.read()
                if not ret:
                    continue
                frame_file = os.path.join(tmp_dir, f"frame_{idx:06d}.jpg")
                cv2.imwrite(frame_file, frame)
                frame_paths.append(frame_file)
            cap.release()
            logger.info(f"[ClaudeAPI] Extracted {len(frame_paths)} frames from {video_path}")
        except Exception as e:
            logger.error(f"[ClaudeAPI] Video frame extraction failed ({video_path}): {e}")
        return frame_paths

    def _encode_image_block(self, img_path: str) -> dict | None:
        """
        Encode an image as an Anthropic image content block.
        When use_file_api=True and file exceeds threshold, file_id is preferred; otherwise base64 inline.
        """
        file_size = os.path.getsize(img_path) if os.path.exists(img_path) else 0
        mime = self._guess_mime(img_path)
        if self.use_file_api and file_size > self.file_api_size_threshold:
            file_id = self._upload_file_claude(img_path, mime)
            if file_id:
                return {"type": "image", "source": {"type": "file", "file_id": file_id}}
            # Upload failed -> fallback to base64
        try:
            with open(img_path, "rb") as f:
                data = base64.b64encode(f.read()).decode("utf-8")
            return {"type": "image", "source": {"type": "base64", "media_type": mime, "data": data}}
        except Exception as e:
            logger.error(f"[ClaudeAPI] Image encode error ({img_path}): {e}")
            return None

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
                f"[ClaudeAPI] Skipping empty user message (tool result already in history), req_len={len(self.req)}"
            )
            return

        logger.debug(
            f"[ClaudeAPI] add_user_message: text_len={len(message)}, has_multimodal={has_multimodal}, req_len_before={len(self.req)}"
        )
        content = []

        # -- Local images --
        if image_path and self.is_img_model:
            for img in image_path:
                block = self._encode_image_block(img)
                if block:
                    content.append(block)

        # -- Base64 images (MCP screenshots, etc.) --
        if image_b64_list and self.is_img_model:
            for img_data in image_b64_list:
                mime = img_data.get("mimeType", "image/png")
                b64 = img_data.get("data", "")
                if b64:
                    content.append({"type": "image", "source": {"type": "base64", "media_type": mime, "data": b64}})

        # -- Audio (Claude does not natively support audio; inject a path hint) --
        if audio_path and self.is_audio_model:
            for audio in audio_path:
                if not audio:
                    continue
                content.append(
                    {
                        "type": "text",
                        "text": f"[Attachment: audio file {audio}. Claude does not natively support audio; please handle based on context.]",
                    }
                )

        # -- Video (extract frames and send as image frames; capped at max_video_frames) --
        if video_path and self.is_video_model:
            for video in video_path:
                if not video:
                    continue
                frame_paths = self._extract_video_frames(video)
                if frame_paths:
                    content.append(
                        {
                            "type": "text",
                            "text": f"[Video file: {os.path.basename(video)}, {len(frame_paths)} frames extracted evenly, shown below]",
                        }
                    )
                    for fp in frame_paths:
                        block = self._encode_image_block(fp)
                        if block:
                            content.append(block)
                else:
                    content.append(
                        {
                            "type": "text",
                            "text": f"[Attachment: video file {video}, frame extraction failed; please handle based on context.]",
                        }
                    )

        content.append({"type": "text", "text": message})
        # If text-only, use simplified storage format
        final_content = content if len(content) > 1 else message
        self.req.append({"role": "user", "content": final_content})

    def add_tool_result(self, tool_name: str, tool_args: dict, result: str, tool_call_id: str = ""):
        """Inject tool execution result into message history in Claude-compatible format.

        Claude's Messages API uses a different format from OpenAI:
        - assistant message with `tool_calls` array (for OpenAI-style tracking)
        - user message with content containing `type: "tool_result"` (Claude native format)

        Both are stored so that _prepare_messages() can convert appropriately before
        sending to the API.

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
            # Preserve reasoning_content from previous assistant message if present
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

        # Tool result in Claude format: user message with tool_result content block.
        # Claude expects content to be a list of content blocks (not a plain string).
        self.req.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_call_id,
                        "content": [{"type": "text", "text": str(result) if result else "(empty result)"}],
                    }
                ],
            }
        )
        self.save_history()

        logger.info(
            f"[ClaudeAPI] add_tool_result: tool={tool_name}, "
            f"call_id={tool_call_id}, mode={_mode}, "
            f"result_len={len(str(result))}, total_req_messages={len(self.req)}"
        )

    def add_pipeline_events(self, events_text: str):
        """Append accumulated pipeline events as a tool-result notification.

        Mirrors ``ChatAPI.add_pipeline_events`` so the runner can call it
        uniformly across providers. External messages (web user, group chat,
        DM, timer, task_watch) are injected as a ``system__event_pipeline``
        tool result rather than a fresh user turn, preserving the never-stop
        architecture.
        """
        if not events_text or not events_text.strip():
            return
        self.add_tool_result(
            tool_name="system__event_pipeline",
            tool_args={},
            result=events_text,
        )
        logger.debug(
            f"[ClaudeAPI] add_pipeline_events: events_len={len(events_text)}, total_req_messages={len(self.req)}"
        )

    def add_assistant_message(self, content: str, reasoning_content: str | None = None):
        """Add assistant message, optionally with reasoning_content for extended thinking pass-back."""
        msg = {"role": "assistant", "content": content}
        if reasoning_content:
            msg["reasoning_content"] = reasoning_content
        if content or reasoning_content:
            self.req.append(msg)

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
            logger.info("[ClaudeAPI] Popped last assistant message to break loop")
            return True
        return False

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
        except (TypeError, AttributeError) as e:
            logger.warning(f"Token count error: {e}")
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
                elif item.get("type") in ("audio", "video"):
                    num_tokens += 400
                elif item.get("type") == "tool_result":
                    tool_content = item.get("content", "")
                    if isinstance(tool_content, list):
                        for tc in tool_content:
                            if isinstance(tc, dict) and tc.get("type") == "text":
                                num_tokens += len(self.encoding.encode(tc["text"]))
                    elif isinstance(tool_content, str):
                        num_tokens += len(self.encoding.encode(tool_content))
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

    def _prepare_messages(self) -> list[dict]:
        """Context compression strategy (trigger threshold and keep-rounds are read from system_config)."""
        current_tokens = self._count_tokens(self.req)

        threshold = syscfg.ctx_trigger_threshold()
        if current_tokens <= self.token_max * threshold:
            return self.req

        logger.warning(
            f"Claude Context limit reached ({current_tokens}/{self.token_max}, threshold={threshold}). Compacting."
        )
        self._emit_with_sid("status", "Context limit reached, compacting...")
        self._emit_with_sid("info", f"Memory overload ({current_tokens} tokens), compacting context for Claude...")

        system_msg = self.req[0]

        if len(self.req) < 10:
            return [system_msg] + self.req[-3:]

        # Preserve the first User message (original intent)
        first_user_idx = -1
        for i in range(1, len(self.req)):
            if self.req[i]["role"] == "user":
                # In Claude, tool_result also has role=user; exclude those
                content = self.req[i].get("content", "")
                is_tool_result = isinstance(content, list) and any(
                    isinstance(item, dict) and item.get("type") == "tool_result" for item in content
                )
                if not is_tool_result:
                    first_user_idx = i
                    break

        # Keep recent tail by number of rounds
        recent_msgs = self._tail_msgs_for_rounds(syscfg.ctx_keep_recent_rounds())
        recent_start_idx = len(self.req) - len(recent_msgs)
        start_scan = (first_user_idx + 1) if first_user_idx != -1 else 1
        end_scan = recent_start_idx

        # Cap how far back rounds-based retention can pull. In a long autonomous
        # tool-calling run the 2nd-to-last real user turn sits near the start of
        # the conversation, so _tail_msgs_for_rounds swallows nearly everything
        # and leaves the summarize range empty — compression becomes a no-op and
        # tokens keep climbing. Fall back to token-budget retention when the
        # recent section exceeds the hard cap.
        msg_tokens = [(i, self._count_message_tokens(m)) for i, m in enumerate(self.req)]
        recent_tokens = sum(t for _, t in msg_tokens[recent_start_idx:])
        recent_hard_cap = int(current_tokens * syscfg.ctx_recent_hard_cap_frac())
        if recent_tokens > recent_hard_cap:
            keep_budget = int(current_tokens * syscfg.ctx_keep_recent_fraction())
            new_start = len(self.req)
            acc = 0
            for idx, tok in reversed(msg_tokens):
                if acc + tok <= keep_budget:
                    acc += tok
                    new_start = idx
                else:
                    break
            logger.warning(
                f"[ClaudeAPI] rounds-based recent section too large "
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
                f"[ClaudeAPI] compression range empty (start={start_scan} end={end_scan}), "
                f"forcing token-budget-only retention"
            )
            keep_budget = int(current_tokens * syscfg.ctx_keep_recent_fraction())
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
                f"[ClaudeAPI] forced recent_start={recent_start_idx}, "
                f"summarize_range=[{start_scan}, {end_scan}) msgs={end_scan - start_scan}"
            )

        if start_scan < end_scan:
            dropped_count = end_scan - start_scan
            msgs_to_summarize = self.req[start_scan:end_scan]

            self._emit_with_sid("info", f"Calling model to summarize {dropped_count} history messages intelligently...")
            summary_content = self._generate_summary(msgs_to_summarize)

            self._latest_summary = f"[Context Summary | Compressed {dropped_count} messages]\n{summary_content}"

            new_req = [system_msg]
            if first_user_idx != -1:
                new_req.append(self.req[first_user_idx])
            new_req.extend(recent_msgs)
            self.req = new_req
            logger.info(f"Compacted Claude context: {dropped_count} messages summarized.")

            new_token_count = self._count_tokens(self.req)
            self._emit_with_sid(
                "info",
                f"Context compaction done: {current_tokens} -> {new_token_count} tokens ({len(self.req)} messages)",
            )

        return self.req

    def _tail_msgs_for_rounds(self, n_rounds: int) -> list[dict]:
        """Return the tail messages covering the most recent n_rounds user turns."""
        msgs = self.req[1:]  # skip system msg
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
        """Build conversation text with overall budget control, avoiding per-message hard truncation."""
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
        """Call Claude itself to generate a state-snapshot-style summary."""
        budget = syscfg.ctx_conv_text_budget_chars()
        max_tokens = syscfg.ctx_summary_max_tokens()
        conv_text = self._build_conv_text(messages, budget)

        prompt = (
            "You are compressing conversation history for an AI Agent that is actively executing a task.\n"
            "The compressed result will replace this history; the Agent must be able to continue seamlessly based on your summary.\n\n"
            "[Hard rules - the following content must be preserved verbatim; never rewrite or omit]\n"
            "- All file paths and directory names\n"
            "- All IDs, ports, version numbers, and configuration values\n"
            "- Original text of all error messages\n"
            "- Requirements, constraints, or preferences explicitly specified by the user\n\n"
            "[Output format - use lists, no long prose, omit irrelevant details]\n\n"
            "## Current Task\n"
            "(The user's original goal, in one sentence)\n\n"
            "## Completed\n"
            "(Actions successfully executed and confirmed, with key output values)\n\n"
            "## Current State\n"
            "(What state the system/files/code is in right now - this is the most important section)\n\n"
            "## Key Parameters\n"
            "(Exact values that will definitely be needed later: paths, configs, API addresses, etc.)\n\n"
            "## Unresolved Issues\n"
            "(Confirmed blockers or errors; omit this section if none)\n\n"
            "---\n"
            f"Conversation history to compress:\n{conv_text}"
        )

        try:
            response = self.client.messages.create(
                model=self.model, max_tokens=max_tokens, messages=[{"role": "user", "content": prompt}], temperature=0.3
            )
            return response.content[0].text
        except Exception as e:
            logger.error(f"Claude summary failed: {e}")
            return "Summary generation failed. Please rely on the First User Query."

    def _convert_tools_to_claude_format(self, openai_tools: list[dict]) -> list[dict]:
        """
        Convert OpenAI Tools JSON Schema to Claude format

        Args:
            openai_tools: List of OpenAI format tools

        Returns:
            List of Claude format tools

        Transformation:
            - Remove "type": "function" wrapper
            - Rename "parameters" -> "input_schema"
            - Keep "name" and "description" unchanged

        Example:
            Input:  [{"type": "function", "function": {"name": "foo", "parameters": {...}}}]
            Output: [{"name": "foo", "input_schema": {...}}]
        """
        claude_tools = []
        for tool in openai_tools:
            if tool.get("type") == "function":
                func = tool["function"]
                claude_tools.append(
                    {
                        "name": func["name"],
                        "description": func.get("description", ""),
                        "input_schema": func["parameters"],
                    }
                )
        return claude_tools

    def _parse_claude_tool_use(self, response_content) -> tuple[str, dict] | None:
        """
        Extract tool_use block from Claude response.content

        Args:
            response_content: Claude response.content list (can contain text and tool_use blocks)

        Returns:
            (tool_name, tool_input) or None

        Example:
            Input:  [{"type": "text", "text": "..."}, {"type": "tool_use", "name": "foo", "input": {...}}]
            Output: ("foo", {...})
        """
        if not response_content:
            return None

        # response_content can be a list or a single block
        content_list = response_content if isinstance(response_content, list) else [response_content]

        for block in content_list:
            # Handle both dict and object formats
            block_dict = block if isinstance(block, dict) else getattr(block, "__dict__", {})
            block_type = block_dict.get("type") or getattr(block, "type", None)

            if block_type == "tool_use":
                tool_name = block_dict.get("name") or getattr(block, "name", None)
                tool_input = block_dict.get("input") or getattr(block, "input", {})
                if tool_name:
                    return (tool_name, tool_input)

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
        """Async wrapper around the synchronous Claude API call.

        The Anthropic SDK call (``client.messages.stream``) is synchronous and
        blocking. Running it via ``asyncio.to_thread`` keeps the asyncio event
        loop responsive (gateway WebSocket, bridge polling, etc.) while the
        runner uniformly ``await``s ``self.chat_api.chat(...)`` across all
        providers (OpenAI/Claude/Google).
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
        Call Claude API with Native Function Calling support

        Args:
            user_message: User input text
            image_path: List of image file paths
            image_b64_list: List of base64-encoded images
            audio_path: List of audio file paths
            video_path: List of video file paths
            tools: OpenAI Tools JSON Schema (will be converted to Claude format)
            tool_choice: "auto" | "required" | "none"
            tool_call_strategy: ToolCallStrategy instance (not used in Claude, for compatibility)
            skip_add_user: If True, do NOT call add_user_message().

        Returns:
            dict: {"text": response_text, "tool_data": List[(tool_name, tool_args)] or None}
        """

        if not skip_add_user:
            self.add_user_message(
                user_message, image_path, image_b64_list=image_b64_list, audio_path=audio_path, video_path=video_path
            )
        all_msgs = self._prepare_messages()

        # -- Separate system message from conversation messages
        system_content = ""
        messages = []
        for m in all_msgs:
            if m["role"] == "system":
                system_content = m["content"]
            else:
                messages.append(m)

        # Convert tools to Claude format
        claude_tools = None
        if tools:
            claude_tools = self._convert_tools_to_claude_format(tools)
            logger.info(f"[ClaudeAPI] Converted {len(tools)} OpenAI tools to Claude format (Native FC enabled)")

        # Inject reasoning_content into assistant messages when tools are involved
        # (Claude extended thinking requires thinking blocks to be passed back in multi-turn tool flows)
        any(m.get("tool_calls") or m.get("role") == "tool" for m in messages)
        if self._prev_reasoning_content and messages:
            injected_count = 0
            for m in messages:
                if m.get("role") == "assistant" and "reasoning_content" not in m:
                    m["reasoning_content"] = self._prev_reasoning_content
                    injected_count += 1
            if injected_count:
                logger.info(
                    f"[ClaudeAPI] Injected reasoning_content into {injected_count} assistant message(s), len={len(self._prev_reasoning_content)}"
                )

        # Strip reasoning_content from messages before sending to Claude API
        # (Claude API doesn't accept reasoning_content; thinking is handled via thinking parameter)
        # reasoning_content is stored in self.req for internal pass-back only
        _stripped_messages = []
        for m in messages:
            if "reasoning_content" in m:
                m_copy = {k: v for k, v in m.items() if k != "reasoning_content"}
                _stripped_messages.append(m_copy)
            else:
                _stripped_messages.append(m)
        messages = _stripped_messages

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

        def _strip_images_from_claude_messages(msgs: list) -> list:
            """Remove image content blocks from Claude messages, keeping only text."""
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

        # Add cache_control to system prompt to enable Claude prefix cache (saves ~90% read cost)
        system_param = (
            [{"type": "text", "text": system_content, "cache_control": {"type": "ephemeral"}}]
            if system_content
            else system_content
        )

        # Build API parameters
        api_params = {
            "model": self.model,
            "max_tokens": 4096,
            "system": system_param,
            "messages": messages,
            "temperature": self.temperature,
        }

        extra_headers = {}
        sid = self._sid_provider() if self._sid_provider else None
        uid = self._user_id_provider() if self._user_id_provider else None
        if sid:
            extra_headers["X-Session-Id"] = sid
        if uid:
            extra_headers["X-User-Id"] = uid
        if extra_headers:
            api_params["extra_headers"] = extra_headers
        if self.top_k > 0:
            api_params["top_k"] = self.top_k

        # Extended thinking: Claude requires temperature=1 and thinking parameter
        if self.is_think:
            api_params["temperature"] = 1  # Claude mandates temperature=1 when thinking is enabled
            api_params["thinking"] = {"type": "enabled", "budget_tokens": self.thinking_budget_tokens}
            logger.info(f"[ClaudeAPI] Extended thinking enabled, budget={self.thinking_budget_tokens}")

        # Add tools and tool_choice if provided
        if claude_tools:
            api_params["tools"] = claude_tools
            # Convert tool_choice to Claude format
            if tool_choice == "required":
                api_params["tool_choice"] = {"type": "any"}
            elif tool_choice == "none":
                api_params["tool_choice"] = {"type": "auto"}
            # "auto" is default, no need to specify

        max_stream_retries = 6  # Increased for rate limit handling
        _images_stripped = False  # Track if images were stripped due to unsupported error
        output_media = []  # Initialize for return dict
        stream_error = False  # Initialize for return dict
        for attempt in range(max_stream_retries + 1):
            got_any_chunk = False
            try:
                if self.stream_parser:
                    self.stream_parser.clean()

                with self.client.messages.stream(**api_params) as stream:
                    full_text = ""
                    collected_thinking = []  # Capture extended thinking content
                    for event in stream:
                        got_any_chunk = True
                        if input_hub.is_stop_requested():
                            logger.info("[ClaudeAPI] Interrupted")
                            break

                        # Capture thinking content blocks
                        if hasattr(event, "type"):
                            if event.type == "content_block_start":
                                block = getattr(event, "content_block", None)
                                if block and getattr(block, "type", None) == "thinking":
                                    logger.debug("[ClaudeAPI] Thinking block started")
                            elif event.type == "content_block_delta":
                                delta = getattr(event, "delta", None)
                                if delta:
                                    # Thinking delta
                                    thinking_text = getattr(delta, "thinking", None)
                                    if thinking_text:
                                        collected_thinking.append(thinking_text)
                                        continue
                                    # Text delta
                                    text = getattr(delta, "text", None)
                                    if text:
                                        full_text += text
                                        self.printer.dynamic_single_callback(text)
                                        if self.stream_parser:
                                            self.stream_parser.feed(text)

                    final_msg = stream.get_final_message()

                    # Extract thinking content from final message (fallback for non-streaming)
                    if not collected_thinking and hasattr(final_msg, "content"):
                        for block in final_msg.content:
                            if getattr(block, "type", None) == "thinking":
                                thinking_text = getattr(block, "thinking", "")
                                if thinking_text:
                                    collected_thinking.append(thinking_text)

                    # Parse tool_use block from response.
                    # _parse_claude_tool_use returns a single (name, args) tuple or None;
                    # wrap it in a list to match the ToolCallStrategy.parse_response contract
                    # (List[Tuple[str, dict]]) and the runner's expectation, which iterates
                    # `for (t_name, t_args) in tool_data` and indexes `t[0]` per element.
                    if hasattr(final_msg, "content"):
                        _tool = self._parse_claude_tool_use(final_msg.content)
                        tool_data = [_tool] if _tool else None
                        if tool_data:
                            logger.info(f"[ClaudeAPI] Detected tool call: {tool_data[0][0]}")

                    if final_msg and hasattr(final_msg, "usage"):
                        _cache_read = getattr(final_msg.usage, "cache_read_input_tokens", 0) or 0
                        _cache_creation = getattr(final_msg.usage, "cache_creation_input_tokens", 0) or 0
                        # Unified to DeepSeek semantics: total_input_tokens = all prompt tokens (including cached portion)
                        # cache_read / cache_creation are subsets of total_input (the cached-hit / cache-written portions)
                        self.total_input_tokens += final_msg.usage.input_tokens + _cache_read + _cache_creation
                        self.total_output_tokens += final_msg.usage.output_tokens
                        self.total_cache_read_tokens += _cache_read
                        self.total_cache_creation_tokens += _cache_creation
                    else:
                        self.total_input_tokens += self._count_tokens(all_msgs)
                        self.total_output_tokens += len(self.encoding.encode(full_text)) if self.encoding else 0

                self.total_requests += 1
                if self.stream_parser:
                    self.stream_parser.finish()

                # Process thinking content
                api_reasoning = None
                if collected_thinking:
                    api_reasoning = "".join(collected_thinking)
                    # Wrap thinking in <think> tags for display consistency with DeepSeek
                    full_text = f"<think>{api_reasoning}</think>\n{full_text}"
                    logger.info(f"[ClaudeAPI] Extended thinking captured, len={len(api_reasoning)}")

                # Save assistant message with reasoning_content for pass-back
                self.add_assistant_message(
                    full_text.split("</think>\n", 1)[-1] if api_reasoning else full_text,
                    reasoning_content=api_reasoning,
                )
                self.save_history()

                # Save reasoning for next turn (required for tool_calls pass-back)
                if api_reasoning:
                    self._prev_reasoning_content = api_reasoning
                    logger.info(f"[ClaudeAPI] Saved reasoning_content for next turn, len={len(api_reasoning)}")

                # Determine finish_reason
                if tool_data:
                    finish_reason = "tool_calls"
                elif full_text.strip():
                    finish_reason = "stop"
                else:
                    finish_reason = "stop"

                # Return dict format aligned with ChatAPI
                return {
                    "text": full_text,
                    "tool_data": tool_data,
                    "output_media": output_media,
                    "finish_reason": finish_reason,
                    "stream_error": stream_error,
                    "timed_out": False,
                }

            except Exception as e:
                is_timeout = _is_timeout_error(e)
                is_image_error = _is_image_not_supported_error(e)
                can_retry_image = is_image_error and (not _images_stripped)

                if can_retry_image:
                    # Model/provider doesn't support image input -- strip images and retry
                    logger.warning(f"[ClaudeAPI] Image not supported by model, stripping images and retrying: {e}")
                    api_params["messages"] = _strip_images_from_claude_messages(api_params["messages"])
                    _images_stripped = True
                    self.is_img_model = False
                    continue

                can_retry = is_timeout and (attempt < max_stream_retries) and (not got_any_chunk)
                if can_retry:
                    wait_s = 0.8 * (attempt + 1)
                    logger.warning(
                        f"[ClaudeAPI] Stream timeout before first chunk, retrying ({attempt + 1}/{max_stream_retries}) after {wait_s:.1f}s: {e}"
                    )
                    time.sleep(wait_s)
                    continue
                timed_out = is_timeout
                stream_error = True
                logger.error(f"Claude API Error: {e}")
                return {
                    "text": f"[Error] {e}",
                    "tool_data": None,
                    "output_media": output_media,
                    "finish_reason": None,
                    "stream_error": True,
                    "timed_out": timed_out,
                }

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
        """Return cumulative token usage (aligned with ChatAPI)."""
        return {
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_tokens": self.total_input_tokens + self.total_output_tokens,
            "total_requests": self.total_requests,
            "cache_read_tokens": self.total_cache_read_tokens,
            "cache_creation_tokens": self.total_cache_creation_tokens,
        }

    def list_sessions(self) -> list[str]:
        """Return list of sessions (aligned with ChatAPI)."""
        self._ensure_history_dir()
        if not os.path.exists(self.history_dir):
            return []
        files = os.listdir(self.history_dir)
        return [f.replace(".json", "") for f in files if f.endswith(".json")]
