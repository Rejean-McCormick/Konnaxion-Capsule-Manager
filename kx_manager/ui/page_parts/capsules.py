# kx_manager/ui/page_parts/capsules.py

"""Capsules page body for the Konnaxion Capsule Manager GUI."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from kx_manager.defaults import DEFAULT_NETWORK_PROFILE, latest_existing_capsule
from kx_manager.ui.form_constants import (
    DEFAULT_CAPSULE_ID,
    DEFAULT_CAPSULE_OUTPUT_DIR,
    DEFAULT_CAPSULE_VERSION,
    DEFAULT_CHANNEL,
    DEFAULT_INSTANCE_ID,
    DEFAULT_SOURCE_DIR,
)
from kx_manager.ui.page_parts.common import (
    DEFAULT_CAPSULE_FILE,
    action_bar,
    action_form,
    button_form,
    capsule_file_field,
    capsule_id_field,
    capsule_output_dir_field,
    capsule_version_field,
    context_value,
    field,
    instance_id_field,
    network_profile_field,
    source_dir_field,
)
from kx_manager.ui.render import render_card, render_grid


DEFAULT_SIGNING_KEY_FILE = (
    r"C:\mycode\Konnaxion\runtime\signing\kx-demo-ed25519-private.pem"
)
DEFAULT_PUBLIC_KEY_FILE = (
    r"C:\mycode\Konnaxion\runtime\signing\kx-demo-ed25519-public.pem"
)


def render(context: Mapping[str, Any]) -> str:
    """Render the Capsules page body."""

    build_profile = str(
        context_value(
            context,
            "network_profile",
            "profile",
            default=DEFAULT_NETWORK_PROFILE,
        )
    )
    capsule_file = context_value(
        context,
        "capsule_file",
        "capsule_path",
        default=DEFAULT_CAPSULE_FILE,
    )
    try:
        selected_path_exists = Path(str(capsule_file)).expanduser().is_file()
    except (OSError, ValueError):
        selected_path_exists = False
    source_dir = context_value(context, "source_dir", default=DEFAULT_SOURCE_DIR)
    output_dir = context_value(
        context,
        "capsule_output_dir",
        "output_dir",
        default=DEFAULT_CAPSULE_OUTPUT_DIR,
    )
    if not selected_path_exists:
        latest = latest_existing_capsule(output_dir)
        if latest is not None:
            capsule_file = str(latest)
    capsule_id = context_value(context, "capsule_id", default=DEFAULT_CAPSULE_ID)
    capsule_version = context_value(
        context,
        "capsule_version",
        "version",
        default=DEFAULT_CAPSULE_VERSION,
    )
    instance_id = context_value(context, "instance_id", default=DEFAULT_INSTANCE_ID)

    signing_key_file = context_value(
        context,
        "signing_key_file",
        "KX_BUILDER_SIGNING_KEY_FILE",
        default=DEFAULT_SIGNING_KEY_FILE,
    )
    public_key_file = context_value(
        context,
        "public_key_file",
        "KX_BUILDER_PUBLIC_KEY_FILE",
        default=DEFAULT_PUBLIC_KEY_FILE,
    )

    build_form = action_form(
        "build_capsule",
        [
            source_dir_field(source_dir),
            capsule_output_dir_field(output_dir),
            capsule_id_field(DEFAULT_CAPSULE_ID),
            capsule_version_field(DEFAULT_CAPSULE_VERSION),
            field("channel", "Channel", DEFAULT_CHANNEL, required=True),
            network_profile_field(build_profile, name="network_profile"),
            field(
                "signing_key_file",
                "Signing Private Key File",
                signing_key_file,
                required=True,
                help_text="Required for signed demo/release capsules.",
            ),
            field(
                "public_key_file",
                "Signing Public Key File",
                public_key_file,
                required=False,
                help_text="Used for post-build signature verification.",
            ),
            field(
                "force",
                "Overwrite existing capsule if needed",
                True,
                field_type="checkbox",
            ),
            field(
                "delete_existing",
                "Delete existing capsule first",
                False,
                field_type="checkbox",
            ),
            field(
                "verify_after_build",
                "Verify after build",
                True,
                field_type="checkbox",
            ),
        ],
    )

    rebuild_form = action_form(
        "rebuild_capsule",
        [
            source_dir_field(source_dir),
            capsule_output_dir_field(output_dir),
            capsule_id_field(capsule_id),
            capsule_version_field(capsule_version),
            field("channel", "Channel", DEFAULT_CHANNEL, required=True),
            network_profile_field(build_profile, name="network_profile"),
            field(
                "signing_key_file",
                "Signing Private Key File",
                signing_key_file,
                required=True,
                help_text="Required for signed demo/release capsules.",
            ),
            field(
                "public_key_file",
                "Signing Public Key File",
                public_key_file,
                required=False,
                help_text="Used for post-build signature verification.",
            ),
            field(
                "force",
                "Overwrite existing capsule",
                True,
                field_type="checkbox",
            ),
            field(
                "delete_existing",
                "Delete existing capsule first",
                True,
                field_type="checkbox",
            ),
            field(
                "verify_after_build",
                "Verify after rebuild",
                True,
                field_type="checkbox",
            ),
        ],
    )

    verify_form = action_form(
        "verify_capsule",
        [
            capsule_file_field(
                value=capsule_file,
                required=True,
            ),
            field(
                "public_key_file",
                "Signing Public Key File",
                public_key_file,
                required=False,
                help_text="Used for cryptographic signature verification.",
            ),
        ],
    )

    import_form = action_form(
        "import_capsule",
        [
            capsule_file_field(
                value=capsule_file,
                required=True,
            ),
            instance_id_field(instance_id),
            network_profile_field(
                str(context_value(context, "network_profile", default=DEFAULT_NETWORK_PROFILE))
            ),
        ],
    )

    lookup_form = action_form(
        "view_capsule",
        [
            capsule_id_field(capsule_id),
            capsule_file_field(
                value=capsule_file,
                required=False,
                must_exist_hint=False,
            ),
        ],
    )

    list_card = render_card(
        "Capsule Registry",
        "<p>List imported or known capsules from the Manager registry.</p>",
        footer=action_bar([button_form("list_capsules", "List Capsules")]),
    )

    return render_grid(
        [
            render_card("Build Capsule", build_form),
            render_card("Rebuild Capsule", rebuild_form, classes="kx-result warn"),
            render_card("Verify Capsule", verify_form),
            render_card("Import Capsule", import_form),
            render_card("View Capsule", lookup_form),
            list_card,
        ]
    )


__all__ = ["render"]