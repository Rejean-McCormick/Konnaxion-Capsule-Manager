from __future__ import annotations

from typing import Any

from kx_manager.services import operation_jobs


def test_publish_packaged_universes_discovers_and_applies_all_manifests() -> None:
    seen: dict[str, Any] = {}

    class Client:
        def _ssh(
            self,
            payload: Any,
            remote_command: str,
            *,
            timeout_seconds: int,
            success_message: str,
        ) -> dict[str, Any]:
            seen["payload"] = payload
            seen["command"] = remote_command
            seen["timeout"] = timeout_seconds
            seen["success_message"] = success_message
            return {
                "ok": True,
                "returncode": 0,
                "stdout": "KX_UNIVERSE_APPLY=levis@0.5.0\nKX_UNIVERSE_COUNT=4\n",
            }

    result = operation_jobs._publish_packaged_universes(
        Client(),
        {"droplet_user": "kx-admin"},
        instance_id="konnaxion-prod",
        remote_root="/opt/konnaxion",
    )

    assert result["ok"] is True
    assert result["universe_count"] == 4
    assert "seed-data/universes" in seen["command"]
    assert "worlds_apply_universe" in seen["command"]
    assert "--pack-version" in seen["command"]
    assert "--promote" in seen["command"]
    assert "--promote </dev/null" in seen["command"]
    assert "worlds_health" in seen["command"]
    assert seen["timeout"] == 3600


def test_publish_packaged_universes_is_supported_long_operation() -> None:
    assert "publish_packaged_universes" in operation_jobs.SUPPORTED_ACTIONS
