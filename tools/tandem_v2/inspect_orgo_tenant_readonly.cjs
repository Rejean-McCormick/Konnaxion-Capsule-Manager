'use strict';
/**
 * Read-only audit against the SAME database as the selected running Orgo API.
 * The caller runs this via Docker `compose exec -T api node -` or local `node -`.
 * No credentials, environment variables, or connection strings are printed.
 */
const MARKER = '__KOA_ORGO_IDENTITY__=';

function safeError(error, stage) {
  // Deliberately never print exception messages, SQL, DSNs or environment variables.
  const rawCode = typeof error?.code === 'string' ? error.code : '';
  const code = /^[A-Z][0-9A-Z_]{1,24}$/.test(rawCode) ? rawCode : 'QUERY_FAILED';
  const msg = String(error?.message || ''); // For classification ONLY, never output.
  const name = String(error?.name || '');
  let kind = 'UNCLASSIFIED';
  let action = 'Verifier les journaux Orgo localement, apres suppression des informations confidentielles.';
  if (rawCode === 'MODULE_NOT_FOUND' || /Cannot find module ['"]@prisma\/client/.test(msg)) {
    kind = 'PRISMA_MODULE_MISSING';
    action = 'Dans Orgo, verifier node_modules/@prisma/client et executer le diagnostic dans le conteneur API actif si disponible. Ne pas lancer de migrations.';
  } else if (/did not initialize yet|run \"prisma generate\"/i.test(msg)) {
    kind = 'PRISMA_CLIENT_NOT_GENERATED';
    action = 'Le client Prisma local n est pas genere. Preferer le conteneur API actif; ne pas lancer de migration.';
  } else if (['P1000'].includes(rawCode)) {
    kind = 'DATABASE_AUTH_FAILED';
    action = 'Les identifiants DB sont rejetes. Verifier la configuration du serveur Orgo, sans partager les secrets.';
  } else if (['P1001', 'P1002', 'P1017'].includes(rawCode) || /ECONNREFUSED|ETIMEDOUT|ENOTFOUND|EAI_AGAIN/i.test(msg)) {
    kind = 'DATABASE_UNREACHABLE';
    action = 'Verifier que PostgreSQL est en marche et accessible depuis le mode utilise. Un hostname Docker comme postgres ne fonctionne pas forcement avec Node Windows.';
  } else if (rawCode === 'P1003') {
    kind = 'DATABASE_NOT_FOUND';
    action = 'La base cible n existe pas ou son nom est incorrect. Verifier la configuration du serveur Orgo.';
  } else if (['P1012', 'P1013'].includes(rawCode)) {
    kind = 'DATABASE_CONFIGURATION';
    action = 'Verifier que DATABASE_URL et la version Prisma correspondent au schema Orgo. Ne pas publier l URL.';
  } else if (['P2021', 'P2022', 'P1014'].includes(rawCode)) {
    kind = 'SCHEMA_NOT_ALIGNED';
    action = 'Table ou colonne requise absente. Verifier la base et les migrations deployees par Orgo, sans executer de migration ici.';
  } else if (rawCode === 'P2010') {
    kind = 'READ_ONLY_PROBE_REJECTED';
    action = 'La DB refuse la commande de transaction lecture seule. Ne pas poursuivre automatiquement.';
  } else if (/PrismaClientValidationError|Unknown (field|argument)|Invalid.*prisma\./i.test(name + ' ' + msg)) {
    kind = 'PRISMA_SCHEMA_MISMATCH';
    action = 'Le client Prisma genere ne correspond probablement pas au schema du depot. Verifier le client du serveur API.';
  } else if (name === 'PrismaClientInitializationError') {
    kind = 'PRISMA_INIT_FAILED';
    action = 'Verifier Prisma et sa connexion dans le processus API Orgo. Aucun schema ne doit etre modifie pour le diagnostic.';
  }
  return {
    ok: false, error_code: code, error_kind: kind, error_stage: stage,
    suggested_action: action, read_only: true
  };
}

async function main() {
  let prisma;
  let stage = 'PRISMA_IMPORT';
  try {
    const { PrismaClient } = require('@prisma/client');
    stage = 'PRISMA_INIT';
    prisma = new PrismaClient({ log: [] });
    stage = 'TRANSACTION_START';
    const report = await prisma.$transaction(async (tx) => {
      // Database-level safeguard: all queries in this transaction MUST be read-only.
      stage = 'SET_READ_ONLY';
      await tx.$executeRaw`SET TRANSACTION READ ONLY`;
      stage = 'ORGANIZATION_LOOKUP';
      const org = await tx.organization.findUnique({
        where: { slug: 'fakeplant' },
        select: { id: true, slug: true, display_name: true, status: true }
      });
      if (!org) return {
        ok: true, schema: 'koa.orgo-tenant-readonly/v1',
        universe: 'fakeplant', organization_found: false,
        organization: null, published_workflows: [],
        note: 'Aucun Organization(slug=fakeplant) dans la base interrogee. Aucune creation effectuee.'
      };
      stage = 'WORKFLOW_LOOKUP';
      const definitions = await tx.workflowDefinition.findMany({
        where: {
          is_active: true,
          OR: [{ organization_id: org.id }, { organization_id: null }]
        },
        select: {
          id: true, code: true, organization_id: true,
          versions: {
            orderBy: { version: 'desc' }, take: 2,
            select: { id: true, version: true, published_at: true }
          }
        },
        orderBy: { code: 'asc' }, take: 60
      });
      const published = definitions.flatMap((d) => d.versions.map((v) => ({
        workflow_code: d.code,
        workflow_version_id: v.id,
        workflow_version: v.version,
        scope: d.organization_id === org.id ? 'organization' : 'global',
        published_at: v.published_at?.toISOString?.() || String(v.published_at)
      })));
      return {
        ok: true, schema: 'koa.orgo-tenant-readonly/v1', universe: 'fakeplant',
        organization_found: true, organization: org, published_workflows: published,
        note: 'Un workflow publie et un UUID ne prouvent PAS les droits ACL ou les echanges IK.'
      };
    });
    process.stdout.write(MARKER + JSON.stringify(report) + '\n');
  } catch (error) {
    process.stdout.write(MARKER + JSON.stringify(safeError(error, stage)) + '\n');
    process.exitCode = 3;
  } finally {
    if (prisma) {
      try { await prisma.$disconnect(); } catch { /* no secrets */ }
    }
  }
}
main();
