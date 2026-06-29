# -*- coding: utf-8 -*-
"""
Message router - decides how to handle group messages based on AI state.
"""
import time
from typing import Dict, Any
import logging

from opensquad.state_manager import get_state_manager
from opensquad.sleep_controller import get_sleep_controller
from opensquad.input_hub import get_input_hub
from opensquad.message_queue import get_message_queue, QueueMessage

logger = logging.getLogger(__name__)

class MessageRouter:
    """
    Routes group messages to the correct destination.
    Decides based on AI state and wake mode:
    - Whether to wake the AI
    - Whether to push the message
    - Whether to only accumulate without notifying

    Cooldown mechanism:
    - After the agent sends a group message, it enters a cooldown period (default 10s).
    - During cooldown, group messages are only enqueued, not triggering __PROCESS_QUEUE__.
    - The first new message after cooldown ends triggers batch consumption.
    - @mentions bypass cooldown and trigger immediately.
    """

    def __init__(self):
        self._cooldown_until = 0.0   # cooldown expiry timestamp
        self._cooldown_seconds = 10  # default cooldown duration in seconds
        self._last_send_time = 0.0   # timestamp of the last group message sent
        self._await_reply_seconds = 30  # reply-wait timeout in seconds (reduced from 120s to 30s to avoid long blocking)

    def set_cooldown(self, seconds: float = None):
        """Set the message-filter cooldown period (filter only, no wake trigger)."""
        dur = seconds if seconds is not None else self._cooldown_seconds
        self._cooldown_until = time.time() + dur
        logger.info(f"[Router] Cooldown set: {dur}s (until {self._cooldown_until:.0f})")

    def set_wakeup_delay(self, seconds: float):
        """
        Set the auto-wakeup wait duration after sending a group message.
        When Runner detects awaiting_reply it enters an interruptible sleep(seconds):
        - Wakes early if a group reply arrives.
        - Otherwise wakes naturally after the timeout.
        seconds: float, e.g. 10.5
        """
        self._await_reply_seconds = max(0.1, float(seconds))
        self._last_send_time = time.time()
        logger.info(f"[Router] Wakeup delay set: {seconds}s")

    def set_cooldown_duration(self, seconds: float):
        """Change the default cooldown duration."""
        self._cooldown_seconds = max(0, seconds)

    @property
    def in_cooldown(self) -> bool:
        return time.time() < self._cooldown_until

    @property
    def awaiting_reply(self) -> bool:
        """Whether currently waiting for a group reply (within _await_reply_seconds after last send)."""
        if self._last_send_time <= 0:
            return False
        return (time.time() - self._last_send_time) < self._await_reply_seconds

    def clear_await_reply(self):
        """Clear the awaiting-reply flag (call when a reply is received)."""
        self._last_send_time = 0.0

    async def route_group_message(self, msg_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Route a group message.
        Returns a result dict describing the action taken.
        """
        from opensquad.state_manager import get_state_manager
        from opensquad.sleep_controller import get_sleep_controller
        from opensquad.input_hub import get_input_hub
        from opensquad.message_queue import get_message_queue

        state_manager = get_state_manager()
        sleep_controller = get_sleep_controller()
        input_hub = get_input_hub()
        message_queue = get_message_queue()
        
        result = {
            "action": "unknown",
            "queued": False,
            "pushed": False,
            "woke_up": False,
            "reason": ""
        }
        
        # Get current AI state
        ai_state = await state_manager.get_state()
        wake_mode = await state_manager.get_wake_mode()
        
        # Check whether the message @-mentions the AI
        is_mentioned = MessageRouter._check_mention(msg_data)
        sender_name = msg_data.get("sender_name", "Unknown user")
        group_name = msg_data.get("group_name") or msg_data.get("source_name", "Unknown group")
        content = msg_data.get("content", "")[:50]  # first 50 chars
        logger.info(f"[Router] ai_state={ai_state}, wake_mode={wake_mode}, is_mentioned={is_mentioned}, sender={sender_name}")
        
        # 1. In strict mode, non-@ messages are discarded — the agent never sees them.
        #    This applies in ALL states (idle/working/sleeping). Previously a sleeping
        #    exception let non-@ messages through while awaiting a reply, but that
        #    defeated the purpose of strict mode: users observed agents still "seeing"
        #    non-@ chat while in strict. A genuine reply almost always @mentions the
        #    agent back, so strict now filters non-@ unconditionally. If a user needs
        #    every message delivered (including while awaiting reply), use normal mode.
        if wake_mode == "strict" and not is_mentioned:
            result.update({
                "action": "filtered",
                "queued": False,
                "pushed": False,
                "reason": "strict mode, not @-mentioned, message filtered and discarded (applies in all states incl. sleeping)"
            })
            logger.info(f"[Router] FILTERED (strict, not mentioned, state={ai_state}): [{group_name}] {sender_name}: {content}")
            return result
        
        # 2. Non-strict or @-mentioned: put into the message pipeline
        
        queue_msg = QueueMessage(
            id=msg_data.get("id", f"msg_{time.time()}"),
            type="group",
            source_id=msg_data.get("group_id", ""),
            source_name=group_name,
            sender_id=msg_data.get("sender_id", ""),
            sender_name=sender_name,
            content=msg_data.get("content", ""),
            timestamp=msg_data.get("timestamp", time.time()),
            mentions=msg_data.get("mentions", []),
            raw_data=msg_data,
            images=msg_data.get("_image_paths", [])
        )
        
        await message_queue.put(queue_msg)
        result["queued"] = True
        
        # Receiving a group message means someone replied; clear the awaiting flag
        self.clear_await_reply()
        
        # 3. Decide follow-up action based on state.
        # (Messages reaching here: @-mentioned / wake_mode=normal / sleeping-state non-@ allowed through)
        
        if ai_state == "sleeping":
            wake_reason = f"group-message-{sender_name}"
            if is_mentioned:
                wake_reason += "(@you)"
            
            sleep_controller.wake_up(wake_reason)
            
            input_hub.push(
                f"[wakeup-{wake_reason}]",
                source="wake"
            )
            
            result.update({
                "action": "wake_from_sleep",
                "pushed": True,
                "woke_up": True,
                "reason": wake_reason
            })
            logger.info(f"[Router] Wake from sleep: {wake_reason}")
            
        elif ai_state == "working":
            if is_mentioned:
                # @mention during working state: push trigger to ensure the message is not missed
                source_label = f"group:{group_name}" if group_name else "chatpro"
                input_hub.push(
                    "__PROCESS_QUEUE__",
                    source=source_label
                )
                result.update({
                    "action": "queue_mention_working",
                    "pushed": True,
                    "reason": "received @mention while working, pushed trigger to ensure delivery"
                })
                logger.info(f"[Router] @mention during working, pushed trigger: {content}")
            else:
                # normal mode non-@: message already queued; Runner turn loop will consume it
                result.update({
                    "action": "queue_notify",
                    "pushed": False,
                    "reason": "working+normal, message queued pending consumption"
                })
                logger.info(f"[Router] Queued for working AI: {content}")
                
        elif ai_state == "idle":
            if self.in_cooldown and not is_mentioned:
                # In cooldown + not @-mentioned: only enqueue, do not trigger
                remaining = self._cooldown_until - time.time()
                result.update({
                    "action": "queue_cooldown",
                    "pushed": False,
                    "reason": f"in cooldown ({remaining:.0f}s remaining), message queued"
                })
                logger.info(f"[Router] Cooldown active ({remaining:.0f}s left), queued: {content}")
            else:
                # Cooldown expired or @-mentioned: trigger consumption
                if is_mentioned and self.in_cooldown:
                    logger.info(f"[Router] @mention overrides cooldown, triggering immediately")
                
                # Use a meaningful source label (e.g. group:agent-chat-group) to guide the AI's reply
                source_label = f"group:{group_name}" if group_name else "chatpro"
                
                input_hub.push(
                    "__PROCESS_QUEUE__",
                    source=source_label
                )
                result.update({
                    "action": "push_trigger",
                    "pushed": True,
                    "reason": "idle, triggering Runner to consume message queue"
                })
                logger.info(f"[Router] Trigger idle AI to process queue: {content}")
        
        return result
    
    @staticmethod
    def _check_mention(msg_data: Dict) -> bool:
        """Check whether the message @-mentions this agent (exact match against AI username/ID)."""
        # Must read the module attribute dynamically, not via a from...import binding.
        # agents_boot.py replaces bridge_module.bridge with the logged-in instance after startup;
        # only dynamic attribute access picks up the replaced value.
        try:
            import opensquad.bridge as _bridge_mod
            _bridge = _bridge_mod.bridge
            if _bridge is None:
                return False  # Bridge not initialized yet
        except (ImportError, AttributeError):
            return False  # Bridge module not available
        
        mentions = msg_data.get("mentions", [])
        content = msg_data.get("content", "").lower()
        
        # AI identity markers (for exact matching)
        ai_user_id = (_bridge.user_id or "").lower()
        ai_user_name = (_bridge.user_name or "").lower()
        
        # 1. Check whether the mentions list contains the AI's user_id or user_name
        if mentions:
            for m in mentions:
                m_lower = m.lower() if isinstance(m, str) else ""
                if m_lower and (m_lower == ai_user_id or m_lower == ai_user_name):
                    return True
        
        # 2. Fallback: search message text for @{agent_name} (own NAME only).
        # Do NOT text-match the user_id here — numeric/UUID ids are almost
        # never typed as @mentions and substring matching causes false
        # positives (e.g. discussing "user 12345678"). The user_id is already
        # covered by the exact mentions-list check above.
        import re
        names_to_check = set()
        if ai_user_name:
            names_to_check.add(ai_user_name)

        for name in names_to_check:
            # Match @name followed by a non-alphanumeric character or end of string
            pattern = rf"@{re.escape(name)}(?:\b|[\s,\.!\?\-\)\]\}}]|$)"
            if re.search(pattern, content, re.MULTILINE):
                return True

        return False

# Global singleton
message_router = MessageRouter()


# ── AgentContext-aware getter (Phase 1a) ──
def get_message_router(ctx=None):
    """Return message_router from AgentContext if available, else global singleton."""
    if ctx is not None:
        return ctx.message_router
    from opensquad._context import get_current_context
    ctx = get_current_context()
    return ctx.message_router if ctx is not None else message_router
