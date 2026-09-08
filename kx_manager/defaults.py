"""Operator-friendly defaults for the local Konnaxion Capsule Manager.

These defaults are deliberately scoped to the Manager/operator workflow. They
must not redefine immutable runtime metadata inside an already installed
Konnaxion Instance.
"""

from __future__ import annotations

import os
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
    "DEFAULT_NETWORK_PROFILE",
    "DEFAULT_RUNTIME_ROOT",
    "DEFAULT_SOURCE_DIR",
    "DEFAULT_TARGET_MODE",
    "SESSION_CHANNEL",
    "auto_capsule_naming_enabled",
    "capsule_date_stamp",
    "make_default_capsule_id",
    "make_default_capsule_version",
]
