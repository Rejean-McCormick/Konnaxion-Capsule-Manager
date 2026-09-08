"""
Konnaxion Capsule packaging utilities.

A Konnaxion Capsule is the signed, portable deployment artifact consumed by the
Konnaxion Capsule Manager and Agent.

Canonical user-facing extension:

    .kxcap

MVP physical format:

    tar archive + zstd compression

This module can package an already-prepared capsule staging directory, and it
also provides the bootstrap build_package() adapter used by kx_builder.main.
When build_package() receives a normal source tree, it creates a canonical
staging directory first, then packages it.

It does not start services, load Docker images, change firewall rules, or mutate
runtime host state.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
from dataclasses import asdict, dataclass, is_dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Iterable, Mapping

from kx_shared.konnaxion_constants import (
    CAPSULE_EXTENSION,
    CAPSULE_FILENAME_PATTERN,
    DEFAULT_CHANNEL,
    KX_CAPSULES_DIR,
)


PACKAGE_SCHEMA_VERSION = "kx-package/v1"


def _build_progress_event(phase: str, progress: int, message: str) -> None:
    """Best-effort progress event for Capsule Manager build jobs.

    The Builder remains standalone: when no KX_BUILD_* paths are supplied this
    function is a no-op.  Build correctness never depends on progress I/O.
    """

    now = datetime.now(UTC).replace(microsecond=0).isoformat()
    log_file = os.getenv("KX_BUILD_JOB_LOG_FILE", "").strip()
    progress_file = os.getenv("KX_BUILD_PROGRESS_FILE", "").strip()

    if log_file:
        try:
            path = Path(log_file).expanduser()
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8", errors="replace") as handle:
                handle.write(f"[{now}] {phase}: {message}\n")
        except OSError:
            pass

    if progress_file:
        try:
            path = Path(progress_file).expanduser()
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "phase": str(phase),
                "progress": max(0, min(100, int(progress))),
                "message": str(message),
                "updated_at": now,
            }
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
            os.replace(tmp, path)
        except (OSError, TypeError, ValueError):
            pass


def _build_progress_log(message: str) -> None:
    log_file = os.getenv("KX_BUILD_JOB_LOG_FILE", "").strip()
    if not log_file:
        return
    now = datetime.now(UTC).replace(microsecond=0).isoformat()
    try:
        path = Path(log_file).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", errors="replace") as handle:
            handle.write(f"[{now}] {message.rstrip()}\n")
    except OSError:
        pass

MANIFEST_FILENAME = "manifest.yaml"
COMPOSE_FILENAME = "docker-compose.capsule.yml"
IMAGE_METADATA_FILENAME = "images.yaml"
CHECKSUMS_FILENAME = "checksums.txt"
SIGNATURE_FILENAME = "signature.sig"
ARTIFACT_DESCRIPTOR_FILENAME = "artifact.yaml"
SOURCE_ARTIFACT_DESCRIPTOR_FILENAME = "capsule-artifact.yaml"

REQUIRED_ROOT_FILES = frozenset(
    {
        MANIFEST_FILENAME,
        COMPOSE_FILENAME,
        IMAGE_METADATA_FILENAME,
        CHECKSUMS_FILENAME,
        SIGNATURE_FILENAME,
    }
)

REQUIRED_ROOT_DIRS = frozenset(
    {
        "images",
        "profiles",
        "env-templates",
        "migrations",
        "healthchecks",
        "policies",
        "metadata",
    }
)

OPTIONAL_ROOT_FILES = frozenset(
    {
        ARTIFACT_DESCRIPTOR_FILENAME,
    }
)

OPTIONAL_ROOT_DIRS = frozenset(
    {
        "seed-data",
        "contributions",
    }
)

ALLOWED_ROOT_ENTRIES = (
    REQUIRED_ROOT_FILES | OPTIONAL_ROOT_FILES | REQUIRED_ROOT_DIRS | OPTIONAL_ROOT_DIRS
)

REQUIRED_ENV_TEMPLATES = frozenset(
    {
        "env-templates/django.env.template",
        "env-templates/frontend.env.template",
        "env-templates/postgres.env.template",
        "env-templates/redis.env.template",
    }
)

REQUIRED_PROFILE_FILES = frozenset(
    {
        "profiles/local_only.yaml",
        "profiles/intranet_private.yaml",
        "profiles/private_tunnel.yaml",
        "profiles/public_temporary.yaml",
        "profiles/public_vps.yaml",
        "profiles/offline.yaml",
    }
)

# Runtime image archives that must be present for a deployable MVP capsule.
# Celery services reuse the django-api image, but each canonical runtime service
# still receives a manifest-visible archive so verification can prove service
# coverage.
REQUIRED_IMAGE_ARCHIVES = frozenset(
    {
        "images/frontend-next.oci.tar",
        "images/django-api.oci.tar",
        "images/traefik.oci.tar",
        "images/postgres.oci.tar",
        "images/redis.oci.tar",
        "images/celeryworker.oci.tar",
        "images/celerybeat.oci.tar",
        "images/media-nginx.oci.tar",
    }
)

OPTIONAL_IMAGE_ARCHIVES = frozenset(
    {
        "images/flower.oci.tar",
    }
)

CANONICAL_IMAGE_TAGS = {
    "frontend-next": "konnaxion/frontend-next:{app_version}",
    "django-api": "konnaxion/django-api:{app_version}",
    "traefik": "traefik:v3.1",
    "postgres": "postgres:16",
    "redis": "redis:7",
    "celeryworker": "konnaxion/django-api:{app_version}",
    "celerybeat": "konnaxion/django-api:{app_version}",
    "flower": "konnaxion/django-api:{app_version}",
    "media-nginx": "nginx:stable",
}

EXTERNAL_RUNTIME_SERVICES = frozenset(
    {
        "traefik",
        "postgres",
        "redis",
        "media-nginx",
    }
)

DJANGO_IMAGE_ALIAS_SERVICES = frozenset(
    {
        "celeryworker",
        "celerybeat",
        "flower",
    }
)


PROFILE_SPECS: dict[str, dict[str, Any]] = {
    "local_only": {
        "exposure_mode": "private",
        "public_mode_enabled": False,
        "binding": "loopback",
        "description": "Accessible only from the local machine.",
    },
    "intranet_private": {
        "exposure_mode": "private",
        "public_mode_enabled": False,
        "binding": "lan",
        "description": "Private LAN or intranet deployment.",
    },
    "private_tunnel": {
        "exposure_mode": "vpn",
        "public_mode_enabled": False,
        "binding": "vpn",
        "description": "Accessible through a private tunnel or VPN.",
    },
    "public_temporary": {
        "exposure_mode": "temporary_tunnel",
        "public_mode_enabled": True,
        "binding": "tunnel",
        "requires_expiration": True,
        "description": "Temporary public demo deployment.",
    },
    "public_vps": {
        "exposure_mode": "public",
        "public_mode_enabled": True,
        "binding": "public",
        "requires_hardened_host": True,
        "description": "Permanent public VPS deployment.",
    },
    "offline": {
        "exposure_mode": "private",
        "public_mode_enabled": False,
        "binding": "none",
        "description": "No external network exposure.",
    },
}

FORBIDDEN_CAPSULE_FILENAMES = frozenset(
    {
        ".env",
        "id_rsa",
        "id_dsa",
        "id_ecdsa",
        "id_ed25519",
        "authorized_keys",
        "known_hosts",
        "docker.sock",
        "kubeconfig",
        "credentials",
        "credentials.json",
        "service-account.json",
    }
)

FORBIDDEN_FILENAME_PATTERNS = (
    re.compile(r".*\.pem$", re.IGNORECASE),
    re.compile(r".*\.key$", re.IGNORECASE),
    re.compile(r".*private.*key.*", re.IGNORECASE),
    re.compile(r".*secret.*", re.IGNORECASE),
    re.compile(r".*token.*", re.IGNORECASE),
    re.compile(r".*password.*", re.IGNORECASE),
)

FORBIDDEN_TEXT_PATTERNS = (
    re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(rb"DJANGO_SECRET_KEY\s*=\s*['\"]?[^<'\"\s][^'\"\n]*"),
    re.compile(rb"POSTGRES_PASSWORD\s*=\s*['\"]?[^<'\"\s][^'\"\n]*"),
    re.compile(rb"DATABASE_URL\s*=\s*postgres://[^<\s]+"),
    re.compile(rb"REDIS_URL\s*=\s*redis://[^<\s]+"),
    re.compile(rb"AKIA[0-9A-Z]{16}"),
    re.compile(rb"ghp_[A-Za-z0-9_]{20,}"),
)

TEXT_SCAN_EXTENSIONS = frozenset(
    {
        ".env",
        ".template",
        ".txt",
        ".yaml",
        ".yml",
        ".json",
        ".toml",
        ".ini",
        ".cfg",
        ".conf",
        ".md",
    }
)

DEFAULT_COMPRESSION_LEVEL = 19
DEFAULT_COMMAND_TIMEOUT_SECONDS = 300

SIGNING_KEY_ENV_VARS = (
    "KX_BUILDER_SIGNING_KEY_FILE",
    "KX_CAPSULE_SIGNING_KEY_FILE",
    "KX_SIGNING_KEY_FILE",
)
SIGNING_KEY_PASSWORD_ENV_VARS = (
    "KX_BUILDER_SIGNING_KEY_PASSWORD",
    "KX_CAPSULE_SIGNING_KEY_PASSWORD",
    "KX_SIGNING_KEY_PASSWORD",
)


class PackageCompression(StrEnum):
    """Supported capsule compression modes."""

    ZSTD = "zstd"


class PackageIssueSeverity(StrEnum):
    """Packaging validation issue severity."""

    WARNING = "warning"
    BLOCKING = "blocking"


@dataclass(frozen=True, slots=True)
class PackageIssue:
    """One package validation issue."""

    severity: PackageIssueSeverity
    path: str
    message: str


@dataclass(frozen=True, slots=True)
class PackageValidationResult:
    """Validation result for a capsule staging directory or artifact."""

    ok: bool
    issues: tuple[PackageIssue, ...] = ()

    @property
    def blocking_issues(self) -> tuple[PackageIssue, ...]:
        return tuple(
            issue
            for issue in self.issues
            if issue.severity == PackageIssueSeverity.BLOCKING
        )

    @property
    def warnings(self) -> tuple[PackageIssue, ...]:
        return tuple(
            issue
            for issue in self.issues
            if issue.severity == PackageIssueSeverity.WARNING
        )


@dataclass(frozen=True, slots=True)
class PackageOptions:
    """Options used when creating a .kxcap artifact."""

    compression: PackageCompression = PackageCompression.ZSTD
    compression_level: int = DEFAULT_COMPRESSION_LEVEL
    deterministic: bool = True
    strict_root: bool = True
    scan_for_secrets: bool = True
    overwrite: bool = False
    include_package_metadata: bool = True


@dataclass(frozen=True, slots=True)
class PackageResult:
    """Result returned after creating a capsule artifact."""

    capsule_file: Path
    staging_dir: Path
    size_bytes: int
    sha256: str
    created_at: datetime
    compression: PackageCompression
    metadata_file: Path | None = None


@dataclass(frozen=True, slots=True)
class CapsuleArchiveEntry:
    """One file entry discovered inside a .kxcap archive."""

    path: str
    size: int
    mode: int
    type: str


@dataclass(frozen=True, slots=True)
class CapsuleArchiveInfo:
    """Summary of a packaged .kxcap file."""

    capsule_file: Path
    size_bytes: int
    sha256: str
    entries: tuple[CapsuleArchiveEntry, ...]


class PackageError(RuntimeError):
    """Base packaging error."""


class PackageValidationError(PackageError):
    """Raised when a capsule staging directory is invalid."""

    def __init__(self, result: PackageValidationResult) -> None:
        self.result = result
        messages = "; ".join(issue.message for issue in result.blocking_issues)
        super().__init__(messages or "capsule validation failed")


class CompressionUnavailableError(PackageError):
    """Raised when no zstd compressor/decompressor is available."""


def utc_now() -> datetime:
    """Return timezone-aware UTC datetime."""

    return datetime.now(UTC)


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat().replace("+00:00", "Z")
    if isinstance(value, StrEnum):
        return value.value
    if is_dataclass(value):
        return asdict(value)
    return str(value)


def ensure_capsule_extension(path: Path | str) -> Path:
    """Return path with canonical .kxcap extension enforced."""

    output = Path(path)
    if output.suffix != CAPSULE_EXTENSION:
        raise ValueError(f"capsule output must end with {CAPSULE_EXTENSION}: {output}")
    return output


def capsule_filename(
    *,
    channel: str = DEFAULT_CHANNEL,
    date: datetime | None = None,
) -> str:
    """Create a canonical capsule filename from the shared filename pattern."""

    timestamp = date or utc_now()
    return CAPSULE_FILENAME_PATTERN.format(
        channel=channel,
        date=timestamp.strftime("%Y.%m.%d"),
    )


def default_output_path(
    *,
    channel: str = DEFAULT_CHANNEL,
    output_dir: Path | str = KX_CAPSULES_DIR,
    date: datetime | None = None,
) -> Path:
    """Build the default output path for a packaged capsule."""

    return Path(output_dir) / capsule_filename(channel=channel, date=date)


def sha256_file(path: Path | str, *, chunk_size: int = 1024 * 1024) -> str:
    """Calculate a file SHA-256 digest."""

    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _safe_relative_path(path: Path, root: Path) -> str:
    resolved_path = path.resolve(strict=False)
    resolved_root = root.resolve(strict=False)

    try:
        relative = resolved_path.relative_to(resolved_root)
    except ValueError as exc:
        raise PackageError(f"path escapes staging root: {path}") from exc

    relative_text = relative.as_posix()
    if (
        relative_text == "."
        or relative_text.startswith("../")
        or relative_text.startswith("/")
    ):
        raise PackageError(f"unsafe relative path: {relative_text}")

    return relative_text


def iter_staging_files(staging_dir: Path | str) -> tuple[Path, ...]:
    """Return deterministic file list under a staging directory."""

    root = Path(staging_dir)
    files = [path for path in root.rglob("*") if path.is_file() or path.is_symlink()]
    return tuple(sorted(files, key=lambda item: _safe_relative_path(item, root)))


def root_entries(staging_dir: Path | str) -> set[str]:
    """Return root entry names from a staging directory."""

    root = Path(staging_dir)
    if not root.exists():
        return set()
    return {path.name for path in root.iterdir()}


def validate_required_layout(
    staging_dir: Path | str,
    *,
    strict_root: bool = True,
) -> list[PackageIssue]:
    """Validate the canonical .kxcap root layout."""

    root = Path(staging_dir)
    issues: list[PackageIssue] = []

    if not root.exists():
        return [
            PackageIssue(
                PackageIssueSeverity.BLOCKING,
                str(root),
                "capsule staging directory does not exist",
            )
        ]

    if not root.is_dir():
        return [
            PackageIssue(
                PackageIssueSeverity.BLOCKING,
                str(root),
                "capsule staging path is not a directory",
            )
        ]

    for filename in sorted(REQUIRED_ROOT_FILES):
        path = root / filename
        if not path.is_file():
            issues.append(
                PackageIssue(
                    PackageIssueSeverity.BLOCKING,
                    filename,
                    f"required capsule file is missing: {filename}",
                )
            )

    for dirname in sorted(REQUIRED_ROOT_DIRS):
        path = root / dirname
        if not path.is_dir():
            issues.append(
                PackageIssue(
                    PackageIssueSeverity.BLOCKING,
                    dirname,
                    f"required capsule directory is missing: {dirname}",
                )
            )

    for required_template in sorted(REQUIRED_ENV_TEMPLATES):
        if not (root / required_template).is_file():
            issues.append(
                PackageIssue(
                    PackageIssueSeverity.BLOCKING,
                    required_template,
                    f"required capsule env template is missing: {required_template}",
                )
            )

    for required_profile in sorted(REQUIRED_PROFILE_FILES):
        if not (root / required_profile).is_file():
            issues.append(
                PackageIssue(
                    PackageIssueSeverity.BLOCKING,
                    required_profile,
                    f"required capsule network profile is missing: {required_profile}",
                )
            )

    if strict_root:
        extra = sorted(root_entries(root) - ALLOWED_ROOT_ENTRIES)
        for entry in extra:
            issues.append(
                PackageIssue(
                    PackageIssueSeverity.WARNING,
                    entry,
                    f"non-canonical root entry will be packaged: {entry}",
                )
            )

    return issues


def validate_required_image_archives(staging_dir: Path | str) -> list[PackageIssue]:
    """Validate that canonical runtime image archives and images.yaml exist.

    A capsule that contains only ``images/README.json`` is structurally
    incomplete for deployment. The Builder must fail before signing/packaging
    such an artifact.
    """

    root = Path(staging_dir)
    issues: list[PackageIssue] = []
    images_dir = root / "images"
    metadata_file = root / IMAGE_METADATA_FILENAME

    if not images_dir.is_dir():
        return issues

    if not metadata_file.is_file():
        issues.append(
            PackageIssue(
                PackageIssueSeverity.BLOCKING,
                IMAGE_METADATA_FILENAME,
                f"required image metadata file is missing: {IMAGE_METADATA_FILENAME}",
            )
        )

    image_archives = sorted(
        path
        for path in images_dir.glob("*.oci.tar")
        if path.is_file()
    )
    if not image_archives:
        issues.append(
            PackageIssue(
                PackageIssueSeverity.BLOCKING,
                "images",
                "capsule images directory contains no .oci.tar image archives",
            )
        )

    for archive in sorted(REQUIRED_IMAGE_ARCHIVES):
        path = root / archive
        if not path.is_file():
            issues.append(
                PackageIssue(
                    PackageIssueSeverity.BLOCKING,
                    archive,
                    f"required runtime image archive is missing: {archive}",
                )
            )
            continue

        if path.stat().st_size <= 0:
            issues.append(
                PackageIssue(
                    PackageIssueSeverity.BLOCKING,
                    archive,
                    f"required runtime image archive is empty: {archive}",
                )
            )

    if metadata_file.is_file():
        issues.extend(_validate_images_yaml(root, metadata_file))

    return issues


def _validate_images_yaml(root: Path, metadata_file: Path) -> list[PackageIssue]:
    """Best-effort validation for images.yaml."""

    issues: list[PackageIssue] = []

    try:
        payload = _read_yaml_file(metadata_file)
    except Exception as exc:
        return [
            PackageIssue(
                PackageIssueSeverity.BLOCKING,
                IMAGE_METADATA_FILENAME,
                f"could not parse {IMAGE_METADATA_FILENAME}: {exc}",
            )
        ]

    images = payload.get("images") if isinstance(payload, Mapping) else None
    if not isinstance(images, list) or not images:
        return [
            PackageIssue(
                PackageIssueSeverity.BLOCKING,
                IMAGE_METADATA_FILENAME,
                f"{IMAGE_METADATA_FILENAME} must contain a non-empty images list",
            )
        ]

    by_archive: dict[str, Mapping[str, Any]] = {}
    by_service: dict[str, Mapping[str, Any]] = {}

    for item in images:
        if not isinstance(item, Mapping):
            issues.append(
                PackageIssue(
                    PackageIssueSeverity.BLOCKING,
                    IMAGE_METADATA_FILENAME,
                    f"invalid image metadata entry: {item!r}",
                )
            )
            continue

        service = str(item.get("service") or "").strip()
        archive = str(item.get("archive") or "").strip()
        sha256 = str(item.get("sha256") or "").strip()
        size_bytes = int(item.get("size_bytes") or 0)

        if not service:
            issues.append(
                PackageIssue(
                    PackageIssueSeverity.BLOCKING,
                    IMAGE_METADATA_FILENAME,
                    "image metadata entry missing service",
                )
            )

        if not archive:
            issues.append(
                PackageIssue(
                    PackageIssueSeverity.BLOCKING,
                    IMAGE_METADATA_FILENAME,
                    f"image metadata entry for {service or '<unknown>'} missing archive",
                )
            )
            continue

        archive_path = archive if archive.startswith("images/") else f"images/{archive}"
        by_archive[archive_path] = item
        if service:
            by_service[service] = item

        file_path = root / archive_path
        if not file_path.is_file():
            issues.append(
                PackageIssue(
                    PackageIssueSeverity.BLOCKING,
                    archive_path,
                    f"image metadata references missing archive: {archive_path}",
                )
            )
            continue

        if size_bytes and file_path.stat().st_size != size_bytes:
            issues.append(
                PackageIssue(
                    PackageIssueSeverity.BLOCKING,
                    archive_path,
                    f"image archive size mismatch for {archive_path}",
                )
            )

        if sha256:
            actual = sha256_file(file_path)
            if actual != sha256:
                issues.append(
                    PackageIssue(
                        PackageIssueSeverity.BLOCKING,
                        archive_path,
                        f"image archive checksum mismatch for {archive_path}",
                    )
                )

    for required_archive in sorted(REQUIRED_IMAGE_ARCHIVES):
        if required_archive not in by_archive:
            issues.append(
                PackageIssue(
                    PackageIssueSeverity.BLOCKING,
                    IMAGE_METADATA_FILENAME,
                    f"{IMAGE_METADATA_FILENAME} missing required archive entry: {required_archive}",
                )
            )

    for required_service in sorted(_service_from_archive(path) for path in REQUIRED_IMAGE_ARCHIVES):
        if required_service not in by_service:
            issues.append(
                PackageIssue(
                    PackageIssueSeverity.BLOCKING,
                    IMAGE_METADATA_FILENAME,
                    f"{IMAGE_METADATA_FILENAME} missing required service entry: {required_service}",
                )
            )

    return issues


def _service_from_archive(path: str) -> str:
    return Path(path).name.removesuffix(".oci.tar")


def _filename_is_forbidden(path: Path) -> bool:
    name = path.name
    if name in FORBIDDEN_CAPSULE_FILENAMES:
        return True
    return any(pattern.fullmatch(name) for pattern in FORBIDDEN_FILENAME_PATTERNS)


def _should_scan_text(path: Path) -> bool:
    return path.suffix.lower() in TEXT_SCAN_EXTENSIONS or path.name in FORBIDDEN_CAPSULE_FILENAMES


def scan_file_for_secret_patterns(path: Path, root: Path) -> list[PackageIssue]:
    """Scan one file for obvious secret patterns."""

    issues: list[PackageIssue] = []
    relative = _safe_relative_path(path, root)

    if _filename_is_forbidden(path):
        issues.append(
            PackageIssue(
                PackageIssueSeverity.BLOCKING,
                relative,
                f"forbidden secret-like filename in capsule: {relative}",
            )
        )

    if not _should_scan_text(path):
        return issues

    try:
        data = path.read_bytes()
    except OSError as exc:
        issues.append(
            PackageIssue(
                PackageIssueSeverity.BLOCKING,
                relative,
                f"could not read file for secret scan: {exc}",
            )
        )
        return issues

    for pattern in FORBIDDEN_TEXT_PATTERNS:
        if pattern.search(data):
            issues.append(
                PackageIssue(
                    PackageIssueSeverity.BLOCKING,
                    relative,
                    f"secret-like content detected in capsule file: {relative}",
                )
            )
            break

    return issues


def scan_staging_for_secrets(staging_dir: Path | str) -> list[PackageIssue]:
    """Scan staging files for obvious secrets that must not enter a capsule."""

    root = Path(staging_dir)
    issues: list[PackageIssue] = []

    for path in iter_staging_files(root):
        if path.is_symlink():
            issues.append(
                PackageIssue(
                    PackageIssueSeverity.BLOCKING,
                    _safe_relative_path(path, root),
                    "symlinks are not allowed in capsule staging",
                )
            )
            continue

        issues.extend(scan_file_for_secret_patterns(path, root))

    return issues


def validate_staging_dir(
    staging_dir: Path | str,
    *,
    options: PackageOptions | None = None,
) -> PackageValidationResult:
    """Validate a capsule staging directory."""

    resolved_options = options or PackageOptions()
    issues: list[PackageIssue] = []

    issues.extend(
        validate_required_layout(
            staging_dir,
            strict_root=resolved_options.strict_root,
        )
    )

    issues.extend(validate_required_image_archives(staging_dir))

    if resolved_options.scan_for_secrets:
        issues.extend(scan_staging_for_secrets(staging_dir))

    blocking = [
        issue
        for issue in issues
        if issue.severity == PackageIssueSeverity.BLOCKING
    ]
    return PackageValidationResult(ok=not blocking, issues=tuple(issues))


def raise_if_invalid(result: PackageValidationResult) -> None:
    """Raise PackageValidationError if validation failed."""

    if not result.ok:
        raise PackageValidationError(result)


def _deterministic_tarinfo(tarinfo: tarfile.TarInfo) -> tarfile.TarInfo:
    tarinfo.uid = 0
    tarinfo.gid = 0
    tarinfo.uname = "root"
    tarinfo.gname = "root"
    tarinfo.mtime = 0

    if tarinfo.isfile():
        tarinfo.mode = 0o644
    elif tarinfo.isdir():
        tarinfo.mode = 0o755

    return tarinfo


def create_tar_archive(
    staging_dir: Path | str,
    tar_path: Path | str,
    *,
    deterministic: bool = True,
) -> Path:
    """Create an intermediate tar archive from staging contents."""

    root = Path(staging_dir)
    output = Path(tar_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    filter_func = _deterministic_tarinfo if deterministic else None

    with tarfile.open(output, "w", format=tarfile.PAX_FORMAT) as tar:
        dirs = sorted(
            [path for path in root.rglob("*") if path.is_dir()],
            key=lambda item: _safe_relative_path(item, root),
        )

        for directory in dirs:
            arcname = _safe_relative_path(directory, root)
            tar.add(directory, arcname=arcname, recursive=False, filter=filter_func)

        for file_path in iter_staging_files(root):
            arcname = _safe_relative_path(file_path, root)
            tar.add(file_path, arcname=arcname, recursive=False, filter=filter_func)

    return output


def _compress_with_python_zstandard(
    tar_path: Path,
    output_path: Path,
    *,
    level: int,
) -> bool:
    """Compress using optional zstandard Python package if installed."""

    try:
        import zstandard as zstd  # type: ignore[import-not-found]
    except ImportError:
        return False

    compressor = zstd.ZstdCompressor(level=level)
    with tar_path.open("rb") as source, output_path.open("wb") as target:
        compressor.copy_stream(source, target)

    return True


def _decompress_with_python_zstandard(
    capsule_path: Path,
    tar_path: Path,
) -> bool:
    """Decompress using optional zstandard Python package if installed."""

    try:
        import zstandard as zstd  # type: ignore[import-not-found]
    except ImportError:
        return False

    decompressor = zstd.ZstdDecompressor()
    with capsule_path.open("rb") as source, tar_path.open("wb") as target:
        decompressor.copy_stream(source, target)

    return True


def _zstd_binary() -> str | None:
    return shutil.which("zstd")


def _compress_with_zstd_binary(
    tar_path: Path,
    output_path: Path,
    *,
    level: int,
    timeout_seconds: int = DEFAULT_COMMAND_TIMEOUT_SECONDS,
) -> bool:
    """Compress using zstd CLI if installed."""

    zstd_bin = _zstd_binary()
    if not zstd_bin:
        return False

    subprocess.run(
        [
            zstd_bin,
            f"-{level}",
            "--force",
            "--quiet",
            "-o",
            str(output_path),
            str(tar_path),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
    )
    return True


def _decompress_with_zstd_binary(
    capsule_path: Path,
    tar_path: Path,
    *,
    timeout_seconds: int = DEFAULT_COMMAND_TIMEOUT_SECONDS,
) -> bool:
    """Decompress using zstd CLI if installed."""

    zstd_bin = _zstd_binary()
    if not zstd_bin:
        return False

    subprocess.run(
        [
            zstd_bin,
            "--decompress",
            "--force",
            "--quiet",
            "-o",
            str(tar_path),
            str(capsule_path),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
    )
    return True


def compress_tar_to_kxcap(
    tar_path: Path | str,
    output_path: Path | str,
    *,
    level: int = DEFAULT_COMPRESSION_LEVEL,
) -> Path:
    """Compress a tar archive to a .kxcap zstd artifact."""

    source = Path(tar_path)
    output = ensure_capsule_extension(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    if output.exists():
        output.unlink()

    if _compress_with_python_zstandard(source, output, level=level):
        return output

    if _compress_with_zstd_binary(source, output, level=level):
        return output

    raise CompressionUnavailableError(
        "zstd compression unavailable: install python package 'zstandard' or the 'zstd' CLI"
    )


def decompress_kxcap_to_tar(
    capsule_file: Path | str,
    tar_path: Path | str,
) -> Path:
    """Decompress a .kxcap artifact to a temporary tar archive."""

    source = ensure_capsule_extension(capsule_file)
    output = Path(tar_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    if _decompress_with_python_zstandard(source, output):
        return output

    if _decompress_with_zstd_binary(source, output):
        return output

    raise CompressionUnavailableError(
        "zstd decompression unavailable: install python package 'zstandard' or the 'zstd' CLI"
    )


def write_package_metadata(
    staging_dir: Path | str,
    *,
    package_result: Mapping[str, Any] | None = None,
) -> Path:
    """Write package metadata under metadata/package.json."""

    root = Path(staging_dir)
    metadata_dir = root / "metadata"
    metadata_dir.mkdir(parents=True, exist_ok=True)
    metadata_file = metadata_dir / "package.json"

    payload = {
        "schema_version": PACKAGE_SCHEMA_VERSION,
        "created_at": utc_now(),
        "format": "tar+zstd",
        "extension": CAPSULE_EXTENSION,
        "package_result": dict(package_result or {}),
    }

    metadata_file.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            default=_json_default,
        )
        + "\n",
        encoding="utf-8",
    )

    return metadata_file


def package_capsule(
    staging_dir: Path | str,
    output_path: Path | str,
    *,
    options: PackageOptions | None = None,
) -> PackageResult:
    """Validate and package a capsule staging directory into a .kxcap file."""

    resolved_options = options or PackageOptions()
    root = Path(staging_dir)
    output = ensure_capsule_extension(output_path)

    validation = validate_staging_dir(root, options=resolved_options)
    raise_if_invalid(validation)

    if output.exists() and not resolved_options.overwrite:
        raise FileExistsError(f"capsule output already exists: {output}")

    output.parent.mkdir(parents=True, exist_ok=True)

    metadata_file: Path | None = None
    if resolved_options.include_package_metadata:
        metadata_file = write_package_metadata(
            root,
            package_result={
                "output_path": str(output),
                "compression": resolved_options.compression.value,
            },
        )

    with tempfile.TemporaryDirectory(prefix="kxcap-package-") as tmp_dir:
        tar_path = Path(tmp_dir) / "capsule.tar"

        _build_progress_event(
            "package",
            92,
            "Creating deterministic capsule TAR archive.",
        )
        create_tar_archive(
            root,
            tar_path,
            deterministic=resolved_options.deterministic,
        )

        if resolved_options.compression != PackageCompression.ZSTD:
            raise PackageError(f"unsupported compression: {resolved_options.compression}")

        if output.exists():
            output.unlink()

        _build_progress_event(
            "compress",
            94,
            f"Compressing capsule archive with zstd level {resolved_options.compression_level}.",
        )
        compress_tar_to_kxcap(
            tar_path,
            output,
            level=resolved_options.compression_level,
        )

    _build_progress_event(
        "digest",
        95,
        "Calculating packaged capsule SHA-256 digest.",
    )
    capsule_sha256 = sha256_file(output)

    return PackageResult(
        capsule_file=output,
        staging_dir=root,
        size_bytes=output.stat().st_size,
        sha256=capsule_sha256,
        created_at=utc_now(),
        compression=resolved_options.compression,
        metadata_file=metadata_file,
    )


def package_to_default_location(
    staging_dir: Path | str,
    *,
    channel: str = DEFAULT_CHANNEL,
    output_dir: Path | str = KX_CAPSULES_DIR,
    options: PackageOptions | None = None,
    date: datetime | None = None,
) -> PackageResult:
    """Package a capsule to the canonical output directory and filename."""

    output = default_output_path(channel=channel, output_dir=output_dir, date=date)
    return package_capsule(staging_dir, output, options=options)


def read_tar_entries(tar_path: Path | str) -> tuple[CapsuleArchiveEntry, ...]:
    """Read archive entries from an uncompressed tar archive."""

    entries: list[CapsuleArchiveEntry] = []

    with tarfile.open(tar_path, "r") as tar:
        for member in tar.getmembers():
            if member.isdir():
                entry_type = "directory"
            elif member.isfile():
                entry_type = "file"
            elif member.issym():
                entry_type = "symlink"
            else:
                entry_type = "other"

            entries.append(
                CapsuleArchiveEntry(
                    path=member.name,
                    size=member.size,
                    mode=member.mode,
                    type=entry_type,
                )
            )

    return tuple(entries)


def inspect_capsule(capsule_file: Path | str) -> CapsuleArchiveInfo:
    """Inspect a .kxcap file without extracting it to a final destination."""

    source = ensure_capsule_extension(capsule_file)

    with tempfile.TemporaryDirectory(prefix="kxcap-inspect-") as tmp_dir:
        tar_path = Path(tmp_dir) / "capsule.tar"
        decompress_kxcap_to_tar(source, tar_path)
        entries = read_tar_entries(tar_path)

    return CapsuleArchiveInfo(
        capsule_file=source,
        size_bytes=source.stat().st_size,
        sha256=sha256_file(source),
        entries=entries,
    )


def validate_capsule_archive(capsule_file: Path | str) -> PackageValidationResult:
    """Validate a packaged .kxcap archive root layout."""

    source = ensure_capsule_extension(capsule_file)
    issues: list[PackageIssue] = []

    if not source.exists():
        return PackageValidationResult(
            ok=False,
            issues=(
                PackageIssue(
                    PackageIssueSeverity.BLOCKING,
                    str(source),
                    "capsule file does not exist",
                ),
            ),
        )

    try:
        info = inspect_capsule(source)
    except Exception as exc:
        return PackageValidationResult(
            ok=False,
            issues=(
                PackageIssue(
                    PackageIssueSeverity.BLOCKING,
                    str(source),
                    f"could not inspect capsule archive: {exc}",
                ),
            ),
        )

    file_entries = {entry.path for entry in info.entries if entry.type == "file"}
    root_entries_in_archive = {entry.path.split("/", 1)[0] for entry in info.entries}

    for required_file in sorted(REQUIRED_ROOT_FILES):
        if required_file not in file_entries:
            issues.append(
                PackageIssue(
                    PackageIssueSeverity.BLOCKING,
                    required_file,
                    f"required capsule file is missing from archive: {required_file}",
                )
            )

    for required_dir in sorted(REQUIRED_ROOT_DIRS):
        if required_dir not in root_entries_in_archive:
            issues.append(
                PackageIssue(
                    PackageIssueSeverity.BLOCKING,
                    required_dir,
                    f"required capsule directory is missing from archive: {required_dir}",
                )
            )

    for required_template in sorted(REQUIRED_ENV_TEMPLATES):
        if required_template not in file_entries:
            issues.append(
                PackageIssue(
                    PackageIssueSeverity.BLOCKING,
                    required_template,
                    f"required capsule env template is missing from archive: {required_template}",
                )
            )

    for required_profile in sorted(REQUIRED_PROFILE_FILES):
        if required_profile not in file_entries:
            issues.append(
                PackageIssue(
                    PackageIssueSeverity.BLOCKING,
                    required_profile,
                    f"required capsule network profile is missing from archive: {required_profile}",
                )
            )

    for required_archive in sorted(REQUIRED_IMAGE_ARCHIVES):
        if required_archive not in file_entries:
            issues.append(
                PackageIssue(
                    PackageIssueSeverity.BLOCKING,
                    required_archive,
                    f"required runtime image archive is missing from archive: {required_archive}",
                )
            )

    image_archive_entries = [
        entry.path
        for entry in info.entries
        if entry.type == "file"
        and entry.path.startswith("images/")
        and entry.path.endswith(".oci.tar")
    ]
    if not image_archive_entries:
        issues.append(
            PackageIssue(
                PackageIssueSeverity.BLOCKING,
                "images",
                "capsule archive images directory contains no .oci.tar image archives",
            )
        )

    for entry in info.entries:
        path = Path(entry.path)

        if path.name in FORBIDDEN_CAPSULE_FILENAMES:
            issues.append(
                PackageIssue(
                    PackageIssueSeverity.BLOCKING,
                    entry.path,
                    f"forbidden filename present in archive: {entry.path}",
                )
            )

        if entry.type == "symlink":
            issues.append(
                PackageIssue(
                    PackageIssueSeverity.BLOCKING,
                    entry.path,
                    "symlink present in archive",
                )
            )

    blocking = [
        issue
        for issue in issues
        if issue.severity == PackageIssueSeverity.BLOCKING
    ]
    return PackageValidationResult(ok=not blocking, issues=tuple(issues))


def extract_capsule(
    capsule_file: Path | str,
    destination_dir: Path | str,
    *,
    overwrite: bool = False,
) -> Path:
    """Extract a .kxcap archive to a destination directory safely.

    This helper is mainly for tests and inspection. Runtime import should be
    owned by kx_agent.capsules.importer.
    """

    source = ensure_capsule_extension(capsule_file)
    destination = Path(destination_dir)

    if destination.exists() and any(destination.iterdir()) and not overwrite:
        raise FileExistsError(f"destination is not empty: {destination}")

    destination.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="kxcap-extract-") as tmp_dir:
        tar_path = Path(tmp_dir) / "capsule.tar"
        decompress_kxcap_to_tar(source, tar_path)

        with tarfile.open(tar_path, "r") as tar:
            for member in tar.getmembers():
                target = (destination / member.name).resolve(strict=False)

                try:
                    target.relative_to(destination.resolve(strict=False))
                except ValueError as exc:
                    raise PackageError(
                        f"archive member escapes destination: {member.name}"
                    ) from exc

                if member.issym() or member.islnk():
                    raise PackageError(
                        f"archive member is a link and is not allowed: {member.name}"
                    )

            tar.extractall(destination)

    return destination


def package_result_to_dict(result: PackageResult) -> dict[str, Any]:
    """Serialize a package result."""

    return json.loads(json.dumps(asdict(result), default=_json_default))


def validation_result_to_dict(result: PackageValidationResult) -> dict[str, Any]:
    """Serialize a validation result."""

    return json.loads(json.dumps(asdict(result), default=_json_default))


def _builder_versions() -> tuple[str, str]:
    """Return Builder-visible app/param versions without hard-failing old imports."""

    try:
        from kx_shared.konnaxion_constants import APP_VERSION, PARAM_VERSION

        return str(APP_VERSION), str(PARAM_VERSION)
    except Exception:
        return "v14", "kx-param-2026.04.30"


def _write_json_file(path: Path, payload: Mapping[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            default=_json_default,
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def _json_safe_payload(value: Any) -> Any:
    """Return a JSON/YAML-safe representation of a nested payload."""

    return json.loads(json.dumps(value, default=_json_default))


def _yaml_scalar(value: Any) -> str:
    """Render one scalar as YAML-safe text."""

    value = _json_safe_payload(value)

    if value is None:
        return "null"

    if isinstance(value, bool):
        return "true" if value else "false"

    if isinstance(value, (int, float)):
        return str(value)

    return json.dumps(str(value), ensure_ascii=False)


def _yaml_lines(value: Any, *, indent: int = 0) -> list[str]:
    """Render a simple JSON-like object as deterministic YAML lines."""

    prefix = " " * indent
    value = _json_safe_payload(value)

    if isinstance(value, Mapping):
        if not value:
            return [prefix + "{}"]

        lines: list[str] = []
        for key, item in value.items():
            key_text = str(key)

            if isinstance(item, Mapping):
                if item:
                    lines.append(f"{prefix}{key_text}:")
                    lines.extend(_yaml_lines(item, indent=indent + 2))
                else:
                    lines.append(f"{prefix}{key_text}: {{}}")
                continue

            if isinstance(item, list):
                if item:
                    lines.append(f"{prefix}{key_text}:")
                    lines.extend(_yaml_lines(item, indent=indent + 2))
                else:
                    lines.append(f"{prefix}{key_text}: []")
                continue

            lines.append(f"{prefix}{key_text}: {_yaml_scalar(item)}")

        return lines

    if isinstance(value, list):
        if not value:
            return [prefix + "[]"]

        lines = []
        for item in value:
            if isinstance(item, Mapping):
                lines.append(prefix + "-")
                lines.extend(_yaml_lines(item, indent=indent + 2))
            elif isinstance(item, list):
                lines.append(prefix + "-")
                lines.extend(_yaml_lines(item, indent=indent + 2))
            else:
                lines.append(f"{prefix}- {_yaml_scalar(item)}")

        return lines

    return [prefix + _yaml_scalar(value)]


def _write_yaml_file(path: Path, payload: Mapping[str, Any]) -> Path:
    """Write a YAML file without requiring PyYAML at bootstrap time."""

    path.parent.mkdir(parents=True, exist_ok=True)

    safe_payload = _json_safe_payload(payload)

    try:
        import yaml  # type: ignore[import-not-found]

        content = yaml.safe_dump(
            safe_payload,
            allow_unicode=True,
            sort_keys=False,
        )
    except Exception:
        content = "\n".join(_yaml_lines(safe_payload)) + "\n"

    path.write_text(content, encoding="utf-8")
    return path


def _read_yaml_file(path: Path) -> dict[str, Any]:
    """Read a YAML or JSON-compatible file."""

    text = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore[import-not-found]

        value = yaml.safe_load(text) or {}
    except Exception:
        value = json.loads(text)

    if not isinstance(value, dict):
        raise PackageError(f"{path} must contain a mapping")

    return value


def _write_text_file(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _source_path_is_excluded(path: Path, source_root: Path) -> bool:
    excluded_parts = {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "venv",
        "env",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".next",
        "dist",
        "build",
        "runtime",
        "capsules",
        "backups",
        "logs",
    }

    try:
        relative = path.relative_to(source_root)
    except ValueError:
        return True

    if any(part in excluded_parts for part in relative.parts):
        return True

    if path.name.startswith(".") and path.name not in {".dockerignore"}:
        return True

    if _filename_is_forbidden(path):
        return True

    if path.suffix.lower() in {".pyc", ".pyo", ".sqlite3", ".db", ".log", ".tmp"}:
        return True

    return False


def _build_source_inventory(source_dir: Path, *, max_files: int = 20_000) -> dict[str, Any]:
    """Create a deterministic source inventory without copying source/secrets into the capsule."""

    files: list[dict[str, Any]] = []

    for path in sorted(source_dir.rglob("*"), key=lambda item: item.as_posix().lower()):
        if _source_path_is_excluded(path, source_dir):
            continue

        if not path.is_file():
            continue

        try:
            relative = path.relative_to(source_dir).as_posix()
            stat = path.stat()
        except OSError:
            continue

        files.append(
            {
                "path": relative,
                "size_bytes": stat.st_size,
                "sha256": sha256_file(path),
            }
        )

    truncated = len(files) > max_files

    return {
        "source_dir": str(source_dir),
        "generated_at": utc_now(),
        "file_count": len(files),
        "truncated": truncated,
        "files": files[:max_files],
    }


def _find_existing_compose(source_dir: Path) -> Path | None:
    """Return an explicit product-owned Capsule Compose file, when present.

    Generic development Compose files are intentionally ignored.  The Capsule
    runtime contract is stricter than an arbitrary developer compose stack and
    must only be overridden by an explicitly named ``docker-compose.capsule.yml``.
    """

    candidates = (
        source_dir / "docker-compose.capsule.yml",
        source_dir / "deploy" / "docker-compose.capsule.yml",
    )

    for candidate in candidates:
        if candidate.is_file():
            return candidate

    return None


def _canonical_compose_template() -> Path:
    """Return the canonical Capsule Compose template shipped with the Builder.

    ``KX_BUILDER_COMPOSE_TEMPLATE`` may override the packaged template for
    controlled development/testing.  Missing templates are a hard build error;
    the Builder must never emit a fake runtime service that the Agent will later
    reject.
    """

    override = os.getenv("KX_BUILDER_COMPOSE_TEMPLATE", "").strip()
    candidates: list[Path] = []
    if override:
        candidates.append(Path(override).expanduser())

    repo_root = Path(__file__).resolve().parents[1]
    candidates.append(repo_root / "templates" / COMPOSE_FILENAME)

    for candidate in candidates:
        if candidate.is_file():
            return candidate

    rendered = ", ".join(str(path) for path in candidates)
    raise PackageError(
        "No explicit docker-compose.capsule.yml was found in the source tree and "
        f"the canonical Builder template is unavailable. Checked: {rendered}"
    )


def _validate_compose_file_for_capsule(path: Path) -> None:
    """Fail the build when the staged Compose file violates Agent constraints."""

    try:
        import yaml  # type: ignore[import-not-found]

        compose = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        raise PackageError(f"Could not parse Capsule Compose file {path}: {exc}") from exc

    if not isinstance(compose, Mapping):
        raise PackageError(f"Capsule Compose file must contain a YAML mapping: {path}")

    from kx_shared.validation import validate_compose_dict

    issues = validate_compose_dict(compose)
    blocking = [issue for issue in issues if getattr(issue, "blocking", True)]
    if blocking:
        detail = "; ".join(issue.message for issue in blocking)
        raise PackageError(f"Capsule Compose validation failed: {detail}")


def _write_compose_file(staging_dir: Path, source_dir: Path, *, capsule_id: str) -> Path:
    """Stage an Agent-compatible Capsule Compose file.

    An explicit product-owned ``docker-compose.capsule.yml`` wins.  Otherwise
    the Builder copies its canonical runtime template.  A placeholder runtime is
    never generated because it would produce a capsule that builds successfully
    but fails Agent verification during import.
    """

    del capsule_id  # retained in the signature for backwards-compatible callers

    output = staging_dir / COMPOSE_FILENAME
    source = _find_existing_compose(source_dir) or _canonical_compose_template()

    output.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    _validate_compose_file_for_capsule(output)

    if source.resolve(strict=False) == _canonical_compose_template().resolve(strict=False):
        _build_progress_log(f"compose: using canonical Builder template {source}")
    else:
        _build_progress_log(f"compose: using product-owned Capsule Compose {source}")

    return output


def _default_manifest_images(*, app_version: str) -> list[dict[str, str]]:
    """Return canonical runtime images declared in manifest.yaml."""

    services = (
        "frontend-next",
        "django-api",
        "traefik",
        "postgres",
        "redis",
        "celeryworker",
        "celerybeat",
        "media-nginx",
    )

    result: list[dict[str, str]] = []
    for service in services:
        result.append(
            {
                "service": service,
                "archive": f"images/{service}.oci.tar",
                "image": CANONICAL_IMAGE_TAGS[service].format(app_version=app_version),
            }
        )

    return result


def _copy_source_tree_for_docker_context(source: Path, destination: Path) -> None:
    """Copy a source tree into a clean temporary Docker build context."""

    if destination.exists():
        shutil.rmtree(destination)

    def ignore(_dir: str, names: list[str]) -> set[str]:
        ignored: set[str] = set()
        for name in names:
            lower = name.lower()
            if name in {
                ".git",
                ".hg",
                ".svn",
                ".venv",
                "venv",
                "env",
                "node_modules",
                "__pycache__",
                ".pytest_cache",
                ".mypy_cache",
                ".ruff_cache",
                ".next",
                "out",
                "dist",
                "build",
                "runtime",
                "capsules",
                "backups",
                "logs",
                "media",
                "staticfiles",
                "coverage",
                "reports",
                "test-results",
                "playwright-report",
                ".cache",
                ".turbo",
                ".vercel",
            }:
                ignored.add(name)
                continue
            if lower.endswith((".pyc", ".pyo", ".pyd", ".sqlite3", ".db", ".log", ".tmp")):
                ignored.add(name)
                continue
            if name in FORBIDDEN_CAPSULE_FILENAMES:
                ignored.add(name)
                continue
            if any(pattern.fullmatch(name) for pattern in FORBIDDEN_FILENAME_PATTERNS):
                ignored.add(name)
        return ignored

    shutil.copytree(source, destination, ignore=ignore)


def _write_frontend_capsule_dockerfile(context_dir: Path) -> Path:
    """Write the production frontend Dockerfile used by capsule builds."""

    dockerfile = context_dir / "Dockerfile.capsule"
    dockerfile.write_text(
        "\n".join(
            [
                "FROM node:20-alpine AS builder",
                "WORKDIR /app",
                "RUN corepack enable",
                "COPY package.json pnpm-lock.yaml* ./",
                "RUN pnpm install --no-frozen-lockfile",
                "COPY . .",
                "ENV NODE_ENV=production",
                "ENV NODE_OPTIONS=--max-old-space-size=4096",
                "RUN pnpm build",
                "FROM node:20-alpine AS runner",
                "WORKDIR /app",
                "ENV NODE_ENV=production",
                "ENV PORT=3000",
                "ENV HOSTNAME=0.0.0.0",
                "ENV NEXT_TELEMETRY_DISABLED=1",
                "COPY --from=builder /app/package.json ./package.json",
                "COPY --from=builder /app/node_modules ./node_modules",
                "COPY --from=builder /app/.next ./.next",
                "COPY --from=builder /app/public ./public",
                "COPY --from=builder /app/next.config.* ./",
                "COPY --from=builder /app/env.mjs ./env.mjs",
                "EXPOSE 3000",
                'CMD ["node", "node_modules/next/dist/bin/next", "start", "-H", "0.0.0.0", "-p", "3000"]',
                "",
            ]
        ),
        encoding="utf-8",
    )
    return dockerfile


def _run_docker_command(
    argv: tuple[str, ...],
    *,
    action: str,
    timeout_seconds: int = DEFAULT_COMMAND_TIMEOUT_SECONDS,
) -> subprocess.CompletedProcess[str]:
    """Run one Docker command for the build/export phase."""

    _build_progress_log(f"docker: starting {action}")
    try:
        completed = subprocess.run(
            argv,
            shell=False,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
        )
    except FileNotFoundError as exc:
        raise PackageError("Docker CLI was not found on this host.") from exc
    except subprocess.TimeoutExpired as exc:
        output = "\n".join(
            str(part or "")
            for part in (exc.stdout, exc.stderr)
            if part
        )
        raise PackageError(f"Docker command timed out while trying to {action}: {output}") from exc

    if completed.returncode != 0:
        output = "\n".join(
            part
            for part in (completed.stdout, completed.stderr)
            if part
        )
        if output:
            tail = "\n".join(output.splitlines()[-80:])
            _build_progress_log(f"docker: {action} failed\n{tail}")
        raise PackageError(
            f"Docker command failed while trying to {action} "
            f"with exit code {completed.returncode}: {output}"
        )

    _build_progress_log(f"docker: completed {action}")
    return completed


def _docker_image_exists(image: str) -> bool:
    completed = subprocess.run(
        ("docker", "image", "inspect", image, "--format", "{{.Id}}"),
        shell=False,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return completed.returncode == 0 and bool(completed.stdout.strip())


def _docker_build_image(
    *,
    context_dir: Path,
    dockerfile: Path,
    image: str,
    action: str,
) -> None:
    _run_docker_command(
        (
            "docker",
            "build",
            "--file",
            str(dockerfile),
            "--tag",
            image,
            str(context_dir),
        ),
        action=action,
        timeout_seconds=1800,
    )


def _docker_pull_image(image: str) -> None:
    if _docker_image_exists(image):
        return

    _run_docker_command(
        ("docker", "pull", image),
        action=f"pull image {image}",
        timeout_seconds=900,
    )


def _docker_save_image(*, image: str, archive: Path) -> None:
    archive.parent.mkdir(parents=True, exist_ok=True)
    if archive.exists():
        archive.unlink()

    if not _docker_image_exists(image):
        raise PackageError(f"Docker image is not available locally: {image}")

    _run_docker_command(
        ("docker", "save", "--output", str(archive), image),
        action=f"export image {image}",
        timeout_seconds=1800,
    )

    if not archive.is_file() or archive.stat().st_size <= 0:
        raise PackageError(f"Docker did not create expected image archive: {archive}")


def _discover_backend_source(source_dir: Path) -> Path:
    candidate = source_dir / "backend"
    if candidate.is_dir():
        return candidate
    if (source_dir / "manage.py").is_file():
        return source_dir
    raise PackageError(
        "Could not find backend source directory. Expected either "
        "<source>/backend or a source directory containing manage.py."
    )


def _discover_frontend_source(source_dir: Path) -> Path:
    candidate = source_dir / "frontend"
    if candidate.is_dir():
        return candidate
    if (source_dir / "package.json").is_file():
        return source_dir
    raise PackageError(
        "Could not find frontend source directory. Expected either "
        "<source>/frontend or a source directory containing package.json."
    )


def _validate_backend_context(context: Path) -> None:
    models_py = context / "konnaxion" / "ethikos" / "models.py"
    if models_py.is_file():
        text = models_py.read_text(encoding="utf-8", errors="replace")
        if "class Migration" in text and "class ArgumentImpactVote" not in text:
            raise PackageError(
                "Backend context appears corrupted: "
                "konnaxion/ethikos/models.py contains migration content."
            )


def _write_images_metadata(
    staging_dir: Path,
    exported: list[dict[str, Any]],
) -> Path:
    """Write canonical root images.yaml and compatibility metadata/images.json."""

    payload = {
        "schema_version": "kx-images/v1",
        "generated_at": utc_now(),
        "images": exported,
    }

    root_metadata = _write_yaml_file(staging_dir / IMAGE_METADATA_FILENAME, payload)

    # Compatibility copy for older diagnostics. The root images.yaml is canonical.
    _write_json_file(
        staging_dir / "metadata" / "images.json",
        payload,
    )

    return root_metadata


def _export_runtime_image_archives(
    source_dir: Path,
    staging_dir: Path,
    *,
    capsule_id: str,
) -> list[dict[str, Any]]:
    """Build/export canonical runtime image archives into staging/images.

    This fixes the historical failure where Builder produced a deployable-looking
    capsule with only ``images/README.json``. The function intentionally builds
    from clean temporary Docker contexts so local virtualenvs, node_modules, and
    generated test output cannot corrupt image contents.
    """

    app_version, _param_version = _builder_versions()
    images_dir = staging_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    backend_source = _discover_backend_source(source_dir)
    frontend_source = _discover_frontend_source(source_dir)

    django_image = CANONICAL_IMAGE_TAGS["django-api"].format(app_version=app_version)
    frontend_image = CANONICAL_IMAGE_TAGS["frontend-next"].format(app_version=app_version)

    _build_progress_event("backend-image", 20, "Preparing backend Docker build context.")
    with tempfile.TemporaryDirectory(prefix="kxcap-image-build-") as tmp_dir:
        tmp_root = Path(tmp_dir)

        backend_context = tmp_root / "backend"
        _copy_source_tree_for_docker_context(backend_source, backend_context)
        _validate_backend_context(backend_context)

        backend_dockerfile = backend_context / "compose" / "production" / "django" / "Dockerfile"
        if not backend_dockerfile.is_file():
            raise PackageError(
                "Backend production Dockerfile is missing: "
                f"{backend_source / 'compose' / 'production' / 'django' / 'Dockerfile'}"
            )

        _build_progress_event("backend-image", 25, "Building django-api Docker image.")
        _docker_build_image(
            context_dir=backend_context,
            dockerfile=backend_dockerfile,
            image=django_image,
            action="build django-api image",
        )

        _build_progress_event("frontend-image", 45, "Preparing frontend Docker build context.")
        frontend_context = tmp_root / "frontend"
        _copy_source_tree_for_docker_context(frontend_source, frontend_context)
        frontend_dockerfile = _write_frontend_capsule_dockerfile(frontend_context)
        if not (frontend_context / "env.mjs").is_file():
            raise PackageError("Frontend env.mjs is required by next.config and is missing.")

        _build_progress_event("frontend-image", 50, "Building frontend-next Docker image.")
        _docker_build_image(
            context_dir=frontend_context,
            dockerfile=frontend_dockerfile,
            image=frontend_image,
            action="build frontend-next image",
        )

    _build_progress_event("runtime-images", 65, "Checking external runtime images.")
    for service in sorted(EXTERNAL_RUNTIME_SERVICES):
        image = CANONICAL_IMAGE_TAGS[service].format(app_version=app_version)
        _docker_pull_image(image)

    image_specs = [
        ("frontend-next", frontend_image, images_dir / "frontend-next.oci.tar"),
        ("django-api", django_image, images_dir / "django-api.oci.tar"),
        ("traefik", CANONICAL_IMAGE_TAGS["traefik"].format(app_version=app_version), images_dir / "traefik.oci.tar"),
        ("postgres", CANONICAL_IMAGE_TAGS["postgres"].format(app_version=app_version), images_dir / "postgres.oci.tar"),
        ("redis", CANONICAL_IMAGE_TAGS["redis"].format(app_version=app_version), images_dir / "redis.oci.tar"),
        ("celeryworker", django_image, images_dir / "celeryworker.oci.tar"),
        ("celerybeat", django_image, images_dir / "celerybeat.oci.tar"),
        ("media-nginx", CANONICAL_IMAGE_TAGS["media-nginx"].format(app_version=app_version), images_dir / "media-nginx.oci.tar"),
    ]

    exported: list[dict[str, Any]] = []
    total_specs = max(1, len(image_specs))
    for index, (service, image, archive) in enumerate(image_specs):
        pct = 68 + int((index / total_specs) * 14)
        _build_progress_event("export-images", pct, f"Exporting runtime image: {service}.")
        _docker_save_image(image=image, archive=archive)
        exported.append(
            {
                "service": service,
                "image": image,
                "archive": archive.name,
                "archive_path": f"images/{archive.name}",
                "sha256": sha256_file(archive),
                "size_bytes": archive.stat().st_size,
                "capsule_id": capsule_id,
                "exported_at": utc_now(),
            }
        )

    _write_images_metadata(staging_dir, exported)
    _build_progress_event("export-images", 82, "Runtime image export completed.")

    return exported


def _write_manifest_file(
    staging_dir: Path,
    *,
    source_dir: Path,
    channel: str,
    capsule_id: str,
    capsule_version: str,
    profile: str,
    sign: bool,
) -> Path:
    """Write the canonical capsule manifest as actual YAML."""

    app_version, param_version = _builder_versions()

    payload = {
        "schema_version": "kx-capsule-manifest/v1",
        "app_name": "Konnaxion",
        "app_version": app_version,
        "param_version": param_version,
        "capsule_id": capsule_id,
        "capsule_version": capsule_version,
        "channel": channel,
        "profile": profile,
        "profiles": sorted(PROFILE_SPECS),
        "created_at": utc_now(),
        "source": {
            "source_dir": str(source_dir),
        },
        "package": {
            "format": "tar+zstd",
            "extension": CAPSULE_EXTENSION,
            "signed": bool(sign),
        },
        "artifact_descriptor": ARTIFACT_DESCRIPTOR_FILENAME,
        "runtime": {
            "compose_file": COMPOSE_FILENAME,
            "images_dir": "images",
            "image_metadata": IMAGE_METADATA_FILENAME,
            "images": _default_manifest_images(app_version=app_version),
        },
    }

    return _write_yaml_file(staging_dir / MANIFEST_FILENAME, payload)


def _write_artifact_descriptor_file(
    staging_dir: Path,
    source_dir: Path,
    *,
    app_version: str,
) -> Path:
    """Write the public installable-artifact descriptor into the capsule.

    A source tree may provide ``capsule-artifact.yaml``.  Otherwise the current
    Konnaxion product descriptor is generated.  Product-specific integrated UI
    manifests remain separate files and are copied into ``contributions/``;
    Capsule never interprets their routes/navigation.
    """

    from kx_shared.artifact_descriptor import (
        ArtifactDescriptor,
        default_konnaxion_descriptor,
        load_artifact_descriptor,
        write_artifact_descriptor,
    )

    source_descriptor = source_dir / SOURCE_ARTIFACT_DESCRIPTOR_FILENAME
    descriptor = (
        load_artifact_descriptor(source_descriptor)
        if source_descriptor.is_file()
        else default_konnaxion_descriptor(version=app_version)
    )

    if descriptor.integrated_ui_enabled and descriptor.integrated_manifest:
        source_manifest = (source_dir / descriptor.integrated_manifest).resolve(strict=False)
        try:
            source_manifest.relative_to(source_dir.resolve(strict=False))
        except ValueError as exc:
            raise PackageError(
                "artifact integrated manifest must stay inside the source tree"
            ) from exc
        if not source_manifest.is_file():
            raise PackageError(
                f"artifact integrated manifest does not exist: {source_manifest}"
            )
        target_relative = f"contributions/{source_manifest.name}"
        target_manifest = staging_dir / target_relative
        target_manifest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_manifest, target_manifest)
        payload = descriptor.to_dict()
        payload["ui"]["integrated"]["manifest"] = target_relative
        descriptor = ArtifactDescriptor.from_mapping(payload)

    return write_artifact_descriptor(
        descriptor, staging_dir / ARTIFACT_DESCRIPTOR_FILENAME
    )


def _write_default_capsule_dirs(staging_dir: Path) -> None:
    for dirname in sorted(REQUIRED_ROOT_DIRS | OPTIONAL_ROOT_DIRS):
        (staging_dir / dirname).mkdir(parents=True, exist_ok=True)

    readme_payload = {
        "generated_at": utc_now(),
        "note": "Directory intentionally present for canonical capsule layout.",
    }

    for dirname in sorted(REQUIRED_ROOT_DIRS):
        marker = staging_dir / dirname / "README.json"
        if dirname != "metadata" and not marker.exists():
            _write_json_file(marker, readme_payload)


def _write_profile_files(staging_dir: Path, *, default_profile: str) -> None:
    """Write all canonical network profile files expected by the verifier."""

    for profile_name, spec in sorted(PROFILE_SPECS.items()):
        payload = {
            "schema_version": "kx-network-profile/v1",
            "profile": profile_name,
            "default_for_capsule": profile_name == default_profile,
            "generated_at": utc_now(),
            **spec,
        }

        _write_yaml_file(staging_dir / "profiles" / f"{profile_name}.yaml", payload)


def _write_env_templates(staging_dir: Path) -> None:
    """Write all canonical env templates expected by the verifier."""

    _write_text_file(
        staging_dir / "env-templates" / "django.env.template",
        "\n".join(
            [
                "# Konnaxion Django runtime environment template.",
                "# Real secrets are generated or provided on the target host.",
                "DJANGO_SETTINGS_MODULE=${DJANGO_SETTINGS_MODULE:-konnaxion.settings}",
                "DJANGO_SECRET_KEY=<GENERATED_ON_INSTALL>",
                "DJANGO_ALLOWED_HOSTS=<GENERATED_FROM_PROFILE>",
                "DJANGO_CSRF_TRUSTED_ORIGINS=<GENERATED_FROM_PROFILE>",
                "CSRF_TRUSTED_ORIGINS=<GENERATED_FROM_PROFILE>",
                "DATABASE_URL=${DATABASE_URL}",
                "REDIS_URL=${REDIS_URL}",
                "KX_INSTANCE_ID=${KX_INSTANCE_ID}",
                "KX_NETWORK_PROFILE=${KX_NETWORK_PROFILE}",
                "KX_EXPOSURE_MODE=${KX_EXPOSURE_MODE}",
                "",
            ]
        ),
    )

    _write_text_file(
        staging_dir / "env-templates" / "postgres.env.template",
        "\n".join(
            [
                "# Konnaxion Postgres runtime environment template.",
                "POSTGRES_DB=${POSTGRES_DB:-konnaxion}",
                "POSTGRES_USER=${POSTGRES_USER:-konnaxion}",
                "POSTGRES_PASSWORD=<GENERATED_ON_INSTALL>",
                "",
            ]
        ),
    )

    _write_text_file(
        staging_dir / "env-templates" / "redis.env.template",
        "\n".join(
            [
                "# Konnaxion Redis runtime environment template.",
                "REDIS_URL=${REDIS_URL}",
                "REDIS_APPENDONLY=${REDIS_APPENDONLY:-yes}",
                "",
            ]
        ),
    )

    _write_text_file(
        staging_dir / "env-templates" / "frontend.env.template",
        "\n".join(
            [
                "# Konnaxion frontend runtime environment template.",
                "NEXT_PUBLIC_API_BASE=<GENERATED_FROM_PROFILE>",
                "NEXT_PUBLIC_BACKEND_BASE=<GENERATED_FROM_PROFILE>",
                "NEXT_TELEMETRY_DISABLED=1",
                "NODE_OPTIONS=--max-old-space-size=4096",
                "KX_PUBLIC_HOST=<GENERATED_FROM_PROFILE>",
                "KX_NETWORK_PROFILE=${KX_NETWORK_PROFILE}",
                "",
            ]
        ),
    )


def _write_healthcheck(staging_dir: Path) -> None:
    healthcheck = staging_dir / "healthchecks" / "capsule-healthcheck.json"
    _write_json_file(
        healthcheck,
        {
            "schema_version": "kx-healthcheck/v1",
            "checks": [
                {
                    "name": "compose-file-present",
                    "type": "file_exists",
                    "path": COMPOSE_FILENAME,
                },
                {
                    "name": "image-metadata-present",
                    "type": "file_exists",
                    "path": IMAGE_METADATA_FILENAME,
                },
            ],
        },
    )


def _write_policy(staging_dir: Path, *, profile: str) -> None:
    _write_json_file(
        staging_dir / "policies" / "capsule-policy.json",
        {
            "schema_version": "kx-policy/v1",
            "profile": profile,
            "secrets_in_capsule_allowed": False,
            "docker_socket_mount_allowed": False,
            "privileged_containers_allowed": False,
            "host_network_allowed": False,
            "generated_at": utc_now(),
        },
    )


def _write_migration_marker(staging_dir: Path) -> None:
    _write_json_file(
        staging_dir / "migrations" / "README.json",
        {
            "schema_version": "kx-migrations/v1",
            "migrations": [],
            "generated_at": utc_now(),
        },
    )


def _write_checksums_file(staging_dir: Path) -> Path:
    entries: list[str] = []

    for path in iter_staging_files(staging_dir):
        relative = _safe_relative_path(path, staging_dir)

        if relative in {CHECKSUMS_FILENAME, SIGNATURE_FILENAME}:
            continue

        entries.append(f"{sha256_file(path)}  {relative}")

    checksums = staging_dir / CHECKSUMS_FILENAME
    checksums.write_text("\n".join(sorted(entries)) + "\n", encoding="utf-8")
    return checksums


def _first_existing_env_path(names: tuple[str, ...]) -> Path | None:
    """Return the first non-empty signing path from the environment."""

    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return Path(value).expanduser()
    return None


def _first_env_bytes(names: tuple[str, ...]) -> bytes | None:
    """Return the first non-empty signing password from the environment."""

    for name in names:
        value = os.getenv(name, "")
        if value:
            return value.encode("utf-8")
    return None


def _coerce_password_bytes(value: str | bytes | None) -> bytes | None:
    if value in (None, "", b""):
        return None
    if isinstance(value, bytes):
        return value
    return str(value).encode("utf-8")


def _write_unsigned_signature_file(
    staging_dir: Path,
    *,
    capsule_id: str,
    capsule_version: str,
) -> Path:
    """Write an explicit unsigned development envelope."""

    manifest = staging_dir / MANIFEST_FILENAME
    checksums = staging_dir / CHECKSUMS_FILENAME

    payload = {
        "schema_version": "kx-signature/v1",
        "capsule_id": capsule_id,
        "capsule_version": capsule_version,
        "mode": "unsigned-development",
        "manifest_sha256": sha256_file(manifest),
        "checksums_sha256": sha256_file(checksums),
        "created_at": utc_now(),
        "warning": "This capsule is intentionally unsigned and must not be deployed with KX_REQUIRE_SIGNED_CAPSULE=true.",
    }

    return _write_json_file(staging_dir / SIGNATURE_FILENAME, payload)


def _write_signature_file(
    staging_dir: Path,
    *,
    capsule_id: str,
    capsule_version: str,
    sign: bool,
    signing_key_file: Path | str | None = None,
    signing_key_password: str | bytes | None = None,
) -> Path:
    """Write signature.sig using the real Builder signing flow."""

    if not sign:
        return _write_unsigned_signature_file(
            staging_dir,
            capsule_id=capsule_id,
            capsule_version=capsule_version,
        )

    resolved_key = (
        Path(signing_key_file).expanduser()
        if signing_key_file not in (None, "")
        else _first_existing_env_path(SIGNING_KEY_ENV_VARS)
    )

    if resolved_key is None:
        raise PackageError(
            "Signing requested, but no signing key was provided. "
            "Set KX_BUILDER_SIGNING_KEY_FILE or pass signing_key_file. "
            "Use sign=False only for local unsigned development artifacts."
        )

    password = _coerce_password_bytes(signing_key_password)
    if password is None:
        password = _first_env_bytes(SIGNING_KEY_PASSWORD_ENV_VARS)

    try:
        from kx_builder.signature import sign_capsule_root_with_private_key_file
    except Exception as exc:
        raise PackageError(
            "kx_builder.signature.sign_capsule_root_with_private_key_file is required "
            "to create a signed capsule."
        ) from exc

    sign_capsule_root_with_private_key_file(
        staging_dir,
        resolved_key,
        private_key_password=password,
        capsule_id=capsule_id,
        capsule_version=capsule_version,
        metadata={
            "builder": "kx_builder.package",
            "package_schema_version": PACKAGE_SCHEMA_VERSION,
        },
        overwrite=True,
    )

    signature_path = staging_dir / SIGNATURE_FILENAME
    if not signature_path.is_file():
        raise PackageError("Signature helper completed but signature.sig was not written.")

    try:
        payload = json.loads(signature_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PackageError("signature.sig is not valid JSON after signing.") from exc

    if not isinstance(payload, Mapping) or not payload.get("signature_base64"):
        raise PackageError("signature.sig does not contain a cryptographic signature.")

    return signature_path


def _stage_capsule_from_source(
    source_dir: Path,
    staging_dir: Path,
    *,
    channel: str,
    capsule_id: str,
    capsule_version: str,
    profile: str,
    sign: bool,
    build_images: bool = True,
    signing_key_file: Path | str | None = None,
    signing_key_password: str | bytes | None = None,
) -> Path:
    """Create a canonical capsule staging directory from a normal source tree."""

    _build_progress_event("staging", 10, "Creating capsule staging metadata and profiles.")
    _write_default_capsule_dirs(staging_dir)
    _write_compose_file(staging_dir, source_dir, capsule_id=capsule_id)
    _write_manifest_file(
        staging_dir,
        source_dir=source_dir,
        channel=channel,
        capsule_id=capsule_id,
        capsule_version=capsule_version,
        profile=profile,
        sign=sign,
    )
    app_version, _ = _builder_versions()
    _write_artifact_descriptor_file(
        staging_dir,
        source_dir,
        app_version=app_version,
    )
    _write_profile_files(staging_dir, default_profile=profile)
    _write_env_templates(staging_dir)
    _write_healthcheck(staging_dir)
    _write_policy(staging_dir, profile=profile)
    _write_migration_marker(staging_dir)

    exported_images: list[dict[str, Any]] = []
    if build_images:
        exported_images = _export_runtime_image_archives(
            source_dir,
            staging_dir,
            capsule_id=capsule_id,
        )
    else:
        _write_images_metadata(staging_dir, exported_images)

    _build_progress_event("metadata", 84, "Writing source inventory and build metadata.")
    _write_json_file(
        staging_dir / "metadata" / "source-inventory.json",
        _build_source_inventory(source_dir),
    )

    _write_json_file(
        staging_dir / "metadata" / "build.json",
        {
            "schema_version": "kx-build/v1",
            "channel": channel,
            "capsule_id": capsule_id,
            "capsule_version": capsule_version,
            "profile": profile,
            "generated_at": utc_now(),
            "images_exported": bool(exported_images),
            "image_count": len(exported_images),
            "required_image_count": len(REQUIRED_IMAGE_ARCHIVES),
        },
    )

    _build_progress_event("checksums", 88, "Calculating capsule checksums.")
    _write_checksums_file(staging_dir)
    _build_progress_event("sign", 91, "Signing capsule metadata.")
    _write_signature_file(
        staging_dir,
        capsule_id=capsule_id,
        capsule_version=capsule_version,
        sign=sign,
        signing_key_file=signing_key_file,
        signing_key_password=signing_key_password,
    )

    return staging_dir


def _looks_like_capsule_staging(path: Path) -> bool:
    entries = root_entries(path)
    return REQUIRED_ROOT_FILES.issubset(entries) and REQUIRED_ROOT_DIRS.issubset(entries)


def _verify_packaged_capsule(capsule_file: Path) -> dict[str, Any]:
    """Run the richer builder verifier when available."""

    try:
        from kx_builder.verify import verify_capsule_file
    except Exception:
        validation = validate_capsule_archive(capsule_file)
        return {
            "ok": validation.ok,
            "issues": [
                {
                    "severity": issue.severity.value,
                    "path": issue.path,
                    "message": issue.message,
                }
                for issue in validation.issues
            ],
        }

    result = verify_capsule_file(capsule_file, strict=False)

    if isinstance(result, Mapping):
        return dict(result)

    to_dict = getattr(result, "to_dict", None)
    if callable(to_dict):
        value = to_dict()
        if isinstance(value, Mapping):
            return dict(value)

    return {
        "ok": bool(getattr(result, "ok", getattr(result, "valid", False))),
        "message": str(getattr(result, "message", "")),
    }


def build_package(
    *,
    source_dir: Path | str,
    output: Path | str,
    channel: str = DEFAULT_CHANNEL,
    capsule_id: str,
    capsule_version: str,
    profile: str,
    sign: bool = True,
    verify: bool = True,
    force: bool = False,
    build_images: bool = True,
    signing_key_file: Path | str | None = None,
    signing_key_password: str | bytes | None = None,
) -> dict[str, Any]:
    """Build a .kxcap package from a source tree or prepared capsule staging dir.

    This is the function expected by kx_builder.main.build_capsule().
    """

    source_root = Path(source_dir).resolve(strict=False)
    output_path = ensure_capsule_extension(output)
    app_version, param_version = _builder_versions()
    _build_progress_event("preflight", 6, "Builder validated request and source paths.")

    if not source_root.exists() or not source_root.is_dir():
        return {
            "ok": False,
            "output": str(output_path),
            "capsule_id": capsule_id,
            "capsule_version": capsule_version,
            "app_version": app_version,
            "param_version": param_version,
            "message": f"Source directory does not exist: {source_root}",
        }

    package_options = PackageOptions(
        overwrite=bool(force),
        include_package_metadata=False,
        strict_root=True,
        scan_for_secrets=True,
    )

    if _looks_like_capsule_staging(source_root):
        image_validation = validate_required_image_archives(source_root)
        blocking_image_validation = [
            issue
            for issue in image_validation
            if issue.severity == PackageIssueSeverity.BLOCKING
        ]
        if blocking_image_validation:
            raise PackageValidationError(
                PackageValidationResult(ok=False, issues=tuple(blocking_image_validation))
            )

        _write_checksums_file(source_root)
        _write_signature_file(
            source_root,
            capsule_id=capsule_id,
            capsule_version=capsule_version,
            sign=sign,
            signing_key_file=signing_key_file,
            signing_key_password=signing_key_password,
        )
        package_result = package_capsule(
            source_root,
            output_path,
            options=package_options,
        )
        staging_used = source_root
    else:
        with tempfile.TemporaryDirectory(prefix="kxcap-build-") as tmp_dir:
            staging_used = Path(tmp_dir) / "staging"
            staging_used.mkdir(parents=True, exist_ok=True)

            _stage_capsule_from_source(
                source_root,
                staging_used,
                channel=channel,
                capsule_id=capsule_id,
                capsule_version=capsule_version,
                profile=profile,
                sign=sign,
                build_images=build_images,
                signing_key_file=signing_key_file,
                signing_key_password=signing_key_password,
            )

            package_result = package_capsule(
                staging_used,
                output_path,
                options=package_options,
            )

    _build_progress_event("package", 96, "Capsule archive created; preparing verification.")
    verification: dict[str, Any] | None = None
    ok = True
    message = "Capsule build completed."

    if verify:
        _build_progress_event("verify", 98, "Verifying packaged capsule.")
        verification = _verify_packaged_capsule(package_result.capsule_file)
        ok = bool(verification.get("ok", False))

        if not ok:
            message = "Capsule build completed, but verification failed."

    _build_progress_event("complete" if ok else "verify", 100 if ok else 99, message)
    return {
        "ok": ok,
        "output": str(package_result.capsule_file),
        "capsule_file": str(package_result.capsule_file),
        "capsule_id": capsule_id,
        "capsule_version": capsule_version,
        "app_version": app_version,
        "param_version": param_version,
        "message": message,
        "data": {
            "capsule_file": str(package_result.capsule_file),
            "staging_dir": str(staging_used),
            "size_bytes": package_result.size_bytes,
            "sha256": package_result.sha256,
            "compression": package_result.compression.value,
            "created_at": package_result.created_at.isoformat(),
            "verification": verification,
        },
    }


__all__ = [
    "SOURCE_ARTIFACT_DESCRIPTOR_FILENAME",
    "OPTIONAL_ROOT_FILES",
    "ARTIFACT_DESCRIPTOR_FILENAME",
    "ALLOWED_ROOT_ENTRIES",
    "CHECKSUMS_FILENAME",
    "COMPOSE_FILENAME",
    "DEFAULT_COMMAND_TIMEOUT_SECONDS",
    "DEFAULT_COMPRESSION_LEVEL",
    "DJANGO_IMAGE_ALIAS_SERVICES",
    "EXTERNAL_RUNTIME_SERVICES",
    "FORBIDDEN_CAPSULE_FILENAMES",
    "FORBIDDEN_FILENAME_PATTERNS",
    "FORBIDDEN_TEXT_PATTERNS",
    "IMAGE_METADATA_FILENAME",
    "MANIFEST_FILENAME",
    "OPTIONAL_IMAGE_ARCHIVES",
    "OPTIONAL_ROOT_DIRS",
    "PACKAGE_SCHEMA_VERSION",
    "PROFILE_SPECS",
    "REQUIRED_ENV_TEMPLATES",
    "REQUIRED_IMAGE_ARCHIVES",
    "REQUIRED_PROFILE_FILES",
    "REQUIRED_ROOT_DIRS",
    "REQUIRED_ROOT_FILES",
    "SIGNATURE_FILENAME",
    "SIGNING_KEY_ENV_VARS",
    "SIGNING_KEY_PASSWORD_ENV_VARS",
    "TEXT_SCAN_EXTENSIONS",
    "CapsuleArchiveEntry",
    "CapsuleArchiveInfo",
    "CompressionUnavailableError",
    "PackageCompression",
    "PackageError",
    "PackageIssue",
    "PackageIssueSeverity",
    "PackageOptions",
    "PackageResult",
    "PackageValidationError",
    "PackageValidationResult",
    "build_package",
    "capsule_filename",
    "compress_tar_to_kxcap",
    "create_tar_archive",
    "decompress_kxcap_to_tar",
    "default_output_path",
    "ensure_capsule_extension",
    "extract_capsule",
    "inspect_capsule",
    "iter_staging_files",
    "package_capsule",
    "package_result_to_dict",
    "package_to_default_location",
    "raise_if_invalid",
    "read_tar_entries",
    "root_entries",
    "scan_file_for_secret_patterns",
    "scan_staging_for_secrets",
    "sha256_file",
    "utc_now",
    "validate_capsule_archive",
    "validate_required_image_archives",
    "validate_required_layout",
    "validate_staging_dir",
    "validation_result_to_dict",
    "write_package_metadata",
]