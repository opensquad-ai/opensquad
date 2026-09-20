"""The Service Manager can uninstall a service, so /api/services/manage must say
which services are allowed to be uninstalled.

A service has no lifecycle of its own -- it is a process its plugin starts -- so
"uninstall this service" is "uninstall the owning plugin", served by
``DELETE /admin/plugins/{id}``. That route rejects ``builtin_plugins.json``
entries (websearch, vision, ...) through ``resource_uninstall``'s
``prepare_plugin_uninstall``. Without a protection flag in the service payload
the UI can only render the button and let the click fail with HTTP 400, so the
launcher resolves the flag while building the list.

These tests pin the flag on both branches of the merge (registered in
``_plugin_services`` vs. discovered-but-never-started), because the two branches
build their payload in different places and only one shared line adds the flag.
"""

import json
import threading

import pytest

import opensquad.launcher.management_api._plugin_services as plugin_services
import opensquad.resource_uninstall as resource_uninstall
from opensquad.launcher.management_api import ExclusiveHTTPServer, ManagementHandler
from opensquad.utils.local_http import open_local

BUILTIN_ID = "websearch"
USER_ID = "step_voice"


@pytest.fixture
def _no_launcher_token(monkeypatch):
    """Remove any ambient launcher token so the requests below are unauthenticated."""
    monkeypatch.setattr(ManagementHandler, "_get_launcher_token", staticmethod(lambda: ""))


@pytest.fixture
def live_server():
    """A real ``ExclusiveHTTPServer`` on an ephemeral port, serving in a thread."""
    server = ExclusiveHTTPServer(("127.0.0.1", 0), ManagementHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _fake_info(plugin_id: str, plugin_type: str = "tool") -> dict:
    """One entry shaped like ``discover_plugin_services`` output."""
    return {
        "plugin_id": plugin_id,
        "display_name": plugin_id.title(),
        "plugin_type": plugin_type,
        "plugin_dir": f"/nonexistent/{plugin_id}",
        "service_cfg": {"host": "0.0.0.0", "default_port": 0, "health_endpoint": "/health"},
    }


class _FakeProcess:
    """The registered-branch stub: only ``get_status`` is used by the merge."""

    def __init__(self, plugin_id: str):
        self.plugin_id = plugin_id

    def get_status(self) -> dict:
        return {
            "plugin_id": self.plugin_id,
            "display_name": self.plugin_id.title(),
            "plugin_type": "platform",
            "alive": True,
            "pid": 4321,
            "port": 0,
            "host": "0.0.0.0",
            "auto_start": False,
            "should_run": True,
            "restart_count": 0,
            "max_restarts": 5,
            "started_at": None,
            "uptime_seconds": 12.0,
            "health_endpoint": "/health",
            "health_ok": None,
            "service_cfg": {"host": "0.0.0.0", "default_port": 0},
        }


@pytest.fixture
def _isolated_discovery(monkeypatch):
    """Deterministic service list: one built-in, one user plugin, one registered.

    ``is_protected_plugin`` is stubbed rather than pointed at a real
    ``builtin_plugins.json`` so the test states the contract ("protected ids come
    back as builtin=True") instead of depending on which workspace it runs in.
    """
    infos = [_fake_info(BUILTIN_ID), _fake_info(USER_ID)]
    monkeypatch.setattr(plugin_services, "discover_all_plugin_services", lambda: infos)
    monkeypatch.setattr(
        resource_uninstall,
        "is_protected_plugin",
        lambda name: name == BUILTIN_ID,
    )
    monkeypatch.setitem(plugin_services._plugin_services, USER_ID, _FakeProcess(USER_ID))


def _fetch_services(port: int) -> list[dict]:
    url = f"http://127.0.0.1:{port}/api/services/manage"
    with open_local(url, timeout=5) as resp:
        assert resp.status == 200
        return json.loads(resp.read().decode("utf-8"))["services"]


def test_every_service_carries_a_boolean_builtin_flag(live_server, _no_launcher_token, _isolated_discovery):
    """The UI reads ``!svc.builtin`` to enable the button, so the field must be
    present on EVERY entry -- a missing key reads as ``undefined``, i.e. as
    "uninstallable", which is the opposite of the safe default."""
    services = _fetch_services(live_server)

    assert services, "stubbed discovery returned nothing"
    for svc in services:
        assert "builtin" in svc, f"{svc.get('plugin_id')} is missing the builtin flag"
        assert isinstance(svc["builtin"], bool)


def test_builtin_flag_follows_the_uninstall_endpoint_protection(live_server, _no_launcher_token, _isolated_discovery):
    """The flag must equal what the uninstall route enforces, on both branches."""
    by_id = {svc["plugin_id"]: svc for svc in _fetch_services(live_server)}

    # Discovered-only branch (never started, payload built inline).
    assert by_id[BUILTIN_ID]["builtin"] is True
    # Registered branch (payload comes from PluginServiceProcess.get_status()).
    assert by_id[USER_ID]["builtin"] is False
    assert by_id[USER_ID]["alive"] is True, "registered branch was not exercised"


def test_real_builtin_registry_marks_websearch_protected():
    """Anchor the stub above to the real registry: websearch really is built-in.

    Without this, the flag could stay green while ``builtin_plugins.json``
    stopped covering the one plugin that is both built-in and a service.
    """
    assert BUILTIN_ID in resource_uninstall.protected_plugin_ids()
    assert resource_uninstall.is_protected_plugin(BUILTIN_ID) is True
