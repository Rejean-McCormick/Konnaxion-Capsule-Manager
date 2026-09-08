"""
UI state models for Konnaxion Capsule Manager.

The UI layer may present friendly labels, but it must store and exchange only
canonical Konnaxion values for instance states, network profiles, exposure
modes, Security Gate statuses, and backup/restore/rollback statuses.

This module is intentionally framework-neutral so it can be reused by a simple
FastAPI/Jinja UI, a local desktop wrapper, a Streamlit prototype, or tests.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum
from typing import Any

from kx_manager.defaults import (
    DEFAULT_EXPOSURE_MODE as MANAGER_DEFAULT_EXPOSURE_MODE,
    DEFAULT_NETWORK_PROFILE as MANAGER_DEFAULT_NETWORK_PROFILE,
    DEFAULT_TARGET_MODE as MANAGER_DEFAULT_TARGET_MODE,
)
from kx_shared.konnaxion_constants import (
    BackupStatus,
    DEFAULT_INSTANCE_ID,
    ExposureMode,
    InstanceState,
    NetworkProfile,
    RestoreStatus,
    RollbackStatus,
    SecurityGateStatus,
)


class UiSeverity(StrEnum):
    """Generic UI severity level.

    These values are UI-only presentation hints. They must not be persisted as
    Agent or Instance states.
    """

    NEUTRAL = "neutral"
    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    DANGER = "danger"
    DISABLED = "disabled"


@dataclass(frozen=True)
class UiBadge:
    """Small display object for status pills/badges."""

    value: str
    label: str
    severity: UiSeverity = UiSeverity.NEUTRAL
    title: str | None = None


@dataclass(frozen=True)
class CapsuleUiState:
    """Capsule summary for UI display."""

    capsule_id: str
    capsule_version: str | None = None
    app_version: str | None = None
    channel: str | None = None
    imported_at: str | None = None
    verified: bool = False
    signature_status: str | None = None
    security_status: str | None = None
    warnings: tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "CapsuleUiState":
        """Create capsule UI state from API/Agent data."""

        return cls(
            capsule_id=str(data.get("capsule_id", "")),
            capsule_version=_optional_str(data.get("capsule_version")),
            app_version=_optional_str(data.get("app_version")),
            channel=_optional_str(data.get("channel")),
            imported_at=_optional_str(data.get("imported_at")),
            verified=bool(data.get("verified", False)),
            signature_status=_optional_str(data.get("signature_status")),
            security_status=_optional_str(data.get("security_status")),
            warnings=_tuple_str(data.get("warnings", ())),
        )


@dataclass(frozen=True)
class SecurityCheckUiState:
    """Single Security Gate check result for UI display."""

    check: str
    status: str = SecurityGateStatus.UNKNOWN.value
    message: str | None = None
    blocking: bool = False

    @property
    def badge(self) -> UiBadge:
        """Return a display badge for this check status."""

        return badge_for_security_status(self.status)


@dataclass(frozen=True)
class SecurityUiState:
    """Security Gate UI state."""

    status: str = SecurityGateStatus.UNKNOWN.value
    checks: tuple[SecurityCheckUiState, ...] = ()
    last_checked_at: str | None = None
    can_start: bool = False
    blocking_messages: tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None) -> "SecurityUiState":
        """Create security UI state from API/Agent data."""

        if not data:
            return cls()

        raw_checks = data.get("checks", ()) or ()
        checks: list[SecurityCheckUiState] = []

        for item in raw_checks:
            if isinstance(item, Mapping):
                checks.append(
                    SecurityCheckUiState(
                        check=str(item.get("check", "")),
                        status=str(item.get("status", SecurityGateStatus.UNKNOWN.value)),
                        message=_optional_str(item.get("message")),
                        blocking=bool(item.get("blocking", False)),
                    )
                )

        status_value = str(data.get("status", SecurityGateStatus.UNKNOWN.value))
        blocking_messages = tuple(
            check.message or check.check
            for check in checks
            if check.status == SecurityGateStatus.FAIL_BLOCKING.value or check.blocking
        )

        return cls(
            status=status_value,
            checks=tuple(checks),
            last_checked_at=_optional_str(data.get("last_checked_at")),
            can_start=status_value in {SecurityGateStatus.PASS.value, SecurityGateStatus.WARN.value}
            and not blocking_messages,
            blocking_messages=blocking_messages,
        )

    @property
    def badge(self) -> UiBadge:
        """Return aggregate Security Gate display badge."""

        return badge_for_security_status(self.status)


@dataclass(frozen=True)
class NetworkUiState:
    """Network profile and exposure state for UI display."""

    network_profile: str = MANAGER_DEFAULT_NETWORK_PROFILE
    exposure_mode: str = MANAGER_DEFAULT_EXPOSURE_MODE
    host: str | None = None
    private_url: str | None = None
    public_url: str | None = None
    public_mode_enabled: bool = False
    public_mode_expires_at: str | None = None

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None) -> "NetworkUiState":
        """Create network UI state from API/Agent data."""

        if not data:
            return cls()

        return cls(
            network_profile=normalize_network_profile(
                data.get("network_profile") or data.get("KX_NETWORK_PROFILE")
            ),
            exposure_mode=normalize_exposure_mode(
                data.get("exposure_mode") or data.get("KX_EXPOSURE_MODE")
            ),
            host=_optional_str(data.get("host") or data.get("KX_HOST")),
            private_url=_optional_str(data.get("private_url")),
            public_url=_optional_str(data.get("public_url")),
            public_mode_enabled=_to_bool(
                data.get("public_mode_enabled")
                if "public_mode_enabled" in data
                else data.get("KX_PUBLIC_MODE_ENABLED", False)
            ),
            public_mode_expires_at=_optional_str(
                data.get("public_mode_expires_at") or data.get("KX_PUBLIC_MODE_EXPIRES_AT")
            ),
        )

    @property
    def profile_badge(self) -> UiBadge:
        """Return network profile badge."""

        return badge_for_network_profile(self.network_profile)

    @property
    def exposure_badge(self) -> UiBadge:
        """Return exposure mode badge."""

        return badge_for_exposure_mode(self.exposure_mode)


@dataclass(frozen=True)
class BackupUiState:
    """Latest backup summary for UI display."""

    backup_id: str | None = None
    status: str | None = None
    backup_class: str | None = None
    created_at: str | None = None
    verified_at: str | None = None
    size_bytes: int | None = None
    path: str | None = None

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None) -> "BackupUiState":
        """Create backup UI state from API/Agent data."""

        if not data:
            return cls()

        size = data.get("size_bytes")
        return cls(
            backup_id=_optional_str(data.get("backup_id") or data.get("id")),
            status=_optional_str(data.get("status")),
            backup_class=_optional_str(data.get("backup_class") or data.get("class")),
            created_at=_optional_str(data.get("created_at")),
            verified_at=_optional_str(data.get("verified_at")),
            size_bytes=int(size) if size not in {None, ""} else None,
            path=_optional_str(data.get("path")),
        )

    @property
    def badge(self) -> UiBadge:
        """Return backup status badge."""

        return badge_for_backup_status(self.status)


@dataclass(frozen=True)
class InstanceUiState:
    """Instance summary for UI display."""

    instance_id: str = DEFAULT_INSTANCE_ID
    state: str = InstanceState.CREATED.value
    capsule_id: str | None = None
    capsule_version: str | None = None
    app_version: str | None = None
    url: str | None = None
    network: NetworkUiState = field(default_factory=NetworkUiState)
    security: SecurityUiState = field(default_factory=SecurityUiState)
    latest_backup: BackupUiState = field(default_factory=BackupUiState)
    service_health: Mapping[str, str] = field(default_factory=dict)
    updated_at: str | None = None
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "InstanceUiState":
        """Create instance UI state from API/Agent data."""

        network_data = data.get("network")
        if not isinstance(network_data, Mapping):
            network_data = data

        security_data = data.get("security")
        latest_backup_data = data.get("latest_backup") or data.get("backup")

        return cls(
            instance_id=str(data.get("instance_id", DEFAULT_INSTANCE_ID)),
            state=normalize_instance_state(data.get("state")),
            capsule_id=_optional_str(data.get("capsule_id")),
            capsule_version=_optional_str(data.get("capsule_version")),
            app_version=_optional_str(data.get("app_version")),
            url=_optional_str(data.get("url")),
            network=NetworkUiState.from_mapping(network_data),
            security=SecurityUiState.from_mapping(
                security_data if isinstance(security_data, Mapping) else None
            ),
            latest_backup=BackupUiState.from_mapping(
                latest_backup_data if isinstance(latest_backup_data, Mapping) else None
            ),
            service_health=dict(data.get("service_health", {}) or {}),
            updated_at=_optional_str(data.get("updated_at")),
            errors=_tuple_str(data.get("errors", ())),
            warnings=_tuple_str(data.get("warnings", ())),
        )

    @property
    def badge(self) -> UiBadge:
        """Return instance state badge."""

        return badge_for_instance_state(self.state)

    @property
    def can_start(self) -> bool:
        """Return whether the UI should enable the Start action."""

        return self.state in {
            InstanceState.CREATED.value,
            InstanceState.READY.value,
            InstanceState.STOPPED.value,
            InstanceState.DEGRADED.value,
        } and self.security.can_start

    @property
    def can_stop(self) -> bool:
        """Return whether the UI should enable the Stop action."""

        return self.state in {
            InstanceState.STARTING.value,
            InstanceState.RUNNING.value,
            InstanceState.DEGRADED.value,
        }

    @property
    def can_backup(self) -> bool:
        """Return whether the UI should enable manual backup."""

        return self.state in {
            InstanceState.RUNNING.value,
            InstanceState.STOPPED.value,
            InstanceState.DEGRADED.value,
        }

    @property
    def can_restore(self) -> bool:
        """Return whether the UI should enable restore actions."""

        return self.state not in {
            InstanceState.IMPORTING.value,
            InstanceState.VERIFYING.value,
            InstanceState.STARTING.value,
            InstanceState.STOPPING.value,
            InstanceState.UPDATING.value,
            InstanceState.ROLLING_BACK.value,
        }


@dataclass(frozen=True)
class TargetModeUiState:
    """Selected GUI target mode state."""

    target_mode: str = MANAGER_DEFAULT_TARGET_MODE
    network_profile: str = MANAGER_DEFAULT_NETWORK_PROFILE
    exposure_mode: str = MANAGER_DEFAULT_EXPOSURE_MODE
    runtime_root: str | None = None
    capsule_dir: str | None = None
    host: str | None = None
    public_mode_expires_at: str | None = None
    confirmed: bool = False

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None) -> "TargetModeUiState":
        """Create target mode UI state from form/API data."""

        if not data:
            return cls()

        return cls(
            target_mode=str(data.get("target_mode") or MANAGER_DEFAULT_TARGET_MODE),
            network_profile=normalize_network_profile(data.get("network_profile")),
            exposure_mode=normalize_exposure_mode(data.get("exposure_mode")),
            runtime_root=_optional_str(data.get("runtime_root") or data.get("target_runtime_root")),
            capsule_dir=_optional_str(data.get("capsule_dir") or data.get("target_capsule_dir")),
            host=_optional_str(data.get("host") or data.get("target_host")),
            public_mode_expires_at=_optional_str(data.get("public_mode_expires_at")),
            confirmed=_to_bool(data.get("confirmed")),
        )


@dataclass(frozen=True)
class DropletTargetUiState(TargetModeUiState):
    """Droplet/VPS target state for UI display."""

    target_mode: str = "droplet"
    network_profile: str = "public_vps"
    exposure_mode: str = "public"
    droplet_name: str | None = None
    droplet_host: str | None = None
    droplet_user: str | None = None
    ssh_key_path: str | None = None
    ssh_port: int = 22
    remote_kx_root: str | None = "/opt/konnaxion"
    remote_capsule_dir: str | None = "/opt/konnaxion/capsules"
    domain: str | None = None
    remote_agent_url: str | None = None

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None) -> "DropletTargetUiState":
        """Create Droplet target UI state from form/API data."""

        if not data:
            return cls()

        return cls(
            target_mode="droplet",
            network_profile="public_vps",
            exposure_mode="public",
            runtime_root=_optional_str(data.get("remote_kx_root") or data.get("runtime_root")),
            capsule_dir=_optional_str(data.get("remote_capsule_dir") or data.get("capsule_dir")),
            host=_optional_str(data.get("droplet_host") or data.get("host")),
            public_mode_expires_at=None,
            confirmed=_to_bool(data.get("confirmed")),
            droplet_name=_optional_str(data.get("droplet_name")),
            droplet_host=_optional_str(data.get("droplet_host") or data.get("host")),
            droplet_user=_optional_str(data.get("droplet_user") or data.get("ssh_user")),
            ssh_key_path=_optional_str(data.get("ssh_key_path")),
            ssh_port=_optional_int(data.get("ssh_port"), default=22),
            remote_kx_root=_optional_str(data.get("remote_kx_root")),
            remote_capsule_dir=_optional_str(data.get("remote_capsule_dir")),
            domain=_optional_str(data.get("domain")),
            remote_agent_url=_optional_str(data.get("remote_agent_url")),
        )


@dataclass(frozen=True)
class BuildTargetUiState:
    """Build/capsule target state for UI display."""

    source_dir: str | None = None
    capsule_output_dir: str | None = None
    capsule_id: str | None = None
    capsule_version: str | None = None
    capsule_file: str | None = None
    target: TargetModeUiState = field(default_factory=TargetModeUiState)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None) -> "BuildTargetUiState":
        """Create build target UI state from form/API data."""

        if not data:
            return cls()

        target_mode = str(data.get("target_mode") or MANAGER_DEFAULT_TARGET_MODE)
        target: TargetModeUiState

        if target_mode == "droplet":
            target = DropletTargetUiState.from_mapping(data)
        else:
            target = TargetModeUiState.from_mapping(data)

        return cls(
            source_dir=_optional_str(data.get("source_dir")),
            capsule_output_dir=_optional_str(data.get("capsule_output_dir") or data.get("output_dir")),
            capsule_id=_optional_str(data.get("capsule_id")),
            capsule_version=_optional_str(data.get("capsule_version") or data.get("version")),
            capsule_file=_optional_str(data.get("capsule_file") or data.get("capsule_path")),
            target=target,
        )


@dataclass(frozen=True)
class ManagerUiState:
    """Top-level UI state for the Capsule Manager."""

    selected_instance_id: str | None = None
    instances: tuple[InstanceUiState, ...] = ()
    capsules: tuple[CapsuleUiState, ...] = ()
    security: SecurityUiState = field(default_factory=SecurityUiState)
    network: NetworkUiState = field(default_factory=NetworkUiState)
    backups: tuple[BackupUiState, ...] = ()
    active_task: str | None = None
    global_errors: tuple[str, ...] = ()
    global_warnings: tuple[str, ...] = ()
    refreshed_at: str | None = None

    @classmethod
    def empty(cls) -> "ManagerUiState":
        """Return initial Manager UI state."""

        return cls(refreshed_at=_utc_now_iso())

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "ManagerUiState":
        """Create Manager UI state from API payload data."""

        instances = tuple(
            InstanceUiState.from_mapping(item)
            for item in data.get("instances", ()) or ()
            if isinstance(item, Mapping)
        )

        capsules = tuple(
            CapsuleUiState.from_mapping(item)
            for item in data.get("capsules", ()) or ()
            if isinstance(item, Mapping)
        )

        selected = _optional_str(data.get("selected_instance_id"))
        if selected is None and instances:
            selected = instances[0].instance_id

        selected_instance = _select_instance(instances, selected)

        security_data = data.get("security")
        if isinstance(security_data, Mapping):
            security = SecurityUiState.from_mapping(security_data)
        elif selected_instance is not None:
            security = selected_instance.security
        else:
            security = SecurityUiState()

        network_data = data.get("network")
        if isinstance(network_data, Mapping):
            network = NetworkUiState.from_mapping(network_data)
        elif selected_instance is not None:
            network = selected_instance.network
        else:
            network = NetworkUiState()

        backups = _manager_backups_from_mapping(data, selected_instance=selected_instance)

        return cls(
            selected_instance_id=selected,
            instances=instances,
            capsules=capsules,
            security=security,
            network=network,
            backups=backups,
            active_task=_optional_str(data.get("active_task")),
            global_errors=_tuple_str(data.get("global_errors", ())),
            global_warnings=_tuple_str(data.get("global_warnings", ())),
            refreshed_at=_optional_str(data.get("refreshed_at")) or _utc_now_iso(),
        )

    @property
    def selected_instance(self) -> InstanceUiState | None:
        """Return the selected instance object, if available."""

        return _select_instance(self.instances, self.selected_instance_id)

    def with_selected_instance(self, instance_id: str) -> "ManagerUiState":
        """Return a copy with the selected instance changed."""

        selected_instance = _select_instance(self.instances, instance_id)
        if selected_instance is None:
            return replace(self, selected_instance_id=instance_id)

        backups = self.backups
        if not backups and _backup_has_data(selected_instance.latest_backup):
            backups = (selected_instance.latest_backup,)

        return replace(
            self,
            selected_instance_id=instance_id,
            security=selected_instance.security,
            network=selected_instance.network,
            backups=backups,
        )


def _select_instance(
    instances: Sequence[InstanceUiState],
    selected_instance_id: str | None,
) -> InstanceUiState | None:
    """Return the selected instance, falling back to the first instance."""

    if not instances:
        return None

    if not selected_instance_id:
        return instances[0]

    for instance in instances:
        if instance.instance_id == selected_instance_id:
            return instance

    return instances[0]


def _manager_backups_from_mapping(
    data: Mapping[str, Any],
    *,
    selected_instance: InstanceUiState | None,
) -> tuple[BackupUiState, ...]:
    """Create top-level backup summaries for Manager UI state."""

    backups = tuple(
        BackupUiState.from_mapping(item)
        for item in data.get("backups", ()) or ()
        if isinstance(item, Mapping)
    )
    if backups:
        return backups

    latest_backup_data = data.get("latest_backup") or data.get("backup")
    if isinstance(latest_backup_data, Mapping):
        backup = BackupUiState.from_mapping(latest_backup_data)
        return (backup,) if _backup_has_data(backup) else ()

    if selected_instance is not None and _backup_has_data(selected_instance.latest_backup):
        return (selected_instance.latest_backup,)

    return ()


def _backup_has_data(backup: BackupUiState) -> bool:
    """Return whether a backup summary contains any concrete data."""

    return any(
        value not in {None, ""}
        for value in (
            backup.backup_id,
            backup.status,
            backup.backup_class,
            backup.created_at,
            backup.verified_at,
            backup.size_bytes,
            backup.path,
        )
    )


def normalize_instance_state(value: Any) -> str:
    """Normalize and validate an instance state."""

    return _normalize_enum_value(
        value,
        enum_type=InstanceState,
        default=InstanceState.CREATED.value,
        field_name="instance state",
    )


def normalize_network_profile(value: Any) -> str:
    """Normalize and validate a network profile."""

    return _normalize_enum_value(
        value,
        enum_type=NetworkProfile,
        default=MANAGER_DEFAULT_NETWORK_PROFILE,
        field_name="network profile",
    )


def normalize_exposure_mode(value: Any) -> str:
    """Normalize and validate an exposure mode."""

    return _normalize_enum_value(
        value,
        enum_type=ExposureMode,
        default=MANAGER_DEFAULT_EXPOSURE_MODE,
        field_name="exposure mode",
    )


def normalize_security_status(value: Any) -> str:
    """Normalize and validate a Security Gate status."""

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
    """Normalize enum values while rejecting invented canonical values."""

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


def badge_for_instance_state(value: Any) -> UiBadge:
    """Return a presentation badge for a canonical instance state."""

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
        InstanceState.READY.value: UiSeverity.SUCCESS,
        InstanceState.RUNNING.value: UiSeverity.SUCCESS,
        InstanceState.DEGRADED.value: UiSeverity.WARNING,
        InstanceState.FAILED.value: UiSeverity.DANGER,
        InstanceState.SECURITY_BLOCKED.value: UiSeverity.DANGER,
        InstanceState.STOPPED.value: UiSeverity.DISABLED,
    }

    return UiBadge(
        value=state,
        label=labels[state],
        severity=severities.get(state, UiSeverity.INFO),
    )


def badge_for_security_status(value: Any) -> UiBadge:
    """Return a presentation badge for a canonical Security Gate status."""

    status = normalize_security_status(value)
    labels = {
        SecurityGateStatus.PASS.value: "Security PASS",
        SecurityGateStatus.WARN.value: "Security WARN",
        SecurityGateStatus.FAIL_BLOCKING.value: "Security blocked",
        SecurityGateStatus.SKIPPED.value: "Security skipped",
        SecurityGateStatus.UNKNOWN.value: "Security unknown",
    }

    severities = {
        SecurityGateStatus.PASS.value: UiSeverity.SUCCESS,
        SecurityGateStatus.WARN.value: UiSeverity.WARNING,
        SecurityGateStatus.FAIL_BLOCKING.value: UiSeverity.DANGER,
        SecurityGateStatus.SKIPPED.value: UiSeverity.DISABLED,
        SecurityGateStatus.UNKNOWN.value: UiSeverity.NEUTRAL,
    }

    return UiBadge(value=status, label=labels[status], severity=severities[status])


def badge_for_network_profile(value: Any) -> UiBadge:
    """Return a presentation badge for a canonical network profile."""

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
        NetworkProfile.PUBLIC_TEMPORARY.value: UiSeverity.WARNING,
        NetworkProfile.PUBLIC_VPS.value: UiSeverity.WARNING,
        NetworkProfile.OFFLINE.value: UiSeverity.DISABLED,
    }

    return UiBadge(
        value=profile,
        label=labels[profile],
        severity=severities.get(profile, UiSeverity.INFO),
    )


def badge_for_exposure_mode(value: Any) -> UiBadge:
    """Return a presentation badge for a canonical exposure mode."""

    mode = normalize_exposure_mode(value)
    labels = {
        ExposureMode.PRIVATE.value: "Private",
        ExposureMode.LAN.value: "LAN",
        ExposureMode.VPN.value: "VPN",
        ExposureMode.TEMPORARY_TUNNEL.value: "Temporary tunnel",
        ExposureMode.PUBLIC.value: "Public",
    }

    severities = {
        ExposureMode.PRIVATE.value: UiSeverity.SUCCESS,
        ExposureMode.LAN.value: UiSeverity.INFO,
        ExposureMode.VPN.value: UiSeverity.INFO,
        ExposureMode.TEMPORARY_TUNNEL.value: UiSeverity.WARNING,
        ExposureMode.PUBLIC.value: UiSeverity.WARNING,
    }

    return UiBadge(value=mode, label=labels[mode], severity=severities[mode])


def badge_for_backup_status(value: Any) -> UiBadge:
    """Return a presentation badge for a canonical backup status."""

    if value in {None, ""}:
        return UiBadge(
            value="none",
            label="No backup",
            severity=UiSeverity.NEUTRAL,
        )

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
        BackupStatus.VERIFIED.value: UiSeverity.SUCCESS,
        BackupStatus.RUNNING.value: UiSeverity.INFO,
        BackupStatus.VERIFYING.value: UiSeverity.INFO,
        BackupStatus.FAILED.value: UiSeverity.DANGER,
        BackupStatus.EXPIRED.value: UiSeverity.DISABLED,
        BackupStatus.DELETED.value: UiSeverity.DISABLED,
        BackupStatus.QUARANTINED.value: UiSeverity.WARNING,
    }

    return UiBadge(
        value=status,
        label=labels[status],
        severity=severities.get(status, UiSeverity.NEUTRAL),
    )


def badge_for_restore_status(value: Any) -> UiBadge:
    """Return a presentation badge for a canonical restore status."""

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
        warning_values={RestoreStatus.DEGRADED.value},
        danger_values={RestoreStatus.FAILED.value},
    )


def badge_for_rollback_status(value: Any) -> UiBadge:
    """Return a presentation badge for a canonical rollback status."""

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


def _badge_for_resource_status(
    *,
    value: Any,
    enum_type: type[StrEnum],
    labels: Mapping[str, str],
    success_values: set[str],
    warning_values: set[str],
    danger_values: set[str],
) -> UiBadge:
    """Return a generic badge for restore/rollback status resources."""

    status = _normalize_enum_value(
        value,
        enum_type=enum_type,
        default=next(iter(labels.keys())),
        field_name="resource status",
    )

    if status in success_values:
        severity = UiSeverity.SUCCESS
    elif status in warning_values:
        severity = UiSeverity.WARNING
    elif status in danger_values:
        severity = UiSeverity.DANGER
    else:
        severity = UiSeverity.INFO

    return UiBadge(value=status, label=labels[status], severity=severity)


def _optional_str(value: Any) -> str | None:
    """Convert optional values to strings."""

    if value in {None, ""}:
        return None
    return str(value)


def _optional_int(value: Any, *, default: int) -> int:
    """Convert optional values to integers."""

    if value in {None, ""}:
        return default

    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _tuple_str(value: Any) -> tuple[str, ...]:
    """Convert API list-ish values to a tuple of strings."""

    if value in {None, ""}:
        return ()

    if isinstance(value, str):
        return (value,)

    if isinstance(value, Sequence):
        return tuple(str(item) for item in value)

    return (str(value),)


def _to_bool(value: Any) -> bool:
    """Parse common boolean-ish values."""

    if isinstance(value, bool):
        return value

    if value is None:
        return False

    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _utc_now_iso() -> str:
    """Return current UTC time as an ISO string."""

    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


__all__ = [
    "BackupUiState",
    "BuildTargetUiState",
    "CapsuleUiState",
    "DropletTargetUiState",
    "InstanceUiState",
    "ManagerUiState",
    "NetworkUiState",
    "SecurityCheckUiState",
    "SecurityUiState",
    "TargetModeUiState",
    "UiBadge",
    "UiSeverity",
    "badge_for_backup_status",
    "badge_for_exposure_mode",
    "badge_for_instance_state",
    "badge_for_network_profile",
    "badge_for_restore_status",
    "badge_for_rollback_status",
    "badge_for_security_status",
    "normalize_exposure_mode",
    "normalize_instance_state",
    "normalize_network_profile",
    "normalize_security_status",
]