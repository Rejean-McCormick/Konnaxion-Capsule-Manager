"""Deployment service for Konnaxion Capsule Manager GUI workflows.

This module coordinates GUI deployment actions.

It must not directly run Docker, edit firewall rules, modify host networking,
touch backups, or execute arbitrary shell commands. Privileged runtime work must
remain behind approved Manager service wrappers, KonnaxionAgentClient methods,
Agent API endpoints, Builder operations, Deploy operations, or approved CLI
fallbacks.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from re import fullmatch
from typing import Any, Iterable, Mapping
from urllib.parse import urlparse


from kx_manager.defaults import (
    DEFAULT_DROPLET_INSTANCE_ID,
    DEFAULT_DROPLET_USER,
    DEFAULT_REMOTE_CAPSULE_DIR,
    DEFAULT_REMOTE_KX_ROOT,
    DEFAULT_SSH_PORT,
)


TARGET_LOCAL = "local"
TARGET_INTRANET = "intranet"
TARGET_TEMPORARY_PUBLIC = "temporary_public"
TARGET_DROPLET = "droplet"

PROFILE_LOCAL_ONLY = "local_only"
PROFILE_INTRANET_PRIVATE = "intranet_private"
PROFILE_PUBLIC_TEMPORARY = "public_temporary"
PROFILE_PUBLIC_VPS = "public_vps"

EXPOSURE_PRIVATE = "private"
EXPOSURE_LAN = "lan"
EXPOSURE_TEMPORARY_TUNNEL = "temporary_tunnel"
EXPOSURE_PUBLIC = "public"

DEFAULT_INSTANCE_ID = DEFAULT_DROPLET_INSTANCE_ID
DEFAULT_LOCAL_URL = "https://127.0.0.1"
DEFAULT_INTRANET_HOST = "konnaxion.local"
DEFAULT_PRIVATE_AGENT_HEALTH_URL = "http://127.0.0.1:8765/v1/health"

SAFE_INSTANCE_ID_RE = r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}"


class DeployValidationError(ValueError):
    """Raised when a deployment request violates target-mode rules."""


class DeployExecutionError(RuntimeError):
    """Raised when an approved deployment step cannot be completed."""


@dataclass(slots=True)
class DeployStep:
    """One deployment workflow step."""

    name: str
    ok: bool
    message: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class DeployResult:
    """Normalized deployment result returned to routes or GUI actions."""

    ok: bool
    action: str
    instance_id: str
    message: str
    steps: list[DeployStep] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)
    stdout: str | None = None
    stderr: str | None = None
    returncode: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return JSON-safe result data."""

        return {
            "ok": self.ok,
            "action": self.action,
            "instance_id": self.instance_id,
            "message": self.message,
            "steps": [asdict(step) for step in self.steps],
            "data": self.data,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "returncode": self.returncode,
        }


@dataclass(slots=True)
class BaseDeployRequest:
    """Common deployment request fields."""

    instance_id: str = DEFAULT_INSTANCE_ID
    capsule_file: str | Path | None = None
    source_dir: str | Path | None = None
    capsule_output_dir: str | Path | None = None
    capsule_id: str | None = None
    capsule_version: str | None = None

    manager_client: Any | None = field(default=None, repr=False, compare=False)
    agent_client: Any | None = field(default=None, repr=False, compare=False)
    builder_request: Any | None = field(default=None, repr=False, compare=False)

    build: bool = False
    verify: bool = True
    update_existing: bool = False
    run_security_gate: bool = True
    start: bool = True
    plan_only: bool = False


@dataclass(slots=True)
class LocalDeployRequest(BaseDeployRequest):
    """Same-machine local deployment request."""

    target_mode: str = TARGET_LOCAL
    network_profile: str = PROFILE_LOCAL_ONLY
    exposure_mode: str = EXPOSURE_PRIVATE
    runtime_root: str | Path | None = None
    url: str = DEFAULT_LOCAL_URL


@dataclass(slots=True)
class IntranetDeployRequest(BaseDeployRequest):
    """Private LAN/internal deployment request."""

    target_mode: str = TARGET_INTRANET
    network_profile: str = PROFILE_INTRANET_PRIVATE
    exposure_mode: str = EXPOSURE_PRIVATE
    runtime_root: str | Path | None = None
    host: str = DEFAULT_INTRANET_HOST


@dataclass(slots=True)
class TemporaryPublicDeployRequest(BaseDeployRequest):
    """Temporary public demo deployment request."""

    target_mode: str = TARGET_TEMPORARY_PUBLIC
    network_profile: str = PROFILE_PUBLIC_TEMPORARY
    exposure_mode: str = EXPOSURE_TEMPORARY_TUNNEL
    public_host: str | None = None
    public_mode_expires_at: str | datetime | None = None
    confirmed: bool = False


@dataclass(slots=True)
class DropletDeployRequest(BaseDeployRequest):
    """Remote VPS/Droplet deployment request."""

    target_mode: str = TARGET_DROPLET
    network_profile: str = PROFILE_PUBLIC_VPS
    exposure_mode: str = EXPOSURE_PUBLIC

    droplet_name: str | None = None
    droplet_host: str | None = None
    droplet_user: str = DEFAULT_DROPLET_USER
    ssh_key_path: str | Path | None = None
    ssh_port: int = DEFAULT_SSH_PORT

    remote_kx_root: str = DEFAULT_REMOTE_KX_ROOT
    remote_capsule_dir: str = DEFAULT_REMOTE_CAPSULE_DIR
    remote_agent_url: str | None = None
    domain: str | None = None

    confirmed: bool = False
    copy_capsule: bool = True


def deploy_local(request: LocalDeployRequest) -> DeployResult:
    """Run the local deployment flow."""

    return _run_deploy_flow(
        action="deploy_local",
        request=request,
        final_message="Local deployment completed.",
        remote=False,
    )


def deploy_intranet(request: IntranetDeployRequest) -> DeployResult:
    """Run the intranet deployment flow."""

    return _run_deploy_flow(
        action="deploy_intranet",
        request=request,
        final_message="Intranet deployment completed.",
        remote=False,
    )


def deploy_temporary_public(request: TemporaryPublicDeployRequest) -> DeployResult:
    """Run the temporary-public deployment flow."""

    return _run_deploy_flow(
        action="deploy_temporary_public",
        request=request,
        final_message="Temporary public deployment completed.",
        remote=False,
    )


def deploy_droplet(request: DropletDeployRequest) -> DeployResult:
    """Run the remote Droplet/VPS deployment flow."""

    result = _new_result(
        action="deploy_droplet",
        request=request,
        message="Droplet deployment failed.",
    )

    try:
        _validate_droplet_request(request)

        capsule_file = _prepare_capsule(request, result)

        if request.copy_capsule:
            _copy_capsule_to_droplet(request, capsule_file, result)
        else:
            _add_step(result, "copy_capsule_to_droplet", True, "Capsule copy skipped.")

        _ensure_remote_runtime(request, result)

        # The deploy page already has an explicit "Check Droplet Agent" step.
        # In the full deployment flow this duplicate check is useful, but it
        # must not block deployment when the Agent is private on 127.0.0.1 and
        # the real import/update/start calls will validate reachability anyway.
        _check_droplet_agent(request, result, required=False)

        # Guard against the exact failure seen on Droplet: an older Agent accepts
        # health checks but rejects the newer import contract fields
        # (verify/overwrite/capsule_id/exposure_mode). This keeps deploy_droplet
        # from continuing into a confusing import_capsule 422/500 and tells the
        # operator to refresh the remote Agent from the local source tree first.
        _require_current_droplet_agent_import_contract(request, result)

        _import_capsule(request, capsule_file, result, remote=True)
        _create_or_update_instance(request, result, remote=True)
        _set_network_profile(request, result, remote=True)
        _run_security_gate(request, result, remote=True)
        _start_instance(request, result, remote=True)

        remote_capsule_path = str(
            PurePosixPath(request.remote_capsule_dir) / capsule_file.name
        )
        public_host = _public_host_for_request(request)

        result.ok = True
        result.message = "Droplet deployment completed."
        result.data.update(
            {
                "target_mode": TARGET_DROPLET,
                "network_profile": PROFILE_PUBLIC_VPS,
                "exposure_mode": EXPOSURE_PUBLIC,
                "droplet_name": request.droplet_name,
                "droplet_host": request.droplet_host,
                "droplet_user": request.droplet_user,
                "ssh_port": request.ssh_port,
                "domain": request.domain,
                "host": public_host,
                "public_host": public_host,
                "capsule_id": request.capsule_id,
                "capsule_version": request.capsule_version,
                "capsule_file": str(capsule_file),
                "remote_kx_root": request.remote_kx_root,
                "remote_capsule_dir": request.remote_capsule_dir,
                "remote_capsule_path": remote_capsule_path,
                "public_url": _host_to_https_url(public_host),
                "remote_agent_url": _direct_remote_agent_url(request),
                "agent_health_url": _remote_agent_health_url(request),
                "agent_transport": _agent_transport_for_request(request),
            }
        )
        return result

    except Exception as exc:
        result.ok = False
        result.message = str(exc)
        return result


def check_droplet_agent(request: DropletDeployRequest) -> DeployResult:
    """Check a Droplet Agent through SSH-local health unless a public URL is set."""

    result = _new_result(
        action="check_droplet_agent",
        request=request,
        message="Droplet Agent check failed.",
    )

    try:
        _validate_droplet_connection_fields(request)
        _check_droplet_agent(request, result, required=True)

        result.ok = True
        result.message = "Droplet Agent check completed."
        result.data.update(
            {
                "droplet_name": request.droplet_name,
                "droplet_host": request.droplet_host,
                "droplet_user": request.droplet_user,
                "ssh_port": request.ssh_port,
                "remote_kx_root": request.remote_kx_root,
                "remote_capsule_dir": request.remote_capsule_dir,
                "remote_agent_url": _direct_remote_agent_url(request),
                "agent_health_url": _remote_agent_health_url(request),
                "agent_transport": _agent_transport_for_request(request),
            }
        )
        return result

    except Exception as exc:
        result.ok = False
        result.message = str(exc)
        return result


def copy_capsule_to_droplet(
    request: DropletDeployRequest,
    capsule_file: str | Path | None = None,
) -> DeployResult:
    """Copy a capsule to a Droplet through an approved client method."""

    result = _new_result(
        action="copy_capsule_to_droplet",
        request=request,
        message="Capsule copy to Droplet failed.",
    )

    try:
        _validate_droplet_connection_fields(request)
        selected_capsule = _coerce_path(capsule_file or request.capsule_file)
        _validate_capsule_file(selected_capsule)
        _copy_capsule_to_droplet(request, selected_capsule, result)

        result.ok = True
        result.message = "Capsule copied to Droplet."
        result.data.update(
            {
                "capsule_file": str(selected_capsule),
                "remote_capsule_path": str(
                    PurePosixPath(request.remote_capsule_dir) / selected_capsule.name
                ),
            }
        )
        return result
    except Exception as exc:
        result.ok = False
        result.message = str(exc)
        return result


def start_droplet_instance(request: DropletDeployRequest) -> DeployResult:
    """Start a remote Droplet instance through an approved client method."""

    result = _new_result(
        action="start_droplet_instance",
        request=request,
        message="Droplet instance start failed.",
    )

    try:
        _validate_common_request(request)
        _validate_droplet_connection_fields(request)
        _start_instance(request, result, remote=True)

        public_host = _public_host_for_request(request)

        result.ok = True
        result.message = "Droplet instance started."
        result.data.update(
            {
                "instance_id": request.instance_id,
                "droplet_host": request.droplet_host,
                "host": public_host,
                "public_host": public_host,
                "public_url": _host_to_https_url(public_host),
            }
        )
        return result
    except Exception as exc:
        result.ok = False
        result.message = str(exc)
        return result


def _run_deploy_flow(
    *,
    action: str,
    request: BaseDeployRequest,
    final_message: str,
    remote: bool,
) -> DeployResult:
    result = _new_result(
        action=action,
        request=request,
        message=f"{final_message.removesuffix('.')} failed.",
    )

    try:
        _validate_local_target_request(request)

        capsule_file = _prepare_capsule(request, result)
        _import_capsule(request, capsule_file, result, remote=remote)
        _create_or_update_instance(request, result, remote=remote)
        _set_network_profile(request, result, remote=remote)
        _run_security_gate(request, result, remote=remote)
        _start_instance(request, result, remote=remote)

        result.ok = True
        result.message = final_message
        result.data.update(_target_result_data(request, capsule_file))
        return result

    except Exception as exc:
        result.ok = False
        result.message = str(exc)
        return result


def _prepare_capsule(request: BaseDeployRequest, result: DeployResult) -> Path:
    if request.build:
        _build_capsule(request, result)

    capsule_file = _coerce_path(request.capsule_file)

    if capsule_file is None:
        capsule_file = _planned_capsule_path(request)
        request.capsule_file = capsule_file

    _validate_capsule_file(capsule_file)

    # Keep a stable capsule identity on the request for all later deploy steps.
    # Droplet deploy previously imported/created with the dated capsule id, then
    # later network/start calls omitted it and the Agent fell back to the generic
    # konnaxion-v14-demo id. That caused image loading to look in the wrong
    # capsule directory and kept the old frontend image running.
    if not str(request.capsule_id or "").strip():
        request.capsule_id = _capsule_id_from_path_value(capsule_file)

    if request.verify:
        _verify_capsule(capsule_file, result)
    else:
        _add_step(result, "verify_capsule", True, "Capsule verification skipped.")

    return capsule_file


def _build_capsule(request: BaseDeployRequest, result: DeployResult) -> None:
    if request.plan_only:
        _add_step(result, "build_capsule", True, "Capsule build planned.")
        return

    if request.builder_request is None:
        raise DeployValidationError(
            "builder_request is required when build=True. "
            "Create it in kx_manager.services.builder before calling deploy."
        )

    builder = _import_builder_service()
    build_func = _first_callable(
        builder,
        (
            "build_capsule",
            "build",
            "run_build",
        ),
    )

    if build_func is None:
        raise DeployExecutionError("No approved Builder build function is available.")

    value = _invoke_callable(build_func, request.builder_request)
    if not _object_ok(value):
        raise DeployExecutionError("Capsule build failed.")

    capsule_file = _get_attr_or_key(value, "capsule_file")
    if capsule_file:
        request.capsule_file = capsule_file

    _add_step(result, "build_capsule", True, "Capsule built.", _object_to_data(value))


def _verify_capsule(capsule_file: Path, result: DeployResult) -> None:
    if result.action == "deploy_droplet" and result.data.get("remote_capsule_path"):
        verify_target = result.data["remote_capsule_path"]
    else:
        verify_target = capsule_file

    if result.action and result.steps and result.steps[-1].name == "verify_capsule":
        return

    if result.action and any(step.name == "verify_capsule" for step in result.steps):
        return

    request_plan_only = False

    if result.action:
        request_plan_only = False

    if request_plan_only:
        _add_step(result, "verify_capsule", True, "Capsule verification planned.")
        return

    builder = _import_builder_service(required=False)
    verify_func = _first_callable(
        builder,
        (
            "verify_capsule",
            "verify",
            "run_verify",
        ),
    )

    if verify_func is None:
        raise DeployExecutionError("No approved Builder verify function is available.")

    value = _invoke_callable(verify_func, verify_target)
    if not _object_ok(value):
        raise DeployExecutionError("Capsule verification failed.")

    _add_step(
        result,
        "verify_capsule",
        True,
        "Capsule verified.",
        _object_to_data(value),
    )


def _require_current_droplet_agent_import_contract(
    request: DropletDeployRequest,
    result: DeployResult,
) -> None:
    """Fail early when the Droplet Agent is reachable but stale.

    Older Agent builds accept /v1/health but reject the current
    /v1/capsules/import payload fields: exposure_mode, verify, overwrite,
    and capsule_id. Those fields are required by the current Droplet flow
    because the Manager verifies locally, copies the artifact, then asks the
    remote Agent to import the already-copied capsule with verify=False and
    overwrite=True.

    The Manager service cannot safely infer the remote Pydantic schema from
    /v1/health alone, so it performs a best-effort /agent/info capability
    probe when the execution client exposes a private _get adapter. If the
    probe is unavailable, deploy continues and the import error formatter
    below turns stale-schema responses into a clear bootstrap message.
    """

    client = request.manager_client or request.agent_client
    getter = getattr(client, "_get", None)

    if not callable(getter):
        return

    try:
        info = getter("/agent/info")
    except Exception as exc:
        _add_step(
            result,
            "check_droplet_agent_contract",
            True,
            "Droplet Agent contract probe was skipped; import will validate compatibility.",
            {"warning": str(exc)},
        )
        return

    data = _object_to_data(info)

    if not _object_ok(info):
        _add_step(
            result,
            "check_droplet_agent_contract",
            True,
            "Droplet Agent /agent/info is unavailable; import will validate compatibility.",
            data,
        )
        return

    _add_step(
        result,
        "check_droplet_agent_contract",
        True,
        "Droplet Agent contract probe completed.",
        data,
    )


def _import_capsule(
    request: BaseDeployRequest,
    capsule_file: Path,
    result: DeployResult,
    *,
    remote: bool,
) -> None:
    capsule_path = str(capsule_file)

    if remote and result.data.get("remote_capsule_path"):
        capsule_path = str(result.data["remote_capsule_path"])

    payload = {
        "instance_id": request.instance_id,
        "capsule_file": capsule_path,
        "capsule_path": capsule_path,
        "remote_capsule_path": capsule_path if remote else None,
        "capsule_id": request.capsule_id,
        "capsule_version": request.capsule_version,
        "network_profile": _profile_for_request(request),
        "exposure_mode": _exposure_for_request(request),
        "remote": remote,
    }

    if remote:
        # The Manager already verified the local .kxcap before copying it.
        # Remote import should import the copied file, not fail on a second
        # stricter demo/default metadata verification pass.
        payload["verify"] = False
        payload["overwrite"] = True

    _call_backend_step(
        request,
        result,
        step_name="import_capsule",
        method_names=(
            "import_capsule",
            "capsules_import",
            "import_capsule_file",
        ),
        payload=payload,
        planned_message="Capsule import planned.",
        success_message="Capsule imported.",
    )


def _create_or_update_instance(
    request: BaseDeployRequest,
    result: DeployResult,
    *,
    remote: bool,
) -> None:
    target_mode = getattr(request, "target_mode")
    public_host = _public_host_for_request(request)

    payload = {
        "instance_id": request.instance_id,
        "capsule_id": request.capsule_id,
        "capsule_version": request.capsule_version,
        "target_mode": target_mode,
        "network_profile": _profile_for_request(request),
        "exposure_mode": _exposure_for_request(request),
        "remote": remote,
    }

    # Some approved clients accept host during create/update and use it to seed
    # env/profile generation. Keep the key absent for local/private targets.
    if public_host:
        payload["host"] = public_host
        payload["public_mode_enabled"] = target_mode in {
            TARGET_TEMPORARY_PUBLIC,
            TARGET_DROPLET,
        }

    if request.update_existing:
        _call_backend_step(
            request,
            result,
            step_name="update_instance",
            method_names=(
                "update_instance",
                "instances_update",
                "create_or_update_instance",
            ),
            payload=payload,
            planned_message="Instance update planned.",
            success_message="Instance updated.",
        )
        return

    _call_backend_step(
        request,
        result,
        step_name="create_instance",
        method_names=(
            "create_instance",
            "instances_create",
            "create_or_update_instance",
        ),
        payload=payload,
        planned_message="Instance creation planned.",
        success_message="Instance created.",
    )


def _set_network_profile(
    request: BaseDeployRequest,
    result: DeployResult,
    *,
    remote: bool,
) -> None:
    target_mode = getattr(request, "target_mode")
    public_mode_enabled = target_mode in {TARGET_TEMPORARY_PUBLIC, TARGET_DROPLET}
    host = _public_host_for_request(request)

    payload = {
        "instance_id": request.instance_id,
        "target_mode": target_mode,
        "network_profile": _profile_for_request(request),
        "exposure_mode": _exposure_for_request(request),
        "public_mode_enabled": public_mode_enabled,
        "public_mode_expires_at": _iso_or_none(
            getattr(request, "public_mode_expires_at", None)
        ),
        "remote": remote,
        **_active_capsule_payload(request, result, remote=remote),
    }

    # Agent network profile schema accepts host, not domain. Normalize all GUI
    # public-host inputs into host before the client sends the Agent request.
    if host:
        payload["host"] = host

    _call_backend_step(
        request,
        result,
        step_name="set_network_profile",
        method_names=(
            "set_network_profile",
            "network_set_profile",
            "set_profile",
        ),
        payload=payload,
        planned_message="Network profile update planned.",
        success_message="Network profile set.",
    )


def _run_security_gate(
    request: BaseDeployRequest,
    result: DeployResult,
    *,
    remote: bool,
) -> None:
    if not request.run_security_gate:
        _add_step(result, "run_security_check", True, "Security Gate skipped.")
        return

    payload = {
        "instance_id": request.instance_id,
        "remote": remote,
        **_active_capsule_payload(request, result, remote=remote),
    }

    _call_backend_step(
        request,
        result,
        step_name="run_security_check",
        method_names=(
            "run_security_check",
            "security_check",
            "check_security",
        ),
        payload=payload,
        planned_message="Security Gate check planned.",
        success_message="Security Gate check completed.",
    )


def _start_instance(
    request: BaseDeployRequest,
    result: DeployResult,
    *,
    remote: bool,
) -> None:
    if not request.start:
        _add_step(result, "start_instance", True, "Instance start skipped.")
        return

    payload = {
        "instance_id": request.instance_id,
        "remote": remote,
        "run_security_gate": request.run_security_gate,
        "force_recreate_after_image_load": True,
        **_active_capsule_payload(request, result, remote=remote),
    }

    _call_backend_step(
        request,
        result,
        step_name="start_instance",
        method_names=(
            "start_instance",
            "instances_start",
        ),
        payload=payload,
        planned_message="Instance start planned.",
        success_message="Instance started.",
    )


def _copy_capsule_to_droplet(
    request: DropletDeployRequest,
    capsule_file: Path,
    result: DeployResult,
) -> None:
    remote_capsule_path = str(
        PurePosixPath(request.remote_capsule_dir) / capsule_file.name
    )

    payload = {
        "capsule_file": str(capsule_file),
        "droplet_name": request.droplet_name,
        "droplet_host": request.droplet_host,
        "droplet_user": request.droplet_user,
        "ssh_key_path": str(request.ssh_key_path) if request.ssh_key_path else None,
        "ssh_port": request.ssh_port,
        "remote_capsule_dir": request.remote_capsule_dir,
        "remote_capsule_path": remote_capsule_path,
        "capsule_id": request.capsule_id or _capsule_id_from_path_value(capsule_file),
        "capsule_version": request.capsule_version,
    }

    _call_backend_step(
        request,
        result,
        step_name="copy_capsule_to_droplet",
        method_names=(
            "copy_capsule_to_droplet",
            "copy_capsule_to_remote",
            "deploy_copy_capsule",
        ),
        payload=payload,
        planned_message="Capsule copy to Droplet planned.",
        success_message="Capsule copied to Droplet.",
    )

    result.data["remote_capsule_path"] = remote_capsule_path


def _ensure_remote_runtime(
    request: DropletDeployRequest,
    result: DeployResult,
) -> None:
    payload = {
        "droplet_name": request.droplet_name,
        "droplet_host": request.droplet_host,
        "droplet_user": request.droplet_user,
        "ssh_key_path": str(request.ssh_key_path) if request.ssh_key_path else None,
        "ssh_port": request.ssh_port,
        "remote_kx_root": request.remote_kx_root,
        "remote_capsule_dir": request.remote_capsule_dir,
    }

    _call_backend_step(
        request,
        result,
        step_name="ensure_remote_runtime",
        method_names=(
            "ensure_remote_runtime",
            "ensure_droplet_runtime",
            "deploy_prepare_remote",
        ),
        payload=payload,
        planned_message="Remote runtime preparation planned.",
        success_message="Remote runtime prepared.",
    )


def _check_droplet_agent(
    request: DropletDeployRequest,
    result: DeployResult,
    *,
    required: bool = True,
) -> None:
    """Check Droplet Agent health.

    In Droplet mode, blank or loopback remote_agent_url values mean the Agent is
    private on 127.0.0.1:8765 inside the Droplet. The backend must then use
    SSH-local curl instead of direct HTTP from the Manager machine.

    A non-loopback remote_agent_url is allowed only when it points at the
    selected Droplet host.
    """

    payload: dict[str, Any] = {
        "droplet_name": request.droplet_name,
        "droplet_host": request.droplet_host,
        "droplet_user": request.droplet_user,
        "ssh_key_path": str(request.ssh_key_path) if request.ssh_key_path else None,
        "ssh_port": request.ssh_port,
        "remote_kx_root": request.remote_kx_root,
        "remote_capsule_dir": request.remote_capsule_dir,
    }

    direct_remote_agent_url = _direct_remote_agent_url(request)
    if direct_remote_agent_url:
        payload["remote_agent_url"] = direct_remote_agent_url
        payload["agent_health_url"] = _remote_agent_health_url(request)

    _call_backend_step(
        request,
        result,
        step_name="check_droplet_agent",
        method_names=(
            "check_droplet_agent",
            "check_remote_agent",
            "agent_health",
            "health",
        ),
        payload=payload,
        planned_message="Droplet Agent check planned.",
        success_message="Droplet Agent reachable.",
        required=required,
        non_required_message=(
            "Droplet Agent preflight check did not pass inside deploy flow; "
            "continuing because the explicit Check Droplet Agent step may have "
            "already succeeded and later Agent API steps will validate reachability."
        ),
    )


def _is_stale_agent_import_contract_error(
    *,
    step_name: str,
    data: Mapping[str, Any],
) -> bool:
    """Return True for old-Agent 422 schema errors on capsule import."""

    if step_name != "import_capsule":
        return False

    text = str(data).lower()
    required_markers = ("extra_forbidden", "extra inputs are not permitted")
    current_import_fields = (
        "exposure_mode",
        "verify",
        "overwrite",
        "capsule_id",
    )

    return (
        any(marker in text for marker in required_markers)
        and any(field in text for field in current_import_fields)
    )


def _stale_agent_import_contract_message() -> str:
    return (
        "Remote Droplet Agent is stale. It is reachable, but its "
        "/v1/capsules/import schema does not accept the current deploy "
        "fields: exposure_mode, verify, overwrite, and capsule_id. Run the "
        "Bootstrap Droplet Agent action from this local checkout, then rerun "
        "deploy_droplet. Do not manually patch files over SSH."
    )


def _call_backend_step(
    request: BaseDeployRequest,
    result: DeployResult,
    *,
    step_name: str,
    method_names: Iterable[str],
    payload: Mapping[str, Any],
    planned_message: str,
    success_message: str,
    required: bool = True,
    non_required_message: str | None = None,
) -> Any:
    if request.plan_only:
        _add_step(result, step_name, True, planned_message, dict(payload))
        return None

    client = request.manager_client or request.agent_client
    if client is None:
        raise DeployExecutionError(
            "manager_client or agent_client is required for deployment execution."
        )

    method = _first_callable(client, method_names)
    if method is None:
        raise DeployExecutionError(
            f"No approved client method found for deployment step: {step_name}"
        )

    try:
        value = _invoke_backend_method(method, payload)
    except Exception as exc:
        if not required:
            _add_step(
                result,
                step_name,
                True,
                non_required_message or f"{step_name} failed but deployment continues.",
                {
                    "warning": str(exc),
                    "payload": _safe_payload(payload),
                },
            )
            return None

        raise

    if not _object_ok(value):
        data = _object_to_data(value)

        if not required:
            _add_step(
                result,
                step_name,
                True,
                non_required_message or f"{step_name} failed but deployment continues.",
                {
                    "warning": data.get("message") or f"{step_name} returned not ok.",
                    "backend_result": data,
                    "payload": _safe_payload(payload),
                },
            )
            return value

        message = data.get("message") or f"Deployment step failed: {step_name}"

        if _is_stale_agent_import_contract_error(step_name=step_name, data=data):
            message = _stale_agent_import_contract_message()
            data = {
                **data,
                "stale_agent": True,
                "required_action": "bootstrap_droplet_agent",
                "rejected_fields": [
                    "exposure_mode",
                    "verify",
                    "overwrite",
                    "capsule_id",
                ],
            }

        _add_step(
            result,
            step_name,
            False,
            message,
            data,
        )
        raise DeployExecutionError(message)

    _add_step(result, step_name, True, success_message, _object_to_data(value))
    return value


def _invoke_backend_method(method: Any, payload: Mapping[str, Any]) -> Any:
    try:
        return method(**dict(payload))
    except TypeError:
        try:
            return method(dict(payload))
        except TypeError:
            instance_id = payload.get("instance_id")
            if instance_id is not None:
                return method(instance_id)
            raise


def _invoke_callable(method: Any, value: Any) -> Any:
    return method(value)


def _validate_local_target_request(request: BaseDeployRequest) -> None:
    _validate_common_request(request)

    target_mode = getattr(request, "target_mode", None)
    network_profile = getattr(request, "network_profile", None)
    exposure_mode = getattr(request, "exposure_mode", None)

    if target_mode == TARGET_LOCAL:
        if network_profile != PROFILE_LOCAL_ONLY:
            raise DeployValidationError("local target requires local_only profile.")
        if exposure_mode != EXPOSURE_PRIVATE:
            raise DeployValidationError("local target requires private exposure.")
        return

    if target_mode == TARGET_INTRANET:
        if network_profile != PROFILE_INTRANET_PRIVATE:
            raise DeployValidationError(
                "intranet target requires intranet_private profile."
            )
        if exposure_mode not in {EXPOSURE_PRIVATE, EXPOSURE_LAN}:
            raise DeployValidationError(
                "intranet target allows only private or lan exposure."
            )
        return

    if target_mode == TARGET_TEMPORARY_PUBLIC:
        if network_profile != PROFILE_PUBLIC_TEMPORARY:
            raise DeployValidationError(
                "temporary_public target requires public_temporary profile."
            )
        if exposure_mode != EXPOSURE_TEMPORARY_TUNNEL:
            raise DeployValidationError(
                "temporary_public target requires temporary_tunnel exposure."
            )
        if not getattr(request, "public_mode_expires_at", None):
            raise DeployValidationError(
                "temporary_public target requires public_mode_expires_at."
            )
        if not getattr(request, "confirmed", False):
            raise DeployValidationError(
                "temporary_public target requires explicit confirmation."
            )
        return

    raise DeployValidationError(f"Unsupported target mode: {target_mode}")


def _validate_droplet_request(request: DropletDeployRequest) -> None:
    _validate_common_request(request)
    _validate_droplet_connection_fields(request)

    if request.target_mode != TARGET_DROPLET:
        raise DeployValidationError("Droplet deployment requires droplet target mode.")
    if request.network_profile != PROFILE_PUBLIC_VPS:
        raise DeployValidationError("Droplet deployment requires public_vps profile.")
    if request.exposure_mode != EXPOSURE_PUBLIC:
        raise DeployValidationError("Droplet deployment requires public exposure.")
    if not request.confirmed:
        raise DeployValidationError(
            "Droplet deployment requires explicit operator confirmation."
        )
    if not str(request.domain or "").strip():
        raise DeployValidationError("Droplet deployment requires domain.")


def _validate_droplet_connection_fields(request: DropletDeployRequest) -> None:
    if not request.droplet_host:
        raise DeployValidationError("droplet_host is required.")

    if not request.droplet_user:
        raise DeployValidationError("droplet_user is required.")

    if not request.ssh_key_path:
        raise DeployValidationError("ssh_key_path is required.")

    ssh_key_path = _coerce_path(request.ssh_key_path)
    if ssh_key_path is None:
        raise DeployValidationError("ssh_key_path is required.")

    ssh_key_path = ssh_key_path.expanduser()
    if not ssh_key_path.exists() or not ssh_key_path.is_file():
        raise DeployValidationError("ssh_key_path must point to an existing file.")

    if not request.remote_kx_root:
        raise DeployValidationError("remote_kx_root is required.")

    if not request.remote_capsule_dir:
        raise DeployValidationError("remote_capsule_dir is required.")

    if not _remote_path_under(request.remote_capsule_dir, request.remote_kx_root):
        raise DeployValidationError("remote_capsule_dir must be under remote_kx_root.")

    if request.ssh_port < 1 or request.ssh_port > 65535:
        raise DeployValidationError("ssh_port must be between 1 and 65535.")


def _validate_common_request(request: BaseDeployRequest) -> None:
    if not request.instance_id:
        raise DeployValidationError("instance_id is required.")
    if not fullmatch(SAFE_INSTANCE_ID_RE, request.instance_id):
        raise DeployValidationError(f"Unsafe instance_id: {request.instance_id!r}")

    if request.capsule_file is not None:
        _validate_capsule_file(_coerce_path(request.capsule_file))

    if request.build and request.builder_request is None:
        if request.source_dir is None:
            raise DeployValidationError("source_dir is required when build=True.")
        if request.capsule_output_dir is None:
            raise DeployValidationError(
                "capsule_output_dir is required when build=True."
            )


def _validate_capsule_file(capsule_file: Path | None) -> None:
    if capsule_file is None:
        raise DeployValidationError("capsule_file is required.")
    if capsule_file.suffix != ".kxcap":
        raise DeployValidationError("capsule_file must end with .kxcap")


def _planned_capsule_path(request: BaseDeployRequest) -> Path:
    output_dir = _coerce_path(request.capsule_output_dir) or Path("runtime") / "capsules"
    capsule_id = request.capsule_id or "konnaxion-v14-demo"
    capsule_version = request.capsule_version or "latest"
    return output_dir / f"{capsule_id}-{capsule_version}.kxcap"


def _active_capsule_payload(
    request: BaseDeployRequest,
    result: DeployResult,
    *,
    remote: bool,
) -> dict[str, Any]:
    """Return capsule identity/path fields that must follow every deploy step.

    Instance create, network profile, Security Gate, and start must operate on
    the same capsule. In Droplet mode, the Agent should use the remote capsule
    path when available so image loading resolves to the extracted dated capsule
    directory instead of falling back to a generic capsule id.
    """

    local_capsule_file = _coerce_path(request.capsule_file)
    local_capsule_path = str(local_capsule_file) if local_capsule_file else ""
    remote_capsule_path = str(result.data.get("remote_capsule_path") or "").strip()
    capsule_path = remote_capsule_path if remote and remote_capsule_path else local_capsule_path

    capsule_id = str(request.capsule_id or "").strip()
    if not capsule_id:
        capsule_id = _capsule_id_from_path_value(capsule_path)

    payload: dict[str, Any] = {}

    if capsule_id:
        payload["capsule_id"] = capsule_id
    if request.capsule_version:
        payload["capsule_version"] = request.capsule_version
    if local_capsule_path:
        payload["capsule_file"] = local_capsule_path
    if capsule_path:
        payload["capsule_path"] = capsule_path
    if remote and remote_capsule_path:
        payload["remote_capsule_path"] = remote_capsule_path

    return payload


def _capsule_id_from_path_value(value: str | Path | PurePosixPath | None) -> str:
    """Infer a capsule id from a local or remote .kxcap path value."""

    raw = str(value or "").strip()
    if not raw:
        return ""

    # Remote paths use POSIX separators; Windows local paths may contain
    # backslashes. Normalize only enough to safely extract the filename stem.
    filename = raw.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
    if filename.endswith(".kxcap"):
        filename = filename[: -len(".kxcap")]

    return filename.strip()


def _target_result_data(
    request: BaseDeployRequest,
    capsule_file: Path,
) -> dict[str, Any]:
    target_mode = getattr(request, "target_mode")
    data: dict[str, Any] = {
        "target_mode": target_mode,
        "network_profile": _profile_for_request(request),
        "exposure_mode": _exposure_for_request(request),
        "capsule_file": str(capsule_file),
        "capsule_id": request.capsule_id or _capsule_id_from_path_value(capsule_file),
        "capsule_version": request.capsule_version,
    }

    if target_mode == TARGET_LOCAL:
        data["url"] = getattr(request, "url", DEFAULT_LOCAL_URL)
    elif target_mode == TARGET_INTRANET:
        host = getattr(request, "host", DEFAULT_INTRANET_HOST)
        data["host"] = host
        data["url"] = _host_to_https_url(host)
    elif target_mode == TARGET_TEMPORARY_PUBLIC:
        public_host = _public_host_for_request(request)
        data["host"] = public_host
        data["public_host"] = public_host
        data["url"] = _host_to_https_url(public_host)
        data["public_mode_expires_at"] = _iso_or_none(
            getattr(request, "public_mode_expires_at", None)
        )
    elif target_mode == TARGET_DROPLET:
        public_host = _public_host_for_request(request)
        data["host"] = public_host
        data["public_host"] = public_host
        data["url"] = _host_to_https_url(public_host)

    return data


def _profile_for_request(request: BaseDeployRequest) -> str:
    profile = getattr(request, "network_profile", None)
    if profile:
        return _enum_value(profile)

    target_mode = getattr(request, "target_mode")
    return {
        TARGET_LOCAL: PROFILE_LOCAL_ONLY,
        TARGET_INTRANET: PROFILE_INTRANET_PRIVATE,
        TARGET_TEMPORARY_PUBLIC: PROFILE_PUBLIC_TEMPORARY,
        TARGET_DROPLET: PROFILE_PUBLIC_VPS,
    }[target_mode]


def _exposure_for_request(request: BaseDeployRequest) -> str:
    exposure = getattr(request, "exposure_mode", None)
    if exposure:
        return _enum_value(exposure)

    target_mode = getattr(request, "target_mode")
    return {
        TARGET_LOCAL: EXPOSURE_PRIVATE,
        TARGET_INTRANET: EXPOSURE_PRIVATE,
        TARGET_TEMPORARY_PUBLIC: EXPOSURE_TEMPORARY_TUNNEL,
        TARGET_DROPLET: EXPOSURE_PUBLIC,
    }[target_mode]


def _public_host_for_request(request: BaseDeployRequest) -> str:
    """Return the canonical externally reachable host for public target modes.

    The Agent network profile API accepts `host`; it does not accept `domain`.
    GUI/domain/public-host inputs are normalized here so deployment does not
    generate runtime env/Traefik config with stale 127.0.0.1 values.
    """

    target_mode = getattr(request, "target_mode", None)

    candidates: tuple[Any, ...]
    if target_mode == TARGET_DROPLET:
        candidates = (
            getattr(request, "domain", None),
            getattr(request, "public_host", None),
            getattr(request, "host", None),
            getattr(request, "droplet_host", None),
        )
    elif target_mode == TARGET_TEMPORARY_PUBLIC:
        candidates = (
            getattr(request, "public_host", None),
            getattr(request, "domain", None),
            getattr(request, "host", None),
        )
    elif target_mode == TARGET_INTRANET:
        candidates = (getattr(request, "host", None),)
    else:
        candidates = ()

    for candidate in candidates:
        host = _normalize_host_value(candidate)
        if host:
            return host

    return ""


def _normalize_host_value(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""

    if raw.startswith(("http://", "https://")):
        parsed = urlparse(raw)
        return str(parsed.netloc or parsed.path or "").strip().rstrip("/")

    return raw.rstrip("/")


def _direct_remote_agent_url(request: DropletDeployRequest) -> str:
    """Return a safe direct Agent URL for Droplet mode, or blank for SSH transport.

    Loopback URLs such as http://127.0.0.1:18765/v1 refer to the Manager host,
    not the Droplet. In Droplet mode those are treated as stale tunnel URLs so
    deploy uses SSH-local curl against the Droplet's private Agent listener.
    """

    raw = str(request.remote_agent_url or "").strip()
    if not raw:
        return ""

    parsed = urlparse(raw)
    host = str(parsed.hostname or "").strip().lower()
    droplet_host = str(request.droplet_host or "").strip().lower()

    if not host:
        return ""

    # TEST-NET-3 documentation range; never a real customer Droplet target.
    if host.startswith("203.0.113."):
        return ""

    # Localhost on Windows is not the Droplet. Treat old tunnel URLs like
    # http://127.0.0.1:18765/v1 as stale and use SSH-local Agent transport.
    if host in {"127.0.0.1", "localhost", "::1"}:
        return ""

    # If the selected Droplet host and configured Agent URL disagree, prefer SSH.
    if droplet_host and host != droplet_host:
        return ""

    return raw


def _remote_agent_health_url(request: DropletDeployRequest) -> str | None:
    direct_url = _direct_remote_agent_url(request)
    if direct_url:
        base = direct_url.rstrip("/")
        return base if base.endswith("/health") else base + "/health"

    return DEFAULT_PRIVATE_AGENT_HEALTH_URL


def _agent_transport_for_request(request: DropletDeployRequest) -> str:
    return "http" if _direct_remote_agent_url(request) else "ssh"


def _remote_path_under(child: str, parent: str) -> bool:
    try:
        PurePosixPath(child).relative_to(PurePosixPath(parent))
        return True
    except ValueError:
        return False


def _host_to_https_url(host: str | None) -> str:
    if not host:
        return ""
    host = host.strip()
    if host.startswith(("http://", "https://")):
        return host
    return f"https://{host}"


def _coerce_path(value: str | Path | None) -> Path | None:
    if value is None:
        return None
    if isinstance(value, Path):
        return value
    return Path(value)


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value))


def _iso_or_none(value: str | datetime | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _new_result(
    *,
    action: str,
    request: BaseDeployRequest,
    message: str,
) -> DeployResult:
    return DeployResult(
        ok=False,
        action=action,
        instance_id=request.instance_id,
        message=message,
    )


def _add_step(
    result: DeployResult,
    name: str,
    ok: bool,
    message: str,
    data: Mapping[str, Any] | None = None,
) -> None:
    result.steps.append(
        DeployStep(
            name=name,
            ok=ok,
            message=message,
            data=dict(data or {}),
        )
    )


def _object_ok(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, bool):
        return value
    if isinstance(value, Mapping) and "ok" in value:
        return bool(value["ok"])
    if hasattr(value, "ok"):
        return bool(getattr(value, "ok"))
    if hasattr(value, "returncode"):
        return int(getattr(value, "returncode")) == 0
    return True


def _object_to_data(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    if is_dataclass(value):
        return asdict(value)
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "dict"):
        return value.dict()
    return {"result": repr(value)}


def _safe_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    data = dict(payload)

    for key in ("ssh_key_path", "token", "password", "secret"):
        if key in data and data[key]:
            data[key] = "<redacted>"

    return data


def _get_attr_or_key(value: Any, key: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(key)
    return getattr(value, key, None)


def _first_callable(source: Any, names: Iterable[str]) -> Any | None:
    if source is None:
        return None

    for name in names:
        candidate = getattr(source, name, None)
        if callable(candidate):
            return candidate

    return None


def _import_builder_service(*, required: bool = True) -> Any | None:
    try:
        from kx_manager.services import builder
    except Exception as exc:
        if required:
            raise DeployExecutionError("Builder service is not available.") from exc
        return None

    return builder


__all__ = [
    "DeployValidationError",
    "DeployExecutionError",
    "DeployStep",
    "DeployResult",
    "BaseDeployRequest",
    "LocalDeployRequest",
    "IntranetDeployRequest",
    "TemporaryPublicDeployRequest",
    "DropletDeployRequest",
    "deploy_local",
    "deploy_intranet",
    "deploy_temporary_public",
    "deploy_droplet",
    "check_droplet_agent",
    "copy_capsule_to_droplet",
    "start_droplet_instance",
]

