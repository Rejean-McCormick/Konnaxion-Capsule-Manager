from __future__ import annotations

from pathlib import Path

from kx_agent.instances.secrets import GeneratedSecrets, SecretGenerationPolicy, build_env_files
from kx_agent.runtime import compose


def _bundle() -> GeneratedSecrets:
    return GeneratedSecrets(
        instance_id="demo-001",
        host="konnaxion.local",
        django_secret_key="x" * 64,
        postgres_password="p" * 48,
        database_url="postgres://konnaxion:secret@postgres:5432/konnaxion",
        django_allowed_hosts="konnaxion.local,localhost,127.0.0.1,django-api",
        django_csrf_trusted_origins="https://konnaxion.local",
        next_public_api_base="https://konnaxion.local/api",
        next_public_backend_base="https://konnaxion.local",
    )


def test_primary_env_writer_enables_worlds_data_plane() -> None:
    files = build_env_files(
        _bundle(),
        SecretGenerationPolicy(
            instance_id="demo-001",
            host="konnaxion.local",
            network_profile="local_only",
            exposure_mode="private",
        ),
    )

    for filename in ("django.env", "runtime.env"):
        assert files[filename]["KONNAXION_WORLDS_DATA_PLANE_ENABLED"] == "true"
        assert files[filename]["KONNAXION_WORLDS_ENFORCE_SCOPED_API"] == "true"


def test_fallback_env_writer_enables_worlds_data_plane(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(compose, "INSTANCES_ROOT", tmp_path)
    monkeypatch.setattr(compose, "assert_under_root", lambda path, *args, **kwargs: Path(path))
    monkeypatch.setattr(
        compose,
        "ensure_dir",
        lambda path: Path(path).mkdir(parents=True, exist_ok=True) or Path(path),
    )

    result = compose._write_minimal_instance_env_files(
        compose.ComposeRenderOptions(
            instance_id="demo-001",
            host="konnaxion.local",
            network_profile="local_only",
            exposure_mode="private",
        )
    )

    assert result["ok"] is True
    env = compose.parse_env_file(tmp_path / "demo-001" / "env" / "django.env")
    runtime_env = compose.parse_env_file(tmp_path / "demo-001" / "env" / "runtime.env")

    assert env["KONNAXION_WORLDS_DATA_PLANE_ENABLED"] == "true"
    assert env["KONNAXION_WORLDS_ENFORCE_SCOPED_API"] == "true"
    assert runtime_env["KONNAXION_WORLDS_DATA_PLANE_ENABLED"] == "true"
    assert runtime_env["KONNAXION_WORLDS_ENFORCE_SCOPED_API"] == "true"
