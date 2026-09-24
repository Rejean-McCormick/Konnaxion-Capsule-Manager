from __future__ import annotations

from kx_manager.ui import app as ui_app


def _legacy_netcup() -> dict[str, object]:
    return {
        "target_mode": "droplet",
        "instance_id": "konnaxion-prod",
        "droplet_name": "netcup-vps",
        "droplet_host": "2.56.97.41",
        "droplet_user": "root",
        "ssh_user": "root",
        "ssh_key_path": r"C:\Users\rejea\.ssh\id_ed25519",
        "ssh_port": "22",
        "remote_kx_root": "/opt/konnaxion",
        "remote_capsule_dir": "/opt/konnaxion/capsules",
        "domain": "konnaxion.com",
    }


def test_legacy_netcup_root_is_migrated_globally(monkeypatch) -> None:
    monkeypatch.delenv("KX_DROPLET_USER", raising=False)
    data = ui_app._normalize_context(_legacy_netcup())
    assert data["droplet_user"] == "kx-admin"
    assert data["ssh_user"] == "kx-admin"
    assert data["user"] == "kx-admin"


def test_custom_root_target_is_not_rewritten(monkeypatch) -> None:
    monkeypatch.delenv("KX_DROPLET_USER", raising=False)
    data = ui_app._normalize_context({
        "target_mode": "droplet",
        "droplet_name": "custom-vps",
        "droplet_host": "203.0.113.20",
        "droplet_user": "root",
    })
    assert data["droplet_user"] == "root"


def test_explicit_environment_user_wins(monkeypatch) -> None:
    monkeypatch.setenv("KX_DROPLET_USER", "deploy")
    data = ui_app._normalize_context(_legacy_netcup())
    assert data["droplet_user"] == "deploy"
    assert data["ssh_user"] == "deploy"


def test_environment_ui_context_carries_droplet_identity(monkeypatch) -> None:
    monkeypatch.setenv("KX_DROPLET_USER", "deploy")
    monkeypatch.setenv("KX_DROPLET_SSH_KEY_PATH", r"C:\keys\prod")
    monkeypatch.setenv("KX_DROPLET_SSH_PORT", "2222")
    values = ui_app._environment_ui_context()
    assert values["droplet_user"] == "deploy"
    assert values["ssh_key_path"] == r"C:\keys\prod"
    assert values["ssh_port"] == "2222"


def test_view_logs_cannot_reintroduce_legacy_root(monkeypatch) -> None:
    monkeypatch.delenv("KX_DROPLET_USER", raising=False)
    payload = ui_app._validated_payload(
        "view_logs",
        {**_legacy_netcup(), "lines": "200", "tail": "true"},
    )
    assert payload["droplet_user"] == "kx-admin"
    assert payload["ssh_user"] == "kx-admin"
