"""Update hosts.gateway in workspace system_config.json if a workspace is configured.

Usage:
    python update_workspace_config.py <gateway_ip>

Example:
    python update_workspace_config.py 127.0.0.1
    python update_workspace_config.py your-gateway-host
"""
import json
import os
import sys


def main():
    if len(sys.argv) < 2:
        print("Usage: update_workspace_config.py <gateway_ip>")
        sys.exit(1)

    gateway_ip = sys.argv[1]

    ws_file = os.path.join(os.path.expanduser("~"), ".opensquad", "last_workspace.json")
    if not os.path.exists(ws_file):
        print("[--] no last_workspace.json, workspace config skipped")
        return

    with open(ws_file, "r", encoding="utf-8") as f:
        ws_path = json.load(f).get("last_workspace", "")

    if not ws_path:
        print("[--] last_workspace.json has no last_workspace key, skipped")
        return

    cfg_path = os.path.join(ws_path, "system_config.json")
    if not os.path.exists(cfg_path):
        print("[--] workspace config not found: " + cfg_path)
        return

    with open(cfg_path, "r", encoding="utf-8-sig") as f:
        cfg = json.load(f)

    cfg.setdefault("hosts", {})["gateway"] = gateway_ip

    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)

    print("[OK] workspace config updated: hosts.gateway = " + gateway_ip + " (" + cfg_path + ")")


if __name__ == "__main__":
    main()
