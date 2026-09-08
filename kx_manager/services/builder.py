"""
Builder service wrapper for Konnaxion Capsule Manager.

The Manager GUI may request capsule build and verify operations, but it must
not execute arbitrary shell commands. This module provides a narrow,
allowlisted wrapper around the approved Konnaxion Capsule Builder commands.

Approved operations:
- uv run kx-builder capsule build ...
- uv run kx-builder capsule verify ...

This service is intentionally framework-independent so it can be used by:
- kx_manager/ui/actions.py
- kx_manager/routes/capsules.py
- tests
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from kx_manager.defaults import (
    DEFAULT_CAPSULE_ID,
    DEFAULT_CAPSULE_OUTPUT_DIR,
    DEFAULT_CAPSULE_VERSION,
    DEFAULT_CHANNEL,
    DEFAULT_NETWORK_PROFILE,
    DEFAULT_SOURCE_DIR,
)

DEFAULT_BUILDER_TIMEOUT_SECONDS = 60 * 60
DEFAULT_VERIFY_TIMEOUT_SECONDS = 15 * 60

DEFAULT_APP_VERSION = "v14"
DEFAULT_PARAM_VERSION = "kx-param-2026.04.30"

DEFAULT_WINDOWS_SOURCE_DIR = Path(DEFAULT_SOURCE_DIR)
DEFAULT_WINDOWS_CAPSULE_OUTPUT_DIR = Path(DEFAULT_CAPSULE_OUTPUT_DIR)
DEFAULT_WINDOWS_CAPSULE_FILE = DEFAULT_WINDOWS_CAPSULE_OUTPUT_DIR / f"{DEFAULT_CAPSULE_ID}.kxcap"

DEFAULT_WINDOWS_SIGNING_DIR = Path(r"C:\mycode\Konnaxion\runtime\signing")
DEFAULT_WINDOWS_SIGNING_KEY_FILE = (
    DEFAULT_WINDOWS_SIGNING_DIR / "kx-demo-ed25519-private.pem"
)
DEFAULT_WINDOWS_PUBLIC_KEY_FILE = (
    DEFAULT_WINDOWS_SIGNING_DIR / "kx-demo-ed25519-public.pem"
)


class BuilderServiceError(ValueError):
    """Raised when a builder request is invalid before a command can run."""

    def __init__(
        self,
        message: str,
        *,
        field: str | None = None,
        data: Mapping[str, Any] | None = None,
    ) -> None:
        self.field = field
        self.data = dict(data or {})
        super().__init__(message)


@dataclass(frozen=True)
class BuildCapsuleRequest:
    """Request to build a Konnaxion Capsule."""

    source_dir: Path = DEFAULT_WINDOWS_SOURCE_DIR
    capsule_output_dir: Path = DEFAULT_WINDOWS_CAPSULE_OUTPUT_DIR
    capsule_file: Path | str | None = None
    capsule_id: str = DEFAULT_CAPSULE_ID
    capsule_version: str = DEFAULT_CAPSULE_VERSION
    channel: str = DEFAULT_CHANNEL
    app_version: str = DEFAULT_APP_VERSION
    param_version: str = DEFAULT_PARAM_VERSION
    network_profile: str = DEFAULT_NETWORK_PROFILE
    force: bool = False
    signing_key_file: Path | str | None = DEFAULT_WINDOWS_SIGNING_KEY_FILE
    public_key_file: Path | str | None = DEFAULT_WINDOWS_PUBLIC_KEY_FILE
    cwd: Path | None = None
    timeout_seconds: int = DEFAULT_BUILDER_TIMEOUT_SECONDS
    env: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class VerifyCapsuleRequest:
    """Request to verify a Konnaxion Capsule."""

    capsule_file: Path | str = DEFAULT_WINDOWS_CAPSULE_FILE
    public_key_file: Path | str | None = DEFAULT_WINDOWS_PUBLIC_KEY_FILE
    cwd: Path | None = None
    timeout_seconds: int = DEFAULT_VERIFY_TIMEOUT_SECONDS
    env: Mapping[str, str] = field(default_factory=dict)
    raise_on_preflight_error: bool = False


@dataclass(frozen=True)
class BuilderCommandResult:
    """Normalized result for a Builder service command."""

    ok: bool
    operation: str
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    message: str
    started_at: str
    finished_at: str
    data: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "operation": self.operation,
            "argv": list(self.argv),
            "returncode": self.returncode,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "message": self.message,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "data": dict(self.data),
        }


@dataclass(frozen=True)
class BuildCapsuleResult:
    """Manager-facing build result."""

    ok: bool
    capsule_file: Path
    capsule_id: str
    capsule_version: str
    channel: str
    app_version: str
    param_version: str
    network_profile: str
    command: BuilderCommandResult

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "capsule_file": str(self.capsule_file),
            "capsule_id": self.capsule_id,
            "capsule_version": self.capsule_version,
            "channel": self.channel,
            "app_version": self.app_version,
            "param_version": self.param_version,
            "network_profile": self.network_profile,
            "command": self.command.to_dict(),
        }


@dataclass(frozen=True)
class VerifyCapsuleResult:
    """Manager-facing verify result."""

    ok: bool
    capsule_file: Path
    command: BuilderCommandResult

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "capsule_file": str(self.capsule_file),
            "command": self.command.to_dict(),
        }


def build_capsule(request: BuildCapsuleRequest | None = None) -> BuildCapsuleResult:
    """Build a signed `.kxcap` capsule through the approved Builder command."""

    request = request or BuildCapsuleRequest()

    source_dir = _require_existing_dir(request.source_dir, "source_dir")
    capsule_output_dir = _ensure_dir(request.capsule_output_dir, "capsule_output_dir")
    capsule_file = _resolve_capsule_file(request, capsule_output_dir)

    signing_key_file = _optional_existing_file(
        request.signing_key_file,
        "signing_key_file",
    )
    public_key_file = _optional_existing_file(
        request.public_key_file,
        "public_key_file",
    )

    argv = [
        "uv",
        "run",
        "kx-builder",
        "capsule",
        "build",
        "--source-dir",
        str(source_dir),
        "--output",
        str(capsule_file),
        "--channel",
        request.channel,
        "--capsule-id",
        request.capsule_id,
        "--version",
        request.capsule_version,
        "--app-version",
        request.app_version,
        "--param-version",
        request.param_version,
        "--profile",
        request.network_profile,
    ]

    if signing_key_file is not None:
        argv.extend(["--signing-key-file", str(signing_key_file)])

    if public_key_file is not None:
        argv.extend(["--public-key-file", str(public_key_file)])

    if request.force:
        argv.append("--force")

    command = _run_approved_builder_command(
        operation="build_capsule",
        argv=argv,
        cwd=request.cwd,
        timeout_seconds=request.timeout_seconds,
        env=_builder_env(request),
    )

    ok = command.ok and capsule_file.exists()

    if not ok and command.ok:
        command = BuilderCommandResult(
            ok=False,
            operation=command.operation,
            argv=command.argv,
            returncode=command.returncode,
            stdout=command.stdout,
            stderr=command.stderr,
            message=f"Builder completed but capsule file was not created: {capsule_file}",
            started_at=command.started_at,
            finished_at=command.finished_at,
            data={
                **dict(command.data),
                "field": "capsule_file",
                "capsule_file": str(capsule_file),
            },
        )

    return BuildCapsuleResult(
        ok=ok,
        capsule_file=capsule_file,
        capsule_id=request.capsule_id,
        capsule_version=request.capsule_version,
        channel=request.channel,
        app_version=request.app_version,
        param_version=request.param_version,
        network_profile=request.network_profile,
        command=command,
    )


def rebuild_capsule(request: BuildCapsuleRequest | None = None) -> BuildCapsuleResult:
    """Delete the requested output capsule if it exists, then build again."""

    request = request or BuildCapsuleRequest()

    capsule_output_dir = _ensure_dir(request.capsule_output_dir, "capsule_output_dir")
    capsule_file = _resolve_capsule_file(request, capsule_output_dir)

    if capsule_file.exists():
        capsule_file.unlink()

    rebuild_request = BuildCapsuleRequest(
        source_dir=request.source_dir,
        capsule_output_dir=request.capsule_output_dir,
        capsule_file=capsule_file,
        capsule_id=request.capsule_id,
        capsule_version=request.capsule_version,
        channel=request.channel,
        app_version=request.app_version,
        param_version=request.param_version,
        network_profile=request.network_profile,
        force=True,
        signing_key_file=request.signing_key_file,
        public_key_file=request.public_key_file,
        cwd=request.cwd,
        timeout_seconds=request.timeout_seconds,
        env=request.env,
    )

    return build_capsule(rebuild_request)


def verify_capsule(
    capsule_file: Path | str | VerifyCapsuleRequest,
    *,
    public_key_file: Path | str | None = None,
    cwd: Path | None = None,
    timeout_seconds: int = DEFAULT_VERIFY_TIMEOUT_SECONDS,
    env: Mapping[str, str] | None = None,
    raise_on_preflight_error: bool = False,
) -> VerifyCapsuleResult:
    """
    Verify a `.kxcap` capsule through the approved Builder command.

    Missing or invalid capsule files are returned as structured failed results
    by default so the Manager GUI can render a useful action result page instead
    of exposing a raw validation exception. Set `raise_on_preflight_error=True`
    for callers that need legacy exception behavior.
    """

    if isinstance(capsule_file, VerifyCapsuleRequest):
        request = capsule_file
        capsule_file = request.capsule_file
        public_key_file = (
            request.public_key_file if public_key_file is None else public_key_file
        )
        cwd = request.cwd if cwd is None else cwd
        timeout_seconds = request.timeout_seconds
        env = request.env if env is None else env
        raise_on_preflight_error = request.raise_on_preflight_error

    capsule_path = _resolve_path(capsule_file)
    public_key = _optional_existing_file(public_key_file, "public_key_file")

    argv = [
        "uv",
        "run",
        "kx-builder",
        "capsule",
        "verify",
        str(capsule_path),
    ]

    if public_key is not None:
        argv.extend(["--public-key-file", str(public_key)])

    preflight = _preflight_verify_capsule(
        capsule_path,
        argv=argv,
        raise_on_error=raise_on_preflight_error,
    )
    if preflight is not None:
        return VerifyCapsuleResult(
            ok=False,
            capsule_file=capsule_path,
            command=preflight,
        )

    command = _run_approved_builder_command(
        operation="verify_capsule",
        argv=argv,
        cwd=cwd,
        timeout_seconds=timeout_seconds,
        env=env or {},
    )

    return VerifyCapsuleResult(
        ok=command.ok,
        capsule_file=capsule_path,
        command=command,
    )


def build_default_capsule() -> BuildCapsuleResult:
    """Build the default development capsule."""

    return build_capsule(BuildCapsuleRequest())


def verify_default_capsule(
    *,
    raise_on_preflight_error: bool = False,
) -> VerifyCapsuleResult:
    """Verify the default development capsule."""

    request = BuildCapsuleRequest()
    capsule_file = _resolve_capsule_file(
        request,
        _ensure_dir(request.capsule_output_dir, "capsule_output_dir"),
    )
    return verify_capsule(
        capsule_file,
        public_key_file=request.public_key_file,
        raise_on_preflight_error=raise_on_preflight_error,
    )


def default_capsule_file() -> Path:
    """Return the default development capsule path."""

    request = BuildCapsuleRequest()
    return _resolve_capsule_file(
        request,
        _ensure_dir(request.capsule_output_dir, "capsule_output_dir"),
    )


def serialize_build_result(result: BuildCapsuleResult) -> dict[str, Any]:
    """Serialize build result for Manager routes or GUI actions."""

    return result.to_dict()


def serialize_verify_result(result: VerifyCapsuleResult) -> dict[str, Any]:
    """Serialize verify result for Manager routes or GUI actions."""

    return result.to_dict()


def _builder_env(request: BuildCapsuleRequest) -> dict[str, str]:
    env = {
        "KX_SOURCE_DIR": str(request.source_dir),
        "KX_CAPSULE_OUTPUT_DIR": str(request.capsule_output_dir),
        "KX_CAPSULE_FILE": str(
            _resolve_capsule_file(
                request,
                Path(request.capsule_output_dir).expanduser().resolve(),
            )
        ),
        "KX_CAPSULE_ID": request.capsule_id,
        "KX_CAPSULE_VERSION": request.capsule_version,
        "KX_CAPSULE_CHANNEL": request.channel,
        "KX_APP_VERSION": request.app_version,
        "KX_PARAM_VERSION": request.param_version,
        "KX_NETWORK_PROFILE": request.network_profile,
    }

    signing_key_file = _optional_path(request.signing_key_file)
    if signing_key_file is not None:
        env["KX_BUILDER_SIGNING_KEY_FILE"] = str(signing_key_file)

    public_key_file = _optional_path(request.public_key_file)
    if public_key_file is not None:
        env["KX_BUILDER_PUBLIC_KEY_FILE"] = str(public_key_file)

    env.update({str(key): str(value) for key, value in request.env.items()})
    return env


def _resolve_capsule_file(
    request: BuildCapsuleRequest,
    capsule_output_dir: Path,
) -> Path:
    if request.capsule_file is not None:
        capsule_file = Path(request.capsule_file).expanduser()
        if not capsule_file.is_absolute():
            capsule_file = capsule_output_dir / capsule_file
    else:
        capsule_file = capsule_output_dir / f"{request.capsule_id}.kxcap"

    _require_kxcap(capsule_file, "capsule_file")
    return capsule_file.resolve()


def _preflight_verify_capsule(
    capsule_file: Path,
    *,
    argv: list[str],
    raise_on_error: bool,
) -> BuilderCommandResult | None:
    if capsule_file.suffix != ".kxcap":
        message = f"capsule_file must end with .kxcap: {capsule_file}"
        if raise_on_error:
            raise BuilderServiceError(
                message,
                field="capsule_file",
                data={"capsule_file": str(capsule_file)},
            )

        return _validation_command_result(
            operation="verify_capsule",
            argv=argv,
            message=message,
            data={
                "field": "capsule_file",
                "capsule_file": str(capsule_file),
                "reason": "invalid_extension",
            },
        )

    if not capsule_file.exists():
        message = (
            "Capsule file does not exist. Build Capsule first or choose an "
            f"existing .kxcap file: {capsule_file}"
        )
        if raise_on_error:
            raise BuilderServiceError(
                message,
                field="capsule_file",
                data={
                    "capsule_file": str(capsule_file),
                    "suggested_action": "build_capsule",
                },
            )

        return _validation_command_result(
            operation="verify_capsule",
            argv=argv,
            message=message,
            data={
                "field": "capsule_file",
                "capsule_file": str(capsule_file),
                "reason": "missing_file",
                "suggested_action": "build_capsule",
                "suggested_action_label": "Build Capsule",
            },
        )

    if not capsule_file.is_file():
        message = f"capsule_file is not a file: {capsule_file}"
        if raise_on_error:
            raise BuilderServiceError(
                message,
                field="capsule_file",
                data={"capsule_file": str(capsule_file)},
            )

        return _validation_command_result(
            operation="verify_capsule",
            argv=argv,
            message=message,
            data={
                "field": "capsule_file",
                "capsule_file": str(capsule_file),
                "reason": "not_a_file",
            },
        )

    return None


def _run_approved_builder_command(
    *,
    operation: str,
    argv: list[str],
    cwd: Path | None,
    timeout_seconds: int,
    env: Mapping[str, str],
) -> BuilderCommandResult:
    if not argv:
        raise BuilderServiceError("argv must not be empty.")

    if timeout_seconds <= 0:
        raise BuilderServiceError(
            "timeout_seconds must be greater than zero.",
            field="timeout_seconds",
            data={"timeout_seconds": timeout_seconds},
        )

    _raise_if_unapproved_builder_command(argv)

    run_cwd = None
    if cwd is not None:
        run_cwd = _require_existing_dir(cwd, "cwd")

    run_env = os.environ.copy()
    run_env.update({str(key): str(value) for key, value in env.items()})

    started_at = _utc_now_iso()

    try:
        completed = subprocess.run(
            argv,
            cwd=str(run_cwd) if run_cwd else None,
            env=run_env,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        finished_at = _utc_now_iso()
        return BuilderCommandResult(
            ok=False,
            operation=operation,
            argv=tuple(argv),
            returncode=124,
            stdout=exc.stdout or "",
            stderr=exc.stderr or "",
            message=f"Builder command timed out after {timeout_seconds} seconds.",
            started_at=started_at,
            finished_at=finished_at,
            data={"timeout_seconds": timeout_seconds},
        )
    except FileNotFoundError as exc:
        finished_at = _utc_now_iso()
        return BuilderCommandResult(
            ok=False,
            operation=operation,
            argv=tuple(argv),
            returncode=127,
            stdout="",
            stderr=str(exc),
            message="Builder command could not be started. Is `uv` installed and on PATH?",
            started_at=started_at,
            finished_at=finished_at,
            data={},
        )

    finished_at = _utc_now_iso()
    ok = completed.returncode == 0

    return BuilderCommandResult(
        ok=ok,
        operation=operation,
        argv=tuple(argv),
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        message="Builder command completed." if ok else "Builder command failed.",
        started_at=started_at,
        finished_at=finished_at,
        data={
            "cwd": str(run_cwd) if run_cwd else None,
        },
    )


def _validation_command_result(
    *,
    operation: str,
    argv: list[str],
    message: str,
    data: Mapping[str, Any],
) -> BuilderCommandResult:
    checked_at = _utc_now_iso()
    return BuilderCommandResult(
        ok=False,
        operation=operation,
        argv=tuple(argv),
        returncode=2,
        stdout="",
        stderr=message,
        message=message,
        started_at=checked_at,
        finished_at=checked_at,
        data=data,
    )


def _raise_if_unapproved_builder_command(argv: list[str]) -> None:
    command_key = tuple(argv[:5])

    approved_prefixes = {
        ("uv", "run", "kx-builder", "capsule", "build"),
        ("uv", "run", "kx-builder", "capsule", "verify"),
    }

    if command_key not in approved_prefixes:
        raise BuilderServiceError(
            f"Unapproved builder command: {argv!r}",
            data={"argv": list(argv)},
        )


def _require_existing_dir(value: Path | str, field_name: str) -> Path:
    path = _resolve_path(value)

    if not path.exists():
        raise BuilderServiceError(
            f"{field_name} does not exist: {path}",
            field=field_name,
            data={field_name: str(path)},
        )

    if not path.is_dir():
        raise BuilderServiceError(
            f"{field_name} is not a directory: {path}",
            field=field_name,
            data={field_name: str(path)},
        )

    return path


def _ensure_dir(value: Path | str, field_name: str) -> Path:
    path = _resolve_path(value)

    if path.exists() and not path.is_dir():
        raise BuilderServiceError(
            f"{field_name} is not a directory: {path}",
            field=field_name,
            data={field_name: str(path)},
        )

    path.mkdir(parents=True, exist_ok=True)
    return path


def _require_existing_file(value: Path | str, field_name: str) -> Path:
    path = _resolve_path(value)

    if not path.exists():
        raise BuilderServiceError(
            f"{field_name} does not exist: {path}",
            field=field_name,
            data={field_name: str(path)},
        )

    if not path.is_file():
        raise BuilderServiceError(
            f"{field_name} is not a file: {path}",
            field=field_name,
            data={field_name: str(path)},
        )

    return path


def _optional_existing_file(value: Path | str | None, field_name: str) -> Path | None:
    path = _optional_path(value)

    if path is None:
        return None

    return _require_existing_file(path, field_name)


def _optional_path(value: Path | str | None) -> Path | None:
    if value is None:
        return None

    if isinstance(value, str) and not value.strip():
        return None

    return _resolve_path(value)


def _require_kxcap(value: Path | str, field_name: str) -> None:
    if Path(value).suffix != ".kxcap":
        raise BuilderServiceError(
            f"{field_name} must end with .kxcap: {value}",
            field=field_name,
            data={field_name: str(value)},
        )


def _resolve_path(value: Path | str) -> Path:
    return Path(value).expanduser().resolve()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


__all__ = [
    "BuildCapsuleRequest",
    "BuildCapsuleResult",
    "BuilderCommandResult",
    "BuilderServiceError",
    "DEFAULT_APP_VERSION",
    "DEFAULT_BUILDER_TIMEOUT_SECONDS",
    "DEFAULT_CAPSULE_ID",
    "DEFAULT_CAPSULE_VERSION",
    "DEFAULT_CHANNEL",
    "DEFAULT_NETWORK_PROFILE",
    "DEFAULT_PARAM_VERSION",
    "DEFAULT_VERIFY_TIMEOUT_SECONDS",
    "DEFAULT_WINDOWS_CAPSULE_FILE",
    "DEFAULT_WINDOWS_CAPSULE_OUTPUT_DIR",
    "DEFAULT_WINDOWS_PUBLIC_KEY_FILE",
    "DEFAULT_WINDOWS_SIGNING_DIR",
    "DEFAULT_WINDOWS_SIGNING_KEY_FILE",
    "DEFAULT_WINDOWS_SOURCE_DIR",
    "VerifyCapsuleRequest",
    "VerifyCapsuleResult",
    "build_capsule",
    "build_default_capsule",
    "default_capsule_file",
    "rebuild_capsule",
    "serialize_build_result",
    "serialize_verify_result",
    "verify_capsule",
    "verify_default_capsule",
]