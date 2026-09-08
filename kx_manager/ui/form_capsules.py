"""Capsule and build form models for the Konnaxion Capsule Manager GUI."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from kx_manager.defaults import DEFAULT_NETWORK_PROFILE
from kx_manager.ui.form_constants import (
    DEFAULT_CAPSULE_ID,
    DEFAULT_CAPSULE_OUTPUT_DIR,
    DEFAULT_CAPSULE_VERSION,
    DEFAULT_CHANNEL,
    DEFAULT_INSTANCE_ID,
    DEFAULT_SOURCE_DIR,
)
from kx_manager.ui.form_errors import FormValidationError
from kx_manager.ui.form_helpers import (
    _bool,
    _capsule_file,
    _capsule_id,
    _capsule_output_dir,
    _capsule_version,
    _computed_capsule_file,
    _existing_dir,
    _instance_id,
    _network_profile,
    _payload,
    _text,
    normalize_form_data,
)


DEFAULT_SIGNING_KEY_FILE = Path(
    r"C:\mycode\Konnaxion\runtime\signing\kx-demo-ed25519-private.pem"
)
DEFAULT_PUBLIC_KEY_FILE = Path(
    r"C:\mycode\Konnaxion\runtime\signing\kx-demo-ed25519-public.pem"
)


def _existing_capsule_file_for_action(
    data: Mapping[str, Any],
    *keys: str,
    action_label: str,
    field: str = "capsule_file",
) -> Path:
    """Return an existing capsule file for verify/import-style actions.

    If the user did not submit a capsule path, fall back to the Manager's
    canonical default capsule path. Missing files produce an operator-facing
    form error instead of letting the service layer emit raw JSON.
    """

    capsule_file = _capsule_file(
        data,
        *keys,
        required=False,
        must_exist=False,
        field=field,
    )

    if capsule_file is None:
        capsule_file = _computed_capsule_file(
            DEFAULT_CAPSULE_OUTPUT_DIR,
            _capsule_id(data, default=DEFAULT_CAPSULE_ID),
        )

    if not capsule_file.exists():
        raise FormValidationError(
            (
                f"{field} does not exist: {capsule_file}. "
                "Build a capsule first, or choose an existing .kxcap file "
                f"before running {action_label}."
            ),
            field=field,
        )

    if not capsule_file.is_file():
        raise FormValidationError(
            f"{field} must be a file: {capsule_file}",
            field=field,
        )

    return capsule_file


def _optional_existing_file(
    data: Mapping[str, Any],
    *keys: str,
    default: Path | str | None = None,
    required: bool = False,
    field: str,
) -> Path | None:
    """Return an optional existing file path from submitted GUI data."""

    raw_value: Any = None

    for key in keys:
        value = data.get(key)
        if value not in (None, ""):
            raw_value = value
            break

    if raw_value in (None, ""):
        raw_value = default

    if raw_value in (None, ""):
        if required:
            raise FormValidationError(
                f"{field} is required.",
                field=field,
            )
        return None

    path = Path(str(raw_value)).expanduser().resolve()

    if not path.exists():
        raise FormValidationError(
            f"{field} does not exist: {path}",
            field=field,
        )

    if not path.is_file():
        raise FormValidationError(
            f"{field} must be a file: {path}",
            field=field,
        )

    return path


def _build_network_profile(data: Mapping[str, Any]) -> Any:
    """Return build network profile while accepting legacy `profile` payloads."""

    if data.get("network_profile") in (None, "") and data.get("profile") not in (
        None,
        "",
    ):
        data = {**dict(data), "network_profile": data["profile"]}

    return _network_profile(data)


@dataclass(frozen=True, slots=True)
class BuildCapsuleForm:
    source_dir: Path
    capsule_output_dir: Path
    capsule_id: str
    capsule_version: str
    capsule_file: Path
    channel: str = DEFAULT_CHANNEL
    network_profile: Any = DEFAULT_NETWORK_PROFILE
    signing_key_file: Path | None = DEFAULT_SIGNING_KEY_FILE
    public_key_file: Path | None = DEFAULT_PUBLIC_KEY_FILE
    force: bool = True
    delete_existing: bool = False
    verify_after_build: bool = False

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "BuildCapsuleForm":
        source_dir = _existing_dir(
            data,
            "source_dir",
            default=DEFAULT_SOURCE_DIR,
            field="source_dir",
        )
        capsule_output_dir = _capsule_output_dir(data)
        capsule_id = _capsule_id(data)
        capsule_file = _capsule_file(
            data,
            "capsule_file",
            "output",
            required=False,
            must_exist=False,
            field="capsule_file",
        ) or _computed_capsule_file(capsule_output_dir, capsule_id)

        channel = _text(
            data,
            "channel",
            default=DEFAULT_CHANNEL,
            required=True,
            field="channel",
        )
        assert channel is not None

        signing_key_file = _optional_existing_file(
            data,
            "signing_key_file",
            "KX_BUILDER_SIGNING_KEY_FILE",
            default=DEFAULT_SIGNING_KEY_FILE,
            required=True,
            field="signing_key_file",
        )

        public_key_file = _optional_existing_file(
            data,
            "public_key_file",
            "KX_BUILDER_PUBLIC_KEY_FILE",
            default=DEFAULT_PUBLIC_KEY_FILE,
            required=False,
            field="public_key_file",
        )

        return cls(
            source_dir=source_dir,
            capsule_output_dir=capsule_output_dir,
            capsule_id=capsule_id,
            capsule_version=_capsule_version(data),
            capsule_file=capsule_file,
            channel=channel,
            network_profile=_build_network_profile(data),
            signing_key_file=signing_key_file,
            public_key_file=public_key_file,
            force=_bool(data, "force", default=True),
            delete_existing=_bool(data, "delete_existing", default=False),
            verify_after_build=_bool(data, "verify_after_build", default=False),
        )

    def to_payload(self) -> dict[str, Any]:
        return _payload(self)


@dataclass(frozen=True, slots=True)
class VerifyCapsuleForm:
    capsule_file: Path
    public_key_file: Path | None = DEFAULT_PUBLIC_KEY_FILE

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "VerifyCapsuleForm":
        return cls(
            capsule_file=_existing_capsule_file_for_action(
                data,
                "capsule_file",
                "capsule_path",
                "path",
                action_label="Verify Capsule",
            ),
            public_key_file=_optional_existing_file(
                data,
                "public_key_file",
                "KX_BUILDER_PUBLIC_KEY_FILE",
                default=DEFAULT_PUBLIC_KEY_FILE,
                required=False,
                field="public_key_file",
            ),
        )

    def to_payload(self) -> dict[str, Any]:
        return _payload(self)


@dataclass(frozen=True, slots=True)
class ImportCapsuleForm:
    capsule_file: Path
    instance_id: str
    network_profile: Any

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "ImportCapsuleForm":
        return cls(
            capsule_file=_existing_capsule_file_for_action(
                data,
                "capsule_file",
                "capsule_path",
                action_label="Import Capsule",
            ),
            instance_id=_instance_id(data, default=DEFAULT_INSTANCE_ID),
            network_profile=_network_profile(data),
        )

    def to_payload(self) -> dict[str, Any]:
        return _payload(self)


@dataclass(frozen=True, slots=True)
class CapsuleLookupForm:
    capsule_id: str
    capsule_file: Path | None = None

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "CapsuleLookupForm":
        return cls(
            capsule_id=_capsule_id(data, default=DEFAULT_CAPSULE_ID),
            capsule_file=_capsule_file(
                data,
                "capsule_file",
                "capsule_path",
                required=False,
                must_exist=False,
                field="capsule_file",
            ),
        )

    def to_payload(self) -> dict[str, Any]:
        return _payload(self)


def parse_build_form(data: Mapping[str, Any]) -> BuildCapsuleForm:
    return BuildCapsuleForm.from_mapping(normalize_form_data(data))


def parse_verify_capsule_form(data: Mapping[str, Any]) -> VerifyCapsuleForm:
    return VerifyCapsuleForm.from_mapping(normalize_form_data(data))


def parse_import_capsule_form(data: Mapping[str, Any]) -> ImportCapsuleForm:
    return ImportCapsuleForm.from_mapping(normalize_form_data(data))


def parse_capsule_lookup_form(data: Mapping[str, Any]) -> CapsuleLookupForm:
    return CapsuleLookupForm.from_mapping(normalize_form_data(data))


__all__ = [
    "BuildCapsuleForm",
    "CapsuleLookupForm",
    "DEFAULT_PUBLIC_KEY_FILE",
    "DEFAULT_SIGNING_KEY_FILE",
    "ImportCapsuleForm",
    "VerifyCapsuleForm",
    "parse_build_form",
    "parse_capsule_lookup_form",
    "parse_import_capsule_form",
    "parse_verify_capsule_form",
]