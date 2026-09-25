# kx_manager/ui/action_constants.py

"""Constants for Konnaxion Capsule Manager GUI action dispatch.

This module owns static action names, labels, routes, backend dispatch mapping,
Agent endpoint metadata, and target defaults.

It must not import action handlers, dispatch functions, HTTP clients, or service
modules. Keep this file dependency-light so other split modules can import it
without circular imports.
"""

from __future__ import annotations

from typing import Any


JsonDict = dict[str, Any]


try:
    from kx_manager.ui.static import (
        ACTION_ALIASES as _STATIC_ACTION_ALIASES,
        ACTION_LABELS as _STATIC_ACTION_LABELS,
        BROWSER_LINK_ACTIONS as _STATIC_BROWSER_LINK_ACTIONS,
        CONTRACT_ACTIONS as _STATIC_CONTRACT_ACTIONS,
        KNOWN_ACTIONS as _STATIC_KNOWN_ACTIONS,
    )

    CONTRACT_ACTIONS: tuple[str, ...] = tuple(
        str(action) for action in _STATIC_CONTRACT_ACTIONS
    )

    ACTION_ALIASES: dict[str, str] = {
        str(key): str(value)
        for key, value in dict(_STATIC_ACTION_ALIASES).items()
    }

    ACTION_LABELS: dict[str, str] = {
        str(key): str(value)
        for key, value in dict(_STATIC_ACTION_LABELS).items()
    }

    BROWSER_LINK_ACTIONS: dict[str, str] = {
        **{
            str(key): str(value)
            for key, value in dict(_STATIC_BROWSER_LINK_ACTIONS).items()
        },
        "open_instance": "runtime_url",
        "open_manager_docs": "/docs",
        "open_agent_docs": "/docs",
    }

    BROWSER_ONLY_ACTIONS: frozenset[str] = frozenset(BROWSER_LINK_ACTIONS)

    KNOWN_ACTIONS: frozenset[str] = (
        frozenset(str(action) for action in _STATIC_KNOWN_ACTIONS)
        | frozenset(ACTION_ALIASES)
        | frozenset(CONTRACT_ACTIONS)
    )

    ACTION_ROUTES: dict[str, str] = {
        action: f"/ui/actions/{action.replace('_', '-')}"
        for action in CONTRACT_ACTIONS
        if action not in BROWSER_ONLY_ACTIONS
    }

except Exception:  # pragma: no cover - staged build compatibility
    CONTRACT_ACTIONS = (
        "check_manager",
        "check_agent",
        "select_source_folder",
        "select_capsule_output_folder",
        "build_capsule",
        "rebuild_capsule",
        "verify_capsule",
        "import_capsule",
        "list_capsules",
        "view_capsule",
        "create_instance",
        "update_instance",
        "start_instance",
        "stop_instance",
        "restart_instance",
        "instance_status",
        "view_logs",
        "view_health",
        "open_instance",
        "rollback_instance",
        "create_backup",
        "list_backups",
        "verify_backup",
        "restore_backup",
        "restore_backup_new",
        "test_restore_backup",
        "run_security_check",
        "set_network_profile",
        "disable_public_mode",
        "set_target_local",
        "set_target_intranet",
        "set_target_droplet",
        "set_target_temporary_public",
        "deploy_local",
        "deploy_intranet",
        "deploy_droplet",
        "initialize_production_data",
        "check_droplet_agent",
        "copy_capsule_to_droplet",
        "start_droplet_instance",
        "open_manager_docs",
        "open_agent_docs",
    )

    ACTION_ALIASES = {
        "open_runtime": "open_instance",
    }

    ACTION_LABELS = {
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
        "initialize_production_data": "Initialize Production Data",
        "check_droplet_agent": "Check Droplet Agent",
        "copy_capsule_to_droplet": "Copy Capsule to Droplet",
        "start_droplet_instance": "Start Droplet Instance",
        "open_manager_docs": "Open Manager Docs",
        "open_agent_docs": "Open Agent Docs",
    }

    BROWSER_LINK_ACTIONS = {
        "open_instance": "runtime_url",
        "open_manager_docs": "/docs",
        "open_agent_docs": "/docs",
    }

    BROWSER_ONLY_ACTIONS = frozenset(BROWSER_LINK_ACTIONS)

    KNOWN_ACTIONS = frozenset(CONTRACT_ACTIONS) | frozenset(ACTION_ALIASES)

    ACTION_ROUTES = {
        action: f"/ui/actions/{action.replace('_', '-')}"
        for action in CONTRACT_ACTIONS
        if action not in BROWSER_ONLY_ACTIONS
    }


AGENT_ENDPOINTS: dict[str, str] = {
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


CLI_FALLBACKS: dict[str, dict[str, Any]] = {}


ACTION_DISPATCH_TABLE: dict[str, str] = {
    "check_manager": "manager_route",
    "check_agent": "agent_client",
    "select_source_folder": "ui_form",
    "select_capsule_output_folder": "ui_form",
    "build_capsule": "builder_service",
    "rebuild_capsule": "builder_service",
    "verify_capsule": "builder_or_agent_client",
    "import_capsule": "agent_client",
    "list_capsules": "manager_route",
    "view_capsule": "manager_route",
    "create_instance": "agent_client",
    "update_instance": "agent_client",
    "start_instance": "agent_client",
    "stop_instance": "agent_client",
    "restart_instance": "composed_agent_client",
    "instance_status": "agent_client",
    "view_logs": "agent_client",
    "view_health": "agent_client",
    "open_instance": "browser_link",
    "rollback_instance": "agent_client",
    "create_backup": "agent_client",
    "list_backups": "manager_route",
    "verify_backup": "manager_route",
    "restore_backup": "agent_client",
    "restore_backup_new": "agent_client",
    "test_restore_backup": "manager_route",
    "run_security_check": "agent_client",
    "set_network_profile": "agent_client",
    "disable_public_mode": "agent_client",
    "set_target_local": "target_service",
    "set_target_intranet": "target_service",
    "set_target_droplet": "target_service",
    "set_target_temporary_public": "target_service",
    "deploy_local": "deploy_service",
    "deploy_intranet": "deploy_service",
    "deploy_droplet": "deploy_service",
    "initialize_production_data": "operation_job",
    "check_droplet_agent": "deploy_service",
    "copy_capsule_to_droplet": "deploy_service",
    "start_droplet_instance": "deploy_service",
    "open_manager_docs": "browser_link",
    "open_agent_docs": "browser_link",
}


TARGET_DEFAULTS: dict[str, JsonDict] = {
    "local": {
        "target_mode": "local",
        "network_profile": "local_only",
        "exposure_mode": "private",
        "public_mode_enabled": False,
        "public_mode_expires_at": None,
    },
    "intranet": {
        "target_mode": "intranet",
        "network_profile": "intranet_private",
        "exposure_mode": "private",
        "public_mode_enabled": False,
        "public_mode_expires_at": None,
    },
    "temporary_public": {
        "target_mode": "temporary_public",
        "network_profile": "public_temporary",
        "exposure_mode": "temporary_tunnel",
        "public_mode_enabled": True,
    },
    "droplet": {
        "target_mode": "droplet",
        "network_profile": "public_vps",
        "exposure_mode": "public",
        "public_mode_enabled": True,
        "public_mode_expires_at": None,
        "remote_kx_root": "/opt/konnaxion",
    },
}


__all__ = [
    "ACTION_ALIASES",
    "ACTION_DISPATCH_TABLE",
    "ACTION_LABELS",
    "ACTION_ROUTES",
    "AGENT_ENDPOINTS",
    "BROWSER_LINK_ACTIONS",
    "BROWSER_ONLY_ACTIONS",
    "CLI_FALLBACKS",
    "CONTRACT_ACTIONS",
    "JsonDict",
    "KNOWN_ACTIONS",
    "TARGET_DEFAULTS",
]