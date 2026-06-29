# -*- coding: utf-8 -*-
"""Routes sub-package."""
from ._main import router
from ._admin import _proxy_get, _proxy_put

__all__ = ["router", "_proxy_get", "_proxy_put"]
