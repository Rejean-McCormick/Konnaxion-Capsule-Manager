"""Verify FakePlant's two live identity projections, without database writes.

Run: py -3 -m tools.tandem_v2.verify_fakeplant_pair
This intentionally DOES NOT claim ACL, OIDC, IK, workflow or deploy readiness.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

KEY = "fakeplant"
EXPECTED_ORGO_UUID = "0c2d7244-a0c0-4a10-8f11-0389f275f133"
KX_REPO = Path(r"C:\mycode\Konnaxion\Konnaxion")
KX_BACKEND = KX_REPO / "backend"
KX_WORLDS = Path(r"C:\mycode\Konnaxion\Konnaxion_Worlds")
ORGO_REPO = Path(r"C:\mycode\Orgo\Orgo")
ORGO_WORLDS = Path(r"C:\mycode\Orgo\Orgo_Worlds")
MANAGER = Path(__file__).resolve().parents[2]
LOCAL_DIR = Path(r"C:\mycode\Konnaxion\Tandem_Local")
BINDING_DRAFT = LOCAL_DIR / "fakeplant-binding.DRAFT.json"
EVIDENCE = LOCAL_DIR / "fakeplant-identity-verified.LOCAL.json"


def digest(strings):
    return hashlib.sha256("\n".join(sorted(strings)).encode("utf-8")).hexdigest()


def checksum(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(ok, message):
    if not ok:
        raise ValueError(message)


def validate_sources():
    canonical = KX_WORLDS / "universes" / KEY / "fixture"
    mirror = ORGO_WORLDS / KEY / "fixture"
    manifest_file = canonical / "MANIFEST.json"
    require(manifest_file.is_file(), "Fixture canonique FakePlant absente")
    require((mirror / "MANIFEST.json").is_file(), "Fixture miroir Orgo absente")
    manifest = json.loads(manifest_file.read_text(encoding="utf-8-sig"))
    sha_table = manifest.get("sha256")
    require(isinstance(sha_table, dict) and len(sha_table) >= 3, "Manifest SHA256 incomplet")
    require(manifest_file.read_bytes() == (mirror / "MANIFEST.json").read_bytes(), "Manifest miroir divergent")
    for filename, expected in sha_table.items():
        require(bool(re.fullmatch(r"[A-Za-z0-9_.-]+\.json", filename)), "Nom de fixture suspect")
        require(bool(re.fullmatch(r"[0-9a-fA-F]{64}", str(expected))), "SHA256 invalide")
        require(checksum(canonical / filename) == expected.lower(), f"SHA256 canonique divergent: {filename}")
        require(checksum(mirror / filename) == expected.lower(), f"SHA256 miroir divergent: {filename}")
    persons = json.loads((canonical / "employes.json").read_text(encoding="utf-8"))["employees"]
    memberships = json.loads((canonical / "acces-worlds.json").read_text(encoding="utf-8"))["memberships"]
    require(len(persons) == 900 and len(memberships) == 2736, "Fixture attendue: 900 personnes, 2736 adhesions")
    ids = [x["id"] for x in persons]
    names = [x["username"] for x in persons]
    emails = [x["email"].lower() for x in persons]
    require(len(set(ids)) == len(set(names)) == len(set(emails)) == 900, "IDs / usernames / courriels dupliques")
    for p in persons:
        require(p["synthetic"] is True and p["login_enabled"] is False and
                p["security"]["no_password"] is True and
                p["security"]["orgo_status"] == "disabled" and
                p["orgo_identity"] == "external_only", "Persona connectable ou non synthetique")
    manifest_tandem = KX_WORLDS / "universes" / KEY / "integrations" / "orgo.tandem.json"
    link = json.loads(manifest_tandem.read_text(encoding="utf-8"))
    require(link.get("contract") == "koa.tandem-universe/v1" and link.get("universe_key") == KEY
            and link.get("orgo", {}).get("organization_slug") == KEY, "Contrat Universe / Orgo divergent")
    return {"ids_sha256": digest(ids), "emails_sha256": digest(emails),
            "users": 900, "world_memberships": 2736,
            "fixture_sha256": checksum(canonical / "employes.json")}


KONNAXION_CHECK = r'''
import json, hashlib
from pathlib import Path
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import connection, transaction
from konnaxion.worlds.models import Universe, World, UniverseMembership, WorldMembership

root = Path(settings.BASE_DIR).parent.parent / 'Konnaxion_Worlds' / 'universes' / 'fakeplant' / 'fixture'
people = json.loads((root / 'employes.json').read_text(encoding='utf-8'))['employees']
links = json.loads((root / 'acces-worlds.json').read_text(encoding='utf-8'))['memberships']
by_name = {p['username']: p for p in people}
by_id = {p['id']: p for p in people}
names = set(by_name)
User = get_user_model()
def must(ok, text):
    if not ok: raise ValueError(text)
def digest(items):
    return hashlib.sha256('\n'.join(sorted(items)).encode('utf-8')).hexdigest()
with transaction.atomic():
    with connection.cursor() as cursor: cursor.execute('SET TRANSACTION READ ONLY')
    universe = Universe.objects.filter(key='fakeplant', status='active').first()
    must(universe is not None, 'Universe FakePlant non actif')
    worlds = {w.key:w for w in World.objects.filter(universe=universe, status='active')}
    must(len(worlds) == 6 and all(w.current_release_id for w in worlds.values()), '6 Worlds actifs/releases requis')
    users = {u.username:u for u in User.objects.filter(username__in=names)}
    must(set(users)==names and len(users)==900, 'Projection Konnaxion 900 incomplete')
    for name, user in users.items():
        expected = by_name[name]
        must(user.email == expected['email'] and user.is_klone and user.account_type=='human'
             and not user.can_interactive_login and not user.has_usable_password()
             and not user.is_staff and not user.is_superuser,
             'Compte FakePlant connectable ou divergent')
    ums = list(UniverseMembership.objects.filter(universe=universe,user__username__in=names).select_related('user'))
    must(len(ums)==900 and {x.user.username for x in ums}==names and
         all(x.role=='member' and x.is_active for x in ums), 'Adhesions Universe divergentes')
    exp = {(by_id[x['person_id']]['username'],'fp-'+x['world_key']):x['role'] for x in links}
    must(len(exp)==2736 and all(key[1] in worlds for key in exp), 'Fixture adhesion World invalide')
    wms = list(WorldMembership.objects.filter(world__in=list(worlds.values()),user__username__in=names).select_related('world','user'))
    actual = {(x.user.username,x.world.key):x.role for x in wms}
    must(len(wms)==2736 and actual==exp and all(x.is_active for x in wms), 'Adhesions World divergentes')
    output = dict(ok=True, database_engine=settings.DATABASES['default']['ENGINE'],
                  database_name=settings.DATABASES['default']['NAME'], users=900,
                  universe_memberships=900, world_memberships=2736,
                  emails_sha256=digest([x.email.lower() for x in users.values()]),
                  worlds=6, interactive_logins=0)
print('__KOA_KX_PROOF__='+json.dumps(output,sort_keys=True,ensure_ascii=True))
'''


ORGO_CHECK = r'''
const crypto = require('node:crypto');
const {PrismaClient} = require('@prisma/client');
const db = new PrismaClient();
const hash = xs => crypto.createHash('sha256').update(xs.slice().sort().join('\n'),'utf8').digest('hex');
(async()=>{
try {
  const result=await db.$transaction(async tx=>{
    await tx.$executeRaw`SET TRANSACTION READ ONLY`;
    const org=await tx.organization.findUnique({where:{slug:'fakeplant'},select:{id:true,status:true}});
    if (!org || org.status!=='active' || org.id!=='0c2d7244-a0c0-4a10-8f11-0389f275f133')
      throw new Error('Tenant FakePlant/UUID non conforme');
    const users=await tx.userAccount.findMany({where:{organization_id:org.id},select:{id:true,email:true,status:true,auth_provider:true,password_hash:true}});
    const profiles=await tx.personProfile.findMany({where:{organization_id:org.id},select:{linked_user_id:true,external_reference:true}});
    if(users.length!==900||profiles.length!==900) throw new Error('900 comptes/profils Orgo attendus');
    if(users.some(u=>u.status!=='disabled'||u.auth_provider!=='external_only'||u.password_hash!==null))
      throw new Error('Compte Orgo actif ou fournisseur divergent');
    const uids=new Set(users.map(u=>u.id));
    const refs=profiles.map(p=>p.external_reference);
    if(new Set(refs).size!==900 || profiles.some(p=>!uids.has(p.linked_user_id)))
      throw new Error('Profils Orgo invalides');
    return {ok:true,organization_id:org.id,tenant:'fakeplant',users:users.length,disabled:users.length,
      profiles:profiles.length,ids_sha256:hash(refs),emails_sha256:hash(users.map(u=>u.email.toLowerCase()))};
  });
  console.log('__KOA_ORGO_PROOF__='+JSON.stringify(result));
} catch(e){console.error('ORGO_PROOF_ERROR:'+(e&&e.message||'unknown'));process.exitCode=2}
finally{await db.$disconnect()}
})();
'''


def run_probe(args, *, cwd, input_text=None, marker, label):
    try:
        output = subprocess.run(args, input=input_text, text=True, encoding="utf-8", errors="replace",
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                cwd=cwd, timeout=150, shell=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError(f"{label} inaccessible : {type(exc).__name__}") from exc
    if output.returncode != 0:
        raise ValueError(f"{label} : lecture impossible (code {output.returncode}). Aucune ecriture.")
    results = [line[len(marker):] for line in output.stdout.splitlines() if line.startswith(marker)]
    require(len(results) == 1, f"{label} : preuve structuree absente")
    payload = json.loads(results[0])
    require(payload.get("ok") is True, f"{label} : preuve invalide")
    return payload


def check_draft():
    if BINDING_DRAFT.exists():
        value = json.loads(BINDING_DRAFT.read_text(encoding="utf-8-sig"))
        require(value.get("contract") == "koa.tandem-identity/v1" and
                value.get("universe_key") == "fakeplant" and
                value.get("orgo_organization_slug") == "fakeplant" and
                value.get("orgo_organization_id") == EXPECTED_ORGO_UUID,
                "Brouillon de liaison deja present mais divergent; aucun ecrasement")
        return False
    model = json.loads((MANAGER / "TANDEM_IDENTITY_EXEMPLE.json").read_text(encoding="utf-8-sig"))
    require(model.get("contract") == "koa.tandem-identity/v1" and
            model.get("universe_key") == "fakeplant" and
            model.get("orgo_organization_slug") == "fakeplant", "Modele de binding incompatible")
    model.update(orgo_organization_id=EXPECTED_ORGO_UUID, konnaxion_origin="A_CONFIGURER",
                 orgo_origin="A_CONFIGURER", orgo_worlds_api_origin="A_CONFIGURER",
                 orgo_workflow_version="CHANGE_ME", orgo_acl_reviewed=False,
                 konnaxion_acl_reviewed=False)
    LOCAL_DIR.mkdir(parents=True, exist_ok=True)
    # exclusive create: existing file never overwritten
    with BINDING_DRAFT.open("x", encoding="utf-8") as dest:
        dest.write(json.dumps(model,indent=2,ensure_ascii=False)+"\n")
    return True


def record_evidence(evidence):
    LOCAL_DIR.mkdir(parents=True,exist_ok=True)
    # Local file only; atomic update. Does not modify any runtime config or DB.
    fd,name=tempfile.mkstemp(prefix=".fakeplant-pair-",suffix=".json",dir=LOCAL_DIR)
    try:
        with os.fdopen(fd,"w",encoding="utf-8") as outfile:
            json.dump(evidence,outfile,indent=2,sort_keys=True,ensure_ascii=False)
            outfile.write("\n")
        os.replace(name,EVIDENCE)
    finally:
        if os.path.exists(name):os.unlink(name)


def main():
    print("=== TANDEM FAKEPLANT : LIAISON DES IDENTITES (SANS ECRITURE DB) ===")
    sources=validate_sources()
    print("[OK] Fixture : 900 personnes, 2736 acces World, miroirs SHA256 identiques")
    kx=run_probe(["uv","run","--no-sync","python","manage.py","shell","-c",KONNAXION_CHECK],
                  cwd=KX_BACKEND,marker="__KOA_KX_PROOF__=",label="Konnaxion Neon")
    require(kx["database_engine"]=="django.db.backends.postgresql" and kx["database_name"]=="neondb",
            "La base Konnaxion n'est pas le PostgreSQL neondb attendu")
    require(kx["users"]==900 and kx["universe_memberships"]==900 and kx["world_memberships"]==2736 and
            kx["interactive_logins"]==0 and kx["emails_sha256"]==sources["emails_sha256"],
            "Projection Konnaxion incoherente avec fixture")
    print("[OK] Neon : 900 comptes non connectables, 900 adhesions Universe, 2736 adhesions World")
    orgo=run_probe(["docker","compose","-f","docker-compose.yml","exec","-T","api","node","-"],
                    cwd=ORGO_REPO,input_text=ORGO_CHECK,
                    marker="__KOA_ORGO_PROOF__=",label="Orgo Docker")
    require(orgo["organization_id"]==EXPECTED_ORGO_UUID and orgo["users"]==900 and
            orgo["disabled"]==900 and orgo["profiles"]==900 and
            orgo["ids_sha256"]==sources["ids_sha256"] and
            orgo["emails_sha256"]==sources["emails_sha256"],
            "Projection Orgo incoherente avec fixture / tenant")
    print("[OK] Orgo Docker : 900 comptes disabled, 900 profils, UUID conforme")
    print("[OK] Correspondance exacte : references externes et courriels des 900 personas")
    created=check_draft()
    evidence={"schema":"koa.tandem-identity-proof-local/v1", "verified_at_utc":datetime.now(timezone.utc).isoformat(),
              "universe_key":KEY,"orgo_organization_id":EXPECTED_ORGO_UUID,
              "orgo_organization_slug":KEY,"fixture_employes_sha256":sources["fixture_sha256"],
              "external_ids_sha256":sources["ids_sha256"],"emails_sha256":sources["emails_sha256"],
              "konnaxion":{"database_name":"neondb","users":900,"universe_memberships":900,"world_memberships":2736},
              "orgo":{"source":"docker-compose api","users":900,"disabled":900,"profiles":900},
              "identity_projections_verified":True,"acl_reviewed":False,"sso_operational":False,
              "interaction_kernel_e2e_verified":False,"deployment_ready":False,
              "status":"IDENTITY_ONLY_LOCAL_EVIDENCE"}
    record_evidence(evidence)
    print("[OK] Brouillon local : " + ("cree" if created else "existant conforme"))
    print("[OK] Preuve locale : " + str(EVIDENCE))
    print("[ATTENTE] ACL, OIDC, Interaction Kernel, workflows, deployement tandem : NON verifies")
    print("[FIN] Aucune modification dans Neon, Orgo ou les manifests sources.")
    return 0


if __name__ == "__main__":
    try: sys.exit(main())
    except (ValueError, KeyError, OSError, json.JSONDecodeError) as exc:
        print("[BLOQUE] " + str(exc),file=sys.stderr)
        sys.exit(2)
