"""
Builder-side verification for Konnaxion Capsules.

This module verifies a built ``.kxcap`` before it is handed to the
Konnaxion Capsule Manager or imported by the Konnaxion Agent.

Scope:
- validate canonical capsule extension
- inspect required capsule root layout
- parse and validate manifest basics
- parse and validate images.yaml
- verify all required runtime OCI image archives are present
- verify listed image archives are non-empty
- verify image archive digests/sizes against images.yaml
- verify ``checksums.txt`` entries
- confirm required image archives are covered by checksums
- confirm ``signature.sig`` is present
- optionally call a caller-provided signature verifier
- reject obvious real secrets in env templates
- return a structured report suitable for CLI output and CI

This verifier does not start services, load OCI images, or mutate host state.
"""

from __future__ import annotations

import io
import json
import re
import tarfile
import tempfile
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import Any

try:  # pragma: no cover - optional dependency
    import yaml
except Exception:  # pragma: no cover - optional dependency
    yaml = None  # type: ignore[assignment]

from kx_shared.konnaxion_constants import (
    APP_VERSION,
    CAPSULE_EXTENSION,
    DEFAULT_CAPSULE_ID,
    DEFAULT_CAPSULE_VERSION,
    NetworkProfile,
    PARAM_VERSION,
)

try:  # pragma: no cover - zstandard may not be installed in all dev envs
    import zstandard as zstd
except Exception:  # pragma: no cover
    zstd = None  # type: ignore[assignment]


SignatureVerifier = Callable[[bytes, bytes, bytes], bool]


class VerifyStatus(StrEnum):
    """Verification status values for builder checks."""

    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"
    SKIPPED = "SKIPPED"


@dataclass(frozen=True)
class VerifyIssue:
    """Single verification issue."""

    code: str
    message: str
    status: VerifyStatus = VerifyStatus.FAIL
    path: str | None = None


@dataclass(frozen=True)
class CapsuleVerifyReport:
    """Structured verification report."""

    ok: bool
    capsule_path: Path
    capsule_id: str | None = None
    capsule_version: str | None = None
    app_version: str | None = None
    param_version: str | None = None
    checks: tuple[VerifyIssue, ...] = field(default_factory=tuple)
    warnings: tuple[VerifyIssue, ...] = field(default_factory=tuple)
    manifest: Mapping[str, Any] | None = None

    @property
    def errors(self) -> tuple[VerifyIssue, ...]:
        """Return blocking verification errors."""

        return tuple(issue for issue in self.checks if issue.status == VerifyStatus.FAIL)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable report."""

        return {
            "ok": self.ok,
            "valid": self.ok,
            "capsule_path": str(self.capsule_path),
            "capsule_file": str(self.capsule_path),
            "capsule_id": self.capsule_id,
            "capsule_version": self.capsule_version,
            "app_version": self.app_version,
            "param_version": self.param_version,
            "errors": [issue_to_dict(issue) for issue in self.errors],
            "warnings": [issue_to_dict(issue) for issue in self.warnings],
            "checks": [issue_to_dict(issue) for issue in self.checks],
            "manifest": dict(self.manifest or {}),
            "message": "Capsule verified." if self.ok else "Capsule verification failed.",
        }

    def to_json(self, *, indent: int = 2) -> str:
        """Return the report as JSON text."""

        return json.dumps(self.to_dict(), indent=indent, sort_keys=True)


class CapsuleVerifyError(ValueError):
    """Raised when caller requests exception-based verification."""

    def __init__(self, report: CapsuleVerifyReport) -> None:
        self.report = report
        detail = "; ".join(issue.message for issue in report.errors)
        super().__init__(detail or "Capsule verification failed")


@dataclass(frozen=True)
class CapsuleArchive:
    """In-memory representation of a capsule archive."""

    path: Path
    members: Mapping[str, bytes]

    def has(self, path: str) -> bool:
        """Return whether an archive member exists."""

        return path in self.members

    def read_text(self, path: str) -> str:
        """Read a member as UTF-8 text."""

        return self.members[path].decode("utf-8")

    def read_bytes(self, path: str) -> bytes:
        """Read a member as bytes."""

        return self.members[path]

    def list_paths(self) -> tuple[str, ...]:
        """Return all normalized member paths."""

        return tuple(sorted(self.members))


REQUIRED_ROOT_FILES = frozenset(
    {
        "manifest.yaml",
        "docker-compose.capsule.yml",
        "images.yaml",
        "checksums.txt",
        "signature.sig",
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

OPTIONAL_ROOT_FILES = frozenset({"artifact.yaml"})
OPTIONAL_ROOT_DIRS = frozenset({"seed-data", "contributions"})

REQUIRED_ENV_TEMPLATES = frozenset(
    {
        "env-templates/django.env.template",
        "env-templates/postgres.env.template",
        "env-templates/redis.env.template",
        "env-templates/frontend.env.template",
    }
)

REQUIRED_PROFILES = frozenset(
    {
        "profiles/local_only.yaml",
        "profiles/intranet_private.yaml",
        "profiles/private_tunnel.yaml",
        "profiles/public_temporary.yaml",
        "profiles/public_vps.yaml",
        "profiles/offline.yaml",
    }
)

REQUIRED_IMAGE_SERVICES = (
    "frontend-next",
    "django-api",
    "traefik",
    "postgres",
    "redis",
    "celeryworker",
    "celerybeat",
    "media-nginx",
)

OPTIONAL_IMAGE_SERVICES = (
    "flower",
)

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

IMAGE_METADATA_FILENAME = "images.yaml"
LEGACY_IMAGE_METADATA_FILENAME = "metadata/images.json"

SECRET_KEY_PATTERNS = (
    re.compile(r"^DJANGO_SECRET_KEY\s*=\s*(?!<GENERATED_ON_INSTALL>|\$\{)", re.I),
    re.compile(r"^POSTGRES_PASSWORD\s*=\s*(?!<GENERATED_ON_INSTALL>|\$\{)", re.I),
    re.compile(r"^DATABASE_URL\s*=\s*postgres://", re.I),
    re.compile(r"PRIVATE_KEY", re.I),
    re.compile(r"API_TOKEN\s*=\s*[^<\s]", re.I),
    re.compile(r"GIT_TOKEN\s*=\s*[^<\s]", re.I),
    re.compile(r"PROVIDER_TOKEN\s*=\s*[^<\s]", re.I),
)

CHECKSUM_DIGEST_FIRST_RE = re.compile(
    r"^(?P<digest>[a-fA-F0-9]{64})\s+\*?(?P<path>.+)$"
)
CHECKSUM_ALGO_FIRST_RE = re.compile(
    r"^sha256\s+(?P<path>.+?)\s+(?P<digest>[a-fA-F0-9]{64})$",
    re.I,
)


def issue_to_dict(issue: VerifyIssue) -> dict[str, Any]:
    """Serialize a verification issue."""

    return {
        "code": issue.code,
        "message": issue.message,
        "status": issue.status.value,
        "path": issue.path,
    }


def verify_capsule_file(
    capsule_file: str | Path,
    *,
    strict: bool = False,
    public_key: bytes | None = None,
    signature_verifier: SignatureVerifier | None = None,
    require_signature_verifier: bool = False,
    raise_on_error: bool = False,
) -> dict[str, Any]:
    """Compatibility entrypoint expected by ``kx_builder.main``.

    Returns a dictionary instead of a dataclass so CLI, UI, and service wrappers
    can serialize it directly.
    """

    report = verify_capsule(
        capsule_file,
        public_key=public_key,
        signature_verifier=signature_verifier,
        require_signature_verifier=require_signature_verifier or strict,
        raise_on_error=raise_on_error,
    )

    data = report.to_dict()

    if strict and report.warnings:
        data["ok"] = False
        data["valid"] = False
        data["strict"] = True
        data["message"] = "Capsule verification failed in strict mode."
        data["strict_warnings"] = [issue_to_dict(issue) for issue in report.warnings]
    else:
        data["strict"] = strict

    return data


def verify_capsule(
    capsule_path: str | Path,
    *,
    public_key: bytes | None = None,
    signature_verifier: SignatureVerifier | None = None,
    require_signature_verifier: bool = False,
    raise_on_error: bool = False,
) -> CapsuleVerifyReport:
    """Verify a built Konnaxion Capsule.

    ``signature_verifier`` receives ``manifest_bytes``, ``checksums_bytes``,
    and ``signature_bytes`` and must return ``True`` when the signature is
    valid. Signature cryptography is deliberately injected so the project can
    choose minisign, age/signify, GPG, Sigstore, or another approved backend
    without changing the report contract.
    """

    path = Path(capsule_path)
    issues: list[VerifyIssue] = []
    warnings: list[VerifyIssue] = []
    manifest: Mapping[str, Any] | None = None

    def fail(code: str, message: str, member_path: str | None = None) -> None:
        issues.append(
            VerifyIssue(
                code=code,
                message=message,
                status=VerifyStatus.FAIL,
                path=member_path,
            )
        )

    def warn(code: str, message: str, member_path: str | None = None) -> None:
        issue = VerifyIssue(
            code=code,
            message=message,
            status=VerifyStatus.WARN,
            path=member_path,
        )
        warnings.append(issue)
        issues.append(issue)

    if not path.name.endswith(CAPSULE_EXTENSION):
        fail(
            "invalid_extension",
            f"Capsule must use canonical {CAPSULE_EXTENSION} extension",
            str(path),
        )

    if not path.exists():
        fail("missing_capsule", f"Capsule does not exist: {path}", str(path))
        report = _build_report(path, issues, warnings, manifest)
        if raise_on_error:
            raise CapsuleVerifyError(report)
        return report

    try:
        archive = read_capsule_archive(path)
    except Exception as exc:
        fail("unreadable_capsule", f"Could not read capsule archive: {exc}", str(path))
        report = _build_report(path, issues, warnings, manifest)
        if raise_on_error:
            raise CapsuleVerifyError(report)
        return report

    _verify_layout(archive, fail=fail, warn=warn)

    if archive.has("manifest.yaml"):
        try:
            manifest = parse_manifest(archive.read_bytes("manifest.yaml"))
            _verify_manifest(manifest, fail=fail, warn=warn)
        except Exception as exc:
            fail("invalid_manifest", f"Could not parse manifest.yaml: {exc}", "manifest.yaml")

    if archive.has("artifact.yaml"):
        try:
            from kx_shared.artifact_descriptor import ArtifactDescriptor
            artifact_payload = _parse_yaml_or_json(
                archive.read_bytes("artifact.yaml"), path="artifact.yaml"
            )
            ArtifactDescriptor.from_mapping(artifact_payload)
        except Exception as exc:
            fail(
                "invalid_artifact_descriptor",
                f"Could not validate artifact.yaml: {exc}",
                "artifact.yaml",
            )
    else:
        warn(
            "legacy_missing_artifact_descriptor",
            "Capsule has no artifact.yaml; legacy import compatibility will synthesize a product descriptor.",
            "artifact.yaml",
        )

    _verify_image_archives(
        archive,
        manifest=manifest,
        fail=fail,
        warn=warn,
    )

    if archive.has("checksums.txt"):
        try:
            _verify_checksums(archive, fail=fail, warn=warn)
        except Exception as exc:
            fail("invalid_checksums", f"Could not verify checksums.txt: {exc}", "checksums.txt")

    _verify_signature(
        archive,
        public_key=public_key,
        signature_verifier=signature_verifier,
        require_signature_verifier=require_signature_verifier,
        fail=fail,
        warn=warn,
    )

    _verify_env_templates(archive, fail=fail, warn=warn)

    report = _build_report(path, issues, warnings, manifest)
    if raise_on_error and not report.ok:
        raise CapsuleVerifyError(report)
    return report


def assert_capsule_valid(
    capsule_path: str | Path,
    *,
    public_key: bytes | None = None,
    signature_verifier: SignatureVerifier | None = None,
    require_signature_verifier: bool = False,
) -> CapsuleVerifyReport:
    """Verify a capsule and raise ``CapsuleVerifyError`` if it is invalid."""

    return verify_capsule(
        capsule_path,
        public_key=public_key,
        signature_verifier=signature_verifier,
        require_signature_verifier=require_signature_verifier,
        raise_on_error=True,
    )


def read_capsule_archive(path: str | Path) -> CapsuleArchive:
    """Read a .kxcap file into memory.

    The canonical package format is a zstd-compressed tar archive, but dev
    workflows may hand a plain tar archive to the verifier. Try direct tar
    first, then zstd decompression.
    """

    capsule_path = Path(path)
    raw = capsule_path.read_bytes()

    try:
        return _archive_from_tar_bytes(capsule_path, raw)
    except tarfile.TarError:
        decompressed = _try_decompress_zstd(raw)
        return _archive_from_tar_bytes(capsule_path, decompressed)


def _try_decompress_zstd(raw: bytes) -> bytes:
    if zstd is None:
        raise CapsuleVerifyError(
            CapsuleVerifyReport(
                ok=False,
                capsule_path=Path("<memory>"),
                checks=(
                    VerifyIssue(
                        code="zstd_unavailable",
                        message="zstandard is required to read compressed .kxcap files",
                    ),
                ),
            )
        )

    decompressor = zstd.ZstdDecompressor()
    with decompressor.stream_reader(io.BytesIO(raw)) as reader:
        return reader.read()


def _archive_from_tar_bytes(path: Path, raw: bytes) -> CapsuleArchive:
    members: dict[str, bytes] = {}

    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:*") as tar:
        for member in tar.getmembers():
            if member.isdir():
                continue

            normalized = normalize_archive_path(member.name)
            if normalized is None:
                continue

            extracted = tar.extractfile(member)
            if extracted is None:
                continue

            members[normalized] = extracted.read()

    return CapsuleArchive(path=path, members=members)


def normalize_archive_path(path: str) -> str | None:
    """Normalize and validate a capsule member path."""

    raw = str(path).strip().replace("\\", "/")
    if not raw:
        return None

    while raw.startswith("./"):
        raw = raw[2:]

    posix = PurePosixPath(raw)
    if posix.is_absolute():
        return None

    parts = posix.parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        return None

    return str(posix)


def parse_manifest(raw: bytes) -> Mapping[str, Any]:
    """Parse manifest.yaml."""

    if yaml is None:
        raise RuntimeError("PyYAML is required to parse manifest.yaml.")

    parsed = yaml.safe_load(raw.decode("utf-8")) or {}
    if not isinstance(parsed, Mapping):
        raise ValueError("manifest.yaml must contain a mapping")

    return parsed


def _parse_yaml_or_json(raw: bytes, *, path: str) -> Mapping[str, Any]:
    """Parse YAML/JSON metadata file into a mapping."""

    text = raw.decode("utf-8")

    if path.endswith(".json"):
        parsed = json.loads(text)
    else:
        if yaml is None:
            raise RuntimeError(f"PyYAML is required to parse {path}.")
        parsed = yaml.safe_load(text) or {}

    if not isinstance(parsed, Mapping):
        raise ValueError(f"{path} must contain a mapping")

    return parsed


def _verify_layout(
    archive: CapsuleArchive,
    *,
    fail: Callable[[str, str, str | None], None],
    warn: Callable[[str, str, str | None], None],
) -> None:
    """Verify required capsule layout."""

    paths = set(archive.list_paths())

    for required_file in sorted(REQUIRED_ROOT_FILES):
        if required_file not in paths:
            fail(
                "missing_required_file",
                f"Capsule is missing required root file {required_file}",
                required_file,
            )

    for required_template in sorted(REQUIRED_ENV_TEMPLATES):
        if required_template not in paths:
            fail(
                "missing_env_template",
                f"Capsule is missing required env template {required_template}",
                required_template,
            )

    for required_profile in sorted(REQUIRED_PROFILES):
        if required_profile not in paths:
            fail(
                "missing_network_profile",
                f"Capsule is missing required network profile {required_profile}",
                required_profile,
            )

    root_entries = {path.split("/", 1)[0] for path in paths}
    for required_dir in sorted(REQUIRED_ROOT_DIRS):
        if required_dir not in root_entries:
            fail(
                "missing_required_directory",
                f"Capsule is missing required directory {required_dir}/",
                required_dir,
            )

    allowed_roots = REQUIRED_ROOT_FILES | OPTIONAL_ROOT_FILES | REQUIRED_ROOT_DIRS | OPTIONAL_ROOT_DIRS
    for root in sorted(root_entries):
        if root not in allowed_roots:
            warn(
                "unknown_root_entry",
                f"Capsule contains non-canonical root entry {root}",
                root,
            )


def _verify_manifest(
    manifest: Mapping[str, Any],
    *,
    fail: Callable[[str, str, str | None], None],
    warn: Callable[[str, str, str | None], None],
) -> None:
    """Verify required manifest fields and canonical defaults."""

    required = {
        "schema_version",
        "capsule_id",
        "capsule_version",
        "app_name",
        "app_version",
        "channel",
        "created_at",
    }

    for key in sorted(required):
        if not manifest.get(key):
            fail("missing_manifest_field", f"manifest.yaml missing {key}", "manifest.yaml")

    app_name = str(manifest.get("app_name", ""))
    if app_name and app_name != "Konnaxion":
        fail(
            "invalid_app_name",
            "manifest.yaml app_name must be Konnaxion",
            "manifest.yaml",
        )

    app_version = str(manifest.get("app_version", ""))
    if app_version and app_version != APP_VERSION:
        fail(
            "invalid_app_version",
            f"manifest.yaml app_version must be {APP_VERSION}",
            "manifest.yaml",
        )

    capsule_id = str(manifest.get("capsule_id", ""))
    if capsule_id and not capsule_id.startswith("konnaxion-v14-"):
        fail(
            "invalid_capsule_id",
            "manifest.yaml capsule_id must start with konnaxion-v14-",
            "manifest.yaml",
        )

    capsule_version = str(manifest.get("capsule_version", ""))
    if capsule_version and capsule_version == DEFAULT_CAPSULE_VERSION:
        warn(
            "default_capsule_version",
            "manifest.yaml uses the default demo capsule version",
            "manifest.yaml",
        )

    if capsule_id == DEFAULT_CAPSULE_ID:
        warn(
            "default_capsule_id",
            "manifest.yaml uses the default demo capsule id",
            "manifest.yaml",
        )

    param_version = str(manifest.get("param_version", ""))
    if param_version and param_version != PARAM_VERSION:
        warn(
            "param_version_mismatch",
            f"manifest.yaml param_version differs from canonical {PARAM_VERSION}",
            "manifest.yaml",
        )

    profiles = manifest.get("profiles")
    if profiles is not None:
        profile_values = set()
        if isinstance(profiles, Mapping):
            profile_values = {str(key) for key in profiles.keys()}
        elif isinstance(profiles, Iterable) and not isinstance(profiles, (str, bytes)):
            profile_values = {str(item) for item in profiles}

        allowed_profiles = {profile.value for profile in NetworkProfile}
        unknown = sorted(profile_values - allowed_profiles)
        if unknown:
            fail(
                "invalid_manifest_profiles",
                f"manifest.yaml contains non-canonical profiles: {', '.join(unknown)}",
                "manifest.yaml",
            )


def _verify_image_archives(
    archive: CapsuleArchive,
    *,
    manifest: Mapping[str, Any] | None,
    fail: Callable[[str, str, str | None], None],
    warn: Callable[[str, str, str | None], None],
) -> None:
    """Verify required capsule image archives and image metadata."""

    paths = set(archive.list_paths())
    image_archives = {
        path
        for path in paths
        if path.startswith("images/") and path.endswith(".oci.tar")
    }

    if "images/README.json" in paths and not image_archives:
        fail(
            "images_readme_only",
            "Capsule images/ contains README.json but no loadable images/*.oci.tar archives",
            "images/README.json",
        )

    if not image_archives:
        fail(
            "missing_image_archives",
            "Capsule images/ must contain required OCI archives; found no images/*.oci.tar",
            "images",
        )

    metadata_by_service: dict[str, set[str]] = {}
    metadata_paths: set[str] = set()
    metadata_entries: tuple[Mapping[str, Any], ...] = ()

    if archive.has(IMAGE_METADATA_FILENAME):
        try:
            metadata = _parse_yaml_or_json(
                archive.read_bytes(IMAGE_METADATA_FILENAME),
                path=IMAGE_METADATA_FILENAME,
            )
            metadata_entries = _image_metadata_entries(metadata)
            metadata_by_service, metadata_paths = _metadata_image_archives(metadata_entries)
        except Exception as exc:
            fail(
                "invalid_images_metadata",
                f"Could not parse {IMAGE_METADATA_FILENAME}: {exc}",
                IMAGE_METADATA_FILENAME,
            )
    else:
        fail(
            "missing_images_metadata",
            f"Capsule is missing required {IMAGE_METADATA_FILENAME}",
            IMAGE_METADATA_FILENAME,
        )
        if archive.has(LEGACY_IMAGE_METADATA_FILENAME):
            warn(
                "legacy_images_metadata_only",
                f"Legacy {LEGACY_IMAGE_METADATA_FILENAME} is present, but "
                f"{IMAGE_METADATA_FILENAME} is required",
                LEGACY_IMAGE_METADATA_FILENAME,
            )

    manifest_by_service, manifest_paths = _manifest_image_archives(manifest or {})
    declared_by_service = _merge_service_archives(manifest_by_service, metadata_by_service)
    declared_paths = set(manifest_paths) | set(metadata_paths)

    for declared_path in sorted(declared_paths):
        normalized = _normalize_image_archive_path(declared_path)
        if normalized is None:
            fail(
                "unsafe_declared_image_archive",
                f"Image metadata declares unsafe image archive path {declared_path}",
                IMAGE_METADATA_FILENAME,
            )
            continue

        if not normalized.startswith("images/") or not normalized.endswith(".oci.tar"):
            fail(
                "invalid_declared_image_archive",
                f"Image archive must be images/*.oci.tar: {normalized}",
                normalized,
            )
            continue

        if not archive.has(normalized):
            fail(
                "declared_image_archive_missing",
                f"Image metadata references missing archive {normalized}",
                normalized,
            )

    for required_archive in sorted(REQUIRED_IMAGE_ARCHIVES):
        if required_archive not in paths:
            fail(
                "missing_required_image_archive",
                "Capsule is missing required OCI image archive for service "
                f"{Path(required_archive).name.removesuffix('.oci.tar')}",
                required_archive,
            )

    for required_service in REQUIRED_IMAGE_SERVICES:
        if not _service_has_image_archive(
            required_service,
            image_archives=image_archives,
            declared_by_service=declared_by_service,
        ):
            fail(
                "missing_required_image_service",
                f"Capsule is missing required image metadata/archive for service {required_service}",
                f"images/{required_service}.oci.tar",
            )

    for image_archive in sorted(image_archives):
        if not archive.read_bytes(image_archive):
            fail(
                "empty_image_archive",
                f"Image archive is empty: {image_archive}",
                image_archive,
            )

    for entry in metadata_entries:
        _verify_image_metadata_entry(
            archive,
            entry=entry,
            fail=fail,
            warn=warn,
        )

    if declared_paths:
        normalized_declared = {
            normalized
            for declared_path in declared_paths
            if (normalized := _normalize_image_archive_path(declared_path)) is not None
        }

        for image_archive in sorted(image_archives - normalized_declared):
            warn(
                "undeclared_image_archive",
                f"Image archive is present but not declared in manifest/images.yaml: {image_archive}",
                image_archive,
            )


def _image_metadata_entries(metadata: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    """Return image entries from images.yaml."""

    images = metadata.get("images")
    if not isinstance(images, list):
        raise ValueError("images.yaml must contain an images list")

    entries: list[Mapping[str, Any]] = []
    for index, item in enumerate(images):
        if not isinstance(item, Mapping):
            raise ValueError(f"images.yaml images[{index}] must be a mapping")
        entries.append(item)

    if not entries:
        raise ValueError("images.yaml images list is empty")

    return tuple(entries)


def _metadata_image_archives(
    entries: Iterable[Mapping[str, Any]],
) -> tuple[dict[str, set[str]], set[str]]:
    """Return images.yaml-declared archives by service and as a path set."""

    by_service: dict[str, set[str]] = {}
    all_paths: set[str] = set()

    for item in entries:
        service = str(item.get("service") or "").strip()
        archive_path = item.get("archive") or item.get("path") or item.get("file")
        if not archive_path:
            continue

        normalized = _normalize_image_archive_path(str(archive_path))
        if normalized is None:
            all_paths.add(str(archive_path))
            continue

        all_paths.add(normalized)
        if service:
            by_service.setdefault(service, set()).add(normalized)

    return by_service, all_paths


def _verify_image_metadata_entry(
    archive: CapsuleArchive,
    *,
    entry: Mapping[str, Any],
    fail: Callable[[str, str, str | None], None],
    warn: Callable[[str, str, str | None], None],
) -> None:
    """Verify one images.yaml entry against the actual archive."""

    service = str(entry.get("service") or "").strip()
    image = str(entry.get("image") or "").strip()
    archive_value = str(entry.get("archive") or "").strip()
    sha_value = str(entry.get("sha256") or "").strip()
    size_value = entry.get("size_bytes")

    if not service:
        fail(
            "image_metadata_missing_service",
            "images.yaml entry is missing service",
            IMAGE_METADATA_FILENAME,
        )
    elif service not in set(REQUIRED_IMAGE_SERVICES) | set(OPTIONAL_IMAGE_SERVICES):
        warn(
            "image_metadata_unknown_service",
            f"images.yaml contains non-required service {service}",
            IMAGE_METADATA_FILENAME,
        )

    if not image:
        fail(
            "image_metadata_missing_image",
            f"images.yaml entry for {service or '<unknown>'} is missing image",
            IMAGE_METADATA_FILENAME,
        )

    normalized_archive = _normalize_image_archive_path(archive_value)
    if normalized_archive is None:
        fail(
            "image_metadata_invalid_archive",
            f"images.yaml entry for {service or '<unknown>'} has invalid archive path",
            IMAGE_METADATA_FILENAME,
        )
        return

    if not archive.has(normalized_archive):
        fail(
            "image_metadata_archive_missing",
            f"images.yaml references missing archive {normalized_archive}",
            normalized_archive,
        )
        return

    data = archive.read_bytes(normalized_archive)
    if not data:
        fail(
            "image_metadata_archive_empty",
            f"images.yaml references empty archive {normalized_archive}",
            normalized_archive,
        )

    if sha_value:
        if not re.fullmatch(r"[a-fA-F0-9]{64}", sha_value):
            fail(
                "image_metadata_invalid_sha256",
                f"images.yaml has invalid sha256 for {normalized_archive}",
                IMAGE_METADATA_FILENAME,
            )
        else:
            actual = sha256(data).hexdigest()
            if actual != sha_value.lower():
                fail(
                    "image_metadata_sha256_mismatch",
                    f"images.yaml sha256 mismatch for {normalized_archive}",
                    normalized_archive,
                )
    else:
        fail(
            "image_metadata_missing_sha256",
            f"images.yaml entry for {normalized_archive} is missing sha256",
            IMAGE_METADATA_FILENAME,
        )

    if size_value in (None, ""):
        fail(
            "image_metadata_missing_size",
            f"images.yaml entry for {normalized_archive} is missing size_bytes",
            IMAGE_METADATA_FILENAME,
        )
        return

    try:
        expected_size = int(size_value)
    except (TypeError, ValueError):
        fail(
            "image_metadata_invalid_size",
            f"images.yaml entry for {normalized_archive} has invalid size_bytes",
            IMAGE_METADATA_FILENAME,
        )
        return

    actual_size = len(data)
    if expected_size <= 0:
        fail(
            "image_metadata_nonpositive_size",
            f"images.yaml entry for {normalized_archive} has non-positive size_bytes",
            IMAGE_METADATA_FILENAME,
        )
    elif actual_size != expected_size:
        fail(
            "image_metadata_size_mismatch",
            f"images.yaml size mismatch for {normalized_archive}: expected "
            f"{expected_size}, got {actual_size}",
            normalized_archive,
        )


def _manifest_image_archives(
    manifest: Mapping[str, Any],
) -> tuple[dict[str, set[str]], set[str]]:
    """Return manifest-declared image archives.

    Supports these common shapes:

    images:
      frontend-next:
        archive: images/frontend-next.oci.tar

    images:
      - service: frontend-next
        archive: images/frontend-next.oci.tar

    runtime:
      images:
        frontend-next:
          archive: images/frontend-next.oci.tar
    """

    by_service: dict[str, set[str]] = {}
    all_paths: set[str] = set()

    def add(service: Any, archive_path: Any) -> None:
        archive_text = str(archive_path or "").strip()
        if not archive_text:
            return

        normalized_archive = _normalize_image_archive_path(archive_text)
        all_paths.add(normalized_archive or archive_text)

        normalized_service = str(service or "").strip()
        if normalized_service:
            by_service.setdefault(normalized_service, set()).add(
                normalized_archive or archive_text
            )

    def consume(images: Any) -> None:
        if images is None:
            return

        if isinstance(images, Mapping):
            for service, value in images.items():
                if isinstance(value, Mapping):
                    add(
                        service,
                        value.get("archive")
                        or value.get("path")
                        or value.get("file")
                        or value.get("oci_archive"),
                    )
                else:
                    add(service, value)
            return

        if isinstance(images, Iterable) and not isinstance(images, (str, bytes)):
            for item in images:
                if isinstance(item, Mapping):
                    add(
                        item.get("service") or item.get("name"),
                        item.get("archive")
                        or item.get("path")
                        or item.get("file")
                        or item.get("oci_archive"),
                    )
                else:
                    add(None, item)

    consume(manifest.get("images"))

    runtime = manifest.get("runtime")
    if isinstance(runtime, Mapping):
        consume(runtime.get("images"))

    services = manifest.get("services")
    if isinstance(services, Mapping):
        for service, value in services.items():
            if isinstance(value, Mapping):
                add(
                    service,
                    value.get("archive")
                    or value.get("image_archive")
                    or value.get("oci_archive"),
                )

    return by_service, all_paths


def _service_has_image_archive(
    service: str,
    *,
    image_archives: set[str],
    declared_by_service: Mapping[str, set[str]],
) -> bool:
    """Return True when a required service has a present archive."""

    canonical = f"images/{service}.oci.tar"
    if canonical in image_archives:
        return True

    for declared_path in declared_by_service.get(service, set()):
        normalized = _normalize_image_archive_path(declared_path)
        if normalized in image_archives:
            return True

    service_token = service.lower().replace("_", "-")
    for archive_path in image_archives:
        filename = PurePosixPath(archive_path).name.lower().replace("_", "-")
        if service_token in filename:
            return True

    return False


def _merge_service_archives(
    *items: Mapping[str, set[str]],
) -> dict[str, set[str]]:
    merged: dict[str, set[str]] = {}
    for item in items:
        for service, paths in item.items():
            merged.setdefault(service, set()).update(paths)
    return merged


def _normalize_image_archive_path(path: str) -> str | None:
    """Normalize an image archive path.

    images.yaml stores archive paths relative to images/ in the image helper,
    while manifest.yaml commonly stores full images/<name>.oci.tar paths.
    Accept both.
    """

    normalized = normalize_archive_path(path)
    if normalized is None:
        return None

    if "/" not in normalized:
        normalized = f"images/{normalized}"

    return normalized


def _verify_checksums(
    archive: CapsuleArchive,
    *,
    fail: Callable[[str, str, str | None], None],
    warn: Callable[[str, str, str | None], None],
) -> None:
    """Verify ``checksums.txt`` entries against archive contents."""

    text = archive.read_text("checksums.txt")
    seen: set[str] = set()

    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        parsed = _parse_checksum_line(line)
        if parsed is None:
            fail(
                "invalid_checksum_line",
                f"Invalid checksums.txt line {line_number}",
                "checksums.txt",
            )
            continue

        digest, checksum_path = parsed
        member_path = normalize_archive_path(checksum_path.strip())

        if member_path is None:
            fail(
                "unsafe_checksum_path",
                f"Unsafe checksums.txt path on line {line_number}",
                "checksums.txt",
            )
            continue

        if member_path == "checksums.txt":
            warn(
                "checksum_self_reference",
                "checksums.txt should not checksum itself",
                "checksums.txt",
            )
            continue

        if not archive.has(member_path):
            fail(
                "checksum_missing_member",
                f"checksums.txt references missing member {member_path}",
                member_path,
            )
            continue

        actual = sha256(archive.read_bytes(member_path)).hexdigest()
        if actual != digest:
            fail(
                "checksum_mismatch",
                f"Checksum mismatch for {member_path}",
                member_path,
            )

        seen.add(member_path)

    for required in sorted(REQUIRED_ROOT_FILES - {"checksums.txt", "signature.sig"}):
        if archive.has(required) and required not in seen:
            warn(
                "required_file_not_checksummed",
                f"{required} is not listed in checksums.txt",
                required,
            )

    for required_archive in sorted(REQUIRED_IMAGE_ARCHIVES):
        if archive.has(required_archive) and required_archive not in seen:
            fail(
                "required_image_archive_not_checksummed",
                f"Required image archive is not listed in checksums.txt: {required_archive}",
                required_archive,
            )

    for image_archive in sorted(
        path
        for path in archive.list_paths()
        if path.startswith("images/") and path.endswith(".oci.tar")
    ):
        if image_archive not in seen:
            fail(
                "image_archive_not_checksummed",
                f"Image archive is not listed in checksums.txt: {image_archive}",
                image_archive,
            )


def _parse_checksum_line(line: str) -> tuple[str, str] | None:
    digest_first = CHECKSUM_DIGEST_FIRST_RE.match(line)
    if digest_first:
        return (
            digest_first.group("digest").lower(),
            digest_first.group("path").strip(),
        )

    algo_first = CHECKSUM_ALGO_FIRST_RE.match(line)
    if algo_first:
        return (
            algo_first.group("digest").lower(),
            algo_first.group("path").strip(),
        )

    return None


def _verify_signature(
    archive: CapsuleArchive,
    *,
    public_key: bytes | None,
    signature_verifier: SignatureVerifier | None,
    require_signature_verifier: bool,
    fail: Callable[[str, str, str | None], None],
    warn: Callable[[str, str, str | None], None],
) -> None:
    """Verify signature presence and optionally cryptographic validity."""

    if not archive.has("signature.sig"):
        fail(
            "missing_signature",
            "Capsule is missing mandatory signature.sig",
            "signature.sig",
        )
        return

    if not archive.read_bytes("signature.sig"):
        fail(
            "empty_signature",
            "signature.sig is empty",
            "signature.sig",
        )
        return

    if not archive.has("manifest.yaml") or not archive.has("checksums.txt"):
        fail(
            "signature_inputs_missing",
            "Cannot verify signature without manifest.yaml and checksums.txt",
            "signature.sig",
        )
        return

    if signature_verifier is None:
        if require_signature_verifier:
            fail(
                "signature_verifier_missing",
                "A signature verifier is required but was not provided",
                "signature.sig",
            )
        else:
            warn(
                "signature_not_cryptographically_verified",
                "signature.sig is present but no cryptographic verifier was provided",
                "signature.sig",
            )
        return

    if public_key is None:
        fail(
            "public_key_missing",
            "A public key is required for cryptographic signature verification",
            "signature.sig",
        )
        return

    ok = signature_verifier(
        archive.read_bytes("manifest.yaml"),
        archive.read_bytes("checksums.txt"),
        archive.read_bytes("signature.sig"),
    )

    if not ok:
        fail(
            "signature_invalid",
            "Capsule signature verification failed",
            "signature.sig",
        )


def _verify_env_templates(
    archive: CapsuleArchive,
    *,
    fail: Callable[[str, str, str | None], None],
    warn: Callable[[str, str, str | None], None],
) -> None:
    """Reject obvious real secrets in env templates."""

    for path in archive.list_paths():
        if not path.startswith("env-templates/"):
            continue

        if not path.endswith((".template", ".env", ".txt")):
            continue

        text = archive.read_text(path)
        for line_number, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue

            for pattern in SECRET_KEY_PATTERNS:
                if pattern.search(stripped):
                    fail(
                        "possible_real_secret",
                        f"Possible real secret in {path} line {line_number}",
                        path,
                    )

        if "DJANGO_SECRET_KEY=<GENERATED_ON_INSTALL>" not in text and path.endswith(
            "django.env.template"
        ):
            warn(
                "missing_django_secret_placeholder",
                "django.env.template should include DJANGO_SECRET_KEY=<GENERATED_ON_INSTALL>",
                path,
            )

        if "POSTGRES_PASSWORD=<GENERATED_ON_INSTALL>" not in text and path.endswith(
            "postgres.env.template"
        ):
            warn(
                "missing_postgres_password_placeholder",
                "postgres.env.template should include POSTGRES_PASSWORD=<GENERATED_ON_INSTALL>",
                path,
            )


def _build_report(
    path: Path,
    issues: Iterable[VerifyIssue],
    warnings: Iterable[VerifyIssue],
    manifest: Mapping[str, Any] | None,
) -> CapsuleVerifyReport:
    """Build final report from accumulated issues."""

    issue_tuple = tuple(issues)
    manifest_data = dict(manifest or {})

    return CapsuleVerifyReport(
        ok=not any(issue.status == VerifyStatus.FAIL for issue in issue_tuple),
        capsule_path=path,
        capsule_id=_optional_str(manifest_data.get("capsule_id")),
        capsule_version=_optional_str(manifest_data.get("capsule_version")),
        app_version=_optional_str(manifest_data.get("app_version")),
        param_version=_optional_str(manifest_data.get("param_version")),
        checks=issue_tuple,
        warnings=tuple(warnings),
        manifest=manifest_data if manifest_data else None,
    )


def _optional_str(value: Any) -> str | None:
    """Return stripped string value or None."""

    if value is None:
        return None

    text = str(value).strip()
    return text or None


__all__ = [
    "CapsuleArchive",
    "CapsuleVerifyError",
    "CapsuleVerifyReport",
    "IMAGE_METADATA_FILENAME",
    "LEGACY_IMAGE_METADATA_FILENAME",
    "OPTIONAL_IMAGE_SERVICES",
    "REQUIRED_IMAGE_ARCHIVES",
    "REQUIRED_IMAGE_SERVICES",
    "SignatureVerifier",
    "VerifyIssue",
    "VerifyStatus",
    "assert_capsule_valid",
    "issue_to_dict",
    "normalize_archive_path",
    "parse_manifest",
    "read_capsule_archive",
    "verify_capsule",
    "verify_capsule_file",
]