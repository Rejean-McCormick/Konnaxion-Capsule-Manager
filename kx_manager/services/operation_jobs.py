"""Persistent asynchronous jobs for long Capsule Manager operations.

The browser UI uses these jobs for operations that can take minutes (large VPS
uploads and full Droplet deployment). State is persisted below KX_ROOT so the
progress page survives refreshes and Manager restarts.
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from kx_manager.defaults import DEFAULT_RUNTIME_ROOT

ACTIVE_STATUSES = frozenset({"queued", "running"})
FINAL_STATUSES = frozenset({"succeeded", "failed", "interrupted"})
SUPPORTED_ACTIONS = frozenset({"copy_capsule_to_droplet", "deploy_droplet"})
DEFAULT_OPERATION_CONCURRENCY = 1
HEARTBEAT_SECONDS = 10

_executor_lock = threading.Lock()
_executor: ThreadPoolExecutor | None = None
_job_write_lock = threading.RLock()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _runtime_root() -> Path:
    explicit = os.getenv("KX_ROOT", "").strip() or os.getenv("KX_RUNTIME_ROOT", "").strip()
    return Path(explicit or DEFAULT_RUNTIME_ROOT).expanduser()


def operation_job_dir() -> Path:
    explicit = os.getenv("KX_OPERATION_JOB_DIR", "").strip()
    path = Path(explicit).expanduser() if explicit else _runtime_root() / "manager" / "operation-jobs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def ensure_operation_job_store() -> Path:
    path = operation_job_dir()
    marker = path / "README.txt"
    if not marker.exists():
        marker.write_text(
            "Konnaxion Capsule Manager operation jobs.\n"
            "Long Droplet uploads/deployments create <job-id>.json and <job-id>.log.\n"
            "latest-job.txt contains the most recently submitted operation job id.\n",
            encoding="utf-8",
        )
    return path


def _job_paths(job_id: str) -> tuple[Path, Path]:
    root = ensure_operation_job_store()
    return root / f"{job_id}.json", root / f"{job_id}.log"


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(dict(payload), indent=2, sort_keys=True, default=str), encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    return dict(value) if isinstance(value, Mapping) else None


def _write_job(job: Mapping[str, Any]) -> dict[str, Any]:
    data = dict(job)
    data["updated_at"] = _utc_now_iso()
    json_path, _log_path = _job_paths(str(data["job_id"]))
    with _job_write_lock:
        _atomic_write_json(json_path, data)
    return data


def _update_job(job_id: str, **changes: Any) -> dict[str, Any]:
    with _job_write_lock:
        current = _read_json(_job_paths(job_id)[0]) or {"job_id": job_id}
        current.update(changes)
        return _write_job(current)


def append_job_log(job_id: str, message: str) -> None:
    _json_path, log_path = _job_paths(job_id)
    timestamp = _utc_now_iso()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8", errors="replace") as handle:
        handle.write(f"[{timestamp}] {message.rstrip()}\n")


def _executor_for_config() -> ThreadPoolExecutor:
    global _executor
    with _executor_lock:
        if _executor is None:
            _executor = ThreadPoolExecutor(
                max_workers=DEFAULT_OPERATION_CONCURRENCY,
                thread_name_prefix="kx-operation",
            )
        return _executor


def _coerce_bool(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on", "checked"}:
        return True
    if text in {"0", "false", "no", "off", ""}:
        return False
    return bool(text)


def _serialize_result(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        result = to_dict()
        return dict(result) if isinstance(result, Mapping) else {"result": str(result)}
    if is_dataclass(value):
        return asdict(value)
    return {"result": repr(value)}


def _safe_job_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Persist useful operator fields without executable client objects."""

    allowed = {
        "instance_id",
        "capsule_file",
        "capsule_id",
        "capsule_version",
        "droplet_name",
        "droplet_host",
        "droplet_user",
        "ssh_port",
        "remote_kx_root",
        "remote_capsule_dir",
        "domain",
        "network_profile",
        "target_mode",
        "copy_capsule",
        "one_click_release",
        "source_dir",
        "capsule_output_dir",
    }
    return {key: value for key, value in dict(payload).items() if key in allowed}


def _public_host(payload: Mapping[str, Any]) -> str:
    for key in ("domain", "droplet_domain", "public_host", "host"):
        value = str(payload.get(key) or "").strip()
        if value:
            return value
    return ""


def _operation_request(
    action: str,
    payload: Mapping[str, Any],
    progress_callback: Any,
) -> Any:
    from kx_manager.services import deploy
    from kx_manager.ui.agent_execution_client import (
        _AgentHttpExecutionClient,
        _remote_agent_base_url,
    )

    data = dict(payload)
    data["target_mode"] = "droplet"
    data["network_profile"] = "public_vps"
    data["exposure_mode"] = "public"
    data["confirmed"] = True

    public_host = _public_host(data)
    if public_host:
        data["host"] = public_host
        data["public_host"] = public_host
        data["domain"] = public_host
        data["droplet_domain"] = public_host

    if data.get("remote_kx_root"):
        data["runtime_root"] = data["remote_kx_root"]
    if data.get("remote_capsule_dir"):
        data["capsule_dir"] = data["remote_capsule_dir"]
    if data.get("capsule_file"):
        data["capsule_path"] = data["capsule_file"]

    data["progress_callback"] = progress_callback
    data["manager_client"] = _AgentHttpExecutionClient(
        base_url=_remote_agent_base_url(data),
        droplet_payload=data,
        progress_callback=progress_callback,
    )

    request_class = deploy.DropletDeployRequest
    allowed = set(request_class.__dataclass_fields__)
    values = {key: value for key, value in data.items() if key in allowed}

    for key in ("confirmed", "copy_capsule", "build", "verify", "update_existing", "run_security_gate", "start", "plan_only"):
        if key in values:
            values[key] = _coerce_bool(values[key], default=bool(getattr(request_class, key, False)))

    if "ssh_port" in values:
        try:
            values["ssh_port"] = int(values["ssh_port"])
        except (TypeError, ValueError):
            values["ssh_port"] = 22

    return request_class(**values)


def _heartbeat(job_id: str, stop_event: threading.Event) -> None:
    while not stop_event.wait(HEARTBEAT_SECONDS):
        current = get_operation_job(job_id, include_log=False)
        if not current or current.get("status") not in ACTIVE_STATUSES:
            return
        _update_job(job_id, heartbeat_at=_utc_now_iso())


def _result_ok(value: Mapping[str, Any] | None) -> bool:
    return bool(value and value.get("ok"))


def _run_one_click_release(
    job_id: str,
    payload: Mapping[str, Any],
    progress_callback: Any,
) -> dict[str, Any]:
    """Run the complete production path behind the Dashboard GO LIVE button."""

    import asyncio
    import httpx

    from kx_manager.services import deploy, release
    from kx_manager.ui.action_backends import _handle_bootstrap_droplet_agent
    from kx_manager.ui.agent_execution_client import (
        _AgentHttpExecutionClient,
        _remote_agent_base_url,
    )

    data = dict(payload)
    data.update(
        {
            "target_mode": "droplet",
            "network_profile": "public_vps",
            "exposure_mode": "public",
            "confirmed": True,
            "background_job": False,
            "copy_capsule": True,
        }
    )

    progress_callback("keys", 5, "Preparing persistent Ed25519 release signing keys.")
    prepared = release.prepare_signed_release(data)
    data.update(
        {
            "capsule_id": prepared["capsule_id"],
            "capsule_version": prepared["capsule_version"],
            "capsule_file": prepared["capsule_file"],
            "capsule_path": prepared["capsule_file"],
            "public_key_file": prepared["public_key_file"],
        }
    )
    _update_job(
        job_id,
        capsule_id=prepared["capsule_id"],
        capsule_file=prepared["capsule_file"],
    )
    append_job_log(
        job_id,
        "release: signed capsule built and verified "
        f"({prepared['capsule_id']} {prepared['capsule_version']})",
    )
    progress_callback("release", 35, "Signed production capsule built and verified.")

    # Refreshing the Agent is deliberate for GO LIVE: it installs the exact
    # Manager/Agent code used by this release and installs the matching PUBLIC
    # capsule key without ever copying the private key to the Droplet.
    progress_callback("agent", 40, "Refreshing Droplet Agent and trusted release public key.")
    bootstrap = asyncio.run(_handle_bootstrap_droplet_agent("bootstrap_droplet_agent", data))
    bootstrap_data = bootstrap.to_dict()
    if not bootstrap.ok:
        raise RuntimeError(f"Droplet Agent bootstrap failed: {bootstrap.message}")
    append_job_log(job_id, "agent: refreshed and trusted release public key installed")

    client = _AgentHttpExecutionClient(
        base_url=_remote_agent_base_url(data),
        droplet_payload=data,
    )
    agent_health = client.check_droplet_agent(**data)
    if not _result_ok(agent_health):
        raise RuntimeError(
            "Droplet Agent health failed after bootstrap: "
            + str(agent_health.get("message") or agent_health)
        )
    progress_callback("agent", 50, "Droplet Agent healthy.")

    # Existing production instance => verified pre-deploy backup. A missing
    # instance is a first deployment and is not an error.
    backup: dict[str, Any] = {"ok": True, "skipped": True, "reason": "instance_not_present"}
    status = client._post("/instances/status", {"instance_id": data["instance_id"]})
    status_data = status.get("data") if isinstance(status.get("data"), Mapping) else {}
    services = status_data.get("services") if isinstance(status_data, Mapping) else None
    status_text = " ".join(
        str(value or "")
        for value in (status.get("message"), status.get("stderr"), status.get("error"))
    ).lower()
    absent_markers = ("not found", "does not exist", "no such file", "compose file")
    instance_absent = (
        _result_ok(status) and isinstance(services, list) and not services
    ) or (
        not _result_ok(status) and any(marker in status_text for marker in absent_markers)
    )

    if _result_ok(status) and not instance_absent:
        progress_callback("backup", 54, "Creating verified pre-deploy backup.")
        backup = client._post(
            "/instances/backup",
            {
                "instance_id": data["instance_id"],
                "backup_class": "pre_update",
                "verify_after_create": True,
            },
        )
        if not _result_ok(backup):
            raise RuntimeError(
                "Pre-deploy backup failed: " + str(backup.get("message") or backup)
            )
        append_job_log(job_id, "backup: verified pre-deploy backup completed")
    elif instance_absent:
        append_job_log(job_id, "backup: no existing instance detected; backup skipped")
    else:
        raise RuntimeError(
            "Unable to determine existing instance state before backup: "
            + str(status.get("message") or status)
        )
    progress_callback("backup", 58, "Pre-deploy backup stage complete.")

    def deploy_progress(
        phase: str,
        progress: int,
        message: str,
        detail: Mapping[str, Any] | None = None,
    ) -> None:
        mapped = 60 + int(max(0, min(100, progress)) * 0.35)
        progress_callback(f"deploy:{phase}", mapped, message, detail)

    request = _operation_request("deploy_droplet", data, deploy_progress)
    request.build = False
    request.verify = True
    request.copy_capsule = True
    request.run_security_gate = True
    request.start = True
    outcome = deploy.deploy_droplet(request)
    deploy_data = _serialize_result(outcome)
    if not bool(deploy_data.get("ok", getattr(outcome, "ok", False))):
        raise RuntimeError(
            "Production deployment failed: "
            + str(deploy_data.get("message") or "deploy_droplet returned failure")
        )

    progress_callback("health", 96, "Checking final instance health.")
    final_health = client._post("/instances/health", {"instance_id": data["instance_id"]})
    if not _result_ok(final_health):
        raise RuntimeError(
            "Final Agent instance health failed: "
            + str(final_health.get("message") or final_health)
        )

    domain = str(data.get("domain") or data.get("droplet_domain") or "").strip()
    public_url = f"https://{domain.strip('/')}" if domain else ""
    https_result: dict[str, Any] = {"ok": False, "url": public_url}
    if not public_url:
        raise RuntimeError("Public domain is missing; HTTPS final check cannot run.")

    progress_callback("https", 98, f"Checking public HTTPS: {public_url}")
    last_error = ""
    for attempt in range(1, 13):
        try:
            response = httpx.get(public_url, follow_redirects=True, timeout=10.0)
            https_result = {
                "ok": response.status_code < 400,
                "url": public_url,
                "status_code": response.status_code,
                "final_url": str(response.url),
                "attempt": attempt,
            }
            if https_result["ok"]:
                break
            last_error = f"HTTP {response.status_code}"
        except Exception as exc:  # noqa: BLE001 - bounded public readiness probe
            last_error = str(exc)
        if attempt < 12:
            import time
            time.sleep(5)

    if not https_result.get("ok"):
        raise RuntimeError(f"Public HTTPS health did not become ready: {last_error}")

    append_job_log(job_id, f"https: public runtime healthy at {public_url}")
    progress_callback("complete", 100, "GO LIVE completed successfully.")
    return {
        "ok": True,
        "action": "deploy_droplet",
        "message": "GO LIVE completed: signed release deployed and healthy.",
        "instance_id": data["instance_id"],
        "capsule_id": prepared["capsule_id"],
        "capsule_version": prepared["capsule_version"],
        "capsule_file": prepared["capsule_file"],
        "public_url": public_url,
        "public_key_fingerprint": prepared["public_key_fingerprint"],
        "signing_keys_created": prepared["signing_keys_created"],
        "bootstrap": bootstrap_data,
        "backup": backup,
        "deploy": deploy_data,
        "health": final_health,
        "https": https_result,
    }


def _run_job(job_id: str, action: str, payload: Mapping[str, Any]) -> None:
    from kx_manager.services import deploy

    stop_event = threading.Event()
    heartbeat = threading.Thread(
        target=_heartbeat,
        args=(job_id, stop_event),
        name=f"kx-operation-heartbeat-{job_id[:8]}",
        daemon=True,
    )

    last_logged: tuple[str, int, str] | None = None

    def progress_callback(
        phase: str,
        progress: int,
        message: str,
        data: Mapping[str, Any] | None = None,
    ) -> None:
        nonlocal last_logged
        progress = max(0, min(99, int(progress)))
        changes: dict[str, Any] = {
            "phase": str(phase),
            "progress": progress,
            "message": str(message),
        }
        if data:
            changes["progress_data"] = dict(data)
        _update_job(job_id, **changes)
        marker = (str(phase), progress, str(message))
        if marker != last_logged:
            append_job_log(job_id, f"{phase} [{progress}%]: {message}")
            last_logged = marker

    try:
        _update_job(
            job_id,
            status="running",
            phase="starting",
            progress=2,
            message="Starting long operation.",
            started_at=_utc_now_iso(),
        )
        append_job_log(job_id, f"starting: {action}")
        heartbeat.start()

        if action == "deploy_droplet" and _coerce_bool(payload.get("one_click_release")):
            result_data = _run_one_click_release(job_id, payload, progress_callback)
            success = bool(result_data.get("ok"))
        else:
            request = _operation_request(action, payload, progress_callback)

            if action == "copy_capsule_to_droplet":
                outcome = deploy.copy_capsule_to_droplet(
                    request,
                    capsule_file=payload.get("capsule_file") or payload.get("capsule_path"),
                )
            elif action == "deploy_droplet":
                outcome = deploy.deploy_droplet(request)
            else:  # pragma: no cover - guarded by create_operation_job
                raise ValueError(f"Unsupported operation action: {action}")

            result_data = _serialize_result(outcome)
            success = bool(result_data.get("ok", getattr(outcome, "ok", False)))

        if success:
            append_job_log(job_id, f"complete: {action} succeeded")
            _update_job(
                job_id,
                status="succeeded",
                phase="complete",
                progress=100,
                message=str(result_data.get("message") or "Operation completed successfully."),
                result=result_data,
                error=None,
                finished_at=_utc_now_iso(),
            )
        else:
            detail = str(result_data.get("message") or "Operation returned a failed result.")
            append_job_log(job_id, f"failed: {detail}")
            current = get_operation_job(job_id, include_log=False) or {}
            _update_job(
                job_id,
                status="failed",
                phase="failed",
                progress=max(2, int(current.get("progress") or 2)),
                message=detail,
                result=result_data,
                error=detail,
                finished_at=_utc_now_iso(),
            )
    except Exception as exc:  # noqa: BLE001 - persisted operator error boundary
        append_job_log(job_id, f"failed: {type(exc).__name__}: {exc}")
        current = get_operation_job(job_id, include_log=False) or {}
        _update_job(
            job_id,
            status="failed",
            phase="failed",
            progress=max(2, int(current.get("progress") or 2)),
            message=f"Operation failed: {exc}",
            error=str(exc),
            finished_at=_utc_now_iso(),
        )
    finally:
        stop_event.set()


def create_operation_job(action: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    if action not in SUPPORTED_ACTIONS:
        raise ValueError(f"Unsupported long operation: {action}")

    root = ensure_operation_job_store()
    job_id = str(uuid.uuid4())
    _json_path, log_path = _job_paths(job_id)
    created_at = _utc_now_iso()

    labels = {
        "copy_capsule_to_droplet": "Copy Capsule to Droplet",
        "deploy_droplet": (
            "GO LIVE — Production Release"
            if _coerce_bool(payload.get("one_click_release"))
            else "Deploy Droplet"
        ),
    }
    job = {
        "job_id": job_id,
        "action": action,
        "label": labels.get(action, action),
        "status": "queued",
        "phase": "queued",
        "progress": 1,
        "message": f"{labels.get(action, action)} queued.",
        "created_at": created_at,
        "started_at": None,
        "updated_at": created_at,
        "finished_at": None,
        "heartbeat_at": None,
        "instance_id": payload.get("instance_id"),
        "capsule_id": payload.get("capsule_id"),
        "capsule_file": payload.get("capsule_file") or payload.get("capsule_path"),
        "droplet_name": payload.get("droplet_name"),
        "droplet_host": payload.get("droplet_host") or payload.get("target_host"),
        "domain": payload.get("domain") or payload.get("droplet_domain"),
        "job_file": str(root / f"{job_id}.json"),
        "log_file": str(log_path),
        "payload": _safe_job_payload(payload),
        "progress_data": {},
        "result": None,
        "error": None,
    }
    _write_job(job)
    append_job_log(job_id, f"queued: {action}")
    (root / "latest-job.txt").write_text(job_id + "\n", encoding="utf-8")
    _executor_for_config().submit(_run_job, job_id, action, dict(payload))
    return get_operation_job(job_id) or job


def get_operation_job(job_id: str, *, include_log: bool = True) -> dict[str, Any] | None:
    json_path, log_path = _job_paths(job_id)
    job = _read_json(json_path)
    if job is None:
        return None
    if include_log:
        try:
            text = log_path.read_text(encoding="utf-8", errors="replace")
        except FileNotFoundError:
            text = ""
        job["log_tail"] = "\n".join(text.splitlines()[-400:])
    return job


def list_operation_jobs(limit: int = 20) -> list[dict[str, Any]]:
    root = ensure_operation_job_store()
    jobs: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        item = _read_json(path)
        if item:
            jobs.append(item)
        if len(jobs) >= max(1, limit):
            break
    return jobs


__all__ = [
    "ACTIVE_STATUSES",
    "FINAL_STATUSES",
    "SUPPORTED_ACTIONS",
    "append_job_log",
    "create_operation_job",
    "ensure_operation_job_store",
    "get_operation_job",
    "list_operation_jobs",
    "operation_job_dir",
]
