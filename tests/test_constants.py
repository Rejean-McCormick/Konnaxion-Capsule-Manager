"""
Tests for canonical Konnaxion constants.

These tests prevent drift across Manager, Agent, Builder, CLI, templates,
Docker Compose generation, Security Gate logic, backup logic, and UI code.

Run with:

    pytest tests/test_constants.py
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kx_shared.konnaxion_constants import (
    AGENT_NAME,
    ALLOWED_ENTRY_PORTS,
    APP_VERSION,
    BLOCKING_SECURITY_CHECKS,
    BUILDER_NAME,
    CANONICAL_DOCKER_SERVICES,
    CAPSULE_EXTENSION,
    CAPSULE_FILENAME_PATTERN,
    DEFAULT_CAPSULE_ID,
    DEFAULT_CAPSULE_VERSION,
    DEFAULT_CHANNEL,
    DEFAULT_EXPOSURE_MODE,
    DEFAULT_INSTANCE_ID,
    DEFAULT_NETWORK_PROFILE,
    DEFAULT_PUBLIC_MODE_ENABLED,
    DEFAULT_RELEASE_ID,
    DockerService,
    ExposureMode,
    FORBIDDEN_PUBLIC_PORTS,
    INSTANCE_NAME,
    INTERNAL_ONLY_PORTS,
    KX_AGENT_DIR,
    KX_BACKUPS_ROOT,
    KX_CAPSULES_DIR,
    KX_ENV_DEFAULTS,
    KX_INSTANCES_DIR,
    KX_MANAGER_DIR,
    KX_RELEASES_DIR,
    KX_ROOT,
    KX_SHARED_DIR,
    MANAGER_NAME,
    PARAM_VERSION,
    PRODUCT_NAME,
    PUBLIC_CLI_COMMANDS,
    ROUTES,
    BackupStatus,
    InstanceState,
    NetworkProfile,
    RestoreStatus,
    RollbackStatus,
    SecurityGateCheck,
    SecurityGateStatus,
    capsule_path,
    instance_backup_root,
    instance_compose_file,
    instance_env_dir,
    instance_root,
    instance_state_dir,
)


FORBIDDEN_SERVICE_ALIASES = {
    "backend",
    "api",
    "web",
    "next",
    "frontend",
    "db",
    "database",
    "cache",
    "worker",
    "scheduler",
    "media",
    "agent",
}


def enum_values(enum_type: type) -> set[str]:
    return {item.value for item in enum_type}


def test_product_identity_constants_are_canonical() -> None:
    assert PRODUCT_NAME == "Konnaxion"
    assert APP_VERSION == "v14"
    assert PARAM_VERSION == "kx-param-2026.04.30"

    assert CAPSULE_EXTENSION == ".kxcap"
    assert CAPSULE_FILENAME_PATTERN == "konnaxion-v14-{channel}-{date}.kxcap"

    assert DEFAULT_CHANNEL == "demo"
    assert DEFAULT_CAPSULE_ID == "konnaxion-v14-demo-2026.04.30"
    assert DEFAULT_CAPSULE_VERSION == "2026.04.30-demo.1"

    assert MANAGER_NAME == "Konnaxion Capsule Manager"
    assert AGENT_NAME == "Konnaxion Agent"
    assert BUILDER_NAME == "Konnaxion Capsule Builder"
    assert INSTANCE_NAME == "Konnaxion Instance"


def test_canonical_paths_are_under_opt_konnaxion() -> None:
    assert KX_ROOT == Path("/opt/konnaxion")

    expected_dirs = {
        KX_CAPSULES_DIR,
        KX_INSTANCES_DIR,
        KX_SHARED_DIR,
        KX_RELEASES_DIR,
        KX_MANAGER_DIR,
        KX_AGENT_DIR,
        KX_BACKUPS_ROOT,
    }

    for path in expected_dirs:
        assert path.is_absolute()
        assert path == KX_ROOT / path.relative_to(KX_ROOT)


def test_instance_path_helpers_are_canonical() -> None:
    instance_id = "demo-001"

    assert DEFAULT_INSTANCE_ID == instance_id
    assert DEFAULT_RELEASE_ID == "20260430_173000"

    assert instance_root(instance_id) == Path("/opt/konnaxion/instances/demo-001")
    assert instance_env_dir(instance_id) == Path("/opt/konnaxion/instances/demo-001/env")
    assert instance_state_dir(instance_id) == Path("/opt/konnaxion/instances/demo-001/state")
    assert instance_compose_file(instance_id) == Path(
        "/opt/konnaxion/instances/demo-001/state/docker-compose.runtime.yml"
    )
    assert instance_backup_root(instance_id) == Path("/opt/konnaxion/backups/demo-001")
    assert capsule_path(DEFAULT_CAPSULE_ID) == Path(
        "/opt/konnaxion/capsules/konnaxion-v14-demo-2026.04.30.kxcap"
    )


def test_network_profiles_are_exact() -> None:
    assert enum_values(NetworkProfile) == {
        "local_only",
        "intranet_private",
        "private_tunnel",
        "public_temporary",
        "public_vps",
        "offline",
    }

    assert DEFAULT_NETWORK_PROFILE == NetworkProfile.INTRANET_PRIVATE


def test_exposure_modes_are_exact() -> None:
    assert enum_values(ExposureMode) == {
        "private",
        "lan",
        "vpn",
        "temporary_tunnel",
        "public",
    }

    assert DEFAULT_EXPOSURE_MODE == ExposureMode.PRIVATE
    assert DEFAULT_PUBLIC_MODE_ENABLED is False


def test_docker_services_are_exact_and_aliases_are_absent() -> None:
    assert enum_values(DockerService) == {
        "traefik",
        "frontend-next",
        "django-api",
        "postgres",
        "redis",
        "celeryworker",
        "celerybeat",
        "flower",
        "media-nginx",
        "kx-agent",
    }

    assert set(CANONICAL_DOCKER_SERVICES) == enum_values(DockerService)
    assert not (set(CANONICAL_DOCKER_SERVICES) & FORBIDDEN_SERVICE_ALIASES)


def test_allowed_entry_ports_are_canonical() -> None:
    assert ALLOWED_ENTRY_PORTS == {
        "https": 443,
        "http_redirect": 80,
        "ssh_admin_restricted": 22,
    }


def test_internal_ports_are_forbidden_publicly() -> None:
    expected_internal_ports = {
        DockerService.FRONTEND_NEXT: 3000,
        DockerService.DJANGO_API: 5000,
        DockerService.POSTGRES: 5432,
        DockerService.REDIS: 6379,
        DockerService.FLOWER: 5555,
        "django_dev_server": 8000,
    }

    assert INTERNAL_ONLY_PORTS == expected_internal_ports
    assert FORBIDDEN_PUBLIC_PORTS == frozenset(expected_internal_ports.values())


def test_routes_are_canonical() -> None:
    assert ROUTES == {
        "/": "frontend-next",
        "/api/": "django-api",
        "/admin/": "django-api",
        "/media/": "media-nginx",
    }


def test_kx_env_defaults_include_required_runtime_variables() -> None:
    expected = {
        "KX_INSTANCE_ID": DEFAULT_INSTANCE_ID,
        "KX_CAPSULE_ID": DEFAULT_CAPSULE_ID,
        "KX_CAPSULE_VERSION": DEFAULT_CAPSULE_VERSION,
        "KX_APP_VERSION": APP_VERSION,
        "KX_PARAM_VERSION": PARAM_VERSION,
        "KX_NETWORK_PROFILE": DEFAULT_NETWORK_PROFILE.value,
        "KX_EXPOSURE_MODE": DEFAULT_EXPOSURE_MODE.value,
        "KX_PUBLIC_MODE_ENABLED": "false",
        "KX_PUBLIC_MODE_EXPIRES_AT": "",
        "KX_REQUIRE_SIGNED_CAPSULE": "true",
        "KX_GENERATE_SECRETS_ON_INSTALL": "true",
        "KX_ALLOW_UNKNOWN_IMAGES": "false",
        "KX_ALLOW_PRIVILEGED_CONTAINERS": "false",
        "KX_ALLOW_DOCKER_SOCKET_MOUNT": "false",
        "KX_ALLOW_HOST_NETWORK": "false",
        "KX_BACKUP_ENABLED": "true",
        "KX_BACKUP_ROOT": "/opt/konnaxion/backups",
        "KX_BACKUP_RETENTION_DAYS": "14",
        "KX_DAILY_BACKUP_RETENTION_DAYS": "14",
        "KX_WEEKLY_BACKUP_RETENTION_WEEKS": "8",
        "KX_MONTHLY_BACKUP_RETENTION_MONTHS": "12",
        "KX_PRE_UPDATE_BACKUP_RETENTION_COUNT": "5",
        "KX_PRE_RESTORE_BACKUP_RETENTION_COUNT": "5",
        "KX_HOST": "",
    }

    assert KX_ENV_DEFAULTS == expected
    assert all(key.startswith("KX_") for key in KX_ENV_DEFAULTS)


def test_security_gate_statuses_are_exact() -> None:
    assert enum_values(SecurityGateStatus) == {
        "PASS",
        "WARN",
        "FAIL_BLOCKING",
        "SKIPPED",
        "UNKNOWN",
    }


def test_security_gate_checks_are_exact() -> None:
    assert enum_values(SecurityGateCheck) == {
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


def test_blocking_security_checks_are_subset_of_all_security_checks() -> None:
    all_checks = set(SecurityGateCheck)
    assert set(BLOCKING_SECURITY_CHECKS).issubset(all_checks)

    expected_blocking = {
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

    assert BLOCKING_SECURITY_CHECKS == frozenset(expected_blocking)


def test_instance_states_are_exact() -> None:
    assert enum_values(InstanceState) == {
        "created",
        "importing",
        "verifying",
        "ready",
        "starting",
        "running",
        "stopping",
        "stopped",
        "updating",
        "rolling_back",
        "degraded",
        "failed",
        "security_blocked",
    }


def test_backup_statuses_are_exact() -> None:
    assert enum_values(BackupStatus) == {
        "created",
        "running",
        "verifying",
        "verified",
        "failed",
        "expired",
        "deleted",
        "quarantined",
    }


def test_restore_statuses_are_exact() -> None:
    assert enum_values(RestoreStatus) == {
        "planned",
        "preflight",
        "creating_pre_restore_backup",
        "restoring_database",
        "restoring_media",
        "running_migrations",
        "running_security_gate",
        "running_healthchecks",
        "restored",
        "degraded",
        "failed",
        "rolled_back",
    }


def test_rollback_statuses_are_exact() -> None:
    assert enum_values(RollbackStatus) == {
        "planned",
        "running",
        "capsule_repointed",
        "data_restored",
        "healthchecking",
        "completed",
        "failed",
    }


def test_public_cli_commands_are_exact() -> None:
    assert PUBLIC_CLI_COMMANDS == (
        "kx capsule build",
        "kx capsule verify",
        "kx capsule import",
        "kx artifact list",
        "kx artifact show",
        "kx artifact remove",
        "kx instance create",
        "kx instance start",
        "kx instance stop",
        "kx instance status",
        "kx instance logs",
        "kx instance backup",
        "kx instance restore",
        "kx instance restore-new",
        "kx instance update",
        "kx instance rollback",
        "kx instance health",
        "kx backup list",
        "kx backup verify",
        "kx backup test-restore",
        "kx security check",
        "kx network set-profile",
    )


@pytest.mark.parametrize(
    "bad_service_name",
    sorted(FORBIDDEN_SERVICE_ALIASES),
)
def test_forbidden_service_aliases_are_not_canonical(bad_service_name: str) -> None:
    assert bad_service_name not in CANONICAL_DOCKER_SERVICES


@pytest.mark.parametrize(
    "path",
    [
        KX_CAPSULES_DIR,
        KX_INSTANCES_DIR,
        KX_SHARED_DIR,
        KX_RELEASES_DIR,
        KX_MANAGER_DIR,
        KX_AGENT_DIR,
        KX_BACKUPS_ROOT,
    ],
)
def test_no_legacy_vps_paths_are_used(path: Path) -> None:
    assert "/home/deploy/apps/Konnaxion" not in str(path)
    assert str(path).startswith("/opt/konnaxion")
