# -*- coding: utf-8 -*-
"""opensquad config — Validate and inspect configuration."""
import json
import os
import sys


REQUIRED_SECTIONS = ["hosts", "ports", "auth"]
KNOWN_PORTS = ["gateway", "launcher", "plugin_registry", "frontend", "external_adapter", "legacy_server"]
KNOWN_HOSTS = ["gateway", "launcher", "external_adapter", "legacy_server", "frontend"]


def run_config(args):
    from opensquad.system_config import syscfg

    ws = syscfg.get_workspace()
    cfg_path = os.path.join(ws, "system_config.json")

    action = getattr(args, "action", "validate")

    if action == "show":
        if not os.path.isfile(cfg_path):
            print(f"[config] Config not found: {cfg_path}")
            sys.exit(1)
        with open(cfg_path, "r", encoding="utf-8-sig") as f:
            print(f.read())
        return

    # Default: validate
    print(f"[config] Validating {cfg_path}...")
    issues = []

    # 1. File exists
    if not os.path.isfile(cfg_path):
        print(f"  \u274C Config file not found: {cfg_path}")
        print(f"  \u2139 Run 'opensquad init' to create it")
        sys.exit(1)

    # 2. Valid JSON
    try:
        with open(cfg_path, "r", encoding="utf-8-sig") as f:
            cfg = json.load(f)
    except json.JSONDecodeError as e:
        print(f"  \u274C Invalid JSON: {e}")
        sys.exit(1)

    # 3. Required sections
    for section in REQUIRED_SECTIONS:
        if section not in cfg:
            issues.append(f"Missing required section: '{section}'")
            print(f"  \u274C Missing section: '{section}'")
        else:
            print(f"  \u2705 Section: '{section}'")

    # 4. Known ports
    ports = cfg.get("ports", {})
    for key in KNOWN_PORTS:
        if key in ports:
            print(f"  \u2705 Port '{key}': {ports[key]}")
        else:
            print(f"  \u26A0 Port '{key}' not set (default will be used)")

    # 5. Known hosts
    hosts = cfg.get("hosts", {})
    for key in KNOWN_HOSTS:
        if key in hosts:
            print(f"  \u2705 Host '{key}': {hosts[key]}")
        else:
            print(f"  \u26A0 Host '{key}' not set (default: 127.0.0.1)")

    # 6. Auth keys
    auth = cfg.get("auth", {})
    if auth.get("gateway_token") in (None, "YOUR_GATEWAY_TOKEN_HERE", "opensquad-gateway-simple-token", ""):
        issues.append("gateway_token is unset or using placeholder")
        print(f"  \u26A0 gateway_token: DEFAULT (insecure for production)")
    else:
        print(f"  \u2705 gateway_token: set")

    if auth.get("external_api_key") in (None, "YOUR_EXTERNAL_API_KEY_HERE", ""):
        issues.append("external_api_key is unset or using placeholder")
        print(f"  \u26A0 external_api_key: DEFAULT (insecure for production)")
    else:
        print(f"  \u2705 external_api_key: set")

    # 7. Plugin service configs
    svc = cfg.get("services", {})
    for name in ("feishu", "telegram", "external_api"):
        enabled = svc.get(name, {}).get("enabled")
        if enabled is True:
            print(f"  \u2705 Plugin '{name}': enabled")
        elif enabled is False:
            print(f"  \u2139 Plugin '{name}': disabled")
        else:
            print(f"  \u2139 Plugin '{name}': not configured")

    # Summary
    print()
    if issues:
        print(f"[config] \u26A0 {len(issues)} issue(s) found:")
        for i in issues:
            print(f"  - {i}")
    else:
        print(f"[config] \u2705 Configuration is valid.")
