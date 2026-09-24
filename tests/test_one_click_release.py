from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest


def test_dashboard_has_single_go_live_button() -> None:
    from kx_manager.ui.page_parts.dashboard import render

    html = render(
        {
            "target_mode": "droplet",
            "droplet_host": "203.0.113.10",
            "domain": "example.test",
            "droplet_user": "root",
            "ssh_key_path": r"C:\\keys\\id_ed25519",
        }
    )

    assert html.count("GO LIVE — BUILD, SIGN &amp; DEPLOY") == 1
    assert 'action="/ui/actions/deploy-droplet"' in html
    assert 'name="one_click_release" value="true"' in html
    assert 'name="background_job" value="true"' in html
    # A fresh release must be built; the one-click form must not submit a stale capsule.
    go_live = html.split("GO LIVE", 2)[2].split("Checks", 1)[0]
    assert 'name="capsule_file"' not in go_live


def test_one_click_deploy_form_allows_missing_capsule(tmp_path: Path) -> None:
    from kx_manager.ui.forms import form_to_payload, parse_action_form

    source = tmp_path / "source"
    source.mkdir()
    payload = {
        "action": "deploy_droplet",
        "target_mode": "droplet",
        "instance_id": "konnaxion-prod",
        "source_dir": str(source),
        "droplet_name": "prod",
        "droplet_host": "203.0.113.10",
        "droplet_user": "root",
        "ssh_key_path": str(tmp_path / "id_ed25519"),
        "ssh_port": "22",
        "remote_kx_root": "/opt/konnaxion",
        "remote_capsule_dir": "/opt/konnaxion/capsules",
        "domain": "example.test",
        "network_profile": "public_vps",
        "exposure_mode": "public",
        "confirmed": "true",
        "background_job": "true",
        "one_click_release": "true",
    }

    form = parse_action_form("deploy_droplet", payload)
    result = form_to_payload(form)
    assert "capsule_file" not in result
    assert result["one_click_release"] is True
    assert result["background_job"] is True


def test_release_keys_are_generated_once_and_reused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from kx_manager.services import release

    payload = {"local_runtime_root": str(tmp_path / "runtime")}
    first = release.ensure_release_signing_keys(payload)
    second = release.ensure_release_signing_keys(payload)

    private_path = Path(first["private_key_file"])
    public_path = Path(first["public_key_file"])
    assert private_path.is_file()
    assert public_path.is_file()
    assert first["created"] is True
    assert second["created"] is False
    assert first["public_key_fingerprint"] == second["public_key_fingerprint"]
    assert b"PRIVATE KEY" in private_path.read_bytes()
    assert b"PUBLIC KEY" in public_path.read_bytes()


def test_go_live_operation_job_uses_production_label(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KX_ROOT", str(tmp_path / "runtime"))
    monkeypatch.delenv("KX_OPERATION_JOB_DIR", raising=False)

    from kx_manager.services import operation_jobs

    class FakeExecutor:
        def submit(self, *args: Any, **kwargs: Any) -> None:
            return None

    monkeypatch.setattr(operation_jobs, "_executor_for_config", lambda: FakeExecutor())
    job = operation_jobs.create_operation_job(
        "deploy_droplet",
        {
            "one_click_release": True,
            "instance_id": "konnaxion-prod",
            "droplet_host": "203.0.113.10",
            "domain": "example.test",
        },
    )
    assert job["label"] == "GO LIVE — Production Release"
    assert job["payload"]["one_click_release"] is True


def test_one_click_release_orchestrates_backup_deploy_and_final_health(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx

    from kx_manager.services import deploy, operation_jobs, release
    from kx_manager.ui import action_backends, agent_execution_client
    from kx_manager.ui.action_models import GuiActionResult

    capsule = tmp_path / "release.kxcap"
    capsule.write_bytes(b"signed")
    public_key = tmp_path / "release-public.pem"
    public_key.write_text("public", encoding="utf-8")

    monkeypatch.setattr(
        release,
        "prepare_signed_release",
        lambda payload: {
            "capsule_id": "konnaxion-v14-release-2026.09.24",
            "capsule_version": "2026.09.24-release.1",
            "capsule_file": str(capsule),
            "public_key_file": str(public_key),
            "public_key_fingerprint": "fp",
            "public_key_file_sha256": "sha",
            "signing_keys_created": True,
            "build": {"ok": True},
            "verify": {"ok": True},
        },
    )

    async def fake_bootstrap(action: str, payload: Any) -> GuiActionResult:
        return GuiActionResult(ok=True, action=action, message="bootstrapped", data={})

    monkeypatch.setattr(action_backends, "_handle_bootstrap_droplet_agent", fake_bootstrap)

    calls: list[tuple[str, dict[str, Any]]] = []

    class FakeClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def check_droplet_agent(self, **payload: Any) -> dict[str, Any]:
            return {"ok": True}

        def _post(self, path: str, payload: Any) -> dict[str, Any]:
            calls.append((path, dict(payload)))
            if path == "/instances/status":
                return {"ok": True, "data": {"state": "running", "services": [{"name": "django", "state": "running"}]}}
            if path == "/instances/backup":
                return {"ok": True, "backup_id": "b1"}
            if path == "/instances/health":
                return {"ok": True, "status": "healthy"}
            return {"ok": True}

    monkeypatch.setattr(agent_execution_client, "_AgentHttpExecutionClient", FakeClient)
    monkeypatch.setattr(agent_execution_client, "_remote_agent_base_url", lambda payload: "http://x")

    class Request:
        build = False
        verify = True
        copy_capsule = True
        run_security_gate = True
        start = True

    monkeypatch.setattr(operation_jobs, "_operation_request", lambda *args, **kwargs: Request())
    monkeypatch.setattr(
        deploy,
        "deploy_droplet",
        lambda request: deploy.DeployResult(
            ok=True,
            action="deploy_droplet",
            instance_id="konnaxion-prod",
            message="deployed",
            data={"public_url": "https://example.test"},
        ),
    )

    class Response:
        status_code = 200
        url = "https://example.test/"

    monkeypatch.setattr(httpx, "get", lambda *args, **kwargs: Response())
    monkeypatch.setattr(operation_jobs, "_update_job", lambda *args, **kwargs: {})
    monkeypatch.setattr(operation_jobs, "append_job_log", lambda *args, **kwargs: None)

    events: list[tuple[str, int, str]] = []
    result = operation_jobs._run_one_click_release(
        "job-1",
        {
            "instance_id": "konnaxion-prod",
            "source_dir": str(tmp_path),
            "capsule_output_dir": str(tmp_path),
            "droplet_host": "203.0.113.10",
            "droplet_user": "root",
            "ssh_key_path": str(tmp_path / "id_ed25519"),
            "remote_kx_root": "/opt/konnaxion",
            "remote_capsule_dir": "/opt/konnaxion/capsules",
            "domain": "example.test",
        },
        lambda phase, progress, message, data=None: events.append((phase, progress, message)),
    )

    assert result["ok"] is True
    assert result["public_url"] == "https://example.test"
    assert ("/instances/backup", {
        "instance_id": "konnaxion-prod",
        "backup_class": "pre_update",
        "verify_after_create": True,
    }) in calls
    assert any(path == "/instances/health" for path, _payload in calls)
    assert events[-1][0] == "complete"


def test_app_validation_preserves_one_click_release_build_inputs(tmp_path: Path) -> None:
    from kx_manager.ui.app import _validated_payload

    source = tmp_path / "Konnaxion"
    source.mkdir()
    output = tmp_path / "runtime" / "capsules"
    payload = {
        "action": "deploy_droplet",
        "target_mode": "droplet",
        "instance_id": "konnaxion-prod",
        "source_dir": str(source),
        "capsule_output_dir": str(output),
        "droplet_name": "prod",
        "droplet_host": "203.0.113.10",
        "droplet_user": "root",
        "ssh_key_path": str(tmp_path / "id_ed25519"),
        "ssh_port": "22",
        "remote_kx_root": "/opt/konnaxion",
        "remote_capsule_dir": "/opt/konnaxion/capsules",
        "domain": "example.test",
        "network_profile": "public_vps",
        "exposure_mode": "public",
        "confirmed": "true",
        "background_job": "true",
        "one_click_release": "true",
    }

    result = _validated_payload("deploy_droplet", payload)
    assert result["one_click_release"] is True
    assert result["background_job"] is True
    assert str(result["source_dir"]) == str(source)
    assert result["capsule_output_dir"] == str(output)
    assert "capsule_file" not in result
