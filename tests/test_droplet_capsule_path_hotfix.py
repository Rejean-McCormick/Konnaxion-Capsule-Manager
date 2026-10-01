from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from kx_agent.actions import _capsule_id_from_path
from kx_manager.ui.agent_execution_client import _AgentHttpExecutionClient


class RecordingClient(_AgentHttpExecutionClient):
    def __init__(self, *, droplet_payload: Mapping[str, Any] | None = None) -> None:
        super().__init__(base_url="http://127.0.0.1:8765/v1", droplet_payload=droplet_payload)
        self.last_path: str | None = None
        self.last_payload: dict[str, Any] | None = None

    def _post(self, path: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        self.last_path = path
        self.last_payload = dict(payload)
        return {"ok": True, "message": "ok"}


def test_droplet_update_never_forwards_windows_local_capsule_path() -> None:
    client = RecordingClient(
        droplet_payload={
            "capsule_file": r"C:\\mycode\\Konnaxion\\runtime\\capsules\\konnaxion-v14-release-2026.09.30.kxcap",
            "capsule_path": r"C:\\mycode\\Konnaxion\\runtime\\capsules\\konnaxion-v14-release-2026.09.30.kxcap",
            "remote_capsule_dir": "/opt/konnaxion/capsules",
        }
    )

    result = client.update_instance(instance_id="konnaxion-prod")

    assert result["ok"] is True
    assert client.last_path == "/instances/update"
    assert client.last_payload is not None
    assert client.last_payload["capsule_path"].startswith("/opt/konnaxion/capsules/")
    assert "C:\\" not in client.last_payload["capsule_path"]


def test_droplet_update_preserves_explicit_posix_remote_capsule_path(tmp_path: Path) -> None:
    local = tmp_path / "release.kxcap"
    client = RecordingClient(
        droplet_payload={
            "capsule_file": str(local),
            "capsule_path": str(local),
            "remote_capsule_path": "/opt/konnaxion/capsules/release.kxcap",
        }
    )

    client.update_instance(instance_id="konnaxion-prod")

    assert client.last_payload is not None
    assert client.last_payload["capsule_path"] == "/opt/konnaxion/capsules/release.kxcap"


def test_agent_derives_safe_capsule_id_from_windows_path() -> None:
    capsule_id = _capsule_id_from_path(
        {
            "capsule_path": r"C:\\mycode\\Konnaxion\\runtime\\capsules\\konnaxion-v14-release-2026.09.30.kxcap"
        }
    )

    assert capsule_id == "konnaxion-v14-release-2026.09.30"
