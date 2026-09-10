"""Operator-friendly defaults for the local Konnaxion Capsule Manager.

These defaults are deliberately scoped to the Manager/operator workflow. They
must not redefine immutable runtime metadata inside an already installed
Konnaxion Instance.
"""

from __future__ import annotations

import os
import re
from datetime import date
from pathlib import Path

from kx_shared.konnaxion_constants import APP_VERSION, CAPSULE_EXTENSION


DEFAULT_TARGET_MODE = "local"
DEFAULT_NETWORK_PROFILE = "local_only"
DEFAULT_EXPOSURE_MODE = "private"
DEFAULT_CHANNEL = "local"
DEFAULT_INSTANCE_ID = "demo-001"


def capsule_date_stamp(value: date | None = None) -> str:
    """Return the date segment used in operator-generated capsule names."""

    current = value or date.today()
    return current.strftime("%Y.%m.%d")


def make_default_capsule_id(
    *,
    value: date | None = None,
    channel: str = DEFAULT_CHANNEL,
) -> str:
    """Build the default capsule ID for a Manager session."""

    normalized_channel = str(channel).strip() or DEFAULT_CHANNEL
    return f"konnaxion-{APP_VERSION}-{normalized_channel}-{capsule_date_stamp(value)}"


def make_default_capsule_version(
    *,
    value: date | None = None,
    channel: str = DEFAULT_CHANNEL,
    revision: int = 1,
) -> str:
    """Build the default capsule version for a Manager session."""

    normalized_channel = str(channel).strip() or DEFAULT_CHANNEL
    normalized_revision = max(1, int(revision))
    return f"{capsule_date_stamp(value)}-{normalized_channel}.{normalized_revision}"


def _env_text(name: str, default: str) -> str:
    value = os.getenv(name, "").strip()
    return value or default


# Default public VPS target for this operator installation (Netcup).
# These affect Droplet/VPS forms only; local/intranet defaults stay unchanged.
DEFAULT_DROPLET_INSTANCE_ID = _env_text("KX_DROPLET_INSTANCE_ID", "konnaxion-prod")
DEFAULT_DROPLET_NAME = _env_text("KX_DROPLET_NAME", "netcup-vps")
DEFAULT_DROPLET_HOST = _env_text("KX_DROPLET_HOST", "2.56.97.41")
DEFAULT_DROPLET_USER = _env_text("KX_DROPLET_USER", "root")
DEFAULT_SSH_KEY_PATH = _env_text(
    "KX_SSH_KEY_PATH",
    r"C:\Users\rejea\.ssh\id_ed25519" if os.name == "nt" else str(Path.home() / ".ssh" / "id_ed25519"),
)
DEFAULT_SSH_PORT = int(_env_text("KX_SSH_PORT", "22"))
DEFAULT_REMOTE_KX_ROOT = _env_text("KX_REMOTE_KX_ROOT", "/opt/konnaxion")
DEFAULT_REMOTE_CAPSULE_DIR = _env_text(
    "KX_REMOTE_CAPSULE_DIR",
    "/opt/konnaxion/capsules",
)
DEFAULT_DROPLET_DOMAIN = _env_text(
    "KX_DROPLET_DOMAIN",
    "2.56.97.41.sslip.io",
)
DEFAULT_REMOTE_AGENT_URL = _env_text("KX_REMOTE_AGENT_URL", "")


DEFAULT_RUNTIME_ROOT = _env_text(
    "KX_ROOT",
    r"C:\mycode\Konnaxion\runtime" if os.name == "nt" else "/opt/konnaxion",
)
DEFAULT_SOURCE_DIR = _env_text(
    "KX_SOURCE_DIR",
    r"C:\mycode\Konnaxion\Konnaxion" if os.name == "nt" else "",
)
DEFAULT_CAPSULE_OUTPUT_DIR = _env_text(
    "KX_CAPSULE_OUTPUT_DIR",
    str(Path(DEFAULT_RUNTIME_ROOT) / "capsules"),
)

SESSION_CHANNEL = _env_text("KX_CHANNEL", DEFAULT_CHANNEL)
DEFAULT_CAPSULE_ID = _env_text(
    "KX_CAPSULE_ID",
    make_default_capsule_id(channel=SESSION_CHANNEL),
)
DEFAULT_CAPSULE_VERSION = _env_text(
    "KX_CAPSULE_VERSION",
    make_default_capsule_version(channel=SESSION_CHANNEL),
)
DEFAULT_CAPSULE_FILE = str(Path(DEFAULT_CAPSULE_OUTPUT_DIR) / f"{DEFAULT_CAPSULE_ID}{CAPSULE_EXTENSION}")


def latest_existing_capsule(output_dir: str | Path = DEFAULT_CAPSULE_OUTPUT_DIR) -> Path | None:
    """Return the newest existing local .kxcap file, if any.

    Operational forms should prefer a real artifact over a date-derived filename
    that has not been built yet.  Modification time wins because rebuilds may
    keep an older date in the capsule ID.
    """

    root = Path(output_dir).expanduser()
    try:
        candidates = [
            path for path in root.glob(f"*{CAPSULE_EXTENSION}") if path.is_file()
        ]
    except OSError:
        return None

    if not candidates:
        return None

    def sort_key(path: Path) -> tuple[int, str]:
        try:
            modified = path.stat().st_mtime_ns
        except OSError:
            modified = 0
        return (modified, path.name.lower())

    return max(candidates, key=sort_key)


def infer_capsule_version_from_id(capsule_id: str, *, fallback: str = "") -> str:
    """Infer the canonical local version from a dated capsule ID when possible."""

    match = re.fullmatch(
        r"konnaxion-[^-]+-(?P<channel>[^-]+)-(?P<date>\d{4}\.\d{2}\.\d{2})",
        str(capsule_id).strip(),
    )
    if match is None:
        return fallback
    return f"{match.group('date')}-{match.group('channel')}.1"


def auto_capsule_naming_enabled() -> bool:
    """Return whether a new Manager process should refresh ID/version by date."""

    value = os.getenv("KX_AUTO_CAPSULE_NAMING", "true").strip().lower()
    return value not in {"0", "false", "no", "off"}


__all__ = [
    "DEFAULT_CAPSULE_FILE",
    "DEFAULT_CAPSULE_ID",
    "DEFAULT_CAPSULE_OUTPUT_DIR",
    "DEFAULT_CAPSULE_VERSION",
    "DEFAULT_CHANNEL",
    "DEFAULT_EXPOSURE_MODE",
    "DEFAULT_INSTANCE_ID",
    "DEFAULT_DROPLET_INSTANCE_ID",
    "DEFAULT_DROPLET_NAME",
    "DEFAULT_DROPLET_HOST",
    "DEFAULT_DROPLET_USER",
    "DEFAULT_SSH_KEY_PATH",
    "DEFAULT_SSH_PORT",
    "DEFAULT_REMOTE_KX_ROOT",
    "DEFAULT_REMOTE_CAPSULE_DIR",
    "DEFAULT_DROPLET_DOMAIN",
    "DEFAULT_REMOTE_AGENT_URL",
    "DEFAULT_NETWORK_PROFILE",
    "DEFAULT_RUNTIME_ROOT",
    "DEFAULT_SOURCE_DIR",
    "DEFAULT_TARGET_MODE",
    "SESSION_CHANNEL",
    "auto_capsule_naming_enabled",
    "capsule_date_stamp",
    "make_default_capsule_id",
    "make_default_capsule_version",
    "latest_existing_capsule",
    "infer_capsule_version_from_id",
]
