"""Konnaxion Capsule Manager API application.

This module builds the Manager-facing FastAPI app and wires the route modules
that speak to the local Konnaxion Agent. The Manager API is intentionally thin:
it exposes operator-safe endpoints and delegates privileged actions to the
Agent instead of touching Docker, firewall, or host state directly.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from kx_manager.client import KonnaxionAgentClient
from kx_manager.config import get_manager_config
from kx_shared.errors import (
    CapsuleError,
    KonnaxionError,
    KonnaxionNetworkError,
    KonnaxionRuntimeError,
    SecurityGateError,
)
from kx_shared.konnaxion_constants import (
    APP_VERSION,
    DEFAULT_EXPOSURE_MODE,
    DEFAULT_NETWORK_PROFILE,
    MANAGER_NAME,
    PARAM_VERSION,
)

try:
    from kx_manager.routes import artifacts, backups, capsules, instances, logs, network, security
except ImportError:  # pragma: no cover - route modules may be generated later.
    artifacts = backups = capsules = instances = logs = network = security = None


API_TITLE = MANAGER_NAME
API_VERSION = "0.1.0"
PUBLIC_API_VERSION = "v1"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load Manager configuration and attach the local Konnaxion Agent client."""

    config = get_manager_config()
    agent_client = KonnaxionAgentClient.from_env()

    app.state.config = config
    app.state.agent_client = agent_client

    try:
        yield
    finally:
        await agent_client.aclose()


def create_app() -> FastAPI:
    """Create and configure the Konnaxion Capsule Manager API."""

    config = get_manager_config()

    app = FastAPI(
        title=API_TITLE,
        version=API_VERSION,
        description=(
            "Operator-facing API for Konnaxion Capsule Manager. "
            "Privileged runtime actions are delegated to Konnaxion Agent."
        ),
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.allowed_cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Requested-With"],
    )

    register_exception_handlers(app)
    register_core_routes(app)
    register_feature_routes(app)

    return app


def register_core_routes(app: FastAPI) -> None:
    """Register built-in Manager API routes."""

    @app.get("/", tags=["system"])
    async def root() -> dict[str, str]:
        """Return basic API metadata."""

        return {
            "name": MANAGER_NAME,
            "service": MANAGER_NAME,
            "api_version": API_VERSION,
            "public_api_version": PUBLIC_API_VERSION,
            "app_version": APP_VERSION,
            "param_version": PARAM_VERSION,
        }

    @app.get("/health", tags=["system"])
    @app.get("/v1/health", tags=["system"])
    async def health() -> dict[str, str | bool]:
        """Return Manager API health."""

        return {
            "ok": True,
            "status": "ok",
            "service": MANAGER_NAME,
            "name": MANAGER_NAME,
            "api_version": PUBLIC_API_VERSION,
            "app_version": APP_VERSION,
            "param_version": PARAM_VERSION,
        }

    @app.get("/config/defaults", tags=["system"])
    async def config_defaults() -> dict[str, str | bool]:
        """Return public, non-secret Manager defaults."""

        return {
            "network_profile": DEFAULT_NETWORK_PROFILE.value,
            "exposure_mode": DEFAULT_EXPOSURE_MODE.value,
            "public_mode_enabled": False,
            "app_version": APP_VERSION,
            "param_version": PARAM_VERSION,
        }


def register_feature_routes(app: FastAPI) -> None:
    """Register optional route modules when they exist."""

    route_modules: tuple[Any, ...] = (
        artifacts,
        capsules,
        instances,
        security,
        network,
        backups,
        logs,
    )

    for module in route_modules:
        router = getattr(module, "router", None)
        if router is not None:
            app.include_router(router)


def register_exception_handlers(app: FastAPI) -> None:
    """Register consistent JSON exception handlers."""

    app.add_exception_handler(KonnaxionError, _konnaxion_error_handler)
    app.add_exception_handler(CapsuleError, _capsule_error_handler)
    app.add_exception_handler(SecurityGateError, _security_gate_error_handler)
    app.add_exception_handler(KonnaxionNetworkError, _network_error_handler)
    app.add_exception_handler(KonnaxionRuntimeError, _runtime_error_handler)
    app.add_exception_handler(HTTPException, _http_exception_handler)
    app.add_exception_handler(Exception, _unhandled_exception_handler)


async def _konnaxion_error_handler(
    request: Request,
    exc: KonnaxionError,
) -> JSONResponse:
    """Handle base Konnaxion domain errors."""

    return _error_response(
        request=request,
        status_code=status.HTTP_400_BAD_REQUEST,
        code="konnaxion_error",
        message=str(exc),
    )


async def _capsule_error_handler(
    request: Request,
    exc: CapsuleError,
) -> JSONResponse:
    """Handle capsule import/verify errors."""

    return _error_response(
        request=request,
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        code="capsule_error",
        message=str(exc),
    )


async def _security_gate_error_handler(
    request: Request,
    exc: SecurityGateError,
) -> JSONResponse:
    """Handle blocking Security Gate errors."""

    return _error_response(
        request=request,
        status_code=status.HTTP_403_FORBIDDEN,
        code="security_gate_error",
        message=str(exc),
    )


async def _network_error_handler(
    request: Request,
    exc: KonnaxionNetworkError,
) -> JSONResponse:
    """Handle network profile and tunnel errors."""

    return _error_response(
        request=request,
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        code="network_error",
        message=str(exc),
    )


async def _runtime_error_handler(
    request: Request,
    exc: KonnaxionRuntimeError,
) -> JSONResponse:
    """Handle runtime, Docker Compose, and log errors."""

    return _error_response(
        request=request,
        status_code=status.HTTP_502_BAD_GATEWAY,
        code="runtime_error",
        message=str(exc),
    )


async def _http_exception_handler(
    request: Request,
    exc: HTTPException,
) -> JSONResponse:
    """Normalize FastAPI HTTP exceptions."""

    detail = exc.detail if isinstance(exc.detail, str) else "HTTP error"
    return _error_response(
        request=request,
        status_code=exc.status_code,
        code="http_error",
        message=detail,
        headers=exc.headers,
    )


async def _unhandled_exception_handler(
    request: Request,
    exc: Exception,
) -> JSONResponse:
    """Hide implementation details for unexpected failures."""

    return _error_response(
        request=request,
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        code="internal_error",
        message="Unexpected Konnaxion Capsule Manager API error.",
    )


def _error_response(
    *,
    request: Request,
    status_code: int,
    code: str,
    message: str,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    """Build a consistent API error payload."""

    return JSONResponse(
        status_code=status_code,
        headers=headers,
        content={
            "error": {
                "code": code,
                "message": message,
                "path": request.url.path,
            }
        },
    )


def get_app() -> FastAPI:
    """Return an application instance for ASGI servers."""

    return create_app()


app = create_app()