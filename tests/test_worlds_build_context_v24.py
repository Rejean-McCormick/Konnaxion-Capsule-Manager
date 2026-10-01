from __future__ import annotations

from pathlib import Path

import pytest

from kx_builder.package import PackageError, _stage_worlds_dependency


def _make_worlds_repo(root: Path) -> Path:
    backend = root / "Konnaxion_Worlds" / "backend"
    package = backend / "konnaxion" / "worlds"
    (package / "migrations").mkdir(parents=True)
    (package / "apps.py").write_text("WORLD_SENTINEL = 'canonical'\n", encoding="utf-8")
    (package / "migrations" / "0001_initial.py").write_text("# migration\n", encoding="utf-8")
    (backend / "pyproject.toml").write_text("[project]\nname='konnaxion-worlds'\n", encoding="utf-8")
    seeds = backend / "seed-data" / "worlds" / "demo-alpha"
    seeds.mkdir(parents=True)
    (seeds / "world.yaml").write_text("slug: demo-alpha\n", encoding="utf-8")
    return backend


def test_worlds_is_staged_from_canonical_sibling_without_mutating_main_repo(tmp_path: Path) -> None:
    source = tmp_path / "Konnaxion"
    backend_context = tmp_path / "context"
    (source / "backend").mkdir(parents=True)
    (backend_context / "konnaxion").mkdir(parents=True)
    _make_worlds_repo(tmp_path)

    staged = _stage_worlds_dependency(source, backend_context)

    assert staged == backend_context / "konnaxion" / "worlds"
    assert (staged / "apps.py").read_text(encoding="utf-8") == "WORLD_SENTINEL = 'canonical'\n"
    assert (backend_context / "seed-data" / "worlds" / "demo-alpha" / "world.yaml").is_file()
    assert not (source / "backend" / "konnaxion" / "worlds").exists()


def test_worlds_source_can_be_overridden_with_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "Konnaxion"
    backend_context = tmp_path / "context"
    (source / "backend").mkdir(parents=True)
    (backend_context / "konnaxion").mkdir(parents=True)
    external = _make_worlds_repo(tmp_path / "external-root")
    monkeypatch.setenv("KX_WORLDS_SOURCE_DIR", str(external.parent))

    staged = _stage_worlds_dependency(source, backend_context)
    assert (staged / "apps.py").is_file()


def test_worlds_missing_is_a_hard_build_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "Konnaxion"
    backend_context = tmp_path / "context"
    (source / "backend").mkdir(parents=True)
    (backend_context / "konnaxion").mkdir(parents=True)
    monkeypatch.delenv("KX_WORLDS_SOURCE_DIR", raising=False)
    monkeypatch.setattr(
        "kx_builder.package._resolve_worlds_backend_candidate",
        lambda _path: None,
    )

    with pytest.raises(PackageError, match="Konnaxion_Worlds"):
        _stage_worlds_dependency(source, backend_context)
