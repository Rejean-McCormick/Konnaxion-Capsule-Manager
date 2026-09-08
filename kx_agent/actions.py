# kx_agent/actions.py

"""
Allowlisted Konnaxion Agent actions.

The Konnaxion Capsule Manager must not execute privileged system operations
directly. It sends a constrained action request to the Konnaxion Agent, and the
Agent dispatches only explicitly registered handlers.

This module intentionally contains the action contract and dispatcher only.
Implementation modules such as runtime.compose, runtime.docker, backups.backup,
network.profiles, and security.gate should register concrete handlers here.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, is_dataclass
from enum import Enum, StrEnum
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping, MutableMapping, Protocol
from uuid import uuid4

from kx_shared.errors import (
    AgentActionNotAllowedError,
    AgentError,
    InvalidVariableError,
    SecurityGateBlockingError,
    as_error_payload,
)


# ---------------------------------------------------------------------
# Canonical action names
# ---------------------------------------------------------------------


class AgentActionName(StrEnum):
    """Canonical allowlisted Agent actions."""

    CAPSULE_VERIFY = "capsule.verify"
    CAPSULE_IMPORT = "capsule.import"
    ARTIFACT_REMOVE = "artifact.remove"

    INSTANCE_CREATE = "instance.create"
    INSTANCE_START = "instance.start"
    INSTANCE_STOP = "instance.stop"
    INSTANCE_STATUS = "instance.status"
    INSTANCE_LOGS = "instance.logs"
    INSTANCE_BACKUP = "instance.backup"
    INSTANCE_RESTORE = "instance.restore"
    INSTANCE_RESTORE_NEW = "instance.restore_new"
    INSTANCE_UPDATE = "instance.update"
    INSTANCE_ROLLBACK = "instance.rollback"
    INSTANCE_HEALTH = "instance.health"

    BACKUP_LIST = "backup.list"
    BACKUP_VERIFY = "backup.verify"
    BACKUP_TEST_RESTORE = "backup.test_restore"

    SECURITY_CHECK = "security.check"

    NETWORK_SET_PROFILE = "network.set_profile"
    NETWORK_DISABLE_PUBLIC = "network.disable_public"
    NETWORK_EXPIRE_TEMPORARY_PUBLIC = "network.expire_temporary_public"


ALLOWLISTED_ACTIONS: frozenset[str] = frozenset(action.value for action in AgentActionName)


MUTATING_ACTIONS: frozenset[str] = frozenset(
    {
        AgentActionName.CAPSULE_IMPORT.value,
        AgentActionName.ARTIFACT_REMOVE.value,
        AgentActionName.INSTANCE_CREATE.value,
        AgentActionName.INSTANCE_START.value,
        AgentActionName.INSTANCE_STOP.value,
        AgentActionName.INSTANCE_BACKUP.value,
        AgentActionName.INSTANCE_RESTORE.value,
        AgentActionName.INSTANCE_RESTORE_NEW.value,
        AgentActionName.INSTANCE_UPDATE.value,
        AgentActionName.INSTANCE_ROLLBACK.value,
        AgentActionName.BACKUP_VERIFY.value,
        AgentActionName.BACKUP_TEST_RESTORE.value,
        AgentActionName.NETWORK_SET_PROFILE.value,
        AgentActionName.NETWORK_DISABLE_PUBLIC.value,
        AgentActionName.NETWORK_EXPIRE_TEMPORARY_PUBLIC.value,
    }
)


SECURITY_GATED_ACTIONS: frozenset[str] = frozenset(
    {
        AgentActionName.CAPSULE_IMPORT.value,
        AgentActionName.INSTANCE_CREATE.value,
        AgentActionName.INSTANCE_START.value,
        AgentActionName.INSTANCE_UPDATE.value,
        AgentActionName.INSTANCE_RESTORE.value,
        AgentActionName.INSTANCE_RESTORE_NEW.value,
        AgentActionName.INSTANCE_ROLLBACK.value,
        AgentActionName.BACKUP_TEST_RESTORE.value,
        AgentActionName.NETWORK_SET_PROFILE.value,
    }
)


# ---------------------------------------------------------------------
# API route-name aliases
# ---------------------------------------------------------------------


API_ACTION_ALIASES: dict[str, str] = {
    "capsule_import": AgentActionName.CAPSULE_IMPORT.value,
    "capsule_verify": AgentActionName.CAPSULE_VERIFY.value,
    "artifact_remove": AgentActionName.ARTIFACT_REMOVE.value,
    "instance_create": AgentActionName.INSTANCE_CREATE.value,
    "instance_start": AgentActionName.INSTANCE_START.value,
    "instance_stop": AgentActionName.INSTANCE_STOP.value,
    "instance_status": AgentActionName.INSTANCE_STATUS.value,
    "instance_logs": AgentActionName.INSTANCE_LOGS.value,
    "instance_backup": AgentActionName.INSTANCE_BACKUP.value,
    "instance_restore": AgentActionName.INSTANCE_RESTORE.value,
    "instance_restore_new": AgentActionName.INSTANCE_RESTORE_NEW.value,
    "instance_update": AgentActionName.INSTANCE_UPDATE.value,
    "instance_rollback": AgentActionName.INSTANCE_ROLLBACK.value,
    "instance_health": AgentActionName.INSTANCE_HEALTH.value,
    "backup_list": AgentActionName.BACKUP_LIST.value,
    "backup_verify": AgentActionName.BACKUP_VERIFY.value,
    "backup_test_restore": AgentActionName.BACKUP_TEST_RESTORE.value,
    "security_check": AgentActionName.SECURITY_CHECK.value,
    "network_set_profile": AgentActionName.NETWORK_SET_PROFILE.value,
    "network_disable_public": AgentActionName.NETWORK_DISABLE_PUBLIC.value,
    "network_expire_temporary_public": AgentActionName.NETWORK_EXPIRE_TEMPORARY_PUBLIC.value,
}


# ---------------------------------------------------------------------
# Action DTOs
# ---------------------------------------------------------------------


class ActionStatus(StrEnum):
    """Stable action lifecycle result values."""

    ACCEPTED = "accepted"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"
    NOT_ALLOWED = "not_allowed"


@dataclass(slots=True, frozen=True)
class ActionRequest:
    """Manager -> Agent action request."""

    action: str
    params: Mapping[str, Any] = field(default_factory=dict)
    request_id: str = field(default_factory=lambda: str(uuid4()))
    actor: str | None = None
    dry_run: bool = False
    require_security_gate: bool = True

    def normalized_action(self) -> str:
        return normalize_action_name(self.action)

    def is_mutating(self) -> bool:
        return self.normalized_action() in MUTATING_ACTIONS

    def is_security_gated(self) -> bool:
        return self.normalized_action() in SECURITY_GATED_ACTIONS


@dataclass(slots=True, frozen=True)
class ActionResult:
    """Agent -> Manager action result."""

    action: str
    status: ActionStatus
    request_id: str
    message: str = ""
    data: Mapping[str, Any] = field(default_factory=dict)
    error: Mapping[str, Any] | None = None

    @classmethod
    def succeeded(
        cls,
        request: ActionRequest,
        *,
        message: str = "",
        data: Mapping[str, Any] | None = None,
    ) -> "ActionResult":
        return cls(
            action=request.normalized_action(),
            status=ActionStatus.SUCCEEDED,
            request_id=request.request_id,
            message=message,
            data=data or {},
        )

    @classmethod
    def accepted(
        cls,
        request: ActionRequest,
        *,
        message: str = "",
        data: Mapping[str, Any] | None = None,
    ) -> "ActionResult":
        return cls(
            action=request.normalized_action(),
            status=ActionStatus.ACCEPTED,
            request_id=request.request_id,
            message=message,
            data=data or {},
        )

    @classmethod
    def failed(cls, request: ActionRequest, error: BaseException) -> "ActionResult":
        status = ActionStatus.FAILED
        if isinstance(error, AgentActionNotAllowedError):
            status = ActionStatus.NOT_ALLOWED
        elif isinstance(error, SecurityGateBlockingError):
            status = ActionStatus.BLOCKED

        payload = as_error_payload(error)
        return cls(
            action=request.normalized_action(),
            status=status,
            request_id=request.request_id,
            message=str(payload.get("message") or error),
            error=payload,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "status": self.status.value,
            "request_id": self.request_id,
            "message": self.message,
            "data": _json_safe(dict(self.data)),
            "error": _json_safe(dict(self.error)) if self.error is not None else None,
        }


# ---------------------------------------------------------------------
# Handler protocol
# ---------------------------------------------------------------------


class AgentActionHandler(Protocol):
    """Callable contract for concrete Agent action handlers."""

    def __call__(self, request: ActionRequest) -> ActionResult:
        ...


PreDispatchHook = Callable[[ActionRequest], None]
PostDispatchHook = Callable[[ActionRequest, ActionResult], None]


# ---------------------------------------------------------------------
# Registry and dispatcher
# ---------------------------------------------------------------------


class AgentActionRegistry:
    """Registry of allowlisted action handlers."""

    def __init__(self) -> None:
        self._handlers: MutableMapping[str, AgentActionHandler] = {}

    def register(self, action: AgentActionName | str, handler: AgentActionHandler) -> None:
        normalized = normalize_action_name(action)

        if normalized not in ALLOWLISTED_ACTIONS:
            raise AgentActionNotAllowedError(
                f"Agent action is not allowlisted: {normalized}",
                {"action": normalized},
            )

        if not callable(handler):
            raise InvalidVariableError(
                "Agent action handler must be callable.",
                {"action": normalized, "handler": repr(handler)},
            )

        self._handlers[normalized] = handler

    def unregister(self, action: AgentActionName | str) -> None:
        self._handlers.pop(normalize_action_name(action), None)

    def get(self, action: AgentActionName | str) -> AgentActionHandler:
        normalized = normalize_action_name(action)

        if normalized not in ALLOWLISTED_ACTIONS:
            raise AgentActionNotAllowedError(
                f"Agent action is not allowlisted: {normalized}",
                {"action": normalized},
            )

        try:
            return self._handlers[normalized]
        except KeyError as exc:
            raise AgentActionNotAllowedError(
                f"Agent action has no registered handler: {normalized}",
                {"action": normalized},
            ) from exc

    def registered_actions(self) -> tuple[str, ...]:
        return tuple(sorted(self._handlers))

    def allowlisted_actions(self) -> tuple[str, ...]:
        return tuple(sorted(ALLOWLISTED_ACTIONS))


class ActionDispatcher:
    """Safe dispatcher for Manager -> Agent requests."""

    def __init__(
        self,
        registry: AgentActionRegistry | None = None,
        *,
        pre_hooks: list[PreDispatchHook] | None = None,
        post_hooks: list[PostDispatchHook] | None = None,
        raise_errors: bool = False,
    ) -> None:
        self.registry = registry or AgentActionRegistry()
        self.pre_hooks = pre_hooks or []
        self.post_hooks = post_hooks or []
        self.raise_errors = raise_errors

    def dispatch(self, request: ActionRequest) -> ActionResult:
        normalized_request = normalize_request(request)

        try:
            validate_request(normalized_request)

            for hook in self.pre_hooks:
                hook(normalized_request)

            handler = self.registry.get(normalized_request.action)
            result = handler(normalized_request)

            if result.action != normalized_request.action:
                raise AgentError(
                    "Agent action handler returned mismatched action.",
                    {
                        "requested_action": normalized_request.action,
                        "returned_action": result.action,
                    },
                )

            for hook in self.post_hooks:
                hook(normalized_request, result)

            return result

        except BaseException as exc:
            if self.raise_errors:
                raise

            failed = ActionResult.failed(normalized_request, exc)

            for hook in self.post_hooks:
                try:
                    hook(normalized_request, failed)
                except Exception:
                    pass

            return failed


# ---------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------


def normalize_action_name(action: AgentActionName | str) -> str:
    if isinstance(action, AgentActionName):
        return action.value

    if not isinstance(action, str):
        raise InvalidVariableError(
            "Agent action name must be a string.",
            {"action": repr(action), "type": type(action).__name__},
        )

    raw = action.strip().lower()

    if not raw:
        raise InvalidVariableError("Agent action name is required.", {"action": action})

    if raw in API_ACTION_ALIASES:
        return API_ACTION_ALIASES[raw]

    if raw in ALLOWLISTED_ACTIONS:
        return raw

    dotted = raw.replace("_", ".")
    if dotted in ALLOWLISTED_ACTIONS:
        return dotted

    return raw


def canonicalize_api_action(action: str) -> str:
    """Map Agent API route action names to internal canonical action names."""

    raw = str(action).strip()
    if raw in API_ACTION_ALIASES:
        return API_ACTION_ALIASES[raw]

    return normalize_action_name(raw)


def normalize_request(request: ActionRequest) -> ActionRequest:
    return ActionRequest(
        action=request.normalized_action(),
        params=dict(request.params),
        request_id=request.request_id,
        actor=request.actor,
        dry_run=request.dry_run,
        require_security_gate=request.require_security_gate,
    )


def validate_request(request: ActionRequest) -> None:
    action = request.normalized_action()

    if action not in ALLOWLISTED_ACTIONS:
        raise AgentActionNotAllowedError(
            f"Agent action is not allowlisted: {action}",
            {"action": action, "allowlisted_actions": sorted(ALLOWLISTED_ACTIONS)},
        )

    if not isinstance(request.params, Mapping):
        raise InvalidVariableError(
            "Agent action params must be a mapping.",
            {"action": action, "params_type": type(request.params).__name__},
        )

    _reject_raw_command_params(action, request.params)


def _reject_raw_command_params(action: str, params: Mapping[str, Any]) -> None:
    """Reject obvious attempts to smuggle shell commands through the API."""

    forbidden_keys = {
        "cmd",
        "command",
        "shell",
        "shell_command",
        "exec",
        "subprocess",
        "script",
        "bash",
        "sh",
        "powershell",
    }

    present = sorted(key for key in params if key.lower() in forbidden_keys)
    if present:
        raise AgentActionNotAllowedError(
            "Raw command parameters are not allowed in Agent action requests.",
            {"action": action, "forbidden_params": present},
        )


# ---------------------------------------------------------------------
# Convenience decorators and default registry
# ---------------------------------------------------------------------


default_registry = AgentActionRegistry()


def register_action(action: AgentActionName | str) -> Callable[[AgentActionHandler], AgentActionHandler]:
    """Register an action handler in the process default registry."""

    def decorator(handler: AgentActionHandler) -> AgentActionHandler:
        default_registry.register(action, handler)
        return handler

    return decorator


def make_dispatcher(*, raise_errors: bool = False) -> ActionDispatcher:
    return ActionDispatcher(default_registry, raise_errors=raise_errors)


# ---------------------------------------------------------------------
# Agent API adapter
# ---------------------------------------------------------------------


class AgentAPIActionHandler:
    """Async adapter used by kx_agent.api."""

    def __init__(self, dispatcher: ActionDispatcher | None = None) -> None:
        self.dispatcher = dispatcher or make_default_dispatcher()

    async def run(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        request = ActionRequest(
            action=canonicalize_api_action(action),
            params=dict(payload),
        )

        result = self.dispatcher.dispatch(request)
        response = action_result_to_api_dict(result)

        # Preserve API route-style action names for the public HTTP schema.
        response["action"] = str(action)
        return response


def action_result_to_api_dict(result: ActionResult) -> dict[str, Any]:
    data = dict(result.data)
    error = dict(result.error or {})

    ok = result.status in {
        ActionStatus.ACCEPTED,
        ActionStatus.SUCCEEDED,
    }

    message = result.message
    if not message and error:
        message = str(error.get("message") or error.get("error") or "")

    return {
        "ok": ok,
        "action": result.action,
        "instance_id": data.get("instance_id"),
        "state": data.get("state"),
        "security_status": data.get("security_status"),
        "restore_status": data.get("restore_status"),
        "rollback_status": data.get("rollback_status"),
        "message": message,
        "data": _json_safe(data if not error else {**data, "error": error}),
    }


def make_default_registry() -> AgentActionRegistry:
    registry = AgentActionRegistry()
    register_capsule_action_handlers(registry)
    register_artifact_action_handlers(registry)
    register_instance_action_handlers(registry)
    register_security_action_handlers(registry)
    register_backup_action_handlers(registry)
    register_network_action_handlers(registry)
    return registry


def make_default_dispatcher(*, raise_errors: bool = False) -> ActionDispatcher:
    return ActionDispatcher(
        make_default_registry(),
        raise_errors=raise_errors,
    )


def make_api_action_handler() -> AgentAPIActionHandler:
    return AgentAPIActionHandler(make_default_dispatcher())


# ---------------------------------------------------------------------
# Capsule action handlers
# ---------------------------------------------------------------------


def register_capsule_action_handlers(
    registry: AgentActionRegistry | None = None,
) -> AgentActionRegistry:
    target = registry or default_registry

    target.register(AgentActionName.CAPSULE_VERIFY, handle_capsule_verify)
    target.register(AgentActionName.CAPSULE_IMPORT, handle_capsule_import)

    return target


def handle_capsule_verify(request: ActionRequest) -> ActionResult:
    """Verify a capsule file through the Agent capsule verifier."""

    params = dict(request.params)
    capsule_path = _require_text(params, "capsule_path", "capsule_file", "remote_capsule_path")

    from kx_agent.capsules.verifier import verify_capsule

    report = verify_capsule(Path(capsule_path))
    data = _object_to_mapping(report)

    ok = bool(
        data.get("accepted")
        or data.get("ok")
        or data.get("valid")
        or str(data.get("status") or "").lower() in {"pass", "passed", "ok", "valid"}
    )

    return ActionResult(
        action=request.normalized_action(),
        status=ActionStatus.SUCCEEDED if ok else ActionStatus.FAILED,
        request_id=request.request_id,
        message="Capsule verified." if ok else "Capsule verification failed.",
        data={
            "capsule_path": capsule_path,
            "verified": ok,
            "report": data,
        },
        error=None if ok else {"message": "Capsule verification failed.", "report": data},
    )


def handle_capsule_import(request: ActionRequest) -> ActionResult:
    """Import a verified capsule into canonical Agent capsule storage."""

    params = dict(request.params)
    capsule_path = _require_text(params, "capsule_path", "capsule_file", "remote_capsule_path")

    from kx_agent.capsules.importer import CapsuleImportOptions, import_capsule

    options = CapsuleImportOptions(
        verify=_bool_param(params.get("verify"), default=True),
        overwrite=_bool_param(params.get("overwrite"), default=True),
        prepare_extract_dir=True,
        capsule_id=_optional_text(params, "capsule_id"),
    )

    result = import_capsule(Path(capsule_path), options)
    data = _object_to_mapping(result)

    instance_id = _optional_text(params, "instance_id")
    if instance_id:
        data["instance_id"] = instance_id

    network_profile = _optional_text(params, "network_profile")
    if network_profile:
        data["network_profile"] = network_profile

    exposure_mode = _optional_text(params, "exposure_mode")
    if exposure_mode:
        data["exposure_mode"] = exposure_mode

    return ActionResult.succeeded(
        request,
        message="Capsule imported.",
        data=data,
    )


# ---------------------------------------------------------------------
# Installed artifact action handlers
# ---------------------------------------------------------------------


def register_artifact_action_handlers(
    registry: AgentActionRegistry | None = None,
) -> AgentActionRegistry:
    target = registry or default_registry
    target.register(AgentActionName.ARTIFACT_REMOVE, handle_artifact_remove)
    return target


def handle_artifact_remove(request: ActionRequest) -> ActionResult:
    """Remove an installed artifact after dependency/runtime guards pass."""

    params = dict(request.params)
    artifact_id = _require_text(params, "artifact_id", "product_id")
    preserve_data = _bool_param(params.get("preserve_data"), default=True)

    from kx_agent.artifacts.registry import remove_artifact

    data = remove_artifact(
        artifact_id,
        preserve_data=preserve_data,
        remove_capsule_files=True,
    )
    return ActionResult.succeeded(
        request,
        message=f"Artifact removed: {artifact_id}.",
        data=data,
    )


# ---------------------------------------------------------------------
# Instance action handlers
# ---------------------------------------------------------------------


def register_instance_action_handlers(
    registry: AgentActionRegistry | None = None,
) -> AgentActionRegistry:
    target = registry or default_registry

    target.register(AgentActionName.INSTANCE_CREATE, handle_instance_create)
    target.register(AgentActionName.INSTANCE_UPDATE, handle_instance_update)
    target.register(AgentActionName.INSTANCE_START, handle_instance_start)
    target.register(AgentActionName.INSTANCE_STOP, handle_instance_stop)
    target.register(AgentActionName.INSTANCE_STATUS, handle_instance_status)
    target.register(AgentActionName.INSTANCE_HEALTH, handle_instance_health)
    target.register(AgentActionName.INSTANCE_LOGS, handle_instance_logs)

    return target


def handle_instance_create(request: ActionRequest) -> ActionResult:
    """Create or render runtime files for a Konnaxion Instance."""

    params = dict(request.params)
    instance_id = _require_text(params, "instance_id")
    capsule_id = _resolve_capsule_id(
        params,
        instance_id=instance_id,
        require=True,
        purpose="instance_create",
    )
    capsule_version = _optional_text(params, "capsule_version")
    network_profile = _optional_text(params, "network_profile") or "local_only"
    exposure_mode = _optional_text(params, "exposure_mode") or "private"
    host = _optional_text(params, "domain", "public_host", "host") or "127.0.0.1"
    generate_secrets = _bool_param(params.get("generate_secrets"), default=True)

    from kx_agent.runtime.compose import (
        ComposeRenderOptions,
        security_context_inputs,
        write_runtime_compose,
    )

    compose_result = write_runtime_compose(
        ComposeRenderOptions(
            instance_id=instance_id,
            host=host,
            capsule_id=capsule_id,
            network_profile=network_profile,
            exposure_mode=exposure_mode,
            public_mode_enabled=network_profile in {"public_temporary", "public_vps"}
            or exposure_mode in {"temporary_tunnel", "public"},
            public_mode_expires_at=_optional_text(params, "public_mode_expires_at"),
            ensure_env_files=generate_secrets,
            overwrite_env_files=_bool_param(params.get("overwrite_env_files"), default=False),
        )
    )

    context_inputs = security_context_inputs(
        instance_id=instance_id,
        compose_file=compose_result.compose_file,
        capsule_id=capsule_id,
    )
    env_validation = dict(context_inputs.get("env_validation") or {})

    data = _object_to_mapping(compose_result)
    data.update(
        {
            "instance_id": instance_id,
            "capsule_id": capsule_id,
            "capsule_version": capsule_version,
            "network_profile": network_profile,
            "exposure_mode": exposure_mode,
            "host": host,
            "generate_secrets": generate_secrets,
            "env_validation": env_validation,
            "manifest_loaded": bool(context_inputs.get("manifest")),
            "manifest_fields": sorted(str(key) for key in dict(context_inputs.get("manifest") or {})),
            "env_keys": sorted(str(key) for key in dict(context_inputs.get("env") or {})),
            "state": "ready",
        }
    )

    return ActionResult.succeeded(
        request,
        message="Instance created.",
        data=data,
    )


def handle_instance_update(request: ActionRequest) -> ActionResult:
    """Update an instance by re-rendering runtime files."""

    delegated = ActionRequest(
        action=AgentActionName.INSTANCE_CREATE.value,
        params=dict(request.params),
        request_id=request.request_id,
        actor=request.actor,
        dry_run=request.dry_run,
        require_security_gate=request.require_security_gate,
    )

    result = handle_instance_create(delegated)
    return ActionResult.succeeded(
        request,
        message="Instance updated.",
        data=dict(result.data),
    )


def _load_capsule_image_archives_for_instance(
    runtime: Any,
    *,
    instance_id: str,
    params: Mapping[str, Any],
) -> dict[str, Any]:
    """Load imported capsule image archives before Compose starts containers.

    A capsule import extracts runtime image archives under the canonical
    capsule extraction directory. Docker tags such as ``konnaxion/frontend-next:v14``
    are mutable, so start must not assume an existing tag is current. Always
    load the archives listed by ``images.yaml`` when they are available, then
    the caller can recreate containers so Compose uses the freshly loaded image
    IDs.
    """

    from kx_agent.runtime.compose import read_compose_file
    from kx_shared.paths import capsule_extract_dir

    compose: Mapping[str, Any] = {}
    compose_file = Path(runtime.config.compose_file)

    if compose_file.exists():
        try:
            compose = read_compose_file(compose_file)
        except Exception as exc:  # pragma: no cover - defensive observability
            compose = {}
            compose_error = str(exc)
        else:
            compose_error = None
    else:
        compose_error = "compose_file_missing"

    capsule_id = _resolve_capsule_id(
        params,
        instance_id=instance_id,
        compose=compose,
        require=False,
        purpose="image_load",
    )

    result: dict[str, Any] = {
        "ok": True,
        "instance_id": instance_id,
        "capsule_id": capsule_id,
        "compose_file": str(compose_file),
        "compose_error": compose_error,
        "loaded_count": 0,
        "archives": [],
        "skipped": False,
    }

    if not capsule_id:
        result.update(
            {
                "skipped": True,
                "reason": "capsule_id_unavailable",
                "message": "Capsule image loading skipped because capsule_id is unavailable.",
            }
        )
        return result

    capsule_dir = capsule_extract_dir(capsule_id)
    images_yaml = capsule_dir / "images.yaml"

    result.update(
        {
            "capsule_dir": str(capsule_dir),
            "images_yaml": str(images_yaml),
        }
    )

    if not images_yaml.exists():
        result.update(
            {
                "skipped": True,
                "reason": "images_yaml_missing",
                "message": "Capsule image loading skipped because images.yaml is missing.",
            }
        )
        return result

    try:
        entries = _read_capsule_image_entries(images_yaml)
    except Exception as exc:
        result.update(
            {
                "ok": False,
                "error": {
                    "message": f"Could not read capsule images.yaml: {exc}",
                    "images_yaml": str(images_yaml),
                },
            }
        )
        return result

    loaded: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []

    for entry in entries:
        if not isinstance(entry, Mapping):
            failures.append(
                {
                    "ok": False,
                    "error": "invalid_image_entry",
                    "entry": _json_safe(entry),
                }
            )
            continue

        service = str(entry.get("service") or "").strip()
        image = str(entry.get("image") or "").strip()
        archive_value = str(
            entry.get("archive_path")
            or (f"images/{entry.get('archive')}" if entry.get("archive") else "")
        ).strip()

        archive_result: dict[str, Any] = {
            "service": service or None,
            "image": image or None,
            "archive_path": archive_value or None,
        }

        if not archive_value:
            archive_result.update(
                {
                    "ok": False,
                    "error": "archive_path_missing",
                }
            )
            failures.append(archive_result)
            continue

        try:
            archive_path = _resolve_capsule_relative_path(capsule_dir, archive_value)
        except ValueError as exc:
            archive_result.update(
                {
                    "ok": False,
                    "error": str(exc),
                }
            )
            failures.append(archive_result)
            continue

        archive_result["archive_file"] = str(archive_path)

        try:
            command = runtime.load_image_archive(archive_path)
        except Exception as exc:
            archive_result.update(
                {
                    "ok": False,
                    "error": str(exc),
                }
            )
            failures.append(archive_result)
            continue

        command_data = _object_to_mapping(command)
        archive_result.update(
            {
                "ok": bool(command_data.get("ok", True)),
                "command": command_data,
            }
        )

        if archive_result["ok"]:
            loaded.append(archive_result)
        else:
            failures.append(archive_result)

    result["archives"] = loaded + failures
    result["loaded_count"] = len(loaded)
    result["failed_count"] = len(failures)

    if failures:
        result.update(
            {
                "ok": False,
                "message": "One or more capsule image archives failed to load.",
            }
        )
    else:
        result["message"] = f"Loaded {len(loaded)} capsule image archive(s)."

    return result


def _read_capsule_image_entries(images_yaml: Path) -> list[Any]:
    """Read capsule images.yaml as a list of image archive entries."""

    try:
        import yaml
    except Exception as exc:  # pragma: no cover - dependency should exist in Agent env
        raise RuntimeError("PyYAML is required to read capsule images.yaml") from exc

    data = yaml.safe_load(images_yaml.read_text(encoding="utf-8"))

    if data is None:
        return []

    if isinstance(data, list):
        return data

    if isinstance(data, Mapping):
        images = data.get("images")
        if isinstance(images, list):
            return images

    raise ValueError("images.yaml must be a list or a mapping with an images list")


def _resolve_capsule_relative_path(capsule_dir: Path, archive_path: str) -> Path:
    """Resolve an images.yaml archive path without allowing traversal."""

    rel = PurePosixPath(archive_path)

    if rel.is_absolute() or ".." in rel.parts:
        raise ValueError(f"unsafe capsule archive path: {archive_path}")

    resolved = (capsule_dir / Path(*rel.parts)).resolve()
    capsule_root = capsule_dir.resolve()

    try:
        resolved.relative_to(capsule_root)
    except ValueError as exc:
        raise ValueError(f"capsule archive path escapes capsule dir: {archive_path}") from exc

    if not resolved.exists() or not resolved.is_file():
        raise ValueError(f"capsule image archive does not exist: {resolved}")

    return resolved


def handle_instance_start(request: ActionRequest) -> ActionResult:
    """Start an instance with the controlled Docker runtime wrapper."""

    params = dict(request.params)
    instance_id = _require_text(params, "instance_id")
    run_security_gate = _bool_param(params.get("run_security_gate"), default=True)

    if run_security_gate:
        security_request = ActionRequest(
            action=AgentActionName.SECURITY_CHECK.value,
            params={
                "instance_id": instance_id,
                "capsule_id": _optional_text(params, "capsule_id"),
                "blocking": True,
            },
            request_id=request.request_id,
            actor=request.actor,
            dry_run=request.dry_run,
            require_security_gate=request.require_security_gate,
        )
        security_result = handle_security_check(security_request)
        if security_result.status not in {ActionStatus.SUCCEEDED, ActionStatus.ACCEPTED}:
            raise SecurityGateBlockingError(
                "Security Gate blocked instance start.",
                {"instance_id": instance_id, "security": dict(security_result.data)},
            )

    from kx_agent.runtime.docker import runtime_for_instance

    runtime = runtime_for_instance(instance_id)
    runtime.validate_available()

    image_load = _load_capsule_image_archives_for_instance(
        runtime,
        instance_id=instance_id,
        params=params,
    )

    if not bool(image_load.get("ok", True)):
        return ActionResult(
            action=request.normalized_action(),
            status=ActionStatus.FAILED,
            request_id=request.request_id,
            message="Instance start failed while loading capsule images.",
            data={
                "instance_id": instance_id,
                "state": "failed",
                "image_load": image_load,
            },
            error={
                "message": "Instance start failed while loading capsule images.",
                "image_load": image_load,
            },
        )

    recreate_after_image_load = _bool_param(
        params.get("force_recreate_after_image_load"),
        default=bool(image_load.get("loaded_count", 0)),
    )

    down_data: Mapping[str, Any] | None = None
    if recreate_after_image_load:
        down_command = runtime.down(remove_orphans=True, volumes=False)
        down_data = _object_to_mapping(down_command)
        if not bool(down_data.get("ok", True)):
            return ActionResult(
                action=request.normalized_action(),
                status=ActionStatus.FAILED,
                request_id=request.request_id,
                message="Instance start failed while recreating containers.",
                data={
                    "instance_id": instance_id,
                    "state": "failed",
                    "image_load": image_load,
                    "down": down_data,
                },
                error={
                    "message": "Instance start failed while recreating containers.",
                    "result": down_data,
                },
            )

    command = runtime.up(detach=True)

    data = _object_to_mapping(command)
    ok = bool(data.get("ok", True))
    data.update(
        {
            "instance_id": instance_id,
            "state": "running" if ok else "failed",
            "image_load": image_load,
            "recreated_containers": recreate_after_image_load,
        }
    )

    if down_data is not None:
        data["down"] = down_data

    return ActionResult(
        action=request.normalized_action(),
        status=ActionStatus.SUCCEEDED if ok else ActionStatus.FAILED,
        request_id=request.request_id,
        message="Instance started." if ok else "Instance start failed.",
        data=data,
        error=None if ok else {"message": "Instance start failed.", "result": data},
    )


def handle_instance_stop(request: ActionRequest) -> ActionResult:
    """Stop an instance with the controlled Docker runtime wrapper."""

    params = dict(request.params)
    instance_id = _require_text(params, "instance_id")

    from kx_agent.runtime.docker import runtime_for_instance

    runtime = runtime_for_instance(instance_id)
    command = runtime.down(remove_orphans=True)

    data = _object_to_mapping(command)
    ok = bool(data.get("ok", True))
    data.update(
        {
            "instance_id": instance_id,
            "state": "stopped" if ok else "failed",
        }
    )

    return ActionResult(
        action=request.normalized_action(),
        status=ActionStatus.SUCCEEDED if ok else ActionStatus.FAILED,
        request_id=request.request_id,
        message="Instance stopped." if ok else "Instance stop failed.",
        data=data,
        error=None if ok else {"message": "Instance stop failed.", "result": data},
    )


def handle_instance_status(request: ActionRequest) -> ActionResult:
    """Return Docker Compose status for an instance."""

    params = dict(request.params)
    instance_id = _require_text(params, "instance_id")

    from kx_agent.runtime.docker import runtime_for_instance

    runtime = runtime_for_instance(instance_id)
    services = tuple(_object_to_mapping(item) for item in runtime.ps())

    running = any(
        str(service.get("state") or service.get("status") or "").lower() in {"running", "healthy"}
        for service in services
    )

    return ActionResult.succeeded(
        request,
        message="Instance status loaded.",
        data={
            "instance_id": instance_id,
            "state": "running" if running else "stopped",
            "services": services,
        },
    )


def handle_instance_health(request: ActionRequest) -> ActionResult:
    """Return Docker health data for an instance."""

    params = dict(request.params)
    instance_id = _require_text(params, "instance_id")

    from kx_agent.runtime.docker import runtime_for_instance

    runtime = runtime_for_instance(instance_id)
    health = runtime.health()

    return ActionResult.succeeded(
        request,
        message="Instance health loaded.",
        data={
            "instance_id": instance_id,
            "state": "running",
            "health": _json_safe(health),
        },
    )


def handle_instance_logs(request: ActionRequest) -> ActionResult:
    """Return runtime logs for an instance."""

    params = dict(request.params)
    instance_id = _require_text(params, "instance_id")
    service = _optional_text(params, "service")
    tail = _int_param(params.get("tail"), default=200, minimum=1, maximum=5000)

    from kx_agent.runtime.docker import runtime_for_instance

    runtime = runtime_for_instance(instance_id)
    command = runtime.logs(service=service, tail=tail)

    data = _object_to_mapping(command)
    data.update(
        {
            "instance_id": instance_id,
            "service": service,
            "tail": tail,
        }
    )

    return ActionResult.succeeded(
        request,
        message="Instance logs loaded.",
        data=data,
    )


# ---------------------------------------------------------------------
# Security action handlers
# ---------------------------------------------------------------------


def register_security_action_handlers(
    registry: AgentActionRegistry | None = None,
) -> AgentActionRegistry:
    target = registry or default_registry

    target.register(AgentActionName.SECURITY_CHECK, handle_security_check)

    return target


def handle_security_check(request: ActionRequest) -> ActionResult:
    """Run Security Gate for an instance using rendered runtime context."""

    params = dict(request.params)
    instance_id = _require_text(params, "instance_id")
    blocking = _bool_param(params.get("blocking"), default=True)
    explicit_capsule_id = _optional_text(params, "capsule_id")

    from kx_agent.runtime.compose import read_compose_file, security_context_inputs
    from kx_agent.runtime.docker import runtime_for_instance
    from kx_agent.security.gate import (
        context_from_compose,
        is_security_gate_passing,
        run_security_gate,
    )
    from kx_agent.security.evidence import (
        collect_runtime_security_evidence,
        write_security_gate_evidence,
    )

    runtime = runtime_for_instance(instance_id)
    compose_file = runtime.config.compose_file

    if not Path(compose_file).exists():
        return ActionResult.succeeded(
            request,
            message="Security Gate skipped because runtime Compose file does not exist yet.",
            data={
                "instance_id": instance_id,
                "security_status": "SKIPPED",
                "status": "SKIPPED",
                "compose_file": str(compose_file),
                "checks": [],
                "blocking_failures": [],
            },
        )

    compose = read_compose_file(compose_file)
    capsule_id = _resolve_capsule_id(
        {**params, "capsule_id": explicit_capsule_id},
        instance_id=instance_id,
        compose=compose,
        require=False,
        purpose="security_check",
    )

    inputs = security_context_inputs(
        instance_id=instance_id,
        compose=compose,
        compose_file=compose_file,
        capsule_id=capsule_id,
    )

    loaded_compose = dict(inputs.get("compose") or compose)
    manifest = dict(inputs.get("manifest") or {})
    env = dict(inputs.get("env") or {})
    env_validation = dict(inputs.get("env_validation") or {})

    runtime_evidence = collect_runtime_security_evidence(
        instance_id=instance_id,
        capsule_id=capsule_id,
        compose=loaded_compose,
        manifest=manifest,
        env=env,
    )

    context = context_from_compose(
        instance_id=instance_id,
        compose=loaded_compose,
        manifest=manifest,
        env=env,
        capsule_signature_verified=runtime_evidence.capsule_signature_verified,
        image_checksums_verified=runtime_evidence.image_checksums_verified,
        firewall_enabled=runtime_evidence.firewall_enabled,
        backup_configured=runtime_evidence.backup_configured,
        admin_surface_private=runtime_evidence.admin_surface_private,
        postgres_public=runtime_evidence.postgres_public,
        redis_public=runtime_evidence.redis_public,
        allowed_images=runtime_evidence.allowed_images,
    )

    report = run_security_gate(context)
    data = _object_to_mapping(report)

    ok = is_security_gate_passing(report)
    status_value = str(
        data.get("status")
        or data.get("security_status")
        or ("PASS" if ok else "FAIL_BLOCKING")
    )

    result_data = {
        "instance_id": instance_id,
        "capsule_id": capsule_id,
        "security_status": status_value,
        "status": status_value,
        "compose_file": str(compose_file),
        "manifest_loaded": bool(manifest),
        "manifest_fields": sorted(str(key) for key in manifest),
        "env_loaded": bool(env),
        "env_keys": sorted(str(key) for key in env),
        "env_validation": env_validation,
        "runtime_evidence": dict(runtime_evidence.details),
        "report": data,
    }

    try:
        evidence_file = write_security_gate_evidence(
            instance_id,
            {
                "instance_id": instance_id,
                "capsule_id": capsule_id,
                "security_status": status_value,
                "status": status_value,
                "compose_file": str(compose_file),
                "results": [
                    {
                        "check": item.get("check"),
                        "status": item.get("status"),
                        "message": item.get("message", ""),
                        "blocking": bool(item.get("blocking", False)),
                    }
                    for item in (data.get("results") or data.get("checks") or [])
                    if isinstance(item, Mapping)
                ],
                "blocking_failures": [
                    str(item.get("check") or item) if isinstance(item, Mapping) else str(item)
                    for item in (data.get("blocking_failures") or [])
                ],
                "warnings": [
                    str(item.get("check") or item) if isinstance(item, Mapping) else str(item)
                    for item in (data.get("warnings") or [])
                ],
                "runtime_evidence": dict(runtime_evidence.details),
            },
        )
        result_data["security_evidence_file"] = str(evidence_file)
    except Exception as exc:  # noqa: BLE001
        result_data["security_evidence_error"] = f"{type(exc).__name__}: {exc}"
        if blocking:
            return ActionResult(
                action=request.normalized_action(),
                status=ActionStatus.BLOCKED,
                request_id=request.request_id,
                message="Security Gate evidence could not be persisted.",
                data=result_data,
                error={"message": "Security Gate evidence could not be persisted."},
            )

    if blocking and not ok:
        return ActionResult(
            action=request.normalized_action(),
            status=ActionStatus.BLOCKED,
            request_id=request.request_id,
            message="Security Gate blocked the operation.",
            data=result_data,
            error={
                "message": "Security Gate blocked the operation.",
                "report": data,
            },
        )

    return ActionResult.succeeded(
        request,
        message="Security Gate check completed.",
        data=result_data,
    )


# ---------------------------------------------------------------------
# Backup action handlers
# ---------------------------------------------------------------------


def register_backup_action_handlers(
    registry: AgentActionRegistry | None = None,
) -> AgentActionRegistry:
    target = registry or default_registry

    target.register(AgentActionName.BACKUP_LIST, handle_backup_list)
    target.register(AgentActionName.BACKUP_VERIFY, handle_backup_verify)
    target.register(AgentActionName.BACKUP_TEST_RESTORE, handle_backup_test_restore)

    return target


def handle_backup_list(request: ActionRequest) -> ActionResult:
    """List backup records from the canonical backup root."""

    params = dict(request.params)
    instance_id = _optional_text(params, "instance_id")
    backup_class = _optional_text(params, "backup_class")
    status_filter = _optional_text(params, "status")

    limit = _int_param(params.get("limit"), default=50, minimum=1, maximum=500)

    backups: list[dict[str, Any]] = []
    for backup_dir in _iter_backup_dirs(instance_id=instance_id, backup_class=backup_class):
        item = _backup_summary_from_dir(backup_dir)

        if status_filter and str(item.get("status") or "") != status_filter:
            continue

        backups.append(item)

    backups.sort(key=lambda item: str(item.get("created_at") or item.get("backup_id") or ""), reverse=True)
    backups = backups[:limit]

    return ActionResult.succeeded(
        request,
        message="Backups listed.",
        data={
            "backups": backups,
            "items": backups,
            "count": len(backups),
            "instance_id": instance_id,
            "backup_class": backup_class,
            "status": status_filter,
        },
    )


def handle_backup_verify(request: ActionRequest) -> ActionResult:
    """Verify one backup through the backup verifier module."""

    params = dict(request.params)
    backup_id = _require_text(params, "backup_id", "source_backup_id")
    instance_id = _optional_text(params, "instance_id")
    backup_class = _optional_text(params, "backup_class")

    located = _locate_backup(
        backup_id=backup_id,
        instance_id=instance_id,
        backup_class=backup_class,
    )
    if located is None:
        raise FileNotFoundError(f"backup not found: {backup_id}")

    located_instance_id, located_backup_class, _backup_dir = located

    from kx_agent.backups.verify import verify_backup

    report = verify_backup(
        located_instance_id,
        located_backup_class,
        backup_id,
    )

    data = _object_to_mapping(report)
    accepted = bool(data.get("accepted", data.get("ok", False)))
    status = str(data.get("backup_status") or data.get("status") or ("verified" if accepted else "failed"))

    return ActionResult(
        action=request.normalized_action(),
        status=ActionStatus.SUCCEEDED if accepted else ActionStatus.FAILED,
        request_id=request.request_id,
        message="Backup verification completed." if accepted else "Backup verification failed.",
        data={
            "backup_id": backup_id,
            "instance_id": located_instance_id,
            "backup_class": located_backup_class,
            "status": status,
            "verified": accepted,
            "report": data,
        },
        error=None if accepted else {"message": "Backup verification failed.", "report": data},
    )


def handle_backup_test_restore(request: ActionRequest) -> ActionResult:
    """Run a constrained backup test-restore handler when implemented."""

    params = dict(request.params)
    backup_id = _require_text(params, "backup_id", "source_backup_id")
    instance_id = _require_text(params, "instance_id")
    target_instance_id = _require_text(
        params,
        "target_instance_id",
        "new_instance_id",
        "test_instance_id",
    )

    try:
        from kx_agent.backups import restore as restore_module
    except Exception as exc:
        raise AgentError(
            "Backup restore module is not available.",
            {"module": "kx_agent.backups.restore"},
        ) from exc

    function = _first_callable(
        restore_module,
        (
            "test_restore_backup",
            "test_restore",
            "run_test_restore",
            "restore_backup_test",
        ),
    )
    if function is None:
        raise AgentError(
            "Backup test-restore handler is not implemented.",
            {
                "module": "kx_agent.backups.restore",
                "expected_functions": [
                    "test_restore_backup",
                    "test_restore",
                    "run_test_restore",
                    "restore_backup_test",
                ],
            },
        )

    result = function(
        instance_id=instance_id,
        backup_id=backup_id,
        target_instance_id=target_instance_id,
        new_instance_id=target_instance_id,
        dry_run=bool(request.dry_run),
    )

    data = _object_to_mapping(result)
    ok = bool(data.get("ok", data.get("accepted", data.get("success", True))))

    return ActionResult(
        action=request.normalized_action(),
        status=ActionStatus.SUCCEEDED if ok else ActionStatus.FAILED,
        request_id=request.request_id,
        message=(
            str(data.get("message"))
            if data.get("message")
            else ("Backup test restore completed." if ok else "Backup test restore failed.")
        ),
        data={
            "backup_id": backup_id,
            "instance_id": instance_id,
            "target_instance_id": target_instance_id,
            "result": data,
        },
        error=None if ok else {"message": "Backup test restore failed.", "result": data},
    )


# ---------------------------------------------------------------------
# Network action handlers
# ---------------------------------------------------------------------


def register_network_action_handlers(
    registry: AgentActionRegistry | None = None,
) -> AgentActionRegistry:
    target = registry or default_registry

    target.register(AgentActionName.NETWORK_SET_PROFILE, handle_network_set_profile)
    target.register(AgentActionName.NETWORK_DISABLE_PUBLIC, handle_network_disable_public)
    target.register(
        AgentActionName.NETWORK_EXPIRE_TEMPORARY_PUBLIC,
        handle_network_expire_temporary_public,
    )

    return target


def handle_network_set_profile(request: ActionRequest) -> ActionResult:
    """Validate and durably apply a network profile to an existing instance.

    This handler must not merely return the computed KX env deltas. Public VPS
    domain changes affect runtime files that Traefik and the application read
    from disk, so the Agent must rewrite the instance env files, runtime Compose
    file, and Traefik dynamic file.
    """

    from kx_agent.network.exposure import (
        ExposureRequest,
        build_exposure_plan,
        parse_datetime_utc,
        parse_exposure_mode,
        parse_network_profile,
        serialize_env_updates,
    )
    from kx_agent.runtime.compose import (
        ComposeRenderOptions,
        security_context_inputs,
        write_runtime_compose,
    )

    params = dict(request.params)

    instance_id = _require_text(params, "instance_id")

    network_profile = parse_network_profile(
        params.get("network_profile")
        or params.get("profile")
        or "intranet_private"
    )
    exposure_mode = parse_exposure_mode(params.get("exposure_mode", "private"))

    expires_raw = params.get("public_mode_expires_at")
    public_mode_expires_at = (
        parse_datetime_utc(str(expires_raw))
        if expires_raw not in (None, "")
        else None
    )

    requested_public_enabled = params.get("public_mode_enabled")
    public_mode_enabled = _bool_param(
        requested_public_enabled,
        default=network_profile.value in {"public_temporary", "public_vps"}
        or exposure_mode.value in {"temporary_tunnel", "public"},
    )

    host = _optional_text(params, "domain", "public_host", "host") or "127.0.0.1"

    plan = build_exposure_plan(
        ExposureRequest(
            network_profile=network_profile,
            exposure_mode=exposure_mode,
            public_mode_enabled=public_mode_enabled,
            public_mode_expires_at=public_mode_expires_at,
            host=host,
            requested_by=str(params.get("requested_by") or "manager"),
            reason=str(params.get("reason") or "set network profile"),
        )
    )

    capsule_id = _resolve_capsule_id(
        params,
        instance_id=instance_id,
        require=True,
        purpose="network_set_profile",
    )

    if request.dry_run:
        return ActionResult.succeeded(
            request,
            message="Network profile validated.",
            data={
                "instance_id": instance_id,
                "capsule_id": capsule_id,
                "network_profile": plan.network_profile.value,
                "exposure_mode": plan.exposure_mode.value,
                "public_mode_enabled": plan.public_mode_enabled,
                "public_mode_expires_at": (
                    plan.public_mode_expires_at.isoformat()
                    if plan.public_mode_expires_at
                    else None
                ),
                "host": plan.host,
                "risk": plan.risk.value,
                "warnings": list(plan.warnings),
                "kx_env": serialize_env_updates(plan),
                "dry_run": True,
            },
        )

    compose_result = write_runtime_compose(
        ComposeRenderOptions(
            instance_id=instance_id,
            host=plan.host,
            capsule_id=capsule_id,
            network_profile=plan.network_profile.value,
            exposure_mode=plan.exposure_mode.value,
            public_mode_enabled=plan.public_mode_enabled,
            public_mode_expires_at=(
                plan.public_mode_expires_at.isoformat()
                if plan.public_mode_expires_at
                else None
            ),
            ensure_env_files=True,
            overwrite_env_files=_bool_param(
                params.get("overwrite_env_files"),
                default=True,
            ),
        )
    )

    context_inputs = security_context_inputs(
        instance_id=instance_id,
        compose_file=compose_result.compose_file,
        capsule_id=capsule_id,
    )
    env_validation = dict(context_inputs.get("env_validation") or {})

    data = _object_to_mapping(compose_result)
    data.update(
        {
            "instance_id": instance_id,
            "capsule_id": capsule_id,
            "network_profile": plan.network_profile.value,
            "exposure_mode": plan.exposure_mode.value,
            "public_mode_enabled": plan.public_mode_enabled,
            "public_mode_expires_at": (
                plan.public_mode_expires_at.isoformat()
                if plan.public_mode_expires_at
                else None
            ),
            "host": plan.host,
            "risk": plan.risk.value,
            "warnings": list(plan.warnings),
            "kx_env": serialize_env_updates(plan),
            "env_validation": env_validation,
            "manifest_loaded": bool(context_inputs.get("manifest")),
            "manifest_fields": sorted(str(key) for key in dict(context_inputs.get("manifest") or {})),
            "env_keys": sorted(str(key) for key in dict(context_inputs.get("env") or {})),
            "state": "ready",
        }
    )

    return ActionResult.succeeded(
        request,
        message="Network profile applied.",
        data=data,
    )


def handle_network_disable_public(request: ActionRequest) -> ActionResult:
    params = {
        **dict(request.params),
        "network_profile": "intranet_private",
        "exposure_mode": "private",
        "public_mode_enabled": False,
        "public_mode_expires_at": None,
        "reason": "disable public mode",
    }

    delegated_request = ActionRequest(
        action=AgentActionName.NETWORK_SET_PROFILE.value,
        params=params,
        request_id=request.request_id,
        actor=request.actor,
        dry_run=request.dry_run,
        require_security_gate=request.require_security_gate,
    )

    result = handle_network_set_profile(delegated_request)

    return ActionResult.succeeded(
        request,
        message="Public mode disabled.",
        data=dict(result.data),
    )


def handle_network_expire_temporary_public(request: ActionRequest) -> ActionResult:
    params = {
        **dict(request.params),
        "network_profile": "intranet_private",
        "exposure_mode": "private",
        "public_mode_enabled": False,
        "public_mode_expires_at": None,
        "reason": "temporary public exposure expired",
    }

    delegated_request = ActionRequest(
        action=AgentActionName.NETWORK_SET_PROFILE.value,
        params=params,
        request_id=request.request_id,
        actor=request.actor,
        dry_run=request.dry_run,
        require_security_gate=request.require_security_gate,
    )

    result = handle_network_set_profile(delegated_request)

    return ActionResult.succeeded(
        request,
        message="Temporary public mode expired and private profile restored.",
        data=dict(result.data),
    )


# ---------------------------------------------------------------------
# Backup filesystem helpers
# ---------------------------------------------------------------------


def _backup_root() -> Path:
    from kx_shared.konnaxion_constants import KX_BACKUPS_ROOT

    return Path(KX_BACKUPS_ROOT)


def _iter_backup_dirs(
    *,
    instance_id: str | None = None,
    backup_class: str | None = None,
) -> tuple[Path, ...]:
    root = _backup_root()
    if not root.exists():
        return ()

    instance_dirs = [root / instance_id] if instance_id else sorted(root.iterdir())
    result: list[Path] = []

    for instance_dir in instance_dirs:
        if not instance_dir.is_dir():
            continue

        class_dirs = [instance_dir / backup_class] if backup_class else sorted(instance_dir.iterdir())
        for class_dir in class_dirs:
            if not class_dir.is_dir():
                continue

            for backup_dir in sorted(class_dir.iterdir()):
                if backup_dir.is_dir():
                    result.append(backup_dir)

    return tuple(result)


def _locate_backup(
    *,
    backup_id: str,
    instance_id: str | None = None,
    backup_class: str | None = None,
) -> tuple[str, str, Path] | None:
    for backup_dir in _iter_backup_dirs(instance_id=instance_id, backup_class=backup_class):
        if backup_dir.name != backup_id:
            continue

        try:
            located_backup_class = backup_dir.parent.name
            located_instance_id = backup_dir.parent.parent.name
        except IndexError:
            continue

        return located_instance_id, located_backup_class, backup_dir

    return None


def _backup_summary_from_dir(backup_dir: Path) -> dict[str, Any]:
    manifest = _load_backup_manifest(backup_dir)

    backup_id = str(manifest.get("backup_id") or backup_dir.name)
    backup_class = str(
        manifest.get("backup_class")
        or manifest.get("class")
        or backup_dir.parent.name
    )
    instance_id = str(
        manifest.get("instance_id")
        or backup_dir.parent.parent.name
    )

    verified = bool(
        manifest.get("verified")
        or manifest.get("verification_status") == "verified"
        or manifest.get("status") == "verified"
    )

    return {
        "backup_id": backup_id,
        "instance_id": instance_id,
        "backup_class": backup_class,
        "status": str(manifest.get("status") or "created"),
        "created_at": str(manifest.get("created_at") or ""),
        "completed_at": str(manifest.get("completed_at") or ""),
        "size_bytes": _optional_int(manifest.get("size_bytes")) or _directory_size(backup_dir),
        "verified": verified,
        "label": str(manifest.get("label") or ""),
        "path": str(backup_dir),
    }


def _load_backup_manifest(backup_dir: Path) -> dict[str, Any]:
    candidates = (
        backup_dir / "manifest.json",
        backup_dir / "backup.json",
        backup_dir / "backup_manifest.json",
        backup_dir / "backup-manifest.json",
        backup_dir / f"{backup_dir.name}.json",
        backup_dir / f"{backup_dir.name}.manifest.json",
    )

    for path in candidates:
        if not path.exists() or not path.is_file():
            continue

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue

        if isinstance(data, Mapping):
            return dict(data)

    return {}


def _directory_size(path: Path) -> int:
    total = 0

    try:
        iterator = path.rglob("*")
    except OSError:
        return total

    for item in iterator:
        try:
            if item.is_file():
                total += item.stat().st_size
        except OSError:
            continue

    return total


# ---------------------------------------------------------------------
# Generic helper functions
# ---------------------------------------------------------------------


def _require_text(payload: Mapping[str, Any], *names: str) -> str:
    for name in names:
        value = payload.get(name)
        if value not in (None, ""):
            text = str(value).strip()
            if text:
                return text

    joined = ", ".join(names)
    raise ValueError(f"Missing required field: {joined}")


def _optional_text(payload: Mapping[str, Any], *names: str) -> str | None:
    for name in names:
        value = payload.get(name)
        if value not in (None, ""):
            text = str(value).strip()
            if text:
                return text
    return None


def _bool_param(value: Any, *, default: bool = False) -> bool:
    if value in (None, ""):
        return default

    if isinstance(value, bool):
        return value

    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "y", "on", "checked"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False

    return bool(normalized)


def _int_param(
    value: Any,
    *,
    default: int,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default

    if minimum is not None:
        parsed = max(minimum, parsed)
    if maximum is not None:
        parsed = min(maximum, parsed)

    return parsed


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None

    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _first_callable(module: Any, names: tuple[str, ...]) -> Callable[..., Any] | None:
    for name in names:
        candidate = getattr(module, name, None)
        if callable(candidate):
            return candidate
    return None



def _resolve_capsule_id(
    payload: Mapping[str, Any],
    *,
    instance_id: str | None = None,
    compose: Mapping[str, Any] | None = None,
    require: bool = False,
    purpose: str = "runtime",
) -> str | None:
    """Resolve the active capsule id without using a generic fallback.

    Capsule identity is part of the runtime contract. Mutating handlers such as
    network_set_profile and instance_start must not silently fall back to a
    generic capsule id because that can make the Agent rewrite Compose files or
    load images from the wrong extracted capsule directory.

    Resolution order:
    1. explicit request fields, including capsule_path-derived ids;
    2. the provided Compose mapping, when available;
    3. existing runtime state for the instance: Compose, state JSON, env files,
       and current-capsule pointer.
    """

    capsule_id = _optional_text(payload, "capsule_id") or _capsule_id_from_path(payload)
    if capsule_id:
        return capsule_id

    if compose:
        capsule_id = _capsule_id_from_compose(compose)
        if capsule_id:
            return capsule_id

    if instance_id:
        capsule_id = _capsule_id_from_instance_runtime(instance_id)
        if capsule_id:
            return capsule_id

    if require:
        raise ValueError(
            "capsule_id is required for "
            f"{purpose}; pass capsule_id from the Manager or ensure the "
            "instance runtime state already contains KX_CAPSULE_ID."
        )

    return None


def _capsule_id_from_instance_runtime(instance_id: str) -> str | None:
    """Best-effort capsule-id lookup from existing instance runtime files."""

    # 1. Current Compose metadata is the most authoritative runtime source.
    try:
        from kx_agent.runtime.compose import read_compose_file
        from kx_shared.konnaxion_constants import instance_compose_file

        compose_path = Path(instance_compose_file(instance_id))
        if compose_path.exists():
            capsule_id = _capsule_id_from_compose(read_compose_file(compose_path))
            if capsule_id:
                return capsule_id
    except Exception:
        pass

    # 2. Instance state JSON may be present on newer runtimes.
    try:
        from kx_shared.konnaxion_constants import instance_state_file

        state_path = Path(instance_state_file(instance_id))
        capsule_id = _capsule_id_from_json_file(state_path)
        if capsule_id:
            return capsule_id
    except Exception:
        pass

    # 3. Generated env files are also valid runtime sources.
    try:
        from kx_shared.konnaxion_constants import instance_env_file

        for filename in ("kx.env", "konnaxion.env", "django.env"):
            capsule_id = _capsule_id_from_env_file(Path(instance_env_file(instance_id, filename)))
            if capsule_id:
                return capsule_id
    except Exception:
        pass

    # 4. A current-capsule pointer/symlink is a final fallback.
    try:
        from kx_shared.konnaxion_constants import instance_current_capsule_link

        pointer = Path(instance_current_capsule_link(instance_id))
        capsule_id = _capsule_id_from_pointer_file(pointer)
        if capsule_id:
            return capsule_id
    except Exception:
        pass

    return None


def _capsule_id_from_json_file(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None

    if isinstance(data, Mapping):
        return _capsule_id_from_mapping(data)

    return None


def _capsule_id_from_mapping(value: Mapping[str, Any]) -> str | None:
    for key in (
        "capsule_id",
        "current_capsule_id",
        "active_capsule_id",
        "kx_capsule_id",
        "KX_CAPSULE_ID",
    ):
        item = value.get(key)
        if item not in (None, ""):
            text = str(item).strip()
            if text:
                return text

    for item in value.values():
        if isinstance(item, Mapping):
            capsule_id = _capsule_id_from_mapping(item)
            if capsule_id:
                return capsule_id

    return None


def _capsule_id_from_env_file(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None

    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        if key.strip() in {"KX_CAPSULE_ID", "CAPSULE_ID"}:
            cleaned = value.strip().strip('"').strip("'")
            if cleaned:
                return cleaned

    return None


def _capsule_id_from_pointer_file(path: Path) -> str | None:
    if not path.exists():
        return None

    candidates: list[str] = []

    try:
        if path.is_symlink():
            candidates.append(str(path.resolve()))
    except OSError:
        pass

    try:
        if path.is_file():
            content = path.read_text(encoding="utf-8").strip()
            if content:
                candidates.append(content)
    except OSError:
        pass

    candidates.append(str(path))

    for candidate in candidates:
        capsule_id = Path(candidate).name.removesuffix(".kxcap").strip()
        if capsule_id:
            return capsule_id

    return None


def _capsule_id_from_path(payload: Mapping[str, Any]) -> str | None:
    for key in ("capsule_path", "capsule_file", "remote_capsule_path"):
        value = payload.get(key)
        if value not in (None, ""):
            stem = Path(str(value)).name.removesuffix(".kxcap")
            if stem:
                return stem
    return None


def _capsule_id_from_compose(compose: Mapping[str, Any]) -> str | None:
    metadata = compose.get("x-konnaxion")
    if isinstance(metadata, Mapping):
        value = metadata.get("capsule_id")
        if value not in (None, ""):
            return str(value).strip()

    services = compose.get("services")
    if not isinstance(services, Mapping):
        return None

    for service in services.values():
        if not isinstance(service, Mapping):
            continue

        env_items = service.get("environment") or {}
        if isinstance(env_items, Mapping):
            value = env_items.get("KX_CAPSULE_ID")
            if value not in (None, ""):
                return str(value).strip()

        if isinstance(env_items, list):
            for item in env_items:
                key, _, value = str(item).partition("=")
                if key == "KX_CAPSULE_ID" and value:
                    return value.strip()

    return None


def _object_to_mapping(value: Any) -> dict[str, Any]:
    if value is None:
        return {}

    if isinstance(value, Mapping):
        return _json_safe(dict(value))

    if is_dataclass(value):
        return _json_safe(asdict(value))

    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        data = to_dict()
        if isinstance(data, Mapping):
            return _json_safe(dict(data))

    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        data = model_dump()
        if isinstance(data, Mapping):
            return _json_safe(dict(data))

    dict_method = getattr(value, "dict", None)
    if callable(dict_method):
        data = dict_method()
        if isinstance(data, Mapping):
            return _json_safe(dict(data))

    return {"result": _json_safe(value)}


def _json_safe(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)

    if isinstance(value, Enum):
        return value.value

    if is_dataclass(value):
        return _json_safe(asdict(value))

    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}

    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]

    if isinstance(value, list):
        return [_json_safe(item) for item in value]

    if isinstance(value, set):
        return sorted(_json_safe(item) for item in value)

    return value


# ---------------------------------------------------------------------
# Minimal placeholder handlers
# ---------------------------------------------------------------------


def not_implemented_handler(request: ActionRequest) -> ActionResult:
    """Placeholder used until concrete modules register real handlers."""

    raise AgentError(
        "Agent action handler is not implemented yet.",
        {"action": request.normalized_action(), "request_id": request.request_id},
    )


def register_placeholder_handlers(registry: AgentActionRegistry | None = None) -> AgentActionRegistry:
    """Register placeholder handlers for every allowlisted action."""

    target = registry or default_registry
    for action in AgentActionName:
        target.register(action, not_implemented_handler)
    return target


__all__ = [
    "register_artifact_action_handlers",
    "handle_artifact_remove",
    "ALLOWLISTED_ACTIONS",
    "API_ACTION_ALIASES",
    "MUTATING_ACTIONS",
    "SECURITY_GATED_ACTIONS",
    "ActionDispatcher",
    "ActionRequest",
    "ActionResult",
    "ActionStatus",
    "AgentAPIActionHandler",
    "AgentActionHandler",
    "AgentActionName",
    "AgentActionRegistry",
    "PostDispatchHook",
    "PreDispatchHook",
    "action_result_to_api_dict",
    "canonicalize_api_action",
    "default_registry",
    "handle_backup_list",
    "handle_backup_test_restore",
    "handle_backup_verify",
    "handle_capsule_import",
    "handle_capsule_verify",
    "handle_instance_create",
    "handle_instance_health",
    "handle_instance_logs",
    "handle_instance_start",
    "handle_instance_status",
    "handle_instance_stop",
    "handle_instance_update",
    "handle_network_disable_public",
    "handle_network_expire_temporary_public",
    "handle_network_set_profile",
    "handle_security_check",
    "make_api_action_handler",
    "make_default_dispatcher",
    "make_default_registry",
    "make_dispatcher",
    "normalize_action_name",
    "normalize_request",
    "not_implemented_handler",
    "register_action",
    "register_backup_action_handlers",
    "register_capsule_action_handlers",
    "register_instance_action_handlers",
    "register_network_action_handlers",
    "register_placeholder_handlers",
    "register_security_action_handlers",
    "validate_request",
]