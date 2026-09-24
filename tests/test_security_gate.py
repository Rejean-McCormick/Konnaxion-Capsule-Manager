"""Security Gate tests for Konnaxion Agent.

These tests enforce the canonical Security Gate contract:
- mandatory checks exist
- blocking failures stop startup
- public/internal port exposure is rejected
- unsafe Docker runtime options are rejected
- signed capsules and checksums are blocking requirements
- runtime Security Gate context must use real capsule manifest data
- runtime Security Gate context must use generated instance secrets
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Any, Mapping

import pytest

from kx_shared.konnaxion_constants import (
    BLOCKING_SECURITY_CHECKS,
    FORBIDDEN_PUBLIC_PORTS,
    SecurityGateCheck,
    SecurityGateStatus,
)


MANDATORY_CHECKS = {
    SecurityGateCheck.CAPSULE_SIGNATURE,
    SecurityGateCheck.IMAGE_CHECKSUMS,
    SecurityGateCheck.MANIFEST_SCHEMA,
    SecurityGateCheck.SECRETS_PRESENT,
    SecurityGateCheck.SECRETS_NOT_DEFAULT,
    SecurityGateCheck.FIREWALL_ENABLED,
    SecurityGateCheck.DANGEROUS_PORTS_BLOCKED,
    SecurityGateCheck.POSTGRES_NOT_PUBLIC,
    SecurityGateCheck.REDIS_NOT_PUBLIC,
    SecurityGateCheck.DOCKER_SOCKET_NOT_MOUNTED,
    SecurityGateCheck.NO_PRIVILEGED_CONTAINERS,
    SecurityGateCheck.NO_HOST_NETWORK,
    SecurityGateCheck.ALLOWED_IMAGES_ONLY,
    SecurityGateCheck.ADMIN_SURFACE_PRIVATE,
    SecurityGateCheck.BACKUP_CONFIGURED,
}


@dataclass(frozen=True)
class FakeSecurityCheckResult:
    """Fallback test fixture matching the expected SecurityCheckResult shape."""

    check: SecurityGateCheck
    status: SecurityGateStatus
    message: str = ""


def test_security_gate_status_values_are_canonical() -> None:
    """Security Gate statuses must remain aligned with DOC-00."""

    assert {status.value for status in SecurityGateStatus} == {
        "PASS",
        "WARN",
        "FAIL_BLOCKING",
        "SKIPPED",
        "UNKNOWN",
    }


def test_mandatory_security_gate_checks_are_canonical() -> None:
    """All canonical Security Gate checks must be present."""

    assert {check.value for check in MANDATORY_CHECKS} == {
        "capsule_signature",
        "image_checksums",
        "manifest_schema",
        "secrets_present",
        "secrets_not_default",
        "firewall_enabled",
        "dangerous_ports_blocked",
        "postgres_not_public",
        "redis_not_public",
        "docker_socket_not_mounted",
        "no_privileged_containers",
        "no_host_network",
        "allowed_images_only",
        "admin_surface_private",
        "backup_configured",
    }


def test_blocking_security_gate_checks_are_canonical() -> None:
    """Critical checks must block startup when they fail."""

    assert BLOCKING_SECURITY_CHECKS == frozenset(
        {
            SecurityGateCheck.CAPSULE_SIGNATURE,
            SecurityGateCheck.IMAGE_CHECKSUMS,
            SecurityGateCheck.MANIFEST_SCHEMA,
            SecurityGateCheck.SECRETS_PRESENT,
            SecurityGateCheck.SECRETS_NOT_DEFAULT,
            SecurityGateCheck.DANGEROUS_PORTS_BLOCKED,
            SecurityGateCheck.POSTGRES_NOT_PUBLIC,
            SecurityGateCheck.REDIS_NOT_PUBLIC,
            SecurityGateCheck.DOCKER_SOCKET_NOT_MOUNTED,
            SecurityGateCheck.NO_PRIVILEGED_CONTAINERS,
            SecurityGateCheck.NO_HOST_NETWORK,
            SecurityGateCheck.ALLOWED_IMAGES_ONLY,
        }
    )


def test_forbidden_public_ports_are_canonical() -> None:
    """Internal service ports must never be exposed publicly."""

    assert FORBIDDEN_PUBLIC_PORTS == frozenset({3000, 5000, 5432, 6379, 5555, 8000})


@pytest.mark.parametrize(
    ("check", "expected_blocking"),
    [
        (SecurityGateCheck.CAPSULE_SIGNATURE, True),
        (SecurityGateCheck.IMAGE_CHECKSUMS, True),
        (SecurityGateCheck.MANIFEST_SCHEMA, True),
        (SecurityGateCheck.SECRETS_PRESENT, True),
        (SecurityGateCheck.SECRETS_NOT_DEFAULT, True),
        (SecurityGateCheck.FIREWALL_ENABLED, False),
        (SecurityGateCheck.DANGEROUS_PORTS_BLOCKED, True),
        (SecurityGateCheck.POSTGRES_NOT_PUBLIC, True),
        (SecurityGateCheck.REDIS_NOT_PUBLIC, True),
        (SecurityGateCheck.DOCKER_SOCKET_NOT_MOUNTED, True),
        (SecurityGateCheck.NO_PRIVILEGED_CONTAINERS, True),
        (SecurityGateCheck.NO_HOST_NETWORK, True),
        (SecurityGateCheck.ALLOWED_IMAGES_ONLY, True),
        (SecurityGateCheck.ADMIN_SURFACE_PRIVATE, False),
        (SecurityGateCheck.BACKUP_CONFIGURED, False),
    ],
)
def test_blocking_check_membership(
    check: SecurityGateCheck,
    expected_blocking: bool,
) -> None:
    """Each check must have the expected blocking behavior."""

    assert (check in BLOCKING_SECURITY_CHECKS) is expected_blocking


def test_security_gate_module_exports_expected_api() -> None:
    """kx_agent.security.gate must expose the expected public functions/classes."""

    gate = importlib.import_module("kx_agent.security.gate")

    for name in (
        "SecurityGateResult",
        "SecurityCheckResult",
        "run_security_gate",
        "is_security_gate_passing",
        "assert_security_gate_passing",
        "context_from_compose",
    ):
        assert hasattr(gate, name), f"kx_agent.security.gate missing {name}"


def test_security_checks_module_exports_expected_api() -> None:
    """kx_agent.security.checks must expose individual check helpers."""

    checks = importlib.import_module("kx_agent.security.checks")

    for name in (
        "check_capsule_signature",
        "check_image_checksums",
        "check_manifest_schema",
        "check_secrets_present",
        "check_secrets_not_default",
        "check_dangerous_ports_blocked",
        "check_postgres_not_public",
        "check_redis_not_public",
        "check_docker_socket_not_mounted",
        "check_no_privileged_containers",
        "check_no_host_network",
        "check_allowed_images_only",
    ):
        assert hasattr(checks, name), f"kx_agent.security.checks missing {name}"


def test_security_gate_passes_when_all_checks_pass() -> None:
    """A gate with only PASS/WARN/SKIPPED checks should be considered passing."""

    gate = importlib.import_module("kx_agent.security.gate")

    results = [
        _make_check_result(
            gate,
            SecurityGateCheck.CAPSULE_SIGNATURE,
            SecurityGateStatus.PASS,
        ),
        _make_check_result(
            gate,
            SecurityGateCheck.FIREWALL_ENABLED,
            SecurityGateStatus.WARN,
        ),
        _make_check_result(
            gate,
            SecurityGateCheck.ADMIN_SURFACE_PRIVATE,
            SecurityGateStatus.SKIPPED,
        ),
    ]

    gate_result = _make_gate_result(gate, results)

    assert gate.is_security_gate_passing(gate_result) is True


def test_security_gate_fails_when_blocking_check_fails() -> None:
    """A FAIL_BLOCKING result for a blocking check must fail the gate."""

    gate = importlib.import_module("kx_agent.security.gate")

    results = [
        _make_check_result(
            gate,
            SecurityGateCheck.CAPSULE_SIGNATURE,
            SecurityGateStatus.FAIL_BLOCKING,
            "unsigned capsule",
        )
    ]

    gate_result = _make_gate_result(gate, results)

    assert gate.is_security_gate_passing(gate_result) is False

    with pytest.raises(Exception):
        gate.assert_security_gate_passing(gate_result)


def test_security_gate_fails_when_blocking_check_is_unknown() -> None:
    """UNKNOWN on a blocking check should not allow startup."""

    gate = importlib.import_module("kx_agent.security.gate")

    results = [
        _make_check_result(
            gate,
            SecurityGateCheck.IMAGE_CHECKSUMS,
            SecurityGateStatus.UNKNOWN,
            "checksum state unavailable",
        )
    ]

    gate_result = _make_gate_result(gate, results)

    assert gate.is_security_gate_passing(gate_result) is False


def test_security_gate_allows_warning_on_nonblocking_check() -> None:
    """WARN on a non-blocking check should not fail the entire gate."""

    gate = importlib.import_module("kx_agent.security.gate")

    results = [
        _make_check_result(
            gate,
            SecurityGateCheck.CAPSULE_SIGNATURE,
            SecurityGateStatus.PASS,
        ),
        _make_check_result(
            gate,
            SecurityGateCheck.FIREWALL_ENABLED,
            SecurityGateStatus.WARN,
            "firewall status could not be fully confirmed",
        ),
    ]

    gate_result = _make_gate_result(gate, results)

    assert gate.is_security_gate_passing(gate_result) is True


def test_security_gate_context_passes_with_real_manifest_and_generated_secrets() -> None:
    """A realistic deploy/start context should pass manifest and secret checks."""

    gate = importlib.import_module("kx_agent.security.gate")

    context = _make_security_context(
        gate,
        compose=_safe_public_vps_compose(),
        manifest=_valid_manifest(),
        env=_valid_runtime_env(),
    )

    result = gate.run_security_gate(context)
    results = _results_by_check(result)

    assert gate.is_security_gate_passing(result) is True
    assert _status_value(results["manifest_schema"]) == SecurityGateStatus.PASS.value
    assert _status_value(results["secrets_present"]) == SecurityGateStatus.PASS.value
    assert _status_value(results["secrets_not_default"]) == SecurityGateStatus.PASS.value


def test_security_gate_context_fails_with_empty_manifest_and_empty_env() -> None:
    """Regression: callers must not run Security Gate with manifest={} and env={}."""

    gate = importlib.import_module("kx_agent.security.gate")

    context = _make_security_context(
        gate,
        compose=_safe_public_vps_compose(),
        manifest={},
        env={},
    )

    result = gate.run_security_gate(context)
    results = _results_by_check(result)

    assert gate.is_security_gate_passing(result) is False
    assert _status_value(results["manifest_schema"]) == SecurityGateStatus.FAIL_BLOCKING.value
    assert _status_value(results["secrets_present"]) == SecurityGateStatus.FAIL_BLOCKING.value
    assert _status_value(results["secrets_not_default"]) == SecurityGateStatus.FAIL_BLOCKING.value

    manifest_details = _details(results["manifest_schema"])
    secret_details = _details(results["secrets_present"])

    assert "schema_version" in set(manifest_details.get("missing_fields", []))
    assert "DATABASE_URL" in set(secret_details.get("missing", []))
    assert "DJANGO_SECRET_KEY" in set(secret_details.get("missing", []))
    assert "POSTGRES_PASSWORD" in set(secret_details.get("missing", []))


def test_security_gate_context_fails_with_placeholder_secrets() -> None:
    """Runtime env must contain generated non-placeholder secrets."""

    gate = importlib.import_module("kx_agent.security.gate")

    context = _make_security_context(
        gate,
        compose=_safe_public_vps_compose(),
        manifest=_valid_manifest(),
        env={
            "DJANGO_SECRET_KEY": "<GENERATED_ON_INSTALL>",
            "POSTGRES_USER": "konnaxion",
            "POSTGRES_PASSWORD": "<POSTGRES_PASSWORD>",
            "POSTGRES_DB": "konnaxion",
            "DATABASE_URL": "postgres://konnaxion:<POSTGRES_PASSWORD>@postgres:5432/konnaxion",
            "REDIS_URL": "redis://redis:6379/0",
            "KX_INSTANCE_ID": "demo-001",
            "KX_NETWORK_PROFILE": "public_vps",
            "KX_EXPOSURE_MODE": "public",
        },
    )

    result = gate.run_security_gate(context)
    results = _results_by_check(result)

    assert gate.is_security_gate_passing(result) is False
    assert _status_value(results["manifest_schema"]) == SecurityGateStatus.PASS.value
    assert _status_value(results["secrets_present"]) == SecurityGateStatus.PASS.value
    assert _status_value(results["secrets_not_default"]) == SecurityGateStatus.FAIL_BLOCKING.value

    details = _details(results["secrets_not_default"])
    invalid_keys = set(details.get("invalid_keys", []))

    assert "DJANGO_SECRET_KEY" in invalid_keys
    assert "POSTGRES_PASSWORD" in invalid_keys
    assert "DATABASE_URL" in invalid_keys


def test_security_gate_context_rejects_missing_manifest_fields() -> None:
    """The manifest check must report required missing capsule metadata."""

    gate = importlib.import_module("kx_agent.security.gate")

    incomplete_manifest = {
        "runtime": {
            "compose_file": "docker-compose.capsule.yml",
            "images_dir": "images",
        },
    }

    context = _make_security_context(
        gate,
        compose=_safe_public_vps_compose(),
        manifest=incomplete_manifest,
        env=_valid_runtime_env(),
    )

    result = gate.run_security_gate(context)
    results = _results_by_check(result)

    assert gate.is_security_gate_passing(result) is False
    assert _status_value(results["manifest_schema"]) == SecurityGateStatus.FAIL_BLOCKING.value
    assert _status_value(results["secrets_present"]) == SecurityGateStatus.PASS.value
    assert _status_value(results["secrets_not_default"]) == SecurityGateStatus.PASS.value

    details = _details(results["manifest_schema"])
    missing = set(details.get("missing_fields", []))

    assert {
        "schema_version",
        "app_name",
        "app_version",
        "capsule_id",
        "capsule_version",
        "channel",
    }.issubset(missing)


def test_security_gate_context_rejects_public_postgres_port() -> None:
    """PostgreSQL must never be exposed publicly in rendered Compose."""

    gate = importlib.import_module("kx_agent.security.gate")

    compose = _safe_public_vps_compose()
    compose["services"]["postgres"]["ports"] = ["5432:5432"]

    context = _make_security_context(
        gate,
        compose=compose,
        manifest=_valid_manifest(),
        env=_valid_runtime_env(),
        postgres_public=True,
    )

    result = gate.run_security_gate(context)
    results = _results_by_check(result)

    assert gate.is_security_gate_passing(result) is False
    assert _status_value(results["dangerous_ports_blocked"]) == SecurityGateStatus.FAIL_BLOCKING.value
    assert _status_value(results["postgres_not_public"]) == SecurityGateStatus.FAIL_BLOCKING.value


def test_security_gate_context_rejects_public_redis_port() -> None:
    """Redis must never be exposed publicly in rendered Compose."""

    gate = importlib.import_module("kx_agent.security.gate")

    compose = _safe_public_vps_compose()
    compose["services"]["redis"]["ports"] = ["6379:6379"]

    context = _make_security_context(
        gate,
        compose=compose,
        manifest=_valid_manifest(),
        env=_valid_runtime_env(),
        redis_public=True,
    )

    result = gate.run_security_gate(context)
    results = _results_by_check(result)

    assert gate.is_security_gate_passing(result) is False
    assert _status_value(results["dangerous_ports_blocked"]) == SecurityGateStatus.FAIL_BLOCKING.value
    assert _status_value(results["redis_not_public"]) == SecurityGateStatus.FAIL_BLOCKING.value


@pytest.mark.parametrize(
    "port",
    [3000, 5000, 5432, 6379, 5555, 8000],
)
def test_dangerous_public_ports_are_rejected(port: int) -> None:
    """Dangerous public ports must be rejected by port policy checks."""

    ports = importlib.import_module("kx_agent.security.ports")

    assert hasattr(ports, "is_forbidden_public_port")
    assert ports.is_forbidden_public_port(port) is True


@pytest.mark.parametrize(
    "port",
    [80, 443],
)
def test_entry_ports_are_not_dangerous_public_ports(port: int) -> None:
    """Canonical entry ports may be exposed through Traefik."""

    ports = importlib.import_module("kx_agent.security.ports")

    assert hasattr(ports, "is_forbidden_public_port")
    assert ports.is_forbidden_public_port(port) is False


def test_security_policy_rejects_docker_socket_mount() -> None:
    """Security policy checks must reject Docker socket mounts."""

    policies = importlib.import_module("kx_agent.security.policies")

    assert hasattr(policies, "validate_runtime_policy")

    unsafe_policy = {
        "services": {
            "django-api": {
                "volumes": [
                    "/var/run/docker.sock:/var/run/docker.sock",
                ],
            },
        },
    }

    with pytest.raises(Exception):
        policies.validate_runtime_policy(unsafe_policy)


def test_security_policy_rejects_privileged_containers() -> None:
    """Security policy checks must reject privileged containers."""

    policies = importlib.import_module("kx_agent.security.policies")

    unsafe_policy = {
        "services": {
            "django-api": {
                "privileged": True,
            },
        },
    }

    with pytest.raises(Exception):
        policies.validate_runtime_policy(unsafe_policy)


def test_security_policy_rejects_host_network() -> None:
    """Security policy checks must reject host networking."""

    policies = importlib.import_module("kx_agent.security.policies")

    unsafe_policy = {
        "services": {
            "django-api": {
                "network_mode": "host",
            },
        },
    }

    with pytest.raises(Exception):
        policies.validate_runtime_policy(unsafe_policy)


def _valid_manifest() -> dict[str, Any]:
    return {
        "schema_version": "kx-capsule-manifest/v1",
        "app_name": "Konnaxion",
        "app_version": "v14",
        "param_version": "kx-param-2026.04.30",
        "capsule_id": "konnaxion-v14-demo-2026.04.30",
        "capsule_version": "2026.04.30-demo.1",
        "channel": "demo",
        "profile": "public_vps",
        "runtime": {
            "compose_file": "docker-compose.capsule.yml",
            "images_dir": "images",
        },
    }


def _valid_runtime_env() -> dict[str, str]:
    # Deterministic generated-style values: long enough for the runtime policy
    # and intentionally free of Security Gate placeholder/default markers.
    postgres_password = "Kx9mP7vR2qL8sT4wY6nB3cD5fG1hJ0kM8pQ2rS7uV4xZ6aC9"
    return {
        "DJANGO_SECRET_KEY": (
            "Kx7sL9vQ2mR8tY4wN6cB3dF5gH1jK0pM8rT2uV7xZ4aC6eG9"
            "nP3qS5wY8"
        ),
        "POSTGRES_USER": "konnaxion",
        "POSTGRES_PASSWORD": postgres_password,
        "POSTGRES_DB": "konnaxion",
        "DATABASE_URL": (
            "postgres://konnaxion:"
            f"{postgres_password}"
            "@postgres:5432/konnaxion"
        ),
        "REDIS_URL": "redis://redis:6379/0",
        "KX_INSTANCE_ID": "demo-001",
        "KX_NETWORK_PROFILE": "public_vps",
        "KX_EXPOSURE_MODE": "public",
    }


def _safe_public_vps_compose() -> dict[str, Any]:
    return {
        "services": {
            "traefik": {
                "image": "traefik",
                "ports": [
                    "80:80",
                    "443:443",
                ],
                "volumes": [],
            },
            "frontend-next": {
                "image": "frontend-next",
                "expose": ["3000"],
                "depends_on": ["django-api"],
            },
            "django-api": {
                "image": "django-api",
                "expose": ["5000"],
                "depends_on": ["postgres", "redis"],
                "env_file": ["../env/runtime.env"],
            },
            "postgres": {
                "image": "postgres",
                "expose": ["5432"],
                "volumes": ["postgres-data:/var/lib/postgresql/data"],
            },
            "redis": {
                "image": "redis",
                "expose": ["6379"],
            },
            "celeryworker": {
                "image": "celeryworker",
                "depends_on": ["django-api", "redis"],
                "env_file": ["../env/runtime.env"],
            },
            "celerybeat": {
                "image": "celerybeat",
                "depends_on": ["django-api", "redis"],
                "env_file": ["../env/runtime.env"],
            },
            "media-nginx": {
                "image": "media-nginx",
                "expose": ["8080"],
            },
        },
        "volumes": {
            "postgres-data": {},
        },
        "networks": {
            "konnaxion-public": {},
            "konnaxion-internal": {
                "internal": True,
            },
        },
    }


def _make_security_context(
    gate_module: Any,
    *,
    compose: Mapping[str, Any],
    manifest: Mapping[str, Any],
    env: Mapping[str, str],
    **overrides: Any,
) -> Any:
    context_from_compose = getattr(gate_module, "context_from_compose")

    kwargs: dict[str, Any] = {
        "instance_id": "demo-001",
        "compose": compose,
        "manifest": manifest,
        "env": env,
        "capsule_signature_verified": True,
        "image_checksums_verified": True,
        "firewall_enabled": True,
        "backup_configured": True,
        "admin_surface_private": True,
        "postgres_public": False,
        "redis_public": False,
    }
    kwargs.update(overrides)

    try:
        return context_from_compose(**kwargs)
    except TypeError:
        compatibility_kwargs = dict(kwargs)
        compatibility_kwargs["capsule_signature_valid"] = compatibility_kwargs.pop(
            "capsule_signature_verified"
        )
        compatibility_kwargs["image_checksums_valid"] = compatibility_kwargs.pop(
            "image_checksums_verified"
        )
        return context_from_compose(**compatibility_kwargs)


def _results_by_check(gate_result: Any) -> dict[str, Any]:
    results = _result_items(gate_result)
    return {_check_value(item): item for item in results}


def _result_items(gate_result: Any) -> list[Any]:
    if isinstance(gate_result, Mapping):
        raw = gate_result.get("results") or gate_result.get("checks") or []
    else:
        raw = getattr(gate_result, "results", None) or getattr(gate_result, "checks", None) or []

    return list(raw)


def _check_value(result: Any) -> str:
    if isinstance(result, Mapping):
        value = result.get("check")
    else:
        value = getattr(result, "check", None)

    if hasattr(value, "value"):
        return str(value.value)

    return str(value)


def _status_value(result: Any) -> str:
    if isinstance(result, Mapping):
        value = result.get("status")
    else:
        value = getattr(result, "status", None)

    if hasattr(value, "value"):
        return str(value.value)

    return str(value)


def _details(result: Any) -> Mapping[str, Any]:
    if isinstance(result, Mapping):
        value = result.get("details", {})
    else:
        value = getattr(result, "details", {})

    if isinstance(value, Mapping):
        return value

    return {}


def _make_check_result(
    gate_module: Any,
    check: SecurityGateCheck,
    status_: SecurityGateStatus,
    message: str = "",
) -> Any:
    """Build a SecurityCheckResult using the project class when available."""

    result_class = getattr(gate_module, "SecurityCheckResult", None)
    if result_class is None:
        return FakeSecurityCheckResult(check=check, status=status_, message=message)

    try:
        return result_class(check=check, status=status_, message=message)
    except TypeError:
        return result_class(check.value, status_.value, message)


def _make_gate_result(gate_module: Any, results: list[Any]) -> Any:
    """Build a SecurityGateResult using the project class when available."""

    gate_result_class = getattr(gate_module, "SecurityGateResult", None)
    if gate_result_class is None:
        return {"results": results}

    try:
        return gate_result_class(results=results)
    except TypeError:
        return gate_result_class(checks=results)