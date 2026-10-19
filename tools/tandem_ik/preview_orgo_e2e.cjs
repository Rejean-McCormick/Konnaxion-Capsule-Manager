'use strict';
const { PrismaClient } = require('@prisma/client');
const db = new PrismaClient();
const UUID = '0c2d7244-a0c0-4a10-8f11-0389f275f133';
const WORLD = 'fp-electromecanique-informatique';
const CODE = 'konnaxion_decision_v1';
(async () => {
  try {
    const result = await db.$transaction(async (tx) => {
      await tx.$executeRaw`SET TRANSACTION READ ONLY`;
      const org = await tx.organization.findUnique({ where: { id: UUID }, select: { id: true, slug: true, status: true } });
      const workflow = await tx.workflowDefinition.findFirst({
        where: { organization_id: UUID, code: CODE },
        select: { id: true, code: true, is_active: true, version: true },
      });
      const versions = workflow ? await tx.workflowVersion.count({ where: { workflow_definition_id: workflow.id } }) : 0;
      const disabled = await tx.userAccount.count({ where: { organization_id: UUID, status: 'disabled' } });
      const total = await tx.userAccount.count({ where: { organization_id: UUID } });
      const envCode = (process.env.KONNAXION_DECISION_WORKFLOW_CODE || '').trim();
      const label = (process.env.KONNAXION_DECISION_LABEL || '').trim();
      const routeWorld = (process.env.ORGO_IK_WORLD || '').trim();
      const ok = !!(org && org.slug === 'fakeplant' && org.status === 'active' && workflow && workflow.is_active && versions > 0 && total === 900 && disabled === 900 && envCode === CODE && /^[1-9]\d*\.[1-9][1-5](?:\.[A-Za-z0-9]+)*$/.test(label) && (!routeWorld || routeWorld === WORLD));
      return {
        ok, organization: org ? org.slug : null, organization_uuid_matches: !!org, workflow_code: envCode,
        workflow_active: !!(workflow && workflow.is_active), published_versions: versions,
        decision_label_format_ok: /^[1-9]\d*\.[1-9][1-5](?:\.[A-Za-z0-9]+)*$/.test(label),
        world_routing_compatible: !routeWorld || routeWorld === WORLD,
        personas_total: total, personas_disabled: disabled, read_only: true,
      };
    });
    console.log('IK_E2E_ORGO_JSON=' + JSON.stringify(result));
    if (!result.ok) process.exitCode = 2;
  } catch (err) {
    console.error('IK_E2E_ORGO_ERROR=' + String(err && err.code || 'QUERY_FAILED'));
    process.exitCode = 1;
  } finally { await db.$disconnect(); }
})();
