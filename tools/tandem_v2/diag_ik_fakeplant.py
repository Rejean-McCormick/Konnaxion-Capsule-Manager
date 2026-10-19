"""Read-only readiness diagnostic for local Konnaxion (uv/Neon) + Orgo Docker.

No credentials are printed or persisted. No SQL write statements are executed.
"""
from __future__ import annotations

import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

FAKEPLANT_UUID = "0c2d7244-a0c0-4a10-8f11-0389f275f133"
KX_BACKEND = Path(r"C:\mycode\Konnaxion\Konnaxion\backend")
ORGO_ROOT = Path(r"C:\mycode\Orgo\Orgo")
EVIDENCE = Path(r"C:\mycode\Konnaxion\Tandem_Local\fakeplant-identity-verified.LOCAL.json")

KONNAXION_CODE = r'''
import json
from django.conf import settings
from konnaxion.ethikos.models import InteractionEmission, OrgoImpactPublication
from urllib.parse import urlsplit
url = str(getattr(settings,'IK_ORGO_INTERACTIONS_URL','') or '').strip()
p = urlsplit(url)
result = {
  'db_postgresql': settings.DATABASES['default']['ENGINE'] == 'django.db.backends.postgresql',
  'db_name': settings.DATABASES['default']['NAME'],
  'ik_out_url': bool(url),
  'ik_out_local_http': bool(p.scheme == 'http' and p.hostname in ('127.0.0.1','localhost','::1')),
  'ik_out_https': bool(p.scheme == 'https'),
  'ik_out_token': bool(getattr(settings,'IK_ORGO_TOKEN','')),
  'ik_target_matches': str(getattr(settings,'IK_ORGO_TARGET_ORGANIZATION','')) == '0c2d7244-a0c0-4a10-8f11-0389f275f133',
  'ik_inbound_token': bool(getattr(settings,'IK_ORGO_INBOUND_TOKEN','')),
  'strict_world_scope': bool(getattr(settings,'KONNAXION_WORLDS_ENFORCE_SCOPED_API',False)),
  'debug': bool(settings.DEBUG),
  'outbound_emissions': InteractionEmission.objects.filter(target_organization='0c2d7244-a0c0-4a10-8f11-0389f275f133').count(),
  'inbound_impacts': OrgoImpactPublication.objects.filter(organization_id='0c2d7244-a0c0-4a10-8f11-0389f275f133').count(),
}
print('IK_DIAG_JSON='+json.dumps(result,default=str))
'''

ORGO_API_CODE = r'''
const { PrismaClient } = require('@prisma/client');
const db = new PrismaClient();
const uuid = '0c2d7244-a0c0-4a10-8f11-0389f275f133';
(async () => {
  try {
    const result = await db.$transaction(async tx => {
      await tx.$executeRaw`SET TRANSACTION READ ONLY`;
      const org = await tx.organization.findUnique({where: {slug: 'fakeplant'}, select: {id:true, status:true}});
      if (!org || org.id !== uuid) throw Error('ORGO_TENANT_MISMATCH');
      const code = (process.env.KONNAXION_DECISION_WORKFLOW_CODE || '').trim();
      const defs = await tx.workflowDefinition.findMany({
        where: {organization_id: uuid, is_active: true, ...(code ? {code} : {})},
        select: {id:true,code:true,versions:{select:{id:true},take:1}}
      });
      const tokens = await tx.apiToken.findMany({
        where: {organization_id: uuid, OR:[{expires_at:null},{expires_at:{gt:new Date()}}]},
        select: {scopes:true}
      });
      const canIK = tokens.filter(t => ['signals:write','workflows:execute'].every(x => t.scopes.includes(x))).length;
      return {
        organization_id: org.id, status:org.status,
        workflow_code_configured:!!code,
        workflow_active_version:defs.some(d=>d.versions.length>0),
        tenant_workflow_definitions_active:defs.length,
        valid_tokens_with_ik_scopes:canIK,
        api_is_configured_for_outbound:!!process.env.KONNAXION_IK_URL,
      };
    });
    console.log('IK_DIAG_JSON='+JSON.stringify(result));
  } catch (err) {
    console.error('IK_DIAG_ERROR='+(err && err.message === 'ORGO_TENANT_MISMATCH' ? 'ORGO_TENANT_MISMATCH' : 'DB_OR_SCHEMA_UNAVAILABLE'));
    process.exitCode = 1;
  } finally { await db.$disconnect(); }
})();
'''

WORKER_CODE = r'''
const u = (process.env.KONNAXION_IK_URL || '').trim();
let url = null;
try { url = u ? new URL(u) : null; } catch {}
const local = url && ['localhost','127.0.0.1','host.docker.internal'].includes(url.hostname);
const permitted = !!url && (url.protocol === 'https:' || (!!local && process.env.ORGO_ALLOW_INSECURE_LOCAL_PROVIDER_HTTP === 'true'));
console.log('IK_DIAG_JSON='+JSON.stringify({
  outbound_url: !!u,
  outbound_token: !!process.env.KONNAXION_IK_TOKEN,
  outbound_url_policy_ok: !!permitted,
  insecure_local_http_permitted: process.env.ORGO_ALLOW_INSECURE_LOCAL_PROVIDER_HTTP === 'true',
  node_env: process.env.NODE_ENV || ''
}));
'''

WORKER_PROBE = r'''
(async()=>{
  try {
    const r = await fetch('http://host.docker.internal:8000/health/ready/',{signal:AbortSignal.timeout(4500)});
    console.log('IK_DIAG_JSON='+JSON.stringify({http_status:r.status,ok:r.ok}));
  } catch {
    console.log('IK_DIAG_JSON='+JSON.stringify({ok:false, error:'KONNAXION_NOT_REACHABLE_FROM_WORKER'}));
  }
})();
'''


def run_json(command: list[str], cwd: Path, stdin: str | None = None, timeout: int = 45) -> dict:
    """Sanitize subprocess failures: never dump environment/secret-bearing output."""
    try:
        result = subprocess.run(command, cwd=str(cwd), input=stdin, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError('outil indisponible ou expiration du delai') from exc
    if result.returncode != 0:
        raise RuntimeError('commande refusee ou service non disponible (code %d)' % result.returncode)
    for line in result.stdout.splitlines():
        if line.startswith('IK_DIAG_JSON='):
            value = json.loads(line[len('IK_DIAG_JSON='):])
            if not isinstance(value, dict):
                raise RuntimeError('reponse non conforme')
            return value
    raise RuntimeError('reponse de diagnostic absente')


def http_ready(url: str) -> bool:
    try:
        with urllib.request.urlopen(urllib.request.Request(url, method='GET'), timeout=4) as response:
            return 200 <= response.status < 300
    except (OSError, urllib.error.URLError):
        return False


def print_check(label: str, value: bool, *, missing: str = 'A_CONFIGURER') -> None:
    print(f"{'[OK]' if value else '['+missing+']'} {label}")


def recommendations(kx: dict, api: dict, worker: dict, host_kx: bool, worker_kx: bool) -> list[str]:
    blockers = []
    if not kx.get('db_postgresql') or kx.get('db_name') != 'neondb':
        blockers.append('Konnaxion doit viser la base attendue neondb (verifier la configuration reelle).')
    if not kx.get('ik_out_url') or not kx.get('ik_out_token') or not kx.get('ik_target_matches'):
        blockers.append('Configurer la sortie IK de Django vers Orgo avec un NOUVEAU jeton scopes signals:write + workflows:execute, tenant fixe.')
    if not kx.get('ik_inbound_token') or not worker.get('outbound_token') or not worker.get('outbound_url'):
        blockers.append('Configurer la paire de secrets Konnaxion ingress / Orgo Worker outbound; ne pas reutiliser le jeton FP-S01.')
    if worker.get('outbound_url') and not worker.get('outbound_url_policy_ok'):
        blockers.append('Le transport Orgo Worker -> Konnaxion exige HTTPS ou exception HTTP locale explicite.')
    if not api.get('workflow_code_configured') or not api.get('workflow_active_version'):
        blockers.append('Publier un workflow minimal pour le tenant FakePlant et configurer le code sur l API Orgo.')
    if not api.get('valid_tokens_with_ik_scopes'):
        blockers.append('Creer un nouveau jeton IK dedie avec les scopes Orgo requis et expiration courte.')
    if not host_kx or not worker_kx:
        blockers.append('Konnaxion doit etre accessible en HTTP depuis Windows ET depuis Orgo Worker (host.docker.internal:8000).')
    return blockers


def main() -> int:
    print('=== TANDEM FAKEPLANT / IK v1.1 : LECTURE SEULE ===')
    print('Topologie : Konnaxion Windows uv/Neon ; Orgo Docker ; IK contrat ik/1.1')
    print('Tenant Orgo attendu : fakeplant / '+FAKEPLANT_UUID)
    print('Aucun secret ni valeur de jeton affiches. Aucune migration, import ou deploiement.')
    if not KX_BACKEND.is_dir() or not ORGO_ROOT.is_dir():
        print('[BLOQUE] Depots absents des chemins attendus.')
        return 2
    try:
        kx = run_json(['uv','run','--no-sync','python','manage.py','shell','-c',KONNAXION_CODE], KX_BACKEND, timeout=60)
        print('\n--- Konnaxion : Django / Neon ---')
        print_check('Base PostgreSQL neondb', kx.get('db_postgresql') and kx.get('db_name') == 'neondb')
        for key,label in [('ik_out_url','IK_ORGO_INTERACTIONS_URL'),('ik_out_token','IK_ORGO_TOKEN'),('ik_target_matches','IK_ORGO_TARGET_ORGANIZATION (UUID)'),('ik_inbound_token','IK_ORGO_INBOUND_TOKEN')]:
            print_check(label, bool(kx.get(key)))
        print_check('Mode World scope strict', bool(kx.get('strict_world_scope')), missing='INFO')
        print(f"[INFO] Emissions IK vers FakePlant : {kx.get('outbound_emissions')}; impacts recus : {kx.get('inbound_impacts')}")
    except RuntimeError as exc:
        print('[BLOQUE] Django : '+str(exc))
        return 2
    try:
        api = run_json(['docker','compose','exec','-T','api','node','-'], ORGO_ROOT, ORGO_API_CODE)
        worker = run_json(['docker','compose','exec','-T','worker','node','-'], ORGO_ROOT, WORKER_CODE)
        print('\n--- Orgo : API Docker / PostgreSQL ---')
        print_check('Tenant FakePlant UUID exact', api.get('organization_id') == FAKEPLANT_UUID)
        print_check('Workflow decision code present', bool(api.get('workflow_code_configured')))
        print_check('Workflow actif avec version publiee', bool(api.get('workflow_active_version')))
        print_check('Au moins un jeton Orgo avec scopes IK', bool(api.get('valid_tokens_with_ik_scopes')))
        print('\n--- Orgo : Worker Docker (sortie vers Konnaxion) ---')
        for key, label in [('outbound_url','KONNAXION_IK_URL'),('outbound_token','KONNAXION_IK_TOKEN'),('outbound_url_policy_ok','Politique transport URL OK')]:
            print_check(label, bool(worker.get(key)))
    except RuntimeError as exc:
        print('[BLOQUE] Orgo Docker : '+str(exc))
        return 2
    host_kx = http_ready('http://127.0.0.1:8000/health/ready/')
    try:
        worker_probe = run_json(['docker','compose','exec','-T','worker','node','-'], ORGO_ROOT, WORKER_PROBE, timeout=15)
        worker_kx = worker_probe.get('ok') is True
    except RuntimeError:
        worker_kx = False
    print('\n--- Reseau (GET sante seulement) ---')
    print_check('Konnaxion repond sur Windows localhost:8000', host_kx)
    print_check('Orgo Worker accede au backend Konnaxion via host.docker.internal:8000', worker_kx)
    print_check('Orgo API repond sur Windows localhost:4000', http_ready('http://127.0.0.1:4000/health/ready'))
    print_check('Preuve d identites locale deja creee', EVIDENCE.is_file())
    blockers = recommendations(kx, api, worker, host_kx, worker_kx)
    if kx.get('strict_world_scope'):
        print('[INFO] Le futur E2E devra fixer explicitement World/release; kit historique inadapté.')
    print('\n--- Etapes restantes ---')
    for i, item in enumerate(blockers,1): print(f'[ATTENTE {i}] {item}')
    if not blockers:
        print('[CONFIG OK] Controles de configuration positifs; aucun test IK POST ni ACL E2E effectue.')
    print('[FIN] Lecture seule DB; requetes de sante GET; aucun jeton imprime ni source modifiee.')
    return 0 if not blockers else 3


if __name__ == '__main__':
    sys.exit(main())
