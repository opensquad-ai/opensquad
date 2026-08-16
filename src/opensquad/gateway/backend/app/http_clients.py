"""Shared httpx clients for gateway outbound HTTP.

Local launcher / workspace proxy is loopback HTTP (trust_env=False so a
system proxy cannot hijack 127.0.0.1). GitHub / models.dev / OpenRouter
reuse one TLS client so keep-alive and the TLS handshake are not paid
per request.
"""

from __future__ import annotations

import logging
import os
import ssl

import httpx

logger = logging.getLogger(__name__)

_local_client: httpx.AsyncClient | None = None
_tls_client: httpx.AsyncClient | None = None


def ssl_verify() -> bool | ssl.SSLContext:
    """False when OPENQUAD_SSL_VERIFY=0; otherwise a certifi SSLContext or True."""
    if os.environ.get("OPENQUAD_SSL_VERIFY", "1") == "0":
        return False
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return True


def get_local_http_client() -> httpx.AsyncClient:
    """Shared client for launcher / workspace proxy (loopback HTTP)."""
    global _local_client
    if _local_client is None or _local_client.is_closed:
        _local_client = httpx.AsyncClient(timeout=30.0, trust_env=False)
    return _local_client


def get_tls_http_client() -> httpx.AsyncClient:
    """Shared client for outbound HTTPS (GitHub, models.dev, OpenRouter)."""
    global _tls_client
    if _tls_client is None or _tls_client.is_closed:
        _tls_client = httpx.AsyncClient(timeout=60.0, verify=ssl_verify())
    return _tls_client


async def close_shared_http_clients() -> None:
    """Close module-level clients (call from FastAPI lifespan shutdown)."""
    global _local_client, _tls_client
    clients = (_local_client, _tls_client)
    _local_client = None
    _tls_client = None
    for client in clients:
        if client is None:
            continue
        try:
            await client.aclose()
        except Exception:
            logger.warning("error closing shared httpx client", exc_info=True)
