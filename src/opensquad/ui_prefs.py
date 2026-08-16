"""Host-side Web UI preferences shared across browser origins / ports.

Vite (:5173) and the packaged Gateway (:9555 / Electron) are different
origins, so ``localStorage`` does not carry theme, language, or the last
agent. Persist those in the workspace so packaged and dev look the same.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

from opensquad.system_config import syscfg

UI_PREFS_FILENAME = "ui_prefs.json"
_PREF_KEYS = ("theme", "lang", "uiMode", "selectedAgent", "view")


def ui_prefs_path() -> str:
    data_dir = syscfg.workspace_data_dir()
    os.makedirs(data_dir, exist_ok=True)
    return os.path.join(data_dir, UI_PREFS_FILENAME)


def load_ui_prefs() -> dict[str, Any]:
    fp = ui_prefs_path()
    if not os.path.isfile(fp):
        return {"savedAt": 0}
    try:
        with open(fp, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {"savedAt": 0}
        return data
    except Exception:
        return {"savedAt": 0}


def merge_ui_prefs(existing: dict[str, Any] | None, incoming: dict[str, Any] | None) -> tuple[str, dict[str, Any]]:
    """Last-write-wins by savedAt; empty incoming fields do not wipe existing.

    Returns ``("ok"|"skipped", next_state)``.
    """
    existing = existing if isinstance(existing, dict) else {}
    incoming = incoming if isinstance(incoming, dict) else {}
    existing_at = int(existing.get("savedAt") or 0)
    incoming_at = int(incoming.get("savedAt") or 0)
    if existing_at and incoming_at and incoming_at < existing_at:
        return "skipped", existing

    next_state = dict(existing)
    for key in _PREF_KEYS:
        if key not in incoming:
            continue
        val = incoming.get(key)
        if val is None or val == "":
            continue
        next_state[key] = val
    next_state["savedAt"] = incoming_at or int(time.time() * 1000)
    next_state["version"] = 1
    return "ok", next_state


def save_ui_prefs(state: dict[str, Any]) -> dict[str, Any]:
    fp = ui_prefs_path()
    tmp = fp + ".tmp"
    payload = dict(state)
    payload.setdefault("version", 1)
    payload.setdefault("savedAt", int(time.time() * 1000))
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, fp)
    return payload


def merge_and_save_ui_prefs(incoming: dict[str, Any] | None) -> dict[str, Any]:
    status, next_state = merge_ui_prefs(load_ui_prefs(), incoming)
    if status == "ok":
        next_state = save_ui_prefs(next_state)
    out = dict(next_state)
    out["status"] = status
    return out


def snapshot_has_workspaces(snap: Any) -> bool:
    """True when an Agent Web chrome snapshot lists at least one workspace."""
    if not isinstance(snap, dict):
        return False
    workspaces = snap.get("workspaces")
    return isinstance(workspaces, list) and len(workspaces) > 0
