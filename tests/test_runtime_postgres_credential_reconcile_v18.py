from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from urllib.parse import unquote, urlparse

from kx_agent.runtime import migrations


def _prepare_env(monkeypatch, tmp_path: Path, *, postgres_secret: str, django_secret: str, runtime_secret: str):
    from kx_agent.instances import secrets as secret_module

    monkeypatch.setattr(secret_module, "instance_env_dir", lambda _instance_id: tmp_path)
    monkeypatch.setattr(
        secret_module,
        "mirror_env_files_for_state_relative_compose",
        lambda *a, **k: {},
    )

    secret_module.write_env_file_atomic(
        tmp_path / secret_module.POSTGRES_ENV_FILE,
        {
            "POSTGRES_USER": "konnaxion",
            "POSTGRES_DB": "konnaxion",
            "POSTGRES_HOST": "postgres",
            "POSTGRES_PORT": "5432",
            "POSTGRES_PASSWORD": postgres_secret,
            "DATABASE_URL": secret_module.build_database_url(
                user="konnaxion",
                password="stale-postgres-url-secret-1234567890",
                host="postgres",
                port="5432",
                database="konnaxion",
            ),
        },
    )
    secret_module.write_env_file_atomic(
        tmp_path / secret_module.DJANGO_ENV_FILE,
        {
            "DJANGO_SECRET_KEY": "django-key",
            "DATABASE_URL": secret_module.build_database_url(
                user="konnaxion",
                password=django_secret,
                host="postgres",
                port="5432",
                database="konnaxion",
            ),
        },
    )
    secret_module.write_env_file_atomic(
        tmp_path / secret_module.RUNTIME_ENV_FILE,
        {
            "POSTGRES_USER": "konnaxion",
            "POSTGRES_DB": "konnaxion",
            "POSTGRES_PASSWORD": runtime_secret,
            "DATABASE_URL": secret_module.build_database_url(
                user="konnaxion",
                password=runtime_secret,
                host="postgres",
                port="5432",
                database="konnaxion",
            ),
        },
    )
    return secret_module


def test_reconcile_uses_postgres_env_and_synchronizes_django_runtime(monkeypatch, tmp_path):
    postgres_secret = "postgres-source-of-truth-1234567890"
    django_secret = "stale-django-password-1234567890"
    runtime_secret = "stale-runtime-password-1234567890"

    secret_module = _prepare_env(
        monkeypatch,
        tmp_path,
        postgres_secret=postgres_secret,
        django_secret=django_secret,
        runtime_secret=runtime_secret,
    )

    monkeypatch.setattr(
        migrations,
        "build_migration_plan",
        lambda *a, **k: SimpleNamespace(
            project_name="konnaxion-konnaxion-prod",
            compose_file=Path("/opt/konnaxion/instances/konnaxion-prod/state/docker-compose.runtime.yml"),
        ),
    )

    calls = []

    def fake_run(argv, **kwargs):
        calls.append((list(argv), dict(kwargs)))
        if "pg_isready" in argv:
            return SimpleNamespace(returncode=0, stdout="accepting connections", stderr="")
        if "psql" in argv:
            joined = " ".join(argv)
            assert postgres_secret not in joined
            assert django_secret not in joined
            assert runtime_secret not in joined
            assert postgres_secret in kwargs.get("input", "")
            assert django_secret not in kwargs.get("input", "")
            assert runtime_secret not in kwargs.get("input", "")
            return SimpleNamespace(returncode=0, stdout="ALTER ROLE\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(migrations.subprocess, "run", fake_run)

    result = migrations.reconcile_postgres_credentials("konnaxion-prod")

    assert result["ok"] is True
    assert result["password_rotated"] is True
    assert result["env_synchronized"] is True
    assert set(result["env_files_rewritten"]) == {"postgres.env", "django.env", "runtime.env"}

    django_env = secret_module.read_env_file(tmp_path / secret_module.DJANGO_ENV_FILE)
    runtime_env = secret_module.read_env_file(tmp_path / secret_module.RUNTIME_ENV_FILE)
    postgres_env = secret_module.read_env_file(tmp_path / secret_module.POSTGRES_ENV_FILE)

    assert postgres_env["POSTGRES_PASSWORD"] == postgres_secret
    assert "DATABASE_URL" not in postgres_env
    assert runtime_env["POSTGRES_PASSWORD"] == postgres_secret

    django_url = urlparse(django_env["DATABASE_URL"])
    runtime_url = urlparse(runtime_env["DATABASE_URL"])
    assert unquote(django_url.password or "") == postgres_secret
    assert unquote(runtime_url.password or "") == postgres_secret

    assert any("up" in argv and "postgres" in argv for argv, _ in calls)
    assert any("pg_isready" in argv for argv, _ in calls)
    assert any("psql" in argv for argv, _ in calls)


def test_reconcile_redacts_postgres_source_secret_on_failure(monkeypatch, tmp_path):
    postgres_secret = "secret-password-that-must-not-leak-123456"

    _prepare_env(
        monkeypatch,
        tmp_path,
        postgres_secret=postgres_secret,
        django_secret="stale-django-secret-1234567890",
        runtime_secret="stale-runtime-secret-1234567890",
    )

    monkeypatch.setattr(
        migrations,
        "build_migration_plan",
        lambda *a, **k: SimpleNamespace(
            project_name="kx",
            compose_file=Path("/tmp/docker-compose.runtime.yml"),
        ),
    )

    def fake_run(argv, **kwargs):
        if "up" in argv:
            return SimpleNamespace(returncode=1, stdout="", stderr=f"boom {postgres_secret}")
        raise AssertionError(argv)

    monkeypatch.setattr(migrations.subprocess, "run", fake_run)

    try:
        migrations.reconcile_postgres_credentials("konnaxion-prod")
    except migrations.MigrationError as exc:
        message = str(exc)
    else:
        raise AssertionError("MigrationError was not raised")

    assert postgres_secret not in message
    assert "<redacted>" in message


def test_run_django_migrations_reconciles_before_migrate(monkeypatch):
    order = []
    plan = migrations.MigrationPlan(
        instance_id="konnaxion-prod",
        compose_file=Path("/tmp/docker-compose.runtime.yml"),
        project_name="kx",
    )

    monkeypatch.setattr(migrations, "build_migration_plan", lambda *a, **k: plan)
    monkeypatch.setattr(migrations, "validate_migration_plan", lambda p: order.append("validate"))
    monkeypatch.setattr(
        migrations,
        "reconcile_postgres_credentials",
        lambda *a, **k: order.append("reconcile") or {"ok": True},
    )
    monkeypatch.setattr(
        migrations,
        "run_migration_command",
        lambda *a, **k: order.append("migrate") or SimpleNamespace(ok=True),
    )

    result = migrations.run_django_migrations("konnaxion-prod", raise_on_failure=False)

    assert result.ok is True
    assert order == ["validate", "reconcile", "migrate"]
