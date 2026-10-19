import ast
import datetime
import pathlib
import types
import unittest
import uuid

code_path = pathlib.Path(__file__).resolve().parents[1] / 'tools' / 'tandem_ik' / 'final_ik_konnaxion.py'
tree = ast.parse(code_path.read_text())
fn = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in ('require','corroborate')]
module = ast.Module(body=fn, type_ignores=[])

ORG='0c2d7244-a0c0-4a10-8f11-0389f275f133'
SIGNAL='2df3399d-69e3-4de3-8801-44c8c654287f'
CASE='6210bf0d-cce4-43b3-85a1-558e996b6ce2'
CORR='ef79bd21-a01d-4ef9-8b46-214929b9e2d6'
OP='a72128e3-5036-46bc-bcd6-aaaf3cad8ace'

class FakeQuerySet:
    def __init__(self,values): self.values=list(values)
    def filter(self,**criteria):
        return FakeQuerySet([obj for obj in self.values if all(getattr(obj,key)==value for key,value in criteria.items())])
    def __getitem__(self,idx):return self.values[idx]
    def count(self):return len(self.values)

class Impact:
    pass

def item():
    v=Impact()
    v.pk=2
    v.operation_id=uuid.uuid5(uuid.NAMESPACE_URL,f'interaction-kernel:{OP}')
    v.organization_id=uuid.UUID(ORG)
    v.subject_type='case'
    v.subject_id=CASE
    v.external_reference=SIGNAL
    v.correlation_id=CORR
    v.idempotency_key=f'{ORG}:{OP}'
    v.status='published'
    v.published_at=datetime.datetime(2026,10,18,21,31,tzinfo=datetime.timezone.utc)
    v.request_json={
      'id':OP, 'correlation_id':CORR,
      'source':{'system':'orgo','organization':ORG},
      'profile':{'id':'accountability.impact.publish','version':'1.0.0'},
      'data':{'artifact_type':'impact_update','summary':{'status':'recorded'}}}
    return v

BASE={
 'organization_id':ORG,'decision_id':'1','signal':{'id':SIGNAL,'case_id':CASE},
 'case':{'id':CASE},'impact_operation':{'id':OP,'correlation_id':CORR,'external_reference':SIGNAL,'subject_id':CASE}
}

class TestImpactCorrelation(unittest.TestCase):
    def setUp(self):
        self.scope={
            'initial':lambda:{'decision_id':1,'interaction_id':'322797c8-122f-4eeb-9c57-4d0c5b5ec89f'},
            'UUID':uuid.UUID,'ORG':ORG,'NAMESPACE_URL':uuid.NAMESPACE_URL,'uuid5':uuid.uuid5,
            'OrgoImpactPublication':types.SimpleNamespace(objects=FakeQuerySet([item()]))}
        exec(compile(module,str(code_path),'exec'),self.scope)
    def test_matches_real_correlations(self):
        r=self.scope['corroborate'](BASE)
        self.assertEqual(r['impact']['id'],2)
        self.assertEqual(r['impact']['correlation_id'],CORR)
        self.assertTrue(all(v==1 for v in r['non_duplication'].values()))
    def test_wrong_operation_id_rejected(self):
        x=dict(BASE);x['impact_operation']=dict(BASE['impact_operation'],id='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa')
        with self.assertRaisesRegex(RuntimeError,'DUPLICATE_OR_MISSING_IMPACT|OPERATION_DIGEST_ID_MISMATCH'):self.scope['corroborate'](x)
    def test_wrong_case_rejected(self):
        x=dict(BASE);x['case']={'id':'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb'}
        with self.assertRaisesRegex(RuntimeError,'CROSS_PROOF_CASE_INVALID'):self.scope['corroborate'](x)
    def test_duplicate_case_impact_rejected(self):
        second=item();second.pk=3; second.correlation_id='11111111-1111-1111-1111-111111111111';second.external_reference='11111111-1111-1111-1111-111111111111';second.idempotency_key='new'
        self.scope['OrgoImpactPublication']=types.SimpleNamespace(objects=FakeQuerySet([item(),second]))
        with self.assertRaisesRegex(RuntimeError,'DUPLICATE_IMPACT_REFERENCE'):self.scope['corroborate'](BASE)

if __name__=='__main__':unittest.main()
