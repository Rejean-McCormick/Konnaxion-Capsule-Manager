from __future__ import annotations

from pathlib import Path

import pytest

from kx_builder.package import (
    KX_WORLDS_COMPOSITION_MODE,
    PackageError,
    _stage_worlds_distribution_dependency,
)


def _make_backend_context(root: Path) -> Path:
    context = root / "context"
    requirements = context / "requirements"
    requirements.mkdir(parents=True)
    (requirements / "production.txt").write_text("-r base.txt\n", encoding="utf-8")
    (requirements / "base.txt").write_text("Django==5.1.9\n", encoding="utf-8")
    (context / "konnaxion").mkdir()
    return context


def _make_worlds_repo(root: Path) -> Path:
    backend = root / "Konnaxion_Worlds" / "backend"
    package = backend / "konnaxion" / "worlds"
    (package / "migrations").mkdir(parents=True)
    (package / "apps.py").write_text("WORLD_SENTINEL = 'canonical'\n", encoding="utf-8")
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "migrations" / "0001_initial.py").write_text("# migration\n", encoding="utf-8")
    (backend / "pyproject.toml").write_text(
        "[build-system]\n"
        "requires=['setuptools>=69','wheel']\n"
        "build-backend='setuptools.build_meta'\n"
        "[project]\n"
        "name='konnaxion-worlds'\n"
        "version='0.3.7'\n"
        "[tool.setuptools.packages.find]\n"
        "where=['.']\n"
        "include=['konnaxion.worlds*']\n"
        "namespaces=true\n",
        encoding="utf-8",
    )
    # V3 source of truth: the Universe owns its nested World pack.
    universe = backend.parent / "universes" / "demo-universe"
    worlds = universe / "worlds" / "demo-alpha"
    (worlds / "scenarios").mkdir(parents=True)
    (universe / "universe.yaml").write_text(
        "universe_contract: kx-universe-pack/v1\n"
        "universe_key: demo-universe\n"
        "title: Demo\n"
        "pack_version: 1.0.0\n"
        "visibility: private\n"
        "worlds:\n"
        "- world_key: demo-alpha\n"
        "  seed_pack_key: demo-alpha\n"
        "  seed_version: 1.0.0\n",
        encoding="utf-8",
    )
    (worlds / "world.yaml").write_text(
        "world_contract: kx-world-pack/v1\n"
        "world_key: demo-alpha\n"
        "title: Alpha\n"
        "pack_version: 1.0.0\n"
        "scenarios:\n"
        "- scenarios/bootstrap.json\n",
        encoding="utf-8",
    )
    (worlds / "scenarios" / "bootstrap.json").write_text("{}\n", encoding="utf-8")
    return backend


def test_worlds_is_composed_as_installed_distribution_without_vendoring_main_repo(
    tmp_path: Path,
) -> None:
    source = tmp_path / "Konnaxion"
    (source / "backend").mkdir(parents=True)
    backend_context = _make_backend_context(tmp_path)
    _make_worlds_repo(tmp_path)

    staged = _stage_worlds_distribution_dependency(source, backend_context)

    assert KX_WORLDS_COMPOSITION_MODE == "installed-distribution-v1"
    assert staged == backend_context / "requirements" / "vendor" / "konnaxion-worlds"
    assert (staged / "pyproject.toml").is_file()
    assert (staged / "konnaxion" / "worlds" / "apps.py").is_file()
    assert "./vendor/konnaxion-worlds" in (
        backend_context / "requirements" / "production.txt"
    ).read_text(encoding="utf-8")
    assert (
        backend_context / "seed-data" / "universes" / "demo-universe" / "worlds"
        / "demo-alpha" / "world.yaml"
    ).is_file()
    assert not (backend_context / "konnaxion" / "worlds").exists()
    assert not (source / "backend" / "konnaxion" / "worlds").exists()


def test_worlds_source_can_be_overridden_with_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "Konnaxion"
    (source / "backend").mkdir(parents=True)
    backend_context = _make_backend_context(tmp_path)
    external = _make_worlds_repo(tmp_path / "external-root")
    monkeypatch.setenv("KX_WORLDS_SOURCE_DIR", str(external.parent))

    staged = _stage_worlds_distribution_dependency(source, backend_context)
    assert (staged / "konnaxion" / "worlds" / "apps.py").is_file()


def test_worlds_missing_is_a_hard_build_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "Konnaxion"
    (source / "backend").mkdir(parents=True)
    backend_context = _make_backend_context(tmp_path)
    monkeypatch.delenv("KX_WORLDS_SOURCE_DIR", raising=False)
    monkeypatch.setattr(
        "kx_builder.package._resolve_worlds_backend_candidate",
        lambda _path: None,
    )

    with pytest.raises(PackageError, match="Konnaxion_Worlds"):
        _stage_worlds_distribution_dependency(source, backend_context)


def test_flat_only_worlds_checkout_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "Konnaxion"
    (source / "backend").mkdir(parents=True)
    context = _make_backend_context(tmp_path)
    backend = _make_worlds_repo(tmp_path)
    import shutil

    shutil.rmtree(backend.parent / "universes")
    (backend / "seed-data" / "worlds" / "demo-alpha").mkdir(parents=True)
    with pytest.raises(PackageError, match="nested Universe catalog is missing"):
        _stage_worlds_distribution_dependency(source, context)


def test_builder_replaces_stale_backend_universe_packs_only_in_context(tmp_path: Path) -> None:
    source = tmp_path / "Konnaxion"
    (source / "backend").mkdir(parents=True)
    context = _make_backend_context(tmp_path)
    backend = _make_worlds_repo(tmp_path)
    old = context / "seed-data" / "universes" / "demo-universe"
    old.mkdir(parents=True)
    (old / "universe.yaml").write_text("OLD STALE PACK", encoding="utf-8")
    (old / "worlds" / "orphan").mkdir(parents=True)
    (old / "worlds" / "orphan" / "world.yaml").write_text("OLD", encoding="utf-8")
    legacy = context / "seed-data" / "worlds" / "legacy"
    legacy.mkdir(parents=True)
    (legacy / "world.yaml").write_text("KEEP", encoding="utf-8")

    _stage_worlds_distribution_dependency(source, context)
    assert (context / "seed-data" / "universes" / "demo-universe" / "universe.yaml").read_text().startswith("universe_contract:")
    assert not (context / "seed-data" / "universes" / "demo-universe" / "worlds" / "orphan").exists()
    assert (legacy / "world.yaml").read_text() == "KEEP"
    assert (backend.parent / "universes" / "demo-universe" / "worlds" / "demo-alpha" / "world.yaml").is_file()


def test_missing_nested_world_fails_closed(tmp_path: Path) -> None:
    source = tmp_path / "Konnaxion"
    (source / "backend").mkdir(parents=True)
    context = _make_backend_context(tmp_path)
    backend = _make_worlds_repo(tmp_path)
    (backend.parent / "universes" / "demo-universe" / "worlds" / "demo-alpha" / "world.yaml").unlink()
    with pytest.raises(PackageError, match="Missing or unsafe nested World manifest"):
        _stage_worlds_distribution_dependency(source, context)


def test_old_worlds_engine_is_refused_before_build(tmp_path: Path) -> None:
    source = tmp_path / "Konnaxion"
    (source / "backend").mkdir(parents=True)
    context = _make_backend_context(tmp_path)
    backend = _make_worlds_repo(tmp_path)
    p = backend / "pyproject.toml"
    p.write_text(p.read_text().replace("version='0.3.7'", "version='0.3.6'"))
    with pytest.raises(PackageError, match="require Konnaxion_Worlds >= 0.3.7"):
        _stage_worlds_distribution_dependency(source, context)
