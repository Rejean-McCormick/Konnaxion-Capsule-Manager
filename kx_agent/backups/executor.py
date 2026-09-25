"""Concrete, fail-closed backup execution for Konnaxion Agent.

This module turns the declarative backup contract into the narrow runtime
operations needed by ``instance.backup``.  It deliberately keeps secrets out
of backup artifacts and streams the PostgreSQL custom dump directly to disk.
"""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import tarfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

from kx_agent.backups.backup import generate_backup_id
from kx_agent.backups.verify import BackupVerificationOptions, verify_backup
from kx_agent.runtime.docker import runtime_for_instance
from kx_shared.konnaxion_constants import DockerService
from kx_shared.paths import (
    backup_database_file,
    backup_dir,
    backup_env_archive,
    backup_logs_archive,
    backup_manifest_file,
    backup_media_archive,
    instance_env_dir,
    instance_logs_dir,
    instance_manifest_file,
    instance_media_dir,
    instance_security_gate_file,
    instance_state_file,
    validate_safe_id,
)


@dataclass(frozen=True)
class BackupExecutionResult:
    instance_id: str
    backup_class: str
    backup_id: str
    backup_path: str
    verified: bool
    verification: Mapping[str, Any] | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "instance_id": self.instance_id,
            "backup_class": self.backup_class,
            "backup_id": self.backup_id,
            "backup_path": self.backup_path,
            "verified": self.verified,
            "verification": dict(self.verification or {}),
        }


def _utc_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return value if isinstance(value, dict) else None


def _redacted_env_metadata(env_root: Path) -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    if env_root.is_dir():
        for path in sorted(env_root.glob("*.env")):
            keys: list[str] = []
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                lines = []
            for raw in lines:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key = line.split("=", 1)[0].strip()
                if key and key.replace("_", "").isalnum():
                    keys.append(key)
            files.append({"file": path.name, "keys": sorted(set(keys))})
    return {
        "schema_version": "kx-redacted-env-v1",
        "secrets_included": False,
        "files": files,
    }


def _tar_zstd_directory(source: Path, destination: Path, *, arcname: str) -> None:
    """Write a zstd-compressed tar without following symlinks."""

    import zstandard as zstd

    destination.parent.mkdir(parents=True, exist_ok=True)

    def safe_filter(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
        if info.issym() or info.islnk():
            return None
        # Portable backup metadata; avoid preserving host ownership identities.
        info.uid = 0
        info.gid = 0
        info.uname = ""
        info.gname = ""
        return info

    with destination.open("wb") as raw:
        compressor = zstd.ZstdCompressor(level=6)
        with compressor.stream_writer(raw, closefd=False) as compressed:
            with tarfile.open(fileobj=compressed, mode="w|") as archive:
                if source.exists():
                    archive.add(source, arcname=arcname, recursive=True, filter=safe_filter)
                else:
                    info = tarfile.TarInfo(name=arcname.rstrip("/") + "/")
                    info.type = tarfile.DIRTYPE
                    info.mode = 0o750
                    info.mtime = int(datetime.now(UTC).timestamp())
                    archive.addfile(info)


def _tar_zstd_json(payload: Mapping[str, Any], destination: Path, *, name: str) -> None:
    import zstandard as zstd

    content = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("wb") as raw:
        compressor = zstd.ZstdCompressor(level=6)
        with compressor.stream_writer(raw, closefd=False) as compressed:
            with tarfile.open(fileobj=compressed, mode="w|") as archive:
                info = tarfile.TarInfo(name=name)
                info.size = len(content)
                info.mode = 0o600
                info.mtime = int(datetime.now(UTC).timestamp())
                archive.addfile(info, io.BytesIO(content))


def _postgres_identity(runtime: Any) -> tuple[str, str]:
    service = DockerService.POSTGRES.value
    user_result = runtime.exec(service, ["printenv", "POSTGRES_USER"])
    db_result = runtime.exec(service, ["printenv", "POSTGRES_DB"])
    user = (user_result.stdout or "").strip() if user_result.ok else ""
    database = (db_result.stdout or "").strip() if db_result.ok else ""
    return user or "konnaxion", database or "konnaxion"


def create_verified_instance_backup(
    instance_id: str,
    backup_class: str = "manual",
    *,
    verify_after_create: bool = True,
) -> BackupExecutionResult:
    """Create one canonical application-data backup for an existing instance."""

    instance_id = validate_safe_id(instance_id, field_name="instance_id")
    backup_class = validate_safe_id(backup_class, field_name="backup_class")
    backup_id = generate_backup_id(instance_id, backup_class)
    root = backup_dir(instance_id, backup_class, backup_id)
    root.mkdir(parents=True, exist_ok=False)

    database_file = backup_database_file(instance_id, backup_class, backup_id)
    media_file = backup_media_archive(instance_id, backup_class, backup_id)
    env_file = backup_env_archive(instance_id, backup_class, backup_id)
    logs_file = backup_logs_archive(instance_id, backup_class, backup_id)
    manifest_file = backup_manifest_file(instance_id, backup_class, backup_id)

    try:
        runtime = runtime_for_instance(instance_id)
        runtime.validate_available()

        postgres_user, postgres_db = _postgres_identity(runtime)
        dump = runtime.exec_to_file(
            DockerService.POSTGRES.value,
            [
                "pg_dump",
                "-U",
                postgres_user,
                "-d",
                postgres_db,
                "--format=custom",
                "--no-owner",
                "--no-acl",
            ],
            database_file,
            timeout_seconds=1800,
        )
        if not dump.ok or not database_file.is_file() or database_file.stat().st_size == 0:
            detail = dump.stderr.strip() or "pg_dump did not produce a usable database artifact"
            raise RuntimeError(f"PostgreSQL backup failed: {detail}")

        _tar_zstd_directory(instance_media_dir(instance_id), media_file, arcname="media")
        _tar_zstd_json(
            _redacted_env_metadata(instance_env_dir(instance_id)),
            env_file,
            name="env-metadata.json",
        )
        _tar_zstd_directory(instance_logs_dir(instance_id), logs_file, arcname="logs")

        checksums = {
            path.name: _sha256(path)
            for path in (database_file, media_file, env_file, logs_file)
        }

        manifest: dict[str, Any] = {
            "schema_version": "kx-backup-manifest-v1",
            "backup_id": backup_id,
            "instance_id": instance_id,
            "backup_class": backup_class,
            "status": "created",
            "created_at": _utc_iso(),
            "database": {
                "engine": "postgres",
                "dump_format": "custom",
                "logical_backup": True,
            },
            "security": {
                "secrets_included": False,
                "full_disk_backup": False,
                "contains_tmp": False,
                "contains_crontabs": False,
                "contains_authorized_keys": False,
                "contains_sudoers": False,
                "contains_docker_socket": False,
            },
            "checksums": checksums,
            "instance": _safe_json(instance_manifest_file(instance_id)),
            "instance_state": _safe_json(instance_state_file(instance_id)),
            "security_gate": _safe_json(instance_security_gate_file(instance_id)),
        }
        manifest_file.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")

        verification_data: dict[str, Any] | None = None
        verified = False
        if verify_after_create:
            report = verify_backup(
                instance_id,
                backup_class,
                backup_id,
                options=BackupVerificationOptions(
                    require_checksums=True,
                    allow_missing_env=False,
                    allow_missing_logs=True,
                ),
            )
            verification_data = report.to_dict()
            if not report.accepted:
                messages = "; ".join(item.message for item in report.blocking_findings)
                raise RuntimeError(messages or "Backup verification failed")
            verified = True
            manifest["status"] = "verified"
            manifest["verification"] = {
                "backup_verified": True,
                "verified_at": report.verified_at,
                "status": report.status,
            }
            manifest_file.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
            final_report = verify_backup(
                instance_id,
                backup_class,
                backup_id,
                options=BackupVerificationOptions(
                    require_checksums=True,
                    allow_missing_env=False,
                    allow_missing_logs=True,
                ),
            )
            if not final_report.accepted:
                messages = "; ".join(item.message for item in final_report.blocking_findings)
                raise RuntimeError(messages or "Final backup verification failed")
            verification_data = final_report.to_dict()

        return BackupExecutionResult(
            instance_id=instance_id,
            backup_class=backup_class,
            backup_id=backup_id,
            backup_path=str(root),
            verified=verified,
            verification=verification_data,
        )
    except Exception:
        shutil.rmtree(root, ignore_errors=True)
        raise


__all__ = ["BackupExecutionResult", "create_verified_instance_backup"]
