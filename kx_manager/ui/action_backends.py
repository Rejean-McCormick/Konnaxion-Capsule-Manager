# kx_manager/ui/action_backends.py

"""Backend handlers for Konnaxion Capsule Manager GUI actions.

This module owns the concrete action handlers used by the GUI dispatcher.

It must not perform arbitrary shell execution. GUI actions are routed only
through approved Manager routes, KonnaxionAgentClient methods, service wrappers,
or browser-link result builders.
"""

from __future__ import annotations

import asyncio

from pathlib import Path, PurePosixPath
from typing import Any, Awaitable, Callable, Mapping
from urllib.parse import quote, urlparse

from kx_manager.client import KonnaxionAgentClient
from kx_manager.defaults import DEFAULT_NETWORK_PROFILE
from kx_manager.ui.action_backend_utils import (
    _remote_agent_base_url,
    _require_payload_text,
)
from kx_manager.ui.action_constants import TARGET_DEFAULTS
from kx_manager.ui.action_helpers import (
    _agent_base_url,
    _bool,
    _call_service_function,
    _http_json_request,
    _import_module,
    _int,
    _manager_base_url,
    _manager_request_first,
    _missing_backend,
    _optional_text,
    _payload_instance_id,
    _query_string,
    _query_string_without_question,
    _require_text,
    _result_from_backend,
    _target_message,
    _truthy,
    _try_import_module,
    _validate_target_config,
)
from kx_manager.ui.action_models import GuiActionResult
from kx_manager.ui.agent_execution_client import _AgentHttpExecutionClient
from kx_manager.ui.droplet_bootstrap import (
    _make_manager_bootstrap_archive,
    _remote_bootstrap_command,
)


ActionHandler = Callable[[str, Mapping[str, Any]], Awaitable[GuiActionResult]]

DROPLET_EXECUTION_ACTIONS = frozenset(
    {
        "deploy_droplet",
        "bootstrap_droplet_agent",
        "check_droplet_agent",
        "copy_capsule_to_droplet",
        "start_droplet_instance",
        # Target-aware read-only runtime actions. These must query the
        # selected Droplet Agent instead of the local Manager Agent.
        "instance_status",
        "view_logs",
        "view_health",
    }
)


def _clean_public_host(value: Any) -> str:
    """Normalize a host-like public runtime value.

    Accepts values such as:
      konnaxion.com
      https://konnaxion.com/
      https://konnaxion.com/api/
      user:pass@konnaxion.com

    Returns:
      konnaxion.com

    This is intentionally only a lightweight normalization step. Final
    profile/security validation still belongs in the Agent.
    """

    raw = str(value or "").strip()
    if not raw:
        return ""

    raw = raw.strip().strip("/")
    if not raw:
        return ""

    if raw.startswith(("http://", "https://")):
        parsed = urlparse(raw)
        host = parsed.netloc or parsed.path
    else:
        host = raw

    host = host.strip().strip("/")
    if not host:
        return ""

    if "/" in host:
        host = host.split("/", 1)[0].strip()

    if "@" in host:
        host = host.rsplit("@", 1)[-1].strip()

    return host


def _public_runtime_host_from_payload(payload: Mapping[str, Any]) -> str:
    """Return the canonical public runtime host for Droplet/public_vps actions.

    Preference intentionally chooses operator-facing public domain fields before
    SSH target fields.

    Correct public VPS meaning:
      host / public_host / domain / droplet_domain = public runtime host
      droplet_host / target_host = SSH target
    """

    candidate_keys = (
        "domain",
        "droplet_domain",
        "public_host",
        "public_url",
        "url",
        "kx_host",
        "KX_HOST",
        "host",
        "droplet_host",
        "target_host",
    )

    for key in candidate_keys:
        value = _clean_public_host(payload.get(key))
        if value:
            return value

    return ""


def _apply_public_runtime_host(data: dict[str, Any]) -> None:
    """Mutate a Droplet action payload with the canonical public runtime host."""

    public_host = _public_runtime_host_from_payload(data)
    if not public_host:
        return

    # Critical durable fix:
    # Always overwrite host with the public runtime host. Do not preserve stale
    # a stale VPS IP in host when domain=konnaxion.com exists.
    data["host"] = public_host
    data.setdefault("public_host", public_host)
    data.setdefault("droplet_domain", public_host)


def _execution_payload(
    action: str,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    data = dict(payload)

    if action in DROPLET_EXECUTION_ACTIONS:
        data["target_mode"] = "droplet"
        data["network_profile"] = "public_vps"
        data["exposure_mode"] = "public"
        data["confirmed"] = True

        # Keep Droplet/VPS concepts separate:
        # - host/public_host/domain/droplet_domain = public runtime host
        # - droplet_host/target_host = SSH/SCP target
        _apply_public_runtime_host(data)

        if data.get("remote_kx_root") and not data.get("runtime_root"):
            data["runtime_root"] = data["remote_kx_root"]
        if data.get("remote_capsule_dir") and not data.get("capsule_dir"):
            data["capsule_dir"] = data["remote_capsule_dir"]
        if data.get("capsule_file") and not data.get("capsule_path"):
            data["capsule_path"] = data["capsule_file"]

        data["manager_client"] = _AgentHttpExecutionClient(
            base_url=_remote_agent_base_url(data),
            droplet_payload=data,
        )

        return data

    data["manager_client"] = _AgentHttpExecutionClient(
        base_url=_agent_base_url(),
        droplet_payload=data,
    )
    return data


async def _handle_check_manager(
    action: str,
    payload: Mapping[str, Any],
) -> GuiActionResult:
    return await _manager_request_first(
        action=action,
        payload=payload,
        attempts=(("GET", "/health"), ("GET", "/v1/health")),
        success_message="Manager is reachable.",
        failure_message="Manager health check failed.",
    )


async def _handle_check_agent(
    action: str,
    payload: Mapping[str, Any],
) -> GuiActionResult:
    async with KonnaxionAgentClient.from_env() as client:
        outcome = await client.health()

    return _result_from_backend(
        action=action,
        outcome=outcome,
        payload=payload,
        default_message="Agent is reachable.",
    )


async def _handle_select_source_folder(
    action: str,
    payload: Mapping[str, Any],
) -> GuiActionResult:
    source_dir = _require_text(payload, "source_dir", "kx_source_dir", "KX_SOURCE_DIR")

    return GuiActionResult(
        ok=True,
        action=action,
        message="Konnaxion source folder selected.",
        data={"source_dir": source_dir},
    )


async def _handle_select_capsule_output_folder(
    action: str,
    payload: Mapping[str, Any],
) -> GuiActionResult:
    output_dir = _require_text(
        payload,
        "capsule_output_dir",
        "output_dir",
        "kx_capsule_output_dir",
        "KX_CAPSULE_OUTPUT_DIR",
    )

    return GuiActionResult(
        ok=True,
        action=action,
        message="Capsule output folder selected.",
        data={"capsule_output_dir": output_dir},
    )


async def _handle_build_capsule(
    action: str,
    payload: Mapping[str, Any],
) -> GuiActionResult:
    """Queue a persistent asynchronous capsule build job.

    Heavy Docker builds must not block the Manager HTTP request.  The browser
    is redirected by ``kx_manager.ui.app`` to the job progress page when the
    returned result contains ``build_job_id``.
    """

    jobs = _import_module("kx_manager.services.build_jobs")
    create_job = getattr(jobs, "create_build_job", None)

    if create_job is None:
        return _missing_backend(action, "kx_manager.services.build_jobs.create_build_job")

    job = create_job(action, payload)
    job_id = str(job.get("job_id") or "")

    return GuiActionResult(
        ok=bool(job_id),
        action=action,
        message="Capsule build queued." if job_id else "Capsule build could not be queued.",
        data={
            "build_job_id": job_id,
            "status": job.get("status"),
            "phase": job.get("phase"),
            "progress": job.get("progress"),
            "status_url": f"/ui/build-jobs/{job_id}" if job_id else None,
            "api_url": f"/api/build-jobs/{job_id}" if job_id else None,
            "log_file": job.get("log_file"),
            "job_file": job.get("job_file"),
        },
    )


async def _handle_verify_capsule(
    action: str,
    payload: Mapping[str, Any],
) -> GuiActionResult:
    builder = _try_import_module("kx_manager.services.builder")

    if builder is not None and hasattr(builder, "verify_capsule"):
        function = getattr(builder, "verify_capsule")
        outcome = await _call_service_function(
            function,
            payload,
            request_module=builder,
            request_class_name="VerifyCapsuleRequest",
        )

        return _result_from_backend(
            action=action,
            outcome=outcome,
            payload=payload,
            default_message="Capsule verification completed.",
        )

    capsule_path = _require_text(payload, "capsule_path", "capsule_file")

    async with KonnaxionAgentClient.from_env() as client:
        outcome = await client.verify_capsule(capsule_path=capsule_path)

    return _result_from_backend(
        action=action,
        outcome=outcome,
        payload=payload,
        default_message="Capsule verification completed.",
    )


async def _handle_import_capsule(
    action: str,
    payload: Mapping[str, Any],
) -> GuiActionResult:
    capsule_path = _require_text(payload, "capsule_path", "capsule_file")
    instance_id = _require_text(payload, "instance_id")
    network_profile = str(payload.get("network_profile") or DEFAULT_NETWORK_PROFILE)

    async with KonnaxionAgentClient.from_env() as client:
        outcome = await client.import_capsule(
            capsule_path=capsule_path,
            instance_id=instance_id,
            network_profile=network_profile,
        )

    return _result_from_backend(
        action=action,
        outcome=outcome,
        payload=payload,
        default_message="Capsule imported.",
    )


async def _handle_create_instance(
    action: str,
    payload: Mapping[str, Any],
) -> GuiActionResult:
    async with KonnaxionAgentClient.from_env() as client:
        outcome = await client.create_instance(
            instance_id=_require_text(payload, "instance_id"),
            capsule_id=_require_text(payload, "capsule_id"),
            network_profile=str(payload.get("network_profile") or DEFAULT_NETWORK_PROFILE),
            exposure_mode=str(payload.get("exposure_mode") or "private"),
            host=_optional_text(payload, "domain", "public_host", "host"),
            generate_secrets=_bool(payload.get("generate_secrets"), default=True),
        )

    return _result_from_backend(
        action=action,
        outcome=outcome,
        payload=payload,
        default_message="Instance created.",
    )


async def _handle_update_instance(
    action: str,
    payload: Mapping[str, Any],
) -> GuiActionResult:
    async with KonnaxionAgentClient.from_env() as client:
        outcome = await client.update_instance(
            instance_id=_require_text(payload, "instance_id"),
            capsule_path=_require_text(payload, "capsule_path", "capsule_file"),
            create_pre_update_backup=_bool(
                payload.get("create_pre_update_backup"),
                default=True,
            ),
        )

    return _result_from_backend(
        action=action,
        outcome=outcome,
        payload=payload,
        default_message="Instance updated.",
    )


async def _handle_start_instance(
    action: str,
    payload: Mapping[str, Any],
) -> GuiActionResult:
    async with KonnaxionAgentClient.from_env() as client:
        outcome = await client.start_instance(
            instance_id=_require_text(payload, "instance_id"),
            run_security_gate=_bool(payload.get("run_security_gate"), default=True),
        )

    return _result_from_backend(
        action=action,
        outcome=outcome,
        payload=payload,
        default_message="Instance started.",
    )


async def _handle_stop_instance(
    action: str,
    payload: Mapping[str, Any],
) -> GuiActionResult:
    async with KonnaxionAgentClient.from_env() as client:
        outcome = await client.stop_instance(
            instance_id=_require_text(payload, "instance_id"),
            timeout_seconds=_int(payload.get("timeout_seconds"), default=60),
        )

    return _result_from_backend(
        action=action,
        outcome=outcome,
        payload=payload,
        default_message="Instance stopped.",
    )


async def _handle_restart_instance(
    action: str,
    payload: Mapping[str, Any],
) -> GuiActionResult:
    stop_result = await _handle_stop_instance("stop_instance", payload)

    if not stop_result.ok:
        return GuiActionResult(
            ok=False,
            action=action,
            message="Restart failed while stopping the instance.",
            instance_id=stop_result.instance_id,
            data={"stop": stop_result.to_dict()},
            stderr=stop_result.stderr,
            returncode=stop_result.returncode,
        )

    start_result = await _handle_start_instance("start_instance", payload)

    return GuiActionResult(
        ok=start_result.ok,
        action=action,
        message=(
            "Instance restarted."
            if start_result.ok
            else "Restart failed while starting the instance."
        ),
        instance_id=start_result.instance_id,
        data={"stop": stop_result.to_dict(), "start": start_result.to_dict()},
        stdout=start_result.stdout,
        stderr=start_result.stderr,
        returncode=start_result.returncode,
    )


def _payload_targets_droplet(payload: Mapping[str, Any]) -> bool:
    """Return whether a runtime action should execute against the Droplet Agent."""

    return (
        str(payload.get("target_mode") or "").strip().lower() == "droplet"
        or bool(str(payload.get("droplet_host") or "").strip())
    )


def _remote_runtime_not_ready_result(
    *,
    action: str,
    payload: Mapping[str, Any],
    outcome: Mapping[str, Any],
) -> GuiActionResult | None:
    """Turn a missing remote compose state into an operator-facing message."""

    if not _payload_targets_droplet(payload) or bool(outcome.get("ok", False)):
        return None

    text = " ".join(
        str(value or "")
        for value in (
            outcome.get("message"),
            outcome.get("stderr"),
            outcome.get("detail"),
            outcome.get("error"),
        )
    )
    if "compose file does not exist" not in text.lower():
        return None

    return GuiActionResult(
        ok=False,
        action=action,
        message=(
            "Droplet instance runtime is not created yet (or is incomplete). "
            "Run Deploy Droplet before requesting runtime status, health, or logs."
        ),
        instance_id=_payload_instance_id(payload),
        data={
            "target_mode": "droplet",
            "droplet_host": payload.get("droplet_host"),
            "remote_kx_root": payload.get("remote_kx_root"),
            "backend": dict(outcome),
        },
    )


async def _handle_instance_status(
    action: str,
    payload: Mapping[str, Any],
) -> GuiActionResult:
    instance_id = _require_text(payload, "instance_id")

    if _payload_targets_droplet(payload):
        service_payload = _execution_payload(action, payload)
        client = service_payload["manager_client"]
        outcome = await asyncio.to_thread(
            client.instance_status,
            instance_id=instance_id,
        )
    else:
        async with KonnaxionAgentClient.from_env() as client:
            outcome = await client.instance_status(instance_id=instance_id)

    friendly = _remote_runtime_not_ready_result(
        action=action, payload=payload, outcome=outcome
    )
    if friendly is not None:
        return friendly

    return _result_from_backend(
        action=action,
        outcome=outcome,
        payload=payload,
        default_message="Instance status loaded.",
    )


async def _handle_view_logs(
    action: str,
    payload: Mapping[str, Any],
) -> GuiActionResult:
    raw_tail = payload.get("lines", payload.get("tail_lines", payload.get("tail", 200)))

    if isinstance(raw_tail, bool):
        raw_tail = 200

    instance_id = _require_text(payload, "instance_id")
    service = payload.get("service") or None
    tail = _int(raw_tail, default=200)

    if _payload_targets_droplet(payload):
        service_payload = _execution_payload(action, payload)
        client = service_payload["manager_client"]
        outcome = await asyncio.to_thread(
            client.instance_logs,
            instance_id=instance_id,
            service=service,
            tail=tail,
        )
    else:
        async with KonnaxionAgentClient.from_env() as client:
            outcome = await client.instance_logs(
                instance_id=instance_id,
                service=service,
                tail=tail,
            )

    friendly = _remote_runtime_not_ready_result(
        action=action, payload=payload, outcome=outcome
    )
    if friendly is not None:
        return friendly

    return _result_from_backend(
        action=action,
        outcome=outcome,
        payload=payload,
        default_message="Instance logs loaded.",
    )


async def _handle_view_health(
    action: str,
    payload: Mapping[str, Any],
) -> GuiActionResult:
    instance_id = _require_text(payload, "instance_id")

    if _payload_targets_droplet(payload):
        service_payload = _execution_payload(action, payload)
        client = service_payload["manager_client"]
        outcome = await asyncio.to_thread(
            client.instance_health,
            instance_id=instance_id,
        )
    else:
        async with KonnaxionAgentClient.from_env() as client:
            outcome = await client.instance_health(instance_id=instance_id)

    friendly = _remote_runtime_not_ready_result(
        action=action, payload=payload, outcome=outcome
    )
    if friendly is not None:
        return friendly

    return _result_from_backend(
        action=action,
        outcome=outcome,
        payload=payload,
        default_message="Instance health loaded.",
    )


async def _handle_rollback_instance(
    action: str,
    payload: Mapping[str, Any],
) -> GuiActionResult:
    async with KonnaxionAgentClient.from_env() as client:
        outcome = await client.rollback_instance(
            instance_id=_require_text(payload, "instance_id"),
            target_release_id=_optional_text(
                payload,
                "target_release_id",
                "target_capsule_id",
            ),
            restore_data=_bool(payload.get("restore_data"), default=True),
        )

    return _result_from_backend(
        action=action,
        outcome=outcome,
        payload=payload,
        default_message="Instance rollback completed.",
    )


async def _handle_create_backup(
    action: str,
    payload: Mapping[str, Any],
) -> GuiActionResult:
    async with KonnaxionAgentClient.from_env() as client:
        outcome = await client.backup_instance(
            instance_id=_require_text(payload, "instance_id"),
            backup_class=str(payload.get("backup_class") or "manual"),
            verify_after_create=_bool(payload.get("verify_after_create"), default=True),
        )

    return _result_from_backend(
        action=action,
        outcome=outcome,
        payload=payload,
        default_message="Backup created.",
    )


async def _handle_restore_backup(
    action: str,
    payload: Mapping[str, Any],
) -> GuiActionResult:
    async with KonnaxionAgentClient.from_env() as client:
        outcome = await client.restore_instance(
            instance_id=_require_text(payload, "instance_id"),
            backup_id=_require_text(payload, "backup_id"),
            create_pre_restore_backup=_bool(
                payload.get("create_pre_restore_backup"),
                default=True,
            ),
        )

    return _result_from_backend(
        action=action,
        outcome=outcome,
        payload=payload,
        default_message="Backup restored.",
    )


async def _handle_restore_backup_new(
    action: str,
    payload: Mapping[str, Any],
) -> GuiActionResult:
    source_backup_id = _require_text(
        payload,
        "source_backup_id",
        "from_backup_id",
        "backup_id",
    )
    new_instance_id = _require_text(
        payload,
        "new_instance_id",
        "target_instance_id",
    )

    async with KonnaxionAgentClient.from_env() as client:
        outcome = await client.restore_new_instance(
            source_backup_id=source_backup_id,
            new_instance_id=new_instance_id,
            network_profile=str(payload.get("network_profile") or DEFAULT_NETWORK_PROFILE),
        )

    return _result_from_backend(
        action=action,
        outcome=outcome,
        payload=payload,
        default_message="Backup restored into new instance.",
    )


async def _handle_run_security_check(
    action: str,
    payload: Mapping[str, Any],
) -> GuiActionResult:
    async with KonnaxionAgentClient.from_env() as client:
        outcome = await client.security_check(
            instance_id=_require_text(payload, "instance_id"),
            blocking=_bool(payload.get("blocking"), default=True),
        )

    return _result_from_backend(
        action=action,
        outcome=outcome,
        payload=payload,
        default_message="Security Gate check completed.",
    )


async def _handle_set_network_profile(
    action: str,
    payload: Mapping[str, Any],
) -> GuiActionResult:
    async with KonnaxionAgentClient.from_env() as client:
        outcome = await client.set_network_profile(
            instance_id=_require_text(payload, "instance_id"),
            network_profile=_require_text(payload, "network_profile"),
            exposure_mode=str(payload.get("exposure_mode") or "private"),
            public_mode_expires_at=payload.get("public_mode_expires_at"),
        )

    return _result_from_backend(
        action=action,
        outcome=outcome,
        payload=payload,
        default_message="Network profile updated.",
    )


async def _handle_disable_public_mode(
    action: str,
    payload: Mapping[str, Any],
) -> GuiActionResult:
    safe_payload = {
        **payload,
        "network_profile": DEFAULT_NETWORK_PROFILE,
        "exposure_mode": "private",
        "public_mode_enabled": False,
        "public_mode_expires_at": None,
    }

    result = await _handle_set_network_profile("set_network_profile", safe_payload)
    result.action = action

    if result.ok:
        result.message = "Public mode disabled."

    return result


async def _handle_manager_capsule_action(
    action: str,
    payload: Mapping[str, Any],
) -> GuiActionResult:
    if action == "list_capsules":
        return await _manager_request_first(
            action=action,
            payload=payload,
            attempts=(("GET", "/capsules"), ("GET", "/v1/capsules")),
            success_message="Capsules listed.",
            failure_message="Unable to list capsules.",
        )

    capsule_id = _require_text(payload, "capsule_id", "capsule_path", "capsule_file", "id")
    quoted = quote(capsule_id, safe="")

    return await _manager_request_first(
        action=action,
        payload=payload,
        attempts=(("GET", f"/capsules/{quoted}"), ("GET", f"/v1/capsules/{quoted}")),
        success_message="Capsule loaded.",
        failure_message="Unable to load capsule.",
    )


async def _handle_manager_backup_action(
    action: str,
    payload: Mapping[str, Any],
) -> GuiActionResult:
    if action == "list_backups":
        instance_id = str(payload.get("instance_id") or "").strip()
        query = _query_string(
            {
                "instance_id": instance_id,
                "status": payload.get("status") or "",
                "backup_class": payload.get("backup_class") or "",
                "limit": payload.get("limit") or 50,
            }
        )

        if instance_id:
            quoted_instance_id = quote(instance_id, safe="")
            instance_query = _query_string_without_question(query, "instance_id")
            attempts = (
                ("GET", f"/instances/{quoted_instance_id}/backups{instance_query}"),
                ("GET", f"/backups{query}"),
                ("GET", f"/v1/instances/{quoted_instance_id}/backups{instance_query}"),
                ("GET", f"/v1/backups{query}"),
            )
        else:
            attempts = (
                ("GET", f"/backups{query}"),
                ("GET", f"/v1/backups{query}"),
            )

        return await _manager_request_first(
            action=action,
            payload=payload,
            attempts=attempts,
            success_message="Backups listed.",
            failure_message="Unable to list backups.",
        )

    if action == "verify_backup":
        backup_id = _require_text(payload, "backup_id", "source_backup_id")
        quoted_backup_id = quote(backup_id, safe="")

        return await _manager_request_first(
            action=action,
            payload=payload,
            attempts=(
                ("POST", f"/backups/{quoted_backup_id}/verify"),
                ("POST", f"/v1/backups/{quoted_backup_id}/verify"),
            ),
            success_message="Backup verification completed.",
            failure_message="Backup verification failed.",
        )

    if action == "test_restore_backup":
        backup_id = _optional_text(payload, "backup_id", "source_backup_id")
        instance_id = _optional_text(payload, "instance_id")

        data: dict[str, Any] = {
            "reason": (
                "No Manager test-restore route is registered yet. "
                "Add a dedicated non-mutating Manager/Agent route before wiring this "
                "button to a backend operation."
            )
        }

        if backup_id:
            data["backup_id"] = backup_id
        if instance_id:
            data["instance_id"] = instance_id

        return GuiActionResult(
            ok=False,
            action=action,
            message="Backup test restore backend is not available.",
            instance_id=instance_id,
            data=data,
        )

    return GuiActionResult(
        ok=False,
        action=action,
        message=f"Unsupported backup action: {action}",
    )


async def _handle_set_target(
    action: str,
    payload: Mapping[str, Any],
) -> GuiActionResult:
    target_mode = action.removeprefix("set_target_")

    if target_mode not in TARGET_DEFAULTS:
        return GuiActionResult(
            ok=False,
            action=action,
            message=f"Unsupported target mode: {target_mode}",
        )

    config = {**payload, **TARGET_DEFAULTS[target_mode], "target_mode": target_mode}

    if target_mode == "intranet":
        exposure_mode = str(payload.get("exposure_mode") or "private")
        if exposure_mode not in {"private", "lan"}:
            raise ValueError("Intranet target only allows exposure_mode private or lan.")
        config["exposure_mode"] = exposure_mode

    if target_mode == "temporary_public":
        _require_text(payload, "public_mode_expires_at")
        if not _truthy(payload.get("confirmed")):
            raise ValueError("Temporary public target requires explicit confirmation.")

    if target_mode == "droplet":
        _require_text(payload, "droplet_host", "target_host", "host")
        _require_text(payload, "droplet_user", "ssh_user", "user")
        _require_text(payload, "ssh_key_path", "ssh_key", "droplet_ssh_key")
        _require_text(payload, "remote_kx_root", "remote_root", "droplet_kx_root")
        if not _truthy(payload.get("confirmed")):
            raise ValueError("Droplet target requires explicit confirmation.")

        _apply_public_runtime_host(config)

    service_data = await _validate_target_config(config)

    return GuiActionResult(
        ok=True,
        action=action,
        message=_target_message(target_mode),
        instance_id=_payload_instance_id(payload),
        data={**config, **service_data},
    )


def _queue_operation_job(
    action: str,
    payload: Mapping[str, Any],
) -> GuiActionResult:
    jobs = _import_module("kx_manager.services.operation_jobs")
    create_job = getattr(jobs, "create_operation_job", None)
    if create_job is None:
        return _missing_backend(action, "kx_manager.services.operation_jobs.create_operation_job")

    job = create_job(action, payload)
    job_id = str(job.get("job_id") or "")
    label = str(job.get("label") or action)
    return GuiActionResult(
        ok=bool(job_id),
        action=action,
        message=f"{label} queued." if job_id else f"{label} could not be queued.",
        instance_id=_payload_instance_id(payload),
        data={
            "operation_job_id": job_id,
            "status": job.get("status"),
            "phase": job.get("phase"),
            "progress": job.get("progress"),
            "status_url": f"/ui/operation-jobs/{job_id}" if job_id else None,
            "api_url": f"/api/operation-jobs/{job_id}" if job_id else None,
            "log_file": job.get("log_file"),
            "job_file": job.get("job_file"),
        },
    )


async def _handle_deploy(
    action: str,
    payload: Mapping[str, Any],
) -> GuiActionResult:
    deploy = _import_module("kx_manager.services.deploy")

    function_name = {
        "deploy_local": "deploy_local",
        "deploy_intranet": "deploy_intranet",
        "deploy_droplet": "deploy_droplet",
    }[action]

    if action == "deploy_droplet":
        _require_text(payload, "droplet_host", "target_host", "host")
        _require_text(payload, "droplet_user", "ssh_user", "user")
        _require_text(payload, "ssh_key_path", "ssh_key", "droplet_ssh_key")
        _require_text(payload, "remote_kx_root", "remote_root", "droplet_kx_root")
        _require_text(payload, "remote_capsule_dir", "capsule_dir")
        _require_text(payload, "domain", "droplet_domain")
        if not _truthy(payload.get("confirmed")):
            raise ValueError("Droplet deploy requires explicit confirmation.")

        # Browser forms opt into a background operation job. Direct service/API
        # callers remain synchronous for backward compatibility.
        if _truthy(payload.get("background_job")):
            return _queue_operation_job(action, payload)

    function = getattr(deploy, function_name, None)

    if function is None:
        return _missing_backend(action, f"kx_manager.services.deploy.{function_name}")

    request_class = {
        "deploy_local": "LocalDeployRequest",
        "deploy_intranet": "IntranetDeployRequest",
        "deploy_droplet": "DropletDeployRequest",
    }[action]

    service_payload = _execution_payload(action, payload)

    outcome = await _call_service_function(
        function,
        service_payload,
        request_module=deploy,
        request_class_name=request_class,
    )

    return _result_from_backend(
        action=action,
        outcome=outcome,
        payload=payload,
        default_message=f"{action} completed.",
    )


async def _handle_bootstrap_droplet_agent(
    action: str,
    payload: Mapping[str, Any],
) -> GuiActionResult:
    """Bootstrap the remote Konnaxion Agent over SSH."""

    _require_text(payload, "droplet_host", "target_host", "host")
    _require_text(payload, "droplet_user", "ssh_user", "user")
    _require_text(payload, "ssh_key_path", "ssh_key", "droplet_ssh_key")
    _require_text(payload, "remote_kx_root", "remote_root", "droplet_kx_root")
    _require_text(payload, "remote_capsule_dir", "capsule_dir")
    _require_text(payload, "domain", "droplet_domain")

    if not _truthy(payload.get("confirmed")):
        raise ValueError("Droplet bootstrap requires explicit confirmation.")

    service_payload = _execution_payload(action, payload)

    droplet_user = str(service_payload.get("droplet_user") or "").strip()
    if droplet_user != "root":
        return GuiActionResult(
            ok=False,
            action=action,
            message=(
                "Bootstrap currently requires SSH user root because it installs "
                "packages and writes a systemd service."
            ),
            instance_id=_payload_instance_id(payload),
            data={
                "droplet_user": droplet_user,
                "required_user": "root",
            },
        )

    client = _AgentHttpExecutionClient(
        base_url=_remote_agent_base_url(service_payload),
        droplet_payload=service_payload,
    )

    archive_path: Path | None = None

    try:
        archive_path = _make_manager_bootstrap_archive()

        remote_archive = f"/tmp/{archive_path.name}"
        remote_kx_root = _require_payload_text(service_payload, "remote_kx_root")
        remote_manager_dir = str(PurePosixPath(remote_kx_root) / "manager")

        copy_result = client._scp_file_to_path(
            service_payload,
            archive_path,
            remote_archive,
            timeout_seconds=600,
        )
        if not copy_result.get("ok"):
            return _result_from_backend(
                action=action,
                outcome=copy_result,
                payload=payload,
                default_message="Bootstrap failed while copying Manager archive.",
            )

        bootstrap_command = _remote_bootstrap_command(
            remote_archive=remote_archive,
            remote_kx_root=remote_kx_root,
            remote_manager_dir=remote_manager_dir,
            instance_id=str(service_payload.get("instance_id") or "demo-001"),
        )

        bootstrap_result = client._ssh(
            service_payload,
            bootstrap_command,
            timeout_seconds=900,
            success_message="Droplet Agent bootstrapped and started.",
        )

        bootstrap_result.setdefault("remote_kx_root", remote_kx_root)
        bootstrap_result.setdefault("remote_manager_dir", remote_manager_dir)
        bootstrap_result.setdefault("systemd_service", "konnaxion-agent.service")
        bootstrap_result.setdefault(
            "remote_agent_health_url",
            "http://127.0.0.1:8765/v1/health",
        )

        return _result_from_backend(
            action=action,
            outcome=bootstrap_result,
            payload=payload,
            default_message="Droplet Agent bootstrapped.",
        )

    finally:
        if archive_path is not None:
            try:
                archive_path.unlink(missing_ok=True)
            except Exception:
                pass


async def _handle_droplet_step(
    action: str,
    payload: Mapping[str, Any],
) -> GuiActionResult:
    deploy = _try_import_module("kx_manager.services.deploy")

    names_by_action = {
        "check_droplet_agent": ("check_droplet_agent", "check_remote_agent"),
        "copy_capsule_to_droplet": ("copy_capsule_to_droplet", "copy_capsule_remote"),
        "start_droplet_instance": ("start_droplet_instance", "start_remote_instance"),
    }

    _require_text(payload, "droplet_host", "target_host", "host")
    _require_text(payload, "droplet_user", "ssh_user", "user")
    _require_text(payload, "ssh_key_path", "ssh_key", "droplet_ssh_key")
    _require_text(payload, "remote_kx_root", "remote_root", "droplet_kx_root")
    _require_text(payload, "remote_capsule_dir", "capsule_dir")
    _require_text(payload, "domain", "droplet_domain")

    if not _truthy(payload.get("confirmed")):
        raise ValueError("Droplet operation requires explicit confirmation.")

    if action == "copy_capsule_to_droplet" and _truthy(payload.get("background_job")):
        # Large capsules should upload in a background job with visible byte-level
        # progress instead of making the browser wait on one long HTTP request.
        return _queue_operation_job(action, payload)

    if deploy is not None:
        service_payload = _execution_payload(action, payload)

        for function_name in names_by_action[action]:
            function = getattr(deploy, function_name, None)
            if function is not None:
                outcome = await _call_service_function(
                    function,
                    service_payload,
                    request_module=deploy,
                    request_class_name="DropletDeployRequest",
                )

                return _result_from_backend(
                    action=action,
                    outcome=outcome,
                    payload=payload,
                    default_message=f"{action} completed.",
                )

    if action == "check_droplet_agent":
        host = _require_text(payload, "droplet_host", "target_host", "host")
        url = str(payload.get("remote_agent_url") or f"http://{host}:8765/v1/health")

        if not url.endswith("/health"):
            url = url.rstrip("/") + "/health"

        data = await _http_json_request("GET", url)

        return _result_from_backend(
            action=action,
            outcome=data,
            payload=payload,
            default_message="Droplet Agent health check completed.",
            ok_default=False,
        )

    return _missing_backend(
        action,
        f"kx_manager.services.deploy.{names_by_action[action][0]}",
    )


async def _handle_open_instance(
    action: str,
    payload: Mapping[str, Any],
) -> GuiActionResult:
    url = str(
        payload.get("url")
        or payload.get("public_url")
        or payload.get("private_url")
        or payload.get("runtime_url")
        or "http://127.0.0.1"
    ).strip()

    return GuiActionResult(
        ok=True,
        action=action,
        message="Runtime URL ready.",
        instance_id=_payload_instance_id(payload),
        data={"url": url, "kind": "browser_link"},
    )


async def _handle_open_manager_docs(
    action: str,
    payload: Mapping[str, Any],
) -> GuiActionResult:
    del payload

    return GuiActionResult(
        ok=True,
        action=action,
        message="Manager API docs URL ready.",
        data={"url": _manager_base_url().rstrip("/") + "/docs", "kind": "browser_link"},
    )


async def _handle_open_agent_docs(
    action: str,
    payload: Mapping[str, Any],
) -> GuiActionResult:
    del payload

    base = _agent_base_url().removesuffix("/v1")

    return GuiActionResult(
        ok=True,
        action=action,
        message="Agent API docs URL ready.",
        data={"url": base.rstrip("/") + "/docs", "kind": "browser_link"},
    )


ACTION_HANDLERS: dict[str, ActionHandler] = {
    "check_manager": _handle_check_manager,
    "check_agent": _handle_check_agent,
    "select_source_folder": _handle_select_source_folder,
    "select_capsule_output_folder": _handle_select_capsule_output_folder,
    "build_capsule": _handle_build_capsule,
    "rebuild_capsule": _handle_build_capsule,
    "verify_capsule": _handle_verify_capsule,
    "import_capsule": _handle_import_capsule,
    "list_capsules": _handle_manager_capsule_action,
    "view_capsule": _handle_manager_capsule_action,
    "create_instance": _handle_create_instance,
    "update_instance": _handle_update_instance,
    "start_instance": _handle_start_instance,
    "stop_instance": _handle_stop_instance,
    "restart_instance": _handle_restart_instance,
    "instance_status": _handle_instance_status,
    "view_logs": _handle_view_logs,
    "view_health": _handle_view_health,
    "open_instance": _handle_open_instance,
    "rollback_instance": _handle_rollback_instance,
    "create_backup": _handle_create_backup,
    "list_backups": _handle_manager_backup_action,
    "verify_backup": _handle_manager_backup_action,
    "restore_backup": _handle_restore_backup,
    "restore_backup_new": _handle_restore_backup_new,
    "test_restore_backup": _handle_manager_backup_action,
    "run_security_check": _handle_run_security_check,
    "set_network_profile": _handle_set_network_profile,
    "disable_public_mode": _handle_disable_public_mode,
    "set_target_local": _handle_set_target,
    "set_target_intranet": _handle_set_target,
    "set_target_droplet": _handle_set_target,
    "set_target_temporary_public": _handle_set_target,
    "deploy_local": _handle_deploy,
    "deploy_intranet": _handle_deploy,
    "deploy_droplet": _handle_deploy,
    "bootstrap_droplet_agent": _handle_bootstrap_droplet_agent,
    "check_droplet_agent": _handle_droplet_step,
    "copy_capsule_to_droplet": _handle_droplet_step,
    "start_droplet_instance": _handle_droplet_step,
    "open_manager_docs": _handle_open_manager_docs,
    "open_agent_docs": _handle_open_agent_docs,
}


__all__ = [
    "ACTION_HANDLERS",
    "ActionHandler",
]

