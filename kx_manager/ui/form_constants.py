"""Constants and canonical enum aliases for Konnaxion Manager UI forms."""

from __future__ import annotations

from kx_shared import konnaxion_constants as kx_constants
from kx_manager.defaults import (
    DEFAULT_CAPSULE_ID,
    DEFAULT_CAPSULE_OUTPUT_DIR,
    DEFAULT_CAPSULE_VERSION,
    DEFAULT_CHANNEL,
    DEFAULT_INSTANCE_ID,
    DEFAULT_RUNTIME_ROOT,
    DEFAULT_SOURCE_DIR,
)


NetworkProfile = kx_constants.NetworkProfile
ExposureMode = kx_constants.ExposureMode
DockerService = kx_constants.DockerService

CAPSULE_EXTENSION = getattr(kx_constants, "CAPSULE_EXTENSION", ".kxcap")

SAFE_ID_CHARS = set(
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "0123456789"
    "._-"
)

TRUE_VALUES = {
    "1",
    "true",
    "yes",
    "y",
    "on",
    "checked",
}

FALSE_VALUES = {
    "0",
    "false",
    "no",
    "n",
    "off",
    "",
}


__all__ = [
    "CAPSULE_EXTENSION",
    "DEFAULT_CAPSULE_ID",
    "DEFAULT_CAPSULE_OUTPUT_DIR",
    "DEFAULT_CAPSULE_VERSION",
    "DEFAULT_CHANNEL",
    "DEFAULT_INSTANCE_ID",
    "DEFAULT_RUNTIME_ROOT",
    "DEFAULT_SOURCE_DIR",
    "DockerService",
    "ExposureMode",
    "FALSE_VALUES",
    "NetworkProfile",
    "SAFE_ID_CHARS",
    "TRUE_VALUES",
]