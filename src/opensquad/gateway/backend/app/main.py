"""
FastAPI main application entry point
"""

import asyncio
import contextlib
import hmac
import logging
import logging.config
import os
import sys
import threading
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles

# ── Phase 2.5: Workspace initialization (must happen before all other imports) ──
# Ensure workspace is initialized, otherwise all paths will be wrong
_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))

# ── PyInstaller compatibility: use executable directory as root in frozen env ──────────────────────
_IS_FROZEN = getattr(sys, "frozen", False)
if _IS_FROZEN:
    # sys.executable = .../dist/run/run.exe
    # All packaged resources (opensquad package, app/, nexuschat-pro/dist, etc.) are under this directory
    _PKG_GATEWAY_DIR = os.path.dirname(sys.executable)
    if _PKG_GATEWAY_DIR not in sys.path:
        sys.path.insert(0, _PKG_GATEWAY_DIR)

from opensquad.system_config import syscfg as _syscfg
from opensquad.workspace_utils import load_last_workspace

# ── Minimal console logger (before workspace-based file logging is set up) ──
_console_log = logging.getLogger("backend_startup")
_console_log.setLevel(logging.DEBUG)
if not _console_log.handlers:
    _ch = logging.StreamHandler()
    _ch.setFormatter(logging.Formatter("%(message)s"))
    _console_log.addHandler(_ch)

# !! Important change: workspace is no longer auto-initialized !!
# On startup, attempt to load the last-used workspace; if none exists, use the install directory
# (user selects/creates workspace via the UI)
#
# Frozen (desktop app): resolve workspace via Electron env vars.
# OPENSQUAD_APP_DATA = fixed Electron userData (app prefs).
# OPENSQUAD_USER_DATA = active workspace path (may differ after user switch).
if _IS_FROZEN and (os.environ.get("OPENSQUAD_APP_DATA") or os.environ.get("OPENSQUAD_USER_DATA")):
    from opensquad.workspace_utils import bootstrap_desktop_workspace

    try:
        _ws_path = bootstrap_desktop_workspace()
        _console_log.info("[Workspace] Desktop workspace ready: %s", _ws_path)
    except Exception as _ws_err:
        _console_log.error("[Workspace] Failed to initialize desktop workspace: %s", _ws_err)
        raise
else:
    last_workspace = load_last_workspace()
    if last_workspace and os.path.exists(last_workspace):
        _syscfg.set_workspace(last_workspace)
        _console_log.info("[Workspace] Loaded from last session: %s", last_workspace)
    else:
        # Use install directory as a temporary workspace until user selects/creates one in the UI
        _syscfg.set_workspace(_root)
        _console_log.info("[Workspace] No workspace configured, using install directory: %s", _root)
        _console_log.info("[Workspace] Please configure workspace in Web UI: Settings -> Workspace")

from app.ai_web.agent_sessions import set_ws_handler as _set_ws_handler
from app.ai_web.routes import router as ai_web_router
from app.ai_web.websocket import agent_handler, launcher_handler, user_handler
from app.api import router
from app.bot_api import router as bot_router
from app.database import init_db
from app.websocket import handle_websocket

# Wire up WS tunnel so remote session reads go through the Launcher WS tunnel
# instead of plain HTTP (which would be unreachable when Gateway is on cloud).
_set_ws_handler(launcher_handler.rpc, launcher_handler.get_any_node_id)
from app.workspace_api import router as workspace_router  # Added: workspace management API

# ── Log file setup (via dictConfig) ──────────────────────────────────────
# All runtime logs go to {workspace}/data/logs/gateway/ — kept separate
# from source code so they can be managed / cleaned up independently.
# Rotation settings are read from system_config.json (logging section).
_GATEWAY_LOG_DIR = _syscfg.workspace_logs_dir("gateway")
os.makedirs(_GATEWAY_LOG_DIR, exist_ok=True)

_MAX_BYTES = _syscfg.log_max_size_mb() * 1024 * 1024
_BACKUP_COUNT = _syscfg.log_backup_count()
_LOG_FMT_STR = _syscfg.log_format()
_LOG_DATEFMT = _syscfg.log_date_format()


def _handler_cfg(filename: str, level: str = "DEBUG") -> dict:
    """Build handler dict for logging.config.dictConfig."""
    return {
        "class": "opensquad.safe_rotating_handler.SafeRotatingFileHandler",
        "filename": os.path.join(_GATEWAY_LOG_DIR, filename),
        "maxBytes": _MAX_BYTES,
        "backupCount": _BACKUP_COUNT,
        "encoding": "utf-8",
        "formatter": "default",
        "level": level,
    }


logging.config.dictConfig(
    {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {
                "format": _LOG_FMT_STR,
                "datefmt": _LOG_DATEFMT,
            },
        },
        "handlers": {
            "ws_auth": _handler_cfg("ws_auth.log"),
            "backend": _handler_cfg("backend.log", level="WARNING"),
            "websocket": _handler_cfg("websocket.log"),
            "startup": _handler_cfg("backend_startup.log"),
            "database": _handler_cfg("database.log"),
            "auth": _handler_cfg("auth.log"),
            "api": _handler_cfg("api.log"),
        },
        "loggers": {
            # 1. ws_auth.log — WebSocket authentication events
            "ws_auth": {"handlers": ["ws_auth"], "level": "DEBUG", "propagate": False},
            # 2. backend.log — general application + uvicorn access/error logs (WARNING-level handler)
            "uvicorn.access": {"handlers": ["backend"], "level": "DEBUG", "propagate": False},
            "uvicorn.error": {"handlers": ["backend"], "level": "DEBUG", "propagate": False},
            "uvicorn": {"handlers": ["backend"], "level": "DEBUG", "propagate": False},
            "fastapi": {"handlers": ["backend"], "level": "DEBUG", "propagate": False},
            "app": {"handlers": ["backend"], "level": "DEBUG", "propagate": False},
            # 3. websocket.log — WebSocket-related logs
            "app.websocket": {"handlers": ["websocket"], "level": "DEBUG", "propagate": False},
            "app.ai_web.websocket": {"handlers": ["websocket"], "level": "DEBUG", "propagate": False},
            "websocket": {"handlers": ["websocket"], "level": "DEBUG", "propagate": False},
            # 4. backend_startup.log — startup / init events
            "backend_startup": {"handlers": ["startup"], "level": "DEBUG", "propagate": False},
            # 5. database.log — database session / query logs
            "database": {"handlers": ["database"], "level": "DEBUG", "propagate": False},
            "app.database": {"handlers": ["database"], "level": "DEBUG", "propagate": False},
            # 6. auth.log — authentication events
            "auth": {"handlers": ["auth"], "level": "DEBUG", "propagate": False},
            "app.auth": {"handlers": ["auth"], "level": "DEBUG", "propagate": False},
            # 7. api.log — API request handling
            "app.api": {"handlers": ["api"], "level": "DEBUG", "propagate": False},
            # 8. Suppress noisy 3rd-party loggers
            "httpx": {"level": "WARNING", "propagate": False},
            "httpcore": {"level": "WARNING", "propagate": False},
            "httpx._client": {"level": "WARNING", "propagate": False},
            "httpx._config": {"level": "WARNING", "propagate": False},
        },
    }
)

# Named loggers for use in this module
_ws_log = logging.getLogger("ws_auth")
_startup_log = logging.getLogger("backend_startup")


from opensquad.system_config import syscfg


def load_config():
    """Load from unified configuration"""
    return {
        "frontend": {"port": syscfg.port("frontend"), "host": syscfg.host("frontend")},
        "backend": {
            "host": syscfg.host("gateway"),
            "port": syscfg.port("gateway"),
            "cors": {"allow_origins": syscfg.cors_origins()},
        },
    }


# Load configuration
config = load_config()
cors_config = config.get("backend", {}).get("cors", {})

# Startup readiness flags:
#   ready_lite — DB up; auth + agent admin API usable (CLI fast path)
#   ready      — full init (default data, model presets, …)
_app_ready_lite = False
_app_ready = False

# DB table creation runs in the background so the TCP port + ready_lite become
# available earlier; DB-backed lite endpoints wait on this event.
_db_ready: asyncio.Event | None = None
_db_task: asyncio.Task | None = None

_LITE_HTTP_PREFIXES = (
    "/health",
    "/api/auth",
    "/api/groups",
    "/api/ai-web/admin",
    "/api/ai-web/nodes",
    "/api/ai-web/agents",
    "/api/ai-web/agent-sessions",
    "/api/launcher",
)


def _path_allowed_lite(path: str) -> bool:
    return any(path == p or path.startswith(p + "/") for p in _LITE_HTTP_PREFIXES)


class ReadinessMiddleware:
    """
    Returns 503 for all HTTP requests (except /health) until the backend is fully initialized.
    Prevents frontend HTTP 500 errors when login/register requests arrive before DB init completes.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        if not _app_ready:
            path = scope.get("path", "")
            if _app_ready_lite and _path_allowed_lite(path):
                # ready_lite is set as soon as the background DB init is
                # scheduled; DB-backed lite endpoints wait here so they never
                # touch uninitialized tables.
                if _db_ready is not None and not _db_ready.is_set():
                    try:
                        await asyncio.wait_for(_db_ready.wait(), timeout=15.0)
                    except asyncio.TimeoutError:
                        _startup_log.warning(f"ReadinessMiddleware: DB not ready in 15s, 503 for {path}")
                        await send(
                            {
                                "type": "http.response.start",
                                "status": 503,
                                "headers": [(b"content-type", b"text/plain")],
                            }
                        )
                        await send(
                            {
                                "type": "http.response.body",
                                "body": b"Database not ready yet, please retry in a few seconds",
                            }
                        )
                        return
                await self.app(scope, receive, send)
                return
            # Allow health check to pass even before ready
            if path.startswith("/health"):
                await self.app(scope, receive, send)
                return
            if path != "/health":
                _startup_log.warning(f"ReadinessMiddleware: 503 for {path} (backend not ready)")
                await send(
                    {
                        "type": "http.response.start",
                        "status": 503,
                        "headers": [(b"content-type", b"text/plain")],
                    }
                )
                await send(
                    {
                        "type": "http.response.body",
                        "body": b"Backend not ready yet, please retry in a few seconds",
                    }
                )
                return
        await self.app(scope, receive, send)


class LazyRoutesMiddleware:
    """
    P1-5: mount the heavy admin/market routers on demand.

    Normally the background startup task mounts them shortly after listen; this
    middleware is the safety net for requests that arrive before then (e.g. an
    admin call racing startup) so they never 404.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            path = scope.get("path", "")
            if path.startswith("/api/ai-web/admin") or path.startswith("/api/ai-web/market"):
                try:
                    from app.ai_web.routes._main import ensure_lazy_routers

                    # self.app is the middleware stack, NOT the FastAPI app —
                    # resolve the real app from the module global (available by
                    # request time).
                    ensure_lazy_routers(globals().get("app"))
                except Exception:
                    pass
        await self.app(scope, receive, send)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifecycle management"""
    global _app_ready_lite, _app_ready, _db_ready, _db_task
    _startup_log.info("Backend starting up...")
    # P2-9: warm the default ThreadPoolExecutor now so the first WS connection
    # does not pay thread-creation latency (~80ms on Windows) on its
    # asyncio.to_thread session-cache calls. Subsequent connects drop from
    # ~85ms to ~5ms.
    try:
        await asyncio.to_thread(lambda: None)
    except Exception:
        pass
    # Capture the gateway event loop for scheduled-task delivery so timer-
    # fired tasks can route prompts to the Agent via the Gateway WS registry
    # even before any admin route lazily created a ScheduledTaskManager.
    try:
        from opensquad.scheduled_tasks import set_gateway_loop, warm_all_task_managers

        _loop = asyncio.get_running_loop()
        set_gateway_loop(_loop)
        # Arm persisted scheduled-task timers immediately (do not wait for the
        # admin UI to open the scheduled-tasks page).
        try:
            n = warm_all_task_managers(_loop)
            _startup_log.info(f"Scheduled-task managers warmed: {n}")
        except Exception as _warm_e:
            _startup_log.warning(f"warm_all_task_managers failed: {_warm_e}")
    except Exception as _e:
        _startup_log.warning(f"set_gateway_loop failed: {_e}")
    # Agent WS liveness sweeper: drop half-dead connections where send_text
    # succeeds but the agent message loop never processes chat.
    try:
        from app.ai_web.registry import registry as _agent_registry

        _agent_registry.ensure_liveness_loop(asyncio.get_running_loop())
    except Exception as _e:
        _startup_log.warning(f"agent liveness sweeper start failed: {_e}")
    # Initialize database in the background — the TCP port and ready-lite
    # become available immediately; DB-backed lite endpoints wait on
    # _db_ready in ReadinessMiddleware.
    _db_ready = asyncio.Event()

    async def _init_db_background() -> None:
        try:
            await init_db()
            _startup_log.info("Database initialized")
        except Exception as _db_e:
            _startup_log.error(f"Database init failed: {_db_e}")
        finally:
            if _db_ready is not None:
                _db_ready.set()

    _db_task = asyncio.create_task(_init_db_background())

    # Phase 1 (fast): CLI / auth / agent admin can proceed
    _app_ready_lite = True
    _startup_log.info("Backend ready-lite (auth + agent admin)")

    async def _task_init_data():
        try:
            from init_data import init_default_data

            await init_default_data()
            _startup_log.info("Backend ready: default data initialized")
        except Exception as _e:
            _startup_log.warning(f"Default data init skipped: {_e}")

    async def _task_reset_users():
        try:
            from sqlalchemy import update

            from app.database import AsyncSessionLocal
            from app.models import User, UserStatus

            async with AsyncSessionLocal() as db:
                await db.execute(update(User).where(User.status == UserStatus.ONLINE).values(status=UserStatus.OFFLINE))
                await db.commit()
            _startup_log.info("Reset all online users to offline on startup")
        except Exception as e:
            _startup_log.warning(f"Failed to reset user statuses on startup: {e}")

    async def _task_model_presets():
        try:
            from app.ai_web import model_preset_service

            await model_preset_service.initialize()
            _startup_log.info("Model preset service initialized from disk cache")
        except Exception as e:
            _startup_log.warning(f"Model preset service init failed: {e}")

    async def _task_lazy_routes():
        # P1-5: import the heavy admin/market routers in the background so the
        # gateway starts listening before their ~1.1s import completes.
        try:
            from app.ai_web.routes._main import ensure_lazy_routers

            ensure_lazy_routers(app)
            _startup_log.info("Lazy routes mounted (background)")
        except Exception as e:
            _startup_log.warning(f"Lazy route mount failed: {e}")

    async def _finish_heavy_startup() -> None:
        global _app_ready
        await asyncio.gather(
            _task_init_data(),
            _task_reset_users(),
            _task_model_presets(),
            _task_lazy_routes(),
        )
        _app_ready = True
        _startup_log.info("Backend fully ready")

    _heavy_task = asyncio.create_task(_finish_heavy_startup())

    # ── Config hot-reload watcher ──
    _config_watch_stop = threading.Event()

    def _config_watch_loop():
        """Poll system_config.json for changes and apply hot-reloadable settings."""
        from opensquad.system_config import syscfg as _sc

        _last_mtime = 0.0
        try:
            _watch_path = _sc.workspace_config_path()
        except Exception:
            _watch_path = None
        if _watch_path and os.path.isfile(_watch_path):
            _last_mtime = os.path.getmtime(_watch_path)
        while not _config_watch_stop.is_set():
            _config_watch_stop.wait(15)
            if _config_watch_stop.is_set():
                break
            if not _watch_path or not os.path.isfile(_watch_path):
                continue
            try:
                current_mtime = os.path.getmtime(_watch_path)
                if current_mtime > _last_mtime:
                    _last_mtime = current_mtime
                    # Trigger syscfg reload
                    _sc.reload()
                    # Detect log_level change
                    new_log_level = _syscfg.log_level()
                    root_logger = logging.getLogger()
                    root_logger.setLevel(getattr(logging, new_log_level.upper(), logging.INFO))
                    _startup_log.info(f"[config-watch] Config reloaded: log_level={new_log_level}")
                    # Detect CORS change
                    new_cors = _syscfg.cors_origins()
                    _startup_log.info(f"[config-watch] CORS origins = {new_cors}")
                    # Note: port/host changes require restart
                    _old_ports = (config.get("backend", {}).get("port"), config.get("backend", {}).get("host"))
                    _new_port = _syscfg.port("gateway")
                    _new_host = _syscfg.host("gateway")
                    if _old_ports != (_new_port, _new_host):
                        _startup_log.warning(
                            f"[config-watch] Port/host changed: {_old_ports} -> ({_new_port}, {_new_host}). "
                            "Restart required for these settings to take effect."
                        )
            except Exception as _we:
                _startup_log.warning(f"[config-watch] Watch error: {_we}")

    _config_watch_thread = threading.Thread(target=_config_watch_loop, daemon=True, name="config-watch")
    _config_watch_thread.start()
    _startup_log.info("Config hot-reload watcher started")

    yield

    _heavy_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await _heavy_task

    # Cancel the background DB-init task if still running.
    if _db_task is not None and not _db_task.done():
        _db_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _db_task

    # Stop config watcher
    _config_watch_stop.set()
    _config_watch_thread.join(timeout=3)

    # Clean up resources on shutdown
    _startup_log.info("Backend shutting down")
    try:
        from app.ai_web import model_preset_service

        model_preset_service.shutdown()
    except Exception:
        pass
    # PERF-9: close the shared httpx client used by the admin proxy routes.
    try:
        from app.ai_web.routes._admin import close_shared_http_client

        await close_shared_http_client()
    except Exception:
        pass


# Create FastAPI application
app = FastAPI(title="OpenSquad API", description="OpenSquad gateway backend API", version="1.0.0", lifespan=lifespan)

# Configure CORS - read allowed origins from system_config.json's gateway.cors_origins
# (falls back to security.cors_allow_origins for backward compatibility).
# Defaults to ["http://localhost:5173"] for local development.
# For production, explicitly set gateway.cors_origins in system_config.json.
_allowed_origins = _syscfg.cors_origins()
_allow_creds = _allowed_origins != ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=_allow_creds,
    allow_methods=["*"],
    allow_headers=["*"],
)
# Restrict credentials=True services (plugin_registry, jsondb, external_api) to localhost only.

# Readiness middleware: returns 503 until DB + default data init completes
app.add_middleware(ReadinessMiddleware)
# P1-5: mounts heavy admin/market routers on first admin request (safety net;
# the background startup task normally mounts them earlier).
app.add_middleware(LazyRoutesMiddleware)


# Register API routes
app.include_router(router, prefix="/api")
app.include_router(bot_router, prefix="/api")
app.include_router(ai_web_router)  # AI Web API routes
app.include_router(workspace_router)  # Workspace management API


# Group chat WebSocket endpoint
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """Group chat WebSocket connection endpoint"""
    # Retrieve token from query parameters
    token = websocket.query_params.get("token")
    if not token:
        await websocket.close(code=4001, reason="Missing token")
        return

    await handle_websocket(websocket, token)


# AI Web WebSocket endpoints
@app.websocket("/ai-ws/register")
async def ai_agent_register(websocket: WebSocket):
    """Agent registration endpoint"""
    await agent_handler.handle_agent_register(websocket)


@app.websocket("/ai-ws/launcher")
async def ai_launcher_tunnel(websocket: WebSocket):
    """Launcher admin RPC tunnel — Launcher connects here from home machine."""
    await launcher_handler.handle_launcher_connect(websocket)


@app.websocket("/ai-web/ws/{agent_id}")
async def ai_user_chat(websocket: WebSocket, agent_id: str):
    """User-to-agent chat endpoint"""
    token = websocket.query_params.get("token")

    async def _reject(code: int, reason: str) -> None:
        # Must accept before close — otherwise browsers can stick in CONNECTING
        # (readyState=0) forever and chat appears dead after service restart.
        try:
            await websocket.accept()
        except Exception:
            return
        try:
            await websocket.close(code=code, reason=reason)
        except Exception:
            pass

    if not token:
        _ws_log.warning("[AI Web WS] Missing token for agent %s", agent_id)
        await _reject(4001, "Missing token")
        return

    # Check against configured gateway_token (for adapter connections)
    cfg_gateway_token = syscfg.auth("gateway_token")
    if (
        cfg_gateway_token
        and cfg_gateway_token not in ("", "YOUR_GATEWAY_TOKEN_HERE")
        and hmac.compare_digest(token, cfg_gateway_token)
    ):
        user_id = "adapter-user"
        _ws_log.info("[AI Web] Adapter connected to agent %s", agent_id)
    else:
        # Decode token to get user_id
        from app.auth import decode_token

        _ws_log.debug("[AI Web WS] Decoding token for agent %s, token length=%d", agent_id, len(token))
        payload = decode_token(token)
        if not payload:
            _ws_log.error("[AI Web WS] REJECTED: invalid token for agent %s", agent_id)
            await _reject(4001, "Invalid token")
            return

        user_id = payload.get("sub")
        if not user_id:
            _ws_log.error("[AI Web WS] REJECTED: no 'sub' in token payload for agent %s", agent_id)
            await _reject(4001, "Invalid user")
            return

        _ws_log.debug("[AI Web WS] ACCEPTED: user=%s -> agent=%s", user_id, agent_id)

    await user_handler.handle_user_chat(websocket, agent_id, user_id)


# Health check
@app.get("/health")
async def health_check():
    """Health check endpoint (returns before full init; check ``ready`` for UI load)."""
    return {
        "status": "ok",
        "service": "OpenSquad API",
        "ready_lite": _app_ready_lite,
        "ready": _app_ready,
    }


@app.get("/health/ready-lite")
async def health_ready_lite():
    """Fast readiness probe for CLI — DB up; auth + agent admin routes allowed."""
    return {"ready_lite": _app_ready_lite, "ready": _app_ready}


# Launcher management API proxy (production / desktop parity with Vite dev proxy).
# Frontend may call /api/launcher/api/... which Vite rewrites to launcher :9600.
# Without this route, unmatched PUT requests fall through to StaticFiles → 405.
@app.api_route(
    "/api/launcher/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
)
async def launcher_http_proxy(path: str, request: Request):
    """Forward /api/launcher/* to the launcher management server."""
    base = syscfg.launcher_url().rstrip("/")
    target = f"/{path}"
    if request.url.query:
        target += f"?{request.url.query}"

    hop_by_hop = {"host", "content-length", "connection", "transfer-encoding"}
    headers = {k: v for k, v in request.headers.items() if k.lower() not in hop_by_hop}
    body = await request.body()

    try:
        async with httpx.AsyncClient(timeout=30.0, trust_env=False) as client:
            upstream = await client.request(request.method, f"{base}{target}", content=body, headers=headers)
    except httpx.ConnectError:
        return Response(
            content='{"detail":"Launcher is not running"}',
            status_code=502,
            media_type="application/json",
        )
    except httpx.RequestError as exc:
        return Response(
            content=f'{{"detail":"Launcher proxy error: {exc!s}"}}',
            status_code=502,
            media_type="application/json",
        )

    excluded = {"content-encoding", "transfer-encoding", "connection"}
    out_headers = {k: v for k, v in upstream.headers.items() if k.lower() not in excluded}
    return Response(content=upstream.content, status_code=upstream.status_code, headers=out_headers)


# Static file serving (frontend application)
# Frontend files are bundled with the installation package

# ── PyInstaller-compatible path ──────────────────────────────────────────────
# Normal source layout: app/main.py → backend/ → gateway/ → (nexuschat-pro/dist under gateway/)
# Packaged (PyInstaller 6.x): the spec adds the frontend dist via
#   `datas += [(FRONTEND_DIST, "nexuschat-pro/dist")]`
# and COLLECT places datas under <exe-dir>/_internal/, so the live path is
# <exe-dir>/_internal/nexuschat-pro/dist. We probe both so a 5.x build
# (legacy layout, files at <exe-dir>/...) still works.
if _IS_FROZEN:
    _pkg_gateway = os.path.dirname(sys.executable)
    _frozen_root = os.path.join(_pkg_gateway, "_internal")
else:
    _pkg_gateway = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    _frozen_root = None

_frontend_candidate = os.path.join(_frozen_root, "nexuschat-pro", "dist") if _frozen_root else None
if _frontend_candidate and os.path.isdir(_frontend_candidate):
    FRONTEND_DIST = _frontend_candidate
else:
    FRONTEND_DIST = os.path.normpath(os.path.join(_pkg_gateway, "nexuschat-pro", "dist"))

# ── Phase 2.5: Runtime resources use workspace path ──
# Uploads live at <workspace>/data/uploads in BOTH dev and frozen modes. In the
# desktop app the workspace IS Electron's userData dir (set above), so uploads
# persist per-user alongside chat.db and are served consistently. (Previously
# frozen mode used <userData>/uploads directly, which diverged from the
# workspace layout and left historical dev uploads unreachable.) The directory
# is also created by ensure_workspace_structure(), but we makedirs here too so
# the StaticFiles mount below never points at a missing dir.
UPLOAD_DIR = syscfg.workspace_uploads_dir()
# Plugins code is in the installation directory (read-only resources)
PLUGINS_ROOT = syscfg.builtin_resources_dir("plugins")

os.makedirs(UPLOAD_DIR, exist_ok=True)

# Mount order: exact matches take priority
# Plugin static resources and uploads are mounted first; frontend is mounted last
if os.path.exists(PLUGINS_ROOT):
    app.mount("/api/plugins/static", StaticFiles(directory=PLUGINS_ROOT), name="plugins_static")

app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")

# ── Frontend serving ─────────────────────────────────────────────────────────
# In development, the Vite dev server (port 5173) provides HMR hot-reload.
# Instead of forcing the user to visit :5173 separately (or rebuild dist/ after
# every change), we detect whether Vite is running and reverse-proxy all
# non-API requests to it. In production (no Vite), we serve the built dist/.
import socket as _socket

import httpx as _httpx

_VITE_HOST = os.environ.get("VITE_DEV_HOST", "127.0.0.1")
_VITE_PORT = int(os.environ.get("VITE_DEV_PORT", str(_syscfg.port("frontend"))))


def _is_vite_running(host: str = _VITE_HOST, port: int = _VITE_PORT) -> bool:
    """Check if the Vite dev server is listening.

    On some Windows hosts a firewall silently drops localhost traffic to the
    Vite port, causing the socket probe to hang for seconds. Keep the timeout
    very short so import-time detection never stalls gateway startup.
    """
    try:
        with _socket.create_connection((host, port), timeout=0.1):
            return True
    except (OSError, TimeoutError):
        return False


# Desktop / PyInstaller builds must serve bundled dist/, never proxy to Vite.
# A false-positive on the frontend port (e.g. 9530) would proxy to a non-Vite
# listener and leave the Electron window blank.
_disable_vite_proxy = _IS_FROZEN or os.environ.get("OPENSQUAD_DISABLE_VITE_PROXY") == "1"
_VITE_AVAILABLE = (not _disable_vite_proxy) and _is_vite_running()
if _disable_vite_proxy:
    _startup_log.info(
        "[Frontend] Vite proxy disabled (frozen=%s, env=%s) — serving dist/ at %s",
        _IS_FROZEN,
        os.environ.get("OPENSQUAD_DISABLE_VITE_PROXY", ""),
        FRONTEND_DIST,
    )
elif _VITE_AVAILABLE:
    _startup_log.info(
        "[Frontend] Vite dev server detected at %s:%d — enabling reverse proxy "
        "(HMR hot-reload active via single port %d)",
        _VITE_HOST,
        _VITE_PORT,
        syscfg.port("gateway"),
    )


def _serve_dist_file(path: str):
    """Serve a file from the built frontend dist/ directory, if it exists.

    Falls back to index.html for SPA routing (same behaviour as
    StaticFiles(..., html=True)). Returns None if the file is not found.
    """
    if not os.path.exists(FRONTEND_DIST):
        return None
    safe_path = os.path.normpath(path).lstrip(os.sep)
    file_path = os.path.join(FRONTEND_DIST, safe_path)
    # Prevent directory traversal outside dist/
    if not file_path.startswith(os.path.normpath(FRONTEND_DIST) + os.sep) and file_path != os.path.normpath(
        FRONTEND_DIST
    ):
        file_path = os.path.join(FRONTEND_DIST, "index.html")
    if os.path.isfile(file_path):
        return file_path
    index_path = os.path.join(FRONTEND_DIST, "index.html")
    if os.path.isfile(index_path):
        return index_path
    return None


if _VITE_AVAILABLE:
    # Reverse-proxy mode: forward non-API requests to Vite for HMR.
    # API routes (/api/*, /ai-web/*, /ai-ws/*, /ws, /uploads) are already
    # registered above and take priority over this catch-all.
    from fastapi import Request
    from fastapi.responses import Response

    # trust_env=False prevents httpx from picking up system proxy settings
    # (HTTP_PROXY/HTTPS_PROXY), which can cause ConnectError on Windows when
    # targeting localhost (e.g. 502 / infinite hang).
    # A short overall timeout prevents a dead Vite dev server from blocking
    # gateway requests for too long on hosts with a silent-drop firewall.
    _vite_client = _httpx.AsyncClient(
        base_url=f"http://{_VITE_HOST}:{_VITE_PORT}",
        timeout=_httpx.Timeout(10.0, connect=1.0),
        trust_env=False,
    )

    # Vite HMR uses a WebSocket on the same port; we can't easily proxy WS
    # through FastAPI, but Vite's HMR client falls back to its own connection
    # to 5173 automatically. The key win is that the HTML/JS/CSS served by
    # the backend now comes from Vite (with HMR client injected).

    @app.api_route(
        "/{path:path}",
        methods=["GET", "HEAD", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    )
    async def _vite_proxy(path: str, request: Request):
        """Proxy non-API requests to the Vite dev server.

        If Vite becomes unreachable (e.g. it crashed or a Windows firewall is
        silently dropping localhost packets), fall back to serving the built
        dist/ files so the UI keeps working.
        """
        # _vite_client is defined in the enclosing module scope; declare it
        # global so the except block's reassignment below mutates the same
        # object instead of triggering UnboundLocalError on first read.
        global _vite_client

        # CRITICAL: this catch-all `/{path:path}` route is registered AFTER
        # the WS/API routes, but FastAPI's router still routes a plain HTTP
        # GET to `/ai-ws/...` here (including the WebSocket handshake GET),
        # which shadows the @app.websocket() handlers and returns the Vite
        # HTML page instead of completing the WebSocket upgrade. Reject those
        # paths up front. Plain HTTP methods on `/api/...` and `/ai-web/...`
        # are still routed correctly by their dedicated routers, so we only
        # need to guard the WebSocket-style paths that the catch-all would
        # otherwise shadow.
        if path.startswith(("ai-ws/", "ai-web/")) or path in ("ai-ws", "ai-web", "ws"):
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail=f"Not Found: /{path}")

        # Build the target URL
        url_path = f"/{path}"
        if request.url.query:
            url_path += f"?{request.url.query}"

        # Forward the request to Vite
        try:
            vite_resp = await _vite_client.request(
                request.method,
                url_path,
                headers={k: v for k, v in request.headers.items() if k.lower() != "host"},
                content=await request.body(),
            )
            # Strip hop-by-hop headers
            excluded = {"content-encoding", "transfer-encoding", "connection"}
            headers = {k: v for k, v in vite_resp.headers.items() if k.lower() not in excluded}
            return Response(content=vite_resp.content, status_code=vite_resp.status_code, headers=headers)
        except (_httpx.ConnectError, _httpx.TimeoutException, _httpx.NetworkError, TimeoutError):
            # Vite went down mid-session or is unreachable — recreate the client
            # in case it needs a fresh connection pool, then fall back to dist/.
            _startup_log.warning(
                "Vite dev server at %s:%d unreachable — serving built dist/ fallback",
                _VITE_HOST,
                _VITE_PORT,
            )
            _vite_client = _httpx.AsyncClient(
                base_url=f"http://{_VITE_HOST}:{_VITE_PORT}",
                timeout=_httpx.Timeout(10.0, connect=1.0),
                trust_env=False,
            )
            dist_file = _serve_dist_file(path)
            if dist_file:
                content_type = "application/octet-stream"
                if dist_file.endswith(".html"):
                    content_type = "text/html"
                elif dist_file.endswith(".js"):
                    content_type = "application/javascript"
                elif dist_file.endswith(".css"):
                    content_type = "text/css"
                with open(dist_file, "rb") as f:
                    return Response(content=f.read(), media_type=content_type)
            return Response(
                content="<html><body><h2>Frontend dev server unavailable</h2>"
                "<p>The Vite dev server is not running and no built dist/ "
                "files were found. Start it with <code>npm run dev</code> "
                "in nexuschat-pro/, or build dist/ with "
                "<code>npm run build</code>.</p></body></html>",
                status_code=502,
                media_type="text/html",
            )

elif os.path.exists(FRONTEND_DIST):
    _startup_log.info("[Frontend] Serving static dist from %s", FRONTEND_DIST)
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
else:
    _startup_log.error("[Frontend] dist directory not found: %s — UI will be blank", FRONTEND_DIST)


if __name__ == "__main__":
    import uvicorn

    from opensquad.system_config import syscfg

    # Read from gateway/config.json, fallback to system_config
    backend_config = config.get("backend", {})
    uvicorn.run(
        "app.main:app",
        host=backend_config.get("host", "0.0.0.0"),
        port=backend_config.get("port", syscfg.port("gateway")),
        reload=backend_config.get("reload", False),
        log_level=backend_config.get("log_level", "warning"),
        access_log=False,
        # Generous server-side WS ping window so long agent turns (blocked
        # briefly by session JSON/disk writes) do not get disconnected as
        # "keepalive ping timeout" → agent flips offline → UI 重连中.
        ws_ping_interval=30,
        ws_ping_timeout=90,
    )
