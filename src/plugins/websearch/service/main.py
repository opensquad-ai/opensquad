import os as _os
import sys
import threading
import warnings

warnings.filterwarnings("ignore", category=DeprecationWarning)

# ── Force UTF-8 stdout/stderr on Windows ──────────────────────────
# On Chinese Windows, the default console encoding is GBK (cp936), which
# cannot encode many Unicode characters (e.g. emoji, check marks). When
# print() encounters such a character, it raises UnicodeEncodeError,
# which propagates up and crashes the API endpoint with a 500 error.
# Reconfigure stdout/stderr to UTF-8 to prevent this.
if sys.platform == "win32":
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")
        elif hasattr(_stream, "buffer"):
            import io as _io

            _new = _io.TextIOWrapper(
                _stream.buffer,
                encoding="utf-8",
                errors="replace",
                line_buffering=_stream.line_buffering if hasattr(_stream, "line_buffering") else False,
            )
            if _stream is sys.stdout:
                sys.stdout = _new
            else:
                sys.stderr = _new

# ── sys.path setup ─────────────────────────────────────────
# _here: plugins/websearch/service/
# We need `plugins._service_runtime` to be importable. In a PyInstaller
# bundle, _service_runtime.py lives at _internal/plugins/_service_runtime.py.
#
# CRITICAL: Do NOT add the entire _internal/ to sys.path. It contains
# 3.11-compiled .pyd files (brotlicffi, socket, etc.) that conflict with
# the Agent Python's own site-packages, causing crashes like
# "Module use of python311.dll conflicts" or "brotlicffi has no attribute
# 'error'". Instead, add ONLY the _internal/plugins/ directory so that
# `import _service_runtime` works (direct module, not package-qualified).
_here = _os.path.dirname(_os.path.abspath(__file__))
# _plugins_dir: plugins/ directory — contains _service_runtime.py and __init__.py
_plugins_dir = _os.path.abspath(_os.path.join(_here, "..", ".."))
if _here not in sys.path:
    sys.path.insert(0, _here)  # Allow `from websearch_api import ...` (absolute import)
if _plugins_dir not in sys.path:
    sys.path.insert(0, _plugins_dir)  # Allow `import _service_runtime` (direct)


from fastapi import FastAPI, HTTPException, Query

# Self-contained runtime helper — does NOT import opensquad (which is not
# available to the Agent Python that runs plugin services in frozen mode).
# Import directly from _service_runtime (not plugins._service_runtime) to
# avoid needing the parent _internal/ dir on sys.path.
try:
    from plugins._service_runtime import port as _runtime_port
    from plugins._service_runtime import workspace_data_dir as _runtime_workspace_data_dir
except ImportError:
    from _service_runtime import port as _runtime_port
    from _service_runtime import workspace_data_dir as _runtime_workspace_data_dir

# --- Dynamically import business modules ---
try:
    from .websearch_api import fetch_and_wash_urls_async, fetch_html_content_async, search_links_async
except ImportError:
    from websearch_api import fetch_and_wash_urls_async, fetch_html_content_async, search_links_async


def _resolve_service_port() -> int:
    """
    Port resolution priority (aligned with Launcher / Agent tools):
    1. PORT env (Launcher sets this when spawning the service)
    2. workspace data/plugins/websearch/config.json
    3. system_config.json ports.websearch
    4. Default value 9001
    """
    port_env = _os.environ.get("PORT")
    if port_env:
        try:
            return int(port_env)
        except ValueError:
            pass
    config_path = _runtime_workspace_data_dir("plugins", "websearch", "config.json")
    if _os.path.isfile(config_path):
        try:
            import json as _json

            with open(config_path, encoding="utf-8") as _f:
                _cfg = _json.load(_f)
            if "port" in _cfg:
                return int(_cfg["port"])
        except Exception:
            pass
    try:
        return _runtime_port("websearch")
    except Exception:
        pass
    return 9001


# --- 1. Initialize FastAPI application ---
app = FastAPI(
    title="Web Search Service API (GET Version)",
    description="An API service providing web search and web content extraction via GET methods.",
    version="1.1.0",
)


@app.on_event("startup")
async def _suppress_proactor_noise():
    """Windows asyncio Proactor prints Tracebacks for benign keep-alive resets.

    When a remote (Bing) closes a keep-alive connection, the transport's
    ``_call_connection_lost`` calls ``socket.shutdown`` on an already-closed
    socket → ``ConnectionResetError [WinError 10054]``. This is harmless (the
    HTTP response was already delivered) but asyncio's default exception
    handler prints it to stderr. The ``logging`` filter can't catch it, so we
    wrap ``loop.set_exception_handler`` here (loop exists during startup).
    """
    import asyncio as _aio

    loop = _aio.get_running_loop()
    default_handler = loop.get_exception_handler()

    def _handler(loop_, context):
        exc = context.get("exception")
        msg = str(context.get("message", ""))
        if exc is not None:
            exc_text = str(exc)
        else:
            exc_text = ""
        noisy = any(
            sig in exc_text or sig in msg
            for sig in ("_call_connection_lost", "ConnectionResetError", "[WinError 10054]", "ConnectionAbortedError")
        )
        if noisy:
            return
        if default_handler is not None:
            default_handler(loop_, context)
        else:
            loop_.default_exception_handler(context)

    try:
        loop.set_exception_handler(_handler)
    except Exception:
        pass


# --- 2. Define API endpoints ---


@app.get("/health", summary="Health check")
async def health():
    payload = {"status": "ok", "service": "websearch"}
    try:
        _plugins = _os.path.abspath(_os.path.join(_here, "..", ".."))
        if _plugins not in sys.path:
            sys.path.insert(0, _plugins)
        from websearch.setup_status import get_setup_status, write_plugin_status

        setup = get_setup_status()
        write_plugin_status(setup)
        payload["bing_login_ready"] = bool(setup.get("bing_login_ready"))
        payload["needs_bing_login"] = bool(setup.get("needs_bing_login"))
    except Exception as exc:
        payload["setup_status_error"] = str(exc)
    return payload


@app.get("/search", summary="Execute web search")
async def search_endpoint(
    queries: str = Query(..., description="Search query terms; separate multiple queries with a comma (,)."),
    max_results: int = Query(20, description="Maximum number of results per query", gt=0),
):
    """
    Accepts URL query parameters, performs a web search, and returns link summaries.
    Example request:
    `http://127.0.0.1:9001/search?queries=FastAPI+intro,Pydantic+usage&max_results=2`
    """
    query_list = [q.strip() for q in queries.split(",") if q.strip()]
    if not query_list:
        raise HTTPException(status_code=400, detail="Query parameter 'queries' must not be empty.")

    try:
        print("query_list", query_list)
        result = await search_links_async(
            queries=query_list,
            max_results_per_query=max_results,
        )
        return {"status": "success", "data": result}
    except Exception as e:
        print(f"---  ERROR in /search endpoint: {e} ---")
        raise HTTPException(status_code=500, detail=f"Internal error during search: {e}")


@app.get("/fetch", summary="Fetch and clean web page content")
async def fetch_endpoint(
    urls: str = Query(..., description="URLs to fetch content from; separate multiple URLs with a comma (,)."),
):
    """
    Accepts URL query parameters, fetches and cleans the content of specified URLs.
    Example request:
    `http://127.0.0.1:9001/fetch?urls=http://example.com,http://example.org`
    """
    url_list = [u.strip() for u in urls.split(",") if u.strip()]
    if not url_list:
        raise HTTPException(status_code=400, detail="URL parameter 'urls' must not be empty.")

    try:
        result = await fetch_and_wash_urls_async(
            url_infos=url_list,
        )
        return {"status": "success", "data": result}
    except Exception as e:
        print(f"---  ERROR in /fetch endpoint: {e} ---")
        raise HTTPException(status_code=500, detail=f"Internal error during content fetch: {e}")


@app.get("/fetch_html", summary="Fetch web page content without cleaning")
async def fetch_html(url: str = Query(..., description="URL to fetch content from")):
    """
    Accepts a URL query parameter, fetches the raw HTML content of the specified URL.
    Example request:
    `http://127.0.0.1:9001/fetch_html?url=http://example.com`
    """
    if not url:
        raise HTTPException(status_code=400, detail="URL parameter 'url' must not be empty.")

    try:
        result = await fetch_html_content_async(
            url=url,
        )
        return {"status": "success", "data": result}
    except Exception as e:
        print(f"---  ERROR in /fetch html: {e} ---")
        raise HTTPException(status_code=500, detail=f"Internal error during content fetch: {e}")


@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup browser singleton and reranker sidecar on service shutdown."""
    try:
        # Try relative import first (when run as package), fallback to
        # absolute (when run as script: `python service/main.py`).
        try:
            from .websearch_api import shutdown_browser
        except ImportError:
            from websearch_api import shutdown_browser

        await shutdown_browser()
    except Exception as e:
        print(f"[WebSearch] Shutdown cleanup error: {e}")
    try:
        try:
            from .reranker_sidecar import stop_reranker_sidecar
        except ImportError:
            from reranker_sidecar import stop_reranker_sidecar

        stop_reranker_sidecar()
    except Exception as e:
        print(f"[WebSearch] Reranker shutdown error: {e}")


# --- 3. Run server ---
if __name__ == "__main__":
    import argparse
    import asyncio
    import logging as _logging
    import signal

    import uvicorn

    _parser = argparse.ArgumentParser(description="OpenSquad websearch service")
    _parser.add_argument(
        "--login-setup",
        action="store_true",
        help="Open headed Chrome with the persistent Bing profile for manual login, then exit",
    )
    _args, _unknown = _parser.parse_known_args()
    if _args.login_setup:
        try:
            from websearch_api import run_login_setup
        except ImportError:
            from .websearch_api import run_login_setup  # type: ignore

        asyncio.run(run_login_setup())
        raise SystemExit(0)

    # Suppress uvicorn access logs for /health endpoint
    class _HealthCheckFilter(_logging.Filter):
        def filter(self, record: _logging.LogRecord) -> bool:
            return "/health" not in record.getMessage()

    _logging.getLogger("uvicorn.access").addFilter(_HealthCheckFilter())

    # Windows ProactorEventLoop noise: when a remote server closes a keep-alive
    # connection, the transport's _call_connection_lost callback tries to
    # socket.shutdown() on an already-closed socket and raises
    # ConnectionResetError [WinError 10054]. This is harmless (the HTTP
    # response has already been delivered) but pollutes the log. Suppress the
    # asyncio "Exception in callback _ProactorBasePipeTransport..." messages.
    if sys.platform == "win32":

        class _ProactorNoiseFilter(_logging.Filter):
            _NOISE_SIGS = (
                "_call_connection_lost",
                "ConnectionResetError",
                "[WinError 10054]",
            )

            def filter(self, record: _logging.LogRecord) -> bool:
                msg = record.getMessage()
                return not any(sig in msg for sig in self._NOISE_SIGS)

        _logging.getLogger("asyncio").addFilter(_ProactorNoiseFilter())

    # Windows: Playwright/Chromium can block graceful shutdown.
    # Force hard-exit on SIGINT/SIGTERM so Ctrl+C always works.
    if sys.platform == "win32":

        def _force_exit(signum, frame):
            try:
                from reranker_sidecar import stop_reranker_sidecar

                stop_reranker_sidecar()
            except Exception:
                pass
            import os

            os._exit(0)

        signal.signal(signal.SIGINT, _force_exit)
        signal.signal(signal.SIGTERM, _force_exit)

    # Start Qwen3-Reranker (:8111) in the background so websearch HTTP can
    # accept traffic immediately. The guardian handles readiness and restarts.
    try:
        from reranker_sidecar import start_reranker_sidecar

        def _start_reranker_background() -> None:
            try:
                start_reranker_sidecar()
            except Exception as e:
                print(f"[WebSearch] Reranker sidecar background start failed: {e}")

        threading.Thread(target=_start_reranker_background, daemon=True, name="reranker-start").start()
    except Exception as e:
        print(f"[WebSearch] Reranker sidecar start skipped: {e}")

    # First-deploy Bing login status → status.json for Service Manager.
    try:
        _plugins = _os.path.abspath(_os.path.join(_here, "..", ".."))
        if _plugins not in sys.path:
            sys.path.insert(0, _plugins)
        from websearch.setup_status import get_setup_status, write_plugin_status

        _setup = get_setup_status()
        write_plugin_status(_setup)
        if _setup.get("needs_bing_login"):
            print("[WebSearch] Bing login recommended (first deploy).")
            print(f"[WebSearch] Run: {_setup.get('setup_command')}")
        else:
            print("[WebSearch] Bing browser profile looks ready.")
    except Exception as e:
        print(f"[WebSearch] Setup status write skipped: {e}")

    port = _resolve_service_port()
    _headless = _os.environ.get("WEBSEARCH_HEADLESS", "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }
    print(f"[WebSearch Service] Starting on port {port} (headless={_headless})")
    print("[WebSearch] Tip: python service/main.py --login-setup  (headed Bing login into persistent profile)")
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=port,
        timeout_graceful_shutdown=3,
        # Force h11 (pure-Python) instead of httptools (C extension). The
        # Agent Python runtime may have a broken/missing httptools C ext
        # (parser.pyd), causing `AttributeError: module 'httptools' has no
        # attribute 'HttpRequestParser'` at request time. h11 is always
        # available (bundled with uvicorn) and has no native deps.
        http="h11",
    )
