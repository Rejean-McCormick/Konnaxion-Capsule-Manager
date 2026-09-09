# kx_manager/ui/page_parts/health.py

"""Health page body for the Konnaxion Capsule Manager GUI."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from kx_manager.ui.page_parts.common import (
    action_bar,
    action_form,
    button_form,
    default_payload,
    instance_id_field,
)
from kx_manager.ui.render import render_card, render_grid


def render(context: Mapping[str, Any]) -> str:
    """Render the Health page body."""

    payload = default_payload(context)

    # Keep independent actions outside the View Health form. Nested forms are
    # invalid HTML and can route clicks to the outer action unexpectedly.
    body = action_form(
        "view_health",
        [instance_id_field(payload["instance_id"])],
        submit_label="View Health",
    ) + action_bar(
        [
            button_form("instance_status", payload=payload),
            button_form("check_agent", payload=payload),
            button_form("check_manager", payload=payload),
        ]
    )

    return render_grid(
        [
            render_card("Health", body),
            render_card(
                "Health Checks",
                "<p>Use this page to inspect Manager, Agent, instance, and runtime health signals.</p>",
            ),
        ]
    )


__all__ = ["render"]
