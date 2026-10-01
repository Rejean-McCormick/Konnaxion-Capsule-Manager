from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from kx_agent.instances import env_writer
from kx_agent.instances import secrets as secret_module
from kx_agent.runtime import compose, migrations


PROBLEM_SECRET = "A-safe-prefix-$nGHSg1Zdyq-and-${MISSING}-tail"


def _assert_compose_escaped(text: str) -> None:
    assert "$$nGHSg1Zdyq" in text
    assert "$${MISSING}" in text
    assert '"' in text


def test_primary_secret_env_writer_escapes_dollar_and_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "django.env"

    secret_module.write_env_file_atomic(
        path,
        {"DJANGO_SECRET_KEY": PROBLEM_SECRET},
    )

    raw = path.read_text(encoding="utf-8")
    _assert_compose_escaped(raw)
    assert secret_module.read_env_file(path)["DJANGO_SECRET_KEY"] == PROBLEM_SECRET


def test_compat_env_writer_escapes_dollar_and_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "django.env"
    raw = env_writer.serialize_env({"DJANGO_SECRET_KEY": PROBLEM_SECRET})
    path.write_text(raw, encoding="utf-8")

    _assert_compose_escaped(raw)
    assert env_writer.load_existing_env_file(path)["DJANGO_SECRET_KEY"] == PROBLEM_SECRET


def test_runtime_compose_fallback_serializer_escapes_dollar_and_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "runtime.env"
    raw = compose.serialize_env({"DJANGO_SECRET_KEY": PROBLEM_SECRET})
    path.write_text(raw, encoding="utf-8")

    _assert_compose_escaped(raw)
    assert compose.parse_env_file(path)["DJANGO_SECRET_KEY"] == PROBLEM_SECRET


def test_existing_literal_double_dollar_round_trips_without_loss(tmp_path: Path) -> None:
    logical = "prefix-$$-middle-$VALUE-tail"
    path = tmp_path / "django.env"

    secret_module.write_env_file_atomic(path, {"DJANGO_SECRET_KEY": logical})

    raw = path.read_text(encoding="utf-8")
    assert "$$$$" in raw
    assert "$$VALUE" in raw
    assert secret_module.read_env_file(path)["DJANGO_SECRET_KEY"] == logical


def test_reconcile_normalizes_legacy_dollar_secret_before_first_compose_call(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(secret_module, "instance_env_dir", lambda _instance_id: tmp_path)
    monkeypatch.setattr(
        secret_module,
        "mirror_env_files_for_state_relative_compose",
        lambda *a, **k: {},
    )

    postgres_secret = "postgres-source-of-truth-1234567890"
    database_url = secret_module.build_database_url(
        user="konnaxion",
        password=postgres_secret,
        host="postgres",
        port="5432",
        database="konnaxion",
    )

    # Simulate v0.1.4 on-disk files: logical value is correct, but the raw '$'
    # is still visible to Docker Compose interpolation.
    (tmp_path / secret_module.POSTGRES_ENV_FILE).write_text(
        "POSTGRES_USER=konnaxion\n"
        "POSTGRES_DB=konnaxion\n"
        "POSTGRES_HOST=postgres\n"
        "POSTGRES_PORT=5432\n"
        f"POSTGRES_PASSWORD={postgres_secret}\n",
        encoding="utf-8",
    )
    legacy_line = f'DJANGO_SECRET_KEY="{PROBLEM_SECRET}"\nDATABASE_URL={database_url}\n'
    (tmp_path / secret_module.DJANGO_ENV_FILE).write_text(legacy_line, encoding="utf-8")
    (tmp_path / secret_module.RUNTIME_ENV_FILE).write_text(
        legacy_line
        + "POSTGRES_USER=konnaxion\n"
        + "POSTGRES_DB=konnaxion\n"
        + "POSTGRES_HOST=postgres\n"
        + "POSTGRES_PORT=5432\n"
        + f"POSTGRES_PASSWORD={postgres_secret}\n",
        encoding="utf-8",
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
        # The very first Compose call must happen only after normalization.
        django_raw = (tmp_path / secret_module.DJANGO_ENV_FILE).read_text(encoding="utf-8")
        runtime_raw = (tmp_path / secret_module.RUNTIME_ENV_FILE).read_text(encoding="utf-8")
        _assert_compose_escaped(django_raw)
        _assert_compose_escaped(runtime_raw)
        calls.append(list(argv))

        if "pg_isready" in argv:
            return SimpleNamespace(returncode=0, stdout="accepting connections", stderr="")
        if "psql" in argv:
            return SimpleNamespace(returncode=0, stdout="ALTER ROLE\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(migrations.subprocess, "run", fake_run)

    result = migrations.reconcile_postgres_credentials("konnaxion-prod")

    assert result["ok"] is True
    assert "django.env" in result["env_files_rewritten"]
    assert "runtime.env" in result["env_files_rewritten"]
    assert secret_module.read_env_file(tmp_path / secret_module.DJANGO_ENV_FILE)[
        "DJANGO_SECRET_KEY"
    ] == PROBLEM_SECRET
    assert calls
