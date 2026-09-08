"""Persistent Capsule product registry and public discovery projection.

The registry is deployment/discovery state. It never owns product UX and never
parses Koali navigation/surface definitions. The product-owned integration
manifest remains opaque; Capsule only verifies its declared location, trust,
contract compatibility and required host capabilities.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
import os
from pathlib import Path
from typing import Any, Iterable, Mapping

from kx_shared.konnaxion_constants import KX_SHARED_DIR
from kx_shared.product_contract import (
    ArtifactKind,
    DEFAULT_SUPPORTED_INTEGRATION_CONTRACTS,
    IntegrationStatus,
    PRODUCT_REGISTRY_SCHEMA_VERSION,
    ProductDescriptor,
    ProductStatus,
    ReadinessStatus,
    derive_product_status,
    load_product_descriptor,
)


DEFAULT_PRODUCT_REGISTRY_FILE = Path(os.fspath(KX_SHARED_DIR)) / "registry" / "products.json"
PRODUCT_DESCRIPTOR_RELATIVE_PATH = Path("metadata") / "product.yaml"


class ProductRegistryError(RuntimeError):
    """Base product registry failure."""


class ArtifactDependencyError(ProductRegistryError):
    """Raised when removal would break a required dependency."""

    def __init__(self, artifact_id: str, dependents: Iterable[str]) -> None:
        self.artifact_id = artifact_id
        self.dependents = tuple(sorted(set(str(item) for item in dependents)))
        super().__init__(
            f"cannot remove required artifact {artifact_id!r}; required by: "
            + ", ".join(self.dependents)
        )


@dataclass(frozen=True, slots=True)
class IntegrationAdmission:
    status: IntegrationStatus
    manifest: str | None = None
    contract: str | None = None
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "available": self.status == IntegrationStatus.READY,
            "manifest": self.manifest,
            "contract": self.contract,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class ProductReadiness:
    runtime_status: ReadinessStatus
    standalone_ui_status: ReadinessStatus
    integration_status: IntegrationStatus
    product_status: ProductStatus

    def to_dict(self) -> dict[str, str]:
        return {
            "runtime_status": self.runtime_status.value,
            "standalone_ui_status": self.standalone_ui_status.value,
            "integration_status": self.integration_status.value,
            "product_status": self.product_status.value,
        }


@dataclass(frozen=True, slots=True)
class RegistryRecord:
    descriptor: ProductDescriptor
    capsule_root: str | None
    readiness: ProductReadiness
    admission: IntegrationAdmission
    trusted: bool
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "descriptor": self.descriptor.to_dict(),
            "capsule_root": self.capsule_root,
            "readiness": self.readiness.to_dict(),
            "admission": self.admission.to_dict(),
            "trusted": self.trusted,
            "updated_at": self.updated_at,
        }


class ProductRegistry:
    """Persistent registry with generation-based change detection."""

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        supported_integration_contracts: Iterable[str] = DEFAULT_SUPPORTED_INTEGRATION_CONTRACTS,
        host_capabilities: Iterable[str] = (),
    ) -> None:
        self.path = Path(path) if path is not None else DEFAULT_PRODUCT_REGISTRY_FILE
        self.supported_integration_contracts = frozenset(str(item) for item in supported_integration_contracts)
        self.host_capabilities = frozenset(str(item) for item in host_capabilities)
        self.generation = 0
        self.records: dict[str, RegistryRecord] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.is_file():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ProductRegistryError(f"invalid product registry: {self.path}") from exc
        if not isinstance(payload, Mapping):
            raise ProductRegistryError("product registry root must be an object")
        self.generation = int(payload.get("generation", 0) or 0)
        raw_records = payload.get("records") or {}
        if not isinstance(raw_records, Mapping):
            raise ProductRegistryError("product registry records must be an object")
        for artifact_id, raw in raw_records.items():
            if not isinstance(raw, Mapping):
                continue
            descriptor_raw = raw.get("descriptor")
            if not isinstance(descriptor_raw, Mapping):
                continue
            from kx_shared.product_contract import descriptor_from_mapping

            descriptor = descriptor_from_mapping(descriptor_raw)
            readiness_raw = raw.get("readiness") or {}
            admission_raw = raw.get("admission") or {}
            readiness = ProductReadiness(
                runtime_status=ReadinessStatus(str(readiness_raw.get("runtime_status", "unknown"))),
                standalone_ui_status=ReadinessStatus(str(readiness_raw.get("standalone_ui_status", "unknown"))),
                integration_status=IntegrationStatus(str(readiness_raw.get("integration_status", "unknown"))),
                product_status=ProductStatus(str(readiness_raw.get("product_status", "degraded"))),
            )
            admission = IntegrationAdmission(
                status=IntegrationStatus(str(admission_raw.get("status", readiness.integration_status.value))),
                manifest=admission_raw.get("manifest"),
                contract=admission_raw.get("contract"),
                reason=admission_raw.get("reason"),
            )
            self.records[str(artifact_id)] = RegistryRecord(
                descriptor=descriptor,
                capsule_root=str(raw.get("capsule_root")) if raw.get("capsule_root") else None,
                readiness=readiness,
                admission=admission,
                trusted=bool(raw.get("trusted", False)),
                updated_at=str(raw.get("updated_at") or _utc_now()),
            )

    def _semantic_payload(self) -> dict[str, Any]:
        """Return persisted content excluding volatile write timestamp."""
        return {
            "schema_version": PRODUCT_REGISTRY_SCHEMA_VERSION,
            "generation": self.generation,
            "records": {
                artifact_id: record.to_dict()
                for artifact_id, record in sorted(self.records.items())
            },
        }

    def _persist(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = self._semantic_payload()
        payload["updated_at"] = _utc_now()
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self.path)

    def _admit_integration(
        self,
        descriptor: ProductDescriptor,
        *,
        capsule_root: Path | None,
        trusted: bool,
    ) -> IntegrationAdmission:
        integration = descriptor.integration
        if integration is None:
            return IntegrationAdmission(
                status=IntegrationStatus.UNAVAILABLE,
                reason="product does not declare a public integration manifest",
            )
        if not trusted:
            return IntegrationAdmission(
                status=IntegrationStatus.REJECTED,
                manifest=integration.manifest,
                contract=integration.contract,
                reason="artifact trust/signature verification is not satisfied",
            )
        if integration.contract not in self.supported_integration_contracts:
            return IntegrationAdmission(
                status=IntegrationStatus.REJECTED,
                manifest=integration.manifest,
                contract=integration.contract,
                reason="integration contract version is not supported",
            )
        missing_capabilities = sorted(
            set(integration.required_capabilities).difference(self.host_capabilities)
        )
        if missing_capabilities:
            return IntegrationAdmission(
                status=IntegrationStatus.REJECTED,
                manifest=integration.manifest,
                contract=integration.contract,
                reason="required host capabilities unavailable: " + ", ".join(missing_capabilities),
            )
        if capsule_root is None:
            return IntegrationAdmission(
                status=IntegrationStatus.REJECTED,
                manifest=integration.manifest,
                contract=integration.contract,
                reason="integration manifest cannot be resolved without capsule root",
            )
        manifest_path = (capsule_root / integration.manifest).resolve(strict=False)
        root_resolved = capsule_root.resolve(strict=False)
        try:
            manifest_path.relative_to(root_resolved)
        except ValueError:
            return IntegrationAdmission(
                status=IntegrationStatus.REJECTED,
                manifest=integration.manifest,
                contract=integration.contract,
                reason="integration manifest path escapes artifact root",
            )
        if not manifest_path.is_file():
            return IntegrationAdmission(
                status=IntegrationStatus.REJECTED,
                manifest=integration.manifest,
                contract=integration.contract,
                reason="declared integration manifest is missing",
            )
        return IntegrationAdmission(
            status=IntegrationStatus.READY,
            manifest=integration.manifest,
            contract=integration.contract,
        )

    def register(
        self,
        descriptor: ProductDescriptor,
        *,
        capsule_root: str | Path | None = None,
        trusted: bool = True,
        runtime_status: ReadinessStatus | str = ReadinessStatus.READY,
        standalone_ui_status: ReadinessStatus | str | None = None,
    ) -> RegistryRecord:
        from kx_shared.product_contract import descriptor_from_mapping

        descriptor = descriptor_from_mapping(descriptor.to_dict())
        root = Path(capsule_root) if capsule_root is not None else None
        runtime = runtime_status if isinstance(runtime_status, ReadinessStatus) else ReadinessStatus(str(runtime_status))
        if standalone_ui_status is None:
            standalone = (
                ReadinessStatus.READY
                if descriptor.standalone is not None
                else ReadinessStatus.UNAVAILABLE
            )
        else:
            standalone = (
                standalone_ui_status
                if isinstance(standalone_ui_status, ReadinessStatus)
                else ReadinessStatus(str(standalone_ui_status))
            )
        admission = self._admit_integration(descriptor, capsule_root=root, trusted=trusted)
        readiness = ProductReadiness(
            runtime_status=runtime,
            standalone_ui_status=standalone,
            integration_status=admission.status,
            product_status=derive_product_status(runtime, standalone),
        )
        candidate = RegistryRecord(
            descriptor=descriptor,
            capsule_root=str(root) if root is not None else None,
            readiness=readiness,
            admission=admission,
            trusted=trusted,
            updated_at=_utc_now(),
        )
        artifact_id = descriptor.artifact.id
        existing = self.records.get(artifact_id)
        if existing is not None and _record_semantic_dict(existing) == _record_semantic_dict(candidate):
            return existing
        self.records[artifact_id] = candidate
        self.generation += 1
        self._persist()
        return candidate

    def register_descriptor_file(
        self,
        descriptor_path: str | Path,
        *,
        capsule_root: str | Path | None = None,
        trusted: bool = True,
    ) -> RegistryRecord:
        descriptor = load_product_descriptor(descriptor_path)
        return self.register(descriptor, capsule_root=capsule_root, trusted=trusted)

    def required_dependents(self, artifact_id: str) -> tuple[str, ...]:
        dependents: list[str] = []
        for candidate_id, record in self.records.items():
            if candidate_id == artifact_id:
                continue
            if artifact_id in {item.id for item in record.descriptor.required_dependencies}:
                dependents.append(candidate_id)
        return tuple(sorted(dependents))

    def remove(self, artifact_id: str) -> None:
        dependents = self.required_dependents(artifact_id)
        if dependents:
            raise ArtifactDependencyError(artifact_id, dependents)
        if artifact_id not in self.records:
            return
        del self.records[artifact_id]
        self.generation += 1
        self._persist()

    def full_projection(self) -> dict[str, Any]:
        groups: dict[str, list[dict[str, Any]]] = {
            "libraries": [],
            "products": [],
            "composition_hosts": [],
        }
        for record in sorted(self.records.values(), key=lambda item: item.descriptor.artifact.id):
            item = self._public_record(record)
            kind = record.descriptor.artifact.kind
            if kind == ArtifactKind.LIBRARY:
                groups["libraries"].append(item)
            elif kind == ArtifactKind.COMPOSITION_HOST:
                groups["composition_hosts"].append(item)
            else:
                groups["products"].append(item)
        return {
            "schema_version": PRODUCT_REGISTRY_SCHEMA_VERSION,
            "generation": self.generation,
            **groups,
        }

    def public_products_projection(self) -> dict[str, Any]:
        products = [
            self._public_record(record)
            for record in sorted(self.records.values(), key=lambda item: item.descriptor.artifact.id)
            if record.descriptor.artifact.kind == ArtifactKind.PRODUCT
        ]
        return {
            "schema_version": PRODUCT_REGISTRY_SCHEMA_VERSION,
            "generation": self.generation,
            "products": products,
        }

    @staticmethod
    def _public_record(record: RegistryRecord) -> dict[str, Any]:
        descriptor = record.descriptor
        standalone = descriptor.standalone
        runtime = descriptor.runtime
        return {
            "id": descriptor.artifact.id,
            "kind": descriptor.artifact.kind.value,
            "version": descriptor.artifact.version,
            "display_name": descriptor.artifact.display_name or descriptor.artifact.id,
            "state": record.readiness.product_status.value,
            "runtime_status": record.readiness.runtime_status.value,
            "standalone_ui_status": record.readiness.standalone_ui_status.value,
            "integration_status": record.readiness.integration_status.value,
            "runtime": {
                "available": record.readiness.runtime_status == ReadinessStatus.READY,
                "entrypoint": runtime.entrypoint if runtime else None,
            },
            "standalone": {
                "available": record.readiness.standalone_ui_status == ReadinessStatus.READY,
                "entrypoint": standalone.entrypoint if standalone else None,
            },
            "integrated": {
                "available": record.admission.status == IntegrationStatus.READY,
                "manifest": record.admission.manifest,
                "contract": record.admission.contract,
                "status": record.admission.status.value,
                "reason": record.admission.reason,
            },
            "capabilities": list(descriptor.capabilities),
        }


def discover_product_descriptor(capsule_root: str | Path) -> Path | None:
    path = Path(capsule_root) / PRODUCT_DESCRIPTOR_RELATIVE_PATH
    return path if path.is_file() else None


def register_extracted_product(
    capsule_root: str | Path,
    *,
    trusted: bool,
    registry: ProductRegistry | None = None,
) -> RegistryRecord | None:
    root = Path(capsule_root)
    descriptor_path = discover_product_descriptor(root)
    if descriptor_path is None:
        return None
    target = registry or ProductRegistry()
    return target.register_descriptor_file(
        descriptor_path,
        capsule_root=root,
        trusted=trusted,
    )


def _record_semantic_dict(record: RegistryRecord) -> dict[str, Any]:
    payload = record.to_dict()
    payload.pop("updated_at", None)
    return payload


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


__all__ = [
    "ArtifactDependencyError",
    "DEFAULT_PRODUCT_REGISTRY_FILE",
    "IntegrationAdmission",
    "PRODUCT_DESCRIPTOR_RELATIVE_PATH",
    "ProductReadiness",
    "ProductRegistry",
    "ProductRegistryError",
    "RegistryRecord",
    "discover_product_descriptor",
    "register_extracted_product",
]
