"""
Backup and restore routes for Konnaxion Capsule Manager.

The Manager must not perform privileged filesystem, Docker, database, or restore
operations directly. This module validates user-facing API payloads and delegates
backup/restore work to the local Konnaxion Agent.

Route summary:
- GET    /backups
- GET    /v1/backups
- GET    /instances/{instance_id}/backups
- GET    /v1/instances/{instance_id}/backups
- POST   /instances/{instance_id}/backups
- POST   /v1/instances/{instance_id}/backups
- GET    /backups/{backup_id}
- GET    /v1/backups/{backup_id}
- POST   /backups/{backup_id}/verify
- POST   /v1/backups/{backup_id}/verify
- POST   /backups/verify
- POST   /v1/backups/verify
- POST   /backups/test-restore
- POST   /v1/backups/test-restore
- POST   /instances/{instance_id}/restore
- POST   /v1/instances/{instance_id}/restore
- POST   /instances/restore-new
- POST   /v1/instances/restore-new
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Mapping, Sequence

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status

from kx_manager.defaults import DEFAULT_NETWORK_PROFILE
from pydantic import BaseModel, Field, field_validator

from kx_manager.config import ManagerConfig, ManagerConfigError, load_config


router = APIRouter(tags=["backups"])


class BackupRouteError(RuntimeError):
    """Raised when a Manager backup route cannot complete."""


class BackupClass(StrEnum):
    """Backup class values used by Manager and Agent."""

    MANUAL = "manual"
    SCHEDULED = "scheduled"
    SCHEDULED_DAILY = "scheduled_daily"
    SCHEDULED_WEEKLY = "scheduled_weekly"
    SCHEDULED_MONTHLY = "scheduled_monthly"
    PRE_UPDATE = "pre_update"
    PRE_RESTORE = "pre_restore"


class BackupStatusValue(StrEnum):
    """Canonical backup resource statuses."""

    CREATED = "created"
    RUNNING = "running"
    VERIFYING = "verifying"
    VERIFIED = "verified"
    FAILED = "failed"
    EXPIRED = "expired"
    DELETED = "deleted"
    QUARANTINED = "quarantined"


class RestoreStatusValue(StrEnum):
    """Canonical restore resource statuses."""

    PLANNED = "planned"
    PREFLIGHT = "preflight"
    CREATING_PRE_RESTORE_BACKUP = "creating_pre_restore_backup"
    RESTORING_DATABASE = "restoring_database"
    RESTORING_MEDIA = "restoring_media"
    RUNNING_MIGRATIONS = "running_migrations"
    RUNNING_SECURITY_GATE = "running_security_gate"
    RUNNING_HEALTHCHECKS = "running_healthchecks"
    RESTORED = "restored"
    DEGRADED = "degraded"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


class BackupCreateRequest(BaseModel):
    """Create a backup for an existing Konnaxion Instance."""

    backup_class: BackupClass = Field(default=BackupClass.MANUAL)
    label: str = Field(default="", max_length=120)
    include_database: bool = True
    include_media: bool = True
    include_env_fingerprint: bool = True
    verify_after_create: bool = True
    reason: str = Field(default="", max_length=500)

    @field_validator("label", "reason")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()


class BackupVerifyRequest(BaseModel):
    """Verify a backup artifact."""

    deep: bool = False
    reason: str = Field(default="", max_length=500)

    @field_validator("reason")
    @classmethod
    def strip_reason(cls, value: str) -> str:
        return value.strip()


class BackupVerifyByIdRequest(BackupVerifyRequest):
    """Verify a backup artifact when backup_id arrives in the body."""

    backup_id: str = Field(min_length=1, max_length=160)

    @field_validator("backup_id")
    @classmethod
    def strip_backup_id(cls, value: str) -> str:
        return value.strip()


class RestoreRequest(BaseModel):
    """Restore an existing Konnaxion Instance from a backup."""

    backup_id: str = Field(min_length=1, max_length=160)
    create_pre_restore_backup: bool = True
    run_migrations: bool = True
    run_security_gate: bool = True
    run_healthchecks: bool = True
    reason: str = Field(default="", max_length=500)

    @field_validator("backup_id", "reason")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()


class RestoreNewRequest(BaseModel):
    """Restore a backup into a new Konnaxion Instance."""

    backup_id: str = Field(min_length=1, max_length=160)
    new_instance_id: str = Field(min_length=1, max_length=120)
    network_profile: str = Field(default=DEFAULT_NETWORK_PROFILE, max_length=80)
    exposure_mode: str = Field(default="private", max_length=80)
    run_migrations: bool = True
    run_security_gate: bool = True
    run_healthchecks: bool = True
    reason: str = Field(default="", max_length=500)

    @field_validator(
        "backup_id",
        "new_instance_id",
        "network_profile",
        "exposure_mode",
        "reason",
    )
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()


class BackupTestRestoreRequest(BaseModel):
    """Run a non-destructive test restore for a backup."""

    backup_id: str = Field(min_length=1, max_length=160)
    instance_id: str = Field(default="", max_length=120)
    target_instance_id: str = Field(default="", max_length=120)
    run_migrations: bool = True
    run_security_gate: bool = True
    run_healthchecks: bool = True
    reason: str = Field(default="", max_length=500)

    @field_validator("backup_id", "instance_id", "target_instance_id", "reason")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()


class BackupSummary(BaseModel):
    """Backup summary returned to Manager UI/API clients."""

    backup_id: str
    instance_id: str
    backup_class: str
    status: str
    created_at: str = ""
    completed_at: str = ""
    size_bytes: int | None = None
    verified: bool = False
    label: str = ""
    path: str = ""


class BackupDetail(BaseModel):
    """Detailed backup metadata."""

    backup_id: str
    instance_id: str
    backup_class: str
    status: str
    created_at: str = ""
    completed_at: str = ""
    size_bytes: int | None = None
    verified: bool = False
    label: str = ""
    path: str = ""
    manifest: Mapping[str, Any] = Field(default_factory=dict)
    verification: Mapping[str, Any] = Field(default_factory=dict)


class BackupOperationResponse(BaseModel):
    """Generic response for backup operations."""

    ok: bool
    operation: str
    instance_id: str = ""
    backup_id: str = ""
    status: str = ""
    message: str = ""
    data: Mapping[str, Any] = Field(default_factory=dict)


class RestoreOperationResponse(BaseModel):
    """Generic response for restore operations."""

    ok: bool
    operation: str
    source_backup_id: str
    instance_id: str = ""
    new_instance_id: str = ""
    status: str = ""
    message: str = ""
    data: Mapping[str, Any] = Field(default_factory=dict)


class AgentErrorResponse(BaseModel):
    """Normalized Agent error payload."""

    ok: bool = False
    error: str
    detail: Any = None


class AgentClient:
    """Small HTTP client for Manager-to-Agent backup calls."""

    def __init__(self, config: ManagerConfig) -> None:
        self.config = config
        self.base_url = config.agent.base_url.rstrip("/")
        self.timeout = config.agent.timeout_seconds
        self.headers = self._headers(config)

    async def get(self, path: str, *, params: Mapping[str, Any] | None = None) -> Any:
        return await self._request("GET", path, params=params)

    async def post(
        self,
        path: str,
        *,
        json_body: Mapping[str, Any] | None = None,
    ) -> Any:
        return await self._request("POST", path, json_body=json_body)

    async def first_available(
        self,
        attempts: Sequence[tuple[str, str]],
        *,
        params: Mapping[str, Any] | None = None,
        json_body: Mapping[str, Any] | None = None,
        continue_statuses: frozenset[int] = frozenset({404}),
    ) -> Any:
        """Try Agent paths in order and return the first non-continuation response."""

        last_error: HTTPException | None = None

        for method, path in attempts:
            try:
                return await self._request(
                    method,
                    path,
                    params=params if method.upper() == "GET" else None,
                    json_body=json_body if method.upper() != "GET" else None,
                )
            except HTTPException as exc:
                agent_status = _agent_status_from_http_exception(exc)
                if agent_status in continue_statuses:
                    last_error = exc
                    continue
                raise

        if last_error is not None:
            raise last_error

        raise BackupRouteError("No Agent backup endpoint attempts were configured.")

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json_body: Mapping[str, Any] | None = None,
    ) -> Any:
        url = f"{self.base_url}{path}"

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.request(
                    method.upper(),
                    url,
                    params=strip_empty(params or {}),
                    json=strip_empty(json_body or {}) if method.upper() != "GET" else None,
                    headers=self.headers,
                )
        except httpx.TimeoutException as exc:
            raise BackupRouteError("Konnaxion Agent request timed out.") from exc
        except httpx.HTTPError as exc:
            raise BackupRouteError(f"Cannot reach Konnaxion Agent: {exc}") from exc

        if response.status_code >= 400:
            raise http_exception_from_agent_response(response)

        if not response.content:
            return {}

        try:
            return response.json()
        except ValueError as exc:
            raise BackupRouteError("Konnaxion Agent returned invalid JSON.") from exc

    @staticmethod
    def _headers(config: ManagerConfig) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "konnaxion-manager/backup-routes",
        }

        if config.agent.token:
            headers["Authorization"] = f"Bearer {config.agent.token}"

        return headers


def get_manager_config() -> ManagerConfig:
    """FastAPI dependency that loads validated Manager config."""

    try:
        return load_config(ensure_paths=False, validate=True)
    except ManagerConfigError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "ok": False,
                "error": "manager_config_error",
                "message": str(exc),
            },
        ) from exc


def get_agent_client(
    config: ManagerConfig = Depends(get_manager_config),
) -> AgentClient:
    """FastAPI dependency for a local Agent client."""

    return AgentClient(config)


@router.get(
    "/backups",
    response_model=list[BackupSummary],
    summary="List backups",
)
@router.get(
    "/v1/backups",
    response_model=list[BackupSummary],
    include_in_schema=False,
)
async def list_backups(
    instance_id: str | None = Query(default=None, max_length=120),
    status_filter: BackupStatusValue | None = Query(default=None, alias="status"),
    backup_class: BackupClass | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    agent: AgentClient = Depends(get_agent_client),
) -> list[BackupSummary]:
    """List backups known to the Agent.

    Some staged Agent builds do not expose backup list routes yet. In that case
    the Manager returns an empty list instead of surfacing a raw 404 into the UI.
    """

    if instance_id:
        assert_safe_identifier(instance_id, field_name="instance_id")

    params = {
        "instance_id": instance_id or "",
        "status": status_filter.value if status_filter else "",
        "backup_class": normalize_backup_class(backup_class.value if backup_class else ""),
        "limit": limit,
    }

    attempts: list[tuple[str, str]] = []

    if instance_id:
        quoted_instance = quote_identifier(instance_id)
        attempts.extend(
            [
                ("GET", f"/instances/{quoted_instance}/backups"),
                ("GET", f"/v1/instances/{quoted_instance}/backups"),
            ]
        )

    attempts.extend(
        [
            ("GET", "/backups"),
            ("GET", "/v1/backups"),
        ]
    )

    try:
        payload = await agent.first_available(attempts, params=params)
    except HTTPException as exc:
        if _agent_status_from_http_exception(exc) == 404:
            return []
        raise

    return [BackupSummary(**normalize_backup_summary(item)) for item in as_list(payload)]


@router.get(
    "/instances/{instance_id}/backups",
    response_model=list[BackupSummary],
    summary="List backups for an instance",
)
@router.get(
    "/v1/instances/{instance_id}/backups",
    response_model=list[BackupSummary],
    include_in_schema=False,
)
async def list_instance_backups(
    instance_id: str,
    status_filter: BackupStatusValue | None = Query(default=None, alias="status"),
    backup_class: BackupClass | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    agent: AgentClient = Depends(get_agent_client),
) -> list[BackupSummary]:
    """List backups for one Konnaxion Instance."""

    assert_safe_identifier(instance_id, field_name="instance_id")

    return await list_backups(
        instance_id=instance_id,
        status_filter=status_filter,
        backup_class=backup_class,
        limit=limit,
        agent=agent,
    )


@router.post(
    "/instances/{instance_id}/backups",
    response_model=BackupOperationResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Create backup",
)
@router.post(
    "/v1/instances/{instance_id}/backups",
    response_model=BackupOperationResponse,
    status_code=status.HTTP_202_ACCEPTED,
    include_in_schema=False,
)
async def create_backup(
    instance_id: str,
    request: BackupCreateRequest,
    agent: AgentClient = Depends(get_agent_client),
) -> BackupOperationResponse:
    """Request a backup through the local Agent."""

    assert_safe_identifier(instance_id, field_name="instance_id")

    body = request.model_dump(mode="json")
    body["backup_class"] = normalize_backup_class(body.get("backup_class"))
    body["instance_id"] = instance_id

    payload = await agent.first_available(
        (
            ("POST", "/instances/backup"),
            ("POST", "/v1/instances/backup"),
            ("POST", f"/instances/{quote_identifier(instance_id)}/backups"),
            ("POST", f"/v1/instances/{quote_identifier(instance_id)}/backups"),
        ),
        json_body=body,
    )

    return BackupOperationResponse(
        **normalize_operation_response(
            payload,
            default_operation="backup",
            default_instance_id=instance_id,
        )
    )


@router.get(
    "/backups/{backup_id}",
    response_model=BackupDetail,
    summary="Get backup detail",
)
@router.get(
    "/v1/backups/{backup_id}",
    response_model=BackupDetail,
    include_in_schema=False,
)
async def get_backup(
    backup_id: str,
    agent: AgentClient = Depends(get_agent_client),
) -> BackupDetail:
    """Fetch detailed metadata for one backup."""

    assert_safe_identifier(backup_id, field_name="backup_id")

    payload = await agent.first_available(
        (
            ("GET", f"/backups/{quote_identifier(backup_id)}"),
            ("GET", f"/v1/backups/{quote_identifier(backup_id)}"),
        )
    )

    return BackupDetail(**normalize_backup_detail(as_mapping(payload)))


@router.post(
    "/backups/{backup_id}/verify",
    response_model=BackupOperationResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Verify backup",
)
@router.post(
    "/v1/backups/{backup_id}/verify",
    response_model=BackupOperationResponse,
    status_code=status.HTTP_202_ACCEPTED,
    include_in_schema=False,
)
async def verify_backup(
    backup_id: str,
    request: BackupVerifyRequest,
    agent: AgentClient = Depends(get_agent_client),
) -> BackupOperationResponse:
    """Request backup verification through the local Agent."""

    assert_safe_identifier(backup_id, field_name="backup_id")

    body = request.model_dump(mode="json")
    body["backup_id"] = backup_id

    payload = await agent.first_available(
        (
            ("POST", f"/backups/{quote_identifier(backup_id)}/verify"),
            ("POST", f"/v1/backups/{quote_identifier(backup_id)}/verify"),
            ("POST", "/backups/verify"),
            ("POST", "/v1/backups/verify"),
        ),
        json_body=body,
    )

    return BackupOperationResponse(
        **normalize_operation_response(
            payload,
            default_operation="backup_verify",
            default_backup_id=backup_id,
        )
    )


@router.post(
    "/backups/verify",
    response_model=BackupOperationResponse,
    status_code=status.HTTP_202_ACCEPTED,
    include_in_schema=False,
)
@router.post(
    "/v1/backups/verify",
    response_model=BackupOperationResponse,
    status_code=status.HTTP_202_ACCEPTED,
    include_in_schema=False,
)
async def verify_backup_from_body(
    request: BackupVerifyByIdRequest,
    agent: AgentClient = Depends(get_agent_client),
) -> BackupOperationResponse:
    """Compatibility route used by GUI action dispatcher."""

    return await verify_backup(
        backup_id=request.backup_id,
        request=BackupVerifyRequest(
            deep=request.deep,
            reason=request.reason,
        ),
        agent=agent,
    )


@router.post(
    "/backups/test-restore",
    response_model=RestoreOperationResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Test restore backup",
)
@router.post(
    "/v1/backups/test-restore",
    response_model=RestoreOperationResponse,
    status_code=status.HTTP_202_ACCEPTED,
    include_in_schema=False,
)
async def test_restore_backup(
    request: BackupTestRestoreRequest,
    agent: AgentClient = Depends(get_agent_client),
) -> RestoreOperationResponse:
    """Request a non-destructive test restore through the local Agent."""

    assert_safe_identifier(request.backup_id, field_name="backup_id")

    if request.instance_id:
        assert_safe_identifier(request.instance_id, field_name="instance_id")

    if request.target_instance_id:
        assert_safe_identifier(request.target_instance_id, field_name="target_instance_id")

    body = request.model_dump(mode="json")
    body["test_only"] = True

    payload = await agent.first_available(
        (
            ("POST", "/backups/test-restore"),
            ("POST", "/v1/backups/test-restore"),
            ("POST", "/instances/restore-new"),
            ("POST", "/v1/instances/restore-new"),
        ),
        json_body=body,
    )

    data = normalize_restore_response(
        payload,
        default_operation="test_restore",
        default_source_backup_id=request.backup_id,
        default_instance_id=request.instance_id,
        default_new_instance_id=request.target_instance_id,
    )

    return RestoreOperationResponse(**data)


@router.post(
    "/instances/{instance_id}/restore",
    response_model=RestoreOperationResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Restore instance from backup",
)
@router.post(
    "/v1/instances/{instance_id}/restore",
    response_model=RestoreOperationResponse,
    status_code=status.HTTP_202_ACCEPTED,
    include_in_schema=False,
)
async def restore_instance(
    instance_id: str,
    request: RestoreRequest,
    agent: AgentClient = Depends(get_agent_client),
) -> RestoreOperationResponse:
    """Request restore of an existing Konnaxion Instance."""

    assert_safe_identifier(instance_id, field_name="instance_id")
    assert_safe_identifier(request.backup_id, field_name="backup_id")

    body = request.model_dump(mode="json")
    body["instance_id"] = instance_id

    payload = await agent.first_available(
        (
            ("POST", "/instances/restore"),
            ("POST", "/v1/instances/restore"),
            ("POST", f"/instances/{quote_identifier(instance_id)}/restore"),
            ("POST", f"/v1/instances/{quote_identifier(instance_id)}/restore"),
        ),
        json_body=body,
    )

    data = normalize_restore_response(
        payload,
        default_operation="restore",
        default_source_backup_id=request.backup_id,
        default_instance_id=instance_id,
    )

    return RestoreOperationResponse(**data)


@router.post(
    "/instances/restore-new",
    response_model=RestoreOperationResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Restore backup into a new instance",
)
@router.post(
    "/v1/instances/restore-new",
    response_model=RestoreOperationResponse,
    status_code=status.HTTP_202_ACCEPTED,
    include_in_schema=False,
)
async def restore_new_instance(
    request: RestoreNewRequest,
    agent: AgentClient = Depends(get_agent_client),
) -> RestoreOperationResponse:
    """Request restore of a backup into a new Konnaxion Instance."""

    assert_safe_identifier(request.backup_id, field_name="backup_id")
    assert_safe_identifier(request.new_instance_id, field_name="new_instance_id")

    body = request.model_dump(mode="json")
    body["source_backup_id"] = request.backup_id

    payload = await agent.first_available(
        (
            ("POST", "/instances/restore-new"),
            ("POST", "/v1/instances/restore-new"),
        ),
        json_body=body,
    )

    data = normalize_restore_response(
        payload,
        default_operation="restore_new",
        default_source_backup_id=request.backup_id,
        default_new_instance_id=request.new_instance_id,
    )

    return RestoreOperationResponse(**data)


def as_list(payload: Any) -> list[Mapping[str, Any]]:
    """Normalize Agent list responses."""

    if isinstance(payload, list):
        return [as_mapping(item) for item in payload]

    if isinstance(payload, dict):
        for key in ("backups", "items", "results"):
            value = payload.get(key)
            if isinstance(value, list):
                return [as_mapping(item) for item in value]

        data = payload.get("data")
        if isinstance(data, list):
            return [as_mapping(item) for item in data]

        if isinstance(data, dict):
            for key in ("backups", "items", "results"):
                value = data.get(key)
                if isinstance(value, list):
                    return [as_mapping(item) for item in value]

    raise HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail={
            "ok": False,
            "error": "invalid_agent_response",
            "message": "Agent response did not contain a backup list.",
        },
    )


def as_mapping(payload: Any, *, default_operation: str = "") -> dict[str, Any]:
    """Normalize an Agent object response."""

    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "ok": False,
                "error": "invalid_agent_response",
                "message": "Agent response was not a JSON object.",
            },
        )

    data = dict(payload)

    if default_operation:
        data.setdefault("operation", default_operation)

    return data


def normalize_backup_summary(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize backup summary fields from Agent variants."""

    data = dict(payload)

    if isinstance(data.get("data"), Mapping):
        nested = dict(data["data"])
        for key, value in nested.items():
            data.setdefault(key, value)

    data.setdefault("backup_id", str(data.get("id") or data.get("backup") or ""))
    data.setdefault("instance_id", str(data.get("instance") or ""))
    data.setdefault("backup_class", str(data.get("class") or data.get("type") or "manual"))
    data.setdefault("status", str(data.get("state") or "created"))
    data.setdefault("created_at", "")
    data.setdefault("completed_at", "")
    data.setdefault("verified", bool(data.get("verified_at") or data.get("verified", False)))
    data.setdefault("label", "")
    data.setdefault("path", str(data.get("display_path") or data.get("root_dir") or ""))

    return data


def normalize_backup_detail(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize backup detail fields from Agent variants."""

    data = normalize_backup_summary(payload)
    data.setdefault("manifest", {})
    data.setdefault("verification", {})
    return data


def normalize_operation_response(
    payload: Any,
    *,
    default_operation: str,
    default_instance_id: str = "",
    default_backup_id: str = "",
) -> dict[str, Any]:
    """Normalize Agent action response into BackupOperationResponse shape."""

    data = as_mapping(payload, default_operation=default_operation)

    nested = data.get("data")
    if isinstance(nested, Mapping):
        nested_data = dict(nested)
        for key, value in nested_data.items():
            data.setdefault(key, value)

    data.setdefault("ok", bool(data.get("success", True)))
    data.setdefault("operation", data.get("action") or default_operation)
    data.setdefault("instance_id", default_instance_id)
    data.setdefault("backup_id", default_backup_id or str(data.get("id") or ""))
    data.setdefault("status", str(data.get("state") or data.get("backup_status") or ""))
    data.setdefault("message", "Backup operation accepted.")
    data.setdefault("data", nested if isinstance(nested, Mapping) else {})

    return data


def normalize_restore_response(
    payload: Any,
    *,
    default_operation: str,
    default_source_backup_id: str,
    default_instance_id: str = "",
    default_new_instance_id: str = "",
) -> dict[str, Any]:
    """Normalize Agent action response into RestoreOperationResponse shape."""

    data = as_mapping(payload, default_operation=default_operation)

    nested = data.get("data")
    if isinstance(nested, Mapping):
        nested_data = dict(nested)
        for key, value in nested_data.items():
            data.setdefault(key, value)

    data.setdefault("ok", bool(data.get("success", True)))
    data.setdefault("operation", data.get("action") or default_operation)
    data.setdefault(
        "source_backup_id",
        str(data.get("source_backup_id") or data.get("backup_id") or default_source_backup_id),
    )
    data.setdefault("instance_id", default_instance_id)
    data.setdefault("new_instance_id", default_new_instance_id)
    data.setdefault("status", str(data.get("state") or data.get("restore_status") or ""))
    data.setdefault("message", "Restore operation accepted.")
    data.setdefault("data", nested if isinstance(nested, Mapping) else {})

    return data


def http_exception_from_agent_response(response: httpx.Response) -> HTTPException:
    """Convert an Agent error response to a Manager API error."""

    try:
        payload = response.json()
    except ValueError:
        payload = {
            "ok": False,
            "error": "agent_error",
            "message": response.text,
        }

    status_code = response.status_code

    if status_code >= 500:
        manager_status = status.HTTP_502_BAD_GATEWAY
    elif status_code == 401:
        manager_status = status.HTTP_502_BAD_GATEWAY
    elif status_code == 403:
        manager_status = status.HTTP_403_FORBIDDEN
    elif status_code == 404:
        manager_status = status.HTTP_404_NOT_FOUND
    elif status_code == 409:
        manager_status = status.HTTP_409_CONFLICT
    elif status_code == 422:
        manager_status = status.HTTP_422_UNPROCESSABLE_ENTITY
    else:
        manager_status = status.HTTP_400_BAD_REQUEST

    return HTTPException(
        status_code=manager_status,
        detail={
            "ok": False,
            "error": "agent_request_failed",
            "agent_status_code": status_code,
            "agent_detail": payload,
        },
    )


def assert_safe_identifier(value: str, *, field_name: str) -> None:
    """
    Reject identifiers that could be interpreted as paths or shell fragments.

    Backup and instance IDs are passed to Agent URLs only, but strict validation
    keeps route behavior deterministic.
    """

    if not value or not value.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "ok": False,
                "error": "invalid_identifier",
                "field": field_name,
                "message": f"{field_name} cannot be empty.",
            },
        )

    allowed_chars = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")

    if any(char not in allowed_chars for char in value):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "ok": False,
                "error": "invalid_identifier",
                "field": field_name,
                "message": (
                    f"{field_name} may only contain letters, numbers, dots, "
                    "underscores, and hyphens."
                ),
            },
        )

    forbidden_tokens = ("..", "/", "\\", "$", "`", ";", "|", "&", "\x00", "\\x00")

    if any(token in value for token in forbidden_tokens):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "ok": False,
                "error": "invalid_identifier",
                "field": field_name,
                "message": f"{field_name} contains a forbidden token.",
            },
        )


def quote_identifier(value: str) -> str:
    """Return a safe URL-path identifier after validation."""

    assert_safe_identifier(value, field_name="identifier")
    return value


def normalize_backup_class(value: Any) -> str:
    """Normalize Manager backup class variants to Agent-compatible values."""

    raw = str(getattr(value, "value", value) or "").strip()

    aliases = {
        "scheduled_daily": "scheduled",
        "scheduled_weekly": "scheduled",
        "scheduled_monthly": "scheduled",
    }

    return aliases.get(raw, raw)


def strip_empty(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Remove None and empty string values before sending JSON/query data."""

    return {key: value for key, value in payload.items() if value is not None and value != ""}


def _agent_status_from_http_exception(exc: HTTPException) -> int | None:
    detail = exc.detail

    if isinstance(detail, Mapping):
        agent_status = detail.get("agent_status_code")
        if isinstance(agent_status, int):
            return agent_status

    return exc.status_code


__all__ = [
    "AgentClient",
    "AgentErrorResponse",
    "BackupClass",
    "BackupCreateRequest",
    "BackupDetail",
    "BackupOperationResponse",
    "BackupRouteError",
    "BackupStatusValue",
    "BackupSummary",
    "BackupTestRestoreRequest",
    "BackupVerifyByIdRequest",
    "BackupVerifyRequest",
    "RestoreNewRequest",
    "RestoreOperationResponse",
    "RestoreRequest",
    "RestoreStatusValue",
    "as_list",
    "as_mapping",
    "assert_safe_identifier",
    "create_backup",
    "get_agent_client",
    "get_backup",
    "get_manager_config",
    "http_exception_from_agent_response",
    "list_backups",
    "list_instance_backups",
    "normalize_backup_class",
    "normalize_backup_detail",
    "normalize_backup_summary",
    "normalize_operation_response",
    "normalize_restore_response",
    "quote_identifier",
    "restore_instance",
    "restore_new_instance",
    "router",
    "strip_empty",
    "test_restore_backup",
    "verify_backup",
    "verify_backup_from_body",
]