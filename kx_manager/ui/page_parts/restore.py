# kx_manager/ui/page_parts/restore.py

"""Restore page body for the Konnaxion Capsule Manager GUI."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from kx_manager.ui.form_constants import DEFAULT_INSTANCE_ID
from kx_manager.ui.page_parts.common import (
    action_form,
    confirmed_field,
    context_target_mode,
    context_value,
    default_payload,
    droplet_payload,
    field,
    instance_id_field,
)
from kx_manager.ui.render import render_card, render_grid


def render(context: Mapping[str, Any]) -> str:
    """Render the Restore page body."""

    instance_id = context_value(context, "instance_id", default=DEFAULT_INSTANCE_ID)
    route_payload = (
        droplet_payload(context)
        if context_target_mode(context) == "droplet"
        else default_payload(context)
    )
    route_hidden = {
        key: value
        for key, value in route_payload.items()
        if key not in {"instance_id", "network_profile", "confirmed"}
        and value is not None
    } if context_target_mode(context) == "droplet" else {}

    restore_form = action_form(
        "restore_backup",
        [
            instance_id_field(instance_id),
            field("backup_id", "Backup ID", "", required=True),
            field("restore_data", "Restore data", True, field_type="checkbox"),
            field("test_only", "Test only", False, field_type="checkbox"),
            confirmed_field("I confirm restore"),
        ],
        hidden=route_hidden,
    )

    restore_new_form = action_form(
        "restore_backup_new",
        [
            instance_id_field(instance_id),
            field("backup_id", "Backup ID", "", required=True),
            field("target_instance_id", "New Instance ID", "demo-restore-001", required=True),
            field("restore_data", "Restore data", True, field_type="checkbox"),
            confirmed_field("I confirm restore into a new instance"),
        ],
        hidden=route_hidden,
    )

    test_restore_form = action_form(
        "test_restore_backup",
        [
            instance_id_field(instance_id),
            field("backup_id", "Backup ID", "", required=True),
            field(
                "target_instance_id",
                "Test Instance ID",
                "konnaxion-restore-test",
                required=True,
            ),
            field("test_only", "Test only", True, field_type="checkbox"),
        ],
        submit_label="Test Restore",
        hidden=route_hidden,
    )

    return render_grid(
        [
            render_card("Restore Backup", restore_form, classes="kx-result warn"),
            render_card("Restore Into New Instance", restore_new_form, classes="kx-result warn"),
            render_card("Test Restore Backup", test_restore_form),
        ]
    )


__all__ = ["render"]
