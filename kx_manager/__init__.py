"""
Konnaxion Capsule Manager package.

The Manager is the user-facing control layer. It must not execute privileged
Docker, firewall, host-network, or backup operations directly. Those actions
belong to Konnaxion Agent allowlisted APIs.

Manager modules should import canonical product/version/profile values from
this package or from kx_shared.konnaxion_constants.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import find_dotenv, load_dotenv


def _load_operator_dotenv() -> str | None:
    """Load the operator .env before Manager defaults are imported.

    Process environment values keep precedence. ``KX_MANAGER_ENV_FILE`` can
    pin an explicit file; otherwise the nearest ``.env`` from the launch
    directory (including parent directories) is used.
    """

    explicit = os.getenv("KX_MANAGER_ENV_FILE", "").strip()
    if explicit:
        candidate = Path(explicit).expanduser()
        if candidate.is_file():
            load_dotenv(candidate, override=False)
            return str(candidate)
        return None

    discovered = find_dotenv(filename=".env", usecwd=True)
    if discovered:
        load_dotenv(discovered, override=False)
        return discovered
    return None


_LOADED_OPERATOR_ENV_FILE = _load_operator_dotenv()

from kx_shared.konnaxion_constants import (
    APP_VERSION,
    MANAGER_NAME,
    PARAM_VERSION,
    PRODUCT_NAME,
)


__version__ = APP_VERSION
__param_version__ = PARAM_VERSION
__product_name__ = PRODUCT_NAME
__manager_name__ = MANAGER_NAME


__all__ = [
    "__version__",
    "__param_version__",
    "__product_name__",
    "__manager_name__",
    "APP_VERSION",
    "MANAGER_NAME",
    "PARAM_VERSION",
    "PRODUCT_NAME",
]
