"""Deployment/discovery contract for Capsule-managed artifacts.

Capsule owns deployment identity, lifecycle, dependency declarations and public
entrypoint discovery. Product UX stays product-owned. Koali/Spaces integration
is referenced as a signed public manifest but its navigation/surface contents
are intentionally opaque to Capsule.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

import yaml


PRODUCT_DESCRIPTOR_SCHEMA_VERSION = "kx-product/v1"
PRODUCT_REGISTRY_SCHEMA_VERSION = "kx-product-registry/v1"
DEFAULT_SUPPORTED_INTEGRATION_CONTRACTS = frozenset({"koali-ui/v1"})


class ProductDescriptorError(ValueError):
    """Raised when a Capsule product descriptor is invalid."""


class ArtifactKind(StrEnum):
    """Deployment/discovery classification, not package/archive format."""

    LIBRARY = "library"
    PRODUCT = "product"
    COMPOSITION_HOST = "composition_host"


class ReadinessStatus(StrEnum):
    READY = "ready"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


class IntegrationStatus(StrEnum):
    READY = "ready"
    UNAVAILABLE = "unavailable"
    REJECTED = "rejected"
    UNKNOWN = "unknown"


class ProductStatus(StrEnum):
    FUNCTIONAL = "functional"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class ArtifactIdentity:
    id: str
    kind: ArtifactKind
    version: str
    display_name: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "id": self.id,
            "kind": self.kind.value,
            "version": self.version,
        }
        if self.display_name:
            payload["display_name"] = self.display_name
        return payload


@dataclass(frozen=True, slots=True)
class EntrypointDescriptor:
    entrypoint: str
    healthcheck: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = {"entrypoint": self.entrypoint}
        if self.healthcheck:
            payload["healthcheck"] = self.healthcheck
        return payload


@dataclass(frozen=True, slots=True)
class ArtifactDependency:
    id: str
    version: str | None = None

    def to_dict(self) -> dict[str, str]:
        payload = {"id": self.id}
        if self.version:
            payload["version"] = self.version
        return payload


@dataclass(frozen=True, slots=True)
class OptionalIntegration:
    product: str
    capability: str | None = None

    def to_dict(self) -> dict[str, str]:
        payload = {"product": self.product}
        if self.capability:
            payload["capability"] = self.capability
        return payload


@dataclass(frozen=True, slots=True)
class IntegrationDescriptor:
    """Reference to a product-owned public integration manifest.

    Capsule validates only deployment/discovery metadata here. It does not parse
    or transform product navigation, routes, surfaces, commands or inspectors.
    """

    manifest: str
    contract: str
    required_capabilities: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "manifest": self.manifest,
            "contract": self.contract,
            "required_capabilities": list(self.required_capabilities),
        }


@dataclass(frozen=True, slots=True)
class ProductDescriptor:
    schema_version: str
    artifact: ArtifactIdentity
    runtime: EntrypointDescriptor | None = None
    standalone: EntrypointDescriptor | None = None
    required_dependencies: tuple[ArtifactDependency, ...] = ()
    optional_integrations: tuple[OptionalIntegration, ...] = ()
    capabilities: tuple[str, ...] = ()
    integration: IntegrationDescriptor | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": self.schema_version,
            "artifact": self.artifact.to_dict(),
            "dependencies": {
                "required": [item.to_dict() for item in self.required_dependencies],
                "optional_integrations": [
                    item.to_dict() for item in self.optional_integrations
                ],
            },
            "capabilities": list(self.capabilities),
            "metadata": dict(self.metadata),
        }
        if self.runtime:
            payload["runtime"] = self.runtime.to_dict()
        if self.standalone:
            payload["standalone"] = self.standalone.to_dict()
        if self.integration:
            payload["integration"] = self.integration.to_dict()
        return payload


_FORBIDDEN_UX_ROOT_KEYS = frozenset(
    {"routes", "navigation", "sidebar", "surfaces", "commands", "inspectors", "widgets"}
)


def _clean_id(value: Any, *, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ProductDescriptorError(f"{field_name} is required")
    if "/" in text or "\\" in text or text in {".", ".."}:
        raise ProductDescriptorError(f"{field_name} must be a stable artifact identifier")
    return text


def _clean_text(value: Any, *, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ProductDescriptorError(f"{field_name} is required")
    return text


def _safe_relative_path(value: Any, *, field_name: str) -> str:
    text = _clean_text(value, field_name=field_name).replace("\\", "/")
    path = PurePosixPath(text)
    if path.is_absolute() or ".." in path.parts:
        raise ProductDescriptorError(f"{field_name} must be a safe relative path")
    return path.as_posix()


def _mapping(value: Any, *, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ProductDescriptorError(f"{field_name} must be an object")
    return value


def _sequence(value: Any, *, field_name: str) -> Sequence[Any]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ProductDescriptorError(f"{field_name} must be a list")
    return value


def _parse_entrypoint(value: Any, *, field_name: str) -> EntrypointDescriptor | None:
    if value is None:
        return None
    data = _mapping(value, field_name=field_name)
    entrypoint = _clean_text(data.get("entrypoint"), field_name=f"{field_name}.entrypoint")
    healthcheck = str(data.get("healthcheck") or "").strip() or None
    return EntrypointDescriptor(entrypoint=entrypoint, healthcheck=healthcheck)


def _parse_dependencies(value: Any) -> tuple[tuple[ArtifactDependency, ...], tuple[OptionalIntegration, ...]]:
    if value is None:
        return (), ()
    data = _mapping(value, field_name="dependencies")

    required: list[ArtifactDependency] = []
    for index, item in enumerate(_sequence(data.get("required"), field_name="dependencies.required")):
        dependency = _mapping(item, field_name=f"dependencies.required[{index}]")
        required.append(
            ArtifactDependency(
                id=_clean_id(dependency.get("id"), field_name=f"dependencies.required[{index}].id"),
                version=str(dependency.get("version") or "").strip() or None,
            )
        )

    optional: list[OptionalIntegration] = []
    for index, item in enumerate(
        _sequence(data.get("optional_integrations"), field_name="dependencies.optional_integrations")
    ):
        integration = _mapping(item, field_name=f"dependencies.optional_integrations[{index}]")
        optional.append(
            OptionalIntegration(
                product=_clean_id(
                    integration.get("product"),
                    field_name=f"dependencies.optional_integrations[{index}].product",
                ),
                capability=str(integration.get("capability") or "").strip() or None,
            )
        )

    return tuple(required), tuple(optional)


def descriptor_from_mapping(data: Mapping[str, Any]) -> ProductDescriptor:
    """Parse and validate one ``metadata/product.yaml`` mapping."""

    if not isinstance(data, Mapping):
        raise ProductDescriptorError("product descriptor root must be an object")

    forbidden = sorted(_FORBIDDEN_UX_ROOT_KEYS.intersection(data.keys()))
    if forbidden:
        raise ProductDescriptorError(
            "product descriptor must not contain product UX definitions: "
            + ", ".join(forbidden)
            + "; place them in the product-owned integration manifest"
        )

    schema_version = _clean_text(data.get("schema_version"), field_name="schema_version")
    if schema_version != PRODUCT_DESCRIPTOR_SCHEMA_VERSION:
        raise ProductDescriptorError(
            f"unsupported product descriptor schema_version: {schema_version!r}"
        )

    artifact_data = _mapping(data.get("artifact"), field_name="artifact")
    try:
        kind = ArtifactKind(_clean_text(artifact_data.get("kind"), field_name="artifact.kind"))
    except ValueError as exc:
        raise ProductDescriptorError(
            "artifact.kind must be one of: library, product, composition_host"
        ) from exc

    artifact = ArtifactIdentity(
        id=_clean_id(artifact_data.get("id"), field_name="artifact.id"),
        kind=kind,
        version=_clean_text(artifact_data.get("version"), field_name="artifact.version"),
        display_name=str(artifact_data.get("display_name") or "").strip() or None,
    )

    runtime = _parse_entrypoint(data.get("runtime"), field_name="runtime")
    standalone = _parse_entrypoint(data.get("standalone"), field_name="standalone")

    if artifact.kind == ArtifactKind.PRODUCT:
        if runtime is None:
            raise ProductDescriptorError("product artifacts must declare runtime.entrypoint")
        if standalone is None:
            raise ProductDescriptorError("product artifacts must declare standalone.entrypoint")

    required_dependencies, optional_integrations = _parse_dependencies(data.get("dependencies"))

    capabilities = tuple(
        sorted(
            {
                _clean_text(item, field_name="capabilities[]")
                for item in _sequence(data.get("capabilities"), field_name="capabilities")
            }
        )
    )

    integration: IntegrationDescriptor | None = None
    if data.get("integration") is not None:
        integration_data = _mapping(data.get("integration"), field_name="integration")
        integration = IntegrationDescriptor(
            manifest=_safe_relative_path(
                integration_data.get("manifest"), field_name="integration.manifest"
            ),
            contract=_clean_text(
                integration_data.get("contract"), field_name="integration.contract"
            ),
            required_capabilities=tuple(
                sorted(
                    {
                        _clean_text(item, field_name="integration.required_capabilities[]")
                        for item in _sequence(
                            integration_data.get("required_capabilities"),
                            field_name="integration.required_capabilities",
                        )
                    }
                )
            ),
        )

    metadata = data.get("metadata") or {}
    if not isinstance(metadata, Mapping):
        raise ProductDescriptorError("metadata must be an object")

    return ProductDescriptor(
        schema_version=schema_version,
        artifact=artifact,
        runtime=runtime,
        standalone=standalone,
        required_dependencies=required_dependencies,
        optional_integrations=optional_integrations,
        capabilities=capabilities,
        integration=integration,
        metadata=dict(metadata),
    )


def load_product_descriptor(path: str | Path) -> ProductDescriptor:
    descriptor_path = Path(path)
    try:
        raw = yaml.safe_load(descriptor_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ProductDescriptorError(f"could not read product descriptor: {descriptor_path}") from exc
    if not isinstance(raw, Mapping):
        raise ProductDescriptorError("product descriptor root must be an object")
    return descriptor_from_mapping(raw)


def write_product_descriptor(descriptor: ProductDescriptor, path: str | Path) -> Path:
    descriptor_path = Path(path)
    descriptor_path.parent.mkdir(parents=True, exist_ok=True)
    validated = descriptor_from_mapping(descriptor.to_dict())
    descriptor_path.write_text(
        yaml.safe_dump(validated.to_dict(), sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return descriptor_path


def build_product_descriptor(
    *,
    artifact_id: str,
    artifact_kind: ArtifactKind | str,
    version: str,
    display_name: str | None = None,
    runtime_entrypoint: str | None = None,
    standalone_entrypoint: str | None = None,
    runtime_healthcheck: str | None = None,
    standalone_healthcheck: str | None = None,
    required_dependencies: Sequence[ArtifactDependency] = (),
    optional_integrations: Sequence[OptionalIntegration] = (),
    capabilities: Sequence[str] = (),
    integration: IntegrationDescriptor | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> ProductDescriptor:
    kind = artifact_kind if isinstance(artifact_kind, ArtifactKind) else ArtifactKind(str(artifact_kind))
    descriptor = ProductDescriptor(
        schema_version=PRODUCT_DESCRIPTOR_SCHEMA_VERSION,
        artifact=ArtifactIdentity(
            id=_clean_id(artifact_id, field_name="artifact.id"),
            kind=kind,
            version=_clean_text(version, field_name="artifact.version"),
            display_name=display_name,
        ),
        runtime=(
            EntrypointDescriptor(runtime_entrypoint, runtime_healthcheck)
            if runtime_entrypoint
            else None
        ),
        standalone=(
            EntrypointDescriptor(standalone_entrypoint, standalone_healthcheck)
            if standalone_entrypoint
            else None
        ),
        required_dependencies=tuple(required_dependencies),
        optional_integrations=tuple(optional_integrations),
        capabilities=tuple(sorted(set(capabilities))),
        integration=integration,
        metadata=dict(metadata or {}),
    )
    # Round-trip through the parser so builders and hand-written descriptors use
    # exactly the same validation rules.
    return descriptor_from_mapping(descriptor.to_dict())


def derive_product_status(
    runtime_status: ReadinessStatus | str,
    standalone_ui_status: ReadinessStatus | str,
) -> ProductStatus:
    runtime = runtime_status if isinstance(runtime_status, ReadinessStatus) else ReadinessStatus(str(runtime_status))
    standalone = (
        standalone_ui_status
        if isinstance(standalone_ui_status, ReadinessStatus)
        else ReadinessStatus(str(standalone_ui_status))
    )

    if runtime == ReadinessStatus.UNAVAILABLE:
        return ProductStatus.UNAVAILABLE
    if runtime == ReadinessStatus.READY and standalone == ReadinessStatus.READY:
        return ProductStatus.FUNCTIONAL
    return ProductStatus.DEGRADED


__all__ = [
    "ArtifactDependency",
    "ArtifactIdentity",
    "ArtifactKind",
    "DEFAULT_SUPPORTED_INTEGRATION_CONTRACTS",
    "EntrypointDescriptor",
    "IntegrationDescriptor",
    "IntegrationStatus",
    "OptionalIntegration",
    "PRODUCT_DESCRIPTOR_SCHEMA_VERSION",
    "PRODUCT_REGISTRY_SCHEMA_VERSION",
    "ProductDescriptor",
    "ProductDescriptorError",
    "ProductStatus",
    "ReadinessStatus",
    "build_product_descriptor",
    "derive_product_status",
    "descriptor_from_mapping",
    "load_product_descriptor",
    "write_product_descriptor",
]
