from __future__ import annotations

import asyncio
from typing import Any

from kx_manager.ui import app as ui_app
from kx_manager.ui import action_backends
from kx_manager.ui.page_parts.health import render as render_health
from kx_manager.ui.page_parts.instances import render as render_instances
from kx_manager.ui.page_parts.logs import render as render_logs


def _droplet_context() -> dict[str, Any]:
    return {
        "target_mode": "droplet",
        "instance_id": "konnaxion-prod",
        "droplet_name": "netcup-vps",
        "droplet_host": "2.56.97.41",
        "droplet_user": "root",
        "ssh_key_path": r"C:\Users\rejea\.ssh\id_ed25519",
        "ssh_port": 22,
        "remote_kx_root": "/opt/konnaxion",
        "remote_capsule_dir": "/opt/konnaxion/capsules",
        "domain": "2.56.97.41.sslip.io",
        "remote_agent_url": "",
    }


def test_logs_page_preserves_droplet_route_fields() -> None:
    html = render_logs(_droplet_context())
    assert 'name="target_mode" value="droplet"' in html
    assert 'name="droplet_host" value="2.56.97.41"' in html
    assert 'name="remote_kx_root" value="/opt/konnaxion"' in html
    assert 'name="instance_id"' in html
    assert 'value="konnaxion-prod"' in html


def test_health_and_instance_status_preserve_droplet_route_fields() -> None:
    for html in (render_health(_droplet_context()), render_instances(_droplet_context())):
        assert 'name="target_mode" value="droplet"' in html
        assert 'name="droplet_host" value="2.56.97.41"' in html
        assert 'name="remote_kx_root" value="/opt/konnaxion"' in html


def test_logs_validation_keeps_target_route_but_rejects_unrelated_fields() -> None:
    raw = {
        **_droplet_context(),
        "action": "view_logs",
        "service": "",
        "lines": "200",
        "tail": "true",
        "untrusted_extra": "must-not-survive",
    }
    payload = ui_app._validated_payload("view_logs", raw)
    assert payload["instance_id"] == "konnaxion-prod"
    assert payload["target_mode"] == "droplet"
    assert payload["droplet_host"] == "2.56.97.41"
    assert payload["remote_kx_root"] == "/opt/konnaxion"
    assert "untrusted_extra" not in payload


def test_view_logs_uses_remote_droplet_client(monkeypatch) -> None:
    calls: list[dict[str, Any]] = []

    class FakeClient:
        def instance_logs(self, **kwargs: Any) -> dict[str, Any]:
            calls.append(kwargs)
            return {"ok": True, "message": "remote logs", "transport": "ssh"}

    def fake_execution_payload(action: str, payload: dict[str, Any]) -> dict[str, Any]:
        assert action == "view_logs"
        assert payload["target_mode"] == "droplet"
        return {**payload, "manager_client": FakeClient()}

    monkeypatch.setattr(action_backends, "_execution_payload", fake_execution_payload)

    result = asyncio.run(
        action_backends._handle_view_logs(
            "view_logs",
            {**_droplet_context(), "lines": 123, "tail": True},
        )
    )

    assert result.ok is True
    assert result.message == "remote logs"
    assert calls == [
        {
            "instance_id": "konnaxion-prod",
            "service": None,
            "tail": 123,
        }
    ]


def test_remote_missing_compose_has_operator_message(monkeypatch) -> None:
    class FakeClient:
        def instance_logs(self, **kwargs: Any) -> dict[str, Any]:
            return {
                "ok": False,
                "message": "Compose file does not exist: /opt/konnaxion/instances/konnaxion-prod/state/docker-compose.runtime.yml",
            }

    monkeypatch.setattr(
        action_backends,
        "_execution_payload",
        lambda action, payload: {**payload, "manager_client": FakeClient()},
    )

    result = asyncio.run(
        action_backends._handle_view_logs(
            "view_logs",
            {**_droplet_context(), "lines": 200},
        )
    )

    assert result.ok is False
    assert "Run Deploy Droplet" in result.message
    assert result.data["target_mode"] == "droplet"

from kx_manager.ui.page_parts.security import render as render_security


def test_security_page_preserves_droplet_route_fields() -> None:
    html = render_security(_droplet_context())
    assert 'name="target_mode" value="droplet"' in html
    assert 'name="droplet_host" value="2.56.97.41"' in html
    assert 'name="remote_kx_root" value="/opt/konnaxion"' in html
    assert 'name="instance_id"' in html
    assert 'value="konnaxion-prod"' in html


def test_security_validation_keeps_target_route() -> None:
    raw = {
        **_droplet_context(),
        "action": "run_security_check",
        "run_security_gate": "true",
        "untrusted_extra": "must-not-survive",
    }
    payload = ui_app._validated_payload("run_security_check", raw)
    assert payload["instance_id"] == "konnaxion-prod"
    assert payload["target_mode"] == "droplet"
    assert payload["droplet_host"] == "2.56.97.41"
    assert payload["remote_kx_root"] == "/opt/konnaxion"
    assert "untrusted_extra" not in payload


def test_security_check_uses_remote_droplet_client(monkeypatch) -> None:
    calls: list[dict[str, Any]] = []

    class FakeClient:
        def security_check(self, **kwargs: Any) -> dict[str, Any]:
            calls.append(kwargs)
            return {
                "ok": True,
                "message": "remote security ok",
                "transport": "ssh",
                "security_status": "pass",
            }

    def fake_execution_payload(action: str, payload: dict[str, Any]) -> dict[str, Any]:
        assert action == "run_security_check"
        assert payload["target_mode"] == "droplet"
        return {**payload, "manager_client": FakeClient()}

    monkeypatch.setattr(action_backends, "_execution_payload", fake_execution_payload)

    result = asyncio.run(
        action_backends._handle_run_security_check(
            "run_security_check",
            {**_droplet_context(), "run_security_gate": True},
        )
    )

    assert result.ok is True
    assert result.message == "remote security ok"
    assert calls == [
        {
            "instance_id": "konnaxion-prod",
            "blocking": True,
        }
    ]

from kx_manager.ui.page_parts.backups import render as render_backups
from kx_manager.ui.page_parts.restore import render as render_restore


def test_backup_pages_preserve_droplet_route_fields() -> None:
    for html in (render_backups(_droplet_context()), render_restore(_droplet_context())):
        assert 'name="target_mode" value="droplet"' in html
        assert 'name="droplet_host" value="2.56.97.41"' in html
        assert 'name="remote_kx_root" value="/opt/konnaxion"' in html
        assert 'name="instance_id"' in html
        assert 'value="konnaxion-prod"' in html



def test_restore_page_test_restore_requires_isolated_target_instance() -> None:
    html = render_restore(_droplet_context())
    # Restore Into New Instance and Test Restore must each submit their own target id.
    assert html.count('name="target_instance_id"') >= 2
    assert 'value="konnaxion-restore-test"' in html


def test_create_backup_validation_keeps_target_route() -> None:
    raw = {
        **_droplet_context(),
        "action": "create_backup",
        "backup_class": "manual",
        "verify_after_create": "true",
        "untrusted_extra": "must-not-survive",
    }
    payload = ui_app._validated_payload("create_backup", raw)
    assert payload["instance_id"] == "konnaxion-prod"
    assert payload["target_mode"] == "droplet"
    assert payload["droplet_host"] == "2.56.97.41"
    assert payload["remote_kx_root"] == "/opt/konnaxion"
    assert "untrusted_extra" not in payload


def test_create_backup_uses_remote_droplet_client(monkeypatch) -> None:
    calls: list[dict[str, Any]] = []

    class FakeClient:
        def backup_instance(self, **kwargs: Any) -> dict[str, Any]:
            calls.append(kwargs)
            return {
                "ok": True,
                "message": "remote backup ok",
                "transport": "ssh",
                "data": {"backup_id": "backup-001"},
            }

    monkeypatch.setattr(
        action_backends,
        "_execution_payload",
        lambda action, payload: {**payload, "manager_client": FakeClient()},
    )

    result = asyncio.run(
        action_backends._handle_create_backup(
            "create_backup",
            {
                **_droplet_context(),
                "backup_class": "manual",
                "verify_after_create": True,
            },
        )
    )

    assert result.ok is True
    assert result.message == "remote backup ok"
    assert calls == [
        {
            "instance_id": "konnaxion-prod",
            "backup_class": "manual",
            "verify_after_create": True,
        }
    ]


def test_backup_read_and_test_restore_use_remote_droplet_client(monkeypatch) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    class FakeClient:
        def list_backups(self, **kwargs: Any) -> dict[str, Any]:
            calls.append(("list", kwargs))
            return {"ok": True, "message": "remote list", "transport": "ssh"}

        def verify_backup(self, **kwargs: Any) -> dict[str, Any]:
            calls.append(("verify", kwargs))
            return {"ok": True, "message": "remote verify", "transport": "ssh"}

        def test_restore_backup(self, **kwargs: Any) -> dict[str, Any]:
            calls.append(("test", kwargs))
            return {"ok": True, "message": "remote test", "transport": "ssh"}

    monkeypatch.setattr(
        action_backends,
        "_execution_payload",
        lambda action, payload: {**payload, "manager_client": FakeClient()},
    )

    base = _droplet_context()
    list_result = asyncio.run(
        action_backends._handle_manager_backup_action(
            "list_backups", {**base, "limit": 20}
        )
    )
    verify_result = asyncio.run(
        action_backends._handle_manager_backup_action(
            "verify_backup", {**base, "backup_id": "backup-001"}
        )
    )
    test_result = asyncio.run(
        action_backends._handle_manager_backup_action(
            "test_restore_backup",
            {
                **base,
                "backup_id": "backup-001",
                "target_instance_id": "demo-test-restore-001",
                "restore_data": True,
            },
        )
    )

    assert list_result.ok and verify_result.ok and test_result.ok
    assert calls[0] == (
        "list",
        {
            "instance_id": "konnaxion-prod",
            "status": None,
            "backup_class": None,
            "limit": 20,
        },
    )
    assert calls[1] == (
        "verify",
        {"backup_id": "backup-001", "instance_id": "konnaxion-prod"},
    )
    assert calls[2] == (
        "test",
        {
            "backup_id": "backup-001",
            "instance_id": "konnaxion-prod",
            "target_instance_id": "demo-test-restore-001",
            "restore_data": True,
        },
    )
