"""Persistent asynchronous jobs for long Capsule Manager operations.

The browser UI uses these jobs for operations that can take minutes (large VPS
uploads and full Droplet deployment). State is persisted below KX_ROOT so the
progress page survives refreshes and Manager restarts.
"""

from __future__ import annotations

import json
import os
import shlex
import tempfile
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
SUPPORTED_ACTIONS = frozenset({"copy_capsule_to_droplet", "deploy_droplet", "initialize_production_data"})
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


def _diagnostic_tail(value: Any, *, limit: int = 3500) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return "..." + text[-limit:]


def _command_failure_detail(label: str, result: Mapping[str, Any] | None) -> str:
    data = dict(result or {})
    parts = [label]
    returncode = data.get("returncode")
    if returncode not in (None, ""):
        parts.append(f"rc={returncode}")
    stderr = _diagnostic_tail(data.get("stderr"))
    stdout = _diagnostic_tail(data.get("stdout"))
    message = str(data.get("message") or "").strip()
    if message and message != "Command failed.":
        parts.append(message)
    if stderr:
        parts.append(f"stderr: {stderr}")
    if stdout:
        parts.append(f"stdout: {stdout}")
    return " | ".join(parts)


def _refresh_healthy_agent_code(client: Any, data: Mapping[str, Any]) -> dict[str, Any]:
    """Refresh only Manager/Agent source on an already healthy Droplet Agent."""

    from pathlib import PurePosixPath

    from kx_manager.ui.droplet_bootstrap import (
        _make_manager_bootstrap_archive,
        _remote_refresh_agent_command,
    )

    archive_path = _make_manager_bootstrap_archive()
    try:
        remote_archive = f"/tmp/{archive_path.name}"
        copied = client._scp_file_to_path(
            data,
            archive_path,
            remote_archive,
            timeout_seconds=600,
        )
        if not _result_ok(copied):
            return copied

        remote_root = str(data.get("remote_kx_root") or "/opt/konnaxion").rstrip("/")
        remote_manager_dir = str(PurePosixPath(remote_root) / "manager")
        command = _remote_refresh_agent_command(
            remote_archive=remote_archive,
            remote_kx_root=remote_root,
            remote_manager_dir=remote_manager_dir,
        )
        ssh_runner = getattr(client, "_ssh_privileged", client._ssh)
        result = ssh_runner(
            data,
            command,
            timeout_seconds=900,
            success_message="Healthy Droplet Agent source refreshed.",
        )
        result.setdefault("remote_manager_dir", remote_manager_dir)
        return result
    finally:
        try:
            archive_path.unlink(missing_ok=True)
        except Exception:
            pass


def _sync_trusted_release_public_key(
    client: Any,
    data: Mapping[str, Any],
    public_key_file: str,
) -> dict[str, Any]:
    """Install only the trusted release public key on a healthy Agent host.

    GO LIVE should not reinstall packages, Docker, uv, or the Agent source tree
    when the existing private Agent is already healthy. Signature verification
    reads the canonical key file for every capsule verification, so rotating the
    public key file is sufficient and keeps the production control plane stable.
    """

    import shlex

    local_key = Path(public_key_file).expanduser()
    if not local_key.is_file():
        return {
            "ok": False,
            "message": f"Trusted release public key is missing locally: {local_key}",
            "returncode": 2,
        }

    remote_tmp = "/tmp/konnaxion-capsule-signing-public.pem"
    copied = client._scp_file_to_path(
        data,
        local_key,
        remote_tmp,
        timeout_seconds=120,
    )
    if not _result_ok(copied):
        return copied

    remote_root = str(data.get("remote_kx_root") or "/opt/konnaxion").rstrip("/")
    key_dir = f"{remote_root}/agent/keys"
    key_file = f"{key_dir}/capsule-signing-public.pem"
    command = f"""set -e
if ! getent group kx-agent >/dev/null 2>&1; then
  echo 'kx-agent group is missing; full bootstrap is required' >&2
  exit 67
fi
install -d -m 0750 -o root -g kx-agent {shlex.quote(key_dir)}
install -m 0644 -o root -g kx-agent {shlex.quote(remote_tmp)} {shlex.quote(key_file)}
rm -f {shlex.quote(remote_tmp)}
test -s {shlex.quote(key_file)}
systemctl is-active --quiet konnaxion-agent
curl --fail-with-body --max-time 10 -sS http://127.0.0.1:8765/v1/health
"""
    ssh_runner = getattr(client, "_ssh_privileged", client._ssh)
    result = ssh_runner(
        data,
        command,
        timeout_seconds=60,
        success_message="Trusted release public key updated on healthy Droplet Agent.",
    )
    result.setdefault("remote_public_key_file", key_file)
    return result


def _run_one_click_release(
    job_id: str,
    payload: Mapping[str, Any],
    progress_callback: Any,
) -> dict[str, Any]:
    """Run the complete production path behind the Dashboard GO LIVE button."""

    import asyncio
    import httpx

    from kx_manager.defaults import droplet_environment_overrides
    from kx_manager.services import deploy, release
    from kx_manager.ui.action_backends import _handle_bootstrap_droplet_agent
    from kx_manager.ui.agent_execution_client import (
        _AgentHttpExecutionClient,
        _remote_agent_base_url,
    )

    data = dict(payload)
    env_overrides = droplet_environment_overrides()
    data.update(env_overrides)

    # Historical Netcup production access uses the hardened non-root operator
    # account ``kx-admin`` with passwordless ``sudo -n``. Older Manager UI
    # state persisted ``root`` and can survive upgrades. When the operator has
    # not explicitly overridden the SSH user via environment, migrate only
    # that known legacy target for GO LIVE instead of repeatedly attempting
    # a root login that production SSH intentionally rejects.
    legacy_user = str(data.get("droplet_user") or data.get("ssh_user") or "").strip()
    droplet_name = str(data.get("droplet_name") or "").strip().lower()
    droplet_host = str(data.get("droplet_host") or data.get("host") or "").strip()
    if (
        "droplet_user" not in env_overrides
        and legacy_user == "root"
        and (droplet_name == "netcup-vps" or droplet_host == "2.56.97.41")
    ):
        data["droplet_user"] = "kx-admin"
        data["ssh_user"] = "kx-admin"
        append_job_log(
            job_id,
            "target: migrated legacy Netcup SSH user root -> kx-admin for GO LIVE",
        )
    if "droplet_host" in env_overrides:
        data["host"] = data["droplet_host"]
    if "remote_kx_root" in env_overrides:
        data["runtime_root"] = data["remote_kx_root"]
    if "remote_capsule_dir" in env_overrides:
        data["capsule_dir"] = data["remote_capsule_dir"]
    if "domain" in env_overrides:
        data["droplet_domain"] = data["domain"]
    if env_overrides:
        append_job_log(
            job_id,
            "target: operator .env/process KX_DROPLET_* values applied to GO LIVE",
        )
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

    client = _AgentHttpExecutionClient(
        base_url=_remote_agent_base_url(data),
        droplet_payload=data,
    )

    # Production-first path: if the private Agent already answers health, keep
    # it running and rotate only the trusted release PUBLIC key. Reinstalling
    # packages/Docker/uv on every application release adds avoidable failure
    # modes and is not required to deploy a compatible signed Konnaxion capsule.
    progress_callback("agent", 40, "Checking existing private Droplet Agent.")
    pre_agent_health = client.check_droplet_agent(**data)
    bootstrap_data: dict[str, Any]
    agent_mode = "reuse"

    if _result_ok(pre_agent_health):
        progress_callback("agent", 43, "Existing Agent healthy; refreshing Agent code and trusted release key.")
        refresh = _refresh_healthy_agent_code(client, data)
        if _result_ok(refresh):
            key_sync = _sync_trusted_release_public_key(
                client,
                data,
                prepared["public_key_file"],
            )
        else:
            key_sync = {"ok": False, "message": "Agent source refresh failed before key sync."}

        if _result_ok(refresh) and _result_ok(key_sync):
            bootstrap_data = {
                "ok": True,
                "skipped": True,
                "reason": "existing_agent_refreshed",
                "refresh": refresh,
                "key_sync": key_sync,
                "pre_health": pre_agent_health,
            }
            agent_mode = "refresh"
            append_job_log(job_id, "agent: healthy Agent source refreshed; trusted release public key updated")
        else:
            failed = refresh if not _result_ok(refresh) else key_sync
            append_job_log(
                job_id,
                "agent: lightweight refresh failed; falling back to full bootstrap: "
                + _command_failure_detail("agent refresh", failed),
            )
            agent_mode = "bootstrap"
            bootstrap = asyncio.run(
                _handle_bootstrap_droplet_agent("bootstrap_droplet_agent", data)
            )
            bootstrap_data = bootstrap.to_dict()
            if not bootstrap.ok:
                raise RuntimeError(
                    _command_failure_detail(
                        "Droplet Agent bootstrap failed",
                        bootstrap_data,
                    )
                )
            append_job_log(job_id, "agent: full bootstrap completed after refresh fallback")
    else:
        agent_mode = "bootstrap"
        append_job_log(
            job_id,
            "agent: existing Agent not healthy; full bootstrap required: "
            + _command_failure_detail("pre-health", pre_agent_health),
        )
        progress_callback("agent", 43, "Agent unavailable; bootstrapping control plane.")
        bootstrap = asyncio.run(_handle_bootstrap_droplet_agent("bootstrap_droplet_agent", data))
        bootstrap_data = bootstrap.to_dict()
        if not bootstrap.ok:
            raise RuntimeError(
                _command_failure_detail(
                    "Droplet Agent bootstrap failed",
                    bootstrap_data,
                )
            )
        append_job_log(job_id, "agent: full bootstrap completed")

    agent_health = client.check_droplet_agent(**data)
    if not _result_ok(agent_health):
        raise RuntimeError(
            _command_failure_detail(
                "Droplet Agent health failed after preparation",
                agent_health,
            )
        )
    progress_callback("agent", 50, f"Droplet Agent healthy ({agent_mode}).")

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
        "agent_mode": agent_mode,
        "bootstrap": bootstrap_data,
        "backup": backup,
        "deploy": deploy_data,
        "health": final_health,
        "https": https_result,
    }


def _normalize_source_database_url(value: str) -> str:
    """Normalize a source PostgreSQL URL without exposing credentials."""

    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

    raw = str(value or "").strip().replace("\r", "").replace("\n", "")
    if not raw:
        return ""

    parts = urlsplit(raw)
    query = []
    for key, item in parse_qsl(parts.query, keep_blank_values=True):
        clean_key = key.strip().replace("\r", "").replace("\n", "")
        clean_value = (
            item.strip()
            .replace("\r", "")
            .replace("\n", "")
            .replace("\\r", "")
            .replace("\\n", "")
        )
        query.append((clean_key, clean_value))

    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def _read_source_database_url(source_dir: str | os.PathLike[str]) -> str:
    """Read the first valid local Konnaxion PostgreSQL URL without logging it."""

    from urllib.parse import urlsplit

    root = Path(source_dir).expanduser()
    candidates = (
        # Django is the canonical source used by local Konnaxion. Do not let a
        # secondary postgres env file overwrite a valid application URL.
        root / "backend" / ".envs" / ".local" / ".django",
        root / ".envs" / ".local" / ".django",
        root / "backend" / ".envs" / ".local" / ".postgres",
        root / ".envs" / ".local" / ".postgres",
    )

    found_any = False
    for path in candidates:
        if not path.is_file():
            continue
        for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.strip()
            if line.startswith("export "):
                line = line[7:].lstrip()
            if not line.startswith("DATABASE_URL="):
                continue

            candidate = line.split("=", 1)[1].strip()
            if len(candidate) >= 2 and candidate[0] == candidate[-1] and candidate[0] in {"'", '"'}:
                candidate = candidate[1:-1]
            if not candidate:
                continue

            found_any = True
            value = _normalize_source_database_url(candidate)
            parts = urlsplit(value)
            if parts.scheme not in {"postgres", "postgresql"}:
                continue
            if not parts.hostname or not parts.username or not parts.path.strip("/"):
                continue

            if "-pooler." in value:
                value = value.replace("-pooler.", ".", 1)
            return value

    tried = ", ".join(str(path) for path in candidates)
    if found_any:
        raise RuntimeError(
            "DATABASE_URL was found but no valid postgres/postgresql URL was available "
            f"below selected Source Folder. Tried: {tried}"
        )
    raise RuntimeError(f"DATABASE_URL not found below selected Source Folder. Tried: {tried}")


def _run_initial_production_data(
    job_id: str,
    payload: Mapping[str, Any],
    progress_callback: Any,
) -> dict[str, Any]:
    """Bootstrap the selected production instance from the local Konnaxion Neon DB."""

    import httpx

    from kx_manager.ui.agent_execution_client import (
        _AgentHttpExecutionClient,
        _remote_agent_base_url,
    )
    from kx_shared.konnaxion_constants import docker_project_name

    data = dict(payload)
    source_dir = str(data.get("source_dir") or "").strip()
    instance_id = str(data.get("instance_id") or "").strip()
    remote_root = str(data.get("remote_kx_root") or "/opt/konnaxion").rstrip("/")
    domain = str(data.get("domain") or data.get("droplet_domain") or "").strip().strip("/")

    if not source_dir:
        raise RuntimeError("Source Folder is missing.")
    if not instance_id:
        raise RuntimeError("Instance ID is missing.")
    if not domain:
        raise RuntimeError("Production domain is missing.")

    progress_callback("source", 8, "Reading local Konnaxion source database settings.")
    source_url = _read_source_database_url(source_dir)

    client = _AgentHttpExecutionClient(
        base_url=_remote_agent_base_url(data),
        droplet_payload=data,
        progress_callback=progress_callback,
    )

    fd, local_name = tempfile.mkstemp(prefix="konnaxion-source-db-", suffix=".url")
    os.close(fd)
    local_secret = Path(local_name)
    local_secret.write_text(source_url, encoding="utf-8")
    try:
        os.chmod(local_secret, 0o600)
    except OSError:
        pass

    remote_secret = f"/tmp/konnaxion-source-db-{job_id}.url"
    project = docker_project_name(instance_id)
    q_remote_secret = shlex.quote(remote_secret)
    q_root = shlex.quote(remote_root)
    q_instance = shlex.quote(instance_id)
    q_project = shlex.quote(project)
    q_domain = shlex.quote(domain)

    progress_callback("source-copy", 15, "Copying source database credential to the Droplet securely.")
    copied = client._scp_file_to_path(data, local_secret, remote_secret, timeout_seconds=120)
    if not _result_ok(copied):
        raise RuntimeError(_command_failure_detail("Source DB credential copy failed", copied))

    remote_script = f'''set -Eeuo pipefail
ROOT={q_root}
INSTANCE={q_instance}
PROJECT={q_project}
DOMAIN={q_domain}
SOURCE_FILE={q_remote_secret}
COMPOSE="$ROOT/instances/$INSTANCE/state/docker-compose.runtime.yml"
PY="$ROOT/manager/.venv/bin/python"
TMP="$ROOT/tmp/production-data-bootstrap-{job_id}"
PGUSER=konnaxion
PGDB=konnaxion
mkdir -p "$TMP"
chmod 700 "$TMP"
chmod 600 "$SOURCE_FILE"
cleanup() {{ rm -rf "$TMP"; rm -f "$SOURCE_FILE"; }}
trap cleanup EXIT

dc() {{ docker compose -p "$PROJECT" -f "$COMPOSE" "$@"; }}

echo PHASE=source-check
dc up -d postgres redis >/dev/null
SOURCE_WORLDS="$(docker run --rm -v "$SOURCE_FILE:/run/source.url:ro" postgres:17 sh -ceu 'u=$(tr -d '\\r\\n' </run/source.url); psql "$u" -v ON_ERROR_STOP=1 -Atc "select count(*) from public.worlds_world;"')"
SOURCE_KX="$(docker run --rm -v "$SOURCE_FILE:/run/source.url:ro" postgres:17 sh -ceu 'u=$(tr -d '\\r\\n' </run/source.url); psql "$u" -v ON_ERROR_STOP=1 -Atc "select count(*) from pg_namespace where nspname ~ '\\''^kx_'\\'';"')"
echo SOURCE_WORLDS="$SOURCE_WORLDS"
echo SOURCE_KX_SCHEMAS="$SOURCE_KX"
if [ "$SOURCE_WORLDS" != "8" ]; then echo "Expected 8 source Worlds, got $SOURCE_WORLDS" >&2; exit 45; fi

PUBLIC_EXISTS="$(dc exec -T postgres psql -U "$PGUSER" -d "$PGDB" -Atc "select to_regclass('public.worlds_world') is not null;")"
SHADOW_EXISTS="$(dc exec -T postgres psql -U "$PGUSER" -d "$PGDB" -Atc "select to_regclass('ekoh_smartvote.worlds_world') is not null;")"
TARGET_WORLDS=0
if [ "$PUBLIC_EXISTS" = "t" ]; then TARGET_WORLDS=$((TARGET_WORLDS + $(dc exec -T postgres psql -U "$PGUSER" -d "$PGDB" -Atc 'select count(*) from public.worlds_world;'))); fi
if [ "$SHADOW_EXISTS" = "t" ]; then TARGET_WORLDS=$((TARGET_WORLDS + $(dc exec -T postgres psql -U "$PGUSER" -d "$PGDB" -Atc 'select count(*) from ekoh_smartvote.worlds_world;'))); fi
echo TARGET_EXISTING_WORLDS="$TARGET_WORLDS"
if [ "$TARGET_WORLDS" != "0" ]; then echo "Target already contains World data; refusing destructive bootstrap." >&2; exit 42; fi

echo PHASE=dump
docker run --rm -v "$SOURCE_FILE:/run/source.url:ro" postgres:17 sh -ceu 'u=$(tr -d '\\r\\n' </run/source.url); pg_dump "$u" --format=custom --no-owner --no-acl' > "$TMP/neon.dump"
test -s "$TMP/neon.dump"
docker run --rm -i postgres:17 pg_restore -l < "$TMP/neon.dump" >/dev/null

echo PHASE=stop
dc stop django-api celeryworker celerybeat frontend-next media-nginx traefik >/dev/null 2>&1 || true

echo PHASE=clean
dc exec -T postgres psql -U "$PGUSER" -d "$PGDB" -v ON_ERROR_STOP=1 <<'SQL'
DO $$
DECLARE r record;
BEGIN
    FOR r IN SELECT nspname FROM pg_namespace
             WHERE nspname NOT LIKE 'pg_%'
               AND nspname <> 'information_schema'
               AND nspname <> 'public'
    LOOP
        EXECUTE format('DROP SCHEMA %I CASCADE', r.nspname);
    END LOOP;
END $$;
DROP SCHEMA public CASCADE;
CREATE SCHEMA public AUTHORIZATION konnaxion;
SQL

echo PHASE=restore
CID="$(dc ps -q postgres)"
NETWORK="$(docker inspect "$CID" --format '{{{{range $n, $_ := .NetworkSettings.Networks}}}}{{{{$n}}}}{{{{"\\n"}}}}{{{{end}}}}' | head -n1)"
PGPASSWORD="$(KX_ROOT="$ROOT" "$PY" -c 'import sys; from kx_agent.instances import secrets as s; e=s.read_env_file(s.instance_env_dir(sys.argv[1]) / s.POSTGRES_ENV_FILE); print(e["POSTGRES_PASSWORD"])' "$INSTANCE")"
docker run --rm -i --network "$NETWORK" -e PGPASSWORD="$PGPASSWORD" postgres:17 \\
    pg_restore -h postgres -U "$PGUSER" -d "$PGDB" --no-owner --no-acl --exit-on-error --single-transaction < "$TMP/neon.dump"
unset PGPASSWORD

echo PHASE=verify
PROD_WORLDS="$(dc exec -T postgres psql -U "$PGUSER" -d "$PGDB" -Atc 'select count(*) from public.worlds_world;')"
PROD_KX="$(dc exec -T postgres psql -U "$PGUSER" -d "$PGDB" -Atc "select count(*) from pg_namespace where nspname ~ '^kx_';")"
echo PROD_WORLDS="$PROD_WORLDS"
echo PROD_KX_SCHEMAS="$PROD_KX"
test "$PROD_WORLDS" = "$SOURCE_WORLDS"
test "$PROD_KX" = "$SOURCE_KX"
dc run --rm django-api python manage.py migrate --check
dc run --rm django-api python manage.py check
dc run --rm django-api python manage.py shell -c "from django.contrib.sessions.models import Session; Session.objects.all().delete(); print('SESSIONS_CLEARED=OK')"
dc run --rm django-api python manage.py shell -c "from django.contrib.sites.models import Site; s=Site.objects.get_current(); s.domain='$DOMAIN'; s.name='Konnaxion'; s.save(); print('SITE_DOMAIN=OK')"
dc run --rm django-api python manage.py worlds_list
dc run --rm django-api python manage.py worlds_health

echo PRODUCTION_DATA_BOOTSTRAP=OK
'''

    try:
        progress_callback("remote-bootstrap", 25, "Dumping Neon and restoring production data on the Droplet.")
        result = client._ssh_privileged(
            data,
            remote_script,
            timeout_seconds=3600,
            success_message="Production data bootstrap completed on Droplet.",
        )
        if not _result_ok(result):
            raise RuntimeError(_command_failure_detail("Production data bootstrap failed", result))

        progress_callback("start", 88, "Starting the production instance through the Agent.")
        started = client._post(
            "/instances/start",
            {
                "instance_id": instance_id,
                "run_security_gate": True,
                "force_recreate_after_image_load": False,
            },
        )
        if not _result_ok(started) or str(started.get("state") or "") != "running":
            raise RuntimeError(f"Production instance start failed: {started}")

        progress_callback("public", 96, "Checking public Worlds API.")
        public_url = f"https://{domain}/api/control/worlds/"
        last_error = ""
        world_count = 0
        for attempt in range(1, 13):
            try:
                response = httpx.get(public_url, follow_redirects=True, timeout=10.0)
                response.raise_for_status()
                public_payload = response.json()
                if isinstance(public_payload, list):
                    world_count = len(public_payload)
                elif isinstance(public_payload, Mapping):
                    items = public_payload.get("results") or public_payload.get("worlds") or public_payload.get("items") or []
                    world_count = len(items) if isinstance(items, list) else 0
                if world_count > 0:
                    break
                last_error = "public Worlds API returned zero Worlds"
            except Exception as exc:  # noqa: BLE001
                last_error = str(exc)
            if attempt < 12:
                import time
                time.sleep(5)
        if world_count <= 0:
            raise RuntimeError(f"Public Worlds API did not become ready: {last_error}")

        progress_callback("complete", 100, "Production data initialized and Worlds are online.")
        return {
            "ok": True,
            "action": "initialize_production_data",
            "message": "Production data initialized from local Konnaxion source and Worlds are online.",
            "instance_id": instance_id,
            "public_url": public_url,
            "public_world_count": world_count,
            "remote": result,
            "start": started,
        }
    finally:
        try:
            client._ssh_privileged(
                data,
                f"rm -f {q_remote_secret}",
                timeout_seconds=30,
                success_message="Temporary source credential removed.",
            )
        except Exception:
            pass
        try:
            local_secret.unlink(missing_ok=True)
        except Exception:
            pass


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

        if action == "initialize_production_data":
            result_data = _run_initial_production_data(job_id, payload, progress_callback)
            success = bool(result_data.get("ok"))
        elif action == "deploy_droplet" and _coerce_bool(payload.get("one_click_release")):
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
        "initialize_production_data": "Initialize Production Data",
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
