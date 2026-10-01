from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import kx_agent.runtime.migrations as migrations
import kx_manager.services.deploy as deploy
from kx_agent.api import InstanceStartRequest, model_payload
from kx_agent.runtime.healthchecks import HTTP_ROUTE_CHECKS


def _ok_result(*, stdout: str = "") -> migrations.MigrationResult:
    now = datetime.now(timezone.utc)
    return migrations.MigrationResult(
        status=migrations.MigrationStatus.SUCCEEDED,
        command=("docker", "compose", "run", "--rm", "django-api", "python", "manage.py", "migrate"),
        returncode=0,
        stdout=stdout,
        stderr="",
        started_at=now,
        finished_at=now,
    )


def test_instance_start_contract_accepts_readiness_and_fresh_repair_flags() -> None:
    request = InstanceStartRequest.model_validate(
        {
            "instance_id": "konnaxion-prod",
            "run_security_gate": True,
            "run_readiness_checks": True,
            "repair_fresh_schema_drift": True,
        }
    )
    payload = model_payload(request)
    assert payload["run_readiness_checks"] is True
    assert payload["repair_fresh_schema_drift"] is True


def test_healthchecks_probe_schema_backed_world_catalog_routes() -> None:
    paths = {item.route_path for item in HTTP_ROUTE_CHECKS}
    assert "/api/control/universes/" in paths
    assert "/api/control/worlds/" in paths


def test_fresh_schema_drift_is_repaired_then_verified(monkeypatch, tmp_path: Path) -> None:
    compose = tmp_path / "docker-compose.runtime.yml"
    compose.write_text("services: {}", encoding="utf-8")
    monkeypatch.setattr(migrations, "reconcile_postgres_credentials", lambda *args, **kwargs: {})

    calls: list[str] = []

    def fake_run(command, *, environment=None):
        del environment
        calls.append("schema" if "shell" in command.argv else "migrate")
        return _ok_result()

    probes = iter(
        [
            {
                "ok": False,
                "missing_tables": {"worlds": ["worlds_universe", "worlds_world"]},
                "repairable_apps": ["worlds"],
                "repaired_apps": [],
                "blocked_repairs": {},
            },
            {
                "ok": False,
                "missing_tables": {"worlds": ["worlds_universe", "worlds_world"]},
                "repairable_apps": ["worlds"],
                "repaired_apps": ["worlds"],
                "blocked_repairs": {},
            },
            {
                "ok": True,
                "missing_tables": {},
                "repairable_apps": [],
                "repaired_apps": [],
                "blocked_repairs": {},
            },
        ]
    )
    monkeypatch.setattr(migrations, "run_migration_command", fake_run)
    monkeypatch.setattr(migrations, "inspect_schema_integrity", lambda *args, **kwargs: next(probes))

    result = migrations.run_django_migrations(
        "konnaxion-prod",
        compose_file=compose,
        project_name="kx-konnaxion-prod",
        raise_on_failure=False,
        repair_fresh_schema_drift=True,
    )
    assert result.ok is True
    assert result.schema_integrity["repair_attempted"] is True
    assert result.schema_integrity["repaired_apps"] == ["worlds"]
    assert calls == ["migrate", "migrate"]


def test_update_schema_drift_fails_without_repair(monkeypatch, tmp_path: Path) -> None:
    compose = tmp_path / "docker-compose.runtime.yml"
    compose.write_text("services: {}", encoding="utf-8")
    monkeypatch.setattr(migrations, "reconcile_postgres_credentials", lambda *args, **kwargs: {})
    monkeypatch.setattr(migrations, "run_migration_command", lambda *args, **kwargs: _ok_result())
    monkeypatch.setattr(
        migrations,
        "inspect_schema_integrity",
        lambda *args, **kwargs: {
            "ok": False,
            "missing_tables": {"worlds": ["worlds_universe"]},
            "repairable_apps": ["worlds"],
            "repaired_apps": [],
            "blocked_repairs": {},
        },
    )

    result = migrations.run_django_migrations(
        "konnaxion-prod",
        compose_file=compose,
        project_name="kx-konnaxion-prod",
        raise_on_failure=False,
        repair_fresh_schema_drift=False,
    )
    assert result.ok is False
    assert result.returncode == 3
    assert "worlds_universe" in result.stderr


def test_manager_start_payload_enables_readiness_and_only_repairs_fresh(monkeypatch) -> None:
    seen: list[dict[str, object]] = []

    class Client:
        def start_instance(self, **payload):
            seen.append(payload)
            return {"ok": True}

    result = deploy.DeployResult(ok=False, action="x", instance_id="demo", message="")
    fresh = SimpleNamespace(
        instance_id="demo",
        run_security_gate=True,
        update_existing=False,
        start=True,
        capsule_id="cap",
        capsule_version="1",
        client=Client(),
    )
    monkeypatch.setattr(deploy, "_call_backend_step", lambda request, result, **kwargs: seen.append(kwargs["payload"]))
    monkeypatch.setattr(deploy, "_active_capsule_payload", lambda *args, **kwargs: {})

    deploy._start_instance(fresh, result, remote=True)
    assert seen[-1]["run_readiness_checks"] is True
    assert seen[-1]["repair_fresh_schema_drift"] is True

    seen.clear()
    update = SimpleNamespace(
        instance_id="demo",
        run_security_gate=True,
        update_existing=True,
        start=True,
        capsule_id="cap",
        capsule_version="2",
        client=Client(),
    )
    deploy._start_instance(update, result, remote=True)
    assert seen[-1]["repair_fresh_schema_drift"] is False


def test_schema_integrity_probe_uses_canonical_host_schemas() -> None:
    script = migrations._schema_integrity_script(repair=False)
    assert "'ekoh': 'ekoh_smartvote'" in script
    assert "'smart_vote': 'ekoh_smartvote'" in script
    assert "pg_catalog.pg_class" in script
    assert "schema_overrides.get(app, \"public\")" in script
    assert "actual = set(connection.introspection.table_names())" not in script


def test_schema_integrity_probe_keeps_world_schemas_out_of_host_matching() -> None:
    script = migrations._schema_integrity_script(repair=True)
    assert "actual_by_schema.get(schema_name, set())" in script
    assert "schema_by_app[app]" in script
    assert "n.nspname NOT LIKE 'pg_%'" in script
