"""Boot auto-start must read the same flag the Auto toggle writes.

The Service Manager's "Auto" chip/toggle is bound to
``services.<id>.enabled`` in system_config.json (PUT
``/api/plugin-services/{id}/auto-start`` → ``_set_service_enabled_in_config``,
and Start/Stop persist the same key). The boot loop instead gated on
``psp.auto_start`` — the *plugin manifest's* ``service.auto_start`` default.

For SenseVoice those disagree (manifest ships ``auto_start: false``, the
workspace has ``services.sensevoice.enabled: true``), so the launcher booted
with::

    - sensevoice (auto_start=False)
    [Launcher] Auto-starting 4 plugin service(s) in parallel: external_api, feishu, telegram, websearch

…while the UI showed Auto on. The user's report: "服务应该在启动 opensquad 时
就自动启动，但是每次都没有自动启动".

  R1  ``resolve_auto_start`` priority: explicit ``services.X.enabled`` >
      manifest ``service.auto_start`` > True;
  R2  ``PluginServiceProcess._resolve_auto_start`` — the method the boot loop
      calls — delegates to it (so status/UI and boot cannot drift again);
  R3  the boot loop in launcher_main consults the resolver, not the raw
      manifest flag, and the discovery listing logs the resolved value (the
      manifest value is what made the log mislead);
  R4  ``service_enabled_explicit`` can say "not configured" — which
      ``is_service_enabled`` (default True) cannot, and that distinction is
      what lets a manifest default stay authoritative until the user opts out.

Mutations verified:
  MA1 boot loop back to `if psp.auto_start:`            → R3
  MA2 priority: manifest read before the explicit value → R1
  MA3 listing logs the manifest value again             → R3
  MA4 `_load`-missing key returns True instead of None  → R4
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from opensquad import launcher_main as lm
from opensquad.launcher import process_manager as pm

SRC_ROOT = Path(pm.__file__).resolve().parents[2]
OPEN_SQUAD = Path(pm.__file__).resolve().parents[1]
LAUNCHER_MAIN_SRC = Path(lm.__file__).resolve().read_text(encoding="utf-8")

MANIFEST_DEFAULT_OFF = {"entry": "service/service.py", "auto_start": False}
MANIFEST_DEFAULT_ON = {"entry": "service/service.py", "auto_start": True}


@pytest.fixture
def explicit(monkeypatch):
    """Stub the system_config lookup; returns a setter for the explicit value."""

    def _set(value):
        monkeypatch.setattr(pm.syscfg, "service_enabled_or_none", lambda _pid: value)

    return _set


# ── R1 ─────────────────────────────────────────────────────────────────────


def test_user_toggle_wins_over_a_manifest_defaulting_off(explicit):
    """The SenseVoice case: manifest says off, the workspace toggle says on."""
    explicit(True)

    assert pm.resolve_auto_start("sensevoice", MANIFEST_DEFAULT_OFF) is True


def test_user_opt_out_wins_over_a_manifest_defaulting_on(explicit):
    explicit(False)

    assert pm.resolve_auto_start("websearch", MANIFEST_DEFAULT_ON) is False


def test_manifest_default_applies_until_the_user_has_an_opinion(explicit):
    explicit(None)

    assert pm.resolve_auto_start("sensevoice", MANIFEST_DEFAULT_OFF) is False
    assert pm.resolve_auto_start("websearch", MANIFEST_DEFAULT_ON) is True


def test_last_resort_is_on_when_neither_source_says_anything(explicit):
    explicit(None)

    assert pm.resolve_auto_start("mystery", {"entry": "service/main.py"}) is True


# ── R2 ─────────────────────────────────────────────────────────────────────


def test_process_wrapper_resolves_through_the_shared_helper(explicit, monkeypatch):
    explicit(True)
    psp = pm.PluginServiceProcess(
        "sensevoice",
        str(SRC_ROOT / "plugins" / "sensevoice"),
        dict(MANIFEST_DEFAULT_OFF),
    )

    # Same inputs as the status endpoint and the boot loop.
    assert psp._resolve_auto_start() is True


def test_status_and_boot_cannot_drift(explicit, monkeypatch):
    """`get_status()` and the boot loop must call the one resolver."""
    explicit(True)
    psp = pm.PluginServiceProcess("sensevoice", str(SRC_ROOT / "plugins" / "sensevoice"), dict(MANIFEST_DEFAULT_OFF))

    assert psp.get_status()["auto_start"] is True
    assert psp._resolve_auto_start() is True


# ── R3 ─────────────────────────────────────────────────────────────────────


def test_boot_loop_uses_the_resolved_value():
    assert "psp._resolve_auto_start()" in LAUNCHER_MAIN_SRC
    assert "if psp.auto_start:" not in LAUNCHER_MAIN_SRC


def test_boot_listing_reports_the_resolved_value():
    """The listing is the line a human reads when auto-start "doesn't work"."""
    assert 'resolve_auto_start(info["plugin_id"], info["service_cfg"])' in LAUNCHER_MAIN_SRC
    assert 'auto = info["service_cfg"].get("auto_start", False)' not in LAUNCHER_MAIN_SRC


def test_no_services_flag_still_skips_auto_start():
    """Frozen-bundle safe mode must keep working (spawning would re-enter the EXE)."""
    assert "skip_auto_start" in LAUNCHER_MAIN_SRC


# ── R4 ─────────────────────────────────────────────────────────────────────


def _config_module():
    return importlib.import_module("opensquad._syscfg._config")


def test_explicit_helper_separates_unset_from_false(monkeypatch):
    mod = _config_module()
    monkeypatch.setattr(mod, "_load", lambda: {"services": {"sensevoice": {"enabled": True}}})

    assert mod.service_enabled_explicit("sensevoice") is True

    monkeypatch.setattr(mod, "_load", lambda: {"services": {"sensevoice": {"enabled": False}}})

    assert mod.service_enabled_explicit("sensevoice") is False

    # Never configured at all — the case is_service_enabled cannot express.
    monkeypatch.setattr(mod, "_load", lambda: {"services": {}})

    assert mod.service_enabled_explicit("sensevoice") is None
    assert mod.is_service_enabled("sensevoice") is True


def test_public_wrapper_is_wired_to_the_explicit_helper(monkeypatch):
    import opensquad.system_config as syscfg_pub

    mod = _config_module()
    monkeypatch.setattr(mod, "_load", lambda: {"services": {"sensevoice": {"enabled": False}}})

    assert syscfg_pub.service_enabled_or_none("sensevoice") is False


# ── the toggle side of the contract ────────────────────────────────────────


def test_auto_toggle_persists_the_key_the_launcher_reads():
    src = (OPEN_SQUAD / "launcher" / "management_api" / "_plugin_services.py").read_text(encoding="utf-8")
    handler = src[src.index("def _handle_plugin_service_auto_start") :]
    handler = handler[: handler.index("def ", 10)]

    assert "self._set_service_enabled_in_config(plugin_id, enabled)" in handler
    assert 'full_cfg["services"][plugin_id]["enabled"] = enabled' in src


def test_list_endpoint_reports_the_same_resolved_value():
    """A service that has never been registered still reports what boot would do."""
    src = (OPEN_SQUAD / "launcher" / "management_api" / "_plugin_services.py").read_text(encoding="utf-8")

    assert '"auto_start": resolve_auto_start(pid, info.get("service_cfg", {}) or {})' in src
    assert '"auto_start": syscfg.is_service_enabled(pid)' not in src
