# kx_manager/ui/page_parts/backups.py

"""Backups page body for the Konnaxion Capsule Manager GUI."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from kx_manager.defaults import DEFAULT_NETWORK_PROFILE
from kx_manager.ui.form_constants import DEFAULT_INSTANCE_ID
from kx_manager.ui.page_parts.common import (
    action_form,
    confirmed_field,
    context_value,
    field,
    instance_id_field,
)
from kx_manager.ui.render import render_card, render_grid


def render(context: Mapping[str, Any]) -> str:
    """Render the Backups page body."""

    instance_id = context_value(
        context,
        "instance_id",
        default=DEFAULT_INSTANCE_ID,
    )

    create_form = action_form(
        "create_backup",
        [
            instance_id_field(instance_id),
            field("backup_class", "Backup Class", "manual", required=True),
            field(
                "verify_after_create",
                "Verify after create",
                True,
                field_type="checkbox",
            ),
        ],
    )

    list_form = action_form(
        "list_backups",
        [
            field(
                "instance_id",
                "Instance ID",
                instance_id,
                required=False,
            ),
            field(
                "backup_class",
                "Backup Class",
                "",
                required=False,
                help_text="Optional backup class filter.",
            ),
            field(
                "status",
                "Status",
                "",
                required=False,
                help_text="Optional backup status filter.",
            ),
            field(
                "limit",
                "Limit",
                50,
                field_type="number",
                required=False,
            ),
        ],
    )

    verify_form = action_form(
        "verify_backup",
        [
            field("backup_id", "Backup ID", "", required=True),
            field(
                "instance_id",
                "Instance ID",
                instance_id,
                required=False,
            ),
        ],
    )

    restore_form = action_form(
        "restore_backup",
        [
            instance_id_field(instance_id),
            field("backup_id", "Backup ID", "", required=True),
            field(
                "create_pre_restore_backup",
                "Create pre-restore backup",
                True,
                field_type="checkbox",
            ),
            field("restore_data", "Restore data", True, field_type="checkbox"),
            confirmed_field("I confirm restore"),
        ],
        submit_label="Restore Backup",
    )

    restore_new_form = action_form(
        "restore_backup_new",
        [
            field("source_backup_id", "Source Backup ID", "", required=True),
            field(
                "new_instance_id",
                "New Instance ID",
                "demo-restore-001",
                required=True,
            ),
            field(
                "network_profile",
                "Network Profile",
                context_value(
                    context,
                    "network_profile",
                    default=DEFAULT_NETWORK_PROFILE,
                ),
                required=True,
            ),
            field("restore_data", "Restore data", True, field_type="checkbox"),
            confirmed_field("I confirm restore into a new instance"),
        ],
        submit_label="Restore Backup New",
    )

    test_restore_form = action_form(
        "test_restore_backup",
        [
            field("backup_id", "Backup ID", "", required=True),
            field(
                "new_instance_id",
                "Test Instance ID",
                "demo-test-restore-001",
                required=True,
            ),
            field(
                "network_profile",
                "Network Profile",
                context_value(
                    context,
                    "network_profile",
                    default=DEFAULT_NETWORK_PROFILE,
                ),
                required=True,
            ),
            field("test_only", "Test only", True, field_type="checkbox"),
        ],
        submit_label="Test Restore Backup",
    )

    return render_grid(
        [
            render_card("Create Backup", create_form),
            render_card("List Backups", list_form),
            render_card("Verify Backup", verify_form),
            render_card("Restore Backup", restore_form, classes="kx-result warn"),
            render_card(
                "Restore Backup New",
                restore_new_form,
                classes="kx-result warn",
            ),
            render_card("Test Restore Backup", test_restore_form),
        ]
    )


__all__ = ["render"]