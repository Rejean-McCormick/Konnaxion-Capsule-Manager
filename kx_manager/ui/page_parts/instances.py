# kx_manager/ui/page_parts/instances.py

"""Instances page body for the Konnaxion Capsule Manager GUI."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from kx_manager.ui.page_parts.common import (
    action_bar,
    action_form,
    button_form,
    capsule_file_field,
    capsule_id_field,
    confirmed_field,
    context_target_mode,
    context_value,
    default_payload,
    droplet_payload,
    exposure_mode_field,
    field,
    instance_id_field,
    network_profile_field,
)
from kx_manager.ui.render import render_card, render_grid


def render(context: Mapping[str, Any]) -> str:
    """Render the Instances page body."""

    payload = (
        droplet_payload(context)
        if context_target_mode(context) == "droplet"
        else default_payload(context)
    )

    create_form = action_form(
        "create_instance",
        [
            instance_id_field(payload["instance_id"]),
            capsule_id_field(payload["capsule_id"]),
            network_profile_field(str(payload["network_profile"])),
            exposure_mode_field(str(payload["exposure_mode"])),
            field("host", "Host", payload["host"], required=False),
            field("domain", "Domain", context_value(context, "domain", default=""), required=False),
            field("generate_secrets", "Generate instance secrets", True, field_type="checkbox"),
        ],
    )

    update_form = action_form(
        "update_instance",
        [
            instance_id_field(payload["instance_id"]),
            capsule_file_field(value=payload["capsule_file"], required=True),
            field("create_pre_update_backup", "Create pre-update backup", True, field_type="checkbox"),
        ],
    )

    # Keep action forms as siblings. HTML does not permit nested <form>
    # elements; nesting these buttons inside the status form causes browsers
    # to submit the outer instance_status route even when Start/Stop/Restart
    # was clicked.
    runtime_hidden = {
        key: value
        for key, value in payload.items()
        if key not in {"instance_id", "run_security_gate", "timeout_seconds"}
        and value is not None
    }
    runtime_status_form = action_form(
        "instance_status",
        [
            instance_id_field(payload["instance_id"]),
            field("run_security_gate", "Run Security Gate before action", True, field_type="checkbox"),
            field("timeout_seconds", "Timeout Seconds", 60, field_type="number"),
        ],
        submit_label="Load Status",
        hidden=runtime_hidden,
    )

    runtime_actions = action_bar(
        [
            button_form(
                "start_instance",
                payload={**payload, "run_security_gate": "true"},
            ),
            button_form(
                "stop_instance",
                payload={**payload, "confirmed": "true", "timeout_seconds": "60"},
                variant="danger",
            ),
            button_form("restart_instance", payload=payload),
            button_form("view_health", payload=payload),
            button_form("view_logs", payload=payload),
            button_form("open_instance", payload=payload),
        ]
    )

    runtime_form = runtime_status_form + runtime_actions

    rollback_form = action_form(
        "rollback_instance",
        [
            instance_id_field(payload["instance_id"]),
            field("restore_data", "Restore data from backup", False, field_type="checkbox"),
            field("backup_id", "Backup ID", "", required=False),
            confirmed_field("I confirm rollback"),
        ],
        submit_label="Rollback Instance",
    )

    return render_grid(
        [
            render_card("Create Instance", create_form),
            render_card("Update Instance", update_form),
            render_card("Runtime Actions", runtime_form),
            render_card("Rollback", rollback_form, classes="kx-result warn"),
        ]
    )


__all__ = ["render"]
