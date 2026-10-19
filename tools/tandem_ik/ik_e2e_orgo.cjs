'use strict';
// Run inside the Orgo API container. Uses READ ONLY transaction and a pinned organization.
const { PrismaClient } = require('@prisma/client');
const db = new PrismaClient();
const ORG = '0c2d7244-a0c0-4a10-8f11-0389f275f133';
const decision = process.env.KOA_FAKEPLANT_DECISION_ID;
const guardMode = decision === "0";
const PREFIX = 'KOA_FAKEPLANT_ORGO_JSON=';
(async () => {
  try {
    if (!/^(0|[1-9][0-9]{0,12})$/.test(decision || '')) throw new Error('decision_id invalide');
    const value = await db.$transaction(async tx => {
      await tx.$executeRaw`SET TRANSACTION READ ONLY`;
      const org = await tx.organization.findUnique({where:{id:ORG},select:{slug:true,status:true}});
      if (!org || org.slug !== 'fakeplant' || org.status !== 'active') throw new Error('Tenant inattendu');
      const workflow = await tx.workflowDefinition.findFirst({
        where:{organization_id:ORG,code:'konnaxion_decision_v1'},
        select:{version:true,is_active:true,versions:{select:{version:true,content:true}}}
      });
      const v2 = workflow?.versions?.find(v=>v.version===2);
      const actions = Array.isArray(v2?.content?.rules)
        ? v2.content.rules.flatMap(r => Array.isArray(r.actions) ? r.actions : []) : [];
      const impactAction = actions.find(a=>a.type==='REQUEST_INTEGRATION' && a.input?.provider==='konnaxion' && a.input?.operation==='publish_impact');
      const input = impactAction?.input?.request || {};
      const scopesRequired = ['signals:write','workflows:execute','signals:read','work:read','work:write','integrations:write'];
      const tokens = await tx.apiToken.findMany({
        where:{organization_id:ORG,name:'koa.fakeplant.ik-outbound-v1'},
        select:{scopes:true,expires_at:true},take:3
      });
      const tokenOk = tokens.length===1 && tokens[0].expires_at && tokens[0].expires_at > new Date()
        && scopesRequired.every(s=>tokens[0].scopes.includes(s))
        && tokens[0].scopes.length === scopesRequired.length;
      const ready = !!(workflow?.is_active && workflow.version === 2 && v2 && impactAction
        && typeof input.external_reference==='string' && input.external_reference.length>0
        && input.summary && typeof input.summary==='object' && tokenOk);
      if (guardMode) return {ok:ready, mode:'guard', workflow_v2_ready:ready, token_scopes_ok:!!tokenOk, read_only:true};
      const ref = `konnaxion:decision:${decision}:r1`;
      const signal = await tx.signal.findFirst({where:{organization_id:ORG,external_reference:ref}, select:{id:true,status:true,case_id:true,correlation_id:true}});
      const title = `Governed decision ${decision}`;
      const cases = await tx.case.findMany({where:{organization_id:ORG,title},select:{id:true,status:true},take:4});
      const tasks = await tx.task.findMany({where:{organization_id:ORG,title},select:{id:true,case_id:true,status:true},take:4});
      const integrations = await tx.integrationOperation.findMany({where:{organization_id:ORG,provider:'konnaxion',operation:'publish_impact'},select:{id:true,correlation_id:true,status:true,error:true},orderBy:{created_at:'desc'},take:12});
      const instances = signal ? await tx.workflowInstance.findMany({where:{organization_id:ORG,signal_id:signal.id},select:{id:true,status:true},take:4}) : [];
      const scopedIntegrations = integrations.filter(i => i.correlation_id === `decision:${decision}`);
      return {
        ok: ready, workflow_v2_ready:ready, token_scopes_ok:!!tokenOk, tenant: 'fakeplant', decision_id: decision,
        signal: signal || null, workflow_instances: instances,
        cases, tasks, impact_operations: scopedIntegrations,
        matched_case_task: cases.length === 1 && tasks.some(t => t.case_id === cases[0].id),
        read_only: true,
      };
    });
    console.log(PREFIX+JSON.stringify(value));
    if (!value.ok) process.exitCode = 2;
  } catch (e) {
    console.log(PREFIX+JSON.stringify({ok:false,error:String((e && e.code)|| (e && e.message) || 'QUERY_FAILED').slice(0,250)}));
    process.exitCode = 1;
  } finally {await db.$disconnect();}
})();
