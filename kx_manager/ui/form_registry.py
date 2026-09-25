# kx_manager/ui/form_registry.py

"""Action form registry for the Konnaxion Capsule Manager GUI.

This module maps canonical GUI action names to framework-neutral form models.

It validates and normalizes submitted GUI payloads only. It must not execute
actions, call Docker, run shell commands, mutate host state, or contact the
Agent. Action execution belongs to ``kx_manager.ui.actions`` and approved
Manager service wrappers.
"""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any, Mapping

from kx_manager.defaults import DEFAULT_DROPLET_NAME

from kx_manager.ui.form_backups import (
    BackupForm,
    BackupLookupForm,
    ListBackupsForm,
    RestoreForm,
)
from kx_manager.ui.form_capsules import (
    BuildCapsuleForm,
    CapsuleLookupForm,
    ImportCapsuleForm,
    VerifyCapsuleForm,
)
from kx_manager.ui.form_core import (
    CapsuleOutputFolderForm,
    EmptyForm,
    SourceFolderForm,
)
from kx_manager.ui.form_errors import FormValidationError
from kx_manager.ui.form_helpers import _payload, normalize_form_data
from kx_manager.ui.form_instances import (
    ConfirmedInstanceActionForm,
    CreateInstanceForm,
    InstanceActionForm,
    LogsForm,
    OpenInstanceForm,
    RollbackForm,
    UpdateInstanceForm,
)
from kx_manager.ui.form_network import (
    DisablePublicModeForm,
    NetworkProfileForm,
)
from kx_manager.ui.form_targets import (
    BootstrapDropletAgentForm,
    CheckDropletAgentForm,
    CopyCapsuleToDropletForm,
    DeployDropletForm,
    DeployIntranetForm,
    DeployLocalForm,
    DropletTargetForm,
    IntranetTargetForm,
    LocalTargetForm,
    StartDropletInstanceForm,
    TemporaryPublicTargetForm,
    parse_droplet_operation_form,
)


ACTION_ALIASES: dict[str, str] = {
    "open_runtime": "open_instance",
}


ACTION_FORM_MODELS: dict[str, type[Any]] = {
    "check_manager": EmptyForm,
    "check_agent": EmptyForm,
    "select_source_folder": SourceFolderForm,
    "select_capsule_output_folder": CapsuleOutputFolderForm,
    "build_capsule": BuildCapsuleForm,
    "rebuild_capsule": BuildCapsuleForm,
    "verify_capsule": VerifyCapsuleForm,
    "import_capsule": ImportCapsuleForm,
    "list_capsules": EmptyForm,
    "view_capsule": CapsuleLookupForm,
    "create_instance": CreateInstanceForm,
    "update_instance": UpdateInstanceForm,
    "start_instance": InstanceActionForm,
    "stop_instance": ConfirmedInstanceActionForm,
    "restart_instance": InstanceActionForm,
    "instance_status": InstanceActionForm,
    "view_logs": LogsForm,
    "view_health": InstanceActionForm,
    "open_instance": OpenInstanceForm,
    "rollback_instance": RollbackForm,
    "create_backup": BackupForm,
    "list_backups": ListBackupsForm,
    "verify_backup": BackupLookupForm,
    "restore_backup": RestoreForm,
    "restore_backup_new": RestoreForm,
    "test_restore_backup": RestoreForm,
    "run_security_check": InstanceActionForm,
    "set_network_profile": NetworkProfileForm,
    "disable_public_mode": DisablePublicModeForm,
    "set_target_local": LocalTargetForm,
    "set_target_intranet": IntranetTargetForm,
    "set_target_droplet": DropletTargetForm,
    "set_target_temporary_public": TemporaryPublicTargetForm,
    "deploy_local": DeployLocalForm,
    "deploy_intranet": DeployIntranetForm,
    "deploy_droplet": DeployDropletForm,
    "bootstrap_droplet_agent": BootstrapDropletAgentForm,
    "check_droplet_agent": CheckDropletAgentForm,
    "initialize_production_data": CheckDropletAgentForm,
    "copy_capsule_to_droplet": CopyCapsuleToDropletForm,
    "start_droplet_instance": StartDropletInstanceForm,
    "open_manager_docs": EmptyForm,
    "open_agent_docs": EmptyForm,
}


DROPLET_NON_CAPSULE_OPERATION_ACTIONS: frozenset[str] = frozenset(
    {
        "bootstrap_droplet_agent",
        "check_droplet_agent",
        "initialize_production_data",
    }
)


DROPLET_CAPSULE_OPERATION_ACTIONS: frozenset[str] = frozenset(
    {
        "deploy_droplet",
        "copy_capsule_to_droplet",
        "start_droplet_instance",
    }
)


DROPLET_OPERATION_ACTIONS: frozenset[str] = frozenset(
    {
        *DROPLET_NON_CAPSULE_OPERATION_ACTIONS,
        *DROPLET_CAPSULE_OPERATION_ACTIONS,
    }
)


TRUE_VALUES: frozenset[str] = frozenset(
    {
        "1",
        "true",
        "yes",
        "y",
        "on",
        "checked",
    }
)


FALSE_VALUES: frozenset[str] = frozenset(
    {
        "0",
        "false",
        "no",
        "n",
        "off",
        "",
    }
)


def canonical_action_value(action: Any) -> str:
    """Return the canonical string value for a GUI action."""

    raw_value = str(getattr(action, "value", action)).strip()

    if not raw_value:
        raise FormValidationError("action is required.", field="action")

    return ACTION_ALIASES.get(raw_value, raw_value)


def is_known_action(action: Any) -> bool:
    """Return whether an action has a registered form model."""

    try:
        action_value = canonical_action_value(action)
    except FormValidationError:
        return False

    return action_value in ACTION_FORM_MODELS


def parse_action_form(action: Any, data: Mapping[str, Any] | None = None) -> Any:
    """Parse and validate a GUI action payload into the matching form model."""

    action_value = canonical_action_value(action)
    form_model = ACTION_FORM_MODELS.get(action_value)

    if form_model is None:
        raise FormValidationError(
            f"Unknown or unsupported GUI action: {action_value}",
            field="action",
        )

    normalized = normalize_form_data(data or {})
    normalized["action"] = action_value

    if action_value in DROPLET_OPERATION_ACTIONS:
        return parse_droplet_operation_form(normalized)

    return form_model.from_mapping(normalized)


def form_to_payload(form: Any) -> dict[str, Any]:
    """Convert a parsed form model to a JSON-safe action payload."""

    if hasattr(form, "to_payload"):
        payload = form.to_payload()
        if isinstance(payload, Mapping):
            return _clean_payload(dict(payload))

    return _clean_payload(_payload(form))


def validate_action_payload(
    action_or_data: Any,
    data: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate a GUI action payload.

    Supported call styles:

    ``validate_action_payload("set_network_profile", payload)``

    ``validate_action_payload({"action": "set_network_profile", ...})``
    """

    if data is None:
        if not isinstance(action_or_data, Mapping):
            raise FormValidationError(
                "action payload mapping is required.",
                field="action",
            )

        normalized = normalize_form_data(action_or_data)
        raw_action = normalized.get("action")

        if raw_action in {None, ""}:
            raise FormValidationError("action is required.", field="action")

        action_value = canonical_action_value(raw_action)
        payload_data = normalized
    else:
        action_value = canonical_action_value(action_or_data)
        payload_data = normalize_form_data(data)

    payload_data["action"] = action_value

    if action_value in DROPLET_OPERATION_ACTIONS:
        return _validate_droplet_operation_action(action_value, payload_data)

    form = parse_action_form(action_value, payload_data)

    return _clean_payload(
        {
            "action": action_value,
            **form_to_payload(form),
        }
    )


def _validate_droplet_operation_action(
    action: str,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate and normalize Droplet operation payloads.

    Droplet operation actions are explicit operator actions. Their submitted
    payload may contain stale target/profile/exposure/confirmation values from
    another UI page, so this path first validates raw required connection
    fields, then delegates to ``form_targets`` for canonical Droplet forcing.

    The pre-parser required-field check is intentionally strict because
    ``parse_droplet_operation_form`` may synthesize defaults for rendering
    convenience. Action payload validation must reject missing submitted
    Droplet connection fields before those defaults can mask omissions.
    """

    data = normalize_form_data(payload)
    data["action"] = action

    _validate_droplet_operation_required_fields(action, data)

    form = parse_droplet_operation_form(data)

    result = _clean_payload(
        {
            "action": action,
            **form_to_payload(form),
        }
    )

    if action in DROPLET_NON_CAPSULE_OPERATION_ACTIONS:
        result.pop("capsule_file", None)
        result.pop("capsule_path", None)

    return result


def _validate_droplet_operation_required_fields(
    action: str,
    payload: Mapping[str, Any],
) -> None:
    """Validate raw required Droplet operation fields before defaults apply.

    This helper validates submitted connection shape only. It does not validate
    the raw confirmation value, because Droplet operation forms intentionally
    force confirmation to true after the operator submits an explicit Droplet
    operation form.
    """

    data = normalize_form_data(payload)
    data["action"] = action

    _required_text(data, "droplet_host", "target_host", "host", field="droplet_host")
    _required_text(data, "droplet_user", "ssh_user", "user", field="droplet_user")
    _required_text(
        data,
        "ssh_key_path",
        "ssh_key",
        "droplet_ssh_key",
        field="ssh_key_path",
    )
    remote_kx_root = _required_text(
        data,
        "remote_kx_root",
        "remote_root",
        "droplet_kx_root",
        "runtime_root",
        field="remote_kx_root",
    )

    remote_capsule_dir = _optional_text(
        data,
        "remote_capsule_dir",
        "droplet_capsule_dir",
        "capsule_dir",
        "target_capsule_dir",
    )

    if remote_capsule_dir:
        _validate_remote_capsule_dir_under_root(
            remote_capsule_dir=remote_capsule_dir,
            remote_kx_root=remote_kx_root,
        )

    _int_value(data.get("ssh_port"), default=22, field="ssh_port")


def _validate_capsule_droplet_required_fields(
    action: str,
    payload: Mapping[str, Any],
) -> None:
    """Validate required Droplet fields before form defaults can mask omissions.

    This helper is retained for compatibility with older internal callers. The
    public validation path for Droplet operation actions now goes through
    ``_validate_droplet_operation_action`` so canonical Droplet values are
    forced before confirmation is evaluated.
    """

    data = normalize_form_data(payload)
    data["action"] = action

    if action in DROPLET_OPERATION_ACTIONS:
        data["target_mode"] = "droplet"
        data["network_profile"] = "public_vps"
        data["exposure_mode"] = "public"
        data["confirmed"] = True

    _required_text(data, "droplet_host", "target_host", "host", field="droplet_host")
    _required_text(data, "droplet_user", "ssh_user", "user", field="droplet_user")
    _required_text(
        data,
        "ssh_key_path",
        "ssh_key",
        "droplet_ssh_key",
        field="ssh_key_path",
    )
    remote_kx_root = _required_text(
        data,
        "remote_kx_root",
        "remote_root",
        "droplet_kx_root",
        "runtime_root",
        field="remote_kx_root",
    )

    remote_capsule_dir = _optional_text(
        data,
        "remote_capsule_dir",
        "droplet_capsule_dir",
        "capsule_dir",
        "target_capsule_dir",
    )

    if remote_capsule_dir:
        _validate_remote_capsule_dir_under_root(
            remote_capsule_dir=remote_capsule_dir,
            remote_kx_root=remote_kx_root,
        )

    if not _bool_value(data.get("confirmed")):
        raise FormValidationError(
            "Droplet operation requires explicit confirmation.",
            field="confirmed",
        )

    _int_value(data.get("ssh_port"), default=22, field="ssh_port")


def _validate_non_capsule_droplet_action(
    action: str,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate bootstrap/check Droplet actions without capsule or FS checks.

    These actions need the Droplet connection payload, but they intentionally do
    not need a real capsule file. This registry-level validator also avoids
    checking that ssh_key_path exists, because the registry validates payload
    shape only; runtime SSH failures belong to the action backend.
    """

    data = normalize_form_data(payload)
    data["action"] = action

    instance_id = _required_text(data, "instance_id")
    droplet_host = _required_text(
        data,
        "droplet_host",
        "target_host",
        "host",
        field="droplet_host",
    )
    droplet_user = _required_text(
        data,
        "droplet_user",
        "ssh_user",
        "user",
        field="droplet_user",
    )
    ssh_key_path = _required_text(
        data,
        "ssh_key_path",
        "ssh_key",
        "droplet_ssh_key",
        field="ssh_key_path",
    )
    remote_kx_root = _required_text(
        data,
        "remote_kx_root",
        "remote_root",
        "droplet_kx_root",
        "runtime_root",
        field="remote_kx_root",
    )
    remote_capsule_dir = _required_text(
        data,
        "remote_capsule_dir",
        "droplet_capsule_dir",
        "capsule_dir",
        "target_capsule_dir",
        field="remote_capsule_dir",
    )
    domain = _required_text(data, "domain", "droplet_domain", field="domain")

    _validate_remote_capsule_dir_under_root(
        remote_capsule_dir=remote_capsule_dir,
        remote_kx_root=remote_kx_root,
    )

    confirmed = _bool_value(data.get("confirmed"))
    if not confirmed:
        raise FormValidationError(
            "Droplet operation requires explicit confirmation.",
            field="confirmed",
        )

    ssh_port = _int_value(data.get("ssh_port"), default=22, field="ssh_port")

    result: dict[str, Any] = {
        "action": action,
        "target_mode": "droplet",
        "network_profile": "public_vps",
        "exposure_mode": "public",
        "public_mode_enabled": True,
        "confirmed": True,
        "instance_id": instance_id,
        "droplet_name": _optional_text(data, "droplet_name") or DEFAULT_DROPLET_NAME,
        "droplet_host": droplet_host,
        "target_host": droplet_host,
        "host": droplet_host,
        "droplet_user": droplet_user,
        "ssh_user": droplet_user,
        "user": droplet_user,
        "ssh_key_path": ssh_key_path,
        "ssh_key": ssh_key_path,
        "droplet_ssh_key": ssh_key_path,
        "ssh_port": ssh_port,
        "remote_kx_root": remote_kx_root,
        "remote_root": remote_kx_root,
        "droplet_kx_root": remote_kx_root,
        "runtime_root": remote_kx_root,
        "remote_capsule_dir": remote_capsule_dir,
        "droplet_capsule_dir": remote_capsule_dir,
        "target_capsule_dir": remote_capsule_dir,
        "capsule_dir": remote_capsule_dir,
        "domain": domain,
        "droplet_domain": domain,
    }

    for key in (
        "source_dir",
        "capsule_output_dir",
        "output_dir",
        "capsule_id",
        "capsule_version",
        "version",
        "channel",
        "remote_agent_url",
        "droplet_agent_url",
    ):
        value = data.get(key)
        if value not in {None, ""}:
            result[key] = value

    if result.get("output_dir") and not result.get("capsule_output_dir"):
        result["capsule_output_dir"] = result["output_dir"]

    if result.get("version") and not result.get("capsule_version"):
        result["capsule_version"] = result["version"]

    if result.get("droplet_agent_url") and not result.get("remote_agent_url"):
        result["remote_agent_url"] = result["droplet_agent_url"]

    if result.get("remote_agent_url") and not result.get("droplet_agent_url"):
        result["droplet_agent_url"] = result["remote_agent_url"]

    cleaned = _clean_payload(result)
    cleaned.pop("capsule_file", None)
    cleaned.pop("capsule_path", None)

    return cleaned


def _clean_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Drop empty values and convert enum/path-like objects to JSON-safe values."""

    cleaned: dict[str, Any] = {}

    for key, value in payload.items():
        if value is None or value == "":
            continue

        cleaned[key] = _json_safe_value(value)

    return cleaned


def _json_safe_value(value: Any) -> Any:
    """Return a JSON-safe primitive where practical."""

    enum_value = getattr(value, "value", None)
    if enum_value is not None:
        return enum_value

    if isinstance(value, PurePosixPath):
        return str(value)

    if hasattr(value, "__fspath__"):
        return str(value)

    return value


def _required_text(
    data: Mapping[str, Any],
    *keys: str,
    field: str | None = None,
) -> str:
    """Return the first non-empty text value for keys or raise validation error."""

    for key in keys:
        value = data.get(key)

        if value is None:
            continue

        text = str(value).strip()
        if text:
            return text

    raise FormValidationError(
        f"{field or keys[0]} is required.",
        field=field or keys[0],
    )


def _optional_text(data: Mapping[str, Any], *keys: str) -> str | None:
    """Return the first non-empty text value for keys."""

    for key in keys:
        value = data.get(key)

        if value is None:
            continue

        text = str(value).strip()
        if text:
            return text

    return None


def _bool_value(value: Any) -> bool:
    """Return a bool from common GUI checkbox/string values."""

    if isinstance(value, bool):
        return value

    if value is None:
        return False

    normalized = str(value).strip().lower()

    if normalized in TRUE_VALUES:
        return True

    if normalized in FALSE_VALUES:
        return False

    return bool(normalized)


def _int_value(value: Any, *, default: int, field: str) -> int:
    """Return an int or raise a form validation error."""

    if value in {None, ""}:
        return default

    try:
        parsed = int(str(value))
    except (TypeError, ValueError) as exc:
        raise FormValidationError(
            f"{field} must be an integer.",
            field=field,
        ) from exc

    if parsed <= 0:
        raise FormValidationError(
            f"{field} must be greater than zero.",
            field=field,
        )

    return parsed


def _validate_remote_capsule_dir_under_root(
    *,
    remote_capsule_dir: str,
    remote_kx_root: str,
) -> None:
    """Ensure remote capsule directory is inside remote KX root."""

    child = PurePosixPath(remote_capsule_dir)
    parent = PurePosixPath(remote_kx_root)

    try:
        child.relative_to(parent)
    except ValueError as exc:
        raise FormValidationError(
            "remote_capsule_dir must be inside remote_kx_root.",
            field="remote_capsule_dir",
        ) from exc


__all__ = [
    "ACTION_ALIASES",
    "ACTION_FORM_MODELS",
    "DROPLET_CAPSULE_OPERATION_ACTIONS",
    "DROPLET_NON_CAPSULE_OPERATION_ACTIONS",
    "DROPLET_OPERATION_ACTIONS",
    "canonical_action_value",
    "form_to_payload",
    "is_known_action",
    "parse_action_form",
    "validate_action_payload",
]