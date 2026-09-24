"""Konnaxion Agent package.

The Agent is the controlled privileged service used by the Konnaxion Capsule
Manager to manage capsules, instances, Docker Compose runtime operations,
network profiles, backups, restores, and Security Gate checks.

Package-level metadata is imported from the shared canonical registry so this
module does not define independent product names, versions, paths, states, or
service identifiers.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import find_dotenv, load_dotenv


def _load_operator_dotenv() -> str | None:
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

from kx_shared.konnaxion_constants import AGENT_NAME, APP_VERSION

__all__ = [
    "__app_name__",
    "__version__",
]

__app_name__ = AGENT_NAME
__version__ = APP_VERSION
