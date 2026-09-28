from kx_agent.instances.secrets import (
    GeneratedSecrets,
    SecretGenerationPolicy,
    build_env_files,
)


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


def test_django_env_contains_frontend_base_url() -> None:
    files = build_env_files(
        _bundle(),
        SecretGenerationPolicy(
            instance_id="demo-001",
            host="konnaxion.local",
            network_profile="local_only",
            exposure_mode="private",
        ),
    )

    assert files["django.env"]["FRONTEND_BASE_URL"] == "https://konnaxion.local"


def test_runtime_env_contains_frontend_base_url() -> None:
    files = build_env_files(
        _bundle(),
        SecretGenerationPolicy(
            instance_id="demo-001",
            host="konnaxion.local",
            network_profile="local_only",
            exposure_mode="private",
        ),
    )

    assert files["runtime.env"]["FRONTEND_BASE_URL"] == "https://konnaxion.local"


def test_postgres_env_never_contains_database_url() -> None:
    files = build_env_files(
        _bundle(),
        SecretGenerationPolicy(
            instance_id="demo-001",
            host="konnaxion.local",
            network_profile="local_only",
            exposure_mode="private",
        ),
    )

    assert "DATABASE_URL" not in files["postgres.env"]
    assert "DATABASE_URL" in files["django.env"]


def test_worlds_data_plane_is_enabled_in_django_env() -> None:
    files = build_env_files(
        _bundle(),
        SecretGenerationPolicy(
            instance_id="demo-001",
            host="konnaxion.local",
            network_profile="local_only",
            exposure_mode="private",
        ),
    )

    assert files["django.env"]["KONNAXION_WORLDS_DATA_PLANE_ENABLED"] == "true"
    assert files["django.env"]["KONNAXION_WORLDS_ENFORCE_SCOPED_API"] == "true"


def test_worlds_data_plane_is_enabled_in_runtime_env() -> None:
    files = build_env_files(
        _bundle(),
        SecretGenerationPolicy(
            instance_id="demo-001",
            host="konnaxion.local",
            network_profile="local_only",
            exposure_mode="private",
        ),
    )

    assert files["runtime.env"]["KONNAXION_WORLDS_DATA_PLANE_ENABLED"] == "true"
    assert files["runtime.env"]["KONNAXION_WORLDS_ENFORCE_SCOPED_API"] == "true"
