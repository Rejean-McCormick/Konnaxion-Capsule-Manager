from __future__ import annotations

from pathlib import Path

import pytest

from kx_agent.instances import env_writer
from kx_agent.instances import secrets as secret_module


def _prefer_dollar_when_possible(alphabet: str) -> str:
    """Force '$' whenever the supplied alphabet permits it."""
    return "$" if "$" in alphabet else alphabet[0]


def test_primary_django_secret_generator_never_starts_with_dollar(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(secret_module.secrets, "choice", _prefer_dollar_when_possible)
    value = secret_module.generate_django_secret_key(64)
    assert not value.startswith("$")
    assert "$" in value[1:]


def test_compat_django_secret_generator_never_starts_with_dollar(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(env_writer.secrets, "choice", _prefer_dollar_when_possible)
    value = env_writer.generate_secret_key(64)
    assert not value.startswith("$")
    assert "$" in value[1:]


def test_secret_validation_rejects_django_environ_proxy_key() -> None:
    with pytest.raises(Exception):
        secret_module.validate_secret_value("DJANGO_SECRET_KEY", "$" + "x" * 63)


def test_preserve_existing_secrets_rotates_proxy_key_but_keeps_postgres_password() -> None:
    bundle = secret_module.GeneratedSecrets(
        instance_id="konnaxion-prod",
        host="example.test",
        django_secret_key="N" * 64,
        postgres_password="p" * 48,
        database_url="postgres://konnaxion:" + ("p" * 48) + "@postgres:5432/konnaxion",
        django_allowed_hosts="example.test,localhost,127.0.0.1,django-api",
        django_csrf_trusted_origins="https://example.test",
        next_public_api_base="https://example.test/api",
        next_public_backend_base="https://example.test",
    )
    preserved = secret_module.preserve_existing_secrets(
        bundle,
        {
            "DJANGO_SECRET_KEY": "$" + "x" * 63,
            "POSTGRES_PASSWORD": "p" * 48,
        },
    )
    assert preserved.django_secret_key == "N" * 64
    assert preserved.postgres_password == "p" * 48


def test_env_writer_load_existing_rotates_only_unsafe_django_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_dir = tmp_path / "env"
    env_dir.mkdir()
    (env_dir / env_writer.DJANGO_ENV_FILENAME).write_text(
        'DJANGO_SECRET_KEY="$' + ('x' * 63) + '"\n', encoding="utf-8"
    )
    (env_dir / env_writer.POSTGRES_ENV_FILENAME).write_text(
        "POSTGRES_PASSWORD=" + ("p" * 48) + "\n", encoding="utf-8"
    )
    monkeypatch.setattr(env_writer, "instance_env_dir", lambda _instance_id: env_dir)
    monkeypatch.setattr(env_writer, "generate_secret_key", lambda length=64: "R" * length)

    result = env_writer.load_existing_runtime_secrets("konnaxion-prod")
    assert result is not None
    assert result.django_secret_key == "R" * 64
    assert result.postgres_password == "p" * 48
