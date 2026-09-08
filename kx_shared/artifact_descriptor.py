"""Public installable-artifact descriptor contract for Konnaxion Capsule.

Capsule owns installation/lifecycle/discovery metadata. Product UX remains owned
by the product, and composition hosts such as kOA Spaces only consume public
integration manifests referenced by this descriptor.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path, PurePosixPath
import re
from typing import Any, Mapping, Sequence

try:
    import yaml
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("PyYAML is required for artifact descriptors") from exc


ARTIFACT_DESCRIPTOR_SCHEMA_VERSION = "kx-artifact-descriptor/v1"
ARTIFACT_DESCRIPTOR_FILENAME = "artifact.yaml"
SOURCE_ARTIFACT_DESCRIPTOR_FILENAME = "capsule-artifact.yaml"

_SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9_.-]*$")


class ArtifactDescriptorError(ValueError):
    """Raised when an installable artifact descriptor is invalid."""


class ArtifactKind(StrEnum):
    LIBRARY = "library"
    PRODUCT = "product"
    COMPOSITION_HOST = "composition_host"


class DependencyInterface(StrEnum):
    PUBLIC = "public"
    PRIVATE = "private"


@dataclass(frozen=True, slots=True)
class ArtifactDependency:
    id: str
    version: str | None = None
    interface: DependencyInterface = DependencyInterface.PUBLIC

    @classmethod
    def from_value(cls, value: str | Mapping[str, Any]) -> "ArtifactDependency":
        if isinstance(value, str):
            return cls(id=_validate_id(value, "dependencies.required[].id"))
        if not isinstance(value, Mapping):
            raise ArtifactDescriptorError("dependencies.required entries must be strings or objects")
        dep_id = _validate_id(str(value.get("id") or ""), "dependencies.required[].id")
        interface_raw = str(value.get("interface") or DependencyInterface.PUBLIC.value)
        try:
            interface = DependencyInterface(interface_raw)
        except ValueError as exc:
            raise ArtifactDescriptorError(
                f"dependencies.required[{dep_id}].interface must be public or private"
            ) from exc
        if interface is DependencyInterface.PRIVATE:
            raise ArtifactDescriptorError(
                f"private cross-artifact dependency is forbidden: {dep_id}; depend on a public contract/capability instead"
            )
        version = str(value.get("version") or "").strip() or None
        return cls(id=dep_id, version=version, interface=interface)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"id": self.id, "interface": self.interface.value}
        if self.version:
            data["version"] = self.version
        return data


@dataclass(frozen=True, slots=True)
class OptionalIntegration:
    product: str
    capability: str | None = None

    @classmethod
    def from_value(cls, value: str | Mapping[str, Any]) -> "OptionalIntegration":
        if isinstance(value, str):
            return cls(product=_validate_id(value, "dependencies.optional_integrations[].product"))
        if not isinstance(value, Mapping):
            raise ArtifactDescriptorError("optional integration entries must be strings or objects")
        product = _validate_id(
            str(value.get("product") or value.get("id") or ""),
            "dependencies.optional_integrations[].product",
        )
        capability = str(value.get("capability") or "").strip() or None
        return cls(product=product, capability=capability)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"product": self.product}
        if self.capability:
            data["capability"] = self.capability
        return data


@dataclass(frozen=True, slots=True)
class ArtifactDescriptor:
    artifact_id: str
    version: str
    kind: ArtifactKind
    removable: bool = True
    standalone: bool = False
    backend_entrypoint: str | None = None
    backend_healthcheck: str | None = None
    standalone_ui_enabled: bool = False
    standalone_ui_entrypoint: str | None = None
    integrated_ui_enabled: bool = False
    integrated_manifest: str | None = None
    integrated_contract: str | None = None
    required_dependencies: tuple[ArtifactDependency, ...] = field(default_factory=tuple)
    optional_integrations: tuple[OptionalIntegration, ...] = field(default_factory=tuple)
    capabilities_provides: tuple[str, ...] = field(default_factory=tuple)
    capabilities_requires: tuple[str, ...] = field(default_factory=tuple)
    schema_version: str = ARTIFACT_DESCRIPTOR_SCHEMA_VERSION

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "ArtifactDescriptor":
        if not isinstance(data, Mapping):
            raise ArtifactDescriptorError("artifact descriptor must be a mapping")
        schema_version = str(data.get("schema_version") or "")
        if schema_version != ARTIFACT_DESCRIPTOR_SCHEMA_VERSION:
            raise ArtifactDescriptorError(
                f"unsupported artifact descriptor schema_version: {schema_version!r}"
            )

        artifact = _mapping(data.get("artifact"), "artifact")
        lifecycle = _mapping(data.get("lifecycle", {}), "lifecycle")
        runtime = _mapping(data.get("runtime", {}), "runtime")
        ui = _mapping(data.get("ui", {}), "ui")
        dependencies = _mapping(data.get("dependencies", {}), "dependencies")
        capabilities = _mapping(data.get("capabilities", {}), "capabilities")

        artifact_id = _validate_id(str(artifact.get("id") or ""), "artifact.id")
        version = str(artifact.get("version") or "").strip()
        if not version:
            raise ArtifactDescriptorError("artifact.version is required")
        try:
            kind = ArtifactKind(str(artifact.get("kind") or ""))
        except ValueError as exc:
            allowed = ", ".join(item.value for item in ArtifactKind)
            raise ArtifactDescriptorError(f"artifact.kind must be one of: {allowed}") from exc

        backend = _mapping(runtime.get("backend", {}), "runtime.backend")
        standalone_ui = _mapping(ui.get("standalone", {}), "ui.standalone")
        integrated_ui = _mapping(ui.get("integrated", {}), "ui.integrated")

        standalone = bool(lifecycle.get("standalone", kind is ArtifactKind.PRODUCT))
        standalone_ui_enabled = bool(standalone_ui.get("enabled", False))
        standalone_ui_entrypoint = _optional_text(standalone_ui.get("entrypoint"))
        if standalone_ui_enabled and not standalone_ui_entrypoint:
            raise ArtifactDescriptorError(
                "ui.standalone.entrypoint is required when standalone UI is enabled"
            )

        integrated_ui_enabled = bool(integrated_ui.get("enabled", False))
        integrated_manifest = _optional_relative_path(
            integrated_ui.get("manifest"), "ui.integrated.manifest"
        )
        integrated_contract = _optional_text(integrated_ui.get("contract"))
        if integrated_ui_enabled and (not integrated_manifest or not integrated_contract):
            raise ArtifactDescriptorError(
                "ui.integrated.manifest and ui.integrated.contract are required when integrated UI is enabled"
            )

        required_values = dependencies.get("required", []) or []
        if not isinstance(required_values, Sequence) or isinstance(required_values, (str, bytes)):
            raise ArtifactDescriptorError("dependencies.required must be a list")
        required = tuple(ArtifactDependency.from_value(value) for value in required_values)
        if artifact_id in {dep.id for dep in required}:
            raise ArtifactDescriptorError("an artifact cannot require itself")

        optional_values = dependencies.get("optional_integrations", []) or []
        if not isinstance(optional_values, Sequence) or isinstance(optional_values, (str, bytes)):
            raise ArtifactDescriptorError("dependencies.optional_integrations must be a list")
        optional = tuple(OptionalIntegration.from_value(value) for value in optional_values)

        provides = _string_tuple(capabilities.get("provides", []), "capabilities.provides")
        requires = _string_tuple(capabilities.get("requires", []), "capabilities.requires")

        return cls(
            schema_version=schema_version,
            artifact_id=artifact_id,
            version=version,
            kind=kind,
            removable=bool(lifecycle.get("removable", True)),
            standalone=standalone,
            backend_entrypoint=_optional_text(backend.get("entrypoint")),
            backend_healthcheck=_optional_text(backend.get("healthcheck")),
            standalone_ui_enabled=standalone_ui_enabled,
            standalone_ui_entrypoint=standalone_ui_entrypoint,
            integrated_ui_enabled=integrated_ui_enabled,
            integrated_manifest=integrated_manifest,
            integrated_contract=integrated_contract,
            required_dependencies=required,
            optional_integrations=optional,
            capabilities_provides=provides,
            capabilities_requires=requires,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "artifact": {
                "id": self.artifact_id,
                "version": self.version,
                "kind": self.kind.value,
            },
            "lifecycle": {
                "removable": self.removable,
                "standalone": self.standalone,
            },
            "runtime": {
                "backend": {
                    "entrypoint": self.backend_entrypoint,
                    "healthcheck": self.backend_healthcheck,
                }
            },
            "ui": {
                "standalone": {
                    "enabled": self.standalone_ui_enabled,
                    "entrypoint": self.standalone_ui_entrypoint,
                },
                "integrated": {
                    "enabled": self.integrated_ui_enabled,
                    "manifest": self.integrated_manifest,
                    "contract": self.integrated_contract,
                },
            },
            "dependencies": {
                "required": [item.to_dict() for item in self.required_dependencies],
                "optional_integrations": [item.to_dict() for item in self.optional_integrations],
            },
            "capabilities": {
                "provides": list(self.capabilities_provides),
                "requires": list(self.capabilities_requires),
            },
        }


def default_konnaxion_descriptor(*, version: str = "v14") -> ArtifactDescriptor:
    return ArtifactDescriptor.from_mapping(
        {
            "schema_version": ARTIFACT_DESCRIPTOR_SCHEMA_VERSION,
            "artifact": {"id": "konnaxion", "version": version, "kind": "product"},
            "lifecycle": {"removable": True, "standalone": True},
            "runtime": {"backend": {"entrypoint": "/api", "healthcheck": "/api/health"}},
            "ui": {
                "standalone": {"enabled": True, "entrypoint": "/"},
                "integrated": {"enabled": False, "manifest": None, "contract": None},
            },
            "dependencies": {"required": [], "optional_integrations": []},
            "capabilities": {
                "provides": ["konnaxion.runtime", "konnaxion.standalone-ui"],
                "requires": [],
            },
        }
    )


def load_artifact_descriptor(path: str | Path) -> ArtifactDescriptor:
    descriptor_path = Path(path)
    try:
        payload = yaml.safe_load(descriptor_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise ArtifactDescriptorError(f"could not read artifact descriptor {descriptor_path}: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise ArtifactDescriptorError("artifact descriptor YAML must contain an object")
    return ArtifactDescriptor.from_mapping(payload)


def write_artifact_descriptor(descriptor: ArtifactDescriptor, path: str | Path) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        yaml.safe_dump(descriptor.to_dict(), sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return output


def _mapping(value: Any, field_name: str) -> Mapping[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ArtifactDescriptorError(f"{field_name} must be an object")
    return value


def _validate_id(value: str, field_name: str) -> str:
    normalized = value.strip().lower()
    if not normalized or not _SAFE_ID.fullmatch(normalized):
        raise ArtifactDescriptorError(
            f"{field_name} must match lowercase [a-z0-9][a-z0-9_.-]*"
        )
    return normalized


def _optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _optional_relative_path(value: Any, field_name: str) -> str | None:
    text = _optional_text(value)
    if text is None:
        return None
    path = PurePosixPath(text.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts:
        raise ArtifactDescriptorError(f"{field_name} must be a safe relative path")
    return path.as_posix()


def _string_tuple(value: Any, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ArtifactDescriptorError(f"{field_name} must be a list")
    result: list[str] = []
    for item in value:
        text = str(item).strip()
        if not text:
            raise ArtifactDescriptorError(f"{field_name} entries must not be empty")
        if text not in result:
            result.append(text)
    return tuple(result)


__all__ = [
    "ARTIFACT_DESCRIPTOR_FILENAME",
    "ARTIFACT_DESCRIPTOR_SCHEMA_VERSION",
    "SOURCE_ARTIFACT_DESCRIPTOR_FILENAME",
    "ArtifactDependency",
    "ArtifactDescriptor",
    "ArtifactDescriptorError",
    "ArtifactKind",
    "DependencyInterface",
    "OptionalIntegration",
    "default_konnaxion_descriptor",
    "load_artifact_descriptor",
    "write_artifact_descriptor",
]
