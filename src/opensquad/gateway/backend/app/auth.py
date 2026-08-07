"""
Authentication-related functionality
"""

import json
import logging
import os
import random
from datetime import datetime, timedelta, timezone

import bcrypt
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from app.schemas import UserCreate
from opensquad.system_config import syscfg as _syscfg

_log = logging.getLogger("auth")


def load_config():
    """Read JWT config from syscfg (compatible with legacy caller interface, returns nested structure)"""
    _log.info("[AUTH] load_config() called via syscfg")
    raw = _syscfg.raw()
    jwt_cfg = raw.get("jwt", {})
    _log.info("[AUTH] jwt config keys: %s", list(jwt_cfg.keys()))
    return {"backend": {"jwt": jwt_cfg}}


# Load configuration
config = load_config()
jwt_config = config.get("backend", {}).get("jwt", {})

# JWT configuration
SECRET_KEY = jwt_config.get("secret_key", "")


def _is_jwt_placeholder(value: str) -> bool:
    """True when the configured JWT secret is an unrotated placeholder.

    SEC-2: a non-empty 'CHANGE_ME' style placeholder was previously accepted
    as a real secret, letting anyone forge gateway JWTs on non-Docker deploys.
    """
    if not value:
        return True
    lowered = value.lower()
    return "change_me" in lowered or lowered.startswith("your_jwt")


if not SECRET_KEY or _is_jwt_placeholder(SECRET_KEY):
    import secrets

    SECRET_KEY = secrets.token_hex(32)
    _log.warning(
        "[AUTH] No valid JWT secret_key configured in system_config.json jwt.secret_key "
        "(empty or placeholder). A random key has been generated and persisted to config. "
    )
    # Persist the generated key so Gateway restarts don't invalidate tokens
    try:
        raw = _syscfg.raw()
        raw.setdefault("jwt", {})["secret_key"] = SECRET_KEY
        # Use opensquad._syscfg._CONFIG_PATH to locate system_config.json
        from opensquad._syscfg._workspace import _CONFIG_PATH

        cfg_path = _CONFIG_PATH
        if os.path.isfile(cfg_path):
            with open(cfg_path, "w", encoding="utf-8") as f:
                json.dump(raw, f, ensure_ascii=False, indent=2)
            _log.info("[AUTH] Generated JWT secret_key persisted to %s", cfg_path)
        _syscfg.reload()
    except Exception as persist_err:
        _log.warning("[AUTH] Failed to persist generated JWT secret_key: %s", persist_err)
ALGORITHM = jwt_config.get("algorithm", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = jwt_config.get("access_token_expire_minutes", 1440)


def _normalize_password(password: str) -> bytes:
    """Normalize password to bcrypt-safe bytes (max 72 bytes)."""
    if password is None:
        password = ""
    if not isinstance(password, str):
        password = str(password)
    return password.encode("utf-8")[:72]


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against its hash"""
    try:
        return bcrypt.checkpw(_normalize_password(plain_password), hashed_password.encode("utf-8"))
    except Exception:
        return False


def get_password_hash(password: str) -> str:
    """Hash a password"""
    return bcrypt.hashpw(_normalize_password(password), bcrypt.gensalt()).decode("utf-8")


def create_access_token(data: dict, expires_delta: timedelta | None = None):
    """Create an access token"""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


async def generate_user_id(db: AsyncSession) -> str:
    """Generate a unique 6-digit numeric user ID (range 100000–999999), guaranteed unique in DB"""
    for _ in range(20):  # Retry up to 20 times
        candidate = str(random.randint(100000, 999999))
        result = await db.execute(select(User).where(User.id == candidate))
        if result.scalar_one_or_none() is None:
            return candidate
    raise RuntimeError("Unable to generate unique user ID after 20 attempts")


async def get_user_by_email(db: AsyncSession, email: str) -> User | None:
    """Get user by email"""
    result = await db.execute(select(User).where(User.email == email))
    return result.scalar_one_or_none()


async def get_user_by_name(db: AsyncSession, name: str) -> User | None:
    """Get user by name (check for duplicates)"""
    result = await db.execute(select(User).where(User.name == name))
    return result.scalar_one_or_none()


async def get_user_by_id(db: AsyncSession, user_id: str) -> User | None:
    """Get user by ID"""
    result = await db.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()


async def authenticate_user(db: AsyncSession, email: str, password: str) -> User | None:
    """Authenticate user"""
    user = await get_user_by_email(db, email)
    if not user:
        return None
    if not verify_password(password, user.hashed_password):
        return None
    return user


async def create_user(db: AsyncSession, user_data: UserCreate, user_id: str | None = None) -> User:
    """Create a new user.

    user_id: optional — if provided, use directly (Agent account path, 6-digit number);
             otherwise, auto-generate a unique 6-digit numeric ID.
    """
    # Check whether email is already registered
    existing_email = await get_user_by_email(db, user_data.email)
    if existing_email:
        raise ValueError("Email already registered")

    # Check whether username already exists
    existing_name = await get_user_by_name(db, user_data.name)
    if existing_name:
        raise ValueError("Username already exists")

    # Determine user ID
    if user_id is not None:
        # Check whether the specified ID is already taken
        existing_id = await get_user_by_id(db, user_id)
        if existing_id:
            raise ValueError(f"User ID {user_id} already exists")
        final_id = user_id
    else:
        final_id = await generate_user_id(db)

    hashed_password = get_password_hash(user_data.password)

    avatar = user_data.avatar or ""
    if not avatar and user_data.email and str(user_data.email).endswith("@ai"):
        from opensquad.avatar_utils import local_bot_avatar_data_uri

        avatar = local_bot_avatar_data_uri(str(final_id or user_data.name or "agent"))

    db_user = User(
        id=final_id,
        email=user_data.email,
        name=user_data.name,
        hashed_password=hashed_password,
        avatar=avatar,
        status="offline",
        created_at=datetime.now(timezone.utc),
        last_seen=datetime.now(timezone.utc),
    )

    db.add(db_user)
    await db.commit()
    return db_user


def decode_token(token: str) -> dict | None:
    """Decode a JWT token"""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except JWTError as e:
        # SECURITY: never log the SECRET_KEY or long token prefixes — they can
        # leak through log aggregation and enable token forgery.
        _log.warning("[AUTH] Token decode FAILED: %s: %s (algorithm=%s)", type(e).__name__, e, ALGORITHM)
        return None
