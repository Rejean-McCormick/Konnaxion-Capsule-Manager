from __future__ import annotations

from pathlib import Path
from typing import Any

from kx_manager.ui import agent_execution_client as aec
from kx_manager.ui.droplet_bootstrap import _remote_bootstrap_command


def _payload() -> dict[str, Any]:
    return {
        "droplet_host": "2.56.97.41",
        "droplet_user": "root",
        "ssh_key_path": "C:/Users/test/.ssh/id_ed25519",
        "ssh_port": 22,
        "remote_kx_root": "/opt/konnaxion",
        "remote_capsule_dir": "/opt/konnaxion/capsules",
    }


def test_bootstrap_migrates_legacy_root_only_capsules_for_agent() -> None:
    command = _remote_bootstrap_command(
        remote_archive="/tmp/konnaxion-manager-bootstrap.tar.gz",
        remote_kx_root="/opt/konnaxion",
        remote_manager_dir="/opt/konnaxion/manager",
        instance_id="konnaxion-prod",
    )

    assert 'if [ -d "$KX_ROOT/capsules" ]; then' in command
    assert 'chown "$KX_AGENT_USER:$KX_AGENT_GROUP" "$KX_ROOT/capsules"' in command
    assert "-name '*.kxcap'" in command
    assert '-exec chown root:"$KX_AGENT_GROUP" {} +' in command
    assert '-exec chmod 0640 {} +' in command


def test_prepare_remote_capsule_access_is_scoped_and_agent_readable(monkeypatch) -> None:
    seen: dict[str, str] = {}

    def fake_ssh(
        self: Any,
        payload: Any,
        remote_command: str,
        *,
        timeout_seconds: int,
        success_message: str,
    ) -> dict[str, Any]:
        del self, payload, timeout_seconds
        seen["command"] = remote_command
        return {"ok": True, "message": success_message, "returncode": 0}

    monkeypatch.setattr(aec._AgentHttpExecutionClient, "_ssh", fake_ssh)
    client = aec._AgentHttpExecutionClient(
        base_url="http://127.0.0.1:8765/v1",
        droplet_payload=_payload(),
    )

    result = client._prepare_remote_capsule_access(
        _payload(),
        "/opt/konnaxion/capsules/demo.kxcap",
    )

    assert result["ok"] is True
    command = seen["command"]
    assert 'chown kx-agent:kx-agent "$CAPSULE_DIR"' in command
    assert 'chown root:kx-agent "$CAPSULE"' in command
    assert 'chmod 0640 "$CAPSULE"' in command
    assert 'runuser -u kx-agent -- test -r "$CAPSULE"' in command


def test_prepare_remote_capsule_access_rejects_escape_before_ssh(monkeypatch) -> None:
    called = False

    def fake_ssh(*args: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal called
        called = True
        return {"ok": True}

    monkeypatch.setattr(aec._AgentHttpExecutionClient, "_ssh", fake_ssh)
    client = aec._AgentHttpExecutionClient(
        base_url="http://127.0.0.1:8765/v1",
        droplet_payload=_payload(),
    )

    result = client._prepare_remote_capsule_access(
        _payload(),
        "/opt/konnaxion/capsules/../manager/agent.token",
    )

    assert result["ok"] is False
    assert called is False
    assert "Refusing" in result["message"]


def test_remote_import_prepares_existing_capsule_before_agent_post(monkeypatch) -> None:
    order: list[str] = []

    def fake_prepare(
        self: Any,
        payload: Any,
        remote_capsule_path: str,
    ) -> dict[str, Any]:
        del self, payload
        order.append("prepare:" + remote_capsule_path)
        return {"ok": True}

    def fake_post(self: Any, path: str, payload: Any) -> dict[str, Any]:
        del self, payload
        order.append("post:" + path)
        return {"ok": True, "message": "imported"}

    monkeypatch.setattr(aec._AgentHttpExecutionClient, "_prepare_remote_capsule_access", fake_prepare)
    monkeypatch.setattr(aec._AgentHttpExecutionClient, "_post", fake_post)

    client = aec._AgentHttpExecutionClient(
        base_url="http://127.0.0.1:8765/v1",
        droplet_payload=_payload(),
    )
    result = client.import_capsule(
        **_payload(),
        remote_capsule_path="/opt/konnaxion/capsules/demo.kxcap",
        instance_id="konnaxion-prod",
        capsule_id="demo",
    )

    assert result["ok"] is True
    assert order == [
        "prepare:/opt/konnaxion/capsules/demo.kxcap",
        "post:/capsules/import",
    ]


def test_copy_capsule_normalizes_permissions_after_upload(tmp_path: Path, monkeypatch) -> None:
    capsule = tmp_path / "demo.kxcap"
    capsule.write_bytes(b"capsule")
    commands: list[str] = []

    def fake_ssh(
        self: Any,
        payload: Any,
        remote_command: str,
        *,
        timeout_seconds: int,
        success_message: str,
    ) -> dict[str, Any]:
        del self, payload, timeout_seconds
        commands.append(remote_command)
        return {"ok": True, "message": success_message, "returncode": 0}

    def fake_stream(
        self: Any,
        payload: Any,
        local_file: Path,
        remote_path: str,
        *,
        timeout_seconds: int,
    ) -> dict[str, Any]:
        del self, payload, local_file, remote_path, timeout_seconds
        return {"ok": True, "returncode": 0, "stdout": "", "stderr": ""}

    monkeypatch.setattr(aec._AgentHttpExecutionClient, "_ssh", fake_ssh)
    monkeypatch.setattr(aec._AgentHttpExecutionClient, "_stream_file_over_ssh", fake_stream)

    client = aec._AgentHttpExecutionClient(
        base_url="http://127.0.0.1:8765/v1",
        droplet_payload=_payload(),
        progress_callback=lambda *args: None,
    )
    result = client.copy_capsule_to_droplet(
        **_payload(),
        capsule_file=str(capsule),
        remote_capsule_path="/opt/konnaxion/capsules/demo.kxcap",
    )

    assert result["ok"] is True
    assert result["remote_capsule_access_prepared"] is True
    assert any('chmod 0640 "$CAPSULE"' in command for command in commands)
