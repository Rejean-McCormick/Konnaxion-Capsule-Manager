from __future__ import annotations

from kx_agent.instances.secrets import (
    SecretGenerationPolicy,
    build_env_files,
    generate_secret_bundle,
)
from kx_agent.runtime.compose import render_traefik_dynamic_config
from kx_manager.ui.agent_execution_client import _public_host_aliases_from_payload


ALIASES = (
    "unesco.konnaxion.com",
    "levis.konnaxion.com",
    "kristal-farms.konnaxion.com",
    "cuba-2026.konnaxion.com",
)


def test_agent_payload_aliases_merge_explicit_universe_hosts_and_www(monkeypatch):
    monkeypatch.setenv("KX_DROPLET_HOST_ALIASES", ",".join(ALIASES))
    aliases = _public_host_aliases_from_payload({}, "konnaxion.com")
    assert aliases == [*ALIASES, "www.konnaxion.com"]


def test_generated_env_supports_same_origin_api_and_universe_host_routing():
    policy = SecretGenerationPolicy(
        instance_id="konnaxion-prod",
        host="konnaxion.com",
        network_profile="public_vps",
        exposure_mode="public",
        host_aliases=ALIASES,
    )
    bundle = generate_secret_bundle(policy)
    files = build_env_files(bundle, policy)

    django = files["django.env"]
    frontend = files["frontend.env"]
    runtime = files["runtime.env"]
    kx = files["kx.env"]

    for alias in ALIASES:
        assert alias in django["DJANGO_ALLOWED_HOSTS"]
        assert f"https://{alias}" in django["DJANGO_CSRF_TRUSTED_ORIGINS"]

    assert kx["KX_HOST_ALIASES"] == ",".join(("www.konnaxion.com", *ALIASES))
    assert django["KONNAXION_UNIVERSE_HOST_ROUTING_ENABLED"] == "true"
    assert django["KONNAXION_UNIVERSE_BASE_DOMAINS"] == "konnaxion.com"
    assert frontend["NEXT_PUBLIC_API_BASE"] == "/api"
    assert frontend["NEXT_PUBLIC_KONNAXION_UNIVERSE_BASE_DOMAIN"] == "konnaxion.com"
    assert runtime["NEXT_PUBLIC_API_BASE"] == "/api"


def test_traefik_routes_explicit_universe_hosts_with_normal_acme():
    config = render_traefik_dynamic_config(
        "konnaxion.com",
        instance_id="konnaxion-prod",
        host_aliases=ALIASES,
        cert_resolver="letsencrypt",
    )
    rule = config["http"]["routers"]["kx-frontend"]["rule"]

    assert "Host(`konnaxion.com`)" in rule
    assert "Host(`www.konnaxion.com`)" in rule
    for alias in ALIASES:
        assert f"Host(`{alias}`)" in rule

    assert config["http"]["routers"]["kx-frontend"]["tls"]["certResolver"] == "letsencrypt"
