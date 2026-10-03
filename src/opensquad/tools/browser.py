"""browser_use — the agent's hands on the built-in browser.

The browser itself lives in the launcher (`opensquad/browser_session.py`): it renders on this
machine, so the page the agent drives and the page the user is looking at in the panel are the
*same* page — which is the point of a built-in browser. These tools are thin: every call is an
HTTP POST to the launcher, so an agent process needs no Playwright of its own and the panel can
watch what happens.

The session id defaults to one per agent (`agent-<id>`), so an agent's tools and its panel are
one browser rather than two; passing an id explicitly addresses another one.

Not the same thing as the Playwright MCP server: that drives its own browser process, invisible
to the user. Use these tools when the user should see the page (previews, walkthroughs,
"look at this" screenshots); MCP stays fine for headless scraping.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)

TIMEOUT_S = 60.0


def _launcher_base() -> str:
    try:
        from opensquad.system_config import syscfg

        return str(syscfg.launcher_url() or "").rstrip("/")
    except Exception:
        return ""


def _post(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    """POST to the launcher, bypassing any ambient proxy (it is a loopback service)."""
    base = _launcher_base()
    if not base:
        return {"ok": False, "error": "this machine's launcher address is unknown"}
    headers = {"Content-Type": "application/json"}
    try:
        from opensquad.system_config import syscfg

        token = str(syscfg.get("auth", "launcher_token", "") or "")
        if token:
            headers["Authorization"] = f"Bearer {token}"
    except Exception:
        pass
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(
        f"{base}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with opener.open(req, timeout=TIMEOUT_S) as resp:
            body = json.loads(resp.read().decode("utf-8") or "{}")
            return body if isinstance(body, dict) else {"ok": False, "error": "unexpected reply"}
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8")[:300]
        except Exception:
            detail = ""
        return {"ok": False, "error": f"launcher refused the call (HTTP {exc.code}) {detail}".strip()}
    except Exception as exc:  # noqa: BLE001 - the agent gets the reason
        return {"ok": False, "error": f"could not reach the launcher: {exc}"}


def _agent_id() -> str:
    try:
        from opensquad.input_hub import input_hub

        return os.path.basename(str(input_hub.agent_dir or "")) or "agent"
    except Exception:
        return "agent"


def _session(session_id: str = "") -> str:
    """One browser per agent unless told otherwise (so the panel sees the same page)."""
    return str(session_id or "").strip() or f"agent-{_agent_id()}"


def _call(op: str, *, session_id: str = "", **extra: Any) -> dict[str, Any]:
    payload = {"session_id": _session(session_id), **extra}
    result = _post(f"/api/agents/{_agent_id()}/browser/{op}", payload)
    if not result.get("ok"):
        logger.info("[browser_use] %s failed: %s", op, result.get("error"))
    return result


def browser_open(session_id: str = "") -> dict[str, Any]:
    """Start (or reuse) the built-in browser for this agent.

    On this machine that opens a **real browser window** (persistent profile, so logins survive)
    which the user can work in directly while you drive the same window — so when you are about
    to browse on the user's behalf, say where to look. `headed` in the reply says whether a
    window exists; when it is false the session is headless and only the panel's preview shows
    the page (`window_note` explains why).
    """
    return _call("open", session_id=session_id)


def browser_navigate(url: str, session_id: str = "") -> dict[str, Any]:
    """Open a URL in the built-in browser. Returns the page's url and title."""
    return _call("navigate", session_id=session_id, url=url)


def browser_click(
    selector: str = "", x: float | None = None, y: float | None = None, session_id: str = ""
) -> dict[str, Any]:
    """Click an element (CSS selector) or a point (x, y) on the page."""
    return _call("click", session_id=session_id, selector=selector, x=x, y=y)


def browser_type(text: str, selector: str = "", submit: bool = False, session_id: str = "") -> dict[str, Any]:
    """Type into a field (CSS selector), optionally pressing Enter afterwards."""
    return _call("type", session_id=session_id, selector=selector, text=text, submit=submit)


def browser_press(key: str, session_id: str = "") -> dict[str, Any]:
    """Press a key on the focused element, e.g. Enter, Escape, PageDown."""
    return _call("press", session_id=session_id, key=key)


def browser_back(session_id: str = "") -> dict[str, Any]:
    """Go back to the previous page."""
    return _call("back", session_id=session_id)


def browser_snapshot(session_id: str = "") -> dict[str, Any]:
    """The page's text and links — read this to decide what to click next.

    Cheaper than a screenshot and good enough for most navigation: the visible text, a
    truncation flag, and up to 40 link URLs.
    """
    return _call("snapshot", session_id=session_id)


def browser_screenshot(session_id: str = "", full_page: bool = False) -> dict[str, Any]:
    """Capture the page as a PNG and save it in the workspace; also returns it inline.

    Use it when the user asked to see the page. The file path is returned so it can be
    attached or referenced; `png` is the base64 payload the platform can show.
    """
    result = _call("screenshot", session_id=session_id, full_page=full_page)
    png = str(result.get("png") or "")
    if not png:
        return result
    path = ""
    try:
        from opensquad.input_hub import input_hub

        agent_dir = str(input_hub.agent_dir or "")
        if agent_dir:
            out_dir = os.path.join(agent_dir, "browser")
            os.makedirs(out_dir, exist_ok=True)
            path = os.path.join(out_dir, f"shot-{abs(hash(png)) % 10**8}.png")
            with open(path, "wb") as fh:
                fh.write(base64.b64decode(png))
    except Exception as exc:  # noqa: BLE001 - the inline PNG is still returned
        logger.debug("[browser_use] could not save the screenshot: %s", exc)
    return {**result, "path": path}


def browser_close(session_id: str = "") -> dict[str, Any]:
    """Stop the built-in browser and free the page."""
    return _call("close", session_id=session_id)
