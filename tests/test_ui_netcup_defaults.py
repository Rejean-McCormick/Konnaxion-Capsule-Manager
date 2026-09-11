from __future__ import annotations

from kx_manager.defaults import (
    DEFAULT_DROPLET_DOMAIN,
    DEFAULT_DROPLET_HOST,
    DEFAULT_DROPLET_INSTANCE_ID,
    DEFAULT_DROPLET_NAME,
    DEFAULT_DROPLET_USER,
    DEFAULT_REMOTE_AGENT_URL,
    DEFAULT_REMOTE_CAPSULE_DIR,
    DEFAULT_REMOTE_KX_ROOT,
    DEFAULT_SSH_PORT,
)
from kx_manager.ui.page_parts.common import droplet_payload
from kx_manager.ui.page_parts.deploy import render as render_deploy
from kx_manager.ui.page_parts.targets import render as render_targets


def test_netcup_droplet_payload_defaults() -> None:
    payload = droplet_payload({})

    assert payload["instance_id"] == "konnaxion-prod"
    assert payload["droplet_name"] == "netcup-vps"
    assert payload["droplet_host"] == "2.56.97.41"
    assert payload["droplet_user"] == "root"
    assert payload["ssh_port"] == 22
    assert payload["remote_kx_root"] == "/opt/konnaxion"
    assert payload["remote_capsule_dir"] == "/opt/konnaxion/capsules"
    assert payload["domain"] == "konnaxion.com"
    assert payload["remote_agent_url"] == ""


def test_netcup_constants_match_operator_target() -> None:
    assert DEFAULT_DROPLET_INSTANCE_ID == "konnaxion-prod"
    assert DEFAULT_DROPLET_NAME == "netcup-vps"
    assert DEFAULT_DROPLET_HOST == "2.56.97.41"
    assert DEFAULT_DROPLET_USER == "root"
    assert DEFAULT_SSH_PORT == 22
    assert DEFAULT_REMOTE_KX_ROOT == "/opt/konnaxion"
    assert DEFAULT_REMOTE_CAPSULE_DIR == "/opt/konnaxion/capsules"
    assert DEFAULT_DROPLET_DOMAIN == "konnaxion.com"
    assert DEFAULT_REMOTE_AGENT_URL == ""


def test_target_and_deploy_pages_render_netcup_defaults() -> None:
    for html in (render_targets({}), render_deploy({})):
        assert 'value="konnaxion-prod"' in html
        assert 'value="netcup-vps"' in html
        assert 'value="2.56.97.41"' in html
        assert 'value="root"' in html
        assert 'value="/opt/konnaxion"' in html
        assert 'value="/opt/konnaxion/capsules"' in html
        assert 'value="konnaxion.com"' in html
