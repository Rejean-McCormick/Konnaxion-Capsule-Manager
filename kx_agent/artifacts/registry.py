"""Persistent installed-artifact registry.

The registry answers presence/lifecycle/discovery questions only. It does not
interpret product navigation, routes, commands, inspectors, or presentation.
Those remain owned by each product and optional composition hosts such as kOA
Spaces.
"""

from __future__ import annotations

from datetime import UTC, datetime
import json
import os
from pathlib import Path
import re
import shutil
import threading
from typing import Any, Mapping

try:
    import yaml
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("PyYAML is required for the artifact registry") from exc

from kx_shared.artifact_descriptor import (
    ARTIFACT_DESCRIPTOR_FILENAME,
    ArtifactDescriptor,
    ArtifactDescriptorError,
    ArtifactKind,
    default_konnaxion_descriptor,
    load_artifact_descriptor,
)
from kx_shared.paths import (
    artifact_registry_dir,
    artifact_registry_file,
    assert_under_root,
    ensure_dir,
    instances_dir,
)


REGISTRY_SCHEMA_VERSION = "kx-installed-artifact-registry/v1"
DEFAULT_ALLOWED_INTEGRATION_CONTRACTS = (
    "koali-ui/v1",
    "module-interface-manifest/v1",
)

_LOCK = threading.RLock()


class ArtifactRegistryError(RuntimeError):
    """Raised when artifact registration/discovery cannot be completed safely."""

    def __init__(self, message: str, *, details: Mapping[str, Any] | None = None) -> None:
        self.details = dict(details or {})
        super().__init__(message)


class ArtifactDependencyError(ArtifactRegistryError):
    """Raised when required installed dependencies/capabilities are absent."""


class ArtifactRemovalBlockedError(ArtifactRegistryError):
    """Raised when removal would violate lifecycle/dependency contracts."""


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def allowed_integration_contracts() -> frozenset[str]:
    raw = os.getenv("KX_ALLOWED_INTEGRATION_CONTRACTS", "").strip()
    if not raw:
        return frozenset(DEFAULT_ALLOWED_INTEGRATION_CONTRACTS)
    return frozenset(item.strip() for item in raw.split(",") if item.strip())


def ensure_registry() -> Path:
    directory = ensure_dir(artifact_registry_dir())
    path = artifact_registry_file()
    if not path.exists():
        _write_registry(_empty_registry())
    readme = directory / "README.txt"
    if not readme.exists():
        readme.write_text(
            "Konnaxion installed artifact registry.\n"
            "Capsule owns presence/lifecycle/discovery; products own product UX.\n",
            encoding="utf-8",
        )
    return path


def load_registry() -> dict[str, Any]:
    with _LOCK:
        path = ensure_registry()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ArtifactRegistryError(f"could not read artifact registry {path}: {exc}") from exc
        if not isinstance(data, dict) or data.get("schema_version") != REGISTRY_SCHEMA_VERSION:
            raise ArtifactRegistryError("installed artifact registry has an unsupported schema")
        if not isinstance(data.get("artifacts"), dict):
            raise ArtifactRegistryError("installed artifact registry artifacts must be an object")
        return data


def list_artifacts(
    *,
    kind: str | ArtifactKind | None = None,
    products_only: bool = False,
    composition_candidates_only: bool = False,
) -> dict[str, Any]:
    registry = load_registry()
    items = list(registry["artifacts"].values())
    if products_only or composition_candidates_only:
        kind = ArtifactKind.PRODUCT
    if kind is not None:
        kind_value = str(getattr(kind, "value", kind))
        items = [item for item in items if item.get("kind") == kind_value]
    if composition_candidates_only:
        items = [
            item
            for item in items
            if isinstance(item.get("integration"), Mapping)
            and item["integration"].get("status") == "available"
        ]
    items = sorted(items, key=lambda item: str(item.get("id", "")))
    return {
        "schema_version": REGISTRY_SCHEMA_VERSION,
        "generation": int(registry.get("generation", 0)),
        "updated_at": registry.get("updated_at"),
        "items": items,
        "count": len(items),
    }


def get_artifact(artifact_id: str) -> dict[str, Any]:
    artifact_id = _safe_id(artifact_id)
    registry = load_registry()
    item = registry["artifacts"].get(artifact_id)
    if not isinstance(item, Mapping):
        raise ArtifactRegistryError(f"artifact is not installed: {artifact_id}", details={"artifact_id": artifact_id})
    return dict(item)


def get_integration_manifest(artifact_id: str) -> dict[str, Any]:
    item = get_artifact(artifact_id)
    integration = item.get("integration") or {}
    if not isinstance(integration, Mapping) or integration.get("status") != "available":
        raise ArtifactRegistryError(
            f"artifact has no admitted integrated UI contribution: {artifact_id}",
            details={"artifact_id": artifact_id, "integration": dict(integration) if isinstance(integration, Mapping) else {}},
        )
    manifest_path = str(integration.get("resolved_manifest") or "")
    if not manifest_path:
        raise ArtifactRegistryError(f"integrated manifest path is unavailable: {artifact_id}")
    path = assert_under_root(Path(manifest_path))
    if not path.is_file():
        raise ArtifactRegistryError(f"integrated manifest is missing: {path}")
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise ArtifactRegistryError(f"could not read integrated manifest {path}: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise ArtifactRegistryError("integrated manifest must contain an object")
    return {
        "artifact_id": artifact_id,
        "contract": integration.get("contract"),
        "manifest": dict(payload),
        "generation": load_registry().get("generation", 0),
    }


def register_artifact_from_capsule(
    *,
    extract_dir: str | Path,
    capsule_id: str,
    capsule_path: str | Path,
    verified: bool,
    verification_report: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    extract_root = assert_under_root(Path(extract_dir))
    descriptor_path = extract_root / ARTIFACT_DESCRIPTOR_FILENAME
    descriptor_source = "artifact.yaml"

    if descriptor_path.is_file():
        try:
            descriptor = load_artifact_descriptor(descriptor_path)
        except ArtifactDescriptorError as exc:
            raise ArtifactRegistryError(f"invalid artifact descriptor: {exc}") from exc
    else:
        descriptor = _legacy_descriptor(extract_root)
        descriptor_source = "legacy-synthesized"

    with _LOCK:
        registry = load_registry()
        _validate_dependencies(descriptor, registry)
        entry = _build_entry(
            descriptor,
            extract_root=extract_root,
            capsule_id=capsule_id,
            capsule_path=assert_under_root(Path(capsule_path)),
            verified=verified,
            verification_report=verification_report,
            descriptor_source=descriptor_source,
        )
        registry["artifacts"][descriptor.artifact_id] = entry
        registry["generation"] = int(registry.get("generation", 0)) + 1
        registry["updated_at"] = utc_now_iso()
        _write_registry(registry)
        return dict(entry)


def remove_artifact(
    artifact_id: str,
    *,
    preserve_data: bool = True,
    remove_capsule_files: bool = True,
) -> dict[str, Any]:
    artifact_id = _safe_id(artifact_id)
    with _LOCK:
        registry = load_registry()
        entry = registry["artifacts"].get(artifact_id)
        if not isinstance(entry, Mapping):
            raise ArtifactRegistryError(f"artifact is not installed: {artifact_id}")
        descriptor = ArtifactDescriptor.from_mapping(entry.get("descriptor") or {})
        if not descriptor.removable:
            raise ArtifactRemovalBlockedError(
                f"artifact is not removable: {artifact_id}", details={"artifact_id": artifact_id}
            )

        dependents = _required_dependents(artifact_id, registry)
        capability_dependents = _capability_dependents(descriptor, artifact_id, registry)
        active_instances = _instances_using_capsule(str(entry.get("capsule_id") or ""))
        if dependents or capability_dependents or active_instances:
            raise ArtifactRemovalBlockedError(
                f"artifact removal blocked: {artifact_id}",
                details={
                    "artifact_id": artifact_id,
                    "required_dependents": dependents,
                    "capability_dependents": capability_dependents,
                    "active_instances": active_instances,
                },
            )

        removed_files: list[str] = []
        if remove_capsule_files:
            for key in ("capsule_path", "extract_dir"):
                raw = str(entry.get(key) or "").strip()
                if not raw:
                    continue
                path = assert_under_root(Path(raw))
                if path.is_dir():
                    shutil.rmtree(path)
                    removed_files.append(str(path))
                elif path.exists():
                    path.unlink()
                    removed_files.append(str(path))

        del registry["artifacts"][artifact_id]
        registry["generation"] = int(registry.get("generation", 0)) + 1
        registry["updated_at"] = utc_now_iso()
        _write_registry(registry)
        return {
            "artifact_id": artifact_id,
            "removed": True,
            "preserve_data": bool(preserve_data),
            "removed_files": removed_files,
            "generation": registry["generation"],
        }


def _build_entry(
    descriptor: ArtifactDescriptor,
    *,
    extract_root: Path,
    capsule_id: str,
    capsule_path: Path,
    verified: bool,
    verification_report: Mapping[str, Any] | None,
    descriptor_source: str,
) -> dict[str, Any]:
    integration_status = "disabled"
    resolved_manifest: str | None = None
    integration_reason: str | None = None

    if descriptor.integrated_ui_enabled:
        integration_status = "rejected"
        relative = descriptor.integrated_manifest or ""
        candidate = (extract_root / relative).resolve(strict=False)
        try:
            candidate.relative_to(extract_root.resolve(strict=False))
        except ValueError:
            integration_reason = "integration manifest escapes capsule extraction root"
        else:
            if not verified:
                integration_reason = "capsule was not verified; integrated contribution is not trusted"
            elif descriptor.integrated_contract not in allowed_integration_contracts():
                integration_reason = f"unsupported integration contract: {descriptor.integrated_contract}"
            elif not candidate.is_file():
                integration_reason = f"integration manifest is missing: {relative}"
            else:
                integration_status = "available"
                resolved_manifest = str(candidate)

    runtime_status = "available" if descriptor.backend_entrypoint else "disabled"
    standalone_status = "available" if descriptor.standalone_ui_enabled else "disabled"
    functional = runtime_status == "available" or standalone_status == "available" or descriptor.kind is ArtifactKind.LIBRARY

    return {
        "id": descriptor.artifact_id,
        "version": descriptor.version,
        "kind": descriptor.kind.value,
        "state": "installed",
        "functional": functional,
        "installed_at": utc_now_iso(),
        "capsule_id": capsule_id,
        "capsule_path": str(capsule_path),
        "extract_dir": str(extract_root),
        "descriptor_source": descriptor_source,
        "descriptor": descriptor.to_dict(),
        "runtime": {
            "status": runtime_status,
            "backend_entrypoint": descriptor.backend_entrypoint,
            "healthcheck": descriptor.backend_healthcheck,
        },
        "standalone_ui": {
            "status": standalone_status,
            "enabled": descriptor.standalone_ui_enabled,
            "entrypoint": descriptor.standalone_ui_entrypoint,
        },
        "integration": {
            "status": integration_status,
            "enabled": descriptor.integrated_ui_enabled,
            "manifest": descriptor.integrated_manifest,
            "resolved_manifest": resolved_manifest,
            "contract": descriptor.integrated_contract,
            "reason": integration_reason,
        },
        "dependencies": {
            "required": [item.to_dict() for item in descriptor.required_dependencies],
            "optional_integrations": [item.to_dict() for item in descriptor.optional_integrations],
        },
        "capabilities": {
            "provides": list(descriptor.capabilities_provides),
            "requires": list(descriptor.capabilities_requires),
        },
        "verification": {
            "verified": bool(verified),
            "status": str((verification_report or {}).get("status") or ""),
        },
    }


def _validate_dependencies(descriptor: ArtifactDescriptor, registry: Mapping[str, Any]) -> None:
    artifacts = registry.get("artifacts") or {}
    installed_ids = set(artifacts) if isinstance(artifacts, Mapping) else set()
    missing = sorted(dep.id for dep in descriptor.required_dependencies if dep.id not in installed_ids)
    version_mismatches: list[dict[str, str]] = []
    if isinstance(artifacts, Mapping):
        for dep in descriptor.required_dependencies:
            if dep.id not in artifacts or not dep.version:
                continue
            entry = artifacts.get(dep.id)
            installed_version = str(entry.get("version") or "") if isinstance(entry, Mapping) else ""
            if not _version_satisfies(installed_version, dep.version):
                version_mismatches.append(
                    {
                        "id": dep.id,
                        "required": dep.version,
                        "installed": installed_version,
                    }
                )

    provided: set[str] = set()
    if isinstance(artifacts, Mapping):
        for entry in artifacts.values():
            if isinstance(entry, Mapping):
                caps = entry.get("capabilities") or {}
                if isinstance(caps, Mapping):
                    provided.update(str(item) for item in caps.get("provides", []) or [])
    missing_caps = sorted(set(descriptor.capabilities_requires) - provided)
    if missing or missing_caps or version_mismatches:
        raise ArtifactDependencyError(
            f"artifact dependencies are not satisfied for {descriptor.artifact_id}",
            details={
                "missing_artifacts": missing,
                "missing_capabilities": missing_caps,
                "version_mismatches": version_mismatches,
            },
        )


def _version_satisfies(installed: str, constraint: str) -> bool:
    """Small dependency constraint evaluator for dotted numeric artifact versions."""

    installed_key = _version_key(installed)
    raw = str(constraint).strip()
    for operator in (">=", "<=", ">", "<", "==", "="):
        if raw.startswith(operator):
            required_key = _version_key(raw[len(operator):].strip())
            if operator == ">=":
                return installed_key >= required_key
            if operator == "<=":
                return installed_key <= required_key
            if operator == ">":
                return installed_key > required_key
            if operator == "<":
                return installed_key < required_key
            return installed_key == required_key
    return installed_key == _version_key(raw)


def _version_key(value: str) -> tuple[tuple[int, Any], ...]:
    text = str(value).strip().lower()
    if text.startswith("v") and len(text) > 1 and text[1].isdigit():
        text = text[1:]
    parts = [part for part in re.split(r"[._+-]", text) if part != ""]
    key: list[tuple[int, Any]] = []
    for part in parts:
        if part.isdigit():
            key.append((0, int(part)))
        else:
            key.append((1, part))
    return tuple(key)


def _required_dependents(artifact_id: str, registry: Mapping[str, Any]) -> list[str]:
    result: list[str] = []
    artifacts = registry.get("artifacts") or {}
    if not isinstance(artifacts, Mapping):
        return result
    for other_id, entry in artifacts.items():
        if other_id == artifact_id or not isinstance(entry, Mapping):
            continue
        deps = (entry.get("dependencies") or {}).get("required", []) if isinstance(entry.get("dependencies") or {}, Mapping) else []
        for dep in deps or []:
            dep_id = dep.get("id") if isinstance(dep, Mapping) else dep
            if dep_id == artifact_id:
                result.append(str(other_id))
                break
    return sorted(result)


def _capability_dependents(descriptor: ArtifactDescriptor, artifact_id: str, registry: Mapping[str, Any]) -> list[str]:
    removed_caps = set(descriptor.capabilities_provides)
    if not removed_caps:
        return []
    artifacts = registry.get("artifacts") or {}
    if not isinstance(artifacts, Mapping):
        return []
    other_provided: set[str] = set()
    for other_id, entry in artifacts.items():
        if other_id == artifact_id or not isinstance(entry, Mapping):
            continue
        caps = entry.get("capabilities") or {}
        if isinstance(caps, Mapping):
            other_provided.update(str(item) for item in caps.get("provides", []) or [])
    exclusively_removed = removed_caps - other_provided
    result: list[str] = []
    for other_id, entry in artifacts.items():
        if other_id == artifact_id or not isinstance(entry, Mapping):
            continue
        caps = entry.get("capabilities") or {}
        if isinstance(caps, Mapping) and exclusively_removed.intersection(str(item) for item in caps.get("requires", []) or []):
            result.append(str(other_id))
    return sorted(result)


def _instances_using_capsule(capsule_id: str) -> list[str]:
    if not capsule_id:
        return []
    result: list[str] = []
    root = instances_dir()
    if not root.exists():
        return result
    for state_file in root.glob("*/state/instance-state.json"):
        try:
            payload = json.loads(state_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if _mapping_contains_value(payload, "capsule_id", capsule_id):
            result.append(state_file.parents[1].name)
    return sorted(set(result))


def _mapping_contains_value(value: Any, key: str, expected: str) -> bool:
    if isinstance(value, Mapping):
        if str(value.get(key) or "") == expected:
            return True
        return any(_mapping_contains_value(item, key, expected) for item in value.values())
    if isinstance(value, list):
        return any(_mapping_contains_value(item, key, expected) for item in value)
    return False


def _legacy_descriptor(extract_root: Path) -> ArtifactDescriptor:
    manifest_path = extract_root / "manifest.yaml"
    app_name = "Konnaxion"
    app_version = "v14"
    if manifest_path.is_file():
        try:
            payload = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
            if isinstance(payload, Mapping):
                app_name = str(payload.get("app_name") or app_name)
                app_version = str(payload.get("app_version") or app_version)
        except (OSError, yaml.YAMLError):
            pass
    if app_name.strip().lower() == "konnaxion":
        return default_konnaxion_descriptor(version=app_version)
    artifact_id = re.sub(r"[^a-z0-9_.-]+", "-", app_name.strip().lower()).strip("-.") or "legacy-product"
    return ArtifactDescriptor.from_mapping(
        {
            "schema_version": "kx-artifact-descriptor/v1",
            "artifact": {"id": artifact_id, "version": app_version, "kind": "product"},
            "lifecycle": {"removable": True, "standalone": True},
            "runtime": {"backend": {}},
            "ui": {"standalone": {"enabled": False}, "integrated": {"enabled": False}},
            "dependencies": {"required": [], "optional_integrations": []},
            "capabilities": {"provides": [], "requires": []},
        }
    )


def _empty_registry() -> dict[str, Any]:
    return {
        "schema_version": REGISTRY_SCHEMA_VERSION,
        "generation": 0,
        "updated_at": utc_now_iso(),
        "artifacts": {},
    }


def _write_registry(data: Mapping[str, Any]) -> None:
    path = artifact_registry_file()
    ensure_dir(path.parent)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(dict(data), indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def _safe_id(value: str) -> str:
    normalized = str(value).strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]*", normalized):
        raise ArtifactRegistryError(f"invalid artifact id: {value!r}")
    return normalized


__all__ = [
    "DEFAULT_ALLOWED_INTEGRATION_CONTRACTS",
    "REGISTRY_SCHEMA_VERSION",
    "ArtifactDependencyError",
    "ArtifactRegistryError",
    "ArtifactRemovalBlockedError",
    "allowed_integration_contracts",
    "ensure_registry",
    "get_artifact",
    "get_integration_manifest",
    "list_artifacts",
    "load_registry",
    "register_artifact_from_capsule",
    "remove_artifact",
]
