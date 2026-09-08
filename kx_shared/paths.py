"""
Canonical filesystem path helpers for Konnaxion.

This module is intentionally small and side-effect free:
- It does not create directories unless explicitly asked through ensure_dir().
- It does not read environment variables.
- It does not hardcode paths outside the canonical /opt/konnaxion tree.
- It validates path identifiers to prevent traversal such as "../x".

All canonical root constants should come from kx_shared.konnaxion_constants.
"""

from __future__ import annotations

from pathlib import Path
import re
from typing import Iterable

from .konnaxion_constants import (
    CAPSULE_EXTENSION,
    KX_AGENT_DIR,
    KX_BACKUPS_ROOT,
    KX_CAPSULES_DIR,
    KX_INSTANCES_DIR,
    KX_MANAGER_DIR,
    KX_RELEASES_DIR,
    KX_ROOT,
    KX_SHARED_DIR,
)


_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


class KonnaxionPathError(ValueError):
    """Raised when an unsafe or invalid Konnaxion path identifier is used."""


def _as_path(value: str | Path) -> Path:
    return value if isinstance(value, Path) else Path(value)


def validate_safe_id(value: str, *, field_name: str = "id") -> str:
    """
    Validate a Konnaxion path identifier.

    Valid examples:
        demo-001
        konnaxion-v14-demo-2026.04.30
        20260430_173000

    Invalid examples:
        ../demo
        /tmp/demo
        demo/001
        ""
    """
    if not isinstance(value, str):
        raise KonnaxionPathError(f"{field_name} must be a string")

    if not value:
        raise KonnaxionPathError(f"{field_name} must not be empty")

    if "/" in value or "\\" in value:
        raise KonnaxionPathError(f"{field_name} must not contain path separators")

    if value in {".", ".."} or ".." in value.split("."):
        raise KonnaxionPathError(f"{field_name} must not contain traversal segments")

    if not _SAFE_ID_RE.fullmatch(value):
        raise KonnaxionPathError(
            f"{field_name} must match {_SAFE_ID_RE.pattern!r}; got {value!r}"
        )

    return value


def assert_under_root(path: str | Path, root: str | Path = KX_ROOT) -> Path:
    """
    Resolve a path and ensure it remains under the canonical Konnaxion root.

    This prevents accidental or malicious writes outside /opt/konnaxion.
    """
    resolved_path = _as_path(path).resolve(strict=False)
    resolved_root = _as_path(root).resolve(strict=False)

    try:
        resolved_path.relative_to(resolved_root)
    except ValueError as exc:
        raise KonnaxionPathError(
            f"path {resolved_path} is outside canonical root {resolved_root}"
        ) from exc

    return resolved_path


def ensure_dir(path: str | Path, *, mode: int = 0o750) -> Path:
    """
    Create a directory under KX_ROOT if it does not exist and return it.

    This function intentionally refuses paths outside the canonical root.
    """
    safe_path = assert_under_root(path)
    safe_path.mkdir(parents=True, exist_ok=True)
    safe_path.chmod(mode)
    return safe_path


# ---------------------------------------------------------------------
# Canonical root-level directories
# ---------------------------------------------------------------------


def root_dir() -> Path:
    return assert_under_root(KX_ROOT)


def capsules_dir() -> Path:
    return assert_under_root(KX_CAPSULES_DIR)


def instances_dir() -> Path:
    return assert_under_root(KX_INSTANCES_DIR)


def shared_dir() -> Path:
    return assert_under_root(KX_SHARED_DIR)


def releases_dir() -> Path:
    return assert_under_root(KX_RELEASES_DIR)


def manager_dir() -> Path:
    return assert_under_root(KX_MANAGER_DIR)


def agent_dir() -> Path:
    return assert_under_root(KX_AGENT_DIR)


def backups_root() -> Path:
    return assert_under_root(KX_BACKUPS_ROOT)


def canonical_root_dirs() -> tuple[Path, ...]:
    """Return the canonical top-level Konnaxion directories."""
    return (
        capsules_dir(),
        instances_dir(),
        shared_dir(),
        releases_dir(),
        manager_dir(),
        agent_dir(),
        backups_root(),
    )


def artifact_registry_dir() -> Path:
    """Return the shared installed-artifact registry directory."""
    return assert_under_root(shared_dir() / "registry")


def artifact_registry_file() -> Path:
    """Return the canonical installed-artifact registry JSON file."""
    return assert_under_root(artifact_registry_dir() / "installed-artifacts.json")


# ---------------------------------------------------------------------
# Capsule paths
# ---------------------------------------------------------------------


def normalize_capsule_filename(capsule_id_or_filename: str) -> str:
    """
    Return a canonical capsule filename ending in .kxcap.

    Accepts either a capsule id or an existing .kxcap filename.
    """
    if capsule_id_or_filename.endswith(CAPSULE_EXTENSION):
        capsule_id = capsule_id_or_filename[: -len(CAPSULE_EXTENSION)]
    else:
        capsule_id = capsule_id_or_filename

    capsule_id = validate_safe_id(capsule_id, field_name="capsule_id")
    return f"{capsule_id}{CAPSULE_EXTENSION}"


def capsule_file(capsule_id_or_filename: str) -> Path:
    """Return /opt/konnaxion/capsules/<CAPSULE_ID>.kxcap."""
    filename = normalize_capsule_filename(capsule_id_or_filename)
    return assert_under_root(capsules_dir() / filename)


def capsule_extract_dir(capsule_id: str) -> Path:
    """Return a deterministic shared extraction/work directory for a capsule."""
    capsule_id = validate_safe_id(capsule_id, field_name="capsule_id")
    return assert_under_root(shared_dir() / "capsules" / capsule_id)


# ---------------------------------------------------------------------
# Instance paths
# ---------------------------------------------------------------------


def instance_dir(instance_id: str) -> Path:
    """Return /opt/konnaxion/instances/<INSTANCE_ID>."""
    instance_id = validate_safe_id(instance_id, field_name="instance_id")
    return assert_under_root(instances_dir() / instance_id)


def instance_env_dir(instance_id: str) -> Path:
    return assert_under_root(instance_dir(instance_id) / "env")


def instance_postgres_dir(instance_id: str) -> Path:
    return assert_under_root(instance_dir(instance_id) / "postgres")


def instance_redis_dir(instance_id: str) -> Path:
    return assert_under_root(instance_dir(instance_id) / "redis")


def instance_media_dir(instance_id: str) -> Path:
    return assert_under_root(instance_dir(instance_id) / "media")


def instance_logs_dir(instance_id: str) -> Path:
    return assert_under_root(instance_dir(instance_id) / "logs")


def instance_local_backups_dir(instance_id: str) -> Path:
    """
    Return optional instance-local backup pointer/cache/state directory.

    Canonical backup storage is backups_instance_root().
    """
    return assert_under_root(instance_dir(instance_id) / "backups")


def instance_state_dir(instance_id: str) -> Path:
    return assert_under_root(instance_dir(instance_id) / "state")


def instance_compose_file(instance_id: str) -> Path:
    """Return /opt/konnaxion/instances/<INSTANCE_ID>/state/docker-compose.runtime.yml."""
    return assert_under_root(instance_state_dir(instance_id) / "docker-compose.runtime.yml")


def instance_state_file(instance_id: str) -> Path:
    return assert_under_root(instance_state_dir(instance_id) / "instance-state.json")


def instance_manifest_file(instance_id: str) -> Path:
    return assert_under_root(instance_state_dir(instance_id) / "manifest.yaml")


def instance_security_gate_file(instance_id: str) -> Path:
    return assert_under_root(instance_state_dir(instance_id) / "security-gate.json")


def instance_current_capsule_link(instance_id: str) -> Path:
    return assert_under_root(instance_state_dir(instance_id) / "current-capsule.kxcap")


def instance_previous_capsule_link(instance_id: str) -> Path:
    return assert_under_root(instance_state_dir(instance_id) / "previous-capsule.kxcap")


def instance_env_file(instance_id: str, name: str) -> Path:
    """
    Return an env file path under /opt/konnaxion/instances/<INSTANCE_ID>/env/.

    Example:
        instance_env_file("demo-001", "django.env")
    """
    if not name.endswith(".env"):
        raise KonnaxionPathError("env file name must end with .env")
    validate_safe_id(name[:-4], field_name="env_file_name")
    return assert_under_root(instance_env_dir(instance_id) / name)


def instance_required_dirs(instance_id: str) -> tuple[Path, ...]:
    """Return all canonical directories required for an instance."""
    return (
        instance_env_dir(instance_id),
        instance_postgres_dir(instance_id),
        instance_redis_dir(instance_id),
        instance_media_dir(instance_id),
        instance_logs_dir(instance_id),
        instance_local_backups_dir(instance_id),
        instance_state_dir(instance_id),
    )


# ---------------------------------------------------------------------
# Backup, restore, and rollback paths
# ---------------------------------------------------------------------


def backups_instance_root(instance_id: str) -> Path:
    """Return /opt/konnaxion/backups/<INSTANCE_ID>."""
    instance_id = validate_safe_id(instance_id, field_name="instance_id")
    return assert_under_root(backups_root() / instance_id)


def backup_class_dir(instance_id: str, backup_class: str) -> Path:
    """
    Return /opt/konnaxion/backups/<INSTANCE_ID>/<BACKUP_CLASS>.

    backup_class examples:
        manual
        scheduled
        pre_update
        pre_restore
    """
    backup_class = validate_safe_id(backup_class, field_name="backup_class")
    return assert_under_root(backups_instance_root(instance_id) / backup_class)


def backup_dir(instance_id: str, backup_class: str, backup_id: str) -> Path:
    """Return /opt/konnaxion/backups/<INSTANCE_ID>/<BACKUP_CLASS>/<BACKUP_ID>."""
    backup_id = validate_safe_id(backup_id, field_name="backup_id")
    return assert_under_root(backup_class_dir(instance_id, backup_class) / backup_id)


def backup_manifest_file(instance_id: str, backup_class: str, backup_id: str) -> Path:
    return assert_under_root(backup_dir(instance_id, backup_class, backup_id) / "backup-manifest.json")


def backup_database_file(instance_id: str, backup_class: str, backup_id: str) -> Path:
    return assert_under_root(backup_dir(instance_id, backup_class, backup_id) / "postgres.dump")


def backup_media_archive(instance_id: str, backup_class: str, backup_id: str) -> Path:
    return assert_under_root(backup_dir(instance_id, backup_class, backup_id) / "media.tar.zst")


def backup_env_archive(instance_id: str, backup_class: str, backup_id: str) -> Path:
    return assert_under_root(backup_dir(instance_id, backup_class, backup_id) / "env.tar.zst")


def backup_logs_archive(instance_id: str, backup_class: str, backup_id: str) -> Path:
    return assert_under_root(backup_dir(instance_id, backup_class, backup_id) / "logs.tar.zst")


# ---------------------------------------------------------------------
# Release paths
# ---------------------------------------------------------------------


def release_dir(release_id: str) -> Path:
    """Return /opt/konnaxion/releases/<RELEASE_ID>."""
    release_id = validate_safe_id(release_id, field_name="release_id")
    return assert_under_root(releases_dir() / release_id)


def current_release_link() -> Path:
    """Return /opt/konnaxion/current."""
    return assert_under_root(root_dir() / "current")


# ---------------------------------------------------------------------
# Bulk helpers
# ---------------------------------------------------------------------


def ensure_root_layout() -> tuple[Path, ...]:
    """Create canonical top-level Konnaxion directories."""
    return tuple(ensure_dir(path) for path in canonical_root_dirs())


def ensure_instance_layout(instance_id: str) -> tuple[Path, ...]:
    """Create canonical instance directories."""
    return tuple(ensure_dir(path) for path in instance_required_dirs(instance_id))


def path_list_as_strings(paths: Iterable[Path]) -> list[str]:
    """Return paths as strings for JSON/API responses."""
    return [str(path) for path in paths]


__all__ = [
    "artifact_registry_dir",
    "artifact_registry_file",
    "KonnaxionPathError",
    "validate_safe_id",
    "assert_under_root",
    "ensure_dir",
    "root_dir",
    "capsules_dir",
    "instances_dir",
    "shared_dir",
    "releases_dir",
    "manager_dir",
    "agent_dir",
    "backups_root",
    "canonical_root_dirs",
    "normalize_capsule_filename",
    "capsule_file",
    "capsule_extract_dir",
    "instance_dir",
    "instance_env_dir",
    "instance_postgres_dir",
    "instance_redis_dir",
    "instance_media_dir",
    "instance_logs_dir",
    "instance_local_backups_dir",
    "instance_state_dir",
    "instance_compose_file",
    "instance_state_file",
    "instance_manifest_file",
    "instance_security_gate_file",
    "instance_current_capsule_link",
    "instance_previous_capsule_link",
    "instance_env_file",
    "instance_required_dirs",
    "backups_instance_root",
    "backup_class_dir",
    "backup_dir",
    "backup_manifest_file",
    "backup_database_file",
    "backup_media_archive",
    "backup_env_archive",
    "backup_logs_archive",
    "release_dir",
    "current_release_link",
    "ensure_root_layout",
    "ensure_instance_layout",
    "path_list_as_strings",
]
