"""
Initialize database data — pre-register seed agent communication accounts.

The first web user is NOT pre-created. On a fresh install, OpenSquad now
shows a language + registration wizard so the first user explicitly chooses
their own credentials via the web UI. The agent comm accounts (pm / coder /
qa) are still pre-created here so they are ready to be added as group
members on the first login (api.login() -> _bootstrap_default_group()).

The default collaboration group + localized welcome message are NOT created
here, because the language preference is only known at first login (the user
picks zh/en on the language selection screen). That bootstrapping lives in
api.login() -> _bootstrap_default_group().

Seed agents are discovered from two locations (in order):
  1. <workspace>/agents/  — after workspace init has copied seeds here.
  2. <install_root>/src/agents/  — fallback for first gateway start when the
     workspace may not exist yet (gateway uses install dir as temp workspace,
     see main.py:50-51).
"""

import asyncio
import json
import os
import random
from datetime import datetime, timezone

from app.auth import get_password_hash
from app.database import AsyncSessionLocal
from app.models import User, UserStatus
from sqlalchemy import select

# Seed agent directory names to pre-register. These must match the directories
# copied by workspace_utils._copy_default_resources() and each must contain a
# config.json with a group_chat block.
_SEED_AGENT_DIRS = ("pm", "coder", "qa")


def _discover_seed_agents() -> list:
    """Scan for seed agent configs from multiple candidate paths.

    Returns a list of dicts: {name, email, password, agent_id, agent_type}.
    Tries <workspace>/agents/ first (after workspace init), then falls back
    to <install_root>/src/agents/ (first gateway start before any workspace
    exists).
    """
    candidates = []

    try:
        from opensquad.system_config import syscfg

        candidates.append(syscfg.workspace_agents_dir())
    except Exception:
        pass

    _here = os.path.dirname(os.path.abspath(__file__))
    _backend = os.path.dirname(_here)
    _gateway = os.path.dirname(_backend)
    _src = os.path.dirname(_gateway)
    candidates.append(os.path.join(_src, "agents"))

    found = []
    seen_emails = set()
    for agents_dir in candidates:
        if not os.path.isdir(agents_dir):
            continue
        for name in _SEED_AGENT_DIRS:
            cfg_path = os.path.join(agents_dir, name, "config.json")
            if not os.path.isfile(cfg_path):
                continue
            try:
                with open(cfg_path, encoding="utf-8") as f:
                    cfg = json.load(f)
            except (OSError, ValueError):
                continue
            gc = cfg.get("group_chat", {})
            if not isinstance(gc, dict) or not gc.get("enabled"):
                continue
            email = gc.get("email", "")
            password = gc.get("password", "")
            if not email or not password or email in seen_emails:
                continue
            seen_emails.add(email)
            found.append(
                {
                    "name": cfg.get("agent_name", name),
                    "email": email,
                    "password": password,
                    "agent_id": cfg.get("agent_id", name),
                    "agent_type": cfg.get("agent_type", "general"),
                }
            )
        if found:
            break
    return found


async def _generate_user_id(db) -> str:
    """Generate a unique 6-digit numeric user ID."""
    for _ in range(20):
        candidate = str(random.randint(100000, 999999))
        result = await db.execute(select(User).where(User.id == candidate))
        if result.scalar_one_or_none() is None:
            return candidate
    raise RuntimeError("Unable to generate unique user ID after 20 attempts")


async def init_default_data():
    """Initialize default data: pre-register seed agent comm accounts only.

    The first web user is intentionally NOT pre-created. The OpenSquad
    first-launch wizard (web UI) walks the user through language selection
    and account registration. Once a web user exists, /auth/register is
    closed for the web (node_secret-bypassed paths used by internal tools
    are still open).

    Only creates agent users (single commit). The default group + welcome
    message are created on first login (api._bootstrap_default_group) once
    the language preference is known.
    """
    async with AsyncSessionLocal() as db:
        # Idempotent guard: only seed an empty DB.
        result = await db.execute(select(User).limit(1))
        if result.scalar_one_or_none():
            print("Database already has data, skipping initialization")
            return

        print("Initializing default data (seed agent accounts)...")

        now = datetime.now(timezone.utc)

        # Discover seed agents and create their comm accounts as OFFLINE
        # users. These accounts exist before the group is created (on first
        # login) so agents can be added as group members immediately.
        seed_agents = _discover_seed_agents()
        for sa in seed_agents:
            existing = await db.execute(select(User).where(User.email == sa["email"]))
            if existing.scalar_one_or_none():
                continue
            uid = await _generate_user_id(db)
            from opensquad.avatar_utils import local_bot_avatar_data_uri

            user = User(
                id=uid,
                name=sa["name"],
                email=sa["email"],
                hashed_password=get_password_hash(sa["password"]),
                avatar=local_bot_avatar_data_uri(uid),
                status=UserStatus.OFFLINE,
                created_at=now,
                last_seen=now,
            )
            db.add(user)
            print(f"  Created agent account (offline): {sa['name']} <{sa['email']}>")

        await db.commit()

        print("Default data initialization complete.")
        print("No default web account — register the first user via the web UI.")


if __name__ == "__main__":
    asyncio.run(init_default_data())
