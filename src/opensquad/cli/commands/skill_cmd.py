"""opensquad skill — list / show / install / rm"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from opensquad.cli.api_client import GatewayClient, handle_api_error, print_json, print_table


def run_skill(args) -> None:
    action = getattr(args, "skill_action", None)
    if not action:
        print("[skill] Usage: opensquad skill {list|show|install|rm}")
        sys.exit(1)

    client = GatewayClient(gateway_url=getattr(args, "gateway", None))
    try:
        if action == "list":
            _list(client)
        elif action == "show":
            _show(client, args.name)
        elif action == "install":
            _install(client, args.path)
        elif action == "rm":
            _rm(client, args.name)
        else:
            print(f"[skill] Unknown action: {action}")
            sys.exit(1)
    except Exception as e:
        handle_api_error(e)
        print(f"[skill] {e}")
        sys.exit(1)


def _list(client: GatewayClient) -> None:
    data = client.admin_get("skills")
    skills = data.get("skills") or []
    rows = []
    for s in skills:
        rows.append(
            {
                "name": s.get("name") or s.get("dir") or "",
                "display": s.get("display_name") or "",
                "version": s.get("version") or "",
                "description": (s.get("description") or "")[:40],
            }
        )
    print_table(
        rows,
        [
            ("name", "NAME"),
            ("display", "DISPLAY"),
            ("version", "VER"),
            ("description", "DESCRIPTION"),
        ],
    )


def _show(client: GatewayClient, name: str) -> None:
    data = client.admin_get(f"skills/{name}/source")
    md = data.get("skill_md") or ""
    print(f"# Skill: {data.get('name') or name}\n")
    if md:
        print(md)
    else:
        print_json(data)


def _install(client: GatewayClient, path: str) -> None:
    root = Path(path).resolve()
    if not root.is_dir():
        print(f"[skill] Not a directory: {path}")
        sys.exit(1)
    # Upload paths include the skill folder name so Launcher preserves it.
    opened = []
    files = []
    try:
        for dirpath, _, filenames in os.walk(root):
            for fn in filenames:
                full = Path(dirpath) / fn
                rel = Path(root.name) / full.relative_to(root)
                fh = open(full, "rb")
                opened.append(fh)
                files.append(("files", (str(rel).replace("\\", "/"), fh)))
        if not files:
            print("[skill] No files found")
            sys.exit(1)
        client.require_auth()
        result = client.request(
            "POST",
            "/api/ai-web/admin/skills/upload",
            files=files,
            timeout=120.0,
        )
    finally:
        for fh in opened:
            try:
                fh.close()
            except Exception:
                pass
    print("[skill] installed")
    print_json(result)


def _rm(client: GatewayClient, name: str) -> None:
    result = client.admin_delete(f"skills/{name}")
    print(f"[skill] deleted: {name}")
    print_json(result)
