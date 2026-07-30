"""Gateway HTTP/WS client for the interactive OpenSquad CLI."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

CREDENTIALS_PATH = Path.home() / ".opensquad" / "cli_credentials.json"
DEFAULT_TIMEOUT = 60.0


class ApiError(Exception):
    def __init__(self, status: int, detail: str, path: str = ""):
        self.status = status
        self.detail = detail
        self.path = path
        super().__init__(f"HTTP {status} {path}: {detail}")


def credentials_path() -> Path:
    return CREDENTIALS_PATH


def load_credentials() -> dict[str, Any]:
    path = credentials_path()
    if not path.is_file():
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_credentials(data: dict[str, Any]) -> None:
    path = credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def clear_credentials() -> None:
    path = credentials_path()
    if path.is_file():
        path.unlink()


def remember_agent(dir_name: str | None) -> None:
    """Cache last-connected agent dir_name (session hint only; not the boot default)."""
    name = (dir_name or "").strip()
    if not name:
        return
    creds = load_credentials()
    if creds.get("last_agent") == name:
        return
    creds["last_agent"] = name
    save_credentials(creds)


def last_agent() -> str | None:
    name = (load_credentials().get("last_agent") or "").strip()
    return name or None


def _agent_dir_name(a: dict[str, Any]) -> str | None:
    name = (a.get("dir_name") or a.get("agent_id") or "").strip()
    return name or None


def _agent_is_autostart(a: dict[str, Any]) -> bool:
    if "auto_start_on_boot" in a:
        return bool(a.get("auto_start_on_boot"))
    cfg = a.get("config") if isinstance(a.get("config"), dict) else {}
    ui = cfg.get("ui") if isinstance(cfg.get("ui"), dict) else {}
    return bool(ui.get("auto_start_on_boot", False))


def list_autostart_agents(client: GatewayClient) -> list[str]:
    """Dir names with ``ui.auto_start_on_boot`` (same flag as Agent Manager)."""
    data = client.admin_get("agents")
    out: list[str] = []
    for a in data.get("agents") or []:
        if not _agent_is_autostart(a):
            continue
        name = _agent_dir_name(a)
        if name and name not in out:
            out.append(name)
    return out


def _unwrap_agent_config(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    cfg = payload.get("config") if isinstance(payload.get("config"), dict) else payload
    return cfg if isinstance(cfg, dict) else {}


def set_agent_autostart(
    client: GatewayClient,
    name: str,
    *,
    enabled: bool = True,
    exclusive: bool = True,
) -> str:
    """Set ``ui.auto_start_on_boot`` on disk (same as Web Agent Manager).

    When ``exclusive`` and enabling, clear the flag on every other agent so
    CLI/Web share a single default boot agent.
    """
    target = (name or "").strip()
    if not target:
        raise ValueError("agent name required")

    data = client.admin_get("agents")
    agents = list(data.get("agents") or [])
    match = next(
        (
            a
            for a in agents
            if a.get("dir_name") == target or a.get("agent_id") == target or a.get("agent_name") == target
        ),
        None,
    )
    if not match:
        raise ValueError(f"agent not found: {target}")
    dir_name = str(match.get("dir_name") or target)

    def _write(dir_n: str, flag: bool) -> None:
        raw = client.admin_get(f"agents/{dir_n}/config")
        cfg = _unwrap_agent_config(raw)
        ui = cfg.get("ui") if isinstance(cfg.get("ui"), dict) else {}
        ui = dict(ui)
        ui["auto_start_on_boot"] = flag
        cfg["ui"] = ui
        client.admin_put(f"agents/{dir_n}/config", {"config": cfg})

    if exclusive and enabled:
        for a in agents:
            other = _agent_dir_name(a)
            if not other or other == dir_name:
                continue
            if _agent_is_autostart(a):
                _write(other, False)

    _write(dir_name, enabled)
    if enabled:
        remember_agent(dir_name)
    elif last_agent() == dir_name:
        creds = load_credentials()
        creds.pop("last_agent", None)
        save_credentials(creds)
    return dir_name


def pick_default_agent(client: GatewayClient) -> str | None:
    """CLI default agent = first agent with auto_start_on_boot (synced with UI).

    Does **not** fall back to an arbitrary ready agent — if none is marked
    for auto-start, return None so the user must ``/start <name>`` or
    ``opensquad agent autostart <name>``.
    """
    try:
        names = list_autostart_agents(client)
    except Exception:
        return None
    if not names:
        return None
    try:
        data = client.admin_get("agents")
        agents = data.get("agents") or []
        by_dir = {_agent_dir_name(a): a for a in agents if _agent_dir_name(a)}
        ready = [n for n in names if (by_dir.get(n) or {}).get("ready")]
        return (ready or names)[0]
    except Exception:
        return names[0]


def resolve_gateway_url(explicit: str | None = None) -> str:
    if explicit:
        return explicit.rstrip("/")
    env = os.environ.get("OPENSQUAD_GATEWAY_URL", "").strip()
    if env:
        return env.rstrip("/")
    creds = load_credentials()
    saved = (creds.get("gateway_url") or "").strip()
    if saved:
        return saved.rstrip("/")
    try:
        from opensquad.system_config import gateway_http

        return gateway_http().rstrip("/")
    except Exception:
        return "http://127.0.0.1:9555"


def resolve_ws_base(gateway_url: str | None = None) -> str:
    base = resolve_gateway_url(gateway_url)
    if base.startswith("https://"):
        return "wss://" + base[len("https://") :]
    if base.startswith("http://"):
        return "ws://" + base[len("http://") :]
    return base


class GatewayClient:
    """Thin authenticated client against Gateway REST APIs."""

    def __init__(
        self,
        gateway_url: str | None = None,
        token: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ):
        self.gateway_url = resolve_gateway_url(gateway_url)
        creds = load_credentials()
        self.token = token or creds.get("token") or ""
        self.timeout = timeout

    @property
    def ws_base(self) -> str:
        return resolve_ws_base(self.gateway_url)

    def require_auth(self) -> None:
        if not self.token:
            raise SystemExit(f"[cli] Not logged in. Run: opensquad login\n  Gateway: {self.gateway_url}")

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if extra:
            headers.update(extra)
        return headers

    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        params: dict[str, Any] | None = None,
        files: Any = None,
        timeout: float | None = None,
    ) -> Any:
        url = f"{self.gateway_url}{path}" if path.startswith("/") else f"{self.gateway_url}/{path}"
        headers = self._headers()
        if files is None and json_body is not None:
            headers["Content-Type"] = "application/json"
        try:
            with httpx.Client(timeout=timeout or self.timeout) as client:
                resp = client.request(
                    method.upper(),
                    url,
                    headers=headers,
                    json=json_body if files is None else None,
                    params=params,
                    files=files,
                    data=json_body if files is not None and isinstance(json_body, dict) else None,
                )
        except httpx.ConnectError as e:
            raise ApiError(
                0,
                f"Cannot connect to Gateway at {self.gateway_url}: {e}",
                path,
            ) from e
        except httpx.TimeoutException as e:
            raise ApiError(
                0,
                f"Gateway timeout at {self.gateway_url}: {e}",
                path,
            ) from e

        if resp.status_code >= 400:
            detail = resp.text
            try:
                payload = resp.json()
                detail = payload.get("detail") or payload.get("error") or detail
            except Exception:
                pass
            raise ApiError(resp.status_code, str(detail), path)

        if resp.status_code == 204 or not resp.content:
            return None
        try:
            return resp.json()
        except Exception:
            return resp.text

    def get(self, path: str, **kw: Any) -> Any:
        return self.request("GET", path, **kw)

    def post(self, path: str, json_body: Any = None, **kw: Any) -> Any:
        return self.request("POST", path, json_body=json_body, **kw)

    def put(self, path: str, json_body: Any = None, **kw: Any) -> Any:
        return self.request("PUT", path, json_body=json_body, **kw)

    def delete(self, path: str, **kw: Any) -> Any:
        return self.request("DELETE", path, **kw)

    # ── Auth ──────────────────────────────────────────────────────────────

    def login(self, email: str, password: str, language: str = "zh") -> dict[str, Any]:
        data = self.post(
            "/api/auth/login",
            {"email": email, "password": password, "language": language},
        )
        token = data.get("access_token") or ""
        user = data.get("user") or {}
        save_credentials(
            {
                "gateway_url": self.gateway_url,
                "token": token,
                "email": email,
                "user": user,
            }
        )
        self.token = token
        return data

    def me(self) -> dict[str, Any]:
        self.require_auth()
        return self.get("/api/auth/me")

    # ── Admin helpers ─────────────────────────────────────────────────────

    def admin_get(self, suffix: str, **kw: Any) -> Any:
        self.require_auth()
        return self.get(f"/api/ai-web/admin/{suffix.lstrip('/')}", **kw)

    def admin_post(self, suffix: str, json_body: Any = None, **kw: Any) -> Any:
        self.require_auth()
        return self.post(f"/api/ai-web/admin/{suffix.lstrip('/')}", json_body=json_body, **kw)

    def admin_put(self, suffix: str, json_body: Any = None, **kw: Any) -> Any:
        self.require_auth()
        return self.put(f"/api/ai-web/admin/{suffix.lstrip('/')}", json_body=json_body, **kw)

    def admin_delete(self, suffix: str, **kw: Any) -> Any:
        self.require_auth()
        return self.delete(f"/api/ai-web/admin/{suffix.lstrip('/')}", **kw)

    def ai_web_get(self, suffix: str, **kw: Any) -> Any:
        self.require_auth()
        return self.get(f"/api/ai-web/{suffix.lstrip('/')}", **kw)

    def resolve_agent_ws_id(self, name_or_id: str) -> str:
        """Map dir_name / display name → registry agent_id for /ai-web/ws/{id}."""
        key = (name_or_id or "").strip()
        if not key:
            return key
        try:
            data = self.admin_get("agents")
        except Exception:
            return key
        agents = data.get("agents") or []
        for a in agents:
            if a.get("agent_id") == key:
                return key
        for a in agents:
            if a.get("dir_name") == key or a.get("agent_name") == key:
                return a.get("agent_id") or a.get("dir_name") or key
        # partial / prefix
        low = key.lower()
        for a in agents:
            did = str(a.get("dir_name") or "")
            aid = str(a.get("agent_id") or "")
            if low in did.lower() or low in aid.lower():
                return a.get("agent_id") or did or key
        return key

    def ai_ws_url(self, agent_id: str) -> str:
        self.require_auth()
        ws_id = self.resolve_agent_ws_id(agent_id)
        return f"{self.ws_base}/ai-web/ws/{quote(ws_id, safe='')}?token={quote(self.token)}"

    def group_ws_url(self) -> str:
        self.require_auth()
        return f"{self.ws_base}/ws?token={quote(self.token)}"


def print_json(data: Any) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False, default=str))


def print_table(rows: list[dict[str, Any]], columns: list[tuple[str, str]]) -> None:
    """Print a simple aligned table. columns = [(key, header), ...]."""
    if not rows:
        print("(empty)")
        return
    widths = []
    for key, header in columns:
        w = len(header)
        for row in rows:
            w = max(w, len(str(row.get(key, "") or "")))
        widths.append(min(w, 48))
    header_line = "  ".join(h.ljust(widths[i]) for i, (_, h) in enumerate(columns))
    print(header_line)
    print("  ".join("-" * widths[i] for i in range(len(columns))))
    for row in rows:
        cells = []
        for i, (key, _) in enumerate(columns):
            val = str(row.get(key, "") or "")
            if len(val) > widths[i]:
                val = val[: widths[i] - 1] + "…"
            cells.append(val.ljust(widths[i]))
        print("  ".join(cells))


def handle_api_error(exc: Exception) -> None:
    if isinstance(exc, ApiError):
        if exc.status in (401, 403):
            print(f"[cli] Auth failed ({exc.status}). Try: opensquad login")
        else:
            print(f"[cli] Error {exc.status}: {exc.detail}")
        raise SystemExit(1) from exc
    raise
