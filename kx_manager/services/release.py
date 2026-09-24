"""One-click production release preparation for Capsule Manager.

This module owns local release-signing key lifecycle plus signed capsule build
and verification. Remote/bootstrap/deploy orchestration remains in
``operation_jobs`` so long-running work stays off the Manager HTTP request.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any, Mapping

from kx_builder.signature import (
    generate_ed25519_keypair_pem,
    load_private_key_pem,
    load_public_key_pem,
    public_key_fingerprint_sha256,
    public_key_from_private_key,
)
from kx_manager.defaults import (
    DEFAULT_CAPSULE_OUTPUT_DIR,
    DEFAULT_RUNTIME_ROOT,
    DEFAULT_SOURCE_DIR,
    make_default_capsule_id,
    make_default_capsule_version,
)
from kx_manager.services import builder

RELEASE_CHANNEL = "release"
RELEASE_PRIVATE_KEY_NAME = "release-ed25519-private.pem"
RELEASE_PUBLIC_KEY_NAME = "release-ed25519-public.pem"


def _path_text(value: Any, default: str) -> Path:
    text = str(value or "").strip() or default
    return Path(text).expanduser()


def release_signing_paths(payload: Mapping[str, Any] | None = None) -> tuple[Path, Path]:
    data = dict(payload or {})
    runtime_root = _path_text(
        data.get("local_runtime_root") or os.getenv("KX_ROOT"),
        DEFAULT_RUNTIME_ROOT,
    )
    signing_dir = runtime_root / "signing"
    return signing_dir / RELEASE_PRIVATE_KEY_NAME, signing_dir / RELEASE_PUBLIC_KEY_NAME


def ensure_release_signing_keys(payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Create or validate the persistent Ed25519 production signing pair.

    Existing keys are never overwritten. An incomplete pair fails closed rather
    than silently replacing trust material. The private key contents are never
    returned to callers or persisted in job state.
    """

    private_path, public_path = release_signing_paths(payload)
    private_exists = private_path.is_file()
    public_exists = public_path.is_file()

    if private_exists != public_exists:
        missing = public_path if private_exists else private_path
        raise RuntimeError(
            "Release signing keypair is incomplete; refusing to replace existing trust material. "
            f"Missing: {missing}"
        )

    created = False
    if not private_exists:
        private_path.parent.mkdir(parents=True, exist_ok=True)
        private_pem, public_pem = generate_ed25519_keypair_pem()
        private_tmp = private_path.with_suffix(private_path.suffix + ".tmp")
        public_tmp = public_path.with_suffix(public_path.suffix + ".tmp")
        private_tmp.write_bytes(private_pem)
        public_tmp.write_bytes(public_pem)
        try:
            os.chmod(private_tmp, 0o600)
            os.chmod(public_tmp, 0o644)
        except OSError:
            pass
        private_tmp.replace(private_path)
        public_tmp.replace(public_path)
        created = True

    private_key = load_private_key_pem(private_path)
    public_key = load_public_key_pem(public_path)
    private_fingerprint = public_key_fingerprint_sha256(
        public_key_from_private_key(private_key)
    )
    public_fingerprint = public_key_fingerprint_sha256(public_key)
    if private_fingerprint != public_fingerprint:
        raise RuntimeError(
            "Release signing private/public keys do not form the same Ed25519 keypair."
        )

    public_pem = public_path.read_bytes()
    return {
        "created": created,
        "private_key_file": str(private_path),
        "public_key_file": str(public_path),
        "public_key_fingerprint": public_fingerprint,
        "public_key_file_sha256": hashlib.sha256(public_pem).hexdigest(),
    }


def prepare_signed_release(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Generate/validate keys, build today's signed release capsule, and verify it."""

    data = dict(payload)
    keys = ensure_release_signing_keys(data)
    source_dir = _path_text(data.get("source_dir"), DEFAULT_SOURCE_DIR)
    output_dir = _path_text(data.get("capsule_output_dir"), DEFAULT_CAPSULE_OUTPUT_DIR)

    capsule_id = make_default_capsule_id(channel=RELEASE_CHANNEL)
    capsule_version = make_default_capsule_version(channel=RELEASE_CHANNEL)
    capsule_file = output_dir / f"{capsule_id}.kxcap"

    request = builder.BuildCapsuleRequest(
        source_dir=source_dir,
        capsule_output_dir=output_dir,
        capsule_file=capsule_file,
        capsule_id=capsule_id,
        capsule_version=capsule_version,
        channel=RELEASE_CHANNEL,
        network_profile="public_vps",
        force=True,
        signing_key_file=keys["private_key_file"],
        public_key_file=keys["public_key_file"],
    )
    built = builder.build_capsule(request)
    if not built.ok:
        detail = built.command.stderr or built.command.stdout or built.command.message
        raise RuntimeError(f"Production capsule build failed: {detail}")

    verified = builder.verify_capsule(
        builder.VerifyCapsuleRequest(
            capsule_file=built.capsule_file,
            public_key_file=keys["public_key_file"],
        )
    )
    if not verified.ok:
        detail = verified.command.stderr or verified.command.stdout or verified.command.message
        raise RuntimeError(f"Production capsule verification failed: {detail}")

    return {
        "capsule_id": capsule_id,
        "capsule_version": capsule_version,
        "capsule_file": str(built.capsule_file),
        "channel": RELEASE_CHANNEL,
        "network_profile": "public_vps",
        "public_key_file": keys["public_key_file"],
        "public_key_fingerprint": keys["public_key_fingerprint"],
        "public_key_file_sha256": keys["public_key_file_sha256"],
        "signing_keys_created": bool(keys["created"]),
        "build": built.to_dict(),
        "verify": verified.to_dict(),
    }


__all__ = [
    "RELEASE_CHANNEL",
    "RELEASE_PRIVATE_KEY_NAME",
    "RELEASE_PUBLIC_KEY_NAME",
    "ensure_release_signing_keys",
    "prepare_signed_release",
    "release_signing_paths",
]
