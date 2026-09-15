"""Regression tests for the model-card PUT merge semantics.

``CardsMixin._handle_put_model_card`` used to rebuild the card JSON from a
hard-coded field whitelist and overwrite the file wholesale.  That shape had two
failure modes, both silent:

1. **Unknown fields were dropped** -- including fields the very request writing
   them carried.  ``extra_headers`` (the Custom Provider form's request headers)
   therefore never reached disk at all, and the builtin ``step-image-edit-2``
   card lost ``image_size`` / ``image_steps`` / ``image_cfg_scale`` the moment
   anybody saved it from the Agent Web, so the runtime fell back to defaults.
2. **Omitted fields were reset to their defaults.**  A partial PUT (only
   ``api_key``) re-applied ``enabled: True``, ``token_max: 128000`` and friends
   over whatever the file already held.

The handler now merges -- request > existing file > default -- and keeps unknown
keys, so both classes of data loss are gone.  These tests pin the precedence,
the unknown-key survival, and one real HTTP round-trip through the composed
handler (the module is imported lazily, so a direct unit call alone would not
prove the route still writes what the UI expects).
"""

from __future__ import annotations

import json
import pathlib
import threading

import pytest

from opensquad.launcher.management_api import ExclusiveHTTPServer, ManagementHandler
from opensquad.launcher.management_api import _cards as cards_mod
from opensquad.launcher.management_api._cards import _MODEL_CARD_DEFAULTS, CardsMixin
from opensquad.utils.local_http import open_local

BUILTIN_CARDS_DIR = pathlib.Path(__file__).resolve().parents[1] / "src" / "model_cards"


class _Recorder:
    """Minimal ``self`` for the unbound handler: it only calls ``_send_json``."""

    def __init__(self):
        self.sent = []

    def _send_json(self, payload, status: int = 200):
        self.sent.append((payload, status))
        return payload


def _put(card_name: str, body: dict):
    handler = _Recorder()
    CardsMixin._handle_put_model_card(handler, card_name, body)
    assert handler.sent and handler.sent[-1][0].get("ok") is True
    return handler


def _read(cards_dir, card_name: str) -> dict:
    with open(cards_dir / f"{card_name}.json", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def cards_dir(tmp_path, monkeypatch):
    """Point the mixin's module-level card directory at a throwaway folder."""
    monkeypatch.setattr(cards_mod, "MODEL_CARDS_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def _no_launcher_token(monkeypatch):
    monkeypatch.setattr(ManagementHandler, "_get_launcher_token", staticmethod(lambda: ""))


@pytest.fixture
def live_server():
    server = ExclusiveHTTPServer(("127.0.0.1", 0), ManagementHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


# ── 1. unknown keys survive ──


def test_unknown_field_already_in_the_file_survives_a_partial_put(cards_dir):
    """A field the table does not list must not be erased by an unrelated save."""
    (cards_dir / "custom.json").write_text(
        json.dumps({"name": "custom", "provider": "Mine", "extra_headers": {"X-Tenant": "acme"}}),
        encoding="utf-8",
    )

    _put("custom", {"api_key": "sk-new"})

    card = _read(cards_dir, "custom")
    assert card["api_key"] == "sk-new"
    assert card["extra_headers"] == {"X-Tenant": "acme"}


def test_unknown_field_sent_by_the_client_is_persisted(cards_dir):
    """The bug behind it: ``extra_headers`` was written by the UI, never stored."""
    _put("mine__llm", {"provider": "Mine", "model_name": "llm", "extra_headers": {"X-Tenant": "acme"}})

    assert _read(cards_dir, "mine__llm")["extra_headers"] == {"X-Tenant": "acme"}


def test_client_supplied_unknown_field_wins_over_the_stale_one(cards_dir):
    _put("c", {"extra_headers": {"A": "1"}})
    _put("c", {"api_key": "k", "extra_headers": {"A": "2"}})

    assert _read(cards_dir, "c")["extra_headers"] == {"A": "2"}


# ── 2. omitted known fields keep the file value, not the default ──


def test_omitted_fields_keep_their_stored_values(cards_dir):
    (cards_dir / "tuned.json").write_text(
        json.dumps(
            {
                "name": "tuned",
                "api_key": "sk-old",
                "token_max": 999,
                "temperature": 0.7,
                "tool_call_mode": "native",
                "enabled": False,
                "render_mode": "full",
            }
        ),
        encoding="utf-8",
    )

    _put("tuned", {"api_key": "sk-new"})

    card = _read(cards_dir, "tuned")
    assert card["api_key"] == "sk-new"
    assert (card["token_max"], card["temperature"], card["tool_call_mode"]) == (999, 0.7, "native")
    assert card["enabled"] is False
    assert card["render_mode"] == "full"


def test_defaults_apply_only_to_a_brand_new_card(cards_dir):
    _put("fresh", {"provider": "Vendor", "model_name": "m"})

    card = _read(cards_dir, "fresh")
    assert card["api_protocol"] == "openai_compat"
    assert card["token_max"] == 128000
    assert card["tool_call_mode"] == "auto"
    assert card["enabled"] is True
    assert card["render_mode"] == "strict"


def test_explicit_false_is_not_swallowed_by_a_default(cards_dir):
    """``body.get(key, default)`` cannot tell False from missing -- the table can."""
    _put("c", {"provider": "V", "enabled": False, "is_think": False})

    card = _read(cards_dir, "c")
    assert card["enabled"] is False
    assert card["is_think"] is False
    # ... while untouched booleans still take their default
    assert card["is_image"] is False


# ── 3. identity / title ──


def test_name_is_forced_to_the_path_segment(cards_dir):
    _put("real-name", {"name": "hijacked", "provider": "V"})

    card = _read(cards_dir, "real-name")
    assert card["name"] == "real-name"
    assert not (cards_dir / "hijacked.json").exists()


def test_title_falls_back_to_card_name_only_when_absent(cards_dir):
    _put("no-title", {"provider": "V"})
    _put("with-title", {"provider": "V", "title": "Nice Name"})

    assert _read(cards_dir, "no-title")["title"] == "no-title"
    assert _read(cards_dir, "with-title")["title"] == "Nice Name"


def test_a_stored_title_is_not_replaced_by_the_card_name(cards_dir):
    _put("kept", {"provider": "V", "title": "Display"})
    _put("kept", {"api_key": "k"})

    assert _read(cards_dir, "kept")["title"] == "Display"


# ── 4. the field table must cover what we ship ──


def test_every_field_on_the_shipped_cards_is_in_the_table():
    """Drift detector: a shipped card using a field the table lacks would be
    written without a default when created fresh from the Agent Web.

    ``name`` is intentionally not a default (it comes from the URL segment).
    """
    missing: dict[str, set[str]] = {}
    for path in sorted(BUILTIN_CARDS_DIR.glob("*.json")):
        card = json.loads(path.read_text(encoding="utf-8"))
        extra = set(card) - set(_MODEL_CARD_DEFAULTS) - {"name"}
        if extra:
            missing[path.name] = extra
    assert not missing, f"shipped cards use fields missing from _MODEL_CARD_DEFAULTS: {missing}"


def test_image_generation_knobs_are_covered():
    """The concrete instance of the drift above (builtin ``step-image-edit-2``)."""
    assert {"image_size", "image_steps", "image_cfg_scale"} <= set(_MODEL_CARD_DEFAULTS)


# ── 5. live round-trip through the composed handler ──


def test_put_then_get_over_a_real_socket_keeps_unknown_fields(cards_dir, live_server, _no_launcher_token):
    base = f"http://127.0.0.1:{live_server}/api/model-cards"
    body = json.dumps(
        {
            "provider": "Mine",
            "model_name": "llm-x",
            "api_key": "sk-live",
            "extra_headers": {"X-Tenant": "acme"},
            "title": "LLM X",
        }
    ).encode("utf-8")

    with open_local(
        f"{base}/mine__llm-x",
        timeout=5,
        method="PUT",
        data=body,
        headers={"Content-Type": "application/json"},
    ) as resp:
        assert resp.status == 200
        assert json.loads(resp.read().decode("utf-8"))["ok"] is True

    # A second save that omits both unknown fields must not erase them.
    with open_local(
        f"{base}/mine__llm-x",
        timeout=5,
        method="PUT",
        data=json.dumps({"api_key": "sk-rotated"}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    ) as resp:
        assert resp.status == 200

    with open_local(f"{base}/mine__llm-x", timeout=5) as resp:
        card = json.loads(resp.read().decode("utf-8"))["card"]

    assert card["api_key"] == "sk-rotated"
    assert card["extra_headers"] == {"X-Tenant": "acme"}
    assert card["title"] == "LLM X"
    assert card["name"] == "mine__llm-x"
