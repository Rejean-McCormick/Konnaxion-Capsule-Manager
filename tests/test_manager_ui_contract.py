"""
Contract tests for the Konnaxion Capsule Manager GUI.

These tests intentionally verify module boundaries and public contracts instead
of implementation details. The GUI must remain FastAPI-compatible, local-first,
canonical-value driven, and free from arbitrary shell execution.
"""

from __future__ import annotations

import builtins
import importlib
import inspect
import sys
from enum import Enum
from pathlib import Path
from types import ModuleType
from typing import Any, Iterable, Mapping

import pytest


REQUIRED_UI_MODULES = (
    "kx_manager.ui",
    "kx_manager.ui.app",
    "kx_manager.ui.pages",
    "kx_manager.ui.state",
    "kx_manager.ui.components",
    "kx_manager.ui.actions",
    "kx_manager.ui.forms",
    "kx_manager.ui.render",
)

OPTIONAL_UI_MODULES = (
    "kx_manager.ui.streamlit_app",
)

REQUIRED_MANAGER_SERVICE_MODULES = (
    "kx_manager.services.builder",
    "kx_manager.services.targets",
    "kx_manager.services.deploy",
    "kx_manager.services.netcup",
)

REQUIRED_PAGE_ROUTES = (
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
    "/ui/about",
    "/ui/targets",
    "/ui/deploy",
)

REQUIRED_UI_ACTION_VALUES = (
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
    "scan_netcup_host_key",
    "provision_netcup_vps",
    "initialize_production_data",
    "bootstrap_droplet_agent",
    "check_droplet_agent",
    "copy_capsule_to_droplet",
    "start_droplet_instance",
    "open_manager_docs",
    "open_agent_docs",
)

REQUIRED_ACTION_ROUTE_VALUES = (
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
    "/ui/actions/scan-netcup-host-key",
    "/ui/actions/provision-netcup-vps",
    "/ui/actions/initialize-production-data",
    "/ui/actions/bootstrap-droplet-agent",
    "/ui/actions/check-droplet-agent",
    "/ui/actions/copy-capsule-to-droplet",
    "/ui/actions/start-droplet-instance",
)

REQUIRED_LABELS = (
    "Check Manager",
    "Check Agent",
    "Select Source Folder",
    "Select Output Folder",
    "Build Capsule",
    "Rebuild Capsule",
    "Verify Capsule",
    "Import Capsule",
    "List Capsules",
    "View Capsule",
    "Create Instance",
    "Update Instance",
    "Start Instance",
    "Stop Instance",
    "Restart Instance",
    "Instance Status",
    "View Logs",
    "Instance Health",
    "Open Instance",
    "Rollback",
    "Create Backup",
    "List Backups",
    "Verify Backup",
    "Restore Backup",
    "Restore Backup New",
    "Test Restore Backup",
    "Run Security Check",
    "Set Network Profile",
    "Disable Public Mode",
    "Set Local Target",
    "Set Intranet Target",
    "Set Droplet Target",
    "Set Temporary Public Target",
    "Deploy Local",
    "Deploy Intranet",
    "Deploy Droplet",
    "Bootstrap Droplet Agent",
    "Check Droplet Agent",
    "Copy Capsule to Droplet",
    "Start Droplet Instance",
    "Open Manager Docs",
    "Open Agent Docs",
)

REQUIRED_STATE_MODELS = (
    "CapsuleUiState",
    "SecurityCheckUiState",
    "SecurityUiState",
    "NetworkUiState",
    "BackupUiState",
    "InstanceUiState",
    "ManagerUiState",
    "TargetModeUiState",
    "DropletTargetUiState",
    "BuildTargetUiState",
)

REQUIRED_FORM_MODELS = (
    "BuildCapsuleForm",
    "VerifyCapsuleForm",
    "ImportCapsuleForm",
    "CreateInstanceForm",
    "UpdateInstanceForm",
    "InstanceActionForm",
    "LogsForm",
    "BackupForm",
    "RestoreForm",
    "RollbackForm",
    "NetworkProfileForm",
    "TargetModeForm",
    "DropletTargetForm",
    "BootstrapDropletAgentForm",
    "CheckDropletAgentForm",
    "CopyCapsuleToDropletForm",
    "DeployDropletForm",
    "StartDropletInstanceForm",
)

REQUIRED_GUI_ACTION_RESULT_FIELDS = (
    "ok",
    "action",
    "message",
    "instance_id",
    "data",
    "stdout",
    "stderr",
    "returncode",
)

REQUIRED_BUILDER_FUNCTIONS = (
    "build_capsule",
    "verify_capsule",
)

REQUIRED_TARGET_FUNCTIONS = (
    "validate_target_config",
    "network_profile_for_target",
    "exposure_mode_for_target",
)

REQUIRED_DEPLOY_FUNCTIONS = (
    "deploy_local",
    "deploy_intranet",
    "deploy_droplet",
)

REQUIRED_TARGET_MODE_VALUES = (
    "local",
    "intranet",
    "temporary_public",
    "droplet",
)

FORBIDDEN_TARGET_MODE_VALUES = (
    "dev",
    "demo",
    "lan_private",
    "vps",
    "server",
    "production",
    "cloud",
    "public_server",
)


def test_required_ui_modules_import() -> None:
    for module_name in REQUIRED_UI_MODULES:
        module = importlib.import_module(module_name)
        assert isinstance(module, ModuleType), module_name


def test_optional_streamlit_app_module_is_isolated_if_present() -> None:
    for module_name in OPTIONAL_UI_MODULES:
        try:
            module = importlib.import_module(module_name)
        except ModuleNotFoundError:
            continue
        assert isinstance(module, ModuleType), module_name


def test_required_manager_service_modules_import() -> None:
    for module_name in REQUIRED_MANAGER_SERVICE_MODULES:
        module = importlib.import_module(module_name)
        assert isinstance(module, ModuleType), module_name


def test_fastapi_ui_register_exists() -> None:
    app_module = importlib.import_module("kx_manager.ui.app")

    assert hasattr(app_module, "register")
    assert callable(app_module.register)

    signature = inspect.signature(app_module.register)
    assert "app" in signature.parameters


def test_fastapi_ui_import_does_not_require_streamlit(monkeypatch: pytest.MonkeyPatch) -> None:
    """FastAPI GUI import must not import or require Streamlit."""

    real_import = builtins.__import__

    def guarded_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "streamlit" or name.startswith("streamlit."):
            raise ModuleNotFoundError("streamlit intentionally blocked by contract test")
        return real_import(name, *args, **kwargs)

    monkeypatch.setitem(sys.modules, "streamlit", None)
    monkeypatch.setattr(builtins, "__import__", guarded_import)

    module = importlib.reload(importlib.import_module("kx_manager.ui.app"))
    assert hasattr(module, "register")
    assert callable(module.register)


def test_pages_module_exposes_routes_and_actions() -> None:
    pages = importlib.import_module("kx_manager.ui.pages")

    assert hasattr(pages, "UiPage")
    assert hasattr(pages, "UiAction")


def test_uipage_values_cover_required_routes() -> None:
    pages = importlib.import_module("kx_manager.ui.pages")

    route_values = set(_enum_values(pages.UiPage))
    expected = set(REQUIRED_PAGE_ROUTES)

    assert expected <= route_values


def test_uipage_does_not_include_forbidden_routes() -> None:
    pages = importlib.import_module("kx_manager.ui.pages")

    route_values = set(_enum_values(pages.UiPage))

    assert "/ui/development" not in route_values
    assert "/ui/debug" not in route_values
    assert "/ui/admin" not in route_values


def test_uiaction_values_match_contract() -> None:
    pages = importlib.import_module("kx_manager.ui.pages")

    assert hasattr(pages, "UiAction"), "kx_manager.ui.pages must expose UiAction"

    values = set(_enum_values(pages.UiAction))
    expected = set(REQUIRED_UI_ACTION_VALUES)

    assert values == expected, (
        "UiAction values must match the GUI action contract. "
        f"Missing={sorted(expected - values)} Extra={sorted(values - expected)}"
    )


def test_uiaction_count_matches_explicit_contract_list() -> None:
    pages = importlib.import_module("kx_manager.ui.pages")
    values = tuple(_enum_values(pages.UiAction))

    assert len(values) == len(REQUIRED_UI_ACTION_VALUES)


def test_ui_routes_are_unique() -> None:
    pages = importlib.import_module("kx_manager.ui.pages")

    routes = tuple(_enum_values(pages.UiPage))

    assert len(routes) == len(set(routes))


def test_ui_actions_are_unique() -> None:
    pages = importlib.import_module("kx_manager.ui.pages")

    actions = tuple(_enum_values(pages.UiAction))

    assert len(actions) == len(set(actions))


def test_required_action_routes_are_registered() -> None:
    actions = importlib.import_module("kx_manager.ui.actions")

    route_values = _collect_route_values(
        actions,
        candidate_names=(
            "ACTION_ROUTES",
            "POST_ACTION_ROUTES",
            "UI_ACTION_ROUTES",
            "ROUTES",
        ),
    )

    assert set(REQUIRED_ACTION_ROUTE_VALUES) <= route_values


def test_required_action_labels_are_registered() -> None:
    actions = importlib.import_module("kx_manager.ui.actions")

    labels = _collect_label_values(actions)

    assert set(REQUIRED_LABELS) <= labels


def test_no_streamlit_symbols_in_fastapi_ui_public_contract() -> None:
    modules = [
        importlib.import_module("kx_manager.ui.app"),
        importlib.import_module("kx_manager.ui.pages"),
        importlib.import_module("kx_manager.ui.actions"),
    ]

    for module in modules:
        public_names = set(getattr(module, "__all__", ()))
        for name in public_names:
            assert "streamlit" not in name.lower()


def test_ui_state_models_exist() -> None:
    state = importlib.import_module("kx_manager.ui.state")

    missing = [
        name
        for name in REQUIRED_STATE_MODELS
        if not hasattr(state, name)
    ]

    assert not missing, f"Missing UI state models: {missing}"


def test_ui_state_models_are_dataclass_or_pydantic_like() -> None:
    state = importlib.import_module("kx_manager.ui.state")

    for name in REQUIRED_STATE_MODELS:
        model = getattr(state, name)
        fields = _dataclass_or_annotation_fields(model)

        assert fields, f"{name} must expose dataclass/model fields"


def test_manager_ui_state_includes_core_sections() -> None:
    state = importlib.import_module("kx_manager.ui.state")
    model = getattr(state, "ManagerUiState")

    fields = _dataclass_or_annotation_fields(model)

    assert "capsules" in fields
    assert "instances" in fields
    assert "security" in fields
    assert "network" in fields
    assert "backups" in fields


def test_target_state_models_cover_canonical_modes() -> None:
    state = importlib.import_module("kx_manager.ui.state")

    target_model = getattr(state, "TargetModeUiState")
    fields = _dataclass_or_annotation_fields(target_model)

    assert "target_mode" in fields
    assert "network_profile" in fields
    assert "exposure_mode" in fields


def test_droplet_target_state_requires_public_vps_metadata() -> None:
    state = importlib.import_module("kx_manager.ui.state")

    model = getattr(state, "DropletTargetUiState")
    fields = _dataclass_or_annotation_fields(model)

    assert "droplet_host" in fields
    assert "droplet_user" in fields
    assert "ssh_key_path" in fields
    assert "remote_kx_root" in fields


def test_form_models_exist() -> None:
    forms = importlib.import_module("kx_manager.ui.forms")

    missing = [
        name
        for name in REQUIRED_FORM_MODELS
        if not hasattr(forms, name)
    ]

    assert not missing, f"Missing form models: {missing}"


def test_form_registry_knows_required_actions() -> None:
    forms = importlib.import_module("kx_manager.ui.forms")

    registry_keys = _collect_mapping_keys(
        forms,
        candidate_names=(
            "ACTION_FORM_MODELS",
            "FORM_REGISTRY",
            "ACTION_FORMS",
        ),
    )

    assert set(REQUIRED_UI_ACTION_VALUES) <= registry_keys


def test_form_validation_function_exists() -> None:
    forms = importlib.import_module("kx_manager.ui.forms")

    assert any(
        callable(getattr(forms, name, None))
        for name in (
            "validate_action_payload",
            "parse_action_form",
            "form_to_payload",
        )
    )


def test_gui_action_result_contract_fields() -> None:
    actions = importlib.import_module("kx_manager.ui.actions")

    result_cls = getattr(actions, "GuiActionResult", None)
    assert result_cls is not None

    fields = _dataclass_or_annotation_fields(result_cls)

    assert set(REQUIRED_GUI_ACTION_RESULT_FIELDS) <= fields


def test_action_dispatcher_exists() -> None:
    actions = importlib.import_module("kx_manager.ui.actions")

    dispatcher = getattr(actions, "dispatch_gui_action", None)

    assert callable(dispatcher)


def test_actions_module_references_approved_backends_only() -> None:
    actions = importlib.import_module("kx_manager.ui.actions")
    source = _safe_getsource(actions)

    approved_markers = (
        "KonnaxionAgentClient",
        "kx_manager.services.builder",
        "kx_manager.services.targets",
        "kx_manager.services.deploy",
        "kx_builder",
        "Manager route",
        "Agent API",
    )

    assert any(marker in source for marker in approved_markers)


def test_actions_module_does_not_use_shell_true_or_os_system() -> None:
    actions = importlib.import_module("kx_manager.ui.actions")
    source = _safe_getsource(actions)

    forbidden_snippets = (
        "shell=True",
        "os.system(",
        "subprocess.Popen(",
        "subprocess.call(",
    )

    for snippet in forbidden_snippets:
        assert snippet not in source


def test_builder_service_functions_exist() -> None:
    builder = importlib.import_module("kx_manager.services.builder")

    for name in REQUIRED_BUILDER_FUNCTIONS:
        assert callable(getattr(builder, name, None)), name


def test_target_service_functions_exist() -> None:
    targets = importlib.import_module("kx_manager.services.targets")

    for name in REQUIRED_TARGET_FUNCTIONS:
        assert callable(getattr(targets, name, None)), name


def test_deploy_service_functions_exist() -> None:
    deploy = importlib.import_module("kx_manager.services.deploy")

    for name in REQUIRED_DEPLOY_FUNCTIONS:
        assert callable(getattr(deploy, name, None)), name


def test_deploy_service_does_not_execute_docker_or_firewall_directly_from_ui() -> None:
    deploy = importlib.import_module("kx_manager.services.deploy")
    source = _safe_getsource(deploy)

    assert "docker compose" not in source.lower()
    assert "ufw " not in source.lower()
    assert "iptables" not in source.lower()


def test_render_module_exposes_page_render_helpers() -> None:
    render = importlib.import_module("kx_manager.ui.render")

    assert any(
        callable(getattr(render, name, None))
        for name in (
            "render_page",
            "render_layout",
            "render_card",
            "render_table",
        )
    )


def test_components_module_exposes_core_components() -> None:
    components = importlib.import_module("kx_manager.ui.components")

    component_names = set(dir(components))

    assert any("card" in name.lower() for name in component_names)
    assert any("status" in name.lower() for name in component_names)


def test_target_mode_values_are_canonical() -> None:
    pages = importlib.import_module("kx_manager.ui.pages")

    target_mode_enum = getattr(pages, "UiTargetMode", None)

    if target_mode_enum is None:
        pytest.skip("UiTargetMode is not implemented yet")

    values = set(_enum_values(target_mode_enum))

    assert set(REQUIRED_TARGET_MODE_VALUES) <= values
    assert set(FORBIDDEN_TARGET_MODE_VALUES).isdisjoint(values)


def test_network_profile_labels_are_canonical_if_present() -> None:
    pages = importlib.import_module("kx_manager.ui.pages")

    profile_enum = getattr(pages, "UiNetworkProfile", None)

    if profile_enum is None:
        pytest.skip("UiNetworkProfile is not implemented yet")

    values = set(_enum_values(profile_enum))

    assert "intranet_private" in values
    assert "public_vps" in values
    assert "local_only" in values
    assert "lan_private" not in values
    assert "vps" not in values


def test_page_routes_do_not_include_dynamic_instance_ids() -> None:
    pages = importlib.import_module("kx_manager.ui.pages")

    route_values = set(_enum_values(pages.UiPage))

    assert not any("{instance_id}" in value for value in route_values)
    assert not any("<instance_id>" in value for value in route_values)


def test_action_values_use_snake_case() -> None:
    pages = importlib.import_module("kx_manager.ui.pages")

    for value in _enum_values(pages.UiAction):
        assert value == value.lower()
        assert "-" not in value
        assert " " not in value


def test_action_routes_use_kebab_case() -> None:
    actions = importlib.import_module("kx_manager.ui.actions")

    route_values = _collect_route_values(
        actions,
        candidate_names=(
            "ACTION_ROUTES",
            "POST_ACTION_ROUTES",
            "UI_ACTION_ROUTES",
            "ROUTES",
        ),
    )

    for route in route_values:
        if not route.startswith("/ui/actions/"):
            continue

        suffix = route.removeprefix("/ui/actions/")
        assert "_" not in suffix
        assert suffix == suffix.lower()


def test_private_target_actions_do_not_require_public_confirmation() -> None:
    forms = importlib.import_module("kx_manager.ui.forms")

    validate = getattr(forms, "validate_action_payload", None)

    if not callable(validate):
        pytest.skip("validate_action_payload is not implemented yet")

    local_payload = {
        "action": "set_target_local",
        "target_mode": "local",
        "network_profile": "local_only",
        "exposure_mode": "private",
        "instance_id": "demo-001",
        "runtime_root": r"C:\mycode\Konnaxion\runtime",
        "capsule_dir": r"C:\mycode\Konnaxion\runtime\capsules",
    }

    result = validate(local_payload)
    assert result is not None


def test_droplet_target_requires_confirmation_if_validator_exists() -> None:
    forms = importlib.import_module("kx_manager.ui.forms")

    validate = getattr(forms, "validate_action_payload", None)

    if not callable(validate):
        pytest.skip("validate_action_payload is not implemented yet")

    payload = {
        "action": "set_target_droplet",
        "target_mode": "droplet",
        "network_profile": "public_vps",
        "exposure_mode": "public",
        "instance_id": "demo-001",
        "droplet_host": "203.0.113.10",
        "droplet_user": "root",
        "ssh_key_path": r"C:\Users\user\.ssh\id_ed25519",
        "remote_kx_root": "/opt/konnaxion",
        "remote_capsule_dir": "/opt/konnaxion/capsules",
        "domain": "example.com",
        "confirmed": False,
    }

    with pytest.raises(Exception):
        validate(payload)


def test_manager_gui_contract_document_mentions_required_targets() -> None:
    """Docs should mention canonical target modes if present in repository."""

    docs_root = Path("docs")
    if not docs_root.exists():
        pytest.skip("docs directory is not available")

    content = "\n".join(
        path.read_text(encoding="utf-8")
        for path in docs_root.glob("DOC-*GUI*.md")
    )

    assert "local" in content
    assert "intranet" in content
    assert "temporary_public" in content
    assert "droplet" in content


def _enum_values(enum_cls: Any) -> tuple[str, ...]:
    if not inspect.isclass(enum_cls):
        return ()

    if issubclass(enum_cls, Enum):
        return tuple(str(item.value) for item in enum_cls)

    values: list[str] = []
    for name in dir(enum_cls):
        if name.startswith("_"):
            continue
        value = getattr(enum_cls, name)
        if isinstance(value, str):
            values.append(value)

    return tuple(values)


def _string_value(value: Any) -> str:
    return str(getattr(value, "value", value))


def _collect_route_values(module: ModuleType, *, candidate_names: tuple[str, ...]) -> set[str]:
    values: set[str] = set()

    for name in candidate_names:
        obj = getattr(module, name, None)
        values.update(_extract_routes(obj))

    return values


def _extract_routes(obj: Any) -> set[str]:
    if obj is None:
        return set()

    if isinstance(obj, str):
        return {obj}

    if isinstance(obj, Mapping):
        routes: set[str] = set()

        for key, value in obj.items():
            routes.update(_extract_routes(key))
            routes.update(_extract_routes(value))

        return routes

    if isinstance(obj, Iterable):
        routes = set()

        for item in obj:
            routes.update(_extract_routes(item))

        return routes

    routes = set()

    for attr_name in ("route", "path", "url", "href"):
        value = getattr(obj, attr_name, None)
        if isinstance(value, str):
            routes.add(value)

    return routes


def _collect_label_values(module: ModuleType) -> set[str]:
    labels: set[str] = set()

    for name in ("UI_ACTION_LABELS", "ACTION_LABELS", "LABELS", "PAGE_LABELS"):
        obj = getattr(module, name, None)

        if isinstance(obj, Mapping):
            labels.update(str(value) for value in obj.values())
        elif isinstance(obj, Iterable) and not isinstance(obj, (str, bytes)):
            labels.update(str(value) for value in obj)

    return labels


def _collect_mapping_keys(module: ModuleType, *, candidate_names: tuple[str, ...]) -> set[str]:
    keys: set[str] = set()

    for name in candidate_names:
        obj = getattr(module, name, None)

        if isinstance(obj, Mapping):
            keys.update(_string_value(key) for key in obj.keys())

    return keys


def _dataclass_or_annotation_fields(model: Any) -> set[str]:
    annotations = getattr(model, "__annotations__", None)
    if isinstance(annotations, Mapping):
        return set(str(key) for key in annotations)

    dataclass_fields = getattr(model, "__dataclass_fields__", None)
    if isinstance(dataclass_fields, Mapping):
        return set(str(key) for key in dataclass_fields)

    model_fields = getattr(model, "model_fields", None)
    if isinstance(model_fields, Mapping):
        return set(str(key) for key in model_fields)

    fields = getattr(model, "__fields__", None)
    if isinstance(fields, Mapping):
        return set(str(key) for key in fields)

    return set()


def _safe_getsource(module: ModuleType) -> str:
    try:
        return inspect.getsource(module)
    except (OSError, TypeError):
        file_value = getattr(module, "__file__", None)
        if not file_value:
            return ""

        path = Path(file_value)
        if not path.exists():
            return ""

        return path.read_text(encoding="utf-8")