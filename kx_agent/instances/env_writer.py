"""
Environment file writer for Konnaxion instances.

This module is owned by the Konnaxion Agent because writing runtime env files
contains generated secrets and affects privileged runtime startup.

Rules enforced here:

- Capsules provide templates only.
- Real secrets are generated on install/update by the Agent.
- Instance env files live under /opt/konnaxion/instances/<INSTANCE_ID>/env/.
- KX_* variables use canonical names from kx_shared.konnaxion_constants.
- Writes are atomic and private by default.
- Host-derived runtime values may be rewritten without rotating secrets.
"""

from __future__ import annotations

import os
import re
import secrets
import string
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import unquote, urlparse

from kx_shared.konnaxion_constants import (
    DATABASE_ENV_DEFAULTS,
    DJANGO_ENV_DEFAULTS,
    FRONTEND_ENV_DEFAULTS,
    KX_ENV_DEFAULTS,
    REDIS_ENV_DEFAULTS,
    DockerService,
    ExposureMode,
    NetworkProfile,
    instance_backup_root,
    instance_compose_file,
    instance_env_dir,
)
from kx_shared.types import (
    CapsuleID,
    CapsuleVersion,
    EnvMap,
    Hostname,
    InstanceID,
    ParamVersion,
    URL,
)


ENV_FILE_MODE = 0o600
ENV_DIR_MODE = 0o700

DJANGO_ENV_FILENAME = "django.env"
POSTGRES_ENV_FILENAME = "postgres.env"
REDIS_ENV_FILENAME = "redis.env"
FRONTEND_ENV_FILENAME = "frontend.env"

# Runtime compose uses ../env/kx.env. Keep the legacy name readable for old
# installs, but write the canonical file as kx.env.
KX_ENV_FILENAME = "kx.env"
LEGACY_KX_ENV_FILENAME = "konnaxion.env"

PLACEHOLDER_PATTERN = re.compile(r"^<[^>]+>$")
TEMPLATE_VAR_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)\}|\{\{\s*([A-Z0-9_]+)\s*\}\}")

FORBIDDEN_SECRET_VALUES = frozenset(
    {
        "",
        "changeme",
        "change-me",
        "password",
        "secret",
        "admin",
        "konnaxion",
        "<GENERATED_ON_INSTALL>",
        "<POSTGRES_PASSWORD>",
        "<DJANGO_SECRET_KEY>",
    }
)

SECRET_ENV_KEYS = frozenset(
    {
        "DJANGO_SECRET_KEY",
        "POSTGRES_PASSWORD",
        "DATABASE_URL",
    }
)

LOOPBACK_HOSTS = frozenset(
    {
        "127.0.0.1",
        "localhost",
        "::1",
        "[::1]",
        "0.0.0.0",
    }
)

DJANGO_INTERNAL_ALLOWED_HOSTS = (
    "127.0.0.1",
    "localhost",
)

DEFAULT_FRONTEND_NODE_OPTIONS = "--max-old-space-size=4096"


@dataclass(frozen=True, slots=True)
class GeneratedSecrets:
    """Runtime secrets generated or preserved by the Agent for one instance."""

    django_secret_key: str
    postgres_password: str


@dataclass(frozen=True, slots=True)
class EnvFileSpec:
    """One env file to write."""

    filename: str
    values: EnvMap
    secret: bool = True


@dataclass(frozen=True, slots=True)
class InstanceEnvBundle:
    """All env files generated for an instance."""

    instance_id: InstanceID
    env_dir: Path
    files: tuple[Path, ...]
    host: Hostname
    public_url: URL
    api_base_url: URL
    host_aliases: tuple[Hostname, ...] = ()


@dataclass(frozen=True, slots=True)
class InstanceEnvContext:
    """Input context for generating canonical instance env files."""

    instance_id: InstanceID
    capsule_id: CapsuleID
    capsule_version: CapsuleVersion
    param_version: ParamVersion
    app_version: str
    network_profile: NetworkProfile
    exposure_mode: ExposureMode
    host: Hostname
    host_aliases: tuple[Hostname, ...] = field(default_factory=tuple)
    public_mode_enabled: bool = False
    public_mode_expires_at: str = ""
    extra_kx_env: Mapping[str, str] = field(default_factory=dict)
    extra_django_env: Mapping[str, str] = field(default_factory=dict)
    extra_postgres_env: Mapping[str, str] = field(default_factory=dict)
    extra_redis_env: Mapping[str, str] = field(default_factory=dict)
    extra_frontend_env: Mapping[str, str] = field(default_factory=dict)


def _enum_value(value: Any) -> str:
    """Return enum.value when present, otherwise str(value)."""

    return str(getattr(value, "value", value))


def _service_value(member_name: str, fallback: str) -> str:
    """Return a DockerService value without hard-failing on old enum names."""

    member = getattr(DockerService, member_name, None)
    if member is None:
        return fallback
    return _enum_value(member)


def _profile_is(context: InstanceEnvContext, value: str) -> bool:
    return _enum_value(context.network_profile) == value


def _exposure_is(context: InstanceEnvContext, value: str) -> bool:
    return _enum_value(context.exposure_mode) == value


def is_public_vps_context(context: InstanceEnvContext) -> bool:
    """Return True when this context is a public VPS/Droplet runtime."""

    return _profile_is(context, NetworkProfile.PUBLIC_VPS.value)


def is_temporary_public_context(context: InstanceEnvContext) -> bool:
    """Return True when this context is temporary public tunnel runtime."""

    return (
        _profile_is(context, NetworkProfile.PUBLIC_TEMPORARY.value)
        or _exposure_is(context, ExposureMode.TEMPORARY_TUNNEL.value)
    )


def effective_public_mode_enabled(context: InstanceEnvContext) -> bool:
    """Return the canonical public-mode flag for this context.

    public_vps is public without requiring an expiration timestamp.
    temporary_tunnel/public_temporary is public and requires an expiration.
    """

    return bool(
        context.public_mode_enabled
        or is_public_vps_context(context)
        or is_temporary_public_context(context)
        or _exposure_is(context, ExposureMode.PUBLIC.value)
    )


def public_mode_requires_expiration(context: InstanceEnvContext) -> bool:
    """Return True only for temporary public exposure modes."""

    return is_temporary_public_context(context)


def generate_secret_key(length: int = 64) -> str:
    """Generate a Django secret key that cannot trigger django-environ proxy lookup."""

    alphabet = string.ascii_letters + string.digits + "!@#$%^&*(-_=+)"
    first_alphabet = string.ascii_letters + string.digits + "!@#%^&*(-_=+)"
    first = secrets.choice(first_alphabet)
    rest = "".join(secrets.choice(alphabet) for _ in range(length - 1))
    return first + rest


def generate_password(length: int = 40) -> str:
    """Generate a strong password suitable for PostgreSQL."""

    alphabet = string.ascii_letters + string.digits + "-_"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def generate_runtime_secrets() -> GeneratedSecrets:
    """Generate all secrets required for a new Konnaxion instance."""

    return GeneratedSecrets(
        django_secret_key=generate_secret_key(),
        postgres_password=generate_password(),
    )


def normalize_host(host: str | Hostname) -> Hostname:
    """Normalize a configured Konnaxion host value.

    Accepts either a bare host, host:port, or a URL. The returned value is always
    the host/netloc without scheme, path, query, fragment, or trailing slash.
    """

    raw = str(host).strip().rstrip("/")
    if not raw:
        raise ValueError("host must not be empty")

    parsed = urlparse(raw if "://" in raw else f"//{raw}")
    netloc = parsed.netloc or parsed.path.split("/", 1)[0]
    normalized = netloc.strip().rstrip("/")

    if not normalized:
        raise ValueError("host must not be empty")

    if "@" in normalized:
        raise ValueError("host must not contain credentials")

    return Hostname(normalized)


def split_host_aliases(value: str | Iterable[str] | None) -> tuple[str, ...]:
    """Split host aliases from comma/whitespace text or an iterable."""

    if value is None:
        return ()

    if isinstance(value, str):
        raw_items = re.split(r"[,\s]+", value.strip())
    else:
        raw_items = [str(item) for item in value]

    return tuple(item.strip() for item in raw_items if item and item.strip())


def normalize_host_aliases(
    primary_host: str | Hostname,
    aliases: Iterable[str | Hostname] | str | None,
) -> tuple[Hostname, ...]:
    """Normalize and dedupe host aliases, excluding the primary host."""

    primary = str(normalize_host(primary_host))
    candidates = split_host_aliases(aliases)

    deduped: list[Hostname] = []
    seen = {primary}

    for candidate in candidates:
        normalized = normalize_host(candidate)
        normalized_text = str(normalized)

        if normalized_text in seen:
            continue

        seen.add(normalized_text)
        deduped.append(normalized)

    return tuple(deduped)


def default_public_host_aliases(host: str | Hostname) -> tuple[Hostname, ...]:
    """Return safe default apex/www aliases for a public runtime host."""

    normalized = str(normalize_host(host))
    lower = normalized.lower()

    if lower in LOOPBACK_HOSTS:
        return ()
    if lower.endswith(".sslip.io"):
        return ()
    if all(part.isdigit() for part in lower.split(".") if part):
        return ()

    if lower.startswith("www."):
        apex = normalized[4:]
        return (Hostname(apex),) if apex else ()

    if "." in lower:
        return (Hostname(f"www.{normalized}"),)

    return ()


def build_host_aliases(context: InstanceEnvContext) -> tuple[Hostname, ...]:
    """Return default and configured public host aliases for an instance."""

    configured_aliases: list[str | Hostname] = [
        *default_public_host_aliases(context.host),
        *context.host_aliases,
    ]

    extra_aliases = context.extra_kx_env.get("KX_HOST_ALIASES", "")
    configured_aliases.extend(split_host_aliases(extra_aliases))

    return normalize_host_aliases(context.host, configured_aliases)


def build_public_hosts(context: InstanceEnvContext) -> tuple[Hostname, ...]:
    """Return primary public host followed by normalized aliases."""

    primary = normalize_host(context.host)
    return (primary, *build_host_aliases(context))


def is_loopback_host(host: str | Hostname) -> bool:
    """Return True if host is a loopback/listen-all host."""

    normalized = str(normalize_host(host)).lower()

    if normalized in LOOPBACK_HOSTS:
        return True

    # Strip a port for common host:port values.
    if ":" in normalized and not normalized.startswith("["):
        base = normalized.rsplit(":", 1)[0]
        return base in LOOPBACK_HOSTS

    return False


def build_base_url(host: str | Hostname) -> URL:
    """Build the canonical HTTPS base URL for a host."""

    normalized = normalize_host(host)
    return URL(f"https://{normalized}")


def build_api_base_url(host: str | Hostname) -> URL:
    """Build the canonical frontend API base URL."""

    return URL(f"{build_base_url(host)}/api")


def build_django_allowed_hosts(context: InstanceEnvContext) -> str:
    """Build comma-separated Django ALLOWED_HOSTS for one instance."""

    instance_id = str(context.instance_id)
    django_service = _service_value("DJANGO_API", "django-api")

    values = [
        *DJANGO_INTERNAL_ALLOWED_HOSTS,
        *(str(host) for host in build_public_hosts(context)),
        django_service,
        f"kx-{instance_id}-django-api",
    ]

    deduped: list[str] = []
    for value in values:
        normalized = str(value).strip()
        if normalized and normalized not in deduped:
            deduped.append(normalized)

    return ",".join(deduped)


def build_origin_list(context: InstanceEnvContext) -> str:
    """Build comma-separated HTTP/HTTPS origins for all public hosts."""

    values: list[str] = []

    for host in build_public_hosts(context):
        host_text = str(host)
        values.append(f"https://{host_text}")
        values.append(f"http://{host_text}")

    deduped: list[str] = []
    for value in values:
        if value and value not in deduped:
            deduped.append(value)

    return ",".join(deduped)


def build_csrf_trusted_origins(context: InstanceEnvContext) -> str:
    """Build comma-separated Django CSRF trusted origins."""

    return build_origin_list(context)


def build_cors_allowed_origins(context: InstanceEnvContext) -> str:
    """Build comma-separated CORS trusted origins."""

    return build_origin_list(context)


def is_placeholder(value: str) -> bool:
    """Return true if a value is an unresolved template placeholder."""

    return bool(PLACEHOLDER_PATTERN.fullmatch(value.strip()))


def validate_secret_value(key: str, value: str) -> None:
    """Reject empty, placeholder, or known-default secret values."""

    normalized = value.strip()
    if normalized in FORBIDDEN_SECRET_VALUES or is_placeholder(normalized):
        raise ValueError(f"{key} must be generated and must not use a default placeholder")
    if key == "DJANGO_SECRET_KEY" and normalized.startswith("$"):
        raise ValueError(
            "DJANGO_SECRET_KEY must not start with '$' because django-environ treats it as an environment-variable reference"
        )


def validate_no_unresolved_placeholders(values: Mapping[str, str]) -> None:
    """Reject env maps containing unresolved placeholders."""

    for key, value in values.items():
        if is_placeholder(str(value)):
            raise ValueError(f"{key} contains unresolved placeholder {value!r}")


def validate_secret_env(values: Mapping[str, str]) -> None:
    """Validate all known secret env values."""

    for key in SECRET_ENV_KEYS:
        if key in values:
            validate_secret_value(key, values[key])


def merge_env(*maps: Mapping[str, str]) -> EnvMap:
    """Merge env mappings from left to right, dropping None-like values."""

    merged: EnvMap = {}
    for mapping in maps:
        for key, value in mapping.items():
            if value is None:
                continue
            merged[str(key)] = str(value)
    return merged


def build_database_url(postgres_password: str) -> str:
    """Build the canonical internal PostgreSQL DATABASE_URL."""

    return f"postgres://konnaxion:{postgres_password}@{DockerService.POSTGRES.value}:5432/konnaxion"


def build_kx_env(context: InstanceEnvContext) -> EnvMap:
    """Build canonical KX_* instance environment variables."""

    instance_id = str(context.instance_id)
    host = normalize_host(context.host)
    host_aliases = build_host_aliases(context)
    compose_file = instance_compose_file(instance_id)
    backup_root = instance_backup_root(instance_id)

    public_enabled = effective_public_mode_enabled(context)
    public_expires_at = (
        context.public_mode_expires_at
        or str(context.extra_kx_env.get("KX_PUBLIC_MODE_EXPIRES_AT", ""))
    )

    if public_mode_requires_expiration(context) and public_enabled and not public_expires_at:
        raise ValueError(
            "KX_PUBLIC_MODE_EXPIRES_AT is mandatory for temporary public exposure."
        )

    if is_public_vps_context(context):
        if is_loopback_host(host):
            raise ValueError("public_vps requires a non-loopback KX_HOST.")

        loopback_aliases = [alias for alias in host_aliases if is_loopback_host(alias)]
        if loopback_aliases:
            aliases_text = ", ".join(str(alias) for alias in loopback_aliases)
            raise ValueError(f"public_vps host aliases must not be loopback: {aliases_text}")

    canonical = {
        "KX_INSTANCE_ID": instance_id,
        "KX_CAPSULE_ID": str(context.capsule_id),
        "KX_CAPSULE_VERSION": str(context.capsule_version),
        "KX_APP_VERSION": context.app_version,
        "KX_PARAM_VERSION": str(context.param_version),
        "KX_NETWORK_PROFILE": context.network_profile.value,
        "KX_EXPOSURE_MODE": context.exposure_mode.value,
        "KX_PUBLIC_MODE_ENABLED": "true" if public_enabled else "false",
        "KX_PUBLIC_MODE_EXPIRES_AT": public_expires_at,
        "KX_COMPOSE_FILE": str(compose_file),
        "KX_BACKUP_DIR": str(backup_root),
        "KX_HOST": str(host),
        "KX_HOST_ALIASES": ",".join(str(alias) for alias in host_aliases),
    }

    # Defaults first, user extras next, canonical runtime values last. This
    # prevents stale templates from overriding KX_HOST back to 127.0.0.1.
    return merge_env(
        KX_ENV_DEFAULTS,
        context.extra_kx_env,
        canonical,
    )


def build_django_env(context: InstanceEnvContext, runtime_secrets: GeneratedSecrets) -> EnvMap:
    """Build canonical Django runtime env."""

    if is_public_vps_context(context) and is_loopback_host(context.host):
        raise ValueError("public_vps requires a non-loopback Django host.")

    canonical = {
        "DJANGO_SECRET_KEY": runtime_secrets.django_secret_key,
        "DJANGO_ALLOWED_HOSTS": build_django_allowed_hosts(context),
        "DJANGO_CSRF_TRUSTED_ORIGINS": build_csrf_trusted_origins(context),
        "CSRF_TRUSTED_ORIGINS": build_csrf_trusted_origins(context),
        "CORS_ALLOWED_ORIGINS": build_cors_allowed_origins(context),
        "FRONTEND_BASE_URL": str(build_base_url(context.host)),
        "DATABASE_URL": build_database_url(runtime_secrets.postgres_password),
    }

    # Canonical values last so stale templates cannot keep ALLOWED_HOSTS at
    # 127.0.0.1 for public_vps.
    values = merge_env(
        DJANGO_ENV_DEFAULTS,
        context.extra_django_env,
        canonical,
    )

    validate_secret_env(values)
    validate_no_unresolved_placeholders(values)
    return values


def build_postgres_env(context: InstanceEnvContext, runtime_secrets: GeneratedSecrets) -> EnvMap:
    """Build canonical PostgreSQL runtime env."""

    canonical = {
        "POSTGRES_PASSWORD": runtime_secrets.postgres_password,
    }

    # postgres.env is loaded after django.env by application services. Keep
    # DATABASE_URL out of this file so it cannot override Django's canonical
    # connection URL with a stale/default credential.
    postgres_defaults = {
        str(key): str(value)
        for key, value in DATABASE_ENV_DEFAULTS.items()
        if str(key) != "DATABASE_URL"
    }
    extra_postgres = {
        str(key): str(value)
        for key, value in context.extra_postgres_env.items()
        if str(key) != "DATABASE_URL"
    }
    values = merge_env(
        postgres_defaults,
        extra_postgres,
        canonical,
    )
    values.pop("DATABASE_URL", None)

    validate_secret_env(values)
    validate_no_unresolved_placeholders(values)
    return values


def build_redis_env(context: InstanceEnvContext) -> EnvMap:
    """Build canonical Redis/Celery runtime env."""

    values = merge_env(REDIS_ENV_DEFAULTS, context.extra_redis_env)
    validate_no_unresolved_placeholders(values)
    return values


def build_frontend_env(context: InstanceEnvContext) -> EnvMap:
    """Build canonical Next.js runtime env."""

    if is_public_vps_context(context) and is_loopback_host(context.host):
        raise ValueError("public_vps requires a non-loopback frontend host.")

    canonical = {
        "NEXT_PUBLIC_API_BASE": str(build_api_base_url(context.host)),
        "NEXT_PUBLIC_BACKEND_BASE": str(build_base_url(context.host)),
        "NEXT_TELEMETRY_DISABLED": "1",
        "NODE_OPTIONS": DEFAULT_FRONTEND_NODE_OPTIONS,
    }

    # Canonical values last so stale templates cannot keep frontend API URLs at
    # https://127.0.0.1/api for public_vps.
    values = merge_env(
        FRONTEND_ENV_DEFAULTS,
        context.extra_frontend_env,
        canonical,
    )

    validate_no_unresolved_placeholders(values)
    return values


def build_env_file_specs(
    context: InstanceEnvContext,
    runtime_secrets: GeneratedSecrets,
) -> tuple[EnvFileSpec, ...]:
    """Build all canonical env file specs for an instance."""

    return (
        EnvFileSpec(KX_ENV_FILENAME, build_kx_env(context), secret=False),
        EnvFileSpec(DJANGO_ENV_FILENAME, build_django_env(context, runtime_secrets), secret=True),
        EnvFileSpec(POSTGRES_ENV_FILENAME, build_postgres_env(context, runtime_secrets), secret=True),
        EnvFileSpec(REDIS_ENV_FILENAME, build_redis_env(context), secret=False),
        EnvFileSpec(FRONTEND_ENV_FILENAME, build_frontend_env(context), secret=False),
    )


def format_env_value(value: str) -> str:
    """Format one env value for dotenv-compatible files."""

    if value == "":
        return ""

    if re.search(r"\s|#|'|\"|\\|\$", value):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'

    return value


def serialize_env(values: Mapping[str, str]) -> str:
    """Serialize an env mapping to deterministic dotenv text."""

    lines = []
    for key in sorted(values):
        value = values[key]
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            raise ValueError(f"invalid env key: {key!r}")
        lines.append(f"{key}={format_env_value(str(value))}")
    return "\n".join(lines) + "\n"


def parse_env_template(path: Path | str) -> EnvMap:
    """Parse a simple KEY=value env template.

    Comments and blank lines are ignored. The parser intentionally does not
    evaluate shell syntax.
    """

    template_path = Path(path)
    values: EnvMap = {}

    for line_number, raw_line in enumerate(
        template_path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"{template_path}:{line_number}: expected KEY=value")
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            raise ValueError(f"{template_path}:{line_number}: invalid env key {key!r}")
        values[key] = value

    return values


def render_template_value(value: str, variables: Mapping[str, str]) -> str:
    """Render ${VAR} and {{ VAR }} placeholders from a variable map."""

    def replace(match: re.Match[str]) -> str:
        key = match.group(1) or match.group(2)
        if key not in variables:
            raise ValueError(f"missing template variable {key}")
        return variables[key]

    return TEMPLATE_VAR_PATTERN.sub(replace, value)


def render_env_template(template: Mapping[str, str], variables: Mapping[str, str]) -> EnvMap:
    """Render all values in an env template using a variable map."""

    return {
        key: render_template_value(value, variables)
        for key, value in template.items()
    }


def ensure_private_env_dir(path: Path) -> None:
    """Create and chmod the instance env directory."""

    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, ENV_DIR_MODE)
    except PermissionError:
        # Some test/dev filesystems do not permit chmod.
        pass


def atomic_write_text(path: Path, content: str, *, mode: int = ENV_FILE_MODE) -> None:
    """Atomically write text to a file and apply private permissions."""

    path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile(
        "w",
        delete=False,
        dir=str(path.parent),
        encoding="utf-8",
        prefix=f".{path.name}.",
    ) as tmp:
        tmp.write(content)
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp_path = Path(tmp.name)

    try:
        os.chmod(tmp_path, mode)
    except PermissionError:
        pass

    tmp_path.replace(path)


def write_env_file(path: Path, values: Mapping[str, str], *, secret: bool = True) -> Path:
    """Write one env file with deterministic ordering."""

    validate_no_unresolved_placeholders(values)
    if secret:
        validate_secret_env(values)

    atomic_write_text(path, serialize_env(values), mode=ENV_FILE_MODE)
    return path


def load_existing_env_file(path: Path | str) -> EnvMap:
    """Load an existing generated env file.

    This is intentionally conservative and supports only KEY=value lines.
    """

    return parse_env_template(path)


def _postgres_password_from_database_url(database_url: str) -> str:
    """Extract PostgreSQL password from DATABASE_URL when available."""

    if not database_url:
        return ""

    parsed = urlparse(database_url)
    if parsed.password is None:
        return ""

    return unquote(parsed.password)


def load_existing_runtime_secrets(instance_id: InstanceID | str) -> GeneratedSecrets | None:
    """Load existing runtime secrets for an instance, if complete.

    This prevents host/profile rewrites from rotating DJANGO_SECRET_KEY,
    POSTGRES_PASSWORD, or the password embedded in DATABASE_URL.
    """

    target_env_dir = instance_env_dir(str(instance_id))
    values: EnvMap = {}

    for filename in (
        LEGACY_KX_ENV_FILENAME,
        KX_ENV_FILENAME,
        DJANGO_ENV_FILENAME,
        POSTGRES_ENV_FILENAME,
        REDIS_ENV_FILENAME,
        FRONTEND_ENV_FILENAME,
    ):
        path = target_env_dir / filename
        if path.exists():
            values.update(load_existing_env_file(path))

    django_secret_key = str(values.get("DJANGO_SECRET_KEY", "")).strip()
    postgres_password = str(values.get("POSTGRES_PASSWORD", "")).strip()

    if not postgres_password:
        postgres_password = _postgres_password_from_database_url(
            str(values.get("DATABASE_URL", ""))
        )

    if not postgres_password:
        return None

    # Legacy deployments may contain a random key beginning with '$'.
    # django-environ interprets that as a reference to another environment
    # variable. Rotate only the Django key and preserve the database password.
    if not django_secret_key or django_secret_key.startswith("$"):
        django_secret_key = generate_secret_key()

    validate_secret_value("DJANGO_SECRET_KEY", django_secret_key)
    validate_secret_value("POSTGRES_PASSWORD", postgres_password)

    return GeneratedSecrets(
        django_secret_key=django_secret_key,
        postgres_password=postgres_password,
    )


def write_instance_env_files(
    context: InstanceEnvContext,
    runtime_secrets: GeneratedSecrets | None = None,
    *,
    overwrite: bool = True,
) -> InstanceEnvBundle:
    """Generate and write canonical env files for a Konnaxion instance.

    When rewriting existing env files and runtime_secrets is omitted, this
    function preserves existing secrets. That lets network/profile/domain
    changes rewrite host-derived values without invalidating the database or
    Django signing/session secrets.
    """

    if runtime_secrets is not None:
        secrets_to_write = runtime_secrets
    elif overwrite:
        secrets_to_write = load_existing_runtime_secrets(context.instance_id) or generate_runtime_secrets()
    else:
        secrets_to_write = generate_runtime_secrets()

    target_env_dir = instance_env_dir(str(context.instance_id))
    ensure_private_env_dir(target_env_dir)

    specs = build_env_file_specs(context, secrets_to_write)
    written: list[Path] = []

    for spec in specs:
        path = target_env_dir / spec.filename
        if path.exists() and not overwrite:
            raise FileExistsError(f"env file already exists: {path}")
        write_env_file(path, spec.values, secret=spec.secret)
        written.append(path)

    return InstanceEnvBundle(
        instance_id=context.instance_id,
        env_dir=target_env_dir,
        files=tuple(written),
        host=normalize_host(context.host),
        public_url=build_base_url(context.host),
        api_base_url=build_api_base_url(context.host),
        host_aliases=build_host_aliases(context),
    )


def load_instance_env(instance_id: InstanceID | str) -> EnvMap:
    """Load all canonical env files for an instance into one map."""

    target_env_dir = instance_env_dir(str(instance_id))
    merged: EnvMap = {}

    # Read legacy first, then canonical kx.env so the current file wins.
    for filename in (
        LEGACY_KX_ENV_FILENAME,
        KX_ENV_FILENAME,
        DJANGO_ENV_FILENAME,
        POSTGRES_ENV_FILENAME,
        REDIS_ENV_FILENAME,
        FRONTEND_ENV_FILENAME,
    ):
        path = target_env_dir / filename
        if path.exists():
            merged.update(load_existing_env_file(path))

    return merged


def env_file_paths(instance_id: InstanceID | str) -> tuple[Path, ...]:
    """Return canonical env file paths for an instance."""

    target_env_dir = instance_env_dir(str(instance_id))
    return (
        target_env_dir / KX_ENV_FILENAME,
        target_env_dir / DJANGO_ENV_FILENAME,
        target_env_dir / POSTGRES_ENV_FILENAME,
        target_env_dir / REDIS_ENV_FILENAME,
        target_env_dir / FRONTEND_ENV_FILENAME,
    )


def assert_env_files_exist(instance_id: InstanceID | str) -> None:
    """Raise if any canonical env file is missing."""

    missing = [path for path in env_file_paths(instance_id) if not path.exists()]
    if missing:
        missing_text = ", ".join(str(path) for path in missing)
        raise FileNotFoundError(f"missing env files for instance {instance_id}: {missing_text}")


def validate_written_env(instance_id: InstanceID | str) -> None:
    """Validate generated env files for startup readiness."""

    assert_env_files_exist(instance_id)
    values = load_instance_env(instance_id)

    required = {
        "KX_INSTANCE_ID",
        "KX_CAPSULE_ID",
        "KX_CAPSULE_VERSION",
        "KX_APP_VERSION",
        "KX_PARAM_VERSION",
        "KX_NETWORK_PROFILE",
        "KX_EXPOSURE_MODE",
        "KX_BACKUP_DIR",
        "KX_COMPOSE_FILE",
        "KX_HOST",
        "DJANGO_SECRET_KEY",
        "DJANGO_ALLOWED_HOSTS",
        "DATABASE_URL",
        "POSTGRES_PASSWORD",
        "POSTGRES_HOST",
        "POSTGRES_PORT",
        "POSTGRES_DB",
        "POSTGRES_USER",
        "REDIS_URL",
        "CELERY_BROKER_URL",
        "CELERY_RESULT_BACKEND",
        "FRONTEND_BASE_URL",
        "NEXT_PUBLIC_API_BASE",
        "NEXT_PUBLIC_BACKEND_BASE",
    }

    missing = sorted(required - set(values))
    if missing:
        raise ValueError(f"missing required env keys: {', '.join(missing)}")

    validate_secret_env(values)
    validate_no_unresolved_placeholders(values)

    network_profile = NetworkProfile(values["KX_NETWORK_PROFILE"])
    exposure_mode = ExposureMode(values["KX_EXPOSURE_MODE"])

    host = normalize_host(values["KX_HOST"])
    host_aliases = normalize_host_aliases(host, values.get("KX_HOST_ALIASES", ""))
    public_hosts = (host, *host_aliases)

    allowed_hosts = {
        item.strip()
        for item in values["DJANGO_ALLOWED_HOSTS"].split(",")
        if item.strip()
    }

    if network_profile == NetworkProfile.PUBLIC_VPS:
        if exposure_mode != ExposureMode.PUBLIC:
            raise ValueError("public_vps generated env requires public exposure.")

        if is_loopback_host(host):
            raise ValueError("public_vps generated env must not use loopback KX_HOST.")

        loopback_aliases = [alias for alias in host_aliases if is_loopback_host(alias)]
        if loopback_aliases:
            aliases_text = ", ".join(str(alias) for alias in loopback_aliases)
            raise ValueError(f"public_vps generated env must not use loopback aliases: {aliases_text}")

        for public_host in public_hosts:
            if str(public_host) not in allowed_hosts:
                raise ValueError(
                    "DJANGO_ALLOWED_HOSTS must include every public host for public_vps: "
                    f"missing {public_host!r}"
                )

        expected_api_base = f"https://{host}/api"
        expected_backend_base = f"https://{host}"

        if values["NEXT_PUBLIC_API_BASE"] != expected_api_base:
            raise ValueError(
                "NEXT_PUBLIC_API_BASE must use KX_HOST for public_vps: "
                f"expected {expected_api_base!r}, got {values['NEXT_PUBLIC_API_BASE']!r}"
            )

        if values["NEXT_PUBLIC_BACKEND_BASE"] != expected_backend_base:
            raise ValueError(
                "NEXT_PUBLIC_BACKEND_BASE must use KX_HOST for public_vps: "
                f"expected {expected_backend_base!r}, got {values['NEXT_PUBLIC_BACKEND_BASE']!r}"
            )

    if (
        network_profile == NetworkProfile.PUBLIC_TEMPORARY
        or exposure_mode == ExposureMode.TEMPORARY_TUNNEL
    ):
        if values.get("KX_PUBLIC_MODE_ENABLED") != "true":
            raise ValueError("temporary public generated env must enable public mode.")
        if not values.get("KX_PUBLIC_MODE_EXPIRES_AT"):
            raise ValueError("temporary public generated env requires expiration.")


__all__ = [
    "DJANGO_ENV_FILENAME",
    "DJANGO_INTERNAL_ALLOWED_HOSTS",
    "ENV_DIR_MODE",
    "ENV_FILE_MODE",
    "FORBIDDEN_SECRET_VALUES",
    "FRONTEND_ENV_FILENAME",
    "GeneratedSecrets",
    "InstanceEnvBundle",
    "InstanceEnvContext",
    "KX_ENV_FILENAME",
    "LEGACY_KX_ENV_FILENAME",
    "LOOPBACK_HOSTS",
    "POSTGRES_ENV_FILENAME",
    "REDIS_ENV_FILENAME",
    "SECRET_ENV_KEYS",
    "TEMPLATE_VAR_PATTERN",
    "atomic_write_text",
    "assert_env_files_exist",
    "build_api_base_url",
    "build_base_url",
    "build_cors_allowed_origins",
    "build_csrf_trusted_origins",
    "build_database_url",
    "build_django_allowed_hosts",
    "build_django_env",
    "build_env_file_specs",
    "build_frontend_env",
    "build_host_aliases",
    "default_public_host_aliases",
    "build_kx_env",
    "build_origin_list",
    "build_postgres_env",
    "build_public_hosts",
    "build_redis_env",
    "effective_public_mode_enabled",
    "env_file_paths",
    "format_env_value",
    "generate_password",
    "generate_runtime_secrets",
    "generate_secret_key",
    "is_loopback_host",
    "is_placeholder",
    "is_public_vps_context",
    "is_temporary_public_context",
    "load_existing_env_file",
    "load_existing_runtime_secrets",
    "load_instance_env",
    "merge_env",
    "normalize_host",
    "normalize_host_aliases",
    "parse_env_template",
    "public_mode_requires_expiration",
    "render_env_template",
    "render_template_value",
    "serialize_env",
    "split_host_aliases",
    "validate_no_unresolved_placeholders",
    "validate_secret_env",
    "validate_secret_value",
    "validate_written_env",
    "write_env_file",
    "write_instance_env_files",
]