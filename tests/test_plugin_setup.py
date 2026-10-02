"""Guided setup for the external-connection plugins.

The promise of the wizard is that a user can be walked through values that only exist
somewhere else (a provider console, a bot-father chat, an app-password page) and that the
thing is *proved* before the service is switched on. Two halves make that true, and both are
tested here:

* the **recipe** — declared in code next to the fields it fills, so it cannot describe a
  field the plugin does not read (that is checked directly), and carries the guidance and
  the field rules the wizard shows;
* the **verification** — a real call to the provider (`getMe`, `tenant_access_token`, an
  IMAP/SMTP login, a search), reported in one shape so the UI has one thing to render.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import plugins.setup_check as sc  # noqa: E402

PLUGINS_DIR = _SRC / "plugins"
GUIDED = {
    "feishu": "FeishuPlugin",
    "telegram": "TelegramPlugin",
    "email_assistant": "EmailAssistantPlugin",
    "bocha_search": "BochaSearchPlugin",
}


def _manifest(name: str) -> dict:
    return json.loads((PLUGINS_DIR / name / "plugin.json").read_text(encoding="utf-8"))


def _recipe(name: str) -> dict:
    return _manifest(name).get("setup") or {}


def _module(name: str):
    return importlib.import_module(f"plugins.{name}.query")


# ── the recipe describes the plugin's real fields ───────────────────────────


@pytest.mark.parametrize("name", sorted(GUIDED))
def test_every_guided_plugin_ships_a_recipe_that_matches_its_schema(name):
    manifest = _manifest(name)
    recipe = _recipe(name)
    schema = manifest.get("config_schema") or {}

    assert recipe.get("title"), f"{name}: the wizard needs a title"
    assert recipe.get("steps"), f"{name}: the wizard needs steps"

    bot_schema = (schema.get("bots") or {}).get("item_schema") or {}
    for step in recipe["steps"]:
        assert step.get("id") and step.get("title"), f"{name}: a step is missing id/title"
        for key in step.get("fields") or []:
            # A recipe that names a field the plugin does not read would be a wizard that
            # silently collects nothing — the exact drift this test exists to stop.
            assert key in schema, f"{name}/{step.get('id')}: unknown top-level field {key!r}"
        for key in step.get("bot_fields") or []:
            assert key in bot_schema, f"{name}/{step.get('id')}: unknown bot field {key!r}"


@pytest.mark.parametrize("name", sorted(GUIDED))
def test_the_recipe_tells_the_user_where_each_required_value_comes_from(name):
    manifest = _manifest(name)
    schema = manifest.get("config_schema") or {}
    bot_schema = (schema.get("bots") or {}).get("item_schema") or {}
    descriptors = {**schema, **bot_schema}

    required = [k for k, f in descriptors.items() if isinstance(f, dict) and f.get("required")]
    assert required, f"{name}: nothing is marked required, so the wizard cannot gate anything"
    for key in required:
        descriptor = descriptors[key]
        assert descriptor.get("label"), f"{name}/{key}: a required field needs a human label"
        assert descriptor.get("hint"), f"{name}/{key}: a required field needs 'where to get it'"

    # …and at least one step points at the page that issues the values, so the wizard can
    # offer "open the console" instead of asking the user to find it.
    assert any(isinstance(f, dict) and f.get("help_url") for f in descriptors.values()), (
        f"{name}: no field links to the page the user must click through"
    )


@pytest.mark.parametrize("name", sorted(GUIDED))
def test_the_recipe_ends_with_a_real_connection_test(name):
    recipe = _recipe(name)

    assert recipe.get("verify", {}).get("action") == "test_connection", (
        f"{name}: the last step must verify against the provider, not just save"
    )


@pytest.mark.parametrize("name", sorted(GUIDED))
def test_each_guided_plugin_can_answer_the_test(name):
    module = _module(name)

    assert hasattr(module, "handle_action"), f"{name}: the launcher dispatches actions here"
    result = module.handle_action(".", "test_connection", {})

    assert result["action"] == "test_connection"
    assert result["ok"] is False  # nothing configured yet — a refusal, not a crash
    assert result["checks"], "the wizard shows what was checked"


def test_an_unknown_action_is_still_reported(name="telegram"):
    assert "error" in _module(name).handle_action(".", "no_such_action", {})


# ── the recipe travels to the manifest (which is what the Launcher reads) ───


def test_generate_plugin_json_carries_the_recipe():
    from opensquad.plugin_api import generate_plugin_json

    module = importlib.import_module("plugins.telegram.plugin")
    generated = generate_plugin_json(module.TelegramPlugin)

    assert generated["setup"]["verify"]["action"] == "test_connection"
    assert generated["setup"]["steps"]


def test_a_hand_authored_recipe_survives_regeneration():
    """@register normally emits the recipe, but a manifest may declare one by hand —
    regeneration must not silently drop it."""
    from plugins.plugin_manager import merge_plugin_manifest

    merged = merge_plugin_manifest(
        {"setup": {"steps": [{"id": "x", "title": "hand written"}]}},
        {"name": "p", "setup": {}, "config": {"schema": {}}},
    )

    assert merged["setup"]["steps"][0]["title"] == "hand written"


# ── read_config: the wizard tests what is on screen ─────────────────────────


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    """A workspace with a system_config.json, and no ambient config shadowing it."""
    (tmp_path / "system_config.json").write_text(
        json.dumps({"telegram": {"bots": [{"bot_token": "stored"}]}}), encoding="utf-8"
    )
    from opensquad.system_config import syscfg

    monkeypatch.setattr(syscfg, "config_path", str(tmp_path / "nowhere.json"), raising=False)
    return tmp_path


def test_form_values_win_over_stored_ones(workspace):
    cfg = sc.read_config(str(workspace), "telegram", section="telegram", overrides={"bots": [{"bot_token": "typed"}]})

    assert cfg["bots"][0]["bot_token"] == "typed"


def test_the_stored_config_is_used_when_the_form_sends_nothing(workspace):
    cfg = sc.read_config(str(workspace), "telegram", section="telegram")

    assert cfg["bots"][0]["bot_token"] == "stored"


def test_a_tool_plugin_reads_its_own_config_file(tmp_path, monkeypatch):
    from opensquad.system_config import syscfg

    monkeypatch.setattr(syscfg, "config_path", str(tmp_path / "nowhere.json"), raising=False)
    target = tmp_path / "data" / "plugins" / "bocha_search"
    target.mkdir(parents=True)
    (target / "config.json").write_text(json.dumps({"api_key": "k"}), encoding="utf-8")

    assert sc.read_config(str(tmp_path), "bocha_search")["api_key"] == "k"
    assert sc.read_config(str(tmp_path), "absent_plugin") == {}


# ── one result shape for every service ──────────────────────────────────────


def test_a_missing_value_is_a_refusal_never_a_guess():
    result = sc.missing(names=["app_id", "app_secret"])

    assert result["ok"] is False
    assert result["error"] == "missing_fields"
    assert "app_id" in result["detail"] and "app_secret" in result["detail"]
    assert [c["ok"] for c in result["checks"]] == [False, False]


def test_the_first_failing_probe_leads_the_message():
    result = sc.result(
        checks=[sc.check("IMAP", True, "ok"), sc.check("SMTP", False, "auth failed", "use an app password")]
    )

    assert result["ok"] is False
    assert result["detail"].startswith("SMTP")
    assert result["hint"] == "use an app password"
    assert len(result["checks"]) == 2


def test_all_probes_passing_is_success():
    result = sc.result(checks=[sc.check("IMAP", True, "已连接")])

    assert result["ok"] is True and result["error"] == ""
    assert result["detail"] == "已连接"


# ── telegram: the token is checked by calling getMe ─────────────────────────


def test_telegram_refuses_a_token_that_is_not_a_token(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(sc, "http_json", lambda *a, **k: calls.append("called") or (200, {}))

    result = _module("telegram").handle_action(".", "test_connection", {"config": {"bots": [{"bot_token": "hello"}]}})

    assert result["ok"] is False and "格式" in result["detail"]
    assert calls == []  # obviously wrong: do not ask the provider


def test_telegram_reports_the_bot_it_reached(monkeypatch):
    monkeypatch.setattr(sc, "http_json", lambda url, **k: (200, {"ok": True, "result": {"username": "open_squad_bot"}}))

    result = _module("telegram").handle_action(
        ".", "test_connection", {"config": {"bots": [{"bot_token": "123456789:" + "A" * 35}]}}
    )

    assert result["ok"] is True and "@open_squad_bot" in result["detail"]


def test_telegram_turns_a_rejected_token_into_what_to_do(monkeypatch):
    monkeypatch.setattr(sc, "http_json", lambda url, **k: (401, {"description": "Unauthorized"}))

    result = _module("telegram").handle_action(
        ".", "test_connection", {"config": {"bots": [{"bot_token": "123456789:" + "A" * 35}]}}
    )

    assert result["ok"] is False
    assert "Unauthorized" in result["detail"]  # the provider's own words
    assert "BotFather" in result["hint"]


def test_telegram_sends_the_proxy_it_was_given(monkeypatch):
    seen: dict = {}

    def _fake(url, **kwargs):
        seen.update(kwargs)
        return 200, {"ok": True, "result": {"username": "b"}}

    monkeypatch.setattr(sc, "http_json", _fake)

    _module("telegram").handle_action(
        ".",
        "test_connection",
        {"config": {"proxy": "http://127.0.0.1:7890", "bots": [{"bot_token": "123456789:" + "A" * 35}]}},
    )

    assert seen.get("proxy") == "http://127.0.0.1:7890"


# ── feishu: credentials are checked by exchanging them ──────────────────────


def test_feishu_exchanges_the_credentials_for_a_token(monkeypatch):
    monkeypatch.setattr(sc, "http_json", lambda url, **k: (200, {"code": 0, "tenant_access_token": "t"}))

    result = _module("feishu").handle_action(
        ".", "test_connection", {"config": {"bots": [{"app_id": "cli_x", "app_secret": "s"}]}}
    )

    assert result["ok"] is True


def test_feishu_passes_on_its_own_error_message(monkeypatch):
    monkeypatch.setattr(sc, "http_json", lambda url, **k: (200, {"code": 10003, "msg": "invalid app_secret"}))

    result = _module("feishu").handle_action(
        ".", "test_connection", {"config": {"bots": [{"app_id": "cli_x", "app_secret": "wrong"}]}}
    )

    assert result["ok"] is False
    assert "invalid app_secret" in result["detail"]
    assert "重新获取" in result["hint"]


def test_feishu_flags_a_value_that_is_not_an_app_id():
    result = _module("feishu").handle_action(
        ".", "test_connection", {"config": {"bots": [{"app_id": "1234", "app_secret": "s"}]}}
    )

    assert result["ok"] is False
    assert "App ID" in result["detail"]
    # the guidance names the shape, so a user can see what they pasted wrong
    assert "cli_" in result["checks"][0]["hint"]


# ── email: a real login, and nothing sent ───────────────────────────────────


class _FakeImap:
    def __init__(self, host=None, port=None):
        self.host, self.port = host, port
        self.logged_in = None

    def login(self, user, password):
        self.logged_in = (user, password)
        if password == "bad":
            import imaplib

            raise imaplib.IMAP4.error("AUTHENTICATIONFAILED")
        return ("OK", [b""])

    def select(self, mailbox, readonly=False):
        return ("OK", [b"1"]) if mailbox.upper() != "MISSING" else ("NO", [b""])

    def logout(self):
        return ("BYE", [b""])


class _FakeSmtp:
    def __init__(self, host=None, port=None, timeout=None):
        self.host, self.port = host, port

    def login(self, user, password):
        if password == "bad":
            import smtplib

            raise smtplib.SMTPAuthenticationError(535, b"bad credentials")
        return (235, b"ok")

    def quit(self):
        return (221, b"bye")


def test_email_logs_in_to_imap_and_smtp(monkeypatch):
    import imaplib
    import smtplib

    monkeypatch.setattr(imaplib, "IMAP4_SSL", _FakeImap)
    monkeypatch.setattr(smtplib, "SMTP_SSL", _FakeSmtp)

    result = _module("email_assistant").handle_action(
        ".",
        "test_connection",
        {
            "config": {
                "imap_host": "imap.example.com",
                "smtp_host": "smtp.example.com",
                "username": "me@example.com",
                "password": "app-password",
            }
        },
    )

    assert result["ok"] is True
    assert [c["name"] for c in result["checks"]] == ["IMAP 登录", "SMTP 登录"]


def test_email_explains_that_a_login_password_will_not_do(monkeypatch):
    import imaplib

    monkeypatch.setattr(imaplib, "IMAP4_SSL", _FakeImap)

    result = _module("email_assistant").handle_action(
        ".",
        "test_connection",
        {"config": {"imap_host": "imap.example.com", "username": "me@example.com", "password": "bad"}},
    )

    assert result["ok"] is False
    assert "授权码" in result["hint"] or "应用专用密码" in result["hint"]


def test_email_reports_a_mailbox_it_cannot_open(monkeypatch):
    import imaplib

    monkeypatch.setattr(imaplib, "IMAP4_SSL", _FakeImap)

    result = _module("email_assistant").handle_action(
        ".",
        "test_connection",
        {
            "config": {
                "imap_host": "imap.example.com",
                "imap_mailbox": "Missing",
                "username": "me@example.com",
                "password": "ok",
            }
        },
    )

    assert result["ok"] is False and "Missing" in result["detail"]


def test_email_tests_imap_alone_when_no_smtp_is_configured(monkeypatch):
    import imaplib

    monkeypatch.setattr(imaplib, "IMAP4_SSL", _FakeImap)

    result = _module("email_assistant").handle_action(
        ".",
        "test_connection",
        {"config": {"imap_host": "imap.example.com", "username": "me@example.com", "password": "ok"}},
    )

    assert result["ok"] is True
    assert [c["name"] for c in result["checks"]] == ["IMAP 登录"]


def test_email_says_which_required_value_is_absent():
    result = _module("email_assistant").handle_action(
        ".", "test_connection", {"config": {"username": "me@example.com"}}
    )

    assert result["ok"] is False and result["error"] == "missing_fields"


# ── bocha: a key is real only if the provider accepts it ────────────────────


def test_bocha_refuses_without_a_key():
    result = _module("bocha_search").handle_action(
        ".", "test_connection", {"config": {"api_key": "", "base_url": "https://api.bocha.cn"}}
    )

    assert result["ok"] is False and "api_key" in result["detail"]


def test_bocha_reports_a_working_key(monkeypatch):
    seen: dict = {}

    def _fake(url, **kwargs):
        seen.update({"url": url, **kwargs})
        return 200, {"code": 200, "data": {}}

    monkeypatch.setattr(sc, "http_json", _fake)

    result = _module("bocha_search").handle_action(
        ".", "test_connection", {"config": {"api_key": "sk-1", "base_url": "https://api.bocha.cn"}}
    )

    assert result["ok"] is True
    assert seen["url"] == "https://api.bocha.cn/v1/web-search"
    assert seen["headers"]["Authorization"] == "Bearer sk-1"


def test_bocha_turns_a_rejected_key_into_what_to_do(monkeypatch):
    monkeypatch.setattr(sc, "http_json", lambda url, **k: (401, {"message": "invalid api key"}))

    result = _module("bocha_search").handle_action(".", "test_connection", {"config": {"api_key": "sk-bad"}})

    assert result["ok"] is False
    assert "invalid api key" in result["detail"]
    assert "API KEY 管理" in result["hint"]


# ── the launcher surfaces all of it ─────────────────────────────────────────


def test_the_config_endpoint_hands_the_recipe_to_the_ui():
    source = (_SRC / "opensquad" / "launcher" / "management_api" / "_plugins.py").read_text(encoding="utf-8")

    assert 'setup = meta.get("setup", {}) or {}' in source
    assert '"setup": setup,' in source
    # …and the plugin list says which cards get the wizard entry
    assert '"has_setup": bool(meta.get("setup"))' in source


def test_verify_result_is_json_serialisable():
    result = sc.result(checks=[sc.check("x", True, "ok")])

    assert json.loads(json.dumps(result))["ok"] is True


def test_a_transport_failure_is_reported_with_a_hint(monkeypatch):
    def _boom(url, **kwargs):
        raise OSError("connection refused")

    monkeypatch.setattr(sc, "http_json", _boom)

    result = _module("telegram").handle_action(
        ".", "test_connection", {"config": {"bots": [{"bot_token": "123456789:" + "A" * 35}]}}
    )

    assert result["ok"] is False
    assert "OSError" in result["detail"]
    assert "代理" in result["hint"] or "网络" in result["hint"]


def test_the_failure_shape_never_raises_at_the_user():
    """A check helper that explodes would take the whole action down; it returns instead."""
    out = sc.failed("probe", ValueError("bad"), "check the value")

    assert out["ok"] is False and out["hint"] == "check the value"
    assert isinstance(out, dict) and SimpleNamespace(**out).name == "probe"
