"""
Canonical Konnaxion CLI entrypoint.

The public operator command is:

    kx

Canonical command groups:
- kx capsule build
- kx capsule verify
- kx capsule import
- kx artifact list/show/remove
- kx instance create
- kx instance start
- kx instance stop
- kx instance status
- kx instance logs
- kx instance backup
- kx instance restore
- kx instance restore-new
- kx instance update
- kx instance rollback
- kx instance health
- kx backup list
- kx backup verify
- kx backup test-restore
- kx security check
- kx network set-profile

This module is intentionally dependency-light. It uses argparse and delegates
runtime operations to the Konnaxion Capsule Manager / Agent boundary.

Important entrypoint compatibility:
- pyproject.toml may point console scripts at ``kx_cli.main:app``.
- tests may check for ``main`` or ``app``.
- both are provided here.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass, field, is_dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from kx_shared.konnaxion_constants import (
    APP_VERSION,
    CAPSULE_EXTENSION,
    CLI_NAME,
    DEFAULT_CHANNEL,
    DEFAULT_EXPOSURE_MODE,
    DEFAULT_INSTANCE_ID,
    DEFAULT_NETWORK_PROFILE,
    MANAGER_NAME,
    PARAM_VERSION,
    PUBLIC_CLI_COMMANDS,
    ExposureMode,
    NetworkProfile,
)


DEFAULT_MANAGER_URL = "http://127.0.0.1:8780"


@dataclass(frozen=True)
class CliContext:
    """Global CLI context passed to command handlers."""

    json_output: bool = False
    verbose: bool = False
    manager_url: str = DEFAULT_MANAGER_URL
    token: str = ""
    timeout_seconds: int = 30


@dataclass(frozen=True)
class CliResult:
    """Structured CLI result for human or JSON output."""

    ok: bool
    command: str
    message: str = ""
    data: Mapping[str, Any] = field(default_factory=dict)
    issues: tuple[Mapping[str, Any], ...] = ()

    @property
    def exit_code(self) -> int:
        return 0 if self.ok else 1


class CliError(RuntimeError):
    """User-facing CLI error with a stable exit code."""

    def __init__(self, message: str, *, exit_code: int = 2) -> None:
        self.exit_code = exit_code
        super().__init__(message)


class ManagerClient:
    """Small standard-library HTTP client for the Manager API."""

    def __init__(self, context: CliContext) -> None:
        self.context = context
        self.base_url = context.manager_url.rstrip("/")

    def get(self, path: str, *, params: Mapping[str, Any] | None = None) -> Any:
        return self.request("GET", path, params=params)

    def post(self, path: str, *, body: Mapping[str, Any] | None = None) -> Any:
        return self.request("POST", path, body=body)

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        body: Mapping[str, Any] | None = None,
    ) -> Any:
        query = ""

        if params:
            clean_params = {
                key: value
                for key, value in params.items()
                if value is not None and value != ""
            }
            if clean_params:
                query = "?" + urlencode(clean_params)

        url = f"{self.base_url}{path}{query}"
        payload = None

        headers = {
            "Accept": "application/json",
            "User-Agent": "kx-cli/main",
        }

        if body is not None:
            payload = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"

        if self.context.token:
            headers["Authorization"] = f"Bearer {self.context.token}"

        request = Request(url, data=payload, headers=headers, method=method)

        try:
            with urlopen(request, timeout=self.context.timeout_seconds) as response:
                raw_body = response.read().decode("utf-8")
        except HTTPError as exc:
            raw_error = exc.read().decode("utf-8", errors="replace")
            raise CliError(
                f"Manager API returned HTTP {exc.code}: {raw_error or exc.reason}",
                exit_code=1,
            ) from exc
        except URLError as exc:
            raise CliError(
                f"Cannot reach Konnaxion Capsule Manager at {self.base_url}: {exc}",
                exit_code=1,
            ) from exc
        except TimeoutError as exc:
            raise CliError("Konnaxion Capsule Manager request timed out.", exit_code=1) from exc

        if not raw_body:
            return {}

        try:
            return json.loads(raw_body)
        except json.JSONDecodeError as exc:
            raise CliError("Manager API returned invalid JSON.", exit_code=1) from exc


def create_parser() -> argparse.ArgumentParser:
    """Create the canonical kx parser."""

    parser = argparse.ArgumentParser(
        prog=CLI_NAME,
        description=f"{MANAGER_NAME} command-line interface.",
    )
    parser.add_argument("--version", action="store_true", help="Show Konnaxion CLI version.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON output.")
    parser.add_argument("--verbose", action="store_true", help="Print diagnostic information.")
    parser.add_argument(
        "--manager-url",
        default=os.getenv("KX_MANAGER_URL", DEFAULT_MANAGER_URL),
        help=f"Konnaxion Capsule Manager API URL. Default: {DEFAULT_MANAGER_URL}",
    )
    parser.add_argument(
        "--token",
        default=os.getenv("KX_MANAGER_TOKEN", ""),
        help="Bearer token for Konnaxion Capsule Manager API.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=_read_int_env("KX_CLI_TIMEOUT_SECONDS", 30),
        help="Manager API timeout in seconds.",
    )

    subparsers = parser.add_subparsers(dest="group")

    _add_capsule_commands(subparsers)
    _add_artifact_commands(subparsers)
    _add_instance_commands(subparsers)
    _add_backup_commands(subparsers)
    _add_security_commands(subparsers)
    _add_network_commands(subparsers)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the canonical kx CLI and return a process exit code."""

    parser = create_parser()
    args = parser.parse_args(argv)

    context = CliContext(
        json_output=bool(getattr(args, "json", False)),
        verbose=bool(getattr(args, "verbose", False)),
        manager_url=str(getattr(args, "manager_url", DEFAULT_MANAGER_URL)),
        token=str(getattr(args, "token", "")),
        timeout_seconds=int(getattr(args, "timeout", 30)),
    )

    if getattr(args, "version", False):
        result = CliResult(
            ok=True,
            command="kx --version",
            message=f"{CLI_NAME} {APP_VERSION}",
            data={
                "cli": CLI_NAME,
                "app_version": APP_VERSION,
                "param_version": PARAM_VERSION,
                "public_commands": list(PUBLIC_CLI_COMMANDS),
            },
        )
        return emit_result(result, context)

    if not getattr(args, "group", None):
        parser.print_help()
        return 2

    try:
        handler = getattr(args, "handler", None)
        if handler is None:
            raise CliError("No handler is registered for this command.")
        result = handler(args, context)
        return emit_result(result, context)
    except CliError as exc:
        result = CliResult(
            ok=False,
            command=_command_from_args(args),
            message=str(exc),
        )
        emit_result(result, context, stream=sys.stderr)
        return exc.exit_code


def app() -> None:
    """
    Console-script compatible callable.

    This keeps ``kx = "kx_cli.main:app"`` working in pyproject.toml while the
    implementation remains argparse-based.
    """

    raise SystemExit(main())


def run(argv: Sequence[str] | None = None) -> int:
    """Backward-compatible alias for custom runners/tests."""

    return main(argv)


def _add_capsule_commands(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    capsule = subparsers.add_parser("capsule", help="Build, verify, and import capsules.")
    capsule_sub = capsule.add_subparsers(dest="capsule_command", required=True)

    build = capsule_sub.add_parser("build", help="Build a signed .kxcap capsule.")
    build.add_argument("source", nargs="?", default=".")
    build.add_argument("--output", "-o", type=Path, default=None)
    build.add_argument("--channel", default=DEFAULT_CHANNEL)
    build.add_argument("--capsule-id", default="")
    build.set_defaults(handler=cmd_capsule_build)

    verify = capsule_sub.add_parser("verify", help="Verify a .kxcap capsule.")
    verify.add_argument("capsule", type=Path)
    verify.set_defaults(handler=cmd_capsule_verify)

    import_cmd = capsule_sub.add_parser("import", help="Import a verified .kxcap capsule.")
    import_cmd.add_argument("capsule", type=Path)
    import_cmd.add_argument("--instance-id", default=DEFAULT_INSTANCE_ID)
    import_cmd.set_defaults(handler=cmd_capsule_import)


def _add_artifact_commands(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    artifact = subparsers.add_parser(
        "artifact",
        help="Discover and remove installed Capsule artifacts/products.",
    )
    artifact_sub = artifact.add_subparsers(dest="artifact_command", required=True)

    list_cmd = artifact_sub.add_parser("list", help="List installed artifacts.")
    list_cmd.add_argument("--kind", choices=("library", "product", "composition_host"), default="")
    list_cmd.add_argument("--products-only", action="store_true")
    list_cmd.add_argument("--composition-candidates", action="store_true")
    list_cmd.set_defaults(handler=cmd_artifact_list)

    show_cmd = artifact_sub.add_parser("show", help="Show one installed artifact.")
    show_cmd.add_argument("artifact_id")
    show_cmd.set_defaults(handler=cmd_artifact_show)

    remove_cmd = artifact_sub.add_parser("remove", help="Remove an installed artifact after dependency guards pass.")
    remove_cmd.add_argument("artifact_id")
    remove_cmd.add_argument("--drop-data", action="store_true", help="Declare that product data does not need preservation.")
    remove_cmd.set_defaults(handler=cmd_artifact_remove)


def _add_instance_commands(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """
    Register instance commands.

    Prefer the dedicated ``kx_cli.instance`` module so the root CLI does not
    duplicate the instance command surface.
    """

    try:
        from kx_cli.instance import register_instance_commands

        register_instance_commands(subparsers)
    except Exception:
        # Fallback keeps import/help usable during partial generation.
        instance = subparsers.add_parser("instance", help="Manage Konnaxion Instances.")
        instance_sub = instance.add_subparsers(dest="instance_command", required=True)

        for command in (
            "create",
            "start",
            "stop",
            "status",
            "logs",
            "backup",
            "restore",
            "restore-new",
            "update",
            "rollback",
            "health",
        ):
            sub = instance_sub.add_parser(command)
            sub.add_argument("instance_id", nargs="?", default=DEFAULT_INSTANCE_ID)
            sub.set_defaults(handler=cmd_instance_fallback)


def _add_backup_commands(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    backup = subparsers.add_parser("backup", help="List, verify, and test-restore backups.")
    backup_sub = backup.add_subparsers(dest="backup_command", required=True)

    list_cmd = backup_sub.add_parser("list", help="List backups.")
    list_cmd.add_argument("--instance-id", default="")
    list_cmd.add_argument("--class", dest="backup_class", default="")
    list_cmd.add_argument("--limit", type=int, default=50)
    list_cmd.set_defaults(handler=cmd_backup_list)

    verify = backup_sub.add_parser("verify", help="Verify a backup artifact.")
    verify.add_argument("backup_id")
    verify.add_argument("--deep", action="store_true")
    verify.set_defaults(handler=cmd_backup_verify)

    test_restore = backup_sub.add_parser("test-restore", help="Run a test restore of a backup.")
    test_restore.add_argument("backup_id")
    test_restore.add_argument("--new-instance-id", default="")
    test_restore.set_defaults(handler=cmd_backup_test_restore)


def _add_security_commands(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    security = subparsers.add_parser("security", help="Run Security Gate checks.")
    security_sub = security.add_subparsers(dest="security_command", required=True)

    check = security_sub.add_parser("check", help="Run the Security Gate for an instance.")
    check.add_argument("instance_id", nargs="?", default=DEFAULT_INSTANCE_ID)
    check.add_argument("--force", action="store_true")
    check.set_defaults(handler=cmd_security_check)


def _add_network_commands(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    network = subparsers.add_parser("network", help="Manage network profiles.")
    network_sub = network.add_subparsers(dest="network_command", required=True)

    set_profile = network_sub.add_parser("set-profile", help="Set a canonical network profile.")
    set_profile.add_argument("instance_id")
    set_profile.add_argument("profile", choices=[profile.value for profile in NetworkProfile])
    set_profile.add_argument(
        "--exposure",
        choices=[mode.value for mode in ExposureMode],
        default=None,
    )
    set_profile.add_argument("--host", default="")
    set_profile.add_argument("--public-expires-at", default="")
    set_profile.set_defaults(handler=cmd_network_set_profile)


def cmd_capsule_build(args: argparse.Namespace, context: CliContext) -> CliResult:
    """Delegate capsule build to kx_builder when available."""

    try:
        from kx_builder.main import main as builder_main
    except Exception as exc:
        raise CliError(f"kx_builder.main is not available: {exc}", exit_code=1) from exc

    builder_args = ["build", str(args.source)]

    if args.output is not None:
        builder_args.extend(["--output", str(args.output)])

    if args.channel:
        builder_args.extend(["--channel", args.channel])

    if args.capsule_id:
        builder_args.extend(["--capsule-id", args.capsule_id])

    exit_code = int(builder_main(builder_args))

    return CliResult(
        ok=exit_code == 0,
        command="kx capsule build",
        message="Capsule build completed." if exit_code == 0 else "Capsule build failed.",
        data={"exit_code": exit_code},
    )


def cmd_capsule_verify(args: argparse.Namespace, context: CliContext) -> CliResult:
    capsule_path = _require_capsule_path(args.capsule)

    try:
        from kx_builder.main import main as builder_main
    except Exception:
        client = ManagerClient(context)
        data = client.post("/capsules/verify", body={"path": str(capsule_path)})
        return _result_from_response("kx capsule verify", data)

    exit_code = int(builder_main(["verify", str(capsule_path)]))

    return CliResult(
        ok=exit_code == 0,
        command="kx capsule verify",
        message="Capsule verification completed." if exit_code == 0 else "Capsule verification failed.",
        data={"exit_code": exit_code, "capsule": str(capsule_path)},
    )


def cmd_capsule_import(args: argparse.Namespace, context: CliContext) -> CliResult:
    capsule_path = _require_capsule_path(args.capsule)
    client = ManagerClient(context)
    data = client.post(
        "/capsules/import",
        body={
            "capsule_path": str(capsule_path),
            "instance_id": args.instance_id,
        },
    )
    return _result_from_response("kx capsule import", data)


def cmd_artifact_list(args: argparse.Namespace, context: CliContext) -> CliResult:
    client = ManagerClient(context)
    data = client.get(
        "/v1/artifacts",
        params={
            "kind": args.kind,
            "products_only": args.products_only,
            "composition_candidates_only": args.composition_candidates,
        },
    )
    return _result_from_response("kx artifact list", data)


def cmd_artifact_show(args: argparse.Namespace, context: CliContext) -> CliResult:
    client = ManagerClient(context)
    data = client.get(f"/v1/artifacts/{args.artifact_id}")
    return _result_from_response("kx artifact show", data)


def cmd_artifact_remove(args: argparse.Namespace, context: CliContext) -> CliResult:
    client = ManagerClient(context)
    data = client.post(
        f"/v1/artifacts/{args.artifact_id}/remove",
        body={"preserve_data": not args.drop_data},
    )
    return _result_from_response("kx artifact remove", data)


def cmd_instance_fallback(args: argparse.Namespace, context: CliContext) -> CliResult:
    command = getattr(args, "instance_command", "")
    instance_id = getattr(args, "instance_id", DEFAULT_INSTANCE_ID)
    client = ManagerClient(context)

    if command == "status":
        data = client.get(f"/instances/{instance_id}")
    elif command == "health":
        data = client.get(f"/instances/{instance_id}/health")
    else:
        data = client.post(f"/instances/{instance_id}/{command}", body={})

    return _result_from_response(f"kx instance {command}", data)


def cmd_backup_list(args: argparse.Namespace, context: CliContext) -> CliResult:
    client = ManagerClient(context)
    data = client.get(
        "/backups",
        params={
            "instance_id": args.instance_id,
            "backup_class": args.backup_class,
            "limit": args.limit,
        },
    )
    return _result_from_response("kx backup list", data)


def cmd_backup_verify(args: argparse.Namespace, context: CliContext) -> CliResult:
    client = ManagerClient(context)
    data = client.post(
        f"/backups/{args.backup_id}/verify",
        body={"deep": args.deep},
    )
    return _result_from_response("kx backup verify", data)


def cmd_backup_test_restore(args: argparse.Namespace, context: CliContext) -> CliResult:
    client = ManagerClient(context)
    data = client.post(
        "/backups/test-restore",
        body={
            "backup_id": args.backup_id,
            "new_instance_id": args.new_instance_id,
        },
    )
    return _result_from_response("kx backup test-restore", data)


def cmd_security_check(args: argparse.Namespace, context: CliContext) -> CliResult:
    client = ManagerClient(context)
    data = client.post(
        f"/instances/{args.instance_id}/security/check",
        body={"force": args.force},
    )
    return _result_from_response("kx security check", data)


def cmd_network_set_profile(args: argparse.Namespace, context: CliContext) -> CliResult:
    client = ManagerClient(context)
    data = client.post(
        f"/instances/{args.instance_id}/network/profile",
        body={
            "network_profile": args.profile,
            "exposure_mode": args.exposure,
            "host": args.host,
            "public_mode_expires_at": args.public_expires_at,
        },
    )
    return _result_from_response("kx network set-profile", data)


def emit_result(
    result: CliResult,
    context: CliContext,
    *,
    stream: Any = sys.stdout,
) -> int:
    """Print a result and return its exit code."""

    payload = serialize(result)

    if context.json_output:
        print(json.dumps(payload, indent=2, sort_keys=True), file=stream)
        return result.exit_code

    if result.message:
        print(result.message, file=stream)

    if result.data:
        print_mapping(result.data, stream=stream)

    if result.issues:
        print("issues:", file=stream)
        for issue in result.issues:
            print_mapping(issue, indent=2, stream=stream)

    return result.exit_code


def print_mapping(data: Mapping[str, Any], *, indent: int = 0, stream: Any = sys.stdout) -> None:
    """Print a readable mapping."""

    prefix = " " * indent

    priority_keys = (
        "ok",
        "operation",
        "instance_id",
        "new_instance_id",
        "backup_id",
        "source_backup_id",
        "capsule_id",
        "capsule_version",
        "status",
        "state",
        "network_profile",
        "exposure_mode",
        "url",
        "host",
        "message",
    )

    printed: set[str] = set()

    for key in priority_keys:
        if key in data:
            print(f"{prefix}{key}: {format_value(data[key])}", file=stream)
            printed.add(key)

    for key in sorted(k for k in data.keys() if k not in printed):
        value = data[key]
        if isinstance(value, Mapping):
            print(f"{prefix}{key}:", file=stream)
            print_mapping(value, indent=indent + 2, stream=stream)
        elif isinstance(value, list):
            print(f"{prefix}{key}: {json.dumps(value, sort_keys=True)}", file=stream)
        else:
            print(f"{prefix}{key}: {format_value(value)}", file=stream)


def format_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"

    if value is None:
        return ""

    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, sort_keys=True)

    return str(value)


def serialize(value: Any) -> Any:
    """Convert dataclasses and nested values to JSON-safe objects."""

    if is_dataclass(value):
        return serialize(asdict(value))

    if isinstance(value, Mapping):
        return {str(key): serialize(item) for key, item in value.items()}

    if isinstance(value, (list, tuple)):
        return [serialize(item) for item in value]

    if isinstance(value, Path):
        return str(value)

    return value


def _result_from_response(command: str, response: Any) -> CliResult:
    if isinstance(response, Mapping):
        ok = bool(response.get("ok", True))
        message = str(response.get("message", "")) or f"{command} completed."
        return CliResult(ok=ok, command=command, message=message, data=dict(response))

    if isinstance(response, list):
        return CliResult(
            ok=True,
            command=command,
            message=f"{command} completed.",
            data={"items": response},
        )

    return CliResult(
        ok=True,
        command=command,
        message=f"{command} completed.",
        data={"result": response},
    )


def _require_capsule_path(path: Path) -> Path:
    resolved = path.expanduser().resolve()

    if not resolved.exists():
        raise CliError(f"Capsule does not exist: {resolved}", exit_code=1)

    if not resolved.is_file():
        raise CliError(f"Capsule path is not a file: {resolved}", exit_code=1)

    if resolved.suffix != CAPSULE_EXTENSION:
        raise CliError(
            f"Capsule must use {CAPSULE_EXTENSION} extension: {resolved}",
            exit_code=1,
        )

    return resolved


def _command_from_args(args: argparse.Namespace) -> str:
    parts = [CLI_NAME]

    for attr in (
        "group",
        "capsule_command",
        "artifact_command",
        "instance_command",
        "backup_command",
        "security_command",
        "network_command",
    ):
        value = getattr(args, attr, None)
        if value:
            parts.append(str(value))

    return " ".join(parts)


def _read_int_env(key: str, default: int) -> int:
    raw = os.getenv(key)

    if raw is None or raw.strip() == "":
        return default

    try:
        return int(raw)
    except ValueError:
        return default


if __name__ == "__main__":
    raise SystemExit(main())
