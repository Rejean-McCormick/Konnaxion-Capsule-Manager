"""Audit historique FakePlant IK. MODE=initial or corroborate. SQL SELECT only.

This script is evaluated with `python manage.py shell -c` by Capsule Manager.
"""
import json
import os
from uuid import UUID, NAMESPACE_URL, uuid5
from django.db import connections
from konnaxion.ethikos.models import DecisionRecord, InteractionEmission, OrgoImpactPublication
from konnaxion.worlds.models import World, WorldRelease
from konnaxion.worlds.services.tasks import world_task_scope

PREFIX = "KOA_FINAL_KX_JSON="
ORG = "0c2d7244-a0c0-4a10-8f11-0389f275f133"
WORLD = "fp-electromecanique-informatique"
TITLE = "KOA IK E2E FakePlant - test synthétique unique V1"


def require(test, code):
    if not test:
        raise RuntimeError(code)


def initial():
    require(connections['default'].settings_dict['NAME'] == 'neondb', 'DATABASE_NOT_NEON')
    world = World.objects.select_related('universe').get(pk=58)
    release = WorldRelease.objects.get(pk=132)
    require(world.key == WORLD and world.universe.key == 'fakeplant' and release.world_id == world.pk,
            'WORLD_RELEASE_INVALID')
    # DecisionRecord and InteractionEmission are release-scoped; read under their pinned context.
    # The impact INGRESS is globally registered in Neon/public; read it separately below.
    with world_task_scope(world_id=world.pk, release_id=release.pk):
        ds = list(DecisionRecord.objects.filter(title=TITLE).order_by('pk')[:3])
        require(len(ds) == 1 and ds[0].pk == 1, 'DUPLICATE_OR_MISSING_DECISION')
        decision = ds[0]
        require(decision.status == 'published' and decision.revision == 1 and len(decision.artifact_digest) == 64,
                'DECISION_NOT_PUBLISHED')
        emissions = list(InteractionEmission.objects.filter(subject_type='decision',subject_id='1').order_by('pk')[:3])
        require(len(emissions) == 1, 'DUPLICATE_OR_MISSING_EMISSION')
        emission = emissions[0]
        envelope = emission.envelope_json if isinstance(emission.envelope_json, dict) else {}
        require(emission.status in ('accepted','succeeded') and emission.attempts == 1,
                'EMISSION_NOT_ACCEPTED_OR_REPLAYED')
        require(emission.target_organization == ORG and emission.target_world == WORLD,
                'EMISSION_TARGET_INVALID')
        require((envelope.get('profile') or {}).get('id') == 'governance.decision.execute' and
                envelope.get('correlation_id') == 'decision:1' and
                (envelope.get('subject') or {}).get('id') == '1', 'EMISSION_ENVELOPE_INVALID')
        require((emission.receipt_json or {}).get('status') in ('accepted','succeeded'), 'EMISSION_RECEIPT_MISSING')
        return {'decision_id':1,'decision_status':decision.status,'revision':decision.revision,
                'artifact_digest':decision.artifact_digest,'interaction_id':str(emission.interaction_id),
                'emission_status':emission.status,'emission_attempts':emission.attempts,
                'emission_receipt_status':(emission.receipt_json or {}).get('status'),
                'world':WORLD,'world_id':world.pk,'release_id':release.pk,
                'organization_id':ORG,'decision_matches':1,'emission_matches':1}


def corroborate(proof):
    baseline = initial()
    require(proof.get('organization_id') == ORG and proof.get('decision_id') == '1', 'CROSS_PROOF_TARGET_INVALID')
    signal = proof.get('signal') or {}
    operation = proof.get('impact_operation') or {}
    case = proof.get('case') or {}
    signal_id = str(UUID(signal['id']))
    op_id = str(UUID(operation['id']))
    op_correlation = str(UUID(operation['correlation_id']))
    case_id = str(UUID(case['id']))
    require(signal.get('case_id') == case_id and operation.get('subject_id') == case_id,
            'CROSS_PROOF_CASE_INVALID')
    require(operation.get('external_reference') == signal_id, 'CROSS_PROOF_SIGNAL_INVALID')
    expected_key = f'{ORG}:{op_id}'
    expected_derived_operation_id = uuid5(NAMESPACE_URL, f'interaction-kernel:{op_id}')
    # Public ingress tables are outside the pinned World context, intentionally.
    qs = OrgoImpactPublication.objects.filter(organization_id=UUID(ORG))
    exact = list(qs.filter(subject_type='case',subject_id=case_id,
                           external_reference=signal_id,correlation_id=op_correlation,
                           idempotency_key=expected_key,status='published')[:3])
    require(len(exact)==1, 'DUPLICATE_OR_MISSING_IMPACT')
    item=exact[0]
    require(item.operation_id == expected_derived_operation_id, 'OPERATION_DIGEST_ID_MISMATCH')
    request = item.request_json if isinstance(item.request_json,dict) else {}
    require(request.get('id') == op_id and request.get('correlation_id') == op_correlation,
            'IMPACT_ENVELOPE_OPERATION_MISMATCH')
    require((request.get('source') or {}).get('organization') == ORG and
            (request.get('source') or {}).get('system') == 'orgo' and
            (request.get('profile') or {}).get('id') == 'accountability.impact.publish' and
            (request.get('data') or {}).get('artifact_type') == 'impact_update' and
            isinstance((request.get('data') or {}).get('summary'),dict), 'IMPACT_ENVELOPE_INVALID')
    # Each uniqueness dimension is checked separately; not merely `count==1` in a conjunction.
    counts = {
        'impact_by_external_reference':qs.filter(external_reference=signal_id).count(),
        'impact_by_idempotency':qs.filter(idempotency_key=expected_key).count(),
        'impact_by_case':qs.filter(subject_type='case',subject_id=case_id).count(),
        'impact_by_correlation':qs.filter(correlation_id=op_correlation).count(),
        'impact_by_operation_id':qs.filter(operation_id=expected_derived_operation_id).count(),
    }
    require(all(n==1 for n in counts.values()), 'DUPLICATE_IMPACT_REFERENCE')
    return {'ok':True,'read_only':True,'konnaxion':baseline,
            'impact':{'id':item.pk,'status':item.status,'published_at':item.published_at.isoformat(),
                      'subject_type':item.subject_type,'subject_id':item.subject_id,
                      'external_reference':item.external_reference,'correlation_id':item.correlation_id,
                      'idempotency_key':item.idempotency_key,'operation_id':str(item.operation_id),
                      'incoming_interaction_id':request.get('id')},
            'non_duplication':counts}


try:
    mode=os.getenv('KOA_FINAL_E2E_MODE','initial')
    require(mode in ('initial','corroborate'),'MODE_INVALID')
    if mode=='initial':
        output={'ok':True,'read_only':True,'konnaxion':initial()}
    else:
        raw=os.getenv('KOA_FINAL_ORGO_JSON','')
        require(0<len(raw)<30000,'CROSS_PROOF_MISSING')
        output=corroborate(json.loads(raw))
    print(PREFIX+json.dumps(output,ensure_ascii=False,default=str,separators=(',',':')))
except Exception as exc:
    # Error codes only. Never render token/database addresses or full exception payloads.
    code=str(exc)
    import re
    if not re.fullmatch('[A-Z_]+',code):code='QUERY_FAILED'
    print(PREFIX+json.dumps({'ok':False,'reason':code}))
    raise SystemExit(1)
