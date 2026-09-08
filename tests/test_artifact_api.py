from __future__ import annotations

from kx_agent.actions import ALLOWLISTED_ACTIONS, API_ACTION_ALIASES
from kx_agent.api import create_app as create_agent_app
from kx_manager.routes.artifacts import router as manager_artifact_router
from kx_manager.client import translate_request


def _routes(app) -> set[tuple[str, str]]:
    result: set[tuple[str, str]] = set()
    for route in app.routes:
        path = getattr(route, "path", "")
        for method in getattr(route, "methods", set()) or set():
            result.add((method, path))
    return result


def test_agent_exposes_artifact_registry_discovery_and_remove() -> None:
    routes = _routes(create_agent_app())
    assert ("GET", "/v1/artifacts") in routes
    assert ("GET", "/v1/artifacts/{artifact_id}") in routes
    assert ("GET", "/v1/artifacts/{artifact_id}/integration-manifest") in routes
    assert ("POST", "/v1/artifacts/remove") in routes


def test_manager_exposes_public_artifact_projection() -> None:
    class RouterApp:
        routes = manager_artifact_router.routes

    routes = _routes(RouterApp())
    assert ("GET", "/v1/artifacts") in routes
    assert ("GET", "/v1/artifacts/{artifact_id}") in routes
    assert ("GET", "/v1/artifacts/{artifact_id}/integration-manifest") in routes
    assert ("POST", "/v1/artifacts/{artifact_id}/remove") in routes


def test_artifact_remove_is_allowlisted_agent_action() -> None:
    assert API_ACTION_ALIASES["artifact_remove"] == "artifact.remove"
    assert "artifact.remove" in ALLOWLISTED_ACTIONS


def test_manager_client_translates_artifact_routes_without_private_ux_knowledge() -> None:
    list_request = translate_request("GET", "/v1/artifacts", params={"products_only": True})
    manifest_request = translate_request("GET", "/v1/artifacts/orgo/integration-manifest")
    remove_request = translate_request(
        "POST",
        "/v1/artifacts/remove",
        json={"artifact_id": "orgo", "preserve_data": True},
    )

    assert list_request.path == "/artifacts"
    assert list_request.params["products_only"] is True
    assert manifest_request.path == "/artifacts/orgo/integration-manifest"
    assert remove_request.path == "/artifacts/remove"
    assert remove_request.payload == {"artifact_id": "orgo", "preserve_data": True}
