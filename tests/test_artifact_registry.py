from __future__ import annotations

import json
from pathlib import Path
import shutil

import pytest
import yaml

from kx_agent.artifacts.registry import (
    ArtifactRemovalBlockedError,
    get_artifact,
    get_integration_manifest,
    list_artifacts,
    register_artifact_from_capsule,
    remove_artifact,
)
from kx_shared.artifact_descriptor import (
    ARTIFACT_DESCRIPTOR_SCHEMA_VERSION,
    ArtifactDescriptor,
    ArtifactDescriptorError,
    write_artifact_descriptor,
)
from kx_shared.konnaxion_constants import KX_ROOT
from kx_shared.paths import artifact_registry_file


@pytest.fixture()
def runtime_root() -> Path:
    root = Path(str(KX_ROOT))
    root.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(root / "shared" / "registry", ignore_errors=True)
    for path in (root / "capsules").glob("cap-*.kxcap") if (root / "capsules").exists() else ():
        path.unlink(missing_ok=True)
    capsule_extracts = root / "shared" / "capsules"
    if capsule_extracts.exists():
        for path in capsule_extracts.glob("cap-*"):
            shutil.rmtree(path, ignore_errors=True)
    return root


def descriptor(
    artifact_id: str,
    *,
    kind: str = "product",
    required: list[object] | None = None,
    optional_integrations: list[object] | None = None,
    provides: list[str] | None = None,
    requires: list[str] | None = None,
    integrated: dict | None = None,
) -> ArtifactDescriptor:
    return ArtifactDescriptor.from_mapping(
        {
            "schema_version": ARTIFACT_DESCRIPTOR_SCHEMA_VERSION,
            "artifact": {"id": artifact_id, "version": "1.0.0", "kind": kind},
            "lifecycle": {"removable": True, "standalone": kind == "product"},
            "runtime": {"backend": {"entrypoint": f"/{artifact_id}/api"} if kind == "product" else {}},
            "ui": {
                "standalone": {
                    "enabled": kind == "product",
                    "entrypoint": f"/{artifact_id}" if kind == "product" else None,
                },
                "integrated": integrated or {"enabled": False},
            },
            "dependencies": {
                "required": required or [],
                "optional_integrations": optional_integrations or [],
            },
            "capabilities": {"provides": provides or [], "requires": requires or []},
        }
    )


def install(runtime_root: Path, item: ArtifactDescriptor, *, verified: bool = True) -> dict:
    extract = runtime_root / "shared" / "capsules" / f"cap-{item.artifact_id}"
    extract.mkdir(parents=True, exist_ok=True)
    write_artifact_descriptor(item, extract / "artifact.yaml")
    capsule = runtime_root / "capsules" / f"cap-{item.artifact_id}.kxcap"
    capsule.parent.mkdir(parents=True, exist_ok=True)
    capsule.write_bytes(b"test capsule")
    return register_artifact_from_capsule(
        extract_dir=extract,
        capsule_id=f"cap-{item.artifact_id}",
        capsule_path=capsule,
        verified=verified,
        verification_report={"status": "PASS" if verified else "SKIPPED"},
    )


def test_private_cross_artifact_ui_dependency_is_rejected() -> None:
    with pytest.raises(ArtifactDescriptorError, match="private cross-artifact dependency"):
        descriptor(
            "orgo",
            required=[{"id": "konnaxion", "interface": "private"}],
        )


def test_library_is_installed_but_not_product_switcher_candidate(runtime_root: Path) -> None:
    install(runtime_root, descriptor("koali-ui", kind="library", provides=["koali-ui-contract"]))
    install(runtime_root, descriptor("orgo", requires=["koali-ui-contract"]))

    all_items = list_artifacts()
    products = list_artifacts(products_only=True)

    assert {item["id"] for item in all_items["items"]} == {"koali-ui", "orgo"}
    assert [item["id"] for item in products["items"]] == ["orgo"]
    assert products["generation"] == 2




def test_required_dependency_version_is_enforced(runtime_root: Path) -> None:
    install(runtime_root, descriptor("koali-ui", kind="library"))
    bad = descriptor("orgo", required=[{"id": "koali-ui", "version": ">=2.0.0"}])

    from kx_agent.artifacts.registry import ArtifactDependencyError

    with pytest.raises(ArtifactDependencyError) as exc_info:
        install(runtime_root, bad)

    assert exc_info.value.details["version_mismatches"][0]["id"] == "koali-ui"

def test_optional_integration_does_not_block_install_or_removal(runtime_root: Path) -> None:
    orgo = descriptor(
        "orgo",
        optional_integrations=[{"product": "konnaxion", "capability": "konnaxion.case-link"}],
    )
    install(runtime_root, orgo)

    assert get_artifact("orgo")["functional"] is True
    result = remove_artifact("orgo")
    assert result["removed"] is True


def test_required_dependency_blocks_removal(runtime_root: Path) -> None:
    install(runtime_root, descriptor("koali-ui", kind="library"))
    install(runtime_root, descriptor("orgo", required=[{"id": "koali-ui"}]))

    with pytest.raises(ArtifactRemovalBlockedError) as exc_info:
        remove_artifact("koali-ui")

    assert exc_info.value.details["required_dependents"] == ["orgo"]
    assert get_artifact("koali-ui")["state"] == "installed"


def test_unknown_integration_contract_is_rejected_without_breaking_product(runtime_root: Path) -> None:
    item = descriptor(
        "orgo",
        integrated={
            "enabled": True,
            "manifest": "contributions/orgo.yaml",
            "contract": "unknown-ui/v9",
        },
    )
    extract = runtime_root / "shared" / "capsules" / "cap-orgo"
    (extract / "contributions").mkdir(parents=True, exist_ok=True)
    (extract / "contributions" / "orgo.yaml").write_text("routes: []\n", encoding="utf-8")
    write_artifact_descriptor(item, extract / "artifact.yaml")
    capsule = runtime_root / "capsules" / "cap-orgo.kxcap"
    capsule.parent.mkdir(parents=True, exist_ok=True)
    capsule.write_bytes(b"test")

    entry = register_artifact_from_capsule(
        extract_dir=extract,
        capsule_id="cap-orgo",
        capsule_path=capsule,
        verified=True,
        verification_report={"status": "PASS"},
    )

    assert entry["functional"] is True
    assert entry["standalone_ui"]["status"] == "available"
    assert entry["integration"]["status"] == "rejected"
    assert "unsupported integration contract" in entry["integration"]["reason"]


def test_valid_public_integration_manifest_is_discoverable_without_ux_interpretation(runtime_root: Path) -> None:
    item = descriptor(
        "orgo",
        integrated={
            "enabled": True,
            "manifest": "contributions/orgo.yaml",
            "contract": "koali-ui/v1",
        },
    )
    extract = runtime_root / "shared" / "capsules" / "cap-orgo"
    (extract / "contributions").mkdir(parents=True, exist_ok=True)
    manifest_payload = {"routes": [{"id": "cases", "path": "/cases"}], "commands": ["new-case"]}
    (extract / "contributions" / "orgo.yaml").write_text(
        yaml.safe_dump(manifest_payload, sort_keys=False), encoding="utf-8"
    )
    write_artifact_descriptor(item, extract / "artifact.yaml")
    capsule = runtime_root / "capsules" / "cap-orgo.kxcap"
    capsule.parent.mkdir(parents=True, exist_ok=True)
    capsule.write_bytes(b"test")

    entry = register_artifact_from_capsule(
        extract_dir=extract,
        capsule_id="cap-orgo",
        capsule_path=capsule,
        verified=True,
        verification_report={"status": "PASS"},
    )
    public_manifest = get_integration_manifest("orgo")

    assert entry["integration"]["status"] == "available"
    assert public_manifest["contract"] == "koali-ui/v1"
    assert public_manifest["manifest"] == manifest_payload


def test_registry_is_persistent_and_generation_is_monotonic(runtime_root: Path) -> None:
    install(runtime_root, descriptor("orgo"))
    first = json.loads(artifact_registry_file().read_text(encoding="utf-8"))
    install(runtime_root, descriptor("konnaxion"))
    second = json.loads(artifact_registry_file().read_text(encoding="utf-8"))

    assert first["generation"] == 1
    assert second["generation"] == 2
    assert set(second["artifacts"]) == {"orgo", "konnaxion"}


def test_composition_candidates_only_returns_admitted_product_contributions(runtime_root: Path) -> None:
    install(runtime_root, descriptor("konnaxion"))

    orgo = descriptor(
        "orgo",
        integrated={
            "enabled": True,
            "manifest": "contributions/orgo.yaml",
            "contract": "koali-ui/v1",
        },
    )
    extract = runtime_root / "shared" / "capsules" / "cap-orgo"
    (extract / "contributions").mkdir(parents=True, exist_ok=True)
    (extract / "contributions" / "orgo.yaml").write_text(
        "surfaces: []\n", encoding="utf-8"
    )
    write_artifact_descriptor(orgo, extract / "artifact.yaml")
    capsule = runtime_root / "capsules" / "cap-orgo.kxcap"
    capsule.parent.mkdir(parents=True, exist_ok=True)
    capsule.write_bytes(b"test")
    register_artifact_from_capsule(
        extract_dir=extract,
        capsule_id="cap-orgo",
        capsule_path=capsule,
        verified=True,
        verification_report={"status": "PASS"},
    )

    candidates = list_artifacts(composition_candidates_only=True)

    assert candidates["generation"] == 2
    assert [item["id"] for item in candidates["items"]] == ["orgo"]
    assert candidates["items"][0]["integration"]["status"] == "available"
