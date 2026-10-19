'use strict';
// Orgo PostgreSQL audit only: nothing is written or dispatched.
const { PrismaClient } = require('@prisma/client');
const { validateProof } = require('./final_ik_proof.cjs');
const db = new PrismaClient({log: []});
const ORG = '0c2d7244-a0c0-4a10-8f11-0389f275f133';
const DECISION = '1';
const PREFIX = 'KOA_FINAL_ORGO_JSON=';
(async () => {
  try {
    const proof = await db.$transaction(async tx => {
      await tx.$executeRaw`SET TRANSACTION READ ONLY`;
      const org = await tx.organization.findUnique({where: {id: ORG}, select: {id:true, slug:true, status:true}});
      if (!org || org.slug !== 'fakeplant' || org.status !== 'active') throw Error('TENANT_INVALID');
      const signals = await tx.signal.findMany({
        where: {organization_id: ORG, external_reference: 'konnaxion:decision:1:r1'},
        select: {id:true, status:true, case_id:true, correlation_id:true, external_reference:true},
        take: 3,
      });
      if (signals.length !== 1 || !signals[0].case_id) throw Error('SIGNAL_NOT_UNIQUE');
      const signal = signals[0];
      const caseRow = await tx.case.findFirst({where:{organization_id:ORG,id:signal.case_id},select:{id:true,title:true,status:true}});
      const casesSameTitle = await tx.case.count({where:{organization_id:ORG,title:`Governed decision ${DECISION}`}});
      const tasks = await tx.task.findMany({
        where:{organization_id:ORG,case_id:signal.case_id},
        select:{id:true,case_id:true,title:true,status:true},take:5
      });
      const instances = await tx.workflowInstance.findMany({
        where:{organization_id:ORG,signal_id:signal.id},
        select:{id:true,status:true,workflow_version:{select:{version:true}},resolved_actions:true},take:5
      });
      const operations = await tx.integrationOperation.findMany({
        where:{organization_id:ORG,provider:'konnaxion',operation:'publish_impact',subject_type:'case',subject_id:signal.case_id},
        select:{id:true,subject_type:true,subject_id:true,correlation_id:true,status:true,error:true,external_reference:true,receipt:true},
        take:5,
      });
      const counts = {
        signals: await tx.signal.count({where:{organization_id:ORG,external_reference: 'konnaxion:decision:1:r1'}}),
        cases: casesSameTitle,
        tasks: await tx.task.count({where:{organization_id:ORG,case_id:signal.case_id}}),
        workflows: await tx.workflowInstance.count({where:{organization_id:ORG,signal_id:signal.id}}),
        operations: await tx.integrationOperation.count({where:{organization_id:ORG,provider:'konnaxion',operation:'publish_impact',subject_type:'case',subject_id:signal.case_id}}),
      };
      if (!caseRow || tasks.length!==1 || instances.length!==1 || operations.length!==1) throw Error('EFFECTS_NOT_UNIQUE');
      const instance = instances[0]; const op = operations[0];
      const actions = Array.isArray(instance.resolved_actions) ? instance.resolved_actions.map(a=>a?.action?.type) : [];
      const report = {
        tenant:'fakeplant', organization_id:ORG, decision_id:DECISION,
        signal:{id:signal.id,status:signal.status,case_id:signal.case_id,correlation_id:signal.correlation_id,external_reference:signal.external_reference},
        workflow:{id:instance.id,status:instance.status,version:instance.workflow_version?.version||null,actions},
        case:{id:caseRow.id,title:caseRow.title,status:caseRow.status},
        task:{id:tasks[0].id,case_id:tasks[0].case_id,title:tasks[0].title,status:tasks[0].status},
        impact_operation:{id:op.id,subject_type:op.subject_type,subject_id:op.subject_id,correlation_id:op.correlation_id,status:op.status,error:op.error,external_reference:op.external_reference,receipt_status:op.receipt?.status||null},
        counts,
      };
      validateProof(report);
      return report;
    });
    console.log(PREFIX+JSON.stringify({ok:true,read_only:true,...proof}));
  } catch (err) {
    // No database messages or secrets in report.
    console.log(PREFIX+JSON.stringify({ok:false,reason: /^([A-Z_]+)$/.test(String(err.message))?err.message:'QUERY_FAILED'}));
    process.exitCode = 1;
  } finally {await db.$disconnect();}
})();
