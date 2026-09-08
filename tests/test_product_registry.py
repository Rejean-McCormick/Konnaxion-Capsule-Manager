"""Tests for Capsule product discovery/admission and lifecycle dependency rules."""

from __future__ import annotations

from pathlib import Path

import pytest

from kx_agent.product_registry import ArtifactDependencyError, ProductRegistry
from kx_shared.product_contract import (
    ArtifactDependency,
    ArtifactKind,
    IntegrationDescriptor,
    IntegrationStatus,
    OptionalIntegration,
    ProductStatus,
    build_product_descriptor,
)


def make_product(
    artifact_id: str,
    *,
    integration: IntegrationDescriptor | None = None,
    required_dependencies: tuple[ArtifactDependency, ...] = (),
    optional_integrations: tuple[OptionalIntegration, ...] = (),
):
    return build_product_descriptor(
        artifact_id=artifact_id,
        artifact_kind=ArtifactKind.PRODUCT,
        version="1.0.0",
        display_name=artifact_id.title(),
        runtime_entrypoint="/",
        standalone_entrypoint="/",
        integration=integration,
        required_dependencies=required_dependencies,
        optional_integrations=optional_integrations,
    )


def test_spaces_absent_does_not_make_product_unhealthy(tmp_path: Path) -> None:
    registry = ProductRegistry(tmp_path / "registry.json")
    record = registry.register(make_product("orgo"), capsule_root=tmp_path, trusted=True)

    assert record.readiness.product_status == ProductStatus.FUNCTIONAL
    assert record.admission.status == IntegrationStatus.UNAVAILABLE

    projection = registry.public_products_projection()
    assert projection["products"][0]["state"] == "functional"
    assert projection["products"][0]["integrated"]["available"] is False
    assert projection["products"][0]["standalone"]["available"] is True


def test_invalid_integration_manifest_rejects_only_integration(tmp_path: Path) -> None:
    descriptor = make_product(
        "orgo",
        integration=IntegrationDescriptor(
            manifest="metadata/koali-integration.yaml",
            contract="koali-ui/v1",
        ),
    )
    registry = ProductRegistry(tmp_path / "registry.json")
    record = registry.register(descriptor, capsule_root=tmp_path, trusted=True)

    assert record.readiness.product_status == ProductStatus.FUNCTIONAL
    assert record.admission.status == IntegrationStatus.REJECTED
    assert "missing" in (record.admission.reason or "")


def test_unsupported_integration_contract_does_not_break_standalone(tmp_path: Path) -> None:
    (tmp_path / "metadata").mkdir()
    (tmp_path / "metadata" / "koali-integration.yaml").write_text("surfaces: []\n")
    descriptor = make_product(
        "orgo",
        integration=IntegrationDescriptor(
            manifest="metadata/koali-integration.yaml",
            contract="koali-ui/v99",
        ),
    )
    registry = ProductRegistry(tmp_path / "registry.json")
    record = registry.register(descriptor, capsule_root=tmp_path, trusted=True)

    assert record.readiness.product_status == ProductStatus.FUNCTIONAL
    assert record.admission.status == IntegrationStatus.REJECTED
    assert "not supported" in (record.admission.reason or "")


def test_admission_requires_trust_and_host_capabilities(tmp_path: Path) -> None:
    (tmp_path / "metadata").mkdir()
    (tmp_path / "metadata" / "koali-integration.yaml").write_text("surfaces: []\n")
    descriptor = make_product(
        "orgo",
        integration=IntegrationDescriptor(
            manifest="metadata/koali-integration.yaml",
            contract="koali-ui/v1",
            required_capabilities=("local_module_surface",),
        ),
    )

    untrusted = ProductRegistry(tmp_path / "untrusted.json", host_capabilities={"local_module_surface"})
    untrusted_record = untrusted.register(descriptor, capsule_root=tmp_path, trusted=False)
    assert untrusted_record.admission.status == IntegrationStatus.REJECTED
    assert "trust" in (untrusted_record.admission.reason or "")

    missing_capability = ProductRegistry(tmp_path / "missing-cap.json")
    missing_record = missing_capability.register(descriptor, capsule_root=tmp_path, trusted=True)
    assert missing_record.admission.status == IntegrationStatus.REJECTED
    assert "capabilities" in (missing_record.admission.reason or "")

    admitted = ProductRegistry(
        tmp_path / "admitted.json", host_capabilities={"local_module_surface"}
    )
    admitted_record = admitted.register(descriptor, capsule_root=tmp_path, trusted=True)
    assert admitted_record.admission.status == IntegrationStatus.READY


def test_required_dependency_blocks_removal_with_dependent_list(tmp_path: Path) -> None:
    registry = ProductRegistry(tmp_path / "registry.json")
    library = build_product_descriptor(
        artifact_id="koali-ui",
        artifact_kind=ArtifactKind.LIBRARY,
        version="2.0.0",
    )
    orgo = make_product(
        "orgo",
        required_dependencies=(ArtifactDependency("koali-ui", ">=2"),),
    )
    registry.register(library, capsule_root=tmp_path)
    registry.register(orgo, capsule_root=tmp_path)

    with pytest.raises(ArtifactDependencyError) as exc:
        registry.remove("koali-ui")

    assert exc.value.dependents == ("orgo",)


def test_optional_dependency_does_not_block_removal(tmp_path: Path) -> None:
    registry = ProductRegistry(tmp_path / "registry.json")
    konnaxion = make_product("konnaxion")
    orgo = make_product(
        "orgo",
        optional_integrations=(OptionalIntegration("konnaxion", "topic.read"),),
    )
    registry.register(konnaxion, capsule_root=tmp_path)
    registry.register(orgo, capsule_root=tmp_path)

    registry.remove("konnaxion")

    assert "orgo" in registry.records
    assert "konnaxion" not in registry.records


def test_public_product_switcher_projection_excludes_libraries_and_hosts(tmp_path: Path) -> None:
    registry = ProductRegistry(tmp_path / "registry.json")
    registry.register(
        build_product_descriptor(
            artifact_id="koali-ui",
            artifact_kind="library",
            version="2",
        ),
        capsule_root=tmp_path,
    )
    registry.register(make_product("orgo"), capsule_root=tmp_path)
    registry.register(
        build_product_descriptor(
            artifact_id="koa-spaces",
            artifact_kind="composition_host",
            version="1",
            runtime_entrypoint="/spaces",
            standalone_entrypoint="/spaces",
        ),
        capsule_root=tmp_path,
    )

    public = registry.public_products_projection()
    full = registry.full_projection()

    assert [item["id"] for item in public["products"]] == ["orgo"]
    assert [item["id"] for item in full["libraries"]] == ["koali-ui"]
    assert [item["id"] for item in full["composition_hosts"]] == ["koa-spaces"]


def test_registry_generation_changes_only_on_semantic_change_and_survives_restart(tmp_path: Path) -> None:
    path = tmp_path / "registry.json"
    registry = ProductRegistry(path)
    descriptor = make_product("orgo")

    registry.register(descriptor, capsule_root=tmp_path)
    first_generation = registry.generation
    assert first_generation == 1

    registry.register(descriptor, capsule_root=tmp_path)
    assert registry.generation == first_generation

    restarted = ProductRegistry(path)
    assert restarted.generation == first_generation
    assert restarted.public_products_projection() == registry.public_products_projection()

    restarted.register(make_product("konnaxion"), capsule_root=tmp_path)
    assert restarted.generation == first_generation + 1


def test_register_extracted_product_reads_signed_metadata_location(tmp_path: Path) -> None:
    from kx_agent.product_registry import register_extracted_product
    from kx_shared.product_contract import write_product_descriptor

    root = tmp_path / "capsule"
    descriptor_path = root / "metadata" / "product.yaml"
    write_product_descriptor(make_product("orgo"), descriptor_path)

    registry = ProductRegistry(tmp_path / "registry.json")
    record = register_extracted_product(root, trusted=True, registry=registry)

    assert record is not None
    assert record.descriptor.artifact.id == "orgo"
    assert registry.generation == 1
