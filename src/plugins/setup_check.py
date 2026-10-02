"""Shared helpers for a plugin's guided-setup "test connection" action.

Every external-connection plugin answers the same question in the wizard — "are these
values actually usable?" — so they answer it in the same shape. The shape matters twice
over: the wizard renders one result component, and the tests can assert one contract:

    {
      "ok": bool,                 # every check passed
      "action": "test_connection",
      "detail": str,              # one line to show the user (success or the reason)
      "error": str,               # the machine-ish reason when it failed
      "hint": str,                # what to do about it
      "checks": [{"name": str, "ok": bool, "detail": str}],
    }

A check is one real call to the provider (Telegram `getMe`, Feishu
`tenant_access_token`, an IMAP login, an SMTP login, a search request). Nothing is
written: testing must not enable a half-configured service.

Two conventions keep this honest for every plugin:

* **The wizard tests what is on screen.** ``overrides`` (the form's current values, sent
  as ``data["config"]``) win over what is stored, so a token can be verified *before* it
  is saved. Without overrides, the stored config is used, so the same action doubles as a
  health check for an already-configured service.
* **No credentials invented.** A missing required value is reported as such, never
  filled with a placeholder.
"""

from __future__ import annotations

import json
import os
from typing import Any

CHECK_TIMEOUT_S = 8.0


def read_config(
    project_root: str,
    plugin_name: str,
    *,
    section: str = "",
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The config to test: stored values, with the wizard's unsaved form values on top.

    Tool/hook plugins store theirs under ``data/plugins/<name>/config.json``; a platform
    plugin (``section``) bridges into ``system_config.json`` as ``{section: {…}}``. Both
    are read here so one helper serves every plugin.
    """
    stored: dict[str, Any] = {}
    if section:
        for path in _system_config_candidates(project_root):
            try:
                with open(path, encoding="utf-8") as fh:
                    data = json.load(fh)
            except Exception:
                continue
            block = data.get(section) if isinstance(data, dict) else None
            if isinstance(block, dict):
                stored = dict(block)
                break
    else:
        path = os.path.join(project_root, "data", "plugins", plugin_name, "config.json")
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                stored = data
        except Exception:
            stored = {}

    if isinstance(overrides, dict):
        merged = dict(stored)
        for key, value in overrides.items():
            # A list of bots is replaced wholesale: the wizard sends the whole list, and
            # merging them row-by-row would test a mixture of old and new credentials.
            merged[key] = value
        return merged
    return stored


def _system_config_candidates(project_root: str) -> list[str]:
    """Where ``system_config.json`` may live (workspace first, then the install root)."""
    out = [os.path.join(project_root, "system_config.json")]
    try:
        from opensquad.system_config import syscfg

        path = getattr(syscfg, "config_path", "")
        if path:
            out.insert(0, str(path))
    except Exception:
        pass
    return [p for p in dict.fromkeys(out) if p]


def check(name: str, ok: bool, detail: str = "", hint: str = "") -> dict[str, Any]:
    """One probe's outcome."""
    out: dict[str, Any] = {"name": str(name), "ok": bool(ok), "detail": str(detail or "")}
    if hint:
        out["hint"] = str(hint)
    return out


def result(*, checks: list[dict[str, Any]], action: str = "test_connection") -> dict[str, Any]:
    """Build the one result shape, from the probes that were run."""
    checks = [c for c in (checks or []) if isinstance(c, dict)]
    failed = [c for c in checks if not c.get("ok")]
    ok = bool(checks) and not failed
    if ok:
        detail = "; ".join(c.get("detail") or c.get("name", "") for c in checks)
        return {
            "ok": True,
            "action": action,
            "detail": detail,
            "error": "",
            "hint": "",
            "checks": checks,
        }
    if not checks:
        return {
            "ok": False,
            "action": action,
            "detail": "没有可测试的内容：先把上面的必填项填好。",
            "error": "nothing_to_test",
            "hint": "至少填一个机器人/账号的凭据，再点测试连接。",
            "checks": [],
        }
    first = failed[0]
    return {
        "ok": False,
        "action": action,
        "detail": f"{first.get('name', '')}：{first.get('detail', '')}",
        "error": str(first.get("detail") or "failed"),
        "hint": str(first.get("hint") or ""),
        "checks": checks,
    }


def missing(*, names: list[str], action: str = "test_connection", hint: str = "") -> dict[str, Any]:
    """A refusal for the values the form still lacks (never a placeholder-filled attempt)."""
    fields = "、".join(names)
    return {
        "ok": False,
        "action": action,
        "detail": f"还缺必填项：{fields}",
        "error": "missing_fields",
        "hint": hint or f"请先填写 {fields}，再点测试连接。",
        "checks": [check(f, False, "未填写", hint) for f in names],
    }


def failed(name: str, error: object, hint: str = "") -> dict[str, Any]:
    """A single failed probe, from an exception a caller caught."""
    return check(name, False, f"{type(error).__name__}: {error}", hint)


def http_json(
    url: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    proxy: str = "",
    timeout: float = CHECK_TIMEOUT_S,
) -> tuple[int, dict[str, Any]]:
    """``(status, body)`` for a short JSON call.

    A non-2xx is *returned*, not raised, so the caller can pass on the provider's own
    words ("invalid app_secret", "chat not found") — which is the guidance a user needs.
    Transport failures still raise, and the caller reports them with a network hint.
    """
    import requests

    proxies = {"http": proxy, "https": proxy} if str(proxy or "").strip() else None
    resp = requests.request(
        method,
        url,
        json=payload,
        headers=headers or {},
        proxies=proxies,
        timeout=float(timeout),
    )
    try:
        body = resp.json()
    except Exception:
        body = {"_text": (resp.text or "")[:500]}
    if not isinstance(body, dict):
        body = {"data": body}
    return int(resp.status_code), body
