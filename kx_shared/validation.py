"""
Shared validation helpers for the Konnaxion Capsule Manager codebase.

This module is intentionally small, deterministic, and dependency-light.
Every Manager, Agent, Builder, CLI, and test module should validate against
the canonical constants instead of redefining Konnaxion names, paths, ports,
profiles, service names, or states locally.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
import re
from typing import Any, Iterable, Mapping, Sequence

from .konnaxion_constants import (
    APP_VERSION,
    BLOCKING_SECURITY_CHECKS,
    CANONICAL_DOCKER_SERVICES,
    CAPSULE_EXTENSION,
    DATABASE_ENV_DEFAULTS,
    DEFAULT_EXPOSURE_MODE,
    DEFAULT_NETWORK_PROFILE,
    DJANGO_ENV_DEFAULTS,
    FORBIDDEN_PUBLIC_PORTS,
    FRONTEND_ENV_DEFAULTS,
    KX_BACKUPS_ROOT,
    KX_ENV_DEFAULTS,
    KX_ROOT,
    NetworkProfile,
    ExposureMode,
    InstanceState,
    BackupStatus,
    RestoreStatus,
    RollbackStatus,
    SecurityGateCheck,
    SecurityGateStatus,
)


_IDENTIFIER_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{1,127}$")
_CAPSULE_ID_RE = re.compile(r"^konnaxion-v14-[a-z0-9_.-]+-\d{4}\.\d{2}\.\d{2}$")
_CAPSULE_VERSION_RE = re.compile(r"^\d{4}\.\d{2}\.\d{2}-[a-z0-9_.-]+\.\d+$")
_PARAM_VERSION_RE = re.compile(r"^kx-param-\d{4}\.\d{2}\.\d{2}$")

_FORBIDDEN_SERVICE_ALIASES = frozenset(
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

_SECRET_MARKERS = frozenset(
    {
        "changeme",
        "change-me",
        "change_me",
        "password",
        "secret",
        "default",
        "example",
        "admin",
        "konnaxion",
    }
)

_REQUIRED_MANIFEST_FIELDS = frozenset(
    {
        "schema_version",
        "capsule_id",
        "capsule_version",
        "app_name",
        "app_version",
        "channel",
        "created_at",
        "services",
        "network_profiles",
        "security",
    }
)

_REQUIRED_ENV_KEYS = frozenset(
    {
        *DJANGO_ENV_DEFAULTS.keys(),
        *DATABASE_ENV_DEFAULTS.keys(),
        *FRONTEND_ENV_DEFAULTS.keys(),
        *KX_ENV_DEFAULTS.keys(),
    }
)


@dataclass(frozen=True)
class ValidationIssue:
    """A structured validation issue that can be shown in CLI/API/UI output."""

    code: str
    message: str
    field: str | None = None
    blocking: bool = True


class ValidationFailed(ValueError):
    """Raised when a blocking validation error is found."""

    def __init__(self, issues: Sequence[ValidationIssue]) -> None:
        self.issues = tuple(issues)
        message = "; ".join(issue.message for issue in self.issues)
        super().__init__(message)


def _enum_values(enum_type: type[Enum]) -> set[str]:
    return {str(item.value) for item in enum_type}


def _as_value(value: Any) -> str:
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)


def _issue(code: str, message: str, field: str | None = None, *, blocking: bool = True) -> ValidationIssue:
    return ValidationIssue(code=code, message=message, field=field, blocking=blocking)


def raise_if_issues(issues: Sequence[ValidationIssue]) -> None:
    """Raise ValidationFailed if any issue is blocking."""

    blocking = [issue for issue in issues if issue.blocking]
    if blocking:
        raise ValidationFailed(blocking)


def require_keys(mapping: Mapping[str, Any], required: Iterable[str], *, context: str) -> list[ValidationIssue]:
    """Validate that a mapping contains the required keys."""

    issues: list[ValidationIssue] = []
    for key in sorted(set(required)):
        if key not in mapping:
            issues.append(_issue("missing_key", f"{context} is missing required key: {key}", key))
    return issues


def validate_identifier(value: str, *, field: str = "identifier") -> list[ValidationIssue]:
    """Validate a canonical lowercase identifier such as INSTANCE_ID."""

    if not value:
        return [_issue("empty_identifier", f"{field} must not be empty.", field)]
    if not _IDENTIFIER_RE.fullmatch(value):
        return [
            _issue(
                "invalid_identifier",
                f"{field} must be lowercase and contain only a-z, 0-9, underscore, dot, or hyphen.",
                field,
            )
        ]
    return []


def validate_capsule_filename(filename: str) -> list[ValidationIssue]:
    """Validate that a capsule uses the canonical .kxcap extension."""

    if not filename.endswith(CAPSULE_EXTENSION):
        return [
            _issue(
                "invalid_capsule_extension",
                f"Capsule filename must end with {CAPSULE_EXTENSION}: {filename}",
                "capsule_filename",
            )
        ]
    return []


def validate_capsule_id(capsule_id: str) -> list[ValidationIssue]:
    """Validate the canonical Konnaxion v14 capsule id pattern."""

    if not _CAPSULE_ID_RE.fullmatch(capsule_id):
        return [
            _issue(
                "invalid_capsule_id",
                "capsule_id must match konnaxion-v14-<channel>-YYYY.MM.DD.",
                "capsule_id",
            )
        ]
    return []


def validate_capsule_version(capsule_version: str) -> list[ValidationIssue]:
    """Validate the canonical capsule version pattern."""

    if not _CAPSULE_VERSION_RE.fullmatch(capsule_version):
        return [
            _issue(
                "invalid_capsule_version",
                "capsule_version must match YYYY.MM.DD-<channel>.<build>.",
                "capsule_version",
            )
        ]
    return []


def validate_app_version(app_version: str) -> list[ValidationIssue]:
    """Validate that the app version stays aligned with the canonical version."""

    if app_version != APP_VERSION:
        return [
            _issue(
                "invalid_app_version",
                f"app_version must be {APP_VERSION}, got {app_version}.",
                "app_version",
            )
        ]
    return []


def validate_param_version(param_version: str) -> list[ValidationIssue]:
    """Validate the canonical parameter version pattern."""

    if not _PARAM_VERSION_RE.fullmatch(param_version):
        return [
            _issue(
                "invalid_param_version",
                "param_version must match kx-param-YYYY.MM.DD.",
                "param_version",
            )
        ]
    return []


def validate_network_profile(profile: str | NetworkProfile) -> list[ValidationIssue]:
    """Validate a canonical network profile value."""

    value = _as_value(profile)
    allowed = _enum_values(NetworkProfile)
    if value not in allowed:
        return [
            _issue(
                "invalid_network_profile",
                f"KX_NETWORK_PROFILE must be one of {sorted(allowed)}, got {value}.",
                "KX_NETWORK_PROFILE",
            )
        ]
    return []


def validate_exposure_mode(mode: str | ExposureMode) -> list[ValidationIssue]:
    """Validate a canonical exposure mode value."""

    value = _as_value(mode)
    allowed = _enum_values(ExposureMode)
    if value not in allowed:
        return [
            _issue(
                "invalid_exposure_mode",
                f"KX_EXPOSURE_MODE must be one of {sorted(allowed)}, got {value}.",
                "KX_EXPOSURE_MODE",
            )
        ]
    return []


def validate_public_mode(env: Mapping[str, str]) -> list[ValidationIssue]:
    """Validate public exposure rules.

    Public mode is never the default. If KX_PUBLIC_MODE_ENABLED=true, then
    KX_PUBLIC_MODE_EXPIRES_AT is mandatory unless the approved public_vps
    profile is explicitly selected.
    """

    issues: list[ValidationIssue] = []
    enabled = env.get("KX_PUBLIC_MODE_ENABLED", "false").lower() == "true"
    expires_at = env.get("KX_PUBLIC_MODE_EXPIRES_AT", "")
    profile = env.get("KX_NETWORK_PROFILE", DEFAULT_NETWORK_PROFILE.value)
    exposure = env.get("KX_EXPOSURE_MODE", DEFAULT_EXPOSURE_MODE.value)

    issues.extend(validate_network_profile(profile))
    issues.extend(validate_exposure_mode(exposure))

    if not enabled and exposure in {"temporary_tunnel", "public"}:
        issues.append(
            _issue(
                "public_exposure_without_flag",
                "Public exposure requires KX_PUBLIC_MODE_ENABLED=true.",
                "KX_PUBLIC_MODE_ENABLED",
            )
        )

    if enabled and exposure == ExposureMode.TEMPORARY_TUNNEL.value and not expires_at:
        issues.append(
            _issue(
                "public_mode_expiration_required",
                "Temporary public mode requires KX_PUBLIC_MODE_EXPIRES_AT.",
                "KX_PUBLIC_MODE_EXPIRES_AT",
            )
        )

    if enabled and exposure == ExposureMode.PUBLIC.value and profile != NetworkProfile.PUBLIC_VPS.value:
        issues.append(
            _issue(
                "public_mode_requires_public_vps",
                "Permanent public exposure requires KX_NETWORK_PROFILE=public_vps.",
                "KX_NETWORK_PROFILE",
            )
        )

    return issues


def validate_kx_env(env: Mapping[str, str]) -> list[ValidationIssue]:
    """Validate canonical KX_* environment variables."""

    issues: list[ValidationIssue] = []
    issues.extend(require_keys(env, KX_ENV_DEFAULTS.keys(), context="Konnaxion environment"))

    for key in env:
        if key.startswith("KX_"):
            continue
        if key in _REQUIRED_ENV_KEYS:
            continue
        # Non-KX variables are allowed for Django/Postgres/Redis/Frontend only when known.
        issues.append(
            _issue(
                "unknown_env_key",
                f"Unknown environment key: {key}",
                key,
                blocking=False,
            )
        )

    instance_id = env.get("KX_INSTANCE_ID")
    if instance_id:
        issues.extend(validate_identifier(instance_id, field="KX_INSTANCE_ID"))

    capsule_id = env.get("KX_CAPSULE_ID")
    if capsule_id:
        issues.extend(validate_capsule_id(capsule_id))

    capsule_version = env.get("KX_CAPSULE_VERSION")
    if capsule_version:
        issues.extend(validate_capsule_version(capsule_version))

    app_version = env.get("KX_APP_VERSION")
    if app_version:
        issues.extend(validate_app_version(app_version))

    param_version = env.get("KX_PARAM_VERSION")
    if param_version:
        issues.extend(validate_param_version(param_version))

    issues.extend(validate_public_mode(env))
    return issues


def validate_generated_secret(value: str, *, field: str) -> list[ValidationIssue]:
    """Reject empty, placeholder, or obviously default secrets."""

    normalized = value.strip().lower()
    if not normalized:
        return [_issue("empty_secret", f"{field} must not be empty.", field)]
    if len(value) < 32:
        return [_issue("weak_secret", f"{field} must be at least 32 characters.", field)]
    if normalized in _SECRET_MARKERS or any(marker in normalized for marker in _SECRET_MARKERS):
        return [_issue("placeholder_secret", f"{field} must not be a placeholder/default value.", field)]
    return []


def validate_no_real_secrets_in_template(env_template: Mapping[str, str]) -> list[ValidationIssue]:
    """Validate that an env template contains placeholders, not real secrets."""

    issues: list[ValidationIssue] = []
    for key, value in env_template.items():
        upper_key = key.upper()
        if not any(token in upper_key for token in ("SECRET", "PASSWORD", "TOKEN", "KEY", "DATABASE_URL")):
            continue

        text = str(value).strip()
        if text.startswith("<") and text.endswith(">"):
            continue
        if re.fullmatch(r"\$\{[A-Za-z_][A-Za-z0-9_]*\}", text):
            continue
        if text in {"", "${GENERATED_ON_INSTALL}", "<GENERATED_ON_INSTALL>"}:
            continue

        issues.append(
            _issue(
                "real_secret_in_template",
                f"{key} appears to contain a concrete secret; templates must use placeholders.",
                key,
            )
        )
    return issues


def validate_service_name(service_name: str) -> list[ValidationIssue]:
    """Validate a canonical Docker Compose service name."""

    if service_name in _FORBIDDEN_SERVICE_ALIASES:
        return [
            _issue(
                "forbidden_service_alias",
                f"Use canonical service name instead of forbidden alias: {service_name}",
                service_name,
            )
        ]

    if service_name not in CANONICAL_DOCKER_SERVICES:
        return [
            _issue(
                "unknown_service_name",
                f"Unknown service name {service_name}; allowed: {sorted(CANONICAL_DOCKER_SERVICES)}.",
                service_name,
            )
        ]

    return []


def validate_service_names(service_names: Iterable[str]) -> list[ValidationIssue]:
    """Validate multiple Docker Compose service names."""

    issues: list[ValidationIssue] = []
    for service_name in sorted(set(service_names)):
        issues.extend(validate_service_name(service_name))
    return issues


def _parse_port_entry(port_entry: Any) -> int | None:
    """Extract the container/host port from common Compose port formats."""

    if isinstance(port_entry, int):
        return port_entry

    if isinstance(port_entry, str):
        text = port_entry.strip()
        if not text:
            return None
        # Examples: "80:80", "127.0.0.1:5432:5432", "443"
        last = text.rsplit(":", maxsplit=1)[-1]
        try:
            return int(last.split("/")[0])
        except ValueError:
            return None

    if isinstance(port_entry, Mapping):
        for key in ("published", "target"):
            value = port_entry.get(key)
            if value is None:
                continue
            try:
                return int(value)
            except (TypeError, ValueError):
                continue

    return None


def validate_public_ports(port_entries: Iterable[Any]) -> list[ValidationIssue]:
    """Reject public exposure of internal-only ports."""

    issues: list[ValidationIssue] = []
    for port_entry in port_entries:
        port = _parse_port_entry(port_entry)
        if port is None:
            issues.append(
                _issue(
                    "unparseable_port",
                    f"Could not parse Compose port entry: {port_entry!r}",
                    "ports",
                )
            )
            continue

        if port in FORBIDDEN_PUBLIC_PORTS:
            issues.append(
                _issue(
                    "forbidden_public_port",
                    f"Port {port} must never be publicly exposed.",
                    "ports",
                )
            )

    return issues


def validate_compose_dict(compose: Mapping[str, Any]) -> list[ValidationIssue]:
    """Validate the safety-critical parts of a Docker Compose mapping."""

    issues: list[ValidationIssue] = []
    services = compose.get("services")

    if not isinstance(services, Mapping):
        return [_issue("missing_services", "Compose file must contain a services mapping.", "services")]

    issues.extend(validate_service_names(str(name) for name in services.keys()))

    for service_name, service_def in services.items():
        if not isinstance(service_def, Mapping):
            issues.append(_issue("invalid_service_def", f"{service_name} must be a mapping.", str(service_name)))
            continue

        if service_def.get("privileged") is True:
            issues.append(
                _issue(
                    "privileged_container",
                    f"{service_name} must not use privileged: true.",
                    str(service_name),
                )
            )

        if service_def.get("network_mode") == "host":
            issues.append(
                _issue(
                    "host_network",
                    f"{service_name} must not use network_mode: host.",
                    str(service_name),
                )
            )

        volumes = service_def.get("volumes", [])
        if isinstance(volumes, Sequence) and not isinstance(volumes, (str, bytes)):
            for volume in volumes:
                if "/var/run/docker.sock" in str(volume):
                    issues.append(
                        _issue(
                            "docker_socket_mount",
                            f"{service_name} must not mount /var/run/docker.sock.",
                            str(service_name),
                        )
                    )

        ports = service_def.get("ports", [])
        if ports:
            issues.extend(validate_public_ports(ports))

    return issues


def validate_path_under_root(path: str | Path, root: str | Path = KX_ROOT) -> list[ValidationIssue]:
    """Validate that a path stays under the canonical Konnaxion root."""

    candidate = Path(path)
    root_path = Path(root)

    try:
        candidate.resolve().relative_to(root_path.resolve())
    except ValueError:
        return [
            _issue(
                "path_outside_kx_root",
                f"Path must be under {root_path}: {candidate}",
                "path",
            )
        ]

    return []


def validate_backup_root(path: str | Path) -> list[ValidationIssue]:
    """Validate that backup storage is rooted in the canonical backup directory."""

    return validate_path_under_root(path, KX_BACKUPS_ROOT)


def validate_instance_state(state: str | InstanceState) -> list[ValidationIssue]:
    """Validate a canonical instance state."""

    value = _as_value(state)
    allowed = _enum_values(InstanceState)
    if value not in allowed:
        return [_issue("invalid_instance_state", f"Invalid instance state: {value}", "state")]
    return []


def validate_backup_status(status: str | BackupStatus) -> list[ValidationIssue]:
    """Validate a canonical backup status."""

    value = _as_value(status)
    allowed = _enum_values(BackupStatus)
    if value not in allowed:
        return [_issue("invalid_backup_status", f"Invalid backup status: {value}", "backup_status")]
    return []


def validate_restore_status(status: str | RestoreStatus) -> list[ValidationIssue]:
    """Validate a canonical restore status."""

    value = _as_value(status)
    allowed = _enum_values(RestoreStatus)
    if value not in allowed:
        return [_issue("invalid_restore_status", f"Invalid restore status: {value}", "restore_status")]
    return []


def validate_rollback_status(status: str | RollbackStatus) -> list[ValidationIssue]:
    """Validate a canonical rollback status."""

    value = _as_value(status)
    allowed = _enum_values(RollbackStatus)
    if value not in allowed:
        return [_issue("invalid_rollback_status", f"Invalid rollback status: {value}", "rollback_status")]
    return []


def validate_security_gate_status(status: str | SecurityGateStatus) -> list[ValidationIssue]:
    """Validate a canonical Security Gate status."""

    value = _as_value(status)
    allowed = _enum_values(SecurityGateStatus)
    if value not in allowed:
        return [_issue("invalid_security_status", f"Invalid Security Gate status: {value}", "security_status")]
    return []


def validate_security_gate_check(check: str | SecurityGateCheck) -> list[ValidationIssue]:
    """Validate a canonical Security Gate check name."""

    value = _as_value(check)
    allowed = _enum_values(SecurityGateCheck)
    if value not in allowed:
        return [_issue("invalid_security_check", f"Invalid Security Gate check: {value}", "security_check")]
    return []


def validate_security_gate_results(results: Mapping[str, str]) -> list[ValidationIssue]:
    """Validate Security Gate result statuses and blocking-check behavior."""

    issues: list[ValidationIssue] = []

    for check, status in results.items():
        issues.extend(validate_security_gate_check(check))
        issues.extend(validate_security_gate_status(status))

    for check in BLOCKING_SECURITY_CHECKS:
        check_value = _as_value(check)
        status = results.get(check_value)
        if status is None:
            issues.append(
                _issue(
                    "missing_blocking_security_check",
                    f"Missing blocking Security Gate check: {check_value}",
                    check_value,
                )
            )
            continue

        if status == SecurityGateStatus.FAIL_BLOCKING.value:
            issues.append(
                _issue(
                    "blocking_security_failure",
                    f"Blocking Security Gate check failed: {check_value}",
                    check_value,
                )
            )

    return issues


def validate_manifest(manifest: Mapping[str, Any]) -> list[ValidationIssue]:
    """Validate a capsule manifest across supported Konnaxion contracts.

    ``kx-capsule-manifest/v1`` is the manifest emitted by the current Builder.
    Older capsules used the legacy ``services`` / ``network_profiles`` /
    ``security`` shape.  The Agent must accept both contracts so a capsule that
    passes Builder verification is not rejected at import solely because the
    shared validator is older than the Builder.
    """

    issues: list[ValidationIssue] = []
    schema_version = str(manifest.get("schema_version") or "").strip()

    base_required = frozenset(
        {
            "schema_version",
            "capsule_id",
            "capsule_version",
            "app_name",
            "app_version",
            "channel",
            "created_at",
        }
    )
    issues.extend(require_keys(manifest, base_required, context="manifest"))

    capsule_id = manifest.get("capsule_id")
    if capsule_id is not None:
        issues.extend(validate_capsule_id(str(capsule_id)))

    capsule_version = manifest.get("capsule_version")
    if capsule_version is not None:
        issues.extend(validate_capsule_version(str(capsule_version)))

    app_version = manifest.get("app_version")
    if app_version is not None:
        issues.extend(validate_app_version(str(app_version)))

    param_version = manifest.get("param_version")
    if param_version is not None:
        issues.extend(validate_param_version(str(param_version)))

    if schema_version == "kx-capsule-manifest/v1":
        # Current Builder contract.
        issues.extend(
            require_keys(
                manifest,
                frozenset({"profile", "profiles", "package", "runtime"}),
                context="manifest",
            )
        )

        default_profile = manifest.get("profile")
        if default_profile is not None:
            issues.extend(validate_network_profile(str(default_profile)))

        profiles = manifest.get("profiles")
        if isinstance(profiles, Sequence) and not isinstance(profiles, (str, bytes)):
            for profile in profiles:
                issues.extend(validate_network_profile(str(profile)))
        elif profiles is not None:
            issues.append(
                _issue(
                    "invalid_manifest_profiles",
                    "manifest.profiles must be a list of canonical profile values.",
                    "profiles",
                )
            )

        runtime = manifest.get("runtime")
        if isinstance(runtime, Mapping):
            images = runtime.get("images")
            if isinstance(images, Sequence) and not isinstance(images, (str, bytes)):
                service_names: list[str] = []
                for item in images:
                    if isinstance(item, Mapping) and item.get("service") is not None:
                        service_names.append(str(item.get("service")))
                issues.extend(validate_service_names(service_names))
            elif images is not None:
                issues.append(
                    _issue(
                        "invalid_manifest_runtime_images",
                        "manifest.runtime.images must be a list.",
                        "runtime.images",
                    )
                )
        elif runtime is not None:
            issues.append(
                _issue(
                    "invalid_manifest_runtime",
                    "manifest.runtime must be a mapping.",
                    "runtime",
                )
            )

        package = manifest.get("package")
        if package is not None and not isinstance(package, Mapping):
            issues.append(
                _issue(
                    "invalid_manifest_package",
                    "manifest.package must be a mapping.",
                    "package",
                )
            )

        return issues

    # Legacy contract retained for existing capsules and tests.
    issues.extend(
        require_keys(
            manifest,
            frozenset({"services", "network_profiles", "security"}),
            context="manifest",
        )
    )

    services = manifest.get("services")
    if isinstance(services, Mapping):
        issues.extend(validate_service_names(str(name) for name in services.keys()))
    elif services is not None:
        issues.append(
            _issue(
                "invalid_manifest_services",
                "manifest.services must be a mapping.",
                "services",
            )
        )

    profiles = manifest.get("network_profiles")
    if isinstance(profiles, Sequence) and not isinstance(profiles, (str, bytes)):
        for profile in profiles:
            if isinstance(profile, Mapping):
                profile_id = profile.get("id")
                if profile_id is not None:
                    issues.extend(validate_network_profile(str(profile_id)))
            else:
                issues.extend(validate_network_profile(str(profile)))
    elif profiles is not None:
        issues.append(
            _issue(
                "invalid_manifest_profiles",
                "manifest.network_profiles must be a list of canonical profile values.",
                "network_profiles",
            )
        )

    return issues


def assert_valid(issues: Sequence[ValidationIssue]) -> None:
    """Convenience alias for callers that prefer assert-style validation."""

    raise_if_issues(issues)


__all__ = [
    "ValidationIssue",
    "ValidationFailed",
    "assert_valid",
    "raise_if_issues",
    "require_keys",
    "validate_identifier",
    "validate_capsule_filename",
    "validate_capsule_id",
    "validate_capsule_version",
    "validate_app_version",
    "validate_param_version",
    "validate_network_profile",
    "validate_exposure_mode",
    "validate_public_mode",
    "validate_kx_env",
    "validate_generated_secret",
    "validate_no_real_secrets_in_template",
    "validate_service_name",
    "validate_service_names",
    "validate_public_ports",
    "validate_compose_dict",
    "validate_path_under_root",
    "validate_backup_root",
    "validate_instance_state",
    "validate_backup_status",
    "validate_restore_status",
    "validate_rollback_status",
    "validate_security_gate_status",
    "validate_security_gate_check",
    "validate_security_gate_results",
    "validate_manifest",
]
