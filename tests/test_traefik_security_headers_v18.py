from __future__ import annotations

from kx_agent.runtime.compose import render_traefik_dynamic_config


def _config() -> dict:
    return render_traefik_dynamic_config(
        "konnaxion.com",
        instance_id="konnaxion-prod",
        cert_resolver="letsencrypt",
    )


def test_all_public_routers_attach_secure_headers() -> None:
    config = _config()
    routers = config["http"]["routers"]
    assert set(routers) >= {"kx-frontend", "kx-api", "kx-admin", "kx-media"}
    for name in ("kx-frontend", "kx-api", "kx-admin", "kx-media"):
        assert "secure-headers" in routers[name]["middlewares"]


def test_secure_headers_satisfy_external_baseline() -> None:
    headers = _config()["http"]["middlewares"]["secure-headers"]["headers"]
    assert headers["stsSeconds"] >= 31536000
    assert headers["contentTypeNosniff"] is True
    assert headers["frameDeny"] is True
    assert headers["referrerPolicy"] == "strict-origin-when-cross-origin"
    assert "frame-ancestors 'none'" in headers["contentSecurityPolicy"]
    assert headers["permissionsPolicy"]


def test_edge_removes_next_powered_by_banner() -> None:
    headers = _config()["http"]["middlewares"]["secure-headers"]["headers"]
    assert headers["customResponseHeaders"]["X-Powered-By"] == ""


def test_hsts_preload_and_subdomain_flags_are_not_forced() -> None:
    headers = _config()["http"]["middlewares"]["secure-headers"]["headers"]
    assert "stsPreload" not in headers
    assert "stsIncludeSubdomains" not in headers
