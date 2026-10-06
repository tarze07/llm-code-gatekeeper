"""Walidowane endpointy HTTP panelu. Wersja API żyje w ścieżce (`/api/v1`)."""

from __future__ import annotations

from .v1 import router as v1_router
from .v2_endpoints import router as v2_router

__all__ = ["v1_router", "v2_router"]
