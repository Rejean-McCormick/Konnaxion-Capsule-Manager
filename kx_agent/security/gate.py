"""
Konnaxion Agent Security Gate.

The Security Gate is the mandatory pre-start validation layer for every
Konnaxion Instance. It evaluates capsule integrity, manifest shape, generated
secrets, exposed ports, service policy, Docker runtime hazards, admin exposure,
and backup readiness.

This module is intentionally deterministic and side-effect-light. It consumes a
SecurityGateContext prepared by higher-level Agent components and returns a
SecurityGateResult that the lifecycle layer can use to allow or block startup.

Compatibility note:
Older Agent call paths sometimes pass manifest={} and env={} to
context_from_compose(). To keep the gate strict while avoiding false negatives,
this module hydrates missing manifest/env values from canonical runtime files
when available:

- /opt/konnaxion/shared/capsules/<capsule_id>/manifest.yaml
- /opt/konnaxion/shared/capsules/<capsule_id>/manifest.json
- /opt/konnaxion/instances/<instance_id>/env/*.env
- /opt/konnaxion/instances/<instance_id>/state/*.env

If those files are not present, checks still fail normally.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from kx_shared.konnaxion_constants import (
    BLOCKING_SECURITY_CHECKS,
    CANONICAL_DOCKER_SERVICES,
    FORBIDDEN_PUBLIC_PORTS,
    INTERNAL_ONLY_PORTS,
    KX_BACKUPS_ROOT,
    KX_ROOT,
    ROUTES,
    DockerService,
    SecurityGateCheck,
    SecurityGateStatus,
)


# ---------------------------------------------------------------------------
# Security Gate constants
# ---------------------------------------------------------------------------

REQUIRED_MANIFEST_FIELDS = frozenset(
    {
        "schema_version",
        "capsule_id",
        "capsule_version",
        "app_name",
        "app_version",
        "channel",
    }
)

REQUIRED_SECRET_KEYS = frozenset(
    {
        "DJANGO_SECRET_KEY",
        "POSTGRES_PASSWORD",
        "DATABASE_URL",
    }
)

SECRET_PLACEHOLDER_VALUES = frozenset(
    {
        "",
        "changeme",
        "change-me",
        "change_me",
        "password",
        "postgres",
        "secret",
        "default",
        "<generated_on_install>",
        "<generated-on-install>",
        "<generated-from-profile>",
        "<postgres_password>",
        "<django_secret_key>",
        "example",
        "test",
        "admin",
        "none",
        "null",
    }
)

SECRET_PLACEHOLDER_SUBSTRINGS = frozenset(
    {
        "changeme",
        "change-me",
        "placeholder",
        "generated",
        "example",
        "dummy",
        "default",
    }
)

FORBIDDEN_MOUNT_PATHS = frozenset(
    {
        "/var/run/docker.sock",
        "/run/docker.sock",
        "\\\\.\\pipe\\docker_engine",
    }
)

FORBIDDEN_PRIVILEGED_VALUES = frozenset(
    {
        True,
        "true",
        "True",
        "TRUE",
        1,
        "1",
        "yes",
        "YES",
        "on",
        "ON",
    }
)

FORBIDDEN_HOST_NETWORK_VALUES = frozenset(
    {
        "host",
        "HOST",
    }
)

DEFAULT_ALLOWED_IMAGES = frozenset(
    {
        "traefik",
        "frontend-next",
        "django-api",
        "postgres",
        "redis",
        "celeryworker",
        "celerybeat",
        "flower",
        "media-nginx",
        "kx-agent",
        "nginx",
    }
)


# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------

def _utc_now() -> datetime:
    return datetime.now(UTC)


def _enum_value(value: object) -> str:
    return str(getattr(value, "value", value))


def _runtime_root() -> Path:
    env_root = os.getenv("KX_ROOT", "").strip()
    if env_root:
        return Path(env_root)

    backup_root = Path(KX_BACKUPS_ROOT)
    if backup_root.name == "backups":
        return backup_root.parent

    return Path(KX_ROOT)


def _instances_root() -> Path:
    return _runtime_root() / "instances"


def _shared_capsules_root() -> Path:
    return _runtime_root() / "shared" / "capsules"


def _read_text(path: Path) -> str | None:
    try:
        if path.exists() and path.is_file():
            return path.read_text(encoding="utf-8")
    except OSError:
        return None
    return None


def _clean_scalar(value: str) -> str:
    cleaned = value.strip()

    if cleaned in {"", "null", "Null", "NULL", "~"}:
        return ""

    if (
        len(cleaned) >= 2
        and cleaned[0] == cleaned[-1]
        and cleaned[0] in {"'", '"'}
    ):
        return cleaned[1:-1]

    return cleaned


def _parse_simple_top_level_yaml(text: str) -> dict[str, Any]:
    """
    Minimal fallback YAML parser.

    It intentionally extracts only simple top-level `key: value` scalars.
    That is enough for required manifest fields and avoids depending on PyYAML.
    """

    result: dict[str, Any] = {}

    for raw_line in text.splitlines():
        if not raw_line.strip():
            continue

        if raw_line.lstrip().startswith("#"):
            continue

        # Only top-level scalars.
        if raw_line[:1].isspace():
            continue

        if ":" not in raw_line:
            continue

        key, value = raw_line.split(":", 1)
        key = key.strip()
        value = value.strip()

        if not key:
            continue

        # Ignore nested map/list starts in this fallback.
        if value == "":
            continue

        result[key] = _clean_scalar(value)

    return result


def _parse_mapping_text(text: str) -> dict[str, Any]:
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = None

    if isinstance(parsed, Mapping):
        return dict(parsed)

    try:
        import yaml  # type: ignore
    except Exception:
        yaml = None

    if yaml is not None:
        try:
            parsed_yaml = yaml.safe_load(text)
        except Exception:
            parsed_yaml = None

        if isinstance(parsed_yaml, Mapping):
            return dict(parsed_yaml)

    return _parse_simple_top_level_yaml(text)


def _parse_env_text(text: str) -> dict[str, str]:
    env: dict[str, str] = {}

    for raw_line in text.splitlines():
        line = raw_line.strip()

        if not line or line.startswith("#"):
            continue

        if line.startswith("export "):
            line = line.removeprefix("export ").strip()

        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = _clean_scalar(value)

        if key:
            env[key] = value

    return env


def _load_env_file(path: Path) -> dict[str, str]:
    text = _read_text(path)
    if text is None:
        return {}
    return _parse_env_text(text)


def _load_instance_env_files(instance_id: str) -> dict[str, str]:
    root = _instances_root() / instance_id
    candidates: list[Path] = []

    for directory_name in ("env", "state"):
        directory = root / directory_name
        try:
            if directory.exists() and directory.is_dir():
                candidates.extend(sorted(directory.glob("*.env")))
        except OSError:
            continue

    preferred = root / "env" / "runtime.env"
    if preferred in candidates:
        candidates.remove(preferred)
        candidates.insert(0, preferred)

    env: dict[str, str] = {}

    for path in candidates:
        env.update(_load_env_file(path))

    return env


def _compose_environment(compose: Mapping[str, Any]) -> dict[str, str]:
    raw_services = compose.get("services", {})
    if not isinstance(raw_services, Mapping):
        return {}

    env: dict[str, str] = {}

    for raw_spec in raw_services.values():
        if not isinstance(raw_spec, Mapping):
            continue

        raw_env = raw_spec.get("environment", {})

        if isinstance(raw_env, Mapping):
            for key, value in raw_env.items():
                if value is not None:
                    env[str(key)] = str(value)

        elif isinstance(raw_env, Sequence) and not isinstance(raw_env, (str, bytes)):
            for item in raw_env:
                text = str(item)
                if "=" not in text:
                    continue
                key, value = text.split("=", 1)
                if key.strip():
                    env[key.strip()] = value.strip()

    return env


def _candidate_capsule_ids_from_manifest(manifest: Mapping[str, Any]) -> tuple[str, ...]:
    raw = str(manifest.get("capsule_id") or "").strip()
    return (raw,) if raw else ()


def _candidate_capsule_ids_from_env(env: Mapping[str, str]) -> tuple[str, ...]:
    raw = str(env.get("KX_CAPSULE_ID") or env.get("CAPSULE_ID") or "").strip()
    return (raw,) if raw else ()


def _candidate_capsule_ids_from_compose(compose: Mapping[str, Any]) -> tuple[str, ...]:
    candidates: list[str] = []

    def visit(value: Any, key_hint: str = "") -> None:
        key_lower = key_hint.lower()

        if isinstance(value, Mapping):
            for key, item in value.items():
                visit(item, str(key))
            return

        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            for item in value:
                visit(item, key_hint)
            return

        text = str(value).strip()
        if not text:
            return

        if key_lower in {"capsule_id", "kx_capsule_id"}:
            candidates.append(text)
            return

        if text.startswith("KX_CAPSULE_ID="):
            candidates.append(text.split("=", 1)[1].strip())
            return

        if text.startswith("CAPSULE_ID="):
            candidates.append(text.split("=", 1)[1].strip())

    visit(compose)

    deduped: list[str] = []
    for item in candidates:
        if item and item not in deduped:
            deduped.append(item)

    return tuple(deduped)


def _manifest_paths_for_capsule_id(capsule_id: str) -> tuple[Path, ...]:
    root = _shared_capsules_root() / capsule_id
    return (
        root / "manifest.yaml",
        root / "manifest.yml",
        root / "manifest.json",
    )


def _all_runtime_manifest_paths() -> tuple[Path, ...]:
    root = _shared_capsules_root()

    try:
        if not root.exists() or not root.is_dir():
            return ()
    except OSError:
        return ()

    paths: list[Path] = []
    for pattern in ("*/manifest.yaml", "*/manifest.yml", "*/manifest.json"):
        try:
            paths.extend(root.glob(pattern))
        except OSError:
            continue

    def mtime(path: Path) -> float:
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0

    return tuple(sorted(paths, key=mtime, reverse=True))


def _load_manifest_path(path: Path) -> dict[str, Any]:
    text = _read_text(path)
    if text is None:
        return {}

    parsed = _parse_mapping_text(text)
    return parsed if isinstance(parsed, dict) else {}


def _manifest_missing_fields(manifest: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(sorted(field for field in REQUIRED_MANIFEST_FIELDS if not manifest.get(field)))


def _hydrate_manifest(
    *,
    instance_id: str,
    compose: Mapping[str, Any] | None,
    manifest: Mapping[str, Any],
    env: Mapping[str, str],
) -> dict[str, Any]:
    """
    Fill missing manifest fields from canonical extracted capsule manifests.

    Explicit caller-provided manifest values take precedence over file values.
    """

    current = dict(manifest or {})
    if not _manifest_missing_fields(current):
        return current

    capsule_ids: list[str] = []
    for source in (
        _candidate_capsule_ids_from_manifest(current),
        _candidate_capsule_ids_from_env(env),
        _candidate_capsule_ids_from_compose(compose or {}),
    ):
        for item in source:
            if item and item not in capsule_ids:
                capsule_ids.append(item)

    candidate_paths: list[Path] = []
    for capsule_id in capsule_ids:
        candidate_paths.extend(_manifest_paths_for_capsule_id(capsule_id))

    candidate_paths.extend(path for path in _all_runtime_manifest_paths() if path not in candidate_paths)

    for path in candidate_paths:
        loaded = _load_manifest_path(path)
        if not loaded:
            continue

        merged = {**loaded, **current}
        merged.setdefault("_manifest_path", str(path))
        merged.setdefault("_manifest_hydrated_for_instance_id", instance_id)

        if not _manifest_missing_fields(merged):
            return merged

        # Even a partial manifest is better than an empty one if no complete
        # candidate exists.
        if len(merged) > len(current):
            current = merged

    return current


def _hydrate_env(
    *,
    instance_id: str,
    compose: Mapping[str, Any] | None,
    env: Mapping[str, str],
) -> dict[str, str]:
    """
    Fill missing env/secrets from canonical instance env files.

    Precedence, lowest to highest:
    - instance env files
    - Compose service environment
    - caller-provided env
    """

    file_env = _load_instance_env_files(instance_id)
    compose_env = _compose_environment(compose or {})

    merged: dict[str, str] = {}
    merged.update(file_env)
    merged.update(compose_env)
    merged.update({str(key): str(value) for key, value in dict(env or {}).items()})

    return merged


def normalize_security_check(value: SecurityGateCheck | str) -> SecurityGateCheck:
    if isinstance(value, SecurityGateCheck):
        return value

    raw = str(value).strip()

    try:
        return SecurityGateCheck(raw)
    except ValueError as exc:
        valid = ", ".join(check.value for check in SecurityGateCheck)
        raise ValueError(f"Unknown Security Gate check: {raw!r}. Valid: {valid}") from exc


def normalize_security_status(value: SecurityGateStatus | str) -> SecurityGateStatus:
    if isinstance(value, SecurityGateStatus):
        return value

    raw = str(value).strip()

    try:
        return SecurityGateStatus(raw)
    except ValueError as exc:
        valid = ", ".join(status.value for status in SecurityGateStatus)
        raise ValueError(f"Unknown Security Gate status: {raw!r}. Valid: {valid}") from exc


def _is_blocking_check(check: SecurityGateCheck | str) -> bool:
    normalized = normalize_security_check(check)
    return normalized in BLOCKING_SECURITY_CHECKS


def _env_bool(env: Mapping[str, str], key: str, default: bool) -> bool:
    value = env.get(key)

    if value is None:
        return default

    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _secret_is_placeholder(value: str | None) -> bool:
    if value is None:
        return True

    normalized = str(value).strip()
    lowered = normalized.lower()

    if lowered in SECRET_PLACEHOLDER_VALUES:
        return True

    return any(fragment in lowered for fragment in SECRET_PLACEHOLDER_SUBSTRINGS)


def _database_url_has_placeholder(value: str | None) -> bool:
    if not value:
        return True

    lowered = value.lower()

    return (
        "<postgres_password>" in lowered
        or "changeme" in lowered
        or "password@" in lowered
        or ":postgres@" in lowered
        or "placeholder" in lowered
    )


def _image_base_name(image: str | None) -> str | None:
    if not image:
        return None

    raw = image.strip()

    if not raw:
        return None

    name_with_tag = raw.rsplit("/", 1)[-1]
    return name_with_tag.split(":", 1)[0]


def _has_forbidden_mount(mounts: Iterable[str]) -> bool:
    for mount in mounts:
        normalized = str(mount).lower()
        if any(forbidden.lower() in normalized for forbidden in FORBIDDEN_MOUNT_PATHS):
            return True

    return False


def _int_or_none(value: Any) -> int | None:
    if value in (None, ""):
        return None

    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Result models expected by tests and lifecycle code
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SecurityCheckResult:
    """Result of one Security Gate check."""

    check: SecurityGateCheck | str
    status: SecurityGateStatus | str
    message: str | None = ""
    blocking: bool | None = None
    details: Mapping[str, Any] = field(default_factory=dict)
    checked_at: datetime | None = None

    def __post_init__(self) -> None:
        normalized_check = normalize_security_check(self.check)
        normalized_status = normalize_security_status(self.status)

        object.__setattr__(self, "check", normalized_check)
        object.__setattr__(self, "status", normalized_status)

        if self.blocking is None:
            object.__setattr__(self, "blocking", normalized_check in BLOCKING_SECURITY_CHECKS)
        else:
            object.__setattr__(self, "blocking", bool(self.blocking))

        object.__setattr__(self, "details", dict(self.details or {}))

    @property
    def failed_blocking(self) -> bool:
        return bool(self.blocking) and self.status == SecurityGateStatus.FAIL_BLOCKING

    @property
    def unknown_blocking(self) -> bool:
        return bool(self.blocking) and self.status == SecurityGateStatus.UNKNOWN

    def to_dict(self) -> dict[str, Any]:
        return {
            "check": self.check.value,
            "status": self.status.value,
            "message": self.message or "",
            "blocking": bool(self.blocking),
            "details": dict(self.details),
            "checked_at": self.checked_at.isoformat() if self.checked_at else None,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SecurityCheckResult":
        checked_at = data.get("checked_at")

        if isinstance(checked_at, str) and checked_at:
            parsed_checked_at = datetime.fromisoformat(checked_at.replace("Z", "+00:00"))
        else:
            parsed_checked_at = None

        return cls(
            check=data["check"],
            status=data["status"],
            message=str(data.get("message") or ""),
            blocking=bool(data.get("blocking", False)),
            details=dict(data.get("details") or {}),
            checked_at=parsed_checked_at,
        )


class SecurityGateResult:
    """
    Aggregated Security Gate result.

    Supports both constructor shapes used across the repo/tests:

        SecurityGateResult(results=[...])
        SecurityGateResult(checks=[...])
    """

    def __init__(
        self,
        *,
        results: Sequence[Any] | None = None,
        checks: Sequence[Any] | None = None,
        status: SecurityGateStatus | str = SecurityGateStatus.UNKNOWN,
        checked_at: datetime | None = None,
        instance_id: str = "",
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        raw_results = results if results is not None else checks
        normalized_results = tuple(_coerce_check_result(result) for result in (raw_results or ()))

        self.instance_id = str(instance_id or "")
        self.results = normalized_results
        self.checks = normalized_results
        self.status = _derive_status(normalized_results, fallback=status)
        self.checked_at = checked_at or _utc_now()
        self.metadata = dict(metadata or {})

    @property
    def passed(self) -> bool:
        return is_security_gate_passing(self)

    @property
    def blocking_failures(self) -> tuple[SecurityCheckResult, ...]:
        return tuple(
            result
            for result in self.results
            if result.failed_blocking or result.unknown_blocking
        )

    @property
    def warnings(self) -> tuple[SecurityCheckResult, ...]:
        return tuple(result for result in self.results if result.status == SecurityGateStatus.WARN)

    def with_derived_status(self) -> "SecurityGateResult":
        return SecurityGateResult(
            results=self.results,
            status=_derive_status(self.results, fallback=self.status),
            checked_at=self.checked_at,
            instance_id=self.instance_id,
            metadata=self.metadata,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "instance_id": self.instance_id,
            "status": self.status.value,
            "passed": self.passed,
            "checked_at": self.checked_at.isoformat(),
            "blocking_failures": [result.to_dict() for result in self.blocking_failures],
            "warnings": [result.to_dict() for result in self.warnings],
            "results": [result.to_dict() for result in self.results],
            "checks": [result.to_dict() for result in self.checks],
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SecurityGateResult":
        raw_results = data.get("results") or data.get("checks") or ()

        checked_at = data.get("checked_at")
        if isinstance(checked_at, str) and checked_at:
            parsed_checked_at = datetime.fromisoformat(checked_at.replace("Z", "+00:00"))
        else:
            parsed_checked_at = None

        return cls(
            results=[_coerce_check_result(result) for result in raw_results],
            status=data.get("status", SecurityGateStatus.UNKNOWN.value),
            checked_at=parsed_checked_at,
            instance_id=str(data.get("instance_id") or ""),
            metadata=dict(data.get("metadata") or {}),
        )


SecurityGateCheckResult = SecurityCheckResult
SecurityGateSummary = SecurityGateResult


class SecurityGateBlocked(RuntimeError):
    """Raised when the Security Gate blocks instance startup."""

    def __init__(self, result: SecurityGateResult) -> None:
        self.result = result
        self.summary = result
        failures = ", ".join(check.check.value for check in result.blocking_failures)
        message = failures or result.status.value
        super().__init__(f"Konnaxion Security Gate blocked startup: {message}")


def _coerce_check_result(value: Any) -> SecurityCheckResult:
    if isinstance(value, SecurityCheckResult):
        return value

    if isinstance(value, Mapping):
        return SecurityCheckResult(
            check=value.get("check"),
            status=value.get("status"),
            message=str(value.get("message") or ""),
            blocking=value.get("blocking"),
            details=dict(value.get("details") or {}),
            checked_at=value.get("checked_at") if isinstance(value.get("checked_at"), datetime) else None,
        )

    check = getattr(value, "check", None)
    status = getattr(value, "status", None)
    message = getattr(value, "message", "")
    blocking = getattr(value, "blocking", None)
    details = getattr(value, "details", {})

    return SecurityCheckResult(
        check=check,
        status=status,
        message=str(message or ""),
        blocking=blocking,
        details=dict(details or {}),
    )


def _extract_results(result: Any) -> tuple[SecurityCheckResult, ...]:
    if isinstance(result, SecurityGateResult):
        return result.results

    if isinstance(result, Mapping):
        raw_results = result.get("results") or result.get("checks") or ()
        return tuple(_coerce_check_result(item) for item in raw_results)

    raw_results = getattr(result, "results", None)
    if raw_results is None:
        raw_results = getattr(result, "checks", None)

    return tuple(_coerce_check_result(item) for item in (raw_results or ()))


def _derive_status(
    results: Sequence[SecurityCheckResult],
    *,
    fallback: SecurityGateStatus | str = SecurityGateStatus.UNKNOWN,
) -> SecurityGateStatus:
    if any(result.failed_blocking for result in results):
        return SecurityGateStatus.FAIL_BLOCKING

    if any(result.unknown_blocking for result in results):
        return SecurityGateStatus.UNKNOWN

    if any(result.status == SecurityGateStatus.WARN for result in results):
        return SecurityGateStatus.WARN

    if results and all(
        result.status in {SecurityGateStatus.PASS, SecurityGateStatus.SKIPPED}
        for result in results
    ):
        return SecurityGateStatus.PASS

    return normalize_security_status(fallback)


def is_security_gate_passing(result: Any) -> bool:
    """
    Return True when a Security Gate result allows startup.

    Rules:
    - FAIL_BLOCKING on a blocking check fails.
    - UNKNOWN on a blocking check fails.
    - WARN on a non-blocking check is allowed.
    - SKIPPED is allowed.
    """

    results = _extract_results(result)

    if any(item.failed_blocking for item in results):
        return False

    if any(item.unknown_blocking for item in results):
        return False

    aggregate_status = getattr(result, "status", None)

    if aggregate_status is not None:
        status = normalize_security_status(aggregate_status)
        if status == SecurityGateStatus.FAIL_BLOCKING:
            return False

        if status == SecurityGateStatus.UNKNOWN and any(item.blocking for item in results):
            return False

    return True


def assert_security_gate_passing(result: Any) -> Any:
    """Raise SecurityGateBlocked if the Security Gate result blocks startup."""

    normalized = result if isinstance(result, SecurityGateResult) else SecurityGateResult(results=_extract_results(result))

    if not is_security_gate_passing(normalized):
        raise SecurityGateBlocked(normalized)

    return result


# ---------------------------------------------------------------------------
# Security Gate context
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PublishedPort:
    """Public or host-published port discovered from Docker Compose."""

    service: str
    host_ip: str | None
    host_port: int | None
    container_port: int | None
    protocol: str | None = None
    raw: Any = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "service": self.service,
            "host_ip": self.host_ip,
            "host_port": self.host_port,
            "container_port": self.container_port,
            "protocol": self.protocol,
            "raw": self.raw,
        }


@dataclass(frozen=True)
class RuntimeServicePolicy:
    """Runtime service security-relevant policy extracted from Compose."""

    name: str
    image: str | None = None
    privileged: bool = False
    network_mode: str | None = None
    mounts: tuple[str, ...] = field(default_factory=tuple)
    published_ports: tuple[PublishedPort, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", str(self.name))
        object.__setattr__(self, "mounts", tuple(str(item) for item in self.mounts))
        object.__setattr__(self, "published_ports", tuple(self.published_ports))


@dataclass(frozen=True)
class SecurityGatePolicy:
    """Runtime policy switches used by Security Gate checks."""

    require_signed_capsule: bool = True
    allow_unknown_images: bool = False
    allow_privileged_containers: bool = False
    allow_docker_socket_mount: bool = False
    allow_host_network: bool = False

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "SecurityGatePolicy":
        return cls(
            require_signed_capsule=_env_bool(env, "KX_REQUIRE_SIGNED_CAPSULE", True),
            allow_unknown_images=_env_bool(env, "KX_ALLOW_UNKNOWN_IMAGES", False),
            allow_privileged_containers=_env_bool(
                env,
                "KX_ALLOW_PRIVILEGED_CONTAINERS",
                False,
            ),
            allow_docker_socket_mount=_env_bool(
                env,
                "KX_ALLOW_DOCKER_SOCKET_MOUNT",
                False,
            ),
            allow_host_network=_env_bool(env, "KX_ALLOW_HOST_NETWORK", False),
        )


@dataclass(frozen=True)
class SecurityGateContext:
    """Input context consumed by the Konnaxion Agent Security Gate."""

    instance_id: str
    manifest: Mapping[str, Any] = field(default_factory=dict)
    env: Mapping[str, str] = field(default_factory=dict)
    services: tuple[RuntimeServicePolicy, ...] = field(default_factory=tuple)
    published_ports: tuple[PublishedPort, ...] = field(default_factory=tuple)
    allowed_images: frozenset[str] = DEFAULT_ALLOWED_IMAGES
    capsule_signature_verified: bool = False
    image_checksums_verified: bool = False
    firewall_enabled: bool = False
    backup_configured: bool = False
    backup_root: Path = KX_BACKUPS_ROOT
    admin_surface_private: bool = True
    postgres_public: bool = False
    redis_public: bool = False
    policy: SecurityGatePolicy = field(default_factory=SecurityGatePolicy)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        instance_id = str(self.instance_id).strip()

        if not instance_id:
            raise ValueError("instance_id is required.")

        env = {str(key): str(value) for key, value in dict(self.env or {}).items()}
        manifest = dict(self.manifest or {})

        # Backstop hydration for direct SecurityGateContext(...) callers.
        if _manifest_missing_fields(manifest):
            manifest = _hydrate_manifest(
                instance_id=instance_id,
                compose={},
                manifest=manifest,
                env=env,
            )

        if any(not env.get(key) for key in REQUIRED_SECRET_KEYS):
            env = _hydrate_env(instance_id=instance_id, compose={}, env=env)

        object.__setattr__(self, "instance_id", instance_id)
        object.__setattr__(self, "manifest", manifest)
        object.__setattr__(self, "env", env)
        object.__setattr__(self, "services", tuple(self.services))
        object.__setattr__(self, "published_ports", tuple(self.published_ports))
        object.__setattr__(self, "allowed_images", frozenset(self.allowed_images))
        object.__setattr__(self, "backup_root", Path(self.backup_root))
        object.__setattr__(self, "metadata", dict(self.metadata or {}))


# ---------------------------------------------------------------------------
# Result helpers
# ---------------------------------------------------------------------------

def _result(
    check: SecurityGateCheck,
    status: SecurityGateStatus,
    message: str,
    *,
    details: Mapping[str, Any] | None = None,
) -> SecurityCheckResult:
    return SecurityCheckResult(
        check=check,
        status=status,
        message=message,
        blocking=check in BLOCKING_SECURITY_CHECKS,
        details=dict(details or {}),
        checked_at=_utc_now(),
    )


def _pass(
    check: SecurityGateCheck,
    message: str,
    *,
    details: Mapping[str, Any] | None = None,
) -> SecurityCheckResult:
    return _result(check, SecurityGateStatus.PASS, message, details=details)


def _warn(
    check: SecurityGateCheck,
    message: str,
    *,
    details: Mapping[str, Any] | None = None,
) -> SecurityCheckResult:
    return _result(check, SecurityGateStatus.WARN, message, details=details)


def _fail(
    check: SecurityGateCheck,
    message: str,
    *,
    details: Mapping[str, Any] | None = None,
) -> SecurityCheckResult:
    return _result(check, SecurityGateStatus.FAIL_BLOCKING, message, details=details)


def _unknown(
    check: SecurityGateCheck,
    message: str,
    *,
    details: Mapping[str, Any] | None = None,
) -> SecurityCheckResult:
    return _result(check, SecurityGateStatus.UNKNOWN, message, details=details)


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------

def check_capsule_signature(context: SecurityGateContext) -> SecurityCheckResult:
    check = SecurityGateCheck.CAPSULE_SIGNATURE

    if not context.policy.require_signed_capsule:
        return _warn(check, "Signed capsule enforcement is disabled by policy.")

    if context.capsule_signature_verified:
        return _pass(check, "Capsule signature verified.")

    return _fail(check, "Capsule signature is missing or invalid.")


def check_image_checksums(context: SecurityGateContext) -> SecurityCheckResult:
    check = SecurityGateCheck.IMAGE_CHECKSUMS

    if context.image_checksums_verified:
        return _pass(check, "Capsule image checksums verified.")

    return _fail(check, "Capsule image checksums are missing or invalid.")


def check_manifest_schema(context: SecurityGateContext) -> SecurityCheckResult:
    check = SecurityGateCheck.MANIFEST_SCHEMA
    missing = sorted(field for field in REQUIRED_MANIFEST_FIELDS if not context.manifest.get(field))

    if not missing:
        details: dict[str, Any] = {
            "required_fields": sorted(REQUIRED_MANIFEST_FIELDS),
        }

        manifest_path = context.manifest.get("_manifest_path")
        if manifest_path:
            details["manifest_path"] = str(manifest_path)

        return _pass(
            check,
            "Capsule manifest contains required fields.",
            details=details,
        )

    return _fail(
        check,
        "Capsule manifest is missing required fields.",
        details={
            "missing_fields": missing,
            "manifest_keys": sorted(str(key) for key in context.manifest.keys()),
            "manifest_path": context.manifest.get("_manifest_path"),
        },
    )


def check_secrets_present(context: SecurityGateContext) -> SecurityCheckResult:
    check = SecurityGateCheck.SECRETS_PRESENT
    missing = sorted(key for key in REQUIRED_SECRET_KEYS if not context.env.get(key))

    if not missing:
        return _pass(
            check,
            "Required instance secrets are present.",
            details={"required_keys": sorted(REQUIRED_SECRET_KEYS)},
        )

    return _fail(
        check,
        "Required instance secrets are missing.",
        details={
            "missing": missing,
            "available_secret_keys": sorted(key for key in REQUIRED_SECRET_KEYS if context.env.get(key)),
        },
    )


def check_secrets_not_default(context: SecurityGateContext) -> SecurityCheckResult:
    check = SecurityGateCheck.SECRETS_NOT_DEFAULT
    bad: list[str] = []

    for key in sorted(REQUIRED_SECRET_KEYS):
        value = context.env.get(key)

        if key == "DATABASE_URL":
            if _database_url_has_placeholder(value):
                bad.append(key)
        elif _secret_is_placeholder(value):
            bad.append(key)

    if not bad:
        return _pass(
            check,
            "Required instance secrets are non-default.",
            details={"checked_keys": sorted(REQUIRED_SECRET_KEYS)},
        )

    return _fail(
        check,
        "Required instance secrets are default, empty, or placeholders.",
        details={"invalid_keys": bad},
    )


def check_firewall_enabled(context: SecurityGateContext) -> SecurityCheckResult:
    check = SecurityGateCheck.FIREWALL_ENABLED

    if context.firewall_enabled:
        return _pass(check, "Host firewall is enabled.")

    return _warn(check, "Host firewall is disabled or unconfirmed.")


def check_dangerous_ports_blocked(context: SecurityGateContext) -> SecurityCheckResult:
    check = SecurityGateCheck.DANGEROUS_PORTS_BLOCKED
    exposed = [
        port.to_dict()
        for port in _public_ports(context)
        if (port.host_port in FORBIDDEN_PUBLIC_PORTS or port.container_port in FORBIDDEN_PUBLIC_PORTS)
    ]

    if not exposed:
        return _pass(
            check,
            "Forbidden internal service ports are not publicly exposed.",
            details={"forbidden_ports": sorted(FORBIDDEN_PUBLIC_PORTS)},
        )

    return _fail(
        check,
        "Forbidden internal service ports are publicly exposed.",
        details={
            "exposed": exposed,
            "forbidden_ports": sorted(FORBIDDEN_PUBLIC_PORTS),
        },
    )


def check_postgres_not_public(context: SecurityGateContext) -> SecurityCheckResult:
    check = SecurityGateCheck.POSTGRES_NOT_PUBLIC

    if context.postgres_public:
        return _fail(check, "PostgreSQL is marked public.")

    offenders = [
        port.to_dict()
        for port in _public_ports(context)
        if port.service == DockerService.POSTGRES.value
        or port.host_port == 5432
        or port.container_port == 5432
    ]

    if offenders:
        return _fail(
            check,
            "PostgreSQL is publicly exposed.",
            details={"ports": offenders},
        )

    return _pass(check, "PostgreSQL is not publicly exposed.")


def check_redis_not_public(context: SecurityGateContext) -> SecurityCheckResult:
    check = SecurityGateCheck.REDIS_NOT_PUBLIC

    if context.redis_public:
        return _fail(check, "Redis is marked public.")

    offenders = [
        port.to_dict()
        for port in _public_ports(context)
        if port.service == DockerService.REDIS.value
        or port.host_port == 6379
        or port.container_port == 6379
    ]

    if offenders:
        return _fail(
            check,
            "Redis is publicly exposed.",
            details={"ports": offenders},
        )

    return _pass(check, "Redis is not publicly exposed.")


def check_docker_socket_not_mounted(context: SecurityGateContext) -> SecurityCheckResult:
    check = SecurityGateCheck.DOCKER_SOCKET_NOT_MOUNTED

    if context.policy.allow_docker_socket_mount:
        return _warn(check, "Docker socket mounts are allowed by policy.")

    offenders = [
        service.name
        for service in context.services
        if _has_forbidden_mount(service.mounts)
    ]

    if not offenders:
        return _pass(check, "Docker socket is not mounted into app containers.")

    return _fail(
        check,
        "Docker socket is mounted into one or more containers.",
        details={"offenders": offenders},
    )


def check_no_privileged_containers(context: SecurityGateContext) -> SecurityCheckResult:
    check = SecurityGateCheck.NO_PRIVILEGED_CONTAINERS

    if context.policy.allow_privileged_containers:
        return _warn(check, "Privileged containers are allowed by policy.")

    offenders = [service.name for service in context.services if service.privileged]

    if not offenders:
        return _pass(check, "No privileged app containers are configured.")

    return _fail(
        check,
        "One or more containers are configured as privileged.",
        details={"offenders": offenders},
    )


def check_no_host_network(context: SecurityGateContext) -> SecurityCheckResult:
    check = SecurityGateCheck.NO_HOST_NETWORK

    if context.policy.allow_host_network:
        return _warn(check, "Host network mode is allowed by policy.")

    offenders = [
        service.name
        for service in context.services
        if str(service.network_mode or "").strip().lower() == "host"
    ]

    if not offenders:
        return _pass(check, "No app containers use host networking.")

    return _fail(
        check,
        "One or more containers use host networking.",
        details={"offenders": offenders},
    )


def check_allowed_images_only(context: SecurityGateContext) -> SecurityCheckResult:
    check = SecurityGateCheck.ALLOWED_IMAGES_ONLY

    if context.policy.allow_unknown_images:
        return _warn(check, "Unknown images are allowed by policy.")

    allowed = set(context.allowed_images or ())
    unknown: dict[str, str] = {}

    for service in context.services:
        base_name = _image_base_name(service.image)
        service_name = service.name

        if not base_name:
            continue

        if service.image in allowed or base_name in allowed or service_name in allowed:
            continue

        unknown[service_name] = service.image or ""

    if not unknown:
        return _pass(
            check,
            "All configured service images are allowlisted.",
            details={"allowed_images": sorted(allowed)},
        )

    return _fail(
        check,
        "One or more configured service images are not allowlisted.",
        details={"unknown_images": unknown, "allowed_images": sorted(allowed)},
    )


def check_admin_surface_private(context: SecurityGateContext) -> SecurityCheckResult:
    check = SecurityGateCheck.ADMIN_SURFACE_PRIVATE

    if context.admin_surface_private:
        return _pass(check, "Admin surface is private.")

    return _warn(check, "Admin surface is public or unconfirmed.")


def check_backup_configured(context: SecurityGateContext) -> SecurityCheckResult:
    check = SecurityGateCheck.BACKUP_CONFIGURED

    if context.backup_configured and str(context.backup_root).strip():
        return _pass(
            check,
            "Backups are enabled and backup root is configured.",
            details={"backup_root": str(context.backup_root)},
        )

    return _warn(check, "Backup configuration is missing or unconfirmed.")


# ---------------------------------------------------------------------------
# Compose parsing helpers
# ---------------------------------------------------------------------------

def parse_port_mapping(service: str, value: Any) -> PublishedPort:
    """Parse one Docker Compose port mapping into a PublishedPort."""

    if isinstance(value, int):
        return PublishedPort(
            service=service,
            host_ip=None,
            host_port=value,
            container_port=value,
            protocol="tcp",
            raw=value,
        )

    if isinstance(value, Mapping):
        published = value.get("published") or value.get("host_port")
        target = value.get("target") or value.get("container_port")
        protocol = value.get("protocol") or "tcp"

        return PublishedPort(
            service=service,
            host_ip=str(value.get("host_ip")) if value.get("host_ip") is not None else None,
            host_port=_int_or_none(published),
            container_port=_int_or_none(target),
            protocol=str(protocol),
            raw=dict(value),
        )

    raw = str(value)
    protocol = "tcp"
    without_protocol = raw

    if "/" in raw:
        without_protocol, protocol = raw.rsplit("/", 1)

    parts = without_protocol.split(":")
    host_ip: str | None = None
    host_port: int | None = None
    container_port: int | None = None

    try:
        if len(parts) == 1:
            container_port = int(parts[0])
            host_port = container_port
        elif len(parts) == 2:
            host_port = int(parts[0])
            container_port = int(parts[1])
        elif len(parts) >= 3:
            host_ip = parts[0]
            host_port = int(parts[-2])
            container_port = int(parts[-1])
    except ValueError:
        host_port = None
        container_port = None

    return PublishedPort(
        service=service,
        host_ip=host_ip,
        host_port=host_port,
        container_port=container_port,
        protocol=protocol,
        raw=value,
    )


def services_from_compose(compose: Mapping[str, Any]) -> tuple[RuntimeServicePolicy, ...]:
    """Extract service policy data from a Docker Compose-like mapping."""

    raw_services = compose.get("services", {})
    if not isinstance(raw_services, Mapping):
        return ()

    services: list[RuntimeServicePolicy] = []

    for service_name, raw_spec in raw_services.items():
        spec = raw_spec if isinstance(raw_spec, Mapping) else {}

        mounts: list[str] = []
        for key in ("volumes", "mounts"):
            value = spec.get(key, ())
            if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
                mounts.extend(str(item) for item in value)

        ports = tuple(
            parse_port_mapping(str(service_name), item)
            for item in spec.get("ports", ()) or ()
        )

        services.append(
            RuntimeServicePolicy(
                name=str(service_name),
                image=str(spec.get("image")) if spec.get("image") is not None else None,
                privileged=bool(spec.get("privileged", False)),
                network_mode=str(spec.get("network_mode")) if spec.get("network_mode") else None,
                mounts=tuple(mounts),
                published_ports=ports,
            )
        )

    return tuple(services)


def _public_ports(context: SecurityGateContext) -> tuple[PublishedPort, ...]:
    service_ports = tuple(
        port
        for service in context.services
        for port in service.published_ports
    )
    return tuple((*context.published_ports, *service_ports))


def context_from_compose(
    *,
    instance_id: str,
    compose: Mapping[str, Any],
    manifest: Mapping[str, Any],
    env: Mapping[str, str],
    capsule_signature_verified: bool,
    image_checksums_verified: bool,
    firewall_enabled: bool,
    backup_configured: bool,
    admin_surface_private: bool = True,
    postgres_public: bool = False,
    redis_public: bool = False,
    allowed_images: Iterable[str] | None = None,
    policy: SecurityGatePolicy | None = None,
) -> SecurityGateContext:
    """
    Build SecurityGateContext from a Compose-like mapping.

    This function accepts explicit manifest/env data, but if either is empty or
    incomplete it attempts read-only hydration from canonical runtime files.
    """

    services = services_from_compose(compose)
    published_ports = tuple(port for service in services for port in service.published_ports)

    hydrated_env = _hydrate_env(
        instance_id=instance_id,
        compose=compose,
        env=env,
    )

    hydrated_manifest = _hydrate_manifest(
        instance_id=instance_id,
        compose=compose,
        manifest=manifest,
        env=hydrated_env,
    )

    metadata = {
        "runtime_root": str(_runtime_root()),
        "manifest_hydrated": bool(hydrated_manifest and hydrated_manifest != dict(manifest or {})),
        "env_hydrated": bool(hydrated_env and hydrated_env != dict(env or {})),
    }

    if hydrated_manifest.get("_manifest_path"):
        metadata["manifest_path"] = str(hydrated_manifest.get("_manifest_path"))

    return SecurityGateContext(
        instance_id=instance_id,
        manifest=hydrated_manifest,
        env=hydrated_env,
        services=services,
        published_ports=published_ports,
        capsule_signature_verified=capsule_signature_verified,
        image_checksums_verified=image_checksums_verified,
        firewall_enabled=firewall_enabled,
        backup_configured=backup_configured,
        admin_surface_private=admin_surface_private,
        postgres_public=postgres_public,
        redis_public=redis_public,
        allowed_images=frozenset(allowed_images) if allowed_images is not None else DEFAULT_ALLOWED_IMAGES,
        policy=policy or SecurityGatePolicy.from_env(hydrated_env),
        metadata=metadata,
    )


# ---------------------------------------------------------------------------
# Gate runner
# ---------------------------------------------------------------------------

class SecurityGate:
    """Runs all canonical Konnaxion Security Gate checks."""

    CHECKS = (
        check_capsule_signature,
        check_image_checksums,
        check_manifest_schema,
        check_secrets_present,
        check_secrets_not_default,
        check_firewall_enabled,
        check_dangerous_ports_blocked,
        check_postgres_not_public,
        check_redis_not_public,
        check_docker_socket_not_mounted,
        check_no_privileged_containers,
        check_no_host_network,
        check_allowed_images_only,
        check_admin_surface_private,
        check_backup_configured,
    )

    def run(self, context: SecurityGateContext) -> SecurityGateResult:
        results = tuple(check(context) for check in self.CHECKS)

        metadata = {
            **dict(context.metadata or {}),
            "routes": dict(ROUTES),
            "canonical_services": tuple(CANONICAL_DOCKER_SERVICES),
            "internal_only_ports": {
                _enum_value(service): port
                for service, port in INTERNAL_ONLY_PORTS.items()
            },
        }

        return SecurityGateResult(
            results=results,
            instance_id=context.instance_id,
            metadata=metadata,
        )

    def assert_start_allowed(self, context: SecurityGateContext) -> SecurityGateResult:
        result = self.run(context)
        assert_security_gate_passing(result)
        return result


def run_security_gate(context: SecurityGateContext) -> SecurityGateResult:
    """Run the canonical Security Gate."""

    return SecurityGate().run(context)


def assert_security_gate_allows_start(context: SecurityGateContext) -> SecurityGateResult:
    """Run the Security Gate and raise if startup must be blocked."""

    return SecurityGate().assert_start_allowed(context)


__all__ = [
    "DEFAULT_ALLOWED_IMAGES",
    "FORBIDDEN_HOST_NETWORK_VALUES",
    "FORBIDDEN_MOUNT_PATHS",
    "FORBIDDEN_PRIVILEGED_VALUES",
    "REQUIRED_MANIFEST_FIELDS",
    "REQUIRED_SECRET_KEYS",
    "SECRET_PLACEHOLDER_SUBSTRINGS",
    "SECRET_PLACEHOLDER_VALUES",
    "PublishedPort",
    "RuntimeServicePolicy",
    "SecurityCheckResult",
    "SecurityGate",
    "SecurityGateBlocked",
    "SecurityGateCheckResult",
    "SecurityGateContext",
    "SecurityGatePolicy",
    "SecurityGateResult",
    "SecurityGateSummary",
    "assert_security_gate_allows_start",
    "assert_security_gate_passing",
    "check_admin_surface_private",
    "check_allowed_images_only",
    "check_backup_configured",
    "check_capsule_signature",
    "check_dangerous_ports_blocked",
    "check_docker_socket_not_mounted",
    "check_firewall_enabled",
    "check_image_checksums",
    "check_manifest_schema",
    "check_no_host_network",
    "check_no_privileged_containers",
    "check_postgres_not_public",
    "check_redis_not_public",
    "check_secrets_not_default",
    "check_secrets_present",
    "context_from_compose",
    "is_security_gate_passing",
    "normalize_security_check",
    "normalize_security_status",
    "parse_port_mapping",
    "run_security_gate",
    "services_from_compose",
]