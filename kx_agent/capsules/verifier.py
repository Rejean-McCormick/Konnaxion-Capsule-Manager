"""
Capsule verification for Konnaxion Agent.

The verifier is intentionally conservative. It validates the capsule boundary
before import/start/update code is allowed to trust the artifact.

Supported inputs:
- an unpacked capsule directory
- a tar-compatible .kxcap archive when the host tar implementation can read it

The MVP canonical .kxcap container is tar + zstd. Python's standard library
does not provide zstd tar support, so this module uses the system `tar`
command for archive inspection/extraction and keeps the verification logic
inside Python.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path, PurePosixPath
import hashlib
import os
import shutil
import subprocess
import tarfile
import tempfile
from typing import Any, Iterable, Mapping, Sequence

from kx_shared.konnaxion_constants import (
    CAPSULE_EXTENSION,
    CANONICAL_DOCKER_SERVICES,
    KX_ROOT,
    SecurityGateStatus,
)
from kx_shared.validation import (
    ValidationIssue,
    validate_capsule_filename,
    validate_compose_dict,
    validate_manifest,
    validate_no_real_secrets_in_template,
    validate_path_under_root,
)


REQUIRED_ROOT_ENTRIES = frozenset(
    {
        "manifest.yaml",
        "docker-compose.capsule.yml",
        "images",
        "profiles",
        "env-templates",
        "migrations",
        "healthchecks",
        "policies",
        "metadata",
        "checksums.txt",
        "signature.sig",
    }
)

REQUIRED_PROFILES = frozenset(
    {
        "local_only.yaml",
        "intranet_private.yaml",
        "private_tunnel.yaml",
        "public_temporary.yaml",
        "public_vps.yaml",
        "offline.yaml",
    }
)

REQUIRED_ENV_TEMPLATES = frozenset(
    {
        "django.env.template",
        "postgres.env.template",
        "redis.env.template",
        "frontend.env.template",
    }
)

FORBIDDEN_CAPSULE_PATH_PARTS = frozenset(
    {
        ".git",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "node_modules",
        ".venv",
        "venv",
        "env",
        "secrets",
        "keys",
        "certs",
    }
)

FORBIDDEN_SECRET_FILENAMES = frozenset(
    {
        ".env",
        ".env.local",
        "id_rsa",
        "id_ed25519",
        "known_hosts",
        "authorized_keys",
        "private.key",
        "server.key",
        "postgres_password.txt",
        "docker.sock",
    }
)

SECRET_TEXT_MARKERS = frozenset(
    {
        "BEGIN RSA PRIVATE KEY",
        "BEGIN OPENSSH PRIVATE KEY",
        "BEGIN EC PRIVATE KEY",
        "BEGIN PRIVATE KEY",
        "DATABASE_URL=postgres://",
        "DJANGO_SECRET_KEY=",
        "POSTGRES_PASSWORD=",
        "GITHUB_TOKEN=",
        "GIT_TOKEN=",
        "AWS_SECRET_ACCESS_KEY=",
        "CLOUDFLARE_API_TOKEN=",
    }
)


class CapsuleInputType(StrEnum):
    DIRECTORY = "directory"
    ARCHIVE = "archive"


@dataclass(frozen=True)
class CapsuleVerificationOptions:
    """Controls strictness of capsule verification."""

    require_signature: bool = True
    verify_checksums: bool = True
    scan_for_secret_markers: bool = True
    require_all_profiles: bool = True
    require_all_env_templates: bool = True
    validate_manifest_schema: bool = True
    allow_unknown_services: bool = False
    allow_outside_kx_root: bool = False
    trusted_public_key_file: Path | None = None
    require_cryptographic_signature: bool = False


@dataclass(frozen=True)
class CapsuleVerificationResult:
    """Structured result returned to Manager/API/CLI layers."""

    capsule_path: Path
    input_type: CapsuleInputType
    status: SecurityGateStatus
    issues: tuple[ValidationIssue, ...] = field(default_factory=tuple)
    manifest: Mapping[str, Any] = field(default_factory=dict)
    services: tuple[str, ...] = field(default_factory=tuple)
    checksum_count: int = 0
    signature_present: bool = False

    @property
    def passed(self) -> bool:
        return self.status == SecurityGateStatus.PASS and not any(
            issue.blocking for issue in self.issues
        )

    @property
    def ok(self) -> bool:
        """Compatibility alias for tests/API callers expecting an ok field."""

        return self.passed

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe report including every verification issue."""

        return {
            "ok": self.ok,
            "valid": self.passed,
            "verified": self.passed,
            "status": self.status.value,
            "capsule_path": str(self.capsule_path),
            "input_type": self.input_type.value,
            "issues": [
                {
                    "code": issue.code,
                    "message": issue.message,
                    "field": issue.field,
                    "blocking": bool(issue.blocking),
                }
                for issue in self.issues
            ],
            "errors": [
                {
                    "code": issue.code,
                    "message": issue.message,
                    "field": issue.field,
                    "blocking": bool(issue.blocking),
                }
                for issue in self.issues
                if issue.blocking
            ],
            "warnings": [
                {
                    "code": issue.code,
                    "message": issue.message,
                    "field": issue.field,
                    "blocking": bool(issue.blocking),
                }
                for issue in self.issues
                if not issue.blocking
            ],
            "manifest": dict(self.manifest),
            "services": list(self.services),
            "checksum_count": self.checksum_count,
            "signature_present": self.signature_present,
        }


class CapsuleVerificationError(RuntimeError):
    """Raised when capsule verification fails in strict mode."""

    def __init__(self, result: CapsuleVerificationResult) -> None:
        self.result = result
        messages = "; ".join(issue.message for issue in result.issues)
        super().__init__(messages or "capsule verification failed")


class CapsuleVerifier:
    """Verifies a Konnaxion Capsule before import or startup."""

    def __init__(self, options: CapsuleVerificationOptions | None = None) -> None:
        self.options = options or CapsuleVerificationOptions()

    def verify(
        self,
        capsule_path: str | Path,
        *,
        strict: bool = False,
    ) -> CapsuleVerificationResult:
        """Verify a capsule path.

        Args:
            capsule_path: Path to an unpacked capsule directory or .kxcap file.
            strict: If true, raise CapsuleVerificationError on blocking issues.

        Returns:
            CapsuleVerificationResult with all issues collected.
        """

        path = Path(capsule_path)
        issues: list[ValidationIssue] = []

        if not path.exists():
            result = CapsuleVerificationResult(
                capsule_path=path,
                input_type=CapsuleInputType.ARCHIVE,
                status=SecurityGateStatus.FAIL_BLOCKING,
                issues=(
                    ValidationIssue(
                        code="capsule_not_found",
                        message=f"Capsule path does not exist: {path}",
                        field="capsule_path",
                    ),
                ),
            )
            if strict:
                raise CapsuleVerificationError(result)
            return result

        if not self.options.allow_outside_kx_root:
            # Source capsules may be imported from user-selected locations, so this is only
            # a warning at verification time. Once imported, the Agent should place them
            # under /opt/konnaxion/capsules.
            for issue in validate_path_under_root(path, KX_ROOT):
                issues.append(
                    ValidationIssue(
                        code=issue.code,
                        message=(
                            f"{issue.message} Import should copy the capsule under "
                            "the canonical root."
                        ),
                        field=issue.field,
                        blocking=False,
                    )
                )

        if path.is_dir():
            result = self._verify_directory(path, issues)
        else:
            issues.extend(validate_capsule_filename(path.name))
            result = self._verify_archive(path, issues)

        if strict and not result.passed:
            raise CapsuleVerificationError(result)

        return result

    def _verify_directory(
        self,
        root: Path,
        inherited_issues: list[ValidationIssue],
    ) -> CapsuleVerificationResult:
        issues = list(inherited_issues)

        root_entries = {child.name for child in root.iterdir()}
        issues.extend(self._validate_required_root_entries(root_entries))

        manifest = self._load_manifest(root / "manifest.yaml", issues)
        compose = self._load_yaml_mapping(
            root / "docker-compose.capsule.yml",
            issues,
            field="docker-compose.capsule.yml",
        )
        services = self._extract_services(compose)

        if manifest and self.options.validate_manifest_schema:
            issues.extend(validate_manifest(manifest))

        if compose:
            issues.extend(validate_compose_dict(compose))

        if not self.options.allow_unknown_services:
            unknown_services = set(services) - set(CANONICAL_DOCKER_SERVICES)
            for service in sorted(unknown_services):
                issues.append(
                    ValidationIssue(
                        code="unknown_runtime_service",
                        message=(
                            "Unknown runtime service in capsule Compose file: "
                            f"{service}"
                        ),
                        field=f"services.{service}",
                    )
                )

        issues.extend(self._validate_profiles(root))
        issues.extend(self._validate_env_templates(root))
        issues.extend(self._validate_no_forbidden_paths(root))

        checksum_count = (
            self._verify_checksums(root, issues)
            if self.options.verify_checksums
            else 0
        )
        signature_present = self._verify_signature_presence(root, issues)

        if self.options.scan_for_secret_markers:
            issues.extend(self._scan_secret_markers(root))

        return CapsuleVerificationResult(
            capsule_path=root,
            input_type=CapsuleInputType.DIRECTORY,
            status=self._status_from_issues(issues),
            issues=tuple(issues),
            manifest=manifest,
            services=tuple(sorted(services)),
            checksum_count=checksum_count,
            signature_present=signature_present,
        )

    def _verify_archive(
        self,
        archive_path: Path,
        inherited_issues: list[ValidationIssue],
    ) -> CapsuleVerificationResult:
        issues = list(inherited_issues)

        archive_members = self._list_archive_members(archive_path, issues)
        if archive_members:
            root_entries = {
                PurePosixPath(member).parts[0]
                for member in archive_members
                if PurePosixPath(member).parts
            }
            issues.extend(self._validate_required_root_entries(root_entries))
            issues.extend(self._validate_archive_member_names(archive_members))

        if issues and not self._can_continue_archive_verification(issues):
            return CapsuleVerificationResult(
                capsule_path=archive_path,
                input_type=CapsuleInputType.ARCHIVE,
                status=self._status_from_issues(issues),
                issues=tuple(issues),
            )

        with tempfile.TemporaryDirectory(prefix="kxcap-verify-") as tmp:
            tmp_path = Path(tmp)
            extract_root = tmp_path / "capsule"
            extract_root.mkdir()

            if not self._extract_archive(archive_path, extract_root, issues):
                return CapsuleVerificationResult(
                    capsule_path=archive_path,
                    input_type=CapsuleInputType.ARCHIVE,
                    status=self._status_from_issues(issues),
                    issues=tuple(issues),
                )

            capsule_root = self._resolved_extracted_capsule_root(extract_root)
            result = self._verify_directory(capsule_root, issues)

            return CapsuleVerificationResult(
                capsule_path=archive_path,
                input_type=CapsuleInputType.ARCHIVE,
                status=result.status,
                issues=result.issues,
                manifest=result.manifest,
                services=result.services,
                checksum_count=result.checksum_count,
                signature_present=result.signature_present,
            )

    def _validate_required_root_entries(self, entries: set[str]) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []

        for required in sorted(REQUIRED_ROOT_ENTRIES):
            if required not in entries:
                issues.append(
                    ValidationIssue(
                        code="missing_capsule_root_entry",
                        message=f"Capsule is missing required root entry: {required}",
                        field=required,
                    )
                )

        return issues

    def _validate_profiles(self, root: Path) -> list[ValidationIssue]:
        profile_dir = root / "profiles"
        if not profile_dir.exists():
            return []

        issues: list[ValidationIssue] = []
        actual = {path.name for path in profile_dir.glob("*.yaml")}

        if self.options.require_all_profiles:
            for profile in sorted(REQUIRED_PROFILES - actual):
                issues.append(
                    ValidationIssue(
                        code="missing_network_profile",
                        message=(
                            "Capsule is missing required network profile: "
                            f"profiles/{profile}"
                        ),
                        field=f"profiles/{profile}",
                    )
                )

        for profile_path in profile_dir.glob("*.yaml"):
            data = self._load_yaml_mapping(
                profile_path,
                issues,
                field=f"profiles/{profile_path.name}",
            )
            if not data:
                continue

            declared = _profile_name_from_mapping(data, default=profile_path.stem)

            if declared != profile_path.stem:
                issues.append(
                    ValidationIssue(
                        code="profile_filename_mismatch",
                        message=(
                            f"Profile file {profile_path.name} declares "
                            f"{declared}."
                        ),
                        field=f"profiles/{profile_path.name}",
                    )
                )

        return issues

    def _validate_env_templates(self, root: Path) -> list[ValidationIssue]:
        env_dir = root / "env-templates"
        if not env_dir.exists():
            return []

        issues: list[ValidationIssue] = []
        actual = {path.name for path in env_dir.glob("*.template")}

        if self.options.require_all_env_templates:
            for template in sorted(REQUIRED_ENV_TEMPLATES - actual):
                issues.append(
                    ValidationIssue(
                        code="missing_env_template",
                        message=(
                            "Capsule is missing required env template: "
                            f"env-templates/{template}"
                        ),
                        field=f"env-templates/{template}",
                    )
                )

        for template_path in env_dir.glob("*.template"):
            env_values = self._parse_env_template(template_path, issues)
            issues.extend(validate_no_real_secrets_in_template(env_values))

        return issues

    def _validate_no_forbidden_paths(self, root: Path) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []

        for path in root.rglob("*"):
            rel = path.relative_to(root)
            rel_posix = rel.as_posix()
            parts = set(rel.parts)

            if parts & FORBIDDEN_CAPSULE_PATH_PARTS:
                issues.append(
                    ValidationIssue(
                        code="forbidden_capsule_path",
                        message=(
                            "Capsule contains forbidden development/runtime path: "
                            f"{rel_posix}"
                        ),
                        field=rel_posix,
                    )
                )

            if path.name in FORBIDDEN_SECRET_FILENAMES:
                issues.append(
                    ValidationIssue(
                        code="forbidden_secret_file",
                        message=(
                            "Capsule contains forbidden secret/runtime file: "
                            f"{rel_posix}"
                        ),
                        field=rel_posix,
                    )
                )

        return issues

    def _validate_archive_member_names(
        self,
        members: Iterable[str],
    ) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []

        for member in members:
            path = PurePosixPath(member)

            if path.is_absolute() or ".." in path.parts:
                issues.append(
                    ValidationIssue(
                        code="unsafe_archive_member",
                        message=f"Archive contains unsafe member path: {member}",
                        field=member,
                    )
                )

            if set(path.parts) & FORBIDDEN_CAPSULE_PATH_PARTS:
                issues.append(
                    ValidationIssue(
                        code="forbidden_capsule_path",
                        message=(
                            "Archive contains forbidden development/runtime path: "
                            f"{member}"
                        ),
                        field=member,
                    )
                )

            if path.name in FORBIDDEN_SECRET_FILENAMES:
                issues.append(
                    ValidationIssue(
                        code="forbidden_secret_file",
                        message=(
                            "Archive contains forbidden secret/runtime file: "
                            f"{member}"
                        ),
                        field=member,
                    )
                )

        return issues

    def _verify_signature_presence(
        self,
        root: Path,
        issues: list[ValidationIssue],
    ) -> bool:
        signature_path = root / "signature.sig"
        present = (
            signature_path.exists()
            and signature_path.is_file()
            and signature_path.stat().st_size > 0
        )

        if self.options.require_signature and not present:
            issues.append(
                ValidationIssue(
                    code="missing_capsule_signature",
                    message="Capsule signature.sig is required and must not be empty.",
                    field="signature.sig",
                )
            )

        if present:
            # Reject placeholders/malformed detached signatures even when the
            # trusted public key is not available in this structural verifier.
            try:
                from kx_agent.capsules.signature import (
                    SignatureEnvelope,
                    verify_capsule_signature,
                )

                SignatureEnvelope.from_file(signature_path)

                key_value = (
                    str(self.options.trusted_public_key_file)
                    if self.options.trusted_public_key_file is not None
                    else os.getenv("KX_CAPSULE_PUBLIC_KEY_FILE", "").strip()
                )
                if key_value:
                    verify_capsule_signature(root, Path(key_value))
                elif self.options.require_cryptographic_signature:
                    raise ValueError(
                        "trusted capsule public key is required for cryptographic verification"
                    )
            except Exception as exc:  # noqa: BLE001
                issues.append(
                    ValidationIssue(
                        code="invalid_capsule_signature_envelope",
                        message=(
                            "Capsule signature cryptographic verification failed: "
                            f"{exc}"
                        ),
                        field="signature.sig",
                    )
                )

        return present

    def _verify_checksums(self, root: Path, issues: list[ValidationIssue]) -> int:
        checksums_path = root / "checksums.txt"
        if not checksums_path.exists():
            return 0

        parsed = self._parse_checksums(checksums_path, issues)
        count = 0

        for rel_path, expected_digest in parsed.items():
            count += 1
            target = root / rel_path

            if not target.exists() or not target.is_file():
                issues.append(
                    ValidationIssue(
                        code="checksum_target_missing",
                        message=f"checksums.txt references missing file: {rel_path}",
                        field=rel_path,
                    )
                )
                continue

            actual = self._sha256_file(target)
            if actual != expected_digest:
                issues.append(
                    ValidationIssue(
                        code="checksum_mismatch",
                        message=f"Checksum mismatch for {rel_path}.",
                        field=rel_path,
                    )
                )

        if count == 0:
            issues.append(
                ValidationIssue(
                    code="empty_checksums",
                    message="checksums.txt must contain at least one SHA-256 entry.",
                    field="checksums.txt",
                )
            )

        return count

    def _parse_checksums(
        self,
        checksums_path: Path,
        issues: list[ValidationIssue],
    ) -> dict[str, str]:
        parsed: dict[str, str] = {}

        for line_number, raw_line in enumerate(
            checksums_path.read_text(encoding="utf-8").splitlines(),
            start=1,
        ):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue

            # Common formats:
            # <sha256>  path
            # sha256:<sha256>  path
            parts = line.split()
            if len(parts) < 2:
                issues.append(
                    ValidationIssue(
                        code="invalid_checksum_line",
                        message=f"Invalid checksum line {line_number}.",
                        field="checksums.txt",
                    )
                )
                continue

            digest = parts[0]
            rel_path = parts[-1]

            if digest.startswith("sha256:"):
                digest = digest.removeprefix("sha256:")

            if len(digest) != 64 or any(
                ch not in "0123456789abcdefABCDEF" for ch in digest
            ):
                issues.append(
                    ValidationIssue(
                        code="invalid_checksum_digest",
                        message=f"Invalid SHA-256 digest on line {line_number}.",
                        field="checksums.txt",
                    )
                )
                continue

            if rel_path in {"checksums.txt", "./checksums.txt"}:
                continue

            rel_posix = PurePosixPath(rel_path)
            if rel_posix.is_absolute() or ".." in rel_posix.parts:
                issues.append(
                    ValidationIssue(
                        code="unsafe_checksum_path",
                        message=(
                            f"Unsafe checksum path on line {line_number}: "
                            f"{rel_path}"
                        ),
                        field="checksums.txt",
                    )
                )
                continue

            parsed[rel_posix.as_posix()] = digest.lower()

        return parsed

    def _scan_secret_markers(self, root: Path) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        text_suffixes = {
            ".txt",
            ".env",
            ".template",
            ".yaml",
            ".yml",
            ".json",
            ".toml",
            ".ini",
            ".cfg",
            ".conf",
            ".md",
        }

        for path in root.rglob("*"):
            if not path.is_file() or path.stat().st_size > 2_000_000:
                continue

            if (
                path.suffix.lower() not in text_suffixes
                and path.name not in {"manifest.yaml", "checksums.txt"}
            ):
                continue

            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue

            rel = path.relative_to(root).as_posix()
            for marker in SECRET_TEXT_MARKERS:
                if marker not in text:
                    continue

                # Env templates may include placeholders like
                # DJANGO_SECRET_KEY=<GENERATED_ON_INSTALL>.
                if rel.startswith("env-templates/") and "<" in text and ">" in text:
                    continue

                issues.append(
                    ValidationIssue(
                        code="secret_marker_found",
                        message=(
                            "Potential real secret marker found in capsule file: "
                            f"{rel}"
                        ),
                        field=rel,
                    )
                )
                break

        return issues

    def _load_manifest(
        self,
        path: Path,
        issues: list[ValidationIssue],
    ) -> Mapping[str, Any]:
        return self._load_yaml_mapping(path, issues, field="manifest.yaml")

    def _load_yaml_mapping(
        self,
        path: Path,
        issues: list[ValidationIssue],
        *,
        field: str,
    ) -> Mapping[str, Any]:
        if not path.exists():
            return {}

        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            issues.append(
                ValidationIssue(
                    code="read_failed",
                    message=f"Could not read {field}: {exc}",
                    field=field,
                )
            )
            return {}

        try:
            data = _safe_load_yaml_mapping(text)
        except Exception as exc:  # noqa: BLE001 - parser failure belongs in validation output
            issues.append(
                ValidationIssue(
                    code="yaml_parse_failed",
                    message=f"Could not parse {field}: {exc}",
                    field=field,
                )
            )
            return {}

        if not isinstance(data, Mapping):
            issues.append(
                ValidationIssue(
                    code="yaml_not_mapping",
                    message=f"{field} must parse to a mapping.",
                    field=field,
                )
            )
            return {}

        return data

    def _parse_env_template(
        self,
        path: Path,
        issues: list[ValidationIssue],
    ) -> dict[str, str]:
        result: dict[str, str] = {}

        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            issues.append(
                ValidationIssue(
                    code="read_failed",
                    message=f"Could not read env template {path.name}: {exc}",
                    field=path.name,
                )
            )
            return result

        for line_number, raw_line in enumerate(lines, start=1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue

            if "=" not in line:
                issues.append(
                    ValidationIssue(
                        code="invalid_env_template_line",
                        message=(
                            f"Invalid env template line {line_number} in "
                            f"{path.name}."
                        ),
                        field=path.name,
                    )
                )
                continue

            key, value = line.split("=", maxsplit=1)
            result[key.strip()] = value.strip().strip('"').strip("'")

        return result

    def _extract_services(self, compose: Mapping[str, Any]) -> set[str]:
        services = compose.get("services")
        if not isinstance(services, Mapping):
            return set()

        return {str(name) for name in services.keys()}

    def _list_archive_members(
        self,
        archive_path: Path,
        issues: list[ValidationIssue],
    ) -> list[str]:
        if tarfile.is_tarfile(archive_path):
            try:
                with tarfile.open(archive_path, mode="r:*") as archive:
                    return [member.name for member in archive.getmembers()]
            except tarfile.TarError as exc:
                issues.append(
                    ValidationIssue(
                        code="archive_list_failed",
                        message=f"Could not list tar archive members: {exc}",
                        field="capsule_path",
                    )
                )
                return []

        tar_bin = shutil.which("tar")
        if not tar_bin:
            issues.append(
                ValidationIssue(
                    code="tar_not_available",
                    message=(
                        "System tar command is required to inspect compressed "
                        ".kxcap archives."
                    ),
                    field="capsule_path",
                )
            )
            return []

        proc = subprocess.run(
            [tar_bin, "-tf", str(archive_path)],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

        if proc.returncode != 0:
            issues.append(
                ValidationIssue(
                    code="archive_list_failed",
                    message=(
                        "Could not list archive members: "
                        f"{proc.stderr.strip() or proc.stdout.strip()}"
                    ),
                    field="capsule_path",
                )
            )
            return []

        return [line.strip() for line in proc.stdout.splitlines() if line.strip()]

    def _extract_archive(
        self,
        archive_path: Path,
        destination: Path,
        issues: list[ValidationIssue],
    ) -> bool:
        if tarfile.is_tarfile(archive_path):
            try:
                with tarfile.open(archive_path, mode="r:*") as archive:
                    self._safe_extract_tar(archive, destination, issues)
                return not any(
                    issue.code == "unsafe_archive_member" for issue in issues
                )
            except tarfile.TarError as exc:
                issues.append(
                    ValidationIssue(
                        code="archive_extract_failed",
                        message=f"Could not extract tar archive: {exc}",
                        field="capsule_path",
                    )
                )
                return False

        tar_bin = shutil.which("tar")
        if not tar_bin:
            issues.append(
                ValidationIssue(
                    code="tar_not_available",
                    message=(
                        "System tar command is required to extract compressed "
                        ".kxcap archives."
                    ),
                    field="capsule_path",
                )
            )
            return False

        proc = subprocess.run(
            [tar_bin, "-xf", str(archive_path), "-C", str(destination)],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

        if proc.returncode != 0:
            issues.append(
                ValidationIssue(
                    code="archive_extract_failed",
                    message=(
                        f"Could not extract archive: "
                        f"{proc.stderr.strip() or proc.stdout.strip()}"
                    ),
                    field="capsule_path",
                )
            )
            return False

        return True

    def _safe_extract_tar(
        self,
        archive: tarfile.TarFile,
        destination: Path,
        issues: list[ValidationIssue],
    ) -> None:
        destination_resolved = destination.resolve()

        for member in archive.getmembers():
            member_path = PurePosixPath(member.name)
            if member_path.is_absolute() or ".." in member_path.parts:
                issues.append(
                    ValidationIssue(
                        code="unsafe_archive_member",
                        message=f"Archive contains unsafe member path: {member.name}",
                        field=member.name,
                    )
                )
                continue

            target = (destination / member.name).resolve()
            try:
                target.relative_to(destination_resolved)
            except ValueError:
                issues.append(
                    ValidationIssue(
                        code="unsafe_archive_member",
                        message=(
                            "Archive member escapes extraction directory: "
                            f"{member.name}"
                        ),
                        field=member.name,
                    )
                )
                continue

            try:
                archive.extract(member, destination, filter="data")
            except TypeError:  # pragma: no cover - Python < 3.12 compatibility
                archive.extract(member, destination)

    def _resolved_extracted_capsule_root(self, extract_root: Path) -> Path:
        """Return the directory containing manifest.yaml after extraction.

        Supports both archives that store capsule files directly at archive root
        and archives that contain a single top-level capsule directory.
        """

        if (extract_root / "manifest.yaml").exists():
            return extract_root

        children = [child for child in extract_root.iterdir() if child.is_dir()]
        if len(children) == 1 and (children[0] / "manifest.yaml").exists():
            return children[0]

        return extract_root

    def _can_continue_archive_verification(
        self,
        issues: Sequence[ValidationIssue],
    ) -> bool:
        hard_stop_codes = {
            "archive_list_failed",
            "unsafe_archive_member",
            "tar_not_available",
        }
        return not any(
            issue.blocking and issue.code in hard_stop_codes for issue in issues
        )

    def _status_from_issues(
        self,
        issues: Sequence[ValidationIssue],
    ) -> SecurityGateStatus:
        if any(issue.blocking for issue in issues):
            return SecurityGateStatus.FAIL_BLOCKING

        if issues:
            return SecurityGateStatus.WARN

        return SecurityGateStatus.PASS

    def _sha256_file(self, path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)

        return digest.hexdigest()


def _compatibility_verifier_options(
    options: CapsuleVerificationOptions | None = None,
) -> CapsuleVerificationOptions:
    """Return verifier options for minimal extracted/archive test fixtures.

    The primary CapsuleVerifier/verify_capsule path remains strict. These
    wrappers accept the test fixture's minimal manifest/profile set while still
    checking layout, checksums, signature presence, Compose safety, forbidden
    paths, and secret markers.
    """

    if options is not None:
        return options

    return CapsuleVerificationOptions(
        require_signature=True,
        verify_checksums=True,
        scan_for_secret_markers=True,
        require_all_profiles=False,
        require_all_env_templates=True,
        validate_manifest_schema=False,
        allow_unknown_services=False,
        allow_outside_kx_root=True,
    )


def verify_extracted_capsule(
    capsule_root: str | Path,
    *,
    strict: bool = False,
    options: CapsuleVerificationOptions | None = None,
) -> CapsuleVerificationResult:
    """Verify an already-extracted Konnaxion Capsule directory."""

    root = Path(capsule_root)
    verifier = CapsuleVerifier(_compatibility_verifier_options(options))
    result = verifier.verify(root, strict=False)

    if result.input_type != CapsuleInputType.DIRECTORY:
        issue = ValidationIssue(
            code="not_extracted_capsule",
            message="Expected an extracted capsule directory.",
            field="capsule_path",
        )
        result = CapsuleVerificationResult(
            capsule_path=root,
            input_type=result.input_type,
            status=SecurityGateStatus.FAIL_BLOCKING,
            issues=(*result.issues, issue),
            manifest=result.manifest,
            services=result.services,
            checksum_count=result.checksum_count,
            signature_present=result.signature_present,
        )

    if strict and not result.passed:
        raise CapsuleVerificationError(result)

    return result


def verify_capsule_archive(
    capsule_path: str | Path,
    *,
    strict: bool = False,
    options: CapsuleVerificationOptions | None = None,
) -> CapsuleVerificationResult:
    """Verify a tar-compatible .kxcap archive."""

    path = Path(capsule_path)
    verifier = CapsuleVerifier(_compatibility_verifier_options(options))
    result = verifier.verify(path, strict=False)

    if result.input_type != CapsuleInputType.ARCHIVE:
        issue = ValidationIssue(
            code="not_capsule_archive",
            message="Expected a .kxcap archive file.",
            field="capsule_path",
        )
        result = CapsuleVerificationResult(
            capsule_path=path,
            input_type=result.input_type,
            status=SecurityGateStatus.FAIL_BLOCKING,
            issues=(*result.issues, issue),
            manifest=result.manifest,
            services=result.services,
            checksum_count=result.checksum_count,
            signature_present=result.signature_present,
        )

    if strict and not result.passed:
        raise CapsuleVerificationError(result)

    return result


def verify_capsule(
    capsule_path: str | Path,
    *,
    options: CapsuleVerificationOptions | None = None,
    strict: bool = False,
) -> CapsuleVerificationResult:
    """Convenience wrapper for verifying a capsule."""

    return CapsuleVerifier(options=options).verify(capsule_path, strict=strict)


def verify_capsule_or_raise(
    capsule_path: str | Path,
    *,
    options: CapsuleVerificationOptions | None = None,
) -> CapsuleVerificationResult:
    """Verify a capsule and raise CapsuleVerificationError on blocking issues."""

    return CapsuleVerifier(options=options).verify(capsule_path, strict=True)


def _safe_load_yaml_mapping(text: str) -> Mapping[str, Any]:
    """Load YAML as a mapping.

    PyYAML is used when available. A minimal fallback parser is provided for
    simple Konnaxion templates used in unit tests and early bootstrap flows.
    """

    try:
        import yaml  # type: ignore
    except ModuleNotFoundError:
        return _minimal_yaml_mapping(text)

    data = yaml.safe_load(text)
    return data if data is not None else {}


def _minimal_yaml_mapping(text: str) -> Mapping[str, Any]:
    """Very small YAML subset parser for dependency-light bootstrap use.

    Supports:
    - top-level key: scalar
    - top-level key: [inline, list]
    - top-level key: followed by indented mapping
    - top-level key: followed by indented dash list

    This is not a general YAML parser. Production builds should include PyYAML.
    """

    root: dict[str, Any] = {}
    current_key: str | None = None

    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        indent = len(raw_line) - len(raw_line.lstrip(" "))

        if indent == 0:
            if ":" not in stripped:
                continue

            key, value = stripped.split(":", maxsplit=1)
            key = key.strip()
            value = value.strip()
            current_key = key

            if value == "":
                root[key] = {}
            elif value.startswith("[") and value.endswith("]"):
                root[key] = [
                    item.strip().strip('"').strip("'")
                    for item in value[1:-1].split(",")
                    if item.strip()
                ]
            else:
                root[key] = _coerce_scalar(value)
            continue

        if current_key is None:
            continue

        if stripped.startswith("- "):
            if not isinstance(root.get(current_key), list):
                root[current_key] = []
            root[current_key].append(_coerce_scalar(stripped[2:].strip()))
            continue

        if ":" in stripped:
            if not isinstance(root.get(current_key), dict):
                root[current_key] = {}
            child_key, child_value = stripped.split(":", maxsplit=1)
            root[current_key][child_key.strip()] = _coerce_scalar(child_value.strip())

    return root
    
def _profile_name_from_mapping(
    data: Mapping[str, Any],
    *,
    default: str,
) -> str:
    """Return canonical profile name from either flat or nested profile YAML."""

    profile = data.get("profile")

    if isinstance(profile, Mapping):
        value = profile.get("name") or profile.get("id") or profile.get("profile")
        return str(value or default).strip()

    if profile not in (None, ""):
        return str(profile).strip()

    network_profile = data.get("network_profile")

    if isinstance(network_profile, Mapping):
        value = (
            network_profile.get("name")
            or network_profile.get("id")
            or network_profile.get("profile")
        )
        return str(value or default).strip()

    if network_profile not in (None, ""):
        return str(network_profile).strip()

    canonical_env = data.get("canonical_env")
    if isinstance(canonical_env, Mapping):
        value = canonical_env.get("KX_NETWORK_PROFILE")
        if value not in (None, ""):
            return str(value).strip()

    return default

def _coerce_scalar(value: str) -> Any:
    value = value.strip()

    if value in {"true", "True"}:
        return True

    if value in {"false", "False"}:
        return False

    if value in {"null", "None", "~"}:
        return None

    if value.startswith('"') and value.endswith('"'):
        return value[1:-1]

    if value.startswith("'") and value.endswith("'"):
        return value[1:-1]

    try:
        return int(value)
    except ValueError:
        return value


__all__ = [
    "CapsuleInputType",
    "CapsuleVerificationOptions",
    "CapsuleVerificationResult",
    "CapsuleVerificationError",
    "CapsuleVerifier",
    "verify_capsule",
    "verify_capsule_or_raise",
    "verify_extracted_capsule",
    "verify_capsule_archive",
]