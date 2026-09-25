"""
Konnaxion Capsule Manager UI page definitions.

This module is intentionally framework-neutral. It is now an orchestrator /
compatibility facade that re-exports the canonical page registry, navigation
structure, page metadata, action bindings, and lightweight render models from
smaller focused modules.

The UI must never execute privileged operations directly. Buttons and forms
should create Manager API requests, which then become allowlisted Konnaxion
Agent actions.
"""

from __future__ import annotations

from enum import StrEnum

# ---------------------------------------------------------------------
# Page identity, action metadata, and render model types
# ---------------------------------------------------------------------

from kx_manager.ui.page_types import (
    ActionIntent,
    Alert,
    MetricCard,
    NavigationGroup,
    PageAction,
    PageContext,
    PageDefinition,
    PageGroup,
    PageId,
    PageRenderModel,
    UiAction,
)

# ---------------------------------------------------------------------
# Canonical page registry
# ---------------------------------------------------------------------

from kx_manager.ui.page_registry import PAGE_REGISTRY

# ---------------------------------------------------------------------
# Page lookup and navigation
# ---------------------------------------------------------------------

from kx_manager.ui.page_navigation import (
    build_breadcrumbs,
    build_navigation,
    find_page_by_route,
    get_page,
    normalize_route,
)

# ---------------------------------------------------------------------
# Render-model builders and page-local action helpers
# ---------------------------------------------------------------------

from kx_manager.ui.page_models import (
    about_page_model,
    backups_page_model,
    capsules_page_model,
    dashboard_page_model,
    health_page_model,
    instance_actions,
    instance_detail_page_model,
    instances_page_model,
    logs_page_model,
    network_page_model,
    restore_page_model,
    security_page_model,
    settings_page_model,
)

# ---------------------------------------------------------------------
# Row/table helpers
# ---------------------------------------------------------------------

from kx_manager.ui.page_rows import (
    backup_summary_row,
    capsule_summary_row,
    instance_summary_row,
    runtime_variables_table,
    service_status_row,
)

# ---------------------------------------------------------------------
# Alert helpers
# ---------------------------------------------------------------------

from kx_manager.ui.page_alerts import (
    backup_alerts,
    security_alerts_from_instances,
    unsigned_capsule_alerts,
)

# ---------------------------------------------------------------------
# Status display helpers
# ---------------------------------------------------------------------

from kx_manager.ui.page_status import (
    status_level_for_backup,
    status_level_for_exposure,
    status_level_for_instance,
    status_level_for_restore,
    status_level_for_security,
    status_level_for_service,
)


# ---------------------------------------------------------------------
# FastAPI UI contract routes and labels
# ---------------------------------------------------------------------


class UiPage(StrEnum):
    """Public FastAPI UI page route contract.

    Values are route strings, not internal page ids. Keep this enum free of
    dynamic route parameters so contract tests and route registration can treat
    it as the finite public UI surface.
    """

    DASHBOARD = "/ui"
    CAPSULES = "/ui/capsules"
    INSTANCES = "/ui/instances"
    SECURITY = "/ui/security"
    NETWORK = "/ui/network"
    BACKUPS = "/ui/backups"
    RESTORE = "/ui/restore"
    LOGS = "/ui/logs"
    HEALTH = "/ui/health"
    SETTINGS = "/ui/settings"
    ABOUT = "/ui/about"
    TARGETS = "/ui/targets"
    DEPLOY = "/ui/deploy"


UI_PAGE_ROUTES: tuple[str, ...] = tuple(page.value for page in UiPage)

PAGE_ROUTES = UI_PAGE_ROUTES
PAGES = UI_PAGE_ROUTES


UI_ACTION_ROUTES: tuple[str, ...] = (
    "/ui/actions/check-manager",
    "/ui/actions/check-agent",
    "/ui/actions/select-source-folder",
    "/ui/actions/select-capsule-output-folder",
    "/ui/actions/build-capsule",
    "/ui/actions/rebuild-capsule",
    "/ui/actions/verify-capsule",
    "/ui/actions/import-capsule",
    "/ui/actions/list-capsules",
    "/ui/actions/view-capsule",
    "/ui/actions/create-instance",
    "/ui/actions/update-instance",
    "/ui/actions/start-instance",
    "/ui/actions/stop-instance",
    "/ui/actions/restart-instance",
    "/ui/actions/instance-status",
    "/ui/actions/view-logs",
    "/ui/actions/view-health",
    "/ui/actions/rollback-instance",
    "/ui/actions/create-backup",
    "/ui/actions/list-backups",
    "/ui/actions/verify-backup",
    "/ui/actions/restore-backup",
    "/ui/actions/restore-backup-new",
    "/ui/actions/test-restore-backup",
    "/ui/actions/run-security-check",
    "/ui/actions/set-network-profile",
    "/ui/actions/disable-public-mode",
    "/ui/actions/set-target-local",
    "/ui/actions/set-target-intranet",
    "/ui/actions/set-target-droplet",
    "/ui/actions/set-target-temporary-public",
    "/ui/actions/deploy-local",
    "/ui/actions/deploy-intranet",
    "/ui/actions/deploy-droplet",
    "/ui/actions/initialize-production-data",
    "/ui/actions/bootstrap-droplet-agent",
    "/ui/actions/check-droplet-agent",
    "/ui/actions/copy-capsule-to-droplet",
    "/ui/actions/start-droplet-instance",
)

ACTION_ROUTES = UI_ACTION_ROUTES
ACTIONS = UI_ACTION_ROUTES


UI_ACTION_LABELS: dict[str, str] = {
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
    "bootstrap_droplet_agent": "Bootstrap Droplet Agent",
    "check_droplet_agent": "Check Droplet Agent",
    "copy_capsule_to_droplet": "Copy Capsule to Droplet",
    "start_droplet_instance": "Start Droplet Instance",
    "open_manager_docs": "Open Manager Docs",
    "open_agent_docs": "Open Agent Docs",
}

ACTION_LABELS = UI_ACTION_LABELS
LABELS = UI_ACTION_LABELS


__all__ = [
    "ACTION_LABELS",
    "ACTION_ROUTES",
    "ACTIONS",
    "ActionIntent",
    "Alert",
    "LABELS",
    "MetricCard",
    "NavigationGroup",
    "PAGE_REGISTRY",
    "PAGE_ROUTES",
    "PAGES",
    "PageAction",
    "PageContext",
    "PageDefinition",
    "PageGroup",
    "PageId",
    "PageRenderModel",
    "UI_ACTION_LABELS",
    "UI_ACTION_ROUTES",
    "UI_PAGE_ROUTES",
    "UiAction",
    "UiPage",
    "about_page_model",
    "backup_alerts",
    "backup_summary_row",
    "backups_page_model",
    "build_breadcrumbs",
    "build_navigation",
    "capsule_summary_row",
    "capsules_page_model",
    "dashboard_page_model",
    "find_page_by_route",
    "get_page",
    "health_page_model",
    "instance_actions",
    "instance_detail_page_model",
    "instance_summary_row",
    "instances_page_model",
    "logs_page_model",
    "network_page_model",
    "normalize_route",
    "restore_page_model",
    "runtime_variables_table",
    "security_alerts_from_instances",
    "security_page_model",
    "service_status_row",
    "settings_page_model",
    "status_level_for_backup",
    "status_level_for_exposure",
    "status_level_for_instance",
    "status_level_for_restore",
    "status_level_for_security",
    "status_level_for_service",
    "unsigned_capsule_alerts",
]