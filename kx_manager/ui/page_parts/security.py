# kx_manager/ui/page_parts/security.py

"""Security page body for the Konnaxion Capsule Manager GUI."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from kx_manager.ui.form_constants import DEFAULT_INSTANCE_ID
from kx_manager.ui.page_parts.common import (
    action_form,
    context_target_mode,
    default_payload,
    droplet_payload,
    field,
    instance_id_field,
)
from kx_manager.ui.render import render_card, render_grid


def render(context: Mapping[str, Any]) -> str:
    """Render the Security page body."""

    payload = (
        droplet_payload(context)
        if context_target_mode(context) == "droplet"
        else default_payload(context)
    )
    security_hidden = {
        key: value
        for key, value in payload.items()
        if key != "instance_id" and value is not None
    }

    form = action_form(
        "run_security_check",
        [
            instance_id_field(payload.get("instance_id") or DEFAULT_INSTANCE_ID),
            field("run_security_gate", "Blocking Security Gate", True, field_type="checkbox"),
        ],
        submit_label="Run Security Gate",
        hidden=security_hidden,
    )

    return render_grid(
        [
            render_card("Security Gate", form),
            render_card(
                "Policy",
                (
                    "<p>Security Gate should run before starts, updates, public exposure, "
                    "restores, and deployment flows.</p>"
                ),
            ),
        ]
    )


__all__ = ["render"]
