const assert = require('node:assert/strict');
const { validateProof } = require('../tools/tandem_ik/final_ik_proof.cjs');
const base = {
  organization_id:'0c2d7244-a0c0-4a10-8f11-0389f275f133',tenant:'fakeplant',decision_id:'1',
  counts:{signals:1,cases:1,tasks:1,workflows:1,operations:1},
  signal:{id:'2df3399d-69e3-4de3-8801-44c8c654287f',status:'PROCESSED',case_id:'6210bf0d-cce4-43b3-85a1-558e996b6ce2',correlation_id:'decision:1',external_reference:'konnaxion:decision:1:r1'},
  case:{id:'6210bf0d-cce4-43b3-85a1-558e996b6ce2',title:'Governed decision 1',status:'open'},
  task:{id:'b90a76f1-67e8-488f-9682-9c094f7c6f25',case_id:'6210bf0d-cce4-43b3-85a1-558e996b6ce2',title:'Governed decision 1',status:'PENDING'},
  workflow:{id:'492f055b-d490-4272-a49f-22ba2a1622de',status:'completed',version:2,actions:['CREATE_CASE','CREATE_TASK','REQUEST_INTEGRATION']},
  impact_operation:{id:'a72128e3-5036-46bc-bcd6-aaaf3cad8ace',subject_type:'case',subject_id:'6210bf0d-cce4-43b3-85a1-558e996b6ce2',correlation_id:'ef79bd21-a01d-4ef9-8b46-214929b9e2d6',status:'SUCCEEDED',error:null,external_reference:'2df3399d-69e3-4de3-8801-44c8c654287f',receipt_status:'succeeded'}
};
assert.equal(validateProof(structuredClone(base)),true);
let tests=1;
const cases=[
 ['double signal',x=>x.counts.signals=2],['double task',x=>x.counts.tasks=2],['double operation',x=>x.counts.operations=2],
 ['wrong tenant',x=>x.organization_id='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'],['wrong signal correlation',x=>x.signal.correlation_id='other'],
 ['wrong signal id',x=>x.impact_operation.external_reference='ffffffff-ffff-ffff-ffff-ffffffffffff'],
 ['wrong subject',x=>x.impact_operation.subject_id='ffffffff-ffff-ffff-ffff-ffffffffffff'],
 ['version 1',x=>x.workflow.version=1],['missing third action',x=>x.workflow.actions.pop()],
 ['pending impact',x=>x.impact_operation.status='RUNNING'],['no receipt',x=>x.impact_operation.receipt_status=null],
];
for(const [name,edit] of cases){const x=structuredClone(base);edit(x);assert.throws(()=>validateProof(x),{message:/[A-Z_]+/},name);tests++;}
console.log('Validations: '+tests+'/'+tests+' cas de preuve Orgo OK');
