from __future__ import annotations

from kx_manager.ui.droplet_bootstrap import _remote_bootstrap_command


def test_bootstrap_restarts_existing_agent_after_source_refresh() -> None:
    command = _remote_bootstrap_command(
        remote_archive="/tmp/konnaxion-manager-bootstrap.tar.gz",
        remote_kx_root="/opt/konnaxion",
        remote_manager_dir="/opt/konnaxion/manager",
        instance_id="konnaxion-prod",
    )
    assert "systemctl enable konnaxion-agent" in command
    assert "systemctl restart konnaxion-agent" in command
    assert "systemctl enable --now konnaxion-agent" not in command
    assert command.index("systemctl restart konnaxion-agent") > command.index("/usr/local/bin/uv sync")
