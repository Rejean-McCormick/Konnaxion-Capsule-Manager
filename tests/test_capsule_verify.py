"""
Tests for Konnaxion Capsule verification.

These tests focus on the verification contract that every `.kxcap` must satisfy:
- required root files exist
- images.yaml exists at the capsule root
- required runtime image archives exist
- manifests contain Security Gate required fields
- checksums are deterministic and strict
- checksum paths cannot escape the capsule root
- tampered files are detected
- secret-bearing files are rejected by verification policy
- placeholder signatures are rejected by strict verification
- imported capsules are extracted into the deterministic capsule work directory
- verifier integration is exercised when `kx_agent.capsules.verifier` exists

The tests intentionally avoid Docker, network, and privileged Agent operations.
"""

from __future__ import annotations

import importlib
import json
import tarfile
from pathlib import Path
from typing import Any, Mapping

import pytest

from kx_agent.capsules.checksums import (
    CHECKSUM_FILENAME,
    SIGNATURE_FILENAME,
    ChecksumEntry,
    InvalidChecksumFileError,
    UnsafeChecksumPathError,
    build_checksum_entries,
    format_checksums,
    normalize_relative_path,
    parse_checksums_text,
    sha256_file,
    verify_capsule_checksums,
    write_checksums_file,
)


CAPSULE_ID = "konnaxion-v14-demo-2026.04.30"
CAPSULE_VERSION = "2026.04.30-demo.1"
APP_NAME = "Konnaxion"
APP_VERSION = "v14"
PARAM_VERSION = "kx-param-2026.04.30"
MANIFEST_SCHEMA_VERSION = "kx-capsule-manifest/v1"

REQUIRED_CAPSULE_ROOT_FILES = (
    "manifest.yaml",
    "docker-compose.capsule.yml",
    "images.yaml",
    "checksums.txt",
    "signature.sig",
)

REQUIRED_CAPSULE_ROOT_DIRS = (
    "images",
    "profiles",
    "env-templates",
    "migrations",
    "healthchecks",
    "policies",
    "metadata",
)

REQUIRED_IMAGE_SERVICES = (
    "traefik",
    "frontend-next",
    "django-api",
    "postgres",
    "redis",
    "celeryworker",
    "celerybeat",
    "media-nginx",
)

REQUIRED_IMAGE_ARCHIVES = tuple(
    f"images/{service}.oci.tar" for service in REQUIRED_IMAGE_SERVICES
)

APP_IMAGE_ARCHIVES = (
    "images/frontend-next.oci.tar",
    "images/django-api.oci.tar",
)

EXTERNAL_RUNTIME_IMAGE_ARCHIVES = (
    "images/traefik.oci.tar",
    "images/postgres.oci.tar",
    "images/redis.oci.tar",
    "images/media-nginx.oci.tar",
)

DJANGO_ALIAS_IMAGE_ARCHIVES = (
    "images/celeryworker.oci.tar",
    "images/celerybeat.oci.tar",
)

SECURITY_GATE_REQUIRED_MANIFEST_FIELDS = (
    "schema_version",
    "app_name",
    "app_version",
    "capsule_id",
    "capsule_version",
    "channel",
)

CANONICAL_PROFILE_FILES = (
    "local_only.yaml",
    "intranet_private.yaml",
    "private_tunnel.yaml",
    "public_temporary.yaml",
    "public_vps.yaml",
    "offline.yaml",
)

FORBIDDEN_SECRET_FILES = (
    ".env",
    "backend/.env",
    "frontend/.env.local",
    "secrets/postgres_password.txt",
    "keys/id_rsa",
    "certs/private.key",
)

PLACEHOLDER_SIGNATURE_MARKERS = (
    "signature placeholder",
    "development-placeholder",
    "unsigned-development",
    "placeholder",
)


@pytest.fixture()
def capsule_root(tmp_path: Path) -> Path:
    """Create a minimal extracted Konnaxion Capsule layout.

    This fixture is valid for checksum/layout/import tests, but intentionally
    contains a placeholder signature so strict verifier tests can prove that
    placeholder signatures are rejected.

    The image archives are deterministic non-empty placeholders. They are not
    real Docker images because these tests intentionally avoid Docker.
    """

    root = tmp_path / "capsule"
    root.mkdir()

    for directory in REQUIRED_CAPSULE_ROOT_DIRS:
        (root / directory).mkdir(parents=True)

    _write_manifest(root)
    _write_compose(root)
    _write_profile_files(root)
    _write_env_templates(root)
    _write_auxiliary_files(root)
    _write_required_image_archives(root)
    _write_images_yaml(root)

    write_checksums_file(root)
    (root / SIGNATURE_FILENAME).write_text(
        "signature placeholder\n",
        encoding="utf-8",
    )

    return root


def _write_manifest(root: Path) -> None:
    manifest_lines = [
        f"schema_version: {MANIFEST_SCHEMA_VERSION}",
        f"app_name: {APP_NAME}",
        f"app_version: {APP_VERSION}",
        f"param_version: {PARAM_VERSION}",
        f"capsule_id: {CAPSULE_ID}",
        f"capsule_version: {CAPSULE_VERSION}",
        "channel: demo",
        "profile: public_vps",
        "package:",
        "  extension: .kxcap",
        "  format: tar+zstd",
        "  signed: true",
        "runtime:",
        "  compose_file: docker-compose.capsule.yml",
        "  images_dir: images",
        "profiles:",
        "  - local_only",
        "  - intranet_private",
        "  - private_tunnel",
        "  - public_temporary",
        "  - public_vps",
        "  - offline",
        "services:",
    ]

    for service in REQUIRED_IMAGE_SERVICES:
        manifest_lines.append(f"  - {service}")

    manifest_lines.extend(
        [
            "images:",
            "  traefik:",
            "    image: traefik:v3.1",
            "    archive: images/traefik.oci.tar",
            "  frontend-next:",
            "    image: konnaxion/frontend-next:v14",
            "    archive: images/frontend-next.oci.tar",
            "  django-api:",
            "    image: konnaxion/django-api:v14",
            "    archive: images/django-api.oci.tar",
            "  postgres:",
            "    image: postgres:16",
            "    archive: images/postgres.oci.tar",
            "  redis:",
            "    image: redis:7",
            "    archive: images/redis.oci.tar",
            "  celeryworker:",
            "    image: konnaxion/django-api:v14",
            "    archive: images/celeryworker.oci.tar",
            "  celerybeat:",
            "    image: konnaxion/django-api:v14",
            "    archive: images/celerybeat.oci.tar",
            "  media-nginx:",
            "    image: nginx:stable",
            "    archive: images/media-nginx.oci.tar",
            "source:",
            "  source_dir: C:\\\\mycode\\\\Konnaxion\\\\Konnaxion",
        ]
    )

    (root / "manifest.yaml").write_text(
        "\n".join(manifest_lines) + "\n",
        encoding="utf-8",
    )


def _write_compose(root: Path) -> None:
    (root / "docker-compose.capsule.yml").write_text(
        "\n".join(
            [
                "services:",
                "  traefik:",
                "    image: traefik:v3.1",
                "    ports:",
                '      - "80:80"',
                '      - "443:443"',
                "  frontend-next:",
                "    image: konnaxion/frontend-next:v14",
                "    expose:",
                '      - "3000"',
                "  django-api:",
                "    image: konnaxion/django-api:v14",
                "    expose:",
                '      - "5000"',
                "  postgres:",
                "    image: postgres:16",
                "    expose:",
                '      - "5432"',
                "  redis:",
                "    image: redis:7",
                "    expose:",
                '      - "6379"',
                "  celeryworker:",
                "    image: konnaxion/django-api:v14",
                "  celerybeat:",
                "    image: konnaxion/django-api:v14",
                "  media-nginx:",
                "    image: nginx:stable",
                "    expose:",
                '      - "80"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def _write_profile_files(root: Path) -> None:
    profile_modes = {
        "local_only": ("private", "false"),
        "intranet_private": ("private", "false"),
        "private_tunnel": ("private_tunnel", "false"),
        "public_temporary": ("temporary_tunnel", "true"),
        "public_vps": ("public", "true"),
        "offline": ("offline", "false"),
    }

    for profile_name in CANONICAL_PROFILE_FILES:
        profile = profile_name.removesuffix(".yaml")
        exposure_mode, public_enabled = profile_modes[profile]

        (root / "profiles" / profile_name).write_text(
            "\n".join(
                [
                    "schema_version: kx-network-profile/v1",
                    f"profile: {profile}",
                    f"default: {'true' if profile == 'intranet_private' else 'false'}",
                    f"exposure_mode: {exposure_mode}",
                    f"public_mode_enabled: {public_enabled}",
                ]
            )
            + "\n",
            encoding="utf-8",
        )


def _write_env_templates(root: Path) -> None:
    (root / "env-templates" / "django.env.template").write_text(
        "\n".join(
            [
                "DJANGO_SECRET_KEY=<GENERATED_ON_INSTALL>",
                "DATABASE_URL=<GENERATED_ON_INSTALL>",
                "DJANGO_ALLOWED_HOSTS=<GENERATED_FROM_PROFILE>",
                "CSRF_TRUSTED_ORIGINS=<GENERATED_FROM_PROFILE>",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (root / "env-templates" / "postgres.env.template").write_text(
        "\n".join(
            [
                "POSTGRES_USER=konnaxion",
                "POSTGRES_PASSWORD=<GENERATED_ON_INSTALL>",
                "POSTGRES_DB=konnaxion",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (root / "env-templates" / "redis.env.template").write_text(
        "REDIS_URL=redis://redis:6379/0\n",
        encoding="utf-8",
    )
    (root / "env-templates" / "frontend.env.template").write_text(
        "\n".join(
            [
                "NEXT_PUBLIC_API_BASE=<GENERATED_FROM_PROFILE>",
                "NEXT_PUBLIC_BACKEND_BASE=<GENERATED_FROM_PROFILE>",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def _write_auxiliary_files(root: Path) -> None:
    (root / "healthchecks" / "capsule-healthcheck.json").write_text(
        json.dumps(
            {
                "routes": ["/", "/api/", "/admin/", "/media/"],
                "services": [
                    "frontend-next",
                    "django-api",
                    "media-nginx",
                ],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    (root / "policies" / "capsule-policy.json").write_text(
        json.dumps(
            {
                "required": True,
                "forbid_secrets": True,
                "require_signature": True,
                "require_images": True,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    (root / "metadata" / "build.json").write_text(
        json.dumps(
            {
                "builder": "kx-builder",
                "app_version": APP_VERSION,
                "capsule_id": CAPSULE_ID,
                "capsule_version": CAPSULE_VERSION,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    (root / "metadata" / "source-inventory.json").write_text(
        json.dumps({"files": []}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (root / "migrations" / "README.json").write_text(
        json.dumps({"required": False}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (root / "images" / "README.json").write_text(
        json.dumps(
            {
                "note": "Image archives in this test fixture are deterministic placeholders.",
                "required_archives": list(REQUIRED_IMAGE_ARCHIVES),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def _write_required_image_archives(root: Path) -> None:
    """Write deterministic non-empty image archive placeholders.

    These are not real Docker images. The tests intentionally avoid Docker.
    Builder verification should require that the capsule includes the required
    image archive members and checksums them; loading/running those archives
    belongs to integration tests.
    """

    payloads = {
        "images/traefik.oci.tar": b"fake oci archive: traefik\n",
        "images/frontend-next.oci.tar": b"fake oci archive: frontend-next\n",
        "images/django-api.oci.tar": b"fake oci archive: django-api\n",
        "images/postgres.oci.tar": b"fake oci archive: postgres\n",
        "images/redis.oci.tar": b"fake oci archive: redis\n",
        "images/celeryworker.oci.tar": b"fake oci archive: celeryworker\n",
        "images/celerybeat.oci.tar": b"fake oci archive: celerybeat\n",
        "images/media-nginx.oci.tar": b"fake oci archive: media-nginx\n",
    }

    for relative_path, content in payloads.items():
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def _write_images_yaml(root: Path) -> None:
    """Write root-level images.yaml metadata for required runtime archives."""

    image_names = {
        "traefik": "traefik:v3.1",
        "frontend-next": "konnaxion/frontend-next:v14",
        "django-api": "konnaxion/django-api:v14",
        "postgres": "postgres:16",
        "redis": "redis:7",
        "celeryworker": "konnaxion/django-api:v14",
        "celerybeat": "konnaxion/django-api:v14",
        "media-nginx": "nginx:stable",
    }

    lines = ["generated_at: '2026-04-30T00:00:00+00:00'", "images:"]

    for service in REQUIRED_IMAGE_SERVICES:
        archive = root / "images" / f"{service}.oci.tar"
        lines.extend(
            [
                f"  - service: {service}",
                f"    image: {image_names[service]}",
                f"    archive: {service}.oci.tar",
                f"    sha256: {sha256_file(archive)}",
                f"    size_bytes: {archive.stat().st_size}",
                "    exported_at: '2026-04-30T00:00:00+00:00'",
            ]
        )

    (root / "images.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_minimal_capsule_fixture_has_required_layout(capsule_root: Path) -> None:
    for relative_path in REQUIRED_CAPSULE_ROOT_FILES:
        assert (capsule_root / relative_path).exists(), relative_path

    for relative_path in REQUIRED_CAPSULE_ROOT_DIRS:
        assert (capsule_root / relative_path).is_dir(), relative_path

    for profile_file in CANONICAL_PROFILE_FILES:
        assert (capsule_root / "profiles" / profile_file).is_file(), profile_file


def test_minimal_capsule_fixture_has_required_image_archives(
    capsule_root: Path,
) -> None:
    for relative_path in REQUIRED_IMAGE_ARCHIVES:
        path = capsule_root / relative_path
        assert path.is_file(), relative_path
        assert path.stat().st_size > 0, relative_path


def test_minimal_capsule_fixture_has_images_yaml(capsule_root: Path) -> None:
    text = (capsule_root / "images.yaml").read_text(encoding="utf-8")

    for service in REQUIRED_IMAGE_SERVICES:
        assert f"service: {service}" in text
        assert f"archive: {service}.oci.tar" in text


def test_capsule_manifest_contains_security_gate_required_fields(
    capsule_root: Path,
) -> None:
    manifest = _parse_top_level_yaml_scalars(capsule_root / "manifest.yaml")

    for field_name in SECURITY_GATE_REQUIRED_MANIFEST_FIELDS:
        assert manifest.get(field_name), field_name

    assert manifest["schema_version"] == MANIFEST_SCHEMA_VERSION
    assert manifest["app_name"] == APP_NAME
    assert manifest["app_version"] == APP_VERSION
    assert manifest["capsule_id"] == CAPSULE_ID
    assert manifest["capsule_version"] == CAPSULE_VERSION
    assert manifest["channel"] == "demo"


def test_sha256_file_is_deterministic(tmp_path: Path) -> None:
    file_path = tmp_path / "data.txt"
    file_path.write_text("konnaxion\n", encoding="utf-8")

    first = sha256_file(file_path)
    second = sha256_file(file_path)

    assert first == second
    assert len(first) == 64


def test_parse_checksums_text_accepts_sha256sum_format() -> None:
    text = (
        "# comment\n"
        + ("a" * 64)
        + "  manifest.yaml\n"
        + ("b" * 64)
        + " *docker-compose.capsule.yml\n"
    )

    entries = parse_checksums_text(text)

    assert entries == (
        ChecksumEntry(relative_path="manifest.yaml", sha256="a" * 64),
        ChecksumEntry(relative_path="docker-compose.capsule.yml", sha256="b" * 64),
    )


def test_parse_checksums_text_rejects_invalid_digest() -> None:
    with pytest.raises(InvalidChecksumFileError):
        parse_checksums_text("not-a-digest  manifest.yaml\n")


def test_parse_checksums_text_rejects_duplicate_paths() -> None:
    text = (
        ("a" * 64)
        + "  manifest.yaml\n"
        + ("b" * 64)
        + "  manifest.yaml\n"
    )

    with pytest.raises(InvalidChecksumFileError):
        parse_checksums_text(text)


@pytest.mark.parametrize(
    "relative_path",
    [
        "../manifest.yaml",
        "/etc/passwd",
        "metadata/../../secret",
        "",
        ".",
        "./manifest.yaml",
        "metadata/./build.json",
        "metadata/././secret",
    ],
)
def test_checksum_paths_must_be_safe(relative_path: str) -> None:
    with pytest.raises(UnsafeChecksumPathError):
        normalize_relative_path(relative_path)


def test_build_checksum_entries_excludes_checksum_and_signature(
    capsule_root: Path,
) -> None:
    entries = build_checksum_entries(capsule_root)
    paths = {entry.relative_path for entry in entries}

    assert CHECKSUM_FILENAME not in paths
    assert SIGNATURE_FILENAME not in paths
    assert "manifest.yaml" in paths
    assert "docker-compose.capsule.yml" in paths
    assert "images.yaml" in paths


def test_build_checksum_entries_includes_required_image_archives(
    capsule_root: Path,
) -> None:
    entries = build_checksum_entries(capsule_root)
    paths = {entry.relative_path for entry in entries}

    for relative_path in REQUIRED_IMAGE_ARCHIVES:
        assert relative_path in paths


def test_format_checksums_is_deterministic() -> None:
    entries = (
        ChecksumEntry(relative_path="z.txt", sha256="f" * 64),
        ChecksumEntry(relative_path="a.txt", sha256="a" * 64),
    )

    assert format_checksums(entries).splitlines() == [
        ("a" * 64) + "  a.txt",
        ("f" * 64) + "  z.txt",
    ]


def test_verify_capsule_checksums_passes_for_untampered_capsule(
    capsule_root: Path,
) -> None:
    report = verify_capsule_checksums(capsule_root)

    assert report.ok is True
    assert report.failure_count == 0
    assert report.missing == ()
    assert report.extra == ()
    assert report.mismatched == ()


def test_verify_capsule_checksums_detects_tampering(capsule_root: Path) -> None:
    (capsule_root / "manifest.yaml").write_text("tampered: true\n", encoding="utf-8")

    report = verify_capsule_checksums(capsule_root)

    assert report.ok is False
    assert report.mismatched
    assert report.mismatched[0].relative_path == "manifest.yaml"


def test_verify_capsule_checksums_detects_missing_file(capsule_root: Path) -> None:
    (capsule_root / "metadata" / "build.json").unlink()

    report = verify_capsule_checksums(capsule_root)

    assert report.ok is False
    assert "metadata/build.json" in report.missing


def test_verify_capsule_checksums_detects_missing_images_yaml(
    capsule_root: Path,
) -> None:
    (capsule_root / "images.yaml").unlink()

    report = verify_capsule_checksums(capsule_root)

    assert report.ok is False
    assert "images.yaml" in report.missing


@pytest.mark.parametrize("relative_path", REQUIRED_IMAGE_ARCHIVES)
def test_verify_capsule_checksums_detects_missing_required_image_archive(
    capsule_root: Path,
    relative_path: str,
) -> None:
    (capsule_root / relative_path).unlink()

    report = verify_capsule_checksums(capsule_root)

    assert report.ok is False
    assert relative_path in report.missing


def test_verify_capsule_checksums_detects_extra_file(capsule_root: Path) -> None:
    (capsule_root / "metadata" / "extra.json").write_text("{}", encoding="utf-8")

    report = verify_capsule_checksums(capsule_root)

    assert report.ok is False
    assert "metadata/extra.json" in report.extra


def test_verify_capsule_checksums_can_allow_extra_files(capsule_root: Path) -> None:
    (capsule_root / "metadata" / "extra.json").write_text("{}", encoding="utf-8")

    report = verify_capsule_checksums(capsule_root, allow_extra_files=True)

    assert report.ok is True
    assert report.extra == ()


def test_placeholder_signature_fixture_is_not_cryptographic(
    capsule_root: Path,
) -> None:
    signature_text = (capsule_root / SIGNATURE_FILENAME).read_text(encoding="utf-8")

    assert _contains_placeholder_signature_marker(signature_text)


def test_capsule_archive_contains_manifest_at_root(
    capsule_root: Path,
    tmp_path: Path,
) -> None:
    archive_path = _write_tar_capsule(capsule_root, tmp_path / f"{CAPSULE_ID}.kxcap")

    with tarfile.open(archive_path, "r:*") as tar:
        names = set(tar.getnames())

    assert "manifest.yaml" in names
    assert "docker-compose.capsule.yml" in names
    assert "images.yaml" in names
    assert "checksums.txt" in names
    assert "signature.sig" in names

    for relative_path in REQUIRED_IMAGE_ARCHIVES:
        assert relative_path in names

    assert all(not name.startswith("./") for name in names)
    assert all("../" not in name for name in names)


def test_import_capsule_prepares_extract_dir_with_manifest_and_checksums(
    monkeypatch: pytest.MonkeyPatch,
    capsule_root: Path,
    tmp_path: Path,
) -> None:
    """Regression coverage for Droplet deploy.

    `capsule.import` must not merely copy the `.kxcap`; it must also prepare
    the deterministic extracted capsule directory used by runtime compose and
    Security Gate.
    """

    importer = import_optional_module("kx_agent.capsules.importer")
    if importer is None:
        pytest.skip("kx_agent.capsules.importer is not available.")

    package = import_optional_module("kx_builder.package")
    if package is None:
        pytest.skip("kx_builder.package unavailable for TAR+Zstandard import test")
    from kx_builder.package import CompressionUnavailableError

    uncompressed_tar = _write_tar_capsule(capsule_root, tmp_path / "capsule-import.tar")
    try:
        archive_path = package.compress_tar_to_kxcap(
            uncompressed_tar, tmp_path / f"{CAPSULE_ID}.kxcap"
        )
    except CompressionUnavailableError:
        pytest.skip("TAR+Zstandard compression unavailable in test environment")

    storage_root = tmp_path / "storage"
    capsules_storage = storage_root / "capsules"
    extracts_storage = storage_root / "shared" / "capsules"

    monkeypatch.setattr(
        importer,
        "capsule_file",
        lambda filename: capsules_storage / str(filename),
    )
    monkeypatch.setattr(
        importer,
        "capsule_extract_dir",
        lambda capsule_id: extracts_storage / str(capsule_id),
    )
    # Redirect only this test's filesystem to a disposable canonical root.
    # Preserve the real under-root guard (with an explicit test root), rather
    # than disabling the path security boundary or using /opt/konnaxion.
    from kx_agent.artifacts import registry
    from kx_shared import paths as canonical_paths

    real_assert_under_root = canonical_paths.assert_under_root
    with pytest.raises(canonical_paths.KonnaxionPathError):
        real_assert_under_root(storage_root.parent / "outside", root=storage_root)

    def test_assert_under_root(path: str | Path) -> Path:
        return real_assert_under_root(path, root=storage_root)

    def test_ensure_dir(path: str | Path, *, mode: int = 0o750) -> Path:
        safe_path = test_assert_under_root(path)
        safe_path.mkdir(parents=True, exist_ok=True)
        safe_path.chmod(mode)
        return safe_path

    for module in (canonical_paths, importer, registry):
        monkeypatch.setattr(module, "assert_under_root", test_assert_under_root)
        monkeypatch.setattr(module, "ensure_dir", test_ensure_dir)
    monkeypatch.setattr(registry, "artifact_registry_dir", lambda: storage_root / "shared" / "registry")
    monkeypatch.setattr(
        registry,
        "artifact_registry_file",
        lambda: storage_root / "shared" / "registry" / "installed-artifacts.json",
    )

    options = importer.CapsuleImportOptions(
        verify=False,
        overwrite=True,
        prepare_extract_dir=True,
        capsule_id=CAPSULE_ID,
    )

    result = importer.import_capsule(archive_path, options)
    data = _result_to_mapping(result)
    extract_dir = Path(str(data["extract_dir"]))

    assert data["capsule_id"] == CAPSULE_ID
    assert Path(str(data["stored_path"])).is_file()
    assert extract_dir.is_dir()
    assert (extract_dir / "manifest.yaml").is_file()
    assert (extract_dir / "docker-compose.capsule.yml").is_file()
    assert (extract_dir / "images.yaml").is_file()
    assert (extract_dir / "checksums.txt").is_file()

    for relative_path in REQUIRED_IMAGE_ARCHIVES:
        assert (extract_dir / relative_path).is_file()

    manifest = _parse_top_level_yaml_scalars(extract_dir / "manifest.yaml")
    assert manifest["schema_version"] == MANIFEST_SCHEMA_VERSION
    assert manifest["capsule_id"] == CAPSULE_ID
    assert manifest["app_name"] == APP_NAME
    assert manifest["app_version"] == APP_VERSION


def test_builder_package_requires_signing_key_when_signing(
    capsule_root: Path,
) -> None:
    """Signed builds must not silently fall back to placeholder signatures.

    The package layer should either produce a real cryptographic signature from
    a provided signing key or fail clearly when signing was requested without
    a key.
    """

    package = import_optional_module("kx_builder.package")
    if package is None:
        pytest.skip("kx_builder.package is not available.")

    write_signature_file = getattr(package, "_write_signature_file", None)
    if write_signature_file is None:
        pytest.skip("kx_builder.package._write_signature_file is not available.")

    with pytest.raises(Exception) as exc_info:
        write_signature_file(
            capsule_root,
            capsule_id=CAPSULE_ID,
            capsule_version=CAPSULE_VERSION,
            sign=True,
        )

    assert _value_mentions_any(
        exc_info.value,
        (
            "signing key",
            "signing_key",
            "KX_BUILDER_SIGNING_KEY_FILE",
            "no signing key",
        ),
    )


@pytest.mark.parametrize("relative_path", FORBIDDEN_SECRET_FILES)
def test_capsule_policy_rejects_secret_bearing_paths(
    capsule_root: Path,
    relative_path: str,
) -> None:
    """Verification policy must reject secret-like files inside a capsule."""

    secret_file = capsule_root / relative_path
    secret_file.parent.mkdir(parents=True, exist_ok=True)
    secret_file.write_text("SECRET=value\n", encoding="utf-8")
    write_checksums_file(capsule_root)

    verifier = import_optional_verifier()

    if verifier is None:
        pytest.skip("kx_agent.capsules.verifier is not generated yet.")

    verify_func = getattr(verifier, "verify_extracted_capsule", None)

    if verify_func is None:
        pytest.skip("verify_extracted_capsule is not implemented yet.")

    result = verify_func(capsule_root)

    _assert_result_not_ok(result)
    assert _result_mentions_any(
        result,
        (
            "secret",
            "forbidden",
            "private.key",
            ".env",
            "id_rsa",
            "postgres_password",
            "FAIL_BLOCKING",
        ),
    )


def test_optional_capsule_verifier_rejects_placeholder_signature(
    capsule_root: Path,
) -> None:
    """Full verifier must not accept a placeholder signature as valid."""

    verifier = import_optional_verifier()

    if verifier is None:
        pytest.skip("kx_agent.capsules.verifier is not generated yet.")

    verify_func = getattr(verifier, "verify_extracted_capsule", None)

    if verify_func is None:
        pytest.skip("verify_extracted_capsule is not implemented yet.")

    result = verify_func(capsule_root)

    _assert_result_not_ok(result)
    assert _result_mentions_any(
        result,
        (
            "signature",
            "cryptographic",
            "placeholder",
            "unsigned",
            "FAIL_BLOCKING",
        ),
    )


def test_optional_capsule_archive_verifier_rejects_placeholder_signature(
    capsule_root: Path,
    tmp_path: Path,
) -> None:
    """Archive-level verifier must reject placeholder-signed .kxcap files.

    This fixture uses a tar-compatible archive so the test remains network-free
    and Docker-free.
    """

    archive_path = _write_tar_capsule(capsule_root, tmp_path / f"{CAPSULE_ID}.kxcap")

    verifier = import_optional_verifier()

    if verifier is None:
        pytest.skip("kx_agent.capsules.verifier is not generated yet.")

    verify_func = getattr(verifier, "verify_capsule_archive", None)

    if verify_func is None:
        pytest.skip("verify_capsule_archive is not implemented yet.")

    result = verify_func(archive_path)

    _assert_result_not_ok(result)
    assert _result_mentions_any(
        result,
        (
            "signature",
            "cryptographic",
            "placeholder",
            "unsigned",
            "FAIL_BLOCKING",
        ),
    )


def test_builder_verify_reports_placeholder_signature_as_issue(
    capsule_root: Path,
    tmp_path: Path,
) -> None:
    """Builder verifier must surface placeholder signatures as a verification issue."""

    archive_path = _write_tar_capsule(capsule_root, tmp_path / f"{CAPSULE_ID}.kxcap")

    builder_verify = import_optional_module("kx_builder.verify")
    if builder_verify is None:
        pytest.skip("kx_builder.verify is not available.")

    verify_func = getattr(builder_verify, "verify_capsule_file", None)
    if verify_func is None:
        pytest.skip("kx_builder.verify.verify_capsule_file is not implemented.")

    result = verify_func(archive_path, strict=False)

    assert _result_mentions_any(
        result,
        (
            "signature_not_cryptographically_verified",
            "signature",
            "cryptographic",
            "placeholder",
            "unsigned",
        ),
    )


def test_builder_verify_rejects_capsule_missing_images_yaml(
    capsule_root: Path,
    tmp_path: Path,
) -> None:
    """Root images.yaml is required for the corrected Builder image contract."""

    (capsule_root / "images.yaml").unlink()
    write_checksums_file(capsule_root)
    archive_path = _write_tar_capsule(capsule_root, tmp_path / f"{CAPSULE_ID}.kxcap")

    result = _run_builder_verify(archive_path)

    _assert_result_not_ok(result)
    assert _result_mentions_any(
        result,
        (
            "images.yaml",
            "missing_required_file",
            "missing",
            "image metadata",
        ),
    )


def test_builder_verify_rejects_capsule_with_no_image_archives(
    capsule_root: Path,
    tmp_path: Path,
) -> None:
    """Regression coverage for the broken Droplet capsule.

    A capsule whose images/ directory contains only README.json must not verify,
    because a clean VPS cannot start frontend/backend containers from it.
    """

    for image_archive in REQUIRED_IMAGE_ARCHIVES:
        path = capsule_root / image_archive
        if path.exists():
            path.unlink()

    # Keep the original images.yaml inventory: the archive was removed on purpose.
    # Recomputing its digest would fail in the fixture before reaching the verifier.
    write_checksums_file(capsule_root)
    archive_path = _write_tar_capsule(capsule_root, tmp_path / f"{CAPSULE_ID}.kxcap")

    result = _run_builder_verify(archive_path)

    _assert_result_not_ok(result)
    assert _result_mentions_any(
        result,
        (
            "missing_image_archives",
            "missing_required_image_archive",
            "images/*.oci.tar",
            "frontend-next",
            "django-api",
            "postgres",
            "redis",
            "celeryworker",
            "celerybeat",
            "media-nginx",
        ),
    )


@pytest.mark.parametrize("relative_path", REQUIRED_IMAGE_ARCHIVES)
def test_builder_verify_rejects_capsule_missing_required_image_archive(
    capsule_root: Path,
    tmp_path: Path,
    relative_path: str,
) -> None:
    """Every required runtime image archive must be present."""

    (capsule_root / relative_path).unlink()
    # Keep the original images.yaml inventory: the archive was removed on purpose.
    # Recomputing its digest would fail in the fixture before reaching the verifier.
    write_checksums_file(capsule_root)
    archive_path = _write_tar_capsule(capsule_root, tmp_path / f"{CAPSULE_ID}.kxcap")

    result = _run_builder_verify(archive_path)

    _assert_result_not_ok(result)
    service = Path(relative_path).name.removesuffix(".oci.tar")
    assert _result_mentions_any(
        result,
        (
            "missing_required_image_archive",
            service,
            relative_path,
        ),
    )


@pytest.mark.parametrize("relative_path", REQUIRED_IMAGE_ARCHIVES)
def test_builder_verify_rejects_image_archive_not_listed_in_checksums(
    capsule_root: Path,
    tmp_path: Path,
    relative_path: str,
) -> None:
    """Every image archive must be covered by checksums.txt."""

    write_checksums_file(capsule_root)

    checksums_path = capsule_root / CHECKSUM_FILENAME
    filtered_lines = [
        line
        for line in checksums_path.read_text(encoding="utf-8").splitlines()
        if relative_path not in line
    ]
    checksums_path.write_text("\n".join(filtered_lines) + "\n", encoding="utf-8")

    archive_path = _write_tar_capsule(capsule_root, tmp_path / f"{CAPSULE_ID}.kxcap")

    result = _run_builder_verify(archive_path)

    _assert_result_not_ok(result)
    assert _result_mentions_any(
        result,
        (
            "image_archive_not_checksummed",
            "checksum_missing_member",
            Path(relative_path).name,
            relative_path,
        ),
    )


def test_builder_verify_rejects_images_yaml_missing_required_service(
    capsule_root: Path,
    tmp_path: Path,
) -> None:
    """images.yaml must list every required runtime service."""

    images_yaml = capsule_root / "images.yaml"
    text = images_yaml.read_text(encoding="utf-8")
    filtered_lines = [
        line
        for line in text.splitlines()
        if "service: celeryworker" not in line
        and "archive: celeryworker.oci.tar" not in line
    ]
    images_yaml.write_text("\n".join(filtered_lines) + "\n", encoding="utf-8")

    write_checksums_file(capsule_root)
    archive_path = _write_tar_capsule(capsule_root, tmp_path / f"{CAPSULE_ID}.kxcap")

    result = _run_builder_verify(archive_path)

    _assert_result_not_ok(result)
    assert _result_mentions_any(
        result,
        (
            "images.yaml",
            "celeryworker",
            "missing",
            "image metadata",
        ),
    )


def import_optional_verifier() -> Any | None:
    return import_optional_module("kx_agent.capsules.verifier")


def import_optional_module(module_name: str) -> Any | None:
    try:
        return importlib.import_module(module_name)
    except ModuleNotFoundError:
        return None


def _run_builder_verify(archive_path: Path) -> Any:
    builder_verify = import_optional_module("kx_builder.verify")
    if builder_verify is None:
        pytest.skip("kx_builder.verify is not available.")

    verify_func = getattr(builder_verify, "verify_capsule_file", None)
    if verify_func is None:
        pytest.skip("kx_builder.verify.verify_capsule_file is not implemented.")

    return verify_func(archive_path, strict=False)


def _write_tar_capsule(capsule_root: Path, archive_path: Path) -> Path:
    with tarfile.open(archive_path, "w") as tar:
        for path in sorted(capsule_root.rglob("*")):
            tar.add(path, arcname=path.relative_to(capsule_root).as_posix())

    return archive_path


def _parse_top_level_yaml_scalars(path: Path) -> dict[str, str]:
    """Parse simple top-level YAML scalar fields without requiring PyYAML."""

    result: dict[str, str] = {}

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue

        if raw_line.startswith((" ", "\t")):
            continue

        if ":" not in raw_line:
            continue

        key, value = raw_line.split(":", 1)
        key = key.strip()
        value = value.strip().strip("\"'")

        if key and value:
            result[key] = value

    return result


def _contains_placeholder_signature_marker(value: str) -> bool:
    normalized = value.lower()
    return any(marker in normalized for marker in PLACEHOLDER_SIGNATURE_MARKERS)


def _assert_result_not_ok(result: Any) -> None:
    data = _result_to_mapping(result)

    if "ok" in data:
        assert data["ok"] is False
        return

    if "valid" in data:
        assert data["valid"] is False
        return

    if "accepted" in data:
        assert data["accepted"] is False
        return

    status = str(data.get("status", "")).upper()
    if status:
        assert status not in {"PASS", "PASSED", "VALID", "VERIFIED", "OK"}
        return

    pytest.fail(f"Verifier returned unsupported result shape: {result!r}")


def _result_mentions_any(result: Any, needles: tuple[str, ...]) -> bool:
    rendered = json.dumps(_json_safe(_result_to_mapping(result)), default=str).lower()
    return any(needle.lower() in rendered for needle in needles)


def _value_mentions_any(value: Any, needles: tuple[str, ...]) -> bool:
    rendered = str(value).lower()
    return any(needle.lower() in rendered for needle in needles)


def _result_to_mapping(result: Any) -> Mapping[str, Any]:
    if isinstance(result, Mapping):
        return result

    to_dict = getattr(result, "to_dict", None)
    if callable(to_dict):
        value = to_dict()
        if isinstance(value, Mapping):
            return value

    model_dump = getattr(result, "model_dump", None)
    if callable(model_dump):
        value = model_dump()
        if isinstance(value, Mapping):
            return value

    data: dict[str, Any] = {}

    for key in (
        "ok",
        "valid",
        "accepted",
        "verified",
        "status",
        "errors",
        "warnings",
        "issues",
        "checks",
        "message",
        "security_status",
        "failure_count",
        "blocking_failures",
    ):
        if hasattr(result, key):
            data[key] = getattr(result, key)

    return data or {"result": repr(result)}


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}

    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]

    enum_value = getattr(value, "value", value)
    if enum_value is not value:
        return enum_value

    if isinstance(value, Path):
        return str(value)

    return value