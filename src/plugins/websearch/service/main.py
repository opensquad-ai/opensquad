import os as _os
import sys
import warnings

warnings.filterwarnings("ignore", category=DeprecationWarning)

# ── sys.path setup ─────────────────────────────────────────
# _here: plugins/websearch/service/
# _project_root: project root (opensquad/) — needed so the Agent Python
# (which has no opensquad package) can import plugins._service_runtime.
#
# In frozen mode, _project_root is the read-only _internal/ dir. APPEND it
# (not insert(0)) so the Agent Python's site-packages wins for third-party
# packages (fastapi, uvicorn, ...). Otherwise `import fastapi` would load
# the loose copy under _internal/fastapi/ but its transitive deps
# (annotated_doc, click) live only in the PYZ archive and crash with
# ModuleNotFoundError. _here (this dir) is still insert(0)'d so that
# `from websearch_api import ...` resolves to the sibling .py file.
_here = _os.path.dirname(_os.path.abspath(__file__))
_project_root = _os.path.abspath(_os.path.join(_here, "..", "..", ".."))
if _here not in sys.path:
    sys.path.insert(0, _here)  # Allow `from websearch_api import ...` (absolute import)
if _project_root not in sys.path:
    if getattr(sys, "frozen", False):
        sys.path.append(_project_root)  # site-packages must win over _internal loose copies
    else:
        sys.path.insert(0, _project_root)  # Allow `from plugins._service_runtime import ...`


from fastapi import FastAPI, HTTPException, Query

# Self-contained runtime helper — does NOT import opensquad (which is not
# available to the Agent Python that runs plugin services in frozen mode).
from plugins._service_runtime import port as _runtime_port
from plugins._service_runtime import workspace_data_dir as _runtime_workspace_data_dir

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


# --- 2. Define API endpoints ---


@app.get("/health", summary="Health check")
async def health():
    return {"status": "ok", "service": "websearch"}


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
    """Cleanup browser singleton on service shutdown."""
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


# --- 3. Run server ---
if __name__ == "__main__":
    import logging as _logging
    import signal

    import uvicorn

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
            import os

            os._exit(0)

        signal.signal(signal.SIGINT, _force_exit)
        signal.signal(signal.SIGTERM, _force_exit)

    port = _resolve_service_port()
    print(f"[WebSearch Service] Starting on port {port}")
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
