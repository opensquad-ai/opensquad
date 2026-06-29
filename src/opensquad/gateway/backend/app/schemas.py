"""
Pydantic data model definitions
"""

from datetime import datetime, timezone

from pydantic import BaseModel, Field


# Beijing time timestamp (milliseconds) — DEPRECATED: returns UTC ms to match models.beijing_timestamp().
# Display conversion to local time should happen in the frontend.
def beijing_timestamp() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


class UserBase(BaseModel):
    name: str
    avatar: str | None = None


class UserCreate(UserBase):
    email: str
    password: str
    language: str | None = None  # "zh" | "en" — localizes the default group on first registration


class UserLogin(BaseModel):
    email: str
    password: str
    language: str | None = None  # "zh" | "en" — localizes the welcome message on first login


class UserUpdate(BaseModel):
    name: str | None = None
    avatar: str | None = None
    status: str | None = None


class UserResponse(UserBase):
    id: str
    email: str
    status: str
    created_at: datetime | None = None
    last_seen: datetime | None = None

    class Config:
        from_attributes = True


class AttachmentBase(BaseModel):
    name: str
    size: str
    url: str
    type: str
    duration: int | None = None  # Voice message duration in seconds


class AttachmentCreate(AttachmentBase):
    pass


class AttachmentResponse(AttachmentBase):
    id: str
    message_id: str

    class Config:
        from_attributes = True


class MessageBase(BaseModel):
    content: str
    type: str = "TEXT"


class MessageCreate(MessageBase):
    group_id: str
    reply_to_id: str | None = None
    attachments: list[AttachmentCreate] | None = None
    mentions: list[str] | None = []


class MessageResponse(MessageBase):
    id: str
    sender_id: str
    group_id: str
    timestamp: int | datetime  # Supports millisecond timestamp or datetime
    reply_to_id: str | None = None
    is_pinned: bool = False
    is_edited: bool = False
    is_deleted: bool = False
    can_undo: bool = False
    deleted_at: int | datetime | None = None  # Supports millisecond timestamp or datetime
    mentions: list[str] | None = []
    attachments: list[AttachmentResponse] = []

    class Config:
        from_attributes = True


class GroupBase(BaseModel):
    name: str
    description: str | None = None
    is_private: bool = False


class GroupCreate(GroupBase):
    member_ids: list[str] | None = []


class GroupUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    avatar: str | None = None
    notification_sound_enabled: bool | None = None


class GroupMemberInfo(BaseModel):
    id: str
    name: str
    avatar: str | None
    status: str


class GroupResponse(GroupBase):
    id: str
    avatar: str | None
    members: list[GroupMemberInfo] = []
    pinned_message_id: str | None = None
    unread_count: int = 0
    has_unread_mention: bool = False
    notification_sound_enabled: bool = True
    created_at: datetime
    created_by: str

    class Config:
        from_attributes = True


class GroupListItem(BaseModel):
    id: str
    name: str
    avatar: str | None
    description: str | None
    unread_count: int
    has_unread_mention: bool
    is_private: bool
    notification_sound_enabled: bool
    last_message: dict | None = None
    created_at: str | None = None


class ChatHistoryRequest(BaseModel):
    group_id: str
    before_timestamp: int | None = None
    limit: int = 20


class WebSocketMessage(BaseModel):
    type: str  # message, typing, presence, notification, etc.
    data: dict
    timestamp: int = Field(default_factory=beijing_timestamp)


class TypingIndicator(BaseModel):
    group_id: str
    user_id: str
    user_name: str
    is_typing: bool


class PresenceUpdate(BaseModel):
    user_id: str
    status: str  # online, offline, busy


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse


class SearchQuery(BaseModel):
    text: str | None = None
    user_id: str | None = None
    date_from: str | None = None
    date_to: str | None = None
    group_id: str | None = None


class AIRequest(BaseModel):
    message: str
    context: list[dict] | None = []
