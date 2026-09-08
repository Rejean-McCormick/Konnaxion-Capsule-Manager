"""Installed artifact discovery/lifecycle routes for Konnaxion Capsule Manager."""

from __future__ import annotations

from typing import Any, Protocol

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field


router = APIRouter(prefix="/v1/artifacts", tags=["artifacts"])


class AgentArtifactClient(Protocol):
    async def list_artifacts(
        self,
        *,
        kind: str | None = None,
        products_only: bool = False,
        composition_candidates_only: bool = False,
    ) -> dict[str, Any]: ...
    async def get_artifact(self, artifact_id: str) -> dict[str, Any]: ...
    async def get_artifact_integration_manifest(self, artifact_id: str) -> dict[str, Any]: ...
    async def remove_artifact(self, *, artifact_id: str, preserve_data: bool = True) -> dict[str, Any]: ...


class ArtifactRemoveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    preserve_data: bool = True


def _agent(request: Request) -> AgentArtifactClient:
    client = getattr(request.app.state, "agent_client", None)
    if client is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Agent client unavailable")
    return client


@router.get("")
@router.get("/")
async def list_installed_artifacts(
    request: Request,
    kind: str | None = Query(default=None),
    products_only: bool = Query(default=False),
    composition_candidates_only: bool = Query(default=False),
) -> dict[str, Any]:
    """Return installed-artifact projections; Spaces should use composition_candidates_only=true."""

    return await _agent(request).list_artifacts(
        kind=kind,
        products_only=products_only,
        composition_candidates_only=composition_candidates_only,
    )


@router.get("/{artifact_id}")
async def installed_artifact(artifact_id: str, request: Request) -> dict[str, Any]:
    return await _agent(request).get_artifact(artifact_id)


@router.get("/{artifact_id}/integration-manifest")
async def installed_artifact_integration_manifest(
    artifact_id: str,
    request: Request,
) -> dict[str, Any]:
    """Return a validated/admitted public contribution without interpreting its UX."""

    return await _agent(request).get_artifact_integration_manifest(artifact_id)


@router.post("/{artifact_id}/remove")
async def remove_installed_artifact(
    artifact_id: str,
    body: ArtifactRemoveRequest,
    request: Request,
) -> dict[str, Any]:
    """Remove an artifact only after Agent dependency/runtime guards pass."""

    return await _agent(request).remove_artifact(
        artifact_id=artifact_id,
        preserve_data=body.preserve_data,
    )


__all__ = ["ArtifactRemoveRequest", "router"]
