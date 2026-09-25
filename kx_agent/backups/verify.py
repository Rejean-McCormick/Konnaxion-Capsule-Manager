"""
Konnaxion backup verification.

Responsibilities:
- Verify backup directory structure.
- Validate backup manifest JSON.
- Check expected backup artifacts exist.
- Verify SHA-256 checksums when present.
- Detect forbidden host/system snapshot artifacts.
- Return typed verification reports for Manager/API/UI layers.

This module does not:
- Create backups.
- Restore backups.
- Delete backups.
- Start or stop services.
- Trust backup contents without verification.

Canonical backup storage:
    /opt/konnaxion/backups/<INSTANCE_ID>/<BACKUP_CLASS>/<BACKUP_ID>
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import Enum, StrEnum
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from kx_shared.paths import (
    KonnaxionPathError,
    assert_under_root,
    backup_database_file,
    backup_dir,
    backup_env_archive,
    backup_logs_archive,
    backup_manifest_file,
    backup_media_archive,
    backups_instance_root,
    validate_safe_id,
)

try:
    from kx_shared.konnaxion_constants import BackupStatus, KX_BACKUPS_ROOT
except ImportError:  # pragma: no cover - early scaffold fallback
    class BackupStatus(StrEnum):
        CREATED = "created"
        RUNNING = "running"
        VERIFYING = "verifying"
        VERIFIED = "verified"
        FAILED = "failed"
        EXPIRED = "expired"
        DELETED = "deleted"
        QUARANTINED = "quarantined"

    KX_BACKUPS_ROOT = Path("/opt/konnaxion/backups")


class BackupVerifyError(RuntimeError):
    """Raised when backup verification cannot be completed."""


class BackupManifestError(BackupVerifyError):
    """Raised when a backup manifest is missing or invalid."""


class BackupVerificationStatus(StrEnum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"


class BackupVerificationSeverity(StrEnum):
    INFO = "INFO"
    WARN = "WARN"
    BLOCK = "BLOCK"


class BackupArtifactKind(StrEnum):
    MANIFEST = "manifest"
    DATABASE = "database"
    MEDIA = "media"
    ENV = "env"
    LOGS = "logs"
    CHECKSUMS = "checksums"
    OTHER = "other"


@dataclass(frozen=True)
class BackupArtifact:
    """A backup artifact expected or discovered during verification."""

    kind: str
    path: str
    required: bool = True
    exists: bool = False
    size_bytes: int | None = None
    sha256: str | None = None
    expected_sha256: str | None = None
    checksum_ok: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BackupVerificationFinding:
    """Single backup verification finding."""

    status: str
    severity: str
    code: str
    message: str
    path: str | None = None
    value: Any | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BackupVerificationReport:
    """Serializable report returned by backup verification."""

    accepted: bool
    status: str
    backup_status: str
    instance_id: str
    backup_class: str
    backup_id: str
    backup_path: str
    verified_at: str
    artifacts: tuple[BackupArtifact, ...] = field(default_factory=tuple)
    findings: tuple[BackupVerificationFinding, ...] = field(default_factory=tuple)
    manifest: Mapping[str, Any] | None = None

    @property
    def blocking_findings(self) -> tuple[BackupVerificationFinding, ...]:
        return tuple(item for item in self.findings if item.severity == BackupVerificationSeverity.BLOCK.value)

    @property
    def warnings(self) -> tuple[BackupVerificationFinding, ...]:
        return tuple(item for item in self.findings if item.severity == BackupVerificationSeverity.WARN.value)

    def to_dict(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "status": self.status,
            "backup_status": self.backup_status,
            "instance_id": self.instance_id,
            "backup_class": self.backup_class,
            "backup_id": self.backup_id,
            "backup_path": self.backup_path,
            "verified_at": self.verified_at,
            "artifacts": [artifact.to_dict() for artifact in self.artifacts],
            "findings": [finding.to_dict() for finding in self.findings],
            "manifest": dict(self.manifest) if self.manifest is not None else None,
        }


@dataclass(frozen=True)
class BackupVerificationOptions:
    """
    Backup verification options.

    require_checksums:
        When true, every required artifact must have an expected checksum.
    allow_missing_logs:
        Logs are useful but may be omitted from some minimal backups.
    allow_missing_env:
        Env archive is sensitive; backup systems may choose not to include it.
    """

    require_checksums: bool = True
    allow_missing_logs: bool = True
    allow_missing_env: bool = False
    max_manifest_bytes: int = 2 * 1024 * 1024


FORBIDDEN_BACKUP_PATH_PARTS = frozenset(
    {
        "dev",
        "proc",
        "sys",
        "tmp",
        "run",
        "var/run/docker.sock",
        "docker.sock",
        "etc/sudoers",
        "etc/cron.d",
        "var/spool/cron",
        "authorized_keys",
        "id_rsa",
        "id_ed25519",
    }
)

DEFAULT_REQUIRED_ARTIFACTS = (
    BackupArtifactKind.MANIFEST,
    BackupArtifactKind.DATABASE,
    BackupArtifactKind.MEDIA,
)

OPTIONAL_ARTIFACTS = (
    BackupArtifactKind.ENV,
    BackupArtifactKind.LOGS,
)


def utc_now_iso() -> str:
    """Return a stable UTC timestamp for reports."""
    return datetime.now(UTC).isoformat(timespec="seconds")


def finding(
    code: str,
    message: str,
    *,
    severity: BackupVerificationSeverity = BackupVerificationSeverity.BLOCK,
    path: str | Path | None = None,
    value: Any | None = None,
) -> BackupVerificationFinding:
    """Create a normalized verification finding."""
    if severity == BackupVerificationSeverity.BLOCK:
        status = BackupVerificationStatus.FAIL
    elif severity == BackupVerificationSeverity.WARN:
        status = BackupVerificationStatus.WARN
    else:
        status = BackupVerificationStatus.PASS

    return BackupVerificationFinding(
        status=status.value,
        severity=severity.value,
        code=code,
        message=message,
        path=str(path) if path is not None else None,
        value=value,
    )


def report_status(findings: Sequence[BackupVerificationFinding]) -> tuple[bool, str, str]:
    """Return accepted/status/backup_status from findings."""
    has_blocking = any(item.severity == BackupVerificationSeverity.BLOCK.value for item in findings)
    has_warn = any(item.severity == BackupVerificationSeverity.WARN.value for item in findings)

    if has_blocking:
        return False, BackupVerificationStatus.FAIL.value, BackupStatus.QUARANTINED.value

    if has_warn:
        return True, BackupVerificationStatus.WARN.value, BackupStatus.VERIFIED.value

    return True, BackupVerificationStatus.PASS.value, BackupStatus.VERIFIED.value


def sha256_file(path: str | Path, *, chunk_size: int = 1024 * 1024) -> str:
    """Compute SHA-256 without loading the full file in memory."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json_file(path: str | Path, *, max_bytes: int = 2 * 1024 * 1024) -> dict[str, Any]:
    """Load a bounded JSON file."""
    file_path = Path(path)

    if file_path.stat().st_size > max_bytes:
        raise BackupManifestError(f"manifest too large: {file_path}")

    try:
        loaded = json.loads(file_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise BackupManifestError(f"invalid JSON manifest: {file_path}") from exc

    if not isinstance(loaded, dict):
        raise BackupManifestError("backup manifest must be a JSON object")

    return loaded


def resolve_backup_path(
    instance_id: str,
    backup_class: str,
    backup_id: str,
) -> Path:
    """Return canonical backup directory path."""
    return backup_dir(
        validate_safe_id(instance_id, field_name="instance_id"),
        validate_safe_id(backup_class, field_name="backup_class"),
        validate_safe_id(backup_id, field_name="backup_id"),
    )


def expected_artifact_paths(instance_id: str, backup_class: str, backup_id: str) -> dict[str, Path]:
    """Return canonical artifact paths for a backup."""
    return {
        BackupArtifactKind.MANIFEST.value: backup_manifest_file(instance_id, backup_class, backup_id),
        BackupArtifactKind.DATABASE.value: backup_database_file(instance_id, backup_class, backup_id),
        BackupArtifactKind.MEDIA.value: backup_media_archive(instance_id, backup_class, backup_id),
        BackupArtifactKind.ENV.value: backup_env_archive(instance_id, backup_class, backup_id),
        BackupArtifactKind.LOGS.value: backup_logs_archive(instance_id, backup_class, backup_id),
    }


def normalize_checksums(manifest: Mapping[str, Any]) -> dict[str, str]:
    """
    Extract checksums from common manifest shapes.

    Supported:
        {"checksums": {"postgres.dump": "sha256..."}}
        {"artifacts": [{"path": "postgres.dump", "sha256": "sha256..."}]}
        {"artifacts": {"database": {"path": "postgres.dump", "sha256": "sha256..."}}}
    """
    checksums: dict[str, str] = {}

    direct = manifest.get("checksums")
    if isinstance(direct, Mapping):
        for key, value in direct.items():
            if value:
                checksums[str(key)] = str(value)

    artifacts = manifest.get("artifacts")
    if isinstance(artifacts, Sequence) and not isinstance(artifacts, (str, bytes, bytearray)):
        for item in artifacts:
            if not isinstance(item, Mapping):
                continue
            path = item.get("path") or item.get("filename") or item.get("name")
            digest = item.get("sha256") or item.get("checksum")
            if path and digest:
                checksums[str(path)] = str(digest)

    if isinstance(artifacts, Mapping):
        for key, item in artifacts.items():
            if not isinstance(item, Mapping):
                continue
            path = item.get("path") or item.get("filename") or key
            digest = item.get("sha256") or item.get("checksum")
            if path and digest:
                checksums[str(path)] = str(digest)

    return checksums


def checksum_for_path(path: Path, checksums: Mapping[str, str]) -> str | None:
    """Find expected checksum for a path using filename or relative-like key."""
    candidates = (
        str(path),
        path.name,
        f"./{path.name}",
    )

    for candidate in candidates:
        if candidate in checksums:
            return checksums[candidate]

    # Allow manifests to store nested relative paths.
    for key, digest in checksums.items():
        if key.endswith(f"/{path.name}"):
            return digest

    return None


def validate_manifest_identity(
    manifest: Mapping[str, Any],
    *,
    instance_id: str,
    backup_class: str,
    backup_id: str,
) -> list[BackupVerificationFinding]:
    """Validate manifest identity fields when present."""
    findings: list[BackupVerificationFinding] = []

    expected = {
        "instance_id": instance_id,
        "backup_class": backup_class,
        "backup_id": backup_id,
    }

    for key, expected_value in expected.items():
        actual = manifest.get(key)
        if actual is not None and str(actual) != expected_value:
            findings.append(
                finding(
                    "manifest_identity_mismatch",
                    f"manifest {key} does not match requested backup",
                    value={"field": key, "expected": expected_value, "actual": actual},
                )
            )

    status = manifest.get("backup_status") or manifest.get("status")
    if status is not None and str(status) in {BackupStatus.FAILED.value, BackupStatus.DELETED.value}:
        findings.append(
            finding(
                "manifest_backup_status_not_usable",
                "backup manifest status indicates the backup is not usable",
                value=status,
            )
        )

    return findings


def detect_forbidden_artifacts(root: Path) -> list[BackupVerificationFinding]:
    """
    Detect forbidden host/system artifacts inside the backup directory.

    Normal Konnaxion backup/restore must not behave like a full host snapshot.
    """
    findings: list[BackupVerificationFinding] = []

    if not root.exists():
        return findings

    for path in root.rglob("*"):
        try:
            relative = path.relative_to(root)
        except ValueError:
            continue

        normalized = str(relative).replace("\\", "/").lower()
        parts = set(normalized.split("/"))

        for forbidden in FORBIDDEN_BACKUP_PATH_PARTS:
            if "/" in forbidden:
                matched = forbidden in normalized
            else:
                matched = forbidden in parts or normalized.endswith(f"/{forbidden}")

            if matched:
                findings.append(
                    finding(
                        "forbidden_backup_artifact",
                        "backup contains forbidden host/system artifact",
                        path=path,
                        value=forbidden,
                    )
                )

    return findings


def inspect_artifact(
    *,
    kind: BackupArtifactKind,
    path: Path,
    required: bool,
    checksums: Mapping[str, str],
    options: BackupVerificationOptions,
) -> tuple[BackupArtifact, list[BackupVerificationFinding]]:
    """Inspect one expected backup artifact."""
    findings: list[BackupVerificationFinding] = []
    exists = path.exists()
    size_bytes = path.stat().st_size if exists and path.is_file() else None
    expected_sha256 = checksum_for_path(path, checksums)
    actual_sha256: str | None = None
    checksum_ok: bool | None = None

    if not exists:
        severity = BackupVerificationSeverity.BLOCK if required else BackupVerificationSeverity.WARN
        findings.append(
            finding(
                "backup_artifact_missing",
                f"backup artifact is missing: {kind.value}",
                severity=severity,
                path=path,
            )
        )
    elif not path.is_file():
        findings.append(
            finding(
                "backup_artifact_not_file",
                f"backup artifact is not a file: {kind.value}",
                path=path,
            )
        )
    elif size_bytes == 0 and required:
        findings.append(
            finding(
                "backup_artifact_empty",
                f"required backup artifact is empty: {kind.value}",
                path=path,
            )
        )

    if exists and path.is_file():
        actual_sha256 = sha256_file(path)

        if expected_sha256:
            checksum_ok = actual_sha256 == expected_sha256
            if not checksum_ok:
                findings.append(
                    finding(
                        "backup_artifact_checksum_mismatch",
                        f"backup artifact checksum mismatch: {kind.value}",
                        path=path,
                        value={"expected": expected_sha256, "actual": actual_sha256},
                    )
                )
        elif options.require_checksums and required and kind != BackupArtifactKind.MANIFEST:
            # The manifest carries the checksum table; requiring a checksum of
            # the manifest inside itself is circular and cannot be satisfied.
            findings.append(
                finding(
                    "backup_artifact_checksum_missing",
                    f"required artifact has no expected checksum: {kind.value}",
                    path=path,
                )
            )
        elif options.require_checksums and not required:
            findings.append(
                finding(
                    "backup_optional_artifact_checksum_missing",
                    f"optional artifact has no expected checksum: {kind.value}",
                    severity=BackupVerificationSeverity.WARN,
                    path=path,
                )
            )

    artifact = BackupArtifact(
        kind=kind.value,
        path=str(path),
        required=required,
        exists=exists,
        size_bytes=size_bytes,
        sha256=actual_sha256,
        expected_sha256=expected_sha256,
        checksum_ok=checksum_ok,
    )

    return artifact, findings


def verify_backup(
    instance_id: str,
    backup_class: str,
    backup_id: str,
    options: BackupVerificationOptions | None = None,
) -> BackupVerificationReport:
    """
    Verify a canonical Konnaxion backup.

    Example:
        report = verify_backup("demo-001", "manual", "demo-001_20260430_230000_manual")
    """
    options = options or BackupVerificationOptions()

    instance_id = validate_safe_id(instance_id, field_name="instance_id")
    backup_class = validate_safe_id(backup_class, field_name="backup_class")
    backup_id = validate_safe_id(backup_id, field_name="backup_id")

    root = resolve_backup_path(instance_id, backup_class, backup_id)
    findings: list[BackupVerificationFinding] = []
    artifacts: list[BackupArtifact] = []
    manifest: dict[str, Any] | None = None
    checksums: dict[str, str] = {}

    if not root.exists():
        findings.append(
            finding(
                "backup_directory_missing",
                "backup directory does not exist",
                path=root,
            )
        )
        accepted, status, backup_status = report_status(findings)
        return BackupVerificationReport(
            accepted=accepted,
            status=status,
            backup_status=backup_status,
            instance_id=instance_id,
            backup_class=backup_class,
            backup_id=backup_id,
            backup_path=str(root),
            verified_at=utc_now_iso(),
            artifacts=tuple(artifacts),
            findings=tuple(findings),
            manifest=manifest,
        )

    if not root.is_dir():
        findings.append(
            finding(
                "backup_path_not_directory",
                "backup path exists but is not a directory",
                path=root,
            )
        )

    manifest_path = backup_manifest_file(instance_id, backup_class, backup_id)

    if not manifest_path.exists():
        findings.append(
            finding(
                "backup_manifest_missing",
                "backup manifest is required",
                path=manifest_path,
            )
        )
    else:
        try:
            manifest = load_json_file(manifest_path, max_bytes=options.max_manifest_bytes)
            checksums = normalize_checksums(manifest)
            findings.extend(
                validate_manifest_identity(
                    manifest,
                    instance_id=instance_id,
                    backup_class=backup_class,
                    backup_id=backup_id,
                )
            )
        except BackupManifestError as exc:
            findings.append(
                finding(
                    "backup_manifest_invalid",
                    str(exc),
                    path=manifest_path,
                )
            )

    artifact_paths = expected_artifact_paths(instance_id, backup_class, backup_id)

    required_kinds = set(DEFAULT_REQUIRED_ARTIFACTS)
    optional_kinds = set(OPTIONAL_ARTIFACTS)

    if not options.allow_missing_env:
        required_kinds.add(BackupArtifactKind.ENV)
        optional_kinds.discard(BackupArtifactKind.ENV)

    if not options.allow_missing_logs:
        required_kinds.add(BackupArtifactKind.LOGS)
        optional_kinds.discard(BackupArtifactKind.LOGS)

    for kind in tuple(DEFAULT_REQUIRED_ARTIFACTS) + tuple(OPTIONAL_ARTIFACTS):
        path = artifact_paths[kind.value]
        required = kind in required_kinds

        artifact, artifact_findings = inspect_artifact(
            kind=kind,
            path=path,
            required=required,
            checksums=checksums,
            options=options,
        )
        artifacts.append(artifact)
        findings.extend(artifact_findings)

    findings.extend(detect_forbidden_artifacts(root))

    accepted, status, backup_status = report_status(findings)

    return BackupVerificationReport(
        accepted=accepted,
        status=status,
        backup_status=backup_status,
        instance_id=instance_id,
        backup_class=backup_class,
        backup_id=backup_id,
        backup_path=str(root),
        verified_at=utc_now_iso(),
        artifacts=tuple(artifacts),
        findings=tuple(findings),
        manifest=manifest,
    )


def verify_backup_at_path(
    backup_path: str | Path,
    *,
    options: BackupVerificationOptions | None = None,
) -> BackupVerificationReport:
    """
    Verify a backup from its canonical path.

    Expected path:
        /opt/konnaxion/backups/<INSTANCE_ID>/<BACKUP_CLASS>/<BACKUP_ID>
    """
    options = options or BackupVerificationOptions()
    path = assert_under_root(backup_path, KX_BACKUPS_ROOT)

    try:
        relative = path.relative_to(Path(KX_BACKUPS_ROOT))
    except ValueError as exc:
        raise BackupVerifyError(f"backup path is outside backup root: {path}") from exc

    parts = relative.parts
    if len(parts) != 3:
        raise BackupVerifyError(
            "backup path must be /opt/konnaxion/backups/<INSTANCE_ID>/<BACKUP_CLASS>/<BACKUP_ID>"
        )

    instance_id, backup_class, backup_id = parts
    return verify_backup(instance_id, backup_class, backup_id, options=options)


def verify_latest_backup(
    instance_id: str,
    backup_class: str | None = None,
    *,
    options: BackupVerificationOptions | None = None,
) -> BackupVerificationReport:
    """
    Verify the latest backup for an instance by lexical directory order.

    Backup ids should include sortable timestamps, for example:
        demo-001_20260430_230000_manual
    """
    instance_id = validate_safe_id(instance_id, field_name="instance_id")
    options = options or BackupVerificationOptions()

    root = backups_instance_root(instance_id)
    if backup_class:
        search_root = root / validate_safe_id(backup_class, field_name="backup_class")
        classes = [search_root]
    else:
        classes = [path for path in root.iterdir() if path.is_dir()] if root.exists() else []

    candidates: list[Path] = []
    for class_dir in classes:
        if class_dir.exists() and class_dir.is_dir():
            candidates.extend(path for path in class_dir.iterdir() if path.is_dir())

    if not candidates:
        requested_class = backup_class or "*"
        raise BackupVerifyError(
            f"no backup candidates found for instance={instance_id} class={requested_class}"
        )

    latest = sorted(candidates, key=lambda item: item.name)[-1]
    return verify_backup_at_path(latest, options=options)


def assert_backup_verified(report: BackupVerificationReport) -> None:
    """Raise BackupVerifyError if a report is not accepted."""
    if not report.accepted:
        messages = "; ".join(item.message for item in report.blocking_findings)
        raise BackupVerifyError(messages or "backup verification failed")


class BackupVerifier:
    """Service object used by Agent actions and tests."""

    def __init__(self, options: BackupVerificationOptions | None = None) -> None:
        self.options = options or BackupVerificationOptions()

    def verify(
        self,
        instance_id: str,
        backup_class: str,
        backup_id: str,
    ) -> BackupVerificationReport:
        return verify_backup(instance_id, backup_class, backup_id, options=self.options)

    def verify_path(self, backup_path: str | Path) -> BackupVerificationReport:
        return verify_backup_at_path(backup_path, options=self.options)

    def verify_latest(
        self,
        instance_id: str,
        backup_class: str | None = None,
    ) -> BackupVerificationReport:
        return verify_latest_backup(instance_id, backup_class, options=self.options)


__all__ = [
    "BackupArtifact",
    "BackupArtifactKind",
    "BackupManifestError",
    "BackupVerificationFinding",
    "BackupVerificationOptions",
    "BackupVerificationReport",
    "BackupVerificationSeverity",
    "BackupVerificationStatus",
    "BackupVerifier",
    "BackupVerifyError",
    "DEFAULT_REQUIRED_ARTIFACTS",
    "FORBIDDEN_BACKUP_PATH_PARTS",
    "OPTIONAL_ARTIFACTS",
    "assert_backup_verified",
    "checksum_for_path",
    "detect_forbidden_artifacts",
    "expected_artifact_paths",
    "finding",
    "inspect_artifact",
    "load_json_file",
    "normalize_checksums",
    "report_status",
    "resolve_backup_path",
    "sha256_file",
    "utc_now_iso",
    "validate_manifest_identity",
    "verify_backup",
    "verify_backup_at_path",
    "verify_latest_backup",
]
