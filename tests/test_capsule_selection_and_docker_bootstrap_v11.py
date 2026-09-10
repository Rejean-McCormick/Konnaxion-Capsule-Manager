from __future__ import annotations

import os
from pathlib import Path

from kx_manager.defaults import DEFAULT_CAPSULE_ID, DEFAULT_CAPSULE_VERSION, latest_existing_capsule
from kx_manager.ui.droplet_bootstrap import _remote_bootstrap_command
from kx_manager.ui.page_parts.capsules import render as render_capsules
from kx_manager.ui.page_parts.common import droplet_payload


def _touch(path: Path, *, mtime: int) -> None:
    path.write_bytes(b"capsule")
    os.utime(path, (mtime, mtime))


def test_latest_existing_capsule_prefers_newest_mtime(tmp_path: Path) -> None:
    older = tmp_path / "konnaxion-v14-local-2026.09.08.kxcap"
    newer = tmp_path / "konnaxion-v14-local-2026.09.09.kxcap"
    _touch(older, mtime=100)
    _touch(newer, mtime=200)

    assert latest_existing_capsule(tmp_path) == newer


def test_droplet_payload_replaces_nonexistent_dated_default_with_real_capsule(tmp_path: Path) -> None:
    existing = tmp_path / "konnaxion-v14-local-2026.09.09.kxcap"
    _touch(existing, mtime=200)
    missing = tmp_path / "konnaxion-v14-local-2026.09.10.kxcap"

    payload = droplet_payload(
        {
            "target_mode": "droplet",
            "capsule_output_dir": str(tmp_path),
            "capsule_file": str(missing),
            "capsule_id": "konnaxion-v14-local-2026.09.10",
            "capsule_version": "2026.09.10-local.1",
        }
    )

    assert payload["capsule_file"] == str(existing)
    assert payload["capsule_path"] == str(existing)
    assert payload["capsule_id"] == "konnaxion-v14-local-2026.09.09"
    assert payload["capsule_version"] == "2026.09.09-local.1"


def test_existing_capsule_file_is_authoritative_for_hidden_identity(tmp_path: Path) -> None:
    existing = tmp_path / "konnaxion-v14-local-2026.09.09.kxcap"
    _touch(existing, mtime=200)

    payload = droplet_payload(
        {
            "target_mode": "droplet",
            "capsule_output_dir": str(tmp_path),
            "capsule_file": str(existing),
            "capsule_id": "konnaxion-v14-local-2026.09.10",
            "capsule_version": "2026.09.10-local.1",
        }
    )

    assert payload["capsule_id"] == "konnaxion-v14-local-2026.09.09"
    assert payload["capsule_version"] == "2026.09.09-local.1"


def test_capsules_page_suggests_existing_file_but_build_uses_current_build_name(tmp_path: Path) -> None:
    existing = tmp_path / "konnaxion-v14-local-2026.09.09.kxcap"
    _touch(existing, mtime=200)
    missing = tmp_path / "konnaxion-v14-local-2099.12.31.kxcap"

    html = render_capsules(
        {
            "capsule_output_dir": str(tmp_path),
            "capsule_file": str(missing),
            "capsule_id": "konnaxion-v14-local-2026.09.09",
            "capsule_version": "2026.09.09-local.1",
        }
    )

    assert str(existing) in html
    assert f'value="{DEFAULT_CAPSULE_ID}"' in html
    assert f'value="{DEFAULT_CAPSULE_VERSION}"' in html


def test_bootstrap_installs_and_verifies_docker_compose() -> None:
    command = _remote_bootstrap_command(
        remote_archive="/tmp/konnaxion-manager-bootstrap.tar.gz",
        remote_kx_root="/opt/konnaxion",
        remote_manager_dir="/opt/konnaxion/manager",
        instance_id="konnaxion-prod",
    )

    assert "apt-get install -y docker.io" in command
    assert "docker-compose-v2" in command
    assert "docker-compose-plugin" in command
    assert "docker-compose" in command
    assert "systemctl enable --now docker" in command
    assert "docker --version" in command
    assert "Neither docker compose nor docker-compose is available after bootstrap" in command
