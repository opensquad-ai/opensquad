# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass
class LlmPreparedCall:
    current_input: str
    is_first_turn: bool
    native_images: list[str] | None
    b64_images: list[str] | None
    audio_paths: list[str]
    video_paths: list[str]


@dataclass
class LlmNormalizedResponse:
    response_text: str
    tool_data_from_api: Any
    output_media: list[Any]
    finish_reason: str | None
    stream_error: bool


class LlmCallOrchestrator:
    """Extracts LLM call prep/call/normalization from `AgentRunner.run()`."""

    def __init__(self, runner: Any):
        self.runner = runner

    async def prepare_call(self, current_input: str, turn_index: int) -> LlmPreparedCall:
        is_first_turn = turn_index == 0
        if is_first_turn:
            from opensquad.event_pipeline import event_pipeline

            drained = event_pipeline.drain_formatted_sync()
            if drained:
                pass

        current_input = self._inject_attachment_paths(current_input)
        native_images = None
        b64_images = None
        audio_paths = self._collect_audio_paths()
        video_paths = self._collect_video_paths()

        if self.runner._current_images:
            images = self.runner._current_images
            self.runner._current_images = []
            for image_path in images:
                if not os.path.exists(image_path):
                    await self.runner._emit("info", f"Warning: image not found: {os.path.basename(image_path)}")
            if self.runner._is_img_mode:
                native_images = images
                await self.runner._emit("info", f"Sending {len(images)} image(s) to model")
            else:
                await self.runner._emit(
                    "info",
                    f"Current model does not support image input; skipped {len(images)} image(s). To enable image recognition, set model.is_image to true in config.json.",
                )

        if audio_paths or video_paths:
            if audio_paths:
                current_input += "\n\n[Audio attachment paths]\n" + "\n".join(audio_paths)
                await self.runner._emit("info", f"Received {len(audio_paths)} audio file(s)")
                current_input += "\n[Tip] To transcribe audio, call whisper_transcribe.transcribe_audio_file(audio_path=...)."
            if video_paths:
                current_input += "\n\n[Video attachment paths]\n" + "\n".join(video_paths)
                await self.runner._emit("info", f"Received {len(video_paths)} video file(s)")
                current_input += "\n[Tip] To process video, use system.run_session_job to call ffmpeg to extract audio/keyframes first."

        if getattr(self.runner, "_tool_result_images", None):
            if self.runner._is_img_mode:
                b64_images = self.runner._tool_result_images
            self.runner._tool_result_images = []

        return LlmPreparedCall(
            current_input=current_input,
            is_first_turn=is_first_turn,
            native_images=native_images,
            b64_images=b64_images,
            audio_paths=audio_paths,
            video_paths=video_paths,
        )

    async def run_before_llm_hook(self) -> bool:
        if not self.runner._plugin_manager:
            return True
        hook_ctx = await self.runner._plugin_manager.run_hook(
            "on_before_llm",
            {
                "messages": self.runner.chat_api.req if hasattr(self.runner.chat_api, "req") else [],
                "model": getattr(self.runner.chat_api, "model", ""),
                "agent_id": self.runner._agent_id,
            },
        )
        return not hook_ctx.get("__stop__")

    async def call_llm(self, prepared: LlmPreparedCall) -> Any:
        from opensquad.structured_log import perf_event
        t0 = __import__("time").perf_counter()
        llm_timeout = getattr(self.runner.chat_api, "timeout", 30.0)
        asyncio_timeout = llm_timeout + 15.0
        elapsed_ms = 0
        try:
            return await asyncio.wait_for(
                self.runner.chat_api.chat(
                    prepared.current_input,
                    image_path=prepared.native_images,
                    image_b64_list=prepared.b64_images,
                    audio_path=prepared.audio_paths if getattr(self.runner.chat_api, "is_audio_model", False) else None,
                    video_path=prepared.video_paths if getattr(self.runner.chat_api, "is_video_model", False) else None,
                    tools=self.runner._current_tools,
                    tool_choice=self.runner._current_tool_choice,
                    tool_call_strategy=self.runner.tool_call_strategy,
                    skip_add_user=not prepared.is_first_turn,
                ),
                timeout=asyncio_timeout,
            )
        except asyncio.TimeoutError:
            await self.runner._emit("status", "LLM API response timed out, please try again later")
            await self.runner._emit(
                "error",
                {"message": f"LLM API call timed out after {asyncio_timeout}s. Please check your network or try again later."},
            )
            elapsed_ms = int((__import__("time").perf_counter() - t0) * 1000)
            perf_event("runner", "llm_call_done", agent_id=getattr(self.runner, "_agent_id", ""), elapsed_ms=elapsed_ms, error="timeout")
            raise
        except Exception as exc:
            err_msg = str(exc)
            elapsed_ms = int((__import__("time").perf_counter() - t0) * 1000)
            perf_event("runner", "llm_call_done", agent_id=getattr(self.runner, "_agent_id", ""), elapsed_ms=elapsed_ms, error=str(exc)[:80])
            if "401" in err_msg or "Unauthorized" in err_msg or "invalid api key" in err_msg.lower():
                friendly = (
                    "LLM API authentication failed (HTTP 401). "
                    "Your api_key is invalid or expired. "
                    "Please update the api_key in model_cards/*.json and restart the agent."
                )
            elif "403" in err_msg or "Forbidden" in err_msg:
                friendly = (
                    "LLM API access denied (HTTP 403). "
                    "Your api_key may not have permission for this model. "
                    "Check your API provider account settings."
                )
            elif "429" in err_msg or "rate limit" in err_msg.lower():
                friendly = "LLM API rate limit exceeded (HTTP 429). Please wait a moment and try again."
            elif "Connection" in err_msg or "connect" in err_msg.lower() or "refused" in err_msg.lower():
                friendly = f"Unable to connect to LLM API: {err_msg[:200]}. Please check your network and base_url configuration."
            else:
                friendly = f"LLM API call failed: {err_msg[:300]}"
            await self.runner._emit("status", "LLM API call failed")
            await self.runner._emit("error", {"message": friendly})
            raise
        else:
            elapsed_ms = int((__import__("time").perf_counter() - t0) * 1000)
            perf_event("runner", "llm_call_done", agent_id=getattr(self.runner, "_agent_id", ""), elapsed_ms=elapsed_ms)

    async def handle_auto_compression(self) -> None:
        if not getattr(self.runner.chat_api, "_auto_compressed", False):
            return
        summary = getattr(self.runner.chat_api, "_latest_summary", "")
        if summary:
            summary_event = {
                "event": "context_summary_generated",
                "text": "Context auto-compacted",
                "summary": summary,
            }
            self.runner._session_manager.add_event("info", summary_event, turn_id=self.runner._current_turn, round_id=self.runner._current_round)
            self.runner._session_manager.add_message("system", summary, msg_type="context_summary")
            self.runner._session_manager.session_data["latest_summary"] = summary
            await self.runner._emit("info", summary_event)
            summary_stream_id = f"auto_compress_{self.runner._session_manager.get_current_session_id()}"
            await self.runner._emit(
                "summary_stream",
                {"id": summary_stream_id, "delta": summary, "text": summary, "done": True, "trace_id": "auto"},
            )

        self.runner._context_builder._has_prompt_snapshot = False
        await self.runner._setup_prompt()
        sid = self.runner._session_manager.get_current_session_id()
        history_data = {
            "messages": self.runner._session_manager.get_messages(),
            "events": self.runner._session_manager.get_events(),
            "session_id": sid,
            "is_working_session": True,
        }
        await self.runner._bus.emit_async("history_sync", history_data)
        await self.runner._bus.emit_async("current_session", {"id": sid, "title": "Current Session"})
        await self.runner._bus.emit_async("session_list", self.runner._session_manager.get_session_list())
        await self.runner._emit(
            "turn_elapsed",
            {"started_ms": int(self.runner._workflow_started_ms), "ended_ms": int(datetime.now().timestamp() * 1000)},
        )
        await self.runner._broadcast_token_stats()
        self.runner.chat_api._auto_compressed = False

    async def normalize_response(self, ai_response: Any) -> LlmNormalizedResponse:
        if isinstance(ai_response, dict):
            response_text = ai_response.get("text", "")
            tool_data_from_api = ai_response.get("tool_data")
            output_media = ai_response.get("output_media", [])
            finish_reason = ai_response.get("finish_reason")
            stream_error = ai_response.get("stream_error", False)
        else:
            response_text = ai_response
            tool_data_from_api = None
            output_media = []
            finish_reason = None
            stream_error = False

        if self.runner._plugin_manager:
            hook_ctx = await self.runner._plugin_manager.run_hook(
                "on_after_llm",
                {"response": response_text, "agent_id": self.runner._agent_id},
            )
            response_text = hook_ctx.get("response", response_text)

        return LlmNormalizedResponse(
            response_text=response_text,
            tool_data_from_api=tool_data_from_api,
            output_media=output_media,
            finish_reason=finish_reason,
            stream_error=stream_error,
        )

    async def handle_stop_after_response(self) -> bool:
        if not self.runner._input_hub.is_stop_requested():
            return False
        self.runner._input_hub.clear_stop_request()
        partial = "".join(getattr(self.runner, "_streamed_user_text", []))
        if partial.strip():
            self.runner._session_manager.add_message("assistant", partial.strip())
        await self.runner._emit("status", "Task stopped")
        return True

    def _collect_audio_paths(self) -> list[str]:
        audio_paths: list[str] = []
        for attachment in self.runner._current_attachments or []:
            if not isinstance(attachment, dict):
                continue
            media_type = attachment.get("type")
            path = attachment.get("path") or attachment.get("url") or ""
            if not media_type:
                if attachment.get("is_video"):
                    media_type = "video"
                elif attachment.get("is_audio"):
                    media_type = "audio"
                else:
                    media_type = "file"
            if media_type == "audio" and path:
                audio_paths.append(path)
        return audio_paths

    def _collect_video_paths(self) -> list[str]:
        video_paths: list[str] = []
        for attachment in self.runner._current_attachments or []:
            if not isinstance(attachment, dict):
                continue
            media_type = attachment.get("type")
            path = attachment.get("path") or attachment.get("url") or ""
            if not media_type:
                if attachment.get("is_video"):
                    media_type = "video"
                elif attachment.get("is_audio"):
                    media_type = "audio"
                else:
                    media_type = "file"
            if media_type == "video" and path:
                video_paths.append(path)
        return video_paths

    def _inject_attachment_paths(self, current_input: str) -> str:
        if not self.runner._current_attachments:
            return current_input
        attachment_lines = []
        for attachment in self.runner._current_attachments:
            if isinstance(attachment, dict):
                name = attachment.get("original_name") or attachment.get("filename") or attachment.get("name") or "unknown"
                path = attachment.get("path") or attachment.get("url") or ""
                media_type = attachment.get("type")
                if not media_type:
                    if attachment.get("is_video"):
                        media_type = "video"
                    elif attachment.get("is_audio"):
                        media_type = "audio"
                    else:
                        media_type = "file"
                if path:
                    attachment_lines.append(f"[{media_type}] {name} (path={path})")
                else:
                    attachment_lines.append(f"[{media_type}] {name}")
            else:
                attachment_lines.append(str(attachment))
        if attachment_lines:
            current_input += "\n\n[Attachments]\n" + "\n".join(attachment_lines)
        return current_input
