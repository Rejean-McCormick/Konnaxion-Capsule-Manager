"""Tandem FakePlant V1 — étapes persistantes, reprises sûres, diagnostic Konfid.

NE JAMAIS rejouer une décision IK ou créer d'utilisateur/tenant/role de manière implicite.
L'unique option modifiante est `guard-install` (deux fichiers source, sauvegardés).
"""
from __future__ import annotations
import argparse
import ast
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1].parent  # Capsule_Manager
REPO = ROOT.parent
BACKEND = REPO/'Konnaxion'/'backend'
KX = BACKEND.parent
ORGO = Path(os.environ.get('KOA_ORGO_REPO',r'C:\mycode\Orgo\Orgo'))
VENDOR = Path(__file__).resolve().parent/'vendor'
REPORT_DIR = Path(os.environ.get('KOA_TANDEM_REPORT_DIR',str(REPO/'Tandem_Local'/'orchestrateur')))
IK_FILE = REPO/'Tandem_Local'/'fakeplant-IK-E2E-LIVE.LOCAL.json'
TENANT = '0c2d7244-a0c0-4a10-8f11-0389f275f133'
WORLD = 'fp-electromecanique-informatique'
STEPS = ('inventory','ik-proof','guard-preview','sso-acl','konfid-inventory')
CODE = 'tandem.fakeplant.pipeline/v1'


def now():return datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')

def scrub(s):
    for pat,rep in [
        (r'(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{8,}',r'\1[REDACTED]'),
        (r'(?i)(\b(?:password|token|client_secret|secret|api_key)\s*[=:]\s*)\S+',r'\1[REDACTED]'),
        (r'(?i)(postgres(?:ql)?://)[^/\s@]+@',r'\1[REDACTED]@'),
        (r'(?i)(https?://)[^/\s@]+@',r'\1[REDACTED]@')]:
        s=re.sub(pat,rep,s)
    return s


def launch(args,cwd,timeout=180):
    start=time.monotonic()
    try:
        p=subprocess.run(args,cwd=str(cwd),text=True,encoding='utf-8',errors='replace',
            stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=timeout,check=False)
        return p.returncode,scrub(p.stdout)[:100000],round(time.monotonic()-start,2)
    except subprocess.TimeoutExpired:
        return 124,f'Commande interrompue après {timeout} s. Aucune relance automatique.',round(time.monotonic()-start,2)
    except OSError as exc:
        return 127,'Commande indisponible : '+scrub(str(exc)),round(time.monotonic()-start,2)


def hashes(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def inventory():
    missing=[name for name,p in [('Capsule Manager',ROOT),('Django',BACKEND/'manage.py'),('Orgo',ORGO/'docker-compose.yml')]
             if not p.exists()]
    tools={c:bool(shutil.which(c)) for c in ('pwsh','uv','docker')}
    checks={}
    try:
        manifest=json.loads((Path(__file__).resolve().parent/'vendor_hashes.json').read_text(encoding='utf-8'))
        checks={name:(VENDOR/name).is_file() and hashes(VENDOR/name)==sha for name,sha in manifest['vendor'].items()}
    except (OSError,ValueError,KeyError):
        return 'failed','Manifeste de versions des outils manquant/invalide.',{}
    if not checks or not all(checks.values()):
        return 'failed','Outil embarqué manquant ou modifié; refus (installer une version contrôlée du pack).',{'vendor_integrity':checks}
    meta={'paths':{'manager':str(ROOT),'backend':str(BACKEND),'orgo':str(ORGO)},'tools':tools,'vendor_integrity':checks,
          'source_world':WORLD,'source_release':132,'organization':TENANT}
    if missing or not all(tools.values()):return 'failed','Chemins / outils manquants : '+', '.join(missing+[k for k,v in tools.items() if not v]),meta
    return 'ok','Chemins, uv, Docker et PowerShell 7 présents.',meta


def ik_proof():
    src=VENDOR/'ik/FINALISER_FAKEPLANT_IK_E2E.ps1'
    if not src.is_file():return 'failed','Vérificateur IK manquant.',{}
    code,out,seconds=launch(['pwsh','-NoProfile','-ExecutionPolicy','Bypass','-File',str(src)],ROOT,timeout=240)
    meta={'exit_code':code,'duration_seconds':seconds,'output':out}
    if code:return 'failed','Corroboration IK refusée (aucune création/réexécution IK).',meta
    try:
        data=json.loads(IK_FILE.read_text(encoding='utf-8-sig'))
        checks=(data.get('schema')=='koa.fakeplant-ik-e2e-local/v2' and data.get('complete') is True
                and data.get('tenant_uuid')==TENANT and data.get('source_world')==WORLD
                and data.get('source_release_id')==132 and data.get('konnaxion',{}).get('decision_id')==1)
        if not checks:return 'failed','Rapport IK V2 non conforme.',meta
        meta['report_sha256']=hashes(IK_FILE)
        return 'ok','IK décision 1 et impact revalidés, non-duplication et preuves Orgo/Neon.',meta
    except (OSError,ValueError,AttributeError,TypeError):return 'failed','Rapport IK absent/invalide après vérification.',meta


def guard_preview():
    src=VENDOR/'guard/patch.py'
    if not src.is_file():return 'failed','Garde-fou absent.',{}
    code,out,seconds=launch([sys.executable,str(src),'--root',str(KX)],BACKEND,timeout=35)
    meta={'exit_code':code,'duration_seconds':seconds,'output':out}
    if code:return 'failed','Structure des fichiers SSO inattendue. Aucun changement.',meta
    try:
        info=json.loads(out)
        if not info.get('ok') or info.get('mode')!='preview':raise ValueError('unexpected')
    except ValueError:return 'failed','Réponse du garde-fou invalide.',meta
    meta={'target_files':info.get('files_to_change',[]),'states':info.get('statuses',{}),'duration_seconds':seconds}
    return 'ok','Prévisualisation garde-fou prête, sans installation automatique.',meta


def sso_acl():
    src=VENDOR/'acl/audit_pair.py'
    if not src.is_file():return 'failed','Audit ACL absent.',{}
    code,out,seconds=launch([sys.executable,str(src)],BACKEND,timeout=180)
    meta={'exit_code':code,'duration_seconds':seconds,'output':out}
    if code:return 'failed','Audit SSO/ACL interrompu; aucun rôle/compte modifié.',meta
    if '[READY]' not in out and '[ATTENTE]' not in out:return 'failed','État SSO inconnu.',meta
    return 'waiting','Audit terminé : SSO humain et Konfid non qualifiés; lire les prérequis.',meta


def konfid_inventory():
    specified=os.getenv('KOA_KONFID_REPO','').strip()
    locations=([Path(specified)] if specified else [])+[REPO/'Konfid'/'Konfid',REPO/'Konfid',REPO/'konfid'/'konfid',REPO/'konfid']
    found=next((p for p in locations if (p/'src/konfid/api.py').is_file() and (p/'docs/INTEGRATIONS.md').is_file()),None)
    meta={'repository':str(found) if found else None,'live_service_verified':False,
          'human_principal_verified':False,'authorization_enforce_enabled':False,
          'oidc_idp_role':'external to Konfid'}
    if not found:
        return 'waiting','Konfid non localisé : configurer KOA_KONFID_REPO si disponible; aucun déploiement présumé.',meta
    try:
        source=(found/'src/konfid/api.py').read_text(encoding='utf-8')
        auth=(found/'docs/AUTH.md').read_text(encoding='utf-8')
        expected=['/identity/resolve','/identity/bindings','/account/bindings','/access/evaluate']
        meta['source_contracts']={p:p in source for p in expected}
        meta['actor_assertion_documented']='actor' in auth.lower()
        meta['service_auth_documented']='workload' in auth.lower()
    except OSError:return 'failed','Impossible de lire les contrats Konfid.',meta
    if not all(meta['source_contracts'].values()):return 'failed','Routes Konfid manquantes; contrat inattendu.',meta
    return 'waiting','Contrats Konfid présents, mais service/principal/bindings humains non testés.',meta


def install_guard():
    result=guard_preview()
    if result[0]!='ok':return result
    files=[KX/'backend/config/settings/base.py',KX/'backend/konnaxion/users/adapters.py']
    if any(not p.is_file() for p in files):return 'failed','Fichiers sources Konnaxion absents.',{}
    folder=REPORT_DIR/'sauvegardes_sources'/now()
    folder.mkdir(parents=True,exist_ok=False)
    manifest={}
    for i,p in enumerate(files):
        dest=folder/f'{i:02d}_{p.name}'
        shutil.copy2(p,dest)
        manifest[str(p.relative_to(KX))]={'sha256':hashes(p),'copie':str(dest)}
    (folder/'MANIFEST.json').write_text(json.dumps(manifest,indent=2,ensure_ascii=False),encoding='utf-8')
    code,out,seconds=launch([sys.executable,str(VENDOR/'guard/patch.py'),'--root',str(KX),'--apply'],BACKEND,timeout=50)
    meta={'backup_directory':str(folder),'exit_code':code,'duration_seconds':seconds,'output':out}
    if code:return 'failed','Installation refusée; sauvegarde conservée pour diagnostic.',meta
    try:
        result=json.loads(out)
        if not result.get('ok') or result.get('mode')!='apply':raise ValueError('output')
        for p in files:ast.parse(p.read_text(encoding='utf-8-sig'))
        meta['changed_files']=result.get('changed_files',[])
    except (SyntaxError,ValueError):return 'failed','Sortie ou syntaxe inattendue; inspecter la sauvegarde avant tout rollback.',meta
    return 'ok','Garde-fou installé, sauvegarde persistante; OIDC demeure désactivé.',meta


HANDLERS={'inventory':inventory,'ik-proof':ik_proof,'guard-preview':guard_preview,
          'sso-acl':sso_acl,'konfid-inventory':konfid_inventory,'guard-install':install_guard}


def latest():
    p=REPORT_DIR/'LATEST_REPORT.json'
    try:return json.loads(p.read_text(encoding='utf8')) if p.is_file() else None
    except (ValueError,OSError):return None


def store(data):
    REPORT_DIR.mkdir(parents=True,exist_ok=True)
    file=REPORT_DIR/(data['run_id']+'.json')
    b=json.dumps(data,indent=2,ensure_ascii=False)
    file.write_text(b,encoding='utf-8')
    temp=REPORT_DIR/('LATEST_REPORT.'+data['run_id']+'.tmp')
    temp.write_text(b,encoding='utf-8')
    os.replace(temp,REPORT_DIR/'LATEST_REPORT.json')
    return file


def print_summary(steps):
    for x in steps:
        print({'ok':'[OK]','waiting':'[ATTENTE]','failed':'[BLOQUE]'}[x['status']],x['name'],'—',x['message'])
        if x['status']!='ok' and 'output' in x.get('details',{}):
            for line in x['details']['output'].splitlines()[-25:]:print('  ',line[:220])


def run(command):
    REPORT_DIR.mkdir(parents=True,exist_ok=True)
    if command=='status':
        data=latest()
        if not data:print('Aucun état. Lancer audit.');return 10
        print_summary(data['steps']);print('Rapport :',REPORT_DIR/'LATEST_REPORT.json')
        return 20 if data['failed_steps'] else (10 if data['waiting_steps'] else 0)
    if command=='export':
        if not (REPORT_DIR/'LATEST_REPORT.json').is_file():
            print('Aucun rapport à exporter.');return 10
        out=REPORT_DIR/('DIAGNOSTIC_TANDEM_'+now()+'.zip')
        with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:z.write(REPORT_DIR/'LATEST_REPORT.json','LATEST_REPORT.json')
        print('ZIP de diagnostic local :',out)
        print('Inspecter les données avant de partager; aucun envoi automatique.');return 0
    if command=='resume':
        old=latest()
        if not old:print('Aucune étape précédente. Lancer audit.');return 10
        # Important: resume never performs a source mutation, even if guard-install previously failed.
        retry=[i['name'] for i in old['steps'] if i['status']!='ok' and i['name'] in STEPS]
        todo=['inventory']+[s for s in retry if s!='inventory']
        if len(todo)==1 and not retry:
            print('Aucune étape échouée/en attente à reprendre. Pour une vérification complète : audit.');return 0
    elif command=='audit':todo=list(STEPS)
    elif command=='guard-install':todo=['inventory','guard-preview','guard-install']
    else:todo=[command]
    print('=== ORCHESTRATEUR TANDEM FakePlant V1 ===')
    print('Mode :',command,' | World :',WORLD,'| Release : 132 | Tenant fixe FakePlant')
    print('Aucun test IK Apply, migration, import ou provisionnement humain dans ce pack.')
    entries=[];blocked=False
    for name in todo:
        if blocked:
            entries.append({'name':name,'status':'waiting','message':'Non exécuté : dépendance bloquante.','details':{}})
            continue
        try:
            state,msg,details=HANDLERS[name]()
        except Exception as exc:
            state,msg,details='failed','Erreur locale : '+type(exc).__name__+' — '+scrub(str(exc))[:180],{}
        entries.append({'name':name,'status':state,'message':msg,'details':details})
        print({'ok':'[OK]','waiting':'[ATTENTE]','failed':'[BLOQUE]'}[state],name,'—',msg)
        if name=='inventory' and state!='ok':blocked=True
        if command=='guard-install' and name=='guard-preview' and state!='ok':blocked=True
    failed=[e['name'] for e in entries if e['status']=='failed']
    waiting=[e['name'] for e in entries if e['status']=='waiting']
    report={'schema':CODE,'run_id':now(),'mode':command,'tenant':TENANT,'world':WORLD,
            'steps':entries,'failed_steps':failed,'waiting_steps':waiting,
            'no_database_writes_by_orchestrator':True,'human_sso_enforce':False,
            'source_guard_installed':command=='guard-install' and not failed and entries[-1]['status']=='ok'}
    output=store(report)
    print('[RAPPORT]',output)
    print('[ETAT]','BLOQUE' if failed else 'CONFIGURATION EN ATTENTE' if waiting else 'OK')
    if failed:print('Créer un diagnostic (menu option 5), puis corriger uniquement l étape en échec.')
    return 20 if failed else (10 if waiting else 0)


def main():
    p=argparse.ArgumentParser(description='Orchestrateur tandem FakePlant permanent, Konfid aligned')
    p.add_argument('command',choices=('audit','resume','status','export',*HANDLERS))
    return run(p.parse_args().command)

if __name__=='__main__':raise SystemExit(main())
