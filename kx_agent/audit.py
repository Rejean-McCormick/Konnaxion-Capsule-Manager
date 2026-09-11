"""
Append-only audit logging for the Konnaxion Agent.

The Agent is the privileged component in the Konnaxion Capsule Manager
architecture. Every privileged or security-sensitive action should create an
audit event here before and after execution.

Audit records are written as JSON Lines so they are easy to inspect, rotate,
backup, and stream into the Manager UI.

This module intentionally keeps audit concerns separate from business logic:
callers describe what happened; this module serializes, redacts, and persists
the event.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import tempfile
import uuid
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Iterable, Mapping, TextIO

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows compatibility for dev/test only.
    fcntl = None  # type: ignore[assignment]

from kx_shared.konnaxion_constants import (
    KX_AGENT_DIR,
    KX_ROOT,
    DockerService,
    ExposureMode,
    InstanceState,
    NetworkProfile,
    SecurityGateStatus,
)
from kx_shared.types import (
    AgentActionName,
    CapsuleID,
    InstanceID,
    JsonObject,
)


# ---------------------------------------------------------------------
# Audit defaults
# ---------------------------------------------------------------------

DEFAULT_AUDIT_DIR = KX_AGENT_DIR / "audit"
DEFAULT_AUDIT_FILE = DEFAULT_AUDIT_DIR / "agent-audit.jsonl"
DEFAULT_AUDIT_FILE_ENV = "KX_AGENT_AUDIT_FILE"
DEFAULT_AUDIT_MODE = 0o600
DEFAULT_AUDIT_DIR_MODE = 0o700

AUDIT_SCHEMA_VERSION = "kx-audit/v1"


# ---------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------

REDACTED_VALUE = "<REDACTED>"

SENSITIVE_KEY_PATTERNS = (
    re.compile(r".*password.*", re.IGNORECASE),
    re.compile(r".*secret.*", re.IGNORECASE),
    re.compile(r".*token.*", re.IGNORECASE),
    re.compile(r".*private[_-]?key.*", re.IGNORECASE),
    re.compile(r".*credential.*", re.IGNORECASE),
    re.compile(r".*api[_-]?key.*", re.IGNORECASE),
    re.compile(r".*dsn.*", re.IGNORECASE),
    re.compile(r".*database[_-]?url.*", re.IGNORECASE),
    re.compile(r".*redis[_-]?url.*", re.IGNORECASE),
)

SENSITIVE_VALUE_PATTERNS = (
    re.compile(r"postgres://[^\s]+", re.IGNORECASE),
    re.compile(r"redis://[^\s]+", re.IGNORECASE),
    re.compile(r"-----BEGIN [A-Z ]+PRIVATE KEY-----"),
)


class AuditSeverity(StrEnum):
    """Severity attached to an audit event."""

    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class AuditOutcome(StrEnum):
    """Outcome attached to an audit event."""

    STARTED = "started"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"
    SKIPPED = "skipped"


class AuditCategory(StrEnum):
    """High-level audit categories used by the Agent."""

    AGENT = "agent"
    CAPSULE = "capsule"
    INSTANCE = "instance"
    RUNTIME = "runtime"
    SECURITY = "security"
    NETWORK = "network"
    BACKUP = "backup"
    RESTORE = "restore"
    ROLLBACK = "rollback"
    CONFIG = "config"


@dataclass(frozen=True, slots=True)
class AuditActor:
    """The actor that requested or performed an action."""

    actor_id: str = "system"
    actor_type: str = "system"
    display_name: str | None = None
    source_ip: str | None = None


@dataclass(frozen=True, slots=True)
class AuditEvent:
    """Single append-only audit event."""

    event_id: str
    schema_version: str
    timestamp: str
    category: AuditCategory
    action: str
    outcome: AuditOutcome
    severity: AuditSeverity
    message: str
    actor: AuditActor
    hostname: str
    instance_id: str | None = None
    capsule_id: str | None = None
    request_id: str | None = None
    correlation_id: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    error: str | None = None


def utc_now() -> datetime:
    """Return timezone-aware UTC datetime."""

    return datetime.now(UTC)


def utc_now_iso() -> str:
    """Return an ISO-8601 UTC timestamp."""

    return utc_now().isoformat().replace("+00:00", "Z")


def new_event_id() -> str:
    """Return a stable audit event id."""

    return f"audit_{uuid.uuid4().hex}"


def make_correlation_id(*parts: object) -> str:
    """Create a short deterministic correlation id from stable values."""

    raw = "|".join(str(part) for part in parts if part is not None)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]
    return f"kx_corr_{digest}"


def _is_sensitive_key(key: str) -> bool:
    return any(pattern.fullmatch(key) for pattern in SENSITIVE_KEY_PATTERNS)


def _redact_string(value: str) -> str:
    for pattern in SENSITIVE_VALUE_PATTERNS:
        if pattern.search(value):
            return REDACTED_VALUE
    return value


def redact(value: Any, *, key_hint: str | None = None) -> Any:
    """Return a JSON-safe copy of *value* with secrets removed."""

    if key_hint and _is_sensitive_key(key_hint):
        return REDACTED_VALUE

    if value is None or isinstance(value, bool | int | float):
        return value

    if isinstance(value, str):
        return _redact_string(value)

    if isinstance(value, Path):
        return str(value)

    if isinstance(value, StrEnum):
        return value.value

    if isinstance(value, Mapping):
        return {
            str(key): redact(item, key_hint=str(key))
            for key, item in value.items()
        }

    if isinstance(value, tuple | list | set | frozenset):
        return [redact(item) for item in value]

    if is_dataclass(value):
        return redact(asdict(value))

    return str(value)


def json_default(value: Any) -> Any:
    """JSON serializer fallback for known shared types."""

    if isinstance(value, Path):
        return str(value)

    if isinstance(value, datetime):
        return value.isoformat().replace("+00:00", "Z")

    if isinstance(value, StrEnum):
        return value.value

    if is_dataclass(value):
        return asdict(value)

    return str(value)


def _ensure_secure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, DEFAULT_AUDIT_DIR_MODE)
    except PermissionError:
        # The Agent may run in a constrained test environment where chmod is not allowed.
        pass


def _open_append_secure(path: Path) -> TextIO:
    _ensure_secure_parent(path)
    handle = path.open("a", encoding="utf-8")
    try:
        os.chmod(path, DEFAULT_AUDIT_MODE)
    except PermissionError:
        pass
    return handle


def _lock_file(handle: TextIO) -> None:
    if fcntl is not None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)


def _unlock_file(handle: TextIO) -> None:
    if fcntl is not None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class AuditLogger:
    """Append-only JSONL audit writer for Agent actions."""

    def __init__(self, audit_file: Path | str = DEFAULT_AUDIT_FILE) -> None:
        self.audit_file = Path(audit_file)

    def event(
        self,
        *,
        category: AuditCategory,
        action: str,
        outcome: AuditOutcome,
        message: str,
        severity: AuditSeverity = AuditSeverity.INFO,
        actor: AuditActor | None = None,
        instance_id: InstanceID | str | None = None,
        capsule_id: CapsuleID | str | None = None,
        request_id: str | None = None,
        correlation_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        error: BaseException | str | None = None,
    ) -> AuditEvent:
        """Create and append an audit event."""

        event = AuditEvent(
            event_id=new_event_id(),
            schema_version=AUDIT_SCHEMA_VERSION,
            timestamp=utc_now_iso(),
            category=category,
            action=action,
            outcome=outcome,
            severity=severity,
            message=message,
            actor=actor or AuditActor(),
            hostname=socket.gethostname(),
            instance_id=str(instance_id) if instance_id is not None else None,
            capsule_id=str(capsule_id) if capsule_id is not None else None,
            request_id=request_id,
            correlation_id=correlation_id,
            metadata=redact(metadata or {}),
            error=str(error) if error is not None else None,
        )
        self.write(event)
        return event

    def started(
        self,
        *,
        category: AuditCategory,
        action: str,
        message: str,
        actor: AuditActor | None = None,
        instance_id: InstanceID | str | None = None,
        capsule_id: CapsuleID | str | None = None,
        request_id: str | None = None,
        correlation_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> AuditEvent:
        return self.event(
            category=category,
            action=action,
            outcome=AuditOutcome.STARTED,
            severity=AuditSeverity.INFO,
            message=message,
            actor=actor,
            instance_id=instance_id,
            capsule_id=capsule_id,
            request_id=request_id,
            correlation_id=correlation_id,
            metadata=metadata,
        )

    def succeeded(
        self,
        *,
        category: AuditCategory,
        action: str,
        message: str,
        actor: AuditActor | None = None,
        instance_id: InstanceID | str | None = None,
        capsule_id: CapsuleID | str | None = None,
        request_id: str | None = None,
        correlation_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> AuditEvent:
        return self.event(
            category=category,
            action=action,
            outcome=AuditOutcome.SUCCEEDED,
            severity=AuditSeverity.INFO,
            message=message,
            actor=actor,
            instance_id=instance_id,
            capsule_id=capsule_id,
            request_id=request_id,
            correlation_id=correlation_id,
            metadata=metadata,
        )

    def failed(
        self,
        *,
        category: AuditCategory,
        action: str,
        message: str,
        error: BaseException | str,
        actor: AuditActor | None = None,
        instance_id: InstanceID | str | None = None,
        capsule_id: CapsuleID | str | None = None,
        request_id: str | None = None,
        correlation_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> AuditEvent:
        return self.event(
            category=category,
            action=action,
            outcome=AuditOutcome.FAILED,
            severity=AuditSeverity.ERROR,
            message=message,
            actor=actor,
            instance_id=instance_id,
            capsule_id=capsule_id,
            request_id=request_id,
            correlation_id=correlation_id,
            metadata=metadata,
            error=error,
        )

    def blocked(
        self,
        *,
        category: AuditCategory,
        action: str,
        message: str,
        actor: AuditActor | None = None,
        instance_id: InstanceID | str | None = None,
        capsule_id: CapsuleID | str | None = None,
        request_id: str | None = None,
        correlation_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        error: BaseException | str | None = None,
    ) -> AuditEvent:
        return self.event(
            category=category,
            action=action,
            outcome=AuditOutcome.BLOCKED,
            severity=AuditSeverity.CRITICAL,
            message=message,
            actor=actor,
            instance_id=instance_id,
            capsule_id=capsule_id,
            request_id=request_id,
            correlation_id=correlation_id,
            metadata=metadata,
            error=error,
        )

    def write(self, event: AuditEvent) -> None:
        """Append one event to the audit file."""

        payload = redact(asdict(event))
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            default=json_default,
            separators=(",", ":"),
        )

        with _open_append_secure(self.audit_file) as handle:
            _lock_file(handle)
            try:
                handle.write(encoded)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            finally:
                _unlock_file(handle)

    def read_events(self, *, limit: int | None = None) -> list[JsonObject]:
        """Read audit events from newest to oldest.

        This is primarily for Manager UI display and tests. Large exports should
        stream the file instead of calling this method.
        """

        if not self.audit_file.exists():
            return []

        lines = self.audit_file.read_text(encoding="utf-8").splitlines()
        if limit is not None:
            lines = lines[-limit:]

        events: list[JsonObject] = []
        for line in reversed(lines):
            if not line.strip():
                continue
            loaded = json.loads(line)
            if isinstance(loaded, dict):
                events.append(loaded)
        return events

    def export_copy(self, destination: Path | str) -> Path:
        """Write a copy of the audit log to *destination*."""

        destination_path = Path(destination)
        destination_path.parent.mkdir(parents=True, exist_ok=True)

        if not self.audit_file.exists():
            destination_path.write_text("", encoding="utf-8")
            return destination_path

        data = self.audit_file.read_bytes()
        with tempfile.NamedTemporaryFile(
            "wb",
            delete=False,
            dir=str(destination_path.parent),
            prefix=f".{destination_path.name}.",
        ) as tmp:
            tmp.write(data)
            tmp.flush()
            os.fsync(tmp.fileno())
            tmp_path = Path(tmp.name)

        tmp_path.replace(destination_path)
        return destination_path


def get_audit_logger(audit_file: Path | str | None = None) -> AuditLogger:
    """Return an ``AuditLogger`` using the configured Agent audit path."""

    configured = os.environ.get(DEFAULT_AUDIT_FILE_ENV, "").strip()
    return AuditLogger(audit_file or configured or DEFAULT_AUDIT_FILE)


def audit_agent_action(
    *,
    action: AgentActionName | str,
    outcome: AuditOutcome,
    message: str,
    category: AuditCategory = AuditCategory.AGENT,
    severity: AuditSeverity = AuditSeverity.INFO,
    actor: AuditActor | None = None,
    instance_id: InstanceID | str | None = None,
    capsule_id: CapsuleID | str | None = None,
    request_id: str | None = None,
    correlation_id: str | None = None,
    metadata: Mapping[str, Any] | None = None,
    error: BaseException | str | None = None,
    logger: AuditLogger | None = None,
) -> AuditEvent:
    """Convenience function for one-shot Agent action auditing."""

    audit_logger = logger or get_audit_logger()
    return audit_logger.event(
        category=category,
        action=str(action),
        outcome=outcome,
        severity=severity,
        message=message,
        actor=actor,
        instance_id=instance_id,
        capsule_id=capsule_id,
        request_id=request_id,
        correlation_id=correlation_id,
        metadata=metadata,
        error=error,
    )


def audit_security_gate_result(
    *,
    instance_id: InstanceID | str,
    status: SecurityGateStatus,
    findings_count: int,
    blocking_failures_count: int,
    capsule_id: CapsuleID | str | None = None,
    network_profile: NetworkProfile | None = None,
    exposure_mode: ExposureMode | None = None,
    actor: AuditActor | None = None,
    request_id: str | None = None,
    logger: AuditLogger | None = None,
) -> AuditEvent:
    """Audit the result of a Security Gate run."""

    if status == SecurityGateStatus.FAIL_BLOCKING:
        outcome = AuditOutcome.BLOCKED
        severity = AuditSeverity.CRITICAL
        message = "Security Gate blocked instance startup"
    elif status == SecurityGateStatus.PASS:
        outcome = AuditOutcome.SUCCEEDED
        severity = AuditSeverity.INFO
        message = "Security Gate passed"
    else:
        outcome = AuditOutcome.SUCCEEDED
        severity = AuditSeverity.WARNING
        message = "Security Gate completed with non-passing status"

    return audit_agent_action(
        action="run_security_gate",
        category=AuditCategory.SECURITY,
        outcome=outcome,
        severity=severity,
        message=message,
        actor=actor,
        instance_id=instance_id,
        capsule_id=capsule_id,
        request_id=request_id,
        metadata={
            "status": status,
            "findings_count": findings_count,
            "blocking_failures_count": blocking_failures_count,
            "network_profile": network_profile,
            "exposure_mode": exposure_mode,
        },
        logger=logger,
    )


def audit_instance_state_change(
    *,
    instance_id: InstanceID | str,
    from_state: InstanceState,
    to_state: InstanceState,
    reason: str,
    actor: AuditActor | None = None,
    request_id: str | None = None,
    logger: AuditLogger | None = None,
) -> AuditEvent:
    """Audit an instance lifecycle state transition."""

    return audit_agent_action(
        action="instance_state_change",
        category=AuditCategory.INSTANCE,
        outcome=AuditOutcome.SUCCEEDED,
        severity=AuditSeverity.INFO,
        message=f"Instance state changed from {from_state.value} to {to_state.value}",
        actor=actor,
        instance_id=instance_id,
        request_id=request_id,
        metadata={
            "from_state": from_state,
            "to_state": to_state,
            "reason": reason,
        },
        logger=logger,
    )


def audit_service_action(
    *,
    instance_id: InstanceID | str,
    service: DockerService,
    action: str,
    outcome: AuditOutcome,
    message: str,
    actor: AuditActor | None = None,
    request_id: str | None = None,
    logger: AuditLogger | None = None,
) -> AuditEvent:
    """Audit a Docker Compose service-level Agent operation."""

    severity = AuditSeverity.ERROR if outcome == AuditOutcome.FAILED else AuditSeverity.INFO

    return audit_agent_action(
        action=action,
        category=AuditCategory.RUNTIME,
        outcome=outcome,
        severity=severity,
        message=message,
        actor=actor,
        instance_id=instance_id,
        request_id=request_id,
        metadata={"service": service},
        logger=logger,
    )


def read_recent_audit_events(
    *,
    limit: int = 100,
    audit_file: Path | str | None = None,
) -> list[JsonObject]:
    """Read recent audit events from the default audit log."""

    return get_audit_logger(audit_file).read_events(limit=limit)


def validate_audit_path(path: Path | str) -> Path:
    """Validate that an audit path is under the Konnaxion root.

    This prevents accidental writes to arbitrary host paths.
    """

    audit_path = Path(path).expanduser()
    resolved_root = KX_ROOT.resolve()
    resolved_path = audit_path.resolve(strict=False)

    try:
        resolved_path.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError(
            f"audit path must be under {resolved_root}: {resolved_path}"
        ) from exc

    return audit_path


__all__ = [
    "AUDIT_SCHEMA_VERSION",
    "DEFAULT_AUDIT_DIR",
    "DEFAULT_AUDIT_DIR_MODE",
    "DEFAULT_AUDIT_FILE",
    "DEFAULT_AUDIT_FILE_ENV",
    "DEFAULT_AUDIT_MODE",
    "REDACTED_VALUE",
    "AuditActor",
    "AuditCategory",
    "AuditEvent",
    "AuditLogger",
    "AuditOutcome",
    "AuditSeverity",
    "audit_agent_action",
    "audit_instance_state_change",
    "audit_security_gate_result",
    "audit_service_action",
    "get_audit_logger",
    "make_correlation_id",
    "new_event_id",
    "read_recent_audit_events",
    "redact",
    "utc_now",
    "utc_now_iso",
    "validate_audit_path",
]
