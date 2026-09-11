"""
HTTP API surface for the Konnaxion Agent.

The Agent API is intentionally narrow. It exposes explicit, allowlisted
operations for the Konnaxion Capsule Manager and never accepts arbitrary
shell commands, arbitrary Docker commands, arbitrary host paths, or
unvalidated service names.

The actual privileged work belongs in ``kx_agent.actions`` and related
modules. This file defines request/response contracts and routes.
"""

from __future__ import annotations

from datetime import UTC, datetime
import os
from ipaddress import ip_address
from typing import Any, Literal, Mapping, Protocol

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from kx_shared.konnaxion_constants import (
    AGENT_NAME,
    APP_VERSION,
    DEFAULT_EXPOSURE_MODE,
    DEFAULT_NETWORK_PROFILE,
    DockerService,
    ExposureMode,
    InstanceState,
    KX_ROOT,
    MANAGER_NAME,
    NetworkProfile,
    PARAM_VERSION,
    RestoreStatus,
    RollbackStatus,
    SecurityGateStatus,
)


API_VERSION = "v1"
API_PREFIX = f"/{API_VERSION}"


AgentActionName = Literal[
    "capsule_import",
    "capsule_verify",
    "artifact_remove",
    "instance_create",
    "instance_start",
    "instance_stop",
    "instance_status",
    "instance_logs",
    "instance_backup",
    "instance_restore",
    "instance_restore_new",
    "instance_update",
    "instance_rollback",
    "instance_health",
    "backup_list",
    "backup_verify",
    "backup_test_restore",
    "security_check",
    "network_set_profile",
    "network_disable_public",
    "network_expire_temporary_public",
]


class AgentAPIError(RuntimeError):
    """Raised when an Agent action cannot be completed."""


class AgentActionHandler(Protocol):
    """Interface implemented by the privileged Agent action dispatcher."""

    async def run(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Run a single allowlisted Agent action."""


class UnconfiguredActionHandler:
    """Default handler used when kx_agent.actions cannot be wired in."""

    async def run(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        raise AgentAPIError(
            f"Agent action dispatcher is not configured for action: {action}"
        )


class APIModel(BaseModel):
    """Base Pydantic model for Agent API contracts."""

    model_config = ConfigDict(
        extra="forbid",
        use_enum_values=True,
        str_strip_whitespace=True,
    )


class LenientAPIModel(BaseModel):
    """Base model for Manager UI bridge endpoints.

    These endpoints accept Manager/UI form payloads and ignore display-only
    fields such as ``action`` or ``confirmed`` while still forwarding only a
    constrained allowlisted payload to the Agent dispatcher.
    """

    model_config = ConfigDict(
        extra="ignore",
        use_enum_values=True,
        str_strip_whitespace=True,
    )


class HealthResponse(APIModel):
    status: Literal["ok"]
    service: str
    api_version: str
    app_version: str
    param_version: str
    timestamp: datetime


class AgentInfoResponse(APIModel):
    service: str
    manager: str
    api_version: str
    app_version: str
    param_version: str
    root_path: str
    default_network_profile: NetworkProfile
    default_exposure_mode: ExposureMode
    allowed_services: list[str]


class ErrorResponse(APIModel):
    error: str
    detail: str | None = None


class InstanceRequest(APIModel):
    instance_id: str = Field(..., min_length=1, max_length=128)


class CapsuleImportRequest(APIModel):
    capsule_path: str = Field(..., min_length=1)
    instance_id: str = Field(..., min_length=1, max_length=128)
    network_profile: NetworkProfile = DEFAULT_NETWORK_PROFILE
    exposure_mode: ExposureMode = DEFAULT_EXPOSURE_MODE
    verify: bool = True
    overwrite: bool = True
    capsule_id: str | None = Field(default=None, max_length=256)


class CapsuleVerifyRequest(APIModel):
    capsule_path: str = Field(..., min_length=1)


class ArtifactRemoveRequest(APIModel):
    artifact_id: str = Field(..., min_length=1, max_length=128)
    preserve_data: bool = True


class InstanceCreateRequest(APIModel):
    """Request body for creating an instance.

    For public_vps, the Manager must send the canonical public runtime host
    during instance creation. Otherwise the first generated env files and
    Traefik Host rules can freeze an old fallback host.

    ``host`` is the canonical public runtime host forwarded to the dispatcher.

    ``domain``, ``public_host``, ``droplet_domain``, and ``droplet_host`` are
    accepted as backwards-compatible input aliases and normalized into ``host``.
    They are not forwarded separately.
    """

    instance_id: str = Field(..., min_length=1, max_length=128)
    capsule_id: str = Field(..., min_length=1, max_length=256)
    network_profile: NetworkProfile = DEFAULT_NETWORK_PROFILE
    exposure_mode: ExposureMode = DEFAULT_EXPOSURE_MODE

    host: str | None = Field(default=None, min_length=1, max_length=253)
    host_aliases: list[str] = Field(default_factory=list)
    public_mode_enabled: bool | None = None
    public_mode_expires_at: datetime | None = None

    # Backward-compatible UI aliases. These are input-only fields.
    domain: str | None = Field(default=None, max_length=253, exclude=True)
    public_host: str | None = Field(default=None, max_length=253, exclude=True)
    droplet_domain: str | None = Field(default=None, max_length=253, exclude=True)
    droplet_host: str | None = Field(default=None, max_length=253, exclude=True)

    generate_secrets: bool = True

    @model_validator(mode="after")
    def normalize_public_host(self) -> "InstanceCreateRequest":
        profile = _enum_value_for_validation(self.network_profile)
        exposure = _enum_value_for_validation(self.exposure_mode)

        canonical_host = (
            _clean_host(self.host)
            or _clean_host(self.public_host)
            or _clean_host(self.domain)
            or _clean_host(self.droplet_domain)
            or _clean_host(self.droplet_host)
        )

        self.host = canonical_host
        self.host_aliases = _clean_host_aliases(self.host_aliases, canonical_host)

        if profile == _enum_value_for_validation(NetworkProfile.PUBLIC_VPS):
            self.public_mode_enabled = True

            if exposure == _enum_value_for_validation(DEFAULT_EXPOSURE_MODE):
                self.exposure_mode = ExposureMode.PUBLIC
                exposure = _enum_value_for_validation(ExposureMode.PUBLIC)

        if exposure in {
            _enum_value_for_validation(ExposureMode.PUBLIC),
            _enum_value_for_validation(ExposureMode.TEMPORARY_TUNNEL),
        }:
            self.public_mode_enabled = True

        public_required = (
            profile == _enum_value_for_validation(NetworkProfile.PUBLIC_VPS)
            or exposure == _enum_value_for_validation(ExposureMode.PUBLIC)
            or exposure == _enum_value_for_validation(ExposureMode.TEMPORARY_TUNNEL)
            or bool(self.public_mode_enabled)
        )

        if public_required and not self.host:
            raise ValueError(
                "host is required for public_vps, public exposure, "
                "temporary public exposure, or public_mode_enabled=true."
            )

        if (
            profile == _enum_value_for_validation(NetworkProfile.PUBLIC_VPS)
            and _is_loopback_or_local_host(self.host)
        ):
            raise ValueError("public_vps host must not be localhost or loopback.")

        if (
            exposure == _enum_value_for_validation(ExposureMode.TEMPORARY_TUNNEL)
            and self.public_mode_expires_at is None
        ):
            raise ValueError(
                "public_mode_expires_at is required for temporary public exposure."
            )

        return self


class InstanceStartRequest(APIModel):
    instance_id: str = Field(..., min_length=1, max_length=128)
    run_security_gate: bool = True

    # Current Manager deploy/start contract. These fields are consumed by the
    # Agent dispatcher to bind image loading/security checks to the capsule
    # that was just imported. Keep them optional for older/local callers.
    capsule_id: str | None = Field(default=None, min_length=1, max_length=256)
    capsule_version: str | None = Field(default=None, min_length=1, max_length=256)
    force_recreate_after_image_load: bool | None = None


class InstanceStopRequest(APIModel):
    instance_id: str = Field(..., min_length=1, max_length=128)
    timeout_seconds: int = Field(default=60, ge=5, le=600)


class InstanceStatusRequest(APIModel):
    instance_id: str = Field(..., min_length=1, max_length=128)


class InstanceLogsRequest(APIModel):
    instance_id: str = Field(..., min_length=1, max_length=128)
    service: DockerService | None = None
    tail: int = Field(default=200, ge=1, le=5000)


class InstanceBackupRequest(APIModel):
    instance_id: str = Field(..., min_length=1, max_length=128)
    backup_class: Literal["manual", "scheduled", "pre_update", "pre_restore"] = "manual"
    verify_after_create: bool = True


class BackupListRequest(LenientAPIModel):
    instance_id: str | None = Field(default=None, max_length=128)
    backup_id: str | None = Field(default=None, max_length=256)
    status: str = Field(default="", max_length=80)
    backup_class: str = Field(default="", max_length=80)
    limit: int = Field(default=50, ge=1, le=500)


class BackupVerifyRequest(LenientAPIModel):
    backup_id: str | None = Field(default=None, max_length=256)
    source_backup_id: str | None = Field(default=None, max_length=256)
    from_backup_id: str | None = Field(default=None, max_length=256)
    instance_id: str | None = Field(default=None, max_length=128)
    deep: bool = False
    reason: str = Field(default="", max_length=500)

    def resolved_backup_id(self) -> str:
        value = self.backup_id or self.source_backup_id or self.from_backup_id
        if not value:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="backup_id is required",
            )
        return value


class BackupVerifyOptions(LenientAPIModel):
    instance_id: str | None = Field(default=None, max_length=128)
    deep: bool = False
    reason: str = Field(default="", max_length=500)


class BackupTestRestoreRequest(LenientAPIModel):
    backup_id: str | None = Field(default=None, max_length=256)
    source_backup_id: str | None = Field(default=None, max_length=256)
    from_backup_id: str | None = Field(default=None, max_length=256)
    instance_id: str | None = Field(default=None, max_length=128)
    target_instance_id: str | None = Field(default=None, max_length=128)
    new_instance_id: str | None = Field(default=None, max_length=128)
    restore_data: bool = True
    test_only: bool = True
    create_pre_restore_backup: bool = False
    run_migrations: bool = True
    run_security_gate: bool = True
    run_healthchecks: bool = True
    reason: str = Field(default="", max_length=500)

    def resolved_backup_id(self) -> str:
        value = self.backup_id or self.source_backup_id or self.from_backup_id
        if not value:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="backup_id is required",
            )
        return value


class InstanceRestoreRequest(APIModel):
    instance_id: str = Field(..., min_length=1, max_length=128)
    backup_id: str = Field(..., min_length=1, max_length=256)
    create_pre_restore_backup: bool = True


class InstanceRestoreNewRequest(APIModel):
    source_backup_id: str = Field(..., min_length=1, max_length=256)
    new_instance_id: str = Field(..., min_length=1, max_length=128)
    network_profile: NetworkProfile = DEFAULT_NETWORK_PROFILE


class InstanceUpdateRequest(APIModel):
    instance_id: str = Field(..., min_length=1, max_length=128)
    capsule_path: str = Field(..., min_length=1)


class InstanceRollbackRequest(APIModel):
    instance_id: str = Field(..., min_length=1, max_length=128)
    target_release_id: str | None = Field(default=None, max_length=256)
    restore_data: bool = True


class InstanceHealthRequest(APIModel):
    instance_id: str = Field(..., min_length=1, max_length=128)


class SecurityCheckRequest(APIModel):
    instance_id: str = Field(..., min_length=1, max_length=128)
    blocking: bool = True


class NetworkSetProfileRequest(APIModel):
    """Request body for changing an instance network profile.

    ``host`` is the canonical public host forwarded to the Agent dispatcher.
    The Manager should send ``host`` directly.

    ``domain``, ``public_host``, and ``droplet_host`` are accepted only as
    backwards-compatible UI bridge aliases. They are normalized into ``host``
    and are not forwarded to the dispatcher as separate keys.
    """

    instance_id: str = Field(..., min_length=1, max_length=128)
    network_profile: NetworkProfile
    exposure_mode: ExposureMode = DEFAULT_EXPOSURE_MODE
    host: str | None = Field(default=None, min_length=1, max_length=253)
    public_mode_enabled: bool = False
    public_mode_expires_at: datetime | None = None

    # Capsule identity is part of the current Manager -> Agent network
    # contract. network_set_profile regenerates runtime compose/env state and
    # must stay bound to the capsule that was just imported.
    capsule_id: str | None = Field(default=None, min_length=1, max_length=256)
    capsule_version: str | None = Field(default=None, min_length=1, max_length=256)

    # Backward-compatible aliases from older Manager/UI payloads.
    # These are input-only fields; model_dump() will not forward them.
    domain: str | None = Field(default=None, max_length=253, exclude=True)
    public_host: str | None = Field(default=None, max_length=253, exclude=True)
    droplet_host: str | None = Field(default=None, max_length=253, exclude=True)

    @model_validator(mode="after")
    def normalize_public_host(self) -> "NetworkSetProfileRequest":
        profile = _enum_value_for_validation(self.network_profile)
        exposure = _enum_value_for_validation(self.exposure_mode)

        canonical_host = (
            _clean_host(self.host)
            or _clean_host(self.public_host)
            or _clean_host(self.domain)
            or _clean_host(self.droplet_host)
        )

        self.host = canonical_host

        if profile == _enum_value_for_validation(NetworkProfile.PUBLIC_VPS):
            self.public_mode_enabled = True

            if exposure == _enum_value_for_validation(DEFAULT_EXPOSURE_MODE):
                self.exposure_mode = ExposureMode.PUBLIC

        if exposure in {
            _enum_value_for_validation(ExposureMode.PUBLIC),
            _enum_value_for_validation(ExposureMode.TEMPORARY_TUNNEL),
        }:
            self.public_mode_enabled = True

        public_required = (
            profile == _enum_value_for_validation(NetworkProfile.PUBLIC_VPS)
            or exposure == _enum_value_for_validation(ExposureMode.PUBLIC)
            or exposure == _enum_value_for_validation(ExposureMode.TEMPORARY_TUNNEL)
            or self.public_mode_enabled
        )

        if public_required and not self.host:
            raise ValueError(
                "host is required for public_vps, public exposure, "
                "temporary public exposure, or public_mode_enabled=true."
            )

        if (
            profile == _enum_value_for_validation(NetworkProfile.PUBLIC_VPS)
            and _is_loopback_or_local_host(self.host)
        ):
            raise ValueError("public_vps host must not be localhost or loopback.")

        return self


class ActionResponse(APIModel):
    ok: bool
    action: str
    instance_id: str | None = None
    state: InstanceState | None = None
    security_status: SecurityGateStatus | None = None
    restore_status: RestoreStatus | None = None
    rollback_status: RollbackStatus | None = None
    message: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)


def utc_now() -> datetime:
    """Return a timezone-aware UTC timestamp."""

    return datetime.now(UTC)


def get_action_handler(request: Request) -> AgentActionHandler:
    """Resolve the Agent action handler from FastAPI app state."""

    handler = getattr(request.app.state, "action_handler", None)
    if handler is None:
        return UnconfiguredActionHandler()
    return handler


def enum_value(value: Any) -> str:
    """Return the string value for enum-like values."""

    return str(getattr(value, "value", value))


def _enum_value_for_validation(value: Any) -> str:
    """Return the enum value string used inside Pydantic validators."""

    return str(getattr(value, "value", value))


def _clean_host(value: str | None) -> str | None:
    """Normalize an optional public host field."""

    if value is None:
        return None

    cleaned = str(value).strip()
    if not cleaned:
        return None

    cleaned = cleaned.removeprefix("https://").removeprefix("http://").strip("/")
    return cleaned or None


def _host_without_port(value: str | None) -> str | None:
    """Return a hostname/IP without a URL scheme, brackets, port, or path."""

    cleaned = _clean_host(value)
    if not cleaned:
        return None

    cleaned = cleaned.split("/", 1)[0].strip()

    if cleaned.startswith("["):
        bracket_index = cleaned.find("]")
        if bracket_index > 0:
            return cleaned[1:bracket_index].strip() or None

    if cleaned.count(":") == 1:
        host, maybe_port = cleaned.rsplit(":", 1)
        if maybe_port.isdigit():
            return host.strip() or None

    return cleaned or None


def _is_loopback_or_local_host(value: str | None) -> bool:
    """Return whether a host is localhost or a loopback IP address."""

    host = _host_without_port(value)
    if not host:
        return False

    normalized = host.strip().rstrip(".").lower()

    if normalized in {
        "localhost",
        "localhost.localdomain",
        "ip6-localhost",
        "ip6-loopback",
    }:
        return True

    try:
        return ip_address(normalized).is_loopback
    except ValueError:
        return False


def _clean_host_aliases(
    values: list[str] | tuple[str, ...] | None,
    canonical_host: str | None = None,
) -> list[str]:
    """Normalize host aliases, remove duplicates, and drop the canonical host."""

    aliases: list[str] = []
    seen: set[str] = set()

    primary = _clean_host(canonical_host)
    primary_key = primary.lower() if primary else None

    for value in values or []:
        cleaned = _clean_host(str(value))
        if not cleaned:
            continue

        key = cleaned.lower()

        if primary_key and key == primary_key:
            continue

        if key in seen:
            continue

        aliases.append(cleaned)
        seen.add(key)

    return aliases


def model_payload(payload: APIModel | LenientAPIModel | Mapping[str, Any]) -> dict[str, Any]:
    """Render a safe dispatcher payload from a request model or mapping."""

    if isinstance(payload, BaseModel):
        return payload.model_dump(mode="json", exclude_none=True)

    return {str(key): value for key, value in dict(payload).items() if value is not None}


async def call_agent_handler(
    handler: AgentActionHandler,
    action: AgentActionName,
    payload: APIModel | LenientAPIModel | Mapping[str, Any],
) -> dict[str, Any]:
    """Execute one Agent action and return the raw dispatcher mapping."""

    try:
        result = await handler.run(action, model_payload(payload))
    except AgentAPIError as exc:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail=str(exc),
        ) from exc
    except PermissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    if not isinstance(result, Mapping):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Agent action handler returned a non-object response",
        )

    return dict(result)


async def run_agent_action(
    handler: AgentActionHandler,
    action: AgentActionName,
    payload: APIModel | LenientAPIModel | Mapping[str, Any],
) -> ActionResponse:
    """Execute one allowlisted Agent action and normalize API errors."""

    result = await call_agent_handler(handler, action, payload)

    return ActionResponse(
        ok=bool(result.get("ok", True)),
        action=str(result.get("action") or action),
        instance_id=result.get("instance_id"),
        state=result.get("state"),
        security_status=result.get("security_status"),
        restore_status=result.get("restore_status"),
        rollback_status=result.get("rollback_status"),
        message=result.get("message"),
        data=mapping_data(result.get("data", {})),
    )


def mapping_data(value: Any) -> dict[str, Any]:
    """Normalize arbitrary response data into an object for ActionResponse."""

    if value is None:
        return {}

    if isinstance(value, Mapping):
        return dict(value)

    return {"result": value}


def ensure_action_ok(result: Mapping[str, Any], *, action: str) -> None:
    """Convert failed dispatcher results into HTTP failures."""

    if bool(result.get("ok", True)):
        return

    data = result.get("data")
    error = data.get("error") if isinstance(data, Mapping) else None

    message = (
        result.get("message")
        or (error.get("message") if isinstance(error, Mapping) else None)
        or f"Agent action failed: {action}"
    )

    error_name = ""
    if isinstance(error, Mapping):
        error_name = str(error.get("error") or error.get("code") or "")

    status_code = status.HTTP_400_BAD_REQUEST
    if "not_allowed" in error_name or "not implemented" in str(message).lower():
        status_code = status.HTTP_501_NOT_IMPLEMENTED

    raise HTTPException(status_code=status_code, detail=str(message))


def extract_items(result: Mapping[str, Any], *, action: str) -> list[dict[str, Any]]:
    """Extract a list payload from an Agent action result."""

    ensure_action_ok(result, action=action)

    candidates: list[Any] = [
        result.get("backups"),
        result.get("items"),
        result.get("results"),
        result.get("data"),
    ]

    data = result.get("data")
    if isinstance(data, Mapping):
        candidates.extend(
            [
                data.get("backups"),
                data.get("items"),
                data.get("results"),
            ]
        )

    for candidate in candidates:
        if isinstance(candidate, list):
            return [mapping_data(item) for item in candidate]

    return []


def extract_object(
    result: Mapping[str, Any],
    *,
    action: str,
    key: str,
    identifier: str,
) -> dict[str, Any]:
    """Extract one object payload from an Agent action result."""

    ensure_action_ok(result, action=action)

    data = result.get("data")

    if isinstance(data, Mapping):
        for candidate_key in (key, "item", "result"):
            candidate = data.get(candidate_key)
            if isinstance(candidate, Mapping):
                return dict(candidate)

        for list_key in ("backups", "items", "results"):
            candidate_list = data.get(list_key)
            if isinstance(candidate_list, list):
                for item in candidate_list:
                    item_data = mapping_data(item)
                    if item_data.get("backup_id") == identifier:
                        return item_data

    for candidate_key in (key, "item", "result"):
        candidate = result.get(candidate_key)
        if isinstance(candidate, Mapping):
            return dict(candidate)

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"{key} not found: {identifier}",
    )


router = APIRouter(prefix=API_PREFIX)


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Return basic Agent process health."""

    return HealthResponse(
        status="ok",
        service=AGENT_NAME,
        api_version=API_VERSION,
        app_version=APP_VERSION,
        param_version=PARAM_VERSION,
        timestamp=utc_now(),
    )


@router.get("/agent/info", response_model=AgentInfoResponse)
async def agent_info() -> AgentInfoResponse:
    """Return non-sensitive Agent metadata for the Manager."""

    return AgentInfoResponse(
        service=AGENT_NAME,
        manager=MANAGER_NAME,
        api_version=API_VERSION,
        app_version=APP_VERSION,
        param_version=PARAM_VERSION,
        root_path=str(KX_ROOT),
        default_network_profile=DEFAULT_NETWORK_PROFILE,
        default_exposure_mode=DEFAULT_EXPOSURE_MODE,
        allowed_services=[service.value for service in DockerService],
    )


@router.post("/capsules/import", response_model=ActionResponse)
async def import_capsule(
    payload: CapsuleImportRequest,
    handler: AgentActionHandler = Depends(get_action_handler),
) -> ActionResponse:
    return await run_agent_action(handler, "capsule_import", payload)


@router.post("/capsules/verify", response_model=ActionResponse)
async def verify_capsule(
    payload: CapsuleVerifyRequest,
    handler: AgentActionHandler = Depends(get_action_handler),
) -> ActionResponse:
    return await run_agent_action(handler, "capsule_verify", payload)


@router.get("/artifacts")
async def artifacts_list(
    kind: str | None = Query(default=None),
    products_only: bool = Query(default=False),
    composition_candidates_only: bool = Query(default=False),
) -> dict[str, Any]:
    from kx_agent.artifacts.registry import ArtifactRegistryError, list_artifacts

    try:
        return list_artifacts(
            kind=kind,
            products_only=products_only,
            composition_candidates_only=composition_candidates_only,
        )
    except (ArtifactRegistryError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/artifacts/{artifact_id}")
async def artifact_detail(artifact_id: str) -> dict[str, Any]:
    from kx_agent.artifacts.registry import ArtifactRegistryError, get_artifact

    try:
        return get_artifact(artifact_id)
    except ArtifactRegistryError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get("/artifacts/{artifact_id}/integration-manifest")
async def artifact_integration_manifest(artifact_id: str) -> dict[str, Any]:
    from kx_agent.artifacts.registry import ArtifactRegistryError, get_integration_manifest

    try:
        return get_integration_manifest(artifact_id)
    except ArtifactRegistryError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post("/artifacts/remove", response_model=ActionResponse)
async def artifact_remove(
    payload: ArtifactRemoveRequest,
    handler: AgentActionHandler = Depends(get_action_handler),
) -> ActionResponse:
    return await run_agent_action(handler, "artifact_remove", payload)


@router.post("/instances/create", response_model=ActionResponse)
async def create_instance(
    payload: InstanceCreateRequest,
    handler: AgentActionHandler = Depends(get_action_handler),
) -> ActionResponse:
    return await run_agent_action(handler, "instance_create", payload)


@router.post("/instances/start", response_model=ActionResponse)
async def start_instance(
    payload: InstanceStartRequest,
    handler: AgentActionHandler = Depends(get_action_handler),
) -> ActionResponse:
    return await run_agent_action(handler, "instance_start", payload)


@router.post("/instances/stop", response_model=ActionResponse)
async def stop_instance(
    payload: InstanceStopRequest,
    handler: AgentActionHandler = Depends(get_action_handler),
) -> ActionResponse:
    return await run_agent_action(handler, "instance_stop", payload)


@router.post("/instances/status", response_model=ActionResponse)
async def instance_status(
    payload: InstanceStatusRequest,
    handler: AgentActionHandler = Depends(get_action_handler),
) -> ActionResponse:
    return await run_agent_action(handler, "instance_status", payload)


@router.post("/instances/logs", response_model=ActionResponse)
async def instance_logs(
    payload: InstanceLogsRequest,
    handler: AgentActionHandler = Depends(get_action_handler),
) -> ActionResponse:
    return await run_agent_action(handler, "instance_logs", payload)


@router.post("/instances/backup", response_model=ActionResponse)
async def backup_instance(
    payload: InstanceBackupRequest,
    handler: AgentActionHandler = Depends(get_action_handler),
) -> ActionResponse:
    return await run_agent_action(handler, "instance_backup", payload)


@router.get("/backups", response_model=list[dict[str, Any]])
async def list_backups(
    instance_id: str | None = Query(default=None, max_length=128),
    status_filter: str | None = Query(default=None, alias="status", max_length=80),
    backup_class: str | None = Query(default=None, max_length=80),
    limit: int = Query(default=50, ge=1, le=500),
    handler: AgentActionHandler = Depends(get_action_handler),
) -> list[dict[str, Any]]:
    """List backups known to the Agent."""

    payload = BackupListRequest(
        instance_id=instance_id,
        status=status_filter or "",
        backup_class=backup_class or "",
        limit=limit,
    )
    result = await call_agent_handler(handler, "backup_list", payload)
    return extract_items(result, action="backup_list")


@router.get("/instances/{instance_id}/backups", response_model=list[dict[str, Any]])
async def list_instance_backups(
    instance_id: str,
    status_filter: str | None = Query(default=None, alias="status", max_length=80),
    backup_class: str | None = Query(default=None, max_length=80),
    limit: int = Query(default=50, ge=1, le=500),
    handler: AgentActionHandler = Depends(get_action_handler),
) -> list[dict[str, Any]]:
    """List backups for one Konnaxion Instance."""

    payload = BackupListRequest(
        instance_id=instance_id,
        status=status_filter or "",
        backup_class=backup_class or "",
        limit=limit,
    )
    result = await call_agent_handler(handler, "backup_list", payload)
    return extract_items(result, action="backup_list")


@router.get("/backups/{backup_id}", response_model=dict[str, Any])
async def get_backup(
    backup_id: str,
    handler: AgentActionHandler = Depends(get_action_handler),
) -> dict[str, Any]:
    """Return metadata for one backup.

    This uses the backup list action with a backup_id filter so the HTTP route
    exists even when the dispatcher keeps one canonical backup listing action.
    """

    payload = BackupListRequest(backup_id=backup_id, limit=1)
    result = await call_agent_handler(handler, "backup_list", payload)
    return extract_object(
        result,
        action="backup_list",
        key="backup",
        identifier=backup_id,
    )


@router.post("/backups/verify", response_model=ActionResponse)
async def verify_backup(
    payload: BackupVerifyRequest,
    handler: AgentActionHandler = Depends(get_action_handler),
) -> ActionResponse:
    """Verify a backup by request body.

    This route supports Manager UI action posts to ``/v1/backups/verify``.
    """

    backup_id = payload.resolved_backup_id()
    data = payload.model_dump(mode="json", exclude_none=True)
    data["backup_id"] = backup_id
    data.pop("source_backup_id", None)
    data.pop("from_backup_id", None)

    return await run_agent_action(handler, "backup_verify", data)


@router.post("/backups/{backup_id}/verify", response_model=ActionResponse)
async def verify_backup_by_id(
    backup_id: str,
    payload: BackupVerifyOptions | None = None,
    handler: AgentActionHandler = Depends(get_action_handler),
) -> ActionResponse:
    """Verify one backup by path parameter."""

    options = payload or BackupVerifyOptions()
    data = options.model_dump(mode="json", exclude_none=True)
    data["backup_id"] = backup_id

    return await run_agent_action(handler, "backup_verify", data)


@router.post("/backups/test-restore", response_model=ActionResponse)
async def test_restore_backup(
    payload: BackupTestRestoreRequest,
    handler: AgentActionHandler = Depends(get_action_handler),
) -> ActionResponse:
    """Run a non-destructive backup test-restore workflow."""

    backup_id = payload.resolved_backup_id()
    data = payload.model_dump(mode="json", exclude_none=True)
    data["backup_id"] = backup_id
    data["test_only"] = True
    data.pop("source_backup_id", None)
    data.pop("from_backup_id", None)

    if data.get("new_instance_id") and not data.get("target_instance_id"):
        data["target_instance_id"] = data["new_instance_id"]

    return await run_agent_action(handler, "backup_test_restore", data)


@router.post("/instances/restore", response_model=ActionResponse)
async def restore_instance(
    payload: InstanceRestoreRequest,
    handler: AgentActionHandler = Depends(get_action_handler),
) -> ActionResponse:
    return await run_agent_action(handler, "instance_restore", payload)


@router.post("/instances/restore-new", response_model=ActionResponse)
async def restore_new_instance(
    payload: InstanceRestoreNewRequest,
    handler: AgentActionHandler = Depends(get_action_handler),
) -> ActionResponse:
    return await run_agent_action(handler, "instance_restore_new", payload)


@router.post("/instances/update", response_model=ActionResponse)
async def update_instance(
    payload: InstanceUpdateRequest,
    handler: AgentActionHandler = Depends(get_action_handler),
) -> ActionResponse:
    return await run_agent_action(handler, "instance_update", payload)


@router.post("/instances/rollback", response_model=ActionResponse)
async def rollback_instance(
    payload: InstanceRollbackRequest,
    handler: AgentActionHandler = Depends(get_action_handler),
) -> ActionResponse:
    return await run_agent_action(handler, "instance_rollback", payload)


@router.post("/instances/health", response_model=ActionResponse)
async def check_instance_health(
    payload: InstanceHealthRequest,
    handler: AgentActionHandler = Depends(get_action_handler),
) -> ActionResponse:
    return await run_agent_action(handler, "instance_health", payload)


@router.post("/security/check", response_model=ActionResponse)
async def security_check(
    payload: SecurityCheckRequest,
    handler: AgentActionHandler = Depends(get_action_handler),
) -> ActionResponse:
    return await run_agent_action(handler, "security_check", payload)


@router.post("/network/set-profile", response_model=ActionResponse)
async def set_network_profile(
    payload: NetworkSetProfileRequest,
    handler: AgentActionHandler = Depends(get_action_handler),
) -> ActionResponse:
    if (
        enum_value(payload.exposure_mode) == enum_value(ExposureMode.TEMPORARY_TUNNEL)
        and payload.public_mode_expires_at is None
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="public_mode_expires_at is required for temporary public exposure",
        )

    return await run_agent_action(handler, "network_set_profile", payload)


@router.post("/network/disable-public", response_model=ActionResponse)
async def disable_public_mode(
    payload: InstanceRequest,
    handler: AgentActionHandler = Depends(get_action_handler),
) -> ActionResponse:
    return await run_agent_action(handler, "network_disable_public", payload)


@router.post("/network/expire-temporary-public", response_model=ActionResponse)
async def expire_temporary_public(
    payload: InstanceRequest,
    handler: AgentActionHandler = Depends(get_action_handler),
) -> ActionResponse:
    return await run_agent_action(handler, "network_expire_temporary_public", payload)



def _env_enabled(name: str, *, default: bool = False) -> bool:
    """Return a strict boolean environment flag for Agent security controls."""

    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _is_protected_agent_write(request: Request) -> bool:
    """Return whether *request* is a privileged Agent API write operation."""

    return (
        request.url.path.startswith(f"{API_PREFIX}/")
        and request.method.upper() not in {"GET", "HEAD", "OPTIONS"}
    )


def _audit_request_event(
    *,
    request: Request,
    outcome: str,
    message: str,
    status_code: int | None = None,
    error: str | None = None,
) -> None:
    """Append one redacted API-boundary audit event."""

    from kx_agent.audit import (
        AuditActor,
        AuditCategory,
        AuditOutcome,
        AuditSeverity,
        audit_agent_action,
    )

    outcome_value = AuditOutcome(outcome)
    severity = (
        AuditSeverity.ERROR
        if outcome_value in {AuditOutcome.FAILED, AuditOutcome.BLOCKED}
        else AuditSeverity.INFO
    )
    remote_host = request.client.host if request.client is not None else None

    audit_agent_action(
        action=f"{request.method.upper()} {request.url.path}",
        category=AuditCategory.AGENT,
        outcome=outcome_value,
        severity=severity,
        message=message,
        actor=AuditActor(
            actor_id="konnaxion-manager",
            actor_type="manager",
            display_name="Konnaxion Capsule Manager",
            source_ip=remote_host,
        ),
        metadata={
            "method": request.method.upper(),
            "path": request.url.path,
            "status_code": status_code,
        },
        error=error,
    )

def create_agent_api(action_handler: AgentActionHandler | None = None) -> FastAPI:
    """Create the FastAPI app used by the Konnaxion Agent service."""

    app = FastAPI(
        title=AGENT_NAME,
        version=APP_VERSION,
        description=(
            "Constrained local API for Konnaxion Capsule Manager privileged actions."
        ),
        responses={
            400: {"model": ErrorResponse},
            401: {"model": ErrorResponse},
            403: {"model": ErrorResponse},
            404: {"model": ErrorResponse},
            501: {"model": ErrorResponse},
        },
    )

    @app.middleware("http")
    async def agent_security_boundary(request: Request, call_next: Any) -> Any:
        """Authenticate and audit privileged local Agent API writes."""

        if not _is_protected_agent_write(request):
            return await call_next(request)

        require_token = _env_enabled("KX_REQUIRE_AGENT_TOKEN")
        require_audit = _env_enabled("KX_REQUIRE_AGENT_AUDIT")

        if require_token:
            from kx_agent.auth import (
                AuthenticationError,
                TokenConfigurationError,
                load_agent_token,
                parse_authorization_header,
                verify_token,
            )

            try:
                provided = parse_authorization_header(request.headers)
                expected = load_agent_token()
                authenticated = verify_token(provided, expected)
            except (AuthenticationError, TokenConfigurationError) as exc:
                if require_audit:
                    _audit_request_event(
                        request=request,
                        outcome="blocked",
                        message="Agent write request rejected by authentication boundary.",
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        error=type(exc).__name__,
                    )
                return JSONResponse(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    content={
                        "error": "unauthorized",
                        "detail": "Authenticated local Agent request required.",
                    },
                )

            if not authenticated:
                if require_audit:
                    _audit_request_event(
                        request=request,
                        outcome="blocked",
                        message="Agent write request rejected by authentication boundary.",
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        error="invalid_bearer_token",
                    )
                return JSONResponse(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    content={
                        "error": "unauthorized",
                        "detail": "Authenticated local Agent request required.",
                    },
                )

        if require_audit:
            try:
                _audit_request_event(
                    request=request,
                    outcome="started",
                    message="Authenticated Agent write request started.",
                )
            except Exception:
                return JSONResponse(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    content={
                        "error": "audit_unavailable",
                        "detail": "Agent audit log is unavailable; write operation refused.",
                    },
                )

        try:
            response = await call_next(request)
        except Exception as exc:
            if require_audit:
                _audit_request_event(
                    request=request,
                    outcome="failed",
                    message="Agent write request raised an exception.",
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    error=type(exc).__name__,
                )
            raise

        if require_audit:
            if response.status_code < 400:
                _audit_request_event(
                    request=request,
                    outcome="succeeded",
                    message="Agent write request completed.",
                    status_code=response.status_code,
                )
            else:
                _audit_request_event(
                    request=request,
                    outcome="failed",
                    message="Agent write request returned an error response.",
                    status_code=response.status_code,
                    error=f"http_{response.status_code}",
                )

        return response

    if action_handler is None:
        try:
            from kx_agent.actions import make_api_action_handler

            action_handler = make_api_action_handler()
        except Exception:
            action_handler = UnconfiguredActionHandler()

    app.state.action_handler = action_handler
    app.include_router(router)
    return app


def create_app(
    *,
    config: Any | None = None,
    action_handler: AgentActionHandler | None = None,
) -> FastAPI:
    """Compatibility factory used by kx_agent.main."""

    app = create_agent_api(action_handler=action_handler)
    app.state.config = config
    return app


app = create_app()


__all__ = [
    "ArtifactRemoveRequest",
    "API_PREFIX",
    "API_VERSION",
    "ActionResponse",
    "AgentAPIError",
    "AgentActionHandler",
    "AgentActionName",
    "AgentInfoResponse",
    "BackupListRequest",
    "BackupTestRestoreRequest",
    "BackupVerifyOptions",
    "BackupVerifyRequest",
    "CapsuleImportRequest",
    "CapsuleVerifyRequest",
    "ErrorResponse",
    "HealthResponse",
    "InstanceBackupRequest",
    "InstanceCreateRequest",
    "InstanceHealthRequest",
    "InstanceLogsRequest",
    "InstanceRequest",
    "InstanceRestoreNewRequest",
    "InstanceRestoreRequest",
    "InstanceRollbackRequest",
    "InstanceStartRequest",
    "InstanceStatusRequest",
    "InstanceStopRequest",
    "InstanceUpdateRequest",
    "NetworkSetProfileRequest",
    "SecurityCheckRequest",
    "UnconfiguredActionHandler",
    "agent_info",
    "app",
    "backup_instance",
    "call_agent_handler",
    "check_instance_health",
    "create_agent_api",
    "create_app",
    "create_instance",
    "disable_public_mode",
    "enum_value",
    "expire_temporary_public",
    "get_action_handler",
    "get_backup",
    "health",
    "import_capsule",
    "instance_logs",
    "instance_status",
    "list_backups",
    "list_instance_backups",
    "restore_instance",
    "restore_new_instance",
    "rollback_instance",
    "router",
    "run_agent_action",
    "security_check",
    "set_network_profile",
    "start_instance",
    "stop_instance",
    "test_restore_backup",
    "update_instance",
    "utc_now",
    "verify_backup",
    "verify_backup_by_id",
    "verify_capsule",
]