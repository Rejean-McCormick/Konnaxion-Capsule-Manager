from pathlib import Path
from types import SimpleNamespace

import kx_manager.ui.agent_execution_client as aec
from kx_manager.services.deploy import DropletDeployRequest, deploy_droplet


def _request(tmp_path: Path) -> DropletDeployRequest:
    capsule = tmp_path / "konnaxion-v14-local-2026.09.09.kxcap"
    capsule.write_bytes(b"kxcap")
    ssh_key = tmp_path / "id_ed25519"
    ssh_key.write_text("test-key", encoding="utf-8")
    return DropletDeployRequest(
        instance_id="konnaxion-prod",
        capsule_file=capsule,
        capsule_id="konnaxion-v14-local-2026.09.09",
        capsule_version="2026.09.09-local.1",
        droplet_name="netcup-vps",
        droplet_host="2.56.97.41",
        droplet_user="root",
        ssh_key_path=ssh_key,
        ssh_port=22,
        remote_kx_root="/opt/konnaxion",
        remote_capsule_dir="/opt/konnaxion/capsules",
        domain="2.56.97.41.sslip.io",
        confirmed=True,
        copy_capsule=False,
        verify=False,
        plan_only=True,
    )


def test_deploy_skips_redundant_runtime_mkdir_when_capsule_already_copied(tmp_path: Path) -> None:
    result = deploy_droplet(_request(tmp_path))
    assert result.ok is True
    runtime = next(step for step in result.steps if step.name == "ensure_remote_runtime")
    assert runtime.ok is True
    assert runtime.data["skipped"] is True
    assert runtime.data["remote_capsule_path"].startswith("/opt/konnaxion/capsules/")


def test_ssh_argv_is_noninteractive_publickey_only(tmp_path: Path) -> None:
    key = tmp_path / "id_ed25519"
    key.write_text("x", encoding="utf-8")
    client = aec._AgentHttpExecutionClient(base_url="http://127.0.0.1")
    argv = client._ssh_argv(
        {
            "droplet_host": "2.56.97.41",
            "droplet_user": "root",
            "ssh_key_path": str(key),
            "ssh_port": 22,
        }
    )
    assert argv[0] == "ssh"
    assert "-T" in argv
    assert "BatchMode=yes" in argv
    assert "PreferredAuthentications=publickey" in argv
    assert "PasswordAuthentication=no" in argv
    assert "KbdInteractiveAuthentication=no" in argv


def test_run_argv_detaches_stdin(monkeypatch) -> None:
    seen = {}

    def fake_run(argv, **kwargs):
        seen.update(kwargs)
        return SimpleNamespace(returncode=0, stdout="ok\n", stderr="")

    monkeypatch.setattr(aec.subprocess, "run", fake_run)
    result = aec._run_argv(["ssh", "example", "true"], timeout_seconds=5)
    assert result["ok"] is True
    assert seen["stdin"] is aec.subprocess.DEVNULL
