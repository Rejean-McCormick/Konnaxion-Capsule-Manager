from __future__ import annotations

from kx_agent.instances import env_writer
from kx_agent.instances import secrets


def _context(host: str) -> env_writer.InstanceEnvContext:
    return env_writer.InstanceEnvContext(
        instance_id="konnaxion-prod",
        capsule_id="konnaxion",
        capsule_version="test",
        param_version="test",
        app_version="test",
        network_profile="public_vps",
        exposure_mode="public",
        host=host,
    )


def test_legacy_secret_writer_adds_apex_and_www() -> None:
    allowed = secrets.build_allowed_hosts("konnaxion.com", instance_id="konnaxion-prod").split(",")
    origins = secrets.build_csrf_trusted_origins("konnaxion.com").split(",")

    assert "konnaxion.com" in allowed
    assert "www.konnaxion.com" in allowed
    assert "https://konnaxion.com" in origins
    assert "https://www.konnaxion.com" in origins


def test_legacy_secret_writer_maps_www_back_to_apex() -> None:
    allowed = secrets.build_allowed_hosts("www.konnaxion.com").split(",")
    assert "www.konnaxion.com" in allowed
    assert "konnaxion.com" in allowed


def test_env_writer_adds_apex_and_www_without_explicit_alias() -> None:
    context = _context("konnaxion.com")
    allowed = env_writer.build_django_allowed_hosts(context).split(",")
    origins = env_writer.build_csrf_trusted_origins(context).split(",")

    assert "konnaxion.com" in allowed
    assert "www.konnaxion.com" in allowed
    assert "https://konnaxion.com" in origins
    assert "https://www.konnaxion.com" in origins


def test_no_automatic_www_alias_for_ip_or_sslip() -> None:
    assert secrets.build_default_host_aliases("2.56.97.41") == ()
    assert secrets.build_default_host_aliases("2-56-97-41.sslip.io") == ()
    assert env_writer.default_public_host_aliases("2.56.97.41") == ()
    assert env_writer.default_public_host_aliases("2-56-97-41.sslip.io") == ()
