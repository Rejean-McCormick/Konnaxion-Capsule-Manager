"""Lot 1C: Agent-measured VPS release + KonnaxionDiag strict verification."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import ModuleType, SimpleNamespace

import pytest

from kx_agent.security import release_admission as ra
from kx_shared.errors import SecurityGateBlockingError

DIGEST = 'sha256:' + 'a' * 64
IMAGE_REF = 'registry.invalid/team/app@sha256:' + 'b' * 64

class Runtime:
    def __init__(self, image=IMAGE_REF, image_id=DIGEST, pinned=True):
        self.image = image
        self.image_id = image_id
        self.pinned = pinned
    def _compose_args(self, args):
        assert args == ['config', '--format', 'json']
        return SimpleNamespace(ok=True, stdout=json.dumps({'services':{'api':{'image':self.image}}}))
    def image_inspect(self, ref):
        return SimpleNamespace(image_id=self.image_id, repo_digests=(ref,) if self.pinned else ())


def _workspace(tmp_path, monkeypatch):
    base = tmp_path / 'release'
    base.mkdir()
    src = base / 'src'
    src.mkdir()
    (src / 'marker').write_text('ok')
    subprocess.run(['git','init','-q',str(src)], check=True)
    subprocess.run(['git','-C',str(src),'add','marker'], check=True)
    subprocess.run(['git','-C',str(src),'-c','user.name=Test','-c','user.email=test@invalid','commit','-qm','base'],check=True)
    (base/'capsule.json').write_text('capsule')
    components = {}
    for name in ra._COMPONENTS:
        path = base / name / 'file.txt';path.parent.mkdir();path.write_text(name)
        row = {'path':name+'/file.txt','digest':ra._hash_file(path)}
        components[name] = [row]
    rs = {'schema':'konnaxiondiag.release-set.v1', 'component_files':components}
    policy = {'schema':'koa.agent.release-admission.v1','instance_id':'example',
        'policy_version':'policy/v1','deployment_root':str(base),'source_marker':'src/marker',
        'capsule_manifest':'capsule.json','release_set':'release-set.json','verdict':'verdict.json',
        'signature':'sig.json','trusted_public_keys':[str(tmp_path/'key.pub')]}
    (tmp_path/'key.pub').write_text('test-key')
    rs['identity']={k:None for k in ('schema','source_commit_sha','source_tree_digest','capsule_digest','capsule_digests','image_digests','runtime_pack_digest','policy_bundle_digest','infra_manifest_digest','sbom_digest','provenance_digest')}
    identity=ra._measure_identity(base,policy,rs,Runtime())
    rs['identity']=identity;rs['release_set_digest']=ra._hash_json(identity)
    rs['complete']=True;rs['problems']=[]
    (base/'release-set.json').write_text(json.dumps(rs))
    (base/'verdict.json').write_text(json.dumps({'verdict':'PASS'}))
    (base/'sig.json').write_text(json.dumps({'schema':'example'}))
    monkeypatch.setattr(ra,'_policy', lambda instance: policy)
    return base, policy, rs


def _pep(monkeypatch, acceptable=True):
    assurance=ModuleType('assurance');assurance.__path__=[]
    pep=ModuleType('assurance.pep')
    calls=[]
    def verify(*args, **kwargs):
        calls.append(kwargs)
        return acceptable, {}
    pep.verify_activation_admission=verify
    monkeypatch.setitem(sys.modules,'assurance',assurance)
    monkeypatch.setitem(sys.modules,'assurance.pep',pep)
    return calls


def test_measured_valid_release_reaches_strict_pep(tmp_path, monkeypatch):
    _base, policy, rs=_workspace(tmp_path,monkeypatch)
    calls=_pep(monkeypatch)
    result=ra.check_activation('example',Runtime())
    assert result['admitted'] is True
    assert result['release_set_digest']==rs['release_set_digest']
    assert calls[0]['measured_release_set_digest']==rs['release_set_digest']
    assert calls[0]['expected_policy_version']=='policy/v1'


def test_kdiag_denial_blocks(tmp_path, monkeypatch):
    _workspace(tmp_path,monkeypatch)
    _pep(monkeypatch,False)
    with pytest.raises(SecurityGateBlockingError,match='denied'):ra.check_activation('example',Runtime())

@pytest.mark.parametrize('mutation',["file",'capsule','source','image','unpinned','wrong_pinned','signed_digest','missing_proof','relative_path'])
def test_runtime_or_evidence_drift_blocks_before_pep(tmp_path,monkeypatch,mutation):
    base, policy, rs = _workspace(tmp_path,monkeypatch)
    calls=_pep(monkeypatch)
    runtime=Runtime()
    if mutation=='file':(base/'sbom/file.txt').write_text('tampered')
    if mutation=='capsule':(base/'capsule.json').write_text('tampered')
    if mutation=='source':(base/'src/marker').write_text('tampered')
    if mutation=='image':runtime.image_id='sha256:'+'f'*64
    if mutation=='unpinned':runtime.image='registry.invalid/app:latest'
    if mutation=='wrong_pinned':runtime.pinned=False
    if mutation=='signed_digest':rs['release_set_digest']=DIGEST;(base/'release-set.json').write_text(json.dumps(rs))
    if mutation=='missing_proof':(base/'sig.json').unlink()
    if mutation=='relative_path':rs['component_files']['sbom'][0]['path']='../secret';(base/'release-set.json').write_text(json.dumps(rs))
    with pytest.raises(SecurityGateBlockingError):ra.check_activation('example',runtime)
    assert calls==[]


def test_key_missing_blocks(tmp_path,monkeypatch):
    _base,policy,_rs=_workspace(tmp_path,monkeypatch)
    (tmp_path/'key.pub').unlink(); calls=_pep(monkeypatch)
    with pytest.raises(SecurityGateBlockingError,match='authority key'):ra.check_activation('example',Runtime())
    assert calls==[]


def test_trusted_policy_missing_blocks(monkeypatch,tmp_path):
    monkeypatch.setattr(ra,'_POLICY_DIR',tmp_path)
    with pytest.raises(SecurityGateBlockingError):ra._policy('example')

@pytest.mark.parametrize('instance_id',['../etc/passwd','bad/name','','a'*129])
def test_invalid_instance_id_fails_closed(instance_id):
    with pytest.raises(SecurityGateBlockingError):ra._trusted_policy_file(instance_id)


def test_unqualified_agent_default_is_local(monkeypatch):
    from kx_agent import config
    monkeypatch.delenv('KX_RELEASE_ACTIVATION_MODE',raising=False)
    monkeypatch.setattr(config,'get_agent_config',lambda:SimpleNamespace(is_public_mode=False))
    monkeypatch.setattr('kx_agent.instances.secrets.read_instance_env_files',lambda _:{'KX_NETWORK_PROFILE':'local_only'})
    assert ra.admission_required('example') is False

@pytest.mark.parametrize('environ,profile,public',[(None,'public_vps',False),(None,'public_temporary',False),('enforce','local_only',False),(None,'local_only',True)])
def test_public_profile_always_requires_proof(monkeypatch,environ,profile,public):
    from kx_agent import config
    if environ is None:monkeypatch.delenv('KX_RELEASE_ACTIVATION_MODE',raising=False)
    else:monkeypatch.setenv('KX_RELEASE_ACTIVATION_MODE',environ)
    monkeypatch.setattr(config,'get_agent_config',lambda:SimpleNamespace(is_public_mode=public))
    monkeypatch.setattr('kx_agent.instances.secrets.read_instance_env_files',lambda _:{'KX_NETWORK_PROFILE':profile})
    assert ra.admission_required('example') is True


def test_agent_start_rejects_public_proof_before_docker_lifecycle(monkeypatch):
    from kx_agent import actions
    from kx_agent.actions import ActionRequest,ActionResult,ActionStatus
    monkeypatch.setattr(actions,'handle_security_check',lambda req:ActionResult(action='security.check',status=ActionStatus.SUCCEEDED,request_id='ok',data={'security_status':'PASS','security_evidence_file':'/evidence'}))
    runtime=SimpleNamespace(validate_available=lambda:None)
    monkeypatch.setattr('kx_agent.runtime.docker.runtime_for_instance',lambda _:runtime)
    monkeypatch.setattr(actions,'_load_capsule_image_archives_for_instance',lambda *a,**kw:{'ok':True,'loaded_count':0})
    monkeypatch.setattr(ra,'enforce_for_start',lambda *a,**kw:ra._deny('no signed release'))
    with pytest.raises(SecurityGateBlockingError, match='no signed release'):
        actions.handle_instance_start(ActionRequest(action='instance.start',params={'instance_id':'example'}))


def test_vps_backup_test_restore_denied_before_backup_lookup(monkeypatch):
    from kx_agent import actions
    monkeypatch.setattr(ra,'admission_required',lambda _:True)
    with pytest.raises(SecurityGateBlockingError, match='test-restore'):
        actions.handle_backup_test_restore(actions.ActionRequest(
            action='backup.test_restore',params={
                'instance_id':'example','target_instance_id':'isolated-test', 'backup_id':'never-opened'}))


def test_local_start_does_not_enable_vps_admission(monkeypatch):
    from kx_agent import config
    monkeypatch.delenv('KX_RELEASE_ACTIVATION_MODE',raising=False)
    monkeypatch.setattr(config,'get_agent_config',lambda:SimpleNamespace(is_public_mode=False))
    monkeypatch.setattr('kx_agent.instances.secrets.read_instance_env_files',lambda _:{})
    assert ra.enforce_for_start('example',None) is None


def test_bogus_global_admission_mode_is_not_ignored(monkeypatch):
    from kx_agent import config
    monkeypatch.setenv('KX_RELEASE_ACTIVATION_MODE','disable')
    monkeypatch.setattr(config,'get_agent_config',lambda:SimpleNamespace(is_public_mode=False))
    with pytest.raises(SecurityGateBlockingError,match='mode'):
        ra.admission_required('example')


def test_agent_immutable_compose_missing_image_blocks(monkeypatch):
    runtime=Runtime(image=IMAGE_REF)
    runtime._compose_args=lambda args:SimpleNamespace(ok=True, stdout=json.dumps({'services':{'api':{'build':'.'}}}))
    with pytest.raises(SecurityGateBlockingError,match='build operations'):
        ra._measure_images(runtime)
