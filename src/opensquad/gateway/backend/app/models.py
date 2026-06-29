"""
Database model definitions

Timezone policy: ALL timestamps stored in DB are UTC.
Legacy beijing_now() is kept for backward compatibility but now returns UTC.
Display conversion to local time should happen in the frontend.
"""
from datetime import datetime, timezone, timedelta
from enum import Enum as PyEnum
from typing import List, Optional

from sqlalchemy import Column, String, Integer, Boolean, DateTime, ForeignKey, Text, Enum, Table
from sqlalchemy.orm import relationship, declarative_base
from sqlalchemy.sql import func

Base = declarative_base()

# ---------------------------------------------------------------------------
# Time helpers — ALL storage is UTC (P0: timezone standardization)
# ---------------------------------------------------------------------------

def utc_now() -> datetime:
    """Return timezone-aware UTC datetime for DB defaults."""
    return datetime.now(timezone.utc)


# Backward-compat alias: old code imported beijing_now(); we redirect to UTC
# so the DB stores consistent timestamps regardless of server location.
def beijing_now() -> datetime:
    """DEPRECATED: returns UTC. Use utc_now() for new code."""
    return datetime.now(timezone.utc)


def beijing_timestamp() -> int:
    """DEPRECATED: returns UTC millisecond timestamp."""
    return int(datetime.now(timezone.utc).timestamp() * 1000)

# Association table: many-to-many relationship between users and groups
group_members = Table(
    'group_members',
    Base.metadata,
    Column('user_id', String, ForeignKey('users.id'), primary_key=True),
    Column('group_id', String, ForeignKey('groups.id'), primary_key=True),
    Column('joined_at', DateTime, default=beijing_now)
)

# Association table: message read status
message_read_status = Table(
    'message_read_status',
    Base.metadata,
    Column('message_id', String, ForeignKey('messages.id'), primary_key=True),
    Column('user_id', String, ForeignKey('users.id'), primary_key=True),
    Column('read_at', DateTime, default=beijing_now)
)


class UserStatus(str, PyEnum):
    ONLINE = 'online'
    OFFLINE = 'offline'
    BUSY = 'busy'


class MessageType(str, PyEnum):
    TEXT = 'TEXT'
    IMAGE = 'IMAGE'
    FILE = 'FILE'
    VIDEO = 'VIDEO'
    VOICE = 'VOICE'
    SYSTEM = 'SYSTEM'


class User(Base):
    __tablename__ = 'users'

    id = Column(String, primary_key=True, index=True)
    name = Column(String, nullable=False)
    email = Column(String, unique=True, nullable=False, index=True)
    hashed_password = Column(String, nullable=False)
    avatar = Column(String, nullable=True)
    status = Column(Enum(UserStatus), default=UserStatus.OFFLINE)
    created_at = Column(DateTime, default=beijing_now)
    last_seen = Column(DateTime, default=beijing_now)

    # Relationships
    sent_messages = relationship("Message", back_populates="sender", foreign_keys="Message.sender_id")
    sent_direct_messages = relationship("DirectMessage", back_populates="sender", foreign_keys="DirectMessage.sender_id")
    received_direct_messages = relationship("DirectMessage", back_populates="recipient", foreign_keys="DirectMessage.recipient_id")
    groups = relationship("Group", secondary=group_members, back_populates="members")


class Group(Base):
    __tablename__ = 'groups'

    id = Column(String, primary_key=True, index=True)
    name = Column(String, nullable=False)
    avatar = Column(String, nullable=True)
    description = Column(Text, nullable=True)
    is_private = Column(Boolean, default=False)
    created_by = Column(String, ForeignKey('users.id'), nullable=False)
    created_at = Column(DateTime, default=beijing_now)
    pinned_message_id = Column(String, ForeignKey('messages.id'), nullable=True)
    notification_sound_enabled = Column(Boolean, default=True)

    # Relationships
    members = relationship("User", secondary=group_members, back_populates="groups")
    messages = relationship("Message", back_populates="group", foreign_keys="Message.group_id", order_by="Message.timestamp")
    pinned_message = relationship("Message", foreign_keys=[pinned_message_id], post_update=True)


class Message(Base):
    __tablename__ = 'messages'

    id = Column(String, primary_key=True, index=True)
    sender_id = Column(String, ForeignKey('users.id'), nullable=False)
    group_id = Column(String, ForeignKey('groups.id'), nullable=False, index=True)
    content = Column(Text, nullable=False)
    timestamp = Column(DateTime, default=beijing_now, index=True)
    type = Column(Enum(MessageType), default=MessageType.TEXT)
    reply_to_id = Column(String, ForeignKey('messages.id'), nullable=True)
    is_pinned = Column(Boolean, default=False)
    is_edited = Column(Boolean, default=False)
    mentions = Column(String, nullable=True)  # JSON string storing a list of user IDs
    is_deleted = Column(Boolean, default=False)  # Marked as deleted (retracted)
    deleted_at = Column(DateTime, nullable=True)  # Deletion timestamp
    can_undo = Column(Boolean, default=True)  # Whether retraction can be undone (within 2 minutes)

    # Relationships
    sender = relationship("User", back_populates="sent_messages", foreign_keys=[sender_id])
    group = relationship("Group", back_populates="messages", foreign_keys=[group_id])
    reply_to = relationship("Message", remote_side="Message.id")
    attachments = relationship("Attachment", back_populates="message", cascade="all, delete-orphan")


class Attachment(Base):
    __tablename__ = 'attachments'

    id = Column(String, primary_key=True, index=True)
    message_id = Column(String, ForeignKey('messages.id'), nullable=False)
    name = Column(String, nullable=False)
    size = Column(String, nullable=False)
    url = Column(String, nullable=False)
    type = Column(String, nullable=False)  # image, video, file, folder, voice
    duration = Column(Integer, nullable=True)  # Voice message duration in seconds

    # Relationships
    message = relationship("Message", back_populates="attachments")


class UserGroupSettings(Base):
    """Per-user personalized settings for each group"""
    __tablename__ = 'user_group_settings'

    user_id = Column(String, ForeignKey('users.id'), primary_key=True)
    group_id = Column(String, ForeignKey('groups.id'), primary_key=True)
    unread_count = Column(Integer, default=0)
    has_unread_mention = Column(Boolean, default=False)
    notification_enabled = Column(Boolean, default=True)
    last_read_message_id = Column(String, nullable=True)
    updated_at = Column(DateTime, default=beijing_now, onupdate=beijing_now)


class DirectMessage(Base):
    """Direct message (peer-to-peer) model"""
    __tablename__ = 'direct_messages'

    id = Column(String, primary_key=True, index=True)
    sender_id = Column(String, ForeignKey('users.id'), nullable=False)
    recipient_id = Column(String, ForeignKey('users.id'), nullable=False)
    title = Column(String, nullable=True)  # Optional title
    content = Column(Text, nullable=False)
    timestamp = Column(DateTime, default=beijing_now)
    is_read = Column(Boolean, default=False)
    read_at = Column(DateTime, nullable=True)
    is_deleted_by_sender = Column(Boolean, default=False)  # Deleted by sender
    is_deleted_by_recipient = Column(Boolean, default=False)  # Deleted by recipient
    attachments = Column(String, nullable=True)  # JSON string storing attachment list

    # Relationships
    sender = relationship("User", foreign_keys=[sender_id], back_populates="sent_direct_messages")
    recipient = relationship("User", foreign_keys=[recipient_id], back_populates="received_direct_messages")
