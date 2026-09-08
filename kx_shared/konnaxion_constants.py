"""
Canonical constants for Konnaxion Capsule Manager.

This module is the code-level source of truth for names, paths, profiles,
ports, services, states, security checks, environment defaults, and CLI
commands used by the Konnaxion Capsule Manager, Agent, Builder, and CLI.

Generated from the Konnaxion canonical documentation.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Final


# ---------------------------------------------------------------------------
# POSIX-stable canonical paths
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CanonicalPath(os.PathLike[str]):
    """
    POSIX-stable path object for canonical appliance paths.

    Why this exists:
    - Konnaxion canonical runtime paths are Linux/POSIX paths.
    - On Windows, pathlib.Path("/opt/konnaxion") renders as "\\opt\\konnaxion".
    - Tests and generated env values must remain "/opt/konnaxion/...".

    This object supports the path operations used by the project while keeping
    str(path), path.as_posix(), and env rendering stable across platforms.
    """

    value: str

    def __post_init__(self) -> None:
        normalized = self._normalize(self.value)
        object.__setattr__(self, "value", normalized)

    @staticmethod
    def _normalize(value: str | os.PathLike[str]) -> str:
        text = os.fspath(value).replace("\\", "/").strip()

        if not text:
            return "."

        while "//" in text:
            text = text.replace("//", "/")

        if len(text) > 1:
            text = text.rstrip("/")

        return text

    def __truediv__(self, other: str | os.PathLike[str]) -> "CanonicalPath":
        right = self._normalize(other).lstrip("/")

        if self.value == "/":
            return CanonicalPath(f"/{right}")

        return CanonicalPath(f"{self.value}/{right}")

    def __fspath__(self) -> str:
        return self.value

    def __str__(self) -> str:
        return self.value

    def __repr__(self) -> str:
        return f"CanonicalPath({self.value!r})"

    def __hash__(self) -> int:
        return hash(self.value)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, CanonicalPath):
            return self.value == other.value

        if isinstance(other, os.PathLike):
            other_text = os.fspath(other)
            other_text = other_text.replace("\\", "/")
            return self.value == self._normalize(other_text)

        if isinstance(other, str):
            return self.value == self._normalize(other)

        return False

    @property
    def parent(self) -> "CanonicalPath":
        if self.value in {".", "/"}:
            return self

        parent = self.value.rsplit("/", 1)[0]

        if not parent:
            parent = "/"

        return CanonicalPath(parent)

    @property
    def name(self) -> str:
        if self.value == "/":
            return ""

        return self.value.rsplit("/", 1)[-1]

    @property
    def suffix(self) -> str:
        name = self.name

        if "." not in name:
            return ""

        return "." + name.rsplit(".", 1)[-1]

    @property
    def stem(self) -> str:
        name = self.name

        if "." not in name:
            return name

        return name.rsplit(".", 1)[0]

    def as_posix(self) -> str:
        return self.value

    def is_absolute(self) -> bool:
        return self.value.startswith("/")

    def joinpath(self, *parts: str | os.PathLike[str]) -> "CanonicalPath":
        current = self

        for part in parts:
            current = current / part

        return current

    def relative_to(self, other: str | os.PathLike[str]) -> "CanonicalPath":
        base = self._normalize(other).rstrip("/")
        value = self.value

        if value == base:
            return CanonicalPath(".")

        prefix = base + "/"

        if not value.startswith(prefix):
            raise ValueError(f"{value!r} is not relative to {base!r}")

        return CanonicalPath(value[len(prefix) :])

    def with_name(self, name: str) -> "CanonicalPath":
        return self.parent / name

    def with_suffix(self, suffix: str) -> "CanonicalPath":
        if suffix and not suffix.startswith("."):
            suffix = "." + suffix

        return self.parent / f"{self.stem}{suffix}"

    def to_path(self) -> Path:
        """Return a platform pathlib.Path for filesystem operations."""
        return Path(self.value)

    def exists(self) -> bool:
        return self.to_path().exists()

    def is_file(self) -> bool:
        return self.to_path().is_file()

    def is_dir(self) -> bool:
        return self.to_path().is_dir()

    def mkdir(self, *args: Any, **kwargs: Any) -> None:
        self.to_path().mkdir(*args, **kwargs)

    def open(self, *args: Any, **kwargs: Any) -> Any:
        return self.to_path().open(*args, **kwargs)

    def read_text(self, *args: Any, **kwargs: Any) -> str:
        return self.to_path().read_text(*args, **kwargs)

    def write_text(self, *args: Any, **kwargs: Any) -> int:
        return self.to_path().write_text(*args, **kwargs)

    def read_bytes(self) -> bytes:
        return self.to_path().read_bytes()

    def write_bytes(self, data: bytes) -> int:
        return self.to_path().write_bytes(data)

    def resolve(self, *args: Any, **kwargs: Any) -> Path:
        return self.to_path().resolve(*args, **kwargs)

    def expanduser(self) -> Path:
        return self.to_path().expanduser()


def canonical_path(value: str | os.PathLike[str]) -> CanonicalPath:
    """Create a POSIX-stable canonical path."""
    return CanonicalPath(os.fspath(value))


def enum_value(value: object) -> str:
    """Return enum .value when available, otherwise stable string."""
    return str(getattr(value, "value", value))


# ---------------------------------------------------------------------------
# Product identity
# ---------------------------------------------------------------------------

PRODUCT_NAME: Final[str] = "Konnaxion"
APP_VERSION: Final[str] = "v14"
PARAM_VERSION: Final[str] = "kx-param-2026.04.30"

CAPSULE_EXTENSION: Final[str] = ".kxcap"
CAPSULE_FILENAME_PATTERN: Final[str] = "konnaxion-v14-{channel}-{date}.kxcap"

DEFAULT_CHANNEL: Final[str] = "demo"
DEFAULT_INSTANCE_ID: Final[str] = "demo-001"
DEFAULT_RELEASE_ID: Final[str] = "20260430_173000"
DEFAULT_CAPSULE_ID: Final[str] = "konnaxion-v14-demo-2026.04.30"
DEFAULT_CAPSULE_VERSION: Final[str] = "2026.04.30-demo.1"

MANAGER_NAME: Final[str] = "Konnaxion Capsule Manager"
AGENT_NAME: Final[str] = "Konnaxion Agent"
BUILDER_NAME: Final[str] = "Konnaxion Capsule Builder"
BOX_NAME: Final[str] = "Konnaxion Box"
HOST_NAME: Final[str] = "Konnaxion Host"
INSTANCE_NAME: Final[str] = "Konnaxion Instance"
CAPSULE_NAME: Final[str] = "Konnaxion Capsule"


# ---------------------------------------------------------------------------
# Canonical paths
# ---------------------------------------------------------------------------

KX_ROOT: Final[CanonicalPath] = canonical_path(os.getenv("KX_ROOT", "").strip() or "/opt/konnaxion")

KX_CAPSULES_DIR: Final[CanonicalPath] = KX_ROOT / "capsules"
KX_INSTANCES_DIR: Final[CanonicalPath] = KX_ROOT / "instances"
KX_SHARED_DIR: Final[CanonicalPath] = KX_ROOT / "shared"
KX_RELEASES_DIR: Final[CanonicalPath] = KX_ROOT / "releases"
KX_MANAGER_DIR: Final[CanonicalPath] = KX_ROOT / "manager"
KX_AGENT_DIR: Final[CanonicalPath] = KX_ROOT / "agent"
KX_BACKUPS_ROOT: Final[CanonicalPath] = KX_ROOT / "backups"

LEGACY_VPS_ROOT: Final[CanonicalPath] = canonical_path("/home/deploy/apps/Konnaxion")
LEGACY_VPS_BACKEND: Final[CanonicalPath] = LEGACY_VPS_ROOT / "backend"
LEGACY_VPS_FRONTEND: Final[CanonicalPath] = LEGACY_VPS_ROOT / "frontend"

LEGACY_VPS_PATHS: Final[tuple[CanonicalPath, ...]] = (
    LEGACY_VPS_ROOT,
    LEGACY_VPS_BACKEND,
    LEGACY_VPS_FRONTEND,
)


def instance_root(instance_id: str) -> CanonicalPath:
    """Return the canonical root directory for an instance."""
    return KX_INSTANCES_DIR / instance_id


def instance_env_dir(instance_id: str) -> CanonicalPath:
    """Return the canonical env directory for an instance."""
    return instance_root(instance_id) / "env"


def instance_postgres_dir(instance_id: str) -> CanonicalPath:
    """Return the canonical Postgres data directory for an instance."""
    return instance_root(instance_id) / "postgres"


def instance_redis_dir(instance_id: str) -> CanonicalPath:
    """Return the canonical Redis data directory for an instance."""
    return instance_root(instance_id) / "redis"


def instance_media_dir(instance_id: str) -> CanonicalPath:
    """Return the canonical media directory for an instance."""
    return instance_root(instance_id) / "media"


def instance_logs_dir(instance_id: str) -> CanonicalPath:
    """Return the canonical logs directory for an instance."""
    return instance_root(instance_id) / "logs"


def instance_local_backups_dir(instance_id: str) -> CanonicalPath:
    """Return the optional instance-local backup pointer/cache/state directory."""
    return instance_root(instance_id) / "backups"


def instance_state_dir(instance_id: str) -> CanonicalPath:
    """Return the canonical state directory for an instance."""
    return instance_root(instance_id) / "state"


def instance_compose_file(instance_id: str) -> CanonicalPath:
    """Return the generated runtime Docker Compose file path for an instance."""
    return instance_state_dir(instance_id) / "docker-compose.runtime.yml"


def instance_backup_root(instance_id: str) -> CanonicalPath:
    """Return the canonical backup storage root for an instance."""
    return KX_BACKUPS_ROOT / instance_id


def instance_backup_dir(
    instance_id: str,
    backup_class: str,
    backup_id: str,
) -> CanonicalPath:
    """Return the canonical backup artifact directory."""
    return instance_backup_root(instance_id) / backup_class / backup_id


def release_root(release_id: str) -> CanonicalPath:
    """Return the canonical release directory."""
    return KX_RELEASES_DIR / release_id


def capsule_path(capsule_id: str) -> CanonicalPath:
    """Return the canonical on-host capsule path."""
    return KX_CAPSULES_DIR / f"{capsule_id}{CAPSULE_EXTENSION}"


# ---------------------------------------------------------------------------
# Capsule internal layout
# ---------------------------------------------------------------------------

CAPSULE_ROOT_FILES: Final[tuple[str, ...]] = (
    "manifest.yaml",
    "docker-compose.capsule.yml",
    "checksums.txt",
    "signature.sig",
)

CAPSULE_ROOT_DIRS: Final[tuple[str, ...]] = (
    "images",
    "profiles",
    "env-templates",
    "migrations",
    "healthchecks",
    "policies",
    "metadata",
)

CAPSULE_REQUIRED_ROOT_ENTRIES: Final[tuple[str, ...]] = (
    *CAPSULE_ROOT_FILES,
    *CAPSULE_ROOT_DIRS,
)


# ---------------------------------------------------------------------------
# Docker services
# ---------------------------------------------------------------------------


class DockerService(StrEnum):
    """Canonical Docker Compose service names."""

    TRAEFIK = "traefik"
    FRONTEND_NEXT = "frontend-next"
    DJANGO_API = "django-api"
    POSTGRES = "postgres"
    REDIS = "redis"
    CELERYWORKER = "celeryworker"
    CELERYBEAT = "celerybeat"
    FLOWER = "flower"
    MEDIA_NGINX = "media-nginx"
    KX_AGENT = "kx-agent"


CANONICAL_DOCKER_SERVICES: Final[tuple[str, ...]] = tuple(
    service.value for service in DockerService
)

FORBIDDEN_SERVICE_ALIASES: Final[frozenset[str]] = frozenset(
    {
        "backend",
        "api",
        "web",
        "next",
        "frontend",
        "db",
        "database",
        "cache",
        "worker",
        "scheduler",
        "media",
        "agent",
    }
)

SERVICE_ALIAS_REPLACEMENTS: Final[dict[str, str]] = {
    "backend": DockerService.DJANGO_API.value,
    "api": DockerService.DJANGO_API.value,
    "web": DockerService.FRONTEND_NEXT.value,
    "next": DockerService.FRONTEND_NEXT.value,
    "frontend": DockerService.FRONTEND_NEXT.value,
    "db": DockerService.POSTGRES.value,
    "database": DockerService.POSTGRES.value,
    "cache": DockerService.REDIS.value,
    "worker": DockerService.CELERYWORKER.value,
    "scheduler": DockerService.CELERYBEAT.value,
    "media": DockerService.MEDIA_NGINX.value,
    "agent": DockerService.KX_AGENT.value,
}


# ---------------------------------------------------------------------------
# Network profiles and exposure modes
# ---------------------------------------------------------------------------


class NetworkProfile(StrEnum):
    """Canonical Konnaxion network profiles."""

    LOCAL_ONLY = "local_only"
    INTRANET_PRIVATE = "intranet_private"
    PRIVATE_TUNNEL = "private_tunnel"
    PUBLIC_TEMPORARY = "public_temporary"
    PUBLIC_VPS = "public_vps"
    OFFLINE = "offline"


class ExposureMode(StrEnum):
    """Canonical Konnaxion exposure modes."""

    PRIVATE = "private"
    LAN = "lan"
    VPN = "vpn"
    TEMPORARY_TUNNEL = "temporary_tunnel"
    PUBLIC = "public"


DEFAULT_NETWORK_PROFILE: Final[NetworkProfile] = NetworkProfile.INTRANET_PRIVATE
DEFAULT_EXPOSURE_MODE: Final[ExposureMode] = ExposureMode.PRIVATE
DEFAULT_PUBLIC_MODE_ENABLED: Final[bool] = False

CANONICAL_NETWORK_PROFILES: Final[tuple[str, ...]] = tuple(
    profile.value for profile in NetworkProfile
)

CANONICAL_EXPOSURE_MODES: Final[tuple[str, ...]] = tuple(
    mode.value for mode in ExposureMode
)

CANONICAL_PROFILE_FILES: Final[dict[str, str]] = {
    NetworkProfile.LOCAL_ONLY.value: "local_only.yaml",
    NetworkProfile.INTRANET_PRIVATE.value: "intranet_private.yaml",
    NetworkProfile.PRIVATE_TUNNEL.value: "private_tunnel.yaml",
    NetworkProfile.PUBLIC_TEMPORARY.value: "public_temporary.yaml",
    NetworkProfile.PUBLIC_VPS.value: "public_vps.yaml",
    NetworkProfile.OFFLINE.value: "offline.yaml",
}

ALLOWED_PROFILE_EXPOSURE: Final[dict[str, frozenset[str]]] = {
    NetworkProfile.LOCAL_ONLY.value: frozenset({ExposureMode.PRIVATE.value}),
    NetworkProfile.INTRANET_PRIVATE.value: frozenset(
        {
            ExposureMode.PRIVATE.value,
            ExposureMode.LAN.value,
        }
    ),
    NetworkProfile.PRIVATE_TUNNEL.value: frozenset(
        {
            ExposureMode.PRIVATE.value,
            ExposureMode.VPN.value,
        }
    ),
    NetworkProfile.PUBLIC_TEMPORARY.value: frozenset(
        {
            ExposureMode.TEMPORARY_TUNNEL.value,
        }
    ),
    NetworkProfile.PUBLIC_VPS.value: frozenset(
        {
            ExposureMode.PUBLIC.value,
        }
    ),
    NetworkProfile.OFFLINE.value: frozenset({ExposureMode.PRIVATE.value}),
}


def is_canonical_network_profile(value: object) -> bool:
    """Return True when value is a canonical Konnaxion network profile."""
    return enum_value(value) in CANONICAL_NETWORK_PROFILES


def is_canonical_exposure_mode(value: object) -> bool:
    """Return True when value is a canonical Konnaxion exposure mode."""
    return enum_value(value) in CANONICAL_EXPOSURE_MODES


def is_canonical_service(value: object) -> bool:
    """Return True when value is a canonical Docker service name."""
    return enum_value(value) in CANONICAL_DOCKER_SERVICES


def is_public_mode(
    network_profile: object = DEFAULT_NETWORK_PROFILE,
    exposure_mode: object = DEFAULT_EXPOSURE_MODE,
    public_mode_enabled: bool = DEFAULT_PUBLIC_MODE_ENABLED,
) -> bool:
    """Return True when profile/mode represents public exposure."""
    profile = enum_value(network_profile)
    mode = enum_value(exposure_mode)

    return (
        bool(public_mode_enabled)
        or profile in {
            NetworkProfile.PUBLIC_TEMPORARY.value,
            NetworkProfile.PUBLIC_VPS.value,
        }
        or mode in {
            ExposureMode.TEMPORARY_TUNNEL.value,
            ExposureMode.PUBLIC.value,
        }
    )


def require_public_expiration(
    *,
    network_profile: object = DEFAULT_NETWORK_PROFILE,
    exposure_mode: object = DEFAULT_EXPOSURE_MODE,
    public_mode_enabled: bool = DEFAULT_PUBLIC_MODE_ENABLED,
    public_mode_expires_at: str | None = None,
    expires_at: str | None = None,
) -> None:
    """Require an expiration timestamp for temporary public exposure."""
    profile = enum_value(network_profile)
    mode = enum_value(exposure_mode)
    expiration = public_mode_expires_at or expires_at

    temporary_public = (
        profile == NetworkProfile.PUBLIC_TEMPORARY.value
        or mode == ExposureMode.TEMPORARY_TUNNEL.value
    )

    if temporary_public and bool(public_mode_enabled) and not expiration:
        raise ValueError("Temporary public exposure requires an expiration timestamp.")


# ---------------------------------------------------------------------------
# Ports and routes
# ---------------------------------------------------------------------------

ALLOWED_ENTRY_PORTS: Final[dict[str, int]] = {
    "https": 443,
    "http_redirect": 80,
    "ssh_admin_restricted": 22,
}

INTERNAL_ONLY_PORTS: Final[dict[DockerService | str, int]] = {
    DockerService.FRONTEND_NEXT: 3000,
    DockerService.DJANGO_API: 5000,
    DockerService.POSTGRES: 5432,
    DockerService.REDIS: 6379,
    DockerService.FLOWER: 5555,
    "django_dev_server": 8000,
}

FORBIDDEN_PUBLIC_PORTS: Final[frozenset[int]] = frozenset(INTERNAL_ONLY_PORTS.values())

ROUTES: Final[dict[str, str]] = {
    "/": DockerService.FRONTEND_NEXT.value,
    "/api/": DockerService.DJANGO_API.value,
    "/admin/": DockerService.DJANGO_API.value,
    "/media/": DockerService.MEDIA_NGINX.value,
}


# ---------------------------------------------------------------------------
# Runtime environment defaults
# ---------------------------------------------------------------------------

DJANGO_ENV_DEFAULTS: Final[dict[str, str]] = {
    "DJANGO_SETTINGS_MODULE": "config.settings.production",
    "DJANGO_SECRET_KEY": "<GENERATED_ON_INSTALL>",
    "DJANGO_DEBUG": "False",
    "DJANGO_ALLOWED_HOSTS": "<GENERATED_FROM_PROFILE>",
    "DJANGO_ADMIN_URL": "admin/",
    "USE_DOCKER": "yes",
    "SENTRY_DSN": "",
}

DATABASE_ENV_DEFAULTS: Final[dict[str, str]] = {
    "DATABASE_URL": "postgres://konnaxion:<POSTGRES_PASSWORD>@postgres:5432/konnaxion",
    "POSTGRES_HOST": DockerService.POSTGRES.value,
    "POSTGRES_PORT": "5432",
    "POSTGRES_DB": "konnaxion",
    "POSTGRES_USER": "konnaxion",
    "POSTGRES_PASSWORD": "<GENERATED_ON_INSTALL>",
}

REDIS_ENV_DEFAULTS: Final[dict[str, str]] = {
    "REDIS_URL": "redis://redis:6379/0",
    "CELERY_BROKER_URL": "redis://redis:6379/0",
    "CELERY_RESULT_BACKEND": "redis://redis:6379/0",
}

FRONTEND_ENV_DEFAULTS: Final[dict[str, str]] = {
    "NEXT_PUBLIC_API_BASE": "https://<PUBLIC_OR_PRIVATE_HOST>/api",
    "NEXT_PUBLIC_BACKEND_BASE": "https://<PUBLIC_OR_PRIVATE_HOST>",
    "NEXT_TELEMETRY_DISABLED": "1",
    "NODE_OPTIONS": "--max-old-space-size=4096",
}

KX_ENV_DEFAULTS: Final[dict[str, str]] = {
    "KX_INSTANCE_ID": DEFAULT_INSTANCE_ID,
    "KX_CAPSULE_ID": DEFAULT_CAPSULE_ID,
    "KX_CAPSULE_VERSION": DEFAULT_CAPSULE_VERSION,
    "KX_APP_VERSION": APP_VERSION,
    "KX_PARAM_VERSION": PARAM_VERSION,
    "KX_NETWORK_PROFILE": DEFAULT_NETWORK_PROFILE.value,
    "KX_EXPOSURE_MODE": DEFAULT_EXPOSURE_MODE.value,
    "KX_PUBLIC_MODE_ENABLED": "false",
    "KX_PUBLIC_MODE_EXPIRES_AT": "",
    "KX_REQUIRE_SIGNED_CAPSULE": "true",
    "KX_GENERATE_SECRETS_ON_INSTALL": "true",
    "KX_ALLOW_UNKNOWN_IMAGES": "false",
    "KX_ALLOW_PRIVILEGED_CONTAINERS": "false",
    "KX_ALLOW_DOCKER_SOCKET_MOUNT": "false",
    "KX_ALLOW_HOST_NETWORK": "false",
    "KX_BACKUP_ENABLED": "true",
    "KX_BACKUP_ROOT": "/opt/konnaxion/backups",
    "KX_BACKUP_RETENTION_DAYS": "14",
    "KX_DAILY_BACKUP_RETENTION_DAYS": "14",
    "KX_WEEKLY_BACKUP_RETENTION_WEEKS": "8",
    "KX_MONTHLY_BACKUP_RETENTION_MONTHS": "12",
    "KX_PRE_UPDATE_BACKUP_RETENTION_COUNT": "5",
    "KX_PRE_RESTORE_BACKUP_RETENTION_COUNT": "5",
    "KX_HOST": "",
}

ALL_ENV_DEFAULTS: Final[dict[str, str]] = {
    **DJANGO_ENV_DEFAULTS,
    **DATABASE_ENV_DEFAULTS,
    **REDIS_ENV_DEFAULTS,
    **FRONTEND_ENV_DEFAULTS,
    **KX_ENV_DEFAULTS,
}


# ---------------------------------------------------------------------------
# Security Gate
# ---------------------------------------------------------------------------


class SecurityGateStatus(StrEnum):
    """Canonical Security Gate result statuses."""

    PASS = "PASS"
    WARN = "WARN"
    FAIL_BLOCKING = "FAIL_BLOCKING"
    SKIPPED = "SKIPPED"
    UNKNOWN = "UNKNOWN"


class SecurityGateCheck(StrEnum):
    """Canonical Security Gate checks."""

    CAPSULE_SIGNATURE = "capsule_signature"
    IMAGE_CHECKSUMS = "image_checksums"
    MANIFEST_SCHEMA = "manifest_schema"
    SECRETS_PRESENT = "secrets_present"
    SECRETS_NOT_DEFAULT = "secrets_not_default"
    FIREWALL_ENABLED = "firewall_enabled"
    DANGEROUS_PORTS_BLOCKED = "dangerous_ports_blocked"
    POSTGRES_NOT_PUBLIC = "postgres_not_public"
    REDIS_NOT_PUBLIC = "redis_not_public"
    DOCKER_SOCKET_NOT_MOUNTED = "docker_socket_not_mounted"
    NO_PRIVILEGED_CONTAINERS = "no_privileged_containers"
    NO_HOST_NETWORK = "no_host_network"
    ALLOWED_IMAGES_ONLY = "allowed_images_only"
    ADMIN_SURFACE_PRIVATE = "admin_surface_private"
    BACKUP_CONFIGURED = "backup_configured"


CANONICAL_SECURITY_GATE_STATUSES: Final[tuple[str, ...]] = tuple(
    status.value for status in SecurityGateStatus
)

CANONICAL_SECURITY_GATE_CHECKS: Final[tuple[str, ...]] = tuple(
    check.value for check in SecurityGateCheck
)

BLOCKING_SECURITY_CHECKS: Final[frozenset[SecurityGateCheck]] = frozenset(
    {
        SecurityGateCheck.CAPSULE_SIGNATURE,
        SecurityGateCheck.IMAGE_CHECKSUMS,
        SecurityGateCheck.MANIFEST_SCHEMA,
        SecurityGateCheck.SECRETS_PRESENT,
        SecurityGateCheck.SECRETS_NOT_DEFAULT,
        SecurityGateCheck.DANGEROUS_PORTS_BLOCKED,
        SecurityGateCheck.POSTGRES_NOT_PUBLIC,
        SecurityGateCheck.REDIS_NOT_PUBLIC,
        SecurityGateCheck.DOCKER_SOCKET_NOT_MOUNTED,
        SecurityGateCheck.NO_PRIVILEGED_CONTAINERS,
        SecurityGateCheck.NO_HOST_NETWORK,
        SecurityGateCheck.ALLOWED_IMAGES_ONLY,
    }
)


# ---------------------------------------------------------------------------
# Lifecycle states
# ---------------------------------------------------------------------------


class InstanceState(StrEnum):
    """Canonical Konnaxion Instance lifecycle states."""

    CREATED = "created"
    IMPORTING = "importing"
    VERIFYING = "verifying"
    READY = "ready"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    UPDATING = "updating"
    ROLLING_BACK = "rolling_back"
    DEGRADED = "degraded"
    FAILED = "failed"
    SECURITY_BLOCKED = "security_blocked"


CANONICAL_INSTANCE_STATES: Final[tuple[str, ...]] = tuple(
    state.value for state in InstanceState
)


# ---------------------------------------------------------------------------
# Backup / restore / rollback statuses
# ---------------------------------------------------------------------------


class BackupStatus(StrEnum):
    """Canonical backup resource statuses."""

    CREATED = "created"
    RUNNING = "running"
    VERIFYING = "verifying"
    VERIFIED = "verified"
    FAILED = "failed"
    EXPIRED = "expired"
    DELETED = "deleted"
    QUARANTINED = "quarantined"


class RestoreStatus(StrEnum):
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


class RollbackStatus(StrEnum):
    """Canonical rollback resource statuses."""

    PLANNED = "planned"
    RUNNING = "running"
    CAPSULE_REPOINTED = "capsule_repointed"
    DATA_RESTORED = "data_restored"
    HEALTHCHECKING = "healthchecking"
    COMPLETED = "completed"
    FAILED = "failed"


CANONICAL_BACKUP_STATUSES: Final[tuple[str, ...]] = tuple(
    status.value for status in BackupStatus
)

CANONICAL_RESTORE_STATUSES: Final[tuple[str, ...]] = tuple(
    status.value for status in RestoreStatus
)

CANONICAL_ROLLBACK_STATUSES: Final[tuple[str, ...]] = tuple(
    status.value for status in RollbackStatus
)

# Backward-compatible alias used by kx_shared.__init__ and older modules.
BACKUP_STATUS: Final[type[BackupStatus]] = BackupStatus


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

CLI_NAME: Final[str] = "kx"

PUBLIC_CLI_COMMANDS: Final[tuple[str, ...]] = (
    "kx capsule build",
    "kx capsule verify",
    "kx capsule import",
    "kx artifact list",
    "kx artifact show",
    "kx artifact remove",
    "kx instance create",
    "kx instance start",
    "kx instance stop",
    "kx instance status",
    "kx instance logs",
    "kx instance backup",
    "kx instance restore",
    "kx instance restore-new",
    "kx instance update",
    "kx instance rollback",
    "kx instance health",
    "kx backup list",
    "kx backup verify",
    "kx backup test-restore",
    "kx security check",
    "kx network set-profile",
)


__all__ = [
    "AGENT_NAME",
    "ALLOWED_ENTRY_PORTS",
    "ALLOWED_PROFILE_EXPOSURE",
    "ALL_ENV_DEFAULTS",
    "APP_VERSION",
    "BACKUP_STATUS",
    "BLOCKING_SECURITY_CHECKS",
    "BOX_NAME",
    "BUILDER_NAME",
    "BackupStatus",
    "CANONICAL_BACKUP_STATUSES",
    "CANONICAL_DOCKER_SERVICES",
    "CANONICAL_EXPOSURE_MODES",
    "CANONICAL_INSTANCE_STATES",
    "CANONICAL_NETWORK_PROFILES",
    "CANONICAL_PROFILE_FILES",
    "CANONICAL_RESTORE_STATUSES",
    "CANONICAL_ROLLBACK_STATUSES",
    "CANONICAL_SECURITY_GATE_CHECKS",
    "CANONICAL_SECURITY_GATE_STATUSES",
    "CAPSULE_EXTENSION",
    "CAPSULE_FILENAME_PATTERN",
    "CAPSULE_NAME",
    "CAPSULE_REQUIRED_ROOT_ENTRIES",
    "CAPSULE_ROOT_DIRS",
    "CAPSULE_ROOT_FILES",
    "CLI_NAME",
    "CanonicalPath",
    "DATABASE_ENV_DEFAULTS",
    "DEFAULT_CAPSULE_ID",
    "DEFAULT_CAPSULE_VERSION",
    "DEFAULT_CHANNEL",
    "DEFAULT_EXPOSURE_MODE",
    "DEFAULT_INSTANCE_ID",
    "DEFAULT_NETWORK_PROFILE",
    "DEFAULT_PUBLIC_MODE_ENABLED",
    "DEFAULT_RELEASE_ID",
    "DJANGO_ENV_DEFAULTS",
    "DockerService",
    "ExposureMode",
    "FORBIDDEN_PUBLIC_PORTS",
    "FORBIDDEN_SERVICE_ALIASES",
    "FRONTEND_ENV_DEFAULTS",
    "HOST_NAME",
    "INSTANCE_NAME",
    "INTERNAL_ONLY_PORTS",
    "InstanceState",
    "KX_AGENT_DIR",
    "KX_BACKUPS_ROOT",
    "KX_CAPSULES_DIR",
    "KX_ENV_DEFAULTS",
    "KX_INSTANCES_DIR",
    "KX_MANAGER_DIR",
    "KX_RELEASES_DIR",
    "KX_ROOT",
    "KX_SHARED_DIR",
    "LEGACY_VPS_BACKEND",
    "LEGACY_VPS_FRONTEND",
    "LEGACY_VPS_PATHS",
    "LEGACY_VPS_ROOT",
    "MANAGER_NAME",
    "NetworkProfile",
    "PARAM_VERSION",
    "PRODUCT_NAME",
    "PUBLIC_CLI_COMMANDS",
    "REDIS_ENV_DEFAULTS",
    "ROUTES",
    "RestoreStatus",
    "RollbackStatus",
    "SERVICE_ALIAS_REPLACEMENTS",
    "SecurityGateCheck",
    "SecurityGateStatus",
    "canonical_path",
    "capsule_path",
    "enum_value",
    "instance_backup_dir",
    "instance_backup_root",
    "instance_compose_file",
    "instance_env_dir",
    "instance_local_backups_dir",
    "instance_logs_dir",
    "instance_media_dir",
    "instance_postgres_dir",
    "instance_redis_dir",
    "instance_root",
    "instance_state_dir",
    "is_canonical_exposure_mode",
    "is_canonical_network_profile",
    "is_canonical_service",
    "is_public_mode",
    "release_root",
    "require_public_expiration",
]