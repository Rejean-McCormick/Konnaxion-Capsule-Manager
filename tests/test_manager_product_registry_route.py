"""Manager proxy contract for Capsule public product discovery."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from kx_manager.routes.registry import router


class FakeAgent:
    async def product_registry(self):
        return {
            "schema_version": "kx-product-registry/v1",
            "generation": 43,
            "products": [
                {
                    "id": "orgo",
                    "version": "1.4.0",
                    "state": "functional",
                    "standalone": {"available": True, "entrypoint": "/"},
                    "integrated": {
                        "available": False,
                        "manifest": None,
                        "contract": None,
                    },
                }
            ],
        }


def test_manager_exposes_agent_product_projection_without_reinterpreting_it() -> None:
    app = FastAPI()
    app.state.agent_client = FakeAgent()
    app.include_router(router)

    response = TestClient(app).get("/registry/products")

    assert response.status_code == 200
    payload = response.json()
    assert payload["generation"] == 43
    assert payload["products"][0]["id"] == "orgo"
    assert payload["products"][0]["integrated"]["available"] is False


def test_manager_agent_client_translates_registry_projection_path() -> None:
    from kx_manager.client import translate_request

    translated = translate_request("GET", "/registry/products")

    assert translated.method == "GET"
    assert translated.path == "/registry/products"
    assert translated.payload == {}
