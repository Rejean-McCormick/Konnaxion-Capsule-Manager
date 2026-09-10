"""
Pydantic schemas for Konnaxion Capsule Manager.

These schemas define the user-facing Manager API contract. The Manager should
store and transmit canonical values only: KX_* variables, canonical network
profiles, canonical exposure modes, canonical instance states, canonical
backup/restore/rollback statuses, and canonical Security Gate status/check
values.

The Manager does not execute privileged operations directly. Mutating requests
should be converted to allowlisted Konnaxion Agent actions.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from kx_shared.konnaxion_constants import (
    APP_VERSION,
    CAPSULE_EXTENSION,
    DEFAULT_CAPSULE_ID,
    DEFAULT_CAPSULE_VERSION,
    DEFAULT_EXPOSURE_MODE,
    DEFAULT_INSTANCE_ID,
    DEFAULT_NETWORK_PROFILE,
    KX_BACKUPS_ROOT,
    PARAM_VERSION,
    BackupStatus,
    ExposureMode,
    InstanceState,
    NetworkProfile,
    RestoreStatus,
    RollbackStatus,
    SecurityGateCheck,
    SecurityGateStatus,
)

try:
    from kx_agent.actions import AgentActionName, ActionStatus
except Exception:  # pragma: no cover - keeps schemas importable during early scaffolding
    class AgentActionName(StrEnum):
        CAPSULE_VERIFY = "capsule.verify"
        CAPSULE_IMPORT = "capsule.import"
        INSTANCE_CREATE = "instance.create"
        INSTANCE_START = "instance.start"
        INSTANCE_STOP = "instance.stop"
        INSTANCE_STATUS = "instance.status"
        INSTANCE_LOGS = "instance.logs"
        INSTANCE_BACKUP = "instance.backup"
        INSTANCE_RESTORE = "instance.restore"
        INSTANCE_RESTORE_NEW = "instance.restore_new"
        INSTANCE_UPDATE = "instance.update"
        INSTANCE_ROLLBACK = "instance.rollback"
        INSTANCE_HEALTH = "instance.health"
        BACKUP_LIST = "backup.list"
        BACKUP_VERIFY = "backup.verify"
        BACKUP_TEST_RESTORE = "backup.test_restore"
        SECURITY_CHECK = "security.check"
        NETWORK_SET_PROFILE = "network.set_profile"

    class ActionStatus(StrEnum):
        ACCEPTED = "accepted"
        RUNNING = "running"
        SUCCEEDED = "succeeded"
        FAILED = "failed"
        BLOCKED = "blocked"
        NOT_ALLOWED = "not_allowed"


# ---------------------------------------------------------------------
# Base schemas
# ---------------------------------------------------------------------


class KxSchema(BaseModel):
    """Base schema for all Manager API payloads."""

    model_config = ConfigDict(
        extra="forbid",
        use_enum_values=False,
        validate_assignment=True,
        str_strip_whitespace=True,
        populate_by_name=True,
    )


class ErrorPayload(KxSchema):
    """Stable API-safe error payload."""

    code: str = Field(..., examples=["KX_SECURITY_GATE_BLOCKING"])
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    exit_code: int = 1
    http_status: int = 500


class ApiResponse(KxSchema):
    """Generic API response envelope."""

    ok: bool
    message: str = ""
    data: dict[str, Any] = Field(default_factory=dict)
    error: ErrorPayload | None = None


class Pagination(KxSchema):
    limit: int = Field(default=50, ge=1, le=500)
    offset: int = Field(default=0, ge=0)
    total: int | None = Field(default=None, ge=0)


# ---------------------------------------------------------------------
# Canonical identity and env schemas
# ---------------------------------------------------------------------


class ProductInfo(KxSchema):
    product_name: Literal["Konnaxion"] = "Konnaxion"
    manager_name: Literal["Konnaxion Capsule Manager"] = "Konnaxion Capsule Manager"
    app_version: str = APP_VERSION
    param_version: str = PARAM_VERSION


class KxRuntimeVariables(KxSchema):
    """Canonical KX_* runtime variables exposed by the Manager."""

    kx_instance_id: str = Field(default=DEFAULT_INSTANCE_ID, alias="KX_INSTANCE_ID")
    kx_capsule_id: str = Field(default=DEFAULT_CAPSULE_ID, alias="KX_CAPSULE_ID")
    kx_capsule_version: str = Field(default=DEFAULT_CAPSULE_VERSION, alias="KX_CAPSULE_VERSION")
    kx_app_version: str = Field(default=APP_VERSION, alias="KX_APP_VERSION")
    kx_param_version: str = Field(default=PARAM_VERSION, alias="KX_PARAM_VERSION")
    kx_network_profile: NetworkProfile = Field(default=DEFAULT_NETWORK_PROFILE, alias="KX_NETWORK_PROFILE")
    kx_exposure_mode: ExposureMode = Field(default=DEFAULT_EXPOSURE_MODE, alias="KX_EXPOSURE_MODE")
    kx_public_mode_enabled: bool = Field(default=False, alias="KX_PUBLIC_MODE_ENABLED")
    kx_public_mode_expires_at: datetime | None = Field(default=None, alias="KX_PUBLIC_MODE_EXPIRES_AT")
    kx_backup_enabled: bool = Field(default=True, alias="KX_BACKUP_ENABLED")
    kx_backup_root: Path = Field(default=KX_BACKUPS_ROOT, alias="KX_BACKUP_ROOT")
    kx_host: str | None = Field(default=None, alias="KX_HOST")

    @model_validator(mode="after")
    def validate_public_expiration(self) -> "KxRuntimeVariables":
        validate_profile_exposure(self.kx_network_profile, self.kx_exposure_mode)

        if self.kx_network_profile == NetworkProfile.PUBLIC_TEMPORARY and self.kx_public_mode_expires_at is None:
            raise ValueError("KX_PUBLIC_MODE_EXPIRES_AT is required for public_temporary")

        if self.kx_exposure_mode == ExposureMode.TEMPORARY_TUNNEL and self.kx_public_mode_expires_at is None:
            raise ValueError("KX_PUBLIC_MODE_EXPIRES_AT is required for temporary_tunnel exposure")

        if self.kx_exposure_mode == ExposureMode.PUBLIC and not self.kx_host:
            raise ValueError("KX_HOST is required for public exposure")

        return self


# ---------------------------------------------------------------------
# Capsule schemas
# ---------------------------------------------------------------------


class CapsuleSummary(KxSchema):
    capsule_id: str
    capsule_version: str
    app_version: str = APP_VERSION
    param_version: str = PARAM_VERSION
    channel: str = "demo"
    path: Path | None = None
    imported_at: datetime | None = None
    verified: bool = False
    signed: bool = True


class CapsuleManifestSummary(KxSchema):
    schema_version: str = "kxcap/v1"
    capsule_id: str
    capsule_version: str
    app_name: Literal["Konnaxion"] = "Konnaxion"
    app_version: str = APP_VERSION
    param_version: str = PARAM_VERSION
    network_profiles: list[NetworkProfile] = Field(default_factory=list)
    services: list[str] = Field(default_factory=list)
    images: list[str] = Field(default_factory=list)


class CapsuleVerifyRequest(KxSchema):
    capsule_path: Path

    @field_validator("capsule_path")
    @classmethod
    def validate_capsule_extension(cls, value: Path) -> Path:
        if value.suffix != CAPSULE_EXTENSION:
            raise ValueError(f"Capsule path must end with {CAPSULE_EXTENSION}")
        return value


class CapsuleVerifyResponse(KxSchema):
    capsule_id: str | None = None
    capsule_version: str | None = None
    valid: bool
    signed: bool
    checksums_valid: bool
    manifest_valid: bool
    security_gate_status: SecurityGateStatus | None = None
    errors: list[ErrorPayload] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class CapsuleImportRequest(CapsuleVerifyRequest):
    target_instance_id: str | None = None
    network_profile: NetworkProfile = DEFAULT_NETWORK_PROFILE
    exposure_mode: ExposureMode = DEFAULT_EXPOSURE_MODE


class CapsuleImportResponse(KxSchema):
    capsule: CapsuleSummary
    instance_id: str | None = None
    action_id: str | None = None
    message: str = "Capsule import accepted."


# ---------------------------------------------------------------------
# Instance schemas
# ---------------------------------------------------------------------


class InstanceSummary(KxSchema):
    instance_id: str
    state: InstanceState
    capsule_id: str | None = None
    capsule_version: str | None = None
    app_version: str = APP_VERSION
    param_version: str = PARAM_VERSION
    network_profile: NetworkProfile = DEFAULT_NETWORK_PROFILE
    exposure_mode: ExposureMode = DEFAULT_EXPOSURE_MODE
    public_mode_enabled: bool = False
    public_mode_expires_at: datetime | None = None
    url: str | None = None
    security_status: SecurityGateStatus | None = None
    backup_enabled: bool = True
    last_backup_id: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class InstanceDetail(InstanceSummary):
    variables: KxRuntimeVariables | None = None
    services: list["ServiceStatus"] = Field(default_factory=list)
    health: "HealthSummary | None" = None
    security_gate: "SecurityGateReport | None" = None


class InstanceCreateRequest(KxSchema):
    instance_id: str = DEFAULT_INSTANCE_ID
    capsule_id: str = DEFAULT_CAPSULE_ID
    network_profile: NetworkProfile = DEFAULT_NETWORK_PROFILE
    exposure_mode: ExposureMode = DEFAULT_EXPOSURE_MODE
    host: str | None = None
    public_mode_enabled: bool = False
    public_mode_expires_at: datetime | None = None
    generate_secrets: bool = True

    @model_validator(mode="after")
    def validate_exposure_matches_profile(self) -> "InstanceCreateRequest":
        validate_profile_exposure(self.network_profile, self.exposure_mode)

        if self.network_profile == NetworkProfile.PUBLIC_TEMPORARY and self.public_mode_expires_at is None:
            raise ValueError("public_temporary requires public_mode_expires_at")

        if self.exposure_mode == ExposureMode.TEMPORARY_TUNNEL and self.public_mode_expires_at is None:
            raise ValueError("temporary_tunnel exposure requires public_mode_expires_at")

        if self.exposure_mode == ExposureMode.PUBLIC and not self.host:
            raise ValueError("public exposure requires host")

        return self


class InstanceActionRequest(KxSchema):
    instance_id: str
    dry_run: bool = False


class InstanceStartRequest(InstanceActionRequest):
    run_security_gate: bool = True
    run_migrations: bool = True
    run_healthchecks: bool = True


class InstanceStopRequest(InstanceActionRequest):
    timeout_seconds: int = Field(default=60, ge=1, le=600)


class InstanceUpdateRequest(InstanceActionRequest):
    capsule_id: str
    capsule_path: Path | None = None
    create_pre_update_backup: bool = True
    run_migrations: bool = True
    run_security_gate: bool = True
    run_healthchecks: bool = True


class InstanceRollbackRequest(InstanceActionRequest):
    rollback_id: str | None = None
    target_capsule_id: str | None = None
    restore_data: bool = True
    run_healthchecks: bool = True


class InstanceListResponse(KxSchema):
    items: list[InstanceSummary]
    pagination: Pagination


# ---------------------------------------------------------------------
# Service, logs, and health schemas
# ---------------------------------------------------------------------


class ServiceStatus(KxSchema):
    service: str
    running: bool
    healthy: bool | None = None
    image: str | None = None
    ports: list[str] = Field(default_factory=list)
    started_at: datetime | None = None
    message: str = ""


class LogRequest(KxSchema):
    instance_id: str
    service: str | None = None
    lines: int = Field(default=200, ge=1, le=5000)
    since: datetime | None = None


class LogLine(KxSchema):
    timestamp: datetime | None = None
    service: str | None = None
    stream: Literal["stdout", "stderr", "system", "agent"] = "system"
    message: str


class LogResponse(KxSchema):
    instance_id: str
    service: str | None = None
    lines: list[LogLine]


class HealthCheckResult(KxSchema):
    name: str
    status: Literal["healthy", "degraded", "unhealthy", "unknown"]
    message: str = ""
    details: dict[str, Any] = Field(default_factory=dict)
    checked_at: datetime | None = None


class HealthSummary(KxSchema):
    instance_id: str
    healthy: bool
    status: Literal["healthy", "degraded", "unhealthy", "unknown"]
    checks: list[HealthCheckResult] = Field(default_factory=list)
    checked_at: datetime | None = None


# ---------------------------------------------------------------------
# Security Gate schemas
# ---------------------------------------------------------------------


class SecurityCheckResultSchema(KxSchema):
    check: SecurityGateCheck
    status: SecurityGateStatus
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    blocking: bool = False


class SecurityGateReport(KxSchema):
    instance_id: str
    status: SecurityGateStatus
    results: list[SecurityCheckResultSchema]
    blocking_failures: list[SecurityCheckResultSchema] = Field(default_factory=list)
    checked_at: datetime | None = None

    @model_validator(mode="after")
    def validate_status(self) -> "SecurityGateReport":
        has_blocking = any(result.status == SecurityGateStatus.FAIL_BLOCKING for result in self.results)
        if has_blocking and self.status != SecurityGateStatus.FAIL_BLOCKING:
            raise ValueError("Security Gate status must be FAIL_BLOCKING when any result is FAIL_BLOCKING")
        return self


class SecurityCheckRequest(KxSchema):
    instance_id: str
    include_non_blocking: bool = True
    dry_run: bool = False


class SecurityCheckResponse(KxSchema):
    report: SecurityGateReport


# ---------------------------------------------------------------------
# Network schemas
# ---------------------------------------------------------------------


class NetworkProfileSummary(KxSchema):
    profile: NetworkProfile
    exposure_mode: ExposureMode
    public_allowed: bool
    temporary_public: bool = False
    description: str = ""


class NetworkSetProfileRequest(KxSchema):
    instance_id: str
    network_profile: NetworkProfile
    exposure_mode: ExposureMode = DEFAULT_EXPOSURE_MODE
    host: str | None = None
    public_mode_enabled: bool = False
    public_mode_expires_at: datetime | None = None
    capsule_id: str | None = None
    capsule_version: str | None = None
    dry_run: bool = False

    @model_validator(mode="after")
    def validate_network(self) -> "NetworkSetProfileRequest":
        validate_profile_exposure(self.network_profile, self.exposure_mode)

        if self.network_profile == NetworkProfile.PUBLIC_TEMPORARY and self.public_mode_expires_at is None:
            raise ValueError("public_temporary requires public_mode_expires_at")

        if self.exposure_mode == ExposureMode.TEMPORARY_TUNNEL and self.public_mode_expires_at is None:
            raise ValueError("temporary_tunnel exposure requires public_mode_expires_at")

        if self.exposure_mode == ExposureMode.PUBLIC and not self.host:
            raise ValueError("public exposure requires host")

        return self


class NetworkSetProfileResponse(KxSchema):
    instance_id: str
    network_profile: NetworkProfile
    exposure_mode: ExposureMode
    public_mode_enabled: bool
    public_mode_expires_at: datetime | None = None
    url: str | None = None
    security_gate_status: SecurityGateStatus | None = None


# ---------------------------------------------------------------------
# Backup / restore / rollback schemas
# ---------------------------------------------------------------------


class BackupSummary(KxSchema):
    backup_id: str
    instance_id: str
    status: BackupStatus
    backup_class: Literal["manual", "scheduled", "pre_update", "pre_restore", "test_restore"] = "manual"
    path: Path | None = None
    size_bytes: int | None = Field(default=None, ge=0)
    created_at: datetime | None = None
    verified_at: datetime | None = None
    capsule_id: str | None = None
    capsule_version: str | None = None


class BackupCreateRequest(KxSchema):
    instance_id: str
    backup_class: Literal["manual", "scheduled", "pre_update", "pre_restore", "test_restore"] = "manual"
    verify_after_create: bool = True
    dry_run: bool = False


class BackupCreateResponse(KxSchema):
    backup: BackupSummary
    action_id: str | None = None


class BackupListRequest(KxSchema):
    instance_id: str | None = None
    status: BackupStatus | None = None
    pagination: Pagination = Field(default_factory=Pagination)


class BackupListResponse(KxSchema):
    items: list[BackupSummary]
    pagination: Pagination


class BackupVerifyRequest(KxSchema):
    backup_id: str
    instance_id: str | None = None


class BackupVerifyResponse(KxSchema):
    backup_id: str
    verified: bool
    status: BackupStatus
    errors: list[ErrorPayload] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class RestoreRequest(KxSchema):
    source_backup_id: str
    target_instance_id: str
    mode: Literal["same_instance", "new_instance"] = "new_instance"
    new_instance_id: str | None = None
    create_pre_restore_backup: bool = True
    restore_media: bool = True
    restore_env_snapshot: bool = False
    run_migrations: bool = True
    run_security_gate: bool = True
    run_healthchecks: bool = True
    dry_run: bool = False

    @model_validator(mode="after")
    def validate_restore(self) -> "RestoreRequest":
        if self.mode == "new_instance" and not self.new_instance_id:
            raise ValueError("new_instance_id is required when mode=new_instance")

        if self.mode == "same_instance" and not self.create_pre_restore_backup:
            raise ValueError("same_instance restore requires create_pre_restore_backup=true")

        return self


class RestoreStepSchema(KxSchema):
    step: str
    status: RestoreStatus
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    started_at: datetime | None = None
    finished_at: datetime | None = None


class RestoreReportSchema(KxSchema):
    restore_id: str
    source_backup_id: str
    target_instance_id: str
    mode: Literal["same_instance", "new_instance"]
    status: RestoreStatus
    steps: list[RestoreStepSchema]
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: ErrorPayload | None = None
    pre_restore_backup_id: str | None = None


class RestoreResponse(KxSchema):
    report: RestoreReportSchema
    action_id: str | None = None


class RollbackRequest(KxSchema):
    instance_id: str
    rollback_id: str | None = None
    target_capsule_id: str | None = None
    restore_data: bool = True
    dry_run: bool = False


class RollbackReport(KxSchema):
    rollback_id: str
    instance_id: str
    status: RollbackStatus
    target_capsule_id: str | None = None
    steps: list[dict[str, Any]] = Field(default_factory=list)
    error: ErrorPayload | None = None


# ---------------------------------------------------------------------
# Agent action schemas
# ---------------------------------------------------------------------


class AgentActionRequestSchema(KxSchema):
    action: AgentActionName
    params: dict[str, Any] = Field(default_factory=dict)
    request_id: str | None = None
    actor: str | None = None
    dry_run: bool = False
    require_security_gate: bool = True

    @field_validator("params")
    @classmethod
    def reject_raw_command_params(cls, value: dict[str, Any]) -> dict[str, Any]:
        forbidden = {
            "cmd",
            "command",
            "shell",
            "shell_command",
            "exec",
            "subprocess",
            "script",
            "bash",
            "sh",
            "powershell",
        }
        present = sorted(key for key in value if key.lower() in forbidden)
        if present:
            raise ValueError(f"Raw command params are forbidden: {present}")
        return value


class AgentActionResponseSchema(KxSchema):
    action: AgentActionName | str
    status: ActionStatus
    request_id: str
    message: str = ""
    data: dict[str, Any] = Field(default_factory=dict)
    error: ErrorPayload | None = None


# ---------------------------------------------------------------------
# Dashboard schemas
# ---------------------------------------------------------------------


class DashboardSummary(KxSchema):
    product: ProductInfo = Field(default_factory=ProductInfo)
    instances: list[InstanceSummary] = Field(default_factory=list)
    capsules: list[CapsuleSummary] = Field(default_factory=list)
    recent_backups: list[BackupSummary] = Field(default_factory=list)
    security_alerts: list[SecurityCheckResultSchema] = Field(default_factory=list)


# ---------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------


def validate_profile_exposure(profile: NetworkProfile, exposure: ExposureMode) -> None:
    """Validate canonical network profile/exposure combinations."""

    allowed: Mapping[NetworkProfile, set[ExposureMode]] = {
        NetworkProfile.LOCAL_ONLY: {ExposureMode.PRIVATE},
        NetworkProfile.INTRANET_PRIVATE: {ExposureMode.PRIVATE, ExposureMode.LAN},
        NetworkProfile.PRIVATE_TUNNEL: {ExposureMode.PRIVATE, ExposureMode.VPN},
        NetworkProfile.PUBLIC_TEMPORARY: {ExposureMode.TEMPORARY_TUNNEL},
        NetworkProfile.PUBLIC_VPS: {ExposureMode.PUBLIC},
        NetworkProfile.OFFLINE: {ExposureMode.PRIVATE},
    }

    if exposure not in allowed[profile]:
        raise ValueError(
            f"Exposure mode {exposure.value} is not valid for network profile {profile.value}"
        )


def error_payload_from_mapping(payload: Mapping[str, Any]) -> ErrorPayload:
    return ErrorPayload(
        code=str(payload.get("code", "KX_ERROR")),
        message=str(payload.get("message", "")),
        details=dict(payload.get("details", {})),
        exit_code=int(payload.get("exit_code", 1)),
        http_status=int(payload.get("http_status", 500)),
    )


__all__ = [
    "AgentActionRequestSchema",
    "AgentActionResponseSchema",
    "ApiResponse",
    "BackupCreateRequest",
    "BackupCreateResponse",
    "BackupListRequest",
    "BackupListResponse",
    "BackupSummary",
    "BackupVerifyRequest",
    "BackupVerifyResponse",
    "CapsuleImportRequest",
    "CapsuleImportResponse",
    "CapsuleManifestSummary",
    "CapsuleSummary",
    "CapsuleVerifyRequest",
    "CapsuleVerifyResponse",
    "DashboardSummary",
    "ErrorPayload",
    "HealthCheckResult",
    "HealthSummary",
    "InstanceActionRequest",
    "InstanceCreateRequest",
    "InstanceDetail",
    "InstanceListResponse",
    "InstanceRollbackRequest",
    "InstanceStartRequest",
    "InstanceStopRequest",
    "InstanceSummary",
    "InstanceUpdateRequest",
    "KxRuntimeVariables",
    "KxSchema",
    "LogLine",
    "LogRequest",
    "LogResponse",
    "NetworkProfileSummary",
    "NetworkSetProfileRequest",
    "NetworkSetProfileResponse",
    "Pagination",
    "ProductInfo",
    "RestoreReportSchema",
    "RestoreRequest",
    "RestoreResponse",
    "RestoreStepSchema",
    "RollbackReport",
    "RollbackRequest",
    "SecurityCheckRequest",
    "SecurityCheckResponse",
    "SecurityCheckResultSchema",
    "SecurityGateReport",
    "ServiceStatus",
    "error_payload_from_mapping",
    "validate_profile_exposure",
]