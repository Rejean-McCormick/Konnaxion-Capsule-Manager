"""Optional Streamlit prototype for the Konnaxion Capsule Manager GUI.

Production GUI entrypoint remains `kx_manager/ui/app.py`.

This Streamlit file is only a local prototype. It must not:
- control Docker directly
- execute arbitrary shell commands
- bypass Manager routes, Manager services, or Agent APIs
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Mapping

from kx_manager.defaults import (
    DEFAULT_CAPSULE_FILE,
    DEFAULT_CAPSULE_ID,
    DEFAULT_CAPSULE_OUTPUT_DIR,
    DEFAULT_CAPSULE_VERSION,
    DEFAULT_EXPOSURE_MODE,
    DEFAULT_INSTANCE_ID,
    DEFAULT_NETWORK_PROFILE,
    DEFAULT_RUNTIME_ROOT,
    DEFAULT_SOURCE_DIR,
    DEFAULT_TARGET_MODE,
)

DEFAULT_MANAGER_URL = "http://127.0.0.1:8714"
DEFAULT_AGENT_URL = "http://127.0.0.1:8765/v1"

TARGET_OPTIONS = {
    "local": {
        "label": "Local",
        "network_profile": "local_only",
        "exposure_mode": "private",
    },
    "intranet": {
        "label": "Intranet",
        "network_profile": "intranet_private",
        "exposure_mode": "private",
    },
    "temporary_public": {
        "label": "Temporary Public",
        "network_profile": "public_temporary",
        "exposure_mode": "temporary_tunnel",
    },
    "droplet": {
        "label": "Droplet",
        "network_profile": "public_vps",
        "exposure_mode": "public",
    },
}

BACKUP_CLASSES = [
    "manual",
    "scheduled_daily",
    "scheduled_weekly",
    "scheduled_monthly",
    "pre_update",
    "pre_restore",
]

ACTION_LABELS = {
    "check_manager": "Check Manager",
    "check_agent": "Check Agent",
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
    "check_droplet_agent": "Check Droplet Agent",
    "copy_capsule_to_droplet": "Copy Capsule to Droplet",
    "start_droplet_instance": "Start Droplet Instance",
}


@dataclass(frozen=True)
class ManagerResponse:
    ok: bool
    status_code: int
    data: dict[str, Any]
    error: str | None = None


def _manager_url() -> str:
    return os.environ.get("KX_MANAGER_URL", DEFAULT_MANAGER_URL).rstrip("/")


def _agent_url() -> str:
    return os.environ.get("KX_AGENT_URL", DEFAULT_AGENT_URL).rstrip("/")


def _quote(value: str) -> str:
    return urllib.parse.quote(value.strip(), safe="")


def _get_json(
    url: str,
    *,
    params: Mapping[str, Any] | None = None,
) -> ManagerResponse:
    if params:
        clean_params = {
            key: value
            for key, value in params.items()
            if value is not None and value != ""
        }
        query = urllib.parse.urlencode(clean_params)
        if query:
            url = f"{url}?{query}"

    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json"},
        method="GET",
    )

    return _request_json(request)


def _post_json(url: str, payload: Mapping[str, Any]) -> ManagerResponse:
    body = json.dumps(dict(payload)).encode("utf-8")

    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )

    return _request_json(request)


def _request_json(request: urllib.request.Request) -> ManagerResponse:
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8")
            data = json.loads(raw) if raw else {}
            return ManagerResponse(
                ok=200 <= response.status < 300,
                status_code=response.status,
                data=data if isinstance(data, dict) else {"data": data},
            )

    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            data = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            data = {"message": raw}

        return ManagerResponse(
            ok=False,
            status_code=exc.code,
            data=data if isinstance(data, dict) else {"data": data},
            error=str(exc),
        )

    except Exception as exc:
        return ManagerResponse(
            ok=False,
            status_code=0,
            data={},
            error=str(exc),
        )


def _default_state() -> dict[str, Any]:
    return {
        "source_dir": os.environ.get("KX_SOURCE_DIR", DEFAULT_SOURCE_DIR),
        "runtime_root": os.environ.get("KX_ROOT", DEFAULT_RUNTIME_ROOT),
        "capsule_output_dir": os.environ.get(
            "KX_CAPSULE_OUTPUT_DIR",
            DEFAULT_CAPSULE_OUTPUT_DIR,
        ),
        "capsule_file": os.environ.get("KX_CAPSULE_FILE", DEFAULT_CAPSULE_FILE),
        "capsule_id": os.environ.get("KX_CAPSULE_ID", DEFAULT_CAPSULE_ID),
        "capsule_version": os.environ.get(
            "KX_CAPSULE_VERSION",
            DEFAULT_CAPSULE_VERSION,
        ),
        "instance_id": os.environ.get("KX_INSTANCE_ID", DEFAULT_INSTANCE_ID),
        "target_mode": os.environ.get("KX_TARGET_MODE", DEFAULT_TARGET_MODE),
        "network_profile": os.environ.get(
            "KX_TARGET_PROFILE",
            DEFAULT_NETWORK_PROFILE,
        ),
        "exposure_mode": os.environ.get(
            "KX_TARGET_EXPOSURE",
            DEFAULT_EXPOSURE_MODE,
        ),
        "target_host": os.environ.get("KX_TARGET_HOST", "konnaxion.local"),
        "public_mode_expires_at": os.environ.get("KX_PUBLIC_MODE_EXPIRES_AT", ""),
        "droplet_name": os.environ.get("KX_DROPLET_NAME", ""),
        "droplet_host": os.environ.get("KX_DROPLET_HOST", ""),
        "droplet_user": os.environ.get("KX_DROPLET_USER", "root"),
        "ssh_key_path": os.environ.get("KX_DROPLET_SSH_KEY_PATH", ""),
        "remote_kx_root": os.environ.get("KX_TARGET_RUNTIME_ROOT", "/opt/konnaxion"),
        "remote_capsule_dir": os.environ.get(
            "KX_TARGET_CAPSULE_DIR",
            "/opt/konnaxion/capsules",
        ),
        "domain": os.environ.get("KX_TARGET_PUBLIC_URL", ""),
        "backup_id": "",
        "backup_class": "manual",
        "backup_label": "",
        "backup_reason": "",
        "backup_deep_verify": False,
        "backup_limit": 50,
        "restore_new_instance_id": "demo-restore-001",
        "restore_data": False,
        "confirm_destructive": False,
        "confirm_public": False,
    }


def _ensure_state(st: Any) -> None:
    for key, value in _default_state().items():
        st.session_state.setdefault(key, value)


def _payload(st: Any) -> dict[str, Any]:
    target_mode = st.session_state["target_mode"]
    target = TARGET_OPTIONS[target_mode]

    return {
        "source_dir": st.session_state["source_dir"],
        "runtime_root": st.session_state["runtime_root"],
        "capsule_output_dir": st.session_state["capsule_output_dir"],
        "capsule_file": st.session_state["capsule_file"],
        "capsule_id": st.session_state["capsule_id"],
        "capsule_version": st.session_state["capsule_version"],
        "instance_id": st.session_state["instance_id"],
        "target_mode": target_mode,
        "network_profile": target["network_profile"],
        "exposure_mode": target["exposure_mode"],
        "target_host": st.session_state["target_host"],
        "public_mode_expires_at": st.session_state["public_mode_expires_at"],
        "droplet_name": st.session_state["droplet_name"],
        "droplet_host": st.session_state["droplet_host"],
        "droplet_user": st.session_state["droplet_user"],
        "ssh_key_path": st.session_state["ssh_key_path"],
        "remote_kx_root": st.session_state["remote_kx_root"],
        "remote_capsule_dir": st.session_state["remote_capsule_dir"],
        "domain": st.session_state["domain"],
    }


def _generic_ui_action_url(action: str) -> str:
    return f"{_manager_url()}/ui/actions/{_quote(action)}"


def _run_action(st: Any, action: str) -> ManagerResponse:
    if action == "check_manager":
        return _get_json(f"{_manager_url()}/health")

    if action == "check_agent":
        return _get_json(f"{_agent_url()}/health")

    if action == "list_backups":
        return _get_json(
            f"{_manager_url()}/backups",
            params={
                "instance_id": st.session_state["instance_id"],
                "limit": st.session_state["backup_limit"],
            },
        )

    if action == "create_backup":
        instance_id = _quote(st.session_state["instance_id"])
        return _post_json(
            f"{_manager_url()}/instances/{instance_id}/backups",
            {
                "backup_class": st.session_state["backup_class"],
                "label": st.session_state["backup_label"],
                "include_database": True,
                "include_media": True,
                "include_env_fingerprint": True,
                "verify_after_create": True,
                "reason": st.session_state["backup_reason"],
            },
        )

    if action == "verify_backup":
        backup_id = _quote(st.session_state["backup_id"])
        return _post_json(
            f"{_manager_url()}/backups/{backup_id}/verify",
            {
                "deep": st.session_state["backup_deep_verify"],
                "reason": st.session_state["backup_reason"],
            },
        )

    if action == "restore_backup":
        if not st.session_state["confirm_destructive"]:
            return ManagerResponse(
                ok=False,
                status_code=0,
                data={
                    "ok": False,
                    "message": "Confirm restore / rollback action before restoring.",
                },
                error="Restore confirmation is required.",
            )

        instance_id = _quote(st.session_state["instance_id"])
        return _post_json(
            f"{_manager_url()}/instances/{instance_id}/restore",
            {
                "backup_id": st.session_state["backup_id"],
                "create_pre_restore_backup": True,
                "run_migrations": True,
                "run_security_gate": True,
                "run_healthchecks": True,
                "reason": st.session_state["backup_reason"],
            },
        )

    if action == "restore_backup_new":
        backup_id = st.session_state["backup_id"]
        new_instance_id = st.session_state["restore_new_instance_id"]
        target_mode = st.session_state["target_mode"]
        target = TARGET_OPTIONS[target_mode]

        return _post_json(
            f"{_manager_url()}/instances/restore-new",
            {
                "backup_id": backup_id,
                "new_instance_id": new_instance_id,
                "network_profile": target["network_profile"],
                "exposure_mode": target["exposure_mode"],
                "run_migrations": True,
                "run_security_gate": True,
                "run_healthchecks": True,
                "reason": st.session_state["backup_reason"],
            },
        )

    payload = _payload(st)
    payload["action"] = action
    return _post_json(_generic_ui_action_url(action), payload)


def _render_response(st: Any, response: ManagerResponse) -> None:
    if response.ok:
        st.success(response.data.get("message", "Action completed."))
    else:
        st.error(response.error or response.data.get("message", "Action failed."))

    if response.data:
        st.json(response.data)


def _action_button(
    st: Any,
    action: str,
    *,
    danger: bool = False,
    disabled: bool = False,
) -> None:
    label = ACTION_LABELS[action]
    button_type = "primary" if not danger else "secondary"

    if st.button(label, key=f"button_{action}", type=button_type, disabled=disabled):
        response = _run_action(st, action)
        _render_response(st, response)


def _render_sidebar(st: Any) -> None:
    with st.sidebar:
        st.header("Manager")
        st.text_input("Manager URL", value=_manager_url(), disabled=True)
        st.text_input("Agent URL", value=_agent_url(), disabled=True)

        st.divider()

        st.header("Target")
        selected = st.selectbox(
            "Target Mode",
            options=list(TARGET_OPTIONS.keys()),
            format_func=lambda value: TARGET_OPTIONS[value]["label"],
            key="target_mode",
        )
        target = TARGET_OPTIONS[selected]

        st.text_input(
            "Network Profile",
            value=target["network_profile"],
            disabled=True,
        )
        st.text_input(
            "Exposure Mode",
            value=target["exposure_mode"],
            disabled=True,
        )


def _render_settings(st: Any) -> None:
    st.subheader("Settings")

    col1, col2 = st.columns(2)

    with col1:
        st.text_input("Konnaxion Source Folder", key="source_dir")
        st.text_input("Runtime Root", key="runtime_root")
        st.text_input("Capsule Output Folder", key="capsule_output_dir")
        st.text_input("Capsule File", key="capsule_file")

    with col2:
        st.text_input("Instance ID", key="instance_id")
        st.text_input("Capsule ID", key="capsule_id")
        st.text_input("Capsule Version", key="capsule_version")
        st.text_input("Private Host / Domain", key="target_host")


def _render_dashboard(st: Any) -> None:
    st.subheader("Dashboard")

    col1, col2, col3 = st.columns(3)

    with col1:
        _action_button(st, "check_manager")
        _action_button(st, "check_agent")
        _action_button(st, "run_security_check")

    with col2:
        _action_button(st, "build_capsule")
        _action_button(st, "verify_capsule")
        _action_button(st, "import_capsule")

    with col3:
        _action_button(st, "create_instance")
        _action_button(st, "start_instance")
        _action_button(st, "instance_status")


def _render_capsules(st: Any) -> None:
    st.subheader("Capsules")

    col1, col2, col3 = st.columns(3)

    with col1:
        _action_button(st, "build_capsule")
        _action_button(st, "rebuild_capsule")

    with col2:
        _action_button(st, "verify_capsule")
        _action_button(st, "import_capsule")

    with col3:
        _action_button(st, "list_capsules")
        _action_button(st, "view_capsule")


def _render_instances(st: Any) -> None:
    st.subheader("Instances")

    col1, col2, col3 = st.columns(3)

    with col1:
        _action_button(st, "create_instance")
        _action_button(st, "update_instance")
        _action_button(st, "start_instance")

    with col2:
        _action_button(st, "stop_instance", danger=True)
        _action_button(st, "restart_instance", danger=True)
        _action_button(st, "rollback_instance", danger=True)

    with col3:
        _action_button(st, "instance_status")
        _action_button(st, "view_health")
        _action_button(st, "view_logs")
        _action_button(st, "open_instance")


def _render_targets(st: Any) -> None:
    st.subheader("Targets")

    target_mode = st.session_state["target_mode"]

    if target_mode == "temporary_public":
        st.warning("Temporary public exposure requires an expiration.")
        st.text_input(
            "Public Mode Expiration ISO-8601",
            key="public_mode_expires_at",
            placeholder="2026-04-30T22:00:00Z",
        )
        st.checkbox("Confirm temporary public exposure", key="confirm_public")

    if target_mode == "droplet":
        st.warning("Droplet deployment is public and requires explicit confirmation.")
        st.text_input("Droplet Name", key="droplet_name")
        st.text_input("Droplet Host / IP", key="droplet_host")
        st.text_input("SSH User", key="droplet_user")
        st.text_input("SSH Key Path", key="ssh_key_path")
        st.text_input("Remote KX_ROOT", key="remote_kx_root")
        st.text_input("Remote Capsule Directory", key="remote_capsule_dir")
        st.text_input("Domain / Public Host", key="domain")
        st.checkbox("Confirm public VPS deployment", key="confirm_public")

    col1, col2, col3 = st.columns(3)

    with col1:
        _action_button(st, "set_target_local")
        _action_button(st, "set_target_intranet")
        _action_button(st, "set_target_temporary_public")

    with col2:
        _action_button(st, "set_target_droplet")
        _action_button(st, "deploy_local")
        _action_button(st, "deploy_intranet")

    with col3:
        _action_button(st, "deploy_droplet", danger=True)
        _action_button(st, "check_droplet_agent")
        _action_button(st, "copy_capsule_to_droplet")
        _action_button(st, "start_droplet_instance")


def _render_security(st: Any) -> None:
    st.subheader("Security")
    _action_button(st, "run_security_check")


def _render_network(st: Any) -> None:
    st.subheader("Network")

    col1, col2 = st.columns(2)

    with col1:
        _action_button(st, "set_network_profile")

    with col2:
        _action_button(st, "disable_public_mode", danger=True)


def _render_backups(st: Any) -> None:
    st.subheader("Backups")

    col1, col2 = st.columns(2)

    with col1:
        st.selectbox("Backup Class", BACKUP_CLASSES, key="backup_class")
        st.text_input("Backup ID", key="backup_id")
        st.text_input("Backup Label", key="backup_label")
        st.text_area("Reason", key="backup_reason")

    with col2:
        st.number_input(
            "List Limit",
            min_value=1,
            max_value=500,
            key="backup_limit",
        )
        st.checkbox("Deep verify backup", key="backup_deep_verify")
        st.text_input("New Instance ID", key="restore_new_instance_id")
        st.checkbox("Restore data during rollback", key="restore_data")
        st.checkbox("Confirm restore / rollback action", key="confirm_destructive")

    col3, col4, col5 = st.columns(3)

    with col3:
        _action_button(st, "create_backup")
        _action_button(st, "list_backups")

    with col4:
        _action_button(st, "verify_backup")
        _action_button(st, "test_restore_backup")

    with col5:
        _action_button(st, "restore_backup", danger=True)
        _action_button(st, "restore_backup_new", danger=True)
        _action_button(st, "rollback_instance", danger=True)


def _render_logs(st: Any) -> None:
    st.subheader("Logs")
    _action_button(st, "view_logs")


def _render_docs(st: Any) -> None:
    st.subheader("Docs")

    manager_docs = f"{_manager_url()}/docs"
    agent_docs = _agent_url().removesuffix("/v1") + "/docs"

    st.link_button("Open Manager Docs", manager_docs)
    st.link_button("Open Agent Docs", agent_docs)


def main() -> None:
    import streamlit as st

    st.set_page_config(
        page_title="Konnaxion Capsule Manager",
        page_icon="KX",
        layout="wide",
    )

    _ensure_state(st)

    st.title("Konnaxion Capsule Manager")
    st.caption("Optional Streamlit prototype. Production GUI lives at `/ui`.")

    _render_sidebar(st)

    page = st.tabs(
        [
            "Dashboard",
            "Capsules",
            "Instances",
            "Targets",
            "Security",
            "Network",
            "Backups",
            "Logs",
            "Settings",
            "Docs",
        ]
    )

    with page[0]:
        _render_dashboard(st)

    with page[1]:
        _render_capsules(st)

    with page[2]:
        _render_instances(st)

    with page[3]:
        _render_targets(st)

    with page[4]:
        _render_security(st)

    with page[5]:
        _render_network(st)

    with page[6]:
        _render_backups(st)

    with page[7]:
        _render_logs(st)

    with page[8]:
        _render_settings(st)

    with page[9]:
        _render_docs(st)


if __name__ == "__main__":
    main()