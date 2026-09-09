from __future__ import annotations

import re


def _assert_no_nested_forms(html: str) -> None:
    depth = 0
    for match in re.finditer(r"</?form\\b[^>]*>", html, flags=re.IGNORECASE):
        token = match.group(0).lower()
        if token.startswith("</form"):
            depth -= 1
            assert depth >= 0, f"unexpected closing form near: {token}"
        else:
            depth += 1
            assert depth == 1, f"nested form detected near: {token}"
    assert depth == 0


def test_instances_runtime_actions_are_sibling_forms() -> None:
    from kx_manager.ui.page_parts.instances import render

    html = render(
        {
            "instance_id": "demo-001",
            "capsule_id": "konnaxion-v14-local-2026.09.08",
            "host": "konnaxion.local",
            "network_profile": "local_only",
            "exposure_mode": "private",
        }
    )

    _assert_no_nested_forms(html)
    assert 'action="/ui/actions/instance-status"' in html
    assert 'action="/ui/actions/start-instance"' in html
    assert 'action="/ui/actions/stop-instance"' in html
    assert 'action="/ui/actions/restart-instance"' in html

    start = re.search(
        r'<form[^>]+action="/ui/actions/start-instance"[^>]*>.*?</form>',
        html,
        flags=re.DOTALL,
    )
    assert start is not None
    assert 'name="instance_id" value="demo-001"' in start.group(0)
    assert 'name="run_security_gate" value="true"' in start.group(0)


def test_health_actions_are_sibling_forms() -> None:
    from kx_manager.ui.page_parts.health import render

    html = render({"instance_id": "demo-001"})
    _assert_no_nested_forms(html)
    assert 'action="/ui/actions/view-health"' in html
    assert 'action="/ui/actions/instance-status"' in html
    assert 'action="/ui/actions/check-agent"' in html
    assert 'action="/ui/actions/check-manager"' in html
