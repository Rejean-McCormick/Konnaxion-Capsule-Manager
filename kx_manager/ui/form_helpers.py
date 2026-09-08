# kx_manager/ui/form_helpers.py

"""Shared form parsing helpers for the Konnaxion Capsule Manager GUI.

This module owns reusable validation/parsing helpers only. It must not execute
actions, call Docker, run shell commands, or mutate host state.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from kx_manager.services.targets import TargetMode

from kx_manager.defaults import DEFAULT_NETWORK_PROFILE
from kx_manager.ui.form_constants import (
    CAPSULE_EXTENSION,
    DEFAULT_CAPSULE_ID,
    DEFAULT_CAPSULE_OUTPUT_DIR,
    DEFAULT_CAPSULE_VERSION,
    DEFAULT_INSTANCE_ID,
    DockerService,
    ExposureMode,
    FALSE_VALUES,
    NetworkProfile,
    SAFE_ID_CHARS,
    TRUE_VALUES,
)
from kx_manager.ui.form_errors import FormValidationError


def _raw(data: Mapping[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in data:
            value = data[key]
            if isinstance(value, (list, tuple)):
                return value[-1] if value else default
            return value

    return default


def _text(
    data: Mapping[str, Any],
    *keys: str,
    default: str | None = None,
    required: bool = False,
    field: str | None = None,
) -> str | None:
    value = _raw(data, *keys, default=default)
    output_field = field or (keys[0] if keys else "value")

    if value is None:
        if required:
            raise FormValidationError(f"{output_field} is required.", field=output_field)
        return default

    value = str(value).strip()

    if not value:
        if required:
            raise FormValidationError(f"{output_field} is required.", field=output_field)
        return default

    return value


def _bool(data: Mapping[str, Any], key: str, *, default: bool = False) -> bool:
    value = _raw(data, key, default=None)

    if value is None:
        return default

    if isinstance(value, bool):
        return value

    normalized = str(value).strip().lower()

    if normalized in TRUE_VALUES:
        return True

    if normalized in FALSE_VALUES:
        return False

    raise FormValidationError(f"{key} must be a boolean value.", field=key)


def _int(
    data: Mapping[str, Any],
    key: str,
    *,
    default: int,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    value = _raw(data, key, default=default)

    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise FormValidationError(f"{key} must be an integer.", field=key) from exc

    if minimum is not None and parsed < minimum:
        raise FormValidationError(f"{key} must be at least {minimum}.", field=key)

    if maximum is not None and parsed > maximum:
        raise FormValidationError(f"{key} must be at most {maximum}.", field=key)

    return parsed


def _path(
    data: Mapping[str, Any],
    *keys: str,
    default: str | None = None,
    required: bool = False,
    must_exist: bool = False,
    must_be_file: bool = False,
    must_be_dir: bool = False,
    field: str | None = None,
) -> Path | None:
    output_field = field or (keys[0] if keys else "path")
    value = _text(data, *keys, default=default, required=required, field=output_field)

    if value is None:
        return None

    path = Path(value).expanduser()

    if must_exist and not path.exists():
        raise FormValidationError(
            f"{output_field} does not exist: {path}",
            field=output_field,
        )

    if must_be_file and path.exists() and not path.is_file():
        raise FormValidationError(
            f"{output_field} must be a file: {path}",
            field=output_field,
        )

    if must_be_dir and path.exists() and not path.is_dir():
        raise FormValidationError(
            f"{output_field} must be a directory: {path}",
            field=output_field,
        )

    return path


def _existing_dir(
    data: Mapping[str, Any],
    *keys: str,
    default: str | None = None,
    field: str | None = None,
) -> Path:
    path = _path(
        data,
        *keys,
        default=default,
        required=True,
        must_exist=True,
        must_be_dir=True,
        field=field,
    )
    assert path is not None
    return path


def _creatable_dir(
    data: Mapping[str, Any],
    *keys: str,
    default: str | None = None,
    field: str | None = None,
) -> Path:
    output_field = field or (keys[0] if keys else "directory")
    path = _path(
        data,
        *keys,
        default=default,
        required=True,
        must_exist=False,
        must_be_dir=True,
        field=output_field,
    )
    assert path is not None

    if path.exists():
        return path

    parent = path
    while not parent.exists() and parent.parent != parent:
        parent = parent.parent

    if not parent.exists() or not parent.is_dir():
        raise FormValidationError(
            f"{output_field} is not creatable: {path}",
            field=output_field,
        )

    return path


def _capsule_file(
    data: Mapping[str, Any],
    *keys: str,
    default: str | None = None,
    required: bool = True,
    must_exist: bool = False,
    field: str = "capsule_file",
) -> Path | None:
    path = _path(
        data,
        *keys,
        default=default,
        required=required,
        must_exist=must_exist,
        must_be_file=must_exist,
        field=field,
    )

    if path is None:
        return None

    if path.suffix.lower() != CAPSULE_EXTENSION:
        raise FormValidationError(
            f"{field} must end with {CAPSULE_EXTENSION}.",
            field=field,
        )

    return path


def _capsule_output_dir(data: Mapping[str, Any]) -> Path:
    return _creatable_dir(
        data,
        "capsule_output_dir",
        "output_dir",
        default=DEFAULT_CAPSULE_OUTPUT_DIR,
        field="capsule_output_dir",
    )


def _safe_identifier(value: str, field: str) -> str:
    value = value.strip()

    if not value:
        raise FormValidationError(f"{field} is required.", field=field)

    if value in {".", ".."}:
        raise FormValidationError(f"{field} is not safe.", field=field)

    if "/" in value or "\\" in value:
        raise FormValidationError(
            f"{field} must not contain path separators.",
            field=field,
        )

    if any(char not in SAFE_ID_CHARS for char in value):
        raise FormValidationError(
            f"{field} may only contain letters, numbers, dots, underscores, and hyphens.",
            field=field,
        )

    if len(value) > 128:
        raise FormValidationError(f"{field} is too long.", field=field)

    return value


def _instance_id(
    data: Mapping[str, Any],
    *,
    default: str = DEFAULT_INSTANCE_ID,
) -> str:
    value = _text(
        data,
        "instance_id",
        default=default,
        required=True,
        field="instance_id",
    )
    assert value is not None
    return _safe_identifier(value, "instance_id")


def _capsule_id(
    data: Mapping[str, Any],
    *,
    default: str = DEFAULT_CAPSULE_ID,
) -> str:
    value = _text(
        data,
        "capsule_id",
        default=default,
        required=True,
        field="capsule_id",
    )
    assert value is not None
    return _safe_identifier(value, "capsule_id")


def _capsule_version(data: Mapping[str, Any]) -> str:
    value = _text(
        data,
        "capsule_version",
        "version",
        default=DEFAULT_CAPSULE_VERSION,
        required=True,
        field="capsule_version",
    )
    assert value is not None
    return value


def _backup_id(data: Mapping[str, Any], *, required: bool = True) -> str | None:
    value = _text(data, "backup_id", required=required, field="backup_id")

    if value is None:
        return None

    return _safe_identifier(value, "backup_id")


def _enum_values(enum_type: type[Any]) -> list[str]:
    return [str(item.value) for item in enum_type]


def _coerce_enum(enum_type: type[Any], value: Any, field: str) -> Any:
    if isinstance(value, enum_type):
        return value

    raw = str(value).strip()

    try:
        return enum_type(raw)
    except ValueError as exc:
        allowed = ", ".join(_enum_values(enum_type))
        raise FormValidationError(
            f"{field} must be one of: {allowed}.",
            field=field,
        ) from exc


def _network_profile(
    data: Mapping[str, Any],
    *,
    default: str = DEFAULT_NETWORK_PROFILE,
) -> Any:
    value = _raw(data, "network_profile", "profile", default=default)
    return _coerce_enum(NetworkProfile, value, "network_profile")


def _exposure_mode(
    data: Mapping[str, Any],
    *,
    default: str = "private",
) -> Any:
    value = _raw(data, "exposure_mode", default=default)
    return _coerce_enum(ExposureMode, value, "exposure_mode")


def _docker_service(data: Mapping[str, Any], *, required: bool = False) -> Any | None:
    value = _text(data, "service", required=required, field="service")

    if value is None:
        return None

    return _coerce_enum(DockerService, value, "service")


def _target_mode(
    data: Mapping[str, Any],
    *,
    default: str = "intranet",
) -> TargetMode:
    value = _raw(data, "target_mode", default=default)
    return _coerce_enum(TargetMode, value, "target_mode")


def _iso_datetime(
    data: Mapping[str, Any],
    key: str,
    *,
    required: bool = False,
) -> str | None:
    value = _text(data, key, "expires_at", required=required, field=key)

    if value is None:
        return None

    normalized = value.replace("Z", "+00:00")

    try:
        datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise FormValidationError(
            f"{key} must be an ISO-8601 datetime.",
            field=key,
        ) from exc

    return value


def _host(
    data: Mapping[str, Any],
    *keys: str,
    required: bool = False,
    field: str = "host",
) -> str | None:
    value = _text(data, *keys, required=required, field=field)

    if value is None:
        return None

    if any(char in value for char in "\r\n\t "):
        raise FormValidationError(
            f"{field} must be a host, IP, or domain without whitespace.",
            field=field,
        )

    return value


def _absolute_posix_path(value: str, field: str) -> str:
    value = value.strip()

    if not value.startswith("/"):
        raise FormValidationError(
            f"{field} must be an absolute POSIX path.",
            field=field,
        )

    if "\\" in value:
        raise FormValidationError(
            f"{field} must use POSIX separators.",
            field=field,
        )

    return str(PurePosixPath(value))


def _remote_capsule_dir_under_root(
    remote_kx_root: str,
    remote_capsule_dir: str,
) -> None:
    root = PurePosixPath(remote_kx_root)
    capsule_dir = PurePosixPath(remote_capsule_dir)

    if capsule_dir == root:
        return

    try:
        capsule_dir.relative_to(root)
    except ValueError as exc:
        raise FormValidationError(
            "remote_capsule_dir must be under remote_kx_root.",
            field="remote_capsule_dir",
        ) from exc


def _computed_capsule_file(output_dir: Path, capsule_id: str) -> Path:
    return output_dir / f"{capsule_id}{CAPSULE_EXTENSION}"


def _payload(obj: Any) -> dict[str, Any]:
    data = asdict(obj)
    output: dict[str, Any] = {}

    for key, value in data.items():
        if isinstance(value, Path):
            output[key] = str(value)
        elif hasattr(value, "value"):
            output[key] = value.value
        else:
            output[key] = value

    return output


def _reject_droplet_fields(data: Mapping[str, Any], target_mode: str) -> None:
    droplet_fields = (
        "droplet_name",
        "droplet_host",
        "droplet_user",
        "ssh_key_path",
        "droplet_ssh_key",
        "remote_kx_root",
        "remote_capsule_dir",
        "remote_agent_url",
    )

    present = [
        field
        for field in droplet_fields
        if _text(data, field, required=False) is not None
    ]

    if present:
        raise FormValidationError(
            f"{target_mode} target must not include droplet fields: {', '.join(present)}.",
            field=present[0],
        )


def _validate_profile_exposure(
    *,
    network_profile: Any,
    exposure_mode: Any,
    public_mode_expires_at: str | None = None,
    confirmed: bool = False,
    host: str | None = None,
    domain: str | None = None,
) -> None:
    local_only = _coerce_enum(NetworkProfile, "local_only", "network_profile")
    intranet_private = _coerce_enum(NetworkProfile, "intranet_private", "network_profile")
    private_tunnel = _coerce_enum(NetworkProfile, "private_tunnel", "network_profile")
    public_temporary = _coerce_enum(NetworkProfile, "public_temporary", "network_profile")
    public_vps = _coerce_enum(NetworkProfile, "public_vps", "network_profile")
    offline = _coerce_enum(NetworkProfile, "offline", "network_profile")

    private = _coerce_enum(ExposureMode, "private", "exposure_mode")
    lan = _coerce_enum(ExposureMode, "lan", "exposure_mode")
    vpn = _coerce_enum(ExposureMode, "vpn", "exposure_mode")
    temporary_tunnel = _coerce_enum(
        ExposureMode,
        "temporary_tunnel",
        "exposure_mode",
    )
    public = _coerce_enum(ExposureMode, "public", "exposure_mode")

    allowed = {
        local_only: {private},
        intranet_private: {private, lan},
        private_tunnel: {vpn},
        public_temporary: {temporary_tunnel},
        public_vps: {public},
        offline: {private},
    }

    if exposure_mode not in allowed[network_profile]:
        allowed_values = ", ".join(
            sorted(item.value for item in allowed[network_profile])
        )
        raise FormValidationError(
            f"exposure_mode must be one of {allowed_values} "
            f"for network_profile={network_profile.value}.",
            field="exposure_mode",
        )

    if network_profile == public_temporary:
        if not public_mode_expires_at:
            raise FormValidationError(
                "public_temporary requires public_mode_expires_at.",
                field="public_mode_expires_at",
            )

        if not confirmed:
            raise FormValidationError(
                "public_temporary requires explicit confirmation.",
                field="confirmed",
            )

    if network_profile == public_vps:
        if not confirmed:
            raise FormValidationError(
                "public_vps requires explicit confirmation.",
                field="confirmed",
            )

        if not host and not domain:
            raise FormValidationError(
                "public_vps requires host or domain.",
                field="host",
            )


def normalize_form_data(data: Mapping[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}

    for key, value in data.items():
        if isinstance(value, (list, tuple)):
            value = value[-1] if value else ""

        if isinstance(value, str):
            normalized[str(key)] = value.strip()
        else:
            normalized[str(key)] = value

    return normalized


__all__ = [
    "_absolute_posix_path",
    "_backup_id",
    "_bool",
    "_capsule_file",
    "_capsule_id",
    "_capsule_output_dir",
    "_capsule_version",
    "_coerce_enum",
    "_computed_capsule_file",
    "_creatable_dir",
    "_docker_service",
    "_enum_values",
    "_existing_dir",
    "_exposure_mode",
    "_host",
    "_instance_id",
    "_int",
    "_iso_datetime",
    "_network_profile",
    "_path",
    "_payload",
    "_raw",
    "_reject_droplet_fields",
    "_remote_capsule_dir_under_root",
    "_safe_identifier",
    "_target_mode",
    "_text",
    "_validate_profile_exposure",
    "normalize_form_data",
]