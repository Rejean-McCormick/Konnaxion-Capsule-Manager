'use strict';
// Pure validation, reusable in offline tests, fails closed.
const ORG = '0c2d7244-a0c0-4a10-8f11-0389f275f133';
const isUuid = x=>typeof x==='string'&&/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(x);
function assert(x,code){if(!x)throw new Error(code);}
function validateProof(p){
 assert(p.organization_id===ORG&&p.tenant==='fakeplant'&&p.decision_id==='1','ORG_OR_DECISION_INVALID');
 for(const [k,v] of Object.entries({signals:1,cases:1,tasks:1,workflows:1,operations:1})) assert(p.counts?.[k]===v,'DUPLICATE_OR_MISSING_'+k.toUpperCase());
 assert(isUuid(p.signal?.id)&&p.signal?.status==='PROCESSED'&&p.signal?.external_reference==='konnaxion:decision:1:r1'&&p.signal?.correlation_id==='decision:1','SIGNAL_INVALID');
 assert(p.signal?.case_id===p.case?.id&&isUuid(p.case?.id)&&p.case?.title==='Governed decision 1','CASE_INVALID');
 assert(isUuid(p.task?.id)&&p.task?.case_id===p.case?.id&&p.task?.title==='Governed decision 1','TASK_INVALID');
 assert(isUuid(p.workflow?.id)&&p.workflow?.status==='completed'&&p.workflow?.version===2,'WORKFLOW_INVALID');
 assert(JSON.stringify(p.workflow?.actions)==='["CREATE_CASE","CREATE_TASK","REQUEST_INTEGRATION"]','ACTIONS_INVALID');
 assert(isUuid(p.impact_operation?.id)&&isUuid(p.impact_operation?.correlation_id)&&p.impact_operation?.subject_type==='case'&&p.impact_operation?.subject_id===p.case?.id,'OPERATION_INVALID');
 assert(p.impact_operation?.status==='SUCCEEDED'&&!p.impact_operation?.error,'OPERATION_NOT_SUCCEEDED');
 assert(p.impact_operation?.receipt_status==='succeeded','RECEIPT_NOT_SUCCEEDED');
 // In Orgo the receipt external_reference is Konnaxion's return identity.
 assert(p.impact_operation?.external_reference===p.signal?.id,'EXTERNAL_REFERENCE_MISMATCH');
 return true;
}
module.exports={validateProof};
