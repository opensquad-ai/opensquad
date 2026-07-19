"""
Input handler module -- internal command routing from the main run() loop.

Handles the ``__STOP__``, ``__NEW_SESSION__``, ``__COMPRESS_CONTEXT__``,
``__LOAD_SESSION__:sid``, ``__REQUEST_TOKEN_STATS__``, ``__RESUME_WORKFLOW__``,
``__SWITCH_AND_REPLY__:sid:content``, and ``__PROCESS_QUEUE__`` commands.

Extracted from runner.py to reduce its size.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import TYPE_CHECKING, Any

from opensquad.tool import logger

if TYPE_CHECKING:
    from opensquad.input_hub import InputHub
    from opensquad.message_queue import MessageQueue

__all__ = ["InputHandler"]


class InputHandler:
    """
    Routes internal commands from the main run() loop.

    Each method returns a ``CommandResult`` or ``None``:

        None              -> command not matched, continue processing
        (handled, None)   -> command consumed, skip this turn
        (handled, str)    -> command consumed, use str as next input

    Extracted from runner.py to keep the main run() loop clean.
    """

    async def handle_idle_command(
        self,
        initial_query: str,
        runner: Any,
        input_hub: InputHub,
        emit: Callable[[str, Any], Any],
        get_session_manager: Callable[[], Any],
        broadcast_token_stats: Callable[[], Any],
    ) -> tuple[bool, str | None] | None:
        """
        Handle internal commands that arrive while the agent is idle.

        Returns:
            ``None`` if ``initial_query`` is not a recognized command.
            ``(handled, None)`` if the command was handled and should be skipped.
            ``(handled, str)`` if the command should be reprocessed as ``str``.
        """
        cmd = initial_query.strip()

        # -- __STOP__ --------------------------------------------------------
        if cmd == "__STOP__":
            logger.info("[InputHandler] Ignoring __STOP__ command in main loop")
            try:
                from opensquad.sub_agent_runner import job_manager

                job_manager.cancel_all("stop_task")
            except Exception:
                pass
            input_hub.clear_stop_request()
            return True, None

        # -- __REQUEST_TOKEN_STATS__ ----------------------------------------
        if cmd == "__REQUEST_TOKEN_STATS__":
            logger.info("[InputHandler] Command: Request token stats broadcast")
            await broadcast_token_stats()
            return True, None

        # -- __RESUME_WORKFLOW__ -------------------------------------------
        if cmd == "__RESUME_WORKFLOW__":
            logger.info("[InputHandler] Command: Resume workflow after refresh")
            return True, "Continue the previous task from where you left off."

        # -- __NEW_SESSION__ -----------------------------------------------
        if cmd == "__NEW_SESSION__":
            logger.info("[InputHandler] Command: Start New Session")
            try:
                from opensquad.sub_agent_runner import job_manager

                job_manager.cancel_all("new_session")
            except Exception:
                pass
            runner._reset_session_stats()
            sm = get_session_manager()
            sm.start_new_session()
            try:
                from opensquad.utils.path_utils import get_workspace_root
                from opensquad.utils.session_changeset import clear_for_new_session

                clear_for_new_session(get_workspace_root())
            except Exception:
                pass
            runner._turn_sid = sm.get_current_session_id()
            runner._load_history()
            await emit("turn_start", 0)
            from opensquad.events import bus

            await bus.emit_async("session_list", sm.get_session_list())
            await bus.emit_async(
                "current_session",
                {"id": sm.get_current_session_id(), "title": "Current Session"},
            )
            await broadcast_token_stats()
            await emit("info", "New session started")
            now_ms = int(datetime.now().timestamp() * 1000)
            await emit("turn_elapsed", {"started_ms": now_ms, "ended_ms": now_ms})
            return True, None

        # -- __LOAD_SESSION__:sid ------------------------------------------
        if cmd.startswith("__LOAD_SESSION__:"):
            sid = cmd.split(":", 1)[1]
            sm = get_session_manager()
            if sm.load_history_session(sid):
                runner._turn_sid = sid
                runner._load_history()
                await emit("turn_start", 0)
                from opensquad.events import bus

                await bus.emit_async(
                    "history_sync",
                    {
                        "messages": sm.get_messages(),
                        "events": sm.get_events(),
                        "session_id": sid,
                        "is_working_session": True,
                    },
                )
                await bus.emit_async("current_session", {"id": sid, "title": "Current Session"})
                await bus.emit_async("session_list", sm.get_session_list())
                await emit("info", f"Session loaded: {sid}")
                now_ms = int(datetime.now().timestamp() * 1000)
                await emit("turn_elapsed", {"started_ms": now_ms, "ended_ms": now_ms})
            return True, None

        # -- __COMPRESS_CONTEXT__ ------------------------------------------
        if cmd == "__COMPRESS_CONTEXT__":
            logger.info("[InputHandler] Command: Manual context compression")
            return True, cmd  # Let the outer loop handle it

        return None

    async def handle_turn_command(
        self,
        cmd: str,
        runner: Any,
        input_hub: InputHub,
        emit: Callable[[str, Any], Any],
        get_session_manager: Callable[[], Any],
        broadcast_token_stats: Callable[[], Any],
    ) -> tuple[bool, str | None] | None:
        """
        Handle internal commands that arrive during a task turn (checkpoint 3).

        Returns the same values as ``handle_idle_command``.
        """
        cmd = cmd.strip()

        # -- __STOP__ ------------------------------------------------------
        if cmd == "__STOP__":
            logger.info("[InputHandler] Stop command received, safely stopping task")
            try:
                from opensquad.sub_agent_runner import job_manager

                job_manager.cancel_all("stop_task")
            except Exception:
                pass
            input_hub.clear_stop_request()
            await emit("status", "Task stopped by user")
            return True, None

        # -- __NEW_SESSION__ -----------------------------------------------
        if cmd == "__NEW_SESSION__":
            logger.info("[InputHandler] Urgent: New session requested during task")
            try:
                from opensquad.sub_agent_runner import job_manager

                job_manager.cancel_all("new_session")
            except Exception:
                pass
            runner._reset_session_stats()
            sm = get_session_manager()
            sm.start_new_session()
            try:
                from opensquad.utils.path_utils import get_workspace_root
                from opensquad.utils.session_changeset import clear_for_new_session

                clear_for_new_session(get_workspace_root())
            except Exception:
                pass
            runner._turn_sid = sm.get_current_session_id()
            runner._load_history()
            await emit("turn_start", 0)
            from opensquad.events import bus

            await bus.emit_async("session_list", sm.get_session_list())
            await bus.emit_async(
                "current_session",
                {"id": sm.get_current_session_id(), "title": "Current Session"},
            )
            await broadcast_token_stats()
            await emit("info", "New session started")
            return True, None

        # -- __COMPRESS_CONTEXT__ ------------------------------------------
        if cmd == "__COMPRESS_CONTEXT__":
            logger.info("[InputHandler] Urgent: Compress context during task")
            return True, cmd

        # -- __LOAD_SESSION__:sid ------------------------------------------
        if cmd.startswith("__LOAD_SESSION__:"):
            sid = cmd.split(":", 1)[1]
            sm = get_session_manager()
            if sm.load_history_session(sid):
                runner._turn_sid = sid
                runner._load_history()
                await emit("turn_start", 0)
                from opensquad.events import bus

                await bus.emit_async(
                    "history_sync",
                    {
                        "messages": sm.get_messages(),
                        "events": sm.get_events(),
                        "session_id": sid,
                        "is_working_session": True,
                    },
                )
                await bus.emit_async("current_session", {"id": sid, "title": "Current Session"})
                await bus.emit_async("session_list", sm.get_session_list())
                await emit("info", f"Session loaded: {sid}")
            return True, None

        # -- __SWITCH_AND_REPLY__:sid:content ----------------------------
        if cmd.startswith("__SWITCH_AND_REPLY__:"):
            parts = cmd.split(":", 2)
            if len(parts) >= 3:
                sid, reply_content = parts[1], (parts[2] or "").strip()
                sm = get_session_manager()
                if sid and sid != sm.get_current_session_id():
                    if sm.load_history_session(sid):
                        runner._turn_sid = sid
                        runner._load_history()
                        if reply_content:
                            await emit("turn_start", 0)
                        from opensquad.events import bus

                        await bus.emit_async("current_session", {"id": sid, "title": "Current Session"})
                        await bus.emit_async("session_list", sm.get_session_list())
                # Empty content = switch only; do not invent a blank user turn
                if not reply_content:
                    return True, None
                cmd_images = runner._current_images
                cmd_attachments = runner._current_attachments
                if cmd_images:
                    logger.info(
                        "[InputHandler] Urgent SWITCH_AND_REPLY with %d images",
                        len(cmd_images),
                    )
                if cmd_attachments:
                    logger.info(
                        "[InputHandler] Urgent SWITCH_AND_REPLY with %d attachments",
                        len(cmd_attachments),
                    )
                return True, reply_content

        return None

    async def handle_queue_process(
        self,
        runner: Any,
        input_hub: InputHub,
        message_queue: MessageQueue,
        emit: Callable[[str, Any], Any],
    ) -> str | None:
        """
        Process ``__PROCESS_QUEUE__`` command: drain message queue + pending web messages.

        Returns:
            ``None`` if there was nothing to process (skip this turn).
            A user message string if messages were found and formatted.
        """
        pending = message_queue.get_all()
        extra_web = input_hub.get_all_pending()

        if extra_web:
            logger.info(
                "[InputHandler] __PROCESS_QUEUE__: also found %d pending web message(s) in input_hub, merging",
                len(extra_web),
            )

        if not pending and not extra_web:
            return None

        # -- Only web messages (no group messages) --------------------------
        if not pending and extra_web:
            first_web = extra_web[0]
            result = first_web.get("content", "")
            runner._current_input_source = first_web.get("source", "gateway")
            runner._current_channel = first_web.get("channel", "web")
            runner._current_images = first_web.get("images", [])
            runner._current_attachments = first_web.get("attachments", [])
            runner._current_sender_name = first_web.get("sender_name", "")
            runner._current_chat_name = first_web.get("chat_name", "")
            runner._current_source_chat_id = first_web.get("source_chat_id", "")
            runner._current_user_id = first_web.get("user_id", "")

            if len(extra_web) > 1:
                extra_parts = [w.get("content", "") for w in extra_web[1:] if w.get("content")]
                if extra_parts:
                    result = result + "\n" + "\n".join(extra_parts)
            logger.info("[InputHandler] __PROCESS_QUEUE__ with only web messages")
            return result

        # -- Format group messages as AI input ----------------------------
        msg_parts = []
        all_images = []
        for msg in pending:
            if msg.type == "group":
                msg_parts.append(f"[{msg.source_name} | group_id={msg.source_id}] {msg.sender_name}: {msg.content}")
            elif msg.type == "dm":
                msg_parts.append(f"[DM] {msg.sender_name}: {msg.content}")
            if msg.images:
                all_images.extend(msg.images)

        if all_images:
            runner._current_images = all_images
            logger.info(
                "[InputHandler] Collected %d images from queue messages",
                len(all_images),
            )

        detailed_messages = [
            {
                "type": msg.type,
                "source_id": msg.source_id,
                "source_name": msg.source_name,
                "sender_id": msg.sender_id,
                "sender_name": msg.sender_name,
                "content": msg.content,
                "mentions": list(getattr(msg, "mentions", []) or []),
                "images": list(msg.images or []),
                "timestamp": getattr(msg, "timestamp", None),
            }
            for msg in pending
        ]
        await emit(
            "info",
            {
                "event": "incoming_messages",
                "count": len(pending),
                "source": "chatpro_group",
                "text": f"Received {len(pending)} new message(s), processing...",
                "messages": detailed_messages,
            },
        )

        result = (
            "[Messages]\n"
            + "\n".join(msg_parts)
            + "[Messages received, please decide how to reply based on the source]"
        )
        runner._current_input_source = "chatpro"
        runner._current_channel = "chatpro_group"

        # Merge pending web messages
        if extra_web:
            web_parts = []
            for w in extra_web:
                wc = w.get("content", "")
                if wc and wc != "__PROCESS_QUEUE__":
                    web_parts.append(f"[Web User] {wc}")
                    if w.get("images"):
                        runner._current_images = (runner._current_images or []) + w["images"]
                    if w.get("attachments"):
                        runner._current_attachments = (runner._current_attachments or []) + w["attachments"]
            if web_parts:
                result += "\n\n[Simultaneously received web messages]\n" + "\n".join(web_parts)
                logger.info(
                    "[InputHandler] Merged %d web message(s) into __PROCESS_QUEUE__ turn",
                    len(web_parts),
                )

        return result

    def merge_group_messages(
        self,
        runner: Any,
        pending_group_messages: list,
    ) -> str | None:
        """
        Merge orphaned group messages (drained during idle) into the current turn input.

        Called when ``idle_wait`` drained messages before returning.

        Returns:
            ``None`` if there were no pending messages.
            A formatted string to append to the user query.
        """
        if not pending_group_messages:
            return None

        msg_parts = []
        all_images = []
        for msg in pending_group_messages:
            if msg.type == "group":
                msg_parts.append(f"[{msg.source_name} | group_id={msg.source_id}] {msg.sender_name}: {msg.content}")
            elif msg.type == "dm":
                msg_parts.append(f"[DM] {msg.sender_name}: {msg.content}")
            if msg.images:
                all_images.extend(msg.images)

        if all_images:
            runner._current_images = (runner._current_images or []) + all_images

        formatted = "\n".join(msg_parts)
        return f"\n\n[Simultaneously received group messages]\n{formatted}"
