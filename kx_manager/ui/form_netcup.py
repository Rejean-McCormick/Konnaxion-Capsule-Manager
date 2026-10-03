"""Forms for fresh Netcup VPS verification and provisioning."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from kx_manager.defaults import (
    DEFAULT_DROPLET_DOMAIN,
    DEFAULT_DROPLET_HOST,
    DEFAULT_DROPLET_INSTANCE_ID,
    DEFAULT_DROPLET_NAME,
    DEFAULT_REMOTE_CAPSULE_DIR,
    DEFAULT_REMOTE_KX_ROOT,
    DEFAULT_SSH_KEY_PATH,
    DEFAULT_SSH_PORT,
)
from kx_manager.ui.form_errors import FormValidationError
from kx_manager.ui.form_helpers import _bool, _int, _path, _payload, _text, normalize_form_data

_FINGERPRINT_RE = re.compile(r"^SHA256:[A-Za-z0-9+/=]+$")
_USERNAME_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")


@dataclass(frozen=True, slots=True)
class NetcupHostScanForm:
    droplet_host: str
    ssh_port: int = DEFAULT_SSH_PORT
    action: str = "scan_netcup_host_key"

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "NetcupHostScanForm":
        normalized = normalize_form_data(data)
        host = _text(normalized, "droplet_host", "host", required=True, field="droplet_host")
        assert host is not None
        return cls(
            droplet_host=host,
            ssh_port=_int(normalized, "ssh_port", default=DEFAULT_SSH_PORT, minimum=1, maximum=65535),
        )

    def to_payload(self) -> dict[str, Any]:
        return _payload(self)


@dataclass(frozen=True, slots=True)
class ProvisionNetcupVpsForm:
    droplet_name: str
    droplet_host: str
    ssh_key_path: Path
    ssh_port: int
    admin_user: str
    ssh_host_fingerprint: str
    domain: str
    instance_id: str
    remote_kx_root: str
    remote_capsule_dir: str
    reinstalled_confirmed: bool
    confirmed: bool
    action: str = "provision_netcup_vps"

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "ProvisionNetcupVpsForm":
        normalized = normalize_form_data(data)
        host = _text(
            normalized,
            "droplet_host",
            "host",
            default=DEFAULT_DROPLET_HOST,
            required=True,
            field="droplet_host",
        )
        admin_user = _text(
            normalized,
            "admin_user",
            default="kx-admin",
            required=True,
            field="admin_user",
        )
        fingerprint = _text(
            normalized,
            "ssh_host_fingerprint",
            required=True,
            field="ssh_host_fingerprint",
        )
        assert host is not None and admin_user is not None and fingerprint is not None
        if not _USERNAME_RE.fullmatch(admin_user) or admin_user in {"root", "kx-agent"}:
            raise FormValidationError("admin_user must be a dedicated Linux account name.", field="admin_user")
        if not _FINGERPRINT_RE.fullmatch(fingerprint):
            raise FormValidationError("ssh_host_fingerprint must use SHA256:... format.", field="ssh_host_fingerprint")

        reinstalled = _bool(normalized, "reinstalled_confirmed", default=False)
        confirmed = _bool(normalized, "confirmed", default=False)
        if not reinstalled:
            raise FormValidationError("Confirm the Netcup disk format/reinstall first.", field="reinstalled_confirmed")
        if not confirmed:
            raise FormValidationError("Explicit Netcup provisioning confirmation is required.", field="confirmed")

        key = _path(
            normalized,
            "ssh_key_path",
            default=DEFAULT_SSH_KEY_PATH,
            required=True,
            must_exist=True,
            must_be_file=True,
            field="ssh_key_path",
        )
        assert key is not None

        return cls(
            droplet_name=_text(normalized, "droplet_name", default=DEFAULT_DROPLET_NAME, required=True, field="droplet_name") or DEFAULT_DROPLET_NAME,
            droplet_host=host,
            ssh_key_path=key,
            ssh_port=_int(normalized, "ssh_port", default=DEFAULT_SSH_PORT, minimum=1, maximum=65535),
            admin_user=admin_user,
            ssh_host_fingerprint=fingerprint,
            domain=_text(normalized, "domain", default=DEFAULT_DROPLET_DOMAIN, required=True, field="domain") or DEFAULT_DROPLET_DOMAIN,
            instance_id=_text(normalized, "instance_id", default=DEFAULT_DROPLET_INSTANCE_ID, required=True, field="instance_id") or DEFAULT_DROPLET_INSTANCE_ID,
            remote_kx_root=_text(normalized, "remote_kx_root", default=DEFAULT_REMOTE_KX_ROOT, required=True, field="remote_kx_root") or DEFAULT_REMOTE_KX_ROOT,
            remote_capsule_dir=_text(normalized, "remote_capsule_dir", default=DEFAULT_REMOTE_CAPSULE_DIR, required=True, field="remote_capsule_dir") or DEFAULT_REMOTE_CAPSULE_DIR,
            reinstalled_confirmed=reinstalled,
            confirmed=confirmed,
        )

    def to_payload(self) -> dict[str, Any]:
        return _payload(self)


__all__ = ["NetcupHostScanForm", "ProvisionNetcupVpsForm"]
