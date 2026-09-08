"""Contract tests for Capsule deployment/discovery product descriptors."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from kx_shared.product_contract import (
    ArtifactDependency,
    ArtifactKind,
    IntegrationDescriptor,
    OptionalIntegration,
    PRODUCT_DESCRIPTOR_SCHEMA_VERSION,
    ProductDescriptorError,
    ProductStatus,
    ReadinessStatus,
    build_product_descriptor,
    derive_product_status,
    descriptor_from_mapping,
    load_product_descriptor,
    write_product_descriptor,
)


def test_three_artifact_kinds_are_canonical() -> None:
    assert {item.value for item in ArtifactKind} == {
        "library",
        "product",
        "composition_host",
    }


def test_product_descriptor_round_trip(tmp_path: Path) -> None:
    descriptor = build_product_descriptor(
        artifact_id="orgo",
        artifact_kind=ArtifactKind.PRODUCT,
        version="1.4.0",
        display_name="Orgo",
        runtime_entrypoint="/",
        standalone_entrypoint="/",
        required_dependencies=(ArtifactDependency("koali-ui", ">=2"),),
        optional_integrations=(OptionalIntegration("konnaxion", "topic.read"),),
        capabilities=("case.read", "task.execute"),
        integration=IntegrationDescriptor(
            manifest="metadata/koali-integration.yaml",
            contract="koali-ui/v1",
            required_capabilities=("local_module_surface",),
        ),
    )

    path = write_product_descriptor(descriptor, tmp_path / "product.yaml")
    loaded = load_product_descriptor(path)

    assert loaded == descriptor
    assert loaded.schema_version == PRODUCT_DESCRIPTOR_SCHEMA_VERSION
    assert loaded.artifact.kind == ArtifactKind.PRODUCT
    assert loaded.required_dependencies[0].id == "koali-ui"


def test_product_requires_runtime_and_standalone_entrypoints() -> None:
    with pytest.raises(ProductDescriptorError, match="runtime.entrypoint"):
        descriptor_from_mapping(
            {
                "schema_version": PRODUCT_DESCRIPTOR_SCHEMA_VERSION,
                "artifact": {"id": "orgo", "kind": "product", "version": "1"},
                "standalone": {"entrypoint": "/"},
            }
        )

    with pytest.raises(ProductDescriptorError, match="standalone.entrypoint"):
        descriptor_from_mapping(
            {
                "schema_version": PRODUCT_DESCRIPTOR_SCHEMA_VERSION,
                "artifact": {"id": "orgo", "kind": "product", "version": "1"},
                "runtime": {"entrypoint": "/"},
            }
        )


def test_library_does_not_need_product_ui_entrypoint() -> None:
    descriptor = build_product_descriptor(
        artifact_id="koali-ui",
        artifact_kind=ArtifactKind.LIBRARY,
        version="2.0.0",
    )

    assert descriptor.standalone is None
    assert descriptor.runtime is None


def test_descriptor_refuses_product_ux_definition() -> None:
    with pytest.raises(ProductDescriptorError, match="must not contain product UX"):
        descriptor_from_mapping(
            {
                "schema_version": PRODUCT_DESCRIPTOR_SCHEMA_VERSION,
                "artifact": {"id": "orgo", "kind": "product", "version": "1"},
                "runtime": {"entrypoint": "/"},
                "standalone": {"entrypoint": "/"},
                "navigation": [{"label": "Cases"}],
            }
        )


def test_integration_manifest_must_be_relative() -> None:
    with pytest.raises(ProductDescriptorError, match="safe relative path"):
        build_product_descriptor(
            artifact_id="orgo",
            artifact_kind="product",
            version="1",
            runtime_entrypoint="/",
            standalone_entrypoint="/",
            integration=IntegrationDescriptor(
                manifest="../koali-integration.yaml",
                contract="koali-ui/v1",
            ),
        )


def test_product_status_does_not_depend_on_spaces_integration() -> None:
    assert (
        derive_product_status(ReadinessStatus.READY, ReadinessStatus.READY)
        == ProductStatus.FUNCTIONAL
    )
    assert (
        derive_product_status(ReadinessStatus.UNAVAILABLE, ReadinessStatus.READY)
        == ProductStatus.UNAVAILABLE
    )


def test_konnaxion_builder_emits_default_product_descriptor(tmp_path: Path) -> None:
    from kx_builder.package import _write_product_descriptor_file

    source = tmp_path / "source"
    staging = tmp_path / "staging"
    source.mkdir()
    (staging / "metadata").mkdir(parents=True)

    path = _write_product_descriptor_file(
        staging,
        source_dir=source,
        capsule_id="konnaxion-v14-demo-2026.04.30",
        capsule_version="2026.04.30-demo.1",
    )
    descriptor = load_product_descriptor(path)

    assert descriptor.artifact.id == "konnaxion"
    assert descriptor.artifact.kind == ArtifactKind.PRODUCT
    assert descriptor.runtime is not None
    assert descriptor.standalone is not None
    assert descriptor.integration is None
