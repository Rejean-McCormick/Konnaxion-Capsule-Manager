from __future__ import annotations

import shutil
from pathlib import Path

import yaml

from kx_builder.package import _stage_capsule_from_source
from kx_shared.artifact_descriptor import ArtifactDescriptor, load_artifact_descriptor


def test_builder_generates_default_konnaxion_artifact_descriptor(tmp_path: Path) -> None:
    source = tmp_path / "source"
    staging = tmp_path / "staging"
    source.mkdir()
    staging.mkdir()
    repo_root = Path(__file__).resolve().parents[1]
    shutil.copy2(repo_root / "templates" / "docker-compose.capsule.yml", source / "docker-compose.capsule.yml")

    _stage_capsule_from_source(
        source,
        staging,
        channel="local",
        capsule_id="konnaxion-v14-local-test",
        capsule_version="test.1",
        profile="local_only",
        sign=False,
        build_images=False,
    )

    item = load_artifact_descriptor(staging / "artifact.yaml")
    manifest = yaml.safe_load((staging / "manifest.yaml").read_text(encoding="utf-8"))

    assert item.artifact_id == "konnaxion"
    assert item.kind.value == "product"
    assert item.standalone_ui_enabled is True
    assert item.integrated_ui_enabled is False
    assert manifest["artifact_descriptor"] == "artifact.yaml"


def test_builder_copies_product_owned_integration_manifest(tmp_path: Path) -> None:
    source = tmp_path / "source"
    staging = tmp_path / "staging"
    source.mkdir()
    staging.mkdir()
    repo_root = Path(__file__).resolve().parents[1]
    shutil.copy2(repo_root / "templates" / "docker-compose.capsule.yml", source / "docker-compose.capsule.yml")

    descriptor = {
        "schema_version": "kx-artifact-descriptor/v1",
        "artifact": {"id": "orgo", "version": "1.4.0", "kind": "product"},
        "lifecycle": {"removable": True, "standalone": True},
        "runtime": {"backend": {"entrypoint": "/orgo/api", "healthcheck": "/orgo/health"}},
        "ui": {
            "standalone": {"enabled": True, "entrypoint": "/orgo"},
            "integrated": {
                "enabled": True,
                "manifest": "koali-integration.yaml",
                "contract": "koali-ui/v1",
            },
        },
        "dependencies": {"required": [], "optional_integrations": []},
        "capabilities": {"provides": ["orgo.cases"], "requires": ["koali-ui-contract"]},
    }
    (source / "capsule-artifact.yaml").write_text(yaml.safe_dump(descriptor, sort_keys=False), encoding="utf-8")
    (source / "koali-integration.yaml").write_text("surfaces:\n  - id: cases\n", encoding="utf-8")

    # Build dependency validation belongs to install/registry, not Builder staging.
    _stage_capsule_from_source(
        source,
        staging,
        channel="local",
        capsule_id="orgo-local-test",
        capsule_version="1.4.0",
        profile="local_only",
        sign=False,
        build_images=False,
    )

    item = load_artifact_descriptor(staging / "artifact.yaml")
    assert item.integrated_manifest == "contributions/koali-integration.yaml"
    assert (staging / item.integrated_manifest).is_file()
