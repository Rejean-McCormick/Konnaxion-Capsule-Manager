from __future__ import annotations

import json
from pathlib import Path

import pytest

from kx_agent.backups import restore
from kx_agent.runtime.docker import DockerRuntime, DockerRuntimeConfig


class _VerifyReport:
    def to_dict(self):
        return {"accepted": True, "status": "PASS", "backup_status": "verified"}


def _make_backup(root: Path, instance_id: str, backup_id: str) -> Path:
    backup_dir = root / instance_id / "manual" / backup_id
    backup_dir.mkdir(parents=True)
    (backup_dir / "postgres.dump").write_bytes(b"PGDMP-test")
    (backup_dir / "media.tar.zst").write_bytes(b"placeholder")
    (backup_dir / "backup-manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "kx-backup-manifest-v1",
                "backup_id": backup_id,
                "instance_id": instance_id,
                "backup_class": "manual",
                "status": "verified",
                "security_gate": {"capsule_id": "konnaxion-v14-release-2026.09.24"},
                "checksums": {"postgres.dump": "x", "media.tar.zst": "y"},
            }
        ),
        encoding="utf-8",
    )
    return backup_dir


def test_current_backup_manifest_does_not_require_legacy_checksums_txt(tmp_path: Path) -> None:
    backup_dir = _make_backup(tmp_path, "source-001", "source-001_20260925_manual")
    artifacts = restore.BackupArtifactPaths.from_backup_dir(backup_dir)
    restore.validate_backup_artifacts(artifacts)


def test_test_restore_dry_run_accepts_current_verified_backup(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    instance_id = "source-001"
    backup_id = "source-001_20260925_manual"
    backup_dir = _make_backup(tmp_path, instance_id, backup_id)

    monkeypatch.setattr(restore, "KX_BACKUPS_ROOT", tmp_path)
    monkeypatch.setattr(restore, "FORBIDDEN_RESTORE_PATH_PREFIXES", ())
    monkeypatch.setattr("kx_agent.backups.verify.verify_backup", lambda *a, **k: _VerifyReport())

    result = restore.test_restore_backup(
        instance_id=instance_id,
        backup_id=backup_id,
        target_instance_id="restore-test-001",
        new_instance_id="restore-test-001",
        backup_class="manual",
        backup_dir=backup_dir,
        dry_run=True,
    )

    assert result["ok"] is True
    assert result["status"] == "PLANNED"
    assert result["target_instance_id"] == "restore-test-001"
    assert result["network_profile"] == "local_only"
    assert result["capsule_id"] == "konnaxion-v14-release-2026.09.24"


def test_exec_from_file_streams_binary_stdin(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    compose = tmp_path / "compose.yml"
    compose.write_text("services: {}\n", encoding="utf-8")
    source = tmp_path / "postgres.dump"
    source.write_bytes(b"PGDMP\x00binary")

    monkeypatch.setattr("kx_agent.runtime.docker.shutil.which", lambda name: "/usr/bin/docker" if name == "docker" else None)

    class _Probe:
        returncode = 0
        stdout = "Docker Compose version"
        stderr = ""

    monkeypatch.setattr("kx_agent.runtime.docker.subprocess.run", lambda *a, **k: _Probe())
    runtime = DockerRuntime(
        DockerRuntimeConfig(
            compose_file=compose,
            project_name="kx-test",
            working_dir=tmp_path,
            compose_binary=None,
            allow_outside_kx_root=True,
        )
    )

    captured = {}

    class _Proc:
        returncode = 0
        stdout = b"ok"
        stderr = b""

    def fake_run(args, **kwargs):
        captured["args"] = args
        captured["stdin"] = kwargs["stdin"].read()
        return _Proc()

    monkeypatch.setattr("kx_agent.runtime.docker.subprocess.run", fake_run)

    result = runtime.exec_from_file("postgres", ["pg_restore", "-U", "kx"], source)

    assert result.ok is True
    assert captured["stdin"] == b"PGDMP\x00binary"
    assert "pg_restore" in captured["args"]
