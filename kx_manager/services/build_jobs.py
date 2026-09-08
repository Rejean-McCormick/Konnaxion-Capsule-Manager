"""Persistent asynchronous capsule build jobs for Capsule Manager.

Builds are intentionally serialized by default because Docker image builds can
consume substantial RAM.  Job state and operator logs live below the configured
Konnaxion runtime root so a browser refresh or Manager restart does not erase
build evidence.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from kx_manager.defaults import DEFAULT_RUNTIME_ROOT

ACTIVE_STATUSES = frozenset({"queued", "running"})
FINAL_STATUSES = frozenset({"succeeded", "failed", "interrupted"})
DEFAULT_BUILD_CONCURRENCY = 1
HEARTBEAT_SECONDS = 15

_executor_lock = threading.Lock()
_executor: ThreadPoolExecutor | None = None
_executor_workers: int | None = None
_job_write_lock = threading.RLock()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _runtime_root() -> Path:
    explicit = os.getenv("KX_ROOT", "").strip() or os.getenv("KX_RUNTIME_ROOT", "").strip()
    return Path(explicit or DEFAULT_RUNTIME_ROOT).expanduser()


def build_job_dir() -> Path:
    explicit = os.getenv("KX_CAPSULE_BUILD_JOB_DIR", "").strip()
    path = Path(explicit).expanduser() if explicit else _runtime_root() / "manager" / "build-jobs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def ensure_build_job_store() -> Path:
    """Create the persistent build-job directory and return it."""

    path = build_job_dir()
    # Make the location discoverable from PowerShell without guessing a UUID.
    marker = path / "README.txt"
    if not marker.exists():
        marker.write_text(
            "Konnaxion Capsule Manager build jobs.\n"
            "Each build creates <job-id>.json and <job-id>.log.\n"
            "latest-job.txt contains the most recently submitted job id.\n",
            encoding="utf-8",
        )
    return path


def _job_paths(job_id: str) -> tuple[Path, Path, Path]:
    root = ensure_build_job_store()
    return root / f"{job_id}.json", root / f"{job_id}.log", root / f"{job_id}.progress.json"


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(dict(payload), indent=2, sort_keys=True, default=str), encoding="utf-8")
    os.replace(tmp, path)


def _append_log_path(path: Path, message: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = _utc_now_iso()
    with path.open("a", encoding="utf-8", errors="replace") as handle:
        handle.write(f"[{timestamp}] {message.rstrip()}\n")


def append_job_log(job_id: str, message: str) -> None:
    _json_path, log_path, _progress_path = _job_paths(job_id)
    _append_log_path(log_path, message)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    return dict(value) if isinstance(value, Mapping) else None


def _write_job(job: Mapping[str, Any]) -> dict[str, Any]:
    data = dict(job)
    data["updated_at"] = _utc_now_iso()
    json_path, _log_path, _progress_path = _job_paths(str(data["job_id"]))
    with _job_write_lock:
        _atomic_write_json(json_path, data)
    return data


def _update_job(job_id: str, **changes: Any) -> dict[str, Any]:
    with _job_write_lock:
        current = _read_json(_job_paths(job_id)[0]) or {"job_id": job_id}
        current.update(changes)
        return _write_job(current)


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


def _build_request(payload: Mapping[str, Any], *, job_id: str) -> Any:
    from kx_manager.services import builder

    request_class = builder.BuildCapsuleRequest
    allowed = set(request_class.__dataclass_fields__)
    values = {key: value for key, value in dict(payload).items() if key in allowed}
    values["force"] = _coerce_bool(values.get("force"), default=False)

    _json_path, log_path, progress_path = _job_paths(job_id)
    env = dict(values.get("env") or {})
    env.update(
        {
            "KX_BUILD_JOB_ID": job_id,
            "KX_BUILD_JOB_LOG_FILE": str(log_path),
            "KX_BUILD_PROGRESS_FILE": str(progress_path),
        }
    )
    values["env"] = env
    return request_class(**values)


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


def _docker_preflight() -> tuple[bool, str]:
    try:
        completed = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            shell=False,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
        )
    except FileNotFoundError:
        return False, "Docker CLI was not found. Install/start Docker Desktop and retry."
    except subprocess.TimeoutExpired:
        return False, "Docker engine preflight timed out. Start Docker Desktop and retry."

    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "Docker engine unavailable.").strip()
        return False, f"Docker engine unavailable. Start Docker Desktop and retry. {detail}"
    version = (completed.stdout or "").strip()
    return True, f"Docker engine ready{f' (server {version})' if version else ''}."


def _heartbeat(job_id: str, stop_event: threading.Event) -> None:
    while not stop_event.wait(HEARTBEAT_SECONDS):
        current = get_build_job(job_id, include_log=False)
        if not current or current.get("status") not in ACTIVE_STATUSES:
            return
        phase = str(current.get("phase") or "building")
        append_job_log(job_id, f"heartbeat: build still running; phase={phase}")
        _update_job(job_id, heartbeat_at=_utc_now_iso())


def _run_job(job_id: str, action: str, payload: Mapping[str, Any]) -> None:
    from kx_manager.services import builder

    stop_event = threading.Event()
    heartbeat: threading.Thread | None = None

    try:
        _update_job(
            job_id,
            status="running",
            phase="preflight",
            progress=2,
            message="Checking Docker engine and build prerequisites.",
            started_at=_utc_now_iso(),
        )
        append_job_log(job_id, "preflight: checking Docker engine")
        ok, message = _docker_preflight()
        append_job_log(job_id, f"preflight: {message}")
        if not ok:
            _update_job(
                job_id,
                status="failed",
                phase="preflight",
                progress=2,
                message=message,
                error=message,
                finished_at=_utc_now_iso(),
            )
            return

        _update_job(
            job_id,
            phase="builder",
            progress=5,
            message="Capsule Builder started. Docker images may take several minutes.",
        )
        append_job_log(job_id, f"builder: starting {action}")

        heartbeat = threading.Thread(
            target=_heartbeat,
            args=(job_id, stop_event),
            name=f"kx-build-heartbeat-{job_id[:8]}",
            daemon=True,
        )
        heartbeat.start()

        request = _build_request(payload, job_id=job_id)
        function = builder.rebuild_capsule if action == "rebuild_capsule" else builder.build_capsule
        result = function(request)
        result_data = _serialize_result(result)
        success = bool(result_data.get("ok", getattr(result, "ok", False)))

        if success:
            append_job_log(job_id, "complete: capsule build succeeded")
            _update_job(
                job_id,
                status="succeeded",
                phase="complete",
                progress=100,
                message="Capsule build completed successfully.",
                result=result_data,
                error=None,
                finished_at=_utc_now_iso(),
            )
        else:
            command = result_data.get("command") if isinstance(result_data, Mapping) else None
            detail = "Capsule Builder returned a failed result."
            if isinstance(command, Mapping):
                detail = str(command.get("stderr") or command.get("message") or detail).strip()
            append_job_log(job_id, f"failed: {detail}")
            _update_job(
                job_id,
                status="failed",
                phase="failed",
                progress=max(5, int(get_build_job(job_id, include_log=False).get("progress", 5))),
                message="Capsule build failed.",
                result=result_data,
                error=detail,
                finished_at=_utc_now_iso(),
            )
    except Exception as exc:  # noqa: BLE001 - persisted operator error boundary
        append_job_log(job_id, f"failed: {type(exc).__name__}: {exc}")
        _update_job(
            job_id,
            status="failed",
            phase="failed",
            message=f"Capsule build failed: {exc}",
            error=f"{type(exc).__name__}: {exc}",
            finished_at=_utc_now_iso(),
        )
    finally:
        stop_event.set()
        if heartbeat is not None and heartbeat.is_alive():
            heartbeat.join(timeout=1)


def _configured_concurrency() -> int:
    raw = os.getenv("KX_CAPSULE_BUILD_CONCURRENCY", str(DEFAULT_BUILD_CONCURRENCY)).strip()
    try:
        return max(1, min(8, int(raw)))
    except ValueError:
        return DEFAULT_BUILD_CONCURRENCY


def _executor_for_config() -> ThreadPoolExecutor:
    global _executor, _executor_workers
    workers = _configured_concurrency()
    with _executor_lock:
        if _executor is None:
            _executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="kx-capsule-build")
            _executor_workers = workers
        return _executor


def create_build_job(action: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    """Persist and enqueue a capsule build/rebuild job."""

    if action not in {"build_capsule", "rebuild_capsule"}:
        raise ValueError(f"Unsupported capsule build action: {action}")

    root = ensure_build_job_store()
    job_id = str(uuid.uuid4())
    _json_path, log_path, _progress_path = _job_paths(job_id)
    created_at = _utc_now_iso()

    job = {
        "job_id": job_id,
        "action": action,
        "status": "queued",
        "phase": "queued",
        "progress": 1,
        "message": "Capsule build queued.",
        "created_at": created_at,
        "started_at": None,
        "updated_at": created_at,
        "finished_at": None,
        "heartbeat_at": None,
        "capsule_id": payload.get("capsule_id"),
        "capsule_version": payload.get("capsule_version") or payload.get("version"),
        "source_dir": payload.get("source_dir"),
        "capsule_output_dir": payload.get("capsule_output_dir") or payload.get("output_dir"),
        "network_profile": payload.get("network_profile") or payload.get("profile"),
        "log_file": str(log_path),
        "job_file": str(root / f"{job_id}.json"),
        "result": None,
        "error": None,
    }
    _write_job(job)
    append_job_log(job_id, f"queued: {action} for capsule_id={job.get('capsule_id') or '-'}")
    (root / "latest-job.txt").write_text(job_id + "\n", encoding="utf-8")

    _executor_for_config().submit(_run_job, job_id, action, dict(payload))
    return get_build_job(job_id) or job


def _merge_builder_progress(job: dict[str, Any], progress_path: Path) -> None:
    progress = _read_json(progress_path)
    if not progress:
        return
    if job.get("status") not in ACTIVE_STATUSES:
        return
    try:
        pct = int(progress.get("progress", job.get("progress", 0)))
    except (TypeError, ValueError):
        pct = int(job.get("progress", 0) or 0)
    job["progress"] = max(int(job.get("progress", 0) or 0), max(0, min(99, pct)))
    if progress.get("phase"):
        job["phase"] = progress["phase"]
    if progress.get("message"):
        job["message"] = progress["message"]
    if progress.get("updated_at"):
        job["builder_updated_at"] = progress["updated_at"]


def get_build_job(job_id: str, *, include_log: bool = True) -> dict[str, Any] | None:
    json_path, log_path, progress_path = _job_paths(job_id)
    job = _read_json(json_path)
    if job is None:
        return None
    _merge_builder_progress(job, progress_path)
    if include_log:
        try:
            text = log_path.read_text(encoding="utf-8", errors="replace")
        except FileNotFoundError:
            text = ""
        # Keep browser rendering bounded while the full .log remains on disk.
        lines = text.splitlines()
        job["log_tail"] = "\n".join(lines[-400:])
    return job


def list_build_jobs(limit: int = 20) -> list[dict[str, Any]]:
    root = ensure_build_job_store()
    jobs: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        if path.name.endswith(".progress.json"):
            continue
        item = _read_json(path)
        if item:
            jobs.append(item)
        if len(jobs) >= max(1, limit):
            break
    return jobs


__all__ = [
    "ACTIVE_STATUSES",
    "DEFAULT_BUILD_CONCURRENCY",
    "FINAL_STATUSES",
    "append_job_log",
    "build_job_dir",
    "create_build_job",
    "ensure_build_job_store",
    "get_build_job",
    "list_build_jobs",
]
