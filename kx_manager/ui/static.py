"""Canonical UI constants, routes, action names, and payload aliases."""

from __future__ import annotations

from typing import Any, Mapping


APP_TITLE = "Konnaxion Capsule Manager"
APP_ICON = "◈"
APP_LOGO_SRC = "/ui/assets/LogoK.svg"
APP_LOGO_ALT = "Konnaxion"
DEFAULT_REFRESH_SECONDS = 5

UI_BASE_PATH = "/ui"
ACTION_BASE_PATH = "/ui/actions"


UI_PAGE_ROUTES: tuple[str, ...] = (
    "/ui",
    "/ui/capsules",
    "/ui/instances",
    "/ui/security",
    "/ui/network",
    "/ui/backups",
    "/ui/restore",
    "/ui/logs",
    "/ui/health",
    "/ui/settings",
    "/ui/targets",
    "/ui/deploy",
    "/ui/about",
)


PAGE_TITLES: dict[str, str] = {
    "/ui": "Dashboard",
    "/ui/capsules": "Capsules",
    "/ui/instances": "Instances",
    "/ui/security": "Security",
    "/ui/network": "Network",
    "/ui/backups": "Backups",
    "/ui/restore": "Restore",
    "/ui/logs": "Logs",
    "/ui/health": "Health",
    "/ui/settings": "Settings",
    "/ui/targets": "Targets",
    "/ui/deploy": "Deploy",
    "/ui/about": "About",
}


CONTRACT_ACTIONS: tuple[str, ...] = (
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
    "bootstrap_droplet_agent",
    "check_droplet_agent",
    "copy_capsule_to_droplet",
    "start_droplet_instance",
    "open_manager_docs",
    "open_agent_docs",
)


ACTION_ALIASES: dict[str, str] = {
    "open_runtime": "open_instance",
}


KNOWN_ACTIONS: frozenset[str] = frozenset(CONTRACT_ACTIONS) | frozenset(
    ACTION_ALIASES
)


ACTION_LABELS: dict[str, str] = {
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


BROWSER_LINK_ACTIONS: dict[str, str] = {
    "open_instance": "runtime_url",
    "open_manager_docs": "/docs",
    "open_agent_docs": "/docs",
}


BROWSER_ONLY_ACTIONS: frozenset[str] = frozenset(BROWSER_LINK_ACTIONS)


ACTION_ROUTES: dict[str, str] = {
    action: f"{ACTION_BASE_PATH}/{action.replace('_', '-')}"
    for action in CONTRACT_ACTIONS
    if action not in BROWSER_ONLY_ACTIONS
}


NAV_ITEMS: tuple[tuple[str, str], ...] = (
    ("Dashboard", "/ui"),
    ("Capsules", "/ui/capsules"),
    ("Instances", "/ui/instances"),
    ("Targets", "/ui/targets"),
    ("Deploy", "/ui/deploy"),
    ("Security", "/ui/security"),
    ("Network", "/ui/network"),
    ("Backups", "/ui/backups"),
    ("Restore", "/ui/restore"),
    ("Logs", "/ui/logs"),
    ("Health", "/ui/health"),
    ("Settings", "/ui/settings"),
    ("About", "/ui/about"),
)


def canonical_action(action: Any) -> str:
    """Return the canonical action value for an enum/string/action alias."""

    value = str(getattr(action, "value", action)).strip()
    return ACTION_ALIASES.get(value, value)


def route_for_action(action: Any) -> str:
    """Return the canonical POST route for a non-browser GUI action."""

    action_value = canonical_action(action)

    if action_value in BROWSER_ONLY_ACTIONS:
        raise KeyError(f"Browser-only action has no POST route: {action_value}")

    return ACTION_ROUTES[action_value]


def browser_link_for_action(action: Any) -> str:
    """Return the configured browser-link target for a browser-only action."""

    action_value = canonical_action(action)
    return BROWSER_LINK_ACTIONS[action_value]


def title_for_route(route: str) -> str:
    """Return the display title for a /ui route."""

    return PAGE_TITLES.get(route, "Dashboard")


def _target_mode(data: Mapping[str, Any]) -> str:
    return str(data.get("target_mode") or "").strip()


def _is_droplet_payload(data: Mapping[str, Any]) -> bool:
    return _target_mode(data) == "droplet"


def _is_temporary_public_payload(data: Mapping[str, Any]) -> bool:
    return _target_mode(data) == "temporary_public"


def normalize_payload_aliases(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """Normalize supported GUI form payload aliases.

    This intentionally does not invent Droplet domain values from host,
    droplet_host, public_host, or target_host. Droplet domain must be supplied
    explicitly as domain or droplet_domain and then validated downstream.
    """

    data = dict(payload or {})

    # Capsule path aliases.
    if data.get("capsule_file") and not data.get("capsule_path"):
        data["capsule_path"] = data["capsule_file"]
    if data.get("capsule_path") and not data.get("capsule_file"):
        data["capsule_file"] = data["capsule_path"]

    # Output aliases.
    if data.get("output_dir") and not data.get("capsule_output_dir"):
        data["capsule_output_dir"] = data["output_dir"]

    # Target host aliases.
    if data.get("target_host") and not data.get("host"):
        data["host"] = data["target_host"]
    if data.get("public_host") and not data.get("host"):
        data["host"] = data["public_host"]
    if data.get("private_host") and not data.get("host"):
        data["host"] = data["private_host"]
    if data.get("droplet_host") and not data.get("host"):
        data["host"] = data["droplet_host"]
    if data.get("host") and not data.get("droplet_host") and _is_droplet_payload(data):
        data["droplet_host"] = data["host"]

    # Droplet SSH/user aliases.
    if data.get("droplet_ssh_key") and not data.get("ssh_key_path"):
        data["ssh_key_path"] = data["droplet_ssh_key"]
    if data.get("ssh_key") and not data.get("ssh_key_path"):
        data["ssh_key_path"] = data["ssh_key"]

    if data.get("ssh_user") and not data.get("droplet_user"):
        data["droplet_user"] = data["ssh_user"]
    if data.get("user") and not data.get("droplet_user"):
        data["droplet_user"] = data["user"]

    # Droplet runtime root aliases.
    if data.get("droplet_kx_root") and not data.get("remote_kx_root"):
        data["remote_kx_root"] = data["droplet_kx_root"]
    if data.get("remote_root") and not data.get("remote_kx_root"):
        data["remote_kx_root"] = data["remote_root"]

    if data.get("remote_kx_root") and not data.get("runtime_root"):
        data["runtime_root"] = data["remote_kx_root"]
    if (
        data.get("runtime_root")
        and not data.get("remote_kx_root")
        and _is_droplet_payload(data)
    ):
        data["remote_kx_root"] = data["runtime_root"]
    if data.get("remote_root") and not data.get("runtime_root"):
        data["runtime_root"] = data["remote_root"]
    if data.get("droplet_kx_root") and not data.get("runtime_root"):
        data["runtime_root"] = data["droplet_kx_root"]
    if data.get("target_runtime_root") and not data.get("runtime_root"):
        data["runtime_root"] = data["target_runtime_root"]

    # Droplet capsule directory aliases.
    if data.get("droplet_capsule_dir") and not data.get("remote_capsule_dir"):
        data["remote_capsule_dir"] = data["droplet_capsule_dir"]

    if data.get("remote_capsule_dir") and not data.get("capsule_dir"):
        data["capsule_dir"] = data["remote_capsule_dir"]
    if (
        data.get("capsule_dir")
        and not data.get("remote_capsule_dir")
        and _is_droplet_payload(data)
    ):
        data["remote_capsule_dir"] = data["capsule_dir"]
    if data.get("droplet_capsule_dir") and not data.get("capsule_dir"):
        data["capsule_dir"] = data["droplet_capsule_dir"]
    if data.get("target_capsule_dir") and not data.get("capsule_dir"):
        data["capsule_dir"] = data["target_capsule_dir"]

    # Domain aliases.
    #
    # Allowed:
    # - domain <-> droplet_domain
    #
    # Not allowed:
    # - droplet_host -> domain
    # - host -> domain
    # - target_host -> domain
    # - public_host -> domain for Droplet
    if data.get("droplet_domain") and not data.get("domain"):
        data["domain"] = data["droplet_domain"]
    if data.get("domain") and not data.get("droplet_domain"):
        data["droplet_domain"] = data["domain"]

    # Temporary-public forms may mirror public_host into domain for display or
    # service compatibility. Droplet payloads still require explicit domain or
    # droplet_domain and must not use public_host as a hidden shortcut.
    if (
        _is_temporary_public_payload(data)
        and data.get("public_host")
        and not data.get("domain")
    ):
        data["domain"] = data["public_host"]

    # Remote Agent aliases.
    if data.get("droplet_agent_url") and not data.get("remote_agent_url"):
        data["remote_agent_url"] = data["droplet_agent_url"]

    # Restore aliases.
    if data.get("target_instance_id") and not data.get("new_instance_id"):
        data["new_instance_id"] = data["target_instance_id"]
    if data.get("new_instance_id") and not data.get("target_instance_id"):
        data["target_instance_id"] = data["new_instance_id"]

    if data.get("backup_id") and not data.get("source_backup_id"):
        data["source_backup_id"] = data["backup_id"]
    if data.get("from_backup_id") and not data.get("source_backup_id"):
        data["source_backup_id"] = data["from_backup_id"]

    # Logs aliases.
    if data.get("tail_lines") and not data.get("lines"):
        data["lines"] = data["tail_lines"]

    return data


__all__ = [
    "ACTION_ALIASES",
    "ACTION_BASE_PATH",
    "ACTION_LABELS",
    "ACTION_ROUTES",
    "APP_ICON",
    "APP_LOGO_ALT",
    "APP_LOGO_SRC",
    "APP_TITLE",
    "BROWSER_LINK_ACTIONS",
    "BROWSER_ONLY_ACTIONS",
    "CONTRACT_ACTIONS",
    "DEFAULT_REFRESH_SECONDS",
    "KNOWN_ACTIONS",
    "NAV_ITEMS",
    "PAGE_TITLES",
    "UI_BASE_PATH",
    "UI_PAGE_ROUTES",
    "browser_link_for_action",
    "canonical_action",
    "normalize_payload_aliases",
    "route_for_action",
    "title_for_route",
]