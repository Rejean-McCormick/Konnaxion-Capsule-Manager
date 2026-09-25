"""Target-mode and deployment form parsing for the Konnaxion Manager GUI."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

from kx_manager.defaults import (
    DEFAULT_DROPLET_DOMAIN,
    DEFAULT_DROPLET_HOST,
    DEFAULT_DROPLET_INSTANCE_ID,
    DEFAULT_DROPLET_NAME,
    DEFAULT_DROPLET_USER,
    DEFAULT_REMOTE_AGENT_URL,
    DEFAULT_REMOTE_CAPSULE_DIR,
    DEFAULT_REMOTE_KX_ROOT,
    DEFAULT_SSH_KEY_PATH,
    DEFAULT_SSH_PORT,
)

from kx_manager.services.targets import (
    DropletTargetConfig,
    TargetConfig,
    TargetMode,
    exposure_mode_for_target,
    network_profile_for_target,
    validate_target_config,
)
from kx_manager.ui.form_constants import (
    DEFAULT_CAPSULE_ID,
    DEFAULT_CAPSULE_OUTPUT_DIR,
    DEFAULT_CAPSULE_VERSION,
    DEFAULT_RUNTIME_ROOT,
    DEFAULT_SOURCE_DIR,
    ExposureMode,
    NetworkProfile,
)
from kx_manager.ui.form_errors import FormValidationError
from kx_manager.ui.form_helpers import (
    _absolute_posix_path,
    _bool,
    _capsule_file,
    _capsule_id,
    _capsule_version,
    _coerce_enum,
    _exposure_mode,
    _host,
    _instance_id,
    _int,
    _iso_datetime,
    _network_profile,
    _path,
    _payload,
    _raw,
    _reject_droplet_fields,
    _remote_capsule_dir_under_root,
    _target_mode,
    _text,
    normalize_form_data,
)


DROPLET_TARGET_ACTION: str = "set_target_droplet"

DROPLET_OPERATION_ACTIONS: frozenset[str] = frozenset(
    {
        "deploy_droplet",
        "bootstrap_droplet_agent",
        "check_droplet_agent",
        "initialize_production_data",
        "copy_capsule_to_droplet",
        "start_droplet_instance",
    }
)

DROPLET_ACTIONS: frozenset[str] = frozenset(
    {
        DROPLET_TARGET_ACTION,
        *DROPLET_OPERATION_ACTIONS,
    }
)

DROPLET_CAPSULE_REQUIRED_ACTIONS: frozenset[str] = frozenset(
    {
        "deploy_droplet",
        "copy_capsule_to_droplet",
        "start_droplet_instance",
    }
)

DROPLET_NON_CAPSULE_ACTIONS: frozenset[str] = frozenset(
    {
        "bootstrap_droplet_agent",
        "check_droplet_agent",
        "initialize_production_data",
    }
)



@dataclass(frozen=True, slots=True)
class TargetModeForm:
    target_mode: TargetMode
    instance_id: str
    network_profile: Any
    exposure_mode: Any
    runtime_root: str
    capsule_dir: str
    source_dir: Path | None = None
    capsule_output_dir: Path | None = None
    host: str | None = None
    public_mode_expires_at: str | None = None
    confirmed: bool = False

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "TargetModeForm":
        normalized = normalize_form_data(data)

        target_mode = _target_mode(normalized)
        default_profile = network_profile_for_target(target_mode)
        default_exposure = exposure_mode_for_target(target_mode)

        network_profile = _coerce_enum(
            NetworkProfile,
            _raw(
                normalized,
                "network_profile",
                "profile",
                default=default_profile.value,
            ),
            "network_profile",
        )
        exposure_mode = _coerce_enum(
            ExposureMode,
            _raw(normalized, "exposure_mode", default=default_exposure.value),
            "exposure_mode",
        )

        if network_profile != default_profile:
            raise FormValidationError(
                f"{target_mode.value} target requires "
                f"network_profile={default_profile.value}.",
                field="network_profile",
            )

        if target_mode == TargetMode.INTRANET:
            allowed_intranet_exposure = {
                _coerce_enum(ExposureMode, "private", "exposure_mode"),
                _coerce_enum(ExposureMode, "lan", "exposure_mode"),
            }
            if exposure_mode not in allowed_intranet_exposure:
                raise FormValidationError(
                    "intranet target allows only private or lan exposure.",
                    field="exposure_mode",
                )
        elif exposure_mode != default_exposure:
            raise FormValidationError(
                f"{target_mode.value} target requires "
                f"exposure_mode={default_exposure.value}.",
                field="exposure_mode",
            )

        public_mode_expires_at = _iso_datetime(
            normalized,
            "public_mode_expires_at",
            required=target_mode == TargetMode.TEMPORARY_PUBLIC,
        )
        confirmed = _bool(normalized, "confirmed", default=False)

        if target_mode == TargetMode.TEMPORARY_PUBLIC and not confirmed:
            raise FormValidationError(
                "temporary_public target requires explicit confirmation.",
                field="confirmed",
            )

        if target_mode in {
            TargetMode.LOCAL,
            TargetMode.INTRANET,
            TargetMode.TEMPORARY_PUBLIC,
        }:
            _reject_droplet_fields(normalized, target_mode.value)

        runtime_root = _text(
            normalized,
            "runtime_root",
            "target_runtime_root",
            default=DEFAULT_RUNTIME_ROOT,
            required=True,
            field="runtime_root",
        )
        assert runtime_root is not None

        capsule_dir = _text(
            normalized,
            "capsule_dir",
            "target_capsule_dir",
            default=str(Path(runtime_root) / "capsules"),
            required=True,
            field="capsule_dir",
        )
        assert capsule_dir is not None

        form = cls(
            target_mode=target_mode,
            instance_id=_instance_id(normalized),
            network_profile=network_profile,
            exposure_mode=exposure_mode,
            runtime_root=runtime_root,
            capsule_dir=capsule_dir,
            source_dir=_path(
                normalized,
                "source_dir",
                default=DEFAULT_SOURCE_DIR or None,
                required=False,
                must_exist=True,
                must_be_dir=True,
                field="source_dir",
            ),
            capsule_output_dir=_path(
                normalized,
                "capsule_output_dir",
                "output_dir",
                default=DEFAULT_CAPSULE_OUTPUT_DIR,
                required=False,
                must_be_dir=True,
                field="capsule_output_dir",
            ),
            host=_host(
                normalized,
                "host",
                "target_host",
                "private_host",
                "public_host",
                required=False,
                field="host",
            ),
            public_mode_expires_at=public_mode_expires_at,
            confirmed=confirmed,
        )

        validate_target_config(form.to_target_config())
        return form

    def to_target_config(self) -> TargetConfig:
        return TargetConfig(
            target_mode=self.target_mode,
            network_profile=self.network_profile,
            exposure_mode=self.exposure_mode,
            instance_id=self.instance_id,
            runtime_root=self.runtime_root,
            capsule_dir=self.capsule_dir,
            host=self.host,
            public_mode_expires_at=self.public_mode_expires_at,
            confirmed=self.confirmed,
        )

    def to_payload(self) -> dict[str, Any]:
        return _payload(self)


@dataclass(frozen=True, slots=True)
class LocalTargetForm(TargetModeForm):
    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "LocalTargetForm":
        merged = normalize_form_data(data)
        merged["target_mode"] = TargetMode.LOCAL.value
        merged["network_profile"] = "local_only"
        merged["exposure_mode"] = "private"

        base = TargetModeForm.from_mapping(merged)
        return cls(**asdict(base))


@dataclass(frozen=True, slots=True)
class IntranetTargetForm(TargetModeForm):
    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "IntranetTargetForm":
        merged = normalize_form_data(data)
        merged["target_mode"] = TargetMode.INTRANET.value
        merged["network_profile"] = "intranet_private"

        if "exposure_mode" not in merged or not merged["exposure_mode"]:
            merged["exposure_mode"] = "private"

        base = TargetModeForm.from_mapping(merged)
        return cls(**asdict(base))


@dataclass(frozen=True, slots=True)
class TemporaryPublicTargetForm(TargetModeForm):
    public_host: str | None = None

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "TemporaryPublicTargetForm":
        merged = normalize_form_data(data)
        merged["target_mode"] = TargetMode.TEMPORARY_PUBLIC.value
        merged["network_profile"] = "public_temporary"
        merged["exposure_mode"] = "temporary_tunnel"

        public_host = _host(
            merged,
            "public_host",
            "host",
            required=True,
            field="public_host",
        )

        base = TargetModeForm.from_mapping(merged)

        return cls(
            **asdict(base),
            public_host=public_host,
        )

    def to_payload(self) -> dict[str, Any]:
        payload = _payload(self)

        if self.public_host:
            payload.setdefault("host", self.public_host)
            payload.setdefault("public_host", self.public_host)

        return payload


@dataclass(frozen=True, slots=True)
class DropletTargetForm:
    target_mode: TargetMode
    instance_id: str
    source_dir: Path | None
    capsule_file: Path | None
    network_profile: Any
    exposure_mode: Any
    droplet_name: str
    droplet_host: str
    droplet_user: str
    ssh_key_path: Path
    ssh_port: int
    remote_kx_root: str
    remote_capsule_dir: str
    domain: str
    remote_agent_url: str | None = None
    confirmed: bool = False

    # Operation/test-visible aliases.
    action: str = DROPLET_TARGET_ACTION
    host: str | None = None
    runtime_root: str | None = None
    capsule_dir: str | None = None
    public_mode_enabled: bool = True
    public_mode_expires_at: str | None = None
    background_job: bool = False
    copy_capsule: bool = True
    one_click_release: bool = False

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "DropletTargetForm":
        normalized = _normalize_droplet_mapping(data)
        action = _canonical_action(normalized) or DROPLET_TARGET_ACTION
        is_operation = action in DROPLET_OPERATION_ACTIONS

        target_mode = _target_mode(
            normalized,
            default=TargetMode.DROPLET.value,
        )

        if target_mode != TargetMode.DROPLET:
            raise FormValidationError(
                "droplet form requires target_mode=droplet.",
                field="target_mode",
            )

        network_profile = _network_profile(
            normalized,
            default="public_vps",
        )
        exposure_mode = _exposure_mode(
            normalized,
            default="public",
        )

        if network_profile != _coerce_enum(
            NetworkProfile,
            "public_vps",
            "network_profile",
        ):
            raise FormValidationError(
                "droplet target requires network_profile=public_vps.",
                field="network_profile",
            )

        if exposure_mode != _coerce_enum(
            ExposureMode,
            "public",
            "exposure_mode",
        ):
            raise FormValidationError(
                "droplet target requires exposure_mode=public.",
                field="exposure_mode",
            )

        confirmed = _bool(normalized, "confirmed", default=False)
        if not confirmed:
            raise FormValidationError(
                "droplet target requires explicit confirmation.",
                field="confirmed",
            )

        droplet_name = _text(
            normalized,
            "droplet_name",
            default=DEFAULT_DROPLET_NAME,
            required=True,
            field="droplet_name",
        )
        droplet_host = _host(
            normalized,
            "droplet_host",
            "target_host",
            "host",
            required=True,
            field="droplet_host",
        )
        droplet_user = _text(
            normalized,
            "droplet_user",
            "ssh_user",
            "user",
            default=DEFAULT_DROPLET_USER,
            required=True,
            field="droplet_user",
        )

        assert droplet_name is not None
        assert droplet_host is not None
        assert droplet_user is not None

        ssh_key_path = _path(
            normalized,
            "ssh_key_path",
            "ssh_key",
            "droplet_ssh_key",
            required=True,
            must_exist=not is_operation,
            must_be_file=not is_operation,
            field="ssh_key_path",
        )
        assert ssh_key_path is not None

        remote_kx_root_raw = _text(
            normalized,
            "remote_kx_root",
            "runtime_root",
            "remote_root",
            "droplet_kx_root",
            default=DEFAULT_REMOTE_KX_ROOT,
            required=True,
            field="remote_kx_root",
        )
        remote_capsule_dir_raw = _text(
            normalized,
            "remote_capsule_dir",
            "capsule_dir",
            "target_capsule_dir",
            "droplet_capsule_dir",
            default=DEFAULT_REMOTE_CAPSULE_DIR,
            required=True,
            field="remote_capsule_dir",
        )

        assert remote_kx_root_raw is not None
        assert remote_capsule_dir_raw is not None

        remote_kx_root = _absolute_posix_path(
            remote_kx_root_raw,
            "remote_kx_root",
        )
        remote_capsule_dir = _absolute_posix_path(
            remote_capsule_dir_raw,
            "remote_capsule_dir",
        )
        _remote_capsule_dir_under_root(remote_kx_root, remote_capsule_dir)

        domain = _host(
            normalized,
            "domain",
            "droplet_domain",
            "public_host",
            required=True,
            field="domain",
        )
        assert domain is not None

        remote_agent_url = _text(
            normalized,
            "remote_agent_url",
            "droplet_agent_url",
            required=False,
        )

        form = cls(
            target_mode=target_mode,
            instance_id=_instance_id(normalized),
            source_dir=_path(
                normalized,
                "source_dir",
                default=DEFAULT_SOURCE_DIR or None,
                required=False,
                must_exist=True,
                must_be_dir=True,
                field="source_dir",
            ),
            capsule_file=_capsule_file(
                normalized,
                "capsule_file",
                "capsule_path",
                required=False,
                must_exist=False,
                field="capsule_file",
            ),
            network_profile=network_profile,
            exposure_mode=exposure_mode,
            droplet_name=droplet_name,
            droplet_host=droplet_host,
            droplet_user=droplet_user,
            ssh_key_path=ssh_key_path,
            ssh_port=_int(
                normalized,
                "ssh_port",
                default=DEFAULT_SSH_PORT,
                minimum=1,
                maximum=65535,
            ),
            remote_kx_root=remote_kx_root,
            remote_capsule_dir=remote_capsule_dir,
            domain=domain,
            remote_agent_url=remote_agent_url,
            confirmed=confirmed,
            action=action,
            host=droplet_host,
            runtime_root=remote_kx_root,
            capsule_dir=remote_capsule_dir,
            public_mode_enabled=True,
            public_mode_expires_at=None,
            background_job=_bool(normalized, "background_job", default=False),
            copy_capsule=_bool(normalized, "copy_capsule", default=True),
            one_click_release=_bool(normalized, "one_click_release", default=False),
        )

        # Target configuration requires an existing local SSH key at target-set
        # time. Operation forms validate payload shape only; execution later
        # reports SSH/runtime failures from the backend.
        if not is_operation:
            validate_target_config(form.to_target_config())

        return form

    def to_target_config(self) -> DropletTargetConfig:
        return DropletTargetConfig(
            target_mode=self.target_mode,
            network_profile=self.network_profile,
            exposure_mode=self.exposure_mode,
            instance_id=self.instance_id,
            runtime_root=self.remote_kx_root,
            capsule_dir=self.remote_capsule_dir,
            host=self.droplet_host,
            public_mode_expires_at=None,
            confirmed=self.confirmed,
            droplet_name=self.droplet_name,
            droplet_host=self.droplet_host,
            droplet_user=self.droplet_user,
            ssh_key_path=self.ssh_key_path,
            remote_kx_root=self.remote_kx_root,
            remote_capsule_dir=self.remote_capsule_dir,
            domain=self.domain,
            remote_agent_url=self.remote_agent_url,
            ssh_port=self.ssh_port,
        )

    def to_payload(self) -> dict[str, Any]:
        payload = _payload(self)

        payload["action"] = self.action
        payload["target_mode"] = TargetMode.DROPLET.value
        payload["network_profile"] = "public_vps"
        payload["exposure_mode"] = "public"
        payload["public_mode_enabled"] = True
        payload["public_mode_expires_at"] = None

        payload["host"] = self.droplet_host
        payload["target_host"] = self.droplet_host
        payload["runtime_root"] = self.remote_kx_root
        payload["capsule_dir"] = self.remote_capsule_dir
        payload["remote_root"] = self.remote_kx_root
        payload["droplet_kx_root"] = self.remote_kx_root
        payload["droplet_capsule_dir"] = self.remote_capsule_dir
        payload["droplet_domain"] = self.domain

        if self.capsule_file is not None:
            capsule_file = str(self.capsule_file)
            payload["capsule_file"] = capsule_file
            payload["capsule_path"] = capsule_file
        else:
            payload.pop("capsule_file", None)
            payload.pop("capsule_path", None)

        if self.source_dir is not None:
            payload["source_dir"] = str(self.source_dir)

        payload["ssh_key_path"] = str(self.ssh_key_path)
        payload["ssh_user"] = self.droplet_user
        payload["user"] = self.droplet_user
        payload["ssh_port"] = self.ssh_port
        payload["confirmed"] = self.confirmed

        return {
            key: value
            for key, value in payload.items()
            if value is not None
        }


@dataclass(frozen=True, slots=True)
class DeployLocalForm(LocalTargetForm):
    capsule_id: str = DEFAULT_CAPSULE_ID
    capsule_version: str = DEFAULT_CAPSULE_VERSION

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "DeployLocalForm":
        normalized = normalize_form_data(data)
        base = LocalTargetForm.from_mapping(normalized)

        return cls(
            **asdict(base),
            capsule_id=_capsule_id(normalized),
            capsule_version=_capsule_version(normalized),
        )


@dataclass(frozen=True, slots=True)
class DeployIntranetForm(IntranetTargetForm):
    capsule_id: str = DEFAULT_CAPSULE_ID
    capsule_version: str = DEFAULT_CAPSULE_VERSION

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "DeployIntranetForm":
        normalized = normalize_form_data(data)
        base = IntranetTargetForm.from_mapping(normalized)

        return cls(
            **asdict(base),
            capsule_id=_capsule_id(normalized),
            capsule_version=_capsule_version(normalized),
        )


@dataclass(frozen=True, slots=True)
class DeployDropletForm(DropletTargetForm):
    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "DeployDropletForm":
        normalized = _normalize_droplet_mapping(data)
        normalized["action"] = "deploy_droplet"
        base = DropletTargetForm.from_mapping(normalized)

        if base.capsule_file is None and not base.one_click_release:
            raise FormValidationError(
                "deploy_droplet requires capsule_file unless one_click_release=true.",
                field="capsule_file",
            )

        return cls(**asdict(base))


@dataclass(frozen=True, slots=True)
class BootstrapDropletAgentForm(DropletTargetForm):
    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "BootstrapDropletAgentForm":
        normalized = _normalize_droplet_mapping(data)
        normalized["action"] = "bootstrap_droplet_agent"
        normalized.pop("capsule_file", None)
        normalized.pop("capsule_path", None)
        base = DropletTargetForm.from_mapping(normalized)
        return cls(**asdict(base))


@dataclass(frozen=True, slots=True)
class CheckDropletAgentForm(DropletTargetForm):
    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "CheckDropletAgentForm":
        normalized = _normalize_droplet_mapping(data)
        normalized["action"] = "check_droplet_agent"
        normalized.pop("capsule_file", None)
        normalized.pop("capsule_path", None)
        base = DropletTargetForm.from_mapping(normalized)
        return cls(**asdict(base))


@dataclass(frozen=True, slots=True)
class CopyCapsuleToDropletForm(DropletTargetForm):
    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "CopyCapsuleToDropletForm":
        normalized = _normalize_droplet_mapping(data)
        normalized["action"] = "copy_capsule_to_droplet"
        base = DropletTargetForm.from_mapping(normalized)

        if base.capsule_file is None:
            raise FormValidationError(
                "copy_capsule_to_droplet requires capsule_file.",
                field="capsule_file",
            )

        return cls(**asdict(base))


@dataclass(frozen=True, slots=True)
class StartDropletInstanceForm(DropletTargetForm):
    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "StartDropletInstanceForm":
        normalized = _normalize_droplet_mapping(data)
        normalized["action"] = "start_droplet_instance"
        base = DropletTargetForm.from_mapping(normalized)

        if base.capsule_file is None:
            raise FormValidationError(
                "start_droplet_instance requires capsule_file.",
                field="capsule_file",
            )

        return cls(**asdict(base))


def parse_target_form(data: Mapping[str, Any]) -> Any:
    normalized = normalize_form_data(data)
    action = _form_action(normalized)

    if action in DROPLET_OPERATION_ACTIONS:
        return parse_droplet_operation_form(normalized)

    target_mode = _target_mode(normalized)

    if target_mode == TargetMode.LOCAL:
        return LocalTargetForm.from_mapping(normalized)

    if target_mode == TargetMode.INTRANET:
        return IntranetTargetForm.from_mapping(normalized)

    if target_mode == TargetMode.TEMPORARY_PUBLIC:
        return TemporaryPublicTargetForm.from_mapping(normalized)

    if target_mode == TargetMode.DROPLET:
        return DropletTargetForm.from_mapping(normalized)

    raise FormValidationError(
        f"Unsupported target mode: {target_mode!r}",
        field="target_mode",
    )


def parse_droplet_operation_form(data: Mapping[str, Any]) -> DropletTargetForm:
    normalized = _normalize_droplet_mapping(data)
    action = _form_action(normalized)

    if action == "deploy_droplet":
        return DeployDropletForm.from_mapping(normalized)

    if action == "bootstrap_droplet_agent":
        return BootstrapDropletAgentForm.from_mapping(normalized)

    if action == "check_droplet_agent":
        return CheckDropletAgentForm.from_mapping(normalized)

    if action == "copy_capsule_to_droplet":
        return CopyCapsuleToDropletForm.from_mapping(normalized)

    if action == "start_droplet_instance":
        return StartDropletInstanceForm.from_mapping(normalized)

    return DropletTargetForm.from_mapping(normalized)


def _form_action(data: Mapping[str, Any]) -> str:
    return _canonical_action(data)


def _canonical_action(data: Mapping[str, Any]) -> str:
    return str(data.get("action") or "").strip().replace("-", "_")


def _force_droplet_operation_values(data: dict[str, Any]) -> None:
    data["target_mode"] = TargetMode.DROPLET.value
    data["network_profile"] = "public_vps"
    data["exposure_mode"] = "public"
    data["confirmed"] = True


def _normalize_droplet_mapping(data: Mapping[str, Any]) -> dict[str, Any]:
    normalized = normalize_form_data(data)
    action = _canonical_action(normalized)

    if action:
        normalized["action"] = action

    force_droplet_values = action in DROPLET_OPERATION_ACTIONS

    if normalized.get("droplet_host") and not normalized.get("host"):
        normalized["host"] = normalized["droplet_host"]
    if normalized.get("host") and not normalized.get("droplet_host"):
        normalized["droplet_host"] = normalized["host"]
    if normalized.get("target_host") and not normalized.get("droplet_host"):
        normalized["droplet_host"] = normalized["target_host"]

    if normalized.get("ssh_user") and not normalized.get("droplet_user"):
        normalized["droplet_user"] = normalized["ssh_user"]
    if normalized.get("user") and not normalized.get("droplet_user"):
        normalized["droplet_user"] = normalized["user"]

    if normalized.get("ssh_key") and not normalized.get("ssh_key_path"):
        normalized["ssh_key_path"] = normalized["ssh_key"]
    if normalized.get("droplet_ssh_key") and not normalized.get("ssh_key_path"):
        normalized["ssh_key_path"] = normalized["droplet_ssh_key"]

    if normalized.get("runtime_root") and not normalized.get("remote_kx_root"):
        normalized["remote_kx_root"] = normalized["runtime_root"]
    if normalized.get("remote_root") and not normalized.get("remote_kx_root"):
        normalized["remote_kx_root"] = normalized["remote_root"]
    if normalized.get("droplet_kx_root") and not normalized.get("remote_kx_root"):
        normalized["remote_kx_root"] = normalized["droplet_kx_root"]

    if normalized.get("capsule_dir") and not normalized.get("remote_capsule_dir"):
        normalized["remote_capsule_dir"] = normalized["capsule_dir"]
    if normalized.get("target_capsule_dir") and not normalized.get("remote_capsule_dir"):
        normalized["remote_capsule_dir"] = normalized["target_capsule_dir"]
    if normalized.get("droplet_capsule_dir") and not normalized.get("remote_capsule_dir"):
        normalized["remote_capsule_dir"] = normalized["droplet_capsule_dir"]

    if normalized.get("domain") and not normalized.get("droplet_domain"):
        normalized["droplet_domain"] = normalized["domain"]
    if normalized.get("droplet_domain") and not normalized.get("domain"):
        normalized["domain"] = normalized["droplet_domain"]
    if normalized.get("public_host") and not normalized.get("domain"):
        normalized["domain"] = normalized["public_host"]

    if normalized.get("droplet_agent_url") and not normalized.get("remote_agent_url"):
        normalized["remote_agent_url"] = normalized["droplet_agent_url"]

    if action not in DROPLET_NON_CAPSULE_ACTIONS:
        if normalized.get("capsule_path") and not normalized.get("capsule_file"):
            normalized["capsule_file"] = normalized["capsule_path"]
        if normalized.get("capsule_file") and not normalized.get("capsule_path"):
            normalized["capsule_path"] = normalized["capsule_file"]
    else:
        normalized.pop("capsule_file", None)
        normalized.pop("capsule_path", None)

    normalized.setdefault("instance_id", DEFAULT_DROPLET_INSTANCE_ID)
    normalized.setdefault("droplet_name", DEFAULT_DROPLET_NAME)
    normalized.setdefault("droplet_host", DEFAULT_DROPLET_HOST)
    normalized.setdefault("droplet_user", DEFAULT_DROPLET_USER)
    normalized.setdefault("ssh_key_path", DEFAULT_SSH_KEY_PATH)
    normalized.setdefault("remote_kx_root", DEFAULT_REMOTE_KX_ROOT)
    normalized.setdefault("remote_capsule_dir", DEFAULT_REMOTE_CAPSULE_DIR)
    normalized.setdefault("domain", DEFAULT_DROPLET_DOMAIN)
    normalized.setdefault("remote_agent_url", DEFAULT_REMOTE_AGENT_URL)
    normalized.setdefault("ssh_port", DEFAULT_SSH_PORT)

    if force_droplet_values:
        _force_droplet_operation_values(normalized)

    return normalized


__all__ = [
    "BootstrapDropletAgentForm",
    "CheckDropletAgentForm",
    "CopyCapsuleToDropletForm",
    "DeployDropletForm",
    "DeployIntranetForm",
    "DeployLocalForm",
    "DropletTargetForm",
    "DROPLET_ACTIONS",
    "DROPLET_CAPSULE_REQUIRED_ACTIONS",
    "DROPLET_NON_CAPSULE_ACTIONS",
    "DROPLET_OPERATION_ACTIONS",
    "DROPLET_TARGET_ACTION",
    "IntranetTargetForm",
    "LocalTargetForm",
    "StartDropletInstanceForm",
    "TargetModeForm",
    "TemporaryPublicTargetForm",
    "parse_droplet_operation_form",
    "parse_target_form",
]