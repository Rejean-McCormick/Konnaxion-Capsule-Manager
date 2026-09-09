"""
Install-time secret generation for Konnaxion Instances.

Capsules must never contain real production secrets. The Konnaxion Agent
generates instance-local secrets during instance creation/update/restore flows
and writes them into the instance env directory with restrictive permissions.

This module owns:
- secure secret generation
- placeholder/default secret rejection
- DATABASE_URL construction
- atomic secret env-file writes
- split env files for runtime containers
- combined runtime.env generation for Security Gate/runtime compatibility
- compatibility state/env mirror for older Compose renderers
- public host env generation for public_vps / public exposure
- redaction helpers for logs/API responses
"""

from __future__ import annotations

import os
import re
import secrets
import string
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import quote

from kx_shared.errors import (
    FileAlreadyExistsError,
    FileMissingError,
    InvalidVariableError,
    MissingRequiredVariableError,
    UnsafePathError,
    ValidationError,
)
from kx_shared.konnaxion_constants import (
    DATABASE_ENV_DEFAULTS,
    DJANGO_ENV_DEFAULTS,
    KX_ENV_DEFAULTS,
    REDIS_ENV_DEFAULTS,
    instance_env_dir,
)


# ---------------------------------------------------------------------
# Canonical secret/env keys
# ---------------------------------------------------------------------


DJANGO_SECRET_KEY = "DJANGO_SECRET_KEY"
POSTGRES_PASSWORD = "POSTGRES_PASSWORD"
DATABASE_URL = "DATABASE_URL"
DJANGO_ALLOWED_HOSTS = "DJANGO_ALLOWED_HOSTS"
DJANGO_CSRF_TRUSTED_ORIGINS = "DJANGO_CSRF_TRUSTED_ORIGINS"
CSRF_TRUSTED_ORIGINS = "CSRF_TRUSTED_ORIGINS"
CORS_ALLOWED_ORIGINS = "CORS_ALLOWED_ORIGINS"
NEXT_PUBLIC_API_BASE = "NEXT_PUBLIC_API_BASE"
NEXT_PUBLIC_BACKEND_BASE = "NEXT_PUBLIC_BACKEND_BASE"
FRONTEND_BASE_URL = "FRONTEND_BASE_URL"

KX_INSTANCE_ID = "KX_INSTANCE_ID"
KX_CAPSULE_ID = "KX_CAPSULE_ID"
KX_CAPSULE_VERSION = "KX_CAPSULE_VERSION"
KX_NETWORK_PROFILE = "KX_NETWORK_PROFILE"
KX_EXPOSURE_MODE = "KX_EXPOSURE_MODE"
KX_HOST = "KX_HOST"
KX_PUBLIC_MODE_ENABLED = "KX_PUBLIC_MODE_ENABLED"
KX_PUBLIC_MODE_EXPIRES_AT = "KX_PUBLIC_MODE_EXPIRES_AT"

POSTGRES_USER = "POSTGRES_USER"
POSTGRES_DB = "POSTGRES_DB"
POSTGRES_HOST = "POSTGRES_HOST"
POSTGRES_PORT = "POSTGRES_PORT"
REDIS_URL = "REDIS_URL"

DJANGO_ENV_FILE = "django.env"
POSTGRES_ENV_FILE = "postgres.env"
REDIS_ENV_FILE = "redis.env"
FRONTEND_ENV_FILE = "frontend.env"
KX_ENV_FILE = "kx.env"

# Compatibility file consumed by Security Gate, older runtime paths, and direct
# diagnostics:
#   grep -R "DATABASE_URL|DJANGO_SECRET_KEY|POSTGRES_PASSWORD" instances/<id>/env
RUNTIME_ENV_FILE = "runtime.env"

CANONICAL_ENV_FILES: tuple[str, ...] = (
    KX_ENV_FILE,
    DJANGO_ENV_FILE,
    POSTGRES_ENV_FILE,
    REDIS_ENV_FILE,
    FRONTEND_ENV_FILE,
    RUNTIME_ENV_FILE,
)

REQUIRED_SECRET_KEYS: frozenset[str] = frozenset(
    {
        DJANGO_SECRET_KEY,
        POSTGRES_PASSWORD,
    }
)

REQUIRED_RUNTIME_SECRET_KEYS: frozenset[str] = frozenset(
    {
        DATABASE_URL,
        DJANGO_SECRET_KEY,
        POSTGRES_PASSWORD,
    }
)

PUBLIC_NETWORK_PROFILES: frozenset[str] = frozenset(
    {
        "public_vps",
        "public_temporary",
    }
)

PUBLIC_EXPOSURE_MODES: frozenset[str] = frozenset(
    {
        "public",
        "temporary_tunnel",
    }
)

LOOPBACK_HOSTS: frozenset[str] = frozenset(
    {
        "127.0.0.1",
        "localhost",
        "0.0.0.0",
    }
)

SENSITIVE_KEY_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"secret",
        r"password",
        r"token",
        r"private[_-]?key",
        r"api[_-]?key",
        r"credential",
        r"database_url",
        r"redis_url",
        r"dsn",
    )
)

PLACEHOLDER_VALUES: frozenset[str] = frozenset(
    {
        "",
        "change-me",
        "changeme",
        "replace-me",
        "replaceme",
        "generated-on-install",
        "<generated_on_install>",
        "<generated-on-install>",
        "<generated-from-profile>",
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
)

_SAFE_PASSWORD_ALPHABET = string.ascii_letters + string.digits + "-_=+.,:;@%~"
_DJANGO_SECRET_ALPHABET = string.ascii_letters + string.digits + string.punctuation


# ---------------------------------------------------------------------
# DTOs
# ---------------------------------------------------------------------


@dataclass(slots=True, frozen=True)
class SecretGenerationPolicy:
    """Input policy for creating instance-local secrets."""

    instance_id: str
    host: str
    capsule_id: str | None = None
    capsule_version: str | None = None
    network_profile: str | None = None
    exposure_mode: str | None = None

    # Preferred field used by actions.py.
    overwrite_existing: bool = False

    # Compatibility field used by older compose.py delegation attempts.
    overwrite: bool | None = None

    # Compatibility metadata accepted from ComposeRenderOptions delegation.
    public_mode_enabled: bool | None = None
    public_mode_expires_at: str | None = None

    django_secret_length: int = 64
    postgres_password_length: int = 48

    def effective_overwrite_existing(self) -> bool:
        if self.overwrite is not None:
            return bool(self.overwrite)
        return bool(self.overwrite_existing)

    def public_runtime_requested(self) -> bool:
        profile = str(self.network_profile or "").strip()
        exposure = str(self.exposure_mode or "").strip()

        if self.public_mode_enabled is True:
            return True

        return profile in PUBLIC_NETWORK_PROFILES or exposure in PUBLIC_EXPOSURE_MODES

    def validate(self) -> None:
        if not self.instance_id or not self.instance_id.strip():
            raise MissingRequiredVariableError(
                "Instance ID is required for secret generation.",
                {"variable": KX_INSTANCE_ID},
            )

        if not self.host or not self.host.strip():
            raise MissingRequiredVariableError(
                "Host is required for generated frontend/backend URLs.",
                {"variable": KX_HOST},
            )

        normalized_host = normalize_host(self.host)
        if self.public_runtime_requested() and normalized_host in LOOPBACK_HOSTS:
            raise InvalidVariableError(
                "Public runtime profiles require a non-loopback host.",
                {
                    "variable": KX_HOST,
                    "host": normalized_host,
                    "network_profile": self.network_profile,
                    "exposure_mode": self.exposure_mode,
                },
            )

        if self.django_secret_length < 50:
            raise InvalidVariableError(
                "Django secret length must be at least 50 characters.",
                {"django_secret_length": self.django_secret_length},
            )

        if self.postgres_password_length < 32:
            raise InvalidVariableError(
                "PostgreSQL password length must be at least 32 characters.",
                {"postgres_password_length": self.postgres_password_length},
            )


@dataclass(slots=True, frozen=True)
class GeneratedSecrets:
    """Generated secret bundle for one Konnaxion Instance."""

    instance_id: str
    host: str
    django_secret_key: str
    postgres_password: str
    database_url: str
    django_allowed_hosts: str
    django_csrf_trusted_origins: str
    next_public_api_base: str
    next_public_backend_base: str
    extra_env: Mapping[str, str] = field(default_factory=dict)

    def to_env(self) -> dict[str, str]:
        env = {
            DJANGO_SECRET_KEY: self.django_secret_key,
            POSTGRES_PASSWORD: self.postgres_password,
            DATABASE_URL: self.database_url,
            DJANGO_ALLOWED_HOSTS: self.django_allowed_hosts,
            DJANGO_CSRF_TRUSTED_ORIGINS: self.django_csrf_trusted_origins,
            CSRF_TRUSTED_ORIGINS: self.django_csrf_trusted_origins,
            CORS_ALLOWED_ORIGINS: self.django_csrf_trusted_origins,
            NEXT_PUBLIC_API_BASE: self.next_public_api_base,
            NEXT_PUBLIC_BACKEND_BASE: self.next_public_backend_base,
        }
        env.update({str(key): str(value) for key, value in self.extra_env.items()})
        return env

    def redacted(self) -> dict[str, str]:
        return redact_env(self.to_env())


# ---------------------------------------------------------------------
# Secret generation
# ---------------------------------------------------------------------


def generate_secret_bundle(policy: SecretGenerationPolicy) -> GeneratedSecrets:
    """Generate a complete secret bundle for an instance."""

    policy.validate()

    postgres_host = str(DATABASE_ENV_DEFAULTS.get("POSTGRES_HOST", "postgres"))
    postgres_port = str(DATABASE_ENV_DEFAULTS.get("POSTGRES_PORT", "5432"))
    postgres_db = str(DATABASE_ENV_DEFAULTS.get("POSTGRES_DB", "konnaxion"))
    postgres_user = str(DATABASE_ENV_DEFAULTS.get("POSTGRES_USER", "konnaxion"))

    django_secret_key = generate_django_secret_key(policy.django_secret_length)
    postgres_password = generate_password(policy.postgres_password_length)
    database_url = build_database_url(
        user=postgres_user,
        password=postgres_password,
        host=postgres_host,
        port=postgres_port,
        database=postgres_db,
    )

    host = normalize_host(policy.host)

    bundle = GeneratedSecrets(
        instance_id=policy.instance_id,
        host=host,
        django_secret_key=django_secret_key,
        postgres_password=postgres_password,
        database_url=database_url,
        django_allowed_hosts=build_allowed_hosts(
            host,
            instance_id=policy.instance_id,
        ),
        django_csrf_trusted_origins=build_csrf_trusted_origins(host),
        next_public_api_base=f"https://{host}/api",
        next_public_backend_base=f"https://{host}",
    )

    validate_generated_secrets(bundle)
    return bundle


def generate_django_secret_key(length: int = 64) -> str:
    """Generate a Django-compatible secret key."""

    if length < 50:
        raise InvalidVariableError(
            "Django secret key length must be at least 50 characters.",
            {"length": length},
        )

    return "".join(secrets.choice(_DJANGO_SECRET_ALPHABET) for _ in range(length))


def generate_password(length: int = 48) -> str:
    """Generate a service password with enough entropy."""

    if length < 32:
        raise InvalidVariableError(
            "Generated password length must be at least 32 characters.",
            {"length": length},
        )

    return "".join(secrets.choice(_SAFE_PASSWORD_ALPHABET) for _ in range(length))


def build_database_url(
    *,
    user: str,
    password: str,
    host: str,
    port: str | int,
    database: str,
) -> str:
    """Build the internal PostgreSQL DATABASE_URL."""

    for name, value in {
        "user": user,
        "password": password,
        "host": host,
        "port": str(port),
        "database": database,
    }.items():
        if not value:
            raise MissingRequiredVariableError(
                "Cannot build DATABASE_URL with missing value.",
                {"missing": name},
            )

    encoded_user = quote(str(user), safe="")
    encoded_password = quote(str(password), safe="")
    encoded_database = quote(str(database), safe="")

    return (
        f"postgres://{encoded_user}:{encoded_password}"
        f"@{host}:{port}/{encoded_database}"
    )


def normalize_host(host: str) -> str:
    """Normalize a host value for env generation."""

    normalized = str(host or "").strip()
    normalized = normalized.removeprefix("https://").removeprefix("http://")
    normalized = normalized.rstrip("/")

    if not normalized:
        raise MissingRequiredVariableError(
            "Host cannot be empty.",
            {"variable": KX_HOST},
        )

    if "/" in normalized:
        raise InvalidVariableError(
            "Host must not contain a path.",
            {"host": host},
        )

    return normalized


def build_allowed_hosts(host: str, *, instance_id: str | None = None) -> str:
    """Build Django allowed hosts for the selected runtime host."""

    normalized = normalize_host(host)
    hosts = [
        normalized,
        "localhost",
        "127.0.0.1",
        "django-api",
    ]

    if instance_id:
        safe_instance_id = str(instance_id).strip()
        if safe_instance_id:
            hosts.append(f"kx-{safe_instance_id}-django-api")

    return ",".join(dict.fromkeys(hosts))


def build_csrf_trusted_origins(host: str) -> str:
    """Build Django CSRF/CORS origins for the selected public host."""

    normalized = normalize_host(host)

    origins = [
        f"https://{normalized}",
        f"http://{normalized}",
    ]

    if normalized not in LOOPBACK_HOSTS:
        origins.extend(
            [
                "https://127.0.0.1",
                "http://127.0.0.1",
                "https://localhost",
                "http://localhost",
            ]
        )

    return ",".join(dict.fromkeys(origins))


# ---------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------


def validate_generated_secrets(bundle: GeneratedSecrets) -> None:
    env = bundle.to_env()

    for key in REQUIRED_SECRET_KEYS:
        value = env.get(key)
        validate_secret_value(key, value)

    if not env.get(DATABASE_URL):
        raise MissingRequiredVariableError(
            "DATABASE_URL is required.",
            {"variable": DATABASE_URL},
        )

    if is_placeholder_secret(env.get(DATABASE_URL)):
        raise InvalidVariableError(
            "DATABASE_URL is empty, default, or placeholder.",
            {"variable": DATABASE_URL},
        )

    encoded_password = quote(bundle.postgres_password, safe="")
    if encoded_password not in bundle.database_url:
        raise ValidationError(
            "DATABASE_URL does not contain the generated PostgreSQL password.",
            {"variable": DATABASE_URL},
        )


def validate_secret_value(key: str, value: str | None) -> None:
    if value is None:
        raise MissingRequiredVariableError(
            "Required secret is missing.",
            {"variable": key},
        )

    if is_placeholder_secret(value):
        raise InvalidVariableError(
            "Secret value is empty, default, or placeholder.",
            {"variable": key},
        )

    minimum = minimum_secret_length(key)
    if len(value) < minimum:
        raise InvalidVariableError(
            "Secret value is too short.",
            {"variable": key, "minimum_length": minimum},
        )


def minimum_secret_length(key: str) -> int:
    normalized = key.upper()
    if normalized == DJANGO_SECRET_KEY:
        return 50
    if normalized == POSTGRES_PASSWORD:
        return 32
    if normalized == DATABASE_URL:
        return 32
    return 16


def is_placeholder_secret(value: str | None) -> bool:
    if value is None:
        return True

    normalized = value.strip().lower()
    normalized = normalized.strip("'\"")

    return normalized in PLACEHOLDER_VALUES


def has_sensitive_key(key: str) -> bool:
    return any(pattern.search(key) for pattern in SENSITIVE_KEY_PATTERNS)


def env_has_required_runtime_secrets(env: Mapping[str, Any]) -> bool:
    """Return whether env contains non-placeholder runtime secrets."""

    keys = {str(key) for key in env}
    if not REQUIRED_RUNTIME_SECRET_KEYS.issubset(keys):
        return False

    return all(
        not is_placeholder_secret(str(env.get(key) or ""))
        and len(str(env.get(key) or "")) >= minimum_secret_length(key)
        for key in REQUIRED_RUNTIME_SECRET_KEYS
    )


# ---------------------------------------------------------------------
# Env-file rendering/writing
# ---------------------------------------------------------------------


def build_env_files(
    bundle: GeneratedSecrets,
    policy: SecretGenerationPolicy,
) -> dict[str, dict[str, str]]:
    """Build canonical env-file contents from generated secrets."""

    postgres_user = str(DATABASE_ENV_DEFAULTS.get("POSTGRES_USER", "konnaxion"))
    postgres_db = str(DATABASE_ENV_DEFAULTS.get("POSTGRES_DB", "konnaxion"))
    postgres_host = str(DATABASE_ENV_DEFAULTS.get("POSTGRES_HOST", "postgres"))
    postgres_port = str(DATABASE_ENV_DEFAULTS.get("POSTGRES_PORT", "5432"))

    redis_url = str(
        REDIS_ENV_DEFAULTS.get("REDIS_URL")
        or REDIS_ENV_DEFAULTS.get("CELERY_BROKER_URL")
        or "redis://redis:6379/0"
    )

    network_profile = policy.network_profile or str(
        KX_ENV_DEFAULTS.get(KX_NETWORK_PROFILE, "")
    )
    exposure_mode = policy.exposure_mode or str(
        KX_ENV_DEFAULTS.get(KX_EXPOSURE_MODE, "")
    )

    public_mode_enabled = (
        policy.public_mode_enabled
        if policy.public_mode_enabled is not None
        else exposure_mode in PUBLIC_EXPOSURE_MODES
        or network_profile in PUBLIC_NETWORK_PROFILES
    )

    kx_env = {
        KX_INSTANCE_ID: policy.instance_id,
        KX_CAPSULE_ID: policy.capsule_id
        or str(KX_ENV_DEFAULTS.get(KX_CAPSULE_ID, "")),
        KX_CAPSULE_VERSION: policy.capsule_version
        or str(KX_ENV_DEFAULTS.get(KX_CAPSULE_VERSION, "")),
        KX_NETWORK_PROFILE: network_profile,
        KX_EXPOSURE_MODE: exposure_mode,
        KX_HOST: bundle.host,
        KX_PUBLIC_MODE_ENABLED: str(bool(public_mode_enabled)).lower(),
        KX_PUBLIC_MODE_EXPIRES_AT: str(policy.public_mode_expires_at or ""),
    }

    django_env = {
        **{str(k): str(v) for k, v in DJANGO_ENV_DEFAULTS.items()},
        DJANGO_SECRET_KEY: bundle.django_secret_key,
        DJANGO_ALLOWED_HOSTS: bundle.django_allowed_hosts,
        DJANGO_CSRF_TRUSTED_ORIGINS: bundle.django_csrf_trusted_origins,
        CSRF_TRUSTED_ORIGINS: bundle.django_csrf_trusted_origins,
        CORS_ALLOWED_ORIGINS: bundle.django_csrf_trusted_origins,
        FRONTEND_BASE_URL: bundle.next_public_backend_base,
        DATABASE_URL: bundle.database_url,
    }

    postgres_env = {
        **{str(k): str(v) for k, v in DATABASE_ENV_DEFAULTS.items()},
        POSTGRES_USER: postgres_user,
        POSTGRES_PASSWORD: bundle.postgres_password,
        POSTGRES_DB: postgres_db,
        POSTGRES_HOST: postgres_host,
        POSTGRES_PORT: postgres_port,
    }

    redis_env = {
        **{str(k): str(v) for k, v in REDIS_ENV_DEFAULTS.items()},
        REDIS_URL: redis_url,
    }

    frontend_env = {
        NEXT_PUBLIC_API_BASE: bundle.next_public_api_base,
        NEXT_PUBLIC_BACKEND_BASE: bundle.next_public_backend_base,
        "NEXT_TELEMETRY_DISABLED": "1",
        "NODE_OPTIONS": "--max-old-space-size=4096",
    }

    runtime_env = {
        **kx_env,
        POSTGRES_USER: postgres_user,
        POSTGRES_PASSWORD: bundle.postgres_password,
        POSTGRES_DB: postgres_db,
        POSTGRES_HOST: postgres_host,
        POSTGRES_PORT: postgres_port,
        DATABASE_URL: bundle.database_url,
        REDIS_URL: redis_url,
        DJANGO_SECRET_KEY: bundle.django_secret_key,
        DJANGO_ALLOWED_HOSTS: bundle.django_allowed_hosts,
        DJANGO_CSRF_TRUSTED_ORIGINS: bundle.django_csrf_trusted_origins,
        CSRF_TRUSTED_ORIGINS: bundle.django_csrf_trusted_origins,
        CORS_ALLOWED_ORIGINS: bundle.django_csrf_trusted_origins,
        FRONTEND_BASE_URL: bundle.next_public_backend_base,
        NEXT_PUBLIC_API_BASE: bundle.next_public_api_base,
        NEXT_PUBLIC_BACKEND_BASE: bundle.next_public_backend_base,
    }

    return {
        KX_ENV_FILE: kx_env,
        DJANGO_ENV_FILE: django_env,
        POSTGRES_ENV_FILE: postgres_env,
        REDIS_ENV_FILE: redis_env,
        FRONTEND_ENV_FILE: frontend_env,
        RUNTIME_ENV_FILE: runtime_env,
    }


def write_instance_secret_env_files(
    policy: SecretGenerationPolicy,
    *,
    env_dir: Path | None = None,
    mirror_state_env: bool = True,
) -> dict[str, Path]:
    """Generate and write canonical env files for an instance."""

    policy.validate()
    target_dir = env_dir or instance_env_dir(policy.instance_id)
    ensure_safe_env_dir(target_dir, policy.instance_id)

    overwrite_existing = policy.effective_overwrite_existing()
    existing_env = read_existing_instance_env_values(policy.instance_id, target_dir)

    bundle = generate_secret_bundle(policy)
    if existing_env:
        bundle = preserve_existing_secrets(bundle, existing_env)

    env_files = build_env_files(bundle, policy)

    written: dict[str, Path] = {}

    for filename, values in env_files.items():
        target_path = target_dir / filename

        if target_path.exists() and not overwrite_existing:
            raise FileAlreadyExistsError(
                "Refusing to overwrite existing env file.",
                {"path": str(target_path)},
            )

        write_env_file_atomic(target_path, values)
        written[filename] = target_path

    if mirror_state_env:
        written.update(
            mirror_env_files_for_state_relative_compose(
                policy.instance_id,
                env_files,
                canonical_env_dir=target_dir,
            )
        )

    return written


def write_instance_env_files(
    policy: SecretGenerationPolicy | str | None = None,
    *,
    instance_id: str | None = None,
    host: str | None = None,
    capsule_id: str | None = None,
    capsule_version: str | None = None,
    network_profile: str | None = None,
    exposure_mode: str | None = None,
    public_mode_enabled: bool | None = None,
    public_mode_expires_at: str | None = None,
    overwrite: bool = False,
    overwrite_existing: bool | None = None,
    env_dir: Path | None = None,
    mirror_state_env: bool = True,
    **extra: Any,
) -> dict[str, Path]:
    """Compatibility wrapper expected by runtime.compose."""

    if isinstance(policy, SecretGenerationPolicy):
        resolved_policy = policy
    else:
        resolved_instance_id = instance_id or (str(policy) if policy else None)
        if not resolved_instance_id:
            raise MissingRequiredVariableError(
                "Instance ID is required for env-file generation.",
                {"variable": KX_INSTANCE_ID},
            )

        resolved_host = resolve_runtime_host(
            host=host,
            network_profile=network_profile,
            exposure_mode=exposure_mode,
            public_mode_enabled=public_mode_enabled,
            extra=extra,
        )

        resolved_policy = SecretGenerationPolicy(
            instance_id=resolved_instance_id,
            host=resolved_host,
            capsule_id=capsule_id,
            capsule_version=capsule_version,
            network_profile=network_profile,
            exposure_mode=exposure_mode,
            overwrite_existing=bool(
                overwrite if overwrite_existing is None else overwrite_existing
            ),
            overwrite=overwrite,
            public_mode_enabled=public_mode_enabled,
            public_mode_expires_at=public_mode_expires_at,
        )

    return write_instance_secret_env_files(
        resolved_policy,
        env_dir=env_dir,
        mirror_state_env=mirror_state_env,
    )


def resolve_runtime_host(
    *,
    host: str | None,
    network_profile: str | None,
    exposure_mode: str | None,
    public_mode_enabled: bool | None,
    extra: Mapping[str, Any],
) -> str:
    """Resolve canonical runtime host from Manager/Agent compatibility fields."""

    candidates = (
        host,
        extra.get("host"),
        extra.get("domain"),
        extra.get("public_host"),
        extra.get("droplet_domain"),
        extra.get("droplet_host"),
    )

    for candidate in candidates:
        text = str(candidate or "").strip()
        if text:
            return normalize_host(text)

    public_runtime_requested = (
        public_mode_enabled is True
        or str(network_profile or "").strip() in PUBLIC_NETWORK_PROFILES
        or str(exposure_mode or "").strip() in PUBLIC_EXPOSURE_MODES
    )

    if public_runtime_requested:
        raise MissingRequiredVariableError(
            "Public runtime profiles require a non-empty host.",
            {
                "variable": KX_HOST,
                "network_profile": network_profile,
                "exposure_mode": exposure_mode,
            },
        )

    return "127.0.0.1"


def preserve_existing_secrets(
    bundle: GeneratedSecrets,
    existing_env: Mapping[str, Any],
) -> GeneratedSecrets:
    """Preserve real existing secrets while regenerating public env values."""

    django_secret_key = str(existing_env.get(DJANGO_SECRET_KEY) or "")
    postgres_password = str(existing_env.get(POSTGRES_PASSWORD) or "")

    if is_placeholder_secret(django_secret_key) or len(django_secret_key) < 50:
        django_secret_key = bundle.django_secret_key

    if is_placeholder_secret(postgres_password) or len(postgres_password) < 32:
        postgres_password = bundle.postgres_password

    postgres_host = str(DATABASE_ENV_DEFAULTS.get("POSTGRES_HOST", "postgres"))
    postgres_port = str(DATABASE_ENV_DEFAULTS.get("POSTGRES_PORT", "5432"))
    postgres_db = str(DATABASE_ENV_DEFAULTS.get("POSTGRES_DB", "konnaxion"))
    postgres_user = str(DATABASE_ENV_DEFAULTS.get("POSTGRES_USER", "konnaxion"))

    database_url = build_database_url(
        user=postgres_user,
        password=postgres_password,
        host=postgres_host,
        port=postgres_port,
        database=postgres_db,
    )

    preserved = GeneratedSecrets(
        instance_id=bundle.instance_id,
        host=bundle.host,
        django_secret_key=django_secret_key,
        postgres_password=postgres_password,
        database_url=database_url,
        django_allowed_hosts=bundle.django_allowed_hosts,
        django_csrf_trusted_origins=bundle.django_csrf_trusted_origins,
        next_public_api_base=bundle.next_public_api_base,
        next_public_backend_base=bundle.next_public_backend_base,
        extra_env=bundle.extra_env,
    )

    validate_generated_secrets(preserved)
    return preserved


def read_existing_instance_env_values(
    instance_id: str,
    canonical_env_dir: Path | None = None,
) -> dict[str, str]:
    """Best-effort read of existing canonical/state env values."""

    env_dir = canonical_env_dir or instance_env_dir(instance_id)
    state_env_dir = _state_relative_env_dir(env_dir)
    env: dict[str, str] = {}

    for filename in CANONICAL_ENV_FILES:
        for path in (env_dir / filename, state_env_dir / filename):
            if not path.is_file():
                continue
            try:
                env.update(read_env_file(path))
            except Exception:
                continue

    return env


def mirror_env_files_for_state_relative_compose(
    instance_id: str,
    env_files: Mapping[str, Mapping[str, str]],
    *,
    canonical_env_dir: Path | None = None,
) -> dict[str, Path]:
    """Mirror env files into state/env for Compose relative-path compatibility."""

    canonical_dir = canonical_env_dir or instance_env_dir(instance_id)
    state_env_dir = _state_relative_env_dir(canonical_dir)

    state_env_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(state_env_dir, 0o700)

    written: dict[str, Path] = {}

    for filename, values in env_files.items():
        if filename not in CANONICAL_ENV_FILES:
            continue

        mirror_path = state_env_dir / filename
        write_env_file_atomic(mirror_path, values)
        written[f"state/env/{filename}"] = mirror_path

    return written


def write_env_file_atomic(path: Path, values: Mapping[str, str]) -> None:
    """Write a .env file atomically with restrictive permissions."""

    path.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
        text=True,
    )

    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            for key, value in values.items():
                handle.write(f"{key}={quote_env_value(str(value))}\n")
            handle.flush()
            os.fsync(handle.fileno())

        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, path)
        os.chmod(path, 0o600)
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        finally:
            raise


def quote_env_value(value: str) -> str:
    """Render a value safely for simple dotenv parsing."""

    if value == "":
        return ""

    if re.fullmatch(r"[A-Za-z0-9_./:@%+=,;~\-]+", value):
        return value

    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def read_env_file(path: Path) -> dict[str, str]:
    """Read a simple KEY=VALUE env file written by this module."""

    if not path.exists():
        raise FileMissingError(
            "Env file does not exist.",
            {"path": str(path)},
        )

    result: dict[str, str] = {}
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        line = raw_line.strip()

        if not line or line.startswith("#"):
            continue

        if line.startswith("export "):
            line = line.removeprefix("export ").strip()

        if "=" not in line:
            raise InvalidVariableError(
                "Invalid env-file line.",
                {"path": str(path), "line_number": line_number},
            )

        key, value = line.split("=", 1)
        result[key.strip()] = unquote_env_value(value.strip())

    return result


def read_instance_env_files(instance_id: str) -> dict[str, str]:
    """Read all canonical env files for an instance into one mapping."""

    env_dir = instance_env_dir(instance_id)
    state_env_dir = _state_relative_env_dir(env_dir)
    env: dict[str, str] = {}

    for filename in CANONICAL_ENV_FILES:
        canonical_path = env_dir / filename
        state_path = state_env_dir / filename

        if canonical_path.is_file():
            env.update(read_env_file(canonical_path))
            continue

        if state_path.is_file():
            env.update(read_env_file(state_path))

    return env


def unquote_env_value(value: str) -> str:
    if len(value) >= 2 and value.startswith('"') and value.endswith('"'):
        inner = value[1:-1]
        return inner.replace('\\"', '"').replace("\\\\", "\\")
    return value


def ensure_safe_env_dir(path: Path, instance_id: str) -> None:
    """Ensure env files are written under the canonical instance env path."""

    expected = instance_env_dir(instance_id).resolve()
    resolved = path.resolve()

    if resolved != expected:
        raise UnsafePathError(
            "Env directory must be the canonical instance env directory.",
            {"expected": str(expected), "received": str(resolved)},
        )

    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, 0o700)


def _state_relative_env_dir(canonical_env_dir: Path) -> Path:
    """Return sibling state/env directory for Compose relative env_file entries."""

    resolved = canonical_env_dir.resolve()

    if resolved.name != "env":
        raise UnsafePathError(
            "Cannot derive state/env compatibility path from non-env directory.",
            {"env_dir": str(canonical_env_dir)},
        )

    instance_root = resolved.parent
    if not instance_root.name:
        raise UnsafePathError(
            "Cannot derive instance root from env directory.",
            {"env_dir": str(canonical_env_dir)},
        )

    return instance_root / "state" / "env"


# ---------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------


def redact_env(values: Mapping[str, Any]) -> dict[str, str]:
    """Redact sensitive env values for logs/API responses."""

    return {
        str(key): redact_value(str(key), value)
        for key, value in values.items()
    }


def redact_value(key: str, value: Any) -> str:
    if has_sensitive_key(key):
        return "<REDACTED>"

    text = "" if value is None else str(value)
    if is_placeholder_secret(text):
        return "<EMPTY_OR_PLACEHOLDER>"

    return text


__all__ = [
    "CANONICAL_ENV_FILES",
    "CORS_ALLOWED_ORIGINS",
    "CSRF_TRUSTED_ORIGINS",
    "DATABASE_URL",
    "DJANGO_ALLOWED_HOSTS",
    "DJANGO_CSRF_TRUSTED_ORIGINS",
    "DJANGO_ENV_FILE",
    "DJANGO_SECRET_KEY",
    "FRONTEND_BASE_URL",
    "FRONTEND_ENV_FILE",
    "GeneratedSecrets",
    "KX_CAPSULE_ID",
    "KX_CAPSULE_VERSION",
    "KX_ENV_FILE",
    "KX_EXPOSURE_MODE",
    "KX_HOST",
    "KX_INSTANCE_ID",
    "KX_NETWORK_PROFILE",
    "KX_PUBLIC_MODE_ENABLED",
    "KX_PUBLIC_MODE_EXPIRES_AT",
    "LOOPBACK_HOSTS",
    "NEXT_PUBLIC_API_BASE",
    "NEXT_PUBLIC_BACKEND_BASE",
    "PLACEHOLDER_VALUES",
    "POSTGRES_DB",
    "POSTGRES_ENV_FILE",
    "POSTGRES_HOST",
    "POSTGRES_PASSWORD",
    "POSTGRES_PORT",
    "POSTGRES_USER",
    "PUBLIC_EXPOSURE_MODES",
    "PUBLIC_NETWORK_PROFILES",
    "REDIS_ENV_FILE",
    "REDIS_URL",
    "REQUIRED_RUNTIME_SECRET_KEYS",
    "REQUIRED_SECRET_KEYS",
    "RUNTIME_ENV_FILE",
    "SENSITIVE_KEY_PATTERNS",
    "SecretGenerationPolicy",
    "build_allowed_hosts",
    "build_csrf_trusted_origins",
    "build_database_url",
    "build_env_files",
    "env_has_required_runtime_secrets",
    "generate_django_secret_key",
    "generate_password",
    "generate_secret_bundle",
    "has_sensitive_key",
    "is_placeholder_secret",
    "minimum_secret_length",
    "mirror_env_files_for_state_relative_compose",
    "normalize_host",
    "preserve_existing_secrets",
    "quote_env_value",
    "read_env_file",
    "read_existing_instance_env_values",
    "read_instance_env_files",
    "redact_env",
    "redact_value",
    "resolve_runtime_host",
    "unquote_env_value",
    "validate_generated_secrets",
    "validate_secret_value",
    "write_env_file_atomic",
    "write_instance_env_files",
    "write_instance_secret_env_files",
]