"""Public deployment/discovery registry routes for Capsule Manager."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from fastapi import APIRouter, HTTPException, Request, status


router = APIRouter(prefix="/registry", tags=["registry"])


class RegistryAgentClientProtocol(Protocol):
    async def product_registry(self) -> Mapping[str, Any]:
        """Return the Agent-owned public product registry projection."""


@router.get("/products", response_model=dict[str, Any])
async def list_products(request: Request) -> dict[str, Any]:
    """Return installed/admitted PRODUCT artifacts for composition hosts.

    The projection is intentionally UX-agnostic: it contains product identity,
    version, readiness, entrypoints and a reference to an admitted integration
    manifest, but not product navigation/surface definitions.
    """

    agent = getattr(request.app.state, "agent_client", None)
    method = getattr(agent, "product_registry", None)
    if not callable(method):
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail={
                "ok": False,
                "error": "product_registry_not_supported",
                "message": "The current Agent client does not expose product discovery.",
            },
        )

    payload = await method()
    if not isinstance(payload, Mapping):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Agent product registry returned a non-object response.",
        )
    return dict(payload)


__all__ = ["RegistryAgentClientProtocol", "list_products", "router"]
