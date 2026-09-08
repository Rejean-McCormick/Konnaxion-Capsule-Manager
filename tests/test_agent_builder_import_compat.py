from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from kx_agent.capsules.verifier import CapsuleVerificationOptions, verify_extracted_capsule
from kx_builder.package import _stage_capsule_from_source
from kx_builder.signature import generate_ed25519_keypair_pem
from kx_shared.validation import validate_manifest, validate_no_real_secrets_in_template


CURRENT_MANIFEST = {
    "schema_version": "kx-capsule-manifest/v1",
    "app_name": "Konnaxion",
    "app_version": "v14",
    "param_version": "kx-param-2026.04.30",
    "capsule_id": "konnaxion-v14-local-2026.09.08",
    "capsule_version": "2026.09.08-local.1",
    "channel": "local",
    "profile": "local_only",
    "profiles": [
        "local_only",
        "intranet_private",
        "private_tunnel",
        "public_temporary",
        "public_vps",
        "offline",
    ],
    "created_at": "2026-09-08T18:25:18+00:00",
    "package": {"format": "tar+zstd", "extension": ".kxcap", "signed": True},
    "runtime": {
        "compose_file": "docker-compose.capsule.yml",
        "images_dir": "images",
        "image_metadata": "images.yaml",
        "images": [
            {"service": service}
            for service in (
                "frontend-next",
                "django-api",
                "traefik",
                "postgres",
                "redis",
                "celeryworker",
                "celerybeat",
                "media-nginx",
            )
        ],
    },
}


def test_shared_manifest_validator_accepts_current_builder_contract() -> None:
    assert validate_manifest(CURRENT_MANIFEST) == []


def test_env_template_validator_accepts_runtime_variable_reference() -> None:
    assert validate_no_real_secrets_in_template({"DATABASE_URL": "${DATABASE_URL}"}) == []


def test_agent_verifier_accepts_builder_signed_staging(tmp_path: Path) -> None:
    source = tmp_path / "source"
    staging = tmp_path / "staging"
    source.mkdir()
    staging.mkdir()

    # Deliberately do not provide docker-compose.capsule.yml in the source tree.
    # The Builder must stage its canonical Agent-compatible template.
    private_pem, public_pem = generate_ed25519_keypair_pem()
    private_key = tmp_path / "private.pem"
    public_key = tmp_path / "public.pem"
    private_key.write_bytes(private_pem)
    public_key.write_bytes(public_pem)

    _stage_capsule_from_source(
        source,
        staging,
        channel="local",
        capsule_id="konnaxion-v14-local-2026.09.08",
        capsule_version="2026.09.08-local.1",
        profile="local_only",
        sign=True,
        build_images=False,
        signing_key_file=private_key,
    )

    report = verify_extracted_capsule(
        staging,
        options=CapsuleVerificationOptions(
            trusted_public_key_file=public_key,
            allow_outside_kx_root=True,
        ),
    )

    assert report.passed is True
    assert report.status.value == "PASS"
    assert report.issues == ()
    assert report.to_dict()["issues"] == []


def test_kx_root_environment_override_is_used_by_shared_paths(tmp_path: Path) -> None:
    env = os.environ.copy()
    env["KX_ROOT"] = str(tmp_path / "runtime")
    repo_root = Path(__file__).resolve().parents[1]
    env["PYTHONPATH"] = str(repo_root)

    code = (
        "from kx_shared.konnaxion_constants import KX_ROOT, KX_CAPSULES_DIR; "
        "print(str(KX_ROOT)); print(str(KX_CAPSULES_DIR))"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    lines = completed.stdout.strip().splitlines()

    expected_root = str(tmp_path / "runtime").replace("\\", "/")
    assert lines[0] == expected_root
    assert lines[1] == expected_root + "/capsules"
