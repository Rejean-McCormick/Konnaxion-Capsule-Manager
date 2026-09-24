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

    bootstrap_calls: list[str] = []

    async def fake_bootstrap(action: str, payload: Any) -> GuiActionResult:
        del payload
        bootstrap_calls.append(action)
        return GuiActionResult(ok=True, action=action, message="bootstrapped", data={})

    monkeypatch.setattr(action_backends, "_handle_bootstrap_droplet_agent", fake_bootstrap)

    calls: list[tuple[str, dict[str, Any]]] = []

    class FakeClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def check_droplet_agent(self, **payload: Any) -> dict[str, Any]:
            return {"ok": True}

        def _scp_file_to_path(
            self,
            payload: Any,
            local_file: Any,
            remote_path: str,
            *,
            timeout_seconds: int,
        ) -> dict[str, Any]:
            del payload, local_file, remote_path, timeout_seconds
            return {"ok": True, "returncode": 0}

        def _ssh(
            self,
            payload: Any,
            remote_command: str,
            *,
            timeout_seconds: int,
            success_message: str,
        ) -> dict[str, Any]:
            del payload, remote_command, timeout_seconds
            return {"ok": True, "returncode": 0, "message": success_message}

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
    assert result["agent_mode"] == "reuse"
    assert bootstrap_calls == []
    assert ("/instances/backup", {
        "instance_id": "konnaxion-prod",
        "backup_class": "pre_update",
        "verify_after_create": True,
    }) in calls
    assert any(path == "/instances/health" for path, _payload in calls)
    assert events[-1][0] == "complete"



def test_command_failure_detail_includes_remote_stderr_and_returncode() -> None:
    from kx_manager.services.operation_jobs import _command_failure_detail

    detail = _command_failure_detail(
        "Droplet Agent bootstrap failed",
        {
            "ok": False,
            "message": "Command failed.",
            "returncode": 67,
            "stderr": "kx-agent group is missing",
            "stdout": "before failure",
        },
    )
    assert "rc=67" in detail
    assert "kx-agent group is missing" in detail
    assert "before failure" in detail


def test_public_key_sync_on_healthy_agent_is_minimal(tmp_path: Path) -> None:
    from kx_manager.services.operation_jobs import _sync_trusted_release_public_key

    key = tmp_path / "release-public.pem"
    key.write_text("public-key", encoding="utf-8")
    seen: dict[str, Any] = {}

    class Client:
        def _scp_file_to_path(self, payload: Any, local_file: Any, remote_path: str, *, timeout_seconds: int) -> dict[str, Any]:
            seen["scp"] = (str(local_file), remote_path, timeout_seconds)
            return {"ok": True, "returncode": 0}

        def _ssh(self, payload: Any, remote_command: str, *, timeout_seconds: int, success_message: str) -> dict[str, Any]:
            seen["command"] = remote_command
            return {"ok": True, "returncode": 0, "message": success_message}

    result = _sync_trusted_release_public_key(
        Client(),
        {"remote_kx_root": "/opt/konnaxion"},
        str(key),
    )
    assert result["ok"] is True
    command = seen["command"]
    assert "apt-get" not in command
    assert "uv sync" not in command
    assert "systemctl restart" not in command
    assert "/opt/konnaxion/agent/keys/capsule-signing-public.pem" in command
    assert "curl --fail-with-body" in command

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


def test_droplet_environment_overrides_use_canonical_env(monkeypatch: pytest.MonkeyPatch) -> None:
    from kx_manager.defaults import droplet_environment_overrides

    monkeypatch.setenv("KX_DROPLET_USER", "deploy")
    monkeypatch.setenv("KX_DROPLET_SSH_KEY_PATH", r"C:\keys\prod_ed25519")
    monkeypatch.setenv("KX_DROPLET_SSH_PORT", "2222")
    values = droplet_environment_overrides()

    assert values["droplet_user"] == "deploy"
    assert values["ssh_key_path"] == r"C:\keys\prod_ed25519"
    assert values["ssh_port"] == 2222


def test_dashboard_go_live_prefers_operator_env_over_stale_context(monkeypatch: pytest.MonkeyPatch) -> None:
    from kx_manager.ui.page_parts.dashboard import render

    monkeypatch.setenv("KX_DROPLET_USER", "deploy")
    monkeypatch.setenv("KX_DROPLET_SSH_KEY_PATH", r"C:\keys\prod_ed25519")
    html = render(
        {
            "target_mode": "droplet",
            "droplet_host": "203.0.113.10",
            "domain": "example.test",
            "droplet_user": "root",
            "ssh_key_path": r"C:\keys\old",
        }
    )
    assert 'name="droplet_user" value="deploy"' in html
    assert 'name="ssh_key_path" value="C:\\keys\\prod_ed25519"' in html


def test_one_click_runtime_prefers_operator_env_over_job_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import httpx
    from kx_manager.services import deploy, operation_jobs, release
    from kx_manager.ui import agent_execution_client

    capsule = tmp_path / "release.kxcap"
    capsule.write_bytes(b"signed")
    public_key = tmp_path / "release-public.pem"
    public_key.write_text("public", encoding="utf-8")
    prod_key = tmp_path / "prod-key"
    prod_key.write_text("key", encoding="utf-8")
    monkeypatch.setenv("KX_DROPLET_USER", "deploy")
    monkeypatch.setenv("KX_DROPLET_SSH_KEY_PATH", str(prod_key))

    monkeypatch.setattr(release, "prepare_signed_release", lambda payload: {
        "capsule_id": "kx", "capsule_version": "1", "capsule_file": str(capsule),
        "public_key_file": str(public_key), "public_key_fingerprint": "fp",
        "public_key_file_sha256": "sha", "signing_keys_created": False,
        "build": {"ok": True}, "verify": {"ok": True},
    })

    seen: dict[str, Any] = {}
    class FakeClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None: pass
        def check_droplet_agent(self, **payload: Any) -> dict[str, Any]:
            seen.update(payload); return {"ok": True}
        def _scp_file_to_path(self, payload: Any, *args: Any, **kwargs: Any) -> dict[str, Any]: return {"ok": True, "returncode": 0}
        def _ssh(self, payload: Any, *args: Any, **kwargs: Any) -> dict[str, Any]: return {"ok": True, "returncode": 0}
        def _post(self, path: str, payload: Any) -> dict[str, Any]:
            if path == "/instances/status": return {"ok": True, "data": {"state": "stopped"}}
            if path == "/instances/health": return {"ok": True}
            return {"ok": True}
    monkeypatch.setattr(agent_execution_client, "_AgentHttpExecutionClient", FakeClient)
    monkeypatch.setattr(agent_execution_client, "_remote_agent_base_url", lambda payload: "http://x")
    class Request:
        build=False; verify=True; copy_capsule=True; run_security_gate=True; start=True
    monkeypatch.setattr(operation_jobs, "_operation_request", lambda *a, **k: Request())
    monkeypatch.setattr(deploy, "deploy_droplet", lambda request: deploy.DeployResult(ok=True, action="deploy_droplet", instance_id="konnaxion-prod", message="ok", data={"public_url":"https://example.test"}))
    class Response:
        status_code=200; url="https://example.test/"
    monkeypatch.setattr(httpx, "get", lambda *a, **k: Response())
    monkeypatch.setattr(operation_jobs, "_update_job", lambda *a, **k: {})
    monkeypatch.setattr(operation_jobs, "append_job_log", lambda *a, **k: None)

    result = operation_jobs._run_one_click_release("j", {
        "instance_id":"konnaxion-prod", "source_dir":str(tmp_path), "capsule_output_dir":str(tmp_path),
        "droplet_host":"203.0.113.10", "droplet_user":"root", "ssh_key_path":str(tmp_path/"old"),
        "remote_kx_root":"/opt/konnaxion", "remote_capsule_dir":"/opt/konnaxion/capsules", "domain":"example.test"
    }, lambda *args, **kwargs: None)
    assert result["ok"] is True
    assert seen["droplet_user"] == "deploy"
    assert seen["ssh_key_path"] == str(prod_key)


def test_one_click_migrates_legacy_netcup_root_to_kx_admin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from kx_manager.services import operation_jobs, release
    from kx_manager.ui import agent_execution_client

    for name in (
        "KX_DROPLET_USER",
        "KX_DROPLET_SSH_KEY_PATH",
        "KX_SSH_KEY_PATH",
    ):
        monkeypatch.delenv(name, raising=False)

    capsule = tmp_path / "release.kxcap"
    capsule.write_bytes(b"signed")
    public_key = tmp_path / "release-public.pem"
    public_key.write_text("public", encoding="utf-8")
    monkeypatch.setattr(
        release,
        "prepare_signed_release",
        lambda payload: {
            "capsule_id": "kx",
            "capsule_version": "1",
            "capsule_file": str(capsule),
            "public_key_file": str(public_key),
            "public_key_fingerprint": "fp",
            "public_key_file_sha256": "sha",
            "signing_keys_created": False,
            "build": {"ok": True},
            "verify": {"ok": True},
        },
    )

    seen: dict[str, Any] = {}

    class StopAfterIdentity(Exception):
        pass

    class FakeClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def check_droplet_agent(self, **payload: Any) -> dict[str, Any]:
            seen.update(payload)
            raise StopAfterIdentity

    monkeypatch.setattr(agent_execution_client, "_AgentHttpExecutionClient", FakeClient)
    monkeypatch.setattr(agent_execution_client, "_remote_agent_base_url", lambda payload: "http://x")
    monkeypatch.setattr(operation_jobs, "_update_job", lambda *a, **k: {})
    monkeypatch.setattr(operation_jobs, "append_job_log", lambda *a, **k: None)

    with pytest.raises(StopAfterIdentity):
        operation_jobs._run_one_click_release(
            "job",
            {
                "instance_id": "konnaxion-prod",
                "source_dir": str(tmp_path),
                "capsule_output_dir": str(tmp_path),
                "droplet_name": "netcup-vps",
                "droplet_host": "2.56.97.41",
                "droplet_user": "root",
                "ssh_key_path": str(tmp_path / "id_ed25519"),
                "remote_kx_root": "/opt/konnaxion",
                "remote_capsule_dir": "/opt/konnaxion/capsules",
                "domain": "konnaxion.com",
            },
            lambda *args, **kwargs: None,
        )

    assert seen["droplet_user"] == "kx-admin"
    assert seen["ssh_user"] == "kx-admin"


def test_privileged_ssh_uses_noninteractive_sudo_for_operator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from kx_manager.ui import agent_execution_client

    seen: dict[str, Any] = {}

    def fake_run(argv: list[str], *, timeout_seconds: int) -> dict[str, Any]:
        seen["argv"] = argv
        seen["timeout"] = timeout_seconds
        return {"ok": True, "returncode": 0, "message": "Command completed."}

    monkeypatch.setattr(agent_execution_client, "_run_argv", fake_run)
    client = agent_execution_client._AgentHttpExecutionClient(base_url="http://127.0.0.1:8765")
    result = client._ssh_privileged(
        {
            "droplet_host": "2.56.97.41",
            "droplet_user": "kx-admin",
            "ssh_key_path": r"C:\Users\rejea\.ssh\id_ed25519",
            "ssh_port": 22,
        },
        "systemctl is-active konnaxion-agent",
        timeout_seconds=30,
        success_message="ok",
    )

    assert result["ok"] is True
    assert "kx-admin@2.56.97.41" in seen["argv"]
    assert seen["argv"][-1].startswith("sudo -n bash -lc ")
    assert "systemctl is-active konnaxion-agent" in seen["argv"][-1]


def test_non_root_capsule_copy_stages_in_tmp_then_installs_with_sudo_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from kx_manager.ui import agent_execution_client

    capsule = tmp_path / "release.kxcap"
    capsule.write_bytes(b"capsule")
    privileged_commands: list[str] = []
    scp_argv: list[str] = []

    def fake_privileged(
        self: Any,
        payload: Any,
        remote_command: str,
        *,
        timeout_seconds: int,
        success_message: str,
    ) -> dict[str, Any]:
        del self, payload, timeout_seconds
        privileged_commands.append(remote_command)
        return {"ok": True, "returncode": 0, "message": success_message}

    def fake_run(argv: list[str], *, timeout_seconds: int) -> dict[str, Any]:
        del timeout_seconds
        scp_argv[:] = argv
        return {"ok": True, "returncode": 0, "message": "ok"}

    monkeypatch.setattr(agent_execution_client._AgentHttpExecutionClient, "_ssh_privileged", fake_privileged)
    monkeypatch.setattr(agent_execution_client, "_run_argv", fake_run)
    monkeypatch.setattr(
        agent_execution_client._AgentHttpExecutionClient,
        "_prepare_remote_capsule_access",
        lambda self, payload, remote_path: {"ok": True, "remote_capsule_path": remote_path},
    )

    client = agent_execution_client._AgentHttpExecutionClient(base_url="http://127.0.0.1:8765")
    result = client._copy_capsule(
        {
            "capsule_file": str(capsule),
            "droplet_host": "2.56.97.41",
            "droplet_user": "kx-admin",
            "ssh_key_path": r"C:\Users\rejea\.ssh\id_ed25519",
            "ssh_port": 22,
            "remote_capsule_dir": "/opt/konnaxion/capsules",
        }
    )

    assert result["ok"] is True
    assert scp_argv[-1].endswith(":/tmp/konnaxion-upload-release.kxcap")
    assert any("install -m 0640 -o root -g kx-agent" in cmd for cmd in privileged_commands)
    assert any("/opt/konnaxion/capsules/release.kxcap" in cmd for cmd in privileged_commands)
