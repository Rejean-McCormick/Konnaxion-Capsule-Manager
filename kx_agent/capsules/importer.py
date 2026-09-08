"""
Konnaxion Capsule import workflow.

Responsibilities:
- Validate that the selected file is a Konnaxion Capsule (`.kxcap`).
- Derive or validate the capsule id.
- Copy the capsule into canonical storage:
  /opt/konnaxion/capsules/<CAPSULE_ID>.kxcap
- Optionally run capsule verification before accepting the import.
- Extract the capsule into canonical extracted storage:
  /opt/konnaxion/shared/capsules/<CAPSULE_ID>
- Return a typed import result for Manager/API/UI layers.

This module must not:
- Invent paths outside kx_shared.paths.
- Trust filenames without validation.
- Generate secrets.
- Start containers.
- Open network ports.
- Modify instance state directly.

The Agent owns this workflow because capsule import affects trusted runtime
artifacts used later by instance creation/update.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

from kx_shared.errors import CapsuleImportError as SharedCapsuleImportError
from kx_shared.paths import (
    assert_under_root,
    capsule_extract_dir,
    capsule_file,
    ensure_dir,
    normalize_capsule_filename,
    validate_safe_id,
)

try:
    from kx_shared.konnaxion_constants import CAPSULE_EXTENSION
except ImportError:  # pragma: no cover - defensive fallback during early scaffolding
    CAPSULE_EXTENSION = ".kxcap"


REQUIRED_EXTRACTED_ROOT_FILES = frozenset(
    {
        "manifest.yaml",
        "docker-compose.capsule.yml",
        "checksums.txt",
        "signature.sig",
    }
)


class CapsuleImportError(SharedCapsuleImportError):
    """Raised when a capsule cannot be imported safely."""


class CapsuleAlreadyExistsError(CapsuleImportError):
    """Raised when the target capsule already exists and overwrite is disabled."""


class CapsuleVerificationUnavailableError(CapsuleImportError):
    """Raised when verification is required but the verifier module is unavailable."""


class CapsuleExtractionError(CapsuleImportError):
    """Raised when a capsule cannot be extracted into canonical storage."""


@dataclass(frozen=True)
class CapsuleImportOptions:
    """
    Options for importing a Konnaxion Capsule.

    verify:
        Run kx_agent.capsules.verifier before accepting the imported file.
    overwrite:
        Replace an existing capsule with the same capsule id.
    prepare_extract_dir:
        Extract the capsule into the deterministic capsule work/extract directory.
    capsule_id:
        Optional explicit capsule id. If omitted, it is derived from filename.
    """

    verify: bool = True
    overwrite: bool = False
    prepare_extract_dir: bool = True
    capsule_id: str | None = None


@dataclass(frozen=True)
class CapsuleImportResult:
    """Serializable result returned by the capsule import workflow."""

    capsule_id: str
    source_path: str
    stored_path: str
    extract_dir: str
    sha256: str
    size_bytes: int
    imported_at: str
    imported: bool = True
    verified: bool = False
    extracted: bool = False
    verification_report: Mapping[str, Any] | None = None
    artifact_id: str | None = None
    artifact_kind: str | None = None
    registry_generation: int | None = None
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def utc_now_iso() -> str:
    """Return a stable UTC timestamp for audit/import records."""

    return datetime.now(UTC).isoformat(timespec="seconds")


def resolve_source_path(source_path: str | Path) -> Path:
    """
    Resolve and validate the source capsule path.

    Source capsules may live outside /opt/konnaxion before import, because they
    can be selected by the user from downloads, removable media, or a build dir.
    """

    path = Path(source_path).expanduser().resolve(strict=False)

    if not path.exists():
        raise CapsuleImportError(f"capsule source does not exist: {path}")

    if not path.is_file():
        raise CapsuleImportError(f"capsule source is not a file: {path}")

    if path.suffix != CAPSULE_EXTENSION:
        raise CapsuleImportError(
            f"capsule file must end with {CAPSULE_EXTENSION}; got {path.name!r}"
        )

    return path


def derive_capsule_id(
    source_path: str | Path,
    explicit_capsule_id: str | None = None,
) -> str:
    """Derive the capsule id from an explicit value or from the `.kxcap` filename."""

    if explicit_capsule_id is not None:
        return validate_safe_id(explicit_capsule_id, field_name="capsule_id")

    source = Path(source_path)
    if source.name.endswith(CAPSULE_EXTENSION):
        capsule_id = source.name[: -len(CAPSULE_EXTENSION)]
    else:
        capsule_id = source.stem

    return validate_safe_id(capsule_id, field_name="capsule_id")


def sha256_file(path: str | Path, *, chunk_size: int = 1024 * 1024) -> str:
    """Compute SHA-256 for a capsule file without loading it all into memory."""

    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_copy_file(source: Path, destination: Path, *, overwrite: bool = False) -> Path:
    """
    Atomically copy a file into place.

    The temporary file is created in the destination directory so os.replace()
    stays atomic on the same filesystem.
    """

    destination = assert_under_root(destination)
    ensure_dir(destination.parent)

    if destination.exists() and not overwrite:
        raise CapsuleAlreadyExistsError(
            f"capsule already exists at {destination}; use overwrite=True to replace it"
        )

    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=str(destination.parent),
    )

    tmp_path = Path(tmp_name)

    try:
        with os.fdopen(fd, "wb") as tmp_handle:
            with source.open("rb") as src_handle:
                shutil.copyfileobj(src_handle, tmp_handle, length=1024 * 1024)
            tmp_handle.flush()
            os.fsync(tmp_handle.fileno())

        os.replace(tmp_path, destination)
        return destination
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        finally:
            raise


def _verification_report_to_mapping(report: Any) -> Mapping[str, Any]:
    """
    Normalize verifier output into a serializable mapping.

    Supported verifier return styles:
    - dataclass-like object with to_dict()
    - plain dict/mapping
    - object with accepted/valid/status attributes
    """

    if report is None:
        return {}

    if hasattr(report, "to_dict") and callable(report.to_dict):
        value = report.to_dict()
        if isinstance(value, Mapping):
            return value

    if isinstance(report, Mapping):
        return report

    normalized: dict[str, Any] = {}
    for name in ("accepted", "valid", "verified", "status", "errors", "warnings"):
        if hasattr(report, name):
            normalized[name] = getattr(report, name)

    return normalized or {"result": str(report)}


def _verification_passed(report: Mapping[str, Any]) -> bool:
    """
    Interpret common verifier report shapes.

    Missing explicit failure fields are treated as pass only when a positive
    field is present.
    """

    for key in ("accepted", "valid", "verified"):
        if key in report:
            return bool(report[key])

    status = str(report.get("status", "")).upper()
    if status in {"PASS", "PASSED", "VALID", "VERIFIED", "OK"}:
        return True

    if status in {"FAIL", "FAILED", "FAIL_BLOCKING", "INVALID", "ERROR"}:
        return False

    errors = report.get("errors")
    if isinstance(errors, (list, tuple, set)):
        return len(errors) == 0

    return False


def run_capsule_verifier(capsule_path: Path) -> tuple[bool, Mapping[str, Any]]:
    """
    Run the capsule verifier.

    The verifier is imported lazily so this importer can be developed and tested
    before kx_agent.capsules.verifier.py exists.
    """

    try:
        from .verifier import verify_capsule  # type: ignore
    except ImportError as exc:
        raise CapsuleVerificationUnavailableError(
            "capsule verification is required, but kx_agent.capsules.verifier "
            "is not available yet"
        ) from exc

    report = verify_capsule(capsule_path)
    normalized = _verification_report_to_mapping(report)
    return _verification_passed(normalized), normalized


def _extract_capsule_archive(capsule_path: Path, destination: Path) -> None:
    """Extract a `.kxcap` archive into an empty destination directory."""

    try:
        from kx_builder.package import extract_capsule
    except ImportError as exc:
        raise CapsuleExtractionError(
            "capsule extraction requires kx_builder.package.extract_capsule"
        ) from exc

    try:
        extract_capsule(capsule_path, destination, overwrite=True)
    except Exception as exc:
        raise CapsuleExtractionError(
            f"could not extract capsule {capsule_path} into {destination}: {exc}"
        ) from exc


def _validate_extracted_capsule_dir(extract_dir: Path) -> None:
    """Validate minimum extracted capsule layout needed by runtime flows."""

    missing = sorted(
        filename
        for filename in REQUIRED_EXTRACTED_ROOT_FILES
        if not (extract_dir / filename).is_file()
    )

    if missing:
        raise CapsuleExtractionError(
            "extracted capsule is missing required root files: "
            + ", ".join(missing)
        )


def _replace_directory_with_backup(source_dir: Path, destination_dir: Path) -> None:
    """
    Replace destination_dir with source_dir while preserving the old directory
    until replacement succeeds.

    This is not a perfect cross-filesystem atomic directory swap, but both paths
    are created under the same parent, and the old extraction is restored if the
    final move fails.
    """

    destination_dir = assert_under_root(destination_dir)
    ensure_dir(destination_dir.parent)

    backup_dir: Path | None = None

    if destination_dir.exists():
        backup_dir = destination_dir.parent / (
            f".{destination_dir.name}.backup."
            f"{os.getpid()}."
            f"{utc_now_iso().replace(':', '-')}"
        )
        if backup_dir.exists():
            shutil.rmtree(backup_dir)
        shutil.move(str(destination_dir), str(backup_dir))

    try:
        shutil.move(str(source_dir), str(destination_dir))
    except Exception:
        if backup_dir is not None and backup_dir.exists() and not destination_dir.exists():
            shutil.move(str(backup_dir), str(destination_dir))
        raise
    else:
        if backup_dir is not None and backup_dir.exists():
            shutil.rmtree(backup_dir, ignore_errors=True)


def extract_capsule_to_work_dir(capsule_path: Path, capsule_id: str) -> Path:
    """
    Extract a stored capsule into its canonical work directory.

    Extraction happens into a temporary sibling directory first. The final
    extracted directory is only replaced after extraction and basic layout
    validation succeed.
    """

    work_dir = assert_under_root(capsule_extract_dir(capsule_id))
    ensure_dir(work_dir.parent)

    temp_dir = Path(
        tempfile.mkdtemp(
            prefix=f".{work_dir.name}.extract.",
            dir=str(work_dir.parent),
        )
    )

    try:
        _extract_capsule_archive(capsule_path, temp_dir)
        _validate_extracted_capsule_dir(temp_dir)
        _replace_directory_with_backup(temp_dir, work_dir)
        return work_dir
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise


def import_capsule(
    source_path: str | Path,
    options: CapsuleImportOptions | None = None,
) -> CapsuleImportResult:
    """
    Import a Konnaxion Capsule into canonical capsule storage.

    Example:
        result = import_capsule(
            "/home/user/Downloads/konnaxion-v14-demo-2026.04.30.kxcap",
            CapsuleImportOptions(verify=True),
        )
    """

    options = options or CapsuleImportOptions()

    source = resolve_source_path(source_path)
    capsule_id = derive_capsule_id(source, options.capsule_id)

    canonical_filename = normalize_capsule_filename(capsule_id)
    target = capsule_file(canonical_filename)

    copied_path = atomic_copy_file(source, target, overwrite=options.overwrite)

    try:
        file_hash = sha256_file(copied_path)
        size_bytes = copied_path.stat().st_size

        verified = False
        extracted = False
        verification_report: Mapping[str, Any] | None = None
        warnings: list[str] = []

        if options.verify:
            verified, verification_report = run_capsule_verifier(copied_path)
            if not verified:
                if not options.overwrite:
                    copied_path.unlink(missing_ok=True)
                blocking_issues = list(verification_report.get("errors") or [])
                issue_summary = "; ".join(
                    str(item.get("message") or item.get("code") or item)
                    if isinstance(item, Mapping)
                    else str(item)
                    for item in blocking_issues[:8]
                )
                if not issue_summary:
                    issue_summary = str(
                        verification_report.get("status")
                        or "verification did not pass"
                    )
                raise CapsuleImportError(
                    f"capsule verification failed for {copied_path}: {issue_summary}",
                    details={
                        "capsule_path": str(copied_path),
                        "verification_report": dict(verification_report),
                    },
                )
        else:
            warnings.append("capsule verification skipped by options.verify=False")

        work_dir = capsule_extract_dir(capsule_id)

        if options.prepare_extract_dir:
            work_dir = extract_capsule_to_work_dir(copied_path, capsule_id)
            extracted = True
        else:
            ensure_dir(work_dir)
            warnings.append("capsule extraction skipped by options.prepare_extract_dir=False")

        artifact_entry: Mapping[str, Any] | None = None
        registry_generation: int | None = None
        if extracted:
            from kx_agent.artifacts.registry import load_registry, register_artifact_from_capsule

            artifact_entry = register_artifact_from_capsule(
                extract_dir=work_dir,
                capsule_id=capsule_id,
                capsule_path=copied_path,
                verified=verified,
                verification_report=verification_report,
            )
            registry_generation = int(load_registry().get("generation", 0))
        else:
            warnings.append("artifact registry update skipped because capsule extraction was skipped")

        return CapsuleImportResult(
            capsule_id=capsule_id,
            source_path=str(source),
            stored_path=str(copied_path),
            extract_dir=str(work_dir),
            sha256=file_hash,
            size_bytes=size_bytes,
            imported_at=utc_now_iso(),
            imported=True,
            verified=verified,
            extracted=extracted,
            verification_report=verification_report,
            artifact_id=(str(artifact_entry.get("id")) if artifact_entry else None),
            artifact_kind=(str(artifact_entry.get("kind")) if artifact_entry else None),
            registry_generation=registry_generation,
            warnings=tuple(warnings),
        )

    except Exception:
        if not options.overwrite:
            copied_path.unlink(missing_ok=True)

        raise


class CapsuleImporter:
    """Small service object used by Agent API handlers and tests."""

    def __init__(self, default_options: CapsuleImportOptions | None = None) -> None:
        self.default_options = default_options or CapsuleImportOptions()

    def import_file(
        self,
        source_path: str | Path,
        *,
        verify: bool | None = None,
        overwrite: bool | None = None,
        prepare_extract_dir: bool | None = None,
        capsule_id: str | None = None,
    ) -> CapsuleImportResult:
        options = CapsuleImportOptions(
            verify=self.default_options.verify if verify is None else verify,
            overwrite=self.default_options.overwrite if overwrite is None else overwrite,
            prepare_extract_dir=(
                self.default_options.prepare_extract_dir
                if prepare_extract_dir is None
                else prepare_extract_dir
            ),
            capsule_id=capsule_id,
        )
        return import_capsule(source_path, options)


__all__ = [
    "CapsuleAlreadyExistsError",
    "CapsuleExtractionError",
    "CapsuleImportError",
    "CapsuleImportOptions",
    "CapsuleImportResult",
    "CapsuleImporter",
    "CapsuleVerificationUnavailableError",
    "atomic_copy_file",
    "derive_capsule_id",
    "extract_capsule_to_work_dir",
    "import_capsule",
    "resolve_source_path",
    "run_capsule_verifier",
    "sha256_file",
    "utc_now_iso",
]
