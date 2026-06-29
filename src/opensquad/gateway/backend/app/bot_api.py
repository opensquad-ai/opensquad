"""
Bot/Application API interface
Supports programmatic access to group chat functionality
"""
import json
import os
import uuid
from datetime import datetime, timezone
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query, UploadFile, File, Body
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, desc, func
from sqlalchemy.orm import selectinload

from app.database import get_db

# Define upload directory
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)
from app.models import User, Group, Message, Attachment, UserGroupSettings, group_members, MessageType, DirectMessage
from app.schemas import MessageResponse, AttachmentResponse
from app.api import get_current_user_dep
from app.websocket import manager, notify_new_message

router = APIRouter(prefix="/bot", tags=["Bot API"])


@router.get("/groups", response_model=List[dict])
async def get_bot_groups(
    current_user: User = Depends(get_current_user_dep),
    db: AsyncSession = Depends(get_db)
):
    """
    Get a list of all groups the bot has joined
    """
    # Query all group IDs the user has joined
    membership_result = await db.execute(
        select(group_members.c.group_id).where(group_members.c.user_id == current_user.id)
    )
    group_ids = [row[0] for row in membership_result.all()]

    if not group_ids:
        return []

    # Query group details
    result = await db.execute(
        select(Group).where(Group.id.in_(group_ids))
    )
    groups = result.scalars().all()

    # Query the number of members in each group (single batch query, replaces N+1)
    count_result = await db.execute(
        select(group_members.c.group_id, func.count().label("cnt")).where(
            group_members.c.group_id.in_(group_ids)
        ).group_by(group_members.c.group_id)
    )
    member_counts = {row[0]: row[1] for row in count_result.all()}

    return [{
        "id": g.id,
        "name": g.name,
        "description": g.description,
        "avatar": g.avatar,
        "is_private": g.is_private,
        "created_at": g.created_at.isoformat() if g.created_at else None,
        "member_count": member_counts.get(g.id, 0)
    } for g in groups]


@router.get("/groups/{group_id}/messages", response_model=List[MessageResponse])
async def get_group_messages(
    group_id: str,
    limit: int = Query(50, ge=1, le=100),
    before: Optional[str] = Query(None, description="ISO format timestamp; retrieve messages before this time"),
    current_user: User = Depends(get_current_user_dep),
    db: AsyncSession = Depends(get_db)
):
    """
    Get message history for a specific group
    """
    # Check if the user is in the group
    membership = await db.execute(
        select(group_members).where(
            and_(
                group_members.c.group_id == group_id,
                group_members.c.user_id == current_user.id
            )
        )
    )
    if not membership.first():
        raise HTTPException(status_code=403, detail="Not a member of this group")

    # Build query
    query = select(Message).where(Message.group_id == group_id)

    if before:
        try:
            before_time = datetime.fromisoformat(before)
            query = query.where(Message.timestamp < before_time)
        except (ValueError, TypeError):
            pass

    query = query.order_by(desc(Message.timestamp)).limit(limit)

    result = await db.execute(
        query.options(selectinload(Message.attachments))
    )
    messages = result.scalars().all()

    return [MessageResponse(
        id=m.id,
        sender_id=m.sender_id,
        group_id=m.group_id,
        content=m.content,
        timestamp=m.timestamp,
        type=m.type.value,
        reply_to_id=m.reply_to_id,
        is_pinned=m.is_pinned,
        is_edited=m.is_edited,
        mentions=json.loads(m.mentions) if m.mentions else [],
        attachments=[AttachmentResponse(
            id=a.id,
            message_id=a.message_id,
            name=a.name,
            size=a.size,
            url=a.url,
            type=a.type
        ) for a in m.attachments]
    ) for m in reversed(messages)]


@router.post("/groups/{group_id}/send", response_model=MessageResponse)
async def send_bot_message(
    group_id: str,
    content: str = Body(..., embed=True),
    reply_to_id: Optional[str] = Body(None),
    mentions: Optional[List[str]] = Body(None),
    attachment_urls: Optional[List[str]] = Body(None),
    current_user: User = Depends(get_current_user_dep),
    db: AsyncSession = Depends(get_db)
):
    """
    Send a message to a specific group
    """
    # Check if the user is in the group
    membership = await db.execute(
        select(group_members).where(
            and_(
                group_members.c.group_id == group_id,
                group_members.c.user_id == current_user.id
            )
        )
    )
    if not membership.first():
        raise HTTPException(status_code=403, detail="Not a member of this group")

    # Create message
    message_id = f"msg_{datetime.now().strftime('%Y%m%d%H%M%S')}_{current_user.id[:8]}"

    # Parse @mentions from content
    mentioned_ids = mentions or []
    if not mentioned_ids:
        import re
        mention_pattern = r'@(\w+)'
        mention_names = re.findall(mention_pattern, content)

        for name in mention_names:
            user_result = await db.execute(
                select(User).where(User.name == name)
            )
            user = user_result.scalar_one_or_none()
            if user and user.id != current_user.id:
                mentioned_ids.append(user.id)

    message = Message(
        id=message_id,
        sender_id=current_user.id,
        group_id=group_id,
        content=content,
        timestamp=datetime.now(timezone.utc),
        type=MessageType.TEXT,
        reply_to_id=reply_to_id,
        mentions=json.dumps(mentioned_ids) if mentioned_ids else None
    )

    db.add(message)
    await db.flush()

    # Handle attachments (complete before commit)
    attachment_responses = []
    if attachment_urls:
        for url in attachment_urls:
            filename = os.path.basename(url)
            file_ext = os.path.splitext(filename)[1].lower()
            
            file_type = "file"
            if file_ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.svg']:
                file_type = "image"
            elif file_ext in ['.mp4', '.avi', '.mov', '.wmv', '.flv', '.mkv']:
                file_type = "video"
            elif file_ext in ['.zip', '.rar', '.7z', '.tar', '.gz']:
                file_type = "folder"
            
            file_path = os.path.join(UPLOAD_DIR, os.path.basename(url))
            file_size = "0"
            if os.path.exists(file_path):
                file_size = str(os.path.getsize(file_path))
            
            att_id = f"att_{uuid.uuid4().hex[:8]}"
            attachment = Attachment(
                id=att_id,
                message_id=message_id,
                name=filename,
                size=file_size,
                url=url,
                type=file_type
            )
            db.add(attachment)
            attachment_responses.append(AttachmentResponse(
                id=att_id,
                message_id=message_id,
                name=filename,
                size=file_size,
                url=url,
                type=file_type
            ))

    # Update unread counts
    member_result = await db.execute(
        select(group_members.c.user_id).where(group_members.c.group_id == group_id)
    )
    member_ids = [row[0] for row in member_result.all()]

    for member_id in member_ids:
        if member_id != current_user.id:
            settings_result = await db.execute(
                select(UserGroupSettings).where(
                    and_(
                        UserGroupSettings.user_id == member_id,
                        UserGroupSettings.group_id == group_id
                    )
                )
            )
            settings = settings_result.scalar_one_or_none()

            if settings:
                settings.unread_count += 1
                if member_id in mentioned_ids:
                    settings.has_unread_mention = True

    # Build response before commit
    response_message = MessageResponse(
        id=message.id,
        sender_id=message.sender_id,
        group_id=message.group_id,
        content=message.content,
        timestamp=message.timestamp,
        type=message.type.value,
        reply_to_id=message.reply_to_id,
        is_pinned=message.is_pinned,
        is_edited=message.is_edited,
        mentions=mentioned_ids,
        attachments=attachment_responses
    )

    await db.commit()

    await db.refresh(message, ['attachments'])

    # Update unread count
    member_result = await db.execute(
        select(group_members.c.user_id).where(group_members.c.group_id == group_id)
    )
    member_ids = [row[0] for row in member_result.all()]

    for member_id in member_ids:
        if member_id != current_user.id:
            settings_result = await db.execute(
                select(UserGroupSettings).where(
                    and_(
                        UserGroupSettings.user_id == member_id,
                        UserGroupSettings.group_id == group_id
                    )
                )
            )
            settings = settings_result.scalar_one_or_none()

            if settings:
                settings.unread_count += 1
                if member_id in mentioned_ids:
                    settings.has_unread_mention = True
                await db.commit()

    response_message = MessageResponse(
        id=message.id,
        sender_id=message.sender_id,
        group_id=message.group_id,
        content=message.content,
        timestamp=message.timestamp,
        type=message.type.value,
        reply_to_id=message.reply_to_id,
        is_pinned=message.is_pinned,
        is_edited=message.is_edited,
        mentions=mentioned_ids,
        attachments=[]
    )

    # Build message response dict
    msg_dict = response_message.model_dump(mode='json')
    msg_dict["sender_name"] = current_user.name

    # Broadcast message
    await notify_new_message(group_id, msg_dict, current_user.id)
    return response_message


@router.post("/groups/{group_id}/join")
async def join_group(
    group_id: str,
    current_user: User = Depends(get_current_user_dep),
    db: AsyncSession = Depends(get_db)
):
    """
    Join a public group
    """
    result = await db.execute(select(Group).where(Group.id == group_id))
    group = result.scalar_one_or_none()

    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    if group.is_private:
        raise HTTPException(status_code=403, detail="Cannot join private group via API")

    membership = await db.execute(
        select(group_members).where(
            and_(
                group_members.c.group_id == group_id,
                group_members.c.user_id == current_user.id
            )
        )
    )
    if membership.first():
        raise HTTPException(status_code=400, detail="Already a member of this group")

    await db.execute(
        group_members.insert().values(
            group_id=group_id,
            user_id=current_user.id
        )
    )
    await db.commit()

    settings = UserGroupSettings(
        user_id=current_user.id,
        group_id=group_id,
        unread_count=0,
        has_unread_mention=False,
        notification_enabled=True
    )
    db.add(settings)
    await db.commit()

    return {"message": "Joined group successfully", "group_id": group_id}


@router.get("/mentions", response_model=List[MessageResponse])
async def get_mentions(
    group_id: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=100),
    current_user: User = Depends(get_current_user_dep),
    db: AsyncSession = Depends(get_db)
):
    membership_result = await db.execute(
        select(group_members.c.group_id).where(group_members.c.user_id == current_user.id)
    )
    user_group_ids = [row[0] for row in membership_result.all()]

    if not user_group_ids:
        return []

    query = select(Message).where(
        and_(
            Message.group_id.in_(user_group_ids),
            Message.mentions.isnot(None),
            Message.mentions.like(f'%"{current_user.id}"%')
        )
    )

    if group_id:
        query = query.where(Message.group_id == group_id)

    query = query.order_by(desc(Message.timestamp)).limit(limit)
    result = await db.execute(query.options(selectinload(Message.attachments)))
    messages = result.scalars().all()

    return [MessageResponse(
        id=m.id,
        sender_id=m.sender_id,
        group_id=m.group_id,
        content=m.content,
        timestamp=m.timestamp,
        type=m.type.value,
        reply_to_id=m.reply_to_id,
        is_pinned=m.is_pinned,
        is_edited=m.is_edited,
        mentions=json.loads(m.mentions) if m.mentions else [],
        attachments=[AttachmentResponse(
            id=a.id,
            message_id=a.message_id,
            name=a.name,
            size=a.size,
            url=a.url,
            type=a.type
        ) for a in m.attachments]
    ) for m in messages]


@router.get("/groups/{group_id}/members", response_model=List[dict])
async def get_group_members(
    group_id: str,
    current_user: User = Depends(get_current_user_dep),
    db: AsyncSession = Depends(get_db)
):
    membership = await db.execute(
        select(group_members).where(
            and_(
                group_members.c.group_id == group_id,
                group_members.c.user_id == current_user.id
            )
        )
    )
    if not membership.first():
        raise HTTPException(status_code=403, detail="Not a member of this group")

    member_result = await db.execute(
        select(group_members.c.user_id).where(group_members.c.group_id == group_id)
    )
    member_ids = [row[0] for row in member_result.all()]

    if not member_ids:
        return members

    # Batch query all users at once (replaces N+1)
    user_result = await db.execute(
        select(User).where(User.id.in_(member_ids))
    )
    users_by_id = {u.id: u for u in user_result.scalars().all()}

    for member_id in member_ids:
        user = users_by_id.get(member_id)
        if user:
            members.append({
                "id": user.id,
                "name": user.name,
                "avatar": user.avatar,
                "status": user.status.value if user.status else "offline"
            })
    return members


@router.post("/webhook/register")
async def register_webhook(
    webhook_url: str,
    events: List[str] = Query(["message", "mention"]),
    current_user: User = Depends(get_current_user_dep)
):
    return {
        "message": "Webhook registered successfully",
        "webhook_url": webhook_url,
        "events": events,
        "user_id": current_user.id
    }


@router.get("/groups/{group_id}/search", response_model=List[MessageResponse])
async def search_group_messages(
    group_id: str,
    q: str = Query(...),
    sender_id: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    limit: int = Query(50),
    current_user: User = Depends(get_current_user_dep),
    db: AsyncSession = Depends(get_db)
):
    query = select(Message).where(
        and_(
            Message.group_id == group_id,
            Message.content.ilike(f"%{q}%")
        )
    )
    query = query.order_by(desc(Message.timestamp)).limit(limit)
    result = await db.execute(query.options(selectinload(Message.attachments)))
    messages = result.scalars().all()
    
    return [MessageResponse(
        id=str(m.id),
        sender_id=str(m.sender_id),
        group_id=str(m.group_id),
        content=str(m.content),
        timestamp=m.timestamp,
        type=m.type.value,
        mentions=json.loads(m.mentions) if m.mentions else [],
        attachments=[]
    ) for m in messages]
