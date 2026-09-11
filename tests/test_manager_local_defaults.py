from __future__ import annotations

import json
from datetime import date
from types import SimpleNamespace

from kx_manager.defaults import (
    DEFAULT_CAPSULE_ID,
    DEFAULT_CAPSULE_VERSION,
    DEFAULT_CHANNEL,
    DEFAULT_EXPOSURE_MODE,
    DEFAULT_NETWORK_PROFILE,
    DEFAULT_TARGET_MODE,
    make_default_capsule_id,
    make_default_capsule_version,
)
from kx_manager.services.builder import BuildCapsuleRequest
from kx_manager.ui import app as ui_app
from kx_manager.ui.page_parts.capsules import render as render_capsules


def test_automatic_capsule_names_use_date_and_local_channel() -> None:
    value = date(2026, 9, 8)

    assert make_default_capsule_id(value=value) == "konnaxion-v14-local-2026.09.08"
    assert make_default_capsule_version(value=value) == "2026.09.08-local.1"


def test_manager_session_defaults_are_local_and_date_named() -> None:
    today = date.today().strftime("%Y.%m.%d")

    assert DEFAULT_TARGET_MODE == "local"
    assert DEFAULT_NETWORK_PROFILE == "local_only"
    assert DEFAULT_EXPOSURE_MODE == "private"
    assert DEFAULT_CHANNEL == "local"
    assert DEFAULT_CAPSULE_ID == f"konnaxion-v14-local-{today}"
    assert DEFAULT_CAPSULE_VERSION == f"{today}-local.1"

    request = BuildCapsuleRequest()
    assert request.network_profile == "local_only"
    assert request.channel == "local"
    assert request.capsule_id == DEFAULT_CAPSULE_ID
    assert request.capsule_version == DEFAULT_CAPSULE_VERSION


def test_capsules_page_defaults_to_local_profile() -> None:
    html = render_capsules({})

    assert f'value="{DEFAULT_CAPSULE_ID}"' in html
    assert f'value="{DEFAULT_CAPSULE_VERSION}"' in html
    assert 'value="local_only" selected' in html
    assert 'value="local"' in html


def test_explicit_local_launcher_overrides_stale_persisted_target(
    monkeypatch,
    tmp_path,
) -> None:
    state_file = tmp_path / "manager-ui-state.json"
    state_file.write_text(
        json.dumps(
            {
                "target_mode": "droplet",
                "network_profile": "public_vps",
                "exposure_mode": "public",
                "capsule_id": "konnaxion-v14-demo-2026.05.08",
                "capsule_version": "2026.05.08-demo.1",
                "droplet_host": "203.0.113.10",
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("KX_MANAGER_UI_STATE_FILE", str(state_file))
    monkeypatch.setenv("KX_TARGET_MODE", "local")
    monkeypatch.setenv("KX_TARGET_PROFILE", "local_only")
    monkeypatch.setenv("KX_TARGET_EXPOSURE", "private")
    monkeypatch.setenv("KX_SOURCE_DIR", r"C:\mycode\Konnaxion\Konnaxion")
    monkeypatch.setenv(
        "KX_CAPSULE_OUTPUT_DIR",
        r"C:\mycode\Konnaxion\runtime\capsules",
    )
    monkeypatch.setenv("KX_AUTO_CAPSULE_NAMING", "true")

    fake_app = SimpleNamespace(state=SimpleNamespace())
    context = ui_app._load_ui_context(fake_app)

    assert context["target_mode"] == "local"
    assert context["network_profile"] == "local_only"
    assert context["exposure_mode"] == "private"
    assert context["capsule_id"] == DEFAULT_CAPSULE_ID
    assert context["capsule_version"] == DEFAULT_CAPSULE_VERSION
    assert context["capsule_file"].endswith(f"{DEFAULT_CAPSULE_ID}.kxcap")


def test_legacy_netcup_sslip_domain_is_migrated_from_persisted_ui_state(
    monkeypatch,
    tmp_path,
) -> None:
    state_file = tmp_path / "manager-ui-state.json"
    state_file.write_text(
        json.dumps(
            {
                "target_mode": "droplet",
                "network_profile": "public_vps",
                "exposure_mode": "public",
                "droplet_host": "2.56.97.41",
                "target_host": "2.56.97.41",
                "domain": "2.56.97.41.sslip.io",
                "droplet_domain": "2.56.97.41.sslip.io",
                "public_host": "2.56.97.41.sslip.io",
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("KX_MANAGER_UI_STATE_FILE", str(state_file))
    for name in (
        "KX_TARGET_MODE",
        "KX_TARGET_PROFILE",
        "KX_TARGET_EXPOSURE",
    ):
        monkeypatch.delenv(name, raising=False)

    fake_app = SimpleNamespace(state=SimpleNamespace())
    context = ui_app._load_ui_context(fake_app)

    assert context["domain"] == "konnaxion.com"
    assert context["droplet_domain"] == "konnaxion.com"
    assert context["public_host"] == "konnaxion.com"

    persisted = json.loads(state_file.read_text(encoding="utf-8"))
    assert persisted["domain"] == "konnaxion.com"
    assert persisted["droplet_domain"] == "konnaxion.com"
    assert persisted["public_host"] == "konnaxion.com"


def test_legacy_domain_migration_does_not_touch_other_vps_targets(
    monkeypatch,
    tmp_path,
) -> None:
    state_file = tmp_path / "manager-ui-state.json"
    state_file.write_text(
        json.dumps(
            {
                "target_mode": "droplet",
                "droplet_host": "203.0.113.25",
                "domain": "2.56.97.41.sslip.io",
                "droplet_domain": "2.56.97.41.sslip.io",
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("KX_MANAGER_UI_STATE_FILE", str(state_file))
    fake_app = SimpleNamespace(state=SimpleNamespace())
    context = ui_app._load_ui_context(fake_app)

    assert context["domain"] == "2.56.97.41.sslip.io"
    assert context["droplet_domain"] == "2.56.97.41.sslip.io"
