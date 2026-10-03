from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from kx_manager.services import netcup
from kx_manager.ui.form_errors import FormValidationError
from kx_manager.ui.form_netcup import ProvisionNetcupVpsForm
from kx_manager.ui.page_parts.dashboard import render as render_dashboard
from kx_manager.ui.page_parts.deploy import render as render_deploy


def _completed(code: int = 0, stdout: bytes = b"", stderr: bytes = b""):
    return SimpleNamespace(returncode=code, stdout=stdout, stderr=stderr)


def test_deploy_page_exposes_secure_netcup_rebuild_workflow() -> None:
    html = render_deploy({})
    assert "Netcup Clean Rebuild + kx-admin" in html
    assert "Scan Fresh VPS Host Key" in html
    assert "Provision Fresh Netcup VPS" in html
    assert "ssh_host_fingerprint" in html
    assert "reinstalled_confirmed" in html
    assert "https://www.servercontrolpanel.de/scp-ui/" in html
    assert "KX_NETCUP_KXADMIN_PASSWORD" in html


def test_provision_form_requires_reinstall_confirmation_and_verified_fingerprint(tmp_path: Path) -> None:
    key = tmp_path / "id_ed25519"
    key.write_text("test", encoding="utf-8")
    base = {
        "droplet_host": "203.0.113.10",
        "ssh_key_path": str(key),
        "ssh_port": "22",
        "domain": "konnaxion.example",
        "admin_user": "kx-admin",
        "ssh_host_fingerprint": "SHA256:AbCdEf1234567890+/=",
        "confirmed": "true",
    }
    with pytest.raises(FormValidationError):
        ProvisionNetcupVpsForm.from_mapping(base)

    form = ProvisionNetcupVpsForm.from_mapping({**base, "reinstalled_confirmed": "true"})
    assert form.admin_user == "kx-admin"
    assert form.reinstalled_confirmed is True
    assert form.confirmed is True


def test_dashboard_blocks_netcup_go_live_until_matching_preflight(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "KX_NETCUP_VPS_PREPARED",
        "KX_NETCUP_VPS_PREPARED_HOST",
        "KX_NETCUP_VPS_PREPARED_USER",
        "KX_NETCUP_SSH_HOST_FINGERPRINT",
    ):
        monkeypatch.delenv(key, raising=False)

    html = render_dashboard({})
    assert "Netcup preflight:" in html
    assert "NOT READY" in html
    assert "GO LIVE — BUILD, SIGN &amp; DEPLOY</button>" in html
    assert "disabled" in html

    monkeypatch.setenv("KX_NETCUP_VPS_PREPARED", "true")
    monkeypatch.setenv("KX_NETCUP_VPS_PREPARED_HOST", "2.56.97.41")
    monkeypatch.setenv("KX_NETCUP_VPS_PREPARED_USER", "kx-admin")
    monkeypatch.setenv("KX_NETCUP_SSH_HOST_FINGERPRINT", "SHA256:AbCdEf1234567890+/=")
    html = render_dashboard({})
    assert "Netcup preflight:" in html
    assert "READY" in html
    # The GO LIVE button itself is enabled; other page markup may legitimately contain disabled controls.
    marker = "GO LIVE — BUILD, SIGN &amp; DEPLOY</button>"
    before = html.split(marker, 1)[0][-300:]
    assert " disabled" not in before


def test_provision_persists_password_only_to_env_and_never_returns_it(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    key = tmp_path / "id_ed25519"
    key.write_text("private", encoding="utf-8")
    known = tmp_path / "known_hosts"
    known.write_text("", encoding="utf-8")
    password = "A-very-strong-Test-Password-123!"
    stored: dict[str, str] = {}
    scripts: list[str] = []
    commands: list[tuple[str, str]] = []

    monkeypatch.setattr(netcup, "_require_executable", lambda name: None)
    monkeypatch.setattr(netcup, "_ssh_scan", lambda host, port: f"{host} ssh-ed25519 AAAATEST\n")
    monkeypatch.setattr(netcup, "_trust_verified_host", lambda host, port, scan, expected: known)
    monkeypatch.setattr(netcup, "_public_key_for_private_key", lambda path: "ssh-ed25519 AAAAPUBLIC manager@test")
    monkeypatch.setattr(netcup, "_new_admin_password", lambda length=32: password)

    def fake_script(**kwargs):
        scripts.append(kwargs["script"])
        return _completed(0, b"ok\n", b"")

    def fake_command(**kwargs):
        commands.append((kwargs["user"], kwargs["command"]))
        return _completed(0, b"ok\n", b"")

    def fake_persist(values):
        stored.update({str(k): str(v) for k, v in values.items()})
        return tmp_path / ".env"

    monkeypatch.setattr(netcup, "_ssh_script", fake_script)
    monkeypatch.setattr(netcup, "_ssh_command", fake_command)
    monkeypatch.setattr(netcup, "_persist_operator_env", fake_persist)

    result = netcup.provision_fresh_vps(
        {
            "droplet_name": "netcup-vps",
            "droplet_host": "203.0.113.10",
            "ssh_key_path": str(key),
            "ssh_port": 22,
            "admin_user": "kx-admin",
            "ssh_host_fingerprint": "SHA256:AbCdEf1234567890+/=",
            "domain": "konnaxion.example",
            "instance_id": "konnaxion-prod",
            "remote_kx_root": "/opt/konnaxion",
            "remote_capsule_dir": "/opt/konnaxion/capsules",
            "reinstalled_confirmed": True,
            "confirmed": True,
        }
    )

    assert result["ok"] is True
    assert stored["KX_NETCUP_KXADMIN_PASSWORD"] == password
    assert stored["KX_DROPLET_USER"] == "kx-admin"
    assert stored["KX_NETCUP_VPS_PREPARED"] == "true"
    assert stored["KX_NETCUP_VPS_PREPARED_HOST"] == "203.0.113.10"
    assert result["data"]["password_env_key"] == "KX_NETCUP_KXADMIN_PASSWORD"
    assert result["data"]["password_value_exposed"] is False
    assert password not in repr(result)
    assert commands[0][0] == "root"
    assert commands[1][0] == "kx-admin"
    assert commands[-1][0] == "kx-admin"
    assert len(scripts) == 2
    assert "PermitRootLogin no" in scripts[1]
    assert "PasswordAuthentication no" in scripts[1]
    # Password is transported in the SSH script as base64/stdin, never as the ssh command argument.
    assert password not in " ".join(command for _user, command in commands)


def test_operator_env_writer_updates_file_and_current_process(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("EXISTING=keep\n", encoding="utf-8")
    monkeypatch.setenv("KX_MANAGER_ENV_FILE", str(env_file))
    monkeypatch.delenv("KX_NETCUP_VPS_PREPARED", raising=False)

    path = netcup._persist_operator_env(
        {
            "KX_NETCUP_VPS_PREPARED": "true",
            "KX_NETCUP_KXADMIN_PASSWORD": "secret-test-value",
        }
    )

    text = path.read_text(encoding="utf-8")
    assert "EXISTING=keep" in text
    assert "KX_NETCUP_VPS_PREPARED='true'" in text or 'KX_NETCUP_VPS_PREPARED="true"' in text
    assert "secret-test-value" in text
    assert os.environ["KX_NETCUP_VPS_PREPARED"] == "true"


def test_one_click_service_blocks_netcup_without_matching_preflight(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from kx_manager.services import operation_jobs, release

    monkeypatch.setattr(operation_jobs, "append_job_log", lambda *args, **kwargs: None)
    monkeypatch.setattr(operation_jobs, "_update_job", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        netcup,
        "netcup_go_live_ready",
        lambda *, host, user: (False, "Fresh Netcup VPS provisioning has not been validated."),
    )

    def should_not_build(_payload):
        raise AssertionError("release build must not start before Netcup preflight")

    monkeypatch.setattr(release, "prepare_signed_release", should_not_build)

    result = operation_jobs._run_one_click_release(
        "job-netcup-blocked",
        {
            "instance_id": "konnaxion-prod",
            "source_dir": str(tmp_path),
            "capsule_output_dir": str(tmp_path),
            "droplet_name": "netcup-vps",
            "droplet_host": "203.0.113.10",
            "droplet_user": "kx-admin",
            "ssh_key_path": str(tmp_path / "id_ed25519"),
            "domain": "konnaxion.example",
        },
        lambda *args, **kwargs: None,
    )

    assert result["ok"] is False
    assert result["phase"] == "netcup_preflight"
    assert result["data"]["netcup_preflight_ready"] is False


def test_env_template_contains_blank_netcup_secret_and_preflight_markers() -> None:
    from kx_manager.config import env_template

    values = env_template()
    assert values["KX_NETCUP_KXADMIN_PASSWORD"] == ""
    assert values["KX_NETCUP_VPS_PREPARED"] == "false"
    assert values["KX_NETCUP_VPS_PREPARED_HOST"] == ""
    assert values["KX_NETCUP_VPS_PREPARED_USER"] == ""
    assert values["KX_NETCUP_SSH_HOST_FINGERPRINT"] == ""


def test_trust_verified_host_persists_only_matching_scanned_key(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    known = tmp_path / "known_hosts"
    known.write_text("old-entry\n", encoding="utf-8")
    monkeypatch.setattr(netcup, "_known_hosts_file", lambda: known)
    monkeypatch.setattr(netcup, "_run", lambda *args, **kwargs: _completed())

    def fake_fingerprints(text: str):
        if "GOODKEY" in text:
            return [{"bits": "256", "fingerprint": "SHA256:GOOD", "key_type": "ED25519"}]
        return [{"bits": "256", "fingerprint": "SHA256:OTHER", "key_type": "ED25519"}]

    monkeypatch.setattr(netcup, "_fingerprints_from_keyscan", fake_fingerprints)
    netcup._trust_verified_host(
        "203.0.113.10",
        22,
        "203.0.113.10 ssh-ed25519 GOODKEY\n203.0.113.10 ssh-rsa OTHERKEY\n",
        "SHA256:GOOD",
    )

    text = known.read_text(encoding="utf-8")
    assert "GOODKEY" in text
    assert "OTHERKEY" not in text
