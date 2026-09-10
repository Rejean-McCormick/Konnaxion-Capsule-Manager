from kx_agent.api import InstanceStartRequest, NetworkSetProfileRequest, model_payload


def test_network_set_profile_accepts_current_manager_capsule_identity_fields() -> None:
    request = NetworkSetProfileRequest.model_validate(
        {
            "instance_id": "konnaxion-prod",
            "network_profile": "public_vps",
            "exposure_mode": "public",
            "public_mode_enabled": True,
            "host": "2.56.97.41.sslip.io",
            "capsule_id": "konnaxion-v14-local-2026.09.09",
            "capsule_version": "2026.09.09-local.1",
        }
    )

    payload = model_payload(request)
    assert payload["host"] == "2.56.97.41.sslip.io"
    assert payload["capsule_id"] == "konnaxion-v14-local-2026.09.09"
    assert payload["capsule_version"] == "2026.09.09-local.1"


def test_instance_start_accepts_current_manager_capsule_identity_fields() -> None:
    request = InstanceStartRequest.model_validate(
        {
            "instance_id": "konnaxion-prod",
            "run_security_gate": True,
            "capsule_id": "konnaxion-v14-local-2026.09.09",
            "capsule_version": "2026.09.09-local.1",
            "force_recreate_after_image_load": True,
        }
    )

    payload = model_payload(request)
    assert payload["capsule_id"] == "konnaxion-v14-local-2026.09.09"
    assert payload["capsule_version"] == "2026.09.09-local.1"
    assert payload["force_recreate_after_image_load"] is True
