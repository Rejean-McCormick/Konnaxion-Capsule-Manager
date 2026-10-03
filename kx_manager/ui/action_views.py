# kx_manager/ui/action_views.py

"""Action view helpers for the Konnaxion Capsule Manager GUI.

This module renders action-oriented UI fragments only.

It must not dispatch GUI actions, execute commands, control Docker, mutate
runtime state, contact the Agent, or perform privileged work. POST submissions
must go through the registered FastAPI action routes and
kx_manager.ui.actions.dispatch_gui_action.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from typing import Any, Iterable, Mapping, Sequence

from fastapi.responses import HTMLResponse


try:
    from kx_manager.ui.static import (
        ACTION_BASE_PATH,
        ACTION_LABELS,
        ACTION_ROUTES,
        BROWSER_LINK_ACTIONS,
        CONTRACT_ACTIONS,
        canonical_action,
        route_for_action,
    )
except Exception:  # pragma: no cover - staged build compatibility
    ACTION_BASE_PATH = "/ui/actions"

    CONTRACT_ACTIONS = (
        "check_manager",
        "check_agent",
        "select_source_folder",
        "select_capsule_output_folder",
        "build_capsule",
        "rebuild_capsule",
        "verify_capsule",
        "import_capsule",
        "list_capsules",
        "view_capsule",
        "create_instance",
        "update_instance",
        "start_instance",
        "stop_instance",
        "restart_instance",
        "instance_status",
        "view_logs",
        "view_health",
        "open_instance",
        "rollback_instance",
        "create_backup",
        "list_backups",
        "verify_backup",
        "restore_backup",
        "restore_backup_new",
        "test_restore_backup",
        "run_security_check",
        "set_network_profile",
        "disable_public_mode",
        "set_target_local",
        "set_target_intranet",
        "set_target_droplet",
        "set_target_temporary_public",
        "deploy_local",
        "deploy_intranet",
        "deploy_droplet",
        "check_droplet_agent",
        "copy_capsule_to_droplet",
        "start_droplet_instance",
        "open_manager_docs",
        "open_agent_docs",
    )

    ACTION_LABELS = {
        action: action.replace("_", " ").title()
        for action in CONTRACT_ACTIONS
    }

    BROWSER_LINK_ACTIONS: dict[str, str] = {
        "open_instance": "runtime_url",
        "open_manager_docs": "/docs",
        "open_agent_docs": "/docs",
    }

    BROWSER_ONLY_ACTIONS: frozenset[str] = frozenset(BROWSER_LINK_ACTIONS)

    ACTION_ROUTES = {
        action: f"{ACTION_BASE_PATH}/{action.replace('_', '-')}"
        for action in CONTRACT_ACTIONS
        if action not in BROWSER_ONLY_ACTIONS
    }

    def canonical_action(action: Any) -> str:
        return str(getattr(action, "value", action)).strip()

    def route_for_action(action: Any) -> str:
        action_value = canonical_action(action)
        if action_value in BROWSER_ONLY_ACTIONS:
            raise KeyError(f"Browser-only action has no POST route: {action_value}")
        return ACTION_ROUTES.get(
            action_value,
            f"{ACTION_BASE_PATH}/{action_value.replace('_', '-')}",
        )


from kx_manager.ui.render import (
    FormField,
    attr,
    css_class,
    h,
    html_response,
    render_action_bar,
    render_card,
    render_definition_list,
    render_empty_state,
    render_form,
    render_grid,
    render_json_block,
    render_link,
    render_log_block,
    render_result_panel,
    render_status,
)


@dataclass(frozen=True, slots=True)
class ActionView:
    """Declarative render model for one GUI action form."""

    action: str
    title: str | None = None
    description: str = ""
    fields: Sequence[FormField | Mapping[str, Any]] | str = ""
    hidden: Mapping[str, Any] | None = None
    submit_label: str | None = None
    tone: str = "info"
    danger: bool = False
    disabled: bool = False
    confirm_text: str | None = None
    extra_actions: str = ""


@dataclass(frozen=True, slots=True)
class ActionGroupView:
    """Declarative render model for a group of related actions."""

    title: str
    actions: Sequence[ActionView | str]
    description: str = ""


@dataclass(frozen=True, slots=True)
class ActionResultView:
    """Normalized action result view model."""

    ok: bool
    action: str
    message: str
    instance_id: str | None = None
    data: Mapping[str, Any] | None = None
    stdout: str | None = None
    stderr: str | None = None
    returncode: int | None = None


ACTION_GROUPS: tuple[ActionGroupView, ...] = (
    ActionGroupView(
        title="Manager",
        description="Manager and Agent connectivity checks.",
        actions=(
            "check_manager",
            "check_agent",
            "open_manager_docs",
            "open_agent_docs",
        ),
    ),
    ActionGroupView(
        title="Capsules",
        description="Select folders, build, verify, import, and inspect capsules.",
        actions=(
            "select_source_folder",
            "select_capsule_output_folder",
            "build_capsule",
            "rebuild_capsule",
            "verify_capsule",
            "import_capsule",
            "list_capsules",
            "view_capsule",
        ),
    ),
    ActionGroupView(
        title="Instances",
        description="Create, update, start, stop, restart, inspect, and open instances.",
        actions=(
            "create_instance",
            "update_instance",
            "start_instance",
            "stop_instance",
            "restart_instance",
            "instance_status",
            "view_health",
            "view_logs",
            "open_instance",
            "rollback_instance",
        ),
    ),
    ActionGroupView(
        title="Security and Network",
        description="Run Security Gate checks and manage network exposure.",
        actions=(
            "run_security_check",
            "set_network_profile",
            "disable_public_mode",
        ),
    ),
    ActionGroupView(
        title="Backups and Restore",
        description="Create, list, verify, test, restore, and rollback backups.",
        actions=(
            "create_backup",
            "list_backups",
            "verify_backup",
            "restore_backup",
            "restore_backup_new",
            "test_restore_backup",
        ),
    ),
    ActionGroupView(
        title="Targets and Deployment",
        description="Select local, intranet, temporary-public, or Droplet deployment targets.",
        actions=(
            "set_target_local",
            "set_target_intranet",
            "set_target_temporary_public",
            "set_target_droplet",
            "deploy_local",
            "deploy_intranet",
            "deploy_droplet",
            "check_droplet_agent",
            "copy_capsule_to_droplet",
            "start_droplet_instance",
        ),
    ),
)


ACTION_DESCRIPTIONS: dict[str, str] = {
    "check_manager": "Check whether the local Manager API is reachable.",
    "check_agent": "Check whether the Konnaxion Agent is reachable.",
    "select_source_folder": "Select the Konnaxion source folder used for capsule builds.",
    "select_capsule_output_folder": "Select where built .kxcap files should be written.",
    "build_capsule": "Build a signed Konnaxion Capsule from the selected source tree.",
    "rebuild_capsule": "Force rebuild a Konnaxion Capsule.",
    "verify_capsule": "Verify a .kxcap capsule before import or deployment.",
    "import_capsule": "Import a verified capsule into the local Agent inventory.",
    "list_capsules": "List available or imported capsules.",
    "view_capsule": "Load details for one capsule.",
    "create_instance": "Create a Konnaxion instance from an imported capsule.",
    "update_instance": "Update an existing instance from a new capsule.",
    "start_instance": "Start a Konnaxion instance through the Agent.",
    "stop_instance": "Stop a Konnaxion instance through the Agent.",
    "restart_instance": "Stop and then start a Konnaxion instance.",
    "instance_status": "Load current instance status.",
    "view_logs": "Load runtime logs for an instance.",
    "view_health": "Load runtime health for an instance.",
    "open_instance": "Prepare the instance URL for opening in a browser.",
    "rollback_instance": "Rollback an instance using an approved rollback flow.",
    "create_backup": "Create a backup for an instance.",
    "list_backups": "List backups, optionally filtered by instance.",
    "verify_backup": "Verify one backup.",
    "restore_backup": "Restore a backup into the current instance.",
    "restore_backup_new": "Restore a backup into a new instance.",
    "test_restore_backup": "Run a test restore workflow.",
    "run_security_check": "Run Security Gate checks for an instance.",
    "set_network_profile": "Set network profile and exposure mode for an instance.",
    "disable_public_mode": "Return an instance to private intranet exposure.",
    "set_target_local": "Select local same-machine deployment.",
    "set_target_intranet": "Select private intranet deployment.",
    "set_target_droplet": "Select remote public Droplet/VPS deployment.",
    "set_target_temporary_public": "Select temporary public demo exposure.",
    "deploy_local": "Run the local deployment flow.",
    "deploy_intranet": "Run the intranet deployment flow.",
    "deploy_droplet": "Run the remote Droplet/VPS deployment flow.",
    "scan_netcup_host_key": "Read the fresh Netcup VPS SSH host-key fingerprints without changing the server.",
    "provision_netcup_vps": "Provision a freshly reinstalled Netcup VPS with kx-admin and hardened SSH.",
    "check_droplet_agent": "Check the remote Droplet Agent.",
    "copy_capsule_to_droplet": "Copy a capsule to the configured Droplet.",
    "start_droplet_instance": "Start the remote Droplet instance.",
    "open_manager_docs": "Prepare the Manager API docs URL.",
    "open_agent_docs": "Prepare the Agent API docs URL.",
}


DANGER_ACTIONS: frozenset[str] = frozenset(
    {
        "stop_instance",
        "restart_instance",
        "rollback_instance",
        "restore_backup",
        "restore_backup_new",
        "disable_public_mode",
        "set_target_droplet",
        "deploy_droplet",
        "provision_netcup_vps",
        "start_droplet_instance",
    }
)


CONFIRM_ACTIONS: frozenset[str] = frozenset(
    {
        "stop_instance",
        "rollback_instance",
        "restore_backup",
        "restore_backup_new",
        "disable_public_mode",
        "set_target_temporary_public",
        "set_target_droplet",
        "deploy_droplet",
        "provision_netcup_vps",
        "start_droplet_instance",
    }
)


ACTION_BACK_ROUTES: dict[str, str] = {
    "check_manager": "/ui/health",
    "check_agent": "/ui/health",
    "select_source_folder": "/ui/settings",
    "select_capsule_output_folder": "/ui/settings",
    "build_capsule": "/ui/capsules",
    "rebuild_capsule": "/ui/capsules",
    "verify_capsule": "/ui/capsules",
    "import_capsule": "/ui/capsules",
    "list_capsules": "/ui/capsules",
    "view_capsule": "/ui/capsules",
    "create_instance": "/ui/instances",
    "update_instance": "/ui/instances",
    "start_instance": "/ui/instances",
    "stop_instance": "/ui/instances",
    "restart_instance": "/ui/instances",
    "instance_status": "/ui/instances",
    "view_logs": "/ui/logs",
    "view_health": "/ui/health",
    "open_instance": "/ui/instances",
    "rollback_instance": "/ui/restore",
    "create_backup": "/ui/backups",
    "list_backups": "/ui/backups",
    "verify_backup": "/ui/backups",
    "restore_backup": "/ui/restore",
    "restore_backup_new": "/ui/restore",
    "test_restore_backup": "/ui/restore",
    "run_security_check": "/ui/security",
    "set_network_profile": "/ui/network",
    "disable_public_mode": "/ui/network",

    # Target configuration actions return to Targets.
    "set_target_local": "/ui/targets",
    "set_target_intranet": "/ui/targets",
    "set_target_droplet": "/ui/targets",
    "set_target_temporary_public": "/ui/targets",

    # Deployment operation actions return to Deploy.
    "deploy_local": "/ui/deploy",
    "deploy_intranet": "/ui/deploy",
    "deploy_droplet": "/ui/deploy",
    "scan_netcup_host_key": "/ui/deploy",
    "provision_netcup_vps": "/ui/deploy",
    "check_droplet_agent": "/ui/deploy",
    "copy_capsule_to_droplet": "/ui/deploy",
    "start_droplet_instance": "/ui/deploy",

    "open_manager_docs": "/ui/about",
    "open_agent_docs": "/ui/about",
}


def action_value(action: Any) -> str:
    """Return the canonical action string."""

    return canonical_action(action)


def action_label(action: Any) -> str:
    """Return a human-readable label for an action."""

    value = action_value(action)
    return ACTION_LABELS.get(value, value.replace("_", " ").title())


def action_description(action: Any) -> str:
    """Return the default help text for an action."""

    return ACTION_DESCRIPTIONS.get(action_value(action), "")


def is_browser_action(action: Any) -> bool:
    """Return whether an action should render as a browser link."""

    return action_value(action) in BROWSER_LINK_ACTIONS


def browser_action_href(
    action: Any,
    payload: Mapping[str, Any] | None = None,
) -> str:
    """Return the href for a browser-only action."""

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


def action_route(action: Any) -> str:
    """Return the registered POST route for an action."""

    value = action_value(action)

    if is_browser_action(value):
        raise KeyError(f"Browser-only action has no POST route: {value}")

    return route_for_action(value)


def is_danger_action(action: Any) -> bool:
    """Return whether an action should be rendered as destructive/risky."""

    return action_value(action) in DANGER_ACTIONS


def requires_confirmation(action: Any) -> bool:
    """Return whether an action should include explicit confirmation."""

    return action_value(action) in CONFIRM_ACTIONS


def default_action_view(
    action: Any,
    *,
    payload: Mapping[str, Any] | None = None,
    fields: Sequence[FormField | Mapping[str, Any]] | str = "",
    title: str | None = None,
    description: str | None = None,
    submit_label: str | None = None,
    disabled: bool = False,
) -> ActionView:
    """Build a default action view for a canonical GUI action."""

    value = action_value(action)
    danger = is_danger_action(value)

    hidden: dict[str, Any] = {"action": value}
    hidden.update(dict(payload or {}))

    confirm_text = None
    if requires_confirmation(value):
        confirm_text = f"Confirm {action_label(value)}."

    return ActionView(
        action=value,
        title=title or action_label(value),
        description=description if description is not None else action_description(value),
        fields=fields,
        hidden=hidden,
        submit_label=submit_label or action_label(value),
        tone="warn" if danger else "info",
        danger=danger,
        disabled=disabled,
        confirm_text=confirm_text,
    )


def render_action_view(view: ActionView | str) -> str:
    """Render one action form card."""

    model = default_action_view(view) if isinstance(view, str) else view
    action = action_value(model.action)
    title = model.title or action_label(action)

    body_parts: list[str] = []

    if model.description:
        body_parts.append(f'<p class="kx-muted">{h(model.description)}</p>')

    if model.confirm_text:
        body_parts.append(
            '<p class="kx-muted">'
            f"{h(model.confirm_text)} Submit only after operator review."
            "</p>"
        )

    hidden = {"action": action}
    hidden.update(dict(model.hidden or {}))

    if is_browser_action(action):
        href = browser_action_href(action, hidden)
        body_parts.append(
            '<div class="kx-actions">'
            + render_link(
                model.submit_label or action_label(action),
                href,
                button=True,
                external=_is_external_href(href),
            )
            + "</div>"
        )
    elif model.disabled:
        body_parts.append(
            '<p class="kx-muted">This action is currently disabled.</p>'
        )
        submit_label = model.submit_label or action_label(action)
        body_parts.append(_render_disabled_form_message(submit_label))
    else:
        button_class = "danger" if model.danger else ""

        body_parts.append(
            render_form(
                action_route(action),
                model.fields,
                method="post",
                submit_label=model.submit_label or action_label(action),
                hidden=hidden,
                extra_actions=model.extra_actions,
                classes=button_class,
            )
        )

    return render_card(
        title,
        "".join(body_parts),
        classes=f"kx-action-view kx-result {model.tone}",
    )


def render_action_group(group: ActionGroupView) -> str:
    """Render a group of related action cards."""

    intro = f'<p class="kx-muted">{h(group.description)}</p>' if group.description else ""
    cards = [render_action_view(action) for action in group.actions]

    return render_card(
        group.title,
        intro + render_grid(cards),
        classes="kx-action-group",
    )


def render_action_groups(
    groups: Sequence[ActionGroupView] = ACTION_GROUPS,
) -> str:
    """Render all action groups."""

    return "".join(render_action_group(group) for group in groups)


def render_action_catalog(
    *,
    groups: Sequence[ActionGroupView] = ACTION_GROUPS,
    result: Any | None = None,
) -> str:
    """Render the complete GUI action catalog."""

    result_html = render_action_result(result) if result is not None else ""

    return (
        result_html
        + render_card(
            "Actions",
            '<p class="kx-muted">All GUI actions post to allowlisted Manager routes.</p>'
            + render_action_groups(groups),
        )
    )


def render_action_button(
    action: Any,
    label: str | None = None,
    *,
    payload: Mapping[str, Any] | None = None,
    variant: str = "primary",
    disabled: bool = False,
) -> str:
    """Render a compact one-button action control."""

    value = action_value(action)

    if is_browser_action(value):
        href = browser_action_href(value, payload)
        return render_link(
            label or action_label(value),
            href,
            button=True,
            external=_is_external_href(href),
        )

    hidden = {"action": value}
    hidden.update(dict(payload or {}))

    button_label = label or action_label(value)
    button_classes = css_class(
        "kx-button",
        "secondary" if variant == "secondary" else None,
        "danger" if variant == "danger" or is_danger_action(value) else None,
    )
    disabled_attr = " disabled" if disabled else ""

    hidden_html = "".join(
        f'<input type="hidden" name="{attr(key)}" value="{attr(item)}">'
        for key, item in hidden.items()
        if item is not None
    )

    return (
        f'<form method="post" action="{attr(action_route(value))}" style="display:inline">'
        f"{hidden_html}"
        f'<button class="{button_classes}" type="submit"{disabled_attr}>{h(button_label)}</button>'
        "</form>"
    )


def render_action_buttons(
    actions: Iterable[Any],
    *,
    payload: Mapping[str, Any] | None = None,
) -> str:
    """Render a compact action bar."""

    return render_action_bar(
        [
            render_action_button(
                action,
                payload=payload,
                variant="danger" if is_danger_action(action) else "secondary",
            )
            for action in actions
        ]
    )


def normalize_action_result(result: Any) -> ActionResultView:
    """Normalize mappings, dataclasses, pydantic models, or result objects."""

    if isinstance(result, ActionResultView):
        return result

    if isinstance(result, Mapping):
        data = dict(result)
    elif is_dataclass(result):
        data = asdict(result)
    else:
        to_dict = getattr(result, "to_dict", None)
        if callable(to_dict):
            raw = to_dict()
            data = dict(raw) if isinstance(raw, Mapping) else {}
        else:
            model_dump = getattr(result, "model_dump", None)
            if callable(model_dump):
                raw = model_dump()
                data = dict(raw) if isinstance(raw, Mapping) else {}
            else:
                data = {
                    "ok": True,
                    "message": str(result),
                    "data": {"result": repr(result)},
                }

    return ActionResultView(
        ok=bool(data.get("ok", data.get("success", False))),
        action=str(data.get("action") or data.get("operation") or ""),
        message=_message_from_result_data(data),
        instance_id=_optional_str(data.get("instance_id")),
        data=_mapping_or_none(data.get("data") or data.get("payload")),
        stdout=_optional_str(data.get("stdout")),
        stderr=_optional_str(data.get("stderr")),
        returncode=_optional_int(data.get("returncode")),
    )


def render_action_result(result: Any) -> str:
    """Render a normalized action result."""

    if result is None:
        return ""

    model = normalize_action_result(result)
    data = action_result_to_mapping(model)

    body = render_result_panel(data)
    browser_link = render_browser_link_result(model)
    output = render_command_output(model)

    return body + browser_link + output


def render_action_result_page(
    *,
    action: Any,
    payload: Mapping[str, Any] | None = None,
    result: Any = None,
) -> HTMLResponse:
    """Render a full HTML page for a submitted GUI action result.

    Browser form posts should land on a Manager UI page, not raw JSON.
    This function only renders the already-dispatched result; it does not
    execute actions or mutate runtime state.
    """

    value = action_value(action)
    label = action_label(value)
    title = f"{label} Result"

    model = normalize_action_result(result) if result is not None else None
    tone = "ok" if model and model.ok else "error" if model else "info"
    status_label = "Success" if model and model.ok else "Failed" if model else "No result"

    result_html = render_action_result(result)
    if not result_html:
        result_html = render_empty_state("No result was returned for this action.")

    body_parts: list[str] = [
        render_card(
            title,
            render_definition_list(
                {
                    "Status": render_status(status_label),
                    "Action": value,
                    "Message": model.message if model else "No result was returned.",
                    "Instance": model.instance_id if model else None,
                }
            ),
            classes=f"kx-result {tone}",
        ),
        render_card(
            "Action result",
            result_html,
            classes="kx-action-result-page",
        ),
    ]

    if model is not None:
        body_parts.append(
            render_card(
                "Result payload",
                render_json_block(action_result_to_mapping(model)),
                classes=f"kx-result {tone}",
            )
        )

    display_payload = _display_payload(payload)
    if display_payload:
        body_parts.append(
            render_card(
                "Submitted values",
                render_json_block(display_payload),
                classes="kx-result info",
            )
        )

    back_href = back_route_for_action(value)

    body_parts.append(
        '<div class="kx-actions">'
        f'{render_link("Back", back_href, button=True)}'
        f'{render_link("Back to Dashboard", "/ui", button=True)}'
        "</div>"
    )

    return html_response(
        title,
        "".join(body_parts),
        subtitle="Local Manager action result.",
        active_href=back_href,
        status_code=200,
    )


def render_browser_link_result(result: Any) -> str:
    """Render a browser-link result if the action returned a URL."""

    model = normalize_action_result(result)
    data = dict(model.data or {})
    url = _optional_str(data.get("url"))

    if not url:
        return ""

    kind = str(data.get("kind") or "")
    if kind and kind != "browser_link":
        return ""

    label = "Open URL"
    if model.action == "open_instance":
        label = "Open Instance"
    elif model.action == "open_manager_docs":
        label = "Open Manager Docs"
    elif model.action == "open_agent_docs":
        label = "Open Agent Docs"

    return render_card(
        "Open",
        render_link(label, url, button=True, external=True)
        + "<h3>URL</h3>"
        + render_definition_list({"url": url}),
        classes="kx-result info",
    )


def render_command_output(result: Any) -> str:
    """Render stdout/stderr blocks from an action result."""

    model = normalize_action_result(result)
    parts: list[str] = []

    if model.stdout:
        parts.append(render_card("Standard output", render_log_block(model.stdout)))

    if model.stderr:
        parts.append(
            render_card(
                "Standard error",
                render_log_block(model.stderr),
                classes="kx-result error",
            )
        )

    return "".join(parts)


def render_action_summary_table(actions: Sequence[Any] = CONTRACT_ACTIONS) -> str:
    """Render a table-like summary of canonical actions."""

    rows = []

    for action in actions:
        value = action_value(action)
        route_or_link = (
            browser_action_href(value)
            if is_browser_action(value)
            else action_route(value)
        )
        rows.append(
            {
                "action": value,
                "label": action_label(value),
                "route": route_or_link,
                "danger": render_status("true" if is_danger_action(value) else "false"),
                "confirmation": render_status("true" if requires_confirmation(value) else "false"),
            }
        )

    from kx_manager.ui.render import render_table

    return render_table(
        rows,
        ["action", "label", "route", "danger", "confirmation"],
    )


def action_result_to_mapping(model: ActionResultView) -> dict[str, Any]:
    """Convert a normalized action result to a JSON-safe mapping."""

    return {
        "ok": model.ok,
        "action": model.action,
        "message": model.message,
        "instance_id": model.instance_id,
        "data": dict(model.data or {}),
        "stdout": model.stdout,
        "stderr": model.stderr,
        "returncode": model.returncode,
    }


def back_route_for_action(action: Any) -> str:
    """Return the best UI page to return to after an action result."""

    value = action_value(action)
    return ACTION_BACK_ROUTES.get(value, "/ui")


def _render_disabled_form_message(submit_label: str) -> str:
    return (
        '<div class="kx-actions">'
        f'<button class="kx-button" type="button" disabled>{h(submit_label)}</button>'
        "</div>"
    )


def _is_external_href(href: str) -> bool:
    return href.startswith(("http://", "https://"))


def _message_from_result_data(data: Mapping[str, Any]) -> str:
    message = data.get("message")
    if message not in (None, ""):
        return str(message)

    detail = data.get("detail")
    if isinstance(detail, str):
        return detail

    if detail not in (None, ""):
        return str(detail)

    error = data.get("error")
    if isinstance(error, Mapping):
        for key in ("message", "detail", "code"):
            value = error.get(key)
            if value not in (None, ""):
                return str(value)
        return str(dict(error))

    if error not in (None, ""):
        return str(error)

    return ""


def _display_payload(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    if not payload:
        return {}

    return {
        str(key): item
        for key, item in dict(payload).items()
        if item is not None and item != ""
    }


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None

    if isinstance(value, str) and value == "":
        return None

    return str(value)


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None

    if isinstance(value, str) and value == "":
        return None

    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _mapping_or_none(value: Any) -> Mapping[str, Any] | None:
    if value is None:
        return None

    if isinstance(value, str) and value == "":
        return None

    if isinstance(value, Mapping):
        return value

    if is_dataclass(value):
        return asdict(value)

    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        data = to_dict()
        if isinstance(data, Mapping):
            return data

    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        data = model_dump()
        if isinstance(data, Mapping):
            return data

    return {"result": value}


__all__ = [
    "ACTION_BACK_ROUTES",
    "ACTION_DESCRIPTIONS",
    "ACTION_GROUPS",
    "ActionGroupView",
    "ActionResultView",
    "ActionView",
    "CONFIRM_ACTIONS",
    "DANGER_ACTIONS",
    "action_description",
    "action_label",
    "action_result_to_mapping",
    "action_route",
    "action_value",
    "back_route_for_action",
    "browser_action_href",
    "default_action_view",
    "is_browser_action",
    "is_danger_action",
    "normalize_action_result",
    "render_action_button",
    "render_action_buttons",
    "render_action_catalog",
    "render_action_group",
    "render_action_groups",
    "render_action_result",
    "render_action_result_page",
    "render_action_summary_table",
    "render_action_view",
    "render_browser_link_result",
    "render_command_output",
    "requires_confirmation",
]