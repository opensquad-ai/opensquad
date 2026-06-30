"""Regression tests for the auth-placeholder auto-generation path (issue #41).

A deployment tester reported that Feishu / Telegram / external_api chat
requests returned 502 with an opaque "Agent 'default-001' is not
available" error. The actual root cause was that ``auth.gateway_token``
and ``auth.node_secret`` in ``system_config.json`` were still the
placeholder values copied from ``system_config.example.json`` -- the
Gateway short-circuits placeholder tokens to the JWT decode path, which
fails and closes the connection, producing a 502 with no indication
that auth was the real problem.

The fix is two-sided:

* Layer A: ``init_workspace`` now auto-replaces placeholders with
  ``secrets.token_urlsafe(32)`` immediately after copying the template.
* Layer B: ``ensure_gateway_token()`` / ``ensure_node_secret()``
  (mirroring the existing ``ensure_external_api_key()``) auto-replace
  placeholders on first access -- the runtime fallback that fixes
  existing user workspaces that pre-date Layer A.

These tests pin the contract for both layers.
"""

from __future__ import annotations

import json
import uuid

import pytest

# ── _is_placeholder_secret ───────────────────────────────────────────────


def test_is_placeholder_recognises_canonical_values():
    from opensquad._syscfg._config import _is_placeholder_secret

    for value in (
        "",
        "YOUR_GATEWAY_TOKEN_HERE",
        "YOUR_NODE_SECRET_HERE",
        "YOUR_EXTERNAL_API_KEY_HERE",
        "opensquad-gateway-simple-token",
    ):
        assert _is_placeholder_secret(value) is True, f"expected placeholder: {value!r}"


def test_is_placeholder_rejects_real_secrets():
    from opensquad._syscfg._config import _is_placeholder_secret

    for value in (
        "abc123",
        "Rsunj6htdgt02gQbSxNu7KfgeNL3UJIy3gGVSednMes",
        uuid.uuid4().hex,
    ):
        assert _is_placeholder_secret(value) is False, f"expected real secret: {value!r}"


def test_is_placeholder_handles_non_strings():
    from opensquad._syscfg._config import _is_placeholder_secret

    assert _is_placeholder_secret(None) is False
    assert _is_placeholder_secret(0) is False
    assert _is_placeholder_secret(False) is False
    assert _is_placeholder_secret(["x"]) is False


# ── _auto_generate_secrets (Layer A) ──────────────────────────────────────


def test_auto_generate_secrets_replaces_all_three_placeholders(tmp_path):
    from opensquad._syscfg._config import _auto_generate_secrets

    target = tmp_path / "system_config.json"
    target.write_text(
        json.dumps(
            {
                "auth": {
                    "node_secret": "YOUR_NODE_SECRET_HERE",
                    "gateway_token": "YOUR_GATEWAY_TOKEN_HERE",
                    "external_api_key": "YOUR_EXTERNAL_API_KEY_HERE",
                },
                "services": {"feishu": {"enabled": True}},
            }
        ),
        encoding="utf-8",
    )

    replaced = _auto_generate_secrets(str(target))
    assert replaced is True

    data = json.loads(target.read_text(encoding="utf-8"))
    auth = data["auth"]
    # All three must be real secrets (not placeholders, not empty).
    assert auth["node_secret"] not in ("", "YOUR_NODE_SECRET_HERE")
    assert auth["gateway_token"] not in ("", "YOUR_GATEWAY_TOKEN_HERE")
    assert auth["external_api_key"] not in ("", "YOUR_EXTERNAL_API_KEY_HERE")
    # And they must differ from each other (independent random draws).
    assert len({auth["node_secret"], auth["gateway_token"], auth["external_api_key"]}) == 3
    # Non-auth keys must be preserved.
    assert data["services"]["feishu"]["enabled"] is True


def test_auto_generate_secrets_is_idempotent_for_real_values(tmp_path):
    """A workspace that already has real secrets must be left untouched."""
    from opensquad._syscfg._config import _auto_generate_secrets

    target = tmp_path / "system_config.json"
    real = {
        "node_secret": "real_node_secret_keep_me",
        "gateway_token": "real_gateway_token_keep_me",
        "external_api_key": "real_external_api_key_keep_me",
    }
    target.write_text(json.dumps({"auth": real}, ensure_ascii=False), encoding="utf-8")

    replaced = _auto_generate_secrets(str(target))
    assert replaced is False

    data = json.loads(target.read_text(encoding="utf-8"))
    assert data["auth"] == real  # untouched


def test_auto_generate_secrets_partial_placeholders(tmp_path):
    """If only some auth keys are placeholders, replace only those."""
    from opensquad._syscfg._config import _auto_generate_secrets

    target = tmp_path / "system_config.json"
    target.write_text(
        json.dumps(
            {
                "auth": {
                    "node_secret": "real_node_keep",
                    "gateway_token": "YOUR_GATEWAY_TOKEN_HERE",  # placeholder
                    "external_api_key": "real_ext_keep",
                },
            }
        ),
        encoding="utf-8",
    )

    replaced = _auto_generate_secrets(str(target))
    assert replaced is True

    data = json.loads(target.read_text(encoding="utf-8"))
    assert data["auth"]["node_secret"] == "real_node_keep"  # preserved
    assert data["auth"]["gateway_token"] not in ("", "YOUR_GATEWAY_TOKEN_HERE")  # replaced
    assert data["auth"]["external_api_key"] == "real_ext_keep"  # preserved


def test_auto_generate_secrets_handles_missing_auth_section(tmp_path):
    """If the file has no auth key, the helper creates one with fresh values."""
    from opensquad._syscfg._config import _auto_generate_secrets

    target = tmp_path / "system_config.json"
    target.write_text(json.dumps({"services": {"feishu": {}}}), encoding="utf-8")

    replaced = _auto_generate_secrets(str(target))
    assert replaced is True

    data = json.loads(target.read_text(encoding="utf-8"))
    assert "auth" in data
    assert data["auth"]["node_secret"]  # non-empty
    assert data["auth"]["gateway_token"]
    assert data["auth"]["external_api_key"]


def test_auto_generate_secrets_handles_corrupted_file(tmp_path, caplog):
    """A malformed JSON file must not crash the helper."""
    from opensquad._syscfg._config import _auto_generate_secrets

    target = tmp_path / "system_config.json"
    target.write_text("{this is not valid json", encoding="utf-8")

    replaced = _auto_generate_secrets(str(target))
    assert replaced is False
    # The file must be left as-is (we don't accidentally wipe it).
    assert target.read_text(encoding="utf-8") == "{this is not valid json"


# ── ensure_gateway_token / ensure_node_secret (Layer B) ───────────────────


@pytest.fixture
def isolated_workspace(tmp_path, monkeypatch):
    """Redirect syscfg to a fresh temp workspace for one test."""
    from opensquad._syscfg import _config
    from opensquad._syscfg import _workspace as _ws

    ws = tmp_path / "ws"
    ws.mkdir()
    cfg_path = ws / "system_config.json"
    cfg_path.write_text(
        json.dumps(
            {
                "auth": {
                    "node_secret": "YOUR_NODE_SECRET_HERE",
                    "gateway_token": "YOUR_GATEWAY_TOKEN_HERE",
                    "external_api_key": "YOUR_EXTERNAL_API_KEY_HERE",
                },
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(_ws, "_WORKSPACE_ROOT", str(ws), raising=False)
    monkeypatch.setattr(_ws, "_CONFIG_PATH", str(cfg_path), raising=False)
    monkeypatch.setattr(_config, "_cache", None)
    monkeypatch.setattr(_config, "_cache_mtime", 0.0)
    monkeypatch.setattr(_config, "_cache_path_at_load", "")

    return cfg_path


def test_ensure_gateway_token_replaces_placeholder(isolated_workspace):
    from opensquad._syscfg._config import ensure_gateway_token

    new_token = ensure_gateway_token()
    assert new_token
    assert new_token != "YOUR_GATEWAY_TOKEN_HERE"
    # Persisted to disk
    on_disk = json.loads(isolated_workspace.read_text(encoding="utf-8"))
    assert on_disk["auth"]["gateway_token"] == new_token
    # Other auth keys untouched in this call
    assert on_disk["auth"]["node_secret"] == "YOUR_NODE_SECRET_HERE"


def test_ensure_node_secret_replaces_placeholder(isolated_workspace):
    from opensquad._syscfg._config import ensure_node_secret

    new_secret = ensure_node_secret()
    assert new_secret
    assert new_secret != "YOUR_NODE_SECRET_HERE"
    on_disk = json.loads(isolated_workspace.read_text(encoding="utf-8"))
    assert on_disk["auth"]["node_secret"] == new_secret
    assert on_disk["auth"]["gateway_token"] == "YOUR_GATEWAY_TOKEN_HERE"  # untouched


def test_ensure_gateway_token_is_idempotent(isolated_workspace):
    from opensquad._syscfg._config import ensure_gateway_token

    first = ensure_gateway_token()
    second = ensure_gateway_token()
    assert first == second
    on_disk = json.loads(isolated_workspace.read_text(encoding="utf-8"))
    assert on_disk["auth"]["gateway_token"] == first


def test_ensure_external_api_key_still_uses_placeholder_check(isolated_workspace):
    """Regression: ensure_external_api_key must now use the same placeholder
    set as ensure_gateway_token / ensure_node_secret, so the
    "opensquad-gateway-simple-token" historical default also gets replaced.
    """
    from opensquad._syscfg._config import ensure_external_api_key

    # Pre-seed with the legacy 'opensquad-gateway-simple-token' value.
    cfg = json.loads(isolated_workspace.read_text(encoding="utf-8"))
    cfg["auth"]["external_api_key"] = "opensquad-gateway-simple-token"
    isolated_workspace.write_text(json.dumps(cfg), encoding="utf-8")

    new_key = ensure_external_api_key()
    assert new_key != "opensquad-gateway-simple-token"
    assert new_key  # real secret


def test_ensure_helpers_keep_real_secrets_untouched(isolated_workspace):
    """The ensures must NEVER overwrite a real secret with a fresh one."""
    from opensquad._syscfg._config import (
        ensure_external_api_key,
        ensure_gateway_token,
        ensure_node_secret,
    )

    cfg = json.loads(isolated_workspace.read_text(encoding="utf-8"))
    real_node = "real_node_KEEP"
    real_gw = "real_gw_KEEP"
    real_ext = "real_ext_KEEP"
    cfg["auth"] = {
        "node_secret": real_node,
        "gateway_token": real_gw,
        "external_api_key": real_ext,
    }
    isolated_workspace.write_text(json.dumps(cfg), encoding="utf-8")

    assert ensure_node_secret() == real_node
    assert ensure_gateway_token() == real_gw
    assert ensure_external_api_key() == real_ext

    on_disk = json.loads(isolated_workspace.read_text(encoding="utf-8"))
    assert on_disk["auth"] == {
        "node_secret": real_node,
        "gateway_token": real_gw,
        "external_api_key": real_ext,
    }
