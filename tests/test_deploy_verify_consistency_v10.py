from pathlib import Path
from types import SimpleNamespace

import kx_manager.services.deploy as deploy


def test_deploy_verify_uses_launcher_public_key(monkeypatch, tmp_path: Path) -> None:
    capsule = tmp_path / "konnaxion-v14-local-2026.09.09.kxcap"
    capsule.write_bytes(b"kxcap")
    public_key = tmp_path / "kx-demo-ed25519-public.pem"
    public_key.write_text("public-key", encoding="utf-8")

    seen = {}

    class VerifyRequest:
        def __init__(self, capsule_file, public_key_file=None, **kwargs):
            self.capsule_file = capsule_file
            self.public_key_file = public_key_file

    def fake_verify(request):
        seen["capsule_file"] = request.capsule_file
        seen["public_key_file"] = request.public_key_file
        return SimpleNamespace(
            ok=True,
            capsule_file=Path(request.capsule_file),
            command=SimpleNamespace(
                ok=True,
                returncode=0,
                stdout="OK: Capsule verified.",
                stderr="",
            ),
        )

    fake_builder = SimpleNamespace(
        VerifyCapsuleRequest=VerifyRequest,
        verify_capsule=fake_verify,
        DEFAULT_WINDOWS_PUBLIC_KEY_FILE=Path("missing-default.pem"),
    )

    monkeypatch.setenv("KX_CAPSULE_PUBLIC_KEY_FILE", str(public_key))
    monkeypatch.setattr(deploy, "_import_builder_service", lambda required=False: fake_builder)

    result = deploy.DeployResult(
        ok=False,
        action="deploy_droplet",
        instance_id="konnaxion-prod",
        message="",
    )
    deploy._verify_capsule(capsule, result)

    assert seen["capsule_file"] == capsule
    assert seen["public_key_file"] == public_key
    assert result.steps[-1].ok is True
    assert result.steps[-1].name == "verify_capsule"


def test_deploy_verify_failure_preserves_builder_reason(monkeypatch, tmp_path: Path) -> None:
    capsule = tmp_path / "konnaxion-v14-local-2026.09.09.kxcap"
    capsule.write_bytes(b"kxcap")

    class FailedResult:
        ok = False
        def to_dict(self):
            return {
                "ok": False,
                "command": {
                    "returncode": 7,
                    "stdout": "verification stdout detail",
                    "stderr": "verification stderr detail",
                },
            }

    fake_builder = SimpleNamespace(
        verify_capsule=lambda path: FailedResult(),
        DEFAULT_WINDOWS_PUBLIC_KEY_FILE=Path("missing-default.pem"),
    )
    monkeypatch.delenv("KX_CAPSULE_PUBLIC_KEY_FILE", raising=False)
    monkeypatch.delenv("KX_BUILDER_PUBLIC_KEY_FILE", raising=False)
    monkeypatch.setattr(deploy, "_import_builder_service", lambda required=False: fake_builder)

    result = deploy.DeployResult(
        ok=False,
        action="deploy_droplet",
        instance_id="konnaxion-prod",
        message="",
    )

    try:
        deploy._verify_capsule(capsule, result)
    except deploy.DeployExecutionError as exc:
        text = str(exc)
    else:
        raise AssertionError("expected DeployExecutionError")

    assert "returncode=7" in text
    assert "verification stderr detail" in text
    assert result.steps[-1].ok is False
