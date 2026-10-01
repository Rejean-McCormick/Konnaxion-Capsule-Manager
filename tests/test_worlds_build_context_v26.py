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
        "version='0.2.0'\n"
        "[tool.setuptools.packages.find]\n"
        "where=['.']\n"
        "include=['konnaxion.worlds*']\n"
        "namespaces=true\n",
        encoding="utf-8",
    )
    seeds = backend / "seed-data" / "worlds" / "demo-alpha"
    seeds.mkdir(parents=True)
    (seeds / "world.yaml").write_text("slug: demo-alpha\n", encoding="utf-8")
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
    assert (backend_context / "seed-data" / "worlds" / "demo-alpha" / "world.yaml").is_file()
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
