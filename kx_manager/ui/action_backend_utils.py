# kx_manager/ui/action_backend_utils.py

"""Utility helpers for Konnaxion Manager GUI action backends.

This module contains low-level helpers used by the GUI action execution adapter:
HTTP response normalization, SSH/SCP subprocess execution, payload cleanup, and
Droplet Agent URL resolution.

It intentionally contains no GUI route handlers and no arbitrary command entry
points.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any, Mapping
from urllib.parse import urlparse

import httpx


def response_payload(response: httpx.Response) -> dict[str, Any]:
    """Normalize an httpx response into a mutable JSON-safe mapping."""

    try:
        data = response.json()
    except ValueError:
        data = {"text": response.text}

    if not isinstance(data, dict):
        data = {"result": data}

    data.setdefault("status_code", response.status_code)

    if response.is_error:
        data.setdefault("ok", False)
        data.setdefault("message", data.get("detail") or response.text)
    else:
        data.setdefault("ok", True)
        data.setdefault("message", "Request completed.")

    return data


def agent_response_from_ssh_result(
    result: Mapping[str, Any],
    *,
    method: str,
    path: str,
    agent_health_url: str,
) -> dict[str, Any]:
    """Normalize a curl-over-SSH result into an Agent-like response mapping."""

    data: dict[str, Any] = {
        "ok": bool(result.get("ok", False)),
        "message": result.get("message") or "Remote Agent request completed.",
        "transport": "ssh",
        "method": method,
        "path": path,
        "agent_health_url": agent_health_url,
        "stdout": result.get("stdout"),
        "stderr": result.get("stderr"),
        "returncode": result.get("returncode"),
    }

    stdout = str(result.get("stdout") or "").strip()

    if stdout:
        try:
            parsed = json.loads(stdout)
        except ValueError:
            parsed = None

        if isinstance(parsed, Mapping):
            data.update(dict(parsed))
            data.setdefault("ok", bool(result.get("ok", False)))
        elif parsed is not None:
            data["result"] = parsed
        else:
            data["text"] = stdout

    if not data.get("ok") and not data.get("message"):
        data["message"] = str(result.get("stderr") or "Remote Agent request failed.")

    return data


def run_argv(argv: list[str], *, timeout_seconds: int) -> dict[str, Any]:
    """Run an approved argv command and return a normalized result.

    Uses shell=False and UTF-8 decoding with replacement so Windows cp1252
    cannot crash when SSH/systemd output contains non-cp1252 bytes.
    """

    try:
        completed = subprocess.run(
            argv,
            shell=False,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
        )
    except FileNotFoundError as exc:
        return {
            "ok": False,
            "message": f"Required executable not found: {argv[0]}",
            "argv": safe_argv(argv),
            "stdout": "",
            "stderr": str(exc),
            "returncode": 127,
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "message": f"Command timed out: {argv[0]}",
            "argv": safe_argv(argv),
            "stdout": coerce_process_output(exc.stdout),
            "stderr": coerce_process_output(exc.stderr) or "command timed out",
            "returncode": 124,
        }

    return {
        "ok": completed.returncode == 0,
        "message": "Command completed." if completed.returncode == 0 else "Command failed.",
        "argv": safe_argv(argv),
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "returncode": completed.returncode,
    }


def safe_argv(argv: list[str]) -> list[str]:
    """Return argv with sensitive argument values redacted."""

    safe: list[str] = []

    skip_next = False
    for item in argv:
        if skip_next:
            safe.append("<redacted>")
            skip_next = False
            continue

        safe.append(item)

        if item in {"-i"}:
            skip_next = True

    return safe


def strip_empty(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Drop None and empty-string values from a payload."""

    return {
        key: value
        for key, value in payload.items()
        if value is not None and value != ""
    }


def bool_payload(value: Any, *, default: bool = False) -> bool:
    """Coerce form/API boolean-ish values into bool."""

    if value in (None, ""):
        return default

    if isinstance(value, bool):
        return value

    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "y", "on", "checked"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False

    return bool(normalized)


def require_payload_text(payload: Mapping[str, Any], key: str) -> str:
    """Return a required non-empty text payload value."""

    value = payload.get(key)
    if value is None or str(value).strip() == "":
        raise ValueError(f"{key} is required.")
    return str(value).strip()


def is_droplet_payload(payload: Mapping[str, Any]) -> bool:
    """Return True when payload represents a Droplet/VPS operation."""

    target_mode = str(payload.get("target_mode") or "").strip().lower()
    droplet_host = str(payload.get("droplet_host") or "").strip()

    return target_mode == "droplet" or bool(droplet_host)


def remote_agent_base_url(payload: Mapping[str, Any]) -> str:
    """Return Agent base URL for direct HTTP mode.

    Droplet execution normally uses SSH-local Agent transport. Blank, stale,
    example, mismatched, and Droplet-local loopback URLs are ignored so the
    Manager does not accidentally call a local tunnel on the Windows machine
    instead of the selected Droplet.
    """

    explicit = explicit_remote_agent_url(payload)
    if explicit:
        base = explicit.removesuffix("/health").rstrip("/")
        return base if base.endswith("/v1") else base + "/v1"

    return "http://127.0.0.1:8765/v1"


def explicit_remote_agent_url(payload: Mapping[str, Any]) -> str:
    """Return a usable direct Agent URL, or blank for SSH-local mode.

    Rules:
    - blank means normal SSH-local access to the private Droplet Agent;
    - 203.0.113.x documentation/test URLs are ignored;
    - mismatched public hosts are ignored;
    - localhost/127.0.0.1 URLs are ignored for Droplet payloads because they
      point at the Manager machine, not the Droplet;
    - localhost/127.0.0.1 URLs remain allowed for non-Droplet explicit local
      Agent/tunnel use.
    """

    raw = str(
        payload.get("remote_agent_url")
        or payload.get("droplet_agent_url")
        or ""
    ).strip()

    if not raw:
        return ""

    parsed = urlparse(raw)
    host = str(parsed.hostname or "").strip().lower()
    droplet_host = str(
        payload.get("droplet_host")
        or payload.get("target_host")
        or payload.get("host")
        or ""
    ).strip().lower()

    if not host:
        return ""

    # TEST-NET-3 documentation range; never a real customer Droplet target.
    if host.startswith("203.0.113."):
        return ""

    # In Droplet mode, localhost means "this Manager machine", not the Droplet.
    # Treat old tunnel URLs such as http://127.0.0.1:18765/v1 as stale and let
    # the execution client use SSH-local curl against 127.0.0.1:8765 inside the
    # Droplet instead.
    if host in {"127.0.0.1", "localhost", "::1"}:
        return "" if is_droplet_payload(payload) else raw

    # If the operator selected a Droplet host and the persisted Agent URL points
    # somewhere else, prefer SSH-local transport into the selected Droplet.
    if droplet_host and host != droplet_host:
        return ""

    return raw


def remote_capsule_path_from_payload(
    payload: Mapping[str, Any],
    capsule_file: Path,
) -> str:
    """Build the canonical remote capsule path from payload + local filename."""

    remote_capsule_dir = str(
        payload.get("remote_capsule_dir") or "/opt/konnaxion/capsules"
    )
    filename = capsule_file.name or Path(str(payload.get("capsule_file") or "")).name

    if not filename:
        capsule_id = str(payload.get("capsule_id") or "konnaxion-v14-demo-2026.04.30")
        filename = f"{capsule_id}.kxcap"

    return str(PurePosixPath(remote_capsule_dir) / filename)


def agent_rejected_new_import_fields(result: Mapping[str, Any]) -> bool:
    """Return True when FastAPI rejected modern import fields as extras."""

    detail = result.get("detail")
    if not isinstance(detail, list):
        return False

    rejected_fields: set[str] = set()

    for item in detail:
        if not isinstance(item, Mapping):
            continue

        if item.get("type") != "extra_forbidden":
            continue

        location = item.get("loc")
        if not isinstance(location, list):
            continue

        if len(location) >= 2:
            rejected_fields.add(str(location[-1]))

    modern_fields = {
        "exposure_mode",
        "verify",
        "overwrite",
        "capsule_id",
    }

    return bool(rejected_fields & modern_fields)


def ssh_common_options() -> list[str]:
    """Return common stable SSH/SCP options for repeated GUI calls."""

    return [
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=20",
        "-o",
        "ServerAliveInterval=10",
        "-o",
        "ServerAliveCountMax=3",
        "-o",
        "StrictHostKeyChecking=yes",
    ]


def coerce_process_output(value: Any) -> str:
    """Return subprocess output as safe text."""

    if value is None:
        return ""

    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")

    return str(value)


# ---------------------------------------------------------------------------
# Compatibility aliases for existing private names
# ---------------------------------------------------------------------------

_response_payload = response_payload
_agent_response_from_ssh_result = agent_response_from_ssh_result
_run_argv = run_argv
_safe_argv = safe_argv
_strip_empty = strip_empty
_bool_payload = bool_payload
_require_payload_text = require_payload_text
_is_droplet_payload = is_droplet_payload
_remote_agent_base_url = remote_agent_base_url
_remote_capsule_path_from_payload = remote_capsule_path_from_payload
_explicit_remote_agent_url = explicit_remote_agent_url
_agent_rejected_new_import_fields = agent_rejected_new_import_fields
_ssh_common_options = ssh_common_options
_coerce_process_output = coerce_process_output


__all__ = [
    "agent_rejected_new_import_fields",
    "agent_response_from_ssh_result",
    "bool_payload",
    "coerce_process_output",
    "explicit_remote_agent_url",
    "is_droplet_payload",
    "remote_agent_base_url",
    "remote_capsule_path_from_payload",
    "require_payload_text",
    "response_payload",
    "run_argv",
    "safe_argv",
    "ssh_common_options",
    "strip_empty",
    "_agent_rejected_new_import_fields",
    "_agent_response_from_ssh_result",
    "_bool_payload",
    "_coerce_process_output",
    "_explicit_remote_agent_url",
    "_is_droplet_payload",
    "_remote_agent_base_url",
    "_remote_capsule_path_from_payload",
    "_require_payload_text",
    "_response_payload",
    "_run_argv",
    "_safe_argv",
    "_ssh_common_options",
    "_strip_empty",
]