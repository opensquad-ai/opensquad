import asyncio
import logging
import queue
from typing import Any

from opensquad.message_queue import get_message_queue

logger = logging.getLogger(__name__)


class InputHub:
    """
    Central input pool: manages concurrent input from CLI, Web, ChatPro, and other sources.
    Automatically checks the message queue each conversation turn to merge group/DM messages into the dialog.

    Both the normal queue and the urgent queue have a maximum capacity of 10,000 items.
    When the queue is full, backpressure is applied:
    - push() and push_urgent() attempt up to 3 retries with 0.1s sleep intervals.
    - If still full after retries, the oldest item is evicted to make room.
    """

    def __init__(self):
        self._queue: asyncio.Queue | None = None
        self._urgent_queue: asyncio.Queue | None = None  # urgent command queue (for interrupts)
        self.last_message_source = None  # records the source of the last message, used for replies
        self._stop_requested = False  # stop request flag
        self.agent_dir = None  # Agent root directory (e.g. agents/ai002)
        # ── Event-driven notification (P0 perf: replaces 1s polling) ──
        self._new_input_event: asyncio.Event | None = None

    def set_agent_context(self, agent_dir: str):
        """Set Agent context for localizing multi-modal resources."""
        self.agent_dir = agent_dir

    def _check_session_cwd(self):
        """Check for .session_cwd signal file and apply working directory.

        Called at the start of every conversation turn (in
        ``get_user_response()``). If the launcher has written a
        ``.session_cwd`` file in the agent's directory, we read the path
        and call ``filesystem.set_session_cwd()`` to update the agent's
        working directory in real-time.

        Also updates ``AgentContext.session_cwd`` so that
        ``get_workspace_root()`` returns the new path.
        """
        if not self.agent_dir:
            logger.debug("[InputHub] _check_session_cwd: agent_dir not set, skipping")
            return
        import os as _os

        from opensquad.utils.session_cwd import read_session_cwd, session_cwd_path

        cwd_file = session_cwd_path(self.agent_dir)
        if not _os.path.isfile(cwd_file):
            try:
                from opensquad._context import get_current_context
                from opensquad.utils.path_utils import set_session_cwd_override

                ctx = get_current_context()
                if ctx and ctx.session_cwd:
                    ctx.session_cwd = ""
                    set_session_cwd_override(None)
                    logger.info("[InputHub] Session working directory reset (signal file removed)")
            except Exception as e:
                logger.debug(f"[InputHub] _check_session_cwd reset skipped: {e}")
            return

        data = read_session_cwd(self.agent_dir)
        if not data:
            return
        new_cwd = (data.get("path") or "").strip()

        if not new_cwd or not _os.path.isdir(new_cwd):
            logger.warning(
                f"[InputHub] _check_session_cwd: invalid path '{new_cwd}' (isdir={_os.path.isdir(new_cwd) if new_cwd else 'N/A'})"
            )
            return

        # Check if already applied (avoid re-applying on every turn)
        try:
            from opensquad._context import get_current_context

            ctx = get_current_context()
            if ctx and ctx.session_cwd == new_cwd:
                logger.debug(f"[InputHub] _check_session_cwd: already applied '{new_cwd}', skipping")
                return
            logger.info(
                f"[InputHub] _check_session_cwd: applying new cwd '{new_cwd}' (ctx.session_cwd was '{ctx.session_cwd if ctx else 'None'}')"
            )
        except Exception as e:
            logger.warning(f"[InputHub] _check_session_cwd: context check failed: {e}")

        # Apply the new working directory
        try:
            from opensquad.tools.filesystem import set_session_cwd

            result = set_session_cwd(new_cwd)
            if result.get("status") == "success":
                logger.info(f"[InputHub] Session working directory applied: {new_cwd}")
            else:
                logger.warning(f"[InputHub] set_session_cwd returned: {result}")
        except Exception as e:
            logger.warning(f"[InputHub] Failed to apply session_cwd: {e}")

    def _get_queue(self) -> asyncio.Queue:
        if self._queue is None:
            self._queue = asyncio.Queue(maxsize=10000)
        return self._queue

    def _get_urgent_queue(self) -> asyncio.Queue:
        if self._urgent_queue is None:
            self._urgent_queue = asyncio.Queue(maxsize=10000)
        return self._urgent_queue

    async def get_user_response(self) -> dict[str, Any]:
        """
        Wait for user input while automatically checking the message pipeline.
        Supports reading from both the normal queue and the urgent queue.

        CRITICAL: Uses try/finally to ensure queue.get() subtasks are properly
        cancelled, preventing asyncio.wait_for() timeout cancellation from leaving
        orphan tasks that steal queued messages.
        """
        # ── Check for session_cwd signal file ──────────────────────────
        # The launcher writes .session_cwd when the user picks a folder via
        # the chat UI folder-picker button. We check it here (at the start
        # of every conversation turn) and apply it before processing the
        # next user message. This ensures the agent's shell commands and
        # file operations use the user-selected working directory.
        self._check_session_cwd()

        queue = self._get_queue()
        urgent_queue = self._get_urgent_queue()
        logger.debug(
            f"[InputHub] get_user_response ENTER - queue_size={queue.qsize()}, urgent_size={urgent_queue.qsize()}"
        )

        # Check urgent queue first
        if not urgent_queue.empty():
            item = await urgent_queue.get()
            logger.info(
                f"[InputHub] get_user_response EXIT (urgent) - source={item.get('source')}, content={str(item.get('content', ''))[:80]}"
            )
            return item

        # If the normal queue is non-empty, return immediately
        if not queue.empty():
            user_input = await queue.get()
            logger.debug(
                f"[InputHub] get_user_response EXIT (immediate) - source={user_input.get('source')}, content={str(user_input.get('content', ''))[:60]}"
            )
        else:
            logger.debug("[InputHub] get_user_response WAITING (blocking) - both queues empty, awaiting...")
            # Wait on both the normal input and the urgent command simultaneously
            q_get = asyncio.create_task(queue.get())
            u_get = asyncio.create_task(urgent_queue.get())

            try:
                done, pending = await asyncio.wait([q_get, u_get], return_when=asyncio.FIRST_COMPLETED)

                # Cancel tasks that have not yet completed
                for task in pending:
                    task.cancel()

                # Retrieve the result
                user_input = next(iter(done)).result()
                logger.debug(
                    f"[InputHub] get_user_response EXIT (awaited) - source={user_input.get('source')}, content={str(user_input.get('content', ''))[:60]}"
                )
            except (asyncio.CancelledError, Exception):
                # wait_for timeout cancelled this coroutine -- must clean up subtasks!
                # If not cancelled, orphan tasks will steal the next push's message
                rescued = 0
                for task, target_q in [(q_get, queue), (u_get, urgent_queue)]:
                    if not task.done():
                        task.cancel()
                    elif not task.cancelled():
                        # task completed but result was never read -- put message back in the CORRECT queue
                        try:
                            item = task.result()
                            qname = "urgent_queue" if target_q is urgent_queue else "queue"
                            logger.info(
                                f"[InputHub] RESCUED orphaned item during cancel: content={str(item.get('content', ''))[:80]}, putting back into {qname}"
                            )
                            target_q.put_nowait(item)
                            rescued += 1
                        except Exception:
                            pass
                if rescued:
                    logger.info(f"[InputHub] Rescued {rescued} item(s) back to their queues")
                raise  # re-raise so wait_for can handle it normally

        # For __PROCESS_QUEUE__ triggers, do not pre-consume message_queue here.
        # Let the Runner's __PROCESS_QUEUE__ handler pull from message_queue itself.
        # Otherwise get_all() here would drain the queue and Runner's get_all() would return empty.
        content = user_input.get("content", "")
        if content == "__PROCESS_QUEUE__":
            logger.info("[InputHub] __PROCESS_QUEUE__ trigger, skipping message_queue drain")
            self.last_message_source = None
            return user_input

        # Check message pipeline (non-blocking, drain all accumulated messages)
        pending_messages = get_message_queue().get_all()
        # Change INFO to DEBUG for frequent polling log
        logger.debug(
            f"[InputHub] get_user_response: source={user_input.get('source')}, content_len={len(content)}, pending_msgs={len(pending_messages)}"
        )

        # Key: clear stale message source on every new input.
        # Only re-set it when the current input actually carries group messages.
        self.last_message_source = None

        if pending_messages:
            # Build message context
            message_context = self._format_messages(pending_messages)

            # Attach message context as supplementary info, not embedded in user input text.
            # Runner will surface it via an info event in the frontend, not in the dialog box.
            user_input["has_messages"] = True
            user_input["message_count"] = len(pending_messages)
            user_input["message_context"] = message_context

            # Record message source (for replies) - use the last message's source
            last_msg = pending_messages[-1]
            if last_msg.type == "group":
                self.last_message_source = {
                    "type": "group",
                    "target_id": last_msg.source_id,
                    "sender_id": last_msg.sender_id,
                }
            elif last_msg.type == "dm":
                self.last_message_source = {
                    "type": "dm",
                    "target_id": last_msg.sender_id,
                    "sender_name": last_msg.sender_name,
                }

        return user_input

    def _format_messages(self, messages: list) -> str:
        """Format messages into a text context."""
        formatted = []
        for msg in messages:
            if msg.type == "group":
                # Add Group ID to the formatted string so agents know it
                formatted.append(f"[Group {msg.source_name} (ID: {msg.source_id})] {msg.sender_name}: {msg.content}")
            elif msg.type == "dm":
                formatted.append(f"[DM] {msg.sender_name}: {msg.content}")
        return "\n".join(formatted)

    def _fix_path(self, path: str) -> str:
        """
        Convert a web-relative path (/uploads/xxx) to a local absolute path.
        If agent_dir is set, also copies the file into the Agent's data/uploads
        directory for resource privatization and isolation.
        """
        if not path:
            return path

        import os
        import shutil

        logger.info(f"[InputHub] _fix_path input: {path}")

        from opensquad.system_config import syscfg

        workspace_root = syscfg.project_root()
        # Uploads are stored in data/uploads/ in the workspace
        uploads_dir = os.path.join(workspace_root, "data", "uploads")

        # Handle /uploads/ paths
        if path.startswith("/uploads/") or path.startswith("uploads/"):
            filename = path.split("/")[-1]  # extract just the filename
            # Absolute source path (data/uploads/)
            src_abs_path = os.path.join(uploads_dir, filename)

            # If agent_dir is configured, copy to the agent's private directory
            if self.agent_dir and os.path.exists(src_abs_path):
                # Target directory: agents/xxx/data/uploads
                target_dir = os.path.join(self.agent_dir, "data", "uploads")
                os.makedirs(target_dir, exist_ok=True)

                filename = os.path.basename(src_abs_path)
                target_abs_path = os.path.join(target_dir, filename)

                # Copy file (if target does not exist or source is newer)
                try:
                    if not os.path.exists(target_abs_path):
                        shutil.copy2(src_abs_path, target_abs_path)
                        logger.info(f"[InputHub] Copied upload to agent dir: {target_abs_path}")
                    return target_abs_path
                except Exception as e:
                    logger.error(f"[InputHub] Failed to copy image: {e}")
                    # Fallback to source path
                    return src_abs_path

            return src_abs_path

        logger.info(f"[InputHub] _fix_path passthrough: {path} (exists={os.path.exists(path)})")
        return path

    def push(
        self,
        content: str,
        source: str = "web",
        images: list | None = None,
        attachments: list | None = None,
        channel: str = "",
        sender_name: str = "",
        chat_name: str = "",
        source_chat_id: str = "",
        user_id: str = "",
    ):
        """Push a new command, optionally with image path list, attachments, and channel identifier.

        Backpressure: when the queue is at maxsize=10000, the oldest item is
        evicted to make room. This is a sync method -- callers in tools,
        plugins, and async handlers all use it without await.
        """
        import os

        q = self._get_queue()
        logger.debug(
            f"[InputHub] PUSH from {source} (channel={channel}): content_len={len(content)}, queue_size_before={q.qsize()}, queue_id={id(q)}"
        )

        # NOTE: event_pipeline push removed from here — it caused the trigger message
        # to be processed twice (once as role=user, once re-injected via role=tool).
        # message_queue.put() already handles event_pipeline push for group/DM messages.
        # Web/gateway messages flow through the normal input_hub queue and are detected
        # by the runner's wait loop via input_hub.get_all_pending().

        # Fix paths in images list
        fixed_images = []
        if images:
            logger.info(f"[InputHub] Received {len(images)} image(s): {images}")
            for img in images:
                fixed = self._fix_path(img)
                logger.info(f"[InputHub] Image path: {img} -> {fixed} (exists={os.path.exists(fixed)})")
                fixed_images.append(fixed)

        # Fix paths in attachments list
        fixed_attachments = []
        if attachments:
            for att in attachments:
                if isinstance(att, dict):
                    path = att.get("path") or att.get("url")
                    if isinstance(path, str):
                        att = {**att}
                        att["path"] = self._fix_path(path)
                        if "url" in att:
                            att["url"] = att["path"]
                    fixed_attachments.append(att)
                else:
                    fixed_attachments.append(att)

        # Fix paths in content (e.g. Markdown links)
        # Simple replacement for typical patterns
        if "/uploads/" in content:
            # Bug 5 fix: use the same path as _fix_path (workspace/data/uploads)
            # instead of the previously hardcoded gateway/backend/uploads.
            import os

            from opensquad.system_config import syscfg

            uploads_abs = os.path.join(syscfg.project_root(), "data", "uploads").replace("\\", "/")
            # Replace /uploads with full path (normalized to forward slashes for consistency in text)
            content = content.replace("/uploads", uploads_abs)

        data = {"source": source, "content": content}
        if channel:
            data["channel"] = channel
        if sender_name:
            data["sender_name"] = sender_name
        if chat_name:
            data["chat_name"] = chat_name
        if source_chat_id:
            data["source_chat_id"] = source_chat_id
        if user_id:
            data["user_id"] = user_id
        if fixed_images:
            data["images"] = fixed_images
        if fixed_attachments:
            data["attachments"] = fixed_attachments

        # ── Backpressure-aware put (sync) ──
        # Try put_nowait; if queue is full, evict oldest item and retry.
        try:
            q.put_nowait(data)
        except asyncio.QueueFull:
            logger.warning(
                "[InputHub] Queue full (size=%d), evicting oldest item for push from %s",
                q.qsize(),
                source,
            )
            try:
                q.get_nowait()  # evict oldest
            except asyncio.QueueEmpty:
                pass
            q.put_nowait(data)

        # Signal event-driven waiters
        if self._new_input_event is not None:
            self._new_input_event.set()
        logger.debug(f"[InputHub] PUSH DONE - queue_size_after={self._get_queue().qsize()}")

    def push_urgent(
        self,
        content: str,
        source: str = "web",
        images: list | None = None,
        attachments: list | None = None,
        channel: str = "",
    ):
        """Push an urgent command (interrupts the current task), optionally with image paths,
        attachments, and channel identifier.

        Backpressure: when the urgent queue is at maxsize=10000, the oldest item
        is evicted to make room. This is a sync method.
        """
        # Fix paths in images list
        fixed_images = []
        if images:
            for img in images:
                fixed_images.append(self._fix_path(img))

        # Fix paths in attachments list
        fixed_attachments = []
        if attachments:
            for att in attachments:
                if isinstance(att, dict):
                    path = att.get("path") or att.get("url")
                    if isinstance(path, str):
                        att = {**att}
                        att["path"] = self._fix_path(path)
                        if "url" in att:
                            att["url"] = att["path"]
                    fixed_attachments.append(att)
                else:
                    fixed_attachments.append(att)

        data = {"source": source, "content": content}
        if channel:
            data["channel"] = channel
        if fixed_images:
            data["images"] = fixed_images
        if fixed_attachments:
            data["attachments"] = fixed_attachments
        uq = self._get_urgent_queue()

        # ── Backpressure-aware put (sync) ──
        try:
            uq.put_nowait(data)
        except asyncio.QueueFull:
            logger.warning(
                "[InputHub] Urgent queue full (size=%d), evicting oldest item for push_urgent from %s",
                uq.qsize(),
                source,
            )
            try:
                uq.get_nowait()  # evict oldest
            except asyncio.QueueEmpty:
                pass
            uq.put_nowait(data)

        # Signal event-driven waiters
        if self._new_input_event is not None:
            self._new_input_event.set()
        logger.info(
            f"[InputHub] PUSH_URGENT: content={str(content)[:80]}, source={source}, urgent_queue_size_after={uq.qsize()}, queue_id={id(uq)}"
        )

    def check_urgent_commands(self) -> list:
        """Non-blocking check for all pending urgent commands."""
        items = []
        q = self._get_urgent_queue()
        while True:
            try:
                items.append(q.get_nowait())
            except asyncio.QueueEmpty:
                break
            except queue.Empty:
                break
        return items

    def request_stop(self):
        """Request that the current task flow be stopped."""
        self._stop_requested = True
        self.push_urgent("__STOP__", source="system")
        # Also abort in-flight sub-agents (sync + async) — otherwise they keep
        # streaming into the UI / next session after the parent stops.
        try:
            from opensquad.sub_agent_runner import job_manager

            n = job_manager.cancel_all("stop_task")
            if n:
                logger.info(f"[InputHub] cancelled {n} sub-agent job(s)/runner(s) on stop")
        except Exception:
            logger.debug("[InputHub] sub-agent cancel_all on stop skipped", exc_info=True)

    def clear_stop_request(self):
        """Clear the stop request."""
        self._stop_requested = False

    def is_stop_requested(self) -> bool:
        """Check whether a stop has been requested."""
        return self._stop_requested

    def get_all_pending(self) -> list:
        """Non-blocking retrieval of all currently pending inputs."""
        items = []
        q = self._get_queue()
        while True:
            try:
                items.append(q.get_nowait())
            except asyncio.QueueEmpty:
                break
            except queue.Empty:
                break
        return items

    # ------------------------------------------------------------------
    # Event-driven notification API (P0 perf: replaces 1s polling)
    # ------------------------------------------------------------------

    def get_input_event(self) -> asyncio.Event:
        """Get (or lazily create) the asyncio.Event that is set when new input arrives."""
        if self._new_input_event is None:
            self._new_input_event = asyncio.Event()
        return self._new_input_event

    async def wait_for_input(self, timeout: float = 5.0) -> bool:
        """Wait up to *timeout* seconds for new input to arrive.

        Returns True if input is available, False on timeout.
        This replaces the `await asyncio.wait_for(get_user_response(), timeout=5.0)`
        pattern with a zero-CPU idle wait.
        """
        event = self.get_input_event()
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
            event.clear()
            return True
        except asyncio.TimeoutError:
            return False


# Global singleton
input_hub = InputHub()


# ── AgentContext-aware getter (Phase 1a) ──
def get_input_hub(ctx=None):
    """Return input_hub from AgentContext if available, else global singleton."""
    if ctx is not None:
        return ctx.input_hub
    from opensquad._context import get_current_context

    ctx = get_current_context()
    return ctx.input_hub if ctx is not None else input_hub
