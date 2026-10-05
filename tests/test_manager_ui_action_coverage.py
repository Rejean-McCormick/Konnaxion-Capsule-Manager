"""
Contract tests for Konnaxion Capsule Manager GUI action coverage.

These tests enforce DOC-17:
- every canonical UiAction exists
- every UiAction has an exact label
- every non-browser action has a POST /ui/actions/... route
- browser-only actions have links
- key safety gates are represented
- FastAPI UI import/register does not require Streamlit
"""

from __future__ import annotations

import importlib
import sys
from collections.abc import Mapping
from typing import Any

import pytest
from fastapi import FastAPI

from kx_shared.konnaxion_constants import DockerService, ExposureMode, NetworkProfile


REQUIRED_ACTION_LABELS: dict[str, str] = {
    "check_manager": "Check Manager",
    "check_agent": "Check Agent",
    "select_source_folder": "Select Source Folder",
    "select_capsule_output_folder": "Select Output Folder",
    "build_capsule": "Build Capsule",
    "rebuild_capsule": "Rebuild Capsule",
    "verify_capsule": "Verify Capsule",
    "import_capsule": "Import Capsule",
    "list_capsules": "List Capsules",
    "view_capsule": "View Capsule",
    "create_instance": "Create Instance",
    "update_instance": "Update Instance",
    "start_instance": "Start Instance",
    "stop_instance": "Stop Instance",
    "restart_instance": "Restart Instance",
    "instance_status": "Instance Status",
    "view_logs": "View Logs",
    "view_health": "Instance Health",
    "open_instance": "Open Instance",
    "rollback_instance": "Rollback",
    "create_backup": "Create Backup",
    "list_backups": "List Backups",
    "verify_backup": "Verify Backup",
    "restore_backup": "Restore Backup",
    "restore_backup_new": "Restore Backup New",
    "test_restore_backup": "Test Restore Backup",
    "run_security_check": "Run Security Check",
    "set_network_profile": "Set Network Profile",
    "disable_public_mode": "Disable Public Mode",
    "set_target_local": "Set Local Target",
    "set_target_intranet": "Set Intranet Target",
    "set_target_droplet": "Set Droplet Target",
    "set_target_temporary_public": "Set Temporary Public Target",
    "deploy_local": "Deploy Local",
    "deploy_intranet": "Deploy Intranet",
    "deploy_droplet": "Deploy Droplet",
    "scan_netcup_host_key": "Scan Netcup Host Key",
    "provision_netcup_vps": "Provision Fresh Netcup VPS",
    "initialize_production_data": "Initialize Production Data",
    "publish_packaged_universes": "Publish Packaged Universes",
    "bootstrap_droplet_agent": "Bootstrap Droplet Agent",
    "check_droplet_agent": "Check Droplet Agent",
    "copy_capsule_to_droplet": "Copy Capsule to Droplet",
    "start_droplet_instance": "Start Droplet Instance",
    "open_manager_docs": "Open Manager Docs",
    "open_agent_docs": "Open Agent Docs",
}


BROWSER_ONLY_ACTIONS: set[str] = {
    "open_instance",
    "open_manager_docs",
    "open_agent_docs",
}


REQUIRED_POST_ACTION_ROUTES: dict[str, str] = {
    "check_manager": "/ui/actions/check-manager",
    "check_agent": "/ui/actions/check-agent",
    "select_source_folder": "/ui/actions/select-source-folder",
    "select_capsule_output_folder": "/ui/actions/select-capsule-output-folder",
    "build_capsule": "/ui/actions/build-capsule",
    "rebuild_capsule": "/ui/actions/rebuild-capsule",
    "verify_capsule": "/ui/actions/verify-capsule",
    "import_capsule": "/ui/actions/import-capsule",
    "list_capsules": "/ui/actions/list-capsules",
    "view_capsule": "/ui/actions/view-capsule",
    "create_instance": "/ui/actions/create-instance",
    "update_instance": "/ui/actions/update-instance",
    "start_instance": "/ui/actions/start-instance",
    "stop_instance": "/ui/actions/stop-instance",
    "restart_instance": "/ui/actions/restart-instance",
    "instance_status": "/ui/actions/instance-status",
    "view_logs": "/ui/actions/view-logs",
    "view_health": "/ui/actions/view-health",
    "rollback_instance": "/ui/actions/rollback-instance",
    "create_backup": "/ui/actions/create-backup",
    "list_backups": "/ui/actions/list-backups",
    "verify_backup": "/ui/actions/verify-backup",
    "restore_backup": "/ui/actions/restore-backup",
    "restore_backup_new": "/ui/actions/restore-backup-new",
    "test_restore_backup": "/ui/actions/test-restore-backup",
    "run_security_check": "/ui/actions/run-security-check",
    "set_network_profile": "/ui/actions/set-network-profile",
    "disable_public_mode": "/ui/actions/disable-public-mode",
    "set_target_local": "/ui/actions/set-target-local",
    "set_target_intranet": "/ui/actions/set-target-intranet",
    "set_target_droplet": "/ui/actions/set-target-droplet",
    "set_target_temporary_public": "/ui/actions/set-target-temporary-public",
    "deploy_local": "/ui/actions/deploy-local",
    "deploy_intranet": "/ui/actions/deploy-intranet",
    "deploy_droplet": "/ui/actions/deploy-droplet",
    "scan_netcup_host_key": "/ui/actions/scan-netcup-host-key",
    "provision_netcup_vps": "/ui/actions/provision-netcup-vps",
    "initialize_production_data": "/ui/actions/initialize-production-data",
    "publish_packaged_universes": "/ui/actions/publish-packaged-universes",
    "bootstrap_droplet_agent": "/ui/actions/bootstrap-droplet-agent",
    "check_droplet_agent": "/ui/actions/check-droplet-agent",
    "copy_capsule_to_droplet": "/ui/actions/copy-capsule-to-droplet",
    "start_droplet_instance": "/ui/actions/start-droplet-instance",
}


AGENT_ENDPOINT_ACTIONS: dict[str, str] = {
    "check_agent": "GET /v1/health",
    "verify_capsule": "POST /v1/capsules/verify",
    "import_capsule": "POST /v1/capsules/import",
    "create_instance": "POST /v1/instances/create",
    "update_instance": "POST /v1/instances/update",
    "start_instance": "POST /v1/instances/start",
    "stop_instance": "POST /v1/instances/stop",
    "instance_status": "POST /v1/instances/status",
    "view_logs": "POST /v1/instances/logs",
    "view_health": "POST /v1/instances/health",
    "rollback_instance": "POST /v1/instances/rollback",
    "create_backup": "POST /v1/instances/backup",
    "restore_backup": "POST /v1/instances/restore",
    "restore_backup_new": "POST /v1/instances/restore-new",
    "run_security_check": "POST /v1/security/check",
    "set_network_profile": "POST /v1/network/set-profile",
    "disable_public_mode": "POST /v1/network/set-profile",
}


def _value_set(enum_cls: Any) -> set[str]:
    return {str(item.value) for item in enum_cls}


def _mapping_to_str_keys(mapping: Mapping[Any, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in mapping.items():
        result[str(getattr(key, "value", key))] = value
    return result


def _require_mapping(module: Any, name: str) -> dict[str, Any]:
    value = getattr(module, name, None)
    assert isinstance(value, Mapping), f"kx_manager.ui.actions.{name} must be a mapping"
    return _mapping_to_str_keys(value)


def _validate_payload(action: str, payload: dict[str, Any]) -> Any:
    forms = importlib.import_module("kx_manager.ui.forms")
    validator = getattr(forms, "validate_action_payload", None)
    assert callable(validator), "kx_manager.ui.forms.validate_action_payload(action, payload) is required"
    return validator(action, payload)


def test_all_uiactions_have_labels() -> None:
    pages = importlib.import_module("kx_manager.ui.pages")
    actions = importlib.import_module("kx_manager.ui.actions")

    action_values = _value_set(pages.UiAction)
    labels = _require_mapping(actions, "ACTION_LABELS")

    assert action_values == set(REQUIRED_ACTION_LABELS)
    assert labels == REQUIRED_ACTION_LABELS


def test_all_uiactions_have_route_or_link() -> None:
    pages = importlib.import_module("kx_manager.ui.pages")
    actions = importlib.import_module("kx_manager.ui.actions")

    action_values = _value_set(pages.UiAction)
    post_routes = _require_mapping(actions, "ACTION_ROUTES")
    browser_links = _require_mapping(actions, "BROWSER_LINK_ACTIONS")

    mapped = set(post_routes) | set(browser_links)

    assert mapped == action_values
    assert set(browser_links) == BROWSER_ONLY_ACTIONS
    assert post_routes == REQUIRED_POST_ACTION_ROUTES


def test_all_post_action_routes_start_with_ui_actions() -> None:
    actions = importlib.import_module("kx_manager.ui.actions")
    post_routes = _require_mapping(actions, "ACTION_ROUTES")

    for action, route in post_routes.items():
        assert isinstance(route, str), action
        assert route.startswith("/ui/actions/"), action


def test_all_required_actions_exist() -> None:
    pages = importlib.import_module("kx_manager.ui.pages")
    assert _value_set(pages.UiAction) == set(REQUIRED_ACTION_LABELS)


def test_no_extra_unmapped_actions_exist() -> None:
    pages = importlib.import_module("kx_manager.ui.pages")
    actions = importlib.import_module("kx_manager.ui.actions")

    action_values = _value_set(pages.UiAction)
    labels = set(_require_mapping(actions, "ACTION_LABELS"))
    routes = set(_require_mapping(actions, "ACTION_ROUTES"))
    links = set(_require_mapping(actions, "BROWSER_LINK_ACTIONS"))

    assert labels == action_values
    assert routes | links == action_values
    assert not (routes & links), "An action cannot be both POST-route and browser-only"


def test_build_capsule_action_exists() -> None:
    assert "build_capsule" in REQUIRED_ACTION_LABELS


def test_rebuild_capsule_action_exists() -> None:
    assert "rebuild_capsule" in REQUIRED_ACTION_LABELS


def test_restart_instance_action_exists() -> None:
    assert "restart_instance" in REQUIRED_ACTION_LABELS


def test_instance_status_action_exists() -> None:
    assert "instance_status" in REQUIRED_ACTION_LABELS


def test_list_backups_action_exists() -> None:
    assert "list_backups" in REQUIRED_ACTION_LABELS


def test_test_restore_backup_action_exists() -> None:
    assert "test_restore_backup" in REQUIRED_ACTION_LABELS


def test_check_manager_action_exists() -> None:
    assert "check_manager" in REQUIRED_ACTION_LABELS


def test_check_agent_action_exists() -> None:
    assert "check_agent" in REQUIRED_ACTION_LABELS


def test_open_docs_actions_exist() -> None:
    assert {"open_manager_docs", "open_agent_docs"} <= set(REQUIRED_ACTION_LABELS)


def test_target_actions_exist() -> None:
    assert {
        "set_target_local",
        "set_target_intranet",
        "set_target_droplet",
        "set_target_temporary_public",
    } <= set(REQUIRED_ACTION_LABELS)


def test_droplet_actions_exist() -> None:
    assert {
        "set_target_droplet",
        "deploy_droplet",
        "bootstrap_droplet_agent",
        "check_droplet_agent",
        "copy_capsule_to_droplet",
        "start_droplet_instance",
    } <= set(REQUIRED_ACTION_LABELS)


def test_bootstrap_droplet_agent_action_exists() -> None:
    assert "bootstrap_droplet_agent" in REQUIRED_ACTION_LABELS
    assert (
        REQUIRED_POST_ACTION_ROUTES["bootstrap_droplet_agent"]
        == "/ui/actions/bootstrap-droplet-agent"
    )


def test_deploy_actions_exist() -> None:
    assert {"deploy_local", "deploy_intranet", "deploy_droplet"} <= set(REQUIRED_ACTION_LABELS)


def test_agent_endpoint_actions_are_mapped() -> None:
    actions = importlib.import_module("kx_manager.ui.actions")
    endpoint_map = _require_mapping(actions, "AGENT_ENDPOINTS")

    for action, endpoint in AGENT_ENDPOINT_ACTIONS.items():
        assert endpoint_map.get(action) == endpoint


def test_action_payloads_use_canonical_network_profiles() -> None:
    values = {item.value for item in NetworkProfile}
    assert {
        "local_only",
        "intranet_private",
        "private_tunnel",
        "public_temporary",
        "public_vps",
        "offline",
    } <= values

    valid = _validate_payload(
        "set_network_profile",
        {
            "instance_id": "demo-001",
            "network_profile": "intranet_private",
            "exposure_mode": "private",
        },
    )
    assert valid is not None

    with pytest.raises(ValueError):
        _validate_payload(
            "set_network_profile",
            {
                "instance_id": "demo-001",
                "network_profile": "public_server",
                "exposure_mode": "public",
            },
        )


def test_action_payloads_use_canonical_exposure_modes() -> None:
    values = {item.value for item in ExposureMode}
    assert {"private", "lan", "vpn", "temporary_tunnel", "public"} <= values

    valid = _validate_payload(
        "set_network_profile",
        {
            "instance_id": "demo-001",
            "network_profile": "intranet_private",
            "exposure_mode": "lan",
        },
    )
    assert valid is not None

    with pytest.raises(ValueError):
        _validate_payload(
            "set_network_profile",
            {
                "instance_id": "demo-001",
                "network_profile": "intranet_private",
                "exposure_mode": "open_web",
            },
        )


def test_action_payloads_use_canonical_docker_services() -> None:
    values = {item.value for item in DockerService}
    assert {
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
    } <= values

    valid = _validate_payload(
        "view_logs",
        {
            "instance_id": "demo-001",
            "service": "django-api",
            "tail": 200,
        },
    )
    assert valid is not None

    with pytest.raises(ValueError):
        _validate_payload(
            "view_logs",
            {
                "instance_id": "demo-001",
                "service": "backend",
                "tail": 200,
            },
        )


def test_public_temporary_requires_expiration() -> None:
    with pytest.raises(ValueError):
        _validate_payload(
            "set_target_temporary_public",
            {
                "target_mode": "temporary_public",
                "network_profile": "public_temporary",
                "exposure_mode": "temporary_tunnel",
                "confirmed": True,
            },
        )


def test_public_vps_requires_confirmation() -> None:
    with pytest.raises(ValueError):
        _validate_payload(
            "set_target_droplet",
            {
                "target_mode": "droplet",
                "network_profile": "public_vps",
                "exposure_mode": "public",
                "droplet_host": "203.0.113.10",
                "droplet_user": "root",
                "ssh_key_path": r"C:\Users\user\.ssh\id_ed25519",
                "remote_kx_root": "/opt/konnaxion",
                "domain": "app.example.com",
                "confirmed": False,
            },
        )


def test_rollback_restore_data_requires_backup_id() -> None:
    with pytest.raises(ValueError):
        _validate_payload(
            "rollback_instance",
            {
                "instance_id": "demo-001",
                "target_release_id": "20260430_173000",
                "restore_data": True,
            },
        )


def test_droplet_deploy_requires_host_user_key_remote_root() -> None:
    base_payload = {
        "source_dir": r"C:\mycode\Konnaxion\Konnaxion",
        "capsule_output_dir": r"C:\mycode\Konnaxion\runtime\capsules",
        "capsule_file": r"C:\mycode\Konnaxion\runtime\capsules\konnaxion-v14-demo-2026.04.30.kxcap",
        "instance_id": "demo-001",
        "target_mode": "droplet",
        "network_profile": "public_vps",
        "exposure_mode": "public",
        "droplet_host": "203.0.113.10",
        "droplet_user": "root",
        "ssh_key_path": r"C:\Users\user\.ssh\id_ed25519",
        "remote_kx_root": "/opt/konnaxion",
        "domain": "app.example.com",
        "confirmed": True,
    }

    for required_field in ("droplet_host", "droplet_user", "ssh_key_path", "remote_kx_root"):
        payload = dict(base_payload)
        payload.pop(required_field)
        with pytest.raises(ValueError):
            _validate_payload("deploy_droplet", payload)


def test_bootstrap_droplet_agent_requires_host_user_key_remote_root() -> None:
    base_payload = {
        "instance_id": "demo-001",
        "target_mode": "droplet",
        "network_profile": "public_vps",
        "exposure_mode": "public",
        "droplet_host": "203.0.113.10",
        "droplet_user": "root",
        "ssh_key_path": r"C:\Users\user\.ssh\id_ed25519",
        "remote_kx_root": "/opt/konnaxion",
        "remote_capsule_dir": "/opt/konnaxion/capsules",
        "domain": "app.example.com",
        "confirmed": True,
    }

    assert _validate_payload("bootstrap_droplet_agent", base_payload) is not None

    for required_field in (
        "droplet_host",
        "droplet_user",
        "ssh_key_path",
        "remote_kx_root",
    ):
        payload = dict(base_payload)
        payload.pop(required_field)
        with pytest.raises(ValueError):
            _validate_payload("bootstrap_droplet_agent", payload)


def test_command_fallback_uses_shell_false() -> None:
    actions = importlib.import_module("kx_manager.ui.actions")
    fallbacks = _require_mapping(actions, "CLI_FALLBACKS")

    for action, config in fallbacks.items():
        if isinstance(config, Mapping):
            assert config.get("shell") is False, action
            argv = config.get("argv")
        else:
            argv = config

        assert isinstance(argv, (list, tuple)), action
        assert argv, action
        assert all(isinstance(part, str) and part for part in argv), action


def test_fastapi_ui_register_exists() -> None:
    app_module = importlib.import_module("kx_manager.ui.app")
    assert callable(getattr(app_module, "register", None))

    app = FastAPI()
    app_module.register(app)

    registered_post_routes = {
        route.path
        for route in app.routes
        if "POST" in getattr(route, "methods", set())
    }

    for route in REQUIRED_POST_ACTION_ROUTES.values():
        assert route in registered_post_routes


def test_streamlit_is_not_required_for_fastapi_ui_import(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "streamlit", None)

    module = importlib.import_module("kx_manager.ui.app")
    module = importlib.reload(module)

    assert callable(getattr(module, "register", None))