"""Read-only, local Windows Konnaxion + Orgo Docker SSO/ACL gate.

The issuer is used only for local comparison and is never printed or stored.
"""
from __future__ import annotations
import json
import subprocess
import sys
from pathlib import Path

MARKER_KX='KOA_SSO_KX_AUDIT='
MARKER_ORGO='KOA_SSO_ORGO_AUDIT='
MANAGER=Path(__file__).resolve().parents[4]
KX=MANAGER.parent/'Konnaxion'/'backend'
ORGO=Path(r'C:\mycode\Orgo\Orgo')


def run(args:list[str],cwd:Path,marker:str,stdin:str|None=None,timeout:int=80)->dict:
    try:
        p=subprocess.run(args,cwd=str(cwd),input=stdin,text=True,encoding='utf-8',errors='replace',capture_output=True,timeout=timeout,check=False)
    except (OSError,subprocess.TimeoutExpired) as exc:
        raise RuntimeError('Service indisponible ou delai depasse') from exc
    matches=[s[len(marker):] for s in p.stdout.splitlines() if s.startswith(marker)]
    if p.returncode!=0 or len(matches)!=1:
        raise RuntimeError('Diagnostic refuse ou reponse absente (exit=%s)'%p.returncode)
    try: value=json.loads(matches[0])
    except json.JSONDecodeError as exc:raise RuntimeError('Reponse JSON invalide') from exc
    if not isinstance(value,dict) or value.get('ok') is not True:
        raise RuntimeError('Reponse de diagnostic non conforme')
    return value


def compare(kx:dict,orgo:dict)->dict:
    kx_issuer=kx.get('oidc_issuer','')
    orgo_sso=orgo.get('sso',{})
    same_issuer=bool(kx_issuer) and kx_issuer==orgo_sso.get('issuer') and orgo_sso.get('shared_issuer_matches') is True
    personas_good=(kx.get('persona_count')==900 and kx.get('noninteractive_personas')==900
        and orgo.get('users',{}).get('total',0)>=900
        and orgo.get('users',{}).get('disabled_synthetic')==900
        and orgo.get('users',{}).get('active_synthetic')==0
        and orgo.get('users',{}).get('profiles',0)>=900)
    # Not enough to establish live SSO: requiring explicit eligible human, actual link and ACL grant.
    acl=orgo.get('acl',{})
    sso_ready=(personas_good and same_issuer and kx.get('oidc_enabled') is True
        and kx.get('oidc_client_configured') is True and orgo_sso.get('client_configured') is True
        and orgo_sso.get('origin_https') is True
        and orgo_sso.get('mode')=='ready' and orgo_sso.get('universe')=='fakeplant'
        and orgo_sso.get('target')=='fakeplant'
        and orgo_sso.get('eligible_links',0)>0 and orgo_sso.get('issuer_matching_links',0)>0
        and acl.get('active_role_assignments',0)>0
        and kx.get('active_humans_in_universe',0)>0
        and not kx.get('oidc_auto_signup_allowed',True)
        and not orgo_sso.get('auto_local_login_enabled',False))
    blockers=[]
    if not personas_good:blockers.append('Personas FakePlant : nombres ou etat non conformes. Stopper avant tout SSO.')
    if not kx.get('oidc_enabled'):blockers.append('Configurer un fournisseur OIDC commun pour Konnaxion ; option encore desactivee.')
    if not same_issuer:blockers.append('Issuer OIDC commun non configure / non identique. Ne pas inventer une URL.')
    if not orgo_sso.get('client_configured') or not orgo_sso.get('origin_https'):
        blockers.append('Orgo : client OIDC distinct et origine publique HTTPS requis.')
    if orgo_sso.get('mode')!='ready':blockers.append('Orgo : mode ready requis avant tests humains ; ne pas activer enforce.')
    if orgo_sso.get('target')!='fakeplant' or orgo_sso.get('universe')!='fakeplant':blockers.append('Orgo : ciblage tenant/Universe FakePlant a configurer.')
    if kx.get('oidc_auto_signup_allowed',True):blockers.append('Konnaxion : inscription OIDC automatique a bloquer pour les liaisons explicites.')
    if kx.get('active_humans_in_universe',0)<1 or orgo.get('users',{}).get('active',0)<1:
        blockers.append('Creer ou identifier UN humain de test actif et autorise dans chaque application.')
    if orgo_sso.get('issuer_matching_links',0)<1 or orgo_sso.get('eligible_links',0)<1:
        blockers.append('Orgo : aucune liaison explicite issuer+subject admissible pour FakePlant.')
    if acl.get('roles',0)<1 or acl.get('active_role_assignments',0)<1:
        blockers.append('Orgo : roles/affectations humaines tenant FakePlant a provisionner puis tester ACL.')
    if orgo_sso.get('auto_local_login_enabled',False):blockers.append('Orgo : desactiver ORGO_LOCAL_AUTO_LOGIN avant SSO humain.')
    if not orgo_sso.get('breakglass_active_local',False):
        blockers.append('Avant enforce : compte humain local de secours actif et verifie requis.')
    return dict(personas_ok=personas_good,issuer_exact_match=same_issuer,ready_prerequisites=sso_ready,blockers=blockers)


def main():
    print('=== TANDEM FAKEPLANT : SSO HUMAIN / ACL — LECTURE SEULE ===')
    print('Base Konnaxion neondb ; Orgo PostgreSQL du conteneur API ; tenant UUID verrouille.')
    print('Ne change ni comptes, ni roles, ni jetons, ni bases, ni .env. Aucun secret imprime.')
    if not (KX/'manage.py').is_file() or not (ORGO/'docker-compose.yml').is_file():
        print('[ERREUR] Depots locaux absents des chemins connus.');return 2
    try:
        code=(Path(__file__).parent/'audit_konnaxion.py').read_text(encoding='utf-8')
        kx=run([sys.executable,'manage.py','shell','-c',code],KX,MARKER_KX,timeout=80)
        js=(Path(__file__).parent/'audit_orgo.cjs').read_text(encoding='utf-8')
        orgo=run(['docker','compose','exec','-T','api','node','-'],ORGO,MARKER_ORGO,stdin=js,timeout=50)
    except RuntimeError as exc:
        print('[ERREUR] '+str(exc));return 2
    if not kx['database_expected'] or orgo['organization_id']!='0c2d7244-a0c0-4a10-8f11-0389f275f133':
        print('[BLOQUE] Base ou tenant inattendu.');return 2
    assessment=compare(kx,orgo)
    print('\n--- Konnaxion ---')
    print('FakePlant : %s Worlds / %s comptes clones non connectables sur %s / %s membres Universe / %s membres Worlds' % (
        kx['world_count'],kx['noninteractive_personas'],kx['persona_count'],kx['universe_memberships'],kx['world_memberships']))
    print('Humains actifs membres Universe :',kx['active_humans_in_universe'])
    print('OIDC :', 'active' if kx['oidc_enabled'] else 'off','| client :',bool(kx['oidc_client_configured']))
    print('Auto-inscription federée :',bool(kx.get('oidc_auto_signup_allowed',True)))
    print('\n--- Orgo FakePlant ---')
    print('Comptes actifs : %s ; desactives : %s ; personas synthetiques desactives : %s ; profils : %s' % (
        orgo['users']['active'],orgo['users']['disabled'],orgo['users']['disabled_synthetic'],orgo['users']['profiles']))
    print('Roles : %s ; grants : %s ; affectations : %s' % (
        orgo['acl']['roles'],orgo['acl']['role_grants'],orgo['acl']['active_role_assignments']))
    print('Liens SSO : %s ; eligibles : %s ; issuer attendu : %s' % (
        orgo['sso']['link_count'],orgo['sso']['eligible_links'],orgo['sso']['issuer_matching_links']))
    print('Mode : %s ; Universe : %s ; Organization : %s ; HTTPS : %s' % (
        orgo['sso']['mode'],orgo['sso']['universe'] or '(non configure)',
        orgo['sso']['target'] or '(non configure)',orgo['sso']['origin_https']))
    print('Compte local de secours actif :',bool(orgo['sso']['breakglass_active_local']))
    print('Issuer identique entre applications :',assessment['issuer_exact_match'])
    print('\n--- Protections / portes ---')
    print('[OK]' if assessment['personas_ok'] else '[BLOQUE]','900 personas en lecture seule, non connectables, sans import')
    print('[READY]' if assessment['ready_prerequisites'] else '[ATTENTE]','Prerequis de configuration SSO/ACL humains')
    for i,item in enumerate(assessment['blockers'],1):print('[ATTENTE %02d] %s'%(i,item))
    print('\n[FIN] Aucun flux de connexion humain teste, aucune migration, aucun changement DB/env/role/jeton.')
    return 0

if __name__=='__main__':raise SystemExit(main())
