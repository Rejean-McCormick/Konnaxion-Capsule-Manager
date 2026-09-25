from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from kx_agent import actions
from kx_agent.backups.verify import (
    BackupArtifactKind,
    BackupVerificationOptions,
    inspect_artifact,
)


def test_default_registry_registers_instance_backup() -> None:
    registry = actions.make_default_registry()
    assert registry.get(actions.AgentActionName.INSTANCE_BACKUP) is actions.handle_instance_backup


def test_instance_backup_handler_delegates_to_verified_executor(monkeypatch) -> None:
    import kx_agent.backups.executor as executor

    observed = {}

    class Result:
        verified = True

        def to_dict(self):
            return {
                "instance_id": "konnaxion-prod",
                "backup_class": "pre_update",
                "backup_id": "konnaxion-prod_20260924_210000_pre_update",
                "backup_path": "/opt/konnaxion/backups/x",
                "verified": True,
                "verification": {"accepted": True},
            }

    def fake_create(instance_id, backup_class, *, verify_after_create):
        observed.update(
            instance_id=instance_id,
            backup_class=backup_class,
            verify_after_create=verify_after_create,
        )
        return Result()

    monkeypatch.setattr(executor, "create_verified_instance_backup", fake_create)

    request = actions.ActionRequest(
        action=actions.AgentActionName.INSTANCE_BACKUP.value,
        params={
            "instance_id": "konnaxion-prod",
            "backup_class": "pre_update",
            "verify_after_create": True,
        },
    )
    result = actions.handle_instance_backup(request)

    assert observed == {
        "instance_id": "konnaxion-prod",
        "backup_class": "pre_update",
        "verify_after_create": True,
    }
    assert result.status == actions.ActionStatus.SUCCEEDED
    assert result.data["backup_id"].endswith("_pre_update")
    assert result.data["backup_status"] == "verified"
    assert "state" not in result.data

    api_payload = actions.action_result_to_api_dict(result)
    assert api_payload["state"] is None
    assert api_payload["data"]["backup_status"] == "verified"


def test_manifest_does_not_require_self_checksum(tmp_path: Path) -> None:
    manifest = tmp_path / "backup-manifest.json"
    manifest.write_text('{"backup_id":"x"}', encoding="utf-8")

    artifact, findings = inspect_artifact(
        kind=BackupArtifactKind.MANIFEST,
        path=manifest,
        required=True,
        checksums={},
        options=BackupVerificationOptions(require_checksums=True),
    )

    assert artifact.exists is True
    assert not any(item.code == "backup_artifact_checksum_missing" for item in findings)


def test_backup_executor_creates_database_media_env_and_verified_manifest(tmp_path: Path, monkeypatch) -> None:
    import json
    import kx_agent.backups.executor as executor

    root = tmp_path / "backup"
    media_source = tmp_path / "media"
    env_source = tmp_path / "env"
    logs_source = tmp_path / "logs"
    media_source.mkdir()
    env_source.mkdir()
    logs_source.mkdir()
    (env_source / "django.env").write_text("SECRET=value\nSAFE_FLAG=1\n", encoding="utf-8")

    monkeypatch.setattr(executor, "generate_backup_id", lambda instance_id, backup_class: "backup-001")
    monkeypatch.setattr(executor, "backup_dir", lambda *args: root)
    monkeypatch.setattr(executor, "backup_database_file", lambda *args: root / "postgres.dump")
    monkeypatch.setattr(executor, "backup_media_archive", lambda *args: root / "media.tar.zst")
    monkeypatch.setattr(executor, "backup_env_archive", lambda *args: root / "env.tar.zst")
    monkeypatch.setattr(executor, "backup_logs_archive", lambda *args: root / "logs.tar.zst")
    monkeypatch.setattr(executor, "backup_manifest_file", lambda *args: root / "backup-manifest.json")
    monkeypatch.setattr(executor, "instance_media_dir", lambda instance_id: media_source)
    monkeypatch.setattr(executor, "instance_env_dir", lambda instance_id: env_source)
    monkeypatch.setattr(executor, "instance_logs_dir", lambda instance_id: logs_source)
    monkeypatch.setattr(executor, "instance_manifest_file", lambda instance_id: tmp_path / "missing-instance.json")
    monkeypatch.setattr(executor, "instance_state_file", lambda instance_id: tmp_path / "missing-state.json")
    monkeypatch.setattr(executor, "instance_security_gate_file", lambda instance_id: tmp_path / "missing-security.json")

    class Runtime:
        def validate_available(self):
            return None

        def exec(self, service, command):
            value = "dbuser\n" if command[-1] == "POSTGRES_USER" else "dbname\n"
            return SimpleNamespace(ok=True, stdout=value, stderr="")

        def exec_to_file(self, service, command, destination, **kwargs):
            Path(destination).write_bytes(b"PGDMP-custom")
            assert "--format=custom" in command
            return SimpleNamespace(ok=True, stdout="", stderr="")

    monkeypatch.setattr(executor, "runtime_for_instance", lambda instance_id: Runtime())
    monkeypatch.setattr(
        executor,
        "_tar_zstd_directory",
        lambda source, destination, arcname: Path(destination).write_bytes(("archive:" + arcname).encode()),
    )
    monkeypatch.setattr(
        executor,
        "_tar_zstd_json",
        lambda payload, destination, name: Path(destination).write_bytes(json.dumps(payload).encode()),
    )

    class Report:
        accepted = True
        blocking_findings = ()
        verified_at = "2026-09-24T21:00:00+00:00"
        status = "PASS"

        def to_dict(self):
            return {"accepted": True, "status": "PASS"}

    monkeypatch.setattr(executor, "verify_backup", lambda *args, **kwargs: Report())

    result = executor.create_verified_instance_backup(
        "konnaxion-prod",
        "pre_update",
        verify_after_create=True,
    )

    manifest = json.loads((root / "backup-manifest.json").read_text(encoding="utf-8"))
    assert result.verified is True
    assert manifest["status"] == "verified"
    assert manifest["security"]["secrets_included"] is False
    assert set(manifest["checksums"]) == {
        "postgres.dump",
        "media.tar.zst",
        "env.tar.zst",
        "logs.tar.zst",
    }
    assert (root / "postgres.dump").read_bytes() == b"PGDMP-custom"
