"""
API route definitions
"""

import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Body, Depends, File, Header, HTTPException, Query, Request, UploadFile, status
from sqlalchemy import and_, asc, desc, func, inspect, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models import (
    Attachment,
    DirectMessage,
    Group,
    Message,
    MessageType,
    User,
    UserGroupSettings,
    UserStatus,
    beijing_now,
    group_members,
)

_log = logging.getLogger("app.api")


def _utc_aware(dt: datetime) -> datetime:
    """Per models.py policy all DB timestamps are UTC, but the DateTime columns
    are timezone-naive so reads come back as naive datetimes. Promote them to
    UTC-aware for arithmetic with beijing_now() (which returns UTC-aware)."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


import contextlib

from app.auth import (
    authenticate_user,
    create_access_token,
    create_user,
    get_password_hash,
    get_user_by_email,
    get_user_by_id,
)
from app.schemas import (
    AttachmentResponse,
    GroupCreate,
    GroupListItem,
    GroupMemberInfo,
    GroupResponse,
    GroupUpdate,
    MessageCreate,
    MessageResponse,
    SearchQuery,
    Token,
    UserCreate,
    UserLogin,
    UserResponse,
    UserUpdate,
)
from app.websocket import manager, notify_message_update, notify_new_message, notify_unread_update

# Define upload directory (avoid circular imports)
# Use syscfg.workspace_uploads_dir() so the path is consistent across
# main.py, api.py, and ai_web/routes/_main.py — and so PyInstaller mode
# (OPENSQUAD_USER_DATA env) is handled in one place. See issue #43.
from opensquad.system_config import syscfg

UPLOAD_DIR = syscfg.workspace_uploads_dir()
os.makedirs(UPLOAD_DIR, exist_ok=True)

router = APIRouter()


def _is_agent_email(email: str | None) -> bool:
    return bool(email) and str(email).endswith("@ai")


def _ensure_agent_user_avatar(user: User) -> str:
    """Backfill empty/Dicebear agent avatars with a stable local robot SVG.

    Persists onto the User row so subsequent group/member payloads stay consistent.
    """
    from opensquad.avatar_utils import ensure_agent_avatar, is_external_dicebear

    current = user.avatar or ""
    if current and not is_external_dicebear(current):
        return current
    if not _is_agent_email(getattr(user, "email", None)):
        return current
    resolved = ensure_agent_avatar(current, str(user.id or user.name or "agent"))
    if resolved != current:
        user.avatar = resolved
    return resolved


_EMAIL_AGENT_ID_CACHE: dict[str, str] = {}
_EMAIL_AGENT_ID_CACHE_TS: float = 0.0


async def _build_email_agent_id_map() -> dict[str, str]:
    """Build chat_email -> agent_id map from launcher /api/agents. Cached 60s.

    Seed agent comm accounts have a numeric user.id that differs from the
    agent's config ``agent_id`` (e.g. user.id="838168" vs agent_id="pm-001"),
    so we resolve the real agent_id via the agent's group_chat email.
    """
    global _EMAIL_AGENT_ID_CACHE, _EMAIL_AGENT_ID_CACHE_TS
    now = time.time()
    if _EMAIL_AGENT_ID_CACHE and (now - _EMAIL_AGENT_ID_CACHE_TS) < 60:
        return _EMAIL_AGENT_ID_CACHE
    from app.ai_web.routes import _proxy_get

    try:
        data = await _proxy_get("/api/agents")
    except Exception:
        data = {"agents": []}
    mapping: dict[str, str] = {}
    for agent in data.get("agents", []):
        agent_id = str(agent.get("agent_id", "") or agent.get("dir_name", "") or "")
        cfg = agent.get("config", {}) or {}
        email = (cfg.get("group_chat", {}) or {}).get("email", "") or ""
        if agent_id and email:
            mapping[email] = agent_id
    _EMAIL_AGENT_ID_CACHE = mapping
    _EMAIL_AGENT_ID_CACHE_TS = now
    return mapping


def _member_info(user: User, status: str | None = None, agent_id: str | None = None) -> GroupMemberInfo:
    return GroupMemberInfo(
        id=user.id,
        name=user.name,
        avatar=_ensure_agent_user_avatar(user),
        status=status if status is not None else user.status.value,
        is_agent=_is_agent_email(getattr(user, "email", None)),
        agent_id=agent_id,
    )


def _sync_agent_name_to_config(user_id: str, new_name: str) -> None:
    """
    When a user's display name is updated, sync it to the corresponding agent's config.json (if a binding exists).
    agent_id == user_id indicates the binding relationship.
    """
    try:
        agents_dir = syscfg.workspace_agents_dir()
        if not os.path.isdir(agents_dir):
            return
        for dir_name in os.listdir(agents_dir):
            config_path = os.path.join(agents_dir, dir_name, "config.json")
            if not os.path.isfile(config_path):
                continue
            try:
                with open(config_path, encoding="utf-8") as f:
                    cfg = json.load(f)
                if str(cfg.get("agent_id", "")) == str(user_id):
                    if cfg.get("agent_name") != new_name:
                        cfg["agent_name"] = new_name
                        with open(config_path, "w", encoding="utf-8") as f:
                            json.dump(cfg, f, ensure_ascii=False, indent=2)
                    break
            except Exception:
                continue
    except Exception:
        pass  # Sync failure does not affect main flow


# ========== Authentication ==========


async def get_current_user_dep(
    token: str = Query(None), authorization: str = Header(None), db: AsyncSession = Depends(get_db)
):
    """Dependency function to retrieve the current user via query parameter or Authorization header"""
    from app.auth import decode_token

    # Prefer token from Header; fall back to query parameter
    actual_token = token
    if authorization and authorization.startswith("Bearer "):
        actual_token = authorization[7:]  # Strip "Bearer " prefix

    if not actual_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token required")

    payload = decode_token(actual_token)
    if not payload or "sub" not in payload:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    user = await get_user_by_id(db, payload["sub"])
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return user


@router.post("/auth/register", response_model=Token)
async def register(user_data: UserCreate, request: Request, db: AsyncSession = Depends(get_db)):
    """User registration.

    Web flow (no ``X-Node-Secret`` header): the very first web user is
    allowed; subsequent web registrations are rejected with 403 because the
    system intentionally only supports a single web account per deployment.

    Internal flow (valid ``X-Node-Secret`` header): always allowed, used by
    trusted internal tools (e.g. the ``chat_account`` agent plugin). These
    internal accounts do NOT count toward the "web user" threshold.
    """
    from opensquad.system_config import syscfg as _syscfg

    expected_node_secret = _syscfg.node_secret()
    header_secret = request.headers.get("X-Node-Secret", "")
    internal_call = bool(expected_node_secret) and (header_secret == expected_node_secret)

    # For web calls (no internal auth), enforce first-user-only.
    if not internal_call:
        email_l = str(user_data.email or "").strip().lower()
        if email_l.endswith("@ai"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Please use a personal email for the web account (@ai is reserved for agents).",
            )
        web_user_exists = await _has_web_user(db)
        if web_user_exists:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Registration closed: this system already has a web account. Only the registered account can sign in.",
            )

    # Standard uniqueness checks + creation.
    existing_user = await get_user_by_email(db, user_data.email)
    if existing_user:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email already registered")

    user = await create_user(db, user_data)

    # First-registration bootstrap: create the default collaboration group
    # (and pinned welcome message) using the language the user just chose
    # in the wizard. Without this, the web-registration flow would land the
    # user in the empty main view because /auth/login is never explicitly
    # called after the wizard's auto-login.
    try:
        await _bootstrap_default_group(db, user, user_data.language)
    except Exception as e:
        _log.warning("[register] Default group bootstrap failed: %s", e)

    access_token = create_access_token(data={"sub": user.id})
    return Token(access_token=access_token, user=UserResponse.model_validate(user))


async def _has_web_user(db: AsyncSession) -> bool:
    """Return True if any non-agent (web) user exists in the database.

    Agent comm accounts use the `*@ai` email convention; any other user
    is treated as a web user. This is the threshold for closing web
    registration to a single account.
    """
    result = await db.execute(select(User).where(~User.email.endswith("@ai")).limit(1))
    return result.scalar_one_or_none() is not None


@router.get("/auth/registration-status")
async def registration_status(db: AsyncSession = Depends(get_db)):
    """First-launch wizard: tell the web UI whether registration is still open.

    - ``registration_required`` is True when no web user exists yet → the
      wizard should render the registration form.
    - When False, the wizard renders the login form (sign-up is closed).
    - ``language`` is the most-recently-observed login language (best-effort
      default). The web UI already persists its own language choice, so this
      field is informational.
    """
    from app.models import User as _User

    web_user_exists = await _has_web_user(db)

    # Best-effort: surface a default language hint for the wizard. Falls back
    # to 'zh' if we have no signal. The web UI overrides this on init.
    lang = "zh"
    result = await db.execute(select(_User).order_by(_User.created_at.desc()).limit(1))
    last_user = result.scalar_one_or_none()
    if last_user is not None and getattr(last_user, "last_seen", None) is not None:
        # No persisted language on User; keep zh as a neutral default.
        lang = "zh"

    return {
        "registration_required": not web_user_exists,
        "language": lang,
    }


@router.post("/auth/login", response_model=Token)
async def login(login_data: UserLogin, db: AsyncSession = Depends(get_db)):
    """User login"""
    user = await authenticate_user(db, login_data.email, login_data.password)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect email or password")

    # Update user status to online
    user.status = UserStatus.ONLINE
    user.last_seen = beijing_now()
    await db.commit()

    # First-login bootstrap: create the default collaboration group with a
    # localized welcome message. The language is chosen on the login screen
    # and passed here; we can only localize once we know it (not at startup).
    try:
        await _bootstrap_default_group(db, user, login_data.language)
    except Exception as e:
        _log.warning("[login] Default group bootstrap failed: %s", e)

    # Create access token
    access_token = create_access_token(data={"sub": user.id})

    return Token(access_token=access_token, user=UserResponse.model_validate(user))


# ── First-login default group + localized welcome ──────────────────────────

_DEFAULT_GROUP_ID = "g-default"

_GROUP_NAMES = {
    "zh": "开发协作组",
    "en": "Development Squad",
}

_WELCOME_MESSAGES = {
    "zh": (
        "👋 欢迎来到 OpenSquad 开发协作组！\n\n"
        "【这是什么】\n"
        "OpenSquad 是一个多 agent 协作平台。@pm 描述任务，它会拆解并分派给 coder（写代码）与 qa（审查测试）协作完成。\n\n"
        "【团队】\n"
        "• pm — 项目经理：拆解需求、派发任务、协调进度\n"
        "• coder — 程序员：实现代码、调试、写测试\n"
        "• qa — 质量保证：代码审查、测试、验收把关\n\n"
        "它们现在是离线状态。开始协作只需三步：\n\n"
        "【第一步：配置模型卡】\n"
        "点击左侧导航栏的「模型卡」图标，填入你的 API Key。OpenSquad 支持多种大模型 API（DeepSeek、OpenAI、Claude、智谱、通义、本地模型等），按需选择。\n\n"
        "【第二步：分配并启动 agent】\n"
        "保存模型卡后，直接在模型卡配置下方给 pm / coder / qa 分配该模型卡，然后在「Agent」面板启动这三个 agent。\n\n"
        "【第三步：在群里派发任务】\n"
        "稍等几秒 agent 启动后，@pm 描述你要完成的任务，pm 会拆解并分派给 coder 与 qa 协作完成。\n\n"
        "【能做什么】\n"
        "• 编程实现与调试\n"
        "• 代码审查与重构\n"
        "• 功能测试与验收\n"
        "• 技术方案讨论\n"
        "• 自定义工作流：你还可以通过「角色 & 协作卡」部分自定义任何工作流，组建自己的 agent 团队。\n"
    ),
    "en": (
        "👋 Welcome to the OpenSquad Development Squad!\n\n"
        "[What is this]\n"
        "OpenSquad is a multi-agent collaboration platform. @pm with a task and it will break it down and delegate to coder (implementation) and qa (review/testing).\n\n"
        "[Team]\n"
        "• pm — Project Manager: breaks down requirements, delegates tasks, coordinates progress\n"
        "• coder — Coder: implements code, debugs, writes tests\n"
        "• qa — Quality Assurance: code review, testing, acceptance\n\n"
        "They are currently offline. Get started in three steps:\n\n"
        "[Step 1: Configure a model card]\n"
        "Click the 'Model Cards' icon in the left sidebar and fill in your API Key. OpenSquad supports many LLM APIs (DeepSeek, OpenAI, Claude, Zhipu, Qwen, local models, etc.) — pick what fits.\n\n"
        "[Step 2: Assign and start agents]\n"
        "After saving the model card, assign it to pm / coder / qa directly below the card's config, then open the 'Agent' panel to start these three agents.\n\n"
        "[Step 3: Dispatch a task in the group]\n"
        "Once the agents are up (a few seconds), @pm describe the task you want done; pm will break it down and delegate to coder and qa.\n\n"
        "[What you can do]\n"
        "• Code implementation & debugging\n"
        "• Code review & refactoring\n"
        "• Feature testing & acceptance\n"
        "• Technical design discussions\n"
        "• Custom workflows: you can also customize any workflow and build your own agent team via the 'Roles & Collaboration Cards' section.\n"
    ),
}


def _write_back_group_to_agents(group_id: str) -> None:
    """Append the default group id to each seed agent's group_chat.groups."""
    candidates = []
    try:
        from opensquad.system_config import syscfg

        candidates.append(syscfg.workspace_agents_dir())
    except Exception:
        pass
    _here = os.path.dirname(os.path.abspath(__file__))
    _src = os.path.dirname(os.path.dirname(os.path.dirname(_here)))
    candidates.append(os.path.join(_src, "agents"))

    for agents_dir in candidates:
        if not os.path.isdir(agents_dir):
            continue
        wrote = False
        for name in ("pm", "coder", "qa"):
            cfg_path = os.path.join(agents_dir, name, "config.json")
            if not os.path.isfile(cfg_path):
                continue
            try:
                with open(cfg_path, encoding="utf-8") as f:
                    cfg = json.load(f)
                gc = cfg.setdefault("group_chat", {})
                groups = gc.get("groups") or []
                if group_id not in groups:
                    groups.append(group_id)
                    gc["groups"] = groups
                    with open(cfg_path, "w", encoding="utf-8") as f:
                        json.dump(cfg, f, ensure_ascii=False, indent=2)
                wrote = True
            except (OSError, ValueError):
                continue
        if wrote:
            break


async def _bootstrap_default_group(db: AsyncSession, admin_user: User, language: str | None) -> None:
    """Create the default collaboration group on first login.

    Idempotent: if the group already exists, does nothing. The group name
    and pinned welcome message are localized to the login-selected language.
    """
    # Already bootstrapped?
    existing = await db.execute(select(Group).where(Group.id == _DEFAULT_GROUP_ID))
    if existing.scalar_one_or_none():
        return

    lang = "zh" if (not language or language.lower().startswith("zh")) else "en"
    group_name = _GROUP_NAMES.get(lang, _GROUP_NAMES["zh"])
    welcome_text = _WELCOME_MESSAGES.get(lang, _WELCOME_MESSAGES["zh"])

    now = datetime.now(timezone.utc)

    # Gather all users: admin + any agent comm accounts (email pattern *@ai).
    all_users = (await db.execute(select(User))).scalars().all()
    agent_users = [u for u in all_users if u.email and u.email.endswith("@ai")]
    member_ids = [admin_user.id] + [u.id for u in agent_users]

    group = Group(
        id=_DEFAULT_GROUP_ID,
        name=group_name,
        description="OpenSquad default collaboration group" if lang == "en" else "OpenSquad 默认协作群",
        avatar="",
        is_private=False,
        created_by=admin_user.id,
        created_at=now,
        notification_sound_enabled=True,
    )
    db.add(group)
    await db.flush()

    # Add all members.
    for member_id in member_ids:
        await db.execute(group_members.insert().values(user_id=member_id, group_id=group.id))
        db.add(
            UserGroupSettings(
                user_id=member_id,
                group_id=group.id,
                unread_count=0,
                has_unread_mention=False,
                notification_enabled=True,
            )
        )

    # Pinned welcome message.
    welcome_msg = Message(
        id="m-welcome",
        group_id=group.id,
        sender_id=admin_user.id,
        content=welcome_text,
        type=MessageType.SYSTEM,
        is_pinned=True,
        timestamp=now,
    )
    db.add(welcome_msg)
    group.pinned_message_id = welcome_msg.id

    await db.commit()

    # Write group id back to seed agent configs so their bridge auto-joins.
    _write_back_group_to_agents(group.id)

    _log.info("[login] Bootstrapped default group '%s' (lang=%s, members=%d)", group_name, lang, len(member_ids))


@router.get("/auth/me", response_model=UserResponse)
async def get_current_user_info(current_user: User = Depends(get_current_user_dep)):
    """Get current user info"""
    return UserResponse.model_validate(current_user)


@router.post("/auth/reset-password")
async def reset_password(
    reset_data: dict,
    db: AsyncSession = Depends(get_db),
    authorization: str = Header(None),
):
    """
    Reset password for an existing user by email.
    Used by agents during auto-login when the account exists but password doesn't match.
    Accepts: {"email": "...", "new_password": "...", "node_secret": "..."}

    Authentication (one of):
      - JWT token via Authorization header (for web UI users)
      - node_secret in request body (for agent auto-registration)
    """
    email = reset_data.get("email", "")
    new_password = reset_data.get("new_password", "")
    node_secret = reset_data.get("node_secret", "")
    if not email or not new_password:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="email and new_password are required")

    # Verify auth: JWT token or node_secret
    from opensquad.system_config import syscfg

    expected_node_secret = syscfg.node_secret()
    token_valid = False
    if authorization and authorization.startswith("Bearer "):
        from app.auth import decode_token

        payload = decode_token(authorization[7:])
        if payload and "sub" in payload:
            token_valid = True
    # SEC-11a: an unset node_secret must NOT short-circuit auth (previously
    # "not expected_node_secret" let anyone reset arbitrary passwords). Compare
    # in constant time so a timing side channel cannot leak the secret.
    if not expected_node_secret:
        node_secret_valid = False
    else:
        import hmac

        node_secret_valid = hmac.compare_digest(node_secret or "", expected_node_secret)

    if not token_valid and not node_secret_valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated: valid token or node_secret required"
        )

    user = await get_user_by_email(db, email)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    user.hashed_password = get_password_hash(new_password)
    await db.commit()
    _log.info(f"[Auth] Password reset for {email}")
    return {"status": "ok"}


# ========== Users ==========


@router.get("/users/{user_id}", response_model=UserResponse)
async def get_user(
    user_id: str, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user_dep)
):
    """Get user info"""
    user = await get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return UserResponse.model_validate(user)


@router.put("/users/me", response_model=UserResponse)
async def update_user(
    user_update: UserUpdate, current_user: User = Depends(get_current_user_dep), db: AsyncSession = Depends(get_db)
):
    """Update current user info; if the user is a bound account for an Agent, also sync agent_name"""
    if user_update.name:
        current_user.name = user_update.name
    if user_update.avatar:
        current_user.avatar = user_update.avatar
    if user_update.status:
        current_user.status = UserStatus(user_update.status)

    await db.commit()

    # If name changed, sync to the corresponding agent's config.json
    if user_update.name:
        _sync_agent_name_to_config(current_user.id, user_update.name)
    # After commit in async context, the object expires; no refresh needed — validate from in-memory object directly
    return UserResponse.model_validate(current_user)


@router.get("/users/search", response_model=list[UserResponse])
async def search_users(
    query: str = Query(..., min_length=1),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dep),
):
    """Search users"""
    result = await db.execute(
        select(User).where(or_(User.name.ilike(f"%{query}%"), User.email.ilike(f"%{query}%"))).limit(20)
    )
    users = result.scalars().all()
    return [UserResponse.model_validate(u) for u in users]


# ========== Groups ==========


@router.get("/groups", response_model=list[GroupListItem])
async def get_user_groups(current_user: User = Depends(get_current_user_dep), db: AsyncSession = Depends(get_db)):
    """Get all groups for the current user (optimized: batch queries)"""
    # 1. Get all groups the user belongs to (select full Group objects to ensure fields like created_at are accessible)
    result = await db.execute(select(Group).join(group_members).where(group_members.c.user_id == current_user.id))
    groups_data = result.scalars().all()

    if not groups_data:
        return []

    group_ids = [g.id for g in groups_data]

    # 2. Batch query user settings for all groups (1 query)
    settings_result = await db.execute(
        select(UserGroupSettings).where(
            and_(UserGroupSettings.user_id == current_user.id, UserGroupSettings.group_id.in_(group_ids))
        )
    )
    settings_map = {s.group_id: s for s in settings_result.scalars().all()}

    # 3. Batch query the last message per group (1 query + window function)
    # Use a subquery to find the latest message ID for each group
    subq = (
        select(Message.group_id, func.max(Message.timestamp).label("max_ts"))
        .where(Message.group_id.in_(group_ids))
        .group_by(Message.group_id)
        .subquery()
    )

    last_msgs_result = await db.execute(
        select(Message).join(subq, and_(Message.group_id == subq.c.group_id, Message.timestamp == subq.c.max_ts))
    )
    last_msgs_map = {msg.group_id: msg for msg in last_msgs_result.scalars().all()}

    # 4. Assemble response data
    group_list = []
    for group in groups_data:
        settings = settings_map.get(group.id)
        unread_count = settings.unread_count if settings else 0
        has_unread_mention = settings.has_unread_mention if settings else False
        notification_enabled = settings.notification_enabled if settings else True

        last_msg = last_msgs_map.get(group.id)
        last_message_data = None
        if last_msg:
            last_message_data = {
                "id": last_msg.id,
                "content": last_msg.content[:100] if last_msg.type == MessageType.TEXT else f"[{last_msg.type}]",
                "timestamp": last_msg.timestamp.isoformat(),
                "sender_id": last_msg.sender_id,
            }

        group_list.append(
            GroupListItem(
                id=group.id,
                name=group.name,
                avatar=group.avatar,
                description=group.description,
                unread_count=unread_count,
                has_unread_mention=has_unread_mention,
                is_private=group.is_private,
                notification_sound_enabled=notification_enabled,
                last_message=last_message_data,
                created_at=group.created_at.isoformat() if group.created_at else None,
            )
        )

    # Sort descending by last message time; groups with no messages (e.g. just created) use created_at as fallback and appear at top
    group_list.sort(key=lambda x: x.last_message["timestamp"] if x.last_message else (x.created_at or ""), reverse=True)

    return group_list


@router.post("/groups", response_model=GroupResponse)
async def create_group(
    group_data: GroupCreate, current_user: User = Depends(get_current_user_dep), db: AsyncSession = Depends(get_db)
):
    """Create a new group"""
    _log.info(
        "[CREATE_GROUP] START: user_id=%s, name=%r, is_private=%s, member_ids=%s",
        current_user.id,
        group_data.name,
        group_data.is_private,
        group_data.member_ids,
    )

    # Generate a 6-character compact ID
    import random
    import string

    group_id = "g" + "".join(random.choices(string.ascii_lowercase + string.digits, k=5))
    _log.info("[CREATE_GROUP] generated group_id=%s", group_id)

    new_group = Group(
        id=group_id,
        name=group_data.name,
        description=group_data.description or "New Group",
        is_private=group_data.is_private,
        created_by=current_user.id,
        created_at=datetime.now(timezone.utc),
        avatar="",  # frontend local SVG fallback (no Dicebear CDN)
        notification_sound_enabled=True,
    )

    db.add(new_group)
    _log.info("[CREATE_GROUP] db.add(new_group) done, calling db.flush()")
    try:
        await db.flush()
        _log.info("[CREATE_GROUP] db.flush() OK")
    except Exception as e:
        _log.error("[CREATE_GROUP] db.flush() FAILED: %s: %s", type(e).__name__, e)
        raise

    # Add creator as member
    _log.info("[CREATE_GROUP] inserting creator as member: user_id=%s", current_user.id)
    await db.execute(group_members.insert().values(user_id=current_user.id, group_id=group_id))

    # Add other members (deduplicate with set)
    for member_id in set(group_data.member_ids or []):
        if member_id != current_user.id:
            _log.info("[CREATE_GROUP] inserting member: user_id=%s", member_id)
            await db.execute(group_members.insert().values(user_id=member_id, group_id=group_id))

    # Create user group settings
    settings = UserGroupSettings(
        user_id=current_user.id, group_id=group_id, unread_count=0, has_unread_mention=False, notification_enabled=True
    )
    db.add(settings)
    _log.info("[CREATE_GROUP] UserGroupSettings added")

    # Build the response before commit to ensure object data is available.
    # At this point new_group.members may be empty or not fully loaded; to be safe, manually build the member list.
    # On creation, members are just created_by plus member_ids.

    # Fetch creator info
    from app.auth import get_user_by_id

    creator = await get_user_by_id(db, current_user.id)
    _log.info("[CREATE_GROUP] creator fetched: %s", creator.id if creator else None)
    members_info = [_member_info(creator)]

    # Fetch info for other members
    if group_data.member_ids:
        other_members_res = await db.execute(select(User).where(User.id.in_(list(set(group_data.member_ids)))))
        for m in other_members_res.scalars():
            if m.id != creator.id:
                members_info.append(_member_info(m))

    response = GroupResponse(
        id=new_group.id,
        name=new_group.name,
        avatar=new_group.avatar,
        description=new_group.description,
        is_private=new_group.is_private,
        members=members_info,
        created_by=new_group.created_by,
        created_at=new_group.created_at,
        notification_sound_enabled=True,
    )

    _log.info("[CREATE_GROUP] calling db.commit()")
    try:
        await db.commit()
        _log.info("[CREATE_GROUP] db.commit() OK, returning group_id=%s", group_id)
    except Exception as e:
        _log.error("[CREATE_GROUP] db.commit() FAILED: %s: %s", type(e).__name__, e)
        raise
    return response


@router.get("/groups/{group_id}", response_model=GroupResponse)
async def get_group(
    group_id: str, current_user: User = Depends(get_current_user_dep), db: AsyncSession = Depends(get_db)
):
    """Get group details"""
    result = await db.execute(select(Group).where(Group.id == group_id).options(selectinload(Group.members)))
    group = result.scalar_one_or_none()

    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    member_ids = [m.id for m in group.members]
    if current_user.id not in member_ids:
        raise HTTPException(status_code=403, detail="Not a member of this group")

    settings_result = await db.execute(
        select(UserGroupSettings).where(
            and_(UserGroupSettings.user_id == current_user.id, UserGroupSettings.group_id == group_id)
        )
    )
    settings = settings_result.scalar_one_or_none()

    email_agent_id_map = await _build_email_agent_id_map()
    member_statuses = {}
    members_info = []
    avatar_dirty = False
    for member in group.members:
        # Members already loaded via selectinload; no need to refresh again
        member_statuses[member.id] = member.status.value
        before = member.avatar or ""
        member_agent_id = None
        if _is_agent_email(getattr(member, "email", None)):
            member_agent_id = email_agent_id_map.get(member.email or "")
        info = _member_info(member, member_statuses[member.id], agent_id=member_agent_id)
        if (member.avatar or "") != before:
            avatar_dirty = True
        members_info.append(info)

    if avatar_dirty:
        await db.commit()

    return GroupResponse(
        id=group.id,
        name=group.name,
        avatar=group.avatar,
        description=group.description,
        is_private=group.is_private,
        members=members_info,
        pinned_message_id=group.pinned_message_id,
        unread_count=settings.unread_count if settings else 0,
        has_unread_mention=settings.has_unread_mention if settings else False,
        notification_sound_enabled=settings.notification_enabled if settings else True,
        created_by=group.created_by,
        created_at=group.created_at,
    )


@router.put("/groups/{group_id}", response_model=GroupResponse)
async def update_group(
    group_id: str,
    group_update: GroupUpdate,
    current_user: User = Depends(get_current_user_dep),
    db: AsyncSession = Depends(get_db),
):
    """Update group info"""
    result = await db.execute(select(Group).where(Group.id == group_id))
    group = result.scalar_one_or_none()

    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    if group_update.name:
        group.name = group_update.name
    if group_update.description is not None:
        group.description = group_update.description
    if group_update.avatar:
        group.avatar = group_update.avatar
    if group_update.notification_sound_enabled is not None:
        group.notification_sound_enabled = group_update.notification_sound_enabled

        settings_result = await db.execute(
            select(UserGroupSettings).where(
                and_(UserGroupSettings.user_id == current_user.id, UserGroupSettings.group_id == group_id)
            )
        )
        settings = settings_result.scalar_one_or_none()
        if settings:
            settings.notification_enabled = group_update.notification_sound_enabled

    # Build the response before commit to avoid object expiry after commit.
    # Manually fetch the current user's settings.
    settings_result = await db.execute(
        select(UserGroupSettings).where(
            and_(UserGroupSettings.user_id == current_user.id, UserGroupSettings.group_id == group_id)
        )
    )
    settings = settings_result.scalar_one_or_none()

    # Note: group.members should already be loaded via selectinload when entering this function.
    # For safety, manually query members here.
    members_res = await db.execute(select(User).join(group_members).where(group_members.c.group_id == group_id))
    members_list = members_res.scalars().all()
    email_agent_id_map = await _build_email_agent_id_map()

    response = GroupResponse(
        id=group.id,
        name=group.name,
        avatar=group.avatar,
        description=group.description,
        is_private=group.is_private,
        members=[
            _member_info(
                m,
                agent_id=(
                    email_agent_id_map.get(m.email or "") if _is_agent_email(getattr(m, "email", None)) else None
                ),
            )
            for m in members_list
        ],
        created_by=group.created_by,
        created_at=group.created_at,
        notification_sound_enabled=group.notification_sound_enabled,
    )

    await db.commit()
    return response


@router.post("/groups/{group_id}/join")
async def join_group(
    group_id: str, current_user: User = Depends(get_current_user_dep), db: AsyncSession = Depends(get_db)
):
    """Join a group"""
    result = await db.execute(select(Group).where(Group.id == group_id))
    group = result.scalar_one_or_none()

    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    if group.is_private:
        raise HTTPException(status_code=403, detail="Cannot join private group")

    member_check = await db.execute(
        select(group_members).where(
            and_(group_members.c.user_id == current_user.id, group_members.c.group_id == group_id)
        )
    )
    if member_check.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Already a member")

    await db.execute(group_members.insert().values(user_id=current_user.id, group_id=group_id))

    # Check if settings already exist (handle inconsistent state)
    settings_res = await db.execute(
        select(UserGroupSettings).where(
            and_(UserGroupSettings.user_id == current_user.id, UserGroupSettings.group_id == group_id)
        )
    )
    settings = settings_res.scalar_one_or_none()

    if not settings:
        settings = UserGroupSettings(
            user_id=current_user.id,
            group_id=group_id,
            unread_count=0,
            has_unread_mention=False,
            notification_enabled=True,
        )
        db.add(settings)
    else:
        # Reset existing settings
        settings.unread_count = 0
        settings.has_unread_mention = False
        settings.notification_enabled = True

    await db.commit()
    return {"message": "Joined group successfully"}


@router.post("/groups/{group_id}/leave")
async def leave_group(
    group_id: str, current_user: User = Depends(get_current_user_dep), db: AsyncSession = Depends(get_db)
):
    """Leave a group"""
    await db.execute(
        group_members.delete().where(
            and_(group_members.c.user_id == current_user.id, group_members.c.group_id == group_id)
        )
    )
    await db.execute(
        UserGroupSettings.__table__.delete().where(
            and_(UserGroupSettings.user_id == current_user.id, UserGroupSettings.group_id == group_id)
        )
    )
    await db.commit()
    return {"message": "Left group successfully"}


@router.post("/groups/{group_id}/members/{user_id}")
async def add_member(
    group_id: str, user_id: str, current_user: User = Depends(get_current_user_dep), db: AsyncSession = Depends(get_db)
):
    """Add a member to a group"""
    result = await db.execute(select(Group).where(Group.id == group_id))
    group = result.scalar_one_or_none()

    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    if group.created_by != current_user.id:
        raise HTTPException(status_code=403, detail="Only group creator can add members")

    user = await get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Check if already a member
    member_check = await db.execute(
        select(group_members).where(and_(group_members.c.user_id == user_id, group_members.c.group_id == group_id))
    )
    if member_check.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Already a member")

    await db.execute(group_members.insert().values(user_id=user_id, group_id=group_id))

    # Check if settings already exist (handle inconsistent state)
    settings_res = await db.execute(
        select(UserGroupSettings).where(
            and_(UserGroupSettings.user_id == user_id, UserGroupSettings.group_id == group_id)
        )
    )
    settings = settings_res.scalar_one_or_none()

    if not settings:
        settings = UserGroupSettings(
            user_id=user_id, group_id=group_id, unread_count=0, has_unread_mention=False, notification_enabled=True
        )
        db.add(settings)
    else:
        # Reset existing settings
        settings.unread_count = 0
        settings.has_unread_mention = False
        settings.notification_enabled = True

    await db.commit()
    await notify_unread_update(user_id, group_id, 0, False)

    return {"message": "Member added successfully"}


@router.get("/groups/{group_id}/available-agents")
async def get_available_agents(
    group_id: str, current_user: User = Depends(get_current_user_dep), db: AsyncSession = Depends(get_db)
):
    """Get all users who have agent-like accounts and are NOT in this group.
    Returns user list suitable for adding as group members."""
    # Get current member IDs
    member_result = await db.execute(select(group_members.c.user_id).where(group_members.c.group_id == group_id))
    member_ids = {row[0] for row in member_result.all()}

    # Query launcher for all agents
    from app.ai_web.routes import _proxy_get

    try:
        launcher_data = await _proxy_get("/api/agents")
    except Exception:
        launcher_data = {"agents": []}
    launcher_agents = launcher_data.get("agents", [])

    # Build result: only agents not in group
    results = []
    for agent in launcher_agents:
        agent_id = str(agent.get("agent_id", ""))
        if not agent_id or agent_id in member_ids:
            continue
        chat_profile = agent.get("chat_profile") or {}
        name = chat_profile.get("chat_user_name") or chat_profile.get("name") or agent.get("agent_name", "")
        avatar = chat_profile.get("chat_user_avatar") or chat_profile.get("avatar") or ""
        if not avatar:
            from opensquad.avatar_utils import local_bot_avatar_data_uri

            avatar = local_bot_avatar_data_uri(agent_id or name or "agent")
        results.append(
            {
                "id": agent_id,
                "name": name,
                "avatar": avatar,
                "dir_name": agent.get("dir_name", ""),
            }
        )

    return {"agents": results}


@router.post("/groups/{group_id}/add-agent")
async def add_agent_to_group(
    group_id: str,
    body: dict = Body(...),
    current_user: User = Depends(get_current_user_dep),
    db: AsyncSession = Depends(get_db),
):
    """Add an agent to a group and update the agent's config to include this group ID.
    Body: {"agent_id": "..."}
    """
    agent_id = body.get("agent_id", "")
    if not agent_id:
        raise HTTPException(400, detail="agent_id is required")

    # Verify group exists
    result = await db.execute(select(Group).where(Group.id == group_id))
    group = result.scalar_one_or_none()
    if not group:
        raise HTTPException(404, detail="Group not found")

    # Only creator can add members
    if group.created_by != current_user.id:
        raise HTTPException(403, detail="Only group creator can add members")

    # Verify user exists
    user = await get_user_by_id(db, agent_id)
    if not user:
        raise HTTPException(404, detail=f"Agent user {agent_id} not found")

    # Check if already a member
    member_check = await db.execute(
        select(group_members).where(and_(group_members.c.user_id == agent_id, group_members.c.group_id == group_id))
    )
    if member_check.scalar_one_or_none():
        raise HTTPException(400, detail="Already a member")

    # Add as member
    await db.execute(group_members.insert().values(user_id=agent_id, group_id=group_id))

    # Create settings
    settings_res = await db.execute(
        select(UserGroupSettings).where(
            and_(UserGroupSettings.user_id == agent_id, UserGroupSettings.group_id == group_id)
        )
    )
    settings = settings_res.scalar_one_or_none()
    if not settings:
        settings = UserGroupSettings(
            user_id=agent_id,
            group_id=group_id,
            unread_count=0,
            has_unread_mention=False,
            notification_enabled=True,
        )
        db.add(settings)
    else:
        settings.unread_count = 0
        settings.has_unread_mention = False
        settings.notification_enabled = True
    await db.commit()

    # Update agent config: add group_id to group_chat.groups
    try:
        from app.ai_web.routes import _proxy_get, _proxy_put

        config_data = await _proxy_get(f"/api/agents/{agent_id}/config")
        agent_config = config_data.get("config", config_data) if isinstance(config_data, dict) else {}

        # Ensure group_chat section exists
        if "group_chat" not in agent_config:
            agent_config["group_chat"] = {"enabled": True, "groups": []}
        if "groups" not in agent_config["group_chat"]:
            agent_config["group_chat"]["groups"] = []

        # Add group_id if not already there
        if group_id not in agent_config["group_chat"]["groups"]:
            agent_config["group_chat"]["groups"].append(group_id)
            agent_config.setdefault("group_chat", {})["enabled"] = True
            await _proxy_put(f"/api/agents/{agent_id}/config", {"config": agent_config})
    except Exception as e:
        _log.warning(f"[add-agent] Config update skipped for agent {agent_id}: {e}")

    # Notify via WebSocket
    await notify_unread_update(agent_id, group_id, 0, False)

    return {"message": "Agent added to group successfully"}


# ========== Messages ==========


def format_message_response(msg: Message, *, sender_name: str | None = None) -> MessageResponse:
    """Unified message conversion logic, including recall status calculation"""
    mentions = json.loads(msg.mentions) if msg.mentions else []

    attachments_list = []
    # Check if attachments are loaded, to avoid MissingGreenlet errors in async context
    state = inspect(msg)
    if "attachments" not in state.unloaded and msg.attachments:
        for att in list(msg.attachments):
            size_value = 0
            if att.size:
                try:
                    size_value = int(att.size)
                except ValueError:
                    size_match = re.match(r"(\d+(?:\.\d+)?)", str(att.size))
                    if size_match:
                        size_value = int(float(size_match.group(1)))

            attachments_list.append(
                AttachmentResponse(
                    id=str(att.id),
                    message_id=str(att.message_id),
                    name=str(att.name) if att.name else "",
                    size=str(size_value),
                    url=str(att.url) if att.url else "",
                    type=str(att.type) if att.type else "",
                    duration=getattr(att, "duration", None),
                )
            )

    # Prefer explicit name; else use eager-loaded sender relation when available
    resolved_name = (sender_name or "").strip() or None
    if not resolved_name and "sender" not in state.unloaded and getattr(msg, "sender", None) is not None:
        resolved_name = (getattr(msg.sender, "name", None) or "").strip() or None

    # Compute in real-time whether recall can be undone (within 2 minutes)
    can_undo = False
    is_deleted = bool(msg.is_deleted) if msg.is_deleted else False
    if is_deleted:
        time_since_sent = beijing_now() - _utc_aware(msg.timestamp)
        can_undo = time_since_sent.total_seconds() < 120

    # Convert datetime to timestamp (milliseconds), ensure JSON serializable
    def dt_to_timestamp(dt):
        if dt is None:
            return None
        return int(dt.timestamp() * 1000)

    return MessageResponse(
        id=str(msg.id),
        sender_id=str(msg.sender_id),
        group_id=str(msg.group_id),
        content=str(msg.content) if msg.content else "",
        timestamp=dt_to_timestamp(msg.timestamp),
        type=msg.type,
        sender_name=resolved_name,
        attachments=attachments_list,
        is_pinned=bool(msg.is_pinned) if msg.is_pinned else False,
        reply_to_id=str(msg.reply_to_id) if msg.reply_to_id else None,
        is_edited=bool(msg.is_edited) if msg.is_edited else False,
        is_deleted=is_deleted,
        can_undo=can_undo,
        deleted_at=dt_to_timestamp(msg.deleted_at),
        mentions=mentions,
    )


@router.get("/groups/{group_id}/messages", response_model=list[MessageResponse])
async def get_messages(
    group_id: str,
    before: str | None = None,
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dep),
):
    """Get group message history"""
    query = select(Message).where(Message.group_id == group_id)
    if before:
        result = await db.execute(select(Message).where(Message.id == before))
        before_msg = result.scalar_one_or_none()
        if before_msg:
            query = query.where(Message.timestamp < before_msg.timestamp)

    query = query.order_by(desc(Message.timestamp)).limit(limit)
    result = await db.execute(query.options(selectinload(Message.attachments), selectinload(Message.sender)))
    messages = result.scalars().all()
    return [format_message_response(msg) for msg in reversed(messages)]


@router.get("/groups/{group_id}/messages-around", response_model=list[MessageResponse])
async def get_messages_around(
    group_id: str,
    timestamp: str = Query(..., description="Center timestamp (ISO format)"),
    before_limit: int = Query(10, ge=0, le=50, description="How many messages to load before (history)"),
    after_limit: int = Query(10, ge=0, le=50, description="How many messages to load after (newer)"),
    current_user: User = Depends(get_current_user_dep),
    db: AsyncSession = Depends(get_db),
):
    """Load messages centered on the given timestamp (bidirectional lazy loading)"""
    member_check = await db.execute(
        select(group_members).where(
            and_(group_members.c.user_id == current_user.id, group_members.c.group_id == group_id)
        )
    )
    if not member_check.scalar_one_or_none():
        raise HTTPException(status_code=403, detail="Not a member of this group")

    try:
        center_dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid timestamp format: {e}")

    before_query = (
        select(Message)
        .where(and_(Message.group_id == group_id, Message.timestamp < center_dt))
        .order_by(desc(Message.timestamp))
        .limit(before_limit)
    )
    after_query = (
        select(Message)
        .where(and_(Message.group_id == group_id, Message.timestamp > center_dt))
        .order_by(asc(Message.timestamp))
        .limit(after_limit)
    )
    exact_query = select(Message).where(and_(Message.group_id == group_id, Message.timestamp == center_dt))

    before_res = await db.execute(before_query.options(selectinload(Message.attachments), selectinload(Message.sender)))
    after_res = await db.execute(after_query.options(selectinload(Message.attachments), selectinload(Message.sender)))
    exact_res = await db.execute(exact_query.options(selectinload(Message.attachments), selectinload(Message.sender)))

    all_messages = (
        list(reversed(before_res.scalars().all())) + list(exact_res.scalars().all()) + list(after_res.scalars().all())
    )
    return [format_message_response(msg) for msg in all_messages]


@router.get("/groups/{group_id}/pinned-messages", response_model=list[MessageResponse])
async def get_pinned_messages(
    group_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dep),
):
    """Get pinned messages in a group"""
    query = select(Message).where(and_(Message.group_id == group_id, Message.is_pinned.is_(True)))
    result = await db.execute(query.options(selectinload(Message.attachments), selectinload(Message.sender)))
    return [format_message_response(msg) for msg in result.scalars().all()]


@router.get("/groups/{group_id}/search", response_model=list[MessageResponse])
async def search_messages_in_group(
    group_id: str,
    q: str = Query(..., min_length=1),
    sender_id: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dep),
):
    """Search messages within a specific group"""
    query = select(Message).where(and_(Message.group_id == group_id, Message.content.ilike(f"%{q}%")))
    if sender_id:
        query = query.where(Message.sender_id == sender_id)
    if date_from:
        with contextlib.suppress(ValueError, TypeError):
            query = query.where(Message.timestamp >= datetime.fromisoformat(date_from.replace("Z", "+00:00")))
    if date_to:
        with contextlib.suppress(ValueError, TypeError):
            query = query.where(Message.timestamp <= datetime.fromisoformat(date_to.replace("Z", "+00:00")))

    query = query.order_by(desc(Message.timestamp)).limit(limit)
    result = await db.execute(query.options(selectinload(Message.attachments), selectinload(Message.sender)))
    return [format_message_response(msg) for msg in result.scalars().all()]


@router.post("/groups/{group_id}/messages", response_model=MessageResponse)
async def send_message(
    group_id: str,
    message_data: MessageCreate,
    current_user: User = Depends(get_current_user_dep),
    db: AsyncSession = Depends(get_db),
):
    """Send a message"""
    member_check = await db.execute(
        select(group_members).where(
            and_(group_members.c.user_id == current_user.id, group_members.c.group_id == group_id)
        )
    )
    if not member_check.scalar_one_or_none():
        raise HTTPException(status_code=403, detail="Not a member of this group")

    # Parse @mentions: frontend may not send mentions; auto-parse from content
    mentioned_ids = list(message_data.mentions) if message_data.mentions else []
    if not mentioned_ids and message_data.content:
        import re as _re

        mention_names = _re.findall(r"@([\w\u4e00-\u9fff][\w\u4e00-\u9fff\-]*)", message_data.content)
        for name in mention_names:
            user_result = await db.execute(select(User).where(User.name == name))
            user = user_result.scalar_one_or_none()
            if user and user.id != current_user.id:
                mentioned_ids.append(user.id)

    message_id = f"m_{datetime.now().timestamp()}"
    new_message = Message(
        id=message_id,
        sender_id=current_user.id,
        group_id=group_id,
        content=message_data.content,
        type=MessageType(message_data.type),
        reply_to_id=message_data.reply_to_id,
        mentions=json.dumps(mentioned_ids) if mentioned_ids else None,
        timestamp=beijing_now(),
    )
    db.add(new_message)
    await db.flush()

    # Manually build attachment list (never touch ORM attachments relation, completely avoids lazy loading)
    attachments_for_response = []
    if message_data.attachments:
        for i, att_data in enumerate(message_data.attachments):
            att_id = f"a_{datetime.now().timestamp()}_{i}"
            db.add(
                Attachment(
                    id=att_id,
                    message_id=message_id,
                    name=att_data.name,
                    size=att_data.size,
                    url=att_data.url,
                    type=att_data.type,
                    duration=att_data.duration,
                )
            )
            # Build response directly from request data, bypassing ORM
            size_value = 0
            if att_data.size:
                try:
                    size_value = int(att_data.size)
                except (ValueError, TypeError):
                    size_match = re.match(r"(\d+(?:\.\d+)?)", str(att_data.size))
                    if size_match:
                        size_value = int(float(size_match.group(1)))
            attachments_for_response.append(
                AttachmentResponse(
                    id=att_id,
                    message_id=message_id,
                    name=str(att_data.name) if att_data.name else "",
                    size=str(size_value),
                    url=str(att_data.url) if att_data.url else "",
                    type=str(att_data.type) if att_data.type else "",
                    duration=att_data.duration,
                )
            )

    await db.flush()

    # Update unread counts (batch query, avoid N+1)
    mentioned_set = set(mentioned_ids) if mentioned_ids else set()
    settings_batch = await db.execute(
        select(UserGroupSettings).where(
            and_(UserGroupSettings.group_id == group_id, UserGroupSettings.user_id != current_user.id)
        )
    )
    for settings in settings_batch.scalars().all():
        settings.unread_count += 1
        if settings.user_id in mentioned_set:
            settings.has_unread_mention = True
        await notify_unread_update(settings.user_id, group_id, settings.unread_count, settings.has_unread_mention)

    # Manually build response, completely independent of ORM relation attributes
    mentions = json.loads(new_message.mentions) if new_message.mentions else []
    is_deleted = bool(new_message.is_deleted) if new_message.is_deleted else False
    can_undo = False
    if is_deleted:
        time_since_sent = beijing_now() - _utc_aware(new_message.timestamp)
        can_undo = time_since_sent.total_seconds() < 120

    def _dt_to_ts(dt):
        return int(dt.timestamp() * 1000) if dt else None

    formatted = MessageResponse(
        id=str(new_message.id),
        sender_id=str(new_message.sender_id),
        group_id=str(new_message.group_id),
        content=str(new_message.content) if new_message.content else "",
        timestamp=_dt_to_ts(new_message.timestamp),
        type=new_message.type,
        sender_name=(current_user.name or "").strip() or None,
        attachments=attachments_for_response,
        is_pinned=bool(new_message.is_pinned) if new_message.is_pinned else False,
        reply_to_id=str(new_message.reply_to_id) if new_message.reply_to_id else None,
        is_edited=bool(new_message.is_edited) if new_message.is_edited else False,
        is_deleted=is_deleted,
        can_undo=can_undo,
        deleted_at=_dt_to_ts(new_message.deleted_at) if hasattr(new_message, "deleted_at") else None,
        mentions=mentions,
    )

    await db.commit()

    # Use unified message notification function, ensure type is "new_message"
    from app.json_utils import make_json_safe

    await notify_new_message(group_id, make_json_safe(formatted.dict()), current_user.id)

    return formatted


@router.put("/messages/{message_id}", response_model=MessageResponse)
async def edit_message(
    message_id: str,
    body: dict = Body(...),
    current_user: User = Depends(get_current_user_dep),
    db: AsyncSession = Depends(get_db),
):
    """Edit a message. Body: {"content": "..."}"""
    content = body.get("content", "")
    if not isinstance(content, str):
        raise HTTPException(status_code=422, detail="content must be a string")
    result = await db.execute(
        select(Message)
        .where(Message.id == message_id)
        .options(selectinload(Message.attachments), selectinload(Message.sender))
    )
    message = result.scalar_one_or_none()
    if not message or message.sender_id != current_user.id:
        raise HTTPException(status_code=403, detail="Forbidden")

    message.content = content
    message.is_edited = True
    # Likewise: format before commit
    formatted = format_message_response(message)
    await db.commit()
    await notify_message_update(message.group_id, formatted.model_dump(mode="json"))
    return formatted


@router.delete("/messages/{message_id}")
async def delete_message(
    message_id: str, current_user: User = Depends(get_current_user_dep), db: AsyncSession = Depends(get_db)
):
    """Recall a message (soft delete)"""
    result = await db.execute(select(Message).where(Message.id == message_id))
    message = result.scalar_one_or_none()
    if not message or message.sender_id != current_user.id:
        raise HTTPException(status_code=403, detail="Forbidden")
    if message.is_deleted:
        raise HTTPException(status_code=400, detail="Already deleted")

    message.is_deleted = True
    message.deleted_at = beijing_now()
    time_since_sent = beijing_now() - _utc_aware(message.timestamp)
    message.can_undo = time_since_sent.total_seconds() < 120

    await db.commit()
    await manager.broadcast_to_group(
        message.group_id,
        {
            "type": "message_recalled",
            "data": {
                "message_id": message_id,
                "can_undo": message.can_undo,
                "deleted_at": int(message.deleted_at.timestamp() * 1000),
            },
        },
    )
    return {"message": "Recalled", "can_undo": message.can_undo}


@router.post("/messages/{message_id}/undo-recall")
async def undo_recall(
    message_id: str, current_user: User = Depends(get_current_user_dep), db: AsyncSession = Depends(get_db)
):
    """Undo message recall"""
    result = await db.execute(select(Message).where(Message.id == message_id))
    message = result.scalar_one_or_none()
    if not message or message.sender_id != current_user.id or not message.is_deleted:
        raise HTTPException(status_code=400, detail="Invalid request")

    # As long as the message is in recalled state and not permanently deleted, allow restore
    message.is_deleted = False
    message.deleted_at = None
    await db.commit()
    await manager.broadcast_to_group(
        message.group_id, {"type": "message_undo_recall", "data": {"message_id": message_id}}
    )
    return {"message": "Undone"}


@router.post("/groups/{group_id}/collab-approvals/{approval_id}/resolve")
async def resolve_collab_approval(
    group_id: str,
    approval_id: str,
    body: dict = Body(default={}),
    current_user: User = Depends(get_current_user_dep),
    db: AsyncSession = Depends(get_db),
):
    """User approves/rejects a group approval card (collab gate / mode switch / generic)."""
    from opensquad.collab_approval import (
        KIND_COLLAB_STEP,
        KIND_MODE_SWITCH,
        normalize_kind,
        parse_approval_payload,
        patch_approval_status_in_content,
    )
    from opensquad.collab_board import list_items, upsert_item

    action = str((body or {}).get("action") or "").strip().lower()
    note = str((body or {}).get("note") or "").strip()
    message_id = str((body or {}).get("message_id") or "").strip() or None

    if action not in ("approve", "reject", "approved", "rejected", "deny", "denied"):
        raise HTTPException(status_code=400, detail="action must be approve or reject")
    approved = action in ("approve", "approved")
    new_status = "approved" if approved else "rejected"

    member_check = await db.execute(
        select(group_members).where(
            and_(group_members.c.user_id == current_user.id, group_members.c.group_id == group_id)
        )
    )
    if not member_check.scalar_one_or_none():
        raise HTTPException(status_code=403, detail="Not a member of this group")

    message = None
    if message_id:
        result = await db.execute(select(Message).where(Message.id == message_id, Message.group_id == group_id))
        message = result.scalar_one_or_none()
    if message is None:
        result = await db.execute(
            select(Message).where(Message.group_id == group_id).order_by(desc(Message.timestamp)).limit(80)
        )
        for m in result.scalars().all():
            payload = parse_approval_payload(m.content or "")
            if payload and str(payload.get("id")) == approval_id:
                message = m
                break
    if message is None:
        raise HTTPException(status_code=404, detail="Approval message not found in this group")

    payload = parse_approval_payload(message.content or "")
    if not payload or str(payload.get("id")) != approval_id:
        raise HTTPException(status_code=400, detail="Message is not a matching approval card")

    prev_status = str(payload.get("status") or "pending")
    if prev_status in ("approved", "rejected"):
        raise HTTPException(status_code=409, detail=f"Approval already {prev_status}")

    kind = normalize_kind(str(payload.get("kind") or ""))
    collab_id = str(payload.get("collab_id") or "")
    agent_id = str(payload.get("agent_id") or payload.get("pm_agent_id") or "")
    agent_name = str(payload.get("agent_name") or payload.get("pm_agent_name") or agent_id)
    step = str(payload.get("step") or "")
    title = str(payload.get("title") or step or "批准请求")
    to_mode = str(payload.get("to_mode") or "").strip().lower()
    from_mode = str(payload.get("from_mode") or "").strip().lower()

    message.content = patch_approval_status_in_content(message.content or "", new_status, note)
    message.is_edited = True
    await db.commit()
    result = await db.execute(
        select(Message)
        .where(Message.id == message.id)
        .options(selectinload(Message.attachments), selectinload(Message.sender))
    )
    message = result.scalar_one()
    formatted = format_message_response(message)
    await notify_message_update(group_id, formatted.model_dump(mode="json"))

    # Collab board bookkeeping (only for collaboration gates)
    if kind == KIND_COLLAB_STEP and collab_id:
        try:
            items = list_items(collab_id=collab_id, visibility="public")
            target = next(
                (i for i in items if str(i.get("item_type")) == "approval" and str(i.get("item_key")) == approval_id),
                None,
            )
            board_agent = str((target or {}).get("agent_id") or agent_id or "pm")
            extra = dict((target or {}).get("extra") or {}) if isinstance((target or {}).get("extra"), dict) else {}
            approval_meta = dict(extra.get("approval") or payload)
            approval_meta["status"] = new_status
            if note:
                approval_meta["resolve_note"] = note
            approval_meta["resolved_by"] = current_user.id
            approval_meta["resolved_by_name"] = current_user.name
            extra["approval"] = approval_meta
            extra["kind"] = "collab_step_approval"
            extra["message_id"] = message.id
            upsert_item(
                collab_id=collab_id,
                agent_id=board_agent,
                item_type="approval",
                item_key=approval_id,
                title=title,
                content=str(payload.get("summary") or ""),
                status=new_status,
                visibility="public",
                task_name=str((target or {}).get("task_name") or collab_id),
                extra=extra,
            )
        except Exception as e:
            logging.getLogger(__name__).warning("[API] Failed to update collab board approval: %s", e)

    # Mode switch: apply / deny via agent command channel
    mode_applied = False
    if kind == KIND_MODE_SWITCH and agent_id:
        try:
            from app.ai_web.registry import registry as agent_registry

            candidates = [agent_id]
            if agent_name and agent_name not in candidates:
                candidates.append(agent_name)
            for aid, info in list(getattr(agent_registry, "agents", {}).items()):
                try:
                    if aid in candidates:
                        continue
                    if str(getattr(info, "agent_name", "") or "") in (agent_id, agent_name):
                        candidates.append(aid)
                except Exception:
                    pass

            if approved and to_mode:
                cmd = {
                    "type": "command",
                    "user_id": current_user.id,
                    "command": "set_agent_mode",
                    "data": {"mode": to_mode, "id": approval_id, "approved_request_id": approval_id},
                }
            else:
                cmd = {
                    "type": "command",
                    "user_id": current_user.id,
                    "command": "deny_mode_switch",
                    "data": {"id": approval_id, "reason": note or "User denied in group chat"},
                }
            for cand in candidates:
                if await agent_registry.send_to_agent(cand, cmd):
                    mode_applied = True
                    break
        except Exception as e:
            logging.getLogger(__name__).warning("[API] Failed to apply mode switch from group approval: %s", e)

    decision_label = "APPROVED (确定)" if approved else "REJECTED (拒绝)"
    if kind == KIND_MODE_SWITCH:
        nudge = (
            f"[System] Mode switch approval {decision_label}\n"
            f"approval_id: {approval_id}\n"
            f"from_mode: {from_mode}\n"
            f"to_mode: {to_mode}\n"
            f"resolved_by: {current_user.name}\n"
        )
        if note:
            nudge += f"note: {note}\n"
        if approved:
            nudge += (
                f"The user approved the mode switch. You are now in {to_mode or 'the requested'} mode. "
                "Continue the task you were waiting on. Do not ask for approval again."
            )
        else:
            nudge += (
                "The user denied the mode switch. Stay in the current mode. "
                "Revise your plan or ask again later if still needed."
            )
    elif kind == KIND_COLLAB_STEP:
        nudge = (
            f"[System] Collaboration step approval {decision_label}\n"
            f"approval_id: {approval_id}\n"
            f"collab_id: {collab_id}\n"
            f"step: {step}\n"
            f"title: {title}\n"
            f"resolved_by: {current_user.name}\n"
        )
        if note:
            nudge += f"note: {note}\n"
        if approved:
            nudge += (
                "The user approved this gate. You may proceed to the next collaboration step "
                "(update the board if needed, then continue)."
            )
        else:
            nudge += (
                "The user rejected this gate. Revise requirements/plan/tasks on the board, "
                "discuss in the group, then call request_step_approval again when ready."
            )
    else:
        nudge = (
            f"[System] Group approval {decision_label}\n"
            f"approval_id: {approval_id}\n"
            f"kind: {kind}\n"
            f"title: {title}\n"
            f"resolved_by: {current_user.name}\n"
        )
        if note:
            nudge += f"note: {note}\n"
        if approved:
            nudge += "The user approved your request. Proceed with the authorized action."
        else:
            nudge += "The user rejected your request. Do not proceed; revise and re-request if needed."

    nudged = False
    # For mode_switch, apply_agent_mode already nudges via input_hub; still send chat if deny
    # or if command delivery failed. For other kinds always chat-nudge.
    should_chat_nudge = kind != KIND_MODE_SWITCH or not mode_applied or not approved
    if agent_id and should_chat_nudge:
        try:
            from app.ai_web.registry import registry as agent_registry

            candidates = [agent_id]
            if agent_name and agent_name not in candidates:
                candidates.append(agent_name)
            for aid, info in list(getattr(agent_registry, "agents", {}).items()):
                try:
                    if aid in candidates:
                        continue
                    if str(getattr(info, "agent_name", "") or "") in (agent_id, agent_name):
                        candidates.append(aid)
                except Exception:
                    pass

            chat_payload = {
                "type": "chat",
                "user_id": current_user.id,
                "content": nudge,
                "channel": "gateway",
                "sender_name": current_user.name or "User",
            }
            for cand in candidates:
                if await agent_registry.send_to_agent(cand, chat_payload):
                    nudged = True
                    break
        except Exception as e:
            logging.getLogger(__name__).warning("[API] Failed to nudge agent after group approval: %s", e)

    try:
        sys_msg_id = f"m_{datetime.now().timestamp()}_appr"
        if kind == KIND_MODE_SWITCH:
            sys_content = (
                f"✅ 已批准模式切换：{from_mode or '?'} → {to_mode or '?'}"
                if approved
                else f"❌ 已拒绝模式切换：{from_mode or '?'} → {to_mode or '?'}"
            )
        elif kind == KIND_COLLAB_STEP:
            sys_content = f"✅ 协作环节已批准：{title}" if approved else f"❌ 协作环节已拒绝：{title}"
        else:
            sys_content = f"✅ 已批准：{title}" if approved else f"❌ 已拒绝：{title}"
        if note:
            sys_content += f"（{note}）"
        mention_ids: list[str] = []
        if agent_id:
            mention_ids.append(str(agent_id))
            if agent_name:
                sys_content += f"\n@{agent_name}"
        sys_msg = Message(
            id=sys_msg_id,
            sender_id=current_user.id,
            group_id=group_id,
            content=sys_content,
            type=MessageType.SYSTEM,
            mentions=json.dumps(mention_ids) if mention_ids else None,
            timestamp=beijing_now(),
        )
        db.add(sys_msg)
        await db.commit()
        result = await db.execute(
            select(Message)
            .where(Message.id == sys_msg_id)
            .options(selectinload(Message.attachments), selectinload(Message.sender))
        )
        sys_msg = result.scalar_one()
        await notify_new_message(group_id, format_message_response(sys_msg).model_dump(mode="json"), current_user.id)
    except Exception as e:
        logging.getLogger(__name__).warning("[API] Failed to post approval system note: %s", e)

    return {
        "ok": True,
        "approval_id": approval_id,
        "status": new_status,
        "kind": kind,
        "collab_id": collab_id or None,
        "step": step or None,
        "to_mode": to_mode or None,
        "mode_applied": mode_applied,
        "message": formatted.model_dump(mode="json"),
        "agent_notified": nudged or mode_applied,
    }


@router.post("/groups/{group_id}/propose-options/{proposal_id}/resolve")
async def resolve_propose_options(
    group_id: str,
    proposal_id: str,
    body: dict = Body(default={}),
    current_user: User = Depends(get_current_user_dep),
    db: AsyncSession = Depends(get_db),
):
    """User resolves an N-way propose-options card posted in group chat.

    action: ``choose`` (value=option_id), ``custom`` (value=free text), or ``ignore``.
    """
    from opensquad.collab_approval import (
        parse_propose_options_payload,
        patch_propose_options_status_in_content,
    )

    action = str((body or {}).get("action") or "").strip().lower()
    value = str((body or {}).get("value") or "").strip()
    note = str((body or {}).get("note") or "").strip()
    message_id = str((body or {}).get("message_id") or "").strip() or None

    if action not in ("choose", "custom", "ignore"):
        raise HTTPException(status_code=400, detail="action must be choose, custom, or ignore")

    member_check = await db.execute(
        select(group_members).where(
            and_(group_members.c.user_id == current_user.id, group_members.c.group_id == group_id)
        )
    )
    if not member_check.scalar_one_or_none():
        raise HTTPException(status_code=403, detail="Not a member of this group")

    message = None
    if message_id:
        result = await db.execute(select(Message).where(Message.id == message_id, Message.group_id == group_id))
        message = result.scalar_one_or_none()
    if message is None:
        result = await db.execute(
            select(Message).where(Message.group_id == group_id).order_by(desc(Message.timestamp)).limit(80)
        )
        for m in result.scalars().all():
            payload = parse_propose_options_payload(m.content or "")
            if payload and str(payload.get("id")) == proposal_id:
                message = m
                break
    if message is None:
        raise HTTPException(status_code=404, detail="Propose-options message not found in this group")

    payload = parse_propose_options_payload(message.content or "")
    if not payload or str(payload.get("id")) != proposal_id:
        raise HTTPException(status_code=400, detail="Message is not a matching propose-options card")

    prev_status = str(payload.get("status") or "pending")
    if prev_status in ("chosen", "ignored", "custom"):
        raise HTTPException(status_code=409, detail=f"Proposal already {prev_status}")

    if action == "choose":
        if not value:
            raise HTTPException(status_code=400, detail="value (option_id or comma-separated ids) required for choose")
        # Accept JSON array string or comma-separated ids for multi-select.
        chosen_ids: list[str] = []
        raw_val = value.strip()
        if raw_val.startswith("["):
            try:
                parsed = json.loads(raw_val)
                if isinstance(parsed, list):
                    chosen_ids = [str(x).strip() for x in parsed if str(x).strip()]
            except Exception:
                chosen_ids = []
        if not chosen_ids:
            chosen_ids = [p.strip() for p in raw_val.split(",") if p.strip()]
        if not chosen_ids:
            raise HTTPException(status_code=400, detail="value (option_id) required for choose")
        allow_multiple = bool(payload.get("allow_multiple"))
        if not allow_multiple and len(chosen_ids) > 1:
            chosen_ids = chosen_ids[:1]
        new_status = "chosen"
        chosen_id = chosen_ids[0]
        custom_answer = ""
    elif action == "custom":
        if not value:
            raise HTTPException(status_code=400, detail="value (custom answer) required for custom")
        new_status = "custom"
        chosen_id = ""
        chosen_ids = []
        custom_answer = value
    else:  # ignore
        new_status = "ignored"
        chosen_id = ""
        chosen_ids = []
        custom_answer = ""

    message.content = patch_propose_options_status_in_content(
        message.content or "",
        new_status,
        chosen=chosen_id,
        custom=custom_answer,
        note=note,
        chosen_ids=chosen_ids,
    )
    message.is_edited = True
    await db.commit()
    result = await db.execute(
        select(Message)
        .where(Message.id == message.id)
        .options(selectinload(Message.attachments), selectinload(Message.sender))
    )
    message = result.scalar_one()
    formatted = format_message_response(message)
    await notify_message_update(group_id, formatted.model_dump(mode="json"))

    # Build nudge message and deliver to the agent
    prompt = str(payload.get("prompt") or "")
    options = payload.get("options") or []
    chosen_titles: list[str] = []
    id_set = set(chosen_ids)
    for opt in options:
        if isinstance(opt, dict) and str(opt.get("id")) in id_set:
            chosen_titles.append(str(opt.get("title") or opt.get("id") or ""))
    chosen_title = ", ".join(t for t in chosen_titles if t) or chosen_id

    if action == "ignore":
        nudge = (
            f"[System] Group propose-options ignored\n"
            f"proposal_id: {proposal_id}\n"
            f"prompt: {prompt}\n"
            f"resolved_by: {current_user.name}\n"
            "The user ignored the proposed options. Ask whether they want a different approach, "
            "or proceed with the most sensible default if they prefer you to decide."
        )
        sys_content = f"⏭ 已忽略选项：{prompt}"
    elif action == "custom":
        nudge = (
            f"[System] Group propose-options custom answer\n"
            f"proposal_id: {proposal_id}\n"
            f"prompt: {prompt}\n"
            f"custom_answer: {custom_answer[:500]}\n"
            f"resolved_by: {current_user.name}\n"
            f'The user typed their own answer instead of picking a listed option: "{custom_answer[:500]}". '
            "Follow their answer as the chosen plan."
        )
        sys_content = f"✏️ 自定义答案：{custom_answer[:40]}"
    else:
        ids_joined = ", ".join(chosen_ids)
        nudge = (
            f"[System] Group propose-options chosen\n"
            f"proposal_id: {proposal_id}\n"
            f"prompt: {prompt}\n"
            f"chosen_option_id: {chosen_id}\n"
            f"chosen_option_ids: [{ids_joined}]\n"
            f"chosen_option_title: {chosen_title}\n"
            f"resolved_by: {current_user.name}\n"
        )
        if len(chosen_ids) > 1:
            nudge += (
                f"The user chose multiple options: [{ids_joined}] ({chosen_title}). "
                "Continue with those plans now (in a sensible order). Do not ask for the choice again."
            )
            sys_content = f"✅ 已选择：{chosen_title}"
        else:
            nudge += (
                f"The user chose option '{chosen_id}' ({chosen_title}). "
                "Continue with that plan now. Do not ask for the choice again."
            )
            sys_content = f"✅ 已选择：{chosen_title or chosen_id}"
    if note:
        nudge += f"\nnote: {note}"

    nudged = False
    agent_id = str(payload.get("agent_id") or "")
    agent_name = str(payload.get("agent_name") or "")
    try:
        from app.ai_web.registry import registry as agent_registry

        candidates: list[str] = []
        if agent_id:
            candidates.append(agent_id)
        if agent_name and agent_name not in candidates:
            candidates.append(agent_name)
        for aid, info in list(getattr(agent_registry, "agents", {}).items()):
            try:
                if aid in candidates:
                    continue
                if str(getattr(info, "agent_name", "") or "") in (agent_id, agent_name) and (agent_id or agent_name):
                    candidates.append(aid)
            except Exception:
                pass
        # Last resort: only if still empty, try the single running agent
        if not candidates:
            for aid, _info in list(getattr(agent_registry, "agents", {}).items()):
                candidates.append(aid)
                break

        # Prefer the same resolve command Agent Web uses (input_hub nudge inside
        # the agent process). Fields MUST live under ``data`` — gateway_adapter
        # reads cmd_data = message["data"], not top-level keys.
        cmd = {
            "type": "command",
            "user_id": current_user.id,
            "command": "resolve_proposed_options",
            "data": {
                "id": proposal_id,
                "chosen_option_id": chosen_id,
                "chosen_option_ids": chosen_ids,
                "custom_answer": custom_answer,
                "ignored": action == "ignore",
            },
        }
        for cand in candidates:
            if await agent_registry.send_to_agent(cand, cmd):
                nudged = True
                break

        # Chat fallback: always try if command delivery failed; also try when
        # no agent_id was embedded (candidates may be wrong / empty).
        if not nudged:
            chat_payload = {
                "type": "chat",
                "user_id": current_user.id,
                "content": nudge,
                "channel": "gateway",
                "sender_name": current_user.name or "User",
            }
            for cand in candidates:
                if await agent_registry.send_to_agent(cand, chat_payload):
                    nudged = True
                    break
    except Exception as e:
        logging.getLogger(__name__).warning("[API] Failed to nudge agent after propose-options: %s", e)

    try:
        sys_msg_id = f"m_{datetime.now().timestamp()}_propopt"
        sys_msg = Message(
            id=sys_msg_id,
            sender_id=current_user.id,
            group_id=group_id,
            content=sys_content,
            type=MessageType.SYSTEM,
            timestamp=beijing_now(),
        )
        db.add(sys_msg)
        await db.commit()
        result = await db.execute(
            select(Message)
            .where(Message.id == sys_msg_id)
            .options(selectinload(Message.attachments), selectinload(Message.sender))
        )
        sys_msg = result.scalar_one()
        await notify_new_message(group_id, format_message_response(sys_msg).model_dump(mode="json"), current_user.id)
    except Exception as e:
        logging.getLogger(__name__).warning("[API] Failed to post propose-options system note: %s", e)

    return {
        "ok": True,
        "proposal_id": proposal_id,
        "status": new_status,
        "chosen_option_id": chosen_id or None,
        "custom_answer": custom_answer or None,
        "message": formatted.model_dump(mode="json"),
        "agent_notified": nudged,
    }


@router.delete("/messages/{message_id}/permanent")
async def permanent_delete_message(
    message_id: str, current_user: User = Depends(get_current_user_dep), db: AsyncSession = Depends(get_db)
):
    """Permanently delete"""
    result = await db.execute(select(Message).where(Message.id == message_id))
    message = result.scalar_one_or_none()
    if not message or message.sender_id != current_user.id:
        raise HTTPException(status_code=403, detail="Forbidden")

    g_id = message.group_id
    await db.delete(message)
    await db.commit()
    await manager.broadcast_to_group(g_id, {"type": "message_permanently_deleted", "data": {"message_id": message_id}})
    return {"message": "Deleted"}


@router.post("/messages/{message_id}/pin")
async def pin_message(
    message_id: str, current_user: User = Depends(get_current_user_dep), db: AsyncSession = Depends(get_db)
):
    result = await db.execute(select(Message).where(Message.id == message_id))
    message = result.scalar_one_or_none()
    if not message:
        raise HTTPException(status_code=404)
    message.is_pinned = not message.is_pinned
    group_res = await db.execute(select(Group).where(Group.id == message.group_id))
    group = group_res.scalar_one()
    group.pinned_message_id = message_id if message.is_pinned else None
    await db.commit()
    await manager.broadcast_to_group(
        message.group_id,
        {
            "type": "message_pinned",
            "data": {"message_id": message_id, "is_pinned": message.is_pinned, "group_id": message.group_id},
        },
    )
    return {"message": "Pinned" if message.is_pinned else "Unpinned"}


@router.post("/groups/{group_id}/read")
async def mark_as_read(
    group_id: str,
    message_id: str | None = None,
    current_user: User = Depends(get_current_user_dep),
    db: AsyncSession = Depends(get_db),
):
    res = await db.execute(
        select(UserGroupSettings).where(
            and_(UserGroupSettings.user_id == current_user.id, UserGroupSettings.group_id == group_id)
        )
    )
    settings = res.scalar_one_or_none()
    if settings:
        settings.unread_count = 0
        settings.has_unread_mention = False
        if message_id:
            settings.last_read_message_id = message_id
        await db.commit()
    return {"message": "Read"}


# ========== File Upload ==========


@router.post("/upload")
async def upload_file(
    file: UploadFile = File(...), folder_path: str = Query(None), current_user: User = Depends(get_current_user_dep)
):
    import hashlib

    # SEC-7: enforce a size cap and a content-type whitelist so the upload
    # endpoint cannot be used as a DoS vector or an HTML/XSS hosting point.
    MAX_UPLOAD_BYTES = 50 * 1024 * 1024
    ALLOWED_UPLOAD_TYPES = {
        "image/jpeg",
        "image/png",
        "image/gif",
        "image/webp",
        "image/svg+xml",
        "video/mp4",
        "video/webm",
        "audio/mpeg",
        "audio/wav",
        "application/pdf",
        "text/plain",
        "application/zip",
        "application/json",
        "text/markdown",
    }
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File too large (max 50MB)")
    c_type = (file.content_type or "").lower()
    if c_type and c_type not in ALLOWED_UPLOAD_TYPES:
        raise HTTPException(status_code=415, detail=f"File type not allowed: {c_type}")

    upload_dir = Path(UPLOAD_DIR)
    file_hash = hashlib.md5(f"{file.filename}{datetime.now()}".encode(), usedforsecurity=False).hexdigest()[:16]
    ext = os.path.splitext(file.filename)[1]
    # SEC-7: sanitise the extension to a safe alphanumeric subset.
    if ext:
        ext = "".join(ch for ch in ext if ch.isalnum() or ch in (".", "_", "-"))[:16]
    new_filename = f"{file_hash}{ext}"
    if folder_path:
        safe_folder = Path(folder_path).name
        target_dir = upload_dir / safe_folder
        target_dir.mkdir(exist_ok=True)
        file_path, file_url = target_dir / new_filename, f"/uploads/{safe_folder}/{new_filename}"
    else:
        file_path, file_url = upload_dir / new_filename, f"/uploads/{new_filename}"
    with open(file_path, "wb") as f:
        f.write(content)
    size_bytes = len(content)
    size_str = (
        f"{size_bytes}B"
        if size_bytes < 1024
        else f"{size_bytes / 1024:.1f}KB"
        if size_bytes < 1024 * 1024
        else f"{size_bytes / (1024 * 1024):.1f}MB"
    )
    f_type = "image" if c_type.startswith("image/") else "video" if c_type.startswith("video/") else "file"
    return {"url": file_url, "name": file.filename, "size": size_str, "type": f_type}


@router.post("/upload-folder")
async def upload_folder_as_zip(files: list[UploadFile] = File(...), current_user: User = Depends(get_current_user_dep)):
    import hashlib
    import zipfile

    upload_dir = Path(UPLOAD_DIR)
    folder_name = files[0].filename.split("/")[0] if files and "/" in files[0].filename else "folder"
    zip_filename = f"{folder_name}_{hashlib.md5(f'{folder_name}{datetime.now()}'.encode(), usedforsecurity=False).hexdigest()[:16]}.zip"
    zip_path = upload_dir / zip_filename
    file_list = []
    # SEC-7: cap total uploaded bytes per request and sanitise arcnames
    # (basename only) to prevent zip traversal / archive abuse.
    MAX_UPLOAD_BYTES = 50 * 1024 * 1024
    total_bytes = 0
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
        for file in files:
            content = await file.read(MAX_UPLOAD_BYTES + 1)
            if len(content) > MAX_UPLOAD_BYTES:
                raise HTTPException(status_code=413, detail="File too large (max 50MB per file)")
            total_bytes += len(content)
            if total_bytes > MAX_UPLOAD_BYTES:
                raise HTTPException(status_code=413, detail="Upload batch too large (max 50MB)")
            arcname = os.path.basename(file.filename.replace("\\", "/"))
            if not arcname:
                arcname = f"file_{len(file_list)}"
            zipf.writestr(arcname, content)
            file_list.append({"name": arcname, "size": f"{len(content) / 1024:.1f}KB"})
    zip_size = zip_path.stat().st_size
    size_str = (
        f"{zip_size}B"
        if zip_size < 1024
        else f"{zip_size / 1024:.1f}KB"
        if zip_size < 1024 * 1024
        else f"{zip_size / (1024 * 1024):.1f}MB"
    )
    return {
        "url": f"/uploads/{zip_filename}",
        "name": f"{folder_name}.zip",
        "original_name": folder_name,
        "size": size_str,
        "type": "folder",
        "file_count": len(files),
        "files": file_list,
    }


@router.post("/search")
async def search_all_messages(
    search_query: SearchQuery, current_user: User = Depends(get_current_user_dep), db: AsyncSession = Depends(get_db)
):
    query = select(Message).join(Group).join(group_members).where(group_members.c.user_id == current_user.id)
    if search_query.text:
        query = query.where(Message.content.ilike(f"%{search_query.text}%"))
    if search_query.user_id:
        query = query.where(Message.sender_id == search_query.user_id)
    if search_query.group_id:
        query = query.where(Message.group_id == search_query.group_id)
    query = query.order_by(desc(Message.timestamp)).limit(50)
    result = await db.execute(query.options(selectinload(Message.attachments), selectinload(Message.sender)))
    return [format_message_response(m) for m in result.scalars().all()]


# ========== Direct Messages ==========


@router.post("/direct-messages")
async def send_direct_message(
    recipient_name: str = Body(...),
    content: str = Body(...),
    title: str | None = Body(None),
    attachments: str | None = Body(None),
    current_user: User = Depends(get_current_user_dep),
    db: AsyncSession = Depends(get_db),
):
    res = await db.execute(select(User).where(User.name == recipient_name))
    recipient = res.scalar_one_or_none()
    if not recipient:
        raise HTTPException(status_code=404)
    new_dm = DirectMessage(
        id=f"dm_{datetime.now().timestamp()}",
        sender_id=current_user.id,
        recipient_id=recipient.id,
        title=title,
        content=content,
        timestamp=beijing_now(),
        is_read=False,
        attachments=attachments,
    )
    db.add(new_dm)
    await db.commit()
    # ID and timestamp already generated manually; no refresh needed
    from .websocket import manager

    await manager.send_to_user(
        recipient.id,
        {
            "type": "new_direct_message",
            "data": {
                "id": new_dm.id,
                "sender_id": current_user.id,
                "sender_name": current_user.name,
                "sender_avatar": current_user.avatar,
                "title": title,
                "content": content,
                "attachments": json.loads(attachments) if attachments else [],
                "timestamp": new_dm.timestamp.isoformat(),
                "is_read": False,
            },
        },
    )
    return {"id": new_dm.id, "message": "Sent"}


@router.get("/direct-messages")
async def get_direct_messages(
    filter_type: str = "all", current_user: User = Depends(get_current_user_dep), db: AsyncSession = Depends(get_db)
):
    # Build query
    if filter_type == "sent":
        query = select(DirectMessage).where(
            and_(DirectMessage.sender_id == current_user.id, DirectMessage.is_deleted_by_sender.is_(False))
        )
    elif filter_type == "received":
        query = select(DirectMessage).where(
            and_(DirectMessage.recipient_id == current_user.id, DirectMessage.is_deleted_by_recipient.is_(False))
        )
    else:
        query = select(DirectMessage).where(
            or_(
                and_(DirectMessage.sender_id == current_user.id, DirectMessage.is_deleted_by_sender.is_(False)),
                and_(DirectMessage.recipient_id == current_user.id, DirectMessage.is_deleted_by_recipient.is_(False)),
            )
        )

    query = query.order_by(desc(DirectMessage.timestamp))

    # Fetch all message IDs first, then batch query
    res = await db.execute(query)
    messages = res.scalars().all()

    # Collect all user IDs
    user_ids = set()
    for msg in messages:
        user_ids.add(msg.sender_id)
        user_ids.add(msg.recipient_id)

    # Batch query all users (single query)
    users_map = {}
    if user_ids:
        users_res = await db.execute(select(User).where(User.id.in_(list(user_ids))))
        for user in users_res.scalars():
            users_map[user.id] = user

    # Build response
    response = []
    for msg in messages:
        sender = users_map.get(msg.sender_id)
        recipient = users_map.get(msg.recipient_id)
        is_s = msg.sender_id == current_user.id
        response.append(
            {
                "id": msg.id,
                "title": msg.title or "New Message",
                "content": msg.content,
                "sender": sender.name if sender and not is_s else "You",
                "sender_avatar": sender.avatar if sender and not is_s else None,
                "recipient": recipient.name if recipient and is_s else "You",
                "timestamp": msg.timestamp,
                "is_read": msg.is_read,
                "read_at": msg.read_at,
                "is_sender": is_s,
                "other_party": recipient.name if recipient and is_s else (sender.name if sender else "Unknown"),
                "attachments": json.loads(msg.attachments) if msg.attachments else [],
            }
        )
    return response


@router.put("/direct-messages/{message_id}/read")
async def mark_direct_message_read(
    message_id: str, current_user: User = Depends(get_current_user_dep), db: AsyncSession = Depends(get_db)
):
    res = await db.execute(
        select(DirectMessage).where(and_(DirectMessage.id == message_id, DirectMessage.recipient_id == current_user.id))
    )
    msg = res.scalar_one_or_none()
    if msg:
        msg.is_read = True
        msg.read_at = beijing_now()
        await db.commit()
    return {"message": "Read"}


@router.delete("/direct-messages/{message_id}")
async def delete_direct_message(
    message_id: str, current_user: User = Depends(get_current_user_dep), db: AsyncSession = Depends(get_db)
):
    res = await db.execute(
        select(DirectMessage).where(
            and_(
                DirectMessage.id == message_id,
                or_(DirectMessage.sender_id == current_user.id, DirectMessage.recipient_id == current_user.id),
            )
        )
    )
    msg = res.scalar_one_or_none()
    if msg:
        if msg.sender_id == current_user.id:
            msg.is_deleted_by_sender = True
        else:
            msg.is_deleted_by_recipient = True
        if msg.is_deleted_by_sender and msg.is_deleted_by_recipient:
            await db.delete(msg)
        await db.commit()
    return {"message": "Deleted"}
