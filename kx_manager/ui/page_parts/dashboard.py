# kx_manager/ui/page_parts/dashboard.py

"""Dashboard page body for the Konnaxion Capsule Manager GUI."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from kx_manager.ui.page_parts.common import (
    action_bar,
    button_form,
    default_payload,
    droplet_payload,
    label,
    safety_note,
)
from kx_manager.ui.render import h, render_card, render_grid, render_metric


def render(context: Mapping[str, Any]) -> str:
    """Render the Dashboard page body."""

    payload = default_payload(context)
    go_live_payload = droplet_payload(context)
    # GO LIVE always creates a fresh signed release; stale/local capsule selection
    # must never turn this into a redeploy of an older artifact.
    go_live_payload.pop("capsule_file", None)
    go_live_payload.pop("capsule_path", None)
    go_live_payload.update(
        {
            "one_click_release": "true",
            "background_job": "true",
            "copy_capsule": "true",
            "confirmed": "true",
        }
    )

    metrics = render_grid(
        [
            render_metric(
                "Target",
                label(payload["target_mode"]),
                hint="Selected deployment target",
            ),
            render_metric(
                "Profile",
                label(payload["network_profile"]),
                hint="Canonical network profile",
            ),
            render_metric(
                "Exposure",
                label(payload["exposure_mode"]),
                hint="Current exposure mode",
            ),
            render_metric(
                "Instance",
                payload["instance_id"],
                hint="Default working instance",
            ),
        ]
    )

    go_live = render_card(
        "GO LIVE",
        (
            "<p><strong>One click production release.</strong> Uses the configured Droplet target "
            "to generate/validate release signing keys, build and verify a signed capsule, refresh "
            "the private Agent trust/runtime, back up an existing instance, deploy through the "
            "Security Gate, start Konnaxion, and verify final health/HTTPS.</p>"
            f"<p>Target: <code>{h(go_live_payload['droplet_host'])}</code> · "
            f"Domain: <code>{h(go_live_payload['domain'])}</code> · "
            f"Instance: <code>{h(go_live_payload['instance_id'])}</code></p>"
        ),
        footer=action_bar(
            [
                button_form(
                    "deploy_droplet",
                    "GO LIVE — BUILD, SIGN & DEPLOY",
                    payload=go_live_payload,
                    variant="danger",
                )
            ]
        ),
        classes="kx-result warn",
    )

    quick_checks = render_card(
        "Checks",
        "<p>Confirm Manager and Agent reachability before capsule or runtime actions.</p>",
        footer=action_bar(
            [
                button_form("check_manager", "Check Manager"),
                button_form("check_agent", "Check Agent"),
                button_form("open_manager_docs", "Manager Docs"),
                button_form("open_agent_docs", "Agent Docs"),
            ]
        ),
    )

    folders = render_card(
        "Folders",
        "<p>Select the source folder and capsule output folder used by builds.</p>",
        footer=action_bar(
            [
                button_form(
                    "select_source_folder",
                    "Select Source Folder",
                    payload=payload,
                ),
                button_form(
                    "select_capsule_output_folder",
                    "Select Output Folder",
                    payload=payload,
                ),
            ]
        ),
    )

    capsules = render_card(
        "Capsules",
        "<p>Build, rebuild, verify, import, list, and inspect capsules.</p>",
        footer=action_bar(
            [
                button_form("build_capsule", payload=payload),
                button_form("rebuild_capsule", payload=payload),
                button_form("verify_capsule", payload=payload),
                button_form("import_capsule", payload=payload),
                button_form("list_capsules", payload=payload),
                button_form("view_capsule", payload=payload),
            ]
        ),
    )

    instances = render_card(
        "Instances",
        "<p>Create, update, start, stop, inspect, and open the default instance.</p>",
        footer=action_bar(
            [
                button_form("create_instance", payload=payload),
                button_form("update_instance", payload=payload),
                button_form("start_instance", payload=payload),
                button_form(
                    "stop_instance",
                    payload={**payload, "confirmed": "true"},
                    variant="danger",
                ),
                button_form("instance_status", "Instance Status", payload=payload),
                button_form("view_health", "Instance Health", payload=payload),
                button_form("view_logs", "View Logs", payload=payload),
                button_form("open_instance", "Open Instance", payload=payload),
            ]
        ),
    )

    safety = render_card(
        "Safety and Network",
        "<p>Run Security Gate checks and manage the default network profile.</p>",
        footer=action_bar(
            [
                button_form("run_security_check", "Security Check", payload=payload),
                button_form("set_network_profile", payload=payload),
                button_form(
                    "disable_public_mode",
                    payload={**payload, "confirmed": "true"},
                    variant="danger",
                ),
            ]
        ),
    )

    return metrics + go_live + render_grid(
        [
            quick_checks,
            folders,
            capsules,
            instances,
            safety,
            safety_note(),
        ]
    )


__all__ = ["render"]