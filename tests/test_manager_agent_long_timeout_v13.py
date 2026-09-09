from __future__ import annotations

import httpx
import pytest

from kx_manager.client import (
    AgentClientConfig,
    KonnaxionAgentClient,
    agent_request_timeout_seconds,
)


def test_instance_start_uses_long_agent_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KX_MANAGER_LONG_AGENT_TIMEOUT_SECONDS", raising=False)
    assert (
        agent_request_timeout_seconds(
            "POST",
            "/instances/start",
            base_timeout_seconds=30,
        )
        == 900
    )


def test_long_agent_timeout_can_be_overridden(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KX_MANAGER_LONG_AGENT_TIMEOUT_SECONDS", "1200")
    assert (
        agent_request_timeout_seconds(
            "POST",
            "/instances/start",
            base_timeout_seconds=30,
        )
        == 1200
    )


@pytest.mark.asyncio
async def test_client_passes_long_timeout_to_start_request() -> None:
    seen: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(
            200,
            json={
                "ok": True,
                "action": "instance_start",
                "message": "Instance started.",
                "data": {"instance_id": "demo-001"},
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1:8765/v1",
        transport=transport,
        timeout=30,
    ) as http_client:
        client = KonnaxionAgentClient(
            AgentClientConfig(
                base_url="http://127.0.0.1:8765/v1",
                timeout_seconds=30,
            ),
            http_client=http_client,
        )
        result = await client.start_instance(instance_id="demo-001")

    assert result["ok"] is True
    assert seen["url"] == "http://127.0.0.1:8765/v1/instances/start"
