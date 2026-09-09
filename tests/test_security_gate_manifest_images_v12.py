from __future__ import annotations

from kx_agent.security.evidence import _manifest_allowed_images
from kx_agent.security.gate import context_from_compose, run_security_gate


def _current_manifest() -> dict:
    return {
        "schema_version": "kx-capsule-manifest/v1",
        "capsule_id": "konnaxion-v14-local-2026.09.08",
        "capsule_version": "2026.09.08-local.1",
        "app_name": "Konnaxion",
        "app_version": "v14",
        "channel": "local",
        "runtime": {
            "images": [
                {"service": "frontend-next", "image": "konnaxion/frontend-next:v14"},
                {"service": "django-api", "image": "konnaxion/django-api:v14"},
                {"service": "traefik", "image": "traefik:v3.1"},
                {"service": "postgres", "image": "postgres:16"},
                {"service": "redis", "image": "redis:7"},
                {"service": "celeryworker", "image": "konnaxion/django-api:v14"},
                {"service": "celerybeat", "image": "konnaxion/django-api:v14"},
                {"service": "media-nginx", "image": "nginx:stable"},
            ]
        },
    }


def test_current_manifest_runtime_images_are_resolved() -> None:
    allowed = _manifest_allowed_images(_current_manifest())
    assert set(allowed) == {
        "konnaxion/frontend-next:v14",
        "konnaxion/django-api:v14",
        "traefik:v3.1",
        "postgres:16",
        "redis:7",
        "nginx:stable",
    }


def test_legacy_top_level_images_remain_supported() -> None:
    allowed = _manifest_allowed_images({
        "images": {
            "django-api": {"image": "konnaxion/django-api:v14"},
            "postgres": "postgres:16",
        }
    })
    assert allowed == ("konnaxion/django-api:v14", "postgres:16")


def test_current_runtime_images_pass_allowed_images_check() -> None:
    manifest = _current_manifest()
    compose = {
        "services": {
            item["service"]: {"image": item["image"]}
            for item in manifest["runtime"]["images"]
        }
    }
    env = {
        "DJANGO_SECRET_KEY": "safe-generated-secret-value-123456789",
        "DATABASE_URL": "postgresql://kx:generated@postgres:5432/kx",
        "POSTGRES_PASSWORD": "safe-generated-password-123456789",
        "KX_REQUIRE_SIGNED_CAPSULE": "true",
        "KX_ALLOW_UNKNOWN_IMAGES": "false",
    }
    allowed = _manifest_allowed_images(manifest)
    context = context_from_compose(
        instance_id="demo-001",
        compose=compose,
        manifest=manifest,
        env=env,
        capsule_signature_verified=True,
        image_checksums_verified=True,
        firewall_enabled=False,
        backup_configured=True,
        allowed_images=allowed,
    )
    report = run_security_gate(context)
    by_check = {str(getattr(r.check, "value", r.check)): r for r in report.results}
    result = by_check["allowed_images_only"]
    assert str(getattr(result.status, "value", result.status)) == "PASS"
