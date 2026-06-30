"""Tests for the first-launch init wizard (feature/gateway-ui-init-wizard).

Verifies:

1. ``init_data`` no longer pre-creates the default admin user
   (``admin@opensquad.ai``/``123456``). On a fresh DB it only seeds the
   ``*@ai`` agent comm accounts.
2. ``/auth/register`` is open to the very first web caller and is closed
   for every subsequent web caller.
3. ``/auth/register`` accepts a valid ``X-Node-Secret`` header and stays
   open regardless of how many web users exist (internal-tool bypass).
4. ``/auth/registration-status`` reports ``registration_required=True``
   when no non-agent user exists and ``False`` once one does.
5. ``/auth/register`` also bootstraps the default collaboration group
   (and pinned welcome message) using the wizard's selected language,
   and degrades gracefully if the bootstrap step itself fails.
"""

from __future__ import annotations

import os
import sys
import types
from unittest.mock import AsyncMock, MagicMock

import pytest

# The gateway backend uses absolute imports (``from app import ...`` and
# ``from init_data import ...``) with the backend dir on sys.path. Add it
# once for the whole module.
_BACKEND_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    os.pardir,
    "src",
    "opensquad",
    "gateway",
    "backend",
)
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)


# ── 1. init_data no longer pre-creates the default admin ─────────────────


def test_init_data_does_not_define_default_admin_constant():
    """The hardcoded admin dict must be removed from init_data."""
    from opensquad.gateway.backend import init_data

    assert not hasattr(init_data, "_DEFAULT_ADMIN"), (
        "init_data._DEFAULT_ADMIN must be removed: the wizard now registers "
        "the first user via the web UI; the backend must not pre-create one."
    )


def test_init_data_init_default_data_only_seeds_agents():
    """``init_data.init_default_data`` must NOT pre-create any web/admin user.

    We assert this by parsing the source and checking that the function body
    no longer contains a ``User(...)`` construction with ``id="admin"`` (the
    pre-wizard hardcoded admin) — that path has been removed in favour of
    a web-UI-driven first registration.
    """
    import ast
    import os

    init_data_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        os.pardir,
        "src",
        "opensquad",
        "gateway",
        "backend",
        "init_data.py",
    )
    with open(init_data_path, encoding="utf-8") as f:
        source = f.read()
    tree = ast.parse(source)

    # Find the ``init_default_data`` async function and inspect its body
    # for the removed admin creation pattern.
    target = None
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "init_default_data":
            target = node
            break
    assert target is not None, "init_default_data function must exist"

    serialized = ast.unparse(target)

    # The pre-wizard code created an admin user with id='admin'. After the
    # change, that block is gone. Pin this contract: the function must not
    # build a User with the literal id 'admin'.
    assert 'id="admin"' not in serialized and "id='admin'" not in serialized, (
        "init_default_data must NOT create a User with id='admin' anymore; "
        "the first web user is registered via the UI.\n\nFunction body:\n" + serialized
    )
    # And the "Default account: admin@opensquad.ai / 123456" banner is gone.
    assert "Default account:" not in source, (
        "The 'Default account: admin@opensquad.ai / 123456' banner must be removed."
    )
    # But agent seeding must still be present.
    assert "_discover_seed_agents" in serialized, (
        "Agent account seeding (_discover_seed_agents) must remain in init_default_data."
    )


# ── 2. /auth/register first-user gate (unit-level with mocked DB) ─────────


def _build_register_user(*, email: str, name: str, password: str):
    """Build a UserCreate-like pydantic model the api.register handler expects."""
    from app.schemas import UserCreate

    return UserCreate(email=email, name=name, password=password)


@pytest.fixture
def fake_db():
    """Fake AsyncSession that tracks ``execute`` results deterministically.

    The ``web_user_exists`` check (a SELECT on users where email NOT LIKE
    '%@ai') is the only query that matters for the register gate. Other
    queries (email/name uniqueness, etc.) are satisfied with no-result
    mocks so the request can proceed to ``create_user``.
    """
    db = MagicMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    db.flush = AsyncMock()

    # Default: no user exists with this email / name (uniqueness check).
    empty_result = MagicMock()
    empty_result.scalar_one_or_none = MagicMock(return_value=None)

    db.execute = AsyncMock(return_value=empty_result)
    return db


@pytest.fixture
def mock_syscfg(monkeypatch):
    """Patch opensquad.system_config.syscfg used inside api.register."""
    fake = types.SimpleNamespace(
        node_secret=lambda: "test-node-secret-abc123",
    )
    # Patch in both possible import paths used by api.register.
    for module_path in ("opensquad.gateway.backend.app.api",):
        # The api.register handler does ``from opensquad.system_config import
        # syscfg as _syscfg`` lazily; we have to patch the module attribute
        # at the *handler's* call site. We do this by patching the symbol
        # ``_syscfg`` inside the api module namespace.
        api_mod = sys.modules.get(module_path)
        if api_mod is not None and hasattr(api_mod, "_syscfg"):
            monkeypatch.setattr(api_mod, "_syscfg", fake, raising=False)
    return fake


def _patched_register_dependencies(monkeypatch, *, web_user_exists: bool):
    """Wire up the minimum mocks api.register needs.

    Returns a function that, when called with a UserCreate, returns the
    ``User`` the fake ``create_user`` would create.
    """
    created = {}

    async def _fake_create_user(db, user_data, user_id=None):
        from datetime import datetime, timezone

        from app.models import User, UserStatus

        u = User(
            id="123456",
            email=user_data.email,
            name=user_data.name,
            hashed_password="hashed",
            avatar="",
            status=UserStatus.OFFLINE,
            created_at=datetime.now(timezone.utc),
            last_seen=datetime.now(timezone.utc),
        )
        created["user"] = u
        return u

    # _has_web_user returns web_user_exists
    async def _fake_has_web_user(db):
        return web_user_exists

    # Token creation returns a dummy string.
    monkeypatch.setattr("opensquad.gateway.backend.app.api.create_user", _fake_create_user)
    monkeypatch.setattr(
        "opensquad.gateway.backend.app.api.create_access_token",
        lambda data: "fake-jwt-token",
    )
    # Import _has_web_user by name from the module and patch via the module
    # namespace (we set it later per-test).
    return created


def _make_fake_user(email: str, name: str, id: str = "123456"):
    """Build a real ``User`` ORM instance suitable for ``UserResponse.model_validate``."""
    from datetime import datetime, timezone

    from app.models import User, UserStatus

    return User(
        id=id,
        email=email,
        name=name,
        hashed_password="hashed",
        avatar="",
        status=UserStatus.OFFLINE,
        created_at=datetime.now(timezone.utc),
        last_seen=datetime.now(timezone.utc),
    )


@pytest.mark.asyncio
async def test_register_first_web_user_succeeds(monkeypatch, fake_db):
    """First web caller (no web user yet) → 200 / Token."""
    from app import api as api_mod

    async def _no_web_user(db):
        return False

    monkeypatch.setattr(api_mod, "_has_web_user", _no_web_user)
    monkeypatch.setattr(api_mod, "create_access_token", lambda data: "tok")
    monkeypatch.setattr(
        api_mod,
        "create_user",
        AsyncMock(return_value=_make_fake_user(email="a@x", name="A")),
    )

    user_data = _build_register_user(email="a@x", name="A", password="pw123456")
    request = MagicMock()
    request.headers.get = MagicMock(return_value="")

    result = await api_mod.register(user_data, request, fake_db)
    assert result.access_token == "tok"
    assert result.user.email == "a@x"


@pytest.mark.asyncio
async def test_register_second_web_user_blocked(monkeypatch, fake_db):
    """Second web caller (web user already exists) → 403."""
    from app import api as api_mod
    from fastapi import HTTPException

    async def _web_user_exists(db):
        return True

    monkeypatch.setattr(api_mod, "_has_web_user", _web_user_exists)

    user_data = _build_register_user(email="b@x", name="B", password="pw123456")
    request = MagicMock()
    request.headers.get = MagicMock(return_value="")

    with pytest.raises(HTTPException) as exc_info:
        await api_mod.register(user_data, request, fake_db)
    assert exc_info.value.status_code == 403
    assert "Registration closed" in exc_info.value.detail


@pytest.mark.asyncio
async def test_register_with_valid_node_secret_bypasses_gate(monkeypatch, fake_db):
    """Internal call (valid X-Node-Secret) is allowed even when a web user exists."""
    from app import api as api_mod

    async def _web_user_exists(db):
        return True

    monkeypatch.setattr(api_mod, "_has_web_user", _web_user_exists)
    monkeypatch.setattr(api_mod, "create_access_token", lambda data: "tok")
    monkeypatch.setattr(
        api_mod,
        "create_user",
        AsyncMock(return_value=_make_fake_user(email="bot@ai", name="Bot")),
    )
    # The handler does ``from opensquad.system_config import syscfg as _syscfg``
    # inside the function, so the name lookup hits `opensquad.system_config.syscfg`.
    import opensquad.system_config as syscfg_mod

    monkeypatch.setattr(
        syscfg_mod,
        "syscfg",
        types.SimpleNamespace(node_secret=lambda: "valid-secret"),
    )

    user_data = _build_register_user(email="bot@ai", name="Bot", password="pw123456")
    request = MagicMock()
    request.headers.get = MagicMock(return_value="valid-secret")

    result = await api_mod.register(user_data, request, fake_db)
    assert result.access_token == "tok"
    assert result.user.email == "bot@ai"


@pytest.mark.asyncio
async def test_register_with_wrong_node_secret_still_blocked(monkeypatch, fake_db):
    """Wrong X-Node-Secret does NOT bypass the gate."""
    from app import api as api_mod
    from fastapi import HTTPException

    async def _web_user_exists(db):
        return True

    monkeypatch.setattr(api_mod, "_has_web_user", _web_user_exists)
    import opensquad.system_config as syscfg_mod

    monkeypatch.setattr(
        syscfg_mod,
        "syscfg",
        types.SimpleNamespace(node_secret=lambda: "correct-secret"),
    )

    user_data = _build_register_user(email="bot@ai", name="Bot", password="pw123456")
    request = MagicMock()
    request.headers.get = MagicMock(return_value="WRONG-secret")

    with pytest.raises(HTTPException) as exc_info:
        await api_mod.register(user_data, request, fake_db)
    assert exc_info.value.status_code == 403


# ── 3. /auth/registration-status ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_registration_status_open_when_no_web_user(monkeypatch):
    from app import api as api_mod

    async def _no_web(db):
        return False

    monkeypatch.setattr(api_mod, "_has_web_user", _no_web)

    db = MagicMock()
    # No users at all (return empty for the "last user" lookup).
    empty = MagicMock()
    empty.scalar_one_or_none = MagicMock(return_value=None)
    db.execute = AsyncMock(return_value=empty)

    result = await api_mod.registration_status(db)
    assert result["registration_required"] is True
    assert result["language"] in ("zh", "en")  # any default


@pytest.mark.asyncio
async def test_registration_status_closed_when_web_user_exists(monkeypatch):
    from app import api as api_mod

    async def _yes_web(db):
        return True

    monkeypatch.setattr(api_mod, "_has_web_user", _yes_web)

    db = MagicMock()
    empty = MagicMock()
    empty.scalar_one_or_none = MagicMock(return_value=None)
    db.execute = AsyncMock(return_value=empty)

    result = await api_mod.registration_status(db)
    assert result["registration_required"] is False


# ── 4. /auth/register also bootstraps the default group ────────────────
#
# Regression: previously, /auth/register only created the user. The default
# collaboration group + pinned welcome message were only created on the
# next /auth/login call. Since the wizard auto-logs the user in after
# registration, the group never appeared until the user explicitly logged
# out and back in. The fix is to call _bootstrap_default_group from the
# register handler as well.


@pytest.mark.asyncio
async def test_register_bootstraps_default_group(monkeypatch, fake_db):
    """First-register must trigger _bootstrap_default_group (not just login)."""
    from app import api as api_mod
    from app.schemas import UserCreate

    bootstrap_calls: list = []

    async def _spy_bootstrap(db, user, language):
        bootstrap_calls.append({"user_id": user.id, "language": language})

    async def _no_web_user(db):
        return False

    monkeypatch.setattr(api_mod, "_has_web_user", _no_web_user)
    monkeypatch.setattr(api_mod, "_bootstrap_default_group", _spy_bootstrap)
    monkeypatch.setattr(api_mod, "create_access_token", lambda data: "tok")

    fake_user = _make_fake_user(id="u-new", email="alice@example.com", name="Alice")
    monkeypatch.setattr(
        api_mod,
        "create_user",
        AsyncMock(return_value=fake_user),
    )

    user_data_with_lang = UserCreate(name="Alice", email="alice@example.com", password="hunter2", language="en")
    user_data_no_lang = UserCreate(name="Bob", email="bob@example.com", password="hunter2")

    request = MagicMock()
    request.headers.get = MagicMock(return_value="")

    # With language
    await api_mod.register(user_data_with_lang, request=request, db=fake_db)
    # Without language (back-compat: should not crash)
    await api_mod.register(user_data_no_lang, request=request, db=fake_db)

    assert len(bootstrap_calls) == 2, (
        "register must call _bootstrap_default_group so the user lands in a populated group view on first registration"
    )
    assert bootstrap_calls[0]["language"] == "en"
    assert bootstrap_calls[1]["language"] is None
    assert bootstrap_calls[0]["user_id"] == "u-new"


@pytest.mark.asyncio
async def test_register_bootstrap_failure_does_not_break_registration(monkeypatch, fake_db):
    """If _bootstrap_default_group raises, register must still return a token."""
    from app import api as api_mod
    from app.schemas import UserCreate

    async def _exploding_bootstrap(db, user, language):
        raise RuntimeError("group table missing")

    async def _no_web_user(db):
        return False

    monkeypatch.setattr(api_mod, "_has_web_user", _no_web_user)
    monkeypatch.setattr(api_mod, "_bootstrap_default_group", _exploding_bootstrap)
    monkeypatch.setattr(api_mod, "create_access_token", lambda data: "tok-issued")

    fake_user = _make_fake_user(id="u-survive", email="carol@example.com", name="Carol")
    monkeypatch.setattr(api_mod, "create_user", AsyncMock(return_value=fake_user))

    user_data = UserCreate(name="Carol", email="carol@example.com", password="hunter2", language="zh")

    request = MagicMock()
    request.headers.get = MagicMock(return_value="")

    response = await api_mod.register(user_data, request=request, db=fake_db)

    assert response.access_token == "tok-issued", (
        "register must still issue a token even if the bootstrap step fails; "
        "the user should not be locked out of the app by a bootstrap error"
    )
