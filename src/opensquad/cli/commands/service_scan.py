"""Shared service discovery: auto-detect plugin services and their ports."""

import json
import os
import socket


def _port_listening(host, port, timeout=1.0):
    try:
        s = socket.create_connection((host, port), timeout=timeout)
        s.close()
        return True
    except (TimeoutError, ConnectionRefusedError, OSError):
        return False


def discover_all_services():
    """Return list of (display_name, port) for all services including plugin ones.
    Automatically scans plugins/ for services with configured ports."""
    from opensquad.system_config import syscfg

    services = [
        # Core infrastructure (always present)
        ("Gateway", syscfg.port("gateway")),
        ("Registry", syscfg.port("plugin_registry")),
        ("Frontend", syscfg.port("frontend")),
        ("Launcher", syscfg.port("launcher")),
        ("External API", syscfg.port("external_adapter")),
    ]

    # Auto-discover plugin services
    plugins_dir = os.path.join(syscfg.get_builtin_root(), "plugins")
    if not os.path.isdir(plugins_dir):
        return services

    seen_ports = {port for _, port in services}

    for name in sorted(os.listdir(plugins_dir)):
        pj = os.path.join(plugins_dir, name, "plugin.json")
        if not os.path.isfile(pj):
            continue
        try:
            with open(pj, encoding="utf-8-sig") as f:
                meta = json.load(f)
        except Exception:
            continue

        # Resolve port: try service.port_key → service.default_port → config.default_port
        svc_cfg = meta.get("service")
        port = None

        if svc_cfg:
            port_key = svc_cfg.get("port_key", "")
            port = syscfg.port(port_key) if port_key else svc_cfg.get("default_port")

        # Fallback: some plugins (e.g. websearch) use config_schema.default_port
        if port is None:
            config_default = meta.get("config", {}).get("schema", {}).get("port", {}).get("default")
            if config_default:
                port = config_default

        if port is None or port in seen_ports:
            continue
        seen_ports.add(port)

        display = meta.get("display_name") or meta.get("name") or name
        services.append((display, port))

    return services


def discover_plugin_status():
    """Return list of (name, display, enabled) for all plugins with service_toggle.
    Automatically reads plugin.json and checks system_config.json services section.
    Returns: list of dicts with keys: name, display, toggle (True/False), configured (bool)
    """
    from opensquad.system_config import syscfg

    plugins_dir = os.path.join(syscfg.get_builtin_root(), "plugins")
    if not os.path.isdir(plugins_dir):
        return []

    cfg_path = os.path.join(syscfg.get_workspace(), "system_config.json")
    svc_cfg = {}
    if os.path.isfile(cfg_path):
        try:
            with open(cfg_path, encoding="utf-8-sig") as f:
                svc_cfg = json.load(f).get("services", {})
        except Exception:
            pass

    result = []
    for name in sorted(os.listdir(plugins_dir)):
        pj = os.path.join(plugins_dir, name, "plugin.json")
        if not os.path.isfile(pj):
            continue
        try:
            with open(pj, encoding="utf-8-sig") as f:
                meta = json.load(f)
        except Exception:
            continue

        # Only show plugins that have service_toggle or a service entry
        has_toggle = meta.get("service_toggle", False)
        has_service = bool(meta.get("service"))
        if not has_toggle and not has_service:
            continue

        display = meta.get("display_name") or meta.get("name") or name
        entry = svc_cfg.get(name, {})
        configured = isinstance(entry, dict) and "enabled" in entry
        enabled = entry.get("enabled", True) if configured else True  # default True for unconfigured

        result.append(
            {
                "name": name,
                "display": display,
                "toggle": has_toggle,
                "enabled": enabled,
                "configured": configured,
            }
        )
    return result


def check_services(services, host="127.0.0.1"):
    """Return (ok_list, down_list) for a list of (name, port)."""
    ok = []
    down = []
    for name, port in services:
        if _port_listening(host, port):
            ok.append((name, port))
        else:
            down.append((name, port))
    return ok, down
