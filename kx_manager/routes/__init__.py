"""
Route package entry point for Konnaxion Capsule Manager.

This module centralizes Manager route registration so the application factory can
attach all route modules from one place.

Expected route modules in this package:

- artifacts.py
- capsules.py
- instances.py
- security.py
- network.py
- backups.py
- logs.py

Each route module may expose either:

- router
- get_router()
- register(app)

The helper functions below support FastAPI-style routers while remaining
lightweight enough for tests and future UI/API adapters.
"""

from __future__ import annotations

from importlib import import_module
from types import ModuleType
from typing import Any, Protocol


ROUTE_MODULE_NAMES: tuple[str, ...] = (
    "artifacts",
    "capsules",
    "instances",
    "security",
    "network",
    "backups",
    "logs",
)


class SupportsIncludeRouter(Protocol):
    """Minimal protocol for FastAPI-like apps."""

    def include_router(self, router: Any, *args: Any, **kwargs: Any) -> Any:
        ...


class RouteModuleError(RuntimeError):
    """Raised when a route module is missing its route contract."""


def import_route_module(name: str) -> ModuleType:
    """Import one route module by short name."""

    if name not in ROUTE_MODULE_NAMES:
        raise ValueError(f"unknown manager route module: {name}")
    return import_module(f"{__name__}.{name}")


def load_route_modules() -> tuple[ModuleType, ...]:
    """Import all canonical Manager route modules."""

    return tuple(import_route_module(name) for name in ROUTE_MODULE_NAMES)


def get_module_router(module: ModuleType) -> Any | None:
    """Return a router object from a module if it exposes one."""

    if hasattr(module, "get_router"):
        router = module.get_router()
        if router is None:
            raise RouteModuleError(f"{module.__name__}.get_router() returned None")
        return router

    if hasattr(module, "router"):
        router = getattr(module, "router")
        if router is None:
            raise RouteModuleError(f"{module.__name__}.router is None")
        return router

    return None


def register_route_module(app: SupportsIncludeRouter, module: ModuleType) -> None:
    """Register one route module against an app."""

    if hasattr(module, "register"):
        module.register(app)
        return

    router = get_module_router(module)
    if router is None:
        raise RouteModuleError(
            f"{module.__name__} must expose register(app), router, or get_router()"
        )

    app.include_router(router)


def register_routes(
    app: SupportsIncludeRouter,
    *,
    module_names: tuple[str, ...] = ROUTE_MODULE_NAMES,
) -> SupportsIncludeRouter:
    """Register all Manager routes on a FastAPI-like app.

    Returns the same app for application-factory chaining.
    """

    for name in module_names:
        module = import_route_module(name)
        register_route_module(app, module)

    return app


def route_module_names() -> tuple[str, ...]:
    """Return canonical Manager route module names."""

    return ROUTE_MODULE_NAMES


__all__ = [
    "ROUTE_MODULE_NAMES",
    "RouteModuleError",
    "SupportsIncludeRouter",
    "get_module_router",
    "import_route_module",
    "load_route_modules",
    "register_route_module",
    "register_routes",
    "route_module_names",
]
