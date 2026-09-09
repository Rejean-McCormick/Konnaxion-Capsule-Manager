from __future__ import annotations

from pathlib import Path

import yaml

from kx_agent.runtime import compose as compose_module
from kx_agent.runtime.compose import ComposeRenderOptions
from kx_shared.konnaxion_constants import DockerService


def test_capsule_compose_image_placeholders_are_not_runtime_images(
    tmp_path: Path,
    monkeypatch,
) -> None:
    capsule_id = "konnaxion-v14-local-2026.09.08"
    capsule_dir = tmp_path / capsule_id
    capsule_dir.mkdir(parents=True)
    compose_file = capsule_dir / "docker-compose.capsule.yml"
    compose_file.write_text(
        yaml.safe_dump(
            {
                "services": {
                    "django-api": {
                        "image": "${KX_IMAGE_DJANGO_API:?KX_IMAGE_DJANGO_API is required}"
                    },
                    "frontend-next": {
                        "image": "${KX_IMAGE_FRONTEND_NEXT:?KX_IMAGE_FRONTEND_NEXT is required}"
                    },
                    "postgres": {"image": "postgres:16"},
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(compose_module, "SHARED_CAPSULES_ROOT", tmp_path)
    monkeypatch.setattr(compose_module, "assert_under_root", lambda value: Path(value))

    result = compose_module.image_map_from_capsule_compose(capsule_id)

    assert "django-api" not in result
    assert "frontend-next" not in result
    assert result["postgres"] == "postgres:16"


def test_current_builder_manifest_image_list_is_supported(monkeypatch) -> None:
    capsule_id = "konnaxion-v14-local-2026.09.08"
    manifest = {
        "runtime": {
            "images": [
                {
                    "service": "frontend-next",
                    "image": "konnaxion/frontend-next:v14",
                    "archive": "images/frontend-next.oci.tar",
                },
                {
                    "service": "django-api",
                    "image": "konnaxion/django-api:v14",
                    "archive": "images/django-api.oci.tar",
                },
                {
                    "service": "celeryworker",
                    "image": "konnaxion/django-api:v14",
                    "archive": "images/celeryworker.oci.tar",
                },
                {
                    "service": "celerybeat",
                    "image": "konnaxion/django-api:v14",
                    "archive": "images/celerybeat.oci.tar",
                },
            ]
        }
    }
    monkeypatch.setattr(compose_module, "read_capsule_manifest", lambda _capsule_id: manifest)

    result = compose_module.image_map_from_capsule_manifest(capsule_id)

    assert result["frontend-next"] == "konnaxion/frontend-next:v14"
    assert result["django-api"] == "konnaxion/django-api:v14"
    assert result["celeryworker"] == "konnaxion/django-api:v14"
    assert result["celerybeat"] == "konnaxion/django-api:v14"


def test_runtime_image_resolution_prefers_signed_manifest_over_template_placeholders(
    tmp_path: Path,
    monkeypatch,
) -> None:
    capsule_id = "konnaxion-v14-local-2026.09.08"
    capsule_dir = tmp_path / capsule_id
    capsule_dir.mkdir(parents=True)
    (capsule_dir / "docker-compose.capsule.yml").write_text(
        yaml.safe_dump(
            {
                "services": {
                    "traefik": {"image": "${KX_IMAGE_TRAEFIK:?required}"},
                    "frontend-next": {"image": "${KX_IMAGE_FRONTEND_NEXT:?required}"},
                    "django-api": {"image": "${KX_IMAGE_DJANGO_API:?required}"},
                    "postgres": {"image": "${KX_IMAGE_POSTGRES:?required}"},
                    "redis": {"image": "${KX_IMAGE_REDIS:?required}"},
                    "celeryworker": {"image": "${KX_IMAGE_DJANGO_API:?required}"},
                    "celerybeat": {"image": "${KX_IMAGE_DJANGO_API:?required}"},
                    "media-nginx": {"image": "${KX_IMAGE_MEDIA_NGINX:?required}"},
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    manifest = {
        "runtime": {
            "images": [
                {"service": "traefik", "image": "traefik:v3.1"},
                {"service": "frontend-next", "image": "konnaxion/frontend-next:v14"},
                {"service": "django-api", "image": "konnaxion/django-api:v14"},
                {"service": "postgres", "image": "postgres:16"},
                {"service": "redis", "image": "redis:7"},
                {"service": "celeryworker", "image": "konnaxion/django-api:v14"},
                {"service": "celerybeat", "image": "konnaxion/django-api:v14"},
                {"service": "media-nginx", "image": "nginx:stable"},
            ]
        }
    }

    monkeypatch.setattr(compose_module, "SHARED_CAPSULES_ROOT", tmp_path)
    monkeypatch.setattr(compose_module, "assert_under_root", lambda value: Path(value))
    monkeypatch.setattr(compose_module, "read_capsule_manifest", lambda _capsule_id: manifest)
    monkeypatch.setattr(compose_module, "image_map_from_environment", lambda: {})

    result = compose_module.resolve_runtime_image_map(
        ComposeRenderOptions(instance_id="demo-001", capsule_id=capsule_id, host="konnaxion.local")
    )

    assert result[DockerService.TRAEFIK.value] == "traefik:v3.1"
    assert result[DockerService.FRONTEND_NEXT.value] == "konnaxion/frontend-next:v14"
    assert result[DockerService.DJANGO_API.value] == "konnaxion/django-api:v14"
    assert result[DockerService.POSTGRES.value] == "postgres:16"
    assert result[DockerService.REDIS.value] == "redis:7"
    assert result[DockerService.CELERYWORKER.value] == "konnaxion/django-api:v14"
    assert result[DockerService.CELERYBEAT.value] == "konnaxion/django-api:v14"
    assert result[DockerService.MEDIA_NGINX.value] == "nginx:stable"
    assert all("${" not in image for image in result.values())
