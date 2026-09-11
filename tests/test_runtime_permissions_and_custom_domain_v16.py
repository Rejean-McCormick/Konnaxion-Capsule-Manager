from __future__ import annotations

from pathlib import Path
from typing import Any

from kx_agent.runtime import compose
from kx_agent.runtime.compose import ComposeRenderOptions, render_traefik_dynamic_config
from kx_manager.defaults import DEFAULT_DROPLET_DOMAIN
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
        "instance_id": "konnaxion-prod",
        "domain": "konnaxion.com",
        "network_profile": "public_vps",
        "exposure_mode": "public",
    }


def test_bootstrap_migrates_only_agent_owned_runtime_bind_directories() -> None:
    command = _remote_bootstrap_command(
        remote_archive="/tmp/konnaxion-manager-bootstrap.tar.gz",
        remote_kx_root="/opt/konnaxion",
        remote_manager_dir="/opt/konnaxion/manager",
        instance_id="konnaxion-prod",
    )

    assert 'if [ -d "$KX_ROOT/instances/$KX_INSTANCE_ID/logs" ]; then' in command
    assert 'chown "$KX_AGENT_USER:$KX_AGENT_GROUP" "$KX_ROOT/instances/$KX_INSTANCE_ID/logs"' in command
    assert 'if [ -d "$KX_ROOT/instances/$KX_INSTANCE_ID/media" ]; then' in command
    assert 'chown "$KX_AGENT_USER:$KX_AGENT_GROUP" "$KX_ROOT/instances/$KX_INSTANCE_ID/media"' in command

    chown_lines = "\n".join(
        line for line in command.splitlines() if "chown" in line and not line.lstrip().startswith("#")
    )
    assert "$KX_INSTANCE_ID/postgres" not in chown_lines
    assert "$KX_INSTANCE_ID/redis" not in chown_lines


def test_existing_container_data_dirs_are_not_rechmodded(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "instance"
    (root / "postgres").mkdir(parents=True)
    (root / "redis").mkdir()

    calls: list[Path] = []

    def fake_ensure_dir(path: str | Path, *, mode: int = 0o750) -> Path:
        del mode
        value = Path(path)
        calls.append(value)
        value.mkdir(parents=True, exist_ok=True)
        return value

    monkeypatch.setattr(compose, "generated_instance_root", lambda options: root)
    monkeypatch.setattr(compose, "ensure_dir", fake_ensure_dir)
    monkeypatch.setattr(compose, "assert_under_root", lambda path: Path(path))
    monkeypatch.setattr(compose, "traefik_acme_enabled", lambda options: False)

    compose._ensure_runtime_dirs(
        ComposeRenderOptions(instance_id="konnaxion-prod", host="konnaxion.com")
    )

    assert root / "logs" in calls
    assert root / "media" in calls
    assert root / "postgres" not in calls
    assert root / "redis" not in calls


def test_remote_create_repairs_runtime_dirs_before_agent_post(monkeypatch) -> None:
    order: list[str] = []

    def fake_prepare(self: Any, payload: Any, instance_id: str) -> dict[str, Any]:
        del self, payload
        order.append("prepare:" + instance_id)
        return {"ok": True}

    def fake_post(self: Any, path: str, payload: Any) -> dict[str, Any]:
        del self
        order.append("post:" + path)
        assert payload["host"] == "konnaxion.com"
        assert payload["host_aliases"] == ["www.konnaxion.com"]
        return {"ok": True, "message": "created"}

    monkeypatch.setattr(aec._AgentHttpExecutionClient, "_prepare_remote_instance_runtime_access", fake_prepare)
    monkeypatch.setattr(aec._AgentHttpExecutionClient, "_post", fake_post)

    client = aec._AgentHttpExecutionClient(
        base_url="http://127.0.0.1:8765/v1",
        droplet_payload=_payload(),
    )
    result = client.create_instance(**_payload(), capsule_id="demo")

    assert result["ok"] is True
    assert order == ["prepare:konnaxion-prod", "post:/instances/create"]


def test_remote_runtime_repair_is_scoped_and_excludes_database_dirs(monkeypatch) -> None:
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
        return {"ok": True, "message": success_message}

    monkeypatch.setattr(aec._AgentHttpExecutionClient, "_ssh", fake_ssh)
    client = aec._AgentHttpExecutionClient(
        base_url="http://127.0.0.1:8765/v1",
        droplet_payload=_payload(),
    )

    result = client._prepare_remote_instance_runtime_access(_payload(), "konnaxion-prod")
    assert result["ok"] is True
    command = seen["command"]
    assert 'for dir in logs media' in command
    assert 'find "$INSTANCE/logs"' in command
    assert 'chown kx-agent:kx-agent "$INSTANCE/$dir"' in command
    assert 'postgres' not in command
    assert 'redis' not in command


def test_custom_domain_routes_apex_and_www() -> None:
    config = render_traefik_dynamic_config(
        "konnaxion.com",
        instance_id="konnaxion-prod",
        cert_resolver="letsencrypt",
    )
    rule = config["http"]["routers"]["kx-frontend"]["rule"]
    assert "Host(`konnaxion.com`)" in rule
    assert "Host(`www.konnaxion.com`)" in rule
    assert config["http"]["routers"]["kx-frontend"]["tls"]["certResolver"] == "letsencrypt"


def test_operator_default_domain_is_real_konnaxion_domain() -> None:
    assert DEFAULT_DROPLET_DOMAIN == "konnaxion.com"
