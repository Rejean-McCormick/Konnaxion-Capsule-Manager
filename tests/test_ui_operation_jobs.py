from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


BROWSER_HEADERS = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def _droplet_payload(tmp_path: Path, *, action: str) -> dict[str, Any]:
    capsule = tmp_path / "demo.kxcap"
    capsule.write_bytes(b"demo")
    return {
        "action": action,
        "target_mode": "droplet",
        "instance_id": "konnaxion-prod",
        "capsule_file": str(capsule),
        "capsule_id": "demo",
        "capsule_version": "1.0",
        "droplet_name": "netcup-vps",
        "droplet_host": "2.56.97.41",
        "droplet_user": "root",
        "ssh_key_path": str(tmp_path / "id_ed25519"),
        "ssh_port": "22",
        "remote_kx_root": "/opt/konnaxion",
        "remote_capsule_dir": "/opt/konnaxion/capsules",
        "domain": "2.56.97.41.sslip.io",
        "network_profile": "public_vps",
        "exposure_mode": "public",
        "confirmed": "true",
        "background_job": "true",
    }


def test_droplet_page_long_actions_enable_background_progress() -> None:
    from kx_manager.ui.page_parts.deploy import render

    html = render({"target_mode": "droplet"})

    assert 'action="/ui/actions/copy-capsule-to-droplet"' in html
    assert 'action="/ui/actions/deploy-droplet"' in html
    assert html.count('name="background_job" value="true"') >= 2
    assert 'name="copy_capsule" value="false"' in html


def test_droplet_form_preserves_background_and_skip_copy(tmp_path: Path) -> None:
    from kx_manager.ui.forms import form_to_payload, parse_action_form

    payload = _droplet_payload(tmp_path, action="deploy_droplet")
    payload["copy_capsule"] = "false"

    form = parse_action_form("deploy_droplet", payload)
    result = form_to_payload(form)

    assert result["background_job"] is True
    assert result["copy_capsule"] is False


def test_copy_action_backend_queues_background_job(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from kx_manager.services import operation_jobs
    from kx_manager.ui import action_backends

    calls: list[tuple[str, dict[str, Any]]] = []

    def fake_create(action: str, payload: Any) -> dict[str, Any]:
        calls.append((action, dict(payload)))
        return {
            "job_id": "op-123",
            "label": "Copy Capsule to Droplet",
            "status": "queued",
            "phase": "queued",
            "progress": 1,
            "log_file": "op.log",
            "job_file": "op.json",
        }

    monkeypatch.setattr(operation_jobs, "create_operation_job", fake_create)
    payload = _droplet_payload(tmp_path, action="copy_capsule_to_droplet")

    result = asyncio.run(
        action_backends._handle_droplet_step("copy_capsule_to_droplet", payload)
    )

    assert result.ok is True
    assert result.data["operation_job_id"] == "op-123"
    assert result.data["status_url"] == "/ui/operation-jobs/op-123"
    assert calls and calls[0][0] == "copy_capsule_to_droplet"


def test_operation_job_page_renders_progress(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KX_ROOT", str(tmp_path / "runtime"))
    monkeypatch.delenv("KX_OPERATION_JOB_DIR", raising=False)

    from kx_manager.services import operation_jobs
    from kx_manager.ui import app as ui_app

    class FakeExecutor:
        def submit(self, *args: Any, **kwargs: Any) -> None:
            return None

    monkeypatch.setattr(operation_jobs, "_executor_for_config", lambda: FakeExecutor())
    job = operation_jobs.create_operation_job(
        "copy_capsule_to_droplet",
        {
            "instance_id": "konnaxion-prod",
            "capsule_file": str(tmp_path / "demo.kxcap"),
            "droplet_name": "netcup-vps",
            "droplet_host": "2.56.97.41",
        },
    )
    operation_jobs._update_job(
        job["job_id"],
        status="running",
        phase="upload",
        progress=42,
        message="Uploading capsule: 38%.",
        progress_data={
            "transfer_percent": 38,
            "bytes_sent": 380,
            "bytes_total": 1000,
            "bytes_per_second": 1048576,
        },
    )

    app = FastAPI()
    ui_app.register(app)
    client = TestClient(app)
    response = client.get(f"/ui/operation-jobs/{job['job_id']}", headers=BROWSER_HEADERS)

    assert response.status_code == 200
    assert "Copy Capsule to Droplet Progress" in response.text
    assert "42%" in response.text
    assert "Transfer: 38%" in response.text
    assert "Live Operation Log" in response.text
    assert 'http-equiv="refresh"' in response.text


def test_missing_build_job_page_waits_instead_of_404(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KX_ROOT", str(tmp_path / "runtime"))

    from kx_manager.ui import app as ui_app

    app = FastAPI()
    ui_app.register(app)
    client = TestClient(app)
    response = client.get("/ui/build-jobs/not-yet-visible", headers=BROWSER_HEADERS)

    assert response.status_code == 200
    assert "Preparing progress view" in response.text
    assert 'http-equiv="refresh"' in response.text


def test_streaming_upload_reports_byte_progress(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    from kx_manager.ui.agent_execution_client import _AgentHttpExecutionClient

    source = tmp_path / "source.kxcap"
    destination = tmp_path / "received.kxcap"
    source.write_bytes(b"x" * (2 * 1024 * 1024 + 123))
    events: list[tuple[str, int, str, Any]] = []

    def fake_ssh_argv(self: Any, payload: Any) -> list[str]:
        script = (
            "import sys; "
            f"open(r'{destination}', 'wb').write(sys.stdin.buffer.read())"
        )
        return [sys.executable, "-c", script]

    monkeypatch.setattr(_AgentHttpExecutionClient, "_ssh_argv", fake_ssh_argv)
    client = _AgentHttpExecutionClient(
        base_url="http://127.0.0.1",
        progress_callback=lambda phase, progress, message, data=None: events.append(
            (phase, progress, message, data)
        ),
    )

    result = client._stream_file_over_ssh(
        {}, source, "/opt/konnaxion/capsules/demo.kxcap", timeout_seconds=30
    )

    assert result["ok"] is True
    assert result["transfer_percent"] == 100
    assert destination.read_bytes() == source.read_bytes()
    assert any(progress >= 95 for _phase, progress, _message, _data in events)


def test_copy_action_redirects_to_operation_progress_page(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from kx_manager.ui import app as ui_app

    async def fake_dispatch(action: Any, payload: Any = None) -> dict[str, Any]:
        return {
            "ok": True,
            "action": "copy_capsule_to_droplet",
            "message": "Copy Capsule to Droplet queued.",
            "instance_id": "konnaxion-prod",
            "data": {"operation_job_id": "op-redirect-1"},
            "stdout": None,
            "stderr": None,
            "returncode": None,
        }

    monkeypatch.setattr(ui_app, "dispatch_gui_action", fake_dispatch, raising=False)
    monkeypatch.setattr(ui_app, "_validated_payload", lambda action, payload: dict(payload), raising=True)

    app = FastAPI()
    ui_app.register(app)
    client = TestClient(app, follow_redirects=False)
    response = client.post(
        "/ui/actions/copy-capsule-to-droplet",
        data={"background_job": "true"},
        headers=BROWSER_HEADERS,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/ui/operation-jobs/op-redirect-1"
