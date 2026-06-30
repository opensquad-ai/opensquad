"""
WebSocket connection management and real-time communication
"""

from fastapi import WebSocket, WebSocketDisconnect
from sqlalchemy import and_, select

from app.database import AsyncSessionLocal
from app.json_utils import make_json_safe
from app.models import Message, User, UserGroupSettings, UserStatus, beijing_now, beijing_timestamp


class ConnectionManager:
    """WebSocket connection manager - supports multiple devices online simultaneously"""

    def __init__(self):
        # user_id -> List[WebSocket] connection list (supports multiple devices)
        self.active_connections: dict[str, list[WebSocket]] = {}
        # connection_id -> user_id reverse lookup
        self.connection_to_user: dict[int, str] = {}
        # group_id -> set of user_ids
        self.group_subscriptions: dict[str, set[str]] = {}
        # user_id -> set of group_ids
        self.user_groups: dict[str, set[str]] = {}

    async def connect(self, websocket: WebSocket, user_id: str):
        """Establish connection - supports multiple devices for the same user"""
        await websocket.accept()

        # Add the connection to the user's connection list
        if user_id not in self.active_connections:
            self.active_connections[user_id] = []
            self.user_groups[user_id] = set()

        # Clean up stale disconnected connections
        active_conns = []
        for conn in self.active_connections[user_id]:
            if hasattr(conn, "client_state") and conn.client_state.name == "CONNECTED":
                active_conns.append(conn)
            else:
                conn_id = id(conn)
                if conn_id in self.connection_to_user:
                    del self.connection_to_user[conn_id]

        self.active_connections[user_id] = active_conns

        self.active_connections[user_id].append(websocket)
        self.connection_to_user[id(websocket)] = user_id

        # Update status to online (regardless of whether this is the first connection)
        conn_count = len(self.active_connections[user_id])
        if conn_count == 1:
            await self.update_user_status(user_id, "online")
            await self.broadcast_presence(user_id, "online")
        else:
            # Only update the database status, do not broadcast (avoid duplicate notifications)
            await self.update_user_status(user_id, "online")

    async def disconnect(self, websocket: WebSocket, user_id: str):
        """Disconnect a specific connection - supports multiple devices"""
        conn_id = id(websocket)

        # Remove from the connection list
        if user_id in self.active_connections:
            if websocket in self.active_connections[user_id]:
                self.active_connections[user_id].remove(websocket)

            # If this is the last connection, clean up user data and broadcast offline
            if not self.active_connections[user_id]:
                del self.active_connections[user_id]

                # Remove from all group subscriptions
                if user_id in self.user_groups:
                    for group_id in self.user_groups[user_id]:
                        if group_id in self.group_subscriptions:
                            self.group_subscriptions[group_id].discard(user_id)
                    del self.user_groups[user_id]

                # Update user status to offline
                await self.update_user_status(user_id, "offline")
                await self.broadcast_presence(user_id, "offline")

        # Remove reverse lookup
        if conn_id in self.connection_to_user:
            del self.connection_to_user[conn_id]

    async def subscribe_to_group(self, user_id: str, group_id: str):
        """Subscribe to group messages"""
        if group_id not in self.group_subscriptions:
            self.group_subscriptions[group_id] = set()
        self.group_subscriptions[group_id].add(user_id)
        self.user_groups[user_id].add(group_id)

    async def unsubscribe_from_group(self, user_id: str, group_id: str):
        """Unsubscribe from group messages"""
        if group_id in self.group_subscriptions:
            self.group_subscriptions[group_id].discard(user_id)
        if user_id in self.user_groups:
            self.user_groups[user_id].discard(group_id)

    async def send_personal_message(self, user_id: str, message: dict):
        """Send a personal message to all devices of a user"""
        if user_id not in self.active_connections:
            return

        # Send the message to all connections of the user
        disconnected = []
        for websocket in self.active_connections[user_id]:
            try:
                # Ensure the message can be JSON serialized
                safe_message = make_json_safe(message)
                await websocket.send_json(safe_message)
            except Exception:
                disconnected.append(websocket)

        # Clean up disconnected connections
        for websocket in disconnected:
            await self.disconnect(websocket, user_id)

    async def broadcast_to_group(self, group_id: str, message: dict, exclude_user: str | None = None):
        """Broadcast a message to a group - sends to all devices of each user"""
        if group_id not in self.group_subscriptions:
            return

        user_ids = list(self.group_subscriptions[group_id])

        for user_id in user_ids:
            if user_id == exclude_user:
                continue

            if user_id in self.active_connections:
                conns = self.active_connections[user_id]
                disconnected = []
                for _i, websocket in enumerate(conns):
                    try:
                        safe_message = make_json_safe(message)
                        await websocket.send_json(safe_message)
                    except Exception:
                        disconnected.append(websocket)

                for websocket in disconnected:
                    await self.disconnect(websocket, user_id)

    async def broadcast_presence(self, user_id: str, status: str):
        """Broadcast a user's status change to all devices of all users"""
        message = {"type": "presence", "data": {"user_id": user_id, "status": status}, "timestamp": beijing_timestamp()}

        # Use list(self.active_connections.items()) to create a copy
        for uid, connections in list(self.active_connections.items()):
            if uid != user_id:
                disconnected = []
                # Use list(connections) to create a copy
                for websocket in list(connections):
                    try:
                        await websocket.send_json(message)
                    except Exception:
                        disconnected.append(websocket)

                # Clean up disconnected connections
                for websocket in disconnected:
                    await self.disconnect(websocket, uid)

    async def broadcast_typing(self, group_id: str, user_id: str, user_name: str, is_typing: bool):
        """Broadcast typing status"""
        message = {
            "type": "typing",
            "data": {"group_id": group_id, "user_id": user_id, "user_name": user_name, "is_typing": is_typing},
            "timestamp": beijing_timestamp(),
        }
        await self.broadcast_to_group(group_id, message, exclude_user=user_id)

    async def update_user_status(self, user_id: str, status: str):
        """Update user status in the database"""
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(User).where(User.id == user_id))
            user = result.scalar_one_or_none()
            if user:
                try:
                    user.status = UserStatus(status)
                    user.last_seen = beijing_now()
                    await db.commit()
                except Exception as e:
                    print(f"[Status] Error updating status: {e}")
                    await db.rollback()

    async def send_to_user(self, user_id: str, message: dict):
        """Send a message to a specific user (supports multiple devices)"""
        if user_id in self.active_connections:
            disconnected = []
            # Use list() to create a copy
            for websocket in list(self.active_connections[user_id]):
                try:
                    await websocket.send_json(message)
                except Exception:
                    disconnected.append(websocket)

            # Clean up disconnected connections
            for websocket in disconnected:
                if websocket in self.active_connections[user_id]:
                    self.active_connections[user_id].remove(websocket)
                conn_id = id(websocket)
                if conn_id in self.connection_to_user:
                    del self.connection_to_user[conn_id]

            # If all connections are gone, clean up the user
            if not self.active_connections[user_id]:
                del self.active_connections[user_id]


# Global connection manager instance
manager = ConnectionManager()


async def handle_websocket(websocket: WebSocket, token: str):
    """Handle a WebSocket connection"""
    from app.auth import decode_token

    # Validate the token
    payload = decode_token(token)
    if not payload or "sub" not in payload:
        await websocket.close(code=4001, reason="Invalid token")
        return

    user_id = payload["sub"]

    # Establish connection
    await manager.connect(websocket, user_id)

    try:
        # Fetch user information
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(User).where(User.id == user_id))
            user = result.scalar_one_or_none()

        if user is None:
            await websocket.close(code=4002, reason="User not found")
            return

        # Send connection success message
        try:
            await websocket.send_json(
                {
                    "type": "connected",
                    "data": {"user_id": user_id, "message": "Connected to chat server"},
                    "timestamp": beijing_timestamp(),
                }
            )
        except Exception:
            return

        # Message processing loop
        while True:
            try:
                data = await websocket.receive_json()
                await handle_message(user_id, data, websocket)
            except WebSocketDisconnect:
                break
            except Exception as e:
                await websocket.send_json(
                    {"type": "error", "data": {"message": str(e)}, "timestamp": beijing_timestamp()}
                )

    except Exception as e:
        print(f"[WebSocket] Error in handle_websocket for user {user_id}: {e}")
        import traceback

        traceback.print_exc()
    finally:
        await manager.disconnect(websocket, user_id)


async def handle_message(user_id: str, data: dict, websocket: WebSocket):
    """Handle a client message"""
    msg_type = data.get("type")
    msg_data = data.get("data", {})

    if msg_type == "subscribe" or msg_type == "join_group":
        # Subscribe to a group (compatible with both "subscribe" and "join_group" types)
        group_id = msg_data.get("group_id")
        if group_id:
            await manager.subscribe_to_group(user_id, group_id)
            await websocket.send_json(
                {
                    "type": "joined" if msg_type == "join_group" else "subscribed",
                    "data": {"group_id": group_id},
                    "timestamp": beijing_timestamp(),
                }
            )

    elif msg_type == "unsubscribe":
        # Unsubscribe from a group
        group_id = msg_data.get("group_id")
        if group_id:
            await manager.unsubscribe_from_group(user_id, group_id)

    elif msg_type == "typing":
        # Typing status
        group_id = msg_data.get("group_id")
        is_typing = msg_data.get("is_typing", False)

        async with AsyncSessionLocal() as db:
            result = await db.execute(select(User).where(User.id == user_id))
            user = result.scalar_one_or_none()
            if user and group_id:
                await manager.broadcast_typing(group_id, user_id, user.name, is_typing)

    elif msg_type == "message":
        # Client sends a message via WebSocket (fallback)
        # Primary message sending should be done via HTTP API
        pass

    elif msg_type == "ping":
        # Heartbeat response
        await websocket.send_json({"type": "pong", "data": {}, "timestamp": beijing_timestamp()})

    elif msg_type == "read":
        # Mark messages as read
        group_id = msg_data.get("group_id")
        message_id = msg_data.get("message_id")
        if group_id:
            await mark_messages_read(user_id, group_id, message_id)


async def mark_messages_read(user_id: str, group_id: str, last_message_id: str | None = None):
    """Mark group messages as read"""
    async with AsyncSessionLocal() as db:
        # Update user group settings
        result = await db.execute(
            select(UserGroupSettings).where(
                and_(UserGroupSettings.user_id == user_id, UserGroupSettings.group_id == group_id)
            )
        )
        settings = result.scalar_one_or_none()

        if settings:
            settings.unread_count = 0
            settings.has_unread_mention = False
            if last_message_id:
                settings.last_read_message_id = last_message_id
            await db.commit()


async def notify_new_message(group_id: str, message: dict, sender_id: str):
    """Notify the group of a new message - auto-inject sender name and embed quoted message summary"""
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from app.database import AsyncSessionLocal
    from app.models import User

    # Fetch sender name
    sender_name = "Unknown"
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).where(User.id == sender_id))
        user = result.scalar_one_or_none()
        if user:
            sender_name = user.name

    # Inject name into the data packet
    message["sender_name"] = sender_name

    # If the message has a reply, fetch the quoted message and embed a summary for agent and frontend use
    reply_to_id = message.get("reply_to_id")
    if reply_to_id:
        try:
            async with AsyncSessionLocal() as db:
                r = await db.execute(
                    select(Message).where(Message.id == reply_to_id).options(selectinload(Message.attachments))
                )
                reply_msg = r.scalar_one_or_none()
                if reply_msg:
                    # Fetch the quoted message sender's name
                    sr = await db.execute(select(User).where(User.id == reply_msg.sender_id))
                    reply_sender = sr.scalar_one_or_none()
                    # Build attachment summary list
                    att_list = [{"name": a.name, "type": a.type, "url": a.url} for a in (reply_msg.attachments or [])]
                    message["reply_to_message"] = {
                        "id": reply_msg.id,
                        "sender_name": reply_sender.name if reply_sender else "Unknown",
                        "type": reply_msg.type.value if reply_msg.type else "TEXT",
                        "content": reply_msg.content or "",
                        "attachments": att_list,
                        "is_deleted": bool(reply_msg.is_deleted),
                    }
        except Exception as _e:
            pass  # Failure to fetch the quoted summary does not affect the main flow

    await manager.broadcast_to_group(
        group_id, {"type": "new_message", "data": message, "timestamp": beijing_timestamp()}
    )


async def notify_message_update(group_id: str, message: dict):
    """Notify of a message update (edit, delete, pin)"""
    await manager.broadcast_to_group(
        group_id, {"type": "message_updated", "data": message, "timestamp": beijing_timestamp()}
    )


async def notify_unread_update(user_id: str, group_id: str, unread_count: int, has_mention: bool):
    """Notify a user of unread message updates"""
    await manager.send_personal_message(
        user_id,
        {
            "type": "unread_update",
            "data": {"group_id": group_id, "unread_count": unread_count, "has_unread_mention": has_mention},
            "timestamp": beijing_timestamp(),
        },
    )
