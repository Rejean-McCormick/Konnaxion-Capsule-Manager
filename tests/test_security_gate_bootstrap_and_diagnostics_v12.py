from __future__ import annotations

from pathlib import Path

from kx_manager.services.deploy import _security_gate_failure_message
from kx_manager.ui.droplet_bootstrap import _remote_bootstrap_command


def test_bootstrap_installs_trusted_public_key_and_exports_path() -> None:
    command = _remote_bootstrap_command(
        remote_archive="/tmp/konnaxion-manager-bootstrap.tar.gz",
        remote_kx_root="/opt/konnaxion",
        remote_manager_dir="/opt/konnaxion/manager",
        instance_id="konnaxion-prod",
    )
    assert "/opt/konnaxion/agent/keys" in command
    assert "BOOTSTRAP_PUBLIC_KEY=/tmp/konnaxion-capsule-signing-public.pem" in command
    assert "install -m 0644" in command
    assert "/opt/konnaxion/agent/keys/capsule-signing-public.pem" in command
    assert "Environment=KX_CAPSULE_PUBLIC_KEY_FILE=/opt/konnaxion/agent/keys/capsule-signing-public.pem" in command
    assert "private.pem" not in command


def test_security_gate_failure_message_lists_blocking_checks() -> None:
    data = {
        "message": "Security Gate blocked the operation.",
        "data": {
            "report": {
                "results": [
                    {"check": "capsule_signature", "status": "FAIL_BLOCKING", "blocking": True},
                    {"check": "firewall_enabled", "status": "WARN", "blocking": False},
                    {"check": "image_checksums", "status": "PASS", "blocking": True},
                ]
            }
        },
    }
    message = _security_gate_failure_message(data, "Security Gate blocked the operation.")
    assert "capsule_signature" in message
    assert "firewall_enabled" not in message
    assert "image_checksums" not in message
    assert "Run Security Check" in message


def test_security_gate_failure_message_reads_explicit_blocking_failures() -> None:
    data = {"data": {"blocking_failures": ["capsule_signature", "allowed_images_only"]}}
    message = _security_gate_failure_message(data, "blocked")
    assert "capsule_signature" in message
    assert "allowed_images_only" in message
