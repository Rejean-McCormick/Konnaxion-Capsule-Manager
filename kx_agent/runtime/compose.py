# kx_agent/runtime/compose.py

"""
Konnaxion Docker Compose runtime renderer and validator.

Responsibilities:
- Render docker-compose.runtime.yml for a Konnaxion Instance.
- Render Traefik dynamic routing config without mounting the Docker socket.
- Use canonical service names, ports, env files, volumes, and networks.
- Validate that generated compose does not expose internal services.
- Write generated files atomically under the canonical instance state path.
- Ensure required instance env files exist for first-run runtime and Security Gate checks.
- Load capsule manifest and instance env context for Security Gate callers.

This module does not:
- Start or stop Docker Compose.
- Import capsules.
- Open network ports.
- Modify firewall rules.

Secrets note:
- Primary secret/env-file generation is delegated to kx_agent.instances.secrets when available.
- A minimal safe fallback exists so Droplet bootstrap/deploy cannot create a compose runtime
  that immediately fails Security Gate because env files are missing.
"""

from __future__ import annotations

import json
import os
import secrets as pysecrets
import string
import tempfile
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

try:
    import yaml
except ImportError as exc:  # pragma: no cover
    yaml = None  # type: ignore[assignment]
    _YAML_IMPORT_ERROR = exc
else:
    _YAML_IMPORT_ERROR = None

from kx_shared.konnaxion_constants import (
    ALLOWED_ENTRY_PORTS,
    APP_VERSION,
    CANONICAL_DOCKER_SERVICES,
    DEFAULT_CAPSULE_ID,
    DEFAULT_EXPOSURE_MODE,
    DEFAULT_NETWORK_PROFILE,
    DEFAULT_PUBLIC_MODE_ENABLED,
    DockerService,
    ExposureMode,
    FORBIDDEN_PUBLIC_PORTS,
    INTERNAL_ONLY_PORTS,
    KX_ROOT,
    NetworkProfile,
    PARAM_VERSION,
)
from kx_shared.paths import (
    assert_under_root,
    ensure_dir,
    instance_compose_file,
    instance_state_dir,
    validate_safe_id,
)


COMPOSE_FILENAME = "docker-compose.runtime.yml"
TRAEFIK_DYNAMIC_FILENAME = "traefik-dynamic.yml"
TRAEFIK_ACME_DIRNAME = "letsencrypt"
TRAEFIK_ACME_FILENAME = "acme.json"
TRAEFIK_ACME_RESOLVER = "letsencrypt"
TRAEFIK_ACME_EMAIL_ENV_KEYS: tuple[str, ...] = (
    "KX_TRAEFIK_ACME_EMAIL",
    "KX_ACME_EMAIL",
    "TRAEFIK_ACME_EMAIL",
)

PUBLIC_NETWORK = "kx-public"
PRIVATE_NETWORK = "kx-private"
DATA_NETWORK = "kx-data"

KONNAXION_ROOT = Path(KX_ROOT)
SHARED_CAPSULES_ROOT = KONNAXION_ROOT / "shared" / "capsules"
INSTANCES_ROOT = KONNAXION_ROOT / "instances"

ENV_DIRNAME = "env"
KX_ENV_FILE = "kx.env"
DJANGO_ENV_FILE = "django.env"
POSTGRES_ENV_FILE = "postgres.env"
REDIS_ENV_FILE = "redis.env"
FRONTEND_ENV_FILE = "frontend.env"
RUNTIME_ENV_FILE = "runtime.env"

ENV_FILENAMES: tuple[str, ...] = (
    KX_ENV_FILE,
    DJANGO_ENV_FILE,
    POSTGRES_ENV_FILE,
    REDIS_ENV_FILE,
    FRONTEND_ENV_FILE,
    RUNTIME_ENV_FILE,
)

SECRET_ENV_FILENAMES: frozenset[str] = frozenset(
    {
        DJANGO_ENV_FILE,
        POSTGRES_ENV_FILE,
        RUNTIME_ENV_FILE,
    }
)

REQUIRED_SECURITY_GATE_ENV_KEYS: frozenset[str] = frozenset(
    {
        "DATABASE_URL",
        "DJANGO_SECRET_KEY",
        "POSTGRES_PASSWORD",
    }
)

FORBIDDEN_SERVICE_ALIASES = {
    "backend",
    "api",
    "web",
    "next",
    "frontend",
    "db",
    "database",
    "cache",
    "worker",
    "scheduler",
    "media",
    "agent",
}

DEFAULT_IMAGES = {
    DockerService.TRAEFIK.value: "traefik:v3.1",
    DockerService.FRONTEND_NEXT.value: "konnaxion/frontend-next:v14",
    DockerService.DJANGO_API.value: "konnaxion/django-api:v14",
    DockerService.POSTGRES.value: "postgres:16",
    DockerService.REDIS.value: "redis:7",
    DockerService.CELERYWORKER.value: "konnaxion/django-api:v14",
    DockerService.CELERYBEAT.value: "konnaxion/django-api:v14",
    DockerService.FLOWER.value: "konnaxion/django-api:v14",
    DockerService.MEDIA_NGINX.value: "nginx:stable",
}

LOCAL_APP_IMAGE_SERVICES: frozenset[str] = frozenset(
    {
        DockerService.FRONTEND_NEXT.value,
        DockerService.DJANGO_API.value,
        DockerService.CELERYWORKER.value,
        DockerService.CELERYBEAT.value,
        DockerService.FLOWER.value,
    }
)

IMAGE_ENV_KEYS: Mapping[str, str] = {
    DockerService.TRAEFIK.value: "KX_IMAGE_TRAEFIK",
    DockerService.FRONTEND_NEXT.value: "KX_IMAGE_FRONTEND_NEXT",
    DockerService.DJANGO_API.value: "KX_IMAGE_DJANGO_API",
    DockerService.POSTGRES.value: "KX_IMAGE_POSTGRES",
    DockerService.REDIS.value: "KX_IMAGE_REDIS",
    DockerService.CELERYWORKER.value: "KX_IMAGE_CELERYWORKER",
    DockerService.CELERYBEAT.value: "KX_IMAGE_CELERYBEAT",
    DockerService.FLOWER.value: "KX_IMAGE_FLOWER",
    DockerService.MEDIA_NGINX.value: "KX_IMAGE_MEDIA_NGINX",
}

_PLACEHOLDER_SECRET_VALUES = {
    "",
    "change-me",
    "changeme",
    "replace-me",
    "replaceme",
    "generated-on-install",
    "<generated_on_install>",
    "<generated-on-install>",
    "<postgres_password>",
    "<django_secret_key>",
    "password",
    "postgres",
    "konnaxion",
    "secret",
    "default",
    "example",
    "test",
    "admin",
    "none",
    "null",
}

_LOCAL_HOSTS = {"127.0.0.1", "localhost", "0.0.0.0", "::1"}


class ComposeRenderError(RuntimeError):
    """Raised when runtime Compose rendering cannot continue."""


class ComposeValidationError(ValueError):
    """Raised when generated Compose violates Konnaxion runtime policy."""


def normalize_image_map(image_map: Mapping[str, Any] | None) -> dict[str, str]:
    """Normalize and validate a service -> image mapping."""

    if not image_map:
        return {}

    normalized: dict[str, str] = {}

    for service_name, image in image_map.items():
        service = str(service_name).strip()
        image_text = str(image or "").strip()

        if not service or not image_text:
            continue

        if service not in CANONICAL_DOCKER_SERVICES:
            raise ComposeRenderError(f"unknown image service name: {service}")

        normalized[service] = image_text

    return normalized


def image_map_from_environment() -> dict[str, str]:
    """Read optional image overrides from environment variables.

    Supported:
        KX_IMAGES_JSON='{"django-api":"registry.example.com/kx/django-api:v14"}'
        KX_IMAGE_DJANGO_API=registry.example.com/kx/django-api:v14
        KX_IMAGE_FRONTEND_NEXT=registry.example.com/kx/frontend-next:v14
    """

    result: dict[str, str] = {}

    raw_json = os.getenv("KX_IMAGES_JSON", "").strip()
    if raw_json:
        try:
            parsed = json.loads(raw_json)
        except json.JSONDecodeError as exc:
            raise ComposeRenderError(f"KX_IMAGES_JSON is invalid JSON: {exc}") from exc

        if not isinstance(parsed, Mapping):
            raise ComposeRenderError("KX_IMAGES_JSON must be a JSON object")

        result.update(normalize_image_map(parsed))

    for service_name, env_key in IMAGE_ENV_KEYS.items():
        value = os.getenv(env_key, "").strip()
        if value:
            result[service_name] = value

    return result


def _is_unresolved_compose_interpolation(value: str) -> bool:
    """Return true when a value still contains Docker Compose interpolation.

    The signed capsule Compose file is allowed to use image placeholders such as
    ``${KX_IMAGE_DJANGO_API:?KX_IMAGE_DJANGO_API is required}``. Those values are
    build/install-time template syntax, not valid image references for the
    generated instance runtime Compose file.
    """

    text = str(value or "").strip()
    return "${" in text or text.startswith("$")


def image_map_from_capsule_compose(capsule_id: str) -> dict[str, str]:
    """Read concrete image references from extracted docker-compose.capsule.yml.

    Unresolved ``${KX_IMAGE_*...}`` placeholders are intentionally ignored.
    Concrete image identity is recovered from the signed capsule manifest (or
    canonical defaults) before the instance Compose file is rendered.
    """

    capsule_id = validate_safe_id(capsule_id, field_name="capsule_id")
    compose_path = assert_under_root(
        SHARED_CAPSULES_ROOT / capsule_id / "docker-compose.capsule.yml"
    )

    if not compose_path.exists() or not compose_path.is_file():
        return {}

    compose = read_mapping_file(compose_path)
    services = compose.get("services")

    if not isinstance(services, Mapping):
        return {}

    result: dict[str, str] = {}

    for service_name, service in services.items():
        service_key = str(service_name).strip()

        if service_key not in CANONICAL_DOCKER_SERVICES:
            continue

        if not isinstance(service, Mapping):
            continue

        image = str(service.get("image") or "").strip()
        if image and not _is_unresolved_compose_interpolation(image):
            result[service_key] = image

    return result


def _image_map_from_manifest_value(value: Any) -> dict[str, str]:
    """Normalize either supported manifest image representation.

    Current Builder manifests use a list of records:

        runtime:
          images:
            - service: django-api
              image: konnaxion/django-api:v14
              archive: images/django-api.oci.tar

    Older manifests may use a direct service-to-image mapping.
    """

    if isinstance(value, Mapping):
        return normalize_image_map(value)

    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        result: dict[str, str] = {}
        for item in value:
            if not isinstance(item, Mapping):
                continue
            service = str(item.get("service") or "").strip()
            image = str(item.get("image") or "").strip()
            if not service or not image:
                continue
            result.update(normalize_image_map({service: image}))
        return result

    return {}


def image_map_from_capsule_manifest(capsule_id: str) -> dict[str, str]:
    """Read optional image references from extracted manifest.yaml.

    Supported manifest shapes:
        images:
          django-api: registry.example.com/kx/django-api:v14

        runtime:
          images:
            django-api: registry.example.com/kx/django-api:v14

        runtime:
          image_map:
            django-api: registry.example.com/kx/django-api:v14
    """

    manifest = read_capsule_manifest(capsule_id)
    result: dict[str, str] = {}

    result.update(_image_map_from_manifest_value(manifest.get("images")))

    runtime = manifest.get("runtime")
    if isinstance(runtime, Mapping):
        result.update(_image_map_from_manifest_value(runtime.get("images")))

        runtime_image_map = runtime.get("image_map")
        if isinstance(runtime_image_map, Mapping):
            result.update(normalize_image_map(runtime_image_map))

    return result


def resolve_runtime_image_map(options: "ComposeRenderOptions") -> dict[str, str]:
    """Resolve final service image map.

    Precedence, lowest to highest:
    1. DEFAULT_IMAGES
    2. extracted docker-compose.capsule.yml
    3. extracted manifest image metadata
    4. environment overrides
    5. explicit options.image_map
    """

    resolved: dict[str, str] = dict(DEFAULT_IMAGES)
    resolved.update(image_map_from_capsule_compose(options.capsule_id))
    resolved.update(image_map_from_capsule_manifest(options.capsule_id))
    resolved.update(image_map_from_environment())
    resolved.update(normalize_image_map(options.image_map))

    return resolved


def local_app_pull_policy(service_name: str, image: str) -> str | None:
    """Return pull policy for local capsule app images.

    Konnaxion app images should normally be loaded from the capsule or built by
    the deployment pipeline. If Compose tries to pull konnaxion/* from Docker
    Hub, it fails with pull-access-denied. pull_policy=never turns that into a
    clearer local-image-missing failure and prevents accidental registry pulls.
    """

    if service_name not in LOCAL_APP_IMAGE_SERVICES:
        return None

    image_text = str(image).strip()

    if image_text.startswith("konnaxion/"):
        return "never"

    if image_text.startswith("localhost/"):
        return "never"

    return None


def apply_pull_policy(service: dict[str, Any], service_name: str, image: str) -> None:
    """Apply image pull policy to one service spec when appropriate."""

    policy = local_app_pull_policy(service_name, image)
    if policy:
        service["pull_policy"] = policy


def enum_value(value: Any) -> str:
    """Return the string value for StrEnum/Enum/string inputs."""

    if isinstance(value, Enum):
        return str(value.value)
    return str(value)


def path_text(path: str | Path) -> str:
    """Return stable POSIX-style path text for generated Compose YAML."""

    return Path(path).as_posix()


def normalize_acme_email(value: Any) -> str | None:
    """Return a normalized ACME email address or None."""

    text = str(value or "").strip()
    if not text:
        return None

    if "@" not in text or text.startswith("@") or text.endswith("@"):
        raise ComposeRenderError(f"invalid Traefik ACME email: {text!r}")

    return text


def normalize_traefik_acme_resolver(value: Any) -> str:
    """Return a safe Traefik ACME resolver name."""

    text = str(value or TRAEFIK_ACME_RESOLVER).strip()
    if not text:
        text = TRAEFIK_ACME_RESOLVER

    validate_safe_id(text, field_name="traefik_acme_resolver")
    return text


def default_acme_email_for_host(host: str) -> str:
    """Return a deterministic non-secret ACME registration email for a host."""

    host_text = normalize_traefik_host(host)
    # Strip an optional port. This keeps local manual probes such as
    # example.com:443 from producing an invalid email domain.
    if host_text.count(":") == 1:
        hostname, maybe_port = host_text.rsplit(":", 1)
        if maybe_port.isdigit():
            host_text = hostname

    return f"admin@{host_text}"


def traefik_acme_email(options: "ComposeRenderOptions") -> str:
    """Resolve the Traefik ACME email from options/env/default host."""

    if options.traefik_acme_email:
        return options.traefik_acme_email

    for key in TRAEFIK_ACME_EMAIL_ENV_KEYS:
        value = normalize_acme_email(os.environ.get(key))
        if value:
            return value

    return default_acme_email_for_host(options.host)


def traefik_acme_enabled(options: "ComposeRenderOptions") -> bool:
    """Return whether public HTTPS should use Traefik ACME/Let’s Encrypt."""

    options = options.normalized()

    if options.traefik_acme_enabled is not None:
        return bool(options.traefik_acme_enabled)

    return (
        options.bind_https
        and options.network_profile == NetworkProfile.PUBLIC_VPS.value
        and options.exposure_mode == ExposureMode.PUBLIC.value
        and options.host.lower() not in _LOCAL_HOSTS
    )


def require_yaml() -> None:
    """Require PyYAML before YAML rendering/reading."""

    if yaml is None:  # pragma: no cover
        raise ComposeRenderError(
            "PyYAML is required to render docker-compose.runtime.yml"
        ) from _YAML_IMPORT_ERROR


def yaml_dump(data: Mapping[str, Any]) -> str:
    """Return deterministic YAML for generated runtime files."""

    require_yaml()
    return yaml.safe_dump(  # type: ignore[union-attr]
        data,
        sort_keys=False,
        default_flow_style=False,
        indent=2,
        width=120,
    )


def atomic_write_text(path: str | Path, content: str, *, mode: int = 0o640) -> Path:
    """Atomically write text under the canonical Konnaxion root."""

    target = assert_under_root(path)
    ensure_dir(target.parent)

    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=str(target.parent),
        text=True,
    )
    tmp_path = Path(tmp_name)

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())

        os.replace(tmp_path, target)
        target.chmod(mode)
        return target
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


@dataclass(frozen=True)
class ComposeImageSet:
    """Images used by the generated Docker Compose runtime."""

    traefik: str = DEFAULT_IMAGES[DockerService.TRAEFIK.value]
    frontend_next: str = DEFAULT_IMAGES[DockerService.FRONTEND_NEXT.value]
    django_api: str = DEFAULT_IMAGES[DockerService.DJANGO_API.value]
    postgres: str = DEFAULT_IMAGES[DockerService.POSTGRES.value]
    redis: str = DEFAULT_IMAGES[DockerService.REDIS.value]
    celeryworker: str = DEFAULT_IMAGES[DockerService.CELERYWORKER.value]
    celerybeat: str = DEFAULT_IMAGES[DockerService.CELERYBEAT.value]
    flower: str = DEFAULT_IMAGES[DockerService.FLOWER.value]
    media_nginx: str = DEFAULT_IMAGES[DockerService.MEDIA_NGINX.value]

    @classmethod
    def from_mapping(cls, image_map: Mapping[str, str] | None = None) -> "ComposeImageSet":
        """Create an image set from a canonical service-name mapping."""

        if not image_map:
            return cls()

        allowed = {
            DockerService.TRAEFIK.value: "traefik",
            DockerService.FRONTEND_NEXT.value: "frontend_next",
            DockerService.DJANGO_API.value: "django_api",
            DockerService.POSTGRES.value: "postgres",
            DockerService.REDIS.value: "redis",
            DockerService.CELERYWORKER.value: "celeryworker",
            DockerService.CELERYBEAT.value: "celerybeat",
            DockerService.FLOWER.value: "flower",
            DockerService.MEDIA_NGINX.value: "media_nginx",
        }

        kwargs: dict[str, str] = {}

        for service_name, image in image_map.items():
            if service_name not in allowed:
                raise ComposeRenderError(f"unknown image service name: {service_name}")

            if not isinstance(image, str) or not image.strip():
                raise ComposeRenderError(f"image for {service_name} must be a non-empty string")

            kwargs[allowed[service_name]] = image.strip()

        return cls(**kwargs)

    def for_service(self, service: DockerService | str) -> str:
        """Return the image for a canonical Docker service."""

        service_value = enum_value(service)
        mapping = {
            DockerService.TRAEFIK.value: self.traefik,
            DockerService.FRONTEND_NEXT.value: self.frontend_next,
            DockerService.DJANGO_API.value: self.django_api,
            DockerService.POSTGRES.value: self.postgres,
            DockerService.REDIS.value: self.redis,
            DockerService.CELERYWORKER.value: self.celeryworker,
            DockerService.CELERYBEAT.value: self.celerybeat,
            DockerService.FLOWER.value: self.flower,
            DockerService.MEDIA_NGINX.value: self.media_nginx,
        }

        try:
            return mapping[service_value]
        except KeyError as exc:
            raise ComposeRenderError(f"non-canonical service name: {service_value}") from exc


@dataclass(frozen=True)
class ComposeRenderOptions:
    """Inputs required to render a safe Konnaxion runtime Compose spec."""

    instance_id: str
    host: str
    capsule_id: str = DEFAULT_CAPSULE_ID
    instance_root: Path | None = None
    network_profile: str = enum_value(DEFAULT_NETWORK_PROFILE)
    exposure_mode: str = enum_value(DEFAULT_EXPOSURE_MODE)
    public_mode_enabled: bool = DEFAULT_PUBLIC_MODE_ENABLED
    public_mode_expires_at: str | None = None
    host_aliases: Sequence[str] = field(default_factory=tuple)
    image_map: Mapping[str, str] | None = None
    include_flower: bool = False
    bind_http: bool = True
    bind_https: bool = True
    allow_http_on_local_only: bool = False
    ensure_env_files: bool = True
    overwrite_env_files: bool = False
    traefik_acme_enabled: bool | None = None
    traefik_acme_email: str | None = None
    traefik_acme_resolver: str = TRAEFIK_ACME_RESOLVER

    def normalized(self) -> "ComposeRenderOptions":
        """Return validated normalized render options."""

        instance_id = validate_safe_id(self.instance_id, field_name="instance_id")
        network_profile = enum_value(self.network_profile)
        exposure_mode = enum_value(self.exposure_mode)
        host = normalize_traefik_host(self.host)
        host_aliases = tuple(normalized_host_aliases(host, self.host_aliases))
        capsule_id = validate_safe_id(
            str(self.capsule_id).strip() or DEFAULT_CAPSULE_ID,
            field_name="capsule_id",
        )
        traefik_acme_resolver = normalize_traefik_acme_resolver(
            self.traefik_acme_resolver
        )
        traefik_acme_email = normalize_acme_email(self.traefik_acme_email)

        if network_profile not in {enum_value(item) for item in NetworkProfile}:
            raise ComposeRenderError(f"invalid network_profile: {network_profile}")

        if exposure_mode not in {enum_value(item) for item in ExposureMode}:
            raise ComposeRenderError(f"invalid exposure_mode: {exposure_mode}")

        if not host:
            raise ComposeRenderError("host must not be empty")

        # public_vps must be rendered with the public host/domain that operators will use.
        # Falling back to 127.0.0.1 creates a valid-looking runtime that Traefik and
        # Django cannot serve publicly.
        if network_profile == NetworkProfile.PUBLIC_VPS.value and host.lower() in _LOCAL_HOSTS:
            raise ComposeRenderError(
                "public_vps requires a public host; refusing localhost/loopback host"
            )

        if network_profile == NetworkProfile.PUBLIC_TEMPORARY.value:
            if exposure_mode != ExposureMode.TEMPORARY_TUNNEL.value:
                raise ComposeRenderError(
                    "public_temporary requires exposure_mode=temporary_tunnel"
                )

            if not self.public_mode_expires_at:
                raise ComposeRenderError(
                    "public_mode_expires_at is required for public_temporary"
                )

        if exposure_mode == ExposureMode.TEMPORARY_TUNNEL.value and not self.public_mode_expires_at:
            raise ComposeRenderError(
                "public_mode_expires_at is required for temporary_tunnel exposure"
            )

        public_mode_enabled = bool(
            self.public_mode_enabled
            or network_profile in {
                NetworkProfile.PUBLIC_TEMPORARY.value,
                NetworkProfile.PUBLIC_VPS.value,
            }
            or exposure_mode in {
                ExposureMode.TEMPORARY_TUNNEL.value,
                ExposureMode.PUBLIC.value,
            }
        )

        return ComposeRenderOptions(
            instance_id=instance_id,
            host=host,
            capsule_id=capsule_id,
            instance_root=self.instance_root,
            network_profile=network_profile,
            exposure_mode=exposure_mode,
            public_mode_enabled=public_mode_enabled,
            public_mode_expires_at=self.public_mode_expires_at,
            host_aliases=host_aliases,
            image_map=self.image_map,
            include_flower=self.include_flower,
            bind_http=self.bind_http,
            bind_https=self.bind_https,
            allow_http_on_local_only=self.allow_http_on_local_only,
            ensure_env_files=self.ensure_env_files,
            overwrite_env_files=self.overwrite_env_files,
            traefik_acme_enabled=self.traefik_acme_enabled,
            traefik_acme_email=traefik_acme_email,
            traefik_acme_resolver=traefik_acme_resolver,
        )


@dataclass(frozen=True)
class ComposeWriteResult:
    """Result returned after rendering/writing runtime Compose files."""

    instance_id: str
    compose_file: str
    traefik_dynamic_file: str
    network_profile: str
    exposure_mode: str
    services: tuple[str, ...]
    warnings: tuple[str, ...] = field(default_factory=tuple)
    env_dir: str | None = None
    env_files: Mapping[str, str] = field(default_factory=dict)
    manifest_file: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def generated_instance_root(options: ComposeRenderOptions) -> Path:
    """Return the instance root used for generated volume paths."""

    if options.instance_root is not None:
        return Path(options.instance_root)

    return INSTANCES_ROOT / options.instance_id


def generated_env_file(filename: str) -> str:
    """Return env-file path relative to state/docker-compose.runtime.yml.

    Docker Compose resolves env_file entries relative to the compose file
    location, not relative to the instance root. Since the compose file is
    written under:

        /opt/konnaxion/instances/<id>/state/docker-compose.runtime.yml

    the canonical env directory must be referenced as:

        ../env/<filename>

    This avoids the broken path:

        /opt/konnaxion/instances/<id>/state/env/<filename>
    """

    clean_name = Path(str(filename)).name
    if not clean_name or clean_name != str(filename):
        raise ComposeRenderError(f"invalid env filename: {filename!r}")

    return f"../{ENV_DIRNAME}/{clean_name}"


def generated_volume_path(options: ComposeRenderOptions, name: str) -> str:
    """Return a stable POSIX-style instance volume path."""

    return path_text(generated_instance_root(options) / name)


def instance_env_dir_path(instance_id: str) -> Path:
    """Return canonical instance env directory path."""

    instance_id = validate_safe_id(instance_id, field_name="instance_id")
    return assert_under_root(INSTANCES_ROOT / instance_id / ENV_DIRNAME)


def env_file_paths(instance_id: str) -> dict[str, Path]:
    """Return canonical env file paths for an instance."""

    env_dir = instance_env_dir_path(instance_id)
    return {filename: env_dir / filename for filename in ENV_FILENAMES}


def capsule_manifest_file(capsule_id: str) -> Path:
    """Return the extracted capsule manifest path for a capsule id."""

    capsule_id = validate_safe_id(capsule_id, field_name="capsule_id")
    return assert_under_root(SHARED_CAPSULES_ROOT / capsule_id / "manifest.yaml")


def container_name(instance_id: str, service: DockerService | str) -> str:
    """Return a deterministic container name."""

    instance_id = validate_safe_id(instance_id, field_name="instance_id")
    service_name = enum_value(service)

    if service_name not in CANONICAL_DOCKER_SERVICES:
        raise ComposeRenderError(f"non-canonical service name: {service_name}")

    return f"kx-{instance_id}-{service_name}"


def service_dns_name(instance_id: str | None, service: DockerService | str) -> str:
    """Return a Docker-network-reachable name for a service.

    Compose service names normally resolve on the project network, but the
    generated Traefik file-provider config is easier to debug when it targets
    deterministic container names. Docker's user-defined bridge DNS resolves
    both service aliases and container names.
    """

    if instance_id:
        return container_name(instance_id, service)
    return enum_value(service)


def env_file_path(instance_id: str, filename: str) -> str:
    """Return canonical Compose env-file path.

    The instance_id argument is retained for API compatibility.
    """

    validate_safe_id(instance_id, field_name="instance_id")
    return generated_env_file(filename)


def service_log_dir(instance_id: str, service: DockerService | str) -> str:
    """Return stable service log directory text."""

    validate_safe_id(instance_id, field_name="instance_id")
    service_name = enum_value(service)
    return path_text(INSTANCES_ROOT / instance_id / "logs" / service_name)


def traefik_dynamic_file(instance_id: str) -> Path:
    """Return canonical Traefik dynamic config path."""

    return assert_under_root(instance_state_dir(instance_id) / TRAEFIK_DYNAMIC_FILENAME)


def runtime_environment(options: ComposeRenderOptions) -> list[str]:
    """Return canonical non-secret KX_* runtime environment entries."""

    options = options.normalized()

    return [
        f"KX_INSTANCE_ID={options.instance_id}",
        f"KX_CAPSULE_ID={options.capsule_id}",
        f"KX_APP_VERSION={APP_VERSION}",
        f"KX_PARAM_VERSION={PARAM_VERSION}",
        f"KX_NETWORK_PROFILE={options.network_profile}",
        f"KX_EXPOSURE_MODE={options.exposure_mode}",
        f"KX_PUBLIC_MODE_ENABLED={str(options.public_mode_enabled).lower()}",
        f"KX_PUBLIC_MODE_EXPIRES_AT={options.public_mode_expires_at or ''}",
        f"KX_HOST={options.host}",
        f"KX_CAPSULE_MANIFEST={path_text(capsule_manifest_file(options.capsule_id))}",
        "KX_REQUIRE_SIGNED_CAPSULE=true",
        "KX_GENERATE_SECRETS_ON_INSTALL=true",
        "KX_ALLOW_UNKNOWN_IMAGES=false",
        "KX_ALLOW_PRIVILEGED_CONTAINERS=false",
        "KX_ALLOW_DOCKER_SOCKET_MOUNT=false",
        "KX_ALLOW_HOST_NETWORK=false",
        "KX_BACKUP_ENABLED=true",
    ]


def runtime_environment_mapping(options: ComposeRenderOptions) -> dict[str, str]:
    """Return canonical non-secret KX_* runtime env as a mapping."""

    result: dict[str, str] = {}
    for item in runtime_environment(options):
        key, _, value = item.partition("=")
        result[key] = value
    return result


def port_bindings_for_profile(options: ComposeRenderOptions) -> list[str]:
    """Return safe Traefik port bindings for the selected network profile.

    Only 80/443 are ever returned. Internal service ports are never published.
    """

    options = options.normalized()

    http_port = int(ALLOWED_ENTRY_PORTS["http_redirect"])
    https_port = int(ALLOWED_ENTRY_PORTS["https"])

    bindings: list[str] = []

    if options.network_profile == NetworkProfile.OFFLINE.value:
        return bindings

    if options.network_profile == NetworkProfile.LOCAL_ONLY.value:
        if options.allow_http_on_local_only and options.bind_http:
            bindings.append(f"127.0.0.1:{http_port}:{http_port}")
        if options.bind_https:
            bindings.append(f"127.0.0.1:{https_port}:{https_port}")
        return bindings

    if options.network_profile in {
        NetworkProfile.INTRANET_PRIVATE.value,
        NetworkProfile.PUBLIC_VPS.value,
    }:
        if options.bind_http:
            bindings.append(f"{http_port}:{http_port}")
        if options.bind_https:
            bindings.append(f"{https_port}:{https_port}")
        return bindings

    if options.network_profile in {
        NetworkProfile.PRIVATE_TUNNEL.value,
        NetworkProfile.PUBLIC_TEMPORARY.value,
    }:
        if options.bind_https:
            bindings.append(f"127.0.0.1:{https_port}:{https_port}")
        return bindings

    raise ComposeRenderError(f"unsupported network_profile: {options.network_profile}")


def base_service_defaults(instance_id: str, service: DockerService | str) -> dict[str, Any]:
    """Return safe defaults common to Konnaxion runtime containers."""

    service_name = enum_value(service)

    return {
        "container_name": container_name(instance_id, service_name),
        "restart": "unless-stopped",
        "security_opt": ["no-new-privileges:true"],
        "read_only": False,
        "privileged": False,
        "networks": [PRIVATE_NETWORK],
        "logging": {
            "driver": "json-file",
            "options": {
                "max-size": "10m",
                "max-file": "5",
            },
        },
    }


def normalize_traefik_host(host: Any) -> str:
    """Normalize a host value for Traefik Host(...) rules."""

    value = str(host or "").strip()
    if value.startswith(("http://", "https://")):
        value = value.split("://", 1)[1]
    value = value.strip().strip("/")
    value = value.split("/", 1)[0].strip()
    if "@" in value:
        value = value.rsplit("@", 1)[-1].strip()

    if not value:
        raise ComposeRenderError("host must not be empty")

    return value


def _looks_like_ip_or_sslip(host: str) -> bool:
    """Return True for hosts where an automatic www alias would be wrong."""

    lower = host.lower()
    if lower in _LOCAL_HOSTS:
        return True
    if lower.endswith(".sslip.io"):
        return True
    return all(part.isdigit() for part in lower.split(".") if part)


def default_public_host_aliases(host: str) -> tuple[str, ...]:
    """Return safe default aliases for a canonical public host."""

    normalized = normalize_traefik_host(host)
    lower = normalized.lower()

    if _looks_like_ip_or_sslip(lower):
        return ()

    # Operators almost always expect apex + www to be routed together.
    if not lower.startswith("www.") and "." in lower:
        return (f"www.{normalized}",)

    if lower.startswith("www."):
        return (normalized[4:],)

    return ()


def normalized_host_aliases(host: str, aliases: Iterable[Any] | None = None) -> tuple[str, ...]:
    """Normalize and dedupe aliases, excluding the canonical host itself."""

    canonical = normalize_traefik_host(host)
    candidates: list[Any] = [*default_public_host_aliases(canonical)]

    for alias in aliases or ():
        if alias in (None, ""):
            continue
        candidates.append(alias)

    values: list[str] = []
    for candidate in candidates:
        try:
            normalized = normalize_traefik_host(candidate)
        except ComposeRenderError:
            continue
        if normalized == canonical:
            continue
        if normalized not in values:
            values.append(normalized)

    return tuple(values)


def traefik_host_rule(host: str, aliases: Iterable[Any] | None = None) -> str:
    """Return a Traefik Host(...) rule for canonical host plus aliases."""

    canonical = normalize_traefik_host(host)
    hosts = [canonical, *normalized_host_aliases(canonical, aliases)]

    if len(hosts) == 1:
        return f"Host(`{hosts[0]}`)"

    return "(" + " || ".join(f"Host(`{item}`)" for item in hosts) + ")"


def traefik_labels(host: str, *, host_aliases: Iterable[Any] | None = None) -> list[str]:
    """Return Traefik labels used by tests and operator inspection.

    Runtime routing is provided by Traefik's file provider, not Docker labels,
    because the runtime intentionally does not mount the Docker socket. These
    labels are kept for diagnostics and compatibility with existing tests.
    """

    host_rule = traefik_host_rule(host, host_aliases)

    return [
        "traefik.enable=true",
        f"traefik.http.routers.kx-frontend.rule={host_rule} && PathPrefix(`/`)",
        "traefik.http.routers.kx-frontend.entrypoints=websecure",
        "traefik.http.routers.kx-frontend.tls=true",
        "traefik.http.routers.kx-frontend.service=frontend-next",
        "traefik.http.services.frontend-next.loadbalancer.server.port=3000",
        f"traefik.http.routers.kx-api.rule={host_rule} && PathPrefix(`/api/`)",
        "traefik.http.routers.kx-api.entrypoints=websecure",
        "traefik.http.routers.kx-api.tls=true",
        "traefik.http.routers.kx-api.service=django-api",
        "traefik.http.services.django-api.loadbalancer.server.port=5000",
        f"traefik.http.routers.kx-admin.rule={host_rule} && PathPrefix(`/admin/`)",
        "traefik.http.routers.kx-admin.entrypoints=websecure",
        "traefik.http.routers.kx-admin.tls=true",
        "traefik.http.routers.kx-admin.service=django-api",
        f"traefik.http.routers.kx-media.rule={host_rule} && PathPrefix(`/media/`)",
        "traefik.http.routers.kx-media.entrypoints=websecure",
        "traefik.http.routers.kx-media.tls=true",
        "traefik.http.routers.kx-media.service=media-nginx",
        "traefik.http.services.media-nginx.loadbalancer.server.port=80",
    ]


def render_traefik_dynamic_config(
    host: str,
    *,
    instance_id: str | None = None,
    host_aliases: Iterable[Any] | None = None,
    cert_resolver: str | None = None,
) -> dict[str, Any]:
    """Render Traefik file-provider dynamic routing config."""

    host = normalize_traefik_host(host)
    host_rule = traefik_host_rule(host, host_aliases)
    tls_config: dict[str, Any] = (
        {"certResolver": normalize_traefik_acme_resolver(cert_resolver)}
        if cert_resolver
        else {}
    )

    frontend_target = service_dns_name(instance_id, DockerService.FRONTEND_NEXT)
    django_target = service_dns_name(instance_id, DockerService.DJANGO_API)
    media_target = service_dns_name(instance_id, DockerService.MEDIA_NGINX)

    return {
        "http": {
            "routers": {
                "kx-frontend": {
                    "rule": f"{host_rule} && PathPrefix(`/`)",
                    "entryPoints": ["websecure"],
                    "service": "frontend-next",
                    "tls": dict(tls_config),
                    "priority": 1,
                },
                "kx-api": {
                    "rule": f"{host_rule} && PathPrefix(`/api/`)",
                    "entryPoints": ["websecure"],
                    "service": "django-api",
                    "tls": dict(tls_config),
                    "priority": 100,
                },
                "kx-admin": {
                    "rule": f"{host_rule} && PathPrefix(`/admin/`)",
                    "entryPoints": ["websecure"],
                    "service": "django-api",
                    "tls": dict(tls_config),
                    "priority": 100,
                },
                "kx-media": {
                    "rule": f"{host_rule} && PathPrefix(`/media/`)",
                    "entryPoints": ["websecure"],
                    "service": "media-nginx",
                    "tls": dict(tls_config),
                    "priority": 100,
                },
            },
            "services": {
                "frontend-next": {
                    "loadBalancer": {
                        "servers": [
                            {"url": f"http://{frontend_target}:3000"}
                        ]
                    }
                },
                "django-api": {
                    "loadBalancer": {
                        "servers": [
                            {"url": f"http://{django_target}:5000"}
                        ]
                    }
                },
                "media-nginx": {
                    "loadBalancer": {
                        "servers": [
                            {"url": f"http://{media_target}:80"}
                        ]
                    }
                },
            },
            "middlewares": {
                "secure-headers": {
                    "headers": {
                        "browserXssFilter": True,
                        "contentTypeNosniff": True,
                        "frameDeny": True,
                    }
                }
            },
        }
    }


def django_socket_healthcheck() -> dict[str, Any]:
    """Return a Django healthcheck that does not depend on wget/curl/routes."""

    return {
        "test": [
            "CMD-SHELL",
            "python -c \"import socket; sock=socket.create_connection(('127.0.0.1',5000),5); sock.close()\"",
        ],
        "interval": "30s",
        "timeout": "5s",
        "retries": 10,
    }


def media_nginx_healthcheck() -> dict[str, Any]:
    """Return a media-nginx healthcheck compatible with stock nginx images."""

    return {
        "test": ["CMD-SHELL", "nginx -t >/dev/null 2>&1"],
        "interval": "30s",
        "timeout": "5s",
        "retries": 5,
    }


def render_compose_spec(options: ComposeRenderOptions) -> dict[str, Any]:
    """Render the canonical Konnaxion Docker Compose runtime spec."""

    options = options.normalized()
    resolved_image_map = resolve_runtime_image_map(options)
    images = ComposeImageSet.from_mapping(resolved_image_map)
    environment = runtime_environment(options)
    services: dict[str, Any] = {}

    acme_enabled = traefik_acme_enabled(options)
    acme_resolver = options.traefik_acme_resolver

    traefik_command = [
        "--providers.file.filename=/etc/traefik/dynamic/traefik-dynamic.yml",
        "--providers.file.watch=true",
        "--entrypoints.web.address=:80",
        "--entrypoints.websecure.address=:443",
        "--entrypoints.web.http.redirections.entrypoint.to=websecure",
        "--entrypoints.web.http.redirections.entrypoint.scheme=https",
        "--api.dashboard=false",
    ]

    traefik_volumes = [
        f"{path_text(generated_instance_root(options) / 'state' / TRAEFIK_DYNAMIC_FILENAME)}:/etc/traefik/dynamic/traefik-dynamic.yml:ro",
        f"{generated_volume_path(options, 'logs')}/traefik:/var/log/traefik",
    ]

    if acme_enabled:
        traefik_command.extend(
            [
                f"--certificatesresolvers.{acme_resolver}.acme.email={traefik_acme_email(options)}",
                f"--certificatesresolvers.{acme_resolver}.acme.storage=/{TRAEFIK_ACME_DIRNAME}/{TRAEFIK_ACME_FILENAME}",
                f"--certificatesresolvers.{acme_resolver}.acme.httpchallenge=true",
                f"--certificatesresolvers.{acme_resolver}.acme.httpchallenge.entrypoint=web",
            ]
        )
        traefik_volumes.append(
            f"{path_text(generated_instance_root(options) / 'state' / TRAEFIK_ACME_DIRNAME)}:/{TRAEFIK_ACME_DIRNAME}"
        )

    traefik = base_service_defaults(options.instance_id, DockerService.TRAEFIK)
    traefik.update(
        {
            "image": images.for_service(DockerService.TRAEFIK),
            "command": traefik_command,
            "ports": port_bindings_for_profile(options),
            "volumes": traefik_volumes,
            "labels": traefik_labels(options.host, host_aliases=options.host_aliases),
            "networks": [PUBLIC_NETWORK, PRIVATE_NETWORK],
            "environment": environment,
        }
    )
    services[DockerService.TRAEFIK.value] = traefik

    frontend = base_service_defaults(options.instance_id, DockerService.FRONTEND_NEXT)
    frontend.update(
        {
            "image": images.for_service(DockerService.FRONTEND_NEXT),
            "expose": [str(INTERNAL_ONLY_PORTS[DockerService.FRONTEND_NEXT])],
            "env_file": [
                generated_env_file(KX_ENV_FILE),
                generated_env_file(FRONTEND_ENV_FILE),
            ],
            "environment": environment,
            "depends_on": [DockerService.DJANGO_API.value],
        }
    )
    apply_pull_policy(
        frontend,
        DockerService.FRONTEND_NEXT.value,
        images.for_service(DockerService.FRONTEND_NEXT),
    )
    services[DockerService.FRONTEND_NEXT.value] = frontend

    django = base_service_defaults(options.instance_id, DockerService.DJANGO_API)
    django.update(
        {
            "image": images.for_service(DockerService.DJANGO_API),
            "command": "/start",
            "expose": [str(INTERNAL_ONLY_PORTS[DockerService.DJANGO_API])],
            "env_file": [
                generated_env_file(KX_ENV_FILE),
                generated_env_file(DJANGO_ENV_FILE),
                generated_env_file(POSTGRES_ENV_FILE),
                generated_env_file(REDIS_ENV_FILE),
            ],
            "environment": environment,
            "depends_on": {
                DockerService.POSTGRES.value: {"condition": "service_healthy"},
                DockerService.REDIS.value: {"condition": "service_healthy"},
            },
            "volumes": [
                f"{generated_volume_path(options, 'media')}:/app/media",
                f"{generated_volume_path(options, 'logs')}/django-api:/app/logs",
            ],
            "healthcheck": django_socket_healthcheck(),
        }
    )
    apply_pull_policy(
        django,
        DockerService.DJANGO_API.value,
        images.for_service(DockerService.DJANGO_API),
    )
    services[DockerService.DJANGO_API.value] = django

    postgres = base_service_defaults(options.instance_id, DockerService.POSTGRES)
    postgres.update(
        {
            "image": images.for_service(DockerService.POSTGRES),
            "expose": [str(INTERNAL_ONLY_PORTS[DockerService.POSTGRES])],
            "env_file": [generated_env_file(POSTGRES_ENV_FILE)],
            "environment": environment,
            "volumes": [
                f"{generated_volume_path(options, 'postgres')}:/var/lib/postgresql/data",
            ],
            "networks": [DATA_NETWORK],
            "healthcheck": {
                "test": ["CMD-SHELL", "pg_isready -U konnaxion -d konnaxion"],
                "interval": "30s",
                "timeout": "5s",
                "retries": 10,
            },
        }
    )
    services[DockerService.POSTGRES.value] = postgres

    redis = base_service_defaults(options.instance_id, DockerService.REDIS)
    redis.update(
        {
            "image": images.for_service(DockerService.REDIS),
            "command": ["redis-server", "--appendonly", "yes"],
            "expose": [str(INTERNAL_ONLY_PORTS[DockerService.REDIS])],
            "env_file": [generated_env_file(REDIS_ENV_FILE)],
            "environment": environment,
            "volumes": [
                f"{generated_volume_path(options, 'redis')}:/data",
            ],
            "networks": [DATA_NETWORK],
            "healthcheck": {
                "test": ["CMD", "redis-cli", "ping"],
                "interval": "30s",
                "timeout": "5s",
                "retries": 10,
            },
        }
    )
    services[DockerService.REDIS.value] = redis

    celeryworker = base_service_defaults(options.instance_id, DockerService.CELERYWORKER)
    celeryworker.update(
        {
            "image": images.for_service(DockerService.CELERYWORKER),
            "command": "/start-celeryworker",
            "env_file": [
                generated_env_file(KX_ENV_FILE),
                generated_env_file(DJANGO_ENV_FILE),
                generated_env_file(POSTGRES_ENV_FILE),
                generated_env_file(REDIS_ENV_FILE),
            ],
            "environment": environment,
            "depends_on": {
                DockerService.DJANGO_API.value: {"condition": "service_healthy"},
                DockerService.REDIS.value: {"condition": "service_healthy"},
            },
            "volumes": [
                f"{generated_volume_path(options, 'media')}:/app/media",
                f"{generated_volume_path(options, 'logs')}/celeryworker:/app/logs",
            ],
        }
    )
    apply_pull_policy(
        celeryworker,
        DockerService.CELERYWORKER.value,
        images.for_service(DockerService.CELERYWORKER),
    )
    services[DockerService.CELERYWORKER.value] = celeryworker

    celerybeat = base_service_defaults(options.instance_id, DockerService.CELERYBEAT)
    celerybeat.update(
        {
            "image": images.for_service(DockerService.CELERYBEAT),
            "command": "/start-celerybeat",
            "env_file": [
                generated_env_file(KX_ENV_FILE),
                generated_env_file(DJANGO_ENV_FILE),
                generated_env_file(POSTGRES_ENV_FILE),
                generated_env_file(REDIS_ENV_FILE),
            ],
            "environment": environment,
            "depends_on": {
                DockerService.DJANGO_API.value: {"condition": "service_healthy"},
                DockerService.REDIS.value: {"condition": "service_healthy"},
            },
            "volumes": [
                f"{generated_volume_path(options, 'logs')}/celerybeat:/app/logs",
            ],
        }
    )
    apply_pull_policy(
        celerybeat,
        DockerService.CELERYBEAT.value,
        images.for_service(DockerService.CELERYBEAT),
    )
    services[DockerService.CELERYBEAT.value] = celerybeat

    if options.include_flower:
        flower = base_service_defaults(options.instance_id, DockerService.FLOWER)
        flower.update(
            {
                "image": images.for_service(DockerService.FLOWER),
                "command": "/start-flower",
                "profiles": ["observability"],
                "expose": [str(INTERNAL_ONLY_PORTS[DockerService.FLOWER])],
                "env_file": [
                    generated_env_file(KX_ENV_FILE),
                    generated_env_file(DJANGO_ENV_FILE),
                    generated_env_file(REDIS_ENV_FILE),
                ],
                "environment": environment,
                "depends_on": {
                    DockerService.REDIS.value: {"condition": "service_healthy"},
                },
                "networks": [PRIVATE_NETWORK, DATA_NETWORK],
            }
        )
        apply_pull_policy(
            flower,
            DockerService.FLOWER.value,
            images.for_service(DockerService.FLOWER),
        )
        services[DockerService.FLOWER.value] = flower

    media = base_service_defaults(options.instance_id, DockerService.MEDIA_NGINX)
    media.update(
        {
            "image": images.for_service(DockerService.MEDIA_NGINX),
            "expose": ["80"],
            "environment": environment,
            "volumes": [
                f"{generated_volume_path(options, 'media')}:/usr/share/nginx/html/media:ro",
                f"{generated_volume_path(options, 'logs')}/media-nginx:/var/log/nginx",
            ],
            "healthcheck": media_nginx_healthcheck(),
        }
    )
    services[DockerService.MEDIA_NGINX.value] = media

    compose: dict[str, Any] = {
        "name": f"konnaxion-{options.instance_id}",
        "services": services,
        "networks": {
            PUBLIC_NETWORK: {
                "name": f"kx-{options.instance_id}-public",
                "driver": "bridge",
            },
            PRIVATE_NETWORK: {
                "name": f"kx-{options.instance_id}-private",
                "driver": "bridge",
                "internal": True,
            },
            DATA_NETWORK: {
                "name": f"kx-{options.instance_id}-data",
                "driver": "bridge",
                "internal": True,
            },
        },
        "x-konnaxion": {
            "instance_id": options.instance_id,
            "capsule_id": options.capsule_id,
            "capsule_manifest": path_text(capsule_manifest_file(options.capsule_id)),
            "network_profile": options.network_profile,
            "exposure_mode": options.exposure_mode,
            "public_mode_enabled": options.public_mode_enabled,
            "public_mode_expires_at": options.public_mode_expires_at,
            "env_dir": path_text(instance_env_dir_path(options.instance_id)),
            "image_map": dict(resolved_image_map),
            "local_app_image_services": sorted(LOCAL_APP_IMAGE_SERVICES),
            "traefik_acme_enabled": acme_enabled,
            "traefik_acme_resolver": acme_resolver if acme_enabled else None,
        },
    }

    for service_name in (
        DockerService.DJANGO_API.value,
        DockerService.CELERYWORKER.value,
        DockerService.CELERYBEAT.value,
    ):
        compose["services"][service_name]["networks"] = [PRIVATE_NETWORK, DATA_NETWORK]

    validate_compose_spec(compose)
    return compose


def generate_runtime_compose(
    *,
    instance_id: str,
    capsule_id: str = DEFAULT_CAPSULE_ID,
    instance_root: str | Path | None = None,
    host: str = "konnaxion.local",
    network_profile: NetworkProfile | str = DEFAULT_NETWORK_PROFILE,
    exposure_mode: ExposureMode | str = DEFAULT_EXPOSURE_MODE,
    public_mode_enabled: bool | None = None,
    public_mode_expires_at: str | None = None,
    image_map: Mapping[str, str] | None = None,
    include_flower: bool = False,
) -> dict[str, Any]:
    """Compatibility wrapper used by tests and Agent actions."""

    profile_value = enum_value(network_profile)
    exposure_value = enum_value(exposure_mode)

    inferred_public_mode = profile_value in {
        NetworkProfile.PUBLIC_TEMPORARY.value,
        NetworkProfile.PUBLIC_VPS.value,
    } or exposure_value in {
        ExposureMode.TEMPORARY_TUNNEL.value,
        ExposureMode.PUBLIC.value,
    }

    options = ComposeRenderOptions(
        instance_id=instance_id,
        host=host,
        capsule_id=capsule_id,
        instance_root=Path(instance_root) if instance_root is not None else None,
        network_profile=profile_value,
        exposure_mode=exposure_value,
        public_mode_enabled=inferred_public_mode if public_mode_enabled is None else public_mode_enabled,
        public_mode_expires_at=public_mode_expires_at,
        image_map=image_map,
        include_flower=include_flower,
        ensure_env_files=False,
    )

    return render_compose_spec(options)


def validate_compose_spec(compose: Mapping[str, Any]) -> None:
    """Validate generated Compose policy before writing."""

    services = compose.get("services")
    if not isinstance(services, Mapping):
        raise ComposeValidationError("compose must contain a services mapping")

    service_names = set(services.keys())
    canonical = set(CANONICAL_DOCKER_SERVICES)

    forbidden_aliases = service_names & FORBIDDEN_SERVICE_ALIASES
    if forbidden_aliases:
        raise ComposeValidationError(
            f"forbidden non-canonical service names: {sorted(forbidden_aliases)}"
        )

    unknown = service_names - canonical
    if unknown:
        raise ComposeValidationError(f"unknown non-canonical service names: {sorted(unknown)}")

    required = {
        DockerService.TRAEFIK.value,
        DockerService.FRONTEND_NEXT.value,
        DockerService.DJANGO_API.value,
        DockerService.POSTGRES.value,
        DockerService.REDIS.value,
        DockerService.CELERYWORKER.value,
        DockerService.CELERYBEAT.value,
        DockerService.MEDIA_NGINX.value,
    }

    missing = required - service_names
    if missing:
        raise ComposeValidationError(f"missing required services: {sorted(missing)}")

    for service_name, service in services.items():
        if not isinstance(service, Mapping):
            raise ComposeValidationError(f"service {service_name} must be a mapping")

        if service.get("privileged") is True:
            raise ComposeValidationError(f"service {service_name} must not be privileged")

        if service.get("network_mode") == "host":
            raise ComposeValidationError(f"service {service_name} must not use host networking")

        if str(service.get("pid") or "").lower() == "host":
            raise ComposeValidationError(f"service {service_name} must not use host PID namespace")

        if str(service.get("ipc") or "").lower() == "host":
            raise ComposeValidationError(f"service {service_name} must not use host IPC namespace")

        for volume in service.get("volumes", []) or []:
            if isinstance(volume, Mapping):
                source = str(volume.get("source") or "").strip()
            else:
                volume_text = str(volume)
                # ``C:/host/path:/container/path`` contains a drive colon.
                # Preserve the complete Windows source instead of treating
                # ``C`` as a named volume.
                if (
                    len(volume_text) >= 3
                    and volume_text[1] == ":"
                    and volume_text[0].isalpha()
                    and volume_text[2] in {"/", "\\"}
                ):
                    separator = volume_text.find(":", 2)
                    source = (
                        volume_text[:separator].strip()
                        if separator >= 0
                        else volume_text.strip()
                    )
                else:
                    source = volume_text.split(":", 1)[0].strip()

            lowered = source.lower()
            if lowered in {"/var/run/docker.sock", "/run/docker.sock"}:
                raise ComposeValidationError(
                    f"service {service_name} must not mount Docker socket"
                )

            # Named volumes are allowed. Absolute host bind mounts must stay
            # inside the configured KX_ROOT. This must work for both POSIX
            # roots and Windows drive paths used by the local Manager.
            is_windows_absolute = (
                len(source) >= 3
                and source[1] == ":"
                and source[0].isalpha()
                and source[2] in {"/", "\\"}
            )
            if source.startswith("/") or is_windows_absolute:
                try:
                    assert_under_root(source)
                except Exception as exc:
                    raise ComposeValidationError(
                        f"service {service_name} uses forbidden host bind mount: {source}"
                    ) from exc

        ports = service.get("ports", []) or []
        if service_name != DockerService.TRAEFIK.value and ports:
            raise ComposeValidationError(
                f"only traefik may publish ports; {service_name} publishes {ports}"
            )

        for port in ports:
            published, target = parse_compose_port(port)
            unsafe = {published, target} & set(int(item) for item in FORBIDDEN_PUBLIC_PORTS)

            if unsafe:
                raise ComposeValidationError(
                    f"service {service_name} exposes forbidden public port(s): {sorted(unsafe)}"
                )

            if target not in {
                int(ALLOWED_ENTRY_PORTS["http_redirect"]),
                int(ALLOWED_ENTRY_PORTS["https"]),
            }:
                raise ComposeValidationError(
                    f"service {service_name} exposes non-entrypoint target port: {target}"
                )

    networks = compose.get("networks", {})
    if not isinstance(networks, Mapping):
        raise ComposeValidationError("compose networks must be a mapping")

    for internal_network in (PRIVATE_NETWORK, DATA_NETWORK):
        network_spec = networks.get(internal_network)
        if not isinstance(network_spec, Mapping) or network_spec.get("internal") is not True:
            raise ComposeValidationError(f"{internal_network} must be an internal network")


def parse_compose_port(port: Any) -> tuple[int, int]:
    """Parse Compose short-form port syntax."""

    if isinstance(port, Mapping):
        return int(port.get("published")), int(port.get("target"))

    text = str(port)
    parts = text.split(":")

    if len(parts) == 1:
        value = int(parts[0])
        return value, value

    if len(parts) == 2:
        return int(parts[0]), int(parts[1])

    if len(parts) == 3:
        return int(parts[1]), int(parts[2])

    raise ComposeValidationError(f"unsupported compose port syntax: {port!r}")


def render_runtime_files(options: ComposeRenderOptions) -> tuple[str, str, dict[str, Any], dict[str, Any]]:
    """Render Compose YAML and Traefik dynamic YAML."""

    options = options.normalized()
    compose_spec = render_compose_spec(options)
    traefik_spec = render_traefik_dynamic_config(
        options.host,
        instance_id=options.instance_id,
        host_aliases=options.host_aliases,
        cert_resolver=(
            options.traefik_acme_resolver if traefik_acme_enabled(options) else None
        ),
    )
    return yaml_dump(compose_spec), yaml_dump(traefik_spec), compose_spec, traefik_spec


def write_runtime_compose(options: ComposeRenderOptions) -> ComposeWriteResult:
    """Render and write docker-compose.runtime.yml and traefik-dynamic.yml."""

    options = options.normalized()

    ensure_dir(instance_state_dir(options.instance_id))
    _ensure_runtime_dirs(options)

    env_files: dict[str, str] = {}
    env_warnings: list[str] = []

    if options.ensure_env_files:
        env_result = ensure_instance_env_files(options)
        env_files = {key: str(value) for key, value in env_result.get("env_files", {}).items()}
        env_warnings.extend(str(item) for item in env_result.get("warnings", []) or [])

        if not env_result.get("ok", False):
            raise ComposeRenderError(
                str(env_result.get("message") or "failed to ensure instance env files")
            )

    compose_yaml, traefik_yaml, compose_spec, _traefik_spec = render_runtime_files(options)

    compose_path = atomic_write_text(instance_compose_file(options.instance_id), compose_yaml)
    traefik_path = atomic_write_text(traefik_dynamic_file(options.instance_id), traefik_yaml)

    services = tuple(compose_spec["services"].keys())
    warnings: list[str] = list(env_warnings)

    if options.network_profile in {
        NetworkProfile.PRIVATE_TUNNEL.value,
        NetworkProfile.PUBLIC_TEMPORARY.value,
    }:
        warnings.append(
            "tunnel profile rendered with localhost-only Traefik binding; "
            "a separate approved tunnel adapter must provide remote access"
        )

    if options.network_profile == NetworkProfile.OFFLINE.value:
        warnings.append("offline profile rendered with no published ports")

    manifest_path = capsule_manifest_file(options.capsule_id)

    return ComposeWriteResult(
        instance_id=options.instance_id,
        compose_file=str(compose_path),
        traefik_dynamic_file=str(traefik_path),
        network_profile=options.network_profile,
        exposure_mode=options.exposure_mode,
        services=services,
        warnings=tuple(warnings),
        env_dir=str(instance_env_dir_path(options.instance_id)),
        env_files=env_files,
        manifest_file=str(manifest_path),
    )


def _ensure_runtime_dirs(options: ComposeRenderOptions) -> None:
    """Create runtime directories referenced by the compose file."""

    root = generated_instance_root(options)
    for dirname in (
        ENV_DIRNAME,
        "state",
        "logs",
        "media",
        "postgres",
        "redis",
    ):
        ensure_dir(root / dirname)

    if traefik_acme_enabled(options):
        acme_dir = root / "state" / TRAEFIK_ACME_DIRNAME
        ensure_dir(acme_dir)
        acme_file = acme_dir / TRAEFIK_ACME_FILENAME
        if not acme_file.exists():
            acme_file.write_text("{}", encoding="utf-8")
        acme_file.chmod(0o600)

    for service_name in (
        DockerService.TRAEFIK.value,
        DockerService.DJANGO_API.value,
        DockerService.CELERYWORKER.value,
        DockerService.CELERYBEAT.value,
        DockerService.MEDIA_NGINX.value,
    ):
        ensure_dir(root / "logs" / service_name)


# ---------------------------------------------------------------------
# Env/secrets helpers
# ---------------------------------------------------------------------


def ensure_instance_env_files(options: ComposeRenderOptions) -> dict[str, Any]:
    """Ensure instance env files exist.

    Preferred path:
        kx_agent.instances.secrets.write_instance_env_files()

    Fallback path:
        local minimal env-file writer. This keeps bootstrap/deploy usable when
        the Agent code on the Droplet is mid-upgrade or an older secrets module
        has a different function signature.

    Important:
        The delegated writer may exist and return success while creating stale
        public host values. This function verifies the canonical env files and
        rewrites public runtime values when host/profile changes, preserving
        existing secrets.
    """

    options = options.normalized()

    delegated = _try_delegate_env_file_creation(options)
    if delegated.get("ok"):
        completeness = _env_files_completeness(options.instance_id, expected_host=options.host)

        if completeness.get("ok"):
            env_files = env_file_paths(options.instance_id)
            return {
                "ok": True,
                "source": delegated.get("source", "kx_agent.instances.secrets"),
                "env_dir": str(instance_env_dir_path(options.instance_id)),
                "env_files": {name: str(path) for name, path in env_files.items() if path.exists()},
                "warnings": delegated.get("warnings", []),
                "data": delegated.get("data", {}),
                "delegate": delegated,
            }

        fallback = _write_minimal_instance_env_files(options)
        fallback.setdefault("warnings", [])
        fallback["warnings"] = [
            *list(delegated.get("warnings", []) or []),
            *list(fallback.get("warnings", []) or []),
            "completed missing or stale canonical env files after delegated secrets writer",
        ]
        fallback["delegate"] = delegated
        fallback["pre_fallback_completeness"] = completeness
        return fallback

    fallback = _write_minimal_instance_env_files(options)
    fallback.setdefault("warnings", [])
    fallback["warnings"] = [
        *list(fallback.get("warnings", []) or []),
        "used compose.py fallback env writer because kx_agent.instances.secrets delegation failed",
    ]
    fallback["delegate_error"] = delegated
    return fallback


def _env_files_completeness(instance_id: str, *, expected_host: str | None = None) -> dict[str, Any]:
    paths = env_file_paths(instance_id)
    missing_files = sorted(name for name, path in paths.items() if not path.exists())
    env_validation = validate_instance_env_for_security_gate(instance_id)
    host_validation = _validate_env_host_values(instance_id, expected_host) if expected_host else {"ok": True, "issues": []}

    return {
        "ok": not missing_files and bool(env_validation.get("ok")) and bool(host_validation.get("ok")),
        "missing_files": missing_files,
        "env_validation": env_validation,
        "host_validation": host_validation,
        "env_dir": str(instance_env_dir_path(instance_id)),
        "env_files": {name: str(path) for name, path in paths.items() if path.exists()},
    }


def _validate_env_host_values(instance_id: str, expected_host: str | None) -> dict[str, Any]:
    expected = str(expected_host or "").strip()
    if not expected:
        return {"ok": True, "issues": []}

    env = load_instance_env(instance_id)
    issues: list[str] = []
    public_base_url = _public_base_url(expected)

    if env.get("KX_HOST") != expected:
        issues.append(f"KX_HOST expected {expected!r}, found {env.get('KX_HOST')!r}")

    allowed_hosts = _split_csv_env(env.get("DJANGO_ALLOWED_HOSTS"))
    if expected not in allowed_hosts:
        issues.append("DJANGO_ALLOWED_HOSTS does not include expected host")

    if env.get("NEXT_PUBLIC_API_BASE") != f"{public_base_url}/api":
        issues.append("NEXT_PUBLIC_API_BASE is stale or missing")

    if env.get("NEXT_PUBLIC_BACKEND_BASE") != public_base_url:
        issues.append("NEXT_PUBLIC_BACKEND_BASE is stale or missing")

    if env.get("FRONTEND_BASE_URL") != public_base_url:
        issues.append("FRONTEND_BASE_URL is stale or missing")

    return {"ok": not issues, "issues": issues, "expected_host": expected}


def _split_csv_env(value: Any) -> set[str]:
    return {item.strip() for item in str(value or "").replace(";", ",").split(",") if item.strip()}


def _try_delegate_env_file_creation(options: ComposeRenderOptions) -> dict[str, Any]:
    """Try supported signatures from kx_agent.instances.secrets."""

    try:
        from kx_agent.instances import secrets as secrets_module
    except Exception as exc:
        return {
            "ok": False,
            "source": "kx_agent.instances.secrets",
            "error": type(exc).__name__,
            "message": str(exc),
        }

    writer = getattr(secrets_module, "write_instance_env_files", None)
    if not callable(writer):
        return {
            "ok": False,
            "source": "kx_agent.instances.secrets",
            "message": "write_instance_env_files is not available",
        }

    attempts: list[tuple[str, Callable[[], Any]]] = []

    attempts.append(
        (
            "keyword_full",
            lambda: writer(
                instance_id=options.instance_id,
                host=options.host,
                capsule_id=options.capsule_id,
                network_profile=options.network_profile,
                exposure_mode=options.exposure_mode,
                public_mode_enabled=options.public_mode_enabled,
                public_mode_expires_at=options.public_mode_expires_at,
                host_aliases=options.host_aliases,
                overwrite=options.overwrite_env_files,
            ),
        )
    )

    attempts.append(
        (
            "keyword_minimal",
            lambda: writer(
                instance_id=options.instance_id,
                host=options.host,
                network_profile=options.network_profile,
                exposure_mode=options.exposure_mode,
                host_aliases=options.host_aliases,
                overwrite=options.overwrite_env_files,
            ),
        )
    )

    policy_class = getattr(secrets_module, "SecretGenerationPolicy", None)
    if policy_class is not None:
        attempts.append(
            (
                "policy_object",
                lambda: writer(
                    policy_class(
                        instance_id=options.instance_id,
                        host=options.host,
                        capsule_id=options.capsule_id,
                        network_profile=options.network_profile,
                        exposure_mode=options.exposure_mode,
                        public_mode_enabled=options.public_mode_enabled,
                        public_mode_expires_at=options.public_mode_expires_at,
                        host_aliases=options.host_aliases,
                        overwrite=options.overwrite_env_files,
                    )
                ),
            )
        )

    attempts.append(("instance_id_only", lambda: writer(options.instance_id)))

    errors: list[dict[str, str]] = []

    for name, call in attempts:
        try:
            result = call()
        except TypeError as exc:
            errors.append({"attempt": name, "error": type(exc).__name__, "message": str(exc)})
            continue
        except Exception as exc:
            errors.append({"attempt": name, "error": type(exc).__name__, "message": str(exc)})
            continue

        return {
            "ok": True,
            "source": "kx_agent.instances.secrets",
            "attempt": name,
            "data": _json_safe(result),
            "warnings": [],
        }

    return {
        "ok": False,
        "source": "kx_agent.instances.secrets",
        "message": "all write_instance_env_files delegation attempts failed",
        "attempt_errors": errors,
    }


def _write_minimal_instance_env_files(options: ComposeRenderOptions) -> dict[str, Any]:
    """Write minimal safe env files required by runtime containers and Security Gate."""

    options = options.normalized()
    paths = env_file_paths(options.instance_id)
    ensure_dir(instance_env_dir_path(options.instance_id))

    existing = load_instance_env(options.instance_id)

    postgres_password = _existing_or_new_secret(
        existing.get("POSTGRES_PASSWORD"),
        length=40,
        alphabet=string.ascii_letters + string.digits + "-_=+.:;@#%~",
    )
    django_secret_key = _existing_or_new_secret(
        existing.get("DJANGO_SECRET_KEY"),
        length=80,
        alphabet=string.ascii_letters + string.digits + string.punctuation,
    )

    postgres_user = existing.get("POSTGRES_USER") or "konnaxion"
    postgres_db = existing.get("POSTGRES_DB") or "konnaxion"
    database_url = (
        existing.get("DATABASE_URL")
        if _is_safe_secret_value(existing.get("DATABASE_URL"))
        else f"postgres://{postgres_user}:{postgres_password}@postgres:5432/{postgres_db}"
    )

    allowed_hosts = _django_allowed_hosts(
        options.host,
        host_aliases=options.host_aliases,
        instance_id=options.instance_id,
    )
    public_base_url = _public_base_url(options.host)
    trusted_origins = _public_trusted_origins(options.host, options.host_aliases)

    kx_env = {
        **runtime_environment_mapping(options),
    }

    postgres_env = {
        "POSTGRES_USER": postgres_user,
        "POSTGRES_PASSWORD": postgres_password,
        "POSTGRES_DB": postgres_db,
    }

    redis_env = {
        "REDIS_URL": existing.get("REDIS_URL") or "redis://redis:6379/0",
    }

    django_env = {
        "DJANGO_SECRET_KEY": django_secret_key,
        "DJANGO_ALLOWED_HOSTS": allowed_hosts,
        "DJANGO_CSRF_TRUSTED_ORIGINS": trusted_origins,
        "CSRF_TRUSTED_ORIGINS": trusted_origins,
        "CORS_ALLOWED_ORIGINS": trusted_origins,
        "FRONTEND_BASE_URL": public_base_url,
        "POSTGRES_USER": postgres_user,
        "POSTGRES_PASSWORD": postgres_password,
        "POSTGRES_DB": postgres_db,
        "DATABASE_URL": database_url,
        "REDIS_URL": redis_env["REDIS_URL"],
        "KX_INSTANCE_ID": options.instance_id,
        "KX_CAPSULE_ID": options.capsule_id,
        "KX_NETWORK_PROFILE": options.network_profile,
        "KX_EXPOSURE_MODE": options.exposure_mode,
    }

    frontend_env = {
        "NEXT_PUBLIC_API_BASE": f"{public_base_url}/api",
        "NEXT_PUBLIC_BACKEND_BASE": public_base_url,
        "NEXT_TELEMETRY_DISABLED": "1",
        "NODE_OPTIONS": "--max-old-space-size=4096",
        "KX_INSTANCE_ID": options.instance_id,
        "KX_NETWORK_PROFILE": options.network_profile,
    }

    runtime_env = {
        **kx_env,
        **postgres_env,
        **redis_env,
        "DJANGO_SECRET_KEY": django_secret_key,
        "DATABASE_URL": database_url,
        "DJANGO_ALLOWED_HOSTS": allowed_hosts,
        "DJANGO_CSRF_TRUSTED_ORIGINS": trusted_origins,
        "CSRF_TRUSTED_ORIGINS": trusted_origins,
        "CORS_ALLOWED_ORIGINS": trusted_origins,
        "FRONTEND_BASE_URL": public_base_url,
        "NEXT_PUBLIC_API_BASE": frontend_env["NEXT_PUBLIC_API_BASE"],
        "NEXT_PUBLIC_BACKEND_BASE": frontend_env["NEXT_PUBLIC_BACKEND_BASE"],
    }

    file_payloads: dict[str, Mapping[str, Any]] = {
        KX_ENV_FILE: kx_env,
        POSTGRES_ENV_FILE: postgres_env,
        REDIS_ENV_FILE: redis_env,
        DJANGO_ENV_FILE: django_env,
        FRONTEND_ENV_FILE: frontend_env,
        RUNTIME_ENV_FILE: runtime_env,
    }

    written: dict[str, str] = {}

    for filename, payload in file_payloads.items():
        path = paths[filename]
        mode = 0o600 if filename in SECRET_ENV_FILENAMES else 0o640
        atomic_write_text(path, serialize_env(payload), mode=mode)
        written[filename] = str(path)

    completeness = _env_files_completeness(options.instance_id, expected_host=options.host)
    env_validation = dict(completeness.get("env_validation") or {})
    host_validation = dict(completeness.get("host_validation") or {})
    missing_secret_keys = list(env_validation.get("missing") or [])
    invalid_secret_keys = list(env_validation.get("invalid_keys") or [])
    missing_files = list(completeness.get("missing_files") or [])
    host_issues = list(host_validation.get("issues") or [])

    if missing_files or missing_secret_keys or invalid_secret_keys or host_issues:
        return {
            "ok": False,
            "source": "compose.py fallback env writer",
            "message": "required runtime env files, secrets, or public host values are still invalid after env-file write",
            "missing_files": missing_files,
            "missing": missing_secret_keys,
            "invalid_keys": invalid_secret_keys,
            "host_issues": host_issues,
            "env_dir": str(instance_env_dir_path(options.instance_id)),
            "env_files": {name: str(path) for name, path in paths.items() if path.exists()},
            "written": written,
            "warnings": [],
        }

    return {
        "ok": True,
        "source": "compose.py fallback env writer",
        "message": "Instance env files exist.",
        "env_dir": str(instance_env_dir_path(options.instance_id)),
        "env_files": {name: str(path) for name, path in paths.items() if path.exists()},
        "written": written,
        "warnings": [],
    }


def _existing_or_new_secret(value: Any, *, length: int, alphabet: str) -> str:
    text = str(value or "").strip()
    if _is_safe_secret_value(text):
        return text

    return "".join(pysecrets.choice(alphabet) for _ in range(length))


def _is_safe_secret_value(value: Any) -> bool:
    text = str(value or "").strip()
    if not text:
        return False

    if text.lower() in _PLACEHOLDER_SECRET_VALUES:
        return False

    if len(text) < 12:
        return False

    return True


def _django_allowed_hosts(
    host: str,
    *,
    host_aliases: Iterable[Any] | None = None,
    instance_id: str | None = None,
) -> str:
    host_text = normalize_traefik_host(host)
    values = {
        "localhost",
        "127.0.0.1",
        host_text,
        *normalized_host_aliases(host_text, host_aliases),
        DockerService.DJANGO_API.value,
    }

    if instance_id:
        values.add(container_name(instance_id, DockerService.DJANGO_API))

    return ",".join(sorted(item for item in values if item))


def _public_trusted_origins(host: str, host_aliases: Iterable[Any] | None = None) -> str:
    hosts = [normalize_traefik_host(host), *normalized_host_aliases(host, host_aliases)]
    origins: list[str] = []
    for item in hosts:
        for origin in (_public_base_url(item), f"http://{item}"):
            if origin not in origins:
                origins.append(origin)
    return ",".join(origins)


def _public_base_url(host: str) -> str:
    host = str(host).strip() or "127.0.0.1"

    if host.startswith("http://") or host.startswith("https://"):
        return host.rstrip("/")

    if host in {"127.0.0.1", "localhost"}:
        return f"http://{host}"

    return f"https://{host}"


def serialize_env(values: Mapping[str, Any]) -> str:
    """Serialize env mapping into KEY=value lines."""

    lines: list[str] = []

    for key in sorted(values):
        value = values[key]
        if value is None:
            value = ""

        lines.append(f"{key}={format_env_value(value)}")

    return "\n".join(lines) + "\n"


def format_env_value(value: Any) -> str:
    """Format a value for a dotenv file."""

    text = str(value)

    if text == "":
        return ""

    safe_chars = set(string.ascii_letters + string.digits + "._-/:,@%+=~")
    if all(char in safe_chars for char in text):
        return text

    escaped = (
        text.replace("\\", "\\\\")
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace('"', '\\"')
    )
    return f'"{escaped}"'


def parse_env_file(path: str | Path) -> dict[str, str]:
    """Parse a simple dotenv file."""

    env_path = Path(path)
    if not env_path.exists() or not env_path.is_file():
        return {}

    result: dict[str, str] = {}

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()

        if not line or line.startswith("#"):
            continue

        if line.startswith("export "):
            line = line.removeprefix("export ").strip()

        key, sep, value = line.partition("=")
        if not sep:
            continue

        key = key.strip()
        value = value.strip()

        if not key:
            continue

        result[key] = _unquote_env_value(value)

    return result


def _unquote_env_value(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]

    return (
        value.replace("\\n", "\n")
        .replace("\\r", "\r")
        .replace('\\"', '"')
        .replace("\\\\", "\\")
    )


def load_instance_env(instance_id: str) -> dict[str, str]:
    """Load all canonical env files for an instance.

    Later files override earlier files so runtime.env can act as a compatibility
    aggregate for Security Gate / operator checks.
    """

    result: dict[str, str] = {}

    for filename, path in env_file_paths(instance_id).items():
        del filename
        result.update(parse_env_file(path))

    return result


def validate_instance_env_for_security_gate(instance_id: str) -> dict[str, Any]:
    """Validate required Security Gate env keys are present and non-placeholder."""

    env = load_instance_env(instance_id)
    missing = sorted(key for key in REQUIRED_SECURITY_GATE_ENV_KEYS if key not in env)
    invalid = sorted(
        key
        for key in REQUIRED_SECURITY_GATE_ENV_KEYS
        if key in env and not _is_safe_secret_value(env.get(key))
    )

    return {
        "ok": not missing and not invalid,
        "instance_id": instance_id,
        "missing": missing,
        "invalid_keys": invalid,
        "env_dir": str(instance_env_dir_path(instance_id)),
        "env_files": {
            name: str(path)
            for name, path in env_file_paths(instance_id).items()
            if path.exists()
        },
    }


# ---------------------------------------------------------------------
# Manifest / Security Gate context helpers
# ---------------------------------------------------------------------


def read_capsule_manifest(capsule_id: str) -> dict[str, Any]:
    """Read extracted capsule manifest from canonical shared capsule directory."""

    path = capsule_manifest_file(capsule_id)
    if not path.exists() or not path.is_file():
        return {}

    return read_mapping_file(path)


def read_mapping_file(path: str | Path) -> dict[str, Any]:
    """Read a JSON or YAML mapping file."""

    file_path = Path(path)

    try:
        text = file_path.read_text(encoding="utf-8")
    except OSError:
        return {}

    if not text.strip():
        return {}

    data: Any

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        require_yaml()
        try:
            data = yaml.safe_load(text)  # type: ignore[union-attr]
        except Exception:
            return {}

    if isinstance(data, Mapping):
        return _json_safe(dict(data))

    return {}


def manifest_from_compose(compose: Mapping[str, Any]) -> dict[str, Any]:
    """Resolve and read capsule manifest referenced by a compose spec."""

    metadata = compose.get("x-konnaxion")
    if not isinstance(metadata, Mapping):
        metadata = {}

    manifest_path = str(metadata.get("capsule_manifest") or "").strip()
    if manifest_path:
        loaded = read_mapping_file(manifest_path)
        if loaded:
            return loaded

    capsule_id = str(metadata.get("capsule_id") or "").strip()
    if capsule_id:
        return read_capsule_manifest(capsule_id)

    services = compose.get("services")
    if isinstance(services, Mapping):
        for service in services.values():
            if not isinstance(service, Mapping):
                continue

            env_items = service.get("environment") or []
            if isinstance(env_items, Mapping):
                capsule_id = str(env_items.get("KX_CAPSULE_ID") or "").strip()
                if capsule_id:
                    return read_capsule_manifest(capsule_id)
            elif isinstance(env_items, list):
                for item in env_items:
                    key, _, value = str(item).partition("=")
                    if key == "KX_CAPSULE_ID" and value:
                        return read_capsule_manifest(value)

    return {}


def security_context_inputs(
    *,
    instance_id: str,
    compose: Mapping[str, Any] | None = None,
    compose_file: str | Path | None = None,
    capsule_id: str | None = None,
) -> dict[str, Any]:
    """Return compose/manifest/env inputs suitable for Security Gate."""

    if compose is None and compose_file is not None:
        compose = read_compose_file(compose_file)

    compose = dict(compose or {})

    manifest: dict[str, Any] = {}
    if capsule_id:
        manifest = read_capsule_manifest(capsule_id)

    if not manifest and compose:
        manifest = manifest_from_compose(compose)

    env = load_instance_env(instance_id)

    return {
        "instance_id": instance_id,
        "compose": compose,
        "manifest": manifest,
        "env": env,
        "env_validation": validate_instance_env_for_security_gate(instance_id),
    }


def read_compose_file(path: str | Path) -> dict[str, Any]:
    """Read a Compose YAML file and return its mapping."""

    require_yaml()
    safe_path = assert_under_root(path)

    with safe_path.open("r", encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle)  # type: ignore[union-attr]

    if not isinstance(loaded, dict):
        raise ComposeValidationError(f"compose file did not contain a mapping: {safe_path}")

    return loaded


def validate_compose_file(path: str | Path) -> None:
    """Load and validate a generated runtime Compose file."""

    validate_compose_spec(read_compose_file(path))


def _json_safe(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)

    if isinstance(value, Enum):
        return value.value

    if hasattr(value, "to_dict") and callable(value.to_dict):
        data = value.to_dict()
        if isinstance(data, Mapping):
            return _json_safe(dict(data))

    if hasattr(value, "model_dump") and callable(value.model_dump):
        data = value.model_dump()
        if isinstance(data, Mapping):
            return _json_safe(dict(data))

    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}

    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]

    if isinstance(value, list):
        return [_json_safe(item) for item in value]

    if isinstance(value, set):
        return sorted(_json_safe(item) for item in value)

    return value


class ComposeRenderer:
    """Service object used by Agent actions and tests."""

    def render(self, options: ComposeRenderOptions) -> dict[str, Any]:
        return render_compose_spec(options)

    def render_files(self, options: ComposeRenderOptions) -> tuple[str, str, dict[str, Any], dict[str, Any]]:
        return render_runtime_files(options)

    def write(self, options: ComposeRenderOptions) -> ComposeWriteResult:
        return write_runtime_compose(options)

    def validate(self, compose: Mapping[str, Any]) -> None:
        validate_compose_spec(compose)

    def ensure_env(self, options: ComposeRenderOptions) -> dict[str, Any]:
        return ensure_instance_env_files(options)

    def security_context(
        self,
        *,
        instance_id: str,
        compose: Mapping[str, Any] | None = None,
        compose_file: str | Path | None = None,
        capsule_id: str | None = None,
    ) -> dict[str, Any]:
        return security_context_inputs(
            instance_id=instance_id,
            compose=compose,
            compose_file=compose_file,
            capsule_id=capsule_id,
        )


__all__ = [
    "COMPOSE_FILENAME",
    "DATA_NETWORK",
    "DEFAULT_IMAGES",
    "DJANGO_ENV_FILE",
    "ENV_DIRNAME",
    "ENV_FILENAMES",
    "FORBIDDEN_SERVICE_ALIASES",
    "FRONTEND_ENV_FILE",
    "IMAGE_ENV_KEYS",
    "KONNAXION_ROOT",
    "KX_ENV_FILE",
    "LOCAL_APP_IMAGE_SERVICES",
    "POSTGRES_ENV_FILE",
    "PRIVATE_NETWORK",
    "PUBLIC_NETWORK",
    "REDIS_ENV_FILE",
    "REQUIRED_SECURITY_GATE_ENV_KEYS",
    "RUNTIME_ENV_FILE",
    "SECRET_ENV_FILENAMES",
    "SHARED_CAPSULES_ROOT",
    "TRAEFIK_DYNAMIC_FILENAME",
    "TRAEFIK_ACME_DIRNAME",
    "TRAEFIK_ACME_FILENAME",
    "TRAEFIK_ACME_RESOLVER",
    "TRAEFIK_ACME_EMAIL_ENV_KEYS",
    "ComposeImageSet",
    "ComposeRenderError",
    "ComposeRenderOptions",
    "ComposeRenderer",
    "ComposeValidationError",
    "ComposeWriteResult",
    "apply_pull_policy",
    "atomic_write_text",
    "base_service_defaults",
    "capsule_manifest_file",
    "container_name",
    "default_acme_email_for_host",
    "django_socket_healthcheck",
    "ensure_instance_env_files",
    "enum_value",
    "env_file_path",
    "env_file_paths",
    "format_env_value",
    "generate_runtime_compose",
    "generated_env_file",
    "image_map_from_capsule_compose",
    "image_map_from_capsule_manifest",
    "image_map_from_environment",
    "instance_env_dir_path",
    "load_instance_env",
    "local_app_pull_policy",
    "manifest_from_compose",
    "media_nginx_healthcheck",
    "normalize_acme_email",
    "normalize_traefik_acme_resolver",
    "normalize_image_map",
    "parse_compose_port",
    "parse_env_file",
    "port_bindings_for_profile",
    "read_capsule_manifest",
    "read_compose_file",
    "read_mapping_file",
    "render_compose_spec",
    "render_runtime_files",
    "normalize_traefik_host",
    "normalized_host_aliases",
    "render_traefik_dynamic_config",
    "traefik_acme_email",
    "traefik_acme_enabled",
    "traefik_host_rule",
    "resolve_runtime_image_map",
    "runtime_environment",
    "runtime_environment_mapping",
    "security_context_inputs",
    "serialize_env",
    "service_dns_name",
    "service_log_dir",
    "traefik_dynamic_file",
    "validate_compose_file",
    "validate_compose_spec",
    "validate_instance_env_for_security_gate",
    "write_runtime_compose",
]
