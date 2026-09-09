from __future__ import annotations

import asyncio

from kx_manager.ui import action_backends


def test_create_instance_gui_forwards_local_runtime_host(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def create_instance(self, **kwargs):
            captured.update(kwargs)
            return {
                "ok": True,
                "message": "Instance created.",
                "data": {"host": kwargs.get("host")},
            }

    class FakeClientFactory:
        @classmethod
        def from_env(cls):
            return FakeClient()

    monkeypatch.setattr(action_backends, "KonnaxionAgentClient", FakeClientFactory)

    result = asyncio.run(
        action_backends._handle_create_instance(
            "create_instance",
            {
                "instance_id": "demo-001",
                "capsule_id": "konnaxion-v14-local-2026.09.08",
                "network_profile": "local_only",
                "exposure_mode": "private",
                "host": "konnaxion.local",
                "generate_secrets": "true",
            },
        )
    )

    assert captured["host"] == "konnaxion.local"
    assert captured["network_profile"] == "local_only"
    assert captured["exposure_mode"] == "private"
    assert result.ok is True


def test_create_instance_client_posts_host(monkeypatch) -> None:
    from kx_manager.client import KonnaxionAgentClient

    captured: dict[str, object] = {}

    async def fake_post(self, path, payload):
        captured["path"] = path
        captured["payload"] = payload
        return {"ok": True}

    monkeypatch.setattr(KonnaxionAgentClient, "_post", fake_post)

    client = object.__new__(KonnaxionAgentClient)
    asyncio.run(
        client.create_instance(
            instance_id="demo-001",
            capsule_id="konnaxion-v14-local-2026.09.08",
            network_profile="local_only",
            exposure_mode="private",
            host="konnaxion.local",
        )
    )

    assert captured["path"] == "/instances/create"
    assert captured["payload"]["host"] == "konnaxion.local"


def test_translate_direct_create_instance_preserves_host() -> None:
    from kx_manager.client import translate_request

    translated = translate_request(
        "POST",
        "/instances/create",
        json={
            "instance_id": "demo-001",
            "capsule_id": "konnaxion-v14-local-2026.09.08",
            "network_profile": "local_only",
            "exposure_mode": "private",
            "host": "konnaxion.local",
            "generate_secrets": True,
        },
    )

    assert translated.path == "/instances/create"
    assert translated.payload["host"] == "konnaxion.local"


def test_direct_create_instance_filter_keeps_runtime_host_fields() -> None:
    from kx_manager.client import filter_direct_payload

    payload = filter_direct_payload(
        "/instances/create",
        {
            "instance_id": "demo-001",
            "capsule_id": "konnaxion-v14-local-2026.09.08",
            "network_profile": "local_only",
            "exposure_mode": "private",
            "host": "konnaxion.local",
            "host_aliases": ["www.konnaxion.local"],
            "public_mode_enabled": False,
            "public_mode_expires_at": None,
            "generate_secrets": True,
            "display_only": "must-be-dropped",
        },
    )

    assert payload["host"] == "konnaxion.local"
    assert payload["host_aliases"] == ["www.konnaxion.local"]
    assert payload["public_mode_enabled"] is False
    assert "public_mode_expires_at" in payload
    assert "display_only" not in payload
