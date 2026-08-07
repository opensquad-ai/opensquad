"""Bing / browser-profile setup status for first-deploy guidance.

Writes ``data/plugins/websearch/status.json`` (launcher → Service Manager
``plugin_status``) and a durable ``login_setup_done.json`` marker after the
user completes ``--login-setup``.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from typing import Any


def _plugin_data_dir() -> str:
    try:
        from plugins._service_runtime import workspace_data_dir
    except ImportError:
        try:
            from _service_runtime import workspace_data_dir
        except ImportError:
            workspace_data_dir = None  # type: ignore[assignment]
    if workspace_data_dir is not None:
        path = workspace_data_dir("plugins", "websearch")
    else:
        path = os.path.join(os.path.expanduser("~"), ".opensquad", "websearch")
    os.makedirs(path, exist_ok=True)
    return path


def resolve_profile_dir() -> str:
    override = os.environ.get("WEBSEARCH_USER_DATA_DIR", "").strip()
    if override:
        return os.path.abspath(override)
    # Same default as websearch_api.resolve_browser_profile_dir()
    return os.path.join(_plugin_data_dir(), "browser_profile")


def _marker_path() -> str:
    return os.path.join(_plugin_data_dir(), "login_setup_done.json")


def _dismiss_path() -> str:
    return os.path.join(_plugin_data_dir(), "setup_banner_dismissed.json")


def _status_path() -> str:
    return os.path.join(_plugin_data_dir(), "status.json")


def _config_path() -> str:
    return os.path.join(_plugin_data_dir(), "config.json")


# Browsers the service can drive (mirrors websearch_api.BROWSER_OPTIONS keys).
BROWSER_OPTIONS: list[str] = ["chrome", "msedge", "chromium", "firefox", "custom"]


def _read_browser_setting() -> str:
    """Browser chosen via UI (config.json) or env override. Defaults to chrome."""
    env = os.environ.get("WEBSEARCH_BROWSER", "").strip().lower()
    if env:
        return env
    try:
        if os.path.isfile(_config_path()):
            with open(_config_path(), encoding="utf-8") as f:
                cfg = json.load(f) or {}
            return str(cfg.get("browser", "")).strip().lower() or "chrome"
    except (OSError, ValueError):
        pass
    return "chrome"


def get_browser_config() -> dict[str, Any]:
    return {"browser": _read_browser_setting(), "options": list(BROWSER_OPTIONS)}


def set_browser(browser: str) -> dict[str, Any]:
    """Persist the browser choice to config.json so the service honors it."""
    browser = (browser or "").strip().lower()
    if browser not in BROWSER_OPTIONS:
        return {
            "ok": False,
            "error": f"Unsupported browser: {browser!r}. Options: {', '.join(BROWSER_OPTIONS)}",
        }
    os.makedirs(_plugin_data_dir(), exist_ok=True)
    cfg: dict[str, Any] = {}
    if os.path.isfile(_config_path()):
        try:
            with open(_config_path(), encoding="utf-8") as f:
                cfg = json.load(f) or {}
        except (OSError, ValueError):
            cfg = {}
    cfg["browser"] = browser
    tmp = _config_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, _config_path())
    return {"ok": True, "browser": browser}


def _profile_has_cookies(profile_dir: str) -> bool:
    """Heuristic: Chromium wrote a non-trivial Cookies DB after a real session."""
    candidates = (
        os.path.join(profile_dir, "Default", "Network", "Cookies"),
        os.path.join(profile_dir, "Default", "Cookies"),
        os.path.join(profile_dir, "Cookies"),
    )
    for path in candidates:
        try:
            if os.path.isfile(path) and os.path.getsize(path) >= 2048:
                return True
        except OSError:
            continue
    return False


def is_login_marked_done() -> bool:
    return os.path.isfile(_marker_path())


def is_banner_dismissed() -> bool:
    return os.path.isfile(_dismiss_path())


def mark_login_done(*, source: str = "login_setup") -> dict[str, Any]:
    payload = {
        "done": True,
        "source": source,
        "done_at": datetime.now(timezone.utc).isoformat(),
    }
    path = _marker_path()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    # Clear dismiss so a future reset can show the banner again if needed.
    try:
        if os.path.isfile(_dismiss_path()):
            os.remove(_dismiss_path())
    except OSError:
        pass
    status = get_setup_status()
    write_plugin_status(status)
    return status


def dismiss_setup_banner() -> dict[str, Any]:
    payload = {"dismissed_at": datetime.now(timezone.utc).isoformat()}
    with open(_dismiss_path(), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    status = get_setup_status()
    write_plugin_status(status)
    return status


def login_setup_command() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    main_py = os.path.join(here, "service", "main.py")
    py = sys.executable
    return f'"{py}" "{main_py}" --login-setup'


def get_setup_status() -> dict[str, Any]:
    profile = resolve_profile_dir()
    marked = is_login_marked_done()
    has_cookies = _profile_has_cookies(profile)
    # Auto-heal: cookies present but no marker (e.g. user logged in before this feature).
    if has_cookies and not marked:
        try:
            mark_login_done(source="cookie_detected")
            marked = True
        except Exception:
            pass

    ready = marked or has_cookies
    dismissed = is_banner_dismissed()
    needs = (not ready) and (not dismissed)
    cmd = login_setup_command()
    return {
        "plugin": "websearch",
        "needs_bing_login": needs,
        "bing_login_ready": ready,
        "login_marked": marked,
        "profile_has_cookies": has_cookies,
        "banner_dismissed": dismissed,
        "profile_dir": profile,
        "setup_command": cmd,
        "browser_config": get_browser_config(),
        "message_zh": (
            "WebSearch 已就绪（Bing 浏览器档案可用）。"
            if ready
            else "首次部署请完成 Bing 登录：停止 WebSearch 服务后，在本机运行登录向导，"
            "用真实 Chrome 登录 Bing/微软账号，按 Enter 保存 Cookie，再重启服务。"
        ),
        "message_en": (
            "WebSearch is ready (Bing browser profile available)."
            if ready
            else "First deploy: stop WebSearch, run the Bing login wizard on this machine, "
            "sign in to Bing/Microsoft in the Chrome window, press Enter to save cookies, "
            "then restart the service."
        ),
        "steps_zh": [
            "打开「服务管理」，停止 WebSearch（避免占用浏览器档案）",
            "点击下方「打开登录窗口」，或在终端运行 setup_command",
            "在弹出的 Chrome 中登录 Bing / 微软账号",
            "回到终端按 Enter 保存 Cookie",
            "在「服务管理」中重新启动 WebSearch",
        ],
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def write_plugin_status(status: dict[str, Any] | None = None) -> str:
    """Write launcher-readable status.json (P1.4 plugin_status)."""
    payload = status or get_setup_status()
    path = _status_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return path


def spawn_login_setup() -> dict[str, Any]:
    """Spawn headed ``--login-setup`` in a new console (Windows) / detached process."""
    import subprocess

    here = os.path.dirname(os.path.abspath(__file__))
    main_py = os.path.join(here, "service", "main.py")
    if not os.path.isfile(main_py):
        return {"ok": False, "error": f"main.py not found: {main_py}"}

    cmd = [sys.executable, main_py, "--login-setup"]
    cwd = os.path.dirname(main_py)
    try:
        if sys.platform == "win32":
            flags = subprocess.CREATE_NEW_CONSOLE  # type: ignore[attr-defined]
            proc = subprocess.Popen(cmd, cwd=cwd, creationflags=flags)
        else:
            proc = subprocess.Popen(
                cmd,
                cwd=cwd,
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
    except Exception as exc:
        return {
            "ok": False,
            "error": str(exc),
            "setup_command": login_setup_command(),
        }
    return {
        "ok": True,
        "spawned": True,
        "pid": proc.pid,
        "setup_command": login_setup_command(),
        "hint_zh": "已打开登录窗口。请先停止 WebSearch 服务（若仍在运行），完成登录后按终端 Enter。",
        "hint_en": "Login window opened. Stop WebSearch if it is still running, finish login, then press Enter in the terminal.",
    }
