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
    droplet_payload,
    field,
    instance_id_field,
    intranet_payload,
    local_payload,
    source_dir_field,
)
from kx_manager.ui.render import render_card, render_grid, render_link


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
                "<li><strong>Netcup Clean Rebuild</strong> formats/reinstalls in Netcup SCP, verifies the new SSH host key, creates <code>kx-admin</code>, and prepares the local <code>.env</code>.</li>"
                "<li><strong>Bootstrap Droplet Agent</strong> installs or refreshes the remote Konnaxion Agent.</li>"
                "<li><strong>Check Droplet Agent</strong> verifies the remote Agent health.</li>"
                "<li><strong>Copy Capsule to Droplet</strong> uploads the built capsule.</li>"
                "<li><strong>Deploy Droplet</strong> imports, configures, checks, and starts the instance.</li>"
                "<li><strong>Start Droplet Instance</strong> is only needed if deploy succeeds but start is still required.</li>"
                "</ol>"
                + _netcup_fresh_vps_card(context)
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


def _netcup_fresh_vps_card(context: Mapping[str, Any]) -> str:
    payload = droplet_payload(context)
    host = payload["droplet_host"]
    port = payload["ssh_port"]

    scan_form = action_form(
        "scan_netcup_host_key",
        [
            field("droplet_host", "Droplet Host / IP", host, required=True),
            field("ssh_port", "SSH Port", port, field_type="number", required=True),
        ],
        submit_label="Scan Fresh VPS Host Key",
        classes="kx-stack",
    )

    provision_form = action_form(
        "provision_netcup_vps",
        [
            field("droplet_host", "Droplet Host / IP", host, required=True),
            field("ssh_key_path", "SSH Private Key", payload["ssh_key_path"], required=True, help_text="Select the same public key during the Netcup image installation."),
            field("ssh_port", "SSH Port", port, field_type="number", required=True),
            field("domain", "Production Domain", payload["domain"], required=True),
            field("admin_user", "Konnaxion Admin User", "kx-admin", required=True, help_text="Created on the fresh VPS with key-only SSH and passwordless sudo for Capsule Manager automation."),
            field("ssh_host_fingerprint", "Verified SSH Host Fingerprint", "", required=True, placeholder="SHA256:...", help_text="Compare Scan Fresh VPS Host Key with: ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub -E sha256 in the Netcup console."),
            field("reinstalled_confirmed", "Netcup disk was formatted and Debian Minimal was freshly reinstalled", False, field_type="checkbox"),
            field("confirmed", "I confirm provisioning will create kx-admin and disable root/password SSH login", False, field_type="checkbox"),
        ],
        hidden={
            "droplet_name": payload["droplet_name"],
            "instance_id": payload["instance_id"],
            "remote_kx_root": payload["remote_kx_root"],
            "remote_capsule_dir": payload["remote_capsule_dir"],
        },
        submit_label="Provision Fresh Netcup VPS",
        classes="kx-stack",
    )

    links = (
        '<div class="kx-actions">'
        + render_link("1. Netcup SCP — Format / Reinstall", "https://www.servercontrolpanel.de/scp-ui/", button=True, external=True)
        + render_link("Netcup Format / Image Instructions", "https://www.netcup.com/en/helpcenter/documentation/server/media", button=True, external=True)
        + "</div>"
    )

    return render_card(
        "0. Netcup Clean Rebuild + kx-admin",
        (
            "<p><strong>Post-compromise clean rebuild.</strong> The real disk format and OS image install happen in Netcup SCP, not over SSH. Stop the VPS, format/reinstall Debian Minimal, and select your trusted SSH public key during image installation.</p>"
            "<p>After reinstall, first scan the new SSH host key. Verify its <code>SHA256:</code> fingerprint from the Netcup console before provisioning. Capsule Manager will refuse to trust a different host key.</p>"
            "<p><strong>Provision</strong> first tries root with your SSH key. If Netcup did not inject it, the Manager can use <code>KX_NETCUP_ROOT_PASSWORD</code> from the local <code>.env</code> to install that key automatically. It then creates <code>kx-admin</code>, stores its generated password as <code>KX_NETCUP_KXADMIN_PASSWORD</code>, enables non-interactive sudo, disables root/password SSH, and marks GO LIVE ready.</p>"
            + links
            + render_card("A. Verify fresh host key", scan_form)
            + render_card("B. Provision fresh VPS", provision_form, classes="kx-result warn")
        ),
        classes="kx-result warn",
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
                "Publish Packaged Universes",
                (
                    "<p>Apply and promote every Universe Pack already shipped in "
                    "the deployed Konnaxion image. GO LIVE performs this "
                    "automatically; use this button to repair a deployment made "
                    "before that step existed.</p>"
                    + droplet_operation_form(
                        "publish_packaged_universes",
                        context,
                        include_capsule=False,
                        submit_label="Publish Packaged Universes",
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