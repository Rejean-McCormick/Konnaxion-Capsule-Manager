"""
Konnaxion restore workflow orchestration.

Konnaxion restore protects application data, not the host. A restore rebuilds
a Konnaxion Instance into a trusted runtime and must pass the Security Gate
before it is considered usable.

This module intentionally avoids arbitrary shell execution. It coordinates
restore steps through injected, allowlisted operations supplied by runtime,
backup, migration, Security Gate, and healthcheck modules.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence

from kx_shared.errors import (
    BackupNotFoundError,
    FileMissingError,
    HealthcheckError,
    KonnaxionError,
    MigrationError,
    RestoreApplyError,
    RestoreError,
    RestorePreflightError,
    SecurityGateBlockingError,
    UnsafePathError,
    as_error_payload,
)
from kx_shared.konnaxion_constants import (
    KX_BACKUPS_ROOT,
    RestoreStatus,
    instance_backup_root,
    instance_root,
)


# ---------------------------------------------------------------------
# Canonical restore files
# ---------------------------------------------------------------------


BACKUP_MANIFEST_FILE = "backup-manifest.json"
BACKUP_CHECKSUMS_FILE = "checksums.txt"
POSTGRES_DUMP_FILE = "postgres.dump"
MEDIA_ARCHIVE_FILE = "media.tar.zst"
ENV_SNAPSHOT_FILE = "env.tar.zst"
RESTORE_REPORT_FILE = "restore-report.json"


FORBIDDEN_RESTORE_PATH_PREFIXES = (
    "/tmp",
    "/dev",
    "/proc",
    "/sys",
    "/run",
    "/var/run/docker.sock",
    "/etc/sudoers",
    "/etc/cron",
    "/root/.ssh",
    "/home",
)


# ---------------------------------------------------------------------
# Restore DTOs
# ---------------------------------------------------------------------


class RestoreMode(StrEnum):
    """Supported restore modes."""

    SAME_INSTANCE = "same_instance"
    NEW_INSTANCE = "new_instance"


class RestoreStep(StrEnum):
    """Internal restore step names used in reports and audit logs."""

    PLAN = "plan"
    PREFLIGHT = "preflight"
    VERIFY_BACKUP = "verify_backup"
    CREATE_PRE_RESTORE_BACKUP = "create_pre_restore_backup"
    STOP_TARGET = "stop_target"
    RESTORE_DATABASE = "restore_database"
    RESTORE_MEDIA = "restore_media"
    RESTORE_ENV = "restore_env"
    RUN_MIGRATIONS = "run_migrations"
    RUN_SECURITY_GATE = "run_security_gate"
    RUN_HEALTHCHECKS = "run_healthchecks"
    MARK_RESTORED = "mark_restored"
    ROLLBACK_FAILED_RESTORE = "rollback_failed_restore"
    WRITE_REPORT = "write_report"


@dataclass(slots=True, frozen=True)
class BackupArtifactPaths:
    """Canonical backup artifact paths."""

    backup_dir: Path
    manifest: Path
    checksums: Path
    postgres_dump: Path
    media_archive: Path
    env_snapshot: Path | None = None

    @classmethod
    def from_backup_dir(cls, backup_dir: str | Path) -> "BackupArtifactPaths":
        root = Path(backup_dir)
        env_snapshot = root / ENV_SNAPSHOT_FILE
        return cls(
            backup_dir=root,
            manifest=root / BACKUP_MANIFEST_FILE,
            checksums=root / BACKUP_CHECKSUMS_FILE,
            postgres_dump=root / POSTGRES_DUMP_FILE,
            media_archive=root / MEDIA_ARCHIVE_FILE,
            env_snapshot=env_snapshot if env_snapshot.exists() else None,
        )

    def required_files(self, *, include_media: bool = True) -> tuple[Path, ...]:
        # kx-backup-manifest-v1 stores canonical artifact checksums inside the
        # JSON manifest.  ``checksums.txt`` is a legacy optional artifact and
        # must not make a valid current backup unrestorable.
        files = [self.manifest, self.postgres_dump]
        if include_media:
            files.append(self.media_archive)
        return tuple(files)


@dataclass(slots=True, frozen=True)
class RestorePlan:
    """A concrete restore request.

    For safer disaster recovery, prefer ``mode=RestoreMode.NEW_INSTANCE``.
    Same-instance restore is supported but requires a pre-restore backup by
    default.
    """

    source_backup_id: str
    source_backup_dir: Path
    target_instance_id: str
    mode: RestoreMode = RestoreMode.NEW_INSTANCE
    current_instance_id: str | None = None
    new_instance_id: str | None = None

    network_profile: str | None = None
    capsule_id: str | None = None
    capsule_version: str | None = None

    require_verified_backup: bool = True
    create_pre_restore_backup: bool = True
    restore_media: bool = True
    restore_env_snapshot: bool = False

    run_migrations: bool = True
    run_security_gate: bool = True
    run_healthchecks: bool = True

    allow_overwrite: bool = False
    dry_run: bool = False

    requested_by: str | None = None
    restore_id: str = field(default_factory=lambda: datetime.now(UTC).strftime("%Y%m%d_%H%M%S_restore"))

    def effective_target_instance_id(self) -> str:
        if self.mode == RestoreMode.NEW_INSTANCE and self.new_instance_id:
            return self.new_instance_id
        return self.target_instance_id

    def artifact_paths(self) -> BackupArtifactPaths:
        return BackupArtifactPaths.from_backup_dir(self.source_backup_dir)


@dataclass(slots=True, frozen=True)
class RestoreStepResult:
    """One restore step result."""

    step: RestoreStep
    status: RestoreStatus
    message: str
    details: Mapping[str, Any] = field(default_factory=dict)
    started_at: str = field(default_factory=lambda: now_iso())
    finished_at: str = field(default_factory=lambda: now_iso())

    def to_dict(self) -> dict[str, Any]:
        return {
            "step": self.step.value,
            "status": self.status.value,
            "message": self.message,
            "details": dict(self.details),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


@dataclass(slots=True, frozen=True)
class RestoreReport:
    """Complete restore report written after every restore attempt."""

    restore_id: str
    source_backup_id: str
    source_backup_dir: str
    target_instance_id: str
    mode: str
    status: RestoreStatus
    steps: Sequence[RestoreStepResult]
    started_at: str
    finished_at: str
    error: Mapping[str, Any] | None = None
    pre_restore_backup_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "restore_id": self.restore_id,
            "source_backup_id": self.source_backup_id,
            "source_backup_dir": self.source_backup_dir,
            "target_instance_id": self.target_instance_id,
            "mode": self.mode,
            "status": self.status.value,
            "steps": [step.to_dict() for step in self.steps],
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": dict(self.error) if self.error is not None else None,
            "pre_restore_backup_id": self.pre_restore_backup_id,
        }


@dataclass(slots=True, frozen=True)
class RestorePreflightResult:
    """Preflight validation result."""

    plan: RestorePlan
    artifacts: BackupArtifactPaths
    manifest: Mapping[str, Any]
    warnings: Sequence[str] = field(default_factory=tuple)


# ---------------------------------------------------------------------
# Operation protocols
# ---------------------------------------------------------------------


class RestoreOperations(Protocol):
    """Injected allowlisted operations used by RestoreRunner."""

    def verify_backup(self, plan: RestorePlan, artifacts: BackupArtifactPaths) -> Mapping[str, Any]:
        ...

    def create_pre_restore_backup(self, plan: RestorePlan) -> str:
        ...

    def stop_target_instance(self, plan: RestorePlan) -> Mapping[str, Any]:
        ...

    def restore_database(self, plan: RestorePlan, artifacts: BackupArtifactPaths) -> Mapping[str, Any]:
        ...

    def restore_media(self, plan: RestorePlan, artifacts: BackupArtifactPaths) -> Mapping[str, Any]:
        ...

    def restore_env_snapshot(self, plan: RestorePlan, artifacts: BackupArtifactPaths) -> Mapping[str, Any]:
        ...

    def run_migrations(self, plan: RestorePlan) -> Mapping[str, Any]:
        ...

    def run_security_gate(self, plan: RestorePlan) -> Mapping[str, Any]:
        ...

    def run_healthchecks(self, plan: RestorePlan) -> Mapping[str, Any]:
        ...

    def rollback_failed_restore(
        self,
        plan: RestorePlan,
        *,
        pre_restore_backup_id: str | None,
        error: BaseException,
    ) -> Mapping[str, Any]:
        ...

    def mark_restore_status(self, plan: RestorePlan, status: RestoreStatus) -> None:
        ...

    def audit(self, plan: RestorePlan, step: RestoreStep, message: str, details: Mapping[str, Any]) -> None:
        ...


Operation = Callable[..., Mapping[str, Any]]


@dataclass(slots=True)
class RestoreOperationSet:
    """Default operation set.

    Every mutating operation must be injected by the Agent runtime layer. This
    default class fails closed so unfinished integrations cannot accidentally
    perform partial restores.
    """

    verify_backup_func: Callable[[RestorePlan, BackupArtifactPaths], Mapping[str, Any]] | None = None
    create_pre_restore_backup_func: Callable[[RestorePlan], str] | None = None
    stop_target_instance_func: Callable[[RestorePlan], Mapping[str, Any]] | None = None
    restore_database_func: Callable[[RestorePlan, BackupArtifactPaths], Mapping[str, Any]] | None = None
    restore_media_func: Callable[[RestorePlan, BackupArtifactPaths], Mapping[str, Any]] | None = None
    restore_env_snapshot_func: Callable[[RestorePlan, BackupArtifactPaths], Mapping[str, Any]] | None = None
    run_migrations_func: Callable[[RestorePlan], Mapping[str, Any]] | None = None
    run_security_gate_func: Callable[[RestorePlan], Mapping[str, Any]] | None = None
    run_healthchecks_func: Callable[[RestorePlan], Mapping[str, Any]] | None = None
    rollback_failed_restore_func: Callable[..., Mapping[str, Any]] | None = None
    mark_restore_status_func: Callable[[RestorePlan, RestoreStatus], None] | None = None
    audit_func: Callable[[RestorePlan, RestoreStep, str, Mapping[str, Any]], None] | None = None

    def verify_backup(self, plan: RestorePlan, artifacts: BackupArtifactPaths) -> Mapping[str, Any]:
        if self.verify_backup_func:
            return self.verify_backup_func(plan, artifacts)
        return {"verified": True, "note": "No external verifier configured; preflight file checks only."}

    def create_pre_restore_backup(self, plan: RestorePlan) -> str:
        if not self.create_pre_restore_backup_func:
            raise RestorePreflightError(
                "Pre-restore backup operation is not configured.",
                {"target_instance_id": plan.effective_target_instance_id()},
            )
        return self.create_pre_restore_backup_func(plan)

    def stop_target_instance(self, plan: RestorePlan) -> Mapping[str, Any]:
        if self.stop_target_instance_func:
            return self.stop_target_instance_func(plan)
        return {"stopped": False, "note": "No stop operation configured."}

    def restore_database(self, plan: RestorePlan, artifacts: BackupArtifactPaths) -> Mapping[str, Any]:
        if not self.restore_database_func:
            raise RestoreApplyError(
                "Database restore operation is not configured.",
                {"postgres_dump": str(artifacts.postgres_dump)},
            )
        return self.restore_database_func(plan, artifacts)

    def restore_media(self, plan: RestorePlan, artifacts: BackupArtifactPaths) -> Mapping[str, Any]:
        if not self.restore_media_func:
            raise RestoreApplyError(
                "Media restore operation is not configured.",
                {"media_archive": str(artifacts.media_archive)},
            )
        return self.restore_media_func(plan, artifacts)

    def restore_env_snapshot(self, plan: RestorePlan, artifacts: BackupArtifactPaths) -> Mapping[str, Any]:
        if not self.restore_env_snapshot_func:
            raise RestoreApplyError(
                "Env snapshot restore operation is not configured.",
                {"env_snapshot": str(artifacts.env_snapshot) if artifacts.env_snapshot else None},
            )
        return self.restore_env_snapshot_func(plan, artifacts)

    def run_migrations(self, plan: RestorePlan) -> Mapping[str, Any]:
        if not self.run_migrations_func:
            raise MigrationError(
                "Migration operation is not configured.",
                {"target_instance_id": plan.effective_target_instance_id()},
            )
        return self.run_migrations_func(plan)

    def run_security_gate(self, plan: RestorePlan) -> Mapping[str, Any]:
        if not self.run_security_gate_func:
            raise SecurityGateBlockingError(
                "Security Gate operation is not configured.",
                {"target_instance_id": plan.effective_target_instance_id()},
            )
        return self.run_security_gate_func(plan)

    def run_healthchecks(self, plan: RestorePlan) -> Mapping[str, Any]:
        if not self.run_healthchecks_func:
            raise HealthcheckError(
                "Healthcheck operation is not configured.",
                {"target_instance_id": plan.effective_target_instance_id()},
            )
        return self.run_healthchecks_func(plan)

    def rollback_failed_restore(
        self,
        plan: RestorePlan,
        *,
        pre_restore_backup_id: str | None,
        error: BaseException,
    ) -> Mapping[str, Any]:
        if self.rollback_failed_restore_func:
            return self.rollback_failed_restore_func(
                plan,
                pre_restore_backup_id=pre_restore_backup_id,
                error=error,
            )
        return {"rolled_back": False, "note": "No rollback operation configured."}

    def mark_restore_status(self, plan: RestorePlan, status: RestoreStatus) -> None:
        if self.mark_restore_status_func:
            self.mark_restore_status_func(plan, status)

    def audit(self, plan: RestorePlan, step: RestoreStep, message: str, details: Mapping[str, Any]) -> None:
        if self.audit_func:
            self.audit_func(plan, step, message, details)


# ---------------------------------------------------------------------
# Restore runner
# ---------------------------------------------------------------------


class RestoreRunner:
    """Execute a Konnaxion restore plan through allowlisted operations."""

    def __init__(
        self,
        operations: RestoreOperations | None = None,
        *,
        report_root: Path | None = None,
    ) -> None:
        self.operations = operations or RestoreOperationSet()
        self.report_root = report_root

    def preflight(self, plan: RestorePlan) -> RestorePreflightResult:
        validate_restore_plan(plan)

        artifacts = plan.artifact_paths()
        validate_backup_artifacts(artifacts, include_media=plan.restore_media)

        manifest = read_backup_manifest(artifacts.manifest)
        warnings = validate_backup_manifest_for_restore(plan, manifest)

        if plan.require_verified_backup:
            verification = self.operations.verify_backup(plan, artifacts)
            if verification.get("verified") is False:
                raise RestorePreflightError(
                    "Backup verification failed.",
                    {"verification": dict(verification)},
                )

        return RestorePreflightResult(
            plan=plan,
            artifacts=artifacts,
            manifest=manifest,
            warnings=tuple(warnings),
        )

    def run(self, plan: RestorePlan) -> RestoreReport:
        started_at = now_iso()
        steps: list[RestoreStepResult] = []
        pre_restore_backup_id: str | None = None

        def step_ok(
            step: RestoreStep,
            status: RestoreStatus,
            message: str,
            details: Mapping[str, Any] | None = None,
        ) -> None:
            result = RestoreStepResult(
                step=step,
                status=status,
                message=message,
                details=details or {},
            )
            steps.append(result)
            self.operations.audit(plan, step, message, details or {})

        try:
            self.operations.mark_restore_status(plan, RestoreStatus.PLANNED)
            step_ok(RestoreStep.PLAN, RestoreStatus.PLANNED, "Restore plan accepted.")

            self.operations.mark_restore_status(plan, RestoreStatus.PREFLIGHT)
            preflight = self.preflight(plan)
            step_ok(
                RestoreStep.PREFLIGHT,
                RestoreStatus.PREFLIGHT,
                "Restore preflight passed.",
                {
                    "warnings": list(preflight.warnings),
                    "backup_dir": str(preflight.artifacts.backup_dir),
                },
            )

            if plan.dry_run:
                report = RestoreReport(
                    restore_id=plan.restore_id,
                    source_backup_id=plan.source_backup_id,
                    source_backup_dir=str(plan.source_backup_dir),
                    target_instance_id=plan.effective_target_instance_id(),
                    mode=plan.mode.value,
                    status=RestoreStatus.PLANNED,
                    steps=tuple(steps),
                    started_at=started_at,
                    finished_at=now_iso(),
                    pre_restore_backup_id=pre_restore_backup_id,
                )
                self.write_report(report)
                return report

            if plan.create_pre_restore_backup and plan.mode == RestoreMode.SAME_INSTANCE:
                self.operations.mark_restore_status(plan, RestoreStatus.CREATING_PRE_RESTORE_BACKUP)
                pre_restore_backup_id = self.operations.create_pre_restore_backup(plan)
                step_ok(
                    RestoreStep.CREATE_PRE_RESTORE_BACKUP,
                    RestoreStatus.CREATING_PRE_RESTORE_BACKUP,
                    "Pre-restore backup created.",
                    {"pre_restore_backup_id": pre_restore_backup_id},
                )

            stop_result = self.operations.stop_target_instance(plan)
            step_ok(
                RestoreStep.STOP_TARGET,
                RestoreStatus.PREFLIGHT,
                "Target instance stop step completed.",
                stop_result,
            )

            self.operations.mark_restore_status(plan, RestoreStatus.RESTORING_DATABASE)
            db_result = self.operations.restore_database(plan, preflight.artifacts)
            step_ok(
                RestoreStep.RESTORE_DATABASE,
                RestoreStatus.RESTORING_DATABASE,
                "Database restore completed.",
                db_result,
            )

            if plan.restore_media:
                self.operations.mark_restore_status(plan, RestoreStatus.RESTORING_MEDIA)
                media_result = self.operations.restore_media(plan, preflight.artifacts)
                step_ok(
                    RestoreStep.RESTORE_MEDIA,
                    RestoreStatus.RESTORING_MEDIA,
                    "Media restore completed.",
                    media_result,
                )

            if plan.restore_env_snapshot:
                env_result = self.operations.restore_env_snapshot(plan, preflight.artifacts)
                step_ok(
                    RestoreStep.RESTORE_ENV,
                    RestoreStatus.RESTORING_MEDIA,
                    "Environment snapshot restore completed.",
                    env_result,
                )

            if plan.run_migrations:
                self.operations.mark_restore_status(plan, RestoreStatus.RUNNING_MIGRATIONS)
                migration_result = self.operations.run_migrations(plan)
                step_ok(
                    RestoreStep.RUN_MIGRATIONS,
                    RestoreStatus.RUNNING_MIGRATIONS,
                    "Migrations completed.",
                    migration_result,
                )

            if plan.run_security_gate:
                self.operations.mark_restore_status(plan, RestoreStatus.RUNNING_SECURITY_GATE)
                security_result = self.operations.run_security_gate(plan)
                if security_result.get("status") == "FAIL_BLOCKING":
                    raise SecurityGateBlockingError(
                        "Security Gate blocked restored instance.",
                        {"security_gate": dict(security_result)},
                    )
                step_ok(
                    RestoreStep.RUN_SECURITY_GATE,
                    RestoreStatus.RUNNING_SECURITY_GATE,
                    "Security Gate passed.",
                    security_result,
                )

            if plan.run_healthchecks:
                self.operations.mark_restore_status(plan, RestoreStatus.RUNNING_HEALTHCHECKS)
                health_result = self.operations.run_healthchecks(plan)
                if health_result.get("healthy") is False:
                    raise HealthcheckError(
                        "Restored instance healthchecks failed.",
                        {"healthchecks": dict(health_result)},
                    )
                step_ok(
                    RestoreStep.RUN_HEALTHCHECKS,
                    RestoreStatus.RUNNING_HEALTHCHECKS,
                    "Healthchecks passed.",
                    health_result,
                )

            self.operations.mark_restore_status(plan, RestoreStatus.RESTORED)
            step_ok(
                RestoreStep.MARK_RESTORED,
                RestoreStatus.RESTORED,
                "Restore completed.",
                {"target_instance_id": plan.effective_target_instance_id()},
            )

            report = RestoreReport(
                restore_id=plan.restore_id,
                source_backup_id=plan.source_backup_id,
                source_backup_dir=str(plan.source_backup_dir),
                target_instance_id=plan.effective_target_instance_id(),
                mode=plan.mode.value,
                status=RestoreStatus.RESTORED,
                steps=tuple(steps),
                started_at=started_at,
                finished_at=now_iso(),
                pre_restore_backup_id=pre_restore_backup_id,
            )
            self.write_report(report)
            return report

        except BaseException as exc:
            error_payload = as_error_payload(exc)

            try:
                rollback_result = self.operations.rollback_failed_restore(
                    plan,
                    pre_restore_backup_id=pre_restore_backup_id,
                    error=exc,
                )
                self.operations.mark_restore_status(plan, RestoreStatus.ROLLED_BACK)
                steps.append(
                    RestoreStepResult(
                        step=RestoreStep.ROLLBACK_FAILED_RESTORE,
                        status=RestoreStatus.ROLLED_BACK,
                        message="Failed restore rollback step completed.",
                        details=rollback_result,
                    )
                )
                final_status = RestoreStatus.ROLLED_BACK
            except Exception as rollback_exc:
                steps.append(
                    RestoreStepResult(
                        step=RestoreStep.ROLLBACK_FAILED_RESTORE,
                        status=RestoreStatus.FAILED,
                        message="Failed restore rollback step failed.",
                        details={"rollback_error": as_error_payload(rollback_exc)},
                    )
                )
                self.operations.mark_restore_status(plan, RestoreStatus.FAILED)
                final_status = RestoreStatus.FAILED

            report = RestoreReport(
                restore_id=plan.restore_id,
                source_backup_id=plan.source_backup_id,
                source_backup_dir=str(plan.source_backup_dir),
                target_instance_id=plan.effective_target_instance_id(),
                mode=plan.mode.value,
                status=final_status,
                steps=tuple(steps),
                started_at=started_at,
                finished_at=now_iso(),
                error=error_payload,
                pre_restore_backup_id=pre_restore_backup_id,
            )
            self.write_report(report)
            return report

    def write_report(self, report: RestoreReport) -> Path | None:
        target_root = self.report_root
        if target_root is None:
            target_root = instance_root(report.target_instance_id) / "state"

        target_root.mkdir(parents=True, exist_ok=True)
        report_path = target_root / RESTORE_REPORT_FILE
        report_path.write_text(
            json.dumps(report.to_dict(), indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return report_path


# ---------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------


def validate_restore_plan(plan: RestorePlan) -> None:
    if not plan.source_backup_id.strip():
        raise RestorePreflightError(
            "Source backup ID is required.",
            {"field": "source_backup_id"},
        )

    if not plan.target_instance_id.strip():
        raise RestorePreflightError(
            "Target instance ID is required.",
            {"field": "target_instance_id"},
        )

    if plan.mode == RestoreMode.NEW_INSTANCE and not plan.effective_target_instance_id().strip():
        raise RestorePreflightError(
            "New instance restore requires a target instance ID.",
            {"mode": plan.mode.value},
        )

    if plan.mode == RestoreMode.SAME_INSTANCE and not plan.create_pre_restore_backup:
        raise RestorePreflightError(
            "Same-instance restore requires a pre-restore backup.",
            {"target_instance_id": plan.target_instance_id},
        )

    validate_backup_source_path(plan.source_backup_dir)

    target = instance_root(plan.effective_target_instance_id())
    if target.exists() and plan.mode == RestoreMode.NEW_INSTANCE and not plan.allow_overwrite:
        raise RestorePreflightError(
            "Target instance already exists; refusing to overwrite.",
            {"target_instance_id": plan.effective_target_instance_id(), "path": str(target)},
        )


def validate_backup_source_path(source_backup_dir: Path) -> None:
    resolved = source_backup_dir.resolve()

    if any(str(resolved).startswith(prefix) for prefix in FORBIDDEN_RESTORE_PATH_PREFIXES):
        raise UnsafePathError(
            "Backup source path is forbidden for restore.",
            {"source_backup_dir": str(resolved)},
        )

    backups_root = Path(KX_BACKUPS_ROOT).resolve()
    if not str(resolved).startswith(str(backups_root)):
        raise UnsafePathError(
            "Backup source must be under the canonical Konnaxion backup root.",
            {
                "backup_root": str(backups_root),
                "source_backup_dir": str(resolved),
            },
        )

    if not resolved.exists():
        raise BackupNotFoundError(
            "Backup source directory does not exist.",
            {"source_backup_dir": str(resolved)},
        )

    if not resolved.is_dir():
        raise BackupNotFoundError(
            "Backup source is not a directory.",
            {"source_backup_dir": str(resolved)},
        )


def validate_backup_artifacts(
    artifacts: BackupArtifactPaths,
    *,
    include_media: bool = True,
) -> None:
    missing = [
        str(path)
        for path in artifacts.required_files(include_media=include_media)
        if not path.exists()
    ]

    if missing:
        raise FileMissingError(
            "Backup is missing required restore artifacts.",
            {"missing": missing, "backup_dir": str(artifacts.backup_dir)},
        )


def read_backup_manifest(path: Path) -> Mapping[str, Any]:
    if not path.exists():
        raise FileMissingError(
            "Backup manifest is missing.",
            {"path": str(path)},
        )

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RestorePreflightError(
            "Backup manifest is not valid JSON.",
            {"path": str(path), "error": str(exc)},
        ) from exc

    if not isinstance(payload, Mapping):
        raise RestorePreflightError(
            "Backup manifest must be a JSON object.",
            {"path": str(path)},
        )

    return payload


def validate_backup_manifest_for_restore(
    plan: RestorePlan,
    manifest: Mapping[str, Any],
) -> list[str]:
    warnings: list[str] = []

    manifest_backup_id = str(manifest.get("backup_id", ""))
    if manifest_backup_id and manifest_backup_id != plan.source_backup_id:
        raise RestorePreflightError(
            "Backup manifest ID does not match requested source backup ID.",
            {
                "requested_backup_id": plan.source_backup_id,
                "manifest_backup_id": manifest_backup_id,
            },
        )

    source_instance_id = str(manifest.get("instance_id", ""))
    if plan.mode == RestoreMode.SAME_INSTANCE and source_instance_id:
        if source_instance_id != plan.target_instance_id:
            warnings.append(
                "Same-instance restore target differs from source instance recorded in manifest."
            )

    if manifest.get("contains_host_snapshot") is True:
        raise RestorePreflightError(
            "Host snapshots are not valid Konnaxion restore sources.",
            {"backup_id": plan.source_backup_id},
        )

    if manifest.get("contains_docker_daemon_state") is True:
        raise RestorePreflightError(
            "Docker daemon state must not be restored.",
            {"backup_id": plan.source_backup_id},
        )

    if manifest.get("contains_system_files") is True:
        raise RestorePreflightError(
            "System files must not be restored by Konnaxion restore.",
            {"backup_id": plan.source_backup_id},
        )

    status = str(manifest.get("status", ""))
    if plan.require_verified_backup and status and status != "verified":
        raise RestorePreflightError(
            "Backup manifest is not marked verified.",
            {"backup_id": plan.source_backup_id, "status": status},
        )

    return warnings


# ---------------------------------------------------------------------
# Concrete isolated test-restore
# ---------------------------------------------------------------------


def _command_payload(result: Any) -> dict[str, Any]:
    """Return a compact serializable command result without leaking env data."""

    return {
        "returncode": int(getattr(result, "returncode", 1)),
        "stdout": str(getattr(result, "stdout", "") or "")[-4000:],
        "stderr": str(getattr(result, "stderr", "") or "")[-4000:],
        "timed_out": bool(getattr(result, "timed_out", False)),
    }


def _raise_command_failure(label: str, result: Any) -> None:
    if bool(getattr(result, "ok", False)):
        return
    detail = str(getattr(result, "stderr", "") or getattr(result, "stdout", "") or "").strip()
    raise RestoreApplyError(
        f"{label} failed.",
        {"returncode": int(getattr(result, "returncode", 1)), "detail": detail[-4000:]},
    )


def _capsule_id_for_test_restore(
    manifest: Mapping[str, Any],
    *,
    source_instance_id: str,
) -> str:
    """Resolve the trusted capsule ID from backup/source evidence."""

    candidates: list[Any] = []
    security_gate = manifest.get("security_gate")
    if isinstance(security_gate, Mapping):
        candidates.append(security_gate.get("capsule_id"))

    instance = manifest.get("instance")
    if isinstance(instance, Mapping):
        capsule = instance.get("capsule")
        if isinstance(capsule, Mapping):
            candidates.append(capsule.get("capsule_id"))
        candidates.append(instance.get("capsule_id"))

    try:
        from kx_shared.paths import instance_security_gate_file

        gate_file = instance_security_gate_file(source_instance_id)
        if gate_file.is_file():
            gate = json.loads(gate_file.read_text(encoding="utf-8"))
            if isinstance(gate, Mapping):
                candidates.append(gate.get("capsule_id"))
    except Exception:
        pass

    for value in candidates:
        text = str(value or "").strip()
        if text:
            from kx_shared.paths import validate_safe_id

            return validate_safe_id(text, field_name="capsule_id")

    raise RestorePreflightError(
        "Could not resolve capsule ID for isolated test restore.",
        {"source_instance_id": source_instance_id},
    )


def _restore_media_archive(archive_path: Path, target_media_dir: Path) -> dict[str, Any]:
    """Safely extract the canonical zstd media archive into a fresh target."""

    import shutil
    import tarfile
    import zstandard as zstd

    target_media_dir.mkdir(parents=True, exist_ok=True)
    extracted_files = 0
    extracted_bytes = 0

    with archive_path.open("rb") as raw:
        with zstd.ZstdDecompressor().stream_reader(raw) as reader:
            with tarfile.open(fileobj=reader, mode="r|") as archive:
                for member in archive:
                    name = member.name.replace("\\", "/").lstrip("/")
                    parts = [part for part in name.split("/") if part not in {"", "."}]
                    if parts and parts[0] == "media":
                        parts = parts[1:]
                    if not parts:
                        continue
                    if any(part == ".." for part in parts):
                        raise UnsafePathError(
                            "Media archive contains path traversal.",
                            {"member": member.name},
                        )
                    if member.issym() or member.islnk() or member.isdev():
                        raise UnsafePathError(
                            "Media archive contains unsupported link/device entry.",
                            {"member": member.name},
                        )

                    destination = target_media_dir.joinpath(*parts)
                    resolved = destination.resolve()
                    media_root = target_media_dir.resolve()
                    if resolved != media_root and media_root not in resolved.parents:
                        raise UnsafePathError(
                            "Media archive member escapes target media root.",
                            {"member": member.name},
                        )

                    if member.isdir():
                        destination.mkdir(parents=True, exist_ok=True)
                        continue
                    if not member.isfile():
                        continue

                    destination.parent.mkdir(parents=True, exist_ok=True)
                    source = archive.extractfile(member)
                    if source is None:
                        raise RestoreApplyError(
                            "Could not read media archive member.",
                            {"member": member.name},
                        )
                    with source, destination.open("wb") as output:
                        shutil.copyfileobj(source, output, length=1024 * 1024)
                    extracted_files += 1
                    extracted_bytes += int(member.size or 0)

    return {"files": extracted_files, "bytes": extracted_bytes}


def _security_gate_for_test_instance(instance_id: str, capsule_id: str) -> dict[str, Any]:
    """Run the canonical Security Gate against the isolated restored instance."""

    from kx_agent.runtime.compose import read_compose_file, security_context_inputs
    from kx_agent.security.evidence import collect_runtime_security_evidence
    from kx_agent.security.gate import context_from_compose, is_security_gate_passing, run_security_gate
    from kx_shared.paths import instance_compose_file

    compose_file = instance_compose_file(instance_id)
    compose = read_compose_file(compose_file)
    inputs = security_context_inputs(
        instance_id=instance_id,
        compose=compose,
        compose_file=compose_file,
        capsule_id=capsule_id,
    )
    manifest = dict(inputs.get("manifest") or {})
    env = dict(inputs.get("env") or {})
    loaded_compose = dict(inputs.get("compose") or compose)
    evidence = collect_runtime_security_evidence(
        instance_id=instance_id,
        capsule_id=capsule_id,
        compose=loaded_compose,
        manifest=manifest,
        env=env,
    )
    context = context_from_compose(
        instance_id=instance_id,
        compose=loaded_compose,
        manifest=manifest,
        env=env,
        capsule_signature_verified=evidence.capsule_signature_verified,
        image_checksums_verified=evidence.image_checksums_verified,
        firewall_enabled=evidence.firewall_enabled,
        backup_configured=evidence.backup_configured,
        admin_surface_private=evidence.admin_surface_private,
        postgres_public=evidence.postgres_public,
        redis_public=evidence.redis_public,
        allowed_images=evidence.allowed_images,
    )
    report = run_security_gate(context)
    payload = report.to_dict() if hasattr(report, "to_dict") else dict(report)
    payload["passed"] = bool(is_security_gate_passing(report))
    return payload


def test_restore_backup(
    *,
    instance_id: str,
    backup_id: str,
    target_instance_id: str,
    new_instance_id: str | None = None,
    backup_class: str = "manual",
    backup_dir: str | Path | None = None,
    dry_run: bool = False,
    stop_after_test: bool = True,
    **_: Any,
) -> dict[str, Any]:
    """Restore a verified backup into a fresh, non-public temporary instance.

    The source instance is never stopped or mutated.  The target is rendered as
    ``local_only`` with no published HTTP/HTTPS ports, receives fresh runtime
    secrets, restores the PostgreSQL dump and media archive, runs migrations,
    starts the application, then runs Django checks, container health and the
    canonical Security Gate.  Successful tests are stopped by default while the
    isolated filesystem is retained for operator inspection/removal.
    """

    from kx_agent.backups.verify import verify_backup
    from kx_agent.runtime.compose import ComposeRenderOptions, write_runtime_compose
    from kx_agent.runtime.docker import ContainerHealth, runtime_for_instance
    from kx_shared.konnaxion_constants import DockerService
    from kx_shared.paths import instance_media_dir, validate_safe_id

    source_instance_id = validate_safe_id(instance_id, field_name="instance_id")
    requested_target = new_instance_id or target_instance_id
    test_instance_id = validate_safe_id(requested_target, field_name="target_instance_id")
    backup_id = validate_safe_id(backup_id, field_name="backup_id")
    backup_class = validate_safe_id(backup_class, field_name="backup_class")

    if test_instance_id == source_instance_id:
        raise RestorePreflightError(
            "Test restore target must differ from the source instance.",
            {"instance_id": source_instance_id, "target_instance_id": test_instance_id},
        )

    source_dir = Path(backup_dir) if backup_dir is not None else instance_backup_root(source_instance_id) / backup_class / backup_id
    plan = make_restore_new_plan(
        source_backup_id=backup_id,
        source_instance_id=source_instance_id,
        new_instance_id=test_instance_id,
        backup_class=backup_class,
        backup_dir=source_dir,
        network_profile="local_only",
        create_pre_restore_backup=False,
        restore_env_snapshot=False,
        dry_run=bool(dry_run),
    )

    verification = verify_backup(source_instance_id, backup_class, backup_id)
    verification_data = verification.to_dict() if hasattr(verification, "to_dict") else dict(verification)
    if not bool(verification_data.get("accepted", False)):
        raise RestorePreflightError(
            "Backup verification failed before test restore.",
            {"backup_id": backup_id, "verification": verification_data},
        )

    preflight_ops = RestoreOperationSet(
        verify_backup_func=lambda _plan, _artifacts: {"verified": True, "report": verification_data}
    )
    preflight = RestoreRunner(preflight_ops).preflight(plan)
    capsule_id = _capsule_id_for_test_restore(preflight.manifest, source_instance_id=source_instance_id)

    if dry_run:
        return {
            "ok": True,
            "status": "PLANNED",
            "message": "Backup test restore preflight passed.",
            "source_instance_id": source_instance_id,
            "target_instance_id": test_instance_id,
            "backup_id": backup_id,
            "backup_class": backup_class,
            "capsule_id": capsule_id,
            "network_profile": "local_only",
            "verified": True,
            "dry_run": True,
        }

    target_root = instance_root(test_instance_id)
    if target_root.exists():
        raise RestorePreflightError(
            "Test restore target instance already exists; refusing to overwrite.",
            {"target_instance_id": test_instance_id, "path": str(target_root)},
        )

    runtime = None
    created = False
    result: dict[str, Any] = {
        "ok": False,
        "status": "FAILED",
        "source_instance_id": source_instance_id,
        "target_instance_id": test_instance_id,
        "backup_id": backup_id,
        "backup_class": backup_class,
        "capsule_id": capsule_id,
        "network_profile": "local_only",
        "verified": True,
        "steps": [],
    }

    try:
        compose_result = write_runtime_compose(
            ComposeRenderOptions(
                instance_id=test_instance_id,
                host="127.0.0.1",
                capsule_id=capsule_id,
                network_profile="local_only",
                exposure_mode="private",
                public_mode_enabled=False,
                bind_http=False,
                bind_https=False,
                ensure_env_files=True,
                overwrite_env_files=False,
            )
        )
        created = True
        result["compose_file"] = compose_result.compose_file
        result["services"] = list(compose_result.services)
        result["steps"].append({"step": "create_target", "status": "PASS"})

        runtime = runtime_for_instance(test_instance_id)
        runtime.validate_available()
        infra = runtime.up(
            detach=True,
            services=[DockerService.POSTGRES.value, DockerService.REDIS.value],
            no_build=True,
            force_recreate=True,
            wait=True,
        )
        _raise_command_failure("Temporary database/cache start", infra)
        result["steps"].append({"step": "start_infrastructure", "status": "PASS"})

        user_result = runtime.exec(DockerService.POSTGRES.value, ["printenv", "POSTGRES_USER"])
        db_result = runtime.exec(DockerService.POSTGRES.value, ["printenv", "POSTGRES_DB"])
        _raise_command_failure("Read temporary PostgreSQL user", user_result)
        _raise_command_failure("Read temporary PostgreSQL database", db_result)
        postgres_user = (user_result.stdout or "").strip() or "konnaxion"
        postgres_db = (db_result.stdout or "").strip() or "konnaxion"

        db_restore = runtime.exec_from_file(
            DockerService.POSTGRES.value,
            [
                "pg_restore",
                "-U",
                postgres_user,
                "-d",
                postgres_db,
                "--no-owner",
                "--no-acl",
                "--exit-on-error",
                "--single-transaction",
            ],
            preflight.artifacts.postgres_dump,
            timeout_seconds=1800,
        )
        _raise_command_failure("Temporary PostgreSQL restore", db_restore)
        result["steps"].append({"step": "restore_database", "status": "PASS"})

        media_result = _restore_media_archive(preflight.artifacts.media_archive, instance_media_dir(test_instance_id))
        result["media"] = media_result
        result["steps"].append({"step": "restore_media", "status": "PASS", **media_result})

        migration = runtime.migrate_django()
        _raise_command_failure("Temporary Django migrations", migration)
        result["steps"].append({"step": "migrations", "status": "PASS"})

        app_start = runtime.up(
            detach=True,
            no_build=True,
            force_recreate=True,
            wait=False,
        )
        _raise_command_failure("Temporary application start", app_start)
        health = runtime.wait_for_healthy(
            required_services=compose_result.services,
            attempts=60,
            interval_seconds=2.0,
        )
        not_ready_health = {
            name: value.value if isinstance(value, ContainerHealth) else str(value)
            for name, value in health.items()
            if value not in {ContainerHealth.HEALTHY, ContainerHealth.NONE, ContainerHealth.UNKNOWN}
        }
        statuses = runtime.ps()
        not_running = [item.service for item in statuses if (item.state or "").lower() != "running"]
        expected = set(compose_result.services)
        seen = {item.service for item in statuses}
        missing_services = sorted(expected - seen)
        if not_ready_health or not_running or missing_services:
            raise HealthcheckError(
                "Temporary restored instance did not become healthy.",
                {
                    "not_ready_health": not_ready_health,
                    "not_running": not_running,
                    "missing_services": missing_services,
                },
            )
        result["health"] = {
            name: value.value if isinstance(value, ContainerHealth) else str(value)
            for name, value in health.items()
        }
        result["steps"].append({"step": "health", "status": "PASS"})

        django_check = runtime.exec(DockerService.DJANGO_API.value, ["python", "manage.py", "check"])
        _raise_command_failure("Temporary Django check", django_check)
        result["steps"].append({"step": "django_check", "status": "PASS"})

        worlds_probe = runtime.exec(
            DockerService.POSTGRES.value,
            [
                "psql",
                "-U",
                postgres_user,
                "-d",
                postgres_db,
                "-Atqc",
                "SELECT CASE WHEN to_regclass('public.worlds_world') IS NULL THEN '-1' ELSE (SELECT count(*)::text FROM worlds_world) END;",
            ],
        )
        if worlds_probe.ok:
            raw_worlds = (worlds_probe.stdout or "").strip()
            try:
                result["worlds_count"] = int(raw_worlds)
            except ValueError:
                result["worlds_count"] = None

        security = _security_gate_for_test_instance(test_instance_id, capsule_id)
        result["security_gate"] = security
        if not bool(security.get("passed", False)):
            raise SecurityGateBlockingError(
                "Security Gate blocked temporary restored instance.",
                {"security_gate": security},
            )
        result["steps"].append({"step": "security_gate", "status": "PASS", "gate_status": security.get("status")})

        result.update(
            {
                "ok": True,
                "status": "PASS",
                "message": "Backup test restore completed successfully in an isolated local-only instance.",
            }
        )
        return result

    except BaseException as exc:
        result["error"] = as_error_payload(exc)
        result["message"] = str(exc)
        return result
    finally:
        if runtime is not None and created and stop_after_test:
            try:
                stopped = runtime.down(remove_orphans=True, volumes=False)
                result["stopped_after_test"] = bool(stopped.ok)
                result["stop_result"] = _command_payload(stopped)
            except Exception as cleanup_exc:
                result["stopped_after_test"] = False
                result["cleanup_error"] = as_error_payload(cleanup_exc)


# ---------------------------------------------------------------------
# Convenience functions
# ---------------------------------------------------------------------


def restore_instance(
    plan: RestorePlan,
    *,
    operations: RestoreOperations | None = None,
    report_root: Path | None = None,
) -> RestoreReport:
    """Run a restore plan with the provided operation set."""

    return RestoreRunner(operations, report_root=report_root).run(plan)


def make_restore_new_plan(
    *,
    source_backup_id: str,
    source_instance_id: str,
    new_instance_id: str,
    backup_class: str = "manual",
    backup_dir: Path | None = None,
    requested_by: str | None = None,
    **overrides: Any,
) -> RestorePlan:
    """Create a safer restore-new plan."""

    source_dir = backup_dir or instance_backup_root(source_instance_id) / backup_class / source_backup_id

    return RestorePlan(
        source_backup_id=source_backup_id,
        source_backup_dir=source_dir,
        target_instance_id=new_instance_id,
        current_instance_id=source_instance_id,
        new_instance_id=new_instance_id,
        mode=RestoreMode.NEW_INSTANCE,
        requested_by=requested_by,
        **overrides,
    )


def make_same_instance_restore_plan(
    *,
    source_backup_id: str,
    instance_id: str,
    backup_class: str = "manual",
    backup_dir: Path | None = None,
    requested_by: str | None = None,
    **overrides: Any,
) -> RestorePlan:
    """Create a same-instance restore plan.

    Same-instance restore keeps ``create_pre_restore_backup=True`` unless the
    caller explicitly overrides it, and validation will reject disabling it.
    """

    source_dir = backup_dir or instance_backup_root(instance_id) / backup_class / source_backup_id

    return RestorePlan(
        source_backup_id=source_backup_id,
        source_backup_dir=source_dir,
        target_instance_id=instance_id,
        current_instance_id=instance_id,
        mode=RestoreMode.SAME_INSTANCE,
        create_pre_restore_backup=True,
        requested_by=requested_by,
        **overrides,
    )


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


__all__ = [
    "BACKUP_CHECKSUMS_FILE",
    "BACKUP_MANIFEST_FILE",
    "ENV_SNAPSHOT_FILE",
    "FORBIDDEN_RESTORE_PATH_PREFIXES",
    "MEDIA_ARCHIVE_FILE",
    "POSTGRES_DUMP_FILE",
    "RESTORE_REPORT_FILE",
    "BackupArtifactPaths",
    "RestoreMode",
    "RestoreOperationSet",
    "RestoreOperations",
    "RestorePlan",
    "RestorePreflightResult",
    "RestoreReport",
    "RestoreRunner",
    "RestoreStep",
    "RestoreStepResult",
    "make_restore_new_plan",
    "make_same_instance_restore_plan",
    "test_restore_backup",
    "now_iso",
    "read_backup_manifest",
    "restore_instance",
    "validate_backup_artifacts",
    "validate_backup_manifest_for_restore",
    "validate_backup_source_path",
    "validate_restore_plan",
]
