from pathlib import Path

from kx_manager.services.deploy import DropletDeployRequest, deploy_droplet


def test_deploy_skip_copy_uses_remote_posix_capsule_path(tmp_path: Path) -> None:
    capsule = tmp_path / "konnaxion-v14-local-2026.09.09.kxcap"
    capsule.write_bytes(b"kxcap")
    ssh_key = tmp_path / "id_ed25519"
    ssh_key.write_text("test-key", encoding="utf-8")

    request = DropletDeployRequest(
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

    result = deploy_droplet(request)

    assert result.ok is True
    expected = "/opt/konnaxion/capsules/konnaxion-v14-local-2026.09.09.kxcap"
    assert result.data["remote_capsule_path"] == expected

    import_step = next(step for step in result.steps if step.name == "import_capsule")
    assert import_step.data["capsule_path"] == expected
    assert import_step.data["capsule_file"] == expected
    assert import_step.data["remote_capsule_path"] == expected
    assert "C:\\" not in import_step.data["capsule_path"]

    copy_step = next(step for step in result.steps if step.name == "copy_capsule_to_droplet")
    assert copy_step.data["remote_capsule_path"] == expected
