# kx_manager/ui/agent_execution_client.py

"""Execution client used by GUI deployment backends.

This module owns the synchronous HTTP/SSH/SCP adapter used by
kx_manager.services.deploy.

Droplet mode normally keeps the Agent private on the Droplet at
http://127.0.0.1:8765/v1 and reaches it through SSH-local curl. Direct
remote_agent_url mode is supported only when explicitly configured for a real
non-loopback Agent endpoint.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping
from urllib.parse import urlparse

import httpx


DEFAULT_AGENT_HTTP_TIMEOUT_SECONDS = 30
DEFAULT_AGENT_MUTATION_TIMEOUT_SECONDS = 300
DEFAULT_AGENT_IMPORT_TIMEOUT_SECONDS = 600
DEFAULT_AGENT_LONG_OPERATION_TIMEOUT_SECONDS = 900
DEFAULT_SSH_TIMEOUT_GRACE_SECONDS = 60
DEFAULT_SSH_AGENT_ATTEMPTS = 3


@dataclass(slots=True)
class _AgentHttpExecutionClient:
    """Synchronous execution adapter used by deploy.py service workflows."""

    base_url: str
    droplet_payload: Mapping[str, Any] | None = None
    timeout_seconds: float = 30.0
    progress_callback: Callable[[str, int, str, Mapping[str, Any] | None], None] | None = None

    # ------------------------------------------------------------------
    # HTTP / Agent request helpers
    # ------------------------------------------------------------------

    def _report_progress(
        self,
        phase: str,
        progress: int,
        message: str,
        data: Mapping[str, Any] | None = None,
    ) -> None:
        callback = self.progress_callback
        if not callable(callback):
            return
        try:
            callback(phase, max(0, min(100, int(progress))), message, data)
        except Exception:
            # Progress reporting must never break deployment execution.
            return

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "konnaxion-capsule-manager-gui",
        }

        token = os.getenv("KX_AGENT_TOKEN", "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"

        return headers

    def _url(self, path: str) -> str:
        path = "/" + str(path).strip().lstrip("/")
        return self.base_url.rstrip("/") + path

    def _use_ssh_agent_transport(self) -> bool:
        payload = self.droplet_payload or {}

        if not payload.get("droplet_host"):
            return False

        return _explicit_remote_agent_url(payload) == ""

    def _get(self, path: str) -> dict[str, Any]:
        if self._use_ssh_agent_transport():
            return self._ssh_agent_request("GET", path)

        timeout_seconds = _agent_request_timeout_seconds(
            "GET",
            path,
            base_timeout_seconds=self.timeout_seconds,
        )

        try:
            with httpx.Client(
                timeout=timeout_seconds,
                headers=self._headers(),
            ) as client:
                response = client.get(self._url(path))
        except Exception as exc:
            return {
                "ok": False,
                "message": str(exc),
                "error": type(exc).__name__,
                "timeout_seconds": timeout_seconds,
            }

        return _response_payload(response)

    def _post(self, path: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        if self._use_ssh_agent_transport():
            return self._ssh_agent_request("POST", path, payload=payload)

        timeout_seconds = _agent_request_timeout_seconds(
            "POST",
            path,
            base_timeout_seconds=self.timeout_seconds,
        )

        try:
            with httpx.Client(
                timeout=timeout_seconds,
                headers=self._headers(),
            ) as client:
                response = client.post(
                    self._url(path),
                    json=_strip_empty(payload),
                )
        except Exception as exc:
            return {
                "ok": False,
                "message": str(exc),
                "error": type(exc).__name__,
                "timeout_seconds": timeout_seconds,
            }

        return _response_payload(response)

    def _ssh_agent_request(
        self,
        method: str,
        path: str,
        *,
        payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Call the private Droplet Agent over SSH-local curl.

        The GUI deploy flow can open several SSH connections in quick succession
        for /health, /agent/info, /capsules/import, /instances/create, etc.
        Some networks/Droplets intermittently timeout one SSH connection even
        though the next connection succeeds.

        Retry only transport-level SSH failures. Do not retry application/schema
        failures returned by the Agent.

        Long-running Agent calls such as /instances/start can legitimately take
        several minutes because the Agent may load capsule OCI image archives
        and recreate Docker Compose services. Those calls must not use the short
        30-second health/probe timeout.
        """

        droplet_payload = self.droplet_payload or {}
        normalized_method = method.upper().strip()
        normalized_path = "/" + str(path).strip().lstrip("/")
        url = f"http://127.0.0.1:8765/v1{normalized_path}"

        curl_timeout_seconds = _agent_request_timeout_seconds(
            normalized_method,
            normalized_path,
            base_timeout_seconds=self.timeout_seconds,
        )
        ssh_timeout_seconds = _ssh_command_timeout_seconds(curl_timeout_seconds)

        if normalized_method == "GET":
            remote_command = (
                f"curl --fail-with-body --max-time {curl_timeout_seconds} -sS "
                f"-X GET {shlex.quote(url)}"
            )
        else:
            body = json.dumps(_strip_empty(payload or {}), separators=(",", ":"))
            remote_root = str(droplet_payload.get("remote_kx_root") or "/opt/konnaxion").rstrip("/")
            token_path = f"{remote_root}/manager/agent.token"

            # The production Droplet Agent requires a local bearer token for
            # privileged/write operations. Read it on the Droplet; never copy
            # the token back to Windows and never place the token value in the
            # SSH command line. curl reads the Authorization header from a
            # short-lived root-only header file.
            remote_command = (
                "set -e; umask 077; "
                f"TOKEN_FILE={shlex.quote(token_path)}; "
                "if [ ! -s \"$TOKEN_FILE\" ]; then "
                "echo 'Konnaxion Agent token is missing on Droplet' >&2; exit 67; fi; "
                "AUTH_FILE=$(mktemp /tmp/konnaxion-agent-auth.XXXXXX); "
                "trap 'rm -f \"$AUTH_FILE\"' EXIT; "
                "printf 'Authorization: Bearer %s\n' \"$(cat \"$TOKEN_FILE\")\" > \"$AUTH_FILE\"; "
                f"curl --fail-with-body --max-time {curl_timeout_seconds} -sS "
                "-H 'Content-Type: application/json' "
                "-H \"@$AUTH_FILE\" "
                f"-X {shlex.quote(normalized_method)} "
                f"--data-raw {shlex.quote(body)} "
                f"{shlex.quote(url)}"
            )

        last_result: dict[str, Any] = {}
        max_attempts = _read_int_env(
            "KX_MANAGER_SSH_AGENT_ATTEMPTS",
            DEFAULT_SSH_AGENT_ATTEMPTS,
        )

        for attempt in range(1, max_attempts + 1):
            ssh_runner = self._ssh if normalized_method == "GET" else self._ssh_privileged
            result = ssh_runner(
                droplet_payload,
                remote_command,
                timeout_seconds=ssh_timeout_seconds,
                success_message="Remote Agent request completed over SSH.",
            )

            result["ssh_attempts"] = attempt
            result["ssh_retries"] = attempt - 1
            result["curl_timeout_seconds"] = curl_timeout_seconds
            result["ssh_timeout_seconds"] = ssh_timeout_seconds
            last_result = result

            if result.get("ok"):
                break

            if not _ssh_result_is_retryable(result):
                break

            if attempt < max_attempts:
                time.sleep(2 * attempt)

        data = _agent_response_from_ssh_result(
            last_result,
            method=normalized_method,
            path=normalized_path,
            agent_health_url="http://127.0.0.1:8765/v1/health",
        )

        data["ssh_attempts"] = last_result.get("ssh_attempts", 1)
        data["ssh_retries"] = last_result.get("ssh_retries", 0)
        data["curl_timeout_seconds"] = curl_timeout_seconds
        data["ssh_timeout_seconds"] = ssh_timeout_seconds

        return data

    # ------------------------------------------------------------------
    # Remote Agent health
    # ------------------------------------------------------------------

    def health(self, **payload: Any) -> dict[str, Any]:
        del payload
        return self._get("/health")

    def agent_health(self, **payload: Any) -> dict[str, Any]:
        return self.check_droplet_agent(**payload)

    def check_remote_agent(self, **payload: Any) -> dict[str, Any]:
        return self.check_droplet_agent(**payload)

    def check_droplet_agent(self, **payload: Any) -> dict[str, Any]:
        """Check Droplet Agent health.

        If a valid non-loopback remote_agent_url is explicitly provided, use
        direct HTTP. If it is blank, stale, local-loopback, or mismatched with
        droplet_host, use SSH-local curl against 127.0.0.1:8765 inside the
        Droplet.
        """

        merged_payload = {**dict(self.droplet_payload or {}), **dict(payload)}
        explicit_remote_agent_url = _explicit_remote_agent_url(merged_payload)

        if explicit_remote_agent_url:
            agent_health_url = explicit_remote_agent_url
            if not agent_health_url.endswith("/health"):
                agent_health_url = agent_health_url.rstrip("/") + "/health"

            try:
                with httpx.Client(
                    timeout=self.timeout_seconds,
                    headers=self._headers(),
                ) as client:
                    response = client.get(agent_health_url)
            except Exception as exc:
                return {
                    "ok": False,
                    "message": str(exc),
                    "error": type(exc).__name__,
                    "agent_health_url": agent_health_url,
                    "transport": "http",
                }

            data = _response_payload(response)
            data.setdefault("agent_health_url", agent_health_url)
            data.setdefault("transport", "http")
            return data

        if merged_payload.get("droplet_host"):
            return self._ssh_local_agent_health(merged_payload)

        return self.health()

    def _ssh_local_agent_health(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        result = self._ssh(
            payload,
            "curl --fail-with-body --max-time 10 -sS http://127.0.0.1:8765/v1/health",
            timeout_seconds=30,
            success_message="Droplet Agent is reachable over SSH-local health check.",
        )

        data = _agent_response_from_ssh_result(
            result,
            method="GET",
            path="/health",
            agent_health_url="http://127.0.0.1:8765/v1/health",
        )
        data["transport"] = "ssh"
        data["curl_timeout_seconds"] = 10
        data["ssh_timeout_seconds"] = 30

        return data

    # ------------------------------------------------------------------
    # SSH/SCP remote file operations
    # ------------------------------------------------------------------

    def ensure_remote_runtime(self, **payload: Any) -> dict[str, Any]:
        return self._ensure_remote_runtime(payload)

    def ensure_droplet_runtime(self, **payload: Any) -> dict[str, Any]:
        return self._ensure_remote_runtime(payload)

    def deploy_prepare_remote(self, **payload: Any) -> dict[str, Any]:
        return self._ensure_remote_runtime(payload)

    def _ensure_remote_runtime(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        remote_kx_root = _require_payload_text(payload, "remote_kx_root")
        remote_capsule_dir = _require_payload_text(payload, "remote_capsule_dir")

        dirs = [
            remote_kx_root,
            str(PurePosixPath(remote_kx_root) / "capsules"),
            str(PurePosixPath(remote_kx_root) / "instances"),
            str(PurePosixPath(remote_kx_root) / "backups"),
            str(PurePosixPath(remote_kx_root) / "shared"),
            str(PurePosixPath(remote_kx_root) / "releases"),
            str(PurePosixPath(remote_kx_root) / "manager"),
            str(PurePosixPath(remote_kx_root) / "agent"),
            remote_capsule_dir,
        ]

        remote_command = "mkdir -p " + " ".join(shlex.quote(item) for item in dirs)

        # This step is idempotent (mkdir -p), so it is safe to retry when a
        # Windows/OpenSSH connection occasionally stalls before the remote
        # command starts. Keep each attempt short so the GUI does not sit at
        # 50% for two minutes with no useful feedback.
        last_result: dict[str, Any] | None = None
        for attempt in range(1, 4):
            if attempt > 1:
                self._report_progress(
                    "runtime",
                    50 + attempt,
                    f"SSH runtime preparation retry {attempt}/3.",
                    {"ssh_attempt": attempt, "ssh_attempts": 3},
                )

            result = self._ssh_privileged(
                payload,
                remote_command,
                timeout_seconds=30,
                success_message="Remote runtime directories exist.",
            )
            last_result = result

            if result.get("ok"):
                if attempt > 1:
                    result["ssh_attempts_used"] = attempt
                return result

            # Retry only timeouts. Authentication, host-key, command, and
            # permission failures should be surfaced immediately.
            if int(result.get("returncode") or 0) != 124:
                return result

            if attempt < 3:
                time.sleep(2)

        assert last_result is not None
        last_result["message"] = (
            "SSH timed out while preparing the remote runtime after 3 attempts."
        )
        last_result["ssh_attempts_used"] = 3
        return last_result

    def copy_capsule_to_droplet(self, **payload: Any) -> dict[str, Any]:
        return self._copy_capsule(payload)

    def copy_capsule_to_remote(self, **payload: Any) -> dict[str, Any]:
        return self._copy_capsule(payload)

    def deploy_copy_capsule(self, **payload: Any) -> dict[str, Any]:
        return self._copy_capsule(payload)

    def _copy_capsule(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        capsule_file = Path(_require_payload_text(payload, "capsule_file"))
        if not capsule_file.exists() or not capsule_file.is_file():
            return {
                "ok": False,
                "message": f"Capsule file does not exist: {capsule_file}",
                "capsule_file": str(capsule_file),
            }

        remote_capsule_dir = _require_payload_text(payload, "remote_capsule_dir")
        remote_capsule_path = str(
            payload.get("remote_capsule_path")
            or PurePosixPath(remote_capsule_dir) / capsule_file.name
        )
        user = _require_payload_text(payload, "droplet_user")
        non_root = user != "root"

        # Hardened production SSH uses kx-admin. Upload as that unprivileged
        # account into /tmp, then atomically install the signed capsule into
        # /opt/konnaxion with sudo -n. Root targets preserve the historical
        # direct-copy path for backwards compatibility.
        upload_path = (
            f"/tmp/konnaxion-upload-{capsule_file.name}"
            if non_root
            else remote_capsule_path
        )

        mkdir_command = "mkdir -p " + shlex.quote(remote_capsule_dir)
        mkdir_result = self._ssh_privileged(
            payload,
            mkdir_command,
            timeout_seconds=120,
            success_message="Remote capsule directory exists.",
        )
        if not mkdir_result.get("ok"):
            return mkdir_result

        self._report_progress(
            "upload",
            10,
            "Starting capsule upload to Droplet.",
            {"capsule_file": str(capsule_file), "remote_capsule_path": remote_capsule_path},
        )

        if callable(self.progress_callback):
            result = self._stream_file_over_ssh(
                payload,
                capsule_file,
                upload_path,
                timeout_seconds=1800,
            )
        else:
            argv = self._scp_argv(payload, capsule_file, upload_path)
            result = _run_argv(argv, timeout_seconds=1800)

        result.update(
            {
                "capsule_file": str(capsule_file),
                "remote_capsule_path": remote_capsule_path,
                "remote_capsule_dir": remote_capsule_dir,
            }
        )

        if result.get("ok") and non_root:
            install_command = f"""set -e
if ! id kx-agent >/dev/null 2>&1 || ! getent group kx-agent >/dev/null 2>&1; then
  echo 'kx-agent service identity is missing' >&2
  exit 67
fi
install -d -m 0750 -o kx-agent -g kx-agent {shlex.quote(remote_capsule_dir)}
install -m 0640 -o root -g kx-agent {shlex.quote(upload_path)} {shlex.quote(remote_capsule_path)}
rm -f {shlex.quote(upload_path)}
runuser -u kx-agent -- test -r {shlex.quote(remote_capsule_path)}
"""
            installed = self._ssh_privileged(
                payload,
                install_command,
                timeout_seconds=60,
                success_message="Uploaded capsule installed into protected runtime directory.",
            )
            if not installed.get("ok"):
                installed.update(
                    {
                        "capsule_file": str(capsule_file),
                        "remote_capsule_path": remote_capsule_path,
                        "remote_capsule_dir": remote_capsule_dir,
                        "upload_completed": True,
                    }
                )
                return installed

        if result.get("ok"):
            access_result = self._prepare_remote_capsule_access(payload, remote_capsule_path)
            if not access_result.get("ok"):
                access_result.update(
                    {
                        "capsule_file": str(capsule_file),
                        "remote_capsule_path": remote_capsule_path,
                        "remote_capsule_dir": remote_capsule_dir,
                        "upload_completed": True,
                    }
                )
                return access_result
            result["remote_capsule_access_prepared"] = True
            result["message"] = "Capsule copied to Droplet."

        return result

    def _prepare_remote_capsule_access(
        self,
        payload: Mapping[str, Any],
        remote_capsule_path: str,
    ) -> dict[str, Any]:
        """Make a staged capsule readable by the hardened ``kx-agent`` service.

        Older root-based Manager releases could leave copied ``.kxcap`` files
        as ``0600 root:root``.  v14 intentionally moved the long-running Agent
        to a non-root identity, so those legacy transfer files must be migrated
        before the Agent can import them.  Only a direct child of the configured
        remote capsule directory is accepted here; arbitrary remote paths are
        never chmod/chowned.
        """

        merged_payload = {**dict(self.droplet_payload or {}), **dict(payload)}
        remote_capsule_dir = str(
            merged_payload.get("remote_capsule_dir") or "/opt/konnaxion/capsules"
        ).strip()
        capsule_path = PurePosixPath(str(remote_capsule_path).strip())
        capsule_dir = PurePosixPath(remote_capsule_dir)

        if (
            not capsule_path.is_absolute()
            or not capsule_dir.is_absolute()
            or ".." in capsule_path.parts
            or ".." in capsule_dir.parts
            or capsule_path.parent != capsule_dir
            or capsule_path.suffix != ".kxcap"
        ):
            return {
                "ok": False,
                "message": "Refusing to change permissions outside the configured remote capsule directory.",
                "remote_capsule_path": str(capsule_path),
                "remote_capsule_dir": str(capsule_dir),
            }

        quoted_path = shlex.quote(str(capsule_path))
        quoted_dir = shlex.quote(str(capsule_dir))
        remote_command = f"""set -e
CAPSULE={quoted_path}
CAPSULE_DIR={quoted_dir}
test -d "$CAPSULE_DIR"
test -f "$CAPSULE"
if id kx-agent >/dev/null 2>&1 && getent group kx-agent >/dev/null 2>&1; then
  chown kx-agent:kx-agent "$CAPSULE_DIR"
  chmod 0750 "$CAPSULE_DIR"
  chown root:kx-agent "$CAPSULE"
  chmod 0640 "$CAPSULE"
  runuser -u kx-agent -- test -r "$CAPSULE"
else
  chown root:root "$CAPSULE"
  chmod 0640 "$CAPSULE"
  test -r "$CAPSULE"
fi
"""

        result = self._ssh_privileged(
            merged_payload,
            remote_command,
            timeout_seconds=30,
            success_message="Remote capsule permissions prepared for Agent import.",
        )
        result.setdefault("remote_capsule_path", str(capsule_path))
        result.setdefault("remote_capsule_dir", str(capsule_dir))
        return result

    def _prepare_remote_instance_runtime_access(
        self,
        payload: Mapping[str, Any],
        instance_id: str,
    ) -> dict[str, Any]:
        """Repair only Agent-owned runtime bind directories for hardened Agent.

        Legacy root-based deployments can leave ``logs`` and ``media`` owned
        by root. The v14+ Agent runs as ``kx-agent`` and therefore cannot chmod
        those directory inodes during a re-render. The repair is intentionally
        narrow and never changes PostgreSQL or Redis data ownership.
        """

        merged_payload = {**dict(self.droplet_payload or {}), **dict(payload)}
        remote_kx_root = str(
            merged_payload.get("remote_kx_root") or "/opt/konnaxion"
        ).strip().rstrip("/")
        safe_instance_id = str(instance_id or "").strip()

        if (
            not remote_kx_root.startswith("/")
            or ".." in PurePosixPath(remote_kx_root).parts
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", safe_instance_id)
        ):
            return {
                "ok": False,
                "message": "Refusing unsafe remote instance runtime permission repair.",
                "instance_id": safe_instance_id,
                "remote_kx_root": remote_kx_root,
            }

        instance_root = PurePosixPath(remote_kx_root) / "instances" / safe_instance_id
        quoted_instance = shlex.quote(str(instance_root))
        remote_command = f"""set -e
INSTANCE={quoted_instance}
if ! id kx-agent >/dev/null 2>&1 || ! getent group kx-agent >/dev/null 2>&1; then
  echo "kx-agent service identity is missing" >&2
  exit 67
fi
if [ -d "$INSTANCE" ]; then
  chown kx-agent:kx-agent "$INSTANCE"
  for dir in logs media; do
    if [ -d "$INSTANCE/$dir" ]; then
      chown kx-agent:kx-agent "$INSTANCE/$dir"
      chmod 0750 "$INSTANCE/$dir"
    fi
  done
  if [ -d "$INSTANCE/logs" ]; then
    find "$INSTANCE/logs" -mindepth 1 -maxdepth 1 -type d \
      -exec chown kx-agent:kx-agent {{}} + \
      -exec chmod 0750 {{}} +
    runuser -u kx-agent -- test -r "$INSTANCE/logs"
    runuser -u kx-agent -- test -w "$INSTANCE/logs"
    runuser -u kx-agent -- test -x "$INSTANCE/logs"
  fi
  if [ -d "$INSTANCE/media" ]; then
    runuser -u kx-agent -- test -r "$INSTANCE/media"
    runuser -u kx-agent -- test -w "$INSTANCE/media"
    runuser -u kx-agent -- test -x "$INSTANCE/media"
  fi
fi
"""

        result = self._ssh_privileged(
            merged_payload,
            remote_command,
            timeout_seconds=30,
            success_message="Remote instance runtime permissions prepared for hardened Agent.",
        )
        result.setdefault("instance_id", safe_instance_id)
        result.setdefault("instance_root", str(instance_root))
        return result

    def _stream_file_over_ssh(
        self,
        payload: Mapping[str, Any],
        local_file: Path,
        remote_path: str,
        *,
        timeout_seconds: int,
    ) -> dict[str, Any]:
        """Stream a file through SSH while emitting byte-level progress.

        OpenSSH's normal scp progress meter is disabled when stderr is captured by
        the Manager. Streaming to a remote ``cat`` gives the GUI deterministic
        progress without requiring extra software on the VPS. The final file is
        atomically moved into place only after the stream completes.
        """

        total_bytes = int(local_file.stat().st_size)
        temp_path = remote_path + ".partial"
        remote_command = (
            "set -e; rm -f "
            + shlex.quote(temp_path)
            + "; cat > "
            + shlex.quote(temp_path)
            + "; mv -f "
            + shlex.quote(temp_path)
            + " "
            + shlex.quote(remote_path)
        )
        argv = self._ssh_argv(payload) + [remote_command]

        started = time.monotonic()
        sent = 0
        last_reported_pct = -1

        try:
            process = subprocess.Popen(
                argv,
                shell=False,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            return {
                "ok": False,
                "message": "Required executable not found: ssh",
                "argv": _safe_argv(argv),
                "stdout": "",
                "stderr": str(exc),
                "returncode": 127,
            }

        try:
            if process.stdin is None:
                raise RuntimeError("SSH stdin pipe was not created.")

            with local_file.open("rb") as source:
                while True:
                    if time.monotonic() - started > timeout_seconds:
                        raise TimeoutError("capsule upload timed out")

                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break

                    process.stdin.write(chunk)
                    sent += len(chunk)

                    raw_pct = int((sent * 100) / total_bytes) if total_bytes else 100
                    pct = 10 + int(raw_pct * 0.85)
                    if pct != last_reported_pct:
                        elapsed = max(0.001, time.monotonic() - started)
                        self._report_progress(
                            "upload",
                            min(95, pct),
                            f"Uploading capsule: {raw_pct}% ({sent:,}/{total_bytes:,} bytes).",
                            {
                                "bytes_sent": sent,
                                "bytes_total": total_bytes,
                                "transfer_percent": raw_pct,
                                "bytes_per_second": int(sent / elapsed),
                                "remote_capsule_path": remote_path,
                            },
                        )
                        last_reported_pct = pct

            process.stdin.close()
            process.stdin = None

            remaining = max(1.0, timeout_seconds - (time.monotonic() - started))
            try:
                stdout_b, stderr_b = process.communicate(timeout=remaining)
            except subprocess.TimeoutExpired:
                process.kill()
                stdout_b, stderr_b = process.communicate()
                return {
                    "ok": False,
                    "message": "Command timed out: ssh",
                    "argv": _safe_argv(argv),
                    "stdout": stdout_b.decode("utf-8", errors="replace"),
                    "stderr": stderr_b.decode("utf-8", errors="replace") or "command timed out",
                    "returncode": 124,
                    "bytes_sent": sent,
                    "bytes_total": total_bytes,
                }

            stdout = stdout_b.decode("utf-8", errors="replace")
            stderr = stderr_b.decode("utf-8", errors="replace")
            ok = process.returncode == 0

            if ok:
                self._report_progress(
                    "upload",
                    96,
                    "Capsule upload completed; finalizing remote file.",
                    {
                        "bytes_sent": sent,
                        "bytes_total": total_bytes,
                        "transfer_percent": 100,
                        "remote_capsule_path": remote_path,
                    },
                )

            return {
                "ok": ok,
                "message": "Command completed." if ok else "Command failed.",
                "argv": _safe_argv(argv),
                "stdout": stdout,
                "stderr": stderr,
                "returncode": process.returncode,
                "bytes_sent": sent,
                "bytes_total": total_bytes,
                "transfer_percent": 100 if ok else int((sent * 100) / total_bytes) if total_bytes else 0,
            }

        except (BrokenPipeError, OSError, RuntimeError, TimeoutError) as exc:
            try:
                if process.stdin is not None:
                    process.stdin.close()
            except Exception:
                pass
            try:
                process.kill()
            except Exception:
                pass
            try:
                stdout_b, stderr_b = process.communicate(timeout=5)
            except Exception:
                stdout_b, stderr_b = b"", b""
            stderr = stderr_b.decode("utf-8", errors="replace")
            if not stderr:
                stderr = str(exc)
            return {
                "ok": False,
                "message": str(exc),
                "argv": _safe_argv(argv),
                "stdout": stdout_b.decode("utf-8", errors="replace"),
                "stderr": stderr,
                "returncode": 124 if isinstance(exc, TimeoutError) else (process.returncode or 1),
                "bytes_sent": sent,
                "bytes_total": total_bytes,
                "transfer_percent": int((sent * 100) / total_bytes) if total_bytes else 0,
            }

    def _ssh(
        self,
        payload: Mapping[str, Any],
        remote_command: str,
        *,
        timeout_seconds: int,
        success_message: str,
    ) -> dict[str, Any]:
        argv = self._ssh_argv(payload) + [remote_command]
        result = _run_argv(argv, timeout_seconds=timeout_seconds)

        if result.get("ok"):
            result["message"] = success_message

        return result

    def _ssh_privileged(
        self,
        payload: Mapping[str, Any],
        remote_command: str,
        *,
        timeout_seconds: int,
        success_message: str,
    ) -> dict[str, Any]:
        """Run a remote command as root without requiring root SSH login.

        Production Netcup access is intentionally performed as ``kx-admin``.
        Privileged maintenance uses passwordless ``sudo -n`` so GO LIVE fails
        closed instead of prompting for a password or weakening SSH policy.
        """

        user = _require_payload_text(payload, "droplet_user")
        if user == "root":
            return self._ssh(
                payload,
                remote_command,
                timeout_seconds=timeout_seconds,
                success_message=success_message,
            )

        wrapped = "sudo -n bash -lc " + shlex.quote(remote_command)
        result = self._ssh(
            payload,
            wrapped,
            timeout_seconds=timeout_seconds,
            success_message=success_message,
        )
        if not result.get("ok"):
            stderr = str(result.get("stderr") or "")
            if "password" in stderr.lower() or "sudo" in stderr.lower():
                result.setdefault(
                    "hint",
                    f"SSH user {user} must have non-interactive sudo (sudo -n).",
                )
        return result

    def _ssh_argv(self, payload: Mapping[str, Any]) -> list[str]:
        host = _require_payload_text(payload, "droplet_host")
        user = _require_payload_text(payload, "droplet_user")
        ssh_key_path = _require_payload_text(payload, "ssh_key_path")
        ssh_port = int(str(payload.get("ssh_port") or 22))

        return [
            "ssh",
            "-T",
            "-i",
            ssh_key_path,
            "-p",
            str(ssh_port),
            "-o",
            "BatchMode=yes",
            "-o",
            "PreferredAuthentications=publickey",
            "-o",
            "PasswordAuthentication=no",
            "-o",
            "KbdInteractiveAuthentication=no",
            "-o",
            "ConnectTimeout=10",
            "-o",
            "ConnectionAttempts=1",
            "-o",
            "ServerAliveInterval=10",
            "-o",
            "ServerAliveCountMax=3",
            "-o",
            "StrictHostKeyChecking=accept-new",
            f"{user}@{host}",
        ]

    def _scp_argv(
        self,
        payload: Mapping[str, Any],
        local_file: Path,
        remote_capsule_path: str,
    ) -> list[str]:
        host = _require_payload_text(payload, "droplet_host")
        user = _require_payload_text(payload, "droplet_user")
        ssh_key_path = _require_payload_text(payload, "ssh_key_path")
        ssh_port = int(str(payload.get("ssh_port") or 22))

        return [
            "scp",
            "-i",
            ssh_key_path,
            "-P",
            str(ssh_port),
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=10",
            "-o",
            "ConnectionAttempts=1",
            "-o",
            "ServerAliveInterval=10",
            "-o",
            "ServerAliveCountMax=3",
            "-o",
            "StrictHostKeyChecking=accept-new",
            str(local_file),
            f"{user}@{host}:{remote_capsule_path}",
        ]

    def _scp_file_to_path(
        self,
        payload: Mapping[str, Any],
        local_file: Path,
        remote_path: str,
        *,
        timeout_seconds: int,
    ) -> dict[str, Any]:
        host = _require_payload_text(payload, "droplet_host")
        user = _require_payload_text(payload, "droplet_user")
        ssh_key_path = _require_payload_text(payload, "ssh_key_path")
        ssh_port = int(str(payload.get("ssh_port") or 22))

        argv = [
            "scp",
            "-i",
            ssh_key_path,
            "-P",
            str(ssh_port),
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=10",
            "-o",
            "ConnectionAttempts=1",
            "-o",
            "ServerAliveInterval=10",
            "-o",
            "ServerAliveCountMax=3",
            "-o",
            "StrictHostKeyChecking=accept-new",
            str(local_file),
            f"{user}@{host}:{remote_path}",
        ]

        result = _run_argv(argv, timeout_seconds=timeout_seconds)
        result["local_file"] = str(local_file)
        result["remote_path"] = remote_path

        if result.get("ok"):
            result["message"] = "File copied to Droplet."

        return result

    # ------------------------------------------------------------------
    # Remote Agent deployment steps
    # ------------------------------------------------------------------

    def import_capsule(self, **payload: Any) -> dict[str, Any]:
        """Import a capsule through the Agent API."""

        merged_payload = {**dict(self.droplet_payload or {}), **dict(payload)}

        capsule_path = str(
            merged_payload.get("remote_capsule_path")
            or merged_payload.get("capsule_path")
            or merged_payload.get("capsule_file")
            or ""
        ).strip()

        if not capsule_path:
            return {
                "ok": False,
                "message": "capsule_path is required.",
                "path": "/capsules/import",
            }

        # In normal Droplet mode the Manager still has the privileged SSH
        # bootstrap channel. Normalize legacy root-only capsule permissions
        # immediately before import so a v14+ non-root Agent can read the
        # already-copied file even when the Copy Capsule step was skipped.
        if self._use_ssh_agent_transport():
            access_result = self._prepare_remote_capsule_access(
                merged_payload,
                capsule_path,
            )
            if not access_result.get("ok"):
                access_result.setdefault("path", "/capsules/import")
                return access_result

        agent_payload: dict[str, Any] = {
            "capsule_path": capsule_path,
            "instance_id": merged_payload.get("instance_id"),
            "network_profile": merged_payload.get("network_profile") or "public_vps",
            "exposure_mode": merged_payload.get("exposure_mode") or "public",
            "verify": _bool_payload(merged_payload.get("verify"), default=True),
            "overwrite": _bool_payload(merged_payload.get("overwrite"), default=True),
            "capsule_id": merged_payload.get("capsule_id"),
        }

        result = self._post("/capsules/import", agent_payload)

        if _agent_rejected_new_import_fields(result):
            result["ok"] = False
            result["message"] = (
                "Remote Droplet Agent is running an older /v1/capsules/import schema. "
                "Run Bootstrap Droplet Agent, then run Deploy Droplet again."
            )
            result["required_action"] = "bootstrap_droplet_agent"
            result["stale_remote_agent_schema"] = True
            result["sent_fields"] = sorted(agent_payload)
            return result

        return result

    def capsules_import(self, **payload: Any) -> dict[str, Any]:
        return self.import_capsule(**payload)

    def import_capsule_file(self, **payload: Any) -> dict[str, Any]:
        return self.import_capsule(**payload)

    def create_instance(self, **payload: Any) -> dict[str, Any]:
        """Create an instance through the Agent API.

        Durable custom-domain fix:

        The Agent must receive the canonical public runtime host during
        instance creation, not only during /network/set-profile. Otherwise
        a fresh public_vps instance can render env files and Traefik rules
        from the fallback sslip.io/IP host and never bind the custom domain.

        The host sent here is the operator-facing public domain chosen by
        _public_host_from_payload(), which intentionally prefers:
        domain / droplet_domain / public_host
        before droplet_host / target_host.
        """

        merged_payload = {**dict(self.droplet_payload or {}), **dict(payload)}

        capsule_id = str(
            merged_payload.get("capsule_id")
            or Path(str(merged_payload.get("capsule_file") or "")).stem
            or Path(str(merged_payload.get("capsule_path") or "")).stem
            or Path(str(merged_payload.get("remote_capsule_path") or "")).stem
            or ""
        ).strip()

        if not capsule_id:
            return {
                "ok": False,
                "message": "capsule_id is required to create an instance.",
                "path": "/instances/create",
                "payload_keys": sorted(str(key) for key in merged_payload),
            }

        instance_id = str(merged_payload.get("instance_id") or "").strip()
        if self._use_ssh_agent_transport() and instance_id:
            access_result = self._prepare_remote_instance_runtime_access(
                merged_payload,
                instance_id,
            )
            if not access_result.get("ok"):
                access_result.setdefault("path", "/instances/create")
                return access_result

        agent_payload: dict[str, Any] = {
            "instance_id": merged_payload.get("instance_id"),
            "capsule_id": capsule_id,
            "network_profile": merged_payload.get("network_profile") or "public_vps",
            "exposure_mode": merged_payload.get("exposure_mode") or "public",
            "generate_secrets": True,
        }

        public_host = _public_host_from_payload(merged_payload)
        if public_host:
            agent_payload["host"] = public_host
            lower_host = public_host.lower()
            if (
                "." in public_host
                and not lower_host.endswith(".sslip.io")
                and not all(part.isdigit() for part in public_host.split(".") if part)
            ):
                agent_payload["host_aliases"] = (
                    [public_host[4:]]
                    if lower_host.startswith("www.")
                    else [f"www.{public_host}"]
                )

        result = self._post("/instances/create", agent_payload)

        if _agent_rejected_new_instance_create_fields(result):
            result["ok"] = False
            result["message"] = (
                "Remote Droplet Agent is running an older /v1/instances/create "
                "schema that does not accept the current public_vps host field. "
                "Run Bootstrap Droplet Agent, then run Deploy Droplet again."
            )
            result["required_action"] = "bootstrap_droplet_agent"
            result["stale_remote_agent_schema"] = True
            result["sent_fields"] = sorted(agent_payload)
            return result

        return result

    def instances_create(self, **payload: Any) -> dict[str, Any]:
        return self.create_instance(**payload)

    def create_or_update_instance(self, **payload: Any) -> dict[str, Any]:
        return self.create_instance(**payload)

    def update_instance(self, **payload: Any) -> dict[str, Any]:
        merged_payload = {**dict(self.droplet_payload or {}), **dict(payload)}

        capsule_file = Path(str(merged_payload.get("capsule_file") or ""))
        remote_capsule_path = str(
            merged_payload.get("remote_capsule_path")
            or merged_payload.get("capsule_path")
            or _remote_capsule_path_from_payload(merged_payload, capsule_file)
        )

        return self._post(
            "/instances/update",
            {
                "instance_id": merged_payload.get("instance_id"),
                "capsule_path": remote_capsule_path,
                "create_pre_update_backup": True,
            },
        )

    def instances_update(self, **payload: Any) -> dict[str, Any]:
        return self.update_instance(**payload)

    def set_network_profile(self, **payload: Any) -> dict[str, Any]:
        """Set the Agent network profile.

        Manager/UI forms may use several field names for the same public host:
        domain, droplet_domain, public_host, host, target_host, or droplet_host.

        The Agent network endpoint should receive the canonical field name
        `host`. It should not receive `domain`, because older/current FastAPI
        schemas reject unknown extra fields.

        For Droplet public_vps, the host is required to generate:
        - KX_HOST
        - DJANGO_ALLOWED_HOSTS
        - NEXT_PUBLIC_API_BASE / NEXT_PUBLIC_BACKEND_BASE
        - Traefik Host(...) rules
        """

        merged_payload = {**dict(self.droplet_payload or {}), **dict(payload)}
        agent_payload = _network_profile_agent_payload(merged_payload)

        result = self._post("/network/set-profile", agent_payload)

        if _agent_rejected_new_network_profile_fields(result):
            result["ok"] = False
            result["message"] = (
                "Remote Droplet Agent is running an older /v1/network/set-profile "
                "schema that does not accept the current public_vps host fields. "
                "Run Bootstrap Droplet Agent, then run Deploy Droplet again."
            )
            result["required_action"] = "bootstrap_droplet_agent"
            result["stale_remote_agent_schema"] = True
            result["sent_fields"] = sorted(agent_payload)
            return result

        return result

    def network_set_profile(self, **payload: Any) -> dict[str, Any]:
        return self.set_network_profile(**payload)

    def set_profile(self, **payload: Any) -> dict[str, Any]:
        return self.set_network_profile(**payload)

    def run_security_check(self, **payload: Any) -> dict[str, Any]:
        return self.security_check(**payload)

    def security_check(self, **payload: Any) -> dict[str, Any]:
        merged_payload = {**dict(self.droplet_payload or {}), **dict(payload)}

        return self._post(
            "/security/check",
            {
                "instance_id": merged_payload.get("instance_id"),
                "blocking": True,
            },
        )

    def check_security(self, **payload: Any) -> dict[str, Any]:
        return self.security_check(**payload)

    def instance_status(self, **payload: Any) -> dict[str, Any]:
        merged_payload = {**dict(self.droplet_payload or {}), **dict(payload)}
        return self._post(
            "/instances/status",
            {"instance_id": merged_payload.get("instance_id")},
        )

    def instance_logs(self, **payload: Any) -> dict[str, Any]:
        merged_payload = {**dict(self.droplet_payload or {}), **dict(payload)}
        return self._post(
            "/instances/logs",
            {
                "instance_id": merged_payload.get("instance_id"),
                "service": merged_payload.get("service") or None,
                "tail": int(merged_payload.get("tail") or 200),
            },
        )

    def instance_health(self, **payload: Any) -> dict[str, Any]:
        merged_payload = {**dict(self.droplet_payload or {}), **dict(payload)}
        return self._post(
            "/instances/health",
            {"instance_id": merged_payload.get("instance_id")},
        )

    def start_instance(self, **payload: Any) -> dict[str, Any]:
        merged_payload = {**dict(self.droplet_payload or {}), **dict(payload)}

        agent_payload: dict[str, Any] = {
            "instance_id": merged_payload.get("instance_id"),
            "run_security_gate": _bool_payload(
                merged_payload.get("run_security_gate"),
                default=True,
            ),
        }

        # Preserve the active capsule identity through instance start.
        # Without this, the Agent may infer a stale/fallback capsule from an
        # already-rendered compose file and skip loading the newly built images.
        agent_payload.update(_capsule_identity_payload(merged_payload))

        if _bool_payload(
            merged_payload.get("force_recreate_after_image_load"),
            default=True,
        ):
            agent_payload["force_recreate_after_image_load"] = True

        result = self._post("/instances/start", agent_payload)

        if _agent_rejected_new_start_instance_fields(result):
            result["ok"] = False
            result["message"] = (
                "Remote Droplet Agent is running an older /v1/instances/start "
                "schema that does not accept capsule identity fields. "
                "Run Bootstrap Droplet Agent, then run Deploy Droplet again."
            )
            result["required_action"] = "bootstrap_droplet_agent"
            result["stale_remote_agent_schema"] = True
            result["sent_fields"] = sorted(agent_payload)
            return result

        return result

    def instances_start(self, **payload: Any) -> dict[str, Any]:
        return self.start_instance(**payload)

    def start_remote_instance(self, **payload: Any) -> dict[str, Any]:
        return self.start_instance(**payload)


def _response_payload(response: httpx.Response) -> dict[str, Any]:
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


def _agent_response_from_ssh_result(
    result: Mapping[str, Any],
    *,
    method: str,
    path: str,
    agent_health_url: str,
) -> dict[str, Any]:
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


def _run_argv(argv: list[str], *, timeout_seconds: int) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            argv,
            shell=False,
            check=False,
            stdin=subprocess.DEVNULL,
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
            "argv": _safe_argv(argv),
            "stdout": "",
            "stderr": str(exc),
            "returncode": 127,
        }
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = exc.stderr or "command timed out"

        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")

        return {
            "ok": False,
            "message": f"Command timed out: {argv[0]}",
            "argv": _safe_argv(argv),
            "stdout": stdout,
            "stderr": stderr,
            "returncode": 124,
        }

    return {
        "ok": completed.returncode == 0,
        "message": "Command completed." if completed.returncode == 0 else "Command failed.",
        "argv": _safe_argv(argv),
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "returncode": completed.returncode,
    }


def _safe_argv(argv: list[str]) -> list[str]:
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


def _strip_empty(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in payload.items()
        if value is not None and value != ""
    }


def _bool_payload(value: Any, *, default: bool = False) -> bool:
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


def _read_int_env(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default

    try:
        value = int(raw)
    except ValueError:
        return default

    if value <= 0:
        return default

    return value


def _agent_request_timeout_seconds(
    method: str,
    path: str,
    *,
    base_timeout_seconds: float,
) -> int:
    """Return a safe curl/http timeout for an Agent request path."""

    normalized_method = method.upper().strip()
    normalized_path = "/" + str(path).strip().lstrip("/")
    base_timeout = max(int(base_timeout_seconds), DEFAULT_AGENT_HTTP_TIMEOUT_SECONDS)

    if normalized_method == "GET":
        return base_timeout

    long_operation_paths = {
        "/instances/start",
        "/instances/update",
        "/instances/restore",
        "/backups/test-restore",
        "/instances/backup",
        "/instances/restore-new",
    }

    import_paths = {
        "/capsules/import",
    }

    mutation_paths = {
        "/instances/create",
        "/network/set-profile",
        "/security/check",
        "/instances/status",
        "/instances/logs",
    }

    if normalized_path in long_operation_paths:
        return _read_int_env(
            "KX_MANAGER_LONG_AGENT_TIMEOUT_SECONDS",
            DEFAULT_AGENT_LONG_OPERATION_TIMEOUT_SECONDS,
        )

    if normalized_path in import_paths:
        return _read_int_env(
            "KX_MANAGER_IMPORT_AGENT_TIMEOUT_SECONDS",
            DEFAULT_AGENT_IMPORT_TIMEOUT_SECONDS,
        )

    if normalized_path in mutation_paths:
        return _read_int_env(
            "KX_MANAGER_MUTATION_AGENT_TIMEOUT_SECONDS",
            DEFAULT_AGENT_MUTATION_TIMEOUT_SECONDS,
        )

    return base_timeout


def _ssh_command_timeout_seconds(curl_timeout_seconds: int) -> int:
    grace_seconds = _read_int_env(
        "KX_MANAGER_SSH_TIMEOUT_GRACE_SECONDS",
        DEFAULT_SSH_TIMEOUT_GRACE_SECONDS,
    )
    return max(curl_timeout_seconds + grace_seconds, 30)


def _require_payload_text(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if value is None or str(value).strip() == "":
        raise ValueError(f"{key} is required.")
    return str(value).strip()


def _is_droplet_payload(payload: Mapping[str, Any]) -> bool:
    target_mode = str(payload.get("target_mode") or "").strip().lower()
    return target_mode == "droplet" or bool(str(payload.get("droplet_host") or "").strip())


def _remote_agent_base_url(payload: Mapping[str, Any]) -> str:
    explicit = _explicit_remote_agent_url(payload)
    if explicit:
        base = explicit.removesuffix("/health").rstrip("/")
        return base if base.endswith("/v1") else base + "/v1"

    return "http://127.0.0.1:8765/v1"


def _remote_capsule_path_from_payload(
    payload: Mapping[str, Any],
    capsule_file: Path,
) -> str:
    remote_capsule_dir = str(payload.get("remote_capsule_dir") or "/opt/konnaxion/capsules")
    filename = capsule_file.name or Path(str(payload.get("capsule_file") or "")).name

    if not filename:
        capsule_id = str(payload.get("capsule_id") or "konnaxion-v14-demo-2026.04.30")
        filename = f"{capsule_id}.kxcap"

    return str(PurePosixPath(remote_capsule_dir) / filename)


def _explicit_remote_agent_url(payload: Mapping[str, Any]) -> str:
    """Return a usable direct Agent URL, or blank for SSH-local Droplet mode."""

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
        or ""
    ).strip()

    if not host:
        return ""

    # TEST-NET-3 documentation range; never a real customer Droplet target.
    if host.startswith("203.0.113."):
        return ""

    # In Droplet mode, localhost means the Manager machine, not the Droplet.
    # Treat old tunnel URLs such as http://127.0.0.1:18765/v1 as stale and use
    # SSH-local curl against the Droplet's private Agent instead.
    if host in {"127.0.0.1", "localhost", "::1"}:
        return "" if _is_droplet_payload(payload) else raw

    # If the operator selected a Droplet host and the configured Agent URL points
    # somewhere else, prefer SSH-local transport into the selected Droplet.
    if droplet_host and host != droplet_host:
        return ""

    return raw


def _capsule_identity_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return capsule identity fields for Agent calls that mutate runtime state.

    Deploy payloads may carry capsule identity explicitly or only through a
    local/remote capsule path. The Agent needs the same identity during
    /network/set-profile and /instances/start so regenerated compose/env files
    and image loading stay bound to the newly imported capsule instead of a
    stale fallback id.
    """

    capsule_id = str(payload.get("capsule_id") or "").strip()

    if not capsule_id:
        for key in ("remote_capsule_path", "capsule_path", "capsule_file"):
            value = str(payload.get(key) or "").strip()
            if value:
                capsule_id = Path(value).stem.strip()
                if capsule_id:
                    break

    capsule_version = str(payload.get("capsule_version") or "").strip()

    identity: dict[str, Any] = {}

    if capsule_id:
        identity["capsule_id"] = capsule_id

    if capsule_version:
        identity["capsule_version"] = capsule_version

    return identity


def _network_profile_agent_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Build the exact /network/set-profile payload accepted by the Agent.

    The Manager/UI layer may carry domain-like values under several keys.
    The Agent endpoint receives only the canonical `host` key.
    """

    network_profile = str(payload.get("network_profile") or "public_vps").strip()
    exposure_mode = str(payload.get("exposure_mode") or "public").strip()
    target_mode = str(payload.get("target_mode") or "").strip().lower()

    public_mode_enabled = _bool_payload(
        payload.get("public_mode_enabled"),
        default=(
            target_mode == "droplet"
            or network_profile in {"public_vps", "public_temporary"}
            or exposure_mode in {"public", "temporary_tunnel"}
        ),
    )

    agent_payload: dict[str, Any] = {
        "instance_id": payload.get("instance_id"),
        "network_profile": network_profile,
        "exposure_mode": exposure_mode,
        "public_mode_enabled": public_mode_enabled,
        "public_mode_expires_at": payload.get("public_mode_expires_at"),
    }

    # Preserve the active capsule identity while regenerating runtime network
    # files. Otherwise /network/set-profile can rewrite compose/env state using
    # a fallback capsule id, causing /instances/start to load images from the
    # wrong capsule directory.
    agent_payload.update(_capsule_identity_payload(payload))

    public_host = _public_host_from_payload(payload)
    if public_host:
        agent_payload["host"] = public_host

    return agent_payload


def _public_host_from_payload(payload: Mapping[str, Any]) -> str:
    """Return the canonical public runtime host for public_vps/Droplet mode.

    Preference order intentionally chooses operator-facing domain fields before
    the Droplet IP. A Droplet IP is valid as a fallback, but sslip.io/custom
    domain values should win because they are what Traefik and Django see in
    the public Host header.
    """

    candidate_keys = (
        "domain",
        "droplet_domain",
        "public_host",
        "public_url",
        "url",
        "host",
        "kx_host",
        "KX_HOST",
        "droplet_host",
        "target_host",
    )

    for key in candidate_keys:
        value = _clean_public_host(payload.get(key))
        if value:
            return value

    return ""


def _clean_public_host(value: Any) -> str:
    """Normalize a host-like value to host[:port] without scheme/path."""

    raw = str(value or "").strip()
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

    # Drop accidental paths when a raw host was supplied with /api or trailing path.
    host = host.split("/", 1)[0].strip()

    # Drop accidental userinfo if any URL-ish string slipped through.
    if "@" in host:
        host = host.rsplit("@", 1)[-1].strip()

    return host


def _agent_rejected_new_import_fields(result: Mapping[str, Any]) -> bool:
    """Return True when FastAPI rejected modern import fields as extras."""

    return bool(
        _rejected_extra_fields(result)
        & {
            "exposure_mode",
            "verify",
            "overwrite",
            "capsule_id",
        }
    )


def _agent_rejected_new_instance_create_fields(result: Mapping[str, Any]) -> bool:
    """Return True when FastAPI rejected current instance create fields."""

    return bool(
        _rejected_extra_fields(result)
        & {
            "host",
        }
    )


def _agent_rejected_new_network_profile_fields(result: Mapping[str, Any]) -> bool:
    """Return True when FastAPI rejected current network profile fields."""

    return bool(
        _rejected_extra_fields(result)
        & {
            "host",
            "public_mode_enabled",
            "public_mode_expires_at",
            "capsule_id",
            "capsule_version",
        }
    )


def _agent_rejected_new_start_instance_fields(result: Mapping[str, Any]) -> bool:
    """Return True when FastAPI rejected current instance start fields."""

    return bool(
        _rejected_extra_fields(result)
        & {
            "capsule_id",
            "capsule_version",
            "force_recreate_after_image_load",
        }
    )


def _rejected_extra_fields(result: Mapping[str, Any]) -> set[str]:
    """Extract FastAPI/Pydantic extra_forbidden field names from a response."""

    detail = result.get("detail")
    if not isinstance(detail, list):
        return set()

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

    return rejected_fields


def _ssh_result_is_retryable(result: Mapping[str, Any]) -> bool:
    """Return True for transient SSH transport failures worth retrying."""

    returncode = int(result.get("returncode") or 0)
    message = str(result.get("message") or "").lower()
    stderr = str(result.get("stderr") or "").lower()
    stdout = str(result.get("stdout") or "").lower()

    combined = " ".join((message, stderr, stdout))

    if returncode in {124, 255}:
        return True

    retry_markers = (
        "command timed out",
        "connection timed out",
        "operation timed out",
        "connection reset",
        "connection refused",
        "connection closed",
        "connection aborted",
        "temporary failure",
        "no route to host",
        "network is unreachable",
        "broken pipe",
    )

    return any(marker in combined for marker in retry_markers)


__all__ = [
    "_AgentHttpExecutionClient",
    "_remote_agent_base_url",
]