"""
Konnaxion Agent entrypoint.

This module is intentionally thin:
- imports canonical values from kx_shared
- bootstraps host directories
- validates runtime configuration
- starts the local Agent API server
- exposes a small CLI for service execution and diagnostics

The Agent performs privileged operations through allowlisted internal modules.
The Manager must not call Docker, firewall, backup, or runtime operations directly.
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn, Sequence

from kx_shared.konnaxion_constants import (
    AGENT_NAME,
    APP_VERSION,
    DEFAULT_EXPOSURE_MODE,
    DEFAULT_INSTANCE_ID,
    DEFAULT_NETWORK_PROFILE,
    KX_AGENT_DIR,
    KX_BACKUPS_ROOT,
    KX_CAPSULES_DIR,
    KX_ENV_DEFAULTS,
    KX_INSTANCES_DIR,
    KX_ROOT,
    KX_SHARED_DIR,
    PARAM_VERSION,
)


LOGGER = logging.getLogger("kx_agent")


class AgentStartupError(RuntimeError):
    """Raised when the Agent cannot safely start."""


@dataclass(frozen=True)
class AgentRuntimeConfig:
    """Runtime settings for the local Konnaxion Agent process."""

    host: str
    port: int
    log_level: str
    instance_id: str
    network_profile: str
    exposure_mode: str
    root_dir: Path
    agent_dir: Path
    instances_dir: Path
    capsules_dir: Path
    backups_root: Path
    shared_dir: Path

    @classmethod
    def from_environment(cls) -> "AgentRuntimeConfig":
        """Build Agent process configuration from environment defaults."""

        return cls(
            host=os.getenv("KX_AGENT_HOST", "127.0.0.1"),
            port=_read_int_env("KX_AGENT_PORT", 8765),
            log_level=os.getenv("KX_AGENT_LOG_LEVEL", "INFO"),
            instance_id=os.getenv("KX_INSTANCE_ID", DEFAULT_INSTANCE_ID),
            network_profile=os.getenv(
                "KX_NETWORK_PROFILE",
                DEFAULT_NETWORK_PROFILE.value,
            ),
            exposure_mode=os.getenv(
                "KX_EXPOSURE_MODE",
                DEFAULT_EXPOSURE_MODE.value,
            ),
            root_dir=Path(os.getenv("KX_ROOT", str(KX_ROOT))),
            agent_dir=Path(os.getenv("KX_AGENT_DIR", str(KX_AGENT_DIR))),
            instances_dir=Path(os.getenv("KX_INSTANCES_DIR", str(KX_INSTANCES_DIR))),
            capsules_dir=Path(os.getenv("KX_CAPSULES_DIR", str(KX_CAPSULES_DIR))),
            backups_root=Path(os.getenv("KX_BACKUP_ROOT", str(KX_BACKUPS_ROOT))),
            shared_dir=Path(os.getenv("KX_SHARED_DIR", str(KX_SHARED_DIR))),
        )


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint for the Konnaxion Agent."""

    parser = build_parser()
    args = parser.parse_args(argv)

    config = AgentRuntimeConfig.from_environment()
    configure_logging(args.log_level or config.log_level)

    if args.command == "run":
        return run_agent(config=config, reload=args.reload)

    if args.command == "doctor":
        return run_doctor(config=config)

    if args.command == "version":
        print_version()
        return 0

    parser.print_help()
    return 2


def app() -> int:
    """
    Console-script entrypoint.

    `pyproject.toml` exposes the command as:

        kx-agent = "kx_agent.main:app"

    Keep this wrapper thin so service supervisors, package scripts, and direct
    Python execution all share the same `main()` behavior.
    """

    return main()


def build_parser() -> argparse.ArgumentParser:
    """Create the Agent CLI parser."""

    parser = argparse.ArgumentParser(
        prog="kx-agent",
        description=f"{AGENT_NAME} local privileged service",
    )
    parser.add_argument(
        "--log-level",
        choices=("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"),
        help="Override Agent log level.",
    )

    subparsers = parser.add_subparsers(dest="command")

    run_parser = subparsers.add_parser(
        "run",
        help="Start the local Agent API server.",
    )
    run_parser.add_argument(
        "--reload",
        action="store_true",
        help="Enable development reload when supported by the server runtime.",
    )

    subparsers.add_parser(
        "doctor",
        help="Validate Agent startup configuration and required directories.",
    )

    subparsers.add_parser(
        "version",
        help="Print Agent and parameter versions.",
    )

    return parser


def run_agent(config: AgentRuntimeConfig, reload: bool = False) -> int:
    """Start the Agent API server."""

    validate_config(config)
    ensure_directories(config)
    install_signal_handlers()

    LOGGER.info(
        "Starting %s on %s:%s for instance=%s profile=%s exposure=%s",
        AGENT_NAME,
        config.host,
        config.port,
        config.instance_id,
        config.network_profile,
        config.exposure_mode,
    )

    try:
        import uvicorn
    except ImportError as exc:
        raise AgentStartupError(
            "Missing dependency: uvicorn is required to run the Agent API server."
        ) from exc

    try:
        from kx_agent.api import create_app
    except ImportError as exc:
        raise AgentStartupError(
            "Cannot import kx_agent.api.create_app. Generate kx_agent/api.py before running the Agent."
        ) from exc

    app = create_app(config=config)

    uvicorn.run(
        app,
        host=config.host,
        port=config.port,
        log_level=config.log_level.lower(),
        reload=reload,
    )

    return 0


def run_doctor(config: AgentRuntimeConfig) -> int:
    """Validate the Agent runtime environment without starting the API server."""

    configure_logging(config.log_level)

    try:
        validate_config(config)
        ensure_directories(config)
    except AgentStartupError as exc:
        LOGGER.error("Doctor failed: %s", exc)
        return 1

    LOGGER.info("Doctor passed.")
    LOGGER.info("root_dir=%s", config.root_dir)
    LOGGER.info("agent_dir=%s", config.agent_dir)
    LOGGER.info("instances_dir=%s", config.instances_dir)
    LOGGER.info("capsules_dir=%s", config.capsules_dir)
    LOGGER.info("backups_root=%s", config.backups_root)
    LOGGER.info("shared_dir=%s", config.shared_dir)
    LOGGER.info("instance_id=%s", config.instance_id)
    LOGGER.info("network_profile=%s", config.network_profile)
    LOGGER.info("exposure_mode=%s", config.exposure_mode)
    return 0


def validate_config(config: AgentRuntimeConfig) -> None:
    """Reject unsafe or malformed Agent process settings."""

    if not config.host:
        raise AgentStartupError("KX_AGENT_HOST cannot be empty.")

    if config.host not in {"127.0.0.1", "::1", "localhost"}:
        raise AgentStartupError(
            "Konnaxion Agent is local-only; KX_AGENT_HOST must be loopback."
        )

    if not 1 <= config.port <= 65535:
        raise AgentStartupError("KX_AGENT_PORT must be between 1 and 65535.")

    if config.instance_id.strip() == "":
        raise AgentStartupError("KX_INSTANCE_ID cannot be empty.")

    if config.network_profile.strip() == "":
        raise AgentStartupError("KX_NETWORK_PROFILE cannot be empty.")

    if config.exposure_mode.strip() == "":
        raise AgentStartupError("KX_EXPOSURE_MODE cannot be empty.")

    public_mode_enabled = _read_bool_env("KX_PUBLIC_MODE_ENABLED", default=False)
    public_mode_expires_at = os.getenv("KX_PUBLIC_MODE_EXPIRES_AT", "")

    if public_mode_enabled and not public_mode_expires_at:
        raise AgentStartupError(
            "KX_PUBLIC_MODE_EXPIRES_AT is mandatory when KX_PUBLIC_MODE_ENABLED=true."
        )

    required_kx_keys = (
        "KX_REQUIRE_SIGNED_CAPSULE",
        "KX_GENERATE_SECRETS_ON_INSTALL",
        "KX_ALLOW_UNKNOWN_IMAGES",
        "KX_ALLOW_PRIVILEGED_CONTAINERS",
        "KX_ALLOW_DOCKER_SOCKET_MOUNT",
        "KX_ALLOW_HOST_NETWORK",
        "KX_BACKUP_ENABLED",
    )

    missing_keys = [
        key
        for key in required_kx_keys
        if os.getenv(key, KX_ENV_DEFAULTS.get(key, "")).strip() == ""
    ]

    if missing_keys:
        raise AgentStartupError(
            "Missing required KX configuration keys: " + ", ".join(missing_keys)
        )

    unsafe_flags = {
        "KX_REQUIRE_SIGNED_CAPSULE": True,
        "KX_GENERATE_SECRETS_ON_INSTALL": True,
        "KX_ALLOW_UNKNOWN_IMAGES": False,
        "KX_ALLOW_PRIVILEGED_CONTAINERS": False,
        "KX_ALLOW_DOCKER_SOCKET_MOUNT": False,
        "KX_ALLOW_HOST_NETWORK": False,
    }

    for key, expected in unsafe_flags.items():
        actual = _read_bool_env(key, default=_read_bool(KX_ENV_DEFAULTS[key]))
        if actual != expected:
            raise AgentStartupError(
                f"{key} must be {str(expected).lower()} for safe Agent startup."
            )


def ensure_directories(config: AgentRuntimeConfig) -> None:
    """Create canonical host directories required by the Agent."""

    for directory in (
        config.root_dir,
        config.agent_dir,
        config.instances_dir,
        config.capsules_dir,
        config.backups_root,
        config.shared_dir,
    ):
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except PermissionError as exc:
            raise AgentStartupError(
                f"Permission denied while creating required directory: {directory}"
            ) from exc

    try:
        from kx_agent.artifacts.registry import ensure_registry

        ensure_registry()
    except Exception as exc:
        raise AgentStartupError(
            f"Could not initialize installed artifact registry: {exc}"
        ) from exc


def install_signal_handlers() -> None:
    """Install basic shutdown handlers for service supervisors."""

    def _handle_shutdown(signum: int, _frame: object) -> NoReturn:
        LOGGER.info("Received signal %s. Shutting down %s.", signum, AGENT_NAME)
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, _handle_shutdown)
    signal.signal(signal.SIGINT, _handle_shutdown)


def configure_logging(level: str) -> None:
    """Configure process logging."""

    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def print_version() -> None:
    """Print Agent version metadata."""

    print(f"{AGENT_NAME}")
    print(f"app_version={APP_VERSION}")
    print(f"param_version={PARAM_VERSION}")


def _read_int_env(key: str, default: int) -> int:
    raw = os.getenv(key)
    if raw is None or raw.strip() == "":
        return default

    try:
        return int(raw)
    except ValueError as exc:
        raise AgentStartupError(f"{key} must be an integer.") from exc


def _read_bool_env(key: str, default: bool) -> bool:
    raw = os.getenv(key)
    if raw is None or raw.strip() == "":
        return default

    return _read_bool(raw)


def _read_bool(value: str) -> bool:
    normalized = value.strip().lower()

    if normalized in {"1", "true", "yes", "y", "on"}:
        return True

    if normalized in {"0", "false", "no", "n", "off"}:
        return False

    raise AgentStartupError(f"Invalid boolean value: {value!r}")


if __name__ == "__main__":
    try:
        raise SystemExit(app())
    except AgentStartupError as exc:
        configure_logging(os.getenv("KX_AGENT_LOG_LEVEL", "INFO"))
        LOGGER.error("%s", exc)
        raise SystemExit(1)
