"""Uploading an agent avatar is one action that has to land in three places.

The picture the Agent Workstation draws comes from the launcher's
``data/profile.json``; the one the group chat draws comes from the ChatPro
``users`` row; and the bytes themselves live under the workspace uploads dir,
served by the same ``/uploads`` mount the chat already loads attachments from.
Nothing enforces that those three agree, so what is pinned here is that the
*same URL string* is handed to all of them — a divergence shows up as a panel
that updates while the group chat keeps the old picture.

Validation matters more than usual because the client posts base64 in a JSON
body (``apiRequest`` always sets ``Content-Type: application/json``, so a browser
cannot add a multipart boundary). The filename and MIME type are therefore just
attacker-controlled strings: only the magic bytes may decide what ends up in a
directory that is served verbatim.
"""

from __future__ import annotations

import base64
import json
import os
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

_BACKEND_ROOT = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "src", "opensquad", "gateway", "backend")
)
if _BACKEND_ROOT not in os.sys.path:
    os.sys.path.insert(0, _BACKEND_ROOT)

from app.ai_web.routes import _admin as admin  # noqa: E402

AGENT_NAME = "avatar_probe_agent"
CHAT_USER_ID = "agent-avatar-probe-001"

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
GIF87 = b"GIF87a" + b"\x00" * 64
GIF89 = b"GIF89a" + b"\x00" * 64
WEBP = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"\x00" * 64
SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'


def _body(blob: bytes) -> dict:
    return {"filename": "whatever.bin", "content": base64.b64encode(blob).decode("ascii")}


# ---------------------------------------------------------------- validation


@pytest.mark.parametrize(
    ("blob", "expected"),
    [(PNG, ".png"), (JPEG, ".jpg"), (GIF87, ".gif"), (GIF89, ".gif"), (WEBP, ".webp")],
)
def test_extension_is_derived_from_the_image_bytes(blob, expected):
    """The stored extension is what the static mount will serve, so it has to
    describe the content rather than repeat a client-supplied name."""
    assert admin._sniff_image_extension(blob) == expected


@pytest.mark.parametrize("blob", [SVG, b"%PDF-1.7\n", b"plain text", b"PK\x03\x04", b""])
def test_non_images_are_not_given_an_extension(blob):
    """SVG is XML — serving one from /uploads would be stored XSS, and the
    browser is told the type by this extension."""
    assert admin._sniff_image_extension(blob) is None


def test_an_svg_declared_as_png_is_rejected():
    """The client-declared filename is not consulted anywhere."""
    with pytest.raises(HTTPException) as exc:
        admin._decode_avatar_payload({"filename": "logo.png", "content": base64.b64encode(SVG).decode()})
    assert exc.value.status_code == 400
    assert "format" in exc.value.detail


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"content": ""},
        {"content": "   "},
        {"content": "not base64 at all!!"},
        {"content": None},
        [],
    ],
)
def test_missing_or_broken_content_is_rejected(body):
    with pytest.raises(HTTPException) as exc:
        admin._decode_avatar_payload(body)
    assert exc.value.status_code == 400


def test_an_oversized_image_is_rejected_with_a_readable_message():
    """The limit has to name itself in KB: the UI shows this detail verbatim."""
    blob = b"\x89PNG\r\n\x1a\n" + b"\x00" * admin._AVATAR_MAX_BYTES
    with pytest.raises(HTTPException) as exc:
        admin._decode_avatar_payload(_body(blob))
    assert exc.value.status_code == 400
    assert f"{admin._AVATAR_MAX_BYTES // 1024} KB" in exc.value.detail


def test_a_pasted_data_uri_is_accepted():
    """`data:` prefixed payloads are what a canvas/FileReader round-trip yields,
    so the prefix is stripped rather than treated as image data."""
    blob = admin._decode_avatar_payload({"content": "data:image/png;base64," + base64.b64encode(PNG).decode()})
    assert blob == PNG


# ------------------------------------------------------------------- wiring


class _Recorder:
    def __init__(self, result=None):
        self.calls: list = []
        self._result = result

    def __call__(self, *args, **kwargs):
        self.calls.append(args if not kwargs else {**kwargs, "args": args})
        return self._result


@pytest.fixture
def uploads(tmp_path, monkeypatch):
    """Redirect the avatar directory into tmp_path and stub the three sinks."""
    monkeypatch.setattr(admin, "_agent_avatar_dir", lambda slug: str(tmp_path / slug))

    profile_put = _Recorder({"ok": True, "profile": {"chat_user_avatar": None}})
    store = _Recorder(True)
    notify = _Recorder(2)
    identity = {
        "agent_id": "avatar-probe-001",
        "email": "probe@ai",
        "chat_user_id": CHAT_USER_ID,
        "group_ids": ["g1", "g2"],
    }

    async def fake_put(path, json_body=None, *a, **kw):
        profile_put(path, json_body)
        return profile_put._result

    async def fake_identity(name):
        return identity

    async def fake_store(ident, avatar):
        store(ident, avatar)
        return True

    async def fake_notify(ident, avatar):
        notify(ident, avatar)
        return len(ident["group_ids"])

    monkeypatch.setattr(admin, "_proxy_put", fake_put)
    monkeypatch.setattr(admin, "_agent_chat_identity", fake_identity)
    monkeypatch.setattr(admin, "_store_agent_chat_avatar", fake_store)
    monkeypatch.setattr(admin, "_notify_avatar_change", fake_notify)
    return {
        "root": tmp_path,
        "profile_put": profile_put,
        "store": store,
        "notify": notify,
        "identity": identity,
    }


@pytest.mark.asyncio
async def test_the_same_avatar_url_reaches_the_file_the_panel_and_the_chat(uploads):
    """One string, four consumers. This is the whole point of the feature."""
    result = await admin.admin_upload_agent_avatar(AGENT_NAME, _body(PNG), current_user=MagicMock())

    url = result["avatar"]
    assert url.startswith("/uploads/agent-avatars/")

    # 1. the bytes are where the URL says they are
    stored = uploads["root"] / admin._avatar_slug(AGENT_NAME) / os.path.basename(url)
    assert stored.read_bytes() == PNG

    # 2. the launcher profile (feeds GET /api/agents)
    assert uploads["profile_put"].calls == [(f"/api/agents/{AGENT_NAME}/profile", {"avatar": url})]

    # 3. the group-chat user row
    assert uploads["store"].calls[-1] == (uploads["identity"], url)

    # 4. the groups the agent is already visible in
    assert uploads["notify"].calls[-1] == (uploads["identity"], url)
    assert result["groups_notified"] == 2


@pytest.mark.asyncio
async def test_a_previous_avatar_is_pruned_so_uploads_do_not_accumulate(uploads):
    agent_dir = uploads["root"] / admin._avatar_slug(AGENT_NAME)
    agent_dir.mkdir(parents=True)
    (agent_dir / "old1111111111.png").write_bytes(PNG)
    (agent_dir / "old2222222222.png").write_bytes(PNG)

    result = await admin.admin_upload_agent_avatar(AGENT_NAME, _body(JPEG), current_user=MagicMock())

    assert [p.name for p in agent_dir.iterdir()] == [os.path.basename(result["avatar"])]


@pytest.mark.asyncio
async def test_pruning_is_scoped_to_the_agent_that_changed(uploads):
    """A slug collision must not be able to delete another agent's picture."""
    other_dir = uploads["root"] / admin._avatar_slug("someone_else")
    other_dir.mkdir(parents=True)
    (other_dir / "keep.png").write_bytes(PNG)

    await admin.admin_upload_agent_avatar(AGENT_NAME, _body(PNG), current_user=MagicMock())

    assert (other_dir / "keep.png").exists()


@pytest.mark.asyncio
async def test_reset_restores_a_default_both_surfaces_agree_on(uploads):
    """Reset writes the generated default rather than clearing the field.

    Clearing would split the two surfaces: the chat backfills a robot face for an
    empty agent avatar while the panel would fall back to its type icon. The seed
    is the chat account id — the same one the agent uses on boot — so the default
    does not change colour just because it went through this endpoint.
    """
    from opensquad.avatar_utils import local_bot_avatar_data_uri

    result = await admin.admin_reset_agent_avatar(AGENT_NAME, current_user=MagicMock())

    expected = local_bot_avatar_data_uri(CHAT_USER_ID)
    assert result["avatar"] == expected
    assert uploads["profile_put"].calls == [(f"/api/agents/{AGENT_NAME}/profile", {"avatar": expected})]
    assert uploads["store"].calls[-1] == (uploads["identity"], expected)
    assert uploads["notify"].calls[-1] == (uploads["identity"], expected)

    # A reset after a reset is a no-op, not a new colour.
    again = await admin.admin_reset_agent_avatar(AGENT_NAME, current_user=MagicMock())
    assert again["avatar"] == expected


@pytest.mark.asyncio
async def test_reset_removes_the_uploaded_files(uploads):
    await admin.admin_upload_agent_avatar(AGENT_NAME, _body(PNG), current_user=MagicMock())
    agent_dir = uploads["root"] / admin._avatar_slug(AGENT_NAME)

    await admin.admin_reset_agent_avatar(AGENT_NAME, current_user=MagicMock())

    assert list(agent_dir.iterdir()) == []


@pytest.mark.asyncio
async def test_reset_still_works_for_an_agent_without_a_chat_account(uploads, monkeypatch):
    """No group chat configured means nothing to sync — not an error.

    The default then falls back to the agent identity so the panel still leaves
    the "custom picture" state behind.
    """
    identity = {"agent_id": "avatar-probe-001", "email": "", "chat_user_id": "", "group_ids": []}

    async def fake_identity(name):
        return identity

    monkeypatch.setattr(admin, "_agent_chat_identity", fake_identity)

    result = await admin.admin_reset_agent_avatar(AGENT_NAME, current_user=MagicMock())

    assert result["avatar"].startswith("data:image/svg+xml")
    assert result["chat_user_id"] is None
    assert result["groups_notified"] == 0


@pytest.mark.asyncio
async def test_a_launcher_refusal_surfaces_instead_of_pretending_to_succeed(uploads, monkeypatch):
    """If profile.json cannot be written the avatar is only half-applied, and the
    user has to be told — the panel is what they are looking at."""

    async def failing_put(path, json_body=None, *a, **kw):
        raise HTTPException(502, "Launcher is not running")

    monkeypatch.setattr(admin, "_proxy_put", failing_put)

    with pytest.raises(HTTPException) as exc:
        await admin.admin_upload_agent_avatar(AGENT_NAME, _body(PNG), current_user=MagicMock())
    assert exc.value.status_code == 502


def test_slug_is_filename_safe():
    """The slug becomes a path segment, so nothing exotic may survive it."""
    assert admin._avatar_slug("agent305") == "agent305"
    assert admin._avatar_slug("News2Theme分析员") == "news2theme"
    assert admin._avatar_slug("../../etc/passwd") == "etc-passwd"
    assert admin._avatar_slug("..") == ""


def test_json_body_round_trip_is_the_documented_shape():
    """Locks the wire contract the frontend sends, so a rename on either side
    fails here rather than silently in the browser."""
    payload = json.dumps({"filename": "a.png", "content": base64.b64encode(PNG).decode()})
    assert admin._decode_avatar_payload(json.loads(payload)) == PNG
