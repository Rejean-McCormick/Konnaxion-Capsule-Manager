"""
Framework-neutral UI page types for Konnaxion Capsule Manager.

This module owns page identity, UI action identity, page metadata types,
navigation model types, alerts, metric cards, and render-model DTOs.

It must not execute Manager, Agent, Builder, Docker, filesystem, network,
backup, restore, or deployment operations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Mapping, Sequence


# ---------------------------------------------------------------------
# Page identity
# ---------------------------------------------------------------------


class PageId(StrEnum):
    DASHBOARD = "dashboard"
    CAPSULES = "capsules"
    CAPSULE_IMPORT = "capsule_import"
    INSTANCES = "instances"
    INSTANCE_DETAIL = "instance_detail"
    INSTANCE_CREATE = "instance_create"
    SECURITY = "security"
    NETWORK = "network"
    BACKUPS = "backups"
    RESTORE = "restore"
    LOGS = "logs"
    HEALTH = "health"
    SETTINGS = "settings"
    ABOUT = "about"
    TARGETS = "targets"
    DEPLOY = "deploy"


class UiPage(StrEnum):
    """Public FastAPI Manager UI routes.

    These values are the browser-facing `/ui/...` routes used by the FastAPI UI
    contract tests. `PageId` remains the framework-neutral page identity enum.
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


class PageGroup(StrEnum):
    OVERVIEW = "overview"
    OPERATIONS = "operations"
    DEPLOYMENT = "deployment"
    SAFETY = "safety"
    SYSTEM = "system"


# ---------------------------------------------------------------------
# UI actions
# ---------------------------------------------------------------------


class UiAction(StrEnum):
    CHECK_MANAGER = "check_manager"
    CHECK_AGENT = "check_agent"

    SELECT_SOURCE_FOLDER = "select_source_folder"
    SELECT_CAPSULE_OUTPUT_FOLDER = "select_capsule_output_folder"

    BUILD_CAPSULE = "build_capsule"
    REBUILD_CAPSULE = "rebuild_capsule"
    VERIFY_CAPSULE = "verify_capsule"
    IMPORT_CAPSULE = "import_capsule"
    LIST_CAPSULES = "list_capsules"
    VIEW_CAPSULE = "view_capsule"

    CREATE_INSTANCE = "create_instance"
    UPDATE_INSTANCE = "update_instance"
    START_INSTANCE = "start_instance"
    STOP_INSTANCE = "stop_instance"
    RESTART_INSTANCE = "restart_instance"
    INSTANCE_STATUS = "instance_status"
    VIEW_LOGS = "view_logs"
    VIEW_HEALTH = "view_health"
    OPEN_INSTANCE = "open_instance"
    ROLLBACK_INSTANCE = "rollback_instance"

    CREATE_BACKUP = "create_backup"
    LIST_BACKUPS = "list_backups"
    VERIFY_BACKUP = "verify_backup"
    RESTORE_BACKUP = "restore_backup"
    RESTORE_BACKUP_NEW = "restore_backup_new"
    TEST_RESTORE_BACKUP = "test_restore_backup"

    RUN_SECURITY_CHECK = "run_security_check"

    SET_NETWORK_PROFILE = "set_network_profile"
    DISABLE_PUBLIC_MODE = "disable_public_mode"

    SET_TARGET_LOCAL = "set_target_local"
    SET_TARGET_INTRANET = "set_target_intranet"
    SET_TARGET_DROPLET = "set_target_droplet"
    SET_TARGET_TEMPORARY_PUBLIC = "set_target_temporary_public"

    DEPLOY_LOCAL = "deploy_local"
    DEPLOY_INTRANET = "deploy_intranet"
    DEPLOY_DROPLET = "deploy_droplet"
    INITIALIZE_PRODUCTION_DATA = "initialize_production_data"

    BOOTSTRAP_DROPLET_AGENT = "bootstrap_droplet_agent"
    CHECK_DROPLET_AGENT = "check_droplet_agent"
    COPY_CAPSULE_TO_DROPLET = "copy_capsule_to_droplet"
    START_DROPLET_INSTANCE = "start_droplet_instance"

    OPEN_MANAGER_DOCS = "open_manager_docs"
    OPEN_AGENT_DOCS = "open_agent_docs"


class ActionIntent(StrEnum):
    PRIMARY = "primary"
    SECONDARY = "secondary"
    WARNING = "warning"
    DANGER = "danger"


# ---------------------------------------------------------------------
# Page/action metadata
# ---------------------------------------------------------------------


@dataclass(slots=True, frozen=True)
class PageAction:
    """UI action metadata.

    The actual operation must be executed by the Manager API/Agent, not by this
    UI metadata layer.
    """

    action: UiAction
    label: str
    intent: ActionIntent = ActionIntent.SECONDARY
    requires_confirmation: bool = False
    disabled: bool = False
    reason_disabled: str = ""
    payload: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action.value,
            "label": self.label,
            "intent": self.intent.value,
            "requires_confirmation": self.requires_confirmation,
            "disabled": self.disabled,
            "reason_disabled": self.reason_disabled,
            "payload": dict(self.payload),
        }


@dataclass(slots=True, frozen=True)
class PageDefinition:
    page_id: PageId
    title: str
    route: str
    group: PageGroup
    nav_label: str | None = None
    description: str = ""
    icon: str = ""
    order: int = 100
    visible_in_nav: bool = True
    requires_instance: bool = False

    def nav_text(self) -> str:
        return self.nav_label or self.title

    def to_dict(self) -> dict[str, Any]:
        return {
            "page_id": self.page_id.value,
            "title": self.title,
            "route": self.route,
            "group": self.group.value,
            "nav_label": self.nav_text(),
            "description": self.description,
            "icon": self.icon,
            "order": self.order,
            "visible_in_nav": self.visible_in_nav,
            "requires_instance": self.requires_instance,
        }


@dataclass(slots=True, frozen=True)
class NavigationGroup:
    group: PageGroup
    label: str
    pages: Sequence[PageDefinition]

    def to_dict(self) -> dict[str, Any]:
        return {
            "group": self.group.value,
            "label": self.label,
            "pages": [page.to_dict() for page in self.pages],
        }


@dataclass(slots=True, frozen=True)
class PageContext:
    """Current UI context shared across page render builders."""

    selected_instance_id: str | None = None
    selected_capsule_id: str | None = None
    selected_backup_id: str | None = None
    current_route: str = "/"
    user_display_name: str | None = None


# ---------------------------------------------------------------------
# Render DTOs
# ---------------------------------------------------------------------


@dataclass(slots=True, frozen=True)
class Alert:
    level: str
    title: str
    message: str
    details: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "title": self.title,
            "message": self.message,
            "details": dict(self.details),
        }


@dataclass(slots=True, frozen=True)
class MetricCard:
    label: str
    value: str
    help_text: str = ""
    status: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "value": self.value,
            "help_text": self.help_text,
            "status": self.status,
        }


@dataclass(slots=True, frozen=True)
class PageRenderModel:
    """Framework-neutral model consumed by the UI renderer."""

    page: PageDefinition
    title: str
    subtitle: str = ""
    metrics: Sequence[MetricCard] = field(default_factory=tuple)
    alerts: Sequence[Alert] = field(default_factory=tuple)
    actions: Sequence[PageAction] = field(default_factory=tuple)
    sections: Mapping[str, Any] = field(default_factory=dict)
    breadcrumbs: Sequence[str] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "page": self.page.to_dict(),
            "title": self.title,
            "subtitle": self.subtitle,
            "metrics": [metric.to_dict() for metric in self.metrics],
            "alerts": [alert.to_dict() for alert in self.alerts],
            "actions": [action.to_dict() for action in self.actions],
            "sections": dict(self.sections),
            "breadcrumbs": list(self.breadcrumbs),
        }


__all__ = [
    "ActionIntent",
    "Alert",
    "MetricCard",
    "NavigationGroup",
    "PageAction",
    "PageContext",
    "PageDefinition",
    "PageGroup",
    "PageId",
    "PageRenderModel",
    "UiAction",
    "UiPage",
]