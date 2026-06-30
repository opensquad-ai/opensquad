"""Routes sub-package."""

from ._admin import _proxy_get, _proxy_put
from ._main import router

__all__ = ["_proxy_get", "_proxy_put", "router"]
