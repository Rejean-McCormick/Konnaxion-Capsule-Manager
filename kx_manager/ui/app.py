"""FastAPI route registration for the Konnaxion Capsule Manager GUI.

This module owns the local /ui browser route surface.

It must remain importable without optional prototype UI dependencies.
Privileged work is not performed here. POST action routes delegate to
kx_manager.ui.actions.dispatch_gui_action, which routes through approved
Manager clients, service wrappers, or Agent APIs.
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from html import escape
import inspect
import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping

from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from kx_manager.defaults import (
    DEFAULT_CAPSULE_FILE,
    DEFAULT_CAPSULE_ID,
    DEFAULT_CAPSULE_OUTPUT_DIR,
    DEFAULT_CAPSULE_VERSION,
    DEFAULT_EXPOSURE_MODE,
    DEFAULT_NETWORK_PROFILE,
    DEFAULT_TARGET_MODE,
    auto_capsule_naming_enabled,
)
from kx_manager.ui.static import (
    ACTION_ROUTES,
    APP_ICON,
    APP_TITLE,
    BROWSER_LINK_ACTIONS,
    DEFAULT_REFRESH_SECONDS,
    UI_PAGE_ROUTES,
    normalize_payload_aliases,
    title_for_route,
)


if sys.modules.get("streamlit") is None:
    sys.modules.pop("streamlit", None)


BROWSER_ONLY_ACTIONS: frozenset[str] = frozenset(BROWSER_LINK_ACTIONS)

FALLBACK_UI_PAGE_ROUTES = UI_PAGE_ROUTES
FALLBACK_UI_ACTION_ROUTES = {
    str(action): str(route)
    for action, route in ACTION_ROUTES.items()
    if str(action) not in BROWSER_ONLY_ACTIONS
}

UI_ASSETS_DIR = Path(__file__).with_name("assets")
UI_ASSETS_ROUTE = "/ui/assets"
APP_LOGO_FILENAME = "LogoK.svg"
APP_LOGO_SRC = f"{UI_ASSETS_ROUTE}/{APP_LOGO_FILENAME}"
APP_LOGO_ALT = "Konnaxion"

MANAGER_HEALTH_ROUTES: tuple[str, ...] = (
    "/health",
    "/v1/health",
)

TARGET_ACTIONS: frozenset[str] = frozenset(
    {
        "set_target_local",
        "set_target_intranet",
        "set_target_droplet",
        "set_target_temporary_public",
    }
)

CONTEXT_PERSISTING_ACTIONS: frozenset[str] = TARGET_ACTIONS | frozenset(
    {
        "select_source_folder",
        "select_capsule_output_folder",
        "build_capsule",
        "rebuild_capsule",
        "verify_capsule",
        "import_capsule",
        "deploy_local",
        "deploy_intranet",
        "deploy_droplet",
        "bootstrap_droplet_agent",
        "check_droplet_agent",
        "copy_capsule_to_droplet",
        "start_droplet_instance",
    }
)

PERSISTABLE_UI_CONTEXT_KEYS: frozenset[str] = frozenset(
    {
        "instance_id",
        "source_dir",
        "capsule_output_dir",
        "output_dir",
        "capsule_id",
        "capsule_version",
        "version",
        "capsule_file",
        "capsule_path",
        "runtime_root",
        "capsule_dir",
        "target_capsule_dir",
        "target_mode",
        "network_profile",
        "exposure_mode",
        "public_mode_enabled",
        "public_mode_expires_at",
        "confirmed",
        "host",
        "private_host",
        "public_host",
        "droplet_name",
        "droplet_host",
        "target_host",
        "droplet_user",
        "ssh_user",
        "user",
        "ssh_key_path",
        "ssh_key",
        "droplet_ssh_key",
        "ssh_port",
        "remote_kx_root",
        "remote_root",
        "droplet_kx_root",
        "remote_capsule_dir",
        "droplet_capsule_dir",
        "domain",
        "droplet_domain",
        "remote_agent_url",
        "droplet_agent_url",
        "runtime_url",
    }
)


FALLBACK_NAV_ITEMS: tuple[tuple[str, str], ...] = (
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


try:
    from kx_manager.ui.actions import dispatch_gui_action
except Exception:  # pragma: no cover - staged build compatibility
    dispatch_gui_action = None  # type: ignore[assignment]


def _load_page_routes() -> tuple[str, ...]:
    """Return canonical GUI page routes."""

    return tuple(str(route) for route in UI_PAGE_ROUTES)


def _load_action_routes() -> dict[str, str]:
    """Return canonical GUI action POST routes."""

    return {
        str(action): str(route)
        for action, route in ACTION_ROUTES.items()
        if str(action) not in BROWSER_ONLY_ACTIONS
    }


def _load_dispatcher() -> Any:
    """Return the current GUI dispatcher.

    This resolves through the module attribute first so tests and staged builds
    can monkeypatch ``kx_manager.ui.app.dispatch_gui_action`` without touching
    route registration.
    """

    dispatcher = dispatch_gui_action

    if dispatcher is not None:
        return dispatcher

    from kx_manager.ui.actions import dispatch_gui_action as loaded_dispatcher

    return loaded_dispatcher


def _page_title_for_route(route: str) -> str:
    return title_for_route(route)


def _coerce_html_response(value: Any, *, status_code: int = 200) -> HTMLResponse:
    if isinstance(value, HTMLResponse):
        return value

    if isinstance(value, bytes):
        content = value.decode("utf-8", errors="replace")
    else:
        content = str(value)

    return HTMLResponse(content=content, status_code=status_code)


def _fallback_nav_html() -> str:
    return "".join(
        f'<a href="{escape(href)}">{escape(label)}</a>'
        for label, href in FALLBACK_NAV_ITEMS
    )


def _ui_state_file() -> Path:
    """Return the local Manager GUI state file path."""

    explicit = os.getenv("KX_MANAGER_UI_STATE_FILE", "").strip()
    if explicit:
        return Path(explicit)

    runtime_root = os.getenv("KX_ROOT", "").strip()
    if runtime_root:
        return Path(runtime_root) / "shared" / "manager-ui-state.json"

    return Path.cwd() / ".kx-ui" / "manager-ui-state.json"


def _environment_ui_context() -> dict[str, Any]:
    """Return explicit launcher overrides plus automatic capsule naming values."""

    env_keys = {
        "KX_INSTANCE_ID": "instance_id",
        "KX_SOURCE_DIR": "source_dir",
        "KX_CAPSULE_OUTPUT_DIR": "capsule_output_dir",
        "KX_TARGET_MODE": "target_mode",
        "KX_TARGET_PROFILE": "network_profile",
        "KX_TARGET_EXPOSURE": "exposure_mode",
        "KX_ROOT": "runtime_root",
        "KX_CAPSULE_ID": "capsule_id",
        "KX_CAPSULE_VERSION": "capsule_version",
        "KX_CAPSULE_FILE": "capsule_file",
    }

    context: dict[str, Any] = {}
    for env_name, context_name in env_keys.items():
        value = os.getenv(env_name, "").strip()
        if value:
            context[context_name] = value

    if auto_capsule_naming_enabled():
        context["capsule_id"] = os.getenv("KX_CAPSULE_ID", "").strip() or DEFAULT_CAPSULE_ID
        context["capsule_version"] = (
            os.getenv("KX_CAPSULE_VERSION", "").strip() or DEFAULT_CAPSULE_VERSION
        )
        output_dir = str(
            context.get("capsule_output_dir")
            or os.getenv("KX_CAPSULE_OUTPUT_DIR", "").strip()
            or DEFAULT_CAPSULE_OUTPUT_DIR
        )
        explicit_file = os.getenv("KX_CAPSULE_FILE", "").strip()
        context["capsule_file"] = explicit_file or str(
            Path(output_dir) / f"{context['capsule_id']}.kxcap"
        )

    # No explicit target variables means the Manager's safe local defaults apply
    # only when there is no persisted target state. Explicit launcher values are
    # merged later and intentionally override stale persisted state.
    return context


def _load_ui_context(app: Any) -> dict[str, Any]:
    """Load persisted GUI context from app state or disk."""

    cached = getattr(app.state, "ui_context", None)
    if isinstance(cached, Mapping):
        return dict(cached)

    path = _ui_state_file()

    environment_context = _environment_ui_context()

    try:
        if path.exists():
            with path.open("r", encoding="utf-8") as file_obj:
                data = json.load(file_obj)

            if isinstance(data, Mapping):
                context = _normalize_context({**dict(data), **environment_context})
                app.state.ui_context = context
                return dict(context)
    except Exception:
        pass

    defaults = {
        "target_mode": DEFAULT_TARGET_MODE,
        "network_profile": DEFAULT_NETWORK_PROFILE,
        "exposure_mode": DEFAULT_EXPOSURE_MODE,
        "capsule_file": DEFAULT_CAPSULE_FILE,
    }
    context = _normalize_context({**defaults, **environment_context})
    app.state.ui_context = context
    return dict(context)


def _save_ui_context(app: Any, context: Mapping[str, Any]) -> None:
    """Persist GUI context to app state and disk."""

    data = {
        key: value
        for key, value in _normalize_context(context).items()
        if key in PERSISTABLE_UI_CONTEXT_KEYS
        and value is not None
        and value != ""
    }

    app.state.ui_context = dict(data)

    path = _ui_state_file()

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_suffix(path.suffix + ".tmp")

        with temp_path.open("w", encoding="utf-8") as file_obj:
            json.dump(data, file_obj, indent=2, sort_keys=True, default=str)

        temp_path.replace(path)
    except Exception:
        # GUI state persistence must not break the action response.
        pass


def _normalize_context(context: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize GUI context aliases and canonical target fields."""

    data = normalize_payload_aliases(dict(context))

    if data.get("output_dir") and not data.get("capsule_output_dir"):
        data["capsule_output_dir"] = data["output_dir"]

    if data.get("version") and not data.get("capsule_version"):
        data["capsule_version"] = data["version"]

    if data.get("capsule_path") and not data.get("capsule_file"):
        data["capsule_file"] = data["capsule_path"]

    if data.get("capsule_file") and not data.get("capsule_path"):
        data["capsule_path"] = data["capsule_file"]

    if data.get("target_host") and not data.get("droplet_host"):
        data["droplet_host"] = data["target_host"]

    if data.get("droplet_host") and not data.get("target_host"):
        data["target_host"] = data["droplet_host"]

    if data.get("ssh_user") and not data.get("droplet_user"):
        data["droplet_user"] = data["ssh_user"]

    if data.get("user") and not data.get("droplet_user"):
        data["droplet_user"] = data["user"]

    if data.get("droplet_user") and not data.get("ssh_user"):
        data["ssh_user"] = data["droplet_user"]

    if data.get("ssh_key") and not data.get("ssh_key_path"):
        data["ssh_key_path"] = data["ssh_key"]

    if data.get("droplet_ssh_key") and not data.get("ssh_key_path"):
        data["ssh_key_path"] = data["droplet_ssh_key"]

    if data.get("ssh_key_path") and not data.get("ssh_key"):
        data["ssh_key"] = data["ssh_key_path"]

    if data.get("remote_root") and not data.get("remote_kx_root"):
        data["remote_kx_root"] = data["remote_root"]

    if data.get("droplet_kx_root") and not data.get("remote_kx_root"):
        data["remote_kx_root"] = data["droplet_kx_root"]

    if data.get("remote_kx_root") and not data.get("remote_root"):
        data["remote_root"] = data["remote_kx_root"]

    if data.get("droplet_capsule_dir") and not data.get("remote_capsule_dir"):
        data["remote_capsule_dir"] = data["droplet_capsule_dir"]

    if data.get("target_capsule_dir") and not data.get("remote_capsule_dir"):
        data["remote_capsule_dir"] = data["target_capsule_dir"]

    if data.get("remote_capsule_dir") and not data.get("target_capsule_dir"):
        data["target_capsule_dir"] = data["remote_capsule_dir"]

    if data.get("droplet_domain") and not data.get("domain"):
        data["domain"] = data["droplet_domain"]

    if data.get("domain") and not data.get("droplet_domain"):
        data["droplet_domain"] = data["domain"]

    if data.get("droplet_agent_url") and not data.get("remote_agent_url"):
        data["remote_agent_url"] = data["droplet_agent_url"]

    if data.get("remote_agent_url") and not data.get("droplet_agent_url"):
        data["droplet_agent_url"] = data["remote_agent_url"]

    if data.get("target_mode") == "droplet":
        data["network_profile"] = "public_vps"
        data["exposure_mode"] = "public"
        data["public_mode_enabled"] = "true"
        data["confirmed"] = "true"

        if data.get("droplet_host"):
            data["host"] = data["droplet_host"]

        if data.get("remote_kx_root"):
            data["runtime_root"] = data["remote_kx_root"]

        if data.get("remote_capsule_dir"):
            data["capsule_dir"] = data["remote_capsule_dir"]

    if data.get("target_mode") == "local":
        data["network_profile"] = "local_only"
        data["exposure_mode"] = "private"
        data["public_mode_enabled"] = "false"

    if data.get("target_mode") == "intranet":
        data["network_profile"] = "intranet_private"
        data["public_mode_enabled"] = "false"

    if data.get("target_mode") == "temporary_public":
        data["network_profile"] = "public_temporary"
        data["exposure_mode"] = "temporary_tunnel"
        data["public_mode_enabled"] = "true"
        data["confirmed"] = "true"

    return data


def _result_data_mapping(result: Mapping[str, Any]) -> dict[str, Any]:
    """Extract the result data object if it is a mapping."""

    data = result.get("data")
    if isinstance(data, Mapping):
        return dict(data)
    return {}


def _persistent_context_update(
    *,
    action: str,
    payload: Mapping[str, Any],
    result: Mapping[str, Any],
) -> dict[str, Any]:
    """Return successful action values that should survive page reloads."""

    if not bool(result.get("ok", False)):
        return {}

    if action not in CONTEXT_PERSISTING_ACTIONS:
        return {}

    data = _result_data_mapping(result)
    merged = _normalize_context({**dict(payload), **data})

    return {
        key: value
        for key, value in merged.items()
        if key in PERSISTABLE_UI_CONTEXT_KEYS
        and value is not None
        and value != ""
    }


def _update_persisted_ui_context(
    *,
    app: Any,
    action: str,
    payload: Mapping[str, Any],
    result: Mapping[str, Any],
) -> None:
    """Merge successful action values into persisted GUI context."""

    update = _persistent_context_update(
        action=action,
        payload=payload,
        result=result,
    )

    if not update:
        return

    context = _load_ui_context(app)
    context.update(update)
    _save_ui_context(app, context)


def _render_page_html(
    route: str,
    *,
    context: Mapping[str, Any] | None = None,
    result: Any | None = None,
) -> str:
    """Render a GUI page.

    The final page body is delegated to ``kx_manager.ui.page_views`` when
    available. The internal fallback keeps routes importable during staged
    builds and tests.
    """

    try:
        from kx_manager.ui.page_views import render_ui_page

        rendered = render_ui_page(route=route, context=context, result=result)
        if isinstance(rendered, HTMLResponse):
            return rendered.body.decode("utf-8", errors="replace")
        return str(rendered)
    except Exception:
        return _render_fallback_page_html(route)


def _render_page_response(
    route: str,
    *,
    context: Mapping[str, Any] | None = None,
    result: Any | None = None,
) -> HTMLResponse:
    try:
        from kx_manager.ui.page_views import render_ui_page

        rendered = render_ui_page(route=route, context=context, result=result)
        return _coerce_html_response(rendered)
    except Exception:
        return HTMLResponse(_render_fallback_page_html(route))


def _render_fallback_page_html(route: str) -> str:
    title = _page_title_for_route(route)
    nav = _fallback_nav_html()

    return (
        "<!doctype html>"
        '<html lang="en">'
        "<head>"
        '<meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{escape(APP_TITLE)} · {escape(title)}</title>"
        "<style>"
        "body{font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;"
        "margin:0;background:#f7f7f8;color:#111827;}"
        "main{max-width:1120px;margin:0 auto;padding:32px;}"
        ".brand{display:flex;align-items:center;gap:12px;margin-bottom:16px;}"
        ".brand-logo{width:40px;height:40px;object-fit:contain;flex:0 0 auto;}"
        ".brand h1{margin:0;}"
        "nav{display:flex;gap:12px;flex-wrap:wrap;margin:16px 0 24px;}"
        "a{color:#1f4fd8;text-decoration:none;}"
        ".card{background:white;border:1px solid #e5e7eb;border-radius:12px;padding:20px;}"
        "code{background:#f3f4f6;padding:2px 6px;border-radius:6px;}"
        "</style>"
        "</head>"
        "<body>"
        "<main>"
        '<header class="brand">'
        f'<img class="brand-logo" src="{escape(APP_LOGO_SRC)}" alt="{escape(APP_LOGO_ALT)}">'
        f"<h1>{escape(APP_TITLE)}</h1>"
        "</header>"
        f"<nav>{nav}</nav>"
        '<section class="card">'
        f"<h2>{escape(title)}</h2>"
        f"<p>Local GUI route: <code>{escape(route)}</code></p>"
        "<p>Page rendering is delegated to "
        "<code>kx_manager.ui.page_views.render_ui_page</code>.</p>"
        "</section>"
        "</main>"
        "</body>"
        "</html>"
    )


async def _request_payload(request: Request) -> dict[str, Any]:
    content_type = request.headers.get("content-type", "")

    if "application/json" in content_type:
        data = await request.json()
        if isinstance(data, Mapping):
            return normalize_payload_aliases(data)
        return {}

    form = await request.form()
    return normalize_payload_aliases(dict(form))


def _validation_error_result(action: str, exc: Exception) -> dict[str, Any]:
    return {
        "ok": False,
        "action": action,
        "message": str(exc),
        "instance_id": None,
        "data": {
            "field": getattr(exc, "field", None),
        },
        "stdout": None,
        "stderr": str(exc),
        "returncode": None,
    }


def _validated_payload(action: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    normalized = normalize_payload_aliases(payload)

    try:
        from kx_manager.ui.forms import form_to_payload, parse_action_form
        from kx_manager.ui.form_errors import FormValidationError
    except Exception:
        return normalized

    try:
        form = parse_action_form(action, normalized)
    except FormValidationError:
        raise

    return normalize_payload_aliases(form_to_payload(form))


def _result_to_dict(result: Any, *, action: str) -> dict[str, Any]:
    if result is None:
        data: dict[str, Any] = {
            "ok": True,
            "action": action,
            "message": "Action accepted.",
            "instance_id": None,
            "data": {},
            "stdout": None,
            "stderr": None,
            "returncode": None,
        }
        return jsonable_encoder(data)

    if isinstance(result, Mapping):
        data = dict(result)
    else:
        to_dict = getattr(result, "to_dict", None)
        if callable(to_dict):
            value = to_dict()
            data = dict(value) if isinstance(value, Mapping) else {}
        elif is_dataclass(result):
            data = asdict(result)
        else:
            model_dump = getattr(result, "model_dump", None)
            if callable(model_dump):
                value = model_dump()
                data = dict(value) if isinstance(value, Mapping) else {}
            else:
                data = {"data": {"result": repr(result)}}

    data.setdefault("ok", True)
    data.setdefault("action", action)
    data.setdefault("message", "Action completed.")
    data.setdefault("instance_id", None)
    data.setdefault("data", {})
    data.setdefault("stdout", None)
    data.setdefault("stderr", None)
    data.setdefault("returncode", None)

    return jsonable_encoder(data)


async def _dispatch_action(action: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    try:
        validated_payload = _validated_payload(action, payload)
    except Exception as exc:
        return _validation_error_result(action, exc)

    dispatcher = _load_dispatcher()
    result = dispatcher(action, dict(validated_payload))

    if inspect.isawaitable(result):
        result = await result

    return _result_to_dict(result, action=action)


def _wants_html_response(request: Request) -> bool:
    content_type = request.headers.get("content-type", "")
    accept = request.headers.get("accept", "")

    if "application/json" in content_type:
        return False

    return "text/html" in accept and "application/json" not in accept


def _json_pretty(value: Any) -> str:
    return json.dumps(jsonable_encoder(value), indent=2, sort_keys=True, default=str)


def _status_class(result: Mapping[str, Any]) -> str:
    return "ok" if bool(result.get("ok", False)) else "error"


def _render_fallback_action_result_page(
    *,
    action: str,
    payload: Mapping[str, Any],
    result: Mapping[str, Any],
) -> HTMLResponse:
    """Render a safe built-in HTML result page for browser action posts.

    This fallback prevents browser button submissions from landing on raw JSON
    when the optional rich action view renderer is unavailable or incomplete.
    """

    ok = bool(result.get("ok", False))
    status = "Success" if ok else "Failed"
    status_class = _status_class(result)
    message = str(result.get("message") or "")
    title = f"{APP_TITLE} · {escape(str(action))} · {status}"

    field = None
    data = result.get("data")
    if isinstance(data, Mapping):
        field = data.get("field")

    field_html = ""
    if field:
        field_html = (
            '<p class="meta">'
            f"<strong>Field:</strong> <code>{escape(str(field))}</code>"
            "</p>"
        )

    stderr = result.get("stderr")
    stderr_html = ""
    if stderr:
        stderr_html = (
            '<section class="card">'
            "<h3>Error detail</h3>"
            f"<pre>{escape(str(stderr))}</pre>"
            "</section>"
        )

    html = (
        "<!doctype html>"
        '<html lang="en">'
        "<head>"
        '<meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{title}</title>"
        "<style>"
        "body{font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;"
        "margin:0;background:#f7f7f8;color:#111827;}"
        "main{max-width:1120px;margin:0 auto;padding:32px;}"
        ".brand{display:flex;align-items:center;gap:12px;margin-bottom:16px;}"
        ".brand-logo{width:40px;height:40px;object-fit:contain;flex:0 0 auto;}"
        ".brand h1{margin:0;}"
        "nav{display:flex;gap:12px;flex-wrap:wrap;margin:16px 0 24px;}"
        "a{color:#1f4fd8;text-decoration:none;}"
        ".card{background:white;border:1px solid #e5e7eb;border-radius:12px;padding:20px;margin:16px 0;}"
        ".badge{display:inline-block;border-radius:999px;padding:4px 10px;font-weight:700;font-size:13px;}"
        ".badge.ok{background:#dcfce7;color:#166534;}"
        ".badge.error{background:#fee2e2;color:#991b1b;}"
        ".meta{color:#4b5563;}"
        "code{background:#f3f4f6;padding:2px 6px;border-radius:6px;}"
        "pre{background:#111827;color:#f9fafb;padding:16px;border-radius:10px;overflow:auto;}"
        ".actions{display:flex;gap:12px;flex-wrap:wrap;margin-top:20px;}"
        ".button{display:inline-block;background:#1f4fd8;color:white;padding:10px 14px;border-radius:8px;"
        "text-decoration:none;font-weight:700;}"
        ".button.secondary{background:#e5e7eb;color:#111827;}"
        "</style>"
        "</head>"
        "<body>"
        "<main>"
        '<header class="brand">'
        f'<img class="brand-logo" src="{escape(APP_LOGO_SRC)}" alt="{escape(APP_LOGO_ALT)}">'
        f"<h1>{escape(APP_TITLE)}</h1>"
        "</header>"
        f"<nav>{_fallback_nav_html()}</nav>"
        '<section class="card">'
        f'<span class="badge {status_class}">{status}</span>'
        f"<h2>{escape(str(action))}</h2>"
        f"<p>{escape(message)}</p>"
        f"{field_html}"
        "</section>"
        f"{stderr_html}"
        '<section class="card">'
        "<h3>Result payload</h3>"
        f"<pre>{escape(_json_pretty(result))}</pre>"
        "</section>"
        '<section class="card">'
        "<h3>Submitted values</h3>"
        f"<pre>{escape(_json_pretty(payload))}</pre>"
        "</section>"
        '<div class="actions">'
        '<a class="button" href="/ui">Back to Dashboard</a>'
        '<a class="button secondary" href="/ui/targets">Targets</a>'
        '<a class="button secondary" href="/ui/deploy">Deploy</a>'
        "</div>"
        "</main>"
        "</body>"
        "</html>"
    )

    return HTMLResponse(content=html, status_code=200)


def _render_action_result_response(
    *,
    request: Request,
    action: str,
    payload: Mapping[str, Any],
    result: Mapping[str, Any],
) -> Any:
    if not _wants_html_response(request):
        return JSONResponse(jsonable_encoder(result))

    try:
        from kx_manager.ui.action_views import render_action_result_page

        rendered = render_action_result_page(
            action=action,
            payload=dict(payload),
            result=dict(result),
        )
        return _coerce_html_response(rendered)
    except Exception:
        return _render_fallback_action_result_page(
            action=action,
            payload=payload,
            result=result,
        )


def _route_name(prefix: str, route: str) -> str:
    normalized = route.strip("/").replace("/", "_").replace("-", "_")
    return f"{prefix}_{normalized or 'root'}"


def _has_route(app: Any, path: str) -> bool:
    """Return whether a route or mounted path already exists."""

    return any(
        getattr(route, "path", None) == path
        for route in getattr(app, "routes", ())
    )


def _manager_health_payload() -> dict[str, Any]:
    """Return local Manager health status.

    This confirms that the local Manager FastAPI process is reachable.
    It does not perform Agent, Docker, firewall, backup, runtime, host-path,
    or privileged service checks.
    """

    return {
        "ok": True,
        "status": "ok",
        "service": "Konnaxion Capsule Manager",
        "api_version": "v1",
        "app_version": "v14",
    }


def _register_manager_health_routes(app: Any) -> None:
    """Register Manager healthcheck routes expected by GUI probes."""

    async def manager_health() -> dict[str, Any]:
        return _manager_health_payload()

    for route in MANAGER_HEALTH_ROUTES:
        if _has_route(app, route):
            continue

        app.add_api_route(
            route,
            manager_health,
            methods=["GET"],
            name=_route_name("manager_health", route),
            response_class=JSONResponse,
            response_model=None,
        )


def _mount_ui_assets(app: Any) -> None:
    """Mount static GUI assets when the assets directory exists."""

    if not UI_ASSETS_DIR.exists():
        return

    if _has_route(app, UI_ASSETS_ROUTE):
        return

    app.mount(
        UI_ASSETS_ROUTE,
        StaticFiles(directory=str(UI_ASSETS_DIR)),
        name="kx_ui_assets",
    )


def _build_job_id_from_result(result: Mapping[str, Any]) -> str | None:
    data = result.get("data")
    if not isinstance(data, Mapping):
        return None
    value = data.get("build_job_id")
    text = str(value or "").strip()
    return text or None


def _render_build_job_page(job: Mapping[str, Any]) -> HTMLResponse:
    status = str(job.get("status") or "unknown")
    phase = str(job.get("phase") or "-")
    message = str(job.get("message") or "")
    try:
        progress = max(0, min(100, int(job.get("progress") or 0)))
    except (TypeError, ValueError):
        progress = 0
    active = status in {"queued", "running"}
    refresh = '<meta http-equiv="refresh" content="2">' if active else ""
    log_tail = str(job.get("log_tail") or "Waiting for first log entry...")
    error = str(job.get("error") or "")
    error_html = ""
    if error:
        error_html = (
            '<section class="card error"><h3>Error</h3><pre>'
            + escape(error)
            + "</pre></section>"
        )

    refresh_note = (
        "This page refreshes every 2 seconds while the build is active."
        if active
        else "Build finished."
    )
    html = (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        + refresh
        + "<title>Capsule Build Progress</title><style>"
        "body{font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;"
        "margin:0;background:#f7f7f8;color:#111827}"
        "main{max-width:1120px;margin:0 auto;padding:28px}"
        "nav{display:flex;gap:12px;flex-wrap:wrap;margin:16px 0 24px}"
        "a{color:#1f4fd8;text-decoration:none}"
        ".card{background:#fff;border:1px solid #e5e7eb;border-radius:12px;padding:20px;margin:16px 0}"
        ".grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}"
        ".metric{background:#f9fafb;border-radius:10px;padding:12px}"
        ".label{font-size:12px;color:#6b7280;text-transform:uppercase;letter-spacing:.04em}"
        ".value{font-size:18px;font-weight:700;margin-top:4px;overflow-wrap:anywhere}"
        ".bar{height:22px;border-radius:999px;background:#e5e7eb;overflow:hidden}"
        ".fill{height:100%;background:#2563eb;min-width:2px;transition:width .3s}"
        "pre{background:#111827;color:#f9fafb;padding:16px;border-radius:10px;overflow:auto;"
        "max-height:480px;white-space:pre-wrap}"
        ".badge{display:inline-block;border-radius:999px;padding:4px 10px;font-weight:700}"
        ".running,.queued{background:#dbeafe;color:#1d4ed8}"
        ".succeeded{background:#dcfce7;color:#166534}"
        ".failed,.interrupted{background:#fee2e2;color:#991b1b}"
        ".error{border-color:#fecaca}code{background:#f3f4f6;padding:2px 6px;border-radius:6px}"
        "</style></head><body><main>"
        "<h1>Capsule Build Progress</h1>"
        '<nav><a href="/ui">Dashboard</a><a href="/ui/capsules">Capsules</a>'
        '<a href="/ui/logs">Logs</a></nav>'
        '<section class="card"><span class="badge '
        + escape(status)
        + '">'
        + escape(status.upper())
        + "</span><h2>"
        + escape(message)
        + '</h2><div class="bar" aria-label="Build progress"><div class="fill" style="width:'
        + str(progress)
        + '%"></div></div><p><strong>'
        + str(progress)
        + "%</strong></p><div class=\"grid\">"
        '<div class="metric"><div class="label">Phase</div><div class="value">'
        + escape(phase)
        + "</div></div>"
        '<div class="metric"><div class="label">Capsule</div><div class="value">'
        + escape(str(job.get("capsule_id") or "-"))
        + "</div></div>"
        '<div class="metric"><div class="label">Created</div><div class="value">'
        + escape(str(job.get("created_at") or "-"))
        + "</div></div>"
        '<div class="metric"><div class="label">Updated</div><div class="value">'
        + escape(str(job.get("builder_updated_at") or job.get("updated_at") or "-"))
        + "</div></div></div></section>"
        '<section class="card"><h3>Persistent files</h3><p>Job: <code>'
        + escape(str(job.get("job_file") or "-"))
        + "</code></p><p>Log: <code>"
        + escape(str(job.get("log_file") or "-"))
        + "</code></p></section>"
        + error_html
        + '<section class="card"><h3>Live Build Log</h3><pre>'
        + escape(log_tail)
        + "</pre></section><p>"
        + escape(refresh_note)
        + "</p></main></body></html>"
    )
    return HTMLResponse(content=html, status_code=200)

def register(app: Any) -> Any:
    """Register FastAPI GUI page and action routes."""

    _mount_ui_assets(app)
    _register_manager_health_routes(app)

    try:
        from kx_manager.services.build_jobs import ensure_build_job_store
        ensure_build_job_store()
    except OSError:
        # Keep the GUI reachable; submitting a build will surface the path error.
        pass

    async def build_job_page(job_id: str) -> Any:
        from kx_manager.services.build_jobs import get_build_job
        job = get_build_job(job_id)
        if job is None:
            return HTMLResponse("Build job not found.", status_code=404)
        return _render_build_job_page(job)

    async def build_job_api(job_id: str) -> Any:
        from kx_manager.services.build_jobs import get_build_job
        job = get_build_job(job_id)
        if job is None:
            return JSONResponse({"ok": False, "message": "Build job not found."}, status_code=404)
        return JSONResponse(jsonable_encoder(job))

    if not _has_route(app, "/ui/build-jobs/{job_id}"):
        app.add_api_route(
            "/ui/build-jobs/{job_id}", build_job_page, methods=["GET"],
            name="ui_build_job_status", response_class=HTMLResponse, response_model=None,
        )
    if not _has_route(app, "/api/build-jobs/{job_id}"):
        app.add_api_route(
            "/api/build-jobs/{job_id}", build_job_api, methods=["GET"],
            name="api_build_job_status", response_class=JSONResponse, response_model=None,
        )

    def make_page_handler(route: str) -> Any:
        async def page_handler(request: Request) -> Any:
            context = _load_ui_context(request.app)
            return _render_page_response(route, context=context)

        return page_handler

    def make_action_handler(action: str) -> Any:
        async def action_handler(request: Request) -> Any:
            payload = await _request_payload(request)
            result = await _dispatch_action(action, payload)

            _update_persisted_ui_context(
                app=request.app,
                action=action,
                payload=payload,
                result=result,
            )

            build_job_id = _build_job_id_from_result(result)
            if action in {"build_capsule", "rebuild_capsule"} and build_job_id:
                status_url = f"/ui/build-jobs/{build_job_id}"
                if _wants_html_response(request):
                    return RedirectResponse(url=status_url, status_code=303)
                body = dict(result)
                body["status_url"] = status_url
                return JSONResponse(jsonable_encoder(body), status_code=202)

            return _render_action_result_response(
                request=request,
                action=action,
                payload=payload,
                result=result,
            )

        return action_handler

    for route in _load_page_routes():
        app.add_api_route(
            route,
            make_page_handler(route),
            methods=["GET"],
            name=_route_name("ui_page", route),
            response_class=HTMLResponse,
            response_model=None,
        )

    for action, route in _load_action_routes().items():
        app.add_api_route(
            route,
            make_action_handler(action),
            methods=["POST"],
            name=_route_name("ui_action", route),
            response_class=HTMLResponse,
            response_model=None,
        )

    return app


def render_app() -> None:
    """Compatibility entrypoint for old direct callers."""

    raise RuntimeError(
        "kx_manager.ui.app is the FastAPI GUI route module. "
        "Create a FastAPI app and call register(app)."
    )


def main() -> None:
    """Console compatibility entrypoint."""

    render_app()


__all__ = [
    "APP_ICON",
    "APP_LOGO_ALT",
    "APP_LOGO_FILENAME",
    "APP_LOGO_SRC",
    "APP_TITLE",
    "BROWSER_ONLY_ACTIONS",
    "CONTEXT_PERSISTING_ACTIONS",
    "DEFAULT_REFRESH_SECONDS",
    "FALLBACK_NAV_ITEMS",
    "FALLBACK_UI_ACTION_ROUTES",
    "FALLBACK_UI_PAGE_ROUTES",
    "MANAGER_HEALTH_ROUTES",
    "PERSISTABLE_UI_CONTEXT_KEYS",
    "TARGET_ACTIONS",
    "UI_ASSETS_DIR",
    "UI_ASSETS_ROUTE",
    "dispatch_gui_action",
    "main",
    "register",
    "render_app",
]