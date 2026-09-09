"""
Authentication and authorization helpers for Konnaxion Agent.

The Agent API is local-only and must reject unauthenticated or unauthorized
Manager requests. This module provides:

- local bearer token loading/generation
- constant-time token verification
- operation allowlist enforcement
- public-mode expiration checks
- simple request-context helpers for API layers

This file intentionally does not execute shell commands, call Docker, or change
host state.
"""

from __future__ import annotations

import hmac
import os
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Mapping

from kx_shared.konnaxion_constants import (
    DEFAULT_EXPOSURE_MODE,
    DEFAULT_NETWORK_PROFILE,
    ExposureMode,
    NetworkProfile,
    KX_ROOT,
    PUBLIC_CLI_COMMANDS,
    is_public_mode,
    require_public_expiration,
)


# ---------------------------------------------------------------------------
# Agent auth constants
# ---------------------------------------------------------------------------

DEFAULT_AGENT_TOKEN_PATH = Path(KX_ROOT) / "manager" / "agent.token"
DEFAULT_AGENT_TOKEN_ENV = "KX_AGENT_TOKEN"
DEFAULT_AGENT_TOKEN_PATH_ENV = "KX_AGENT_TOKEN_PATH"

TOKEN_BYTES = 32
TOKEN_FILE_MODE = 0o600

AUTH_SCHEME_BEARER = "Bearer"
LOCAL_BIND_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


# ---------------------------------------------------------------------------
# Canonical Agent operations
# ---------------------------------------------------------------------------

class AgentOperation(StrEnum):
    """Allowlisted Manager-to-Agent operations."""

    CAPSULE_VERIFY = "capsule.verify"
    CAPSULE_IMPORT = "capsule.import"

    INSTANCE_CREATE = "instance.create"
    INSTANCE_START = "instance.start"
    INSTANCE_STOP = "instance.stop"
    INSTANCE_STATUS = "instance.status"
    INSTANCE_LOGS = "instance.logs"
    INSTANCE_BACKUP = "instance.backup"
    INSTANCE_RESTORE = "instance.restore"
    INSTANCE_UPDATE = "instance.update"
    INSTANCE_ROLLBACK = "instance.rollback"

    SECURITY_CHECK = "security.check"
    NETWORK_SET_PROFILE = "network.set_profile"


CANONICAL_AGENT_OPERATIONS = tuple(operation.value for operation in AgentOperation)

# Internal Manager/Agent operation names mapped from public CLI commands.
CLI_TO_AGENT_OPERATION = {
    "kx capsule verify": AgentOperation.CAPSULE_VERIFY.value,
    "kx capsule import": AgentOperation.CAPSULE_IMPORT.value,
    "kx instance create": AgentOperation.INSTANCE_CREATE.value,
    "kx instance start": AgentOperation.INSTANCE_START.value,
    "kx instance stop": AgentOperation.INSTANCE_STOP.value,
    "kx instance status": AgentOperation.INSTANCE_STATUS.value,
    "kx instance logs": AgentOperation.INSTANCE_LOGS.value,
    "kx instance backup": AgentOperation.INSTANCE_BACKUP.value,
    "kx instance restore": AgentOperation.INSTANCE_RESTORE.value,
    "kx instance restore-new": AgentOperation.INSTANCE_RESTORE.value,
    "kx instance update": AgentOperation.INSTANCE_UPDATE.value,
    "kx instance rollback": AgentOperation.INSTANCE_ROLLBACK.value,
    "kx instance health": AgentOperation.SECURITY_CHECK.value,
    "kx backup verify": AgentOperation.INSTANCE_BACKUP.value,
    "kx backup test-restore": AgentOperation.INSTANCE_RESTORE.value,
    "kx security check": AgentOperation.SECURITY_CHECK.value,
    "kx network set-profile": AgentOperation.NETWORK_SET_PROFILE.value,
}

# Commands that are intentionally not Agent privileged operations.
NON_AGENT_PUBLIC_CLI_COMMANDS = frozenset(
    command for command in PUBLIC_CLI_COMMANDS if command not in CLI_TO_AGENT_OPERATION
)

FORBIDDEN_AGENT_OPERATIONS = frozenset(
    {
        "shell.exec",
        "shell.run",
        "command.run",
        "docker.run",
        "docker.image.run",
        "docker.compose.run_arbitrary",
        "docker.compose.use_arbitrary_file",
        "docker.socket.mount",
        "docker.privileged.enable",
        "docker.host_network.enable",
        "docker.port.publish_arbitrary",
        "systemd.unit.create_arbitrary",
        "sudoers.modify",
        "ssh.config.modify",
        "security.disable_gate",
        "security.disable_signature_validation",
        "network.open_arbitrary_port",
    }
)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class AuthError(Exception):
    """Base class for Agent authentication and authorization errors."""


class AuthenticationError(AuthError):
    """Raised when a request is not authenticated."""


class AuthorizationError(AuthError):
    """Raised when a request is authenticated but not authorized."""


class LocalOnlyError(AuthError):
    """Raised when a request does not come through an approved local channel."""


class TokenConfigurationError(AuthError):
    """Raised when token configuration is missing or invalid."""


# ---------------------------------------------------------------------------
# Request context
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AgentRequestContext:
    """
    Minimal request context consumed by Agent API handlers.

    The API layer should construct this from its framework-specific request
    object and pass it to authenticate_request().
    """

    operation: str
    headers: Mapping[str, str]
    remote_host: str = "127.0.0.1"
    via_unix_socket: bool = False
    network_profile: str = DEFAULT_NETWORK_PROFILE.value
    exposure_mode: str = DEFAULT_EXPOSURE_MODE.value
    public_mode_enabled: bool = False
    public_mode_expires_at: str | None = None


@dataclass(frozen=True)
class AuthResult:
    """Successful authentication and authorization result."""

    operation: str
    authenticated: bool
    authorized: bool
    subject: str = "konnaxion-manager"


# ---------------------------------------------------------------------------
# Token helpers
# ---------------------------------------------------------------------------

def get_agent_token_path() -> Path:
    """Return the configured Agent token file path."""
    return Path(os.environ.get(DEFAULT_AGENT_TOKEN_PATH_ENV, str(DEFAULT_AGENT_TOKEN_PATH)))


def generate_agent_token() -> str:
    """Generate a cryptographically strong local Agent token."""
    return secrets.token_urlsafe(TOKEN_BYTES)


def write_agent_token(token_path: Path | None = None, *, overwrite: bool = False) -> str:
    """
    Create the local Agent token file.

    Args:
        token_path: Optional token path. Defaults to KX_AGENT_TOKEN_PATH or
            /opt/konnaxion/manager/agent.token.
        overwrite: If False, keep an existing token.

    Returns:
        The token value.

    Raises:
        FileExistsError: if the token exists and overwrite=False.
    """
    path = token_path or get_agent_token_path()

    if path.exists() and not overwrite:
        raise FileExistsError(f"Agent token already exists: {path}")

    token = generate_agent_token()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(token + "\n", encoding="utf-8")
    path.chmod(TOKEN_FILE_MODE)
    return token


def load_agent_token(token_path: Path | None = None) -> str:
    """
    Load the configured Agent token.

    Environment variable KX_AGENT_TOKEN takes precedence. Otherwise, the token is
    read from KX_AGENT_TOKEN_PATH or /opt/konnaxion/manager/agent.token.
    """
    env_token = os.environ.get(DEFAULT_AGENT_TOKEN_ENV)
    if env_token:
        return env_token.strip()

    path = token_path or get_agent_token_path()
    if not path.exists():
        raise TokenConfigurationError(f"Agent token file does not exist: {path}")

    token = path.read_text(encoding="utf-8").strip()
    if not token:
        raise TokenConfigurationError(f"Agent token file is empty: {path}")

    return token


def parse_authorization_header(headers: Mapping[str, str]) -> str:
    """
    Extract a Bearer token from request headers.

    Header lookup is case-insensitive.
    """
    auth_header = ""

    for key, value in headers.items():
        if key.lower() == "authorization":
            auth_header = value.strip()
            break

    if not auth_header:
        raise AuthenticationError("Missing Authorization header.")

    scheme, _, token = auth_header.partition(" ")
    if scheme != AUTH_SCHEME_BEARER or not token:
        raise AuthenticationError("Authorization header must use Bearer token format.")

    return token.strip()


def verify_token(provided_token: str, expected_token: str) -> bool:
    """Verify tokens using constant-time comparison."""
    if not provided_token or not expected_token:
        return False
    return hmac.compare_digest(provided_token, expected_token)


# ---------------------------------------------------------------------------
# Local-only checks
# ---------------------------------------------------------------------------

def is_local_request(remote_host: str, *, via_unix_socket: bool = False) -> bool:
    """Return True if the request came through a local-only channel."""
    if via_unix_socket:
        return True
    return remote_host in LOCAL_BIND_HOSTS


def require_local_request(remote_host: str, *, via_unix_socket: bool = False) -> None:
    """Raise if the request did not come through a local-only channel."""
    if not is_local_request(remote_host, via_unix_socket=via_unix_socket):
        raise LocalOnlyError("Konnaxion Agent API accepts local-only requests.")


# ---------------------------------------------------------------------------
# Authorization helpers
# ---------------------------------------------------------------------------

def normalize_operation(operation: str | AgentOperation) -> str:
    """Return the canonical operation string."""
    if isinstance(operation, AgentOperation):
        return operation.value
    return str(operation).strip()


def is_allowed_operation(operation: str | AgentOperation) -> bool:
    """Return True if operation is in the Agent operation allowlist."""
    return normalize_operation(operation) in CANONICAL_AGENT_OPERATIONS


def is_forbidden_operation(operation: str | AgentOperation) -> bool:
    """Return True if operation is explicitly forbidden."""
    return normalize_operation(operation) in FORBIDDEN_AGENT_OPERATIONS


def require_allowed_operation(operation: str | AgentOperation) -> str:
    """
    Validate an Agent operation.

    Raises:
        AuthorizationError: if operation is unknown or forbidden.
    """
    op = normalize_operation(operation)

    if is_forbidden_operation(op):
        raise AuthorizationError(f"Forbidden Agent operation: {op}")

    if not is_allowed_operation(op):
        raise AuthorizationError(f"Operation is not allowlisted for Konnaxion Agent: {op}")

    return op


def require_profile_authorization(
    *,
    operation: str | AgentOperation,
    network_profile: str = DEFAULT_NETWORK_PROFILE.value,
    exposure_mode: str = DEFAULT_EXPOSURE_MODE.value,
    public_mode_enabled: bool = False,
    public_mode_expires_at: str | None = None,
) -> None:
    """
    Enforce profile-sensitive authorization rules.

    Public exposure is allowed only through explicit profile/exposure values and
    must have an expiration when enabled.
    """
    op = normalize_operation(operation)

    # Network profile changes are allowed as operations, but unsafe public
    # exposure must still carry an expiration.
    if public_mode_enabled or is_public_mode(network_profile, exposure_mode):
        require_public_expiration(public_mode_enabled=True, expires_at=public_mode_expires_at)

    if (
        op == AgentOperation.NETWORK_SET_PROFILE.value
        and network_profile == NetworkProfile.PUBLIC_TEMPORARY.value
        and exposure_mode != ExposureMode.TEMPORARY_TUNNEL.value
    ):
        raise AuthorizationError(
            "public_temporary profile requires temporary_tunnel exposure mode."
        )

    if (
        op == AgentOperation.NETWORK_SET_PROFILE.value
        and exposure_mode == ExposureMode.PUBLIC.value
        and network_profile != NetworkProfile.PUBLIC_VPS.value
    ):
        raise AuthorizationError("public exposure mode requires public_vps network profile.")


def parse_datetime_utc(value: str) -> datetime:
    """Parse an ISO-8601 datetime and normalize it to UTC."""
    normalized = value.strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)

    return parsed.astimezone(UTC)


def require_unexpired_public_mode(expires_at: str | None, *, now: datetime | None = None) -> None:
    """
    Ensure a public exposure expiration exists and has not passed.

    Raises:
        AuthorizationError: if expiration is missing, invalid, or expired.
    """
    if not expires_at:
        raise AuthorizationError("Public mode expiration is required.")

    current = now or datetime.now(UTC)

    try:
        expiration = parse_datetime_utc(expires_at)
    except ValueError as exc:
        raise AuthorizationError("Public mode expiration must be ISO-8601 datetime.") from exc

    if expiration <= current:
        raise AuthorizationError("Public mode expiration has passed.")


# ---------------------------------------------------------------------------
# Request authentication
# ---------------------------------------------------------------------------

def authenticate_request(
    context: AgentRequestContext,
    *,
    expected_token: str | None = None,
) -> AuthResult:
    """
    Authenticate and authorize a Manager-to-Agent request.

    API handlers should call this before performing privileged work.

    Args:
        context: Request metadata from the Agent API layer.
        expected_token: Optional token override for tests.

    Returns:
        AuthResult when the request is accepted.

    Raises:
        LocalOnlyError: request is not local-only.
        AuthenticationError: token missing or invalid.
        AuthorizationError: operation not allowed.
        TokenConfigurationError: Agent token unavailable.
    """
    require_local_request(
        context.remote_host,
        via_unix_socket=context.via_unix_socket,
    )

    operation = require_allowed_operation(context.operation)

    expected = expected_token if expected_token is not None else load_agent_token()
    provided = parse_authorization_header(context.headers)

    if not verify_token(provided, expected):
        raise AuthenticationError("Invalid Agent token.")

    require_profile_authorization(
        operation=operation,
        network_profile=context.network_profile,
        exposure_mode=context.exposure_mode,
        public_mode_enabled=context.public_mode_enabled,
        public_mode_expires_at=context.public_mode_expires_at,
    )

    if context.public_mode_enabled:
        require_unexpired_public_mode(context.public_mode_expires_at)

    return AuthResult(
        operation=operation,
        authenticated=True,
        authorized=True,
    )


def make_authorization_header(token: str) -> dict[str, str]:
    """Return a Bearer authorization header for Manager clients/tests."""
    return {"Authorization": f"{AUTH_SCHEME_BEARER} {token}"}


__all__ = [
    "AUTH_SCHEME_BEARER",
    "CANONICAL_AGENT_OPERATIONS",
    "CLI_TO_AGENT_OPERATION",
    "DEFAULT_AGENT_TOKEN_ENV",
    "DEFAULT_AGENT_TOKEN_PATH",
    "DEFAULT_AGENT_TOKEN_PATH_ENV",
    "FORBIDDEN_AGENT_OPERATIONS",
    "LOCAL_BIND_HOSTS",
    "NON_AGENT_PUBLIC_CLI_COMMANDS",
    "TOKEN_BYTES",
    "TOKEN_FILE_MODE",
    "AgentOperation",
    "AgentRequestContext",
    "AuthError",
    "AuthResult",
    "AuthenticationError",
    "AuthorizationError",
    "LocalOnlyError",
    "TokenConfigurationError",
    "authenticate_request",
    "generate_agent_token",
    "get_agent_token_path",
    "is_allowed_operation",
    "is_forbidden_operation",
    "is_local_request",
    "load_agent_token",
    "make_authorization_header",
    "normalize_operation",
    "parse_authorization_header",
    "parse_datetime_utc",
    "require_allowed_operation",
    "require_local_request",
    "require_profile_authorization",
    "require_unexpired_public_mode",
    "verify_token",
    "write_agent_token",
]
