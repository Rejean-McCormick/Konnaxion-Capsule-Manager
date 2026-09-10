# kx_manager/ui/page_parts/common.py

"""Shared page-part helpers for Konnaxion Capsule Manager UI pages.

Page-part modules render page bodies only. They do not return HTMLResponse and
do not dispatch actions. POST forms submit to canonical /ui/actions routes.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from kx_manager.ui.form_constants import (
    DEFAULT_CAPSULE_ID,
    DEFAULT_CAPSULE_OUTPUT_DIR,
    DEFAULT_CAPSULE_VERSION,
    DEFAULT_INSTANCE_ID,
    DEFAULT_RUNTIME_ROOT,
    DEFAULT_SOURCE_DIR,
    DockerService,
    ExposureMode,
    NetworkProfile,
)
from kx_manager.defaults import (
    DEFAULT_DROPLET_DOMAIN,
    DEFAULT_DROPLET_HOST,
    DEFAULT_DROPLET_INSTANCE_ID,
    DEFAULT_DROPLET_NAME,
    DEFAULT_DROPLET_USER,
    DEFAULT_EXPOSURE_MODE,
    DEFAULT_NETWORK_PROFILE,
    DEFAULT_REMOTE_AGENT_URL,
    DEFAULT_REMOTE_CAPSULE_DIR,
    DEFAULT_REMOTE_KX_ROOT,
    DEFAULT_SSH_KEY_PATH,
    DEFAULT_SSH_PORT,
    DEFAULT_TARGET_MODE,
)
from kx_manager.ui.render import (
    FormField,
    SelectOption,
    css_class,
    h,
    render_card,
    render_form,
    render_hidden,
    render_link,
    safe_href,
)

try:
    from kx_manager.services.targets import TargetMode
except Exception:  # pragma: no cover - staged build compatibility
    TargetMode = None  # type: ignore[assignment]

try:
    from kx_manager.ui.static import (
        ACTION_LABELS,
        APP_TITLE,
        BROWSER_LINK_ACTIONS,
        UI_BASE_PATH,
        route_for_action,
    )
except Exception:  # pragma: no cover - staged build compatibility
    APP_TITLE = "Konnaxion Capsule Manager"
    UI_BASE_PATH = "/ui"

    ACTION_LABELS = {
        "check_manager": "Check Manager",
        "check_agent": "Check Agent",
        "select_source_folder": "Select Source Folder",
        "select_capsule_output_folder": "Select Output Folder",
        "build_capsule": "Build Capsule",
        "rebuild_capsule": "Rebuild Capsule",
        "verify_capsule": "Verify Capsule",
        "import_capsule": "Import Capsule",
        "list_capsules": "List Capsules",
        "view_capsule": "View Capsule",
        "create_instance": "Create Instance",
        "update_instance": "Update Instance",
        "start_instance": "Start Instance",
        "stop_instance": "Stop Instance",
        "restart_instance": "Restart Instance",
        "instance_status": "Instance Status",
        "view_logs": "View Logs",
        "view_health": "Instance Health",
        "open_instance": "Open Instance",
        "rollback_instance": "Rollback",
        "create_backup": "Create Backup",
        "list_backups": "List Backups",
        "verify_backup": "Verify Backup",
        "restore_backup": "Restore Backup",
        "restore_backup_new": "Restore Backup New",
        "test_restore_backup": "Test Restore Backup",
        "run_security_check": "Run Security Check",
        "set_network_profile": "Set Network Profile",
        "disable_public_mode": "Disable Public Mode",
        "set_target_local": "Set Local Target",
        "set_target_intranet": "Set Intranet Target",
        "set_target_droplet": "Set Droplet Target",
        "set_target_temporary_public": "Set Temporary Public Target",
        "deploy_local": "Deploy Local",
        "deploy_intranet": "Deploy Intranet",
        "bootstrap_droplet_agent": "Bootstrap Droplet Agent",
        "check_droplet_agent": "Check Droplet Agent",
        "copy_capsule_to_droplet": "Copy Capsule to Droplet",
        "deploy_droplet": "Deploy Droplet",
        "start_droplet_instance": "Start Droplet Instance",
        "open_manager_docs": "Open Manager Docs",
        "open_agent_docs": "Open Agent Docs",
    }

    BROWSER_LINK_ACTIONS = {
        "open_instance": "runtime_url",
        "open_manager_docs": "/docs",
        "open_agent_docs": "/docs",
    }

    def route_for_action(action: Any) -> str:
        action_value = str(getattr(action, "value", action)).strip()
        return f"/ui/actions/{action_value.replace('_', '-')}"


DEFAULT_CAPSULE_FILE = (
    f"{DEFAULT_CAPSULE_OUTPUT_DIR}\\{DEFAULT_CAPSULE_ID}.kxcap"
    if "\\" in str(DEFAULT_CAPSULE_OUTPUT_DIR)
    else f"{DEFAULT_CAPSULE_OUTPUT_DIR}/{DEFAULT_CAPSULE_ID}.kxcap"
)

DEFAULT_PUBLIC_EXPIRATION = "2026-04-30T22:00:00Z"
DEFAULT_PRIVATE_HOST = "konnaxion.local"



def label(value: Any) -> str:
    """Return a human-readable label for an enum or canonical string."""

    text = str(getattr(value, "value", value))
    return text.replace("_", " ").replace("-", " ").title()


def enum_options(enum_type: Any) -> list[tuple[str, str]]:
    """Return select options for an enum-like type."""

    try:
        return [(str(item.value), label(item.value)) for item in enum_type]
    except TypeError:
        return []


def target_mode_options() -> list[tuple[str, str]]:
    """Return target-mode select options."""

    if TargetMode is None:
        return [
            ("local", "Local"),
            ("intranet", "Intranet"),
            ("temporary_public", "Temporary Public"),
            ("droplet", "Droplet"),
        ]

    return [(str(item.value), label(item.value)) for item in TargetMode]


def service_options() -> list[tuple[str, str]]:
    """Return Docker service select options with an all-services placeholder."""

    return [("", "All services")] + enum_options(DockerService)


def field(
    name: str,
    label_text: str,
    value: Any = "",
    *,
    field_type: str = "text",
    required: bool = False,
    placeholder: str = "",
    help_text: str = "",
    options: Sequence[SelectOption | tuple[str, str] | str] | None = None,
) -> FormField:
    """Create a render.FormField without exposing page-private helpers."""

    return FormField(
        name=name,
        label=label_text,
        value=value,
        field_type=field_type,
        required=required,
        placeholder=placeholder,
        help_text=help_text,
        options=options,
    )


def context_value(
    context: Mapping[str, Any],
    *names: str,
    default: Any = "",
) -> Any:
    """Return the first non-empty context value for the provided names."""

    for name in names:
        value = context.get(name)
        if value not in (None, ""):
            return value
    return default


def context_target_mode(context: Mapping[str, Any]) -> str:
    """Return the current target mode from context, defaulting to local."""

    return (
        str(context_value(context, "target_mode", default=DEFAULT_TARGET_MODE)).strip()
        or DEFAULT_TARGET_MODE
    )


def instance_id_field(value: Any = DEFAULT_INSTANCE_ID) -> FormField:
    return field(
        "instance_id",
        "Instance ID",
        value,
        required=True,
        help_text="Letters, numbers, dots, underscores, and hyphens only.",
    )


def capsule_id_field(value: Any = DEFAULT_CAPSULE_ID) -> FormField:
    return field(
        "capsule_id",
        "Capsule ID",
        value,
        required=True,
        help_text="Auto-generated from the current date at Manager startup; editable.",
    )


def capsule_version_field(value: Any = DEFAULT_CAPSULE_VERSION) -> FormField:
    return field(
        "capsule_version",
        "Capsule Version",
        value,
        required=True,
        help_text="Auto-generated from the current date at Manager startup; editable.",
    )


def capsule_file_field(
    *,
    value: Any = DEFAULT_CAPSULE_FILE,
    required: bool = True,
    must_exist_hint: bool = True,
) -> FormField:
    return field(
        "capsule_file",
        "Capsule File",
        value,
        required=required,
        help_text=(
            "Path to an existing .kxcap file."
            if must_exist_hint
            else "Path to a .kxcap file."
        ),
    )


def source_dir_field(value: Any = DEFAULT_SOURCE_DIR) -> FormField:
    return field("source_dir", "Konnaxion Source Folder", value, required=True)


def capsule_output_dir_field(value: Any = DEFAULT_CAPSULE_OUTPUT_DIR) -> FormField:
    return field("capsule_output_dir", "Capsule Output Folder", value, required=True)


def network_profile_field(
    value: str = DEFAULT_NETWORK_PROFILE,
    *,
    name: str = "network_profile",
) -> FormField:
    return field(
        name,
        "Network Profile" if name == "network_profile" else "Build Profile",
        value,
        field_type="select",
        required=True,
        options=enum_options(NetworkProfile),
    )


def exposure_mode_field(value: str = "private") -> FormField:
    return field(
        "exposure_mode",
        "Exposure Mode",
        value,
        field_type="select",
        required=True,
        options=enum_options(ExposureMode),
    )


def confirmed_field(label_text: str = "I confirm this action") -> FormField:
    return field(
        "confirmed",
        label_text,
        True,
        field_type="checkbox",
        help_text="Required for destructive or public-exposure actions.",
    )


def action_value(action: Any) -> str:
    return str(getattr(action, "value", action)).strip()


def action_label(action: Any) -> str:
    value = action_value(action)
    return ACTION_LABELS.get(value, label(value))


def is_browser_action(action: Any) -> bool:
    return action_value(action) in BROWSER_LINK_ACTIONS


def browser_action_url(
    action: Any,
    payload: Mapping[str, Any] | None = None,
) -> str:
    value = action_value(action)
    target = BROWSER_LINK_ACTIONS.get(value, "#")
    data = dict(payload or {})

    if target == "runtime_url":
        return str(
            data.get("runtime_url")
            or data.get("url")
            or data.get("public_url")
            or data.get("private_url")
            or data.get("local_url")
            or "#"
        )

    return str(target or "#")


def browser_action_link(
    action: Any,
    *,
    payload: Mapping[str, Any] | None = None,
    label_text: str | None = None,
    external: bool = False,
) -> str:
    value = action_value(action)

    return render_link(
        label_text or action_label(value),
        browser_action_url(value, payload),
        button=True,
        external=external,
    )


def action_path(action: Any) -> str:
    value = action_value(action)

    if value in BROWSER_LINK_ACTIONS:
        return "#"

    return route_for_action(value)


def action_form(
    action: str,
    fields: Sequence[FormField | Mapping[str, Any]] | str = "",
    *,
    submit_label: str | None = None,
    hidden: Mapping[str, Any] | None = None,
    extra_actions: str = "",
    classes: str = "",
) -> str:
    """Render a canonical page action form."""

    value = action_value(action)
    submit = submit_label or action_label(value)

    if is_browser_action(value):
        return browser_action_link(
            value,
            payload=hidden,
            label_text=submit,
        )

    form_hidden = {
        "action": value,
        **{key: item for key, item in dict(hidden or {}).items() if item is not None},
    }

    return render_form(
        action_path(value),
        fields,
        method="post",
        submit_label=submit,
        hidden=form_hidden,
        extra_actions=extra_actions,
        classes=classes,
    )


def button_form(
    action: str,
    label_text: str | None = None,
    *,
    payload: Mapping[str, Any] | None = None,
    variant: str = "secondary",
    disabled: bool = False,
) -> str:
    """Render a compact one-button POST action form."""

    value = action_value(action)

    if is_browser_action(value):
        return browser_action_link(
            value,
            payload=payload,
            label_text=label_text or action_label(value),
        )

    disabled_attr = " disabled" if disabled else ""
    button_class = css_class(
        "kx-button",
        "secondary" if variant == "secondary" else None,
        "danger" if variant == "danger" else None,
    )

    hidden = {
        "action": value,
        **{key: item for key, item in dict(payload or {}).items() if item is not None},
    }
    hidden_html = "".join(render_hidden(key, item) for key, item in hidden.items())

    return (
        f'<form method="post" action="{safe_href(action_path(value))}" '
        'style="display:inline">'
        f"{hidden_html}"
        f'<button class="{button_class}" type="submit"{disabled_attr}>'
        f"{h(label_text or action_label(value))}"
        "</button>"
        "</form>"
    )


def action_bar(actions: Sequence[str]) -> str:
    return f'<div class="kx-actions">{"".join(actions)}</div>'


def safety_note() -> str:
    return render_card(
        "Safety Boundary",
        (
            "<p>GUI pages only collect form input and submit canonical Manager actions. "
            "Privileged runtime work stays behind Manager services and the Konnaxion Agent.</p>"
        ),
    )


def default_payload(context: Mapping[str, Any]) -> dict[str, Any]:
    """Return the canonical local/private default payload."""

    return {
        "instance_id": context_value(
            context,
            "instance_id",
            default=DEFAULT_INSTANCE_ID,
        ),
        "capsule_id": context_value(
            context,
            "capsule_id",
            default=DEFAULT_CAPSULE_ID,
        ),
        "capsule_version": context_value(
            context,
            "capsule_version",
            "version",
            default=DEFAULT_CAPSULE_VERSION,
        ),
        "capsule_file": context_value(
            context,
            "capsule_file",
            "capsule_path",
            default=DEFAULT_CAPSULE_FILE,
        ),
        "capsule_path": context_value(
            context,
            "capsule_path",
            "capsule_file",
            default=DEFAULT_CAPSULE_FILE,
        ),
        "source_dir": context_value(
            context,
            "source_dir",
            default=DEFAULT_SOURCE_DIR,
        ),
        "capsule_output_dir": context_value(
            context,
            "capsule_output_dir",
            "output_dir",
            default=DEFAULT_CAPSULE_OUTPUT_DIR,
        ),
        "target_mode": context_value(context, "target_mode", default=DEFAULT_TARGET_MODE),
        "network_profile": context_value(
            context,
            "network_profile",
            default=DEFAULT_NETWORK_PROFILE,
        ),
        "exposure_mode": context_value(
            context,
            "exposure_mode",
            default=DEFAULT_EXPOSURE_MODE,
        ),
        "runtime_root": context_value(
            context,
            "runtime_root",
            default=DEFAULT_RUNTIME_ROOT,
        ),
        "capsule_dir": context_value(
            context,
            "capsule_dir",
            default=f"{DEFAULT_RUNTIME_ROOT}\\capsules",
        ),
        "host": context_value(context, "host", default=DEFAULT_PRIVATE_HOST),
        "domain": context_value(context, "domain", default=""),
        "runtime_url": context_value(
            context,
            "runtime_url",
            default="http://127.0.0.1",
        ),
    }


def local_payload(context: Mapping[str, Any]) -> dict[str, Any]:
    payload = default_payload(context)
    runtime_root = context_value(context, "runtime_root", default=DEFAULT_RUNTIME_ROOT)
    capsule_dir = context_value(
        context,
        "capsule_dir",
        "target_capsule_dir",
        default=f"{DEFAULT_RUNTIME_ROOT}\\capsules",
    )

    payload.update(
        {
            "target_mode": "local",
            "network_profile": "local_only",
            "exposure_mode": "private",
            "public_mode_enabled": "false",
            "public_mode_expires_at": "",
            "runtime_root": runtime_root,
            "capsule_dir": capsule_dir,
            "host": "",
            "confirmed": "",
        }
    )
    return payload


def intranet_payload(context: Mapping[str, Any]) -> dict[str, Any]:
    payload = default_payload(context)
    runtime_root = context_value(context, "runtime_root", default=DEFAULT_RUNTIME_ROOT)
    capsule_dir = context_value(
        context,
        "capsule_dir",
        "target_capsule_dir",
        default=f"{DEFAULT_RUNTIME_ROOT}\\capsules",
    )

    payload.update(
        {
            "target_mode": "intranet",
            "network_profile": "intranet_private",
            "exposure_mode": context_value(
                context,
                "exposure_mode",
                default="private",
            ),
            "public_mode_enabled": "false",
            "public_mode_expires_at": "",
            "runtime_root": runtime_root,
            "capsule_dir": capsule_dir,
            "host": context_value(
                context,
                "private_host",
                "host",
                default=DEFAULT_PRIVATE_HOST,
            ),
            "confirmed": "",
        }
    )
    return payload


def temporary_public_payload(context: Mapping[str, Any]) -> dict[str, Any]:
    payload = default_payload(context)
    runtime_root = context_value(context, "runtime_root", default=DEFAULT_RUNTIME_ROOT)
    capsule_dir = context_value(
        context,
        "capsule_dir",
        "target_capsule_dir",
        default=f"{DEFAULT_RUNTIME_ROOT}\\capsules",
    )
    public_host = context_value(context, "public_host", "host", default="")

    payload.update(
        {
            "target_mode": "temporary_public",
            "network_profile": "public_temporary",
            "exposure_mode": "temporary_tunnel",
            "public_mode_enabled": "true",
            "runtime_root": runtime_root,
            "capsule_dir": capsule_dir,
            "host": public_host,
            "public_host": public_host,
            "public_mode_expires_at": context_value(
                context,
                "public_mode_expires_at",
                default=DEFAULT_PUBLIC_EXPIRATION,
            ),
            "confirmed": "true",
        }
    )
    return payload


def _context_is_droplet(context: Mapping[str, Any]) -> bool:
    if context_target_mode(context) == "droplet":
        return True
    # A context may come from an older Manager state without target_mode but
    # still contain explicit Droplet-only fields. Treat those as intentional.
    return any(
        context.get(name) not in (None, "")
        for name in (
            "droplet_name",
            "droplet_host",
            "droplet_user",
            "ssh_key_path",
            "droplet_ssh_key",
            "remote_kx_root",
            "droplet_kx_root",
            "remote_capsule_dir",
            "droplet_capsule_dir",
            "droplet_domain",
        )
    )


def _droplet_host_from_context(context: Mapping[str, Any]) -> Any:
    explicit_host = context_value(
        context,
        "droplet_host",
        "target_host",
        default="",
    )
    if explicit_host:
        return explicit_host

    # Generic `host` belongs to whichever target mode was active previously.
    # Do not let a local/intranet host overwrite the Netcup Droplet default.
    if _context_is_droplet(context):
        host_alias = context_value(context, "host", default="")
        if host_alias and host_alias != DEFAULT_PRIVATE_HOST:
            return host_alias

    return DEFAULT_DROPLET_HOST


def droplet_payload(context: Mapping[str, Any]) -> dict[str, Any]:
    """Return a dedicated Droplet payload.

    This intentionally avoids inheriting private/intranet defaults from
    ``default_payload``. Droplet actions must submit an explicit Droplet payload,
    so a default private host such as ``konnaxion.local`` is never silently
    posted as the VPS host.

    ``remote_agent_url`` is optional. When blank, the backend should use
    SSH-local access to the Agent private listener inside the Droplet:
    http://127.0.0.1:8765/v1.
    """

    capsule_file = context_value(
        context,
        "capsule_file",
        "capsule_path",
        default=DEFAULT_CAPSULE_FILE,
    )
    is_droplet_context = _context_is_droplet(context)
    droplet_host = _droplet_host_from_context(context)
    explicit_domain = context_value(
        context,
        "droplet_domain",
        *( ("domain", "public_host") if is_droplet_context else () ),
        default="",
    )
    # Use the Netcup sslip.io hostname only for the configured default Netcup
    # host. If an operator types another VPS host, do not silently pair it with
    # the Netcup domain.
    domain = (
        explicit_domain
        if explicit_domain
        else DEFAULT_DROPLET_DOMAIN if droplet_host == DEFAULT_DROPLET_HOST else ""
    )
    remote_kx_root_names = ["remote_kx_root", "remote_root", "droplet_kx_root"]
    remote_capsule_dir_names = [
        "remote_capsule_dir",
        "target_capsule_dir",
        "droplet_capsule_dir",
    ]
    if is_droplet_context:
        remote_kx_root_names.append("runtime_root")
        remote_capsule_dir_names.append("capsule_dir")

    remote_kx_root = context_value(
        context,
        *remote_kx_root_names,
        default=DEFAULT_REMOTE_KX_ROOT,
    )
    remote_capsule_dir = context_value(
        context,
        *remote_capsule_dir_names,
        default=DEFAULT_REMOTE_CAPSULE_DIR,
    )

    return {
        "instance_id": (
            context_value(context, "instance_id", default=DEFAULT_DROPLET_INSTANCE_ID)
            if is_droplet_context
            else DEFAULT_DROPLET_INSTANCE_ID
        ),
        "capsule_id": context_value(
            context,
            "capsule_id",
            default=DEFAULT_CAPSULE_ID,
        ),
        "capsule_version": context_value(
            context,
            "capsule_version",
            "version",
            default=DEFAULT_CAPSULE_VERSION,
        ),
        "capsule_file": capsule_file,
        "capsule_path": capsule_file,
        "source_dir": context_value(
            context,
            "source_dir",
            default=DEFAULT_SOURCE_DIR,
        ),
        "capsule_output_dir": context_value(
            context,
            "capsule_output_dir",
            "output_dir",
            default=DEFAULT_CAPSULE_OUTPUT_DIR,
        ),
        "target_mode": "droplet",
        "network_profile": "public_vps",
        "exposure_mode": "public",
        "public_mode_enabled": "true",
        "public_mode_expires_at": "",
        "droplet_name": context_value(
            context,
            "droplet_name",
            default=DEFAULT_DROPLET_NAME,
        ),
        "droplet_host": droplet_host,
        "host": droplet_host,
        "droplet_user": context_value(
            context,
            "droplet_user",
            "ssh_user",
            "user",
            default=DEFAULT_DROPLET_USER,
        ),
        "ssh_key_path": context_value(
            context,
            "ssh_key_path",
            "droplet_ssh_key",
            "ssh_key",
            default=DEFAULT_SSH_KEY_PATH,
        ),
        "ssh_port": context_value(
            context,
            "ssh_port",
            default=DEFAULT_SSH_PORT,
        ),
        "remote_kx_root": remote_kx_root,
        "runtime_root": remote_kx_root,
        "remote_capsule_dir": remote_capsule_dir,
        "capsule_dir": remote_capsule_dir,
        "domain": domain,
        "droplet_domain": domain,
        "remote_agent_url": context_value(
            context,
            "remote_agent_url",
            "droplet_agent_url",
            default=DEFAULT_REMOTE_AGENT_URL,
        ),
        "confirmed": "true",
    }


def droplet_visible_field_names(*, include_capsule: bool) -> set[str]:
    names = {
        "instance_id",
        "droplet_name",
        "droplet_host",
        "droplet_user",
        "ssh_key_path",
        "ssh_port",
        "remote_kx_root",
        "remote_capsule_dir",
        "domain",
        "remote_agent_url",
        "confirmed",
    }

    if include_capsule:
        names.add("capsule_file")

    return names


def droplet_operation_hidden(
    context: Mapping[str, Any],
    *,
    include_capsule: bool,
) -> dict[str, Any]:
    payload = droplet_payload(context)
    visible = droplet_visible_field_names(include_capsule=include_capsule)

    hidden = {
        key: value
        for key, value in payload.items()
        if key not in visible and value is not None
    }

    if not include_capsule:
        hidden.pop("capsule_file", None)
        hidden.pop("capsule_path", None)

    hidden.update(
        {
            "target_mode": "droplet",
            "network_profile": "public_vps",
            "exposure_mode": "public",
            "public_mode_enabled": "true",
            "confirmed": "true",
        }
    )

    return hidden


def droplet_operation_fields(
    context: Mapping[str, Any],
    *,
    include_capsule: bool,
) -> list[FormField]:
    payload = droplet_payload(context)

    fields: list[FormField] = [
        instance_id_field(payload["instance_id"]),
    ]

    if include_capsule:
        fields.append(
            capsule_file_field(
                value=payload["capsule_file"],
                required=True,
                must_exist_hint=True,
            )
        )

    fields.extend(
        [
            field(
                "droplet_name",
                "Droplet Name",
                payload["droplet_name"],
                required=True,
            ),
            field(
                "droplet_host",
                "Droplet Host / IP",
                payload["droplet_host"],
                required=True,
            ),
            field(
                "droplet_user",
                "SSH User",
                payload["droplet_user"],
                required=True,
            ),
            field(
                "ssh_key_path",
                "SSH Key Path",
                payload["ssh_key_path"],
                required=True,
            ),
            field(
                "ssh_port",
                "SSH Port",
                payload["ssh_port"],
                field_type="number",
                required=True,
            ),
            field(
                "remote_kx_root",
                "Remote KX Root",
                payload["remote_kx_root"],
                required=True,
            ),
            field(
                "remote_capsule_dir",
                "Remote Capsule Directory",
                payload["remote_capsule_dir"],
                required=True,
            ),
            field(
                "domain",
                "Domain",
                payload["domain"],
                required=True,
                help_text=(
                    "Required public DNS name, sslip.io host, "
                    "or accepted public host alias."
                ),
            ),
            field(
                "remote_agent_url",
                "Remote Agent URL",
                payload["remote_agent_url"],
                required=False,
                help_text=(
                    "Optional. Leave blank for the normal private Droplet Agent. "
                    "Blank means the Manager will reach "
                    "http://127.0.0.1:8765/v1 inside the Droplet over SSH. "
                    "Use this only for an explicit tunnel such as "
                    "http://127.0.0.1:18765/v1."
                ),
            ),
            confirmed_field("I confirm public VPS operation"),
        ]
    )

    return fields


def droplet_operation_form(
    action: str,
    context: Mapping[str, Any],
    *,
    include_capsule: bool = False,
    submit_label: str | None = None,
    classes: str = "",
) -> str:
    hidden = droplet_operation_hidden(context, include_capsule=include_capsule)

    # Step 3 on the Droplet page already uploads the capsule. Step 4 must not
    # silently upload the same ~GB capsule a second time. Direct service/API
    # callers can still explicitly request copy_capsule=true.
    if action in {"copy_capsule_to_droplet", "deploy_droplet"}:
        hidden["background_job"] = "true"

    if action == "deploy_droplet":
        hidden["copy_capsule"] = "false"

    return action_form(
        action,
        droplet_operation_fields(context, include_capsule=include_capsule),
        submit_label=submit_label,
        hidden=hidden,
        classes=classes,
    )


__all__ = [
    "APP_TITLE",
    "DEFAULT_CAPSULE_FILE",
    "DEFAULT_DROPLET_DOMAIN",
    "DEFAULT_DROPLET_HOST",
    "DEFAULT_DROPLET_INSTANCE_ID",
    "DEFAULT_DROPLET_NAME",
    "DEFAULT_DROPLET_USER",
    "DEFAULT_PRIVATE_HOST",
    "DEFAULT_PUBLIC_EXPIRATION",
    "DEFAULT_REMOTE_AGENT_URL",
    "DEFAULT_REMOTE_CAPSULE_DIR",
    "DEFAULT_REMOTE_KX_ROOT",
    "DEFAULT_SSH_KEY_PATH",
    "DEFAULT_SSH_PORT",
    "UI_BASE_PATH",
    "action_bar",
    "action_form",
    "action_label",
    "action_path",
    "action_value",
    "browser_action_link",
    "browser_action_url",
    "button_form",
    "capsule_file_field",
    "capsule_id_field",
    "capsule_output_dir_field",
    "capsule_version_field",
    "confirmed_field",
    "context_target_mode",
    "context_value",
    "default_payload",
    "droplet_operation_fields",
    "droplet_operation_form",
    "droplet_operation_hidden",
    "droplet_payload",
    "droplet_visible_field_names",
    "exposure_mode_field",
    "field",
    "instance_id_field",
    "intranet_payload",
    "is_browser_action",
    "label",
    "local_payload",
    "network_profile_field",
    "safety_note",
    "service_options",
    "source_dir_field",
    "target_mode_options",
    "temporary_public_payload",
]