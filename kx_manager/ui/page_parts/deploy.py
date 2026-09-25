# kx_manager/ui/page_parts/deploy.py

"""Deployment page body for the Konnaxion Capsule Manager GUI."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from kx_manager.ui.page_parts.common import (
    action_form,
    capsule_id_field,
    capsule_output_dir_field,
    capsule_version_field,
    droplet_operation_form,
    field,
    instance_id_field,
    intranet_payload,
    local_payload,
    source_dir_field,
)
from kx_manager.ui.render import render_card, render_grid


def render(context: Mapping[str, Any]) -> str:
    """Render the Deploy page body."""

    return (
        render_card(
            "Local / Intranet Deployment",
            render_grid(
                [
                    _local_deploy_card(context),
                    _intranet_deploy_card(context),
                ]
            ),
        )
        + render_card(
            "Droplet Deployment",
            (
                "<p>Run VPS deployment operations in workflow order. "
                "These forms force <code>target_mode=droplet</code>, "
                "<code>network_profile=public_vps</code>, "
                "<code>exposure_mode=public</code>, and require explicit "
                "public Droplet confirmation.</p>"
                "<ol>"
                "<li><strong>Bootstrap Droplet Agent</strong> installs or refreshes the remote Konnaxion Agent.</li>"
                "<li><strong>Check Droplet Agent</strong> verifies the remote Agent health.</li>"
                "<li><strong>Copy Capsule to Droplet</strong> uploads the built capsule.</li>"
                "<li><strong>Deploy Droplet</strong> imports, configures, checks, and starts the instance.</li>"
                "<li><strong>Start Droplet Instance</strong> is only needed if deploy succeeds but start is still required.</li>"
                "</ol>"
                + _droplet_operation_cards(context)
            ),
            classes="kx-result warn",
        )
    )


def _local_deploy_card(context: Mapping[str, Any]) -> str:
    payload = local_payload(context)

    return render_card(
        "Deploy Local",
        (
            "<p>Build, verify, import, and start a local private instance.</p>"
            + action_form(
                "deploy_local",
                [
                    instance_id_field(payload["instance_id"]),
                    source_dir_field(payload["source_dir"]),
                    capsule_output_dir_field(payload["capsule_output_dir"]),
                    capsule_id_field(payload["capsule_id"]),
                    capsule_version_field(payload["capsule_version"]),
                    field(
                        "runtime_root",
                        "Runtime Root",
                        payload["runtime_root"],
                        required=True,
                    ),
                    field(
                        "capsule_dir",
                        "Capsule Directory",
                        payload["capsule_dir"],
                        required=True,
                    ),
                    field("build", "Build", True, field_type="checkbox"),
                    field("verify", "Verify", True, field_type="checkbox"),
                    field(
                        "import_capsule",
                        "Import Capsule",
                        True,
                        field_type="checkbox",
                    ),
                    field("start", "Start", True, field_type="checkbox"),
                ],
                hidden={
                    "target_mode": "local",
                    "network_profile": "local_only",
                    "exposure_mode": "private",
                    "public_mode_enabled": "false",
                    "public_mode_expires_at": "",
                    "confirmed": "",
                },
                classes="kx-stack",
            )
        ),
    )


def _intranet_deploy_card(context: Mapping[str, Any]) -> str:
    payload = intranet_payload(context)

    return render_card(
        "Deploy Intranet",
        (
            "<p>Build, verify, import, and start an intranet/private LAN instance.</p>"
            + action_form(
                "deploy_intranet",
                [
                    instance_id_field(payload["instance_id"]),
                    source_dir_field(payload["source_dir"]),
                    capsule_output_dir_field(payload["capsule_output_dir"]),
                    capsule_id_field(payload["capsule_id"]),
                    capsule_version_field(payload["capsule_version"]),
                    field(
                        "runtime_root",
                        "Runtime Root",
                        payload["runtime_root"],
                        required=True,
                    ),
                    field(
                        "capsule_dir",
                        "Capsule Directory",
                        payload["capsule_dir"],
                        required=True,
                    ),
                    field(
                        "host",
                        "Private Host",
                        payload["host"],
                        required=False,
                    ),
                    field(
                        "exposure_mode",
                        "Exposure Mode",
                        payload["exposure_mode"],
                        field_type="select",
                        required=True,
                        options=[
                            ("private", "Private"),
                            ("lan", "LAN"),
                        ],
                    ),
                    field("build", "Build", True, field_type="checkbox"),
                    field("verify", "Verify", True, field_type="checkbox"),
                    field(
                        "import_capsule",
                        "Import Capsule",
                        True,
                        field_type="checkbox",
                    ),
                    field("start", "Start", True, field_type="checkbox"),
                ],
                hidden={
                    "target_mode": "intranet",
                    "network_profile": "intranet_private",
                    "exposure_mode": "private",
                    "public_mode_enabled": "false",
                    "public_mode_expires_at": "",
                    "confirmed": "",
                },
                classes="kx-stack",
            )
        ),
    )


def _droplet_operation_cards(context: Mapping[str, Any]) -> str:
    """Render Droplet deployment cards in operator workflow order."""

    return render_grid(
        [
            render_card(
                "1. Bootstrap Droplet Agent",
                (
                    "<p>Install or refresh the Konnaxion Manager/Agent code on the "
                    "Droplet, create required runtime folders, install the Agent "
                    "service, start it on <code>127.0.0.1:8765</code>, and verify "
                    "health through the Droplet itself.</p>"
                    + droplet_operation_form(
                        "bootstrap_droplet_agent",
                        context,
                        include_capsule=False,
                        submit_label="Bootstrap Droplet Agent",
                        classes="kx-stack",
                    )
                ),
                classes="kx-result warn",
            ),
            render_card(
                "2. Check Droplet Agent",
                (
                    "<p>Verify that the Droplet Agent is reachable before copying "
                    "or deploying anything.</p>"
                    + droplet_operation_form(
                        "check_droplet_agent",
                        context,
                        include_capsule=False,
                        submit_label="Check Droplet Agent",
                        classes="kx-stack",
                    )
                ),
            ),
            render_card(
                "3. Copy Capsule to Droplet",
                (
                    "<p>Upload the existing local <code>.kxcap</code> file to the "
                    "remote capsule directory.</p>"
                    + droplet_operation_form(
                        "copy_capsule_to_droplet",
                        context,
                        include_capsule=True,
                        submit_label="Copy Capsule to Droplet",
                        classes="kx-stack",
                    )
                ),
                classes="kx-result warn",
            ),
            render_card(
                "4. Deploy Droplet",
                (
                    "<p>Import the remote capsule, create or update the instance, "
                    "apply the public VPS network profile, run Security Gate, and "
                    "start the instance.</p>"
                    + droplet_operation_form(
                        "deploy_droplet",
                        context,
                        include_capsule=True,
                        submit_label="Deploy Droplet",
                        classes="kx-stack",
                    )
                ),
                classes="kx-result warn",
            ),
            render_card(
                "Initialize Production Data",
                (
                    "<p>One-time production bootstrap: copy the local Konnaxion "
                    "Neon database into the selected production instance, preserve "
                    "all World schemas, run health checks, and start production. "
                    "The source database is read from the selected Source Folder; "
                    "Droplet/SSH/domain settings come from the saved target.</p>"
                    + droplet_operation_form(
                        "initialize_production_data",
                        context,
                        include_capsule=False,
                        submit_label="Initialize Production Data",
                        classes="kx-stack",
                    )
                ),
                classes="kx-result warn",
            ),
            render_card(
                "5. Start Droplet Instance",
                (
                    "<p>Use this only if deployment completed but the remote "
                    "instance still needs to be started.</p>"
                    + droplet_operation_form(
                        "start_droplet_instance",
                        context,
                        include_capsule=True,
                        submit_label="Start Droplet Instance",
                        classes="kx-stack",
                    )
                ),
                classes="kx-result warn",
            ),
        ]
    )


__all__ = ["render"]