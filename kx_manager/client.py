"""Client for the Konnaxion Agent API.

The Konnaxion Capsule Manager must not control Docker, firewall rules,
host paths, backups, or runtime services directly. It communicates with
the local Konnaxion Agent through this constrained client.

This client supports two call styles:
- direct Agent API paths, for example ``/instances/start``
- Manager route-style paths, for example ``/instances/demo-001/start``

Manager route-style paths are translated into the narrow Agent API contract
exposed by ``kx_agent.api``.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

import httpx

from kx_shared.konnaxion_constants import (
    DEFAULT_EXPOSURE_MODE,
    DEFAULT_NETWORK_PROFILE,
    DockerService,
    ExposureMode,
    NetworkProfile,
)


DEFAULT_AGENT_BASE_URL = "http://127.0.0.1:8765/v1"
DEFAULT_TIMEOUT_SECONDS = 30.0

SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")

INSTANCE_PATH_RE = re.compile(
    r"^/instances/(?P<instance_id>[A-Za-z0-9][A-Za-z0-9_.-]*)"
    r"(?:/(?P<action>[A-Za-z0-9_.-]+))?/?$"
)

BACKUP_PATH_RE = re.compile(
    r"^/backups/(?P<backup_id>[A-Za-z0-9][A-Za-z0-9_.-]*)"
    r"(?:/(?P<action>[A-Za-z0-9_.-]+))?/?$"
)


class KonnaxionAgentClientError(RuntimeError):
    """Base error raised by the Manager's Agent API client."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int = 502,
        details: Any | None = None,
    ) -> None:
        self.status_code = status_code
        self.details = details
        super().__init__(message)


class KonnaxionAgentConnectionError(KonnaxionAgentClientError):
    """Raised when the Manager cannot reach the local Agent."""


class KonnaxionAgentResponseError(KonnaxionAgentClientError):
    """Raised when the Agent returns an error response."""

    def __init__(
        self,
        *,
        status_code: int,
        message: str,
        response_body: Any | None = None,
    ) -> None:
        self.response_body = response_body
        super().__init__(message, status_code=status_code, details=response_body)


# Compatibility names expected by existing Manager route modules.
AgentClientError = KonnaxionAgentClientError


@dataclass(frozen=True, slots=True)
class AgentClientConfig:
    """Connection settings for the local Konnaxion Agent."""

    base_url: str = DEFAULT_AGENT_BASE_URL
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    token: str | None = None

    @classmethod
    def from_env(cls) -> "AgentClientConfig":
        """Build Agent client config from environment variables."""

        explicit_url = os.getenv("KX_AGENT_URL", "").strip()

        if explicit_url:
            base_url = explicit_url
        else:
            host = os.getenv("KX_AGENT_HOST", "127.0.0.1").strip() or "127.0.0.1"
            port = os.getenv("KX_AGENT_PORT", "8765").strip() or "8765"
            scheme = os.getenv("KX_AGENT_SCHEME", "http").strip() or "http"
            prefix = os.getenv("KX_AGENT_API_PREFIX", "/v1").strip() or "/v1"
            base_url = f"{scheme}://{host}:{port}/{prefix.strip('/')}"

        return cls(
            base_url=base_url,
            timeout_seconds=read_float_env(
                "KX_AGENT_TIMEOUT_SECONDS",
                DEFAULT_TIMEOUT_SECONDS,
            ),
            token=os.getenv("KX_AGENT_TOKEN", "").strip() or None,
        )

    def normalized_base_url(self) -> str:
        return self.base_url.rstrip("/")


@dataclass(frozen=True, slots=True)
class TranslatedRequest:
    """Normalized request sent to the Agent API."""

    method: str
    path: str
    payload: dict[str, Any]
    params: dict[str, Any]


class KonnaxionAgentClient:
    """Typed async client used by Konnaxion Capsule Manager."""

    def __init__(
        self,
        config: AgentClientConfig | None = None,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.config = config or AgentClientConfig.from_env()
        self._owns_client = http_client is None
        self._client = http_client or httpx.AsyncClient(
            base_url=self.config.normalized_base_url(),
            timeout=self.config.timeout_seconds,
            headers=self._default_headers(),
        )

    @classmethod
    def from_env(cls) -> "KonnaxionAgentClient":
        """Create a client from ``KX_AGENT_*`` environment variables."""

        return cls(AgentClientConfig.from_env())

    async def __aenter__(self) -> "KonnaxionAgentClient":
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Close the underlying HTTP client if this object owns it."""

        if self._owns_client:
            await self._client.aclose()

    def _default_headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "konnaxion-capsule-manager",
        }
        if self.config.token:
            headers["Authorization"] = f"Bearer {self.config.token}"
        return headers

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Mapping[str, Any] | None = None,
    ) -> Any:
        """
        Send a request to the Agent.

        Existing Manager routes call this generic method with Manager-style
        paths. This method translates those paths into the Agent's constrained
        flat action API.
        """

        translated = translate_request(method, path, params=params, json=json)

        try:
            response = await self._client.request(
                translated.method,
                translated.path,
                params=strip_empty(translated.params),
                json=strip_empty(translated.payload) if translated.payload else None,
            )
        except httpx.ConnectError as exc:
            raise KonnaxionAgentConnectionError(
                "Unable to connect to the local Konnaxion Agent.",
                status_code=502,
                details={
                    "method": translated.method,
                    "path": translated.path,
                },
            ) from exc
        except httpx.TimeoutException as exc:
            raise KonnaxionAgentConnectionError(
                "Timed out while communicating with the local Konnaxion Agent.",
                status_code=504,
                details={
                    "method": translated.method,
                    "path": translated.path,
                },
            ) from exc
        except httpx.HTTPError as exc:
            raise KonnaxionAgentConnectionError(
                f"Unable to communicate with the local Konnaxion Agent: {exc}",
                status_code=502,
                details={
                    "method": translated.method,
                    "path": translated.path,
                },
            ) from exc

        return parse_response(response)

    async def _get(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        result = await self.request("GET", path, params=params)
        return ensure_mapping(result)

    async def _post(self, path: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        result = await self.request("POST", path, json=payload)
        return ensure_mapping(result)

    async def health(self) -> dict[str, Any]:
        """Return basic Agent process health."""

        return await self._get("/health")

    async def info(self) -> dict[str, Any]:
        """Return non-sensitive Agent metadata."""

        return await self._get("/agent/info")

    async def list_capsules(self) -> dict[str, Any]:
        """List local capsule files available to the Manager.

        This is intentionally Manager-local inventory. The current Agent API
        supports capsule import and verify actions, but does not expose a
        capsule inventory endpoint.
        """

        capsule_dir = resolve_capsules_dir()
        capsule_dir.mkdir(parents=True, exist_ok=True)

        capsules: list[dict[str, Any]] = []

        for capsule_file in sorted(capsule_dir.glob("*.kxcap")):
            stat = capsule_file.stat()
            capsule_id = capsule_file.stem
            updated_at = datetime.fromtimestamp(stat.st_mtime).isoformat()

            capsules.append(
                {
                    "id": capsule_id,
                    "capsule_id": capsule_id,
                    "filename": capsule_file.name,
                    "path": str(capsule_file),
                    "capsule_file": str(capsule_file),
                    "size_bytes": stat.st_size,
                    "created_at": updated_at,
                    "updated_at": updated_at,
                    "status": "available",
                    "imported": True,
                }
            )

        return {
            "ok": True,
            "capsules": capsules,
            "items": capsules,
            "count": len(capsules),
            "capsule_dir": str(capsule_dir),
        }

    async def import_capsule(
        self,
        *,
        capsule_path: str,
        instance_id: str,
        network_profile: NetworkProfile | str = DEFAULT_NETWORK_PROFILE,
    ) -> dict[str, Any]:
        return await self._post(
            "/capsules/import",
            {
                "capsule_path": capsule_path,
                "instance_id": instance_id,
                "network_profile": enum_value(network_profile),
            },
        )

    async def verify_capsule(self, *, capsule_path: str) -> dict[str, Any]:
        return await self._post(
            "/capsules/verify",
            {
                "capsule_path": capsule_path,
            },
        )

    async def create_instance(
        self,
        *,
        instance_id: str,
        capsule_id: str,
        network_profile: NetworkProfile | str = DEFAULT_NETWORK_PROFILE,
        exposure_mode: ExposureMode | str = DEFAULT_EXPOSURE_MODE,
        generate_secrets: bool = True,
    ) -> dict[str, Any]:
        return await self._post(
            "/instances/create",
            {
                "instance_id": instance_id,
                "capsule_id": capsule_id,
                "network_profile": enum_value(network_profile),
                "exposure_mode": enum_value(exposure_mode),
                "generate_secrets": generate_secrets,
            },
        )

    async def start_instance(
        self,
        *,
        instance_id: str,
        run_security_gate: bool = True,
    ) -> dict[str, Any]:
        return await self._post(
            "/instances/start",
            {
                "instance_id": instance_id,
                "run_security_gate": run_security_gate,
            },
        )

    async def stop_instance(
        self,
        *,
        instance_id: str,
        timeout_seconds: int = 60,
    ) -> dict[str, Any]:
        return await self._post(
            "/instances/stop",
            {
                "instance_id": instance_id,
                "timeout_seconds": timeout_seconds,
            },
        )

    async def instance_status(self, *, instance_id: str) -> dict[str, Any]:
        return await self._post(
            "/instances/status",
            {
                "instance_id": instance_id,
            },
        )

    async def instance_logs(
        self,
        *,
        instance_id: str,
        service: DockerService | str | None = None,
        tail: int = 200,
    ) -> dict[str, Any]:
        return await self._post(
            "/instances/logs",
            {
                "instance_id": instance_id,
                "service": enum_value(service) if service is not None else None,
                "tail": tail,
            },
        )

    async def backup_instance(
        self,
        *,
        instance_id: str,
        backup_class: str = "manual",
        verify_after_create: bool = True,
    ) -> dict[str, Any]:
        return await self._post(
            "/instances/backup",
            {
                "instance_id": instance_id,
                "backup_class": normalize_backup_class(backup_class),
                "verify_after_create": verify_after_create,
            },
        )

    async def list_backups(
        self,
        *,
        instance_id: str | None = None,
        status: str | None = None,
        backup_class: str | None = None,
        limit: int = 50,
    ) -> Any:
        """List backups known to the Agent."""

        params: dict[str, Any] = {
            "instance_id": instance_id,
            "status": enum_value(status) if status is not None else None,
            "backup_class": (
                normalize_backup_class(backup_class)
                if backup_class not in (None, "")
                else None
            ),
            "limit": limit,
        }

        return await self.request("GET", "/backups", params=params)

    async def get_backup(self, *, backup_id: str) -> dict[str, Any]:
        """Return details for one backup."""

        validate_safe_id(backup_id, field_name="backup_id")
        return await self._get(f"/backups/{backup_id}")

    async def verify_backup(
        self,
        *,
        backup_id: str,
        deep: bool = False,
        reason: str = "",
    ) -> dict[str, Any]:
        """Request backup verification through the Agent."""

        return await self._post(
            "/backups/verify",
            {
                "backup_id": backup_id,
                "deep": deep,
                "reason": reason,
            },
        )

    async def test_restore_backup(
        self,
        *,
        backup_id: str,
        instance_id: str | None = None,
        target_instance_id: str | None = None,
        restore_data: bool = True,
        run_migrations: bool = True,
        run_security_gate: bool = True,
        run_healthchecks: bool = True,
        reason: str = "",
    ) -> dict[str, Any]:
        """Run a test restore workflow through the Agent."""

        return await self._post(
            "/backups/test-restore",
            {
                "backup_id": backup_id,
                "instance_id": instance_id,
                "target_instance_id": target_instance_id,
                "restore_data": restore_data,
                "run_migrations": run_migrations,
                "run_security_gate": run_security_gate,
                "run_healthchecks": run_healthchecks,
                "reason": reason,
                "test_only": True,
            },
        )

    async def restore_instance(
        self,
        *,
        instance_id: str,
        backup_id: str,
        create_pre_restore_backup: bool = True,
    ) -> dict[str, Any]:
        return await self._post(
            "/instances/restore",
            {
                "instance_id": instance_id,
                "backup_id": backup_id,
                "create_pre_restore_backup": create_pre_restore_backup,
            },
        )

    async def restore_new_instance(
        self,
        *,
        source_backup_id: str,
        new_instance_id: str,
        network_profile: NetworkProfile | str = DEFAULT_NETWORK_PROFILE,
    ) -> dict[str, Any]:
        return await self._post(
            "/instances/restore-new",
            {
                "source_backup_id": source_backup_id,
                "new_instance_id": new_instance_id,
                "network_profile": enum_value(network_profile),
            },
        )

    async def update_instance(
        self,
        *,
        instance_id: str,
        capsule_path: str,
        create_pre_update_backup: bool = True,
    ) -> dict[str, Any]:
        return await self._post(
            "/instances/update",
            {
                "instance_id": instance_id,
                "capsule_path": capsule_path,
                "create_pre_update_backup": create_pre_update_backup,
            },
        )

    async def rollback_instance(
        self,
        *,
        instance_id: str,
        target_release_id: str | None = None,
        restore_data: bool = True,
    ) -> dict[str, Any]:
        return await self._post(
            "/instances/rollback",
            {
                "instance_id": instance_id,
                "target_release_id": target_release_id,
                "restore_data": restore_data,
            },
        )

    async def instance_health(self, *, instance_id: str) -> dict[str, Any]:
        return await self._post(
            "/instances/health",
            {
                "instance_id": instance_id,
            },
        )

    async def security_check(
        self,
        *,
        instance_id: str,
        blocking: bool = True,
    ) -> dict[str, Any]:
        return await self._post(
            "/security/check",
            {
                "instance_id": instance_id,
                "blocking": blocking,
            },
        )

    async def list_artifacts(
        self,
        *,
        kind: str | None = None,
        products_only: bool = False,
        composition_candidates_only: bool = False,
    ) -> dict[str, Any]:
        return await self._get(
            "/artifacts",
            params={
                "kind": kind,
                "products_only": products_only,
                "composition_candidates_only": composition_candidates_only,
            },
        )

    async def get_artifact(self, artifact_id: str) -> dict[str, Any]:
        validate_safe_id(artifact_id, field_name="artifact_id")
        return await self._get(f"/artifacts/{artifact_id}")

    async def get_artifact_integration_manifest(self, artifact_id: str) -> dict[str, Any]:
        validate_safe_id(artifact_id, field_name="artifact_id")
        return await self._get(f"/artifacts/{artifact_id}/integration-manifest")

    async def remove_artifact(
        self,
        *,
        artifact_id: str,
        preserve_data: bool = True,
    ) -> dict[str, Any]:
        validate_safe_id(artifact_id, field_name="artifact_id")
        return await self._post(
            "/artifacts/remove",
            {"artifact_id": artifact_id, "preserve_data": preserve_data},
        )

    async def set_network_profile(
        self,
        *,
        instance_id: str,
        network_profile: NetworkProfile | str,
        exposure_mode: ExposureMode | str = DEFAULT_EXPOSURE_MODE,
        public_mode_expires_at: datetime | str | None = None,
    ) -> dict[str, Any]:
        return await self._post(
            "/network/set-profile",
            {
                "instance_id": instance_id,
                "network_profile": enum_value(network_profile),
                "exposure_mode": enum_value(exposure_mode),
                "public_mode_expires_at": serialize_datetime(public_mode_expires_at),
            },
        )


# Compatibility class name expected by existing Manager route modules.
AgentClient = KonnaxionAgentClient


DIRECT_AGENT_PATHS = {
    ("GET", "/health"),
    ("GET", "/agent/info"),
    ("GET", "/artifacts"),
    ("POST", "/artifacts/remove"),
    ("GET", "/backups"),
    ("POST", "/backups/verify"),
    ("POST", "/backups/test-restore"),
    ("POST", "/capsules/import"),
    ("POST", "/capsules/verify"),
    ("POST", "/instances/create"),
    ("POST", "/instances/start"),
    ("POST", "/instances/stop"),
    ("POST", "/instances/status"),
    ("POST", "/instances/logs"),
    ("POST", "/instances/backup"),
    ("POST", "/instances/restore"),
    ("POST", "/instances/restore-new"),
    ("POST", "/instances/update"),
    ("POST", "/instances/rollback"),
    ("POST", "/instances/health"),
    ("POST", "/security/check"),
    ("POST", "/network/set-profile"),
}


def translate_request(
    method: str,
    path: str,
    *,
    params: Mapping[str, Any] | None = None,
    json: Mapping[str, Any] | None = None,
) -> TranslatedRequest:
    """Translate Manager route-style requests into Agent API requests."""

    normalized_method = method.upper().strip()
    normalized_path = normalize_path(path)
    payload = dict(json or {})
    query = dict(params or {})

    if (normalized_method, normalized_path) in DIRECT_AGENT_PATHS:
        return TranslatedRequest(
            method=normalized_method,
            path=normalized_path,
            payload=filter_direct_payload(normalized_path, payload),
            params=query,
        )

    if normalized_path.startswith("/artifacts"):
        return translate_artifact_path(
            normalized_method,
            normalized_path,
            payload,
            query,
        )

    if normalized_path.startswith("/backups"):
        return translate_backup_path(
            normalized_method,
            normalized_path,
            payload,
            query,
        )

    match = INSTANCE_PATH_RE.fullmatch(normalized_path)
    if match:
        return translate_instance_path(
            normalized_method,
            match.group("instance_id"),
            match.group("action") or "status",
            payload,
            query,
        )

    raise KonnaxionAgentClientError(
        f"Unsupported Agent client path: {normalized_method} {normalized_path}",
        status_code=501,
        details={
            "method": normalized_method,
            "path": normalized_path,
        },
    )


def translate_instance_path(
    method: str,
    instance_id: str,
    action: str,
    payload: dict[str, Any],
    query: dict[str, Any],
) -> TranslatedRequest:
    """Translate ``/instances/{instance_id}/...`` paths."""

    validate_safe_id(instance_id, field_name="instance_id")

    action = action.strip().lower()
    merged = {**query, **payload, "instance_id": instance_id}

    if method == "GET" and action in {"status", ""}:
        return post("/instances/status", keep(merged, "instance_id"))

    if method == "GET" and action == "health":
        return post("/instances/health", keep(merged, "instance_id"))

    if method == "GET" and action == "logs":
        return post(
            "/instances/logs",
            keep(merged, "instance_id", "service", "tail"),
        )

    if method == "GET" and action == "security":
        return post(
            "/security/check",
            keep(merged, "instance_id", "blocking"),
        )

    if method == "GET" and action == "backups":
        return get(
            "/backups",
            params=keep(
                merged,
                "instance_id",
                "status",
                "backup_class",
                "limit",
            ),
        )

    if method == "POST" and action == "start":
        return post(
            "/instances/start",
            keep(merged, "instance_id", "run_security_gate"),
        )

    if method == "POST" and action == "stop":
        return post(
            "/instances/stop",
            keep(merged, "instance_id", "timeout_seconds"),
        )

    if method == "POST" and action in {"backup", "backups"}:
        body = keep(merged, "instance_id", "backup_class", "verify_after_create")
        body["backup_class"] = normalize_backup_class(
            body.get("backup_class", "manual")
        )
        return post("/instances/backup", body)

    if method == "POST" and action == "restore":
        return post(
            "/instances/restore",
            keep(
                merged,
                "instance_id",
                "backup_id",
                "create_pre_restore_backup",
            ),
        )

    if method == "POST" and action == "restore-new":
        body = {
            "source_backup_id": (
                merged.get("source_backup_id")
                or merged.get("from_backup_id")
                or merged.get("backup_id")
            ),
            "new_instance_id": (
                merged.get("new_instance_id")
                or merged.get("target_instance_id")
            ),
            "network_profile": merged.get(
                "network_profile",
                DEFAULT_NETWORK_PROFILE.value,
            ),
        }
        return post("/instances/restore-new", body)

    if method == "POST" and action == "update":
        body = {
            "instance_id": instance_id,
            "capsule_path": merged.get("capsule_path") or merged.get("capsule_id"),
            "create_pre_update_backup": merged.get(
                "create_pre_update_backup",
                merged.get("backup_before_update", True),
            ),
        }
        return post("/instances/update", body)

    if method == "POST" and action == "rollback":
        body = {
            "instance_id": instance_id,
            "target_release_id": (
                merged.get("target_release_id")
                or merged.get("target_capsule_id")
            ),
            "restore_data": merged.get(
                "restore_data",
                bool(merged.get("backup_id", True)),
            ),
        }
        return post("/instances/rollback", body)

    if method == "POST" and action in {"network", "set-profile"}:
        body = {
            "instance_id": instance_id,
            "network_profile": merged.get("network_profile"),
            "exposure_mode": merged.get(
                "exposure_mode",
                DEFAULT_EXPOSURE_MODE.value,
            ),
            "public_mode_expires_at": merged.get("public_mode_expires_at"),
        }
        return post("/network/set-profile", body)

    if method == "POST" and action in {"security", "security.check", "check"}:
        return post(
            "/security/check",
            keep(merged, "instance_id", "blocking"),
        )

    raise KonnaxionAgentClientError(
        f"Unsupported instance Agent path: {method} /instances/{instance_id}/{action}",
        status_code=501,
        details={
            "method": method,
            "instance_id": instance_id,
            "action": action,
        },
    )


def translate_artifact_path(
    method: str,
    path: str,
    payload: dict[str, Any],
    query: dict[str, Any],
) -> TranslatedRequest:
    """Translate installed-artifact registry routes into Agent API routes."""

    if path == "/artifacts":
        if method != "GET":
            raise KonnaxionAgentClientError(
                f"Unsupported artifact Agent path: {method} {path}", status_code=501
            )
        return get(
            "/artifacts",
            params=keep(
                {**query, **payload},
                "kind",
                "products_only",
                "composition_candidates_only",
            ),
        )

    if path == "/artifacts/remove":
        if method != "POST":
            raise KonnaxionAgentClientError(
                f"Unsupported artifact remove method: {method}", status_code=501
            )
        return post(
            "/artifacts/remove",
            keep(payload, "artifact_id", "preserve_data"),
        )

    parts = [part for part in path.strip("/").split("/") if part]
    if len(parts) >= 2 and parts[0] == "artifacts":
        artifact_id = validate_safe_id(parts[1], field_name="artifact_id")
        if method == "GET" and len(parts) == 2:
            return get(f"/artifacts/{artifact_id}")
        if method == "GET" and len(parts) == 3 and parts[2] == "integration-manifest":
            return get(f"/artifacts/{artifact_id}/integration-manifest")

    raise KonnaxionAgentClientError(
        f"Unsupported artifact Agent path: {method} {path}",
        status_code=501,
        details={"method": method, "path": path},
    )


def translate_backup_path(
    method: str,
    path: str,
    payload: dict[str, Any],
    query: dict[str, Any],
) -> TranslatedRequest:
    """Translate Manager-style backup routes into Agent backup API routes."""

    if path == "/backups":
        if method != "GET":
            raise KonnaxionAgentClientError(
                f"Unsupported backup Agent path: {method} {path}",
                status_code=501,
                details={
                    "method": method,
                    "path": path,
                },
            )

        return get(
            "/backups",
            params=keep(
                {**query, **payload},
                "instance_id",
                "status",
                "backup_class",
                "limit",
            ),
        )

    if path == "/backups/verify":
        if method != "POST":
            raise KonnaxionAgentClientError(
                f"Unsupported backup verify method: {method}",
                status_code=501,
                details={
                    "method": method,
                    "path": path,
                },
            )

        return post(
            "/backups/verify",
            filter_direct_payload("/backups/verify", payload),
        )

    if path == "/backups/test-restore":
        if method != "POST":
            raise KonnaxionAgentClientError(
                f"Unsupported backup test-restore method: {method}",
                status_code=501,
                details={
                    "method": method,
                    "path": path,
                },
            )

        return post(
            "/backups/test-restore",
            filter_direct_payload("/backups/test-restore", payload),
        )

    match = BACKUP_PATH_RE.fullmatch(path)
    if not match:
        raise KonnaxionAgentClientError(
            f"Unsupported backup Agent path: {method} {path}",
            status_code=501,
            details={
                "method": method,
                "path": path,
            },
        )

    backup_id = validate_safe_id(match.group("backup_id"), field_name="backup_id")
    action = (match.group("action") or "").strip().lower()
    merged = {**query, **payload, "backup_id": backup_id}

    if method == "GET" and not action:
        return get(f"/backups/{backup_id}")

    if method == "POST" and action == "verify":
        return post(
            "/backups/verify",
            filter_direct_payload("/backups/verify", merged),
        )

    if method == "POST" and action == "test-restore":
        return post(
            "/backups/test-restore",
            filter_direct_payload("/backups/test-restore", merged),
        )

    raise KonnaxionAgentClientError(
        f"Unsupported backup Agent path: {method} {path}",
        status_code=501,
        details={
            "method": method,
            "backup_id": backup_id,
            "action": action,
        },
    )


def get(path: str, *, params: Mapping[str, Any] | None = None) -> TranslatedRequest:
    return TranslatedRequest(
        method="GET",
        path=path,
        payload={},
        params=dict(params or {}),
    )


def post(path: str, payload: Mapping[str, Any]) -> TranslatedRequest:
    return TranslatedRequest(
        method="POST",
        path=path,
        payload=dict(payload),
        params={},
    )


def resolve_capsules_dir() -> Path:
    """Resolve the Manager-local capsule inventory directory."""

    explicit = os.getenv("KX_CAPSULES_DIR", "").strip()
    if explicit:
        return Path(explicit)

    root = os.getenv("KX_ROOT", "").strip()
    if root:
        return Path(root) / "capsules"

    try:
        from kx_shared.konnaxion_constants import KX_CAPSULES_DIR
    except ImportError:
        return Path.cwd() / "runtime" / "capsules"

    return Path(KX_CAPSULES_DIR)


def filter_direct_payload(path: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    """Filter payload fields for direct Agent paths with Pydantic extra=forbid."""

    allowed_by_path = {
        "/artifacts/remove": {
            "artifact_id",
            "preserve_data",
        },
        "/backups/verify": {
            "backup_id",
            "deep",
            "reason",
        },
        "/backups/test-restore": {
            "backup_id",
            "instance_id",
            "target_instance_id",
            "restore_data",
            "run_migrations",
            "run_security_gate",
            "run_healthchecks",
            "reason",
            "test_only",
        },
        "/capsules/import": {
            "capsule_path",
            "instance_id",
            "network_profile",
        },
        "/capsules/verify": {
            "capsule_path",
        },
        "/instances/create": {
            "instance_id",
            "capsule_id",
            "network_profile",
            "exposure_mode",
            "generate_secrets",
        },
        "/instances/start": {
            "instance_id",
            "run_security_gate",
        },
        "/instances/stop": {
            "instance_id",
            "timeout_seconds",
        },
        "/instances/status": {
            "instance_id",
        },
        "/instances/logs": {
            "instance_id",
            "service",
            "tail",
        },
        "/instances/backup": {
            "instance_id",
            "backup_class",
            "verify_after_create",
        },
        "/instances/restore": {
            "instance_id",
            "backup_id",
            "create_pre_restore_backup",
        },
        "/instances/restore-new": {
            "source_backup_id",
            "new_instance_id",
            "network_profile",
        },
        "/instances/update": {
            "instance_id",
            "capsule_path",
            "create_pre_update_backup",
        },
        "/instances/rollback": {
            "instance_id",
            "target_release_id",
            "restore_data",
        },
        "/instances/health": {
            "instance_id",
        },
        "/security/check": {
            "instance_id",
            "blocking",
        },
        "/network/set-profile": {
            "instance_id",
            "network_profile",
            "exposure_mode",
            "public_mode_expires_at",
        },
    }

    allowed = allowed_by_path.get(path)
    if allowed is None:
        return dict(payload)

    result = {
        key: value
        for key, value in dict(payload).items()
        if key in allowed
    }

    if path == "/instances/backup" and "backup_class" in result:
        result["backup_class"] = normalize_backup_class(result["backup_class"])

    return result


def keep(payload: Mapping[str, Any], *keys: str) -> dict[str, Any]:
    return {
        key: payload[key]
        for key in keys
        if key in payload
    }


def normalize_path(path: str) -> str:
    """Normalize caller paths to paths relative to the Agent API prefix."""

    normalized = "/" + str(path).strip().lstrip("/")
    normalized = normalized.rstrip("/") if normalized != "/" else normalized

    if normalized == "/v1":
        return "/"

    if normalized.startswith("/v1/"):
        normalized = normalized.removeprefix("/v1")

    return normalized


def enum_value(value: Any) -> Any:
    """Return the string value for enum-like objects."""

    return getattr(value, "value", value)


def serialize_datetime(value: datetime | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def strip_none(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Remove None values from a JSON payload."""

    return {
        key: value
        for key, value in payload.items()
        if value is not None
    }


def strip_empty(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Remove None and empty string values from request data."""

    return {
        key: value
        for key, value in payload.items()
        if value is not None and value != ""
    }


def extract_error_message(body: Any) -> str:
    """Extract a useful error message from an Agent error response."""

    if isinstance(body, dict):
        detail = body.get("detail") or body.get("error") or body.get("message")

        if isinstance(detail, str):
            return detail

        if isinstance(detail, list):
            return "; ".join(str(item) for item in detail)

        if detail is not None:
            return str(detail)

    return "Unknown Agent API error."


def parse_response(response: httpx.Response) -> Any:
    """Parse and validate an Agent HTTP response."""

    if not response.content:
        body: Any = {}
    else:
        try:
            body = response.json()
        except ValueError:
            body = {
                "detail": response.text,
            }

    if response.is_error:
        message = extract_error_message(body)
        raise KonnaxionAgentResponseError(
            status_code=response.status_code,
            message=message,
            response_body=body,
        )

    return body


def ensure_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value

    raise KonnaxionAgentResponseError(
        status_code=502,
        message="Agent returned a non-object JSON response.",
        response_body=value,
    )


def normalize_backup_class(value: Any) -> str:
    """Map Manager backup class names to Agent API backup classes."""

    raw = str(enum_value(value) or "manual").strip()

    aliases = {
        "scheduled_daily": "scheduled",
        "scheduled_weekly": "scheduled",
        "scheduled_monthly": "scheduled",
    }

    normalized = aliases.get(raw, raw)

    if normalized not in {"manual", "scheduled", "pre_update", "pre_restore"}:
        raise KonnaxionAgentClientError(
            f"Invalid backup_class: {raw!r}",
            status_code=422,
            details={
                "allowed": [
                    "manual",
                    "scheduled",
                    "pre_update",
                    "pre_restore",
                ],
            },
        )

    return normalized


def validate_safe_id(value: str, *, field_name: str = "id") -> str:
    if not isinstance(value, str) or not SAFE_ID_RE.fullmatch(value):
        raise KonnaxionAgentClientError(
            f"Invalid {field_name}: {value!r}",
            status_code=422,
            details={
                "field": field_name,
                "value": value,
            },
        )

    if value in {".", ".."} or "/" in value or "\\" in value:
        raise KonnaxionAgentClientError(
            f"Invalid {field_name}: {value!r}",
            status_code=422,
            details={
                "field": field_name,
                "value": value,
            },
        )

    return value


def read_float_env(key: str, default: float) -> float:
    raw = os.getenv(key)

    if raw is None or raw.strip() == "":
        return default

    try:
        return float(raw)
    except ValueError:
        return default


def get_agent_client(config: AgentClientConfig | None = None) -> AgentClient:
    """Return a configured Agent client for non-FastAPI callers."""

    return AgentClient(config or AgentClientConfig.from_env())


__all__ = [
    "AgentClient",
    "AgentClientConfig",
    "AgentClientError",
    "DEFAULT_AGENT_BASE_URL",
    "DEFAULT_TIMEOUT_SECONDS",
    "KonnaxionAgentClient",
    "KonnaxionAgentClientError",
    "KonnaxionAgentConnectionError",
    "KonnaxionAgentResponseError",
    "TranslatedRequest",
    "enum_value",
    "extract_error_message",
    "get_agent_client",
    "normalize_backup_class",
    "parse_response",
    "resolve_capsules_dir",
    "strip_empty",
    "strip_none",
    "translate_request",
]