from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from kx_agent.api import create_agent_api
from kx_manager.ui import agent_execution_client as aec
from kx_manager.ui.droplet_bootstrap import (
    _remote_bootstrap_command,
    _should_include_bootstrap_path,
)


class _SuccessHandler:
    async def run(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "ok": True,
            "action": action,
            "message": "ok",
            "data": {"received": sorted(payload)},
        }


def _bootstrap() -> str:
    return _remote_bootstrap_command(
        remote_archive="/tmp/konnaxion-manager-bootstrap.tar.gz",
        remote_kx_root="/opt/konnaxion",
        remote_manager_dir="/opt/konnaxion/manager",
        instance_id="konnaxion-prod",
    )


def test_bootstrap_archive_never_includes_local_agent_token(tmp_path: Path) -> None:
    token = tmp_path / "agent.token"
    token.write_text("do-not-ship\n", encoding="utf-8")
    assert _should_include_bootstrap_path(token, tmp_path) is False


def test_bootstrap_runs_agent_as_dedicated_service_identity() -> None:
    command = _bootstrap()

    assert "useradd --system" in command
    assert "--shell /usr/sbin/nologin" in command
    assert "User=kx-agent" in command
    assert "Group=kx-agent" in command
    assert "SupplementaryGroups=docker" in command
    assert "usermod -aG docker" not in command
    assert "ExecStart=/opt/konnaxion/manager/.venv/bin/kx-agent run" in command


def test_bootstrap_provisions_token_audit_and_systemd_hardening() -> None:
    command = _bootstrap()

    assert 'KX_TOKEN_PATH="$KX_MANAGER_DIR/agent.token"' in command
    assert "TOKEN_BACKUP" in command
    assert "secrets.token_urlsafe(32)" in command
    assert "chmod 0600 \"$KX_TOKEN_PATH\"" in command
    assert 'KX_AUDIT_FILE="$KX_AUDIT_DIR/agent-audit.jsonl"' in command
    assert "chmod 0600 \"$KX_AUDIT_FILE\"" in command
    assert "Environment=KX_REQUIRE_AGENT_TOKEN=true" in command
    assert "Environment=KX_REQUIRE_AGENT_AUDIT=true" in command
    assert "NoNewPrivileges=yes" in command
    assert "PrivateTmp=yes" in command
    assert "ProtectHome=yes" in command
    assert "ProtectSystem=full" in command
    assert "RestrictSUIDSGID=yes" in command


def test_bootstrap_does_not_recursively_chown_database_runtime_data() -> None:
    command = _bootstrap()

    assert 'chown -R "$KX_AGENT_USER:$KX_AGENT_GROUP" "$KX_ROOT/instances/$KX_INSTANCE_ID"' not in command
    assert '"$KX_ROOT/instances/$KX_INSTANCE_ID/env"' not in command or "owned_subdir" in command
    assert "postgres" not in "\n".join(
        line for line in command.splitlines() if line.lstrip().startswith("chown")
    )
    assert "redis" not in "\n".join(
        line for line in command.splitlines() if line.lstrip().startswith("chown")
    )


def test_ssh_write_request_uses_remote_token_without_copying_value(monkeypatch) -> None:
    seen: dict[str, str] = {}

    def fake_ssh(
        self: Any,
        payload: Any,
        remote_command: str,
        *,
        timeout_seconds: int,
        success_message: str,
    ) -> dict[str, Any]:
        del self, payload, timeout_seconds, success_message
        seen["command"] = remote_command
        return {
            "ok": True,
            "returncode": 0,
            "stdout": json.dumps({"ok": True, "action": "capsule_import"}),
            "stderr": "",
        }

    monkeypatch.setattr(aec._AgentHttpExecutionClient, "_ssh", fake_ssh)
    client = aec._AgentHttpExecutionClient(
        base_url="http://127.0.0.1:8765/v1",
        droplet_payload={
            "droplet_host": "2.56.97.41",
            "droplet_user": "root",
            "remote_kx_root": "/opt/konnaxion",
        },
    )

    result = client._ssh_agent_request(
        "POST",
        "/capsules/import",
        payload={"capsule_path": "/opt/konnaxion/capsules/test.kxcap"},
    )

    assert result["ok"] is True
    command = seen["command"]
    assert "/opt/konnaxion/manager/agent.token" in command
    assert "Authorization: Bearer %s" in command
    assert '-H "@$AUTH_FILE"' in command


def test_agent_write_auth_and_audit_boundary(tmp_path: Path, monkeypatch) -> None:
    token_file = tmp_path / "agent.token"
    token_file.write_text("test-token\n", encoding="utf-8")
    token_file.chmod(0o600)
    audit_file = tmp_path / "agent-audit.jsonl"

    monkeypatch.setenv("KX_REQUIRE_AGENT_TOKEN", "true")
    monkeypatch.setenv("KX_AGENT_TOKEN_PATH", str(token_file))
    monkeypatch.setenv("KX_REQUIRE_AGENT_AUDIT", "true")
    monkeypatch.setenv("KX_AGENT_AUDIT_FILE", str(audit_file))

    client = TestClient(create_agent_api(action_handler=_SuccessHandler()))

    denied = client.post("/v1/capsules/verify", json={"capsule_path": "/tmp/demo.kxcap"})
    assert denied.status_code == 401

    allowed = client.post(
        "/v1/capsules/verify",
        json={"capsule_path": "/tmp/demo.kxcap"},
        headers={"Authorization": "Bearer test-token"},
    )
    assert allowed.status_code == 200
    assert allowed.json()["ok"] is True

    lines = [json.loads(line) for line in audit_file.read_text(encoding="utf-8").splitlines()]
    outcomes = [item["outcome"] for item in lines]
    assert "blocked" in outcomes
    assert "started" in outcomes
    assert "succeeded" in outcomes
    assert audit_file.stat().st_mode & 0o777 == 0o600
