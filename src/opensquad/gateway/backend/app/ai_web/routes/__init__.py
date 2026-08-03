"""Routes sub-package.

P1-5: ``_admin``/``_market`` are heavy modules (~1.1s import, launcher proxy +
marketplace). They are now mounted lazily via
``app.ai_web.routes._main.ensure_lazy_routers()`` and re-exported here with
PEP 562 ``__getattr__`` so existing ``from app.ai_web.routes import _proxy_get``
call sites keep working without pulling ``_admin`` in at package import time.
"""

from ._main import router

__all__ = ["_proxy_get", "_proxy_put", "router"]


def __getattr__(name):
    # Lazily import the admin proxies only when a call site actually needs them
    # (app.api proxies launcher calls at request time).
    if name in ("_proxy_get", "_proxy_put"):
        from ._admin import _proxy_get, _proxy_put  # noqa: PLC0415

        return {"_proxy_get": _proxy_get, "_proxy_put": _proxy_put}[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
