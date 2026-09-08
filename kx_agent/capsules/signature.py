"""Capsule signature helpers for Konnaxion Agent.

This module verifies detached capsule signatures before import/startup.
The MVP format signs the canonical digest payload generated from
``manifest.yaml`` and ``checksums.txt``. The signature file is expected at
``signature.sig`` inside the extracted capsule directory.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, padding, rsa

from kx_shared.errors import CapsuleSignatureError
from kx_shared.konnaxion_constants import CAPSULE_EXTENSION


MANIFEST_FILENAME: Final[str] = "manifest.yaml"
CHECKSUMS_FILENAME: Final[str] = "checksums.txt"
SIGNATURE_FILENAME: Final[str] = "signature.sig"

SIGNATURE_VERSION: Final[str] = "kxcap-signature/v1"
SIGNATURE_ALGORITHM_ED25519: Final[str] = "ed25519"
SIGNATURE_ALGORITHM_RSA_PSS_SHA256: Final[str] = "rsa-pss-sha256"


@dataclass(frozen=True)
class SignaturePayload:
    """Canonical files covered by the detached capsule signature."""

    manifest_bytes: bytes
    checksums_bytes: bytes

    def canonical_bytes(self) -> bytes:
        """Return deterministic bytes used for signature verification."""

        return b"\n".join(
            (
                SIGNATURE_VERSION.encode("utf-8"),
                f"{MANIFEST_FILENAME}:".encode("utf-8"),
                self.manifest_bytes.rstrip(b"\n"),
                f"{CHECKSUMS_FILENAME}:".encode("utf-8"),
                self.checksums_bytes.rstrip(b"\n"),
                b"",
            )
        )


@dataclass(frozen=True)
class SignatureEnvelope:
    """Parsed detached signature metadata and bytes."""

    algorithm: str
    signature: bytes

    @classmethod
    def from_file(cls, signature_file: Path) -> "SignatureEnvelope":
        """Parse a ``signature.sig`` file.

        Supported formats:

        1. Raw base64 signature bytes.
        2. Simple key-value text format:

           ``algorithm=ed25519``
           ``signature=<base64>``
        """

        if not signature_file.exists():
            raise CapsuleSignatureError(f"Missing capsule signature file: {signature_file}")

        raw = signature_file.read_text(encoding="utf-8").strip()
        if not raw:
            raise CapsuleSignatureError(f"Empty capsule signature file: {signature_file}")

        if raw.startswith("{"):
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise CapsuleSignatureError(
                    f"Invalid JSON signature envelope in {signature_file}: {exc}"
                ) from exc
            if not isinstance(payload, dict):
                raise CapsuleSignatureError(
                    f"Signature JSON envelope must be an object: {signature_file}"
                )
            signature_text = payload.get("signature_base64") or payload.get("signature")
            if not signature_text:
                raise CapsuleSignatureError(
                    f"Missing signature_base64 in {signature_file}"
                )
            return cls(
                algorithm=str(payload.get("algorithm") or SIGNATURE_ALGORITHM_ED25519),
                signature=_decode_base64(str(signature_text), signature_file),
            )

        if "=" not in raw:
            return cls(
                algorithm=SIGNATURE_ALGORITHM_ED25519,
                signature=_decode_base64(raw, signature_file),
            )

        values: dict[str, str] = {}
        for line in raw.splitlines():
            clean = line.strip()
            if not clean or clean.startswith("#"):
                continue
            if "=" not in clean:
                raise CapsuleSignatureError(
                    f"Invalid signature metadata line in {signature_file}: {clean}"
                )
            key, value = clean.split("=", 1)
            values[key.strip()] = value.strip()

        algorithm = values.get("algorithm", SIGNATURE_ALGORITHM_ED25519)
        signature_text = values.get("signature")
        if not signature_text:
            raise CapsuleSignatureError(f"Missing signature value in {signature_file}")

        return cls(
            algorithm=algorithm,
            signature=_decode_base64(signature_text, signature_file),
        )


def build_signature_payload(capsule_dir: Path) -> SignaturePayload:
    """Load canonical capsule files covered by the signature."""

    manifest_file = capsule_dir / MANIFEST_FILENAME
    checksums_file = capsule_dir / CHECKSUMS_FILENAME

    if not manifest_file.exists():
        raise CapsuleSignatureError(f"Missing manifest file: {manifest_file}")
    if not checksums_file.exists():
        raise CapsuleSignatureError(f"Missing checksums file: {checksums_file}")

    return SignaturePayload(
        manifest_bytes=manifest_file.read_bytes(),
        checksums_bytes=checksums_file.read_bytes(),
    )


def verify_capsule_signature(
    capsule_dir: Path,
    public_key_file: Path,
) -> None:
    """Verify the detached capsule signature.

    Raises:
        CapsuleSignatureError: if the signature, payload, or public key is invalid.
    """

    capsule_dir = capsule_dir.resolve()
    public_key_file = public_key_file.resolve()

    if not capsule_dir.exists() or not capsule_dir.is_dir():
        raise CapsuleSignatureError(f"Capsule directory does not exist: {capsule_dir}")

    signature_file = capsule_dir / SIGNATURE_FILENAME
    raw_signature = signature_file.read_text(encoding="utf-8").lstrip()

    # Current Builder writes the canonical kx-signature/v1 JSON envelope.
    # Delegate JSON verification to the Builder signing implementation so the
    # Agent and Builder use the exact same signing payload contract.
    if raw_signature.startswith("{"):
        try:
            from kx_builder.signature import verify_capsule_root_with_public_key_file

            result = verify_capsule_root_with_public_key_file(
                capsule_dir,
                public_key_file,
                require_signature=True,
            )
        except Exception as exc:
            raise CapsuleSignatureError(
                f"Capsule signature verification failed: {exc}"
            ) from exc
        if not bool(getattr(result, "valid", False)):
            raise CapsuleSignatureError(
                str(getattr(result, "message", "Capsule signature verification failed."))
            )
        return

    # Legacy raw/key-value signature format.
    payload = build_signature_payload(capsule_dir)
    envelope = SignatureEnvelope.from_file(signature_file)
    public_key = load_public_key(public_key_file)

    try:
        if isinstance(public_key, ed25519.Ed25519PublicKey):
            if envelope.algorithm != SIGNATURE_ALGORITHM_ED25519:
                raise CapsuleSignatureError(
                    "Signature algorithm does not match Ed25519 public key."
                )
            public_key.verify(envelope.signature, payload.canonical_bytes())
            return

        if isinstance(public_key, rsa.RSAPublicKey):
            if envelope.algorithm != SIGNATURE_ALGORITHM_RSA_PSS_SHA256:
                raise CapsuleSignatureError(
                    "Signature algorithm does not match RSA public key."
                )
            public_key.verify(
                envelope.signature,
                payload.canonical_bytes(),
                padding.PSS(
                    mgf=padding.MGF1(hashes.SHA256()),
                    salt_length=padding.PSS.MAX_LENGTH,
                ),
                hashes.SHA256(),
            )
            return

    except InvalidSignature as exc:
        raise CapsuleSignatureError("Capsule signature verification failed.") from exc

    raise CapsuleSignatureError(
        f"Unsupported public key type for capsule signature: {type(public_key).__name__}"
    )


def load_public_key(public_key_file: Path) -> ed25519.Ed25519PublicKey | rsa.RSAPublicKey:
    """Load an Ed25519 or RSA public key from PEM."""

    if not public_key_file.exists():
        raise CapsuleSignatureError(f"Missing public key file: {public_key_file}")

    try:
        key = serialization.load_pem_public_key(public_key_file.read_bytes())
    except ValueError as exc:
        raise CapsuleSignatureError(f"Invalid public key file: {public_key_file}") from exc

    if isinstance(key, ed25519.Ed25519PublicKey | rsa.RSAPublicKey):
        return key

    raise CapsuleSignatureError(
        f"Unsupported public key type: {type(key).__name__}"
    )


def validate_capsule_file_extension(capsule_file: Path) -> None:
    """Reject non-canonical capsule filenames before extraction."""

    if capsule_file.suffix != CAPSULE_EXTENSION:
        raise CapsuleSignatureError(
            f"Capsule file must use {CAPSULE_EXTENSION} extension: {capsule_file}"
        )


def _decode_base64(value: str, source_file: Path) -> bytes:
    """Decode strict base64 signature data."""

    try:
        return base64.b64decode(value.encode("ascii"), validate=True)
    except ValueError as exc:
        raise CapsuleSignatureError(f"Invalid base64 signature in {source_file}") from exc
