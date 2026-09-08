# kx_manager/ui/view_state.py

"""Framework-neutral view-state models for the Konnaxion Capsule Manager GUI.

This module converts Manager/API/action payloads into UI-safe state objects.

It is presentation state only. It must not execute GUI actions, call Docker,
run shell commands, mutate host state, contact the Agent, or perform backup /
runtime operations.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field, is_dataclass, replace
from datetime import UTC, datetime
from enum import Enum, StrEnum
from typing import Any

from kx_manager.defaults import (
    DEFAULT_EXPOSURE_MODE as MANAGER_DEFAULT_EXPOSURE_MODE,
    DEFAULT_NETWORK_PROFILE as MANAGER_DEFAULT_NETWORK_PROFILE,
    DEFAULT_TARGET_MODE as MANAGER_DEFAULT_TARGET_MODE,
)
from kx_shared.konnaxion_constants import (
    BackupStatus,
    DEFAULT_EXPOSURE_MODE,
    DEFAULT_INSTANCE_ID,
    DEFAULT_NETWORK_PROFILE,
    ExposureMode,
    InstanceState,
    NetworkProfile,
    RestoreStatus,
    RollbackStatus,
    SecurityGateStatus,
)

try:
    from kx_manager.ui.static import (
        ACTION_LABELS,
        DEFAULT_REFRESH_SECONDS,
        NAV_ITEMS,
        PAGE_TITLES,
        UI_BASE_PATH,
        canonical_action,
        normalize_payload_aliases,
        title_for_route,
    )
except Exception:  # pragma: no cover - staged-build compatibility.
    ACTION_LABELS = {}
    DEFAULT_REFRESH_SECONDS = 5
    NAV_ITEMS = (("Dashboard", "/ui"),)
    PAGE_TITLES = {"/ui": "Dashboard"}
    UI_BASE_PATH = "/ui"

    def canonical_action(action: Any) -> str:
        return str(getattr(action, "value", action)).strip()

    def normalize_payload_aliases(payload: Mapping[str, Any] | None) -> dict[str, Any]:
        return dict(payload or {})

    def title_for_route(route: str) -> str:
        return PAGE_TITLES.get(route, "Dashboard")


class ViewSeverity(StrEnum):
    """Generic UI severity level."""

    NEUTRAL = "neutral"
    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    DANGER = "danger"
    DISABLED = "disabled"


@dataclass(frozen=True, slots=True)
class ViewBadge:
    """Small display object for status badges."""

    value: str
    label: str
    severity: ViewSeverity = ViewSeverity.NEUTRAL
    title: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(asdict(self))


@dataclass(frozen=True, slots=True)
class ViewNavItem:
    """Navigation item for page rendering."""

    label: str
    href: str
    active: bool = False

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(asdict(self))


@dataclass(frozen=True, slots=True)
class ViewAction:
    """UI action presentation state."""

    action: str
    label: str
    href: str | None = None
    method: str = "POST"
    payload: Mapping[str, Any] = field(default_factory=dict)
    disabled: bool = False
    danger: bool = False
    confirmation: str | None = None

    @classmethod
    def from_action(
        cls,
        action: Any,
        *,
        payload: Mapping[str, Any] | None = None,
        href: str | None = None,
        method: str = "POST",
        disabled: bool = False,
        danger: bool = False,
        confirmation: str | None = None,
    ) -> "ViewAction":
        action_value = canonical_action(action)

        return cls(
            action=action_value,
            label=ACTION_LABELS.get(action_value, _humanize(action_value)),
            href=href,
            method=method.upper(),
            payload=normalize_payload_aliases(payload),
            disabled=disabled,
            danger=danger,
            confirmation=confirmation,
        )

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(asdict(self))


@dataclass(frozen=True, slots=True)
class ViewResult:
    """Normalized GUI action result for rendering."""

    ok: bool
    action: str | None = None
    message: str = ""
    instance_id: str | None = None
    data: Mapping[str, Any] = field(default_factory=dict)
    stdout: str | None = None
    stderr: str | None = None
    returncode: int | None = None

    @classmethod
    def from_value(cls, value: Any) -> "ViewResult | None":
        if value is None:
            return None

        data = to_mapping(value)

        if not data:
            data = {
                "ok": bool(getattr(value, "ok", getattr(value, "success", False))),
                "action": getattr(value, "action", getattr(value, "operation", None)),
                "message": getattr(value, "message", ""),
                "instance_id": getattr(value, "instance_id", None),
                "data": getattr(value, "data", getattr(value, "payload", {})),
                "stdout": getattr(value, "stdout", None),
                "stderr": getattr(value, "stderr", None),
                "returncode": getattr(value, "returncode", None),
            }

        payload_data = data.get("data", data.get("payload", {}))
        if not isinstance(payload_data, Mapping):
            payload_data = {"result": payload_data}

        return cls(
            ok=bool(data.get("ok", data.get("success", False))),
            action=_optional_str(data.get("action") or data.get("operation")),
            message=str(data.get("message") or data.get("detail") or ""),
            instance_id=_optional_str(data.get("instance_id")),
            data=dict(payload_data),
            stdout=_optional_str(data.get("stdout")),
            stderr=_optional_str(data.get("stderr")),
            returncode=_optional_int(data.get("returncode"), default=None),
        )

    @property
    def badge(self) -> ViewBadge:
        return ViewBadge(
            value="ok" if self.ok else "error",
            label="Success" if self.ok else "Failed",
            severity=ViewSeverity.SUCCESS if self.ok else ViewSeverity.DANGER,
        )

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(asdict(self))


@dataclass(frozen=True, slots=True)
class CapsuleViewState:
    """Capsule summary for UI display."""

    capsule_id: str
    capsule_version: str | None = None
    app_version: str | None = None
    channel: str | None = None
    path: str | None = None
    imported_at: str | None = None
    verified: bool = False
    signature_verified: bool | None = None
    checksum_verified: bool | None = None
    signature_status: str | None = None
    security_status: str | None = None
    warnings: tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "CapsuleViewState":
        return cls(
            capsule_id=str(data.get("capsule_id", "")),
            capsule_version=_optional_str(data.get("capsule_version")),
            app_version=_optional_str(data.get("app_version")),
            channel=_optional_str(data.get("channel")),
            path=_optional_str(
                data.get("path")
                or data.get("local_path")
                or data.get("capsule_file")
                or data.get("capsule_path")
            ),
            imported_at=_optional_str(data.get("imported_at")),
            verified=_to_bool(data.get("verified")),
            signature_verified=_optional_bool(data.get("signature_verified")),
            checksum_verified=_optional_bool(data.get("checksum_verified")),
            signature_status=_optional_str(data.get("signature_status")),
            security_status=_optional_str(data.get("security_status")),
            warnings=_tuple_str(data.get("warnings", ())),
        )

    @property
    def badge(self) -> ViewBadge:
        if self.verified or self.signature_verified or self.checksum_verified:
            return ViewBadge("verified", "Verified", ViewSeverity.SUCCESS)

        if self.security_status:
            return badge_for_security_status(self.security_status)

        return ViewBadge("unknown", "Not verified", ViewSeverity.WARNING)

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(asdict(self))


@dataclass(frozen=True, slots=True)
class SecurityCheckViewState:
    """Single Security Gate check result for UI display."""

    check: str
    status: str = SecurityGateStatus.UNKNOWN.value
    message: str | None = None
    remediation: str | None = None
    blocking: bool = False
    blocks_startup: bool = False

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "SecurityCheckViewState":
        return cls(
            check=str(data.get("check", "")),
            status=normalize_security_status(data.get("status")),
            message=_optional_str(data.get("message")),
            remediation=_optional_str(data.get("remediation")),
            blocking=_to_bool(data.get("blocking")),
            blocks_startup=_to_bool(data.get("blocks_startup")),
        )

    @property
    def badge(self) -> ViewBadge:
        return badge_for_security_status(self.status)

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(asdict(self))


@dataclass(frozen=True, slots=True)
class SecurityViewState:
    """Security Gate UI state."""

    status: str = SecurityGateStatus.UNKNOWN.value
    checks: tuple[SecurityCheckViewState, ...] = ()
    checked_at: str | None = None
    can_start: bool = False
    blocking_messages: tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None) -> "SecurityViewState":
        if not data:
            return cls()

        raw_checks = data.get("checks") or data.get("findings") or ()
        checks = tuple(
            SecurityCheckViewState.from_mapping(item)
            for item in raw_checks
            if isinstance(item, Mapping)
        )

        status = normalize_security_status(data.get("status"))

        blocking_messages = tuple(
            check.message or check.check
            for check in checks
            if check.blocking
            or check.blocks_startup
            or check.status == SecurityGateStatus.FAIL_BLOCKING.value
        )

        explicit_can_start = data.get("can_start")
        can_start = (
            _to_bool(explicit_can_start)
            if explicit_can_start not in {None, ""}
            else status in {SecurityGateStatus.PASS.value, SecurityGateStatus.WARN.value}
            and not blocking_messages
        )

        return cls(
            status=status,
            checks=checks,
            checked_at=_optional_str(
                data.get("checked_at")
                or data.get("last_checked_at")
                or data.get("created_at")
            ),
            can_start=can_start,
            blocking_messages=blocking_messages,
        )

    @property
    def badge(self) -> ViewBadge:
        return badge_for_security_status(self.status)

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(asdict(self))


@dataclass(frozen=True, slots=True)
class NetworkViewState:
    """Network profile and exposure state for UI display."""

    network_profile: str = DEFAULT_NETWORK_PROFILE.value
    exposure_mode: str = DEFAULT_EXPOSURE_MODE.value
    host: str | None = None
    domain: str | None = None
    private_url: str | None = None
    public_url: str | None = None
    url: str | None = None
    public_mode_enabled: bool = False
    public_mode_expires_at: str | None = None
    is_public: bool = False

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None) -> "NetworkViewState":
        if not data:
            return cls()

        normalized = normalize_payload_aliases(data)

        network_profile = normalize_network_profile(
            normalized.get("network_profile")
            or normalized.get("profile")
            or normalized.get("KX_NETWORK_PROFILE")
        )
        exposure_mode = normalize_exposure_mode(
            normalized.get("exposure_mode")
            or normalized.get("KX_EXPOSURE_MODE")
        )

        public_mode_enabled = _to_bool(
            normalized.get("public_mode_enabled")
            if "public_mode_enabled" in normalized
            else network_profile
            in {NetworkProfile.PUBLIC_TEMPORARY.value, NetworkProfile.PUBLIC_VPS.value}
        )

        return cls(
            network_profile=network_profile,
            exposure_mode=exposure_mode,
            host=_optional_str(normalized.get("host") or normalized.get("KX_HOST")),
            domain=_optional_str(normalized.get("domain")),
            private_url=_optional_str(normalized.get("private_url")),
            public_url=_optional_str(normalized.get("public_url")),
            url=_optional_str(
                normalized.get("url")
                or normalized.get("runtime_url")
                or normalized.get("public_url")
                or normalized.get("private_url")
            ),
            public_mode_enabled=public_mode_enabled,
            public_mode_expires_at=_optional_str(
                normalized.get("public_mode_expires_at")
                or normalized.get("KX_PUBLIC_MODE_EXPIRES_AT")
            ),
            is_public=public_mode_enabled
            or exposure_mode in {ExposureMode.TEMPORARY_TUNNEL.value, ExposureMode.PUBLIC.value},
        )

    @property
    def profile_badge(self) -> ViewBadge:
        return badge_for_network_profile(self.network_profile)

    @property
    def exposure_badge(self) -> ViewBadge:
        return badge_for_exposure_mode(self.exposure_mode)

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(asdict(self))


@dataclass(frozen=True, slots=True)
class BackupViewState:
    """Backup summary for UI display."""

    backup_id: str | None = None
    instance_id: str | None = None
    status: str | None = None
    backup_class: str | None = None
    capsule_version: str | None = None
    created_at: str | None = None
    verified_at: str | None = None
    size_bytes: int | None = None
    path: str | None = None
    usable_for_restore: bool | None = None
    error: str | None = None

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None) -> "BackupViewState":
        if not data:
            return cls()

        return cls(
            backup_id=_optional_str(data.get("backup_id") or data.get("id")),
            instance_id=_optional_str(data.get("instance_id")),
            status=_optional_str(data.get("status")),
            backup_class=_optional_str(data.get("backup_class") or data.get("class")),
            capsule_version=_optional_str(data.get("capsule_version")),
            created_at=_optional_str(data.get("created_at")),
            verified_at=_optional_str(data.get("verified_at")),
            size_bytes=_optional_int(data.get("size_bytes"), default=None),
            path=_optional_str(data.get("path") or data.get("display_path") or data.get("root_dir")),
            usable_for_restore=_optional_bool(data.get("usable_for_restore")),
            error=_optional_str(data.get("error")),
        )

    @property
    def badge(self) -> ViewBadge:
        return badge_for_backup_status(self.status)

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(asdict(self))


@dataclass(frozen=True, slots=True)
class ServiceViewState:
    """Runtime service health row for UI display."""

    service: str
    status: str = "unknown"
    running: bool | None = None
    healthy: bool | None = None
    desired: bool | None = None
    message: str | None = None
    checked_at: str | None = None

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "ServiceViewState":
        return cls(
            service=str(data.get("service", "")),
            status=str(data.get("status") or data.get("status_label") or "unknown"),
            running=_optional_bool(data.get("running")),
            healthy=_optional_bool(data.get("healthy")),
            desired=_optional_bool(data.get("desired")),
            message=_optional_str(data.get("message") or data.get("detail")),
            checked_at=_optional_str(data.get("checked_at")),
        )

    @property
    def badge(self) -> ViewBadge:
        return badge_for_service_status(self.status)

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(asdict(self))


@dataclass(frozen=True, slots=True)
class HealthViewState:
    """Instance/runtime health state."""

    healthy: bool | None = None
    ready: bool | None = None
    state: str | None = None
    message: str | None = None
    checked_at: str | None = None
    services: tuple[ServiceViewState, ...] = ()

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None) -> "HealthViewState":
        if not data:
            return cls()

        raw_services = data.get("services") or ()
        services = tuple(
            ServiceViewState.from_mapping(item)
            for item in raw_services
            if isinstance(item, Mapping)
        )

        return cls(
            healthy=_optional_bool(data.get("healthy")),
            ready=_optional_bool(data.get("ready")),
            state=_optional_str(data.get("state")),
            message=_optional_str(data.get("message")),
            checked_at=_optional_str(data.get("checked_at")),
            services=services,
        )

    @property
    def badge(self) -> ViewBadge:
        if self.healthy is True:
            return ViewBadge("healthy", "Healthy", ViewSeverity.SUCCESS)
        if self.healthy is False:
            return ViewBadge("unhealthy", "Unhealthy", ViewSeverity.DANGER)
        if self.ready is True:
            return ViewBadge("ready", "Ready", ViewSeverity.SUCCESS)
        return ViewBadge("unknown", "Unknown", ViewSeverity.WARNING)

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(asdict(self))


@dataclass(frozen=True, slots=True)
class InstanceViewState:
    """Instance summary for UI display."""

    instance_id: str = DEFAULT_INSTANCE_ID
    state: str = InstanceState.CREATED.value
    capsule_id: str | None = None
    capsule_version: str | None = None
    app_version: str | None = None
    url: str | None = None
    network: NetworkViewState = field(default_factory=NetworkViewState)
    security: SecurityViewState = field(default_factory=SecurityViewState)
    health: HealthViewState = field(default_factory=HealthViewState)
    latest_backup: BackupViewState = field(default_factory=BackupViewState)
    service_health: Mapping[str, str] = field(default_factory=dict)
    updated_at: str | None = None
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "InstanceViewState":
        network_data = data.get("network")
        if not isinstance(network_data, Mapping):
            network_data = data

        security_data = data.get("security") or data.get("security_gate")
        health_data = data.get("health")
        latest_backup_data = data.get("latest_backup") or data.get("last_backup") or data.get("backup")

        return cls(
            instance_id=str(data.get("instance_id", DEFAULT_INSTANCE_ID)),
            state=normalize_instance_state(data.get("state")),
            capsule_id=_optional_str(data.get("capsule_id")),
            capsule_version=_optional_str(data.get("capsule_version")),
            app_version=_optional_str(data.get("app_version")),
            url=_optional_str(data.get("url") or data.get("runtime_url")),
            network=NetworkViewState.from_mapping(network_data),
            security=SecurityViewState.from_mapping(
                security_data if isinstance(security_data, Mapping) else None
            ),
            health=HealthViewState.from_mapping(
                health_data if isinstance(health_data, Mapping) else None
            ),
            latest_backup=BackupViewState.from_mapping(
                latest_backup_data if isinstance(latest_backup_data, Mapping) else None
            ),
            service_health=dict(data.get("service_health", {}) or {}),
            updated_at=_optional_str(data.get("updated_at")),
            errors=_tuple_str(data.get("errors", ())),
            warnings=_tuple_str(data.get("warnings", ())),
        )

    @property
    def badge(self) -> ViewBadge:
        return badge_for_instance_state(self.state)

    @property
    def can_start(self) -> bool:
        return self.state in {
            InstanceState.CREATED.value,
            InstanceState.READY.value,
            InstanceState.STOPPED.value,
            InstanceState.DEGRADED.value,
        } and self.security.can_start

    @property
    def can_stop(self) -> bool:
        return self.state in {
            InstanceState.STARTING.value,
            InstanceState.RUNNING.value,
            InstanceState.DEGRADED.value,
        }

    @property
    def can_backup(self) -> bool:
        return self.state in {
            InstanceState.RUNNING.value,
            InstanceState.STOPPED.value,
            InstanceState.DEGRADED.value,
        }

    @property
    def can_restore(self) -> bool:
        return self.state not in {
            InstanceState.IMPORTING.value,
            InstanceState.VERIFYING.value,
            InstanceState.STARTING.value,
            InstanceState.STOPPING.value,
            InstanceState.UPDATING.value,
            InstanceState.ROLLING_BACK.value,
        }

    @property
    def can_rollback(self) -> bool:
        return self.can_restore and self.latest_backup.backup_id is not None

    def action_payload(self) -> dict[str, str]:
        return {"instance_id": self.instance_id}

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(asdict(self))


@dataclass(frozen=True, slots=True)
class TargetViewState:
    """Selected target-mode state for UI display."""

    target_mode: str = MANAGER_DEFAULT_TARGET_MODE
    network_profile: str = MANAGER_DEFAULT_NETWORK_PROFILE
    exposure_mode: str = MANAGER_DEFAULT_EXPOSURE_MODE
    runtime_root: str | None = None
    capsule_dir: str | None = None
    host: str | None = None
    domain: str | None = None
    public_mode_expires_at: str | None = None
    confirmed: bool = False

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None) -> "TargetViewState":
        if not data:
            return cls()

        normalized = normalize_payload_aliases(data)
        target_mode = str(normalized.get("target_mode") or MANAGER_DEFAULT_TARGET_MODE)

        if target_mode == "droplet":
            return DropletTargetViewState.from_mapping(normalized)

        defaults = _target_defaults(target_mode)

        return cls(
            target_mode=target_mode,
            network_profile=normalize_network_profile(
                normalized.get("network_profile") or defaults["network_profile"]
            ),
            exposure_mode=normalize_exposure_mode(
                normalized.get("exposure_mode") or defaults["exposure_mode"]
            ),
            runtime_root=_optional_str(
                normalized.get("runtime_root")
                or normalized.get("target_runtime_root")
            ),
            capsule_dir=_optional_str(
                normalized.get("capsule_dir")
                or normalized.get("target_capsule_dir")
            ),
            host=_optional_str(normalized.get("host")),
            domain=_optional_str(normalized.get("domain")),
            public_mode_expires_at=_optional_str(normalized.get("public_mode_expires_at")),
            confirmed=_to_bool(normalized.get("confirmed")),
        )

    @property
    def profile_badge(self) -> ViewBadge:
        return badge_for_network_profile(self.network_profile)

    @property
    def exposure_badge(self) -> ViewBadge:
        return badge_for_exposure_mode(self.exposure_mode)

    @property
    def public_mode_enabled(self) -> bool:
        return self.target_mode in {"temporary_public", "droplet"}

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(asdict(self))


@dataclass(frozen=True, slots=True)
class DropletTargetViewState(TargetViewState):
    """Droplet/VPS target state for UI display."""

    target_mode: str = "droplet"
    network_profile: str = NetworkProfile.PUBLIC_VPS.value
    exposure_mode: str = ExposureMode.PUBLIC.value
    droplet_name: str | None = None
    droplet_host: str | None = None
    droplet_user: str | None = None
    ssh_key_path: str | None = None
    ssh_port: int = 22
    remote_kx_root: str | None = "/opt/konnaxion"
    remote_capsule_dir: str | None = "/opt/konnaxion/capsules"
    remote_agent_url: str | None = None

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None) -> "DropletTargetViewState":
        if not data:
            return cls()

        normalized = normalize_payload_aliases(data)

        return cls(
            runtime_root=_optional_str(normalized.get("remote_kx_root") or normalized.get("runtime_root")),
            capsule_dir=_optional_str(normalized.get("remote_capsule_dir") or normalized.get("capsule_dir")),
            host=_optional_str(normalized.get("droplet_host") or normalized.get("host")),
            domain=_optional_str(normalized.get("domain")),
            confirmed=_to_bool(normalized.get("confirmed")),
            droplet_name=_optional_str(normalized.get("droplet_name")),
            droplet_host=_optional_str(normalized.get("droplet_host") or normalized.get("host")),
            droplet_user=_optional_str(normalized.get("droplet_user")),
            ssh_key_path=_optional_str(normalized.get("ssh_key_path")),
            ssh_port=_optional_int(normalized.get("ssh_port"), default=22) or 22,
            remote_kx_root=_optional_str(normalized.get("remote_kx_root")) or "/opt/konnaxion",
            remote_capsule_dir=_optional_str(normalized.get("remote_capsule_dir")) or "/opt/konnaxion/capsules",
            remote_agent_url=_optional_str(normalized.get("remote_agent_url")),
        )


@dataclass(frozen=True, slots=True)
class BuildViewState:
    """Build/capsule target state for UI display."""

    source_dir: str | None = None
    capsule_output_dir: str | None = None
    capsule_id: str | None = None
    capsule_version: str | None = None
    capsule_file: str | None = None
    channel: str | None = None
    force: bool = True
    verify_after_build: bool = False
    target: TargetViewState = field(default_factory=TargetViewState)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None) -> "BuildViewState":
        if not data:
            return cls()

        normalized = normalize_payload_aliases(data)

        return cls(
            source_dir=_optional_str(normalized.get("source_dir")),
            capsule_output_dir=_optional_str(normalized.get("capsule_output_dir")),
            capsule_id=_optional_str(normalized.get("capsule_id")),
            capsule_version=_optional_str(
                normalized.get("capsule_version")
                or normalized.get("version")
            ),
            capsule_file=_optional_str(normalized.get("capsule_file")),
            channel=_optional_str(normalized.get("channel")),
            force=_to_bool(normalized.get("force"), default=True),
            verify_after_build=_to_bool(normalized.get("verify_after_build")),
            target=TargetViewState.from_mapping(normalized),
        )

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(asdict(self))


@dataclass(frozen=True, slots=True)
class PageViewState:
    """Current page presentation state."""

    route: str = UI_BASE_PATH
    title: str = "Dashboard"
    nav_items: tuple[ViewNavItem, ...] = ()
    result: ViewResult | None = None
    refreshed_at: str | None = None
    refresh_seconds: int = DEFAULT_REFRESH_SECONDS

    @classmethod
    def for_route(
        cls,
        route: str = UI_BASE_PATH,
        *,
        result: Any | None = None,
    ) -> "PageViewState":
        return cls(
            route=route,
            title=title_for_route(route),
            nav_items=build_nav_items(route),
            result=ViewResult.from_value(result),
            refreshed_at=_utc_now_iso(),
        )

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(asdict(self))


@dataclass(frozen=True, slots=True)
class ManagerViewState:
    """Top-level UI state for the Capsule Manager."""

    selected_instance_id: str | None = None
    instances: tuple[InstanceViewState, ...] = ()
    capsules: tuple[CapsuleViewState, ...] = ()
    backups: tuple[BackupViewState, ...] = ()
    target: TargetViewState = field(default_factory=TargetViewState)
    build: BuildViewState = field(default_factory=BuildViewState)
    active_task: str | None = None
    global_errors: tuple[str, ...] = ()
    global_warnings: tuple[str, ...] = ()
    refreshed_at: str | None = None

    @classmethod
    def empty(cls) -> "ManagerViewState":
        return cls(refreshed_at=_utc_now_iso())

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None) -> "ManagerViewState":
        if not data:
            return cls.empty()

        instances = tuple(
            InstanceViewState.from_mapping(item)
            for item in data.get("instances", ()) or ()
            if isinstance(item, Mapping)
        )

        capsules = tuple(
            CapsuleViewState.from_mapping(item)
            for item in data.get("capsules", ()) or ()
            if isinstance(item, Mapping)
        )

        backups = tuple(
            BackupViewState.from_mapping(item)
            for item in data.get("backups", ()) or ()
            if isinstance(item, Mapping)
        )

        selected = _optional_str(data.get("selected_instance_id"))
        if selected is None and instances:
            selected = instances[0].instance_id

        target_data = data.get("target")
        if not isinstance(target_data, Mapping):
            target_data = data

        build_data = data.get("build")
        if not isinstance(build_data, Mapping):
            build_data = data

        return cls(
            selected_instance_id=selected,
            instances=instances,
            capsules=capsules,
            backups=backups,
            target=TargetViewState.from_mapping(target_data),
            build=BuildViewState.from_mapping(build_data),
            active_task=_optional_str(data.get("active_task")),
            global_errors=_tuple_str(data.get("global_errors", ())),
            global_warnings=_tuple_str(data.get("global_warnings", ())),
            refreshed_at=_optional_str(data.get("refreshed_at")) or _utc_now_iso(),
        )

    @property
    def selected_instance(self) -> InstanceViewState | None:
        if not self.selected_instance_id:
            return self.instances[0] if self.instances else None

        for instance in self.instances:
            if instance.instance_id == self.selected_instance_id:
                return instance

        return self.instances[0] if self.instances else None

    @property
    def running_instance_count(self) -> int:
        return sum(1 for item in self.instances if item.state == InstanceState.RUNNING.value)

    @property
    def security_blocked_instance_count(self) -> int:
        return sum(1 for item in self.instances if item.state == InstanceState.SECURITY_BLOCKED.value)

    @property
    def verified_capsule_count(self) -> int:
        return sum(1 for item in self.capsules if item.verified or item.signature_verified or item.checksum_verified)

    def with_selected_instance(self, instance_id: str) -> "ManagerViewState":
        return replace(self, selected_instance_id=instance_id)

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(asdict(self))


def build_nav_items(active_href: str = UI_BASE_PATH) -> tuple[ViewNavItem, ...]:
    return tuple(
        ViewNavItem(label=label, href=href, active=href == active_href)
        for label, href in NAV_ITEMS
    )


def to_mapping(value: Any) -> Mapping[str, Any]:
    """Convert DTO/view models to dictionaries when possible."""

    if value is None:
        return {}

    if isinstance(value, Mapping):
        return value

    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        data = to_dict()
        if isinstance(data, Mapping):
            return data

    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        data = model_dump()
        if isinstance(data, Mapping):
            return data

    dict_method = getattr(value, "dict", None)
    if callable(dict_method):
        data = dict_method()
        if isinstance(data, Mapping):
            return data

    if is_dataclass(value):
        return asdict(value)

    return {}


def normalize_instance_state(value: Any) -> str:
    return _normalize_enum_value(
        value,
        enum_type=InstanceState,
        default=InstanceState.CREATED.value,
        field_name="instance state",
    )


def normalize_network_profile(value: Any) -> str:
    return _normalize_enum_value(
        value,
        enum_type=NetworkProfile,
        default=DEFAULT_NETWORK_PROFILE.value,
        field_name="network profile",
    )


def normalize_exposure_mode(value: Any) -> str:
    return _normalize_enum_value(
        value,
        enum_type=ExposureMode,
        default=DEFAULT_EXPOSURE_MODE.value,
        field_name="exposure mode",
    )


def normalize_security_status(value: Any) -> str:
    return _normalize_enum_value(
        value,
        enum_type=SecurityGateStatus,
        default=SecurityGateStatus.UNKNOWN.value,
        field_name="security status",
    )


def _normalize_enum_value(
    value: Any,
    *,
    enum_type: type[StrEnum],
    default: str,
    field_name: str,
) -> str:
    if value in {None, ""}:
        return default

    candidate = str(getattr(value, "value", value))
    allowed = {item.value for item in enum_type}

    if candidate not in allowed:
        raise ValueError(
            f"Invalid {field_name}: {candidate!r}; expected one of: "
            f"{', '.join(sorted(allowed))}"
        )

    return candidate


def badge_for_instance_state(value: Any) -> ViewBadge:
    state = normalize_instance_state(value)

    labels = {
        InstanceState.CREATED.value: "Created",
        InstanceState.IMPORTING.value: "Importing",
        InstanceState.VERIFYING.value: "Verifying",
        InstanceState.READY.value: "Ready",
        InstanceState.STARTING.value: "Starting",
        InstanceState.RUNNING.value: "Running",
        InstanceState.STOPPING.value: "Stopping",
        InstanceState.STOPPED.value: "Stopped",
        InstanceState.UPDATING.value: "Updating",
        InstanceState.ROLLING_BACK.value: "Rolling back",
        InstanceState.DEGRADED.value: "Degraded",
        InstanceState.FAILED.value: "Failed",
        InstanceState.SECURITY_BLOCKED.value: "Security blocked",
    }

    severities = {
        InstanceState.READY.value: ViewSeverity.SUCCESS,
        InstanceState.RUNNING.value: ViewSeverity.SUCCESS,
        InstanceState.DEGRADED.value: ViewSeverity.WARNING,
        InstanceState.FAILED.value: ViewSeverity.DANGER,
        InstanceState.SECURITY_BLOCKED.value: ViewSeverity.DANGER,
        InstanceState.STOPPED.value: ViewSeverity.DISABLED,
    }

    return ViewBadge(
        value=state,
        label=labels[state],
        severity=severities.get(state, ViewSeverity.INFO),
    )


def badge_for_security_status(value: Any) -> ViewBadge:
    status = normalize_security_status(value)

    labels = {
        SecurityGateStatus.PASS.value: "Security PASS",
        SecurityGateStatus.WARN.value: "Security WARN",
        SecurityGateStatus.FAIL_BLOCKING.value: "Security blocked",
        SecurityGateStatus.SKIPPED.value: "Security skipped",
        SecurityGateStatus.UNKNOWN.value: "Security unknown",
    }

    severities = {
        SecurityGateStatus.PASS.value: ViewSeverity.SUCCESS,
        SecurityGateStatus.WARN.value: ViewSeverity.WARNING,
        SecurityGateStatus.FAIL_BLOCKING.value: ViewSeverity.DANGER,
        SecurityGateStatus.SKIPPED.value: ViewSeverity.DISABLED,
        SecurityGateStatus.UNKNOWN.value: ViewSeverity.NEUTRAL,
    }

    return ViewBadge(value=status, label=labels[status], severity=severities[status])


def badge_for_network_profile(value: Any) -> ViewBadge:
    profile = normalize_network_profile(value)

    labels = {
        NetworkProfile.LOCAL_ONLY.value: "Local only",
        NetworkProfile.INTRANET_PRIVATE.value: "Intranet private",
        NetworkProfile.PRIVATE_TUNNEL.value: "Private tunnel",
        NetworkProfile.PUBLIC_TEMPORARY.value: "Public temporary",
        NetworkProfile.PUBLIC_VPS.value: "Public VPS",
        NetworkProfile.OFFLINE.value: "Offline",
    }

    severities = {
        NetworkProfile.LOCAL_ONLY.value: ViewSeverity.SUCCESS,
        NetworkProfile.INTRANET_PRIVATE.value: ViewSeverity.SUCCESS,
        NetworkProfile.PRIVATE_TUNNEL.value: ViewSeverity.SUCCESS,
        NetworkProfile.PUBLIC_TEMPORARY.value: ViewSeverity.WARNING,
        NetworkProfile.PUBLIC_VPS.value: ViewSeverity.WARNING,
        NetworkProfile.OFFLINE.value: ViewSeverity.DISABLED,
    }

    return ViewBadge(value=profile, label=labels[profile], severity=severities[profile])


def badge_for_exposure_mode(value: Any) -> ViewBadge:
    mode = normalize_exposure_mode(value)

    labels = {
        ExposureMode.PRIVATE.value: "Private",
        ExposureMode.LAN.value: "LAN",
        ExposureMode.VPN.value: "VPN",
        ExposureMode.TEMPORARY_TUNNEL.value: "Temporary tunnel",
        ExposureMode.PUBLIC.value: "Public",
    }

    severities = {
        ExposureMode.PRIVATE.value: ViewSeverity.SUCCESS,
        ExposureMode.LAN.value: ViewSeverity.SUCCESS,
        ExposureMode.VPN.value: ViewSeverity.SUCCESS,
        ExposureMode.TEMPORARY_TUNNEL.value: ViewSeverity.WARNING,
        ExposureMode.PUBLIC.value: ViewSeverity.WARNING,
    }

    return ViewBadge(value=mode, label=labels[mode], severity=severities[mode])


def badge_for_backup_status(value: Any) -> ViewBadge:
    if value in {None, ""}:
        return ViewBadge("none", "No backup", ViewSeverity.NEUTRAL)

    status = str(getattr(value, "value", value))
    allowed = {item.value for item in BackupStatus}

    if status not in allowed:
        raise ValueError(
            f"Invalid backup status: {status!r}; expected one of: "
            f"{', '.join(sorted(allowed))}"
        )

    labels = {
        BackupStatus.CREATED.value: "Backup created",
        BackupStatus.RUNNING.value: "Backup running",
        BackupStatus.VERIFYING.value: "Backup verifying",
        BackupStatus.VERIFIED.value: "Backup verified",
        BackupStatus.FAILED.value: "Backup failed",
        BackupStatus.EXPIRED.value: "Backup expired",
        BackupStatus.DELETED.value: "Backup deleted",
        BackupStatus.QUARANTINED.value: "Backup quarantined",
    }

    severities = {
        BackupStatus.VERIFIED.value: ViewSeverity.SUCCESS,
        BackupStatus.RUNNING.value: ViewSeverity.INFO,
        BackupStatus.VERIFYING.value: ViewSeverity.INFO,
        BackupStatus.FAILED.value: ViewSeverity.DANGER,
        BackupStatus.EXPIRED.value: ViewSeverity.DISABLED,
        BackupStatus.DELETED.value: ViewSeverity.DISABLED,
        BackupStatus.QUARANTINED.value: ViewSeverity.WARNING,
    }

    return ViewBadge(
        value=status,
        label=labels[status],
        severity=severities.get(status, ViewSeverity.NEUTRAL),
    )


def badge_for_restore_status(value: Any) -> ViewBadge:
    return _badge_for_resource_status(
        value=value,
        enum_type=RestoreStatus,
        labels={
            RestoreStatus.PLANNED.value: "Restore planned",
            RestoreStatus.PREFLIGHT.value: "Restore preflight",
            RestoreStatus.CREATING_PRE_RESTORE_BACKUP.value: "Creating safety backup",
            RestoreStatus.RESTORING_DATABASE.value: "Restoring database",
            RestoreStatus.RESTORING_MEDIA.value: "Restoring media",
            RestoreStatus.RUNNING_MIGRATIONS.value: "Running migrations",
            RestoreStatus.RUNNING_SECURITY_GATE.value: "Running Security Gate",
            RestoreStatus.RUNNING_HEALTHCHECKS.value: "Running healthchecks",
            RestoreStatus.RESTORED.value: "Restored",
            RestoreStatus.DEGRADED.value: "Restore degraded",
            RestoreStatus.FAILED.value: "Restore failed",
            RestoreStatus.ROLLED_BACK.value: "Restore rolled back",
        },
        success_values={RestoreStatus.RESTORED.value},
        warning_values={RestoreStatus.DEGRADED.value, RestoreStatus.ROLLED_BACK.value},
        danger_values={RestoreStatus.FAILED.value},
    )


def badge_for_rollback_status(value: Any) -> ViewBadge:
    return _badge_for_resource_status(
        value=value,
        enum_type=RollbackStatus,
        labels={
            RollbackStatus.PLANNED.value: "Rollback planned",
            RollbackStatus.RUNNING.value: "Rollback running",
            RollbackStatus.CAPSULE_REPOINTED.value: "Capsule repointed",
            RollbackStatus.DATA_RESTORED.value: "Data restored",
            RollbackStatus.HEALTHCHECKING.value: "Healthchecking",
            RollbackStatus.COMPLETED.value: "Rollback completed",
            RollbackStatus.FAILED.value: "Rollback failed",
        },
        success_values={RollbackStatus.COMPLETED.value},
        warning_values=set(),
        danger_values={RollbackStatus.FAILED.value},
    )


def badge_for_service_status(value: Any) -> ViewBadge:
    status = str(getattr(value, "value", value) or "unknown").lower()

    severity = {
        "healthy": ViewSeverity.SUCCESS,
        "running": ViewSeverity.SUCCESS,
        "ready": ViewSeverity.SUCCESS,
        "ok": ViewSeverity.SUCCESS,
        "unhealthy": ViewSeverity.DANGER,
        "failed": ViewSeverity.DANGER,
        "error": ViewSeverity.DANGER,
        "not_running": ViewSeverity.DISABLED,
        "stopped": ViewSeverity.DISABLED,
        "starting": ViewSeverity.INFO,
        "unknown": ViewSeverity.WARNING,
    }.get(status, ViewSeverity.WARNING)

    return ViewBadge(value=status, label=_humanize(status), severity=severity)


def _badge_for_resource_status(
    *,
    value: Any,
    enum_type: type[StrEnum],
    labels: Mapping[str, str],
    success_values: set[str],
    warning_values: set[str],
    danger_values: set[str],
) -> ViewBadge:
    status = _normalize_enum_value(
        value,
        enum_type=enum_type,
        default=next(iter(labels.keys())),
        field_name="resource status",
    )

    if status in success_values:
        severity = ViewSeverity.SUCCESS
    elif status in warning_values:
        severity = ViewSeverity.WARNING
    elif status in danger_values:
        severity = ViewSeverity.DANGER
    else:
        severity = ViewSeverity.INFO

    return ViewBadge(value=status, label=labels[status], severity=severity)


def _target_defaults(target_mode: str) -> dict[str, str]:
    return {
        "local": {
            "network_profile": NetworkProfile.LOCAL_ONLY.value,
            "exposure_mode": ExposureMode.PRIVATE.value,
        },
        "intranet": {
            "network_profile": NetworkProfile.INTRANET_PRIVATE.value,
            "exposure_mode": ExposureMode.PRIVATE.value,
        },
        "temporary_public": {
            "network_profile": NetworkProfile.PUBLIC_TEMPORARY.value,
            "exposure_mode": ExposureMode.TEMPORARY_TUNNEL.value,
        },
        "droplet": {
            "network_profile": NetworkProfile.PUBLIC_VPS.value,
            "exposure_mode": ExposureMode.PUBLIC.value,
        },
    }.get(
        target_mode,
        {
            "network_profile": MANAGER_DEFAULT_NETWORK_PROFILE,
            "exposure_mode": MANAGER_DEFAULT_EXPOSURE_MODE,
        },
    )


def _optional_str(value: Any) -> str | None:
    if value in {None, ""}:
        return None
    return str(getattr(value, "value", value))


def _optional_bool(value: Any) -> bool | None:
    if value in {None, ""}:
        return None
    return _to_bool(value)


def _optional_int(value: Any, *, default: int | None) -> int | None:
    if value in {None, ""}:
        return default

    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _tuple_str(value: Any) -> tuple[str, ...]:
    if value in {None, ""}:
        return ()

    if isinstance(value, str):
        return (value,)

    if isinstance(value, Sequence):
        return tuple(str(item) for item in value)

    return (str(value),)


def _to_bool(value: Any, *, default: bool = False) -> bool:
    if value in {None, ""}:
        return default

    if isinstance(value, bool):
        return value

    return str(value).strip().lower() in {
        "1",
        "true",
        "yes",
        "y",
        "on",
        "checked",
        "confirmed",
    }


def _humanize(value: Any) -> str:
    raw = str(getattr(value, "value", value) or "")
    return raw.replace("_", " ").replace("-", " ").title()


def _json_safe(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value

    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}

    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]

    if isinstance(value, list):
        return [_json_safe(item) for item in value]

    if is_dataclass(value):
        return _json_safe(asdict(value))

    return value


def _utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


__all__ = [
    "BackupViewState",
    "BuildViewState",
    "CapsuleViewState",
    "DropletTargetViewState",
    "HealthViewState",
    "InstanceViewState",
    "ManagerViewState",
    "NetworkViewState",
    "PageViewState",
    "SecurityCheckViewState",
    "SecurityViewState",
    "ServiceViewState",
    "TargetViewState",
    "ViewAction",
    "ViewBadge",
    "ViewNavItem",
    "ViewResult",
    "ViewSeverity",
    "badge_for_backup_status",
    "badge_for_exposure_mode",
    "badge_for_instance_state",
    "badge_for_network_profile",
    "badge_for_restore_status",
    "badge_for_rollback_status",
    "badge_for_security_status",
    "badge_for_service_status",
    "build_nav_items",
    "normalize_exposure_mode",
    "normalize_instance_state",
    "normalize_network_profile",
    "normalize_security_status",
    "to_mapping",
]