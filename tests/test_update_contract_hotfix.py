from __future__ import annotations

import json

from kx_agent.api import InstanceUpdateRequest
from kx_manager.ui.agent_execution_client import _agent_response_from_ssh_result


def test_instance_update_request_accepts_manager_backup_flag() -> None:
    request = InstanceUpdateRequest.model_validate(
        {
            "instance_id": "konnaxion-prod",
            "capsule_path": "/opt/konnaxion/capsules/release.kxcap",
            "create_pre_update_backup": True,
        }
    )
    assert request.create_pre_update_backup is True


def test_ssh_agent_validation_error_surfaces_fastapi_detail() -> None:
    payload = {
        "detail": [
            {
                "type": "extra_forbidden",
                "loc": ["body", "create_pre_update_backup"],
                "msg": "Extra inputs are not permitted",
            }
        ]
    }
    result = _agent_response_from_ssh_result(
        {
            "ok": False,
            "message": "Command failed.",
            "stdout": json.dumps(payload),
            "stderr": "curl: (22) The requested URL returned error: 422",
            "returncode": 22,
        },
        method="POST",
        path="/instances/update",
        agent_health_url="http://127.0.0.1:8765/v1/health",
    )

    assert result["ok"] is False
    assert "create_pre_update_backup" in result["message"]
    assert "Extra inputs are not permitted" in result["message"]
