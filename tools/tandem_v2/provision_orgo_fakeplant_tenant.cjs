'use strict';
/**
 * Minimal, idempotent FakePlant organization provisioning.
 * Intended ONLY for stdin execution inside the running Orgo API container:
 *   docker compose exec -T api node - --preview|--apply
 * Never seeds users, credentials, profiles, Cases, workflows or roles.
 * Do not print error.message: it can contain connection strings.
 */
const MARK = '__KOA_ORGO_TENANT_V2__=';
function report(x) { process.stdout.write(MARK + JSON.stringify(x) + '\n'); }
const mode = process.argv.includes('--apply') ? 'apply' : 'preview';
const allowed = ['--apply', '--preview'];
if (process.argv.slice(2).some(s => !allowed.includes(s)) ||
    (process.argv.includes('--apply') && process.argv.includes('--preview'))) {
  report({ok:false, error_code:'BAD_ARGUMENTS'});
  process.exitCode=2;
} else {
  main().catch(e => {
    const code = typeof e?.code === 'string' && /^[A-Z][0-9A-Z_]{1,24}$/.test(e.code) ? e.code : 'QUERY_FAILED';
    report({ok:false, error_code:code, error_stage:'TENANT_PROVISION', no_other_entities_touched:true});
    process.exitCode=3;
  });
}
async function main() {
  const {PrismaClient} = require('@prisma/client');
  const db = new PrismaClient({log: []});
  try {
    if (!process.env.DATABASE_URL) throw Object.assign(new Error('No DATABASE_URL'), {code:'MISSING_DATABASE_URL'});
    const database = await db.$queryRaw`SELECT current_database() AS name`;
    const dbName = String(database[0]?.name || 'unknown');
    if (mode === 'preview') {
      const existing = await db.$transaction(async tx => {
        await tx.$executeRaw`SET TRANSACTION READ ONLY`;
        return tx.organization.findUnique({
          where: {slug:'fakeplant'},
          select: {id:true, slug:true, display_name:true, status:true, timezone:true, default_locale:true}
        });
      });
      report({ok:true, mode, database:dbName, organization:existing,
        would_create: !existing, would_change_existing:false, no_other_entities_touched:true});
      return;
    }
    const value = await db.$transaction(async tx => {
      const existing = await tx.organization.findUnique({
        where: {slug:'fakeplant'},
        select: {id:true,slug:true,display_name:true,status:true,timezone:true,default_locale:true}
      });
      if (existing) {
        if (existing.status !== 'active') {
          throw Object.assign(new Error('Existing org inactive'), {code:'EXISTING_INACTIVE_ORG'});
        }
        return {organization:existing, created:false};
      }
      const organization = await tx.organization.create({
        data:{
          slug:'fakeplant',
          display_name:'FakePlant \u2014 Simulation industrielle',
          status:'active', timezone:'America/Toronto', default_locale:'fr-CA'
        },
        select:{id:true, slug:true, display_name:true, status:true, timezone:true, default_locale:true}
      });
      return {organization, created:true};
    });
    const confirmed = await db.organization.findUnique({
      where: {slug:'fakeplant'},
      select: {id:true, slug:true, status:true}
    });
    if (!confirmed || confirmed.id !== value.organization.id || confirmed.status !== 'active') {
      throw Object.assign(new Error('Postcheck mismatch'), {code:'POSTCHECK_MISMATCH'});
    }
    report({ok:true, mode, database:dbName, organization:value.organization,
      created:value.created, verified:true, no_other_entities_touched:true,
      identity_binding_complete:false, ik_e2e_validated:false});
  } finally {
    await db.$disconnect();
  }
}
