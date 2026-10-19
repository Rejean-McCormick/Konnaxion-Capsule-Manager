"""Lot 1: fail-closed security admission on every Agent instance start."""
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from kx_agent import actions
from kx_agent.api import InstanceStartRequest
from kx_agent.actions import ActionRequest, ActionResult, ActionStatus, AgentActionName
from kx_agent.security import evidence
from kx_manager.services import deploy
from kx_shared.errors import SecurityGateBlockingError


def _request(params=None, *, requires_gate=True):
    return ActionRequest(
        action=AgentActionName.INSTANCE_START.value,
        params={"instance_id": "demo-001", **(params or {})},
        require_security_gate=requires_gate,
    )


@pytest.mark.parametrize('value', [False, 'false', '0', 0])
def test_agent_rejects_explicit_start_bypass_before_touching_runtime(monkeypatch, value):
    monkeypatch.setattr(actions, 'handle_security_check', lambda *_: pytest.fail('should not run gate'))
    with pytest.raises(SecurityGateBlockingError, match='mandatory'):
        actions.handle_instance_start(_request({'run_security_gate': value}))


def test_agent_rejects_request_flag_bypass():
    with pytest.raises(SecurityGateBlockingError, match='mandatory'):
        actions.handle_instance_start(_request(requires_gate=False))


@pytest.mark.parametrize('status,gate_state,evidence_file', [
    (ActionStatus.SUCCEEDED, 'SKIPPED', None),
    (ActionStatus.SUCCEEDED, 'UNKNOWN', '/tmp/evidence.json'),
    (ActionStatus.SUCCEEDED, 'FAIL_BLOCKING', '/tmp/evidence.json'),
    (ActionStatus.ACCEPTED, 'PASS', '/tmp/evidence.json'),
    (ActionStatus.SUCCEEDED, 'PASS', None),
    (ActionStatus.FAILED, 'PASS', '/tmp/evidence.json'),
    (ActionStatus.BLOCKED, 'PASS', '/tmp/evidence.json'),
])
def test_agent_rejects_unqualified_gate_result(monkeypatch, status, gate_state, evidence_file):
    result = ActionResult(
        action=AgentActionName.SECURITY_CHECK.value,
        status=status,
        request_id='gate',
        data={'security_status': gate_state, 'security_evidence_file': evidence_file},
    )
    monkeypatch.setattr(actions, 'handle_security_check', lambda *_: result)
    with pytest.raises(SecurityGateBlockingError, match='missing or negative evidence'):
        actions.handle_instance_start(_request())


def test_agent_requires_blocking_gate_with_true_flag(monkeypatch):
    seen = []
    def fake_gate(request):
        seen.append(request)
        return ActionResult(
            action=AgentActionName.SECURITY_CHECK.value,
            status=ActionStatus.BLOCKED,
            request_id=request.request_id,
            data={'security_status': 'FAIL_BLOCKING'},
        )
    monkeypatch.setattr(actions, 'handle_security_check', fake_gate)
    with pytest.raises(SecurityGateBlockingError):
        actions.handle_instance_start(_request())
    assert len(seen) == 1
    assert seen[0].params['blocking'] is True
    assert seen[0].require_security_gate is True


def test_missing_compose_blocks_blocking_gate(monkeypatch, tmp_path: Path):
    class FakeRuntime:
        config = SimpleNamespace(compose_file=tmp_path / 'nonexistent.yml')
    monkeypatch.setattr('kx_agent.runtime.docker.runtime_for_instance', lambda _: FakeRuntime())
    result = actions.handle_security_check(ActionRequest(
        action=AgentActionName.SECURITY_CHECK.value,
        params={'instance_id': 'demo-001', 'blocking': True},
    ))
    assert result.status == ActionStatus.BLOCKED
    assert result.data['security_status'] == 'SKIPPED'
    assert 'runtime_compose_missing' in result.data['blocking_failures']


def test_missing_compose_can_be_diagnosed_nonblocking(monkeypatch, tmp_path: Path):
    class FakeRuntime:
        config = SimpleNamespace(compose_file=tmp_path / 'nonexistent.yml')
    monkeypatch.setattr('kx_agent.runtime.docker.runtime_for_instance', lambda _: FakeRuntime())
    result = actions.handle_security_check(ActionRequest(
        action=AgentActionName.SECURITY_CHECK.value,
        params={'instance_id': 'demo-001', 'blocking': False},
    ))
    assert result.status == ActionStatus.SUCCEEDED
    assert result.data['security_status'] == 'SKIPPED'


@pytest.mark.parametrize('value', [False, 'false', 0])
def test_agent_start_http_contract_rejects_false(value):
    with pytest.raises(ValidationError, match='forbidden'):
        InstanceStartRequest.model_validate({'instance_id': 'demo-001', 'run_security_gate': value})


def test_manager_deploy_refuses_gate_skip():
    request = deploy.BaseDeployRequest(instance_id='demo-001', run_security_gate=False)
    result = deploy.DeployResult(ok=False, action='local', instance_id='demo-001', message='')
    with pytest.raises(deploy.DeployValidationError, match='cannot be disabled'):
        deploy._run_security_gate(request, result, remote=False)
    assert result.steps == []


def test_manager_start_payload_always_forces_gate(monkeypatch):
    seen=[]
    monkeypatch.setattr(deploy, '_active_capsule_payload', lambda *_a, **_kw: {})
    def fake_step(request, result, *, payload, **kwargs):
        seen.append(dict(payload)); return {'ok': True}
    monkeypatch.setattr(deploy, '_call_backend_step', fake_step)
    req = deploy.BaseDeployRequest(instance_id='demo-001', run_security_gate=False)
    report=deploy.DeployResult(ok=False,action='local',instance_id='demo-001',message='')
    deploy._start_instance(req, report, remote=False)
    assert seen and seen[0]['run_security_gate'] is True


def test_gate_true_accepted_by_http_schema():
    assert InstanceStartRequest.model_validate({'instance_id': 'demo-001'}).run_security_gate is True
